"""Copilot interaction metadata from the unified audit log (Standard default).

Uses the Microsoft Purview Audit Log Query API (``/v1.0/security/auditLog/
queries``) with an application permission. Queries run asynchronously in the
service, so collection polls within a time budget and retains investigation
metadata. No prompt or response content is stored.

Enable with ``--preview-collectors copilot-audit`` and grant
``AuditLogsQuery.Read.All`` (setup ``-PreviewCollectors CopilotAudit``).
"""

import asyncio
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

from .new_recommendation import new_recommendation

AUTH_PATH = "graph_audit_query"
SOURCE = "Microsoft Purview Audit Log Query API"
QUERIES_PATH = "/v1.0/security/auditLog/queries"
DEFAULT_DAYS = 7
DEFAULT_MAX_WAIT_MINUTES = 8
MAX_RECORDS = 50000


def _base(**extra):
    from .auth_plan import credential_kind_for_path, graph_credential_kind
    state = {
        "available": False, "availability_status": "unavailable", "records_collected": 0,
        "reason": "", "source": SOURCE, "auth_path_id": AUTH_PATH, "preview": False,
        "credential_type": credential_kind_for_path(AUTH_PATH, {"graph_credential": graph_credential_kind()}),
        "coverage": "partial", "evidence_quality": "standard",
    }
    state.update(extra)
    return state


def _wait_budget_seconds(max_wait_minutes=None):
    if max_wait_minutes is None:
        try:
            max_wait_minutes = float(os.environ.get("COPILOT_AUDIT_MAX_WAIT_MINUTES", DEFAULT_MAX_WAIT_MINUTES))
        except ValueError:
            max_wait_minutes = DEFAULT_MAX_WAIT_MINUTES
    return max(0.0, max_wait_minutes) * 60


async def _create_query(graph_client, start, end):
    """Create the query, falling back from a record-type to an operation filter."""
    base = {
        "@odata.type": "#microsoft.graph.security.auditLogQuery",
        "displayName": f"AI readiness Copilot interactions {start.date().isoformat()}",
        "filterStartDateTime": start.isoformat().replace("+00:00", "Z"),
        "filterEndDateTime": end.isoformat().replace("+00:00", "Z"),
    }
    last_error = None
    for filters in ({"recordTypeFilters": ["copilotInteraction"]}, {"operationFilters": ["CopilotInteraction"]}):
        try:
            response = await graph_client.request("POST", QUERIES_PATH, json={**base, **filters})
            payload = response.json() if response.content else {}
            if payload.get("id"):
                return payload, next(iter(filters))
        except Exception as exc:
            last_error = exc
            if getattr(exc, "status_code", 0) not in {400}:
                raise
    raise last_error or RuntimeError("The audit query was not created.")


def _app_host(record):
    data = record.get("auditData") if isinstance(record.get("auditData"), dict) else {}
    event = data.get("CopilotEventData") if isinstance(data.get("CopilotEventData"), dict) else {}
    return str(event.get("AppHost") or data.get("AppHost") or record.get("service") or "Unknown")


def _resource_metadata(record):
    data = record.get('auditData') or {}
    if not isinstance(data,dict): return []
    event = data.get('CopilotEventData') or {}
    if not isinstance(event,dict): return []
    return [{key:value for key,value in resource.items() if key in
             {'ID','Id','Action','SiteUrl','ListItemUniqueId','Type','Name','SensitivityLabelId'}}
            for resource in event.get('AccessedResources',[]) or [] if isinstance(resource,dict)]


def summarize_records(records, include_user_detail=True):
    per_day, per_host, users = Counter(), Counter(), set()
    user_rows = Counter()
    for record in records:
        if not isinstance(record, dict):
            continue
        created = str(record.get("createdDateTime") or "")[:10]
        if created:
            per_day[created] += 1
        per_host[_app_host(record)] += 1
        user = str(record.get("userPrincipalName") or record.get("userId") or "")
        if user:
            users.add(user.lower())
            if include_user_detail:
                user_rows[user.lower()] += 1
    summary = {
        "total_events": sum(per_host.values()),
        "distinct_users": len(users),
        "events_by_day": dict(sorted(per_day.items())),
        "events_by_app_host": dict(per_host.most_common()),
    }
    if include_user_detail:
        summary["events_by_user"] = dict(user_rows.most_common())
    return summary


async def collect_copilot_interaction_audit(graph_client, *, days=DEFAULT_DAYS, max_wait_minutes=None,
                                            include_user_detail=True, poll_initial=15, poll_max=60,
                                            sleep=asyncio.sleep, clock=None):
    """Return aggregate Copilot interaction evidence; never raises."""
    clock = clock or (lambda: datetime.now(timezone.utc))
    end = clock().replace(microsecond=0)
    start = end - timedelta(days=days)
    budget = _wait_budget_seconds(max_wait_minutes)
    try:
        query, filter_basis = await _create_query(graph_client, start, end)
    except Exception as exc:
        from .access_errors import status_code_of
        status_code = status_code_of(exc)
        reason = (f"The audit query could not be created ({type(exc).__name__}).")
        if status_code == 403:
            reason = ("Microsoft Graph denied the audit query (HTTP 403). Grant and consent AuditLogsQuery.Read.All "
                      "(setup -PreviewCollectors CopilotAudit) and confirm audit is enabled.")
        return _base(reason=reason, status_code=status_code, window_days=days,
                     window_start=start.isoformat(), window_end=end.isoformat(), query_status='not_created')
    query_id = query["id"]
    waited, interval = 0.0, float(poll_initial)
    status = str(query.get("status") or "notStarted")
    while status not in {"succeeded", "failed", "cancelled"} and waited < budget:
        pause = min(interval, max(budget - waited, 0))
        await sleep(pause)
        waited += pause
        interval = min(interval * 2, float(poll_max))
        try:
            status = str((await graph_client.get_json(f"{QUERIES_PATH}/{query_id}")).get("status") or status)
        except Exception:
            continue
    window = {"window_days": days, "window_start": start.isoformat(), "window_end": end.isoformat(),
              "query_id": query_id, "filter_basis": filter_basis}
    if status != "succeeded":
        reason = ("The audit query did not finish within the time budget; rerun later or raise COPILOT_AUDIT_MAX_WAIT_MINUTES."
                  if status not in {"failed", "cancelled"} else f"The audit query ended with status {status}.")
        return _base(reason=reason, availability_status="partial" if status not in {"failed", "cancelled"} else "unavailable",
                     query_status=status, **window)
    try:
        result = await graph_client.get_collection(f"{QUERIES_PATH}/{query_id}/records", params={"$top": "1000"},
                                                   max_pages=max(1, MAX_RECORDS // 1000))
    except Exception as exc:
        return _base(reason=f'Record retrieval failed ({type(exc).__name__}); the saved query succeeded.',
                     query_status=status, availability_status='unavailable', **window)
    records = result.get("value", []) if isinstance(result, dict) else []
    summary = summarize_records(records, include_user_detail=include_user_detail)
    # Only investigation metadata is retained. auditData may contain customer
    # content and is deliberately reduced to documented identifiers and host.
    detail = [{key: record.get(key) for key in (
        'id', 'createdDateTime', 'operation', 'userId', 'userPrincipalName',
        'service', 'recordType', 'objectId', 'clientIp', 'administrativeUnits') if key in record}
        | {'AppHost': _app_host(record), 'AccessedResources':_resource_metadata(record)} for record in records if isinstance(record, dict)]
    truncated = bool(result.get("truncated")) if isinstance(result, dict) else False
    available = bool(result.get("available")) if isinstance(result, dict) else False
    return _base(
        available=available,
        availability_status=("partial" if truncated else "available") if available else "unavailable",
        records_collected=len(records), truncated=truncated, query_status=status,
        reason=result.get("reason", "") if isinstance(result, dict) else "",
        summary=summary, records=detail,
        evidence_quality='standard', evidence_level='observed_operation',
        source_api=QUERIES_PATH, scope='Returned Copilot interaction events in the requested window',
        pages_collected=result.get('pages_collected', 0), complete=available and not truncated,
        **window,
    )


def build_copilot_audit_recommendations(evidence):
    """Reference finding; qualifies usage evidence and never affects the decision."""
    if not isinstance(evidence, dict) or not evidence.get("available"):
        return []
    summary = evidence.get("summary") or {}
    total = summary.get("total_events", 0)
    days = evidence.get("window_days", DEFAULT_DAYS)
    hosts = list((summary.get("events_by_app_host") or {}).items())[:4]
    host_text = ", ".join(f"{name} ({count})" for name, count in hosts)
    if total:
        observation = (f"The unified audit log returned {total} Copilot interaction event(s) from {summary.get('distinct_users', 0)} "
                       f"distinct user(s) in the last {days} days" + (f"; top experiences: {host_text}." if host_text else "."))
        recommendation = ("Use these events alongside the Copilot usage reports: audit events include experiences the licensed-user "
                          "reports omit, and they confirm interactions are being logged for investigation.")
    else:
        observation = f"The unified audit log returned no Copilot interaction events in the last {days} days."
        recommendation = ("Confirm query scope, retention, access and expected activity with the audit owner. "
                          "An empty query cannot establish whether logging failed or no matching activity occurred.")
    if evidence.get("truncated"):
        observation += " The record read was capped; counts are a lower bound."
    return [new_recommendation(
        service="M365", feature="Copilot interaction audit events", observation=observation,
        recommendation=recommendation, link_text="Audit logs for Copilot",
        link_url="https://learn.microsoft.com/purview/audit-copilot",
        priority="Low", status="Insight", disposition="Reference",
        finding_key="copilot.audit.interactions", evidence_key="ai_usage_detail",
        evidence_basis="Audit event metadata", confidence="Medium",
    )]

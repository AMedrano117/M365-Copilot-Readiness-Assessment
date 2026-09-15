"""Evidence-based Microsoft 365 Copilot, app, and optional Shadow AI usage collection."""

import asyncio
import csv
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

GRAPH_BASE = "https://graph.microsoft.com"
COPILOT_PERIODS = ("D7", "D28", "D90", "D180")
COPILOT_SKU_IDS = {
    # Documented paid Microsoft 365 Copilot SKUs; service plans below also
    # recognize newer offers/bundles without guessing their SKU identifiers.
    # https://learn.microsoft.com/entra/identity/users/licensing-service-plan-reference
    "639dec6b-bb19-468b-871c-c5c441c4b0cb",
    "a809996b-059e-42e2-9866-db24b99a9782",
    "ad9c22b3-52d7-4e7e-973c-88121ea96436",
    "15f2e9fc-b782-4f73-bf51-81d8b7fff6f4",
}
COPILOT_SERVICE_PLAN_IDS = {
    "a62f8878-de10-42f3-b68f-6149a25ceb97",  # M365_COPILOT_APPS
    "3f30311c-6b1e-48a4-ab79-725b469da960",  # M365_COPILOT_BUSINESS_CHAT
}
COPILOT_SERVICE_PLAN_NAMES = {"M365_COPILOT_APPS", "M365_COPILOT_BUSINESS_CHAT"}
# Recognizable qualifying workloads only. Exchange Foundation, Bing Chat
# Enterprise, Copilot Studio and security/storage add-ons are not base plans.
# This remains an estimate, not a complete product-terms eligibility audit.
COPILOT_BASE_SERVICE_PLAN_NAMES = {
    "EXCHANGE_S_STANDARD", "EXCHANGE_S_ENTERPRISE", "EXCHANGE_S_DESKLESS",
    "OFFICESUBSCRIPTION", "O365_BUSINESS",
    "SHAREPOINTSTANDARD", "SHAREPOINTENTERPRISE", "SHAREPOINTDESKLESS",
    "TEAMS1",
}
COPILOT_BASE_SKU_NAMES = {
    "SPB", "O365_BUSINESS_ESSENTIALS", "O365_BUSINESS_PREMIUM", "O365_BUSINESS",
    "SPE_E3", "SPE_E5", "SPE_E7", "SPE_F1", "SPE_F3",
    "STANDARDPACK", "ENTERPRISEPACK", "ENTERPRISEPREMIUM", "DESKLESSPACK",
    "OFFICESUBSCRIPTION",
}
COPILOT_APPS = {
    "microsoftTeams": "Teams",
    "word": "Word",
    "powerPoint": "PowerPoint",
    "outlook": "Outlook",
    "excel": "Excel",
    "oneNote": "OneNote",
    "loop": "Loop",
    "edge": "Edge",
    "copilotChat": "Copilot Chat",
    "copilotChatWork": "Copilot Chat (work)",
    "copilotChatWeb": "Copilot Chat (web)",
}


def _base_evidence(source, period=""):
    return {
        "available": False,
        "availability_status": "unavailable",
        "reason": "Not collected",
        "source": source,
        "period": period,
        "selected_period": "",
        "refresh_date": "",
        "freshness": "Unknown",
        "stale": False,
        "records_collected": 0,
        "pages_collected": 0,
        "truncated": False,
    }


def _parse_date(value):
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _set_freshness(evidence, max_age_days, evaluation_date=None):
    parsed = _parse_date(evidence.get("refresh_date"))
    if not parsed:
        evidence["freshness"] = "Unknown"
        evidence["stale"] = False
        return evidence
    evaluated_at = _parse_date(evaluation_date) if evaluation_date else datetime.now(timezone.utc)
    age_days = (evaluated_at.date() - parsed.date()).days
    if age_days < 0:
        evidence.update(age_days=None, freshness='Future', stale=False)
        return evidence
    evidence["age_days"] = age_days
    evidence["stale"] = age_days > max_age_days
    evidence["freshness"] = "Stale" if evidence["stale"] else "Fresh"
    return evidence


def _http_reason(status_code, body=""):
    if status_code == 401:
        return "Authentication failed"
    if status_code == 403:
        return "Permission missing or service not available"
    if status_code == 404:
        return "Report is not available for this tenant"
    detail = str(body or "").strip().replace("\n", " ")[:180]
    return "HTTP {}{}".format(status_code, ": " + detail if detail else "")


async def _request_with_retry(client, path, params=None, attempts=3):
    """Apply a small bounded retry for Microsoft report throttling/transient failures."""
    response = None
    for attempt in range(attempts):
        response = await client.get(path, params=params)
        if response.status_code not in (429, 502, 503, 504) or attempt == attempts - 1:
            return response
        headers = getattr(response, "headers", {}) or {}
        try:
            retry_after = float(headers.get("Retry-After", 0) or 0)
        except (TypeError, ValueError):
            retry_after = 0
        await asyncio.sleep(min(max(retry_after, 2 ** attempt), 15))
    return response


async def _get_json(client, path, params=None):
    try:
        response = await _request_with_retry(client, path, params=params)
    except Exception as exc:
        return None, "Request failed: {}".format(type(exc).__name__)
    if response.status_code != 200:
        return None, _http_reason(response.status_code, response.text)
    try:
        return response.json(), ""
    except (ValueError, json.JSONDecodeError):
        return None, "Microsoft returned an unreadable report payload"


async def _get_all_json(client, path, params=None):
    """Read every OData page while retaining the first response envelope."""
    payload, error = await _get_json(client, path, params)
    if payload is None or not isinstance(payload, dict):
        return payload, error
    combined = dict(payload)
    combined["value"] = list(_value_rows(payload))
    pages = 1
    next_link = payload.get("@odata.nextLink")
    seen = set()
    while next_link and next_link not in seen:
        seen.add(next_link)
        page, page_error = await _get_json(client, next_link)
        if page is None:
            return combined, page_error
        combined["value"].extend(_value_rows(page))
        pages += 1
        next_link = page.get("@odata.nextLink") if isinstance(page, dict) else None
    combined.pop("@odata.nextLink", None)
    combined["_collection_metadata"] = {
        "records_collected": len(combined["value"]),
        "pages_collected": pages,
        "truncated": False,
    }
    return combined, ""


def _report_header_key(header):
    """Convert Microsoft report CSV headings to their documented JSON field names."""
    words = re.findall(r"[A-Za-z0-9]+", str(header or ""))
    if not words:
        return ""
    key = words[0].lower() + "".join(word[:1].upper() + word[1:] for word in words[1:])
    replacements = {
        "powerpoint": "powerPoint",
        "onenote": "oneNote",
    }
    for source, target in replacements.items():
        if key.startswith(source):
            key = target + key[len(source):]
    return key


def _csv_report_payload(text):
    reader = csv.DictReader(io.StringIO(str(text or "").lstrip("\ufeff")))
    rows = []
    for source in reader:
        row = {_report_header_key(key): value for key, value in source.items() if key is not None}
        if any(value not in (None, "") for value in row.values()):
            rows.append(row)
    return {"value": rows} if rows else None


async def _get_report_payload(client, path):
    """Request the stable CSV stream and accept JSON if Microsoft returns it instead."""
    try:
        response = await _request_with_retry(client, path, params={"$format": "text/csv"})
    except Exception as exc:
        return None, "Request failed: {}".format(type(exc).__name__)
    if response.status_code == 200:
        if response.text.lstrip().startswith(('{', '[')):
            try:
                return response.json(), ""
            except (ValueError, json.JSONDecodeError):
                return None, "Microsoft returned an unreadable report payload"
        csv_payload = _csv_report_payload(response.text)
        if csv_payload:
            return csv_payload, ""
        try:
            return response.json(), ""
        except (ValueError, json.JSONDecodeError):
            return None, "Microsoft returned an unreadable report payload"
    return None, _http_reason(response.status_code, response.text)


def _value_rows(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    value = payload.get("value", [])
    return value if isinstance(value, list) else []


def _period_number(period):
    try:
        return int(str(period).upper().replace("D", ""))
    except (TypeError, ValueError):
        return None


def _find_period_record(payload, period, collection_name):
    target = _period_number(period)
    candidates = []
    for root in _value_rows(payload):
        if not isinstance(root, dict):
            continue
        nested = root.get(collection_name)
        if isinstance(nested, list):
            candidates.extend(item for item in nested if isinstance(item, dict))
        else:
            candidates.append(root)
    for item in candidates:
        if item.get("reportPeriod") in (target, str(target), period):
            return item
    # A few JSON fixtures omit reportPeriod when the requested period is implicit.
    # Never relabel an explicitly different provider period as the requested period.
    if len(candidates) == 1 and candidates[0].get("reportPeriod") in (None, ""):
        return candidates[0]
    return None


def _integer(record, *keys):
    for key in keys:
        value = record.get(key) if isinstance(record, dict) else None
        if value not in (None, ""):
            try:
                return int(float(value))
            except (TypeError, ValueError):
                continue
    return 0


def _number(record, *keys):
    for key in keys:
        value = record.get(key) if isinstance(record, dict) else None
        if value not in (None, ""):
            try:
                return round(float(value), 2)
            except (TypeError, ValueError):
                continue
    return 0


def _optional_integer(record, *keys):
    """Return None when a version-specific report field was not supplied."""
    for key in keys:
        value = record.get(key) if isinstance(record, dict) else None
        if value not in (None, ""):
            try:
                return int(float(value))
            except (TypeError, ValueError):
                continue
    return None


def _optional_number(record, *keys):
    for key in keys:
        value = record.get(key) if isinstance(record, dict) else None
        if value not in (None, ""):
            try:
                return round(float(value), 2)
            except (TypeError, ValueError):
                continue
    return None


def _normalize_copilot_period(payload, period):
    record = _find_period_record(payload, period, "adoptionByProduct")
    if not record:
        return None
    enabled = _optional_integer(record, "microsoft365CopilotEnabledUsers", "anyAppEnabledUsers")
    active = _optional_integer(record, "microsoft365CopilotActiveUsers", "anyAppActiveUsers")
    apps = {}
    for prefix, display_name in COPILOT_APPS.items():
        app_enabled = _optional_integer(record, prefix + "EnabledUsers")
        app_active = _optional_integer(record, prefix + "ActiveUsers")
        if app_enabled is not None or app_active is not None:
            apps[display_name] = {
                "enabled_users": app_enabled,
                "active_users": app_active,
                "active_rate": (
                    round(app_active * 100 / app_enabled, 1)
                    if app_enabled and app_active is not None else None
                ),
            }
    return {
        "period": period,
        "enabled_users": enabled,
        "active_users": active,
        "active_rate": round(active * 100 / enabled, 1) if enabled and active is not None else None,
        "unused_licenses": max(enabled - active, 0) if enabled is not None and active is not None else None,
        "total_prompts": _optional_integer(record, "totalPromptsSubmitted", "promptsSubmitted"),
        "average_prompts": _optional_number(record, "averagePromptsSubmitted", "averagePromptsSubmittedPerUser"),
        "apps": apps,
    }


def _normalize_copilot_trend(payload, period):
    root = _find_period_record(payload, period, "adoptionByDate")
    rows = []
    # Some responses put adoptionByDate beside reportPeriod rather than beneath it.
    for item in _value_rows(payload):
        if isinstance(item, dict) and isinstance(item.get("adoptionByDate"), list):
            rows.extend(item["adoptionByDate"])
        elif isinstance(item, dict) and item.get("reportDate"):
            rows.append(item)
    if not rows and isinstance(root, dict):
        rows = [root]
    normalized = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("reportDate"):
            continue
        normalized.append({
            "date": row.get("reportDate"),
            "enabled_users": _optional_integer(row, "microsoft365CopilotEnabledUsers", "anyAppEnabledUsers"),
            "active_users": _optional_integer(row, "microsoft365CopilotActiveUsers", "anyAppActiveUsers"),
            "copilot_chat_work_active_users": _optional_integer(row, "copilotChatWorkActiveUsers"),
            "copilot_chat_web_active_users": _optional_integer(row, "copilotChatWebActiveUsers"),
        })
    return sorted(normalized, key=lambda row: str(row.get("date", "")))


def _latest_refresh_date(payloads):
    values = []
    for payload in payloads:
        for row in _value_rows(payload):
            if isinstance(row, dict) and row.get("reportRefreshDate"):
                values.append(str(row["reportRefreshDate"]))
    return max(values) if values else ""


async def collect_copilot_usage(client, include_user_detail=False):
    evidence = _base_evidence("Microsoft Graph Microsoft 365 Copilot usage reports", "D7/D28/D90/D180")
    summary_paths = [
        "/v1.0/copilot/reports/getMicrosoft365CopilotUserCountSummary(period='{}',version='v2')".format(period)
        for period in COPILOT_PERIODS
    ]
    summary_results = await asyncio.gather(*[
        _get_report_payload(client, path) for path in summary_paths
    ])
    periods = {}
    payloads = []
    period_errors = {}
    for period, result in zip(COPILOT_PERIODS, summary_results):
        payload, error = result
        if payload is not None:
            payloads.append(payload)
            normalized = _normalize_copilot_period(payload, period)
            if normalized:
                periods[period] = normalized
        elif error:
            period_errors[period] = error

    # ALL is a provider-supported v2 request and is a useful fallback when an
    # individual period is temporarily omitted by the reporting service.
    missing_periods = [period for period in COPILOT_PERIODS if period not in periods]
    if missing_periods:
        all_payload, all_error = await _get_report_payload(
            client,
            "/v1.0/copilot/reports/getMicrosoft365CopilotUserCountSummary(period='ALL',version='v2')",
        )
        if all_payload is not None:
            payloads.append(all_payload)
            for period in missing_periods:
                normalized = _normalize_copilot_period(all_payload, period)
                if normalized:
                    periods[period] = normalized
                    period_errors.pop(period, None)
        elif all_error:
            for period in missing_periods:
                period_errors.setdefault(period, all_error)

    trend_payload, trend_error = await _get_report_payload(
        client,
        "/v1.0/copilot/reports/getMicrosoft365CopilotUserCountTrend(period='D28',version='v2')",
    )
    if trend_payload is not None:
        payloads.append(trend_payload)

    missing_periods = [period for period in COPILOT_PERIODS if period not in periods]
    partial_reason = "; ".join(
        "{}: {}".format(period, period_errors.get(period, "No report row returned"))
        for period in missing_periods
    )
    evidence.update({
        "available": bool(periods),
        "availability_status": (
            "available" if len(periods) == len(COPILOT_PERIODS) and not trend_error
            else "partial" if periods else "unavailable"
        ),
        "reason": partial_reason if periods else (partial_reason or "No report rows returned"),
        "periods": periods,
        "period_status": {
            period: {
                "available": period in periods,
                "reason": "" if period in periods else period_errors.get(period, "No report row returned"),
            }
            for period in COPILOT_PERIODS
        },
        "partial_errors": period_errors,
        "trend": _normalize_copilot_trend(trend_payload, "D28") if trend_payload else [],
        "trend_reason": trend_error,
        "refresh_date": _latest_refresh_date(payloads),
        "records_collected": len(periods),
        "pages_collected": len(payloads),
        "selected_period": next((period for period in ("D28", "D7", "D90", "D180") if period in periods), ""),
    })
    deployment_period = periods.get("D28") or periods.get("D7") or {}
    evidence["deployment_state"] = (
        "Not deployed" if evidence["available"] and deployment_period.get("enabled_users") == 0 else
        "Deployed" if evidence["available"] else "Unknown"
    )

    # Aggregate v2 engagement on every run; retain identities only on explicit opt-in.
    detail_payload, detail_error = await _get_report_payload(
        client, "/v1.0/copilot/reports/getMicrosoft365CopilotUsageUserDetail(period='D28',version='v2')",
    )
    user_rows = list(_value_rows(detail_payload))
    if not detail_error and not (isinstance(detail_payload, list) or
                                isinstance(detail_payload, dict) and isinstance(detail_payload.get('value'), list)):
        detail_error = 'Incomplete usage detail: the report rows were not returned.'
    next_link = detail_payload.get('@odata.nextLink') if isinstance(detail_payload, dict) else None
    seen_links = set()
    while next_link and not detail_error:
        from urllib.parse import urlsplit
        target = urlsplit(str(next_link))
        if (target.scheme != 'https' or target.netloc.lower() != 'graph.microsoft.com'
                or not target.path.startswith('/v1.0/copilot/reports/')
                or next_link in seen_links or len(seen_links) >= 1000):
            detail_error = 'Incomplete usage detail: an unsupported or repeated paging link was returned.'
            break
        seen_links.add(next_link)
        page, detail_error = await _get_report_payload(client, next_link)
        if not detail_error and not (isinstance(page, list) or
                                    isinstance(page, dict) and isinstance(page.get('value'), list)):
            detail_error = 'Incomplete usage detail: a page did not contain report rows.'
        user_rows.extend(_value_rows(page))
        next_link = page.get('@odata.nextLink') if isinstance(page, dict) else None
    from .copilot_activity_summary import summarize_activity
    evidence['engagement_summary'] = summarize_activity(user_rows, error=detail_error)
    evidence['user_detail_reason'] = detail_error
    evidence["user_detail"] = user_rows if include_user_detail else []
    return _set_freshness(evidence, 7)


def _normalize_m365_count_report(payload):
    """Unwrap the documented report envelope into dated count rows.

    Both M365 app-count endpoints return ``value[].userCounts[]`` in JSON. Older
    fixtures and CSV responses can be flat, so accept both shapes without ever
    manufacturing zeroes for a malformed envelope.
    """
    rows = []
    for root in _value_rows(payload):
        if not isinstance(root, dict):
            continue
        nested = root.get("userCounts")
        if isinstance(nested, list):
            for item in nested:
                if not isinstance(item, dict):
                    continue
                normalized = dict(item)
                normalized.setdefault("reportRefreshDate", root.get("reportRefreshDate", ""))
                normalized.setdefault("reportPeriod", root.get("reportPeriod", ""))
                rows.append(normalized)
        elif "reportDate" in root:
            rows.append(dict(root))
    rows.sort(key=lambda row: str(row.get("reportDate", row.get("reportRefreshDate", ""))))
    return rows


def _summarize_m365_count_trend(rows):
    """Summarize daily trend rows without treating the newest day as a 30-day total."""
    metadata = {"reportRefreshDate", "reportDate", "reportPeriod"}
    keys = sorted({key for row in rows for key in row if key not in metadata})
    summary = {}
    for key in keys:
        observations = []
        for row in rows:
            value = _optional_integer(row, key)
            if value is not None:
                observations.append((str(row.get("reportDate", "")), value))
        if not observations:
            continue
        active_dates = [date for date, value in observations if value > 0 and date]
        summary[key] = {
            "peak_daily_active_users": max(value for _, value in observations),
            "active_days": sum(1 for _, value in observations if value > 0),
            "latest_activity_date": max(active_dates) if active_dates else "",
            "latest_report_date": max((date for date, _ in observations if date), default=""),
        }
    return summary


def _m365_readiness_signal(app_summary, platform_summary):
    active_apps = sorted(
        ((name, values.get("peak_daily_active_users", 0)) for name, values in app_summary.items()),
        key=lambda item: (-item[1], item[0].lower()),
    )
    active_apps = [(name, value) for name, value in active_apps if value > 0]
    active_platforms = sorted(
        ((name, values.get("peak_daily_active_users", 0)) for name, values in platform_summary.items()),
        key=lambda item: (-item[1], item[0].lower()),
    )
    active_platforms = [(name, value) for name, value in active_platforms if value > 0]
    if not active_apps and not active_platforms:
        return "No Microsoft 365 app activity was reported on any day in the last 30 days."
    app_text = ", ".join(f"{name} {value}" for name, value in active_apps[:4])
    platform_text = ", ".join(f"{name} {value}" for name, value in active_platforms[:3])
    parts = []
    if app_text:
        parts.append(f"peak daily app users: {app_text}")
    if platform_text:
        parts.append(f"peak daily platform users: {platform_text}")
    return "The last-30-days trend showed " + "; ".join(parts) + "."


async def collect_m365_app_readiness(client):
    evidence = _base_evidence("Microsoft Graph Microsoft 365 Apps usage reports", "D30")
    counts_result, platforms_result = await asyncio.gather(
        _get_report_payload(client, "/v1.0/reports/getM365AppUserCounts(period='D30')"),
        _get_report_payload(client, "/v1.0/reports/getM365AppPlatformUserCounts(period='D30')"),
    )
    counts_payload, counts_error = counts_result
    platforms_payload, platforms_error = platforms_result
    count_rows = _normalize_m365_count_report(counts_payload)
    platform_rows = _normalize_m365_count_report(platforms_payload)
    app_activity_summary = _summarize_m365_count_trend(count_rows)
    platform_activity_summary = _summarize_m365_count_trend(platform_rows)
    partial_errors = {}
    if not count_rows:
        partial_errors["app_user_counts"] = counts_error or "No report rows returned"
    if not platform_rows:
        partial_errors["platform_user_counts"] = platforms_error or "No report rows returned"
    evidence.update({
        "available": bool(count_rows or platform_rows),
        "availability_status": (
            "available" if count_rows and platform_rows
            else "partial" if count_rows or platform_rows else "unavailable"
        ),
        "reason": "; ".join("{}: {}".format(key, value) for key, value in partial_errors.items()),
        "app_user_counts": count_rows,
        "platform_user_counts": platform_rows,
        "app_activity_summary": app_activity_summary,
        "platform_activity_summary": platform_activity_summary,
        "readiness_signal": _m365_readiness_signal(app_activity_summary, platform_activity_summary),
        "partial_errors": partial_errors,
        "refresh_date": _latest_refresh_date([payload for payload in (counts_payload, platforms_payload) if payload]),
        "records_collected": len(count_rows) + len(platform_rows),
        "pages_collected": int(bool(counts_payload)) + int(bool(platforms_payload)),
        "selected_period": "D30" if count_rows or platform_rows else "",
    })
    return _set_freshness(evidence, 7)


async def collect_license_coverage(client):
    """Resolve assigned tenant products to paid Copilot plans, without user identities.

    Counts describe assigned licenses, not active usage or successful provisioning.
    A subscription catalog failure leaves the total unknown rather than asserting
    that newer Copilot Business/bundle SKUs are unlicensed.
    """
    evidence = _base_evidence("Microsoft Graph user licensing", "Current")
    payload, error = await _get_all_json(
        client,
        "/v1.0/users",
        {"$select": "id,accountEnabled,assignedLicenses", "$top": "999"},
    )
    if payload is None:
        evidence["reason"] = error
        return evidence
    catalog, catalog_error = await _get_all_json(client, "/v1.0/subscribedSkus")
    catalog_valid = isinstance(catalog, dict) and isinstance(catalog.get("value"), list)
    catalog_rows = _value_rows(catalog) if catalog_valid else []
    catalog_by_id = {
        str(sku.get("skuId", "")).lower(): sku for sku in catalog_rows
        if isinstance(sku, dict) and sku.get("skuId")
    }
    paid_sku_ids = set(COPILOT_SKU_IDS)
    base_sku_ids = set()
    for sku_id, sku in catalog_by_id.items():
        plans = [plan for plan in (sku.get("servicePlans", []) or []) if isinstance(plan, dict)]
        plan_ids = {str(plan.get("servicePlanId", "")).lower() for plan in plans}
        plan_names = {str(plan.get("servicePlanName", "")).upper() for plan in plans}
        if plan_ids & COPILOT_SERVICE_PLAN_IDS or plan_names & COPILOT_SERVICE_PLAN_NAMES:
            paid_sku_ids.add(sku_id)
        if (plan_names & COPILOT_BASE_SERVICE_PLAN_NAMES
                or str(sku.get("skuPartNumber", "")).upper() in COPILOT_BASE_SKU_NAMES):
            base_sku_ids.add(sku_id)

    eligible = 0
    copilot_licensed = 0
    copilot_licensed_eligible = 0
    enabled = 0
    without_recognized_base = 0
    unresolved_sku_ids = set()
    for row in _value_rows(payload):
        if not isinstance(row, dict) or row.get("accountEnabled") is False:
            continue
        enabled += 1
        sku_ids = {
            str(item.get("skuId", "")).lower()
            for item in (row.get("assignedLicenses", []) or [])
            if isinstance(item, dict) and item.get("skuId")
        }
        unresolved_sku_ids.update(sku_ids - catalog_by_id.keys() - COPILOT_SKU_IDS)
        has_copilot = bool(sku_ids & paid_sku_ids)
        has_base = bool(sku_ids & base_sku_ids)
        if has_copilot:
            copilot_licensed += 1
        if has_base:
            eligible += 1
            copilot_licensed_eligible += int(has_copilot)
        elif sku_ids:
            without_recognized_base += 1

    catalog_complete = catalog_valid and not catalog_error and not unresolved_sku_ids
    users_complete = not error
    coverage_complete = catalog_complete and users_complete and not without_recognized_base
    reasons = []
    if error:
        reasons.append("User licensing collection is incomplete: " + error)
    if not catalog_valid or catalog_error:
        reasons.append("Subscription catalog unavailable or incomplete: " + (catalog_error or "Unreadable response"))
    if unresolved_sku_ids:
        reasons.append("{} assigned SKU(s) could not be resolved from the subscription catalog".format(len(unresolved_sku_ids)))
    if without_recognized_base:
        reasons.append("{} enabled licensed user(s) have no recognized qualifying base plan; eligibility is not established".format(without_recognized_base))
    evidence.update({
        "available": True,
        "availability_status": "available" if coverage_complete else "partial",
        "reason": "; ".join(reasons),
        "total_users": len(_value_rows(payload)),
        "enabled_users": enabled,
        "eligible_users": eligible,
        "copilot_licensed_users": copilot_licensed if catalog_complete and users_complete else None,
        "known_copilot_licensed_users": copilot_licensed,
        "copilot_license_coverage": (
            round(copilot_licensed_eligible * 100 / eligible, 2)
            if eligible and coverage_complete else None
        ),
        "coverage_population": "enabled users with recognized qualifying Microsoft 365 workload/base plans (tenant-derived eligibility estimate)",
        "license_detection_basis": "assigned paid Microsoft 365 Copilot SKU or service-plan identity; includes qualifying Business offers and bundles, excludes Chat-only plans",
        "subscription_catalog_available": catalog_valid and not catalog_error,
        "unresolved_assigned_sku_count": len(unresolved_sku_ids),
        "users_without_recognized_base_license": without_recognized_base,
        "refresh_date": datetime.now(timezone.utc).date().isoformat(),
        "refresh_basis": "assessment collection time",
        "freshness": "Current",
        "records_collected": len(_value_rows(payload)),
        "pages_collected": (payload.get("_collection_metadata", {}) or {}).get("pages_collected", 1),
        "truncated": bool(error) or (payload.get("_collection_metadata", {}) or {}).get("truncated", False),
        "selected_period": "Current",
    })
    from .copilot_admin_review import subscription_capacity
    evidence['copilot_subscription_capacity'] = subscription_capacity(
        catalog_rows, paid_sku_ids, complete=catalog_valid and not catalog_error)
    return evidence


def _read_csv_rows(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_copilot_dashboard_export(path):
    evidence = _base_evidence("Microsoft Copilot Dashboard export")
    if not path:
        evidence["availability_status"] = "not_requested"
        evidence["reason"] = (
            "Optional export not supplied. Export Copilot Dashboard data from Viva Insights "
            "and rerun with --copilot-dashboard-export PATH."
        )
        return evidence
    source = Path(path)
    if not source.exists() or not source.is_file():
        evidence["availability_status"] = "unavailable"
        evidence["reason"] = "Copilot Dashboard export was not found: {}".format(path)
        return evidence
    try:
        rows = _read_csv_rows(source)
    except Exception as exc:
        evidence["reason"] = "Unable to read Copilot Dashboard export: {}".format(type(exc).__name__)
        return evidence

    lowered = [{str(key).strip().lower(): value for key, value in row.items()} for row in rows]
    returning_columns = set()
    action_columns = set()
    date_values = []
    for row in lowered:
        for key, value in row.items():
            if "returning" in key:
                returning_columns.add(key)
            if "action" in key or "prompt" in key:
                action_columns.add(key)
            if "date" in key and _parse_date(value):
                date_values.append(str(value)[:10])

    def truthy(value):
        return str(value or "").strip().lower() in {"1", "true", "yes", "y"}

    def numeric(value):
        try:
            return float(str(value or "").replace(",", "").strip())
        except ValueError:
            return None

    returning_users = sum(
        1 for row in lowered if any(truthy(row.get(column)) for column in returning_columns)
    )
    if returning_columns:
        reported_returning = [
            numeric(row.get(column)) for row in lowered for column in returning_columns
            if numeric(row.get(column)) is not None
        ]
        if reported_returning and not returning_users:
            returning_users = int(sum(reported_returning))

    intensity = {}
    for row in lowered:
        for key, value in row.items():
            if "intensity" not in key or not str(value or "").strip():
                continue
            band = str(value).strip()
            intensity[band] = intensity.get(band, 0) + 1

    detailed_actions = {}
    for column in action_columns:
        values = [numeric(row.get(column)) for row in lowered]
        numeric_values = [value for value in values if value is not None]
        if numeric_values:
            detailed_actions[column] = round(sum(numeric_values), 2)

    unlicensed_chat_activity = 0
    unlicensed_columns = {
        key for row in lowered for key in row
        if "unlicensed" in key and ("chat" in key or "copilot" in key)
    }
    for row in lowered:
        for column in unlicensed_columns:
            value = numeric(row.get(column))
            if value is not None:
                unlicensed_chat_activity += value
            elif truthy(row.get(column)):
                unlicensed_chat_activity += 1
    evidence.update({
        "available": bool(rows),
        "availability_status": "available" if rows else "partial",
        "reason": "" if rows else "The export contained no data rows",
        "records": len(rows),
        "records_collected": len(rows),
        "pages_collected": 1,
        "truncated": False,
        "returning_users": returning_users,
        "returning_metrics_present": bool(returning_columns),
        "action_metrics_present": bool(action_columns),
        "usage_intensity": intensity,
        "detailed_actions": detailed_actions,
        "unlicensed_copilot_chat_activity": round(unlicensed_chat_activity, 2),
        "unlicensed_chat_metrics_present": bool(unlicensed_columns),
        "columns": sorted(set().union(*(row.keys() for row in lowered))) if lowered else [],
        "refresh_date": max(date_values) if date_values else datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).date().isoformat(),
        "source_file": source.name,
        "refresh_basis": "provider date in export" if date_values else "source file modified time",
    })
    return _set_freshness(evidence, 30)


async def collect_shadow_ai_usage(client):
    evidence = _base_evidence("Microsoft Defender for Cloud Apps discovery (Microsoft Graph beta)", "P30D")
    streams_payload, error = await _get_json(
        client, "/beta/security/dataDiscovery/cloudAppDiscovery/uploadedStreams"
    )
    streams = _value_rows(streams_payload)
    if streams_payload is None:
        evidence["availability_status"] = "unavailable"
        evidence["reason"] = error
        return evidence
    if not streams:
        evidence["availability_status"] = "unavailable"
        evidence["reason"] = (
            "Cloud Discovery has no uploaded or continuous data stream. Enable Defender for "
            "Endpoint forwarding, a Cloud Discovery log source, or Global Secure Access Shadow AI discovery, then rerun."
        )
        return evidence

    results = await asyncio.gather(*[
        _get_json(
            client,
            "/beta/security/dataDiscovery/cloudAppDiscovery/uploadedStreams/{}/aggregatedAppsDetails(period=duration'P30D')".format(stream.get("id")),
        ) for stream in streams if stream.get("id")
    ])
    apps = []
    errors = []
    for payload, request_error in results:
        if payload is None:
            if request_error:
                errors.append(request_error)
            continue
        for row in _value_rows(payload):
            category = str(row.get("category", "") or "")
            name = str(row.get("displayName", row.get("name", "")) or "")
            searchable = "{} {}".format(category, name).lower()
            if "generative ai" not in searchable and not any(
                marker in searchable for marker in ("chatgpt", "openai", "claude", "anthropic", "cursor", "deepseek", "gemini")
            ):
                continue
            apps.append({
                "display_name": name or "Unknown AI application",
                "category": category or "Generative AI",
                "risk_rating": row.get("riskScore", row.get("riskRating")),
                "active_users": row.get("userCount", row.get("activeUsers")),
                "traffic_bytes": _integer(row, "uploadNetworkTrafficInBytes")
                + _integer(row, "downloadNetworkTrafficInBytes"),
                "last_seen": row.get("lastSeenDateTime", row.get("lastSeen", "")),
            })
    last_seen = [str(app["last_seen"])[:10] for app in apps if _parse_date(app.get("last_seen"))]
    stream_dates = []
    for stream in streams:
        for key in ("lastUpdatedDateTime", "lastDataReceivedDateTime", "lastUploadDateTime"):
            if _parse_date(stream.get(key)):
                stream_dates.append(str(stream[key])[:10])
    evidence.update({
        "available": True,
        "availability_status": "partial" if errors else "available",
        "reason": "" if apps else "Cloud Discovery is available; no generative AI applications were returned",
        "applications": apps,
        "application_count": len(apps),
        "refresh_date": max(last_seen + stream_dates) if (last_seen or stream_dates) else "",
        "partial_errors": errors,
        "records_collected": len(apps),
        "pages_collected": 1 + len(results),
        "truncated": False,
    })
    return _set_freshness(evidence, 7)


async def collect_ai_usage(include_user_detail=False, copilot_dashboard_export=None, preview_collectors="none"):
    # Offline CSV parsing does not require the Graph/Azure or HTTP dependencies.
    import httpx
    from .get_graph_client import get_shared_credential

    credential = get_shared_credential()
    token = credential.get_token("https://graph.microsoft.com/.default")
    async with httpx.AsyncClient(
        base_url=GRAPH_BASE,
        headers={"Authorization": "Bearer {}".format(token.token), "Accept": "application/json"},
        timeout=60.0,
        follow_redirects=True,
    ) as client:
        copilot_usage, app_readiness, license_coverage = await asyncio.gather(
            collect_copilot_usage(client, include_user_detail=include_user_detail),
            collect_m365_app_readiness(client),
            collect_license_coverage(client),
        )
        if preview_collectors in {"shadow-ai", "all"}:
            shadow_ai = await collect_shadow_ai_usage(client)
        else:
            shadow_ai = _base_evidence("Microsoft Defender for Cloud Apps discovery (preview)", "P30D")
            shadow_ai["availability_status"] = "not_requested"
            shadow_ai["reason"] = "Preview Shadow AI collector was not enabled"

    return {
        "copilot_usage": copilot_usage,
        "m365_app_readiness": app_readiness,
        "license_coverage": license_coverage,
        "copilot_dashboard": load_copilot_dashboard_export(copilot_dashboard_export),
        "shadow_ai_usage": shadow_ai,
    }

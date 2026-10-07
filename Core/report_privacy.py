"""Microsoft 365 report privacy setting (concealed user names).

When "Display concealed user, group, and site names in all reports" is on,
Microsoft 365 and Copilot usage reports return pseudonymous identifiers instead
of user names. Aggregate counts are unaffected, but per-user adoption evidence
cannot be tied to people. ``GET /v1.0/admin/reportSettings`` reads the setting
with the ``ReportSettings.Read.All`` application permission.
"""

from .auth_plan import credential_kind_for_path, graph_credential_kind
from .new_recommendation import new_recommendation

AUTH_PATH = "graph_report_settings"
SOURCE = "Microsoft Graph report settings"


def _unavailable(reason, status_code=None, credential_type=""):
    state = {
        "available": False, "availability_status": "unavailable", "records_collected": 0,
        "pages_collected": 0, "truncated": False, "reason": reason, "source": SOURCE,
        "display_concealed_names": None, "auth_path_id": AUTH_PATH,
        "credential_type": credential_type, "coverage": "full", "evidence_quality": "standard",
    }
    if status_code:
        state["status_code"] = status_code
    return state


async def collect_report_settings(graph_client):
    """Return report-settings evidence; never raises."""
    credential_type = credential_kind_for_path(AUTH_PATH, {"graph_credential": graph_credential_kind()})
    try:
        payload = await graph_client.get_json("/v1.0/admin/reportSettings")
    except Exception as exc:
        from .access_errors import status_code_of
        status_code = status_code_of(exc)
        if status_code == 403:
            reason = ("Microsoft Graph denied report settings (HTTP 403). Grant and consent the ReportSettings.Read.All "
                      "application permission to confirm whether usage reports conceal user names.")
        else:
            reason = f"Report settings were not returned ({type(exc).__name__})."
        return _unavailable(reason, status_code, credential_type)
    payload = payload.get("value", payload) if isinstance(payload, dict) and isinstance(payload.get("value"), dict) else (payload or {})
    concealed = payload.get("displayConcealedNames") if isinstance(payload, dict) else None
    if not isinstance(concealed, bool):
        return _unavailable("Microsoft Graph returned report settings without displayConcealedNames.", None, credential_type)
    return {
        "available": True, "availability_status": "available", "records_collected": 1,
        "pages_collected": 1, "truncated": False, "reason": "", "source": SOURCE,
        "display_concealed_names": concealed, "auth_path_id": AUTH_PATH,
        "credential_type": credential_type, "coverage": "full", "evidence_quality": "standard",
    }


def build_report_privacy_recommendations(report_settings):
    """Reference finding when usage reports conceal identities; no decision effect."""
    if not isinstance(report_settings, dict) or report_settings.get("display_concealed_names") is not True:
        return []
    return [new_recommendation(
        service="M365",
        feature="Usage report privacy setting",
        observation=(
            "Microsoft 365 usage reports conceal user, group and site names. Copilot and workload usage "
            "details use pseudonymous identifiers; tenant-wide and aggregate counts are unaffected."
        ),
        recommendation=(
            "Keep the setting if the privacy owner requires it and use aggregate adoption metrics. If named, per-user "
            "adoption evidence is needed for the pilot (for example to follow up with inactive licensed users), ask the "
            "privacy owner to approve turning off 'Display concealed user, group, and site names in all reports' in "
            "Microsoft 365 admin center > Settings > Org settings > Reports, for the assessment period only."
        ),
        link_text="Report privacy settings",
        link_url="https://learn.microsoft.com/troubleshoot/microsoft-365/admin/miscellaneous/reports-show-anonymous-user-name",
        priority="Low",
        status="Insight",
        disposition="Reference",
        finding_key="m365.reports.concealed_names",
        evidence_key="ai_usage_detail",
        evidence_basis="Tenant configuration",
        confidence="High",
    )]

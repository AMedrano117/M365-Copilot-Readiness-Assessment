"""Normalize SharePoint sharing configuration and create evidence-based findings."""

from datetime import datetime, timezone

from .new_recommendation import (
    CATEGORY_SCAN_COVERAGE,
    NOT_ASSESSED_STATUS,
    new_recommendation,
)


ANYONE_VALUES = {"externaluserandguestsharing", "anonymousaccess"}


def _text(value):
    return str(value or "").strip()


def _bool(value):
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"true", "yes", "1", "enabled"}


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _date(value):
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def summarize_sharepoint_governance(payload):
    payload = payload if isinstance(payload, dict) else {}
    tenant = payload.get("tenant") or {}
    sites = payload.get("sites") or {}
    reports = payload.get("dag_reports") or {}
    activity = payload.get("dag_activity_data") or {}
    settings = tenant.get("settings") or {}
    site_items = sites.get("items") or []
    report_items = reports.get("reports") or []

    anyone_sites = [row for row in site_items if _text(row.get("SharingCapability")).lower() in ANYONE_VALUES]
    completed_reports = [row for row in report_items if _text(row.get("Status")).lower() in {"completed", "complete", "succeeded"}]
    now = datetime.now(timezone.utc)
    recent_completed = []
    stale_completed = []
    for row in completed_reports:
        completed = next((_date(row.get(key)) for key in (
            "CompletedDateTime", "ReportEndTime", "EndTime", "CreatedDateTime",
            "TriggeredDateTime", "ReportStartTime", "StartTime",
        ) if _date(row.get(key))), None)
        if completed and (now - completed).days <= 30:
            recent_completed.append(row)
        else:
            stale_completed.append(row)
    running_reports = [row for row in report_items if _text(row.get("Status")).lower() in {
        "started", "notstarted", "running", "inprogress", "queued", "inqueue",
    }]

    def matches(rows, entity, workload=""):
        return [row for row in rows if (
            _text(row.get("RequestedEntity") or row.get("ReportEntity")).lower() == entity.lower()
            and (not workload or _text(row.get("RequestedWorkload") or row.get("Workload")).lower() == workload.lower())
        )]

    coverage_specs = [
        ("SharePoint permission state", [("PermissionedUsers", "SharePoint")]),
        ("OneDrive permission state", [("PermissionedUsers", "OneDriveForBusiness")]),
        ("Everyone and Everyone except external users", [("Everyone", ""), ("EveryoneExceptExternalUsers", "")]),
        ("Anyone and guest sharing links", [("SharingLinks_Anyone", "SharePoint"), ("SharingLinks_Guests", "SharePoint")]),
        ("Everyone except external users activity", [("EveryoneExceptExternalUsersAtSite", "SharePoint"), ("EveryoneExceptExternalUsersForItems", "SharePoint")]),
    ]
    dag_coverage = []
    for label, requirements in coverage_specs:
        if all(matches(recent_completed, entity, workload) for entity, workload in requirements):
            state = "Recent"
        elif any(matches(completed_reports, entity, workload) for entity, workload in requirements):
            state = "Stale or incomplete"
        elif any(matches(running_reports, entity, workload) for entity, workload in requirements):
            state = "Running"
        else:
            state = "Missing"
        dag_coverage.append({"report": label, "status": state})

    copilot_activity = next((
        row for row in (activity.get("items") or [])
        if _text(row.get("RequestedEntity") or row.get("ReportEntity")).lower() == "copilotappinsights"
    ), None)
    dag_coverage.append({
        "report": "Copilot app insight activity data",
        "status": _text((copilot_activity or {}).get("Status") or (copilot_activity or {}).get("State")) or "Not available",
    })
    return {
        "available": bool(tenant.get("available")),
        "reason": tenant.get("reason") or payload.get("reason", ""),
        "source": payload.get("source", "SharePoint Online Management Shell"),
        "authentication": payload.get("authentication", ""),
        "settings": settings,
        "sites_available": bool(sites.get("available")),
        "sites": site_items,
        "site_count": len(site_items),
        "anyone_site_count": len(anyone_sites),
        "dag_available": bool(reports.get("available")),
        "dag_reports": report_items,
        "dag_completed_count": len(completed_reports),
        "dag_recent_completed_count": len(recent_completed),
        "dag_stale_completed_count": len(stale_completed),
        "dag_running_count": len(running_reports),
        "dag_coverage": dag_coverage,
        "dag_reason": reports.get("reason", ""),
        "dag_activity_available": bool(activity.get("available")),
        "dag_activity_states": activity.get("items") or [],
        "dag_activity_reason": activity.get("reason", ""),
        "collection_status": payload.get("collection_status") or {},
    }


def build_sharepoint_recommendations(governance):
    governance = summarize_sharepoint_governance(governance)
    if not governance.get("available"):
        return [new_recommendation(
            service="M365",
            feature="SharePoint sharing and oversharing assessment",
            observation="SharePoint tenant sharing settings were not collected; permissive sharing defaults and Data Access Governance report status remain unverified.",
            recommendation="Install the SharePoint Online Management Shell and rerun. Use a SharePoint application certificate for unattended collection, or allow the SharePoint browser sign-in. The existing Graph client secret cannot read these settings.",
            link_text="Connect to SharePoint Online",
            link_url="https://learn.microsoft.com/powershell/module/microsoft.online.sharepoint.powershell/connect-sposervice",
            priority="High",
            status=NOT_ASSESSED_STATUS,
            category=CATEGORY_SCAN_COVERAGE,
            finding_key="sharepoint.governance.not_assessed",
            evidence_key="sharepoint_governance_detail",
            evidence_basis="Collection status",
            confidence="High",
        )]

    settings = governance.get("settings") or {}
    sharing = _text(settings.get("SharingCapability"))
    onedrive_sharing = _text(settings.get("OneDriveSharingCapability"))
    default_link = _text(settings.get("DefaultSharingLinkType"))
    file_link = _text(settings.get("FileAnonymousLinkType"))
    folder_link = _text(settings.get("FolderAnonymousLinkType"))
    expiration = _int(settings.get("RequireAnonymousLinksExpireInDays"), 0)
    permits_anyone = sharing.lower() in ANYONE_VALUES or onedrive_sharing.lower() in ANYONE_VALUES
    anonymous_default = default_link.lower() in {"anonymousaccess", "anonymous"}
    edit_anyone = file_link.lower() == "edit" or folder_link.lower() == "edit"
    recommendations = []

    if permits_anyone and anonymous_default and expiration <= 0:
        recommendations.append(new_recommendation(
            service="M365",
            feature="SharePoint and OneDrive sharing defaults",
            observation=(
                f"SharePoint sharing is {sharing or 'not returned'} and OneDrive sharing is {onedrive_sharing or 'not returned'}. "
                f"Anyone links are the default, no tenant-wide expiration is enforced"
                + (", and anonymous file or folder links permit editing." if edit_anyone else ".")
            ),
            recommendation="Change the default link to Specific people, require an expiration for Anyone links, and use view-only anonymous links unless a documented business case requires editing. Review site exceptions before changing the tenant maximum.",
            link_text="Manage external sharing",
            link_url="https://learn.microsoft.com/sharepoint/turn-external-sharing-on-or-off",
            priority="High",
            status="Action Required",
            finding_key="sharepoint.sharing.permissive_anonymous_defaults",
            evidence_key="sharepoint_governance_detail",
            evidence_summary="Tenant and site sharing settings were collected from SharePoint Online.",
            impact_area="Content access & grounding",
            ai_applicability="AI grounded in SharePoint or OneDrive can surface content to anyone the requesting user can access; permissive links increase unintended access paths.",
            evidence_basis="Tenant configuration",
            confidence="High",
        ))
    elif permits_anyone:
        recommendations.append(new_recommendation(
            service="M365",
            feature="SharePoint and OneDrive external sharing",
            observation=f"The tenant permits Anyone sharing. {governance.get('anyone_site_count', 0)} collected site(s) also permit Anyone links.",
            recommendation="Confirm that Anyone sharing is required, review the sites where it is allowed, and use a recent Data Access Governance sharing-link report to measure actual exposure before broad AI deployment.",
            link_text="External sharing overview",
            link_url="https://learn.microsoft.com/sharepoint/external-sharing-overview",
            priority="Medium",
            status="Attention Required",
            finding_key="sharepoint.sharing.anyone_enabled",
            evidence_key="sharepoint_governance_detail",
            evidence_basis="Tenant and site configuration",
            confidence="High",
        ))

    if _bool(settings.get("LegacyAuthProtocolsEnabled")):
        recommendations.append(new_recommendation(
            service="M365",
            feature="SharePoint legacy authentication",
            observation="SharePoint is configured to permit legacy authentication protocols. This setting alone does not prove that legacy sign-ins are occurring or bypassing Entra controls.",
            recommendation="Validate recent Entra sign-in evidence and effective Conditional Access coverage, then disable SharePoint legacy authentication after confirming that required clients and integrations use modern authentication.",
            link_text="Block legacy authentication",
            link_url="https://learn.microsoft.com/entra/identity/conditional-access/block-legacy-authentication",
            priority="Medium",
            status="Attention Required",
            finding_key="sharepoint.authentication.legacy_permitted",
            evidence_key="sharepoint_governance_detail",
            evidence_basis="Tenant configuration",
            confidence="Medium",
        ))

    required_dag_gaps = [
        row for row in (governance.get("dag_coverage") or [])[:5]
        if row.get("status") != "Recent"
    ]
    if not governance.get("dag_available") or required_dag_gaps:
        activity_states = governance.get("dag_activity_states") or []
        not_started = [
            _text(row.get("RequestedEntity") or row.get("ReportEntity"))
            for row in activity_states
            if _text(row.get("Status") or row.get("State")).lower() in {"notinitiated", "paused", "disabled"}
        ]
        collection_note = (
            " Recent-activity data collection is not ready for: " + ", ".join(filter(None, not_started)) + "."
            if not_started else ""
        )
        recommendations.append(new_recommendation(
            service="M365",
            feature="SharePoint Data Access Governance reports",
            observation=(
                "SharePoint Data Access Governance evidence is incomplete for: "
                + ", ".join(f"{row['report']} ({row['status'].lower()})" for row in required_dag_gaps)
                + ". Actual broad permissions and sharing-link activity remain unverified."
                + collection_note
            ),
            recommendation=(
                "If SharePoint Advanced Management is licensed, open SharePoint admin center > Reports > Data access governance. "
                "Enable activity-data collection where it is Not initiated or Paused, then run Permissioned users for SharePoint and OneDrive, "
                "Everyone/Everyone except external users item-level reports, and recent Anyone/guest sharing-link reports. "
                "Do not wait in this tool: Microsoft's first permission-state report can take up to five days; later runs generally complete within 24 hours. "
                "Rerun this assessment after the reports show Completed; it will reuse and download the results."
            ),
            link_text="Data Access Governance reports",
            link_url="https://learn.microsoft.com/sharepoint/data-access-governance-reports",
            priority="High",
            status=NOT_ASSESSED_STATUS,
            category=CATEGORY_SCAN_COVERAGE,
            finding_key="sharepoint.dag.not_assessed",
            evidence_key="sharepoint_governance_detail",
            evidence_basis="Report inventory",
            confidence="High",
        ))
    return recommendations

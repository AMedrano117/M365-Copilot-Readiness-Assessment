"""Normalize SharePoint sharing configuration and create evidence-based findings."""

import re
from datetime import datetime, timezone

from .new_recommendation import (
    CATEGORY_SCAN_COVERAGE,
    NOT_ASSESSED_STATUS,
    new_recommendation,
)


ANYONE_VALUES = {"externaluserandguestsharing", "anonymousaccess"}

# SharePoint Online PowerShell serializes these enums as numbers; Microsoft
# Graph returns names. Members follow the Set-SPOTenant documentation (see
# customer_report._sharing_setting_value for the verified display mapping).
_CAPABILITY = {0: "disabled", 1: "externalusersharingonly", 2: "externaluserandguestsharing", 3: "existingexternalusersharingonly"}
_LINK_PERMISSION = {0: "none", 1: "view", 2: "edit"}
SHARING_ENUMS = {
    "SharingCapability": _CAPABILITY, "OneDriveSharingCapability": _CAPABILITY,
    "DefaultSharingLinkType": {0: "none", 1: "direct", 2: "internal", 3: "anonymousaccess"},
    "FileAnonymousLinkType": _LINK_PERMISSION, "FolderAnonymousLinkType": _LINK_PERMISSION,
    "DefaultLinkPermission": _LINK_PERMISSION,
}
SHARING_LABELS = {
    "disabled": "off", "externalusersharingonly": "new and existing guests",
    "externaluserandguestsharing": "Anyone links and guests", "existingexternalusersharingonly": "existing guests only",
    "direct": "specific people", "specificpeople": "specific people", "internal": "people in the organization",
    "anonymousaccess": "Anyone", "none": "the most permissive allowed", "view": "view", "edit": "edit",
}


def setting_name(key, value):
    """Lowercase enum name for a SharePoint sharing setting given as a name or a number."""
    text = _text(value) if not isinstance(value, bool) else str(value)
    names = SHARING_ENUMS.get(key)
    if names and not isinstance(value, bool) and re.fullmatch(r"[+-]?\d+", text):
        return names.get(int(text), text.lower())
    return text.lower()


def setting_label(key, value):
    name = setting_name(key, value)
    return SHARING_LABELS.get(name, name or "not returned")


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


def merge_sharepoint_payloads(graph_payload, spo_payload):
    """Combine the Graph baseline with SharePoint administration results.

    SharePoint administration (certificate or delegated) is authoritative for
    the settings it returns. Graph remains the baseline when administration was
    unavailable, and its dataset state is kept either way. A disagreement on a
    shared setting is recorded as a conflict instead of silently choosing one.
    """
    graph_payload = graph_payload if isinstance(graph_payload, dict) else {}
    spo_payload = spo_payload if isinstance(spo_payload, dict) else {}
    graph_tenant = graph_payload.get("tenant") or {}
    spo_tenant = spo_payload.get("tenant") or {}
    states = {}
    states.update(graph_payload.get("collection_status") or {})
    states.update(spo_payload.get("collection_status") or {})
    if spo_tenant.get("available"):
        merged = dict(spo_payload)
        merged["tenant"] = dict(spo_tenant)
        conflicts = []
        graph_settings = graph_tenant.get("settings") or {}
        for name, value in graph_settings.items():
            spo_value = (spo_tenant.get("settings") or {}).get(name)
            if spo_value is not None and _text(spo_value).lower() != _text(value).lower():
                conflicts.append({"setting": name, "sharepoint_admin": spo_value, "graph": value})
        if conflicts:
            merged["conflicts"] = conflicts
        if graph_tenant.get("available"):
            merged["graph_settings"] = graph_settings
    elif graph_tenant.get("available"):
        merged = dict(graph_payload)
        # Keep any SharePoint administration datasets that did return data.
        for name in ("sites", "dag_reports", "dag_activity_data"):
            if (spo_payload.get(name) or {}).get("available"):
                merged[name] = spo_payload[name]
        if spo_payload:
            merged["administration_reason"] = spo_payload.get("reason") or spo_tenant.get("reason") or ""
            for key in ("configuration_required", "admin_url", "exported_files"):
                if spo_payload.get(key):
                    merged[key] = spo_payload[key]
    else:
        merged = dict(spo_payload or graph_payload)
        reasons = [str(value).strip().rstrip(".") + "." for value in (spo_payload.get("reason"), graph_payload.get("reason")) if value]
        if reasons:
            merged["reason"] = " ".join(dict.fromkeys(reasons))
    merged["collection_status"] = states
    tenant = merged.get("tenant") or {}
    administration = [(merged.get(name) or {}).get("available") for name in ("sites", "dag_reports")]
    merged["available"] = bool(tenant.get("available")) or any(administration)
    partial = tenant.get("coverage") == "partial" or not all(administration) or any(
        not state.get("available") for state in states.values() if isinstance(state, dict))
    merged["availability_status"] = ("partial" if merged["available"] and partial else
                                     "available" if merged["available"] else
                                     merged.get("availability_status") or "unavailable")
    return merged


def summarize_sharepoint_governance(payload):
    payload = payload if isinstance(payload, dict) else {}
    tenant = payload.get("tenant") or {}
    sites = payload.get("sites") or {}
    reports = payload.get("dag_reports") or {}
    activity = payload.get("dag_activity_data") or {}
    settings = tenant.get("settings") or {}
    site_items = sites.get("items") or []
    report_items = reports.get("reports") or []

    anyone_sites = [row for row in site_items if setting_name("SharingCapability", row.get("SharingCapability")) in ANYONE_VALUES]
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
        "availability_status": payload.get("availability_status", "available" if tenant.get("available") else "unavailable"),
        "reason": tenant.get("reason") or payload.get("reason", ""),
        "configuration_required": payload.get("configuration_required", ""),
        "source": payload.get("source", "SharePoint Online Management Shell"),
        "authentication": payload.get("authentication", ""),
        "coverage": tenant.get("coverage", "full" if tenant.get("available") else ""),
        "unavailable_fields": tenant.get("unavailable_fields") or [],
        "administration_reason": payload.get("administration_reason", ""),
        "conflicts": payload.get("conflicts") or [],
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
        "copilot_content_controls": payload.get("copilot_content_controls") or {},
    }


def build_copilot_content_control_recommendations(governance):
    """Reference rows for SharePoint controls that scope what Copilot can ground on.

    These describe configuration only; they never change the readiness decision.
    """
    rows = []
    settings = governance.get("settings") or {}
    controls = governance.get("copilot_content_controls") or {}
    mode = _text(controls.get("restricted_search_mode"))
    if controls.get("available") and mode:
        enabled = mode.lower() == "enabled"
        allowed = controls.get("restricted_search_allowed_sites")
        allowed_text = f" with {allowed} allowed site(s)" if isinstance(allowed, int) else ""
        rows.append(new_recommendation(
            service="M365",
            feature="Restricted SharePoint Search",
            observation=(f"Restricted SharePoint Search is enabled{allowed_text}. Organization-wide search and Copilot are limited to "
                         "allowed sites plus content users already own or frequently use."
                         if enabled else
                         "Restricted SharePoint Search is not enabled. Copilot can ground on any content each user can already access."),
            recommendation=("Keep it as a temporary guardrail while oversharing is remediated, then plan to replace it with permission cleanup "
                            "and Restricted Content Discovery for specific sites."
                            if enabled else
                            "Fix oversharing first. If remediation cannot finish before the pilot, consider Restricted Content Discovery for "
                            "the riskiest sites or Restricted SharePoint Search as a temporary tenant-wide guardrail."),
            link_text="Restricted SharePoint Search",
            link_url="https://learn.microsoft.com/sharepoint/restricted-sharepoint-search",
            priority="Low", status="Insight", disposition="Reference",
            finding_key="sharepoint.copilot.restricted_search",
            evidence_key="sharepoint_governance_detail", evidence_basis="Tenant configuration", confidence="High",
        ))
    sites = governance.get("sites") or []
    if governance.get("sites_available") and any("RestrictContentOrgWideSearch" in row for row in sites):
        restricted = [row for row in sites if _bool(row.get("RestrictContentOrgWideSearch"))]
        rows.append(new_recommendation(
            service="M365",
            feature="Restricted Content Discovery",
            observation=(f"{len(restricted)} of {len(sites)} collected site(s) use Restricted Content Discovery, which keeps their content "
                         "out of Copilot and organization-wide search results for users who do not already use it."),
            recommendation=("Review whether sites holding sensitive content that is not yet permission-remediated should use Restricted "
                            "Content Discovery during the pilot. It hides content from discovery; it does not change access."),
            link_text="Restricted Content Discovery",
            link_url="https://learn.microsoft.com/sharepoint/restricted-content-discovery",
            priority="Low", status="Insight", disposition="Reference",
            finding_key="sharepoint.copilot.restricted_content_discovery",
            evidence_key="sharepoint_governance_detail", evidence_basis="Site configuration", confidence="High",
        ))
    if "EnableRestrictedAccessControl" in settings:
        enabled = _bool(settings.get("EnableRestrictedAccessControl"))
        rows.append(new_recommendation(
            service="M365",
            feature="Restricted access control",
            observation=("Restricted access control is enabled for the tenant; sites can limit access to members of specified groups."
                         if enabled else "Restricted access control is not enabled for the tenant."),
            recommendation="" if enabled else (
                "If SharePoint Advanced Management is licensed, consider restricted access control for sites whose content must stay "
                "with a defined group regardless of sharing, before Copilot is broadly enabled."),
            link_text="Restricted access control",
            link_url="https://learn.microsoft.com/sharepoint/restricted-access-control",
            priority="Low", status="Insight", disposition="Reference",
            finding_key="sharepoint.copilot.restricted_access_control",
            evidence_key="sharepoint_governance_detail", evidence_basis="Tenant configuration", confidence="High",
        ))
    return rows


def build_sharepoint_recommendations(governance):
    governance = summarize_sharepoint_governance(governance)
    if not governance.get("available"):
        excluded = governance.get('availability_status') == 'not_requested'
        needs_admin_url = governance.get('configuration_required') == 'SHAREPOINT_ADMIN_URL'
        if excluded:
            next_step = "Supply supported customer SAM/DAG exports for their covered access evidence and retain a dated owner review or saved configuration for tenant sharing settings. Exports do not reconstruct every excluded administrative check."
        elif needs_admin_url:
            next_step = "Open the target tenant's SharePoint admin center and copy its HTTPS origin. Set SHAREPOINT_ADMIN_URL in the selected environment file or pass --sharepoint-admin-url, then rerun preflight and collection. The initial onmicrosoft.com domain does not verify the SharePoint hostname."
        else:
            next_step = ("Grant the Microsoft Graph application permission SharePointTenantSettings.Read.All for the "
                         "tenant sharing baseline (works with the existing client secret). For default link types, "
                         "anonymous link expiry, per-site settings and Data Access Governance reports, also run setup "
                         "with -EnableSharePointAppOnly (certificate) or allow the SharePoint administrator browser sign-in.")
        return [new_recommendation(
            service="M365",
            feature="SharePoint sharing and oversharing assessment",
            observation=(governance.get('reason') + ' ' if (excluded or needs_admin_url) and governance.get('reason') else '')
                + "SharePoint tenant sharing settings were not collected; permissive sharing defaults and Data Access Governance report status remain unverified.",
            recommendation=next_step,
            link_text="SharePoint Data Access Governance" if excluded else "Connect to SharePoint Online",
            link_url=("https://learn.microsoft.com/sharepoint/data-access-governance-reports" if excluded else
                      "https://learn.microsoft.com/powershell/module/microsoft.online.sharepoint.powershell/connect-sposervice"),
            priority="High",
            status=NOT_ASSESSED_STATUS,
            category=CATEGORY_SCAN_COVERAGE,
            finding_key="sharepoint.governance.not_assessed",
            evidence_key="sharepoint_governance_detail",
            evidence_basis="Collection status",
            confidence="High",
        )]

    settings = governance.get("settings") or {}
    sharing = setting_label("SharingCapability", settings.get("SharingCapability")) if "SharingCapability" in settings else ""
    onedrive_sharing = setting_label("OneDriveSharingCapability", settings.get("OneDriveSharingCapability")) if "OneDriveSharingCapability" in settings else ""
    default_link = setting_name("DefaultSharingLinkType", settings.get("DefaultSharingLinkType"))
    file_link = setting_name("FileAnonymousLinkType", settings.get("FileAnonymousLinkType"))
    folder_link = setting_name("FolderAnonymousLinkType", settings.get("FolderAnonymousLinkType"))
    expiration = _int(settings.get("RequireAnonymousLinksExpireInDays"), 0)
    permits_anyone = (setting_name("SharingCapability", settings.get("SharingCapability")) in ANYONE_VALUES
                      or setting_name("OneDriveSharingCapability", settings.get("OneDriveSharingCapability")) in ANYONE_VALUES)
    anonymous_default = default_link in {"anonymousaccess", "anonymous"}
    edit_anyone = file_link == "edit" or folder_link == "edit"
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
        site_note = (f" {governance.get('anyone_site_count', 0)} collected site(s) also permit Anyone links."
                     if governance.get("sites_available") else
                     " Per-site sharing settings were not collected, so the sites that allow Anyone links are not identified.")
        default_note = ""
        if anonymous_default:
            default_note = (f" Anyone links are the default link type and expire after {expiration} day(s)"
                            + (" with edit access allowed." if edit_anyone else "."))
        recommendations.append(new_recommendation(
            service="M365",
            feature="SharePoint and OneDrive external sharing",
            observation="The tenant permits Anyone sharing." + default_note + site_note,
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

    if (not permits_anyone and default_link == "internal" and governance.get("coverage") != "partial"):
        recommendations.append(new_recommendation(
            service="M365",
            feature="SharePoint default sharing link",
            observation="Anyone links are off, but new sharing links default to People in your organization. Anyone in the organization who receives or redeems such a link can open the content, and Copilot can then use it in their answers.",
            recommendation="Change the default link type to Specific people so new links reach only the named recipients. Keep People in your organization available for content that is meant to be shared broadly.",
            link_text="Change the default sharing link",
            link_url="https://learn.microsoft.com/sharepoint/turn-external-sharing-on-or-off",
            priority="Medium",
            status="Attention Required",
            finding_key="sharepoint.sharing.organization_default",
            evidence_key="sharepoint_governance_detail",
            evidence_basis="Tenant configuration",
            confidence="High",
        ))

    # Tenant sharing configuration read from SharePoint administration answers
    # the sharing-defaults check. Without an explicit disposition, M365 rows
    # default to optional opportunities and never reach the pilot decision.
    for row in recommendations:
        if row.get("FindingKey") in {"sharepoint.sharing.permissive_anonymous_defaults", "sharepoint.sharing.anyone_enabled",
                                     "sharepoint.sharing.organization_default"}:
            row.update(Disposition="Action", ControlId="CONTENT.SHARING", DomainId="content")

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

    if governance.get("coverage") == "partial":
        unverified = [name for name in governance.get("unavailable_fields") or [] if name not in settings]
        recommendations.append(new_recommendation(
            service="M365",
            feature="SharePoint sharing defaults (partial evidence)",
            observation=(
                "SharePoint tenant settings were read through Microsoft Graph with application permissions. "
                "Graph does not return: " + (", ".join(unverified) or "some sharing defaults")
                + ". Default link type, anonymous link expiry and per-site exceptions therefore remain unverified."
            ),
            recommendation=(
                "Confirm these settings in the SharePoint admin center, or collect them with SharePoint administration: "
                "run setup-service-principal.ps1 -EnableSharePointAppOnly (certificate) or allow the SharePoint "
                "administrator browser sign-in (--interactive-auth auto)."
            ),
            link_text="Manage sharing settings",
            link_url="https://learn.microsoft.com/sharepoint/turn-external-sharing-on-or-off",
            priority="Medium",
            status=NOT_ASSESSED_STATUS,
            category=CATEGORY_SCAN_COVERAGE,
            finding_key="sharepoint.sharing.partial_settings",
            evidence_key="sharepoint_governance_detail",
            evidence_basis="Collection status",
            confidence="High",
        ))

    recommendations.extend(build_copilot_content_control_recommendations(governance))

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
        recommendations[-1]["DagGaps"] = [{"report": row["report"], "status": row["status"]} for row in required_dag_gaps]
    return recommendations


# Exported Data access governance reports (--reports-dir, --sam-report) that
# answer each report family the SharePoint collector looks for. The permission
# snapshot carries per-site Anyone-link and guest counts, so it also answers
# the sharing-link family.
_EXPORT_COVERAGE = (
    ("SharePoint permission state", lambda r: r.get("report_type") == "permission_snapshot" and _text(r.get("workload")).lower() == "sharepoint"),
    ("OneDrive permission state", lambda r: r.get("report_type") == "permission_snapshot" and _text(r.get("workload")).lower() == "onedrive"),
    ("Everyone and Everyone except external users", lambda r: r.get("report_type") == "special_group_permissions"),
    ("Anyone and guest sharing links", lambda r: r.get("report_type") in {"permission_snapshot", "sharing_links_activity"}),
)


def _dag_gaps(row):
    if isinstance(row.get("DagGaps"), list):
        return [dict(item) for item in row["DagGaps"] if isinstance(item, dict)]
    # Rows saved before DagGaps was recorded list the gaps in the observation.
    import re
    text = _text(row.get("Observation")).split("incomplete for: ", 1)[-1].split(". ", 1)[0]
    return [{"report": name.strip(), "status": status.strip()} for name, status in re.findall(r"([^,(]+)\(([^)]+)\)", text)]


def reconcile_dag_coverage(recommendations, sam_reports):
    """Close report families that supplied, current Microsoft exports already answer."""
    usable = [report for report in sam_reports or []
              if report.get("status") in {"selected", "no_activity"} and report.get("freshness") == "fresh"]
    covered = [label for label, answers in _EXPORT_COVERAGE if any(answers(report) for report in usable)]
    if not covered:
        return list(recommendations or [])
    reconciled = []
    for row in recommendations or []:
        if row.get("FindingKey") != "sharepoint.dag.not_assessed":
            reconciled.append(row)
            continue
        gaps = _dag_gaps(row)
        remaining = [gap for gap in gaps if gap["report"] not in covered]
        answered = [gap["report"] for gap in gaps if gap["report"] in covered]
        if not answered:
            reconciled.append(row)
            continue
        if not remaining:
            reconciled.append(dict(row, Disposition="Reference", Status="Insight",
                                   Observation="The supplied Data access governance exports cover every required report family: "
                                               + ", ".join(answered) + ".", Recommendation="", DagGaps=[]))
            continue
        reconciled.append(dict(
            row, DagGaps=remaining,
            Observation=("SharePoint Data Access Governance evidence is incomplete for: "
                         + ", ".join(f"{gap['report']} ({gap['status'].lower()})" for gap in remaining)
                         + ". The supplied exports cover: " + ", ".join(answered) + "."),
        ))
    return reconciled

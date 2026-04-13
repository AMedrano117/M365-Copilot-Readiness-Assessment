"""
Engineer follow-up evidence bundle builder.
"""

from collections import OrderedDict, defaultdict
from datetime import datetime


SHEET_DEFINITIONS = OrderedDict([
    ("app_access_detail", {
        "title": "App Access Detail",
        "appendix_title": "Appendix: Application Access Detail",
        "default_note": "See App Access Detail for the flagged applications, granted scopes, flag reasons, and recent activity context.",
        "preview_columns": ["App Display Name", "Flagged Because", "Activity Band", "Last Activity"],
    }),
    ("admin_role_detail", {
        "title": "Admin Role Detail",
        "appendix_title": "Appendix: Administrative Role Detail",
        "default_note": "See Admin Role Detail for the privileged assignments that support this recommendation.",
        "preview_columns": ["Assignment Type", "Principal ID", "Role Definition ID", "Reason Flagged"],
    }),
    ("access_review_detail", {
        "title": "Access Review Detail",
        "appendix_title": "Appendix: Access Review Detail",
        "default_note": "See Access Review Detail for the review definitions, cadence, and scope supporting this recommendation.",
        "preview_columns": ["Review Name", "Status", "Review Type", "Is Recurring"],
    }),
    ("conditional_access_detail", {
        "title": "Conditional Access Detail",
        "appendix_title": "Appendix: Conditional Access Detail",
        "default_note": "See Conditional Access Detail for the policies, grant controls, and targeting details behind this recommendation.",
        "preview_columns": ["Policy Name", "State", "Requires MFA", "Targets M365", "Reason Flagged"],
    }),
    ("guest_access_detail", {
        "title": "Guest Access Detail",
        "appendix_title": "Appendix: Guest Access Detail",
        "default_note": "See Guest Access Detail for guest accounts and external collaboration settings relevant to this recommendation.",
        "preview_columns": ["Object Type", "Display Name", "User Principal Name", "Flagged Because"],
    }),
    ("purview_policy_detail", {
        "title": "Purview Policy Detail",
        "appendix_title": "Appendix: Purview Policy Detail",
        "default_note": "See Purview Policy Detail for the policies, labels, and compliance objects reviewed for this recommendation.",
        "preview_columns": ["Object Type", "Name", "Enabled", "Mode"],
    }),
    ("defender_incident_detail", {
        "title": "Defender Incident Detail",
        "appendix_title": "Appendix: Defender Incident Detail",
        "default_note": "See Defender Incident Detail for the active incidents contributing to this finding.",
        "preview_columns": ["Incident ID", "Title", "Severity", "Status"],
    }),
    ("defender_device_detail", {
        "title": "Defender Device Detail",
        "appendix_title": "Appendix: Defender Device Detail",
        "default_note": "See Defender Device Detail for the device risk posture that supports this recommendation.",
        "preview_columns": ["Device Name", "Risk Score", "Exposure Level", "Reason Flagged"],
    }),
    ("power_platform_detail", {
        "title": "Power Platform Detail",
        "appendix_title": "Appendix: Power Platform Detail",
        "default_note": "See Power Platform Detail for environment, DLP, flow, app, and capacity detail relevant to this recommendation.",
        "preview_columns": ["Detail Type", "Name", "Subtype", "State"],
    }),
    ("m365_activity_detail", {
        "title": "M365 Activity Detail",
        "appendix_title": "Appendix: M365 Activity Detail",
        "default_note": "See M365 Activity Detail for the workload metrics and usage baselines referenced by this recommendation.",
        "preview_columns": ["Workload", "Metric", "Value", "Detail"],
    }),
])


SERVICE_PREFIXES = {
    "M365": "M365",
    "Entra": "ENT",
    "Defender": "DEF",
    "Purview": "PUR",
    "Power Platform": "PPL",
    "Copilot Studio": "CST",
}


HIGH_PRIVILEGE_SCOPE_MARKERS = [
    "mail.readwrite",
    "files.readwrite",
    "directory.readwrite",
]

MAIL_SCOPE_MARKERS = ["mail."]
FILES_SCOPE_MARKERS = ["files."]
SITES_SCOPE_MARKERS = ["sites."]
GRAPH_SCOPE_MARKERS = ["user.read", "group.read", "directory.read", "mail.", "files.", "sites."]


EXPLICIT_FEATURE_FALLBACKS = {
    ("Entra", "Conditional Access"): "conditional_access_detail",
    ("Defender", "Copilot Security Posture"): "app_access_detail;defender_incident_detail;defender_device_detail;purview_policy_detail",
    ("Defender", "Copilot Threat Intelligence"): "app_access_detail;defender_incident_detail",
    ("Defender", "Copilot Data Governance"): "purview_policy_detail",
    ("Defender", "Defender for Endpoint - Device Onboarding"): "defender_device_detail",
}


EXPLICIT_SERVICE_FALLBACKS = {
    "Purview": "purview_policy_detail",
    "Power Platform": "power_platform_detail",
    "Copilot Studio": "power_platform_detail",
}


def _camel_to_snake(value):
    parts = []
    for character in str(value or ""):
        if character.isupper() and parts:
            parts.append("_")
        parts.append(character.lower())
    return "".join(parts)


def _safe_get(obj, key, default=""):
    if obj is None:
        return default
    if isinstance(obj, dict):
        if key in obj:
            value = obj.get(key)
            return default if value is None else value
        snake_key = _camel_to_snake(key)
        if snake_key in obj:
            value = obj.get(snake_key)
            return default if value is None else value
        return default
    if hasattr(obj, key):
        value = getattr(obj, key)
        return default if value is None else value
    snake_key = _camel_to_snake(key)
    if hasattr(obj, snake_key):
        value = getattr(obj, snake_key)
        return default if value is None else value
    return default


def _ensure_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _stringify_list(values):
    normalized = []
    seen = set()
    for value in _ensure_list(values):
        text = str(value or "").strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(text)
    return normalized


def _split_evidence_keys(value):
    normalized = []
    for item in _stringify_list(str(value or "").replace(",", ";").split(";")):
        normalized.append(item.lower())
    return normalized


def _iso_text(value):
    text = str(value or "").strip()
    return text


def _bool_text(value):
    if isinstance(value, bool):
        return "Yes" if value else "No"
    text = str(value or "").strip().lower()
    if text in {"true", "yes", "enabled", "active"}:
        return "Yes"
    if text in {"false", "no", "disabled", "inactive"}:
        return "No"
    return ""


def _activity_band(count, available):
    if not available:
        return "Unavailable"
    if count <= 0:
        return "Inactive"
    if count <= 5:
        return "Low"
    if count <= 25:
        return "Moderate"
    return "High"


def assign_recommendation_ids(recommendations):
    per_service_counter = defaultdict(int)
    enriched = []
    for recommendation in recommendations:
        record = dict(recommendation)
        service = str(record.get("Service", "") or "Unknown")
        prefix = SERVICE_PREFIXES.get(service, "REC")
        per_service_counter[prefix] += 1
        record["RecommendationId"] = record.get("RecommendationId") or f"{prefix}-{per_service_counter[prefix]:03d}"
        record["EvidenceKey"] = str(record.get("EvidenceKey", "") or "")
        record["EvidenceSummary"] = str(record.get("EvidenceSummary", "") or "")
        record["EvidenceSheet"] = str(record.get("EvidenceSheet", "") or "")
        record["EvidenceAvailable"] = str(record.get("EvidenceAvailable", "No") or "No")
        enriched.append(record)
    return enriched


def build_evidence_bundle(
    recommendations,
    m365_result,
    entra_info,
    purview_info,
    defender_info,
    power_platform_info,
    copilot_studio_info,
):
    recommendations = assign_recommendation_ids(recommendations)

    m365_info, _ = m365_result
    m365_client = m365_info.get("_client") if isinstance(m365_info, dict) else None
    entra_client = entra_info.get("_client") if isinstance(entra_info, dict) else None
    purview_client = purview_info.get("_client") if isinstance(purview_info, dict) else None
    defender_client = defender_info.get("_client") if isinstance(defender_info, dict) else None
    pp_client = power_platform_info.get("_client") if isinstance(power_platform_info, dict) else None
    if not pp_client and isinstance(copilot_studio_info, dict):
        pp_client = copilot_studio_info.get("_client")

    sheets = OrderedDict()
    builders = [
        ("app_access_detail", lambda: _build_app_access_sheet(entra_client, defender_client)),
        ("admin_role_detail", lambda: _build_admin_role_sheet(entra_client)),
        ("access_review_detail", lambda: _build_access_review_sheet(entra_client)),
        ("conditional_access_detail", lambda: _build_conditional_access_sheet(entra_client)),
        ("guest_access_detail", lambda: _build_guest_access_sheet(entra_client)),
        ("purview_policy_detail", lambda: _build_purview_policy_sheet(purview_client)),
        ("defender_incident_detail", lambda: _build_defender_incident_sheet(defender_client)),
        ("defender_device_detail", lambda: _build_defender_device_sheet(defender_client)),
        ("power_platform_detail", lambda: _build_power_platform_sheet(pp_client)),
        ("m365_activity_detail", lambda: _build_m365_activity_sheet(m365_client)),
    ]

    for key, builder in builders:
        sheet = builder()
        if sheet and sheet.get("rows"):
            definition = SHEET_DEFINITIONS[key]
            sheet["key"] = key
            sheet["title"] = definition["title"]
            sheet["appendix_title"] = definition["appendix_title"]
            sheet["default_note"] = definition["default_note"]
            sheet["preview_columns"] = definition["preview_columns"]
            sheets[key] = sheet

    recommendations = _apply_explicit_evidence_fallbacks(recommendations, sheets)
    recommendations = _resolve_recommendation_evidence(recommendations, sheets)
    _link_sheet_rows_to_recommendations(sheets, recommendations)

    evidence_index = [{
        "RecommendationId": recommendation.get("RecommendationId", ""),
        "Service": recommendation.get("Service", ""),
        "Feature": recommendation.get("Feature", ""),
        "Evidence Available": recommendation.get("EvidenceAvailable", "No"),
        "Workbook Tab": recommendation.get("EvidenceSheet", ""),
        "Engineer Follow-Up": recommendation.get("EvidenceSummary", ""),
    } for recommendation in recommendations]

    appendix_sections = [{
        "key": key,
        "title": sheet["appendix_title"],
        "workbook_tab": sheet["title"],
        "summary": sheet.get("summary", ""),
        "details": sheet.get("details", []),
        "preview_columns": sheet.get("preview_columns", []),
        "preview_rows": sheet.get("rows", [])[:5],
    } for key, sheet in sheets.items()]

    return {
        "recommendations": recommendations,
        "sheets": sheets,
        "evidence_index": evidence_index,
        "appendix_sections": appendix_sections,
    }


def _resolve_recommendation_evidence(recommendations, sheets):
    resolved = []
    for recommendation in recommendations:
        record = dict(recommendation)
        evidence_keys = [key for key in _split_evidence_keys(record.get("EvidenceKey", "")) if key in sheets]
        if evidence_keys:
            titles = []
            notes = []
            for evidence_key in evidence_keys:
                sheet = sheets[evidence_key]
                titles.append(sheet["title"])
                notes.append(sheet["default_note"])
            record["EvidenceAvailable"] = "Yes"
            record["EvidenceSheet"] = "; ".join(_stringify_list(titles))
            if not record.get("EvidenceSummary"):
                if len(notes) == 1:
                    record["EvidenceSummary"] = notes[0]
                else:
                    record["EvidenceSummary"] = (
                        f"See workbook tabs: {'; '.join(_stringify_list(titles))} for the exact flagged objects, reasons, "
                        "and available activity context."
                    )
        else:
            record["EvidenceAvailable"] = "No"
            record["EvidenceSheet"] = ""
        resolved.append(record)
    return resolved


def _link_sheet_rows_to_recommendations(sheets, recommendations):
    recommendations_by_key = defaultdict(list)
    for recommendation in recommendations:
        for evidence_key in _split_evidence_keys(recommendation.get("EvidenceKey", "")):
            if evidence_key in sheets:
                recommendations_by_key[evidence_key].append(recommendation)

    for key, sheet in sheets.items():
        linked_recommendations = recommendations_by_key.get(key, [])
        recommendation_ids = [item.get("RecommendationId", "") for item in linked_recommendations if item.get("RecommendationId")]
        flagged_by = [item.get("Feature", "") for item in linked_recommendations if item.get("Feature")]
        for row in sheet.get("rows", []):
            existing_ids = _stringify_list(str(row.get("RecommendationId", "") or "").split(";"))
            existing_features = _stringify_list(str(row.get("Flagged By", "") or "").split(";"))
            row["RecommendationId"] = "; ".join(_stringify_list(existing_ids + recommendation_ids))
            row["Flagged By"] = "; ".join(_stringify_list(existing_features + flagged_by))


def _apply_explicit_evidence_fallbacks(recommendations, sheets):
    resolved = []
    available_keys = set(sheets.keys())

    for recommendation in recommendations:
        record = dict(recommendation)
        if _split_evidence_keys(record.get("EvidenceKey", "")):
            resolved.append(record)
            continue

        service = str(record.get("Service", "") or "")
        feature = str(record.get("Feature", "") or "")
        fallback_keys = ""

        if (service, feature) in EXPLICIT_FEATURE_FALLBACKS:
            fallback_keys = EXPLICIT_FEATURE_FALLBACKS[(service, feature)]
        elif service in EXPLICIT_SERVICE_FALLBACKS:
            fallback_keys = EXPLICIT_SERVICE_FALLBACKS[service]

        filtered_keys = [key for key in _split_evidence_keys(fallback_keys) if key in available_keys]
        if filtered_keys:
            record["EvidenceKey"] = "; ".join(filtered_keys)

        resolved.append(record)

    return resolved


def _build_defender_app_risk_index(defender_client):
    risk_index = {}
    if not defender_client:
        return risk_index
    for grouped_grants in _ensure_list(getattr(defender_client, "oauth_apps", [])):
        app_id = ""
        scopes = set()
        for grant in _ensure_list(grouped_grants):
            app_id = app_id or _iso_text(_safe_get(grant, "clientId"))
            scopes.update(_stringify_list((_safe_get(grant, "scope") or "").split()))
        if not app_id:
            continue
        lowered_scopes = [scope.lower() for scope in scopes]
        severity = ""
        if any(scope in lowered_scopes for scope in ["mail.readwrite", "files.readwrite.all", "sites.readwrite.all", "user.readwrite.all"]):
            severity = "High"
        elif any(scope in lowered_scopes for scope in ["mail.read", "files.read.all", "sites.read.all"]):
            severity = "Medium"
        risk_index[app_id.lower()] = {
            "severity": severity,
            "over_privileged": len(scopes) > 10,
        }
    return risk_index


def _build_app_access_sheet(entra_client, defender_client):
    if not entra_client:
        return None

    service_principals = _ensure_list(getattr(entra_client, "service_principals", []))
    oauth_grants = _ensure_list(getattr(entra_client, "oauth_permission_grants", []))
    activity_summary = getattr(entra_client, "app_activity_summary", {}) or {}
    activity_by_app = activity_summary.get("by_app", {})
    activity_available = bool(activity_summary.get("available"))
    defender_risk_by_app = _build_defender_app_risk_index(defender_client)

    app_index = {}

    for service_principal in service_principals:
        app_id = _iso_text(_safe_get(service_principal, "appId"))
        if not app_id:
            continue
        key = app_id.lower()
        publisher_name = _iso_text(_safe_get(service_principal, "publisherName"))
        app_index.setdefault(
            key,
            {
                "app_id": app_id,
                "service_principal_id": _iso_text(_safe_get(service_principal, "id")),
                "display_name": _iso_text(_safe_get(service_principal, "displayName")) or app_id,
                "publisher_name": publisher_name,
                "publisher_verified": "No" if not publisher_name or publisher_name.lower() == "unverified" else "Yes",
                "consent_types": set(),
                "permission_types": set(),
                "scopes": set(),
                "high_privilege_match_count": 0,
            },
        )

    for grant in oauth_grants:
        app_id = _iso_text(_safe_get(grant, "clientId"))
        if not app_id:
            continue
        key = app_id.lower()
        app_record = app_index.setdefault(
            key,
            {
                "app_id": app_id,
                "service_principal_id": "",
                "display_name": app_id,
                "publisher_name": "",
                "publisher_verified": "No",
                "consent_types": set(),
                "permission_types": set(),
                "scopes": set(),
                "high_privilege_match_count": 0,
            },
        )
        app_record["consent_types"].add(_iso_text(_safe_get(grant, "consentType")) or "Unknown")
        app_record["permission_types"].add("Delegated")
        grant_scopes = _stringify_list((_safe_get(grant, "scope") or "").split())
        if any(any(marker in scope.lower() for marker in HIGH_PRIVILEGE_SCOPE_MARKERS) for scope in grant_scopes):
            app_record["high_privilege_match_count"] += 1
        for scope in grant_scopes:
            app_record["scopes"].add(scope)

    rows = []
    total_flag_instances = 0

    for app_record in app_index.values():
        normalized_scopes = sorted(app_record["scopes"], key=str.lower)
        scopes_lower = [scope.lower() for scope in normalized_scopes]

        graph_access = any(any(marker in scope for marker in GRAPH_SCOPE_MARKERS) for scope in scopes_lower)
        mail_access = any(any(marker in scope for marker in MAIL_SCOPE_MARKERS) for scope in scopes_lower)
        files_access = any(any(marker in scope for marker in FILES_SCOPE_MARKERS) for scope in scopes_lower)
        sites_access = any(any(marker in scope for marker in SITES_SCOPE_MARKERS) for scope in scopes_lower)

        high_privilege_reasons = []
        for scope in normalized_scopes:
            lowered = scope.lower()
            if any(marker in lowered for marker in HIGH_PRIVILEGE_SCOPE_MARKERS):
                high_privilege_reasons.append(scope)

        is_high_privilege = bool(high_privilege_reasons)
        is_unverified = app_record["publisher_verified"] == "No"
        scope_count = len(normalized_scopes)
        over_privileged = scope_count > 10

        defender_risk = defender_risk_by_app.get(app_record["app_id"].lower(), {})
        defender_severity = defender_risk.get("severity", "")
        if defender_risk.get("over_privileged"):
            over_privileged = True

        flagged_because = []
        if is_high_privilege:
            flagged_because.append("High-privilege delegated permissions")
        if is_unverified:
            flagged_because.append("Unverified publisher")
        if graph_access:
            flagged_because.append("Microsoft Graph access")
        if mail_access:
            flagged_because.append("Mail access")
        if files_access:
            flagged_because.append("Files access")
        if sites_access:
            flagged_because.append("Sites access")
        if over_privileged:
            flagged_because.append("Over-privileged consent footprint")
        if defender_severity:
            flagged_because.append(f"Defender OAuth risk: {defender_severity}")

        if not flagged_because:
            continue

        activity_record = activity_by_app.get(app_record["app_id"].lower(), {})
        activity_count = int(activity_record.get("activity_count", 0) or 0)
        last_activity = _iso_text(activity_record.get("last_activity"))
        activity_band = _activity_band(activity_count, activity_available)
        flag_instance_count = int(app_record.get("high_privilege_match_count", 0) or 0) + int(is_unverified)
        total_flag_instances += flag_instance_count

        rows.append(
            {
                "RecommendationId": "",
                "Flagged By": "",
                "App Display Name": app_record["display_name"],
                "App ID": app_record["app_id"],
                "Service Principal ID": app_record["service_principal_id"],
                "Publisher Name": app_record["publisher_name"],
                "Publisher Verification State": app_record["publisher_verified"],
                "Consent Type": "; ".join(sorted(app_record["consent_types"], key=str.lower)),
                "Permission Type": "; ".join(sorted(app_record["permission_types"], key=str.lower)) or "Unknown",
                "Granted Scopes": "; ".join(normalized_scopes),
                "Scope Count": scope_count,
                "Graph Access": _bool_text(graph_access),
                "Mail Access": _bool_text(mail_access),
                "Files Access": _bool_text(files_access),
                "Sites Access": _bool_text(sites_access),
                "High Privilege Reasons": "; ".join(high_privilege_reasons),
                "High Privilege Match Count": app_record.get("high_privilege_match_count", 0),
                "Over-Privileged": _bool_text(over_privileged),
                "Defender Risk Severity": defender_severity,
                "Flagged Because": "; ".join(flagged_because),
                "Counted In Headline": _bool_text(flag_instance_count > 0),
                "Flag Instance Count": flag_instance_count,
                "Activity Count (30d)": activity_count if activity_available else "",
                "Last Activity": last_activity,
                "Activity Band": activity_band,
            }
        )

    rows.sort(
        key=lambda row: (
            -int(row.get("Flag Instance Count", 0) or 0),
            0 if row.get("Defender Risk Severity") == "High" else 1,
            -int(row.get("Activity Count (30d)", 0) or 0),
            str(row.get("App Display Name", "")).lower(),
        )
    )

    activity_note = (
        "Thirty-day activity was collected from Entra application sign-in reporting and service principal sign-in activity."
        if activity_available
        else "Thirty-day application activity was not available from the reviewed source data."
    )

    return {
        "rows": rows,
        "summary": f"{len(rows)} unique application(s) were flagged across {total_flag_instances} counted risk instance(s).",
        "details": [
            "The review merged enterprise application inventory, delegated permission grants, and OAuth risk indicators into one application access inventory.",
            "Rows show the exact app, granted scopes, publisher state, and the specific reasons the app was carried into engineer follow-up detail.",
            activity_note,
        ],
    }


def _build_admin_role_sheet(entra_client):
    if not entra_client:
        return None

    role_assignments = _ensure_list(getattr(entra_client, "role_assignments", []))
    eligible_assignments = _ensure_list(getattr(entra_client, "role_eligibility_schedules", []))
    time_bound_assignments = _ensure_list(getattr(entra_client, "role_assignment_schedules", []))
    pim_summary = getattr(entra_client, "pim_summary", {}) or {}
    rows = []

    for assignment in role_assignments:
        rows.append({
            "RecommendationId": "",
            "Flagged By": "",
            "Assignment Type": "Permanent Active",
            "Principal ID": _iso_text(_safe_get(assignment, "principalId")),
            "Role Definition ID": _iso_text(_safe_get(assignment, "roleDefinitionId")),
            "Directory Scope ID": _iso_text(_safe_get(assignment, "directoryScopeId")),
            "Start Date": _iso_text(_safe_get(assignment, "startDateTime")),
            "End Date": _iso_text(_safe_get(assignment, "endDateTime")) or "Permanent",
            "Reason Flagged": "Standing privileged assignment",
        })

    for assignment in eligible_assignments:
        rows.append({
            "RecommendationId": "",
            "Flagged By": "",
            "Assignment Type": "Eligible",
            "Principal ID": _iso_text(_safe_get(assignment, "principalId")),
            "Role Definition ID": _iso_text(_safe_get(assignment, "roleDefinitionId")),
            "Directory Scope ID": _iso_text(_safe_get(assignment, "directoryScopeId")),
            "Start Date": _iso_text(_safe_get(assignment, "startDateTime")),
            "End Date": _iso_text(_safe_get(assignment, "endDateTime")),
            "Reason Flagged": "Just-in-time eligible assignment",
        })

    for assignment in time_bound_assignments:
        rows.append({
            "RecommendationId": "",
            "Flagged By": "",
            "Assignment Type": "Time-Bound Active",
            "Principal ID": _iso_text(_safe_get(assignment, "principalId")),
            "Role Definition ID": _iso_text(_safe_get(assignment, "roleDefinitionId")),
            "Directory Scope ID": _iso_text(_safe_get(assignment, "directoryScopeId")),
            "Start Date": _iso_text(_safe_get(assignment, "startDateTime")),
            "End Date": _iso_text(_safe_get(assignment, "endDateTime")),
            "Reason Flagged": "Active privileged assignment with schedule",
        })

    rows.sort(key=lambda row: (str(row.get("Assignment Type", "")).lower(), str(row.get("Principal ID", "")).lower()))

    return {
        "rows": rows,
        "summary": (
            f"{pim_summary.get('permanent_assignments', 0)} permanent active assignment(s), "
            f"{pim_summary.get('total_eligible_assignments', 0)} eligible assignment(s), and "
            f"{pim_summary.get('total_time_bound_assignments', 0)} scheduled active assignment(s) were reviewed."
        ),
        "details": [
            "The review captured role assignment objects available from Entra privileged access data.",
            "Where directory display names were not present in the reviewed source content, the engineer workbook retains principal and role definition IDs for direct follow-up.",
        ],
    }


def _build_access_review_sheet(entra_client):
    if not entra_client:
        return None

    access_reviews = _ensure_list(getattr(entra_client, "access_reviews", []))
    review_summary = getattr(entra_client, "access_review_summary", {}) or {}
    rows = []

    for review in access_reviews:
        scope = _safe_get(review, "scope")
        query = _iso_text(_safe_get(scope, "query")) if scope else ""
        settings = _safe_get(review, "settings")
        recurrence_type = _iso_text(_safe_get(settings, "recurrenceType")) if settings else ""

        review_type = "Group Membership"
        lowered_query = query.lower()
        if "role" in lowered_query:
            review_type = "Role Assignment"
        elif "guest" in lowered_query:
            review_type = "Guest Access"
        elif "principal" in lowered_query or "application" in lowered_query:
            review_type = "Application Assignment"

        rows.append({
            "RecommendationId": "",
            "Flagged By": "",
            "Review Name": _iso_text(_safe_get(review, "displayName")) or _iso_text(_safe_get(review, "id")),
            "Status": _iso_text(_safe_get(review, "status")),
            "Review Type": review_type,
            "Is Recurring": _bool_text(bool(recurrence_type)),
            "Recurrence Type": recurrence_type,
            "Created Date": _iso_text(_safe_get(review, "createdDateTime")),
            "Scope Query": query,
            "Reason Flagged": "Access review object included in current Entra governance posture",
        })

    rows.sort(key=lambda row: (str(row.get("Review Name", "")).lower(), str(row.get("Status", "")).lower()))

    return {
        "rows": rows,
        "summary": (
            f"{review_summary.get('total_definitions', 0)} access review definition(s) were reviewed, "
            f"including {review_summary.get('active_reviews', 0)} active review(s)."
        ),
        "details": [
            "The review cataloged access review definitions, their scope, and whether the campaigns are recurring or one-time.",
            "This appendix lets the follow-up engineer see which governance objects already exist before adjusting review coverage.",
        ],
    }


def _build_conditional_access_sheet(entra_client):
    if not entra_client:
        return None

    policies = _ensure_list(getattr(entra_client, "ca_policies", []))
    rows = []

    for policy in policies:
        grant_controls = _safe_get(policy, "grantControls")
        built_in_controls = _stringify_list(_safe_get(grant_controls, "builtInControls")) if grant_controls else []
        conditions = _safe_get(policy, "conditions")
        applications = _safe_get(conditions, "applications") if conditions else None
        include_applications = _stringify_list(_safe_get(applications, "includeApplications")) if applications else []
        client_app_types = _stringify_list(_safe_get(conditions, "clientAppTypes")) if conditions else []
        user_risk_levels = _stringify_list(_safe_get(conditions, "userRiskLevels")) if conditions else []
        sign_in_risk_levels = _stringify_list(_safe_get(conditions, "signInRiskLevels")) if conditions else []

        requires_mfa = any(control.lower() == "mfa" for control in built_in_controls)
        requires_compliance = any(control.lower() == "compliantdevice" for control in built_in_controls)
        targets_all_apps = any(value.lower() == "all" for value in include_applications)
        targets_m365 = "00000003-0000-0ff1-ce00-000000000000" in include_applications or targets_all_apps
        lowered_client_apps = [value.lower() for value in client_app_types]
        blocks_legacy = bool(client_app_types) and "exchangeactivesync" not in lowered_client_apps and "other" not in lowered_client_apps

        reasons = []
        state = _iso_text(_safe_get(policy, "state"))
        if state.lower() != "enabled":
            reasons.append(f"Policy state is {state or 'unknown'}")
        if not requires_mfa:
            reasons.append("No MFA grant control")
        if not targets_m365:
            reasons.append("Does not target Microsoft 365 or all apps")
        if not blocks_legacy:
            reasons.append("Legacy-auth coverage not evident from client app targeting")
        if not reasons:
            reasons.append("Policy is present in current Conditional Access baseline")

        rows.append({
            "RecommendationId": "",
            "Flagged By": "",
            "Policy Name": _iso_text(_safe_get(policy, "displayName")) or _iso_text(_safe_get(policy, "id")),
            "State": state,
            "Include Applications": "; ".join(include_applications),
            "Grant Controls": "; ".join(built_in_controls),
            "Client App Types": "; ".join(client_app_types),
            "User Risk Levels": "; ".join(user_risk_levels),
            "Sign-In Risk Levels": "; ".join(sign_in_risk_levels),
            "Targets All Apps": _bool_text(targets_all_apps),
            "Targets M365": _bool_text(targets_m365),
            "Requires MFA": _bool_text(requires_mfa),
            "Requires Compliant Device": _bool_text(requires_compliance),
            "Blocks Legacy Auth": _bool_text(blocks_legacy),
            "Reason Flagged": "; ".join(reasons),
        })

    rows.sort(key=lambda row: (str(row.get("Policy Name", "")).lower(), str(row.get("State", "")).lower()))
    ca_summary = getattr(entra_client, "ca_summary", {}) or {}

    return {
        "rows": rows,
        "summary": (
            f"{ca_summary.get('total', 0)} Conditional Access policie(s) were reviewed, "
            f"including {ca_summary.get('enabled', 0)} enabled policie(s) and "
            f"{ca_summary.get('require_mfa', 0)} policie(s) requiring MFA."
        ),
        "details": [
            "Each row captures the policy state, targeting, and grant controls that were available from the tenant review.",
            "These details help the follow-up engineer verify whether the observed coverage gaps are isolated to a few policies or reflect a broader Conditional Access pattern.",
        ],
    }


def _build_guest_access_sheet(entra_client):
    if not entra_client:
        return None

    guests = _ensure_list(getattr(entra_client, "guest_users", []))
    b2b_summary = getattr(entra_client, "b2b_summary", {}) or {}
    rows = []

    invite_setting = _iso_text(b2b_summary.get("guest_invite_restrictions"))
    cross_tenant_configured = bool(b2b_summary.get("cross_tenant_access_configured"))
    partner_count = int(b2b_summary.get("partner_configurations", 0) or 0)

    for guest in guests:
        assigned_licenses = _ensure_list(_safe_get(guest, "assignedLicenses"))
        is_licensed = bool(assigned_licenses)
        reasons = []
        if is_licensed:
            reasons.append("Guest user has assigned Microsoft 365 licenses")
        if invite_setting and "admins" not in invite_setting.lower() and "limited" not in invite_setting.lower():
            reasons.append("Guest invitation policy is permissive")
        if not cross_tenant_configured or partner_count == 0:
            reasons.append("Cross-tenant partner governance is incomplete")
        if not reasons:
            reasons.append("Guest access object included for collaboration review")

        rows.append({
            "RecommendationId": "",
            "Flagged By": "",
            "Object Type": "Guest User",
            "Display Name": _iso_text(_safe_get(guest, "displayName")) or _iso_text(_safe_get(guest, "id")),
            "User Principal Name": _iso_text(_safe_get(guest, "userPrincipalName")),
            "Created Date": _iso_text(_safe_get(guest, "createdDateTime")),
            "Licensed": _bool_text(is_licensed),
            "Guest Invite Restriction": invite_setting,
            "Cross-Tenant Configured": _bool_text(cross_tenant_configured),
            "Partner Policy Count": partner_count,
            "Flagged Because": "; ".join(reasons),
        })

    if invite_setting or cross_tenant_configured or partner_count:
        policy_reasons = []
        if invite_setting and "admins" not in invite_setting.lower() and "limited" not in invite_setting.lower():
            policy_reasons.append("Guest invitations are broadly allowed")
        if not cross_tenant_configured:
            policy_reasons.append("Cross-tenant access settings not configured")
        elif partner_count == 0:
            policy_reasons.append("Cross-tenant defaults apply without named partner policies")
        if not policy_reasons:
            policy_reasons.append("Guest collaboration settings were reviewed")

        rows.insert(0, {
            "RecommendationId": "",
            "Flagged By": "",
            "Object Type": "Tenant Policy",
            "Display Name": "External Collaboration Settings",
            "User Principal Name": "",
            "Created Date": "",
            "Licensed": "",
            "Guest Invite Restriction": invite_setting,
            "Cross-Tenant Configured": _bool_text(cross_tenant_configured),
            "Partner Policy Count": partner_count,
            "Flagged Because": "; ".join(policy_reasons),
        })

    return {
        "rows": rows,
        "summary": (
            f"{b2b_summary.get('total_guests', 0)} guest account(s) were reviewed, "
            f"including {b2b_summary.get('guests_with_licenses', 0)} licensed guest(s)."
        ),
        "details": [
            "This appendix combines tenant-level external collaboration settings with individual guest objects already visible in the reviewed source content.",
            "The engineer workbook highlights where guest licensing, invitation settings, or cross-tenant governance may warrant follow-up.",
        ],
    }


def _build_purview_policy_sheet(purview_client):
    if not purview_client:
        return None

    rows = []

    def add_objects(object_type, collection, name_keys=None, enabled_key="Enabled", mode_key="Mode"):
        for item in _ensure_list(collection):
            name = ""
            for key in _ensure_list(name_keys or ["Name", "DisplayName", "CaseName"]):
                name = _iso_text(_safe_get(item, key))
                if name:
                    break
            enabled_value = _safe_get(item, enabled_key)
            mode_value = _safe_get(item, mode_key)
            rows.append({
                "RecommendationId": "",
                "Flagged By": "",
                "Object Type": object_type,
                "Name": name or _iso_text(_safe_get(item, "id")),
                "Enabled": _bool_text(enabled_value),
                "Mode": _iso_text(mode_value),
                "Additional Context": _iso_text(_safe_get(item, "Status")) or _iso_text(_safe_get(item, "Comment")),
            })

    add_objects("DLP Policy", purview_client.dlp_policies.get("policies", []))
    add_objects("Communication Compliance Policy", purview_client.comm_compliance.get("policies", []))
    add_objects("Information Barrier Policy", purview_client.information_barriers.get("policies", []))
    add_objects("Sensitivity Label", purview_client.sensitivity_labels.get("labels", []), name_keys=["DisplayName", "Name"])
    add_objects("Label Policy", purview_client.label_policies.get("policies", []))
    add_objects("Retention Label", purview_client.retention_labels.get("labels", []), name_keys=["DisplayName", "Name"])
    add_objects("Insider Risk Policy", purview_client.insider_risk.get("policies", []))
    add_objects("eDiscovery Case", purview_client.ediscovery_cases.get("cases", []), name_keys=["Name", "CaseName"], enabled_key="Status", mode_key="CaseType")

    rows.sort(key=lambda row: (str(row.get("Object Type", "")).lower(), str(row.get("Name", "")).lower()))

    return {
        "rows": rows,
        "summary": f"{len(rows)} Purview policy or governance object(s) were included in the workbook detail.",
        "details": [
            "The workbook consolidates labels, policy objects, and case records already available from the tenant review into one engineer follow-up tab.",
            "This lets the follow-up engineer validate whether the recommendation came from missing objects, disabled objects, or incomplete coverage across policy types.",
        ],
    }


def _build_defender_incident_sheet(defender_client):
    if not defender_client:
        return None

    rows = []
    seen_ids = set()

    for source_name, incidents in (
        ("Graph Security", _ensure_list(getattr(defender_client, "security_incidents", []))),
        ("Defender API", _ensure_list(getattr(defender_client, "defender_incidents", []))),
    ):
        for incident in incidents:
            incident_id = _iso_text(_safe_get(incident, "id"))
            dedupe_key = (source_name, incident_id)
            if dedupe_key in seen_ids:
                continue
            seen_ids.add(dedupe_key)
            rows.append({
                "RecommendationId": "",
                "Flagged By": "",
                "Incident ID": incident_id,
                "Source": source_name,
                "Title": _iso_text(_safe_get(incident, "title")) or _iso_text(_safe_get(incident, "displayName")),
                "Severity": _iso_text(_safe_get(incident, "severity")),
                "Status": _iso_text(_safe_get(incident, "status")),
                "Created Date": _iso_text(_safe_get(incident, "createdDateTime")),
                "Assigned To": _iso_text(_safe_get(incident, "assignedTo")),
            })

    incident_summary = getattr(defender_client, "incident_summary", {}) or {}
    rows.sort(key=lambda row: (str(row.get("Severity", "")).lower(), str(row.get("Created Date", ""))), reverse=True)

    return {
        "rows": rows,
        "summary": (
            f"{incident_summary.get('total', 0)} security incident(s) were reviewed, "
            f"including {incident_summary.get('active', 0)} active incident(s) and "
            f"{incident_summary.get('high_severity', 0)} high-severity incident(s)."
        ),
        "details": [
            "Incident detail combines the Graph Security and Defender API incident views when they were available from the reviewed tenant.",
            "The engineer workbook shows the exact incidents that supported higher-severity security posture observations.",
        ],
    }


def _build_defender_device_sheet(defender_client):
    if not defender_client:
        return None

    devices = _ensure_list(getattr(defender_client, "defender_devices", []))
    rows = []

    for device in devices:
        risk_score = _iso_text(_safe_get(device, "riskScore"))
        exposure_level = _iso_text(_safe_get(device, "exposureLevel"))
        reasons = []
        if risk_score and risk_score.lower() in {"high", "medium"}:
            reasons.append(f"Risk score {risk_score}")
        if exposure_level and exposure_level.lower() in {"high", "medium"}:
            reasons.append(f"Exposure level {exposure_level}")
        if not reasons:
            reasons.append("Device inventory included for security posture review")

        rows.append({
            "RecommendationId": "",
            "Flagged By": "",
            "Device Name": _iso_text(_safe_get(device, "computerDnsName")) or _iso_text(_safe_get(device, "deviceName")) or _iso_text(_safe_get(device, "id")),
            "Risk Score": risk_score,
            "Exposure Level": exposure_level,
            "Health Status": _iso_text(_safe_get(device, "healthStatus")),
            "OS Platform": _iso_text(_safe_get(device, "osPlatform")),
            "Onboarding Status": _iso_text(_safe_get(device, "onboardingStatus")),
            "Last Seen": _iso_text(_safe_get(device, "lastSeen")),
            "Reason Flagged": "; ".join(reasons),
        })

    device_summary = getattr(defender_client, "device_summary", {}) or {}
    rows.sort(key=lambda row: (str(row.get("Risk Score", "")).lower(), str(row.get("Device Name", "")).lower()))

    return {
        "rows": rows,
        "summary": (
            f"{device_summary.get('total', 0)} Defender device(s) were reviewed, "
            f"including {device_summary.get('high_risk', 0)} high-risk device(s)."
        ),
        "details": [
            "Device detail captures the risk and exposure fields returned by Defender for Endpoint in the tenant review.",
            "This gives the follow-up engineer a direct starting point for devices that may weaken Copilot rollout security posture.",
        ],
    }


def _build_power_platform_sheet(pp_client):
    if not pp_client:
        return None

    rows = []

    for environment in _ensure_list(getattr(pp_client, "environments", [])):
        properties = _safe_get(environment, "properties") or {}
        states = _safe_get(properties, "states") or {}
        management = _safe_get(states, "management") or {}
        rows.append({
            "RecommendationId": "",
            "Flagged By": "",
            "Detail Type": "Environment",
            "Name": _iso_text(_safe_get(properties, "displayName")) or _iso_text(_safe_get(environment, "name")),
            "Subtype": _iso_text(_safe_get(properties, "environmentType")) or _iso_text(_safe_get(properties, "environmentSku")),
            "State": _iso_text(_safe_get(management, "id")),
            "Additional Context": _iso_text(_safe_get(environment, "location")),
        })

    for flow in _ensure_list(getattr(pp_client, "flows", [])):
        properties = _safe_get(flow, "properties") or {}
        rows.append({
            "RecommendationId": "",
            "Flagged By": "",
            "Detail Type": "Flow",
            "Name": _iso_text(_safe_get(properties, "displayName")) or _iso_text(_safe_get(flow, "name")),
            "Subtype": _iso_text(_safe_get(properties, "flowType")),
            "State": _iso_text(_safe_get(properties, "state")),
            "Additional Context": _iso_text(_safe_get(flow, "id")),
        })

    for app in _ensure_list(getattr(pp_client, "apps", [])):
        properties = _safe_get(app, "properties") or {}
        rows.append({
            "RecommendationId": "",
            "Flagged By": "",
            "Detail Type": "App",
            "Name": _iso_text(_safe_get(properties, "displayName")) or _iso_text(_safe_get(app, "name")),
            "Subtype": _iso_text(_safe_get(properties, "appType")),
            "State": _iso_text(_safe_get(properties, "state")),
            "Additional Context": _iso_text(_safe_get(app, "id")),
        })

    for policy in _ensure_list(getattr(pp_client, "dlp_policies", [])):
        properties = _safe_get(policy, "properties") or {}
        rows.append({
            "RecommendationId": "",
            "Flagged By": "",
            "Detail Type": "DLP Policy",
            "Name": _iso_text(_safe_get(properties, "displayName")) or _iso_text(_safe_get(policy, "name")),
            "Subtype": _iso_text(_safe_get(properties, "scope")),
            "State": _iso_text(_safe_get(properties, "state")),
            "Additional Context": _iso_text(_safe_get(policy, "id")),
        })

    for solution in _ensure_list(getattr(pp_client, "solutions", [])):
        rows.append({
            "RecommendationId": "",
            "Flagged By": "",
            "Detail Type": "Solution",
            "Name": _iso_text(_safe_get(solution, "uniquename")) or _iso_text(_safe_get(solution, "friendlyname")),
            "Subtype": "Managed" if _safe_get(solution, "ismanaged", False) else "Unmanaged",
            "State": "",
            "Additional Context": _iso_text(_safe_get(solution, "version")),
        })

    capacity_summary = getattr(pp_client, "capacity_summary", {}) or {}
    if capacity_summary:
        rows.append({
            "RecommendationId": "",
            "Flagged By": "",
            "Detail Type": "Capacity",
            "Name": "Dataverse Capacity",
            "Subtype": "Database",
            "State": f"{capacity_summary.get('database_usage_percent', 0)}% used" if capacity_summary.get("available") else "Unavailable",
            "Additional Context": f"{capacity_summary.get('database_used_mb', 0)} MB used of {capacity_summary.get('database_capacity_mb', 0)} MB",
        })

    return {
        "rows": rows,
        "summary": f"{len(rows)} Power Platform detail row(s) were generated from environments, apps, flows, DLP, solutions, and capacity data.",
        "details": [
            "This detail sheet consolidates the deployment objects already returned by the Power Platform enrichment path.",
            "The workbook helps the follow-up engineer see whether the recommendation stems from environment sprawl, governance gaps, or workload inventory shape.",
        ],
    }


def _build_m365_activity_sheet(m365_client):
    if not m365_client:
        return None

    rows = []

    def add_metric(workload, metric, value, detail=""):
        rows.append({
            "RecommendationId": "",
            "Flagged By": "",
            "Workload": workload,
            "Metric": metric,
            "Value": value,
            "Detail": detail,
        })

    email_summary = getattr(m365_client, "email_summary", {}) or {}
    teams_summary = getattr(m365_client, "teams_summary", {}) or {}
    sharepoint_summary = getattr(m365_client, "sharepoint_summary", {}) or {}
    onedrive_summary = getattr(m365_client, "onedrive_summary", {}) or {}
    activations_summary = getattr(m365_client, "activations_summary", {}) or {}
    active_users_summary = getattr(m365_client, "active_users_summary", {}) or {}
    users_summary = getattr(m365_client, "users_summary", {}) or {}

    add_metric("Users", "Total Users", users_summary.get("total", 0), "Tenant user inventory reviewed for activity baselines.")
    add_metric("Users", "Copilot Licensed Users", users_summary.get("copilot_licensed", 0), f"Adoption rate {users_summary.get('copilot_adoption_rate', 0)}%.")
    add_metric("Email", "Active Users", email_summary.get("active_users", 0), f"Average sent per user {email_summary.get('avg_sent_per_user', 0)}.")
    add_metric("Email", "Total Sent", email_summary.get("total_sent", 0), f"Average received per user {email_summary.get('avg_received_per_user', 0)}.")
    add_metric("Teams", "Active Users", teams_summary.get("active_users", 0), f"Average meetings per user {teams_summary.get('avg_meetings_per_user', 0)}.")
    add_metric("Teams", "Total Meetings", teams_summary.get("total_meetings", 0), f"Average messages per user {teams_summary.get('avg_messages_per_user', 0)}.")
    add_metric("SharePoint", "Active Sites", sharepoint_summary.get("active_sites", 0), f"Site activity rate {sharepoint_summary.get('site_activity_rate', 0)}%.")
    add_metric("SharePoint", "Total Files", sharepoint_summary.get("total_files", 0), f"Average files per site {sharepoint_summary.get('avg_files_per_site', 0)}.")
    add_metric("OneDrive", "Active Accounts", onedrive_summary.get("active_accounts", 0), f"Adoption rate {onedrive_summary.get('adoption_rate', 0)}%.")
    add_metric("OneDrive", "Storage Used (GB)", onedrive_summary.get("storage_used_gb", 0), f"Average files per active account {onedrive_summary.get('avg_files_per_user', 0)}.")
    add_metric("Office Activations", "Desktop Adoption Rate", activations_summary.get("desktop_adoption_rate", 0), f"Windows users {activations_summary.get('windows_users', 0)}, Mac users {activations_summary.get('mac_users', 0)}.")
    add_metric("Active Users Snapshot", "Office 365 Active", active_users_summary.get("office_365_active", 0), f"Exchange {active_users_summary.get('exchange_active', 0)}, Teams {active_users_summary.get('teams_active', 0)}.")

    return {
        "rows": rows,
        "summary": "M365 Activity Detail summarizes the workload usage baselines already collected for Exchange, Teams, SharePoint, OneDrive, activations, and active-user reporting.",
        "details": [
            "The workbook captures workload-level baselines rather than per-user detail because those are the metrics currently present in the reviewed source content.",
            "These rows give the follow-up engineer the workload counts behind activity-driven M365 recommendations without changing the recommendation wording.",
        ],
    }

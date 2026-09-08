"""
Engineer follow-up evidence bundle builder.
"""

from collections import OrderedDict, defaultdict
from datetime import datetime

from .friendly_names import get_friendly_plan_name, get_friendly_sku_name
from .assessment_model import enrich_assessment_records


SHEET_DEFINITIONS = OrderedDict([
    ("app_access_detail", {
        "title": "App Access Detail",
        "appendix_title": "Appendix: Application Access Detail",
        "default_note": "See App Access Detail for the flagged applications, granted scopes, flag reasons, and recent activity context.",
        "preview_columns": ["App Display Name", "Flagged Because", "Activity Band", "Last Activity"],
    }),
    ("app_consent_policy_detail", {
        "title": "Application Consent Policy",
        "appendix_title": "Appendix: Application Consent Policy",
        "default_note": "See Application Consent Policy for the effective default-user consent assignments returned by Microsoft Graph.",
        "preview_columns": ["Setting", "Value", "Policy ID", "Source State"],
    }),
    ("admin_role_detail", {
        "title": "Admin Role Detail",
        "appendix_title": "Appendix: Administrative Role Detail",
        "default_note": "See Admin Role Detail for the privileged assignments that support this recommendation.",
        "preview_columns": ["Assignment Type", "Principal Display Name", "Role Name", "Reason Flagged"],
    }),
    ("authentication_detail", {
        "title": "Authentication Coverage",
        "appendix_title": "Appendix: Authentication Coverage",
        "default_note": "See Authentication Coverage for the aggregate registration counts returned by Microsoft Graph.",
        "preview_columns": ["Metric", "Value", "Source State"],
    }),
    ("identity_risk_detail", {
        "title": "Identity Risk Detail",
        "appendix_title": "Appendix: Identity Risk Detail",
        "default_note": "See Identity Risk Detail for the risky-user and risk-detection records supporting this action.",
        # Keep identities in the engineer workbook; the HTML preview remains aggregate.
        "preview_columns": ["Record Type", "Risk Level", "Risk State", "Last Updated"],
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
    ("data_exposure_detail", {
        "title": "Data Exposure Detail",
        "appendix_title": "Appendix: Data Exposure and Oversharing Detail",
        "default_note": "See Data Exposure Detail for the SAM/DSPM source, freshness, affected location, and explicit risk signals supporting this finding.",
        "preview_columns": ["Detail Type", "Source", "Site Name", "Risk Signals", "Severity"],
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
    ("ai_usage_detail", {
        "title": "AI Adoption Usage",
        "appendix_title": "Appendix: AI Adoption and Usage",
        "default_note": "See AI Adoption Usage for aggregate Copilot, Microsoft 365 Apps, and optional external-AI activity evidence.",
        "preview_columns": ["Evidence Source", "Period", "Metric", "Value", "Freshness"],
    }),
    ("copilot_user_usage_detail", {
        "title": "Copilot User Usage",
        "appendix_title": "Appendix: Restricted Copilot User Usage",
        "default_note": "Restricted workbook evidence contains user-level Copilot activity collected only by explicit request.",
        "preview_columns": ["Period", "Activity State", "Last Activity Date"],
    }),
    ("service_plan_inventory", {
        "title": "Service Plan Inventory",
        "appendix_title": "Appendix: Service Plan Inventory",
        "default_note": "See Service Plan Inventory for the full list of licensed service plans and their provisioning status.",
        "preview_columns": ["SKU", "Service Plan", "Provisioning Status", "Assessed"],
    }),
])


SERVICE_PREFIXES = {
    "M365": "M365",
    "Entra": "ENT",
    "Defender": "DEF",
    "Purview": "PUR",
    "Power Platform": "PPL",
    "Copilot Studio": "CST",
    "Data Exposure": "DEX",
}


HIGH_PRIVILEGE_SCOPE_MARKERS = [
    "mail.readwrite",
    "files.readwrite",
    "directory.readwrite",
]

# Microsoft documents these home tenants as Microsoft-owned service tenants. A small
# application-ID allowlist covers legacy first-party principals whose owner metadata is not
# populated in older tenants. Display names are deliberately not used as publisher proof.
MICROSOFT_OWNER_TENANT_IDS = {
    "f8cdef31-a31e-4b4a-93e4-5f571e91255a",
    "72f988bf-86f1-41af-91ab-2d7cd011db47",
}
MICROSOFT_FIRST_PARTY_APP_IDS = {
    "00000002-0000-0000-c000-000000000000",  # Azure AD Graph (legacy)
    "00000003-0000-0000-c000-000000000000",  # Microsoft Graph
    "00000002-0000-0ff1-ce00-000000000000",  # Exchange Online
    "00000003-0000-0ff1-ce00-000000000000",  # SharePoint Online
    "08e18876-6177-487e-b8b5-cf950c1e598c",  # SharePoint web client extensibility
    "14d82eec-204b-4c2f-b7e8-296a70dab67e",  # Microsoft Graph command-line tools
    "1950a258-227b-4e31-a9cf-717495945fc2",  # Microsoft Azure PowerShell
}

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


def _count_phrase(count, singular, plural=None):
    label = singular if count == 1 else (plural or singular + "s")
    display = f"{count:,}" if isinstance(count, int) else str(count)
    return f"{display} {label}"


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


_PRIORITY_RANK = {"High": 0, "Medium": 1, "Low": 2, "": 3}

# Statuses ordered by how much attention they demand, used to keep the most serious framing when
# two cards describing one condition are merged.
_STATUS_RANK_FOR_MERGE = {
    "critical": 0, "action required": 1, "attention required": 2, "warning": 3,
    "not assessed": 4, "disabled": 5, "pendingactivation": 6, "pendinginput": 7,
    "insight": 8, "success": 9,
}


def _merge_rank(recommendation):
    priority = _PRIORITY_RANK.get(recommendation.get("Priority", ""), 3)
    status = _STATUS_RANK_FOR_MERGE.get(
        str(recommendation.get("Status", "") or "").strip().lower(), 99
    )
    return (priority, status)


def deduplicate_findings(recommendations):
    """Collapse cards that describe the same tenant condition.

    One condition often reaches the report several times because several licences grant the
    control that was checked: eDiscovery cases are evaluated by three service plans, Customer
    Lockbox by two, the risky OAuth apps by five. Each emitted its own card, so a reader saw one
    issue as several - sometimes with different severities for the identical action.

    Two passes:
      1. Explicit - cards sharing a FindingKey are the same condition by construction.
      2. Exact text - identical Service, Observation and Recommendation cannot be two findings.

    The highest-severity card in a group survives and gains a "Also licensed via" note listing
    the other contributing features, so nothing about licensing coverage is lost.
    """
    if not recommendations:
        return recommendations

    groups = OrderedDict()
    for recommendation in recommendations:
        finding_key = str(recommendation.get("FindingKey", "") or "").strip()
        if finding_key:
            # Finding keys identify tenant conditions, not producing modules.  Allow the same
            # underlying condition to collapse when Entra and Defender both surface it.
            key = ("finding", finding_key)
        else:
            key = (
                "text",
                recommendation.get("Service", ""),
                str(recommendation.get("Observation", "") or "").strip(),
                str(recommendation.get("Recommendation", "") or "").strip(),
            )
        groups.setdefault(key, []).append(recommendation)

    merged = []
    for members in groups.values():
        if len(members) == 1:
            merged.append(members[0])
            continue

        winner = dict(min(members, key=_merge_rank))
        others = [
            str(m.get("Feature", "") or "")
            for m in members
            if str(m.get("Feature", "") or "") != str(winner.get("Feature", "") or "")
        ]
        seen = []
        for feature in others:
            if feature and feature not in seen:
                seen.append(feature)
        if seen:
            winner["AlsoLicensedVia"] = "; ".join(seen)

        # Preserve every evidence bucket the group referenced.
        evidence_keys = []
        for member in members:
            for key in _split_evidence_keys(member.get("EvidenceKey", "")):
                if key not in evidence_keys:
                    evidence_keys.append(key)
        if evidence_keys:
            winner["EvidenceKey"] = "; ".join(evidence_keys)

        merged.append(winner)

    return merged


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
    data_exposure_info=None,
):
    # Collapse duplicate findings before IDs are assigned, so identifiers are stable and
    # sequential across the report the reader actually sees.
    recommendations = deduplicate_findings(recommendations)
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
        ("app_consent_policy_detail", lambda: _build_app_consent_policy_sheet(entra_client)),
        ("admin_role_detail", lambda: _build_admin_role_sheet(entra_client)),
        ("authentication_detail", lambda: _build_authentication_sheet(entra_client)),
        ("identity_risk_detail", lambda: _build_identity_risk_sheet(entra_client)),
        ("access_review_detail", lambda: _build_access_review_sheet(entra_client)),
        ("conditional_access_detail", lambda: _build_conditional_access_sheet(entra_client)),
        ("guest_access_detail", lambda: _build_guest_access_sheet(entra_client)),
        ("purview_policy_detail", lambda: _build_purview_policy_sheet(purview_client)),
        ("data_exposure_detail", lambda: _build_data_exposure_sheet(data_exposure_info)),
        ("defender_incident_detail", lambda: _build_defender_incident_sheet(defender_client)),
        ("defender_device_detail", lambda: _build_defender_device_sheet(defender_client)),
        ("power_platform_detail", lambda: _build_power_platform_sheet(pp_client)),
        ("m365_activity_detail", lambda: _build_m365_activity_sheet(m365_client)),
        ("ai_usage_detail", lambda: _build_ai_usage_sheet(m365_client)),
        ("copilot_user_usage_detail", lambda: _build_copilot_user_usage_sheet(m365_client)),
        ("service_plan_inventory", lambda: _build_service_plan_inventory_sheet(m365_info)),
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
    recommendations = enrich_assessment_records(recommendations)
    _link_sheet_rows_to_recommendations(sheets, recommendations)

    evidence_index = [{
        "RecommendationId": recommendation.get("RecommendationId", ""),
        "Service": recommendation.get("Service", ""),
        "Disposition": recommendation.get("Disposition", ""),
        "Readiness Stage": recommendation.get("ReadinessStage", ""),
        "Impact Area": recommendation.get("ImpactArea", ""),
        "AI Applicability": recommendation.get("AIApplicability", ""),
        "Feature": recommendation.get("Feature", ""),
        "Evidence Basis": recommendation.get("EvidenceBasis", ""),
        "Confidence": recommendation.get("Confidence", ""),
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
        "ai_usage": {
            "copilot_usage": getattr(m365_client, "copilot_usage", {}) if m365_client else {},
            "m365_app_readiness": getattr(m365_client, "m365_app_readiness", {}) if m365_client else {},
            "copilot_dashboard": getattr(m365_client, "copilot_dashboard", {}) if m365_client else {},
            "shadow_ai_usage": getattr(m365_client, "shadow_ai_usage", {}) if m365_client else {},
            "license_summary": getattr(m365_client, "users_summary", {}) if m365_client else {},
        },
        "power_platform_inventory": getattr(pp_client, "power_platform_inventory", {}) if pp_client else {
            "available": False,
            "reason": "Optional inventory not assessed. Enable Manage > Inventory, download its CSV, and pass --power-platform-inventory PATH; this does not change core readiness.",
            "source": "Power Platform unified inventory",
            "freshness": "Unknown",
        },
    }


def _build_data_exposure_sheet(data_exposure_info):
    if not isinstance(data_exposure_info, dict):
        return None

    rows = []
    sources = data_exposure_info.get("sources", {}) or {}
    for source_key, source in sources.items():
        if not isinstance(source, dict) or not source.get("files_loaded"):
            continue
        source_name = "SharePoint Advanced Management" if source_key == "sam" else "Microsoft Purview DSPM"
        rows.append({
            "RecommendationId": "",
            "Flagged By": "",
            "Detail Type": "Source Summary",
            "Source": source_name,
            "Source File": f"{source.get('files_loaded', 0)} file(s)",
            "Source Sheet": "",
            "Workload": "SharePoint / OneDrive",
            "Site Name": "",
            "Site URL": "",
            "Item URL": "",
            "Owner": "",
            "Sensitivity Label": "",
            "Risk Signals": f"{sum((source.get('signals') or {}).values()):,} explicit signal(s)",
            "Signal Count": sum((source.get("signals") or {}).values()),
            "Severity": "",
            "Freshness": str(source.get("freshness", "") or "").title(),
            "Report Date": source.get("latest_report_date", ""),
            "Records Read": source.get("records_read", 0),
            "Max Age (Days)": source.get("max_age_days", ""),
        })

    for evidence_row in data_exposure_info.get("evidence_rows", []) or []:
        row = dict(evidence_row)
        row["Detail Type"] = "Risk Evidence"
        row["Freshness"] = ""
        row["Report Date"] = ""
        row["Records Read"] = ""
        row["Max Age (Days)"] = ""
        rows.append(row)

    if not rows:
        return None

    total_records = sum(
        int(source.get("records_read", 0) or 0)
        for source in sources.values()
        if isinstance(source, dict)
    )
    details = [
        "Only explicit fields from Microsoft Purview DSPM and SharePoint Advanced Management exports are treated as exposure evidence; usage volume alone is not a risk signal.",
        "Source Summary rows record report freshness and volume. Risk Evidence rows identify the affected sites or items and the signal found in the authoritative export.",
    ]
    if data_exposure_info.get("evidence_truncated"):
        details.append(
            "The workbook is limited to the first 25,000 risk rows; headline counts include every parsed record."
        )
    return {
        "rows": rows,
        "summary": f"{total_records:,} SAM/DSPM record(s) were parsed; {len(data_exposure_info.get('evidence_rows', []) or []):,} risk evidence row(s) are included.",
        "details": details,
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
        primary_evidence_text = " ".join(str(record.get(field, "") or "") for field in (
            "Feature", "Observation",
        )).lower()
        fallback_keys = ""

        if (service, feature) in EXPLICIT_FEATURE_FALLBACKS:
            fallback_keys = EXPLICIT_FEATURE_FALLBACKS[(service, feature)]
        elif service == "Entra" and ("risky user" in primary_evidence_text or "identity protection" in primary_evidence_text or "risk detection" in primary_evidence_text):
            fallback_keys = "identity_risk_detail"
        elif service == "Entra" and ("role assignment" in primary_evidence_text or "privileged role" in primary_evidence_text or "pim" in primary_evidence_text):
            fallback_keys = "admin_role_detail"
        elif service == "Entra" and ("user consent" in primary_evidence_text or "admin consent" in primary_evidence_text or "consent policy" in primary_evidence_text):
            fallback_keys = "app_consent_policy_detail"
        elif service == "Entra" and ("enterprise application" in primary_evidence_text or "oauth" in primary_evidence_text or "publisher" in primary_evidence_text):
            fallback_keys = "app_access_detail"
        elif service == "Entra" and "conditional access" in primary_evidence_text:
            fallback_keys = "conditional_access_detail"
        elif service == "Entra" and ("mfa" in primary_evidence_text or "authentication method" in primary_evidence_text or "passwordless" in primary_evidence_text):
            fallback_keys = "authentication_detail"
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
    tenant_id = _iso_text(getattr(entra_client, "tenant_id", "")).lower()
    collection_status = getattr(entra_client, "collection_status", {}) or {}

    app_index = {}

    for service_principal in service_principals:
        app_id = _iso_text(_safe_get(service_principal, "appId"))
        service_principal_id = _iso_text(_safe_get(service_principal, "id"))
        if not service_principal_id:
            continue
        key = service_principal_id.lower()
        publisher_name = _iso_text(_safe_get(service_principal, "publisherName"))
        verified_publisher = _safe_get(service_principal, "verifiedPublisher")
        verified_name = _iso_text(_safe_get(verified_publisher, "displayName")) if verified_publisher else ""
        verified_id = _iso_text(_safe_get(verified_publisher, "verifiedPublisherId")) if verified_publisher else ""
        principal_type = _iso_text(_safe_get(service_principal, "servicePrincipalType"))
        owner_tenant = _iso_text(_safe_get(service_principal, "appOwnerOrganizationId")).lower()
        classification_basis = ""
        if tenant_id and owner_tenant == tenant_id:
            publisher_verified = "Internal"
            publisher_type = "Tenant-owned"
            classification_basis = "App owner organization matches the assessed tenant"
        elif principal_type.lower() == "managedidentity":
            publisher_verified = "Not applicable"
            publisher_type = "Managed identity"
            classification_basis = "Graph servicePrincipalType is ManagedIdentity"
        elif owner_tenant in MICROSOFT_OWNER_TENANT_IDS:
            publisher_verified = "Yes"
            publisher_type = "Microsoft first-party"
            classification_basis = "App owner organization is a documented Microsoft service tenant"
        elif app_id.lower() in MICROSOFT_FIRST_PARTY_APP_IDS:
            publisher_verified = "Yes"
            publisher_type = "Microsoft first-party"
            classification_basis = "Application ID is in the assessment's Microsoft first-party allowlist"
        elif verified_name or verified_id:
            publisher_verified = "Yes"
            publisher_type = "Verified external"
            classification_basis = "Graph returned verifiedPublisher metadata"
        elif owner_tenant:
            publisher_verified = "No"
            publisher_type = "Unverified external"
            classification_basis = "External app owner organization returned without verifiedPublisher metadata"
        else:
            publisher_verified = "Unknown"
            publisher_type = "Unknown"
            classification_basis = "Graph did not return sufficient publisher metadata"
        display_name = _iso_text(_safe_get(service_principal, "displayName"))
        identity_resolution = "Resolved" if display_name and app_id else "Partial"
        app_index.setdefault(
            key,
            {
                "app_id": app_id,
                "service_principal_id": service_principal_id,
                "display_name": display_name or "Unnamed service principal",
                "publisher_name": publisher_name,
                "publisher_verified": publisher_verified,
                "publisher_type": publisher_type,
                "owner_tenant": owner_tenant,
                "classification_basis": classification_basis,
                "identity_resolution": identity_resolution,
                "consent_types": set(),
                "permission_types": set(),
                "scopes": set(),
                "high_privilege_match_count": 0,
            },
        )

    for grant in oauth_grants:
        client_id = _iso_text(_safe_get(grant, "clientId"))
        if not client_id:
            continue
        key = client_id.lower()
        app_record = app_index.setdefault(
            key,
            {
                "app_id": "",
                "service_principal_id": client_id,
                "display_name": "Unresolved service principal",
                "publisher_name": "",
                "publisher_verified": "Unknown",
                "publisher_type": "Unknown",
                "owner_tenant": "",
                "classification_basis": "Service principal metadata was not resolved",
                "identity_resolution": "Unresolved",
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
        is_unverified = app_record["publisher_verified"] == "No" and bool(app_record["consent_types"])
        scope_count = len(normalized_scopes)
        over_privileged = scope_count > 10

        defender_risk = defender_risk_by_app.get(app_record["app_id"].lower(), {}) if app_record["app_id"] else {}
        defender_severity = defender_risk.get("severity", "")
        if defender_risk.get("over_privileged"):
            over_privileged = True

        flagged_because = []
        if is_high_privilege:
            flagged_because.append("High-privilege delegated permissions")
        if is_unverified:
            flagged_because.append("Unverified publisher")
        if over_privileged:
            flagged_because.append("Over-privileged consent footprint")
        if defender_severity:
            flagged_because.append(f"Defender OAuth risk: {defender_severity}")

        if not flagged_because:
            continue

        activity_record = activity_by_app.get(app_record["app_id"].lower(), {}) if app_record["app_id"] else {}
        activity_count = int(activity_record.get("activity_count", 0) or 0)
        last_activity = _iso_text(activity_record.get("last_activity"))
        activity_band = _activity_band(activity_count, activity_available and bool(app_record["app_id"]))
        flag_instance_count = int(app_record.get("high_privilege_match_count", 0) or 0) + int(is_unverified)
        total_flag_instances += flag_instance_count

        rows.append(
            {
                "RecommendationId": "",
                "Flagged By": "",
                "App Display Name": app_record["display_name"],
                "Application (Client) ID": app_record["app_id"],
                "Enterprise Application Object ID": app_record["service_principal_id"],
                "Identity Resolution": app_record["identity_resolution"],
                "Publisher Name": app_record["publisher_name"],
                "Publisher Type": app_record["publisher_type"],
                "Publisher Verification State": app_record["publisher_verified"],
                "App Owner Organization ID": app_record["owner_tenant"],
                "Publisher Classification Basis": app_record["classification_basis"],
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
                "Evidence Confidence": "High" if app_record["identity_resolution"] == "Resolved" else "Medium",
                "Activity Count (30d)": activity_count if activity_available and app_record["app_id"] else "",
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

    unresolved_count = sum(1 for row in rows if row.get("Identity Resolution") == "Unresolved")
    app_inventory_status = collection_status.get("service_principals", {}) or {}
    grant_status = collection_status.get("oauth_grants", {}) or {}
    activity_note = (
        "Thirty-day activity was collected from Entra application sign-in reporting and service principal sign-in activity."
        if activity_available
        else "Thirty-day application activity was not available from the reviewed source data."
    )

    return {
        "rows": rows,
        "summary": (
            f"Focused follow-up includes {_count_phrase(len(rows), 'application')}; "
            f"{_count_phrase(total_flag_instances, 'high-privilege or unverified-publisher grant instance')} "
            f"contributed to headline findings. Unresolved identities: {_count_phrase(unresolved_count, 'application identity record')}."
        ),
        "details": [
            "The review merged enterprise application inventory, delegated permission grants, and OAuth risk indicators into a focused application follow-up list.",
            "Routine Graph access alone is not treated as a finding. Rows are included only when high-impact scopes, an unverified external publisher, an unusually broad consent footprint, or a Defender OAuth risk signal is present.",
            f"Enterprise application collection: {app_inventory_status.get('availability_status', 'unknown')}; OAuth grant collection: {grant_status.get('availability_status', 'unknown')}.",
            activity_note,
        ],
    }


def _build_app_consent_policy_sheet(entra_client):
    if not entra_client:
        return None
    state = (getattr(entra_client, "collection_status", {}) or {}).get("authorization_policy", {}) or {}
    availability = str(state.get("availability_status", "unavailable") or "unavailable")
    if availability not in {"available", "partial"}:
        return None

    policy = getattr(entra_client, "authorization_policy", {}) or {}
    default_permissions = _safe_get(policy, "defaultUserRolePermissions") or {}
    assigned = _safe_get(default_permissions, "permissionGrantPoliciesAssigned", None)
    if assigned is None:
        assigned = _safe_get(default_permissions, "permission_grant_policies_assigned", None)
    if assigned is None:
        return None

    assigned = [str(value) for value in _ensure_list(assigned) if str(value)]
    user_consent_policies = [
        value for value in assigned
        if value.lower().startswith("managepermissiongrantsforself.")
    ]
    rows = [{
        "RecommendationId": "",
        "Flagged By": "",
        "Setting": "Default users may consent to applications",
        "Value": "Yes" if user_consent_policies else "No",
        "Policy ID": "; ".join(user_consent_policies),
        "Source State": availability.title(),
        "Evidence Confidence": "High" if availability == "available" else "Medium",
    }]
    for policy_id in assigned:
        rows.append({
            "RecommendationId": "",
            "Flagged By": "",
            "Setting": "Policy assigned to the default user role",
            "Value": "User self-consent" if policy_id in user_consent_policies else "Other permission grant policy",
            "Policy ID": policy_id,
            "Source State": availability.title(),
            "Evidence Confidence": "High" if availability == "available" else "Medium",
        })

    return {
        "rows": rows,
        "summary": (
            "Default-user application consent is enabled through the listed policy assignments."
            if user_consent_policies else
            "No self-consent policy is assigned to the default user role; application consent requires an administrator or another authorized role."
        ),
        "details": [
            "The effective boundary comes from authorizationPolicy.defaultUserRolePermissions.permissionGrantPoliciesAssigned.",
            "Permission-grant policy definitions are supporting metadata and are not treated as proof that users are assigned those policies.",
        ],
    }


def _build_authentication_sheet(entra_client):
    if not entra_client:
        return None
    state = (getattr(entra_client, "collection_status", {}) or {}).get("auth_methods", {}) or {}
    availability = str(state.get("availability_status", "unavailable") or "unavailable")
    if availability not in {"available", "partial"}:
        return None
    summary = getattr(entra_client, "auth_summary", {}) or {}
    total = int(summary.get("total_users", 0) or 0)
    registered = int(summary.get("mfa_registered", 0) or 0)
    capable = int(summary.get("mfa_capable", 0) or 0)
    passwordless = int(summary.get("passwordless_enabled", 0) or 0)
    rate = round(registered / total * 100, 1) if total else None
    rows = [
        {"RecommendationId": "", "Flagged By": "", "Metric": "Users assessed", "Value": total, "Source State": availability.title(), "Evidence Confidence": "High"},
        {"RecommendationId": "", "Flagged By": "", "Metric": "MFA registered users", "Value": registered, "Source State": availability.title(), "Evidence Confidence": "High"},
        {"RecommendationId": "", "Flagged By": "", "Metric": "MFA registration rate", "Value": "Not calculated" if rate is None else f"{rate}%", "Source State": availability.title(), "Evidence Confidence": "High"},
        {"RecommendationId": "", "Flagged By": "", "Metric": "MFA capable users", "Value": capable, "Source State": availability.title(), "Evidence Confidence": "High"},
        {"RecommendationId": "", "Flagged By": "", "Metric": "Passwordless registered users", "Value": passwordless, "Source State": availability.title(), "Evidence Confidence": "High"},
    ]
    return {
        "rows": rows,
        "summary": f"Microsoft Graph returned authentication registration data for {total} users; {registered} were registered for MFA.",
        "details": [
            "Counts come from reports/authenticationMethods/userRegistrationDetails and contain no user identities in the HTML report.",
            "Use the restricted source or Entra admin center to follow up with individual users.",
        ],
    }


def _build_identity_risk_sheet(entra_client):
    if not entra_client:
        return None
    states = getattr(entra_client, "collection_status", {}) or {}
    user_state = (states.get("risky_users", {}) or {}).get("availability_status", "unavailable")
    detection_state = (states.get("risk_detections", {}) or {}).get("availability_status", "unavailable")
    if user_state not in {"available", "partial"} and detection_state not in {"available", "partial"}:
        return None

    rows = []
    for user in _ensure_list(getattr(entra_client, "risky_users", [])):
        level = _iso_text(_safe_get(user, "riskLevel"))
        state = _iso_text(_safe_get(user, "riskState"))
        if level.lower() not in {"high", "medium"} and state.lower() not in {"atrisk", "confirmedcompromised"}:
            continue
        rows.append({
            "RecommendationId": "", "Flagged By": "", "Record Type": "Risky User",
            "Display Name": _iso_text(_safe_get(user, "userDisplayName")) or "Identity available by object ID",
            "User Principal Name": _iso_text(_safe_get(user, "userPrincipalName")),
            "Object ID": _iso_text(_safe_get(user, "id")),
            "Risk Level": level or "Unknown", "Risk State": state or "Unknown",
            "Risk Detail": _iso_text(_safe_get(user, "riskDetail")),
            "Last Updated": _iso_text(_safe_get(user, "riskLastUpdatedDateTime")),
            "Resolution Status": "Resolved" if _safe_get(user, "id") else "Unresolved",
        })
    for detection in _ensure_list(getattr(entra_client, "risk_detections", [])):
        level = _iso_text(_safe_get(detection, "riskLevel"))
        state = _iso_text(_safe_get(detection, "riskState"))
        rows.append({
            "RecommendationId": "", "Flagged By": "", "Record Type": "Risk Detection",
            "Display Name": _iso_text(_safe_get(detection, "userDisplayName")) or "Identity available by object ID",
            "User Principal Name": _iso_text(_safe_get(detection, "userPrincipalName")),
            "Object ID": _iso_text(_safe_get(detection, "id")),
            "Risk Level": level or "Unknown", "Risk State": state or "Unknown",
            "Risk Detail": _iso_text(_safe_get(detection, "riskEventType")),
            "Last Updated": _iso_text(_safe_get(detection, "lastUpdatedDateTime")) or _iso_text(_safe_get(detection, "detectedDateTime")),
            "Resolution Status": "Resolved" if _safe_get(detection, "id") else "Unresolved",
        })
    if not rows:
        rows.append({
            "RecommendationId": "", "Flagged By": "", "Record Type": "Source Summary",
            "Display Name": "", "User Principal Name": "", "Object ID": "",
            "Risk Level": "None returned", "Risk State": "None returned", "Risk Detail": "",
            "Last Updated": "", "Resolution Status": "Available",
        })
    return {
        "rows": rows,
        "summary": f"{_count_phrase(len(rows), 'current risky-user or risk-detection evidence row')} retained for engineer follow-up.",
        "details": [
            "User identities are retained only in the engineer workbook; the HTML appendix preview shows risk state without names.",
            "Review current state in Microsoft Entra ID Protection before taking remediation action.",
        ],
    }


def _build_admin_role_sheet(entra_client):
    if not entra_client:
        return None

    role_assignments = _ensure_list(getattr(entra_client, "role_assignments", []))
    eligible_assignments = _ensure_list(getattr(entra_client, "role_eligibility_schedules", []))
    time_bound_assignments = _ensure_list(getattr(entra_client, "role_assignment_schedules", []))
    role_definitions = {
        _iso_text(_safe_get(item, "id")).lower(): item
        for item in _ensure_list(getattr(entra_client, "role_definitions", []))
        if _iso_text(_safe_get(item, "id"))
    }
    rows = []

    def identity_key(assignment):
        return (
            _iso_text(_safe_get(assignment, "principalId")).lower(),
            _iso_text(_safe_get(assignment, "roleDefinitionId")).lower(),
            _iso_text(_safe_get(assignment, "directoryScopeId")).lower() or "/",
        )

    def normalized_row(assignment, assignment_type, end_date="", reason=""):
        principal = _safe_get(assignment, "principal") or {}
        role_definition_id = _iso_text(_safe_get(assignment, "roleDefinitionId"))
        role_definition = _safe_get(assignment, "roleDefinition") or role_definitions.get(role_definition_id.lower(), {})
        principal_name = (
            _iso_text(_safe_get(principal, "displayName"))
            or _iso_text(_safe_get(principal, "userPrincipalName"))
        )
        role_name = _iso_text(_safe_get(role_definition, "displayName"))
        principal_type = _iso_text(_safe_get(principal, "@odata.type")).replace("#microsoft.graph.", "")
        resolution = "Resolved" if principal_name and role_name else "Partial" if principal_name or role_name else "Unresolved"
        if resolution != "Resolved":
            reason = (reason + "; " if reason else "") + "Identity resolution required before changing this assignment"
        return {
            "RecommendationId": "",
            "Flagged By": "",
            "Principal Display Name": principal_name or "Unresolved principal",
            "Principal Type": principal_type or "Unknown",
            "Role Name": role_name or "Unresolved role definition",
            "Assignment Type": assignment_type,
            "Principal ID": _iso_text(_safe_get(assignment, "principalId")),
            "Role Definition ID": role_definition_id,
            "Role Template ID": _iso_text(_safe_get(role_definition, "templateId")),
            "Directory Scope ID": _iso_text(_safe_get(assignment, "directoryScopeId")) or "/",
            "Start Date": _iso_text(_safe_get(assignment, "startDateTime")),
            "End Date": end_date,
            "Identity Resolution": resolution,
            "Reason Flagged": reason,
        }

    scheduled_keys = set()
    for assignment in time_bound_assignments:
        schedule_info = _safe_get(assignment, "scheduleInfo") or {}
        expiration = _safe_get(schedule_info, "expiration") or {}
        expiration_type = _iso_text(_safe_get(expiration, "type"))
        end_date = _iso_text(_safe_get(expiration, "endDateTime")) or _iso_text(_safe_get(assignment, "endDateTime"))
        is_permanent = "noexpiration" in expiration_type.lower().replace("_", "")
        scheduled_keys.add(identity_key(assignment))
        rows.append(normalized_row(
            assignment,
            "Permanent Active" if is_permanent else "Time-Bound Active",
            end_date or ("Permanent" if is_permanent else "Unclassified"),
            "Standing privileged assignment" if is_permanent else "Active privileged assignment with schedule",
        ))

    # A unified roleAssignment and its assignmentSchedule describe the same grant. Keep the
    # richer schedule row and retain only base assignments that have no schedule match.
    for assignment in role_assignments:
        if identity_key(assignment) in scheduled_keys:
            continue
        rows.append(normalized_row(
            assignment,
            "Active (Duration Unverified)",
            _iso_text(_safe_get(assignment, "endDateTime")) or "Duration unavailable",
            "Active assignment; no matching schedule was returned",
        ))

    for assignment in eligible_assignments:
        rows.append(normalized_row(
            assignment,
            "Eligible",
            _iso_text(_safe_get(assignment, "endDateTime")),
            "Just-in-time eligible assignment",
        ))

    rows.sort(key=lambda row: (
        str(row.get("Assignment Type", "")).lower(),
        str(row.get("Role Name", "")).lower(),
        str(row.get("Principal Display Name", "")).lower(),
    ))

    permanent_count = sum(row.get("Assignment Type") == "Permanent Active" for row in rows)
    eligible_count = sum(row.get("Assignment Type") == "Eligible" for row in rows)
    time_bound_count = sum(row.get("Assignment Type") == "Time-Bound Active" for row in rows)
    unresolved_count = sum(row.get("Identity Resolution") != "Resolved" for row in rows)

    return {
        "rows": rows,
        "summary": (
            f"{_count_phrase(permanent_count, 'permanent active assignment')}, "
            f"{_count_phrase(eligible_count, 'eligible assignment')}, and "
            f"{_count_phrase(time_bound_count, 'time-bound active assignment')} were reviewed after schedule deduplication."
        ),
        "details": [
            "The review merges active assignments with their schedules so the same grant is not counted twice.",
            f"{_count_phrase(unresolved_count, 'row')} lack either a principal or role display name; their identifiers are retained for follow-up.",
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
            f"Review covered {_count_phrase(ca_summary.get('total', 0), 'Conditional Access policy', 'Conditional Access policies')}, "
            f"including {_count_phrase(ca_summary.get('enabled', 0), 'enabled policy', 'enabled policies')} and "
            f"{_count_phrase(ca_summary.get('require_mfa', 0), 'policy', 'policies')} requiring MFA."
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
            f"Review covered {_count_phrase(b2b_summary.get('total_guests', 0), 'guest account')}, "
            f"including {_count_phrase(b2b_summary.get('guests_with_licenses', 0), 'licensed guest')}."
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
    inventory_evidence = getattr(pp_client, "power_platform_inventory", {}) or {}
    rows.append({
        "RecommendationId": "",
        "Flagged By": "",
        "Detail Type": "Source Coverage",
        "Name": inventory_evidence.get("source", "Power Platform enrichment"),
        "Subtype": "Preview" if inventory_evidence.get("preview") else "Supported source",
        "State": "Available" if inventory_evidence.get("available") else "Supplemental evidence not assessed",
        "Additional Context": inventory_evidence.get("reason", "") or "Freshness: {}".format(inventory_evidence.get("freshness", "Unknown")),
    })

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

    for agent in _ensure_list(getattr(pp_client, "agents", [])):
        properties = _safe_get(agent, "properties") or {}
        rows.append({
            "RecommendationId": "",
            "Flagged By": "",
            "Detail Type": "Agent",
            "Name": _iso_text(_safe_get(properties, "displayName")) or _iso_text(_safe_get(agent, "name")),
            "Subtype": _iso_text(_safe_get(agent, "type")) or "Copilot Studio agent",
            "State": _iso_text(_safe_get(properties, "state")),
            "Additional Context": "Environment: {}".format(_iso_text(_safe_get(properties, "environmentId"))),
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
    license_coverage = users_summary.get("copilot_license_coverage")
    coverage_text = "Not calculated" if license_coverage is None else f"{license_coverage}%"
    add_metric(
        "Users",
        "Copilot Licensed Users",
        users_summary.get("copilot_licensed", 0),
        f"License coverage {coverage_text} of {users_summary.get('coverage_population', 'the assessed population')}.",
    )
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
        "summary": "Aggregate workload activity for Exchange, Teams, SharePoint, OneDrive, and Microsoft 365 Apps.",
        "details": [
            "No user-level activity is included.",
            "Use these baselines to select pilot groups and compare activity after the pilot.",
        ],
    }


def _build_ai_usage_sheet(m365_client):
    if not m365_client:
        return None

    rows = []

    def add(source, period, metric, value, evidence):
        rows.append({
            "RecommendationId": "",
            "Flagged By": "",
            "Evidence Source": source,
            "Period": period,
            "Metric": metric,
            "Value": value,
            "Refresh Date": evidence.get("refresh_date", ""),
            "Freshness": evidence.get("freshness", "Unknown"),
            "Availability": "Available" if evidence.get("available") else "Not assessed",
            "Detail": evidence.get("reason", ""),
        })

    users = getattr(m365_client, "users_summary", {}) or {}
    coverage = users.get("copilot_license_coverage")
    add(
        "Microsoft Graph user licensing",
        "Current",
        "Copilot license coverage of estimated eligible users",
        "Not calculated" if coverage is None else f"{coverage}%",
        {"available": bool(users), "freshness": "Current", "reason": users.get("coverage_population", "")},
    )

    copilot = getattr(m365_client, "copilot_usage", {}) or {}
    if copilot.get("available"):
        returned_evidence = dict(copilot)
        returned_evidence["reason"] = ""
        for period, metrics in (copilot.get("periods", {}) or {}).items():
            add(copilot.get("source", "Microsoft Graph"), period, "Enabled users", metrics.get("enabled_users"), returned_evidence)
            add(copilot.get("source", "Microsoft Graph"), period, "Active users", metrics.get("active_users"), returned_evidence)
            add(copilot.get("source", "Microsoft Graph"), period, "Active-user rate", "N/A" if metrics.get("active_rate") is None else f"{metrics.get('active_rate')}%", returned_evidence)
            add(copilot.get("source", "Microsoft Graph"), period, "Unused enabled licenses", metrics.get("unused_licenses"), returned_evidence)
            add(copilot.get("source", "Microsoft Graph"), period, "Prompts submitted", metrics.get("total_prompts"), returned_evidence)
            add(copilot.get("source", "Microsoft Graph"), period, "Average prompts", metrics.get("average_prompts"), returned_evidence)
            for app_name, app_metrics in (metrics.get("apps", {}) or {}).items():
                add(
                    copilot.get("source", "Microsoft Graph"), period,
                    f"{app_name} active users", app_metrics.get("active_users"), returned_evidence,
                )
    else:
        add(copilot.get("source", "Microsoft Graph Copilot usage"), copilot.get("period", ""), "Copilot usage", "Not assessed", copilot)

    apps = getattr(m365_client, "m365_app_readiness", {}) or {}
    if apps.get("available"):
        for category, summary in (
            ("app", apps.get("app_activity_summary", {}) or {}),
            ("platform", apps.get("platform_activity_summary", {}) or {}),
        ):
            for key, values in summary.items():
                add(
                    apps.get("source", "Microsoft Graph"), "D30",
                    f"M365 {category} peak daily active users: {key}",
                    values.get("peak_daily_active_users"), apps,
                )
                rows[-1]["Detail"] = (
                    f"Activity appeared on {values.get('active_days', 0)} daily report rows; "
                    f"latest reported activity {values.get('latest_activity_date') or 'none'}; "
                    f"latest report date {values.get('latest_report_date') or 'not provided'}."
                )
    else:
        add(apps.get("source", "Microsoft Graph M365 Apps usage"), "D30", "M365 app readiness", "Not assessed", apps)

    for evidence_name in ("copilot_dashboard", "shadow_ai_usage"):
        evidence = getattr(m365_client, evidence_name, {}) or {}
        metric = "Copilot Dashboard supplemental evidence" if evidence_name == "copilot_dashboard" else "External generative AI applications observed"
        value = evidence.get("application_count", evidence.get("records", "Not assessed")) if evidence.get("available") else "Not assessed"
        add(evidence.get("source", evidence_name), evidence.get("period", ""), metric, value, evidence)
        if evidence_name == "copilot_dashboard" and evidence.get("available"):
            add(evidence.get("source", evidence_name), evidence.get("period", ""), "Returning users", evidence.get("returning_users", "Not present"), evidence)
            add(evidence.get("source", evidence_name), evidence.get("period", ""), "Unlicensed Copilot Chat activity", evidence.get("unlicensed_copilot_chat_activity", "Not present"), evidence)
            for action_name, action_value in (evidence.get("detailed_actions", {}) or {}).items():
                add(evidence.get("source", evidence_name), evidence.get("period", ""), f"Dashboard action: {action_name}", action_value, evidence)
        if evidence_name == "shadow_ai_usage" and evidence.get("available"):
            for application in evidence.get("applications", []) or []:
                add(
                    evidence.get("source", evidence_name), evidence.get("period", ""),
                    f"External AI: {application.get('display_name', 'Unknown')}",
                    application.get("active_users", ""), evidence,
                )
                rows[-1]["Detail"] = (
                    f"Risk score {application.get('risk_rating', 'unknown')}; "
                    f"traffic {application.get('traffic_bytes', 0)} bytes; "
                    f"last seen {application.get('last_seen', 'unknown')}."
                )

    return {
        "rows": rows,
        "summary": "Aggregate AI adoption and usage evidence is separated from security readiness and includes source availability and freshness.",
        "details": [
            "License coverage is not active adoption. Active-user rate is calculated only from a successfully returned Microsoft 365 Copilot usage report.",
            "Microsoft 365 workload activity indicates potential pilot fit; it does not prove Copilot value or return on investment.",
            "Optional preview and uploaded evidence is supplemental and cannot change the core readiness decision.",
        ],
    }


def _build_copilot_user_usage_sheet(m365_client):
    if not m365_client:
        return None
    copilot = getattr(m365_client, "copilot_usage", {}) or {}
    source_rows = copilot.get("user_detail", []) or []
    if not source_rows:
        return None
    rows = []
    for source in source_rows:
        if not isinstance(source, dict):
            continue
        period_detail = {}
        for candidate in source.get("copilotActivityUserDetailsByPeriod", []) or []:
            if isinstance(candidate, dict) and candidate.get("reportPeriod") in (28, "28", "D28"):
                period_detail = candidate
                break
        last_activity = source.get("microsoft365CopilotLastActivityDate", source.get("lastActivityDate", ""))
        rows.append({
            "RecommendationId": "",
            "Flagged By": "",
            "User Principal Name": source.get("userPrincipalName", ""),
            "Display Name": source.get("displayName", ""),
            "Period": "D28",
            "Activity State": "Active" if last_activity else "No activity returned",
            "Last Activity Date": last_activity,
            "Active Usage Days": period_detail.get("activeUsageDays", period_detail.get("activeUsageDaysForAllApps", source.get("activeUsageDays", ""))),
            "Prompts Submitted": period_detail.get("promptsSubmitted", period_detail.get("promptsSubmittedForAllApps", source.get("promptsSubmitted", ""))),
            "Copilot Agent Last Activity": source.get("copilotAgentLastActivityDate", ""),
        })
    return {
        "rows": rows,
        "summary": f"{len(rows)} licensed-user Copilot activity row(s) were explicitly requested for the restricted workbook.",
        "details": [
            "This sheet may contain identifiable user activity. Do not distribute it with the customer-facing HTML report.",
            "The HTML report uses aggregate metrics only and never renders these identity fields.",
        ],
    }


def _assessed_plan_names():
    """Service plan names that have a dedicated recommendation module.

    Anything outside this set is inventory only: the assessment records that it is licensed but
    makes no Copilot-specific claim about it, because no module exists to evaluate it.
    """
    from pathlib import Path as _Path

    helper_modules = {
        "m365_insights", "entra_insights", "defender_insights", "purview_insights",
        "pp_insights", "__init__",
    }
    assessed = set()
    recommendations_root = _Path(__file__).resolve().parent.parent / "Recommendations"
    for service_dir in recommendations_root.iterdir():
        if not service_dir.is_dir() or service_dir.name.startswith("__"):
            continue
        for module_path in service_dir.glob("*.py"):
            if module_path.stem not in helper_modules:
                assessed.add(module_path.stem.upper())
    return assessed


def _build_service_plan_inventory_sheet(m365_info):
    """Full licensed service plan inventory.

    Plans without a dedicated recommendation module used to produce a boilerplate card each
    ("X is active ... providing infrastructure and services that M365 Copilot depends on"),
    which dominated the report without saying anything. Those cards are suppressed; this sheet
    keeps the underlying inventory so nothing is lost, and marks which plans were actually
    evaluated against Copilot criteria.
    """
    if not isinstance(m365_info, dict):
        return None

    licenses = m365_info.get("licenses") or []
    if not licenses:
        return None

    assessed = _assessed_plan_names()
    rows = []
    for license_entry in licenses:
        if not isinstance(license_entry, dict):
            continue
        sku_raw = license_entry.get("sku_part_number", "") or "Unknown"
        sku_friendly = get_friendly_sku_name(sku_raw)
        for plan in license_entry.get("service_plans", []) or []:
            if not isinstance(plan, dict):
                continue
            plan_name = str(plan.get("name", "") or "")
            if not plan_name:
                continue
            was_assessed = plan_name.upper() in assessed
            rows.append({
                "RecommendationId": "",
                "Flagged By": "",
                "SKU": sku_friendly,
                "SKU Part Number": sku_raw,
                "Service Plan": get_friendly_plan_name(plan_name),
                "Service Plan Name": plan_name,
                "Provisioning Status": str(plan.get("status", "") or "Unknown"),
                "Assessed": "Yes" if was_assessed else "No",
                "Reason Flagged": (
                    "Evaluated against Copilot readiness criteria" if was_assessed
                    else "Licensed; inventory only - no Copilot-specific criteria defined"
                ),
            })

    if not rows:
        return None

    rows.sort(key=lambda row: (str(row.get("SKU", "")).lower(),
                               str(row.get("Service Plan", "")).lower()))

    assessed_count = sum(1 for row in rows if row["Assessed"] == "Yes")
    seats = sum(int(entry.get("enabled") or 0) for entry in licenses if isinstance(entry, dict))

    return {
        "rows": rows,
        "summary": (
            f"{len(rows)} licensed service plan(s) across {len(licenses)} SKU(s) "
            f"({seats} seat(s) enabled). {assessed_count} plan(s) were evaluated against Copilot "
            f"readiness criteria; the remaining {len(rows) - assessed_count} are recorded as "
            f"inventory only."
        ),
        "details": [
            "Every licensed service plan is listed here with its provisioning status, so the licensing picture is complete even though only Copilot-relevant plans generate findings.",
            "Plans marked Assessed = No have no Copilot-specific criteria defined, so the assessment makes no claim about them beyond the fact that they are licensed.",
        ],
    }

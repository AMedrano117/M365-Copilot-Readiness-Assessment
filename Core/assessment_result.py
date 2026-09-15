"""One evidence-qualified assessment shared by customer and technical deliverables."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import re

from .assessment_model import NEGATIVE_CONDITION, enrich_assessment_record
from .cross_provider_assessment import METHODOLOGY_VERSION
from .evidence_contract import (
    EVIDENCE_SCHEMA_VERSION, RECONCILIATION_VERSION, evaluation_day,
    normalize_observation, parse_date, reconcile_observations, stable_id,
)


DOMAINS = (
    ("identity", "Identity and access", "Identity and access administrator", "Copilot uses the access of the person signed in. Protect accounts and privileged roles before expanding access."),
    ("content", "Content access and ownership", "SharePoint administrator and content owners", "Copilot can surface content a user already has permission to read. Broad access and absent owners need separate review."),
    ("data_protection", "Data protection", "Information protection and compliance owner", "Classification, data loss prevention, retention and audit controls govern how sensitive content is used and investigated."),
    ("applications", "Applications and connectors", "Application and security administrators", "Application grants and connected sources can extend access to organizational information."),
    ("endpoints", "Endpoints and threat protection", "Endpoint and security operations owners", "Managed devices and incident response help protect the accounts and data used with Copilot."),
    ("licensing", "Copilot licensing and prerequisites", "Microsoft 365 administrator", "License assignment and application prerequisites determine who can use the intended Copilot experience."),
    ("adoption", "Pilot suitability and adoption", "Business sponsor and adoption lead", "Usage and workload activity help choose a pilot and measure its outcomes; they do not prove security readiness."),
)
OPTIONAL_DOMAINS = (
    ("agents", "Agents and extensibility", "Agent platform owner", "Agent identities, knowledge sources and actions need an accountable owner and bounded permissions."),
    ("external_ai", "External AI services", "AI service and security owners", "Approved products, contractual terms and data handling need review for the scoped external services."),
)
DOMAIN_LOOKUP = {row[0]: row for row in (*DOMAINS, *OPTIONAL_DOMAINS)}

# Required questions deliberately separate permissions, settings and lifecycle.
# EvidenceKey identifies the question; a service-wide licensing row cannot pass it.
QUESTIONS = (
    ("IDENTITY.AUTH", "identity", "Confirm sign-in policy coverage for the pilot", ("conditional_access_detail",), ("conditional_access", "sign-in_policy"), True),
    ("IDENTITY.MFA", "identity", "Confirm multifactor authentication registration coverage", ("authentication_detail",), ("authentication_registration", "mfa_registration", "enrolled_in_mfa"), True),
    ("IDENTITY.ADMIN", "identity", "Review privileged access and administrative roles", ("admin_role_detail",), ("admin_role", "privileged", "role_assignment"), True),
    ("CONTENT.SHARING", "content", "Confirm tenant sharing defaults", ("sharepoint_governance_detail",), ("sharing_settings", "tenant_sharing"), True),
    ("CONTENT.PERMISSIONS", "content", "Review broad permissions to business content", ("data_exposure_detail",), ("oversharing", "permissions_snapshot", "broad_access"), True),
    ("CONTENT.OWNERSHIP", "content", "Confirm accountable owners and inactive-site decisions", ("sharepoint_lifecycle_detail",), ("lifecycle", "ownerless", "inactive_sites"), True),
    ("DATA.LABELS", "data_protection", "Confirm sensitivity label definitions for business content", ("purview_policy_detail",), ("sensitivity_label", "labels_are_defined"), True),
    ("DATA.PUBLISHING", "data_protection", "Confirm sensitivity labels are published to intended users", ("purview_policy_detail",), ("label_policies", "publishing", "publish", "publication"), True),
    ("DATA.DLP", "data_protection", "Confirm data loss prevention coverage and enforcement", ("purview_policy_detail",), ("dlp", "data_loss_prevention"), True),
    ("DATA.AUDIT", "data_protection", "Confirm audit coverage for investigation", ("purview_policy_detail",), ("audit",), True),
    ("DATA.RETENTION", "data_protection", "Confirm content retention requirements", ("purview_policy_detail",), ("retention",), True),
    ("DATA.EXPOSURE", "data_protection", "Confirm who can access sensitive pilot content", ("data_exposure_detail",), ("sensitive_data", "sensitive-data", "dspm"), True),
    ("APPS.CONSENT", "applications", "Review application consent and granted permissions", ("app_consent_policy_detail", "app_access_detail"), ("consent", "oauth", "app_access"), True),
    ("APPS.CONNECTIONS", "applications", "Confirm connected sources and their access boundaries", ("external_connection_detail",), ("external_connection", "connector"), True),
    ("ENDPOINT.POSTURE", "endpoints", "Confirm the pilot device and browser protection baseline", ("defender_device_detail",), ("device", "endpoint", "browser"), True),
    ("THREAT.INCIDENTS", "endpoints", "Review active incidents affecting the pilot", ("defender_incident_detail",), ("incident", "threat"), True),
    ("LICENSE.ASSIGNMENT", "licensing", "Confirm Copilot license assignment for the pilot population", ("copilot_readiness_detail", "ai_usage_detail"), ("copilot_license", "license_coverage", "portal_copilot_readiness"), True),
    ("LICENSE.APPS", "licensing", "Confirm application prerequisites for pilot users", ("copilot_readiness_detail", "ai_usage_detail"), ("m365_app_readiness", "portal_copilot_readiness", "prerequisite"), True),
    ("ADOPTION.BASELINE", "adoption", "Agree a pilot population, usage baseline and success measures", ("ai_usage_detail", "m365_activity_detail"), ("copilot_usage", "activity", "adoption", "pilot"), False),
)
OPTIONAL_QUESTIONS = (
    ("AGENTS.BOUNDARIES", "agents", "Confirm agent owners, identities, knowledge sources and action permissions", ("power_platform_detail",), ("agent", "power_platform", "connector"), False),
    ("EXTERNAL.SERVICES", "external_ai", "Confirm approved external AI products and their data handling", (), ("external_ai", "provider", "shadow_ai"), False),
)


def _text(row):
    return " ".join(str(row.get(key) or "") for key in (
        "Service", "Feature", "Observation", "FindingKey", "ImpactArea", "EvidenceKey", "Area", "Key",
    )).lower()


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def domain_for(row):
    explicit = row.get("DomainId") or row.get("domain_id")
    if explicit in DOMAIN_LOOKUP:
        return explicit
    text = _text(row)
    service = str(row.get("Service") or "").lower()
    evidence_keys = {key.strip() for key in str(row.get("EvidenceKey") or "").split(";")}
    if "sharepoint_governance_detail" in evidence_keys or row.get("FindingKey") == "data_exposure.snapshot_scope":
        return "content"
    if service == "shadow ai" or any(word in text for word in ("external ai", "third-party ai", "shadow_ai")):
        return "external_ai"
    if service in {"power platform", "copilot studio"}:
        return "agents"
    if any(word in text for word in ("consent", "oauth", "app_access", "application access", "external_connection", "connector")):
        return "applications"
    if any(word in text for word in ("identity", "conditional access", "conditional_access", "multifactor", "mfa", "privileged", "admin role", "admin_role", "guest access", "authentication")):
        return "identity"
    if any(word in text for word in ("endpoint", "device", "intune", "incident", "threat", "browser")) or service == "defender":
        return "endpoints"
    if service in {"purview"} or any(word in text for word in ("data protection", "sensitivity", "label", "dlp", "retention", "audit", "rights management", "rms", "dspm")):
        return "data_protection"
    if service == "data exposure" or any(word in text for word in ("overshar", "lifecycle", "ownerless", "sharing", "permission", "content access", "sharepoint governance")):
        return "content"
    if any(word in text for word in ("license", "licensing", "prerequisite", "app readiness", "m365_app_readiness", "portal_copilot_readiness")):
        return "licensing"
    if any(word in text for word in ("usage", "activity", "adoption", "pilot", "training", "meetings")):
        return "adoption"
    if "sharepoint" in text or "onedrive" in text:
        return "content"
    return "identity" if service == "entra" else "licensing"


def _scope_domains(bundle):
    enabled = {row[0] for row in DOMAINS}
    profile = bundle.get("assessment_profile") or {}
    from .control_reviews import _profile_raw
    raw = _profile_raw(profile)
    scope = raw.get("scope") or {}
    if isinstance(scope, list):
        scope = {str(item): True for item in scope}
    if not isinstance(scope, dict):
        scope = {}
    products = profile.get("products") or raw.get("ai_products") or []
    products = products if isinstance(products, list) else []
    use_cases = profile.get("use_cases") or raw.get("use_cases") or []
    products = [*products, *(use_cases if isinstance(use_cases, list) else [])]
    product_text = " ".join(str(product.get("name", product.get("product", product))) if isinstance(product, dict) else str(product) for product in products).lower()
    if scope.get("agents") is True or any(word in product_text for word in ("copilot studio", "agent", "power platform")):
        enabled.add("agents")
    external_provider = any(isinstance(product, dict) and str(product.get("provider") or "").strip().lower() not in {"", "microsoft", "microsoft 365"} for product in products)
    if scope.get("external_ai") is True or external_provider or any(word in product_text for word in ("openai", "chatgpt", "claude", "gemini", "external")):
        enabled.add("external_ai")
    return enabled


def _provenance(row, bundle, expected_tenant_id):
    context = bundle.get("collection_context") or {}
    service = str(row.get("Service") or "").lower()
    states = bundle.get("source_statuses") or {}
    keys = [key.strip() for key in str(row.get("EvidenceKey") or "").split(";") if key.strip()]
    source = states.get(row.get("EvidenceSource"), {})
    for key, state in states.items():
        if source:
            break
        if not isinstance(state, dict):
            continue
        if any(key == item or key.removeprefix(service + "_") == item.removesuffix("_detail") for item in keys):
            source = state
            break
    if not source:
        source = next((state for key, state in states.items() if isinstance(state, dict)
                       and key.startswith(service + "_") and state.get("source_type") == "purview_cache"), {})
    # Service caches carry their own timestamp; the report build does not refresh it.
    date = row.get("ObservationDate") or row.get("ObservedAt") or row.get("ReportDate") or source.get("collected_at") or source.get("refresh_date")
    source_type = row.get("SourceType") or source.get("source_type") or "tenant_collection"
    source_file = row.get("SourceFile") or source.get("source_file") or context.get("source_file", "")
    if not date and source_type not in {"purview_cache", "portal_export", "prior_assessment"}:
        date = context.get("collected_at")
    # A row emitted by an imported portal report must use its report date, not the
    # date of an unrelated directory collection.
    exposure = bundle.get("data_exposure") or {}
    if service == "data exposure":
        source_type = "portal_export"
        date = row.get("ObservationDate") or row.get("ReportDate")
        evidence_dates = []
        for detail in (exposure.get("details") or []):
            if isinstance(detail, dict):
                candidate = detail.get("report_date") or detail.get("Report Date")
                if parse_date(candidate):
                    evidence_dates.append(str(candidate))
        if not date and len(set(evidence_dates)) == 1:
            date = evidence_dates[0]
    return {
        "observed_at": date or "", "source_type": source_type, "source_file": source_file,
        "tenant_id": row.get("TenantId") or source.get("tenant_id") or expected_tenant_id or "",
        "scope": row.get("EvidenceScope") or source.get("scope") or (
            "Tenant configuration returned by Purview" if source_type == "purview_cache" else
            "Assessed tenant collection" if context.get("collected_at") else ""),
        "complete": row.get("EvidenceComplete", source.get("complete", not source.get("truncated", False))) and source.get("availability_status", "available") == "available",
        "truncated": source.get("truncated", False), "source_schema": source.get("schema_version", ""),
        "source_hash": row.get("SourceHash") or source.get("source_hash", ""),
        **({"max_age_days": row['EvidenceMaxAgeDays']} if row.get('EvidenceMaxAgeDays') else {}),
        **({"qualifications": ["Report date confirmed by the operator; the original export is retained unchanged."]}
           if row.get("EvidenceDateBasis") == "Operator-confirmed report date" else {}),
    }


def _qualify_record(original, bundle, day, expected_tenant_id, *, historical=False):
    row = enrich_assessment_record(original)
    if original.get("ActionType") == "Confirmation" and original.get("OriginalFeature"):
        row["Feature"] = original["OriginalFeature"]
        row["Recommendation"] = original.get("OriginalRecommendation", original.get("Recommendation", ""))
    observation = str(row.get("Observation") or "")
    # Old modules sometimes mislabeled a service-plan entitlement as tenant
    # evidence. A positive SKU statement never establishes configured assurance.
    inventory_only = bool(re.search(r"\b(?:is active in|are active in|included in|is licensed through|service plan is (?:active|licensed))\b", observation, re.I))
    if (row.get("Service") == "M365" and row.get("Disposition") in {"Assurance", "Opportunity"}
            and not original.get("FindingKey")):
        # Product modules reuse tenant user/file volumes in capability narratives.
        # Those counts do not demonstrate deployment of the named product.
        row.update(Disposition="Reference", EvidenceBasis="Workload inventory context", Confidence="Low")
    if inventory_only and not NEGATIVE_CONDITION.search(observation):
        row.update(Disposition="Reference", EvidenceBasis="License signal", Confidence="Low")
    if row.get("FindingKey") == "offline.tenant_coverage" or str(row.get("FindingKey") or "").startswith("portal.unrecognized."):
        # Required control questions account for actual gaps. Build mode and an
        # unsupported supplemental file belong in the operator receipt.
        row.update(Disposition="Reference", EvidenceBasis="Collection context")
    domain = domain_for(row)
    meta = _provenance(row, bundle, expected_tenant_id)
    historical = historical or original.get("SourceType") == "prior_assessment"
    if historical:
        prior = bundle.get("prior_report") or {}
        meta.update(source_type="prior_assessment", source_file=original.get("SourceFile") or prior.get("source_file", ""),
                    observed_at=original.get("ObservationDate") or original.get("ObservedAt") or "",
                    tenant_id=original.get("TenantId") or prior.get("tenant_id", ""), scope=original.get("EvidenceScope", ""), complete=False)
        row["PriorReportDate"] = original.get("PriorReportDate") or prior.get("generated_at", "")
        row["OriginalMethodologyVersion"] = original.get("OriginalMethodologyVersion") or prior.get("methodology_version") or original.get("MethodologyVersion", "")
        row["OriginalRecommendationId"] = original.get("OriginalRecommendationId") or row.get("RecommendationId", "")
        if row.get("Disposition") == "Coverage":
            # An earlier failed/missing collection is not a permanent tenant
            # finding. Current required questions describe today's evidence gaps.
            row.update(Disposition="Reference", HistoricalCoverage="Yes", EvidenceBasis="Historical collection context")
    gap = row.get("Disposition") == "Coverage"
    metric = row.get("FindingKey") or f"{row.get('Service', '')}.observation.{_finding_identity(row)}"
    fact = normalize_observation({
        **meta, "domain_id": domain, "control_id": row.get("ControlId", ""),
        "metric_id": metric, "value": None if gap else row.get("Observation", ""),
        "unit": "control observation", "availability": "unknown" if gap else "available",
        "historical_conclusion": historical,
    }, evaluation_date=day, expected_tenant_id=expected_tenant_id)
    status = "gap" if gap else "historical" if historical else "supported" if fact["freshness"] == "current" else fact["freshness"]
    if status == "supported" and (not fact["complete"] or not fact["scope"] or not fact["tenant_id"]
                                  or row.get("EvidenceBasis") == "License signal"):
        status = "limited"
    qualification = fact["qualification"]
    if historical:
        report_date = f" dated {parse_date(row['PriorReportDate']).isoformat()}" if parse_date(row["PriorReportDate"]) else " with no recorded report date"
        qualification = f"Earlier assessment{report_date}. Its conclusion has not been revalidated from saved facts. " + qualification
    row.update({
        "DomainId": domain, "Domain": DOMAIN_LOOKUP[domain][1],
        "OwnerRole": row.get("OwnerRole") or DOMAIN_LOOKUP[domain][2],
        "ObservationDate": fact["observed_at"], "SourceType": fact["source_type"],
        "SourceFile": fact["source_file"], "Freshness": fact["freshness"],
        "EvidenceStatus": status, "Qualification": qualification.strip(),
        "Historical": "Yes" if historical else "No", "EvidenceId": fact["evidence_id"],
        "EvidenceScope": fact["scope"], "EvidenceComplete": fact["complete"],
        "MethodologyVersion": METHODOLOGY_VERSION,
    })
    if status != "supported" and row.get("Disposition") in {"Action", "Assurance"}:
        row["Confidence"] = "Unknown" if fact["freshness"] in {"unknown", "future"} else "Low"
    return row, fact


def _finding_identity(row):
    return str(row.get("FindingKey") or "").lower() or stable_id([
        str(row.get("Service") or "").lower(), str(row.get("Feature") or "").lower(),
        str(row.get("Observation") or "").strip(), str(row.get("Recommendation") or "").strip(),
        row.get("ControlId"), row.get("AffectedObjectIds"),
    ])


def _deduplicate_records(records):
    grouped = defaultdict(list)
    for row in records:
        grouped[(_finding_identity(row), str(row.get("EvidenceScope") or ""))].append(row)
    selected, archived = [], []
    priority_rank = {"High": 0, "Medium": 1, "Low": 2}
    for group in grouped.values():
        supported = [row for row in group if row["EvidenceStatus"] == "supported"]
        latest = max((row.get("ObservationDate", "") for row in supported), default="")
        candidates = [row for row in supported if row.get("ObservationDate") == latest] or [row for row in group if row["Historical"] != "Yes"] or group
        winner = min(candidates, key=lambda row: (priority_rank.get(row.get("Priority"), 3), row.get("Feature", "")))
        winner = dict(winner)
        if len({(row.get("Disposition"), row.get("Observation")) for row in candidates}) > 1 and supported:
            winner["EvidenceStatus"] = "conflict"
            winner["Disposition"] = "Action"
            winner["Qualification"] = "Comparable observations for the same date disagree. Confirm the condition before relying on it."
        # Do not treat a broad domain/control match as proof a historical issue was fixed.
        history = [row for row in group if row["Historical"] == "Yes"]
        winner["RelatedEvidenceIds"] = list(dict.fromkeys(row["EvidenceId"] for row in group))
        winner["HistoricalReferences"] = [{
            "RecommendationId": row.get("OriginalRecommendationId", ""),
            "ReportDate": row.get("PriorReportDate", ""), "SourceFile": row.get("SourceFile", ""),
        } for row in history]
        if history and supported:
            winner["Qualification"] = (winner["Qualification"] + " The same condition appeared in an earlier assessment; the comparable current evidence is used here.").strip()
        selected.append(winner)
        archived.extend(dict(row, Selection="superseded") for row in group if row is not next((item for item in group if item["EvidenceId"] == winner["EvidenceId"]), None))
    return selected, archived


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip().replace(",", "").rstrip("%")
    try:
        number = float(text)
        return int(number) if number.is_integer() else number
    except ValueError:
        return None


_USAGE_METRICS = {
    "enabled_users": ("Enabled users", "users"), "active_users": ("Active users", "users"),
    "active_rate": ("Active-user rate", "%"), "unused_licenses": ("Unused enabled licenses", "licenses"),
    "total_prompts": ("Prompts submitted", "prompts"), "average_prompts": ("Average prompts", "prompts per user"),
}
_COPILOT_APPS = {"teams", "word", "powerpoint", "outlook", "excel", "onenote", "loop", "edge",
                 "copilot_chat", "copilot_chat_work", "copilot_chat_web"}


def _window_label(period):
    matched = re.fullmatch(r"D?(\d+)", str(period or ""), re.IGNORECASE)
    if matched:
        return f"{int(matched.group(1))} days"
    return "Period not recorded" if not period else "Snapshot" if period == "Current" else str(period)


def _adoption_observations(bundle, day, expected_tenant_id):
    facts = []
    context = bundle.get("collection_context") or {}
    usage = (bundle.get("ai_usage") or {}).get("copilot_usage") or {}

    def add(metric, label, value, unit, source, period="", historical=False, kind="summary", population=None):
        availability = source.get("availability_status") or source.get("Availability") or ("available" if source.get("available") else "unknown")
        if str(availability).lower() in {"not assessed", "not present", "not calculated"}:
            availability = "unknown"
        observation_date = source.get("refresh_date") or source.get("Refresh Date") or source.get("observed_at") or ""
        facts.append({
            "domain_id": "adoption", "control_id": "ADOPTION.BASELINE", "metric_id": metric,
            "label": label, "value": _number(value), "unit": unit,
            "availability": str(availability).lower(), "observed_at": observation_date,
            "window": period, "window_label": _window_label(period), "reporting_basis": "reporting period",
            "metric_kind": kind,
            "scope": source.get("scope") or ("Assessed tenant" if not historical and expected_tenant_id else ""),
            "population": population or ("Included Copilot Chat" if metric.startswith("copilot_chat.") else "Paid Microsoft 365 Copilot"),
            "tenant_id": source.get("tenant_id") or ("" if historical else expected_tenant_id) or "",
            "source_type": "prior_assessment" if historical else "tenant_collection",
            "source_file": source.get("source_file") or context.get("source_file", ""),
            "source_label": source.get("Evidence Source") or source.get("source") or "Microsoft 365 workload evidence",
            "source_schema": source.get("schema_version", ""), "source_hash": source.get("source_hash", ""),
            "complete": source.get("complete", not historical and not source.get("truncated", False)),
            "truncated": source.get("truncated", False), "max_age_days": 7,
            "qualifications": ["Historical aggregate usage; it does not establish current activity."] if historical else [],
        })

    for period, metrics in (usage.get("periods") or {}).items():
        for key, (label, unit) in _USAGE_METRICS.items():
            if key in metrics:
                add("copilot." + key, label, metrics[key], unit, usage, period)
        for name, app in (metrics.get("apps") or {}).items():
            if _slug(name) in _COPILOT_APPS:
                add("copilot.apps." + _slug(name) + ".active_users", name + " active users",
                    app.get("active_users"), "users", usage, period, kind="application")
    if not usage.get("periods"):
        add("copilot.active_users", "Active users", None, "users", usage)
    prior = bundle.get("prior_report") or {}
    aliases = {_slug(label): key for key, (label, _) in _USAGE_METRICS.items()}
    def from_sheet(title, sheet, historical):
        for raw in sheet.get("rows", []):
            metric_name = str(raw.get("Metric") or "")
            metric_key = aliases.get(_slug(metric_name))
            source = dict(raw, source_file=prior.get("source_file", "") if historical else context.get("source_file", ""),
                          tenant_id=prior.get("tenant_id", "") if historical else expected_tenant_id)
            if "Availability" not in source:
                source["Availability"] = "available" if _number(raw.get("Value")) is not None else "unknown"
            period = str(raw.get("Period") or "")
            if not historical and (metric_key or metric_name.lower().endswith(' active users')) and period in (usage.get('periods') or {}):
                # The workbook summary is derived from this same usage payload.
                # Preserve its population and date so it reconciles as a duplicate,
                # while a disagreeing derived value remains an explicit conflict.
                source = {**usage, **source, 'scope': usage.get('scope'),
                    'complete': usage.get('complete', not usage.get('truncated', False)),
                    'refresh_date': raw.get('Refresh Date') or usage.get('refresh_date')}
            if not historical and (period == 'Current' or raw.get('Workload') == 'Users') and not source.get('Refresh Date'):
                source['observed_at'] = context.get('collected_at', '')
                period = period or 'Current'
            if _slug(title) == "m365_activity_detail":
                workload = str(raw.get("Workload") or "")
                metric = f"m365.{_slug(workload)}.{_slug(metric_name)}"
                unit = "users" if "user" in metric_name.lower() or "office 365 active" in metric_name.lower() else (
                    "sites" if "site" in metric_name.lower() else "files" if "file" in metric_name.lower() else
                    "accounts" if "account" in metric_name.lower() else "GB" if "(gb)" in metric_name.lower() else
                    "%" if "rate" in metric_name.lower() else "meetings" if "meeting" in metric_name.lower() else
                    "messages" if "sent" in metric_name.lower() else "count")
                add(metric, f"{workload}: {metric_name}", raw.get("Value"), unit, source, period,
                    historical, kind="workload", population="Microsoft 365 workload activity")
            elif metric_key:
                add("copilot." + metric_key, metric_name, raw.get("Value"), _USAGE_METRICS[metric_key][1], source, period, historical)
            elif metric_name.lower().endswith(" active users") and _slug(metric_name[:-13]) in _COPILOT_APPS:
                add("copilot.apps." + _slug(metric_name[:-13]) + ".active_users", metric_name, raw.get("Value"), "users", source,
                    period, historical, kind="application")
            elif metric_name.lower().startswith("m365 ") and "peak daily active users:" in metric_name.lower():
                add("m365.apps." + _slug(metric_name), metric_name, raw.get("Value"), "users", source, period, historical,
                    kind="application_readiness", population="Microsoft 365 application activity")
            elif metric_name == "Copilot license coverage of estimated eligible users":
                add("copilot.estimated_license_coverage", metric_name, raw.get("Value"), "%", source, period, historical,
                    kind="license_assignment", population="Estimated eligible Microsoft 365 users")
            elif metric_name == "Unlicensed Copilot Chat activity":
                add("copilot_chat.unlicensed_activity", metric_name, raw.get("Value"), "reported activity", source, period,
                    historical, kind="included_chat")

    for title, sheet in (prior.get("sheets") or {}).items():
        if _slug(title) in {"ai_adoption_usage", "ai_usage", "m365_activity_detail"}:
            from_sheet(title, sheet, True)
    for key in ("ai_usage_detail", "m365_activity_detail"):
        sheet = (bundle.get("sheets") or {}).get(key) or {}
        if sheet:
            from_sheet("M365 Activity Detail" if key == "m365_activity_detail" else "AI Adoption Usage", sheet, False)
    # Older snapshots store aggregate periods in collection coverage instead.
    for source in prior.get("collection_coverage") or []:
        if not isinstance(source, dict) or "copilot" not in str(source.get("Source", "")).lower():
            continue
        for period, metrics in (source.get("periods") or {}).items():
            for key, (label, unit) in _USAGE_METRICS.items():
                if key in metrics:
                    add("copilot." + key, label, metrics[key], unit,
                        dict(source, source_file=prior.get("source_file", ""), tenant_id=prior.get("tenant_id", "")), period, True)
    return reconcile_observations(facts, evaluation_date=day, expected_tenant_id=expected_tenant_id)


def _matches_question(row, question):
    control, domain, _, keys, markers, _ = question
    if row.get("FindingKey") == "data_exposure.coverage.sensitive-data_exposure_evidence":
        return control == "DATA.EXPOSURE"
    canonical_ids = {item[0] for item in (*QUESTIONS, *OPTIONAL_QUESTIONS)}
    if row.get("ControlId") in canonical_ids:
        return row.get("ControlId") == control
    if row["DomainId"] != domain:
        return False
    if row.get("ControlId") == control and row.get("EvidenceBasis") != "License signal":
        return True
    evidence_keys = {key.strip() for key in str(row.get("EvidenceKey") or "").split(";")}
    # Purview uses a shared tab for many different controls; require the relevant
    # metric name as well so one DLP policy does not establish audit or labeling.
    key_match = bool(evidence_keys.intersection(keys))
    # Impact-area labels contain words such as connectors; those labels are not
    # evidence that an actual connector inventory or prerequisite was assessed.
    specific_text = " ".join(str(row.get(key) or "") for key in ("Feature", "Observation", "FindingKey", "EvidenceKey")).lower().replace(" ", "_")
    marker_match = any(marker in specific_text for marker in markers)
    if control == "APPS.CONNECTIONS":
        return "external_connection_detail" in evidence_keys or any(marker in specific_text for marker in (
            "external_connection", "graph_connector", "connected_grounding_source"))
    if control == "LICENSE.APPS":
        return "copilot_readiness_detail" in evidence_keys or any(marker in specific_text for marker in (
            "m365_app_readiness", "portal_copilot_readiness", "application_prerequisite", "app_readiness"))
    if control == "LICENSE.ASSIGNMENT":
        return "copilot_readiness_detail" in evidence_keys or any(marker in specific_text for marker in (
            "copilot_license", "license_coverage", "portal_copilot_readiness"))
    if control == "ADOPTION.BASELINE":
        # Workload activity establishes a baseline measurement, not an agreed
        # business population, success measures or decision to proceed.
        return (row.get("ControlId") == control or row.get("FindingKey") in {"pilot_plan", "adoption.pilot_plan"}) and row.get("EvidenceBasis") == "Reviewed pilot plan"
    if control == "CONTENT.PERMISSIONS":
        text = _text(row).replace(" ", "_")
        permissions = any(marker in text for marker in (
            "overshar", "permission_snapshot", "permissions_snapshot", "broad_access",
            "everyone", "anyone", "sharing_link", "item_permission", "sensitive_data_access",
        ))
        return key_match and permissions or marker_match
    if control == "THREAT.INCIDENTS":
        return key_match and "incident" in str(row.get("Observation") or "").lower()
    if control == "ENDPOINT.POSTURE":
        # An inventory count or service-plan entitlement cannot establish a
        # population-wide device and browser baseline. Adapters must identify an
        # actual reviewed baseline, or a specific measured device failure.
        return (key_match and (row.get("EvidenceBasis") == "Reviewed endpoint baseline" or row.get("Disposition") in {"Action", "Coverage"})
                or row.get("Disposition") == "Coverage" and marker_match)
    return (key_match and marker_match) if domain == "data_protection" else key_match or marker_match


def _gap(question, day):
    control, domain, title, keys, _, security_gate = question
    identifier = "GAP-" + control.replace(".", "-")
    return {
        "RecommendationId": identifier, "FindingKey": "coverage." + control.lower(),
        "ControlId": control, "MethodologyVersion": METHODOLOGY_VERSION,
        "Service": DOMAIN_LOOKUP[domain][1], "Feature": title,
        "Observation": "The supplied evidence does not establish this check for the intended pilot population.",
        "Recommendation": title + ". Record the scope, observation date and the result of the review.",
        "Priority": "Medium", "Status": "Not Assessed", "Disposition": "Coverage",
        "ReadinessStage": "Before pilot" if security_gate else "Pilot planning",
        "ImpactArea": DOMAIN_LOOKUP[domain][1], "DomainId": domain, "Domain": DOMAIN_LOOKUP[domain][1],
        "OwnerRole": DOMAIN_LOOKUP[domain][2], "CompletionEvidence": "A dated review covering the pilot population, with the result and any agreed remediation recorded.",
        "EvidenceKey": ";".join(keys), "EvidenceSheet": "", "EvidenceAvailable": "No",
        "EvidenceBasis": "Not verified", "EvidenceStatus": "gap", "Confidence": "Unknown",
        "ActionType": "Evidence", "Historical": "No", "ObservationDate": "", "Freshness": "unknown",
        "Qualification": "This is an unanswered assessment question, not a measured control failure.",
        "SourceType": "assessment_requirement", "SourceFile": "", "SecurityGate": security_gate,
        "EvidenceId": "", "EvidenceScope": "", "EvidenceComplete": False,
    }


def _supports_question(row, control):
    if row.get("EvidenceStatus") != "supported" or row.get("Disposition") not in {"Action", "Assurance"}:
        return False
    if row.get("Disposition") == "Action":
        return True
    # Inventory and configuration summaries establish that policies exist. They
    # do not establish that the intended users/content are covered or that the
    # relevant rules enforce protection. These checks need an explicit scoped
    # control result (including a validated owner review), evaluated separately.
    return control not in {"IDENTITY.AUTH", "DATA.PUBLISHING", "DATA.DLP"}


def _action(row):
    result = dict(row)
    disposition = result.get("Disposition")
    if disposition == "Coverage":
        result["ActionType"] = "Evidence"
        if result.get("FindingKey") == "offline.tenant_coverage":
            result["Feature"] = "Confirm the remaining tenant controls"
            result["Observation"] = "The supplied observations do not cover all controls needed for a deployment decision."
            result["Recommendation"] = "Complete the domain checks listed below and record their scope and observation dates."
    elif result["EvidenceStatus"] != "supported":
        result["ActionType"] = "Confirmation"
        original = result.get("Feature") or "the earlier finding"
        result["OriginalFeature"] = original
        result["OriginalRecommendation"] = result.get("OriginalRecommendation", result.get("Recommendation", ""))
        text = _text(result)
        topics = (
            (("conditional access", "conditional_access"), "Confirm Conditional Access coverage"),
            (("mfa", "multifactor", "authentication"), "Confirm multifactor authentication coverage"),
            (("privileged", "admin role", "permanent role", "directory role", "role assignment", "administrator assignments"), "Confirm privileged access controls"),
            (("risky users", "risk detection", "identity risk"), "Confirm remediation of identity risk detections"),
            (("guest", "access review"), "Confirm guest access and review ownership"),
            (("dlp", "data loss prevention"), "Confirm data loss prevention coverage"),
            (("sensitivity", "label publication", "label publishing"), "Confirm sensitivity label publication"),
            (("audit",), "Confirm audit logging and investigation requirements"),
            (("retention",), "Confirm content retention requirements"),
            (("consent", "oauth"), "Confirm application consent and permissions"),
            (("application grants", "unverified publisher", "high-privilege permissions"), "Confirm the review of application grants"),
            (("overshar", "broad access", "anyone link"), "Confirm access restrictions for business content"),
            (("endpoint", "device"), "Confirm the pilot device protection baseline"),
        )
        result["ActionTitle"] = next((title for words, title in topics if any(word in text for word in words)), "Confirm remediation of " + original)
        export_titles = {
            'data_exposure.anonymous_links': 'Confirm the need for links that allow access without sign-in',
            'data_exposure.broad_internal_access': 'Confirm broad group permissions and organization links',
        }
        if result.get('FindingKey') in export_titles:
            result['ActionTitle'] = export_titles[result['FindingKey']]
        elif result['ActionTitle'].startswith('Confirm remediation of Review '):
            result['ActionTitle'] = 'Confirm the review of ' + original[7:]
        result["Feature"] = result["ActionTitle"]
        result["Recommendation"] = "Confirm whether this condition still applies to the pilot population. " + str(result.get("Recommendation") or "Record the outcome and close or update the finding.")
        result["ReadinessStage"] = "Before pilot" if result.get("Status", "").lower() == "critical" else "Before broad rollout"
    else:
        result["ActionType"] = "Remediation"
        if any(term in str(result.get("Observation") or "").lower() for term in ("risky users", "risky user accounts", "identity risk detections")):
            result["OriginalFeature"] = result.get("OriginalFeature", result.get("Feature", ""))
            result["ActionTitle"] = "Review and remediate risky user accounts"
            result["Feature"] = result["ActionTitle"]
    result["CompletionEvidence"] = result.get("CompletionEvidence") or (
        "A dated validation of the affected population, with the condition closed or an approved treatment recorded."
        if result["ActionType"] != "Evidence" else
        "A dated result for this check, including the assessed population and any follow-up actions."
    )
    result["SecurityGate"] = result.get("SecurityGate", result["DomainId"] not in {"adoption", "agents", "external_ai"}
                                        and str(result.get("OptionalEvidence") or "").lower() != "yes")
    return result


def build_assessment_result(recommendations, evidence_bundle=None, *, evaluation_date=None, expected_tenant_id=None):
    """Build the only decision, domain register, counts and aggregate usage model.

    Legacy conclusions are retained as confirmation work. Saved raw observations
    can support a finding or a strength under identical date/completeness rules.
    The function does not mutate inputs or perform I/O.
    """
    bundle = evidence_bundle or {}
    day = evaluation_day(evaluation_date or bundle.get("evaluation_date"))
    prior = bundle.get("prior_report") or {}
    if expected_tenant_id and prior.get("tenant_id") and str(prior["tenant_id"]).lower() != str(expected_tenant_id).lower():
        raise ValueError("Prior assessment tenant ID does not match the assessed tenant.")
    enabled = _scope_domains(bundle)
    questions = [*QUESTIONS, *(question for question in OPTIONAL_QUESTIONS if question[1] in enabled)]
    from .control_reviews import build_review_evidence
    review = build_review_evidence(bundle.get("assessment_profile") or {}, evaluation_date=day,
                                  expected_tenant_id=expected_tenant_id,
                                  allowed_controls={question[0] for question in questions})
    records, observations = [], []
    for original in recommendations or []:
        if original.get("SourceType") == "assessment_requirement":
            continue
        row, fact = _qualify_record(original, bundle, day, expected_tenant_id)
        records.append(row)
        observations.append(fact)
    for original in prior.get("recommendations") or []:
        row, fact = _qualify_record(original, bundle, day, expected_tenant_id, historical=True)
        records.append(row)
        observations.append(fact)
    # Facts behind executive strengths pass through exactly the same qualifier as
    # risks; a service cache never gets a new date just because it was imported.
    for original in bundle.get("verified_strengths") or []:
        domain = domain_for(original)
        service = "Purview" if domain == "data_protection" else "M365" if domain == "content" else "Entra"
        row, fact = _qualify_record({
            **original, "Service": service, "Feature": original.get("Area", "Observed control"),
            "Observation": original.get("Strength", ""), "Recommendation": "", "Status": "Success",
            "Disposition": "Assurance", "EvidenceBasis": "Tenant evidence", "EvidenceAvailable": "Yes",
            "EvidenceKey": original.get("EvidenceKey", ""), "FindingKey": original.get("Key", ""),
            "Priority": "Low", "DomainId": domain,
        }, bundle, day, expected_tenant_id)
        records.append(row)
        observations.append(fact)
    records, superseded = _deduplicate_records(records)
    for row in records:
        if row.get("FindingKey") != "conditional-access-mfa" or row.get("Disposition") != "Assurance":
            continue
        if any(other is not row and other.get("Disposition") == "Assurance"
               and other.get("EvidenceStatus") == row.get("EvidenceStatus")
               and other.get("ObservationDate") == row.get("ObservationDate")
               and other.get("EvidenceScope") == row.get("EvidenceScope")
               and "conditional_access_detail" in str(other.get("EvidenceKey") or "")
               and re.search(r"\d+\s+Conditional Access polic", str(other.get("Observation") or ""), re.I)
               and "requiring MFA" in str(other.get("Observation") or "") for other in records):
            row.update(Disposition="Reference", EvidenceBasis="Summary of the same Conditional Access evidence")
    optional_records = [row for row in records if row["DomainId"] not in enabled]
    records = [row for row in records if row["DomainId"] in enabled]
    metrics = _adoption_observations(bundle, day, expected_tenant_id)
    observations = reconcile_observations(observations, evaluation_date=day, expected_tenant_id=expected_tenant_id)
    observations.extend(metrics)
    # Canonical explicit evidence can be supplied by collectors/package replay.
    pilot_scope = review.get("pilot_scope") or {}
    review_facts = [dict(row, label=next(question[2] for question in questions if question[0] == row["control_id"]))
                    for row in review["observations"] if row.get("scope_id") == pilot_scope.get("id")]
    plan = review.get("pilot_plan") or {}
    if plan.get("current") and pilot_scope.get("current") and parse_date(plan["reviewed_at"]) >= parse_date(pilot_scope["reviewed_at"]):
        review_facts.append({
            "tenant_id": review["tenant_id"], "domain_id": "adoption", "control_id": "ADOPTION.BASELINE",
            "metric_id": "reviewed_pilot_plan", "label": "Reviewed pilot plan", "value": plan["baseline"],
            "unit": "pilot plan", "availability": "available", "control_result": "pass", "complete": True,
            "scope": {"id": pilot_scope["id"], "description": pilot_scope["description"]},
            "observed_at": plan["reviewed_at"], "source_type": "operator_attestation",
            "source_file": (bundle.get("assessment_profile") or {}).get("filename", ""),
            "reviewer_role": plan["reviewer_role"], "evidence_reference": plan["evidence_reference"],
            "qualifications": ["Dated operator review of the pilot plan: " + plan["evidence_reference"]],
        })
    explicit = reconcile_observations([*(bundle.get("observations") or []), *review_facts], evaluation_date=day, expected_tenant_id=expected_tenant_id)
    observations.extend(explicit)
    observations.extend(row for row in review["observations"] if row.get("scope_id") != pilot_scope.get("id"))
    for fact in explicit:
        if (fact.get("control_result") != "fail" or fact["selection"] != "selected"
                or fact["domain_id"] not in enabled):
            continue
        # An adapter must identify the evaluated control result. A raw count,
        # including measured zero, does not say whether a control passed.
        status = "supported" if fact["freshness"] == "current" and fact["complete"] and fact["scope"] and fact["tenant_id"] else "limited"
        domain = fact["domain_id"]
        records.append({
            "FindingKey": "evidence." + fact["metric_id"], "ControlId": fact["control_id"],
            "Service": DOMAIN_LOOKUP[domain][1], "Feature": fact.get("label", fact["metric_id"]),
            "Observation": f"{fact.get('label', fact['metric_id'])}: {fact['value']} {fact['unit']}.",
            "Recommendation": fact.get("remediation") or "Review the measured condition and record its remediation or approved treatment.",
            "Priority": fact.get("priority", "Medium"), "Status": "Action Required", "Disposition": "Action",
            "ReadinessStage": "Before broad rollout", "DomainId": domain, "Domain": DOMAIN_LOOKUP[domain][1],
            "OwnerRole": DOMAIN_LOOKUP[domain][2], "EvidenceId": fact["evidence_id"], "EvidenceStatus": status,
            "EvidenceBasis": "Tenant evidence", "EvidenceAvailable": "Yes", "EvidenceKey": fact.get("evidence_key", ""),
            "EvidenceSheet": fact.get("evidence_sheet", ""), "EvidenceScope": fact["scope"], "EvidenceComplete": fact["complete"],
            "ObservationDate": fact["observed_at"], "Freshness": fact["freshness"], "Qualification": fact["qualification"],
            "SourceType": fact["source_type"], "SourceFile": fact["source_file"], "Historical": "No",
            "MethodologyVersion": METHODOLOGY_VERSION,
        })
    controls = []
    for question in questions:
        control, domain, title, _, _, security_gate = question
        matching = [row for row in records if _matches_question(row, question)]
        supported = [row for row in matching if _supports_question(row, control)]
        known_facts = [row for row in explicit if row.get("control_id") == control and row.get("control_result") in {"pass", "fail"} and row["selection"] == "selected"
                       and row["freshness"] == "current" and row["complete"] and row["tenant_id"] and row["scope"]]
        existing_gap = [row for row in matching if row.get("Disposition") == "Coverage"
                        or (row.get('Disposition') == 'Action' and row.get('EvidenceStatus') != 'supported')]
        if not supported and not known_facts and not existing_gap:
            gap = _gap(question, day)
            records.append(gap)
            matching.append(gap)
        controls.append({
            "control_id": control, "title": title, "domain_id": domain,
            "status": "Action required" if any(row.get("Disposition") == "Action" for row in supported) or any(row.get("control_result") == "fail" for row in known_facts)
                      else "Observed" if supported or known_facts else "Not established",
            "security_gate": security_gate,
            "evidence_ids": [row["EvidenceId"] for row in supported] + [row["evidence_id"] for row in known_facts],
            "recommendation_ids": [row.get("RecommendationId", "") for row in matching],
        })
    reviewed_passes = {fact["control_id"]: fact for fact in explicit
                       if fact.get("source_type") == "operator_attestation" and fact.get("control_result") == "pass"
                       and fact["selection"] == "selected" and fact["complete"] and fact["freshness"] == "current"}
    for row in records:
        if row.get("Disposition") != "Coverage" or row.get("EvidenceStatus") != "gap":
            continue
        matching_questions = [question[0] for question in questions if _matches_question(row, question)]
        if matching_questions and all(control in reviewed_passes for control in matching_questions):
            # Preserve the original collection diagnostic in the workbook. A
            # scoped review may answer its question; it cannot close an Action.
            row.update(Disposition="Reference", OriginalDisposition="Coverage", ReviewClosure="Question answered by a current scoped operator review",
                       ReviewEvidenceIds=[reviewed_passes[control]["evidence_id"] for control in matching_questions])
    for fact in explicit:
        if fact["selection"] == "conflict" and fact["domain_id"] in enabled:
            question = ("CONFLICT." + fact["metric_id"], fact["domain_id"], "Resolve conflicting evidence for " + fact.get("label", fact["metric_id"]), (), (), True)
            if not any(row.get("FindingKey") == "coverage." + question[0].lower() for row in records):
                gap = _gap(question, day)
                gap.update(EvidenceStatus="conflict", Qualification=fact["qualification"], EvidenceId=fact["evidence_id"])
                records.append(gap)
    used_ids = set()
    for row in records:
        identifier = row.get("RecommendationId") or "REC-" + stable_id([_finding_identity(row), row.get("SourceType"), row.get("EvidenceScope")])[:10].upper()
        if identifier in used_ids:
            identifier += "-" + stable_id([row.get("SourceType"), row.get("EvidenceId")])[:5].upper()
        row["RecommendationId"] = identifier
        used_ids.add(identifier)
    actions = [_action(row) for row in records if row.get("Disposition") in {"Action", "Coverage"}]
    actions.sort(key=lambda row: ({"Critical": 0, "High": 1, "Medium": 2, "Low": 3}.get("Critical" if str(row.get("Status", "")).lower() == "critical" else row.get("Priority"), 4),
                                 {"Remediation": 0, "Confirmation": 1, "Evidence": 2}[row["ActionType"]], row["DomainId"], row["Feature"]))
    actions_by_id = {row["RecommendationId"]: row for row in actions}
    records = [actions_by_id.get(row["RecommendationId"], row) for row in records]
    strengths = [row for row in records if row.get("Disposition") == "Assurance" and row["EvidenceStatus"] == "supported"]
    prior_titles = {_slug(title) for title in (prior.get("sheets") or {})}
    def has_historical_support(row):
        if str(row.get("EvidenceAvailable") or "").lower() != "yes":
            return False
        if "service_plan_inventory" in str(row.get("EvidenceKey") or "") or "Service Plan Inventory" in str(row.get("EvidenceSheet") or ""):
            return False
        if row["Historical"] != "Yes":
            return bool(row.get("EvidenceKey") or row.get("EvidenceSheet"))
        return any(_slug(title.strip()) in prior_titles for title in str(row.get("EvidenceSheet") or "").split(";") if title.strip())
    historical_strengths = [row for row in records if row.get("Disposition") == "Assurance" and row["EvidenceStatus"] != "supported" and has_historical_support(row)]
    opportunities = [row for row in records if row.get("Disposition") == "Opportunity" and row["Historical"] != "Yes"]
    coverage = [row for row in actions if row["ActionType"] == "Evidence"]
    unresolved = [row for row in controls if row["security_gate"] and row["status"] == "Not established"]
    confirmed_actions = [row for row in actions if row["ActionType"] == "Remediation" and row["SecurityGate"]]
    critical = [row for row in confirmed_actions if str(row.get("Status", "")).lower() == "critical"]
    high = [row for row in confirmed_actions if row.get("Priority") == "High"]
    medium = [row for row in confirmed_actions if row.get("Priority") == "Medium"]
    confirmation = [row for row in actions if row["ActionType"] == "Confirmation"]
    if critical:
        decision, rationale = "Not ready for pilot", "Confirmed critical conditions require remediation before the pilot proceeds."
    elif unresolved or any(row["SecurityGate"] for row in coverage) or any(row["SecurityGate"] for row in confirmation):
        decision, rationale = "Readiness unconfirmed", "Readiness is not established. Confirm the unresolved controls and earlier findings for the intended pilot population before authorizing expansion."
    elif high:
        decision, rationale = "Pilot only — remediation required", "Address the confirmed high-priority conditions before broad deployment and agree the pilot boundaries."
    elif medium:
        decision, rationale = "Controlled pilot with conditions", "The required checks are covered; complete the remaining measured conditions within the agreed pilot plan."
    else:
        decision, rationale = "Ready for a controlled pilot", "The required checks are supported by the supplied evidence. Start with a bounded population and review the agreed success measures."
    counts = {
        "actions": len(actions), "remediation": sum(row["ActionType"] == "Remediation" for row in actions), "confirmation": len(confirmation),
        "evidence_gaps": len(coverage), "strengths": len(strengths), "opportunities": len(opportunities),
        "critical": len(critical), "high": sum(row.get("Priority") == "High" for row in actions),
        "medium": sum(row.get("Priority") == "Medium" for row in actions),
        "low": sum(row.get("Priority") == "Low" for row in actions),
    }
    domains = []
    for domain, title, owner, why in (*DOMAINS, *OPTIONAL_DOMAINS):
        if domain not in enabled:
            continue
        findings = [row for row in records if row["DomainId"] == domain
                    and row.get("EvidenceBasis") != "License signal"
                    and row.get("Disposition") != "Reference"
                    and (row["Historical"] != "Yes" or row.get("Disposition") == "Action" or row in historical_strengths)]
        domain_actions = [row for row in actions if row["DomainId"] == domain]
        domain_strengths = [row for row in strengths if row["DomainId"] == domain]
        domain_gaps = [row for row in coverage if row["DomainId"] == domain]
        historical_count = sum(row["ActionType"] == "Confirmation" for row in domain_actions)
        status = "Action required" if any(row["ActionType"] == "Remediation" for row in domain_actions) else "Confirmation required" if historical_count else "Evidence required" if domain_gaps else "Observed"
        if domain_strengths:
            summary = ' '.join(str(row.get('Observation') or row.get('Strength') or '') for row in domain_strengths[:2])
            if domain_gaps:
                summary += ' The remaining checks establish how these controls cover the intended pilot.'
        elif any(row['ActionType'] == 'Remediation' for row in domain_actions):
            summary = 'The evidence identifies conditions that require remediation. Use the action plan to agree their treatment before the next rollout stage.'
        elif historical_count:
            summary = 'Earlier or limited observations identify conditions requiring attention. Confirm their current status and coverage of the intended pilot population.'
        else:
            summary = 'The supplied evidence does not yet establish the required checks for the pilot population.'
        if domain == 'adoption' and metrics:
            summary = 'Dated usage and workload observations support pilot planning. Agree the intended population and outcome measures with the business sponsor.'
        domains.append({
            "id": domain, "title": title, "owner_role": owner, "why_it_matters": why,
            "status": status, "summary": summary, "findings": findings, "actions": domain_actions,
            "strengths": domain_strengths, "historical_strengths": [row for row in historical_strengths if row["DomainId"] == domain],
            "opportunities": [row for row in opportunities if row["DomainId"] == domain], "coverage": domain_gaps,
            "metrics": [row for row in metrics if row["domain_id"] == domain and row["selection"] == "selected"],
            "action_count": len(domain_actions),
        })
    control_results = []
    for control in controls:
        question = next(question for question in questions if question[0] == control["control_id"])
        matching = [row for row in records if _matches_question(row, question) or row.get("ControlId") == control["control_id"]]
        control["recommendation_ids"] = [row["RecommendationId"] for row in matching]
        control_results.append({
            "Control ID": control["control_id"], "Control": control["title"],
            "Methodology Version": METHODOLOGY_VERSION,
            "Status": "Fail" if control["status"] == "Action required" else "Pass" if control["status"] == "Observed" else "Not assessed",
            "Result": "fail" if control["status"] == "Action required" else "pass" if control["status"] == "Observed" else "not_assessed",
            "Domain": DOMAIN_LOOKUP[control["domain_id"]][1],
            "Required for deployment decision": "Yes" if control["security_gate"] else "No",
            "Recommendation IDs": "; ".join(control["recommendation_ids"]),
            "Evidence IDs": "; ".join(control["evidence_ids"]),
            "Qualification": "Supported result" if control["status"] != "Not established" else "A dated, scoped result or confirmation is still required.",
        })
    dates = sorted({row["observed_at"] for row in observations if row.get("observed_at")})
    result = {
        "schema_version": EVIDENCE_SCHEMA_VERSION, "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "reconciliation_version": RECONCILIATION_VERSION,
        "methodology_version": METHODOLOGY_VERSION, "evaluation_date": day.isoformat(),
        "report_completeness": "Complete", "evidence_completeness": "Incomplete" if any(row["status"] == "Not established" for row in controls) else "Complete",
        "tenant_id": expected_tenant_id or "", "decision": decision, "rationale": rationale,
        "recommendations": records, "customer_findings": [row for domain in domains for row in domain["findings"]],
        "actions": actions, "domains": domains, "controls": controls, "control_results": control_results,
        "strengths": strengths, "historical_strengths": historical_strengths, "opportunities": opportunities,
        "coverage": coverage, "decision_coverage": [row for row in coverage if row["SecurityGate"]],
        "optional_coverage": optional_records, "counts": counts, "critical": critical, "high": high, "medium": medium,
        "evidence": observations, "adoption_metrics": [row for row in metrics if row["selection"] == "selected"],
        "superseded_findings": superseded, "evidence_period": {"start": dates[0] if dates else None, "end": dates[-1] if dates else None},
        "rollout_conditions": [
            {"stage": "Prepare", "condition": "Agree a bounded pilot population, business owner and permitted use cases."},
            {"stage": "Authorize pilot", "condition": "Close critical findings and confirm required controls for that population."},
            {"stage": "Expand", "condition": "Close or approve the remaining remediation, confirm earlier findings and review pilot outcomes."},
        ],
    }
    result["readiness_review"] = review
    from .readiness_progress import build_rollout_progress
    result["rollout_progress"] = build_rollout_progress(result, review)
    milestone = result["rollout_progress"]["current_stage_id"]
    if milestone == "broader":
        result["decision"] = "Ready for broader adoption"
        result["rationale"] = "The pilot outcomes, sponsor approval and current control reviews support the explicitly defined larger population."
    elif milestone != "pilot" and result["decision"] in {
            "Ready for a controlled pilot", "Controlled pilot with conditions", "Pilot only — remediation required"}:
        result["decision"] = "Readiness unconfirmed"
        result["rationale"] = "Complete the dated pilot scope, reviewed plan and remaining pilot requirements before authorizing the next rollout stage."
    return result

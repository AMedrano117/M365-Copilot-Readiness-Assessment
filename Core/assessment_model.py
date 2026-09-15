"""Normalize raw service recommendations into an honest AI-readiness model.

The collectors produce useful observations, but they historically treated every licensed
service plan as a tenant finding.  This module keeps collection detail intact while separating
the four decisions a report reader actually needs to make:

* Action: a measured or configured condition that needs remediation.
* Opportunity: an optional adoption or value-improvement idea.
* Assurance: a healthy control or capability observed during the assessment.
* Coverage: something this run could not verify.

The classification is deliberately conservative.  Licensing alone is low-confidence evidence
of availability; it is never proof that a control is configured or effective.
"""

import re

from .new_recommendation import (
    CATEGORY_SCAN_COVERAGE,
    NOT_ASSESSED_STATUS,
)


DISPOSITION_ACTION = "Action"
DISPOSITION_OPPORTUNITY = "Opportunity"
DISPOSITION_ASSURANCE = "Assurance"
DISPOSITION_COVERAGE = "Coverage"
DISPOSITION_REFERENCE = "Reference"


COVERAGE_STATUSES = {
    NOT_ASSESSED_STATUS.lower(),
    "permission required",
    "missing prerequisite",
    "pendinginput",
}

ACTION_STATUSES = {
    "critical",
    "action required",
    "attention required",
    "warning",
    "not licensed",
    "missing",
}


# A recommendation module sometimes supplies status=Success even though the observation states
# that a configuration is absent.  Keep those genuine gaps actionable while preventing positive
# deployment ideas from being promoted into failures merely because they have Medium priority.
NEGATIVE_CONDITION = re.compile(
    r"\b(?:cannot|denied|disabled|excessive|failed|gap|gaps|inactive|missing|none|"
    r"not\s+(?:active|available|configured|deployed|enabled|enforced|found|reporting|verified)|"
    r"no\s+(?:active|configured|devices?|polic(?:y|ies)|users?|cases?|data|managed)|"
    r"over-privileged|overshar(?:e|ed|ing)|high-risk|risky\s+(?:app|application|device|identity|"
    r"sign-in|user)|risk\s+(?:detected|factor)|unavailable|unable|unmanaged|"
    r"without\s+(?:approval|control|encryption|mfa|protection))\b",
    re.IGNORECASE,
)

COVERAGE_LANGUAGE = re.compile(
    r"\b(?:cannot be verified|could not be determined|could not be retrieved|"
    r"deployment status requires manual verification|unable to assess|unable to check|"
    r"permission (?:is )?not granted|requires [^.]{0,60} administrator access)\b",
    re.IGNORECASE,
)


IMPACT_RULES = [
    ("Apps, connectors & agents", (
        "oauth", "app consent", "user consent", "permission grant", "enterprise application", "connector", "plugin", "mcp",
        "copilot studio", "power virtual agent", "agent identity", "custom agent",
    )),
    ("Identity & access", (
        "conditional access", "multi-factor", "mfa", "passwordless", "privileged identity",
        "pim", "admin role", "access review", "guest", "cross-tenant", "identity protection",
        "risky user", "sign-in",
    )),
    ("Endpoint & browser controls", (
        "endpoint dlp", "defender for endpoint", "device", "intune", "browser", "byod",
        "mobile application management",
    )),
    ("Data protection & compliance", (
        "data loss prevention", "dlp", "sensitivity", "label", "retention", "records",
        "ediscovery", "audit", "communication compliance", "information barrier", "lockbox",
        "rights management", "rms", "encryption", "insider risk", "data governance",
    )),
    ("Content access & grounding", (
        "sharepoint", "onedrive", "oversharing", "site access", "external sharing",
        "knowledge base", "search index", "content explorer",
    )),
    ("Threat protection", (
        "defender", "threat", "incident", "malware", "phishing", "safe documents",
        "security posture",
    )),
    ("Licensing & prerequisites", (
        "license", "licensing", "capacity", "pendingactivation", "subscription",
    )),
    ("Adoption & value", (
        "adoption", "active users", "activity", "usage", "meetings", "email activity",
        "pilot", "roi", "training", "use case",
    )),
]


FOUNDATION_IMPACT_AREAS = {
    "Identity & access",
    "Endpoint & browser controls",
    "Data protection & compliance",
    "Content access & grounding",
    "Threat protection",
}


CROSS_PLATFORM_MANUAL_CHECKS = [
    {
        "control": "Approved AI service inventory",
        "why": "Identify sanctioned and unsanctioned AI services, owners, user populations, and data flows.",
        "verify": "Review SaaS discovery, browser/network telemetry, procurement records, and employee use cases.",
    },
    {
        "control": "Enterprise workspace and identity controls",
        "why": "Personal AI accounts bypass centralized access, offboarding, role, and sharing controls.",
        "verify": "For each provider, validate enterprise terms, domain controls, SSO, provisioning/deprovisioning, MFA, and admin RBAC.",
    },
    {
        "control": "Provider data handling",
        "why": "Training use, retention, residency, subprocessors, and deletion behavior differ by product and contract.",
        "verify": "Record the contracted settings for prompts, files, outputs, logs, abuse monitoring, retention, residency, and model training.",
    },
    {
        "control": "Sensitive-data egress controls",
        "why": "Users can paste or upload M365 data to browser, desktop, IDE, extension, API, or agent experiences.",
        "verify": "Validate endpoint/browser DLP or equivalent controls on managed devices and test representative upload and paste scenarios.",
    },
    {
        "control": "Connectors, tools, actions, and agents",
        "why": "Connected AI can read or change business data with the user's or agent's permissions.",
        "verify": "Inventory connectors and agent identities; require least privilege, trusted publishers, bounded actions, and human approval for consequential writes.",
    },
    {
        "control": "Use-case and data policy",
        "why": "A technical control cannot decide which regulated, confidential, or safety-sensitive use cases are acceptable.",
        "verify": "Map approved use cases to data classifications, prohibited uses, human-review requirements, and accountable business owners.",
    },
    {
        "control": "Audit, response, and legal readiness",
        "why": "AI activity must fit existing investigation, records, privacy, and incident-response processes.",
        "verify": "Confirm provider logs, retention, eDiscovery/export, alerting, incident playbooks, and evidence ownership.",
    },
    {
        "control": "Outcome measurement",
        "why": "Usage volume alone does not show that AI improves quality, cycle time, risk, or employee experience.",
        "verify": "Define pilot baselines, success measures, quality checks, risk thresholds, and stop/expand decisions by use case.",
    },
]


def _combined_text(record):
    return " ".join(str(record.get(key, "") or "") for key in (
        "Service", "Feature", "Status", "Observation", "Recommendation"
    )).lower()


def infer_disposition(record):
    explicit = str(record.get("Disposition", "") or "").strip()
    if explicit:
        return explicit

    category = str(record.get("Category", "") or "").strip()
    status = str(record.get("Status", "") or "").strip().lower()
    source_status = str(record.get("SourceStatus", record.get("Status", "")) or "").strip().lower()
    recommendation = str(record.get("Recommendation", "") or "").strip()
    observation = str(record.get("Observation", "") or "").strip()

    if category == CATEGORY_SCAN_COVERAGE:
        return DISPOSITION_COVERAGE
    if str(record.get("Service", "") or "") == "M365" and status != "critical":
        # M365 modules primarily describe product availability and adoption.  They do not
        # collect object-level oversharing or data-protection evidence, so they cannot create a
        # security gate.  Keep their suggested work in the value/prerequisite lane.
        return DISPOSITION_OPPORTUNITY if recommendation else DISPOSITION_ASSURANCE
    if status in COVERAGE_STATUSES or COVERAGE_LANGUAGE.search(observation):
        return DISPOSITION_COVERAGE
    if source_status == "insight" or status == "insight":
        return DISPOSITION_OPPORTUNITY
    if source_status == "success":
        if not recommendation:
            return DISPOSITION_ASSURANCE
        if NEGATIVE_CONDITION.search(observation):
            return DISPOSITION_ACTION
        return DISPOSITION_OPPORTUNITY
    if status in ACTION_STATUSES:
        return DISPOSITION_ACTION
    if status in {"disabled", "pendingactivation"}:
        return DISPOSITION_ACTION if record.get("Priority") == "High" else DISPOSITION_OPPORTUNITY
    if recommendation:
        return DISPOSITION_OPPORTUNITY
    return DISPOSITION_REFERENCE


def infer_impact_area(record):
    explicit = str(record.get("ImpactArea", "") or "").strip()
    if explicit:
        return explicit
    text = _combined_text(record)
    for area, markers in IMPACT_RULES:
        if any(marker in text for marker in markers):
            return area
    if str(record.get("Service", "") or "") in {"Copilot Studio", "Power Platform"}:
        return "Apps, connectors & agents"
    return "Platform capability"


def infer_ai_applicability(record, impact_area):
    explicit = str(record.get("AIApplicability", "") or "").strip()
    if explicit:
        return explicit
    text = _combined_text(record)
    if any(marker in text for marker in (
        "third-party ai", "other ai", "external ai", "chatgpt", "claude", "cursor",
        "generative ai site", "unmanaged ai",
    )):
        return "External and managed AI"
    if impact_area in FOUNDATION_IMPACT_AREAS:
        return "All AI using M365 data"
    if impact_area == "Apps, connectors & agents":
        return "AI agents and connected apps"
    return "Microsoft 365 Copilot"


def infer_evidence(record, disposition):
    explicit_basis = str(record.get("EvidenceBasis", "") or "").strip()
    explicit_confidence = str(record.get("Confidence", "") or "").strip()
    if explicit_basis:
        return explicit_basis, explicit_confidence or "Medium"
    if disposition == DISPOSITION_COVERAGE:
        return "Not verified", "Unknown"
    text = _combined_text(record)
    # A workbook tab may contain the licensed service-plan inventory, but that does not turn
    # an entitlement statement into evidence that a control is configured or effective.
    if re.search(r"\b(?:active in|included in|license|licensed|licensing|service plan)\b", text):
        return "License signal", "Low"
    if str(record.get("EvidenceAvailable", "") or "").strip().lower() == "yes":
        return "Tenant evidence", "High"
    return "Tenant observation", "Medium"


def readiness_stage(record, disposition):
    if disposition == DISPOSITION_COVERAGE:
        return "Complete assessment"
    if disposition == DISPOSITION_ASSURANCE:
        return "Maintain"
    if disposition == DISPOSITION_OPPORTUNITY:
        return "Optimize"
    priority = str(record.get("Priority", "") or "")
    status = str(record.get("Status", "") or "").lower()
    if status == "critical":
        return "Before pilot"
    if priority == "High":
        return "Before broad rollout"
    if priority == "Medium":
        return "Pilot condition"
    return "Planned improvement"


def enrich_assessment_record(record):
    enriched = dict(record)
    disposition = infer_disposition(enriched)
    impact_area = infer_impact_area(enriched)
    basis, confidence = infer_evidence(enriched, disposition)
    enriched["Disposition"] = disposition
    enriched["ImpactArea"] = impact_area
    enriched["AIApplicability"] = infer_ai_applicability(enriched, impact_area)
    enriched["EvidenceBasis"] = basis
    enriched["Confidence"] = confidence
    enriched["ReadinessStage"] = readiness_stage(enriched, disposition)
    return enriched


def enrich_assessment_records(records):
    return [enrich_assessment_record(record) for record in (records or [])]


def build_assessment_result(recommendations, evidence_bundle=None, *, evaluation_date=None, expected_tenant_id=None):
    """Public entry point for the shared evidence-qualified assessment result."""
    from .assessment_result import build_assessment_result as build
    return build(recommendations, evidence_bundle, evaluation_date=evaluation_date,
                 expected_tenant_id=expected_tenant_id)


def summarize_readiness(records):
    enriched = enrich_assessment_records(records)
    actions = [r for r in enriched if r.get("Disposition") == DISPOSITION_ACTION]
    coverage = [r for r in enriched if r.get("Disposition") == DISPOSITION_COVERAGE]
    # Power Platform, Copilot Studio, and preview discovery are supplemental enrichment.
    # Missing them narrows extensibility/usage visibility but must not lower the core M365
    # security and governance deployment decision.
    optional_coverage = [
        r for r in coverage
        if str(r.get("OptionalEvidence", "") or "").lower() == "yes"
        or str(r.get("Service", "") or "") in {"Power Platform", "Copilot Studio", "Shadow AI"}
    ]
    decision_coverage = [r for r in coverage if r not in optional_coverage]
    critical = [r for r in actions if str(r.get("Status", "")).lower() == "critical"]
    high = [r for r in actions if r.get("Priority") == "High"]
    medium = [r for r in actions if r.get("Priority") == "Medium"]

    offline_coverage = any(r.get('FindingKey') == 'offline.tenant_coverage' for r in decision_coverage)
    if offline_coverage:
        decision = "Assessment incomplete"
        rationale = "The offline evidence does not include a current tenant collection. Review the available findings, but tenant readiness is not established."
    elif critical:
        decision = "Not ready for pilot"
        noun = "condition" if len(critical) == 1 else "conditions"
        rationale = f"{len(critical)} critical {noun} require remediation before AI access is expanded."
    elif high:
        decision = "Pilot only — remediation required"
        noun = "condition" if len(high) == 1 else "conditions"
        rationale = f"{len(high)} high-priority {noun} should be closed before broad deployment."
    elif medium:
        decision = "Controlled pilot with conditions"
        noun = "condition" if len(medium) == 1 else "conditions"
        rationale = f"No critical blockers were found; {len(medium)} medium-priority {noun} remain."
    elif decision_coverage:
        decision = "Assessment incomplete"
        rationale = "No blocking condition was found in the available data, but important areas were not verified."
    else:
        decision = "Ready for a controlled pilot"
        rationale = "No critical or high-priority conditions were found in the assessed M365 data estate."

    return {
        "decision": decision,
        "rationale": rationale,
        "actions": actions,
        "coverage": coverage,
        "decision_coverage": decision_coverage,
        "optional_coverage": optional_coverage,
        "critical": critical,
        "high": high,
        "medium": medium,
    }

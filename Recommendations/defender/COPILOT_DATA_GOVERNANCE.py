"""Concise Purview governance collection summary for the engineer register."""

from Core.new_recommendation import CATEGORY_SCAN_COVERAGE, NOT_ASSESSED_STATUS, new_recommendation


CORE_SOURCES = {
    "dlp_policies": "DLP policies",
    "dlp_rules": "DLP rules",
    "sensitivity_labels": "sensitivity labels",
    "label_policies": "label publishing policies",
    "retention_policies": "retention policies",
    "audit_config": "audit configuration",
}


def get_recommendation(purview_client=None, defender_client=None, defender_insights=None):
    if not purview_client:
        return new_recommendation(
            service="Defender", feature="Microsoft 365 data governance evidence",
            status=NOT_ASSESSED_STATUS,
            observation="Core Purview governance evidence was not collected. This is an evidence gap in assessment coverage, not proof that tenant controls are absent.",
            recommendation="Run the normal main.py assessment and complete the Purview sign-in. To bypass cached data, run: python main.py --interactive-auth fresh.",
            priority="High", link_text="Microsoft Purview permissions",
            link_url="https://learn.microsoft.com/purview/purview-permissions",
            category=CATEGORY_SCAN_COVERAGE, disposition="Coverage",
            evidence_basis="Not verified", confidence="Unknown",
        )

    source_status = getattr(purview_client, "collection_status", {}) or {}
    unavailable = []
    reasons = []
    for source_key, label in CORE_SOURCES.items():
        state = source_status.get(source_key, {}) or {}
        if state and not state.get("available"):
            unavailable.append(label)
            detail = state.get("reason") or state.get("error_category") or "unavailable"
            reasons.append(f"{label}: {detail}")

    if unavailable:
        return new_recommendation(
            service="Defender", feature="Microsoft 365 data governance evidence",
            status=NOT_ASSESSED_STATUS,
            observation=f"Core Purview collection was incomplete for: {', '.join(unavailable)}.",
            recommendation="Review the Purview collection coverage table for the source-specific reason and read role, then rerun with: python main.py --interactive-auth fresh. " + " ".join(reasons),
            priority="High", link_text="Microsoft Purview permissions",
            link_url="https://learn.microsoft.com/purview/purview-permissions",
            category=CATEGORY_SCAN_COVERAGE, disposition="Coverage",
            evidence_key="purview_policy_detail",
            evidence_basis="Not verified", confidence="Unknown",
        )

    dlp = getattr(purview_client, "dlp_policies", {}) or {}
    rules = getattr(purview_client, "dlp_rules", {}) or {}
    labels = getattr(purview_client, "sensitivity_labels", {}) or {}
    publishing = getattr(purview_client, "label_policies", {}) or {}
    retention = getattr(purview_client, "retention_labels", {}) or {}
    audit = getattr(purview_client, "audit_config", {}) or {}
    observation = (
        "Core Purview evidence was collected: "
        f"{dlp.get('total_policies', 0)} DLP policies, {rules.get('total_rules', 0)} DLP rules, "
        f"{labels.get('total_labels', 0)} sensitivity labels, "
        f"{publishing.get('total_policies', 0)} label publishing policies, and "
        f"{retention.get('total_labels', 0)} retention policies. "
        f"Unified audit logging is {'enabled' if audit.get('unified_audit_enabled') else 'not enabled'} according to the returned configuration."
    )
    return new_recommendation(
        service="Defender", feature="Microsoft 365 data governance evidence",
        observation=observation, recommendation="", status="Success",
        link_text="Purview for AI", link_url="https://learn.microsoft.com/purview/ai-microsoft-purview",
        disposition="Reference", evidence_key="purview_policy_detail",
        evidence_summary="The Purview Policy Detail tab contains the collected policy, label, and DLP rule evidence.",
        impact_area="Data protection & compliance", ai_applicability="All AI using M365 data",
        evidence_basis="Tenant evidence", confidence="High",
    )

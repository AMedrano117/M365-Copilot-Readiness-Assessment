"""Evidence-based Purview DLP assessment.

The service-plan record establishes entitlement only. Policy and rule conclusions come from
the interactive Purview collection that the default ``main.py`` path runs.
"""

from Core.friendly_names import get_friendly_sku_name
from Core.new_recommendation import CATEGORY_SCAN_COVERAGE, NOT_ASSESSED_STATUS, new_recommendation


def _enabled_policy(policy):
    enabled = policy.get("Enabled")
    mode = str(policy.get("Mode", "") or "").strip().lower()
    return enabled is True or str(enabled).strip().lower() in {"true", "yes"} or mode in {
        "enable", "enforce", "testwithnotifications", "testwithoutnotifications"
    }


def _enabled_rule(rule):
    disabled = rule.get("Disabled")
    return disabled in (None, "", False) or str(disabled).strip().lower() in {"false", "no"}


def _scope(policy):
    labels = []
    for label, key in (
        ("Exchange", "ExchangeLocation"), ("SharePoint", "SharePointLocation"),
        ("OneDrive", "OneDriveLocation"), ("Teams", "TeamsLocation"),
        ("devices", "EndpointDlpLocation"), ("Power BI", "PowerBILocation"),
    ):
        if policy.get(key) not in (None, "", [], False):
            labels.append(label)
    return labels


def _coverage(feature, observation, recommendation, link_url):
    return new_recommendation(
        service="Purview", feature=feature, observation=observation,
        recommendation=recommendation, link_text="Microsoft Purview collection requirements",
        link_url=link_url, priority="High", status=NOT_ASSESSED_STATUS,
        category=CATEGORY_SCAN_COVERAGE, disposition="Coverage",
        evidence_basis="Not verified", confidence="Unknown",
    )


async def get_recommendation(sku_name, status="Success", client=None, purview_client=None):
    feature_name = "Data Loss Prevention"
    friendly_sku = get_friendly_sku_name(sku_name)
    license_rec = new_recommendation(
        service="Purview", feature=f"{feature_name} entitlement",
        observation=f"A DLP service plan is {status.lower()} in {friendly_sku}. This confirms licensing only, not policy coverage or enforcement.",
        recommendation="", link_text="Learn about Microsoft Purview DLP",
        link_url="https://learn.microsoft.com/purview/dlp-learn-about-dlp",
        status=status, disposition="Reference", impact_area="Data protection & compliance",
        ai_applicability="All AI using M365 data", evidence_basis="License signal", confidence="Low",
    )
    if status != "Success":
        return [license_rec]

    if not purview_client:
        return [license_rec, _coverage(
            f"{feature_name} configuration",
            "DLP policies and rules were not assessed because the interactive Purview collection was unavailable.",
            "Run the normal main.py assessment and complete the Purview sign-in. To force a new sign-in instead of reusing cached data, run: python main.py --interactive-auth fresh.",
            "https://learn.microsoft.com/purview/purview-permissions",
        )]

    policies_data = getattr(purview_client, "dlp_policies", {}) or {}
    rules_data = getattr(purview_client, "dlp_rules", {}) or {}
    source_status = getattr(purview_client, "collection_status", {}) or {}
    if not policies_data.get("available"):
        source = source_status.get("dlp_policies", {}) or {}
        reason = source.get("reason") or "The Purview session did not return DLP policies."
        role = source.get("required_role") or "View-Only DLP Compliance Management or Compliance Administrator"
        return [license_rec, _coverage(
            f"{feature_name} policy collection", f"DLP policies were not assessed. {reason}",
            f"Grant the signed-in user read access through {role}, then rerun: python main.py --interactive-auth fresh.",
            "https://learn.microsoft.com/purview/purview-permissions",
        )]

    policies = policies_data.get("policies", []) or []
    enabled_policies = [policy for policy in policies if _enabled_policy(policy)]
    enforced_policies = [p for p in enabled_policies if str(p.get("Mode", "") or "").strip().lower() in {"enable", "enforce"}]
    locations = sorted({label for policy in enabled_policies for label in _scope(policy)})

    if not policies:
        return [license_rec, new_recommendation(
            service="Purview", feature=f"{feature_name} baseline",
            observation="Purview returned zero DLP policies. No policy-based DLP coverage was available to evaluate for Microsoft 365 data.",
            recommendation="Define the sensitive information and sharing scenarios that matter to the planned AI use cases, then deploy a baseline DLP policy for the applicable Exchange, SharePoint, OneDrive, Teams, device, and browser locations. Start in simulation, review matches and false positives, then enforce the confirmed high-risk scenarios.",
            link_text="Create and deploy DLP policies", link_url="https://learn.microsoft.com/purview/dlp-create-deploy-policy",
            priority="High", status="Action Required", finding_key="purview.dlp.no_policies",
            evidence_key="purview_policy_detail", evidence_summary="The Purview Policy Detail tab records that policy collection succeeded and returned no DLP policies.",
            impact_area="Data protection & compliance", ai_applicability="All AI using M365 data",
            evidence_basis="Tenant evidence", confidence="High",
        )]

    if not rules_data.get("available"):
        source = source_status.get("dlp_rules", {}) or {}
        reason = source.get("reason") or "The Purview session returned policies but not their rules."
        role = source.get("required_role") or "View-Only DLP Compliance Management or Compliance Administrator"
        item = _coverage(
            f"{feature_name} rule collection",
            f"Purview returned {len(policies)} DLP policies, but rule conditions and actions were not assessed. {reason}",
            f"Grant the signed-in user read access through {role}, then rerun: python main.py --interactive-auth fresh. Policy names alone cannot establish what sensitive data is detected or what action occurs on a match.",
            "https://learn.microsoft.com/powershell/module/exchangepowershell/get-dlpcompliancerule",
        )
        item["EvidenceKey"] = "purview_policy_detail"
        return [license_rec, item]

    rules = rules_data.get("rules", []) or []
    enabled_rules = [rule for rule in rules if _enabled_rule(rule)]
    if not enabled_policies or not enabled_rules:
        return [license_rec, new_recommendation(
            service="Purview", feature=f"{feature_name} enforcement",
            observation=f"Purview returned {len(policies)} DLP policies and {len(rules)} rules, but only {len(enabled_policies)} policies and {len(enabled_rules)} rules are enabled.",
            recommendation="Review the disabled policies and rules against the approved AI data-use scenarios. Enable only the applicable protections, validate them in simulation, and move confirmed high-risk rules to enforcement.",
            link_text="DLP policy deployment guidance", link_url="https://learn.microsoft.com/purview/dlp-create-deploy-policy",
            priority="High", status="Action Required", finding_key="purview.dlp.no_enabled_protection",
            evidence_key="purview_policy_detail", evidence_summary="The Purview Policy Detail tab lists policies and rules with their enabled state, conditions, and actions.",
            impact_area="Data protection & compliance", ai_applicability="All AI using M365 data",
            evidence_basis="Tenant evidence", confidence="High",
        )]

    if not enforced_policies:
        return [license_rec, new_recommendation(
            service="Purview", feature=f"{feature_name} enforcement",
            observation=f"Purview returned {len(enabled_policies)} enabled DLP policies and {len(enabled_rules)} enabled rules, but no enabled policy was identified in enforcement mode. Current scope: {', '.join(locations) or 'not returned'}.",
            recommendation="Review simulation results and false positives for the applicable policies. Move validated high-risk rules to enforcement and document any policies intentionally left in simulation.",
            link_text="DLP policy deployment guidance", link_url="https://learn.microsoft.com/purview/dlp-create-deploy-policy",
            priority="Medium", status="Attention Required", finding_key="purview.dlp.simulation_only",
            evidence_key="purview_policy_detail", evidence_summary="The Purview Policy Detail tab lists each DLP policy mode and rule behavior.",
            impact_area="Data protection & compliance", ai_applicability="All AI using M365 data",
            evidence_basis="Tenant evidence", confidence="High",
        )]

    return [license_rec, new_recommendation(
        service="Purview", feature=f"{feature_name} configuration",
        observation=f"Purview returned {len(enabled_policies)} enabled DLP policies and {len(enabled_rules)} enabled rules, including {len(enforced_policies)} policies in enforcement mode. Scope returned: {', '.join(locations) or 'not specified'}.",
        recommendation="", link_text="DLP policy reference", link_url="https://learn.microsoft.com/purview/dlp-policy-reference",
        status="Success", disposition="Reference", evidence_key="purview_policy_detail",
        evidence_summary="The Purview Policy Detail tab lists policy modes, locations, rule conditions, and configured actions.",
        impact_area="Data protection & compliance", ai_applicability="All AI using M365 data",
        evidence_basis="Tenant evidence", confidence="High",
    )]

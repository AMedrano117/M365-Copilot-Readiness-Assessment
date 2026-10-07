"""
Information Protection for Office 365 - Standard - Copilot & Agent Adoption Recommendation

Label evidence comes only from collected Purview data: Purview PowerShell
(complete label definitions and publishing policies) or the Microsoft Graph
beta label baseline (definitions only, preview quality). This module makes no
Graph calls of its own.
"""
from Core.new_recommendation import new_recommendation
from Core.friendly_names import get_friendly_sku_name
from Core.source_evidence import source_is_complete


def _label_names(labels, limit):
    names = [label.get('DisplayName') or label.get('Name') or 'Unnamed' for label in labels if isinstance(label, dict)]
    text = ', '.join(names[:limit])
    if len(names) > limit:
        text += f" (+{len(names) - limit} more)"
    return text


async def get_recommendation(sku_name, status="Success", client=None, purview_client=None):
    """
    Information Protection Standard provides basic sensitivity labels
    that guide Copilot's handling of classified content.
    Returns the license status and, when evidence exists, label deployment status.
    """
    feature_name = "Information Protection for Office 365 - Standard"
    friendly_sku = get_friendly_sku_name(sku_name)

    if status == "Success":
        license_rec = new_recommendation(
            service="Purview",
            feature=feature_name,
            observation=f"{feature_name} is active in {friendly_sku}, enabling basic sensitivity labeling for Copilot content",
            recommendation="",
            link_text="Basic Information Protection",
            link_url="https://learn.microsoft.com/purview/information-protection/",
            status=status
        )
    else:
        license_rec = new_recommendation(
            service="Purview",
            feature=feature_name,
            observation=f"{feature_name} is {status} in {friendly_sku}, lacking basic content classification controls",
            recommendation=f"Enable {feature_name} to apply manual sensitivity labels to documents and emails that Copilot processes. Standard protection provides the foundation for data classification, allowing users to mark content as Public, Internal, Confidential, or Highly Confidential. While labels are manually applied (unlike Premium's automatic classification), they inform Copilot's behavior when summarizing or sharing labeled content. Standard is the minimum protection level recommended for organizations starting Copilot adoption, with Premium recommended for automated enforcement.",
            link_text="Basic Information Protection",
            link_url="https://learn.microsoft.com/purview/information-protection/",
            priority="Medium",
            status=status
        )

    if status != "Success" or purview_client is None:
        return [license_rec]

    labels_data = getattr(purview_client, 'sensitivity_labels', None) or {}
    if not source_is_complete(purview_client, "sensitivity_labels", labels_data):
        # Unavailable, partial, or preview-quality (Graph beta) label evidence.
        # The shared saved-evidence check rewrites this row with the returned
        # count and the specific steps that unlock complete evidence.
        return [license_rec, new_recommendation(
            service="Purview",
            feature=f"{feature_name} - Label Deployment",
            observation="Sensitivity label deployment could not be verified from complete Purview evidence.",
            recommendation="Verify label definitions and publishing policies in the Microsoft Purview portal, or enable Purview PowerShell collection and rerun.",
            link_text="Manage Sensitivity Labels",
            link_url="https://learn.microsoft.com/purview/create-sensitivity-labels",
            priority="Medium",
            status="Not Assessed",
            disposition="Coverage",
            finding_key="purview.sensitivity_labels.deployed",
        )]

    labels = labels_data.get('labels', []) or []
    total_labels = labels_data.get('total_labels', len(labels))
    policies_data = getattr(purview_client, 'label_policies', None) or {}
    policies_known = source_is_complete(purview_client, "label_policies", policies_data)
    total_policies = policies_data.get('total_policies', 0) if policies_known else None
    policy_note = (f" ({total_policies} label policies)" if total_policies is not None
                   else " (label publishing policies were not collected)")

    if total_labels >= 4:
        observation = f"{total_labels} sensitivity labels configured{policy_note}: {_label_names(labels, 4)}"
        policy_clause = f"Currently {total_policies} label policies are configured." if total_policies is not None else \
            "Label publishing policies were not collected; confirm them in the Purview portal."
        deployment_rec = new_recommendation(
            service="Purview",
            feature=f"{feature_name} - Label Deployment",
            observation=observation,
            finding_key="purview.sensitivity_labels.deployed",
            recommendation=f"Verify labels cover Copilot scenarios: 1) Test: label document 'Confidential' > ask Copilot to summarize > attempt external sharing (should block/warn), 2) Set default label policy ('General' or 'Internal Only') for all users, 3) Enable mandatory labeling for sensitive locations (Finance, HR, Legal OneDrive/SharePoint), 4) Train users: Copilot respects label restrictions when sharing AI-generated content. {policy_clause}",
            link_text="Sensitivity Label Best Practices",
            link_url="https://learn.microsoft.com/purview/information-protection-deployment",
            priority="Low",
            status="Success"
        )
    elif total_labels >= 1:
        deployment_rec = new_recommendation(
            service="Purview",
            feature=f"{feature_name} - Label Deployment",
            observation=f"Only {total_labels} sensitivity label(s) configured{policy_note}: {_label_names(labels, 10)} - insufficient granularity for Copilot protection",
            recommendation=f"Expand from {total_labels} to minimum 4 labels: 'Public' (external), 'General' (default internal), 'Confidential' (sensitive), 'Highly Confidential' (regulated). Without granular labels, users cannot properly classify content for Copilot - everything is treated equally. Deploy comprehensive taxonomy in Purview > Information protection > Labels.",
            link_text="Create Sensitivity Labels",
            link_url="https://learn.microsoft.com/purview/create-sensitivity-labels",
            priority="High",
            status="Success"
        )
    else:
        deployment_rec = new_recommendation(
            service="Purview",
            feature=f"{feature_name} - Label Deployment",
            observation="Information Protection license active but ZERO sensitivity labels configured - no content classification",
            finding_key="purview.sensitivity_labels.deployed",
            recommendation="Deploy sensitivity labels IMMEDIATELY before Copilot rollout. Create 4 baseline labels: 1) Public (marketing, public docs), 2) General (default for all internal content), 3) Confidential (customer data, contracts, roadmaps), 4) Highly Confidential (financials, M&A, HR). Without labels, Copilot has no protection boundaries - all content treated equally. Configure in Purview > Information protection > Labels, publish to all users.",
            link_text="Create Sensitivity Labels",
            link_url="https://learn.microsoft.com/purview/create-sensitivity-labels",
            priority="High",
            status="Success"
        )
    return [license_rec, deployment_rec]

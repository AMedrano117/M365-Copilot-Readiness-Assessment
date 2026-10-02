from Core.source_evidence import source_is_complete
"""
Customer Lockbox - Copilot & Agent Adoption Recommendation
"""
from Core.new_recommendation import new_recommendation
from Core.friendly_names import get_friendly_sku_name

async def get_recommendation(sku_name, status="Success", client=None, purview_client=None):
    """
    Customer Lockbox requires approval before Microsoft engineers
    access organizational data, including content used by Copilot.
    """
    feature_name = "Customer Lockbox (Enterprise A)"
    friendly_sku = get_friendly_sku_name(sku_name)
    
    # License recommendation
    if status == "Success":
        license_rec = new_recommendation(
            service="Purview",
            feature=feature_name,
            observation=f"{feature_name} is active in {friendly_sku}, requiring approval for Microsoft access to Copilot-indexed content",
            recommendation="",
            link_text="Control Microsoft Data Access",
            link_url="https://learn.microsoft.com/purview/customer-lockbox-requests",
            status=status
        )
    else:
        license_rec = new_recommendation(
            service="Purview",
            feature=feature_name,
            observation=f"{feature_name} is {status} in {friendly_sku}, missing control over Microsoft's access to AI training data",
            recommendation=f"Enable {feature_name} to require explicit approval before Microsoft engineers can access your organization's data during support operations. With Copilot processing sensitive business information, Lockbox ensures Microsoft cannot view your AI interactions, prompts, or Copilot-generated content without permission. Critical for regulated industries and high-security environments where even Microsoft support access to AI training data or troubleshooting logs must be approved and audited. Provides additional layer of protection for confidential information that Copilot may process.",
            link_text="Control Microsoft Data Access",
            link_url="https://learn.microsoft.com/purview/customer-lockbox-requests",
            priority="Medium",
            status=status
        )
    
    # Check deployment status from PowerShell data
    deployment_recs = []
    if status == "Success" and source_is_complete(purview_client, "org_config", getattr(purview_client, "org_config", None)):
        org_config = purview_client.org_config
        is_enabled = org_config.get('customer_lockbox_enabled', org_config.get('CustomerLockboxEnabled', org_config.get('CustomerLockBoxEnabled')))
        
        if is_enabled is True:
            deployment_recs.append(new_recommendation(
                service="Purview",
                feature=f"{feature_name} - Configuration",
                observation="Customer Lockbox is ENABLED - Microsoft support requires approval for data access",
                recommendation="",
                link_text="Manage Lockbox Requests",
                link_url="https://learn.microsoft.com/purview/customer-lockbox-requests",
                status="Success"
            ))
        elif is_enabled is False:
            deployment_recs.append(new_recommendation(
                service="Purview",
                feature=f"{feature_name} - Configuration",
                observation="Customer Lockbox is licensed but disabled; explicit customer approval is not required for eligible Microsoft support access requests",
                finding_key="purview.customer_lockbox.state",
                recommendation="Evaluate Customer Lockbox against contractual, regulatory, and support-access requirements. Enable it when explicit approval for eligible support access is required; it is not a universal AI deployment prerequisite.",
                link_text="Enable Customer Lockbox",
                link_url="https://learn.microsoft.com/purview/customer-lockbox-requests#enable-customer-lockbox",
                priority="Low",
                status="Insight",
                disposition="Opportunity"
            ))
        else:
            deployment_recs.append(new_recommendation(
                service="Purview", feature=f"{feature_name} - Configuration",
                observation="The organization configuration did not return an unambiguous Customer Lockbox Boolean setting; its enabled state is unverified",
                finding_key="purview.customer_lockbox.state",
                recommendation="Confirm the Customer Lockbox setting in a dated organization-configuration export or the admin center before deciding whether to change it.",
                status="Not Assessed", disposition="Coverage"
            ))
    
    if deployment_recs:
        return [license_rec] + deployment_recs
    return [license_rec]

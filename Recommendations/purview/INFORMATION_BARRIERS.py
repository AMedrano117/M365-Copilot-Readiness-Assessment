from Core.source_evidence import source_is_complete
"""
Information Barriers - Copilot & Agent Adoption Recommendation
"""
from Core.new_recommendation import new_recommendation
from Core.friendly_names import get_friendly_sku_name

async def get_recommendation(sku_name, status="Success", client=None, purview_client=None):
    """
    Information Barriers prevent Copilot from inadvertently sharing information
    between restricted groups, essential for regulated industries and ethical walls.
    """
    feature_name = "Information Barriers"
    friendly_sku = get_friendly_sku_name(sku_name)
    
    if status == "Success":
        license_rec = new_recommendation(
            service="Purview",
            feature=feature_name,
            observation=f"{feature_name} is licensed through {friendly_sku}; licensing alone does not confirm that any separation policy is active",
            recommendation="",
            link_text="Information Barriers for AI Compliance",
            link_url="https://learn.microsoft.com/purview/information-barriers",
            status=status
        )
    else:
        license_rec = new_recommendation(
            service="Purview",
            feature=feature_name,
            observation=f"{feature_name} is {status} in {friendly_sku}; applicability depends on documented ethical-wall or separation requirements",
            recommendation="If the organization has regulatory or contractual separation requirements, validate whether Information Barriers is the appropriate control and scope it to those segments. Do not treat this optional capability as a universal AI prerequisite.",
            link_text="Information Barriers for AI Compliance",
            link_url="https://learn.microsoft.com/purview/information-barriers",
            priority="Medium",
            status="Insight",
            disposition="Opportunity"
        )
    
    # Check deployment status from PowerShell data
    deployment_recs = []
    if status == "Success" and source_is_complete(purview_client, "information_barriers", getattr(purview_client, "information_barriers", None)):
        ib_data = purview_client.information_barriers
        
        # Generate recommendation whether data is available or not (0 count = not configured)
        total_policies = ib_data.get('total_policies', 0)
        policies = ib_data.get('policies', [])
        
        if total_policies > 0:
            active_policies = [p for p in policies if p.get('State') == 'Active']
            policy_names = ', '.join([p.get('Name', 'Unnamed') for p in policies[:2]])
            if len(policies) > 2:
                policy_names += f" (+{len(policies)-2} more)"
            
            deployment_rec = new_recommendation(
                    service="Purview",
                    feature=f"{feature_name} - Policy Status",
                    observation=f"{total_policies} Information Barrier policies configured ({len(active_policies)} active): {policy_names}",
                    recommendation=f"Verify barriers enforce ethical walls for Copilot: 1) Test: user in restricted group asks Copilot about prohibited project (should not retrieve), 2) Ensure segments cover all groups needing separation (M&A teams, trading desks, legal matters), 3) Validate Copilot respects barriers in search, chat, and document access, 4) Review policy application status. Currently {len(active_policies)}/{total_policies} policies active.",
                    link_text="Information Barrier Policies",
                    link_url="https://learn.microsoft.com/purview/information-barriers-policies",
                    priority="Medium" if len(active_policies) > 0 else "High",
                    status="Success"
            )
            deployment_recs.append(deployment_rec)
        else:
            deployment_rec = new_recommendation(
                    service="Purview",
                    feature=f"{feature_name} - Policy Status",
                    observation="Information Barriers is licensed but no policies are configured; this is a gap only when the organization has defined separation requirements",
                    recommendation="Confirm whether ethical-wall, conflict-of-interest, or regulated separation requirements apply. If they do, design and test scoped Information Barrier segments before affected users receive AI access.",
                    link_text="Configure Information Barriers",
                    link_url="https://learn.microsoft.com/purview/information-barriers-policies",
                    priority="Medium",
                    status="Insight",
                    disposition="Opportunity"
            )
            deployment_recs.append(deployment_rec)
    
    if deployment_recs:
        return [license_rec] + deployment_recs
    
    return [license_rec]

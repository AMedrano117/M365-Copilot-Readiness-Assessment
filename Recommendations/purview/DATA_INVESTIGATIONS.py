from Core.source_evidence import source_is_complete
"""
Data Investigations - Copilot & Agent Adoption Recommendation
"""
from Core.new_recommendation import new_recommendation
from Core.friendly_names import get_friendly_sku_name

async def get_recommendation(sku_name, status="Success", client=None, purview_client=None):
    """
    Data Investigations enables forensic analysis of Copilot usage patterns
    and AI-assisted data access during security incidents or compliance audits.
    """
    feature_name = "Data Investigations (Standard)"
    friendly_sku = get_friendly_sku_name(sku_name)
    
    if status == "Success":
        license_rec = new_recommendation(
            service="Purview",
            feature=feature_name,
            observation=f"{feature_name} is active in {friendly_sku}, enabling investigations of AI-related security incidents",
            recommendation="",
            link_text="Investigate Copilot Security Incidents",
            link_url="https://learn.microsoft.com/purview/overview-data-investigations",
            status=status
        )
    else:
        license_rec = new_recommendation(
            service="Purview",
            feature=feature_name,
            observation=f"{feature_name} is {status} in {friendly_sku}, limiting forensic analysis of Copilot misuse",
            recommendation=f"Enable {feature_name} to investigate security incidents involving Copilot and agents. When suspicious activity is detected, reconstruct what information an attacker gathered through AI, identify all sensitive content accessed via Copilot during a specific timeframe, and analyze patterns showing how compromised accounts used AI for reconnaissance. Data Investigations provides the forensics needed to understand the scope of AI-assisted security breaches and demonstrate compliance during regulatory inquiries about data handling.",
            link_text="Investigate Copilot Security Incidents",
            link_url="https://learn.microsoft.com/purview/overview-data-investigations",
            priority="Medium",
            status=status
        )
    
    # Check deployment status from PowerShell data
    deployment_recs = []
    if status == "Success" and source_is_complete(purview_client, "ediscovery_cases", getattr(purview_client, "ediscovery_cases", None)):
        ediscovery_data = purview_client.ediscovery_cases
        
        if ediscovery_data.get('available'):
            total_cases = ediscovery_data.get('total_cases', 0)
            
            if total_cases > 0:
                deployment_rec = new_recommendation(
                    service="Purview",
                    feature=f"{feature_name} - Active Cases",
                    observation=f"Data Investigations has {total_cases} active eDiscovery case(s) configured - investigation framework is deployed",
                    recommendation=f"You have {total_cases} active eDiscovery case(s). Ensure these cases are configured to capture Copilot-related activities: 1) Include Teams messages with Copilot interactions, 2) Search for AI-generated documents and summaries, 3) Capture Copilot query logs where available, 4) Review case scope to include OneDrive/SharePoint content accessed via AI. When investigating security incidents, Data Investigations can reveal what sensitive information was accessed through Copilot, reconstruct AI-assisted data gathering patterns, and identify anomalous Copilot usage by compromised accounts. Use Get-ComplianceCase to review active investigations.",
                    link_text="Manage eDiscovery Cases",
                    link_url="https://learn.microsoft.com/purview/ediscovery-cases",
                    priority="Low",
                    status="Success"
                )
                deployment_recs.append(deployment_rec)
            else:
                deployment_rec = new_recommendation(
                    service="Purview",
                    feature=f"{feature_name} - Configuration",
                    observation="No eDiscovery cases were present at collection time; cases should exist only for active legal or investigation matters",
                    finding_key="purview.ediscovery.case_configuration",
                    recommendation="",
                    link_text="Manage eDiscovery Cases",
                    link_url="https://learn.microsoft.com/purview/ediscovery-standard-get-started",
                    status="Success"
                )
                deployment_recs.append(deployment_rec)
    
    if deployment_recs:
        return [license_rec] + deployment_recs
    
    return [license_rec]

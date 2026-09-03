"""
Microsoft Endpoint DLP - Copilot & Agent Adoption Recommendation
"""
from Core.new_recommendation import new_recommendation
from Core.friendly_names import get_friendly_sku_name

async def get_recommendation(sku_name, status="Success", client=None, purview_client=None):
    """
    Endpoint DLP prevents sensitive data from being copied from Copilot responses
    to unauthorized locations, securing AI-generated content at the device level.
    """
    feature_name = "Microsoft Endpoint DLP"
    friendly_sku = get_friendly_sku_name(sku_name)
    
    if status == "Success":
        license_rec = new_recommendation(
            service="Purview",
            feature=feature_name,
            observation=f"{feature_name} is licensed through {friendly_sku}; licensing alone does not confirm endpoint or browser enforcement",
            recommendation="",
            link_text="Endpoint DLP for Copilot Security",
            link_url="https://learn.microsoft.com/purview/endpoint-dlp-learn-about",
            status=status,
            impact_area="Endpoint & browser controls",
            ai_applicability="External and managed AI"
        )
    else:
        license_rec = new_recommendation(
            service="Purview",
            feature=feature_name,
            observation=f"{feature_name} is {status} in {friendly_sku}; sensitive-data sharing to external AI sites was not protected by this capability",
            recommendation="If employees may use browser-based or desktop AI services with M365 data, evaluate Endpoint DLP or an equivalent egress control. Confirm device onboarding, browser coverage, supported activities, and tested enforcement before broad use.",
            link_text="Endpoint DLP for Copilot Security",
            link_url="https://learn.microsoft.com/purview/endpoint-dlp-learn-about",
            priority="Medium",
            status="Insight",
            disposition="Opportunity",
            impact_area="Endpoint & browser controls",
            ai_applicability="External and managed AI"
        )
    
    # Check deployment status from PowerShell data
    deployment_recs = []
    if status == "Success" and purview_client and hasattr(purview_client, 'dlp_policies'):
        dlp_data = purview_client.dlp_policies
        
        if dlp_data.get('available'):
            total_policies = dlp_data.get('total_policies', 0)
            endpoint_policies = dlp_data.get('endpoint_policies', 0)
            
            if total_policies > 0 and endpoint_policies > 0:
                deployment_rec = new_recommendation(
                    service="Purview",
                    feature=f"{feature_name} - Active Policies",
                    observation=f"Endpoint DLP has {endpoint_policies} active endpoint-scoped policy/policies (of {total_policies} total DLP policies)",
                    recommendation=f"You have {endpoint_policies} Endpoint DLP policy/policies protecting devices from Copilot-related data exfiltration. Ensure these policies: 1) Block copying sensitive Copilot responses to personal email/USB drives/unauthorized cloud storage, 2) Prevent screenshots of confidential AI-generated content, 3) Restrict printing documents that Copilot creates from sensitive sources, 4) Monitor file transfers when users export Copilot summaries containing PII/financial data. Review policy scopes to cover all devices where users access Copilot (Windows endpoints, macOS if deployed). Verify rules detect content patterns common in AI outputs (aggregated data, multi-source summaries, formatted reports). Use Get-DlpCompliancePolicy to audit configurations.",
                    link_text="Manage Endpoint DLP Policies",
                    link_url="https://learn.microsoft.com/purview/endpoint-dlp-using",
                    priority="Low",
                    status="Success",
                    impact_area="Endpoint & browser controls",
                    ai_applicability="External and managed AI"
                )
                deployment_recs.append(deployment_rec)
            elif total_policies > 0:
                deployment_rec = new_recommendation(
                    service="Purview",
                    feature=f"{feature_name} - Configuration",
                    observation=f"DLP policies exist ({total_policies} total) but NONE are configured for endpoint protection",
                    recommendation=f"Decide which sensitive data may be pasted or uploaded to external AI services, then add tested endpoint/browser DLP coverage for managed devices. Start in audit or warn mode, validate representative browser and file-upload scenarios, and move confirmed high-risk events to blocking. Existing M365-location DLP policies ({total_policies}) do not establish endpoint coverage.",
                    link_text="Create Endpoint DLP Policies",
                    link_url="https://learn.microsoft.com/purview/endpoint-dlp-getting-started",
                    priority="Medium",
                    status="Attention Required",
                    impact_area="Endpoint & browser controls",
                    ai_applicability="External and managed AI"
                )
                deployment_recs.append(deployment_rec)
            else:
                deployment_rec = new_recommendation(
                    service="Purview",
                    feature=f"{feature_name} - Configuration",
                    observation="Endpoint DLP license is active but NO DLP policies are configured",
                    recommendation="Before broad use of external AI services with organizational data, define sensitive-data egress rules and deploy tested endpoint/browser DLP or an equivalent control. Begin with audit or warn mode and validate supported paste and upload scenarios before enforcing blocks.",
                    link_text="Deploy Endpoint DLP",
                    link_url="https://learn.microsoft.com/purview/endpoint-dlp-getting-started",
                    priority="High",
                    status="Action Required",
                    impact_area="Endpoint & browser controls",
                    ai_applicability="External and managed AI"
                )
                deployment_recs.append(deployment_rec)
    
    if deployment_recs:
        return [license_rec] + deployment_recs
    
    return [license_rec]

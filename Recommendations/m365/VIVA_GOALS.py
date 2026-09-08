"""
Microsoft Viva Goals - Copilot & Agent Adoption Recommendation
"""
from Core.new_recommendation import new_recommendation
from Core.friendly_names import get_friendly_sku_name

def get_recommendation(sku_name, status="Success", m365_insights=None):
    """
    Viva Goals provides OKR tracking that Copilot can update based on
    progress discussions and automatically measure adoption metrics.
    """
    feature_name = "Microsoft Viva Goals"
    friendly_sku = get_friendly_sku_name(sku_name)
    recommendations = []
    
    if status == "Success":
        license_rec = new_recommendation(
            service="M365",
            feature=feature_name,
            observation=f"{feature_name} is active in {friendly_sku}, enabling AI-driven OKR tracking and progress updates",
            recommendation="",
            link_text="AI-Powered Goals Management",
            link_url="https://learn.microsoft.com/viva/goals/",
            status=status
        )
        recommendations.append(license_rec)
    else:
        license_rec = new_recommendation(
            service="M365",
            feature=feature_name,
            observation=f"{feature_name} is {status} in {friendly_sku}, missing structured goal tracking for AI adoption",
            recommendation=f"Enable {feature_name} only when the organization has named AI use cases and customer-owned measures. Establish a current baseline, choose an adoption or task-outcome target from tenant evidence, set a review date, and record an expand, adjust, or stop decision. Do not use a generic usage or time-savings percentage as proof of value.",
            link_text="AI-Powered Goals Management",
            link_url="https://learn.microsoft.com/viva/goals/",
            priority="Low",
            status=status
        )
        recommendations.append(license_rec)
    
    if status == "Success" and m365_insights and m365_insights.get('available'):
        total_active_users = m365_insights.get('total_active_users', 0)
        
        obs_rec = new_recommendation(
            service="M365",
            feature=feature_name,
            observation=f"OKR tracking readiness: {total_active_users:,} users. Viva Goals enables structured objectives and key results - track Copilot adoption as organizational goal with measurable KRs. AI updates goal progress automatically from usage analytics. Framework for AI transformation accountability and visibility.",
            recommendation="",
            link_text="Viva Goals OKRs",
            link_url="https://learn.microsoft.com/viva/goals/",
            status="Success"
        )
        recommendations.append(obs_rec)
    
    return recommendations

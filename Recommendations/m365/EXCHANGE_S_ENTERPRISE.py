"""
Exchange Online (Plan 2) - Copilot & Agent Adoption Recommendation
"""
from Core.new_recommendation import new_recommendation
from Core.friendly_names import get_friendly_sku_name

async def get_recommendation(sku_name, status="Success", client=None, m365_insights=None):
    """
    Generate recommendation for Exchange Online (Plan 2).
    Exchange provides email and calendar services that Copilot uses
    to draft emails, summarize threads, and manage communications intelligently.
    Returns 2 recommendations: license status + activity baseline status.
    
    Args:
        sku_name: License SKU name
        status: Provisioning status
        client: Graph API client (optional, for legacy support)
        m365_insights: Pre-computed M365 metrics (preferred)
    """
    feature_name = "Exchange Online (Plan 2)"
    friendly_sku = get_friendly_sku_name(sku_name)
    
    # First recommendation: License status
    if status == "Success":
        license_rec = new_recommendation(
            service="M365",
            feature=feature_name,
            observation=f"{feature_name} is active in {friendly_sku}, enabling Copilot to assist with email management and calendar intelligence",
            recommendation="",
            link_text="Intelligent Email with Copilot",
            link_url="https://learn.microsoft.com/exchange/exchange-online",
            status=status
        )
    else:
        license_rec = new_recommendation(
            service="M365",
            feature=feature_name,
            observation=f"{feature_name} is {status} in {friendly_sku}, limiting Copilot's email and scheduling capabilities",
            recommendation=f"Enable {feature_name} to let Copilot transform email productivity. Copilot uses Exchange to draft professional responses, summarize lengthy email threads, suggest meeting times based on calendar availability, and extract action items from conversations.",
            link_text="Intelligent Email with Copilot",
            link_url="https://learn.microsoft.com/exchange/exchange-online",
            priority="High",
            status=status
        )
    
    # Second recommendation: Activity baseline status (only if license is active)
    if status == "Success":
        # Use insights if available, otherwise fall back to legacy client approach
        if m365_insights and m365_insights.get('email_report_available'):
            active_users = m365_insights.get('email_active_users', 0)
            avg_sent = m365_insights.get('email_avg_sent_per_user', 0)
            
            deployment_rec = new_recommendation(
                service="M365",
                feature=f"{feature_name} - Activity Baseline",
                observation=(
                    f"The available email report shows {active_users} active users and an "
                    f"average of {avg_sent} sent messages per reported user. This is workload "
                    "context for pilot selection, not evidence of Copilot value."
                ),
                recommendation="Track the same customer-defined email measures before and during a Copilot pilot, such as drafting cycle time, response quality, rework, and after-hours activity. Select roles with a documented business need and make expansion contingent on measured results.",
                link_text="Exchange Activity Reports",
                link_url="https://learn.microsoft.com/microsoft-365/admin/activity-reports/email-activity",
                priority="Low",
                status=status
            )
            return [license_rec, deployment_rec]

    return [license_rec]

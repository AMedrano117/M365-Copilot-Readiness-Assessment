"""Microsoft Teams entitlement and workload-context inventory."""

from Core.friendly_names import get_friendly_sku_name
from Core.new_recommendation import new_recommendation


async def get_recommendation(sku_name, status="Success", client=None, m365_insights=None):
    """Report Teams entitlement and measured workload context without inferring AI value."""
    feature = "Microsoft Teams"
    sku = get_friendly_sku_name(sku_name)

    if status == "Success":
        license_rec = new_recommendation(
            service="M365",
            feature=feature,
            observation=(
                f"{feature} is active in {sku}. This confirms that Teams-based Copilot "
                "experiences can be considered for appropriately licensed pilot users."
            ),
            recommendation="",
            link_text="Microsoft Teams overview",
            link_url="https://learn.microsoft.com/microsoftteams/teams-overview",
            status=status,
        )
    else:
        license_rec = new_recommendation(
            service="M365",
            feature=feature,
            observation=f"{feature} is {status} in {sku}.",
            recommendation=(
                "If an approved use case depends on Teams meetings, chat, or Teams-hosted "
                "agents, confirm that the intended pilot users have the required Teams service."
            ),
            link_text="Microsoft Teams overview",
            link_url="https://learn.microsoft.com/microsoftteams/teams-overview",
            priority="Medium",
            status="Warning",
        )

    if status != "Success" or not m365_insights or not m365_insights.get("teams_report_available"):
        return [license_rec]

    active_users = m365_insights.get("teams_active_users", 0)
    avg_meetings = m365_insights.get("teams_avg_meetings_per_user", 0)
    total_messages = (
        m365_insights.get("teams_total_team_chat_messages", 0)
        + m365_insights.get("teams_total_private_messages", 0)
    )
    context_rec = new_recommendation(
        service="M365",
        feature=f"{feature} - Workload Context",
        observation=(
            f"The available Teams report shows {active_users} active users, "
            f"{avg_meetings:.1f} average meetings per reported user, and "
            f"{total_messages:,} messages in its reporting period. These measures can help "
            "select a relevant pilot cohort; they do not demonstrate Copilot adoption or value."
        ),
        recommendation="",
        link_text="Microsoft Teams activity reports",
        link_url="https://learn.microsoft.com/microsoft-365/admin/activity-reports/microsoft-teams-user-activity",
        status="Success",
    )
    return [license_rec, context_rec]

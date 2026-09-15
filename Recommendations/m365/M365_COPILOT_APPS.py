"""Microsoft 365 Copilot in Apps entitlement inventory."""

from Core.friendly_names import get_friendly_sku_name
from Core.new_recommendation import new_recommendation


def get_recommendation(sku_name, status="Success", m365_insights=None):
    feature = "Microsoft 365 Copilot in Apps"
    sku = get_friendly_sku_name(sku_name)
    if status == "Success":
        return new_recommendation(
            service="M365",
            feature=feature,
            observation=(
                f"{feature} is active in {sku}. Actual Word, Excel, PowerPoint, "
                "Outlook, Teams, and other adoption is reported from Copilot usage APIs."
            ),
            recommendation="",
            status=status,
            link_text="Copilot in Microsoft 365 Apps",
            link_url="https://learn.microsoft.com/microsoft-365-copilot/microsoft-365-copilot-overview",
        )
    return new_recommendation(
        service="M365",
        feature=feature,
        observation=f"{feature} is {status} in {sku}.",
        recommendation=(
            "Map the named pilot use case to the Microsoft 365 application it needs. "
            "Resolve this entitlement only for users whose pilot requires in-app Copilot."
        ),
        priority="Medium",
        status=status,
        link_text="Copilot in Microsoft 365 Apps",
        link_url="https://learn.microsoft.com/microsoft-365-copilot/microsoft-365-copilot-overview",
    )

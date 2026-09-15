"""Microsoft 365 Copilot Intelligent Search entitlement inventory."""

from Core.friendly_names import get_friendly_sku_name
from Core.new_recommendation import new_recommendation


def get_recommendation(sku_name, status="Success", m365_insights=None):
    feature = "Microsoft 365 Copilot - Intelligent Search"
    sku = get_friendly_sku_name(sku_name)
    if status == "Success":
        return new_recommendation(
            service="M365",
            feature=feature,
            observation=(
                f"{feature} is active in {sku}. This confirms entitlement only; "
                "content volume does not establish search quality or AI value."
            ),
            recommendation="",
            status=status,
            link_text="Microsoft Search",
            link_url="https://learn.microsoft.com/microsoftsearch/overview-microsoft-search",
        )
    return new_recommendation(
        service="M365",
        feature=feature,
        observation=f"{feature} is {status} in {sku}.",
        recommendation=(
            "If a proposed Microsoft 365 Copilot use case depends on tenant-grounded "
            "search, confirm the required entitlement for its pilot users."
        ),
        priority="Medium",
        status=status,
        link_text="Microsoft Search",
        link_url="https://learn.microsoft.com/microsoftsearch/overview-microsoft-search",
    )

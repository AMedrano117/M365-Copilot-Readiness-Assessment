"""OneDrive for Business Plan 2 entitlement inventory."""

from Core.friendly_names import get_friendly_sku_name
from Core.new_recommendation import new_recommendation


async def get_recommendation(sku_name, status="Success", client=None, m365_insights=None):
    feature = "OneDrive for Business (Plan 2)"
    sku = get_friendly_sku_name(sku_name)

    if status == "Success":
        return [new_recommendation(
            service="M365",
            feature=feature,
            observation=(
                f"{feature} is active in {sku}. This confirms entitlement only; "
                "provisioned-drive or file volume does not prove AI adoption or value."
            ),
            recommendation="",
            status=status,
            link_text="OneDrive documentation",
            link_url="https://learn.microsoft.com/onedrive/plan-onedrive-enterprise",
        )]

    return [new_recommendation(
        service="M365",
        feature=feature,
        observation=f"{feature} is {status} in {sku}.",
        recommendation=(
            "Confirm whether the proposed use case needs access to users' OneDrive content. "
            "If it does, resolve the entitlement for the intended pilot users."
        ),
        priority="Medium",
        status=status,
        link_text="OneDrive documentation",
        link_url="https://learn.microsoft.com/onedrive/plan-onedrive-enterprise",
    )]

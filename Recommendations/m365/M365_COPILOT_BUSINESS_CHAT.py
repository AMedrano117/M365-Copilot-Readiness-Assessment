"""Microsoft 365 Copilot Business Chat entitlement inventory."""

from Core.friendly_names import get_friendly_sku_name
from Core.new_recommendation import new_recommendation


def get_recommendation(sku_name, status="Success", m365_insights=None):
    feature = "Microsoft 365 Copilot - Business Chat"
    sku = get_friendly_sku_name(sku_name)
    if status == "Success":
        return new_recommendation(
            service="M365",
            feature=feature,
            observation=(
                f"{feature} is active in {sku}. Entitlement does not prove adoption, "
                "answer quality, or a business outcome."
            ),
            recommendation="",
            status=status,
            link_text="Microsoft 365 Copilot Chat",
            link_url="https://learn.microsoft.com/microsoft-365-copilot/microsoft-365-copilot-chat",
        )
    return new_recommendation(
        service="M365",
        feature=feature,
        observation=f"{feature} is {status} in {sku}.",
        recommendation=(
            "Confirm whether the proposed pilot requires work-grounded Business Chat. "
            "If it does, resolve the entitlement for the intended users before testing it."
        ),
        priority="Medium",
        status=status,
        link_text="Microsoft 365 Copilot Chat",
        link_url="https://learn.microsoft.com/microsoft-365-copilot/microsoft-365-copilot-chat",
    )

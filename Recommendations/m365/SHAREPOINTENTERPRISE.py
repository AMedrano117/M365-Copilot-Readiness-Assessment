"""SharePoint Plan 2 entitlement inventory.

Site or file volume does not establish AI value, content quality, or oversharing risk.
Those questions are handled by Microsoft 365 usage evidence and the dedicated
SharePoint/DAG assessment, so this service-plan module records entitlement only.
"""

from Core.friendly_names import get_friendly_sku_name
from Core.new_recommendation import new_recommendation


async def get_recommendation(sku_name, status="Success", client=None, m365_insights=None):
    feature_name = "SharePoint (Plan 2)"
    friendly_sku = get_friendly_sku_name(sku_name)
    if status == "Success":
        return [new_recommendation(
            service="M365",
            feature=feature_name,
            observation=(
                f"{feature_name} is active in {friendly_sku}. This confirms the "
                "SharePoint entitlement, not content quality, adoption, or safe access."
            ),
            recommendation="",
            link_text="SharePoint documentation",
            link_url="https://learn.microsoft.com/sharepoint/",
            status=status,
        )]

    return [new_recommendation(
        service="M365",
        feature=feature_name,
        observation=f"{feature_name} is {status} in {friendly_sku}.",
        recommendation=(
            "Confirm whether Microsoft 365 Copilot or another proposed AI use case must "
            "ground responses in SharePoint. If so, resolve the SharePoint entitlement "
            "for the intended users before that use case is piloted."
        ),
        link_text="SharePoint documentation",
        link_url="https://learn.microsoft.com/sharepoint/",
        priority="Medium",
        status=status,
    )]

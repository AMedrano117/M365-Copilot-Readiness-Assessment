"""Microsoft Bookings license inventory.

Bookings can support a named appointment-scheduling use case, but ordinary email or meeting
volume does not prove that the tenant needs it. Its provisioning state therefore remains
supporting inventory and never becomes an AI-readiness or scan-coverage finding.
"""
from Core.new_recommendation import new_recommendation
from Core.friendly_names import get_friendly_sku_name

def get_recommendation(sku_name, status="Success", m365_insights=None):
    """Record Bookings availability without inferring value from unrelated activity."""
    feature_name = "Microsoft Bookings (Service)"
    friendly_sku = get_friendly_sku_name(sku_name)
    state = "available" if status == "Success" else str(status or "unknown").lower()
    return [new_recommendation(
        service="M365",
        feature=feature_name,
        observation=(
            f"Microsoft Bookings is {state} in {friendly_sku}. It may support a specifically "
            "approved appointment-scheduling use case, but its availability does not affect "
            "general AI readiness."
        ),
        recommendation="",
        link_text="Microsoft Bookings overview",
        link_url="https://learn.microsoft.com/microsoft-365/bookings/bookings-overview",
        status=status,
        disposition="Reference",
        impact_area="Use-case capability",
        ai_applicability="Named scheduling use cases only",
        evidence_basis="License signal",
        confidence="High",
    )]

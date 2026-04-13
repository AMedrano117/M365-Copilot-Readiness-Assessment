"""
Module for creating recommendation objects
"""

def new_recommendation(
    service,
    feature,
    observation,
    recommendation="",
    link_text="",
    link_url="",
    priority="Medium",
    status="Success",
    evidence_key="",
    evidence_summary="",
):
    """
    Create a new recommendation object
    
    Args:
        service: Service name (e.g., "Entra", "Defender", "Power Platform")
        feature: Feature/Service plan name
        observation: What was observed (e.g., "Feature is disabled")
        recommendation: Suggested action
        link_text: Display text for documentation link
        link_url: URL to relevant documentation
        priority: Priority level ("High", "Medium", "Low") - empty for observations without recommendations
        status: Status of the feature (e.g., "Success", "Disabled", "Not Available")
        evidence_key: Optional evidence bucket identifier for engineer follow-up drill-down.
            Multiple workbook tabs may be supplied as a semicolon-delimited string.
        evidence_summary: Optional short note explaining the follow-up detail available
    
    Returns:
        dict: Recommendation object
    """
    if not all([service, feature, observation]):
        raise ValueError("Service, Feature, and Observation are required")
    
    # If there's no recommendation, don't require or include priority
    if recommendation and priority not in ["High", "Medium", "Low"]:
        raise ValueError("Priority must be 'High', 'Medium', or 'Low' when recommendation is provided")
    
    # For observations without recommendations, set priority to empty string
    if not recommendation:
        priority = ""
    
    return {
        "Service": service,
        "Feature": feature,
        "Status": status,
        "Priority": priority,
        "Observation": observation,
        "Recommendation": recommendation,
        "LinkText": link_text,
        "LinkUrl": link_url,
        "RecommendationId": "",
        "EvidenceKey": evidence_key or "",
        "EvidenceSummary": evidence_summary or "",
        "EvidenceSheet": "",
        "EvidenceAvailable": "No",
    }

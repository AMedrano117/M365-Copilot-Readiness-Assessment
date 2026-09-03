"""
Module for creating recommendation objects
"""

# Status used when the assessment could not read the underlying data (API denied, permission
# missing, report unavailable). Keeps "we could not check this" distinct from "we checked and
# it was clean" - absence of evidence is not evidence of absence.
NOT_ASSESSED_STATUS = "Not Assessed"

# Recommendation categories. Tenant findings describe the customer's environment; scan coverage
# items describe limits of this assessment run (missing API permission, an admin role the service
# principal lacks, a collector error). Mixing the two inflates the finding count and asks the
# customer to action the tool's own configuration.
CATEGORY_TENANT_FINDING = "Tenant Finding"
CATEGORY_SCAN_COVERAGE = "Scan Coverage"


# Statuses that already communicate "something needs doing". A recommendation carrying one of
# these is internally consistent and is left alone.
_ACTION_STATUSES = {
    "critical", "action required", "attention required", "warning", "disabled",
    "pendinginput", "pendingactivation", "not assessed", "not licensed",
    "missing", "missing prerequisite", "permission required",
}


def _reconcile_status_with_priority(status, priority, recommendation):
    """Stop Status and Priority from contradicting each other.

    Cards were being emitted with Status "Success" and Priority "High" - for example
    "Customer Lockbox license active but DISABLED" or "DLP policies exist but NONE target
    endpoints". That makes the Status filter useless: filtering to Success returns
    high-priority failures, and a reader scanning green badges skips real gaps.

    "Success" means the control was checked and is in good shape. If there is an action to
    take, the status is raised to match its urgency. A Low-priority tip on an otherwise healthy
    control is left as Success, because that is what it is.
    """
    if not recommendation:
        return status

    normalized = str(status or "").strip().lower()
    if normalized in _ACTION_STATUSES or normalized == "insight":
        return status
    if normalized != "success":
        return status

    if priority == "High":
        return "Action Required"
    if priority == "Medium":
        return "Attention Required"
    return status


def new_recommendation(
    service,
    feature,
    observation,
    recommendation="",
    link_text="",
    link_url="",
    priority="Medium",
    status="Success",
    category=CATEGORY_TENANT_FINDING,
    finding_key="",
    evidence_key="",
    evidence_summary="",
    disposition="",
    impact_area="",
    ai_applicability="",
    evidence_basis="",
    confidence="",
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
        category: CATEGORY_TENANT_FINDING (default) for findings about the customer's tenant, or
            CATEGORY_SCAN_COVERAGE for limits of this assessment run (missing permission, collector
            error) which are reported separately and excluded from tenant finding counts
        finding_key: Optional stable identifier for the underlying tenant condition, e.g.
            "purview.ediscovery.no_cases". Several licences can surface the same condition -
            eDiscovery cases are checked by three different service plans - and without a shared
            key each emits its own card for one issue. Cards sharing a key are collapsed to the
            highest-severity one, which lists the contributing licences.
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

    # Preserve what the service module reported before consistency repair.  The assessment
    # model uses this to distinguish an optional suggestion attached to a healthy control from
    # a real gap.  For example, "start building three agents" is an opportunity, while
    # "Customer Lockbox is disabled" is an action even if an older module supplied Success.
    source_status = status
    status = _reconcile_status_with_priority(status, priority, recommendation)

    return {
        "Service": service,
        "Feature": feature,
        "Status": status,
        "Priority": priority,
        "Observation": observation,
        "Recommendation": recommendation,
        "LinkText": link_text,
        "LinkUrl": link_url,
        "Category": category or CATEGORY_TENANT_FINDING,
        "FindingKey": finding_key or "",
        "RecommendationId": "",
        "EvidenceKey": evidence_key or "",
        "EvidenceSummary": evidence_summary or "",
        "EvidenceSheet": "",
        "EvidenceAvailable": "No",
        "SourceStatus": source_status or "",
        "Disposition": disposition or "",
        "ImpactArea": impact_area or "",
        "AIApplicability": ai_applicability or "",
        "EvidenceBasis": evidence_basis or "",
        "Confidence": confidence or "",
        "ReadinessStage": "",
    }

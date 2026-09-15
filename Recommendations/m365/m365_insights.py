"""
M365 Insights - Pre-computed usage & deployment metrics from M365 client
Similar to entra_insights and defender_insights patterns.

This module provides helper functions to generate observation text,
recommendations, and parsed metrics from Graph API usage reports.

All CSV reports are parsed ONCE during client initialization (not in concurrent feature processing).
Feature files get pre-computed metrics via simple dict access - no parsing overhead.
"""

# ============================================================================
# OBSERVATION HELPERS - Generate text for deployment status
# ============================================================================

def get_sites_observation(m365_insights):
    """
    Generate SharePoint sites observation text.

    Args:
        m365_insights: dict from extract_m365_insights_from_client()
    
    Returns:
        str: Observation text for sites deployment, or empty string if no data
    """
    if not m365_insights or not m365_insights.get('available'):
        return ""
    
    total_sites = m365_insights.get('total_sites', 0)
    
    if total_sites == 0:
        return "No SharePoint sites were returned by the site inventory"
    return (
        f"{total_sites} SharePoint sites were returned by the site inventory. "
        "Site count is workload context and does not establish content quality, "
        "permission safety, or AI value."
    )


def get_sites_recommendation(m365_insights):
    """
    Generate SharePoint sites recommendation text.
    
    Args:
        m365_insights: dict from extract_m365_insights_from_client()
    
    Returns:
        str: Recommendation text, or empty string if no action needed
    """
    if not m365_insights or not m365_insights.get('available'):
        return ""
    
    total_sites = m365_insights.get('total_sites', 0)
    
    # A site count alone cannot justify creating more sites or predict AI value.
    return ""


def get_users_observation(m365_insights):
    """
    Generate user licensing observation text.
    
    Args:
        m365_insights: dict from extract_m365_insights_from_client()
    
    Returns:
        str: Observation text for user counts and Copilot adoption
    """
    if not m365_insights or not m365_insights.get('available'):
        return ""
    
    total_users = m365_insights.get('total_users', 0)
    copilot_licensed = m365_insights.get('copilot_licensed_users')
    license_coverage = m365_insights.get('copilot_license_coverage')
    
    if total_users == 0:
        return "User data unavailable (User.Read.All permission may be missing)"
    
    observation_parts = [
        f"{total_users} users returned (collection incomplete)"
        if m365_insights.get('user_data_sampled') else f"{total_users} total users"
    ]

    if copilot_licensed is None:
        known = m365_insights.get('known_copilot_licensed_users')
        licensing_text = "Copilot license count not established"
        if known:
            licensing_text += f" (at least {known} assigned licenses confirmed)"
        reason = m365_insights.get('copilot_license_coverage_reason')
        if reason:
            licensing_text += f": {reason}"
        observation_parts.append(licensing_text)
    elif copilot_licensed > 0:
        population = m365_insights.get('copilot_license_coverage_population', 'the estimated eligible population')
        coverage_text = "coverage not calculated" if license_coverage is None else f"{license_coverage}% license coverage of {population}"
        observation_parts.append(f"{copilot_licensed} with Copilot licenses ({coverage_text})")
    else:
        observation_parts.append("no Copilot licenses assigned yet")
    
    return ", ".join(observation_parts)


def get_copilot_adoption_recommendation(m365_insights):
    """
    Generate Copilot license adoption recommendation.
    
    Args:
        m365_insights: dict from extract_m365_insights_from_client()
    
    Returns:
        str: Recommendation for Copilot license rollout strategy
    """
    if not m365_insights or not m365_insights.get('available'):
        return ""
    
    total_users = m365_insights.get('total_users', 0)
    copilot_licensed = m365_insights.get('copilot_licensed_users')
    license_coverage = m365_insights.get('copilot_license_coverage')
    
    if total_users == 0:
        return ""
    
    if copilot_licensed is None:
        return "Confirm access to user license assignments and the tenant subscription catalog, then rerun licensing collection. Review existing entitlements before making Copilot rollout or license changes."
    if copilot_licensed == 0:
        return "Define a bounded pilot from the approved use cases and intended-user profile. Establish the customer's outcome and risk measures before assigning Copilot licenses."
    elif license_coverage is not None and license_coverage < 10:
        return f"Copilot licenses currently cover {license_coverage}% of the tenant-derived eligible-user estimate. Treat this as deployment reach, not adoption: validate active use, task quality, and a customer-defined outcome before expanding to similar roles."
    elif license_coverage is not None and license_coverage < 30:
        return f"Copilot licenses currently cover {license_coverage}% of the tenant-derived eligible-user estimate. Review the dedicated active-user and prompt reports, identify supported use cases, and expand only when the tenant's own success criteria are met."
    elif license_coverage is not None and license_coverage < 60:
        return f"Copilot licenses currently cover {license_coverage}% of the tenant-derived eligible-user estimate. Compare license assignment with active usage, address enablement barriers, and reclaim or reassign persistently unused licenses."
    
    return ""  # High adoption already achieved


def get_reports_observation(m365_insights):
    """
    Generate observation about available usage reports.
    
    Args:
        m365_insights: dict from extract_m365_insights_from_client()
    
    Returns:
        str: Summary of what usage data is available for analysis
    """
    if not m365_insights or not m365_insights.get('available'):
        return ""
    
    available_reports = []
    
    if m365_insights.get('email_report_available'):
        available_reports.append('Email activity')
    if m365_insights.get('teams_report_available'):
        available_reports.append('Teams activity')
    if m365_insights.get('sharepoint_report_available'):
        available_reports.append('SharePoint usage')
    if m365_insights.get('onedrive_report_available'):
        available_reports.append('OneDrive usage')
    if m365_insights.get('activations_report_available'):
        available_reports.append('Office activations')
    if m365_insights.get('active_users_report_available'):
        available_reports.append('Active users')
    
    if not available_reports:
        return "Usage reports unavailable (Reports.Read.All permission may be missing)"
    
    report_list = ', '.join(available_reports)
    return f"Usage reports available: {report_list} (workload context for pilot selection)"


def get_reports_recommendation(m365_insights):
    """
    Generate recommendation about establishing usage baselines.
    
    Args:
        m365_insights: dict from extract_m365_insights_from_client()
    
    Returns:
        str: Guidance on using usage reports for Copilot ROI measurement
    """
    if not m365_insights or not m365_insights.get('available'):
        return ""
    
    has_teams = m365_insights.get('teams_report_available', False)
    has_email = m365_insights.get('email_report_available', False)
    has_sharepoint = m365_insights.get('sharepoint_report_available', False)
    
    missing_reports = []
    if not has_teams:
        missing_reports.append('Teams')
    if not has_email:
        missing_reports.append('Email')
    if not has_sharepoint:
        missing_reports.append('SharePoint')
    
    if missing_reports:
        return (
            f"Confirm Reports.Read.All access if {', '.join(missing_reports)} workload "
            "context is needed for pilot selection. Missing workload reports do not prove "
            "low adoption or block a security-readiness conclusion."
        )
    
    # If all reports available, provide ROI guidance
    return "Establish use-case-specific baselines before rollout, such as task cycle time, quality review results, rework, or risk exceptions. After deployment, compare the same measures for the pilot cohort and apply a customer-approved expand, adjust, or stop decision. Workload volume alone does not demonstrate ROI."


def has_sufficient_data_for_observations(m365_insights):
    """
    Check if M365 client has enough data to provide meaningful observations.
    
    Args:
        m365_insights: dict from extract_m365_insights_from_client()
    
    Returns:
        bool: True if at least basic data (sites OR users OR reports) is available
    """
    if not m365_insights or not m365_insights.get('available'):
        return False
    
    has_sites = m365_insights.get('total_sites', 0) > 0
    has_users = m365_insights.get('total_users', 0) > 0
    has_any_report = (
        m365_insights.get('email_report_available', False) or
        m365_insights.get('teams_report_available', False) or
        m365_insights.get('sharepoint_report_available', False) or
        m365_insights.get('onedrive_report_available', False)
    )
    
    return has_sites or has_users or has_any_report


def get_missing_permissions_warning(m365_insights):
    """
    Get a warning message if critical permissions are missing.
    
    Args:
        m365_insights: dict from extract_m365_insights_from_client()
    
    Returns:
        str: Warning message or empty string
    """
    if not m365_insights:
        return ""
    
    missing = m365_insights.get('missing_permissions', [])
    
    if not missing:
        return ""
    
    if len(missing) == 1:
        return f"Missing permission: {missing[0]} - some deployment observations unavailable"
    else:
        return f"Missing permissions: {', '.join(missing)} - limited deployment visibility"


# ============================================================================
# METRIC ACCESS - Pre-parsed values from CSV reports (no parsing needed!)
# ============================================================================

def get_site_count(m365_insights):
    """Get total SharePoint sites deployed"""
    return m365_insights.get('total_sites', 0) if m365_insights else 0

def get_site_names(m365_insights):
    """Get list of SharePoint site display names"""
    return m365_insights.get('site_names', []) if m365_insights else []

def get_total_users(m365_insights):
    """Get total user count (sampled if >999)"""
    return m365_insights.get('total_users', 0) if m365_insights else 0

def get_copilot_licensed_count(m365_insights):
    """Get the assigned Copilot license count, or None when not established."""
    return m365_insights.get('copilot_licensed_users') if m365_insights else None

def get_copilot_adoption_percentage(m365_insights):
    """Compatibility accessor: returns license coverage, not active adoption."""
    if not m365_insights:
        return None
    return m365_insights.get('copilot_license_coverage')

def is_user_data_sampled(m365_insights):
    """Check if user data is sampled (>999 users - only first 999 retrieved)"""
    return m365_insights.get('user_data_sampled', False) if m365_insights else False

# Teams Metrics
def get_teams_active_users(m365_insights):
    """Get number of Teams active users in last 30 days"""
    return m365_insights.get('teams_active_users', 0) if m365_insights else 0

def get_teams_total_meetings(m365_insights):
    """Get total Teams meetings in last 30 days"""
    return m365_insights.get('teams_total_meetings', 0) if m365_insights else 0

def get_teams_avg_meetings_per_user(m365_insights):
    """Get average meetings per user"""
    return m365_insights.get('teams_avg_meetings_per_user', 0) if m365_insights else 0

# Email Metrics
def get_email_active_users(m365_insights):
    """Get number of email active users in last 30 days"""
    return m365_insights.get('email_active_users', 0) if m365_insights else 0

def get_email_avg_sent_per_user(m365_insights):
    """Get average emails sent per user"""
    return m365_insights.get('email_avg_sent_per_user', 0) if m365_insights else 0

# SharePoint Metrics
def get_sharepoint_active_sites(m365_insights):
    """Get number of active SharePoint sites (with page views)"""
    return m365_insights.get('sharepoint_active_sites', 0) if m365_insights else 0

def get_sharepoint_activity_rate(m365_insights):
    """Get SharePoint site activity rate as percentage"""
    return m365_insights.get('sharepoint_activity_rate', 0) if m365_insights else 0

# OneDrive Metrics
def get_onedrive_adoption_rate(m365_insights):
    """Get OneDrive adoption rate as percentage"""
    return m365_insights.get('onedrive_adoption_rate', 0) if m365_insights else 0

def get_onedrive_active_accounts(m365_insights):
    """Get number of active OneDrive accounts"""
    return m365_insights.get('onedrive_active_accounts', 0) if m365_insights else 0

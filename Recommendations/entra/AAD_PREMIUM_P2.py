"""
Microsoft Entra ID P2 - Copilot & Agent Adoption Recommendation
"""
from Core.new_recommendation import new_recommendation, NOT_ASSESSED_STATUS
from Core.friendly_names import get_friendly_sku_name
from .entra_insights import build_pim_metrics, entra_source_was_read

def get_recommendation(sku_name, status="Success", client=None, entra_insights=None):
    """
    Microsoft Entra ID P2 provides identity governance and privileged identity management
    critical for managing Copilot admin roles and protecting AI workloads.
    
    When entra_insights is provided, enriches observations with:
    - Privileged Identity Management (PIM) metrics
    - Access Review metrics
    - Identity Protection metrics
    """
    feature_name = "Microsoft Entra ID P2"
    friendly_sku = get_friendly_sku_name(sku_name)
    recommendations = []
    
    if status == "Success":
        # Base observation for license check
        observation = f"{feature_name} is active in {friendly_sku}, providing identity governance for Copilot administration"
        recommendation_text = ""
        
        # Cache metrics dictionaries (empty if entra_insights not available)
        pim_metrics = {}
        access_review_metrics = {}
        risk_metrics = {}
        
        # Enrich with metrics when entra_insights is available
        if entra_insights and entra_insights.get('available'):
            # Populate metrics dictionaries to avoid redundant lookups
            pim_metrics = entra_insights.get('pim_metrics', {})
            access_review_metrics = entra_insights.get('access_review_metrics', {})
            risk_metrics = entra_insights.get('risk_metrics', {})
            
            metrics = []
            
            # PIM metrics - only show positive findings
            if pim_metrics.get('eligible_admins_count', 0) > 0:
                metrics.append(f"{pim_metrics['eligible_admins_count']} eligible admin(s) configured with PIM")
            
            # Access Review metrics - only show positive findings
            if access_review_metrics.get('total_active_reviews', 0) > 0:
                metrics.append(f"{access_review_metrics['total_active_reviews']} active access review(s)")
            
            if metrics:
                observation += ". " + ", ".join(metrics)
            
            # Generate proactive recommendations based on findings
            _, recommendation_text = build_pim_metrics(entra_insights)
        
        # Primary license check observation
        recommendations.append(new_recommendation(
            service="Entra",
            feature=feature_name,
            observation=observation,
            recommendation=recommendation_text,
            link_text="Identity Governance for Copilot Admins",
            link_url="https://learn.microsoft.com/entra/id-governance/identity-governance-overview",
            status=status
        ))
        
        # Additional observations when entra_insights is available
        if entra_insights and entra_insights.get('available'):
            # Observation 1: PIM configuration status
            permanent_count = pim_metrics.get('permanent_admins_count', 0)
            permanent_global_admins = pim_metrics.get('permanent_global_admins', 0)
            if permanent_count > 0:
                # Role assignment schedules explicitly marked no-expiration are standing.
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation=(
                        f"{permanent_count} active directory role assignment schedule(s) have no expiration"
                        + (f", including {permanent_global_admins} Global Administrator assignment(s)" if permanent_global_admins else "")
                    ),
                    recommendation="Review the named principals and roles in Admin Role Detail. Convert standing assignments to PIM eligibility or time-bound activation where operationally feasible, retaining documented emergency-access exceptions.",
                    link_text="Configure PIM for Copilot Admins",
                    link_url="https://learn.microsoft.com/entra/id-governance/privileged-identity-management/pim-configure",
                    priority="High" if permanent_global_admins else "Medium",
                    status="Action Required",
                    evidence_key="admin_role_detail",
                    evidence_summary="See Admin Role Detail for the privileged assignments that support this PIM follow-up."
                ))
            elif not entra_source_was_read(entra_insights, 'role_assignment_schedules'):
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation="Privileged role assignment schedules could not be read, so standing-versus-time-bound administrative access is unverified",
                    recommendation="Grant RoleManagement.Read.Directory and rerun, or review active and eligible assignments in Privileged Identity Management before broad AI rollout.",
                    link_text="Review PIM Assignments",
                    link_url="https://learn.microsoft.com/entra/id-governance/privileged-identity-management/pim-how-to-add-role-to-user",
                    priority="Medium",
                    status="Not Assessed",
                    disposition="Coverage",
                    evidence_key="admin_role_detail",
                    evidence_summary="Active role assignments were not treated as permanent without schedule-expiration evidence."
                ))
            else:
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation="No no-expiration active role assignment schedules were returned by the assessed PIM endpoint",
                    recommendation="",
                    link_text="PIM Best Practices",
                    link_url="https://learn.microsoft.com/entra/id-governance/privileged-identity-management/pim-configure",
                    status="Success",
                    evidence_key="admin_role_detail",
                    evidence_summary="See Admin Role Detail for the eligible and scheduled assignments currently present in the tenant."
                ))
            
            # Observation 2: Access Review configuration status
            active_reviews = access_review_metrics.get('total_active_reviews', 0)
            if active_reviews == 0:
                # Access reviews are useful lifecycle governance, but their absence does not by
                # itself prove that AI or M365 access is inappropriate.
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation="No active access reviews were returned for user access governance",
                    recommendation="Consider periodic reviews for privileged roles, guests, and AI entitlement groups where manual lifecycle controls are insufficient. Confirm the population and risk before creating campaigns.",
                    link_text="Configure Access Reviews",
                    link_url="https://learn.microsoft.com/entra/id-governance/access-reviews-overview",
                    priority="Medium",
                    status="Insight",
                    disposition="Opportunity",
                    evidence_key="access_review_detail",
                    evidence_summary="See Access Review Detail for the current review definitions and cadence available for engineer follow-up."
                ))
            else:
                # Success: Access reviews are configured (positive finding)
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation=f"{active_reviews} active access review(s) monitoring user access to Copilot services",
                    recommendation="",
                    link_text="Access Reviews Overview",
                    link_url="https://learn.microsoft.com/entra/id-governance/access-reviews-overview",
                    status="Success",
                    evidence_key="access_review_detail",
                    evidence_summary="See Access Review Detail for the active review definitions and review scope already configured."
                ))
            
            # Observation 3: Identity Protection risk status
            high_risk = risk_metrics.get('high_risk_users', 0)
            medium_risk = risk_metrics.get('medium_risk_users', 0)
            if high_risk > 0 or medium_risk > 0:
                # Action Required: Risky users detected
                total_risky = high_risk + medium_risk
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation=f"{total_risky} risky user(s) detected ({high_risk} high-risk, {medium_risk} medium-risk) who may have access to Copilot services",
                    recommendation="Review and remediate risky users immediately. Compromised accounts with Copilot access can lead to data exfiltration through AI prompts. Enable risk-based conditional access policies to block risky sign-ins automatically.",
                    link_text="Investigate Risky Users",
                    link_url="https://learn.microsoft.com/entra/id-protection/howto-identity-protection-investigate-risk",
                    priority="High" if high_risk > 0 else "Medium",
                    status="Action Required"
                ))
            elif not entra_source_was_read(entra_insights, 'risky_users', 'risk_detections'):
                # Identity Protection data was never read - do not report unread as clean
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation="Identity Protection risk data could not be retrieved, so account risk is unverified",
                    recommendation="Grant the assessment IdentityRiskyUser.Read.All and IdentityRiskEvent.Read.All, then rerun. Compromised accounts are a primary route to Copilot data exfiltration, so risk status should be confirmed before broad rollout. Meanwhile review Entra ID Protection > Risky users directly.",
                    link_text="Identity Protection Risk Reports",
                    link_url="https://learn.microsoft.com/entra/id-protection/howto-identity-protection-investigate-risk",
                    priority="Medium",
                    status=NOT_ASSESSED_STATUS
                ))
            else:
                # Success: risk data was read and came back empty (positive finding)
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation="No risky users detected, Identity Protection is actively monitoring and protecting accounts",
                    recommendation="",
                    link_text="Identity Protection Overview",
                    link_url="https://learn.microsoft.com/entra/id-protection/overview-identity-protection",
                    status="Success"
                ))
            
            # Observation 4: Guest user (B2B) access to Copilot
            b2b_metrics = entra_insights.get('b2b_summary', {})
            total_guests = b2b_metrics.get('total_guests', 0)
            guests_with_licenses = b2b_metrics.get('guests_with_licenses', 0)
            
            if guests_with_licenses > 0:
                # An M365 license does not prove Copilot entitlement or inappropriate content
                # access. Surface this for scoped review without declaring a security failure.
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation=f"{guests_with_licenses} of {total_guests} guest users have an M365 license; this does not by itself prove Copilot entitlement or inappropriate data access",
                    recommendation=f"Verify that the {guests_with_licenses} licensed guest account(s) still need their assigned products and content permissions. Review actual site, team, and group access separately.",
                    link_text="Manage Guest Access",
                    link_url="https://learn.microsoft.com/entra/external-id/what-is-b2b",
                    priority="Medium",
                    status="Insight",
                    disposition="Opportunity",
                    evidence_key="guest_access_detail",
                    evidence_summary="See Guest Access Detail for the guest objects, licensing state, and external collaboration settings that support this finding."
                ))
            elif total_guests > 10:
                # Informational: Many guests but no licenses
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation=f"{total_guests} guest users in tenant, none with M365 licenses - external collaboration isolated from Copilot access",
                    recommendation="",
                    link_text="External Identities Overview",
                    link_url="https://learn.microsoft.com/entra/external-id/",
                    status="Success",
                    evidence_key="guest_access_detail",
                    evidence_summary="See Guest Access Detail for the guest inventory and current licensing state reviewed for this observation."
                ))
            
            # Observation 5: External sharing policy restrictions
            guest_invite_setting = b2b_metrics.get('guest_invite_restrictions', 'Unknown')

            
            # Convert enum to string if needed
            if not isinstance(guest_invite_setting, str):
                guest_invite_setting = str(guest_invite_setting)
            
            if guest_invite_setting == 'Unknown':
                pass  # Skip if data not available
            elif 'admins' not in guest_invite_setting.lower() and 'limited' not in guest_invite_setting.lower():
                # Invitation eligibility is not the same as resource access. Treat this as a
                # governance choice and review actual sharing/permissions separately.
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation=f"Guest invitation eligibility is configured as '{guest_invite_setting}'; invited guests still require explicit resource access",
                    recommendation="Confirm that the invitation model matches collaboration policy. If tighter sponsorship is required, limit invitation rights or add approval; validate actual SharePoint, Teams, and group permissions independently.",
                    link_text="Configure External Collaboration",
                    link_url="https://learn.microsoft.com/entra/external-id/external-collaboration-settings-configure",
                    priority="Medium",
                    status="Insight",
                    disposition="Opportunity",
                    evidence_key="guest_access_detail",
                    evidence_summary="See Guest Access Detail for the invitation restrictions and guest inventory associated with this external collaboration setting."
                ))
            else:
                # Success: Restrictive guest invites
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation=f"Guest user invitations restricted to '{guest_invite_setting}', controlling external access to Copilot-searchable content",
                    recommendation="",
                    link_text="External Collaboration Settings",
                    link_url="https://learn.microsoft.com/entra/external-id/external-collaboration-settings-configure",
                    status="Success",
                    evidence_key="guest_access_detail",
                    evidence_summary="See Guest Access Detail for the external collaboration settings and guest object inventory reviewed for this control."
                ))
            
            # Observation 6: Cross-tenant access settings
            cross_tenant_configured = b2b_metrics.get('cross_tenant_access_configured', False)
            partner_count = b2b_metrics.get('partner_configurations', 0)
            
            if not cross_tenant_configured:
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation="No named cross-tenant partner configuration was returned; Entra default inbound and outbound settings apply",
                    recommendation="",
                    link_text="Cross-Tenant Access Settings",
                    link_url="https://learn.microsoft.com/entra/external-id/cross-tenant-access-overview",
                    status="Success",
                    evidence_key="guest_access_detail",
                    evidence_summary="See Guest Access Detail for the cross-tenant configuration state and guest collaboration context behind this finding."
                ))
            elif partner_count == 0:
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation="Cross-tenant access uses the configured default settings; no partner-specific overrides were returned",
                    recommendation="",
                    link_text="Configure Partner Organizations",
                    link_url="https://learn.microsoft.com/entra/external-id/cross-tenant-access-settings-b2b-collaboration",
                    status="Success",
                    evidence_key="guest_access_detail",
                    evidence_summary="See Guest Access Detail for the cross-tenant access configuration and partner-policy counts supporting this observation."
                ))
            else:
                # Success: Cross-tenant policies configured with partners
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation=f"Cross-tenant access policies configured with {partner_count} partner organization(s), governing external access to Copilot content",
                    recommendation="",
                    link_text="Cross-Tenant Access Settings",
                    link_url="https://learn.microsoft.com/entra/external-id/cross-tenant-access-overview",
                    status="Success",
                    evidence_key="guest_access_detail",
                    evidence_summary="See Guest Access Detail for the guest configuration and partner-policy inventory reviewed for this control."
                ))
            
            # Observation 7: Application Consent Settings
            consent_metrics = entra_insights.get('consent_summary', {})
            user_consent_allowed = consent_metrics.get('user_consent_allowed', False)
            admin_consent_required = consent_metrics.get('admin_consent_required', False)
            data_sources = entra_insights.get('data_sources', {}) or {}
            consent_policy_read = data_sources.get('consent_policies', False)

            if not consent_policy_read:
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation="Application consent policy settings could not be read, so the user-versus-admin consent boundary is unverified",
                    recommendation="Grant Policy.Read.All and rerun, or verify user consent settings and the admin-consent workflow directly in Entra.",
                    link_text="Configure User Consent",
                    link_url="https://learn.microsoft.com/entra/identity/enterprise-apps/configure-user-consent",
                    priority="Medium",
                    status="Not Assessed",
                    disposition="Coverage",
                    evidence_key="app_access_detail",
                    evidence_summary="Application grants remain available for review, but the tenant consent-policy conclusion was not collected."
                ))
            elif user_consent_allowed and not admin_consent_required:
                # Action Required: User consent enabled
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation="User consent enabled for applications, allowing users to grant apps access to Copilot-generated content and M365 data without admin review",
                    recommendation="Apply a risk-based app-consent policy. Limit user consent to verified publishers and low-impact delegated permissions where appropriate, require admin review for higher-impact scopes, and operate an admin-consent workflow. Review existing grants and recent activity before revocation.",
                    link_text="Configure User Consent",
                    link_url="https://learn.microsoft.com/entra/identity/enterprise-apps/configure-user-consent",
                    priority="High",
                    status="Action Required",
                    evidence_key="app_access_detail",
                    evidence_summary="See App Access Detail for the application grants, flagged scopes, publisher state, and available activity behind this consent finding."
                ))
            elif admin_consent_required:
                # Success: Admin consent required
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation="Admin consent required for applications, protecting Copilot data from unauthorized app access",
                    recommendation="",
                    link_text="Admin Consent Workflow",
                    link_url="https://learn.microsoft.com/entra/identity/enterprise-apps/configure-admin-consent-workflow",
                    status="Success",
                    evidence_key="app_access_detail",
                    evidence_summary="See App Access Detail for the reviewed application grants and publishers associated with current app access."
                ))
            else:
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation="The assessed consent policy did not identify either an allowed user-consent policy or an admin-only consent boundary",
                    recommendation="Review the current consent policy identifiers and confirm that higher-impact permissions require administrator approval.",
                    link_text="Configure User Consent",
                    link_url="https://learn.microsoft.com/entra/identity/enterprise-apps/configure-user-consent",
                    priority="Medium",
                    status="Insight",
                    disposition="Opportunity",
                    evidence_key="app_access_detail",
                    evidence_summary="See App Access Detail for the existing application grants and publishers associated with this policy review."
                ))
            
            # Observation 8: Risky Application Permissions
            high_privilege_apps = consent_metrics.get('high_privilege_apps', 0)
            unverified_publishers = consent_metrics.get('unverified_publishers', 0)
            
            app_grants_read = data_sources.get('oauth_grants', False) and data_sources.get('service_principals', False)
            if not app_grants_read:
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation="Enterprise application grants or publisher metadata could not be read, so connected-app access to Microsoft 365 data is unverified",
                    recommendation="Grant Application.Read.All and DelegatedPermissionGrant.Read.All (or the documented equivalent), then rerun and review exact scopes and recent activity.",
                    link_text="Review Enterprise Applications",
                    link_url="https://learn.microsoft.com/entra/identity/enterprise-apps/overview",
                    priority="Medium",
                    status="Not Assessed",
                    disposition="Coverage",
                    evidence_key="app_access_detail",
                    evidence_summary="The application inventory or delegated grant dataset was unavailable; no clean app-governance conclusion is supported."
                ))
            elif high_privilege_apps > 0:
                risk_details = []
                if high_privilege_apps > 0:
                    risk_details.append(f"{high_privilege_apps} with high-privilege permissions")
                if unverified_publishers > 0:
                    risk_details.append(f"{unverified_publishers} from unverified publishers")
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation=f"Application grants requiring review were detected: {', '.join(risk_details)}",
                    recommendation="Review the flagged enterprise applications, their owners, grant type, exact scopes, publisher, business purpose, and recent activity. Remove unused grants and reduce scopes where the evidence shows access exceeds the documented need; do not revoke solely from the aggregate count.",
                    link_text="App Governance",
                    link_url="https://learn.microsoft.com/defender-cloud-apps/app-governance-manage-app-governance",
                    priority="High",
                    status="Action Required",
                    finding_key="entra.app_consent.high_impact_grants",
                    evidence_key="app_access_detail",
                    evidence_summary="See App Access Detail for the exact risky applications, granted scopes, count logic, and available 30-day activity."
                ))
            elif unverified_publishers > 0:
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation=f"{unverified_publishers} externally owned application(s) with delegated grants did not return verified-publisher metadata",
                    recommendation="Confirm the publisher, business owner, granted scopes, and recent use before expanding AI integrations. Publisher verification is a review signal, not proof that an application is malicious.",
                    link_text="Publisher Verification",
                    link_url="https://learn.microsoft.com/entra/identity-platform/publisher-verification-overview",
                    priority="Medium",
                    status="Insight",
                    disposition="Opportunity",
                    evidence_key="app_access_detail",
                    evidence_summary="See App Access Detail for the affected applications and exact delegated scopes."
                ))
            else:
                # Success: No risky apps
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation="The assessed grant rules did not flag high-impact permissions or unverified publishers; this does not replace application-owner review",
                    recommendation="",
                    link_text="App Governance",
                    link_url="https://learn.microsoft.com/defender-cloud-apps/app-governance-manage-app-governance",
                    status="Success",
                    evidence_key="app_access_detail",
                    evidence_summary="See App Access Detail for the application inventory and flagged permission review captured during the assessment."
                ))
            
            # Observation 9: Access Reviews for Copilot Governance
            access_review_metrics = entra_insights.get('access_review_summary', {})
            if access_review_metrics:
                total_reviews = access_review_metrics.get('total_definitions', 0)
                active_reviews = access_review_metrics.get('active_reviews', 0)
                group_reviews = access_review_metrics.get('group_reviews', 0)
                role_reviews = access_review_metrics.get('role_reviews', 0)
                guest_reviews = access_review_metrics.get('guest_reviews', 0)
                recurring_reviews = access_review_metrics.get('recurring_reviews', 0)
                
                if total_reviews == 0:
                    # Optional lifecycle control unless a scoped requirement exists.
                    recommendations.append(new_recommendation(
                        service="Entra",
                        feature=feature_name,
                        observation="No access review definitions were returned",
                        recommendation="Consider recurring reviews for privileged roles, guests, and AI entitlement groups where lifecycle risk or compliance requirements justify them. Do not create reviews solely to increase an assessment score.",
                        link_text="Configure Access Reviews",
                        link_url="https://learn.microsoft.com/entra/id-governance/deploy-access-reviews",
                        priority="Medium",
                        status="Insight",
                        disposition="Opportunity",
                        evidence_key="access_review_detail",
                        evidence_summary="See Access Review Detail for the current review definitions and missing cadence relevant to this governance gap."
                    ))
                elif recurring_reviews == 0:
                    recommendations.append(new_recommendation(
                        service="Entra",
                        feature=feature_name,
                        observation=f"{total_reviews} access review(s) configured, but none are recurring - Copilot access not continuously governed",
                        recommendation=f"Convert {total_reviews} one-time access review(s) to recurring campaigns for continuous governance. One-time reviews: 1) Expire after completion (no ongoing oversight), 2) Require manual re-creation (administrative burden), 3) Create gaps in compliance coverage, 4) Miss new users added between reviews. Update review configurations to recur quarterly for group memberships, monthly for privileged roles, and semi-annually for guest access. Configure auto-apply results to automatically remove access for non-approved users. Set up notifications to reviewers 1 week before due date.",
                        link_text="Create Recurring Reviews",
                        link_url="https://learn.microsoft.com/entra/id-governance/create-access-review",
                        priority="Medium",
                        status="Insight",
                        disposition="Opportunity",
                        evidence_key="access_review_detail",
                        evidence_summary="See Access Review Detail for the one-time and recurring review definitions supporting this recommendation."
                    ))
                elif group_reviews == 0:
                    recommendations.append(new_recommendation(
                        service="Entra",
                        feature=feature_name,
                        observation=f"{total_reviews} access review(s) active ({recurring_reviews} recurring), but no group membership reviews for Copilot license governance",
                        recommendation=f"If Copilot license groups are used, consider recurring membership reviews where manager attestation adds value. Current reviews cover {role_reviews} role scope(s) and {guest_reviews} guest scope(s).",
                        link_text="Review Group Memberships",
                        link_url="https://learn.microsoft.com/entra/id-governance/create-access-review",
                        priority="Medium",
                        status="Insight",
                        disposition="Opportunity",
                        evidence_key="access_review_detail",
                        evidence_summary="See Access Review Detail for the review scopes currently present and where group-review coverage is missing."
                    ))
                else:
                    # Success: Comprehensive access reviews configured
                    review_types = []
                    if group_reviews > 0:
                        review_types.append(f"{group_reviews} group")
                    if role_reviews > 0:
                        review_types.append(f"{role_reviews} role")
                    if guest_reviews > 0:
                        review_types.append(f"{guest_reviews} guest")
                    
                    recommendations.append(new_recommendation(
                        service="Entra",
                        feature=feature_name,
                        observation=f"{active_reviews} active access review(s) ({', '.join(review_types)}), {recurring_reviews} recurring - continuous governance for Copilot access",
                        recommendation="",
                        link_text="Access Review Best Practices",
                        link_url="https://learn.microsoft.com/entra/id-governance/deploy-access-reviews",
                        status="Success",
                        evidence_key="access_review_detail",
                        evidence_summary="See Access Review Detail for the active recurring reviews and review scopes already configured."
                    ))
        
        return recommendations
    
    # Non-Success: Keep original license-check recommendation unchanged
    return new_recommendation(
        service="Entra",
        feature=feature_name,
        observation=f"{feature_name} is {status} in {friendly_sku}, preventing advanced identity protection for AI services",
        recommendation=f"Enable {feature_name} to leverage Privileged Identity Management (PIM) for just-in-time Copilot admin access, implement access reviews for users with Copilot licenses, and protect against identity-based attacks targeting AI assistants with advanced risk detection.",
        link_text="Identity Governance for Copilot Admins",
        link_url="https://learn.microsoft.com/entra/id-governance/identity-governance-overview",
        priority="High",
        status=status
    )

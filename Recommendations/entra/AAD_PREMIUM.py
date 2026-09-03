"""
Microsoft Entra ID P1 - Copilot & Agent Adoption Recommendation
"""
from Core.new_recommendation import new_recommendation, NOT_ASSESSED_STATUS
from Core.friendly_names import get_friendly_sku_name
from .entra_insights import get_ca_recommendation

def get_recommendation(sku_name, status="Success", client=None, entra_insights=None):
    """
    Entra ID P1 provides advanced identity management including
    Conditional Access required for secure Copilot deployment.
    
    When entra_insights is provided, enriches observations with:
    - Conditional Access policy metrics
    - MFA enrollment and usage metrics
    - Sign-in analytics and legacy authentication detection
    """
    feature_name = "Microsoft Entra ID P1"
    friendly_sku = get_friendly_sku_name(sku_name)
    recommendations = []
    
    # Cache metrics dictionaries (empty if entra_insights not available)
    ca_metrics = {}
    mfa_metrics = {}
    signin_metrics = {}
    
    if status == "Success":
        # Base observation for license check
        observation = f"{feature_name} is active in {friendly_sku}, providing advanced identity capabilities for Copilot security"
        recommendation_text = ""
        
        # Enrich with metrics when entra_insights is available
        if entra_insights and entra_insights.get('available'):
            # Populate metrics dictionaries to avoid redundant lookups
            ca_metrics = entra_insights.get('ca_metrics', {})
            mfa_metrics = entra_insights.get('mfa_metrics', {})
            signin_metrics = entra_insights.get('signin_metrics', {})
            
            metrics = []
            
            # Conditional Access metrics - only show positive findings
            if ca_metrics.get('total_policies', 0) > 0:
                metrics.append(f"{ca_metrics['total_policies']} Conditional Access policy(ies) configured")
            
            # MFA metrics - only show positive findings
            if mfa_metrics.get('mfa_enabled_users', 0) > 0:
                metrics.append(f"{mfa_metrics['mfa_enabled_users']} user(s) enrolled in MFA")
            
            if metrics:
                observation += ". " + ", ".join(metrics)
            
            # Generate proactive recommendations based on findings
            recommendation_text = get_ca_recommendation(entra_insights)
        
        # Primary license check observation
        recommendations.append(new_recommendation(
            service="Entra",
            feature=feature_name,
            observation=observation,
            recommendation=recommendation_text,
            link_text="Identity Foundation for AI",
            link_url="https://learn.microsoft.com/entra/identity/",
            status=status
        ))
        
        # Additional observations when entra_insights is available
        if entra_insights and entra_insights.get('available'):
            # Observation 1: Conditional Access policy coverage
            total_policies = ca_metrics.get('total_policies', 0)
            require_mfa = ca_metrics.get('require_mfa', 0)
            require_compliant_device = ca_metrics.get('require_compliant_device', 0)
            block_legacy_auth = ca_metrics.get('block_legacy_auth', 0)
            
            if total_policies == 0:
                # Action Required: No CA policies
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation="No Conditional Access policies configured, leaving Copilot access unprotected",
                    recommendation="Implement Conditional Access policies to protect Copilot access. Start with requiring MFA for all users, blocking legacy authentication, and enforcing device compliance. CA policies are essential for preventing unauthorized AI usage and protecting sensitive data accessed through Copilot.",
                    link_text="Configure Conditional Access",
                    link_url="https://learn.microsoft.com/entra/identity/conditional-access/overview",
                    priority="High",
                    status="Action Required"
                ))
            elif not any((require_mfa, require_compliant_device, block_legacy_auth)):
                # The resource name is not a reliable test of protection. Evaluate the controls
                # detected in the policy inventory instead of requiring a policy named Copilot.
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation=f"{total_policies} Conditional Access policy(ies) were found, but the inventory did not detect MFA, compliant-device, or legacy-authentication controls",
                    recommendation="Review policy assignments and grant controls using report-only mode. Protect Microsoft 365 and other Entra-integrated AI resources through appropriately scoped Conditional Access policies; do not rely on an application name containing 'Copilot' as proof of coverage.",
                    link_text="Conditional Access target resources",
                    link_url="https://learn.microsoft.com/entra/identity/conditional-access/concept-conditional-access-cloud-apps",
                    priority="Medium",
                    status="Action Required"
                ))
            else:
                # Success: relevant controls were detected. Assignment effectiveness still
                # requires policy evaluation and is not inferred from a display name.
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation=f"{total_policies} Conditional Access policy(ies) found; detected controls include {require_mfa} requiring MFA, {require_compliant_device} requiring compliant devices, and {block_legacy_auth} blocking legacy authentication",
                    recommendation="",
                    link_text="Conditional Access Best Practices",
                    link_url="https://learn.microsoft.com/entra/identity/conditional-access/plan-conditional-access",
                    status="Success"
                ))
            
            # Observation 2: MFA enrollment and usage
            total_users = mfa_metrics.get('total_users', 0)
            mfa_enabled = mfa_metrics.get('mfa_enabled_users', 0)
            mfa_percentage = (mfa_enabled / total_users * 100) if total_users > 0 else 0

            if total_users == 0:
                # No denominator means the MFA registration report was not readable. Reporting
                # "0 of 0 users (0.0%)" as a finding invents a metric that was never measured.
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation="MFA enrollment could not be determined - the authentication methods registration report returned no users",
                    recommendation="Grant the assessment Reports.Read.All and UserAuthenticationMethod.Read.All, then rerun. Until MFA coverage is measured, Copilot access cannot be assumed protected against credential theft. Meanwhile, review coverage directly in Entra ID > Authentication methods > Registration.",
                    link_text="Authentication Methods Activity Report",
                    link_url="https://learn.microsoft.com/entra/identity/authentication/howto-authentication-methods-activity",
                    priority="High",
                    status=NOT_ASSESSED_STATUS
                ))
            elif mfa_percentage < 50:
                # Action Required: Low MFA adoption
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation=f"Only {mfa_enabled} of {total_users} users ({mfa_percentage:.1f}%) enrolled in MFA, exposing Copilot to credential theft",
                    recommendation="Enforce MFA registration for all users accessing Copilot. Use Conditional Access to require MFA for Microsoft 365 apps. Compromised accounts without MFA can access Copilot to exfiltrate organizational data through AI prompts. Aim for 100% MFA coverage.",
                    link_text="Configure MFA Requirements",
                    link_url="https://learn.microsoft.com/entra/identity/authentication/howto-mfa-getstarted",
                    priority="High",
                    status="Action Required"
                ))
            elif mfa_percentage < 90:
                # Action Required: Moderate MFA adoption
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation=f"{mfa_enabled} of {total_users} users ({mfa_percentage:.1f}%) enrolled in MFA, approaching secure coverage for Copilot access",
                    recommendation=f"Continue rolling out MFA to remaining {total_users - mfa_enabled} users. Use Conditional Access to require MFA for all Copilot and Microsoft 365 access. Target 100% MFA coverage to fully protect AI services from compromised credentials.",
                    link_text="MFA Deployment Guide",
                    link_url="https://learn.microsoft.com/entra/identity/authentication/howto-mfa-getstarted",
                    priority="Medium",
                    status="Action Required"
                ))
            else:
                # Success: High MFA adoption
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation=f"{mfa_enabled} of {total_users} users ({mfa_percentage:.1f}%) enrolled in MFA, strongly protecting Copilot access",
                    recommendation="",
                    link_text="MFA Best Practices",
                    link_url="https://learn.microsoft.com/entra/identity/authentication/concept-mfa-howitworks",
                    status="Success"
                ))
            
            # Observation 3: Legacy authentication detection
            legacy_auth_count = signin_metrics.get('legacy_auth_sign_ins', 0)
            
            if legacy_auth_count > 0:
                # Action Required: Legacy auth detected
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation=f"{legacy_auth_count} legacy authentication sign-in(s) detected in the past 30 days, bypassing MFA and CA protections",
                    recommendation="Block legacy authentication protocols (IMAP, POP3, SMTP AUTH) using Conditional Access. Legacy auth bypasses MFA and cannot be protected by Conditional Access policies, creating a backdoor for attackers to access Copilot. Migrate apps to modern authentication (OAuth 2.0) and block legacy protocols tenant-wide.",
                    link_text="Block Legacy Authentication",
                    link_url="https://learn.microsoft.com/entra/identity/conditional-access/block-legacy-authentication",
                    priority="High",
                    status="Action Required"
                ))
            else:
                # Success: No legacy auth
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation="No legacy authentication sign-ins detected, all access uses modern authentication with full security controls",
                    recommendation="",
                    link_text="Modern Authentication Overview",
                    link_url="https://learn.microsoft.com/microsoft-365/enterprise/hybrid-modern-auth-overview",
                    status="Success"
                ))
            
            # Observation 4: Passwordless authentication adoption
            auth_metrics = entra_insights.get('auth_summary', {})
            if auth_metrics:
                # Get method breakdown from nested structure
                methods = auth_metrics.get('methods', {})
                fido2_users = methods.get('fido2', 0)
                windows_hello_users = methods.get('windowsHello', 0)
                authenticator_users = methods.get('microsoftAuthenticator', 0)
                total_passwordless = fido2_users + windows_hello_users + authenticator_users
                passwordless_rate = auth_metrics.get('passwordless_adoption_rate', 0)
                
                # Build method breakdown text
                method_details = []
                if fido2_users > 0:
                    method_details.append(f"{fido2_users} FIDO2")
                if windows_hello_users > 0:
                    method_details.append(f"{windows_hello_users} Windows Hello")
                if authenticator_users > 0:
                    method_details.append(f"{authenticator_users} Authenticator app")
                method_text = f" ({', '.join(method_details)})" if method_details else ""
                
                if passwordless_rate < 10:
                    # Valuable identity hardening, but not a standalone AI deployment gate.
                    recommendations.append(new_recommendation(
                        service="Entra",
                        feature=feature_name,
                        observation=f"{passwordless_rate:.1f}% of users ({total_passwordless}{method_text}) use passwordless authentication",
                        recommendation="Plan phishing-resistant authentication for administrators and high-risk users first, then expand based on risk and user readiness. Evaluate effective MFA and Conditional Access separately.",
                        link_text="Deploy Passwordless Authentication",
                        link_url="https://learn.microsoft.com/entra/identity/authentication/concept-authentication-passwordless",
                        priority="Medium",
                        status="Insight",
                        disposition="Opportunity"
                    ))
                elif passwordless_rate < 50:
                    # Optional hardening opportunity, not a standalone AI readiness gate.
                    recommendations.append(new_recommendation(
                        service="Entra",
                        feature=feature_name,
                        observation=f"{passwordless_rate:.1f}% of users ({total_passwordless}{method_text}) use passwordless authentication, making progress toward phishing-resistant Copilot access",
                        recommendation="Continue risk-based rollout of phishing-resistant authentication. Prioritize administrators and users with access to sensitive data; use Temporary Access Pass for onboarding where appropriate.",
                        link_text="Passwordless Deployment Guide",
                        link_url="https://learn.microsoft.com/entra/identity/authentication/howto-authentication-passwordless-deployment",
                        priority="Medium",
                        status="Insight",
                        disposition="Opportunity"
                    ))
                else:
                    # Success: High passwordless adoption
                    recommendations.append(new_recommendation(
                        service="Entra",
                        feature=feature_name,
                        observation=f"{passwordless_rate:.1f}% of users ({total_passwordless}{method_text}) use passwordless authentication, providing phishing-resistant protection for Copilot",
                        recommendation="",
                        link_text="Passwordless Best Practices",
                        link_url="https://learn.microsoft.com/entra/identity/authentication/concept-authentication-passwordless",
                        status="Success"
                    ))
            
            # Observation 5: Group-based licensing for Copilot
            group_metrics = entra_insights.get('group_licensing_summary', {})
            if group_metrics:
                total_license_groups = group_metrics.get('total_groups_with_licenses', 0)
                copilot_groups = group_metrics.get('copilot_license_groups', 0)
                dynamic_groups = group_metrics.get('dynamic_groups', 0)
                groups_with_errors = group_metrics.get('groups_with_errors', 0)
                
                if copilot_groups == 0 and total_license_groups == 0:
                    # Operational optimization, not a security or AI prerequisite.
                    recommendations.append(new_recommendation(
                        service="Entra",
                        feature=feature_name,
                        observation="No group-based license assignments were returned; individual assignment may be in use",
                        recommendation="Consider group-based licensing if it improves joiner, mover, leaver administration and license reclamation. This is an operational choice, not proof of weak access control.",
                        link_text="Group-Based Licensing",
                        link_url="https://learn.microsoft.com/entra/identity/users/licensing-groups-assign",
                        priority="Medium",
                        status="Insight",
                        disposition="Opportunity"
                    ))
                elif copilot_groups == 0:
                    # Operational optimization, not a security or AI prerequisite.
                    recommendations.append(new_recommendation(
                        service="Entra",
                        feature=feature_name,
                        observation=f"{total_license_groups} group(s) use license assignment, but none configured for Copilot licenses",
                        recommendation=f"Consider using the existing {dynamic_groups} dynamic group(s) to automate Copilot license lifecycle where membership rules match approved use cases.",
                        link_text="Assign Copilot Licenses to Groups",
                        link_url="https://learn.microsoft.com/entra/identity/users/licensing-groups-assign",
                        priority="Medium",
                        status="Insight",
                        disposition="Opportunity"
                    ))
                elif groups_with_errors > 0:
                    # Action Required: License assignment errors
                    recommendations.append(new_recommendation(
                        service="Entra",
                        feature=feature_name,
                        observation=f"{copilot_groups} group(s) configured for Copilot licensing, but {groups_with_errors} group(s) have license assignment errors",
                        recommendation=f"Resolve license assignment errors in {groups_with_errors} group(s) to ensure users receive Copilot access. Common errors: 1) Insufficient licenses available (purchase more or remove inactive users), 2) Conflicting service plans (resolve by disabling conflicting features), 3) Duplicate assignments (user in multiple groups with same license), 4) Usage location not set (required for license assignment). Review errors in Azure AD > Groups > Licenses tab and remediate. Ensure all Copilot users have usage location configured and sufficient licenses are available.",
                        link_text="Troubleshoot Group Licensing",
                        link_url="https://learn.microsoft.com/entra/identity/users/licensing-groups-resolve-problems",
                        priority="High",
                        status="Action Required"
                    ))
                else:
                    # Success: Group-based licensing active
                    recommendations.append(new_recommendation(
                        service="Entra",
                        feature=feature_name,
                        observation=f"{copilot_groups} group(s) manage Copilot license assignment ({dynamic_groups} dynamic), automating access governance",
                        recommendation="",
                        link_text="Group-Based Licensing Best Practices",
                        link_url="https://learn.microsoft.com/entra/identity/users/licensing-groups-assign",
                        status="Success"
                    ))
        
        return recommendations
    
    # Non-Success: Keep original license-check recommendation unchanged
    return new_recommendation(
        service="Entra",
        feature=feature_name,
        observation=f"{feature_name} is {status} in {friendly_sku}, missing critical identity features for AI governance",
        recommendation=f"Enable {feature_name} to access Conditional Access, group-based licensing, self-service password reset, and advanced identity protection required for Copilot governance. P1 enables context-aware policies that restrict Copilot based on user risk, device compliance, and location - essential for securing AI access. Use dynamic groups to automate Copilot license assignment, simplify access management, and ensure only authorized users can leverage AI capabilities. P1 is the minimum identity tier recommended for enterprise Copilot deployments.",
        link_text="Identity Foundation for AI",
        link_url="https://learn.microsoft.com/entra/identity/",
        priority="High",
        status=status
    )


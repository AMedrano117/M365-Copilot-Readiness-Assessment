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
    print(f"[DEBUG P1] FUNCTION CALLED - sku_name={sku_name}, status={status}, entra_insights={entra_insights is not None}")
    
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
        
        # DEBUG: Check if entra_insights is passed
        print(f"[DEBUG P1] entra_insights passed: {entra_insights is not None}")
        if entra_insights:
            print(f"[DEBUG P1] entra_insights.available: {entra_insights.get('available')}")
            print(f"[DEBUG P1] ca_metrics keys: {list(entra_insights.get('ca_metrics', {}).keys())}")
            print(f"[DEBUG P1] mfa_metrics keys: {list(entra_insights.get('mfa_metrics', {}).keys())}")
        
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
            print(f"[DEBUG P1] Entering additional observations section")
            print(f"[DEBUG P1] ca_metrics total_policies: {ca_metrics.get('total_policies', 0)}")
            print(f"[DEBUG P1] mfa_metrics total_users: {mfa_metrics.get('total_users', 0)}")
            print(f"[DEBUG P1] signin_metrics legacy_auth: {signin_metrics.get('legacy_auth_sign_ins', 0)}")
            
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
            print(f"[DEBUG P1] Passwordless check - entra_insights available: {entra_insights is not None}")
            auth_metrics = entra_insights.get('auth_summary', {})
            print(f"[DEBUG P1] auth_summary extracted: {auth_metrics}")
            passwordless_rate = auth_metrics.get('passwordless_adoption_rate', 0)
            fido2_users = auth_metrics.get('methods', {}).get('fido2', 0)
            windows_hello_users = auth_metrics.get('methods', {}).get('windowsHello', 0)
            authenticator_users = auth_metrics.get('methods', {}).get('microsoftAuthenticator', 0)
            total_passwordless = fido2_users + windows_hello_users + authenticator_users
            print(f"[DEBUG P1] Passwordless rate: {passwordless_rate}%, Total users: {total_passwordless} (FIDO2: {fido2_users}, Hello: {windows_hello_users}, Auth: {authenticator_users})")
            
            if passwordless_rate < 10:
                # Valuable identity hardening, but not a standalone AI deployment gate.
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation=f"{passwordless_rate:.1f}% of users use passwordless authentication ({total_passwordless} users with FIDO2/Windows Hello/Authenticator)",
                    recommendation="Plan phishing-resistant authentication for administrators and high-risk users first, then expand based on risk and user readiness. Evaluate effective MFA and Conditional Access separately.",
                    link_text="Deploy Passwordless Authentication",
                    link_url="https://learn.microsoft.com/entra/identity/authentication/howto-authentication-passwordless-deployment",
                    priority="Medium",
                    status="Insight",
                    disposition="Opportunity"
                ))
            elif passwordless_rate < 50:
                # Optional hardening opportunity, not a standalone AI readiness gate.
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation=f"{passwordless_rate:.1f}% of users use passwordless authentication ({total_passwordless} users: {fido2_users} FIDO2, {windows_hello_users} Windows Hello, {authenticator_users} Authenticator), progressing toward secure Copilot access",
                    recommendation="Continue risk-based rollout of phishing-resistant authentication. Prioritize administrators and users with access to sensitive data; measure enrollment and sign-in success before expanding.",
                    link_text="Passwordless Authentication Methods",
                    link_url="https://learn.microsoft.com/entra/identity/authentication/concept-authentication-passwordless",
                    priority="Medium",
                    status="Insight",
                    disposition="Opportunity"
                ))
            else:
                # Success: High passwordless adoption
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation=f"{passwordless_rate:.1f}% of users use passwordless authentication ({total_passwordless} users: {fido2_users} FIDO2, {windows_hello_users} Windows Hello, {authenticator_users} Authenticator), strongly securing Copilot access",
                    recommendation="",
                    link_text="Passwordless Best Practices",
                    link_url="https://learn.microsoft.com/entra/identity/authentication/concept-authentication-passwordless",
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


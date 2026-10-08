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
                policy_count = ca_metrics['total_policies']
                policy_label = "policy" if policy_count == 1 else "policies"
                metrics.append(f"{policy_count} Conditional Access {policy_label} configured")
            
            # MFA metrics - only show positive findings
            if mfa_metrics.get('mfa_enabled_users', 0) > 0:
                enrolled_count = mfa_metrics['mfa_enabled_users']
                user_label = "user" if enrolled_count == 1 else "users"
                metrics.append(f"{enrolled_count} {user_label} enrolled in MFA")
            
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
            policy_label = "policy" if total_policies == 1 else "policies"
            policy_verb = "was" if total_policies == 1 else "were"
            require_mfa = ca_metrics.get('require_mfa', 0)
            require_compliant_device = ca_metrics.get('require_compliant_device', 0)
            block_legacy_auth = ca_metrics.get('block_legacy_auth', 0)
            verified_controls = []
            if require_mfa:
                verified_controls.append(f"{require_mfa} requiring MFA")
            if require_compliant_device:
                verified_controls.append(f"{require_compliant_device} requiring compliant devices")
            if block_legacy_auth:
                verified_controls.append(f"{block_legacy_auth} blocking legacy authentication")
            if not verified_controls:
                verified_control_text = ""
            elif len(verified_controls) == 1:
                verified_control_text = verified_controls[0]
            elif len(verified_controls) == 2:
                verified_control_text = " and ".join(verified_controls)
            else:
                verified_control_text = ", ".join(verified_controls[:-1]) + f", and {verified_controls[-1]}"
            
            if total_policies == 0:
                # Action Required: No CA policies
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature=feature_name,
                    observation="No Conditional Access policies were returned by this inventory; effective authentication protection requires separate confirmation",
                    recommendation="Review Security Defaults, Conditional Access scope, authentication strengths and documented external-provider requirements. Validate the intended population and observed sign-in results before changing enforcement.",
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
                    observation=f"{total_policies} Conditional Access {policy_label} {policy_verb} found, but the inventory did not detect MFA, compliant-device, or legacy-authentication controls",
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
                    observation=f"{total_policies} Conditional Access {policy_label} found; verified controls include {verified_control_text}",
                    recommendation="",
                    link_text="Conditional Access Best Practices",
                    link_url="https://learn.microsoft.com/entra/identity/conditional-access/plan-conditional-access",
                    status="Success"
                ))
            
            # Registration uses reconciled known flags, never the complement of an aggregate.
            from Core.authentication_findings import registration_recommendation
            recommendations.append(registration_recommendation(mfa_metrics, feature_name))

            # Observation 3: Legacy authentication detection
            legacy_auth_count = signin_metrics.get('legacy_auth_sign_ins', 0)
            
            if legacy_auth_count > 0:
                # Action Required: Legacy auth detected
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature="Legacy authentication sign-ins",
                    observation=f"{legacy_auth_count} legacy authentication {'sign-in' if legacy_auth_count == 1 else 'sign-ins'} detected in the returned sign-in records. Review the protocols and the controls that apply to those requests.",
                    recommendation="Review the linked sign-in records to identify the accounts, applications, clients and IP addresses involved. Distinguish successful requests from failed or blocked attempts using the error code and Conditional Access result. Confirm business dependencies, migrate required clients to modern authentication, and test a policy to block legacy authentication before enforcement.",
                    link_text="Block Legacy Authentication",
                    link_url="https://learn.microsoft.com/entra/identity/conditional-access/block-legacy-authentication",
                    priority="High",
                    status="Action Required",
                    finding_key="entra.signins.legacy_auth",
                    evidence_key="legacy_signin_detail"
                ))
            else:
                # Success: No legacy auth
                recommendations.append(new_recommendation(
                    service="Entra",
                    feature="Legacy authentication sign-ins",
                    observation="No legacy authentication sign-ins were found in the returned sign-in records. This sample does not establish that all access uses modern authentication or that every security control is effective.",
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
                authenticator_users = methods.get('microsoftAuthenticatorPasswordless', 0)
                total_passwordless = auth_metrics.get('passwordless_enabled', 0)
                passwordless_rate = auth_metrics.get('passwordless_adoption_rate', 0)
                
                # Build method breakdown text
                method_details = []
                if fido2_users > 0:
                    method_details.append(f"{fido2_users} FIDO2")
                if windows_hello_users > 0:
                    method_details.append(f"{windows_hello_users} Windows Hello")
                if authenticator_users > 0:
                    method_details.append(f"{authenticator_users} Authenticator passwordless phone sign-in")
                method_text = f" ({', '.join(method_details)})" if method_details else ""
                
                if isinstance(passwordless_rate, (int, float)) and passwordless_rate < 10:
                    # Valuable identity hardening, but not a standalone AI deployment gate.
                    recommendations.append(new_recommendation(
                        service="Entra",
                        feature=feature_name,
                        observation=f"{passwordless_rate:.1f}% of returned users ({total_passwordless}{method_text}) have a passwordless method registered. Method counts can overlap; registration does not establish actual use.",
                        recommendation="Plan phishing-resistant authentication for administrators and high-risk users first, then expand based on risk and user readiness. Evaluate effective MFA and Conditional Access separately.",
                        link_text="Deploy Passwordless Authentication",
                        link_url="https://learn.microsoft.com/entra/identity/authentication/concept-authentication-passwordless",
                        priority="Medium",
                        status="Insight",
                        disposition="Opportunity"
                    ))
                elif isinstance(passwordless_rate, (int, float)) and passwordless_rate < 50:
                    # Optional hardening opportunity, not a standalone AI readiness gate.
                    recommendations.append(new_recommendation(
                        service="Entra",
                        feature=feature_name,
                        observation=f"{passwordless_rate:.1f}% of returned users ({total_passwordless}{method_text}) have a passwordless method registered. Method counts can overlap; registration does not establish actual use or enforced phishing resistance.",
                        recommendation="Continue risk-based rollout of phishing-resistant authentication. Prioritize administrators and users with access to sensitive data; use Temporary Access Pass for onboarding where appropriate.",
                        link_text="Passwordless Deployment Guide",
                        link_url="https://learn.microsoft.com/entra/identity/authentication/howto-authentication-passwordless-deployment",
                        priority="Medium",
                        status="Insight",
                        disposition="Opportunity"
                    ))
                elif isinstance(passwordless_rate, (int, float)):
                    # High registration is context, not observed use.
                    recommendations.append(new_recommendation(
                        service="Entra",
                        feature=feature_name,
                        observation=f"{passwordless_rate:.1f}% of returned users ({total_passwordless}{method_text}) have a passwordless method registered. Method counts can overlap; registration does not establish actual use or enforced phishing resistance.",
                        recommendation="",
                        link_text="Passwordless Best Practices",
                        link_url="https://learn.microsoft.com/entra/identity/authentication/concept-authentication-passwordless",
                        status="Insight",
                        disposition="Reference"
                    ))
            
            # Observation 5: Group-based licensing for Copilot
            group_metrics = entra_insights.get('group_licensing_summary', {})
            if not (entra_insights.get('data_sources', {}) or {}).get('groups', False):
                reason = (entra_insights.get('collection_status', {}) or {}).get('groups', {}).get('reason', '')
                recommendations.append(new_recommendation(
                    service="Entra", feature=feature_name,
                    observation="Group-based licensing is not assessed. " + (reason or "The licensed-group inventory was not collected completely."),
                    recommendation="Review group-based license assignments using customer-provided evidence before drawing conclusions about the assignment model.",
                    status=NOT_ASSESSED_STATUS, disposition="Coverage",
                    finding_key="entra.group_licensing.not_assessed",
                ))
            elif group_metrics:
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
        
        if client is not None:
            from Core.authentication_findings import qualify_legacy_finding
            from Core.signin_evidence import is_legacy_signin_finding
            recommendations = [qualify_legacy_finding(row, client) if is_legacy_signin_finding(row)
                or 'No legacy authentication sign-ins' in row.get('Observation', '') else row for row in recommendations]
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


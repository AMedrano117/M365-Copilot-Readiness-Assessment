"""
Microsoft Entra ID P2 - Copilot & Agent Adoption Recommendation

The license row describes service-plan availability only. Technical identity
conclusions (privileged schedules, identity risk, guests, consent, application
grants and access reviews) come from governance_recommendations(), which the
shared identity producer evaluates for every tenant from retained evidence.
"""
from Core.new_recommendation import new_recommendation, NOT_ASSESSED_STATUS
from Core.friendly_names import get_friendly_sku_name
from Core.source_evidence import envelope_availability, envelope_complete
from .entra_insights import build_pim_metrics, entra_source_was_read

# Built-in policies Microsoft maintains for end-user consent. Their conditions
# limit which publishers and permissions a user can approve.
MICROSOFT_CONSENT_POLICIES = {
    'microsoft-user-default-recommended': 'Microsoft-managed recommended policy',
    'microsoft-user-default-allow-consent-apps': 'Microsoft-managed mail-client policy',
    'microsoft-user-default-low': 'built-in low-impact verified-publisher policy',
}
ALLOW_ALL_CONSENT_POLICY = 'microsoft-user-default-legacy'
TOPICS = ('privileged', 'risk', 'guests', 'guest_invitations', 'cross_tenant', 'consent', 'grants', 'access_reviews')


def get_recommendation(sku_name, status="Success", client=None, entra_insights=None):
    """
    Microsoft Entra ID P2 provides identity governance and privileged identity management
    critical for managing Copilot admin roles and protecting AI workloads.
    """
    feature_name = "Microsoft Entra ID P2"
    friendly_sku = get_friendly_sku_name(sku_name)
    recommendations = []

    if status == "Success":
        observation = f"{feature_name} is active in {friendly_sku}, providing identity governance for Copilot administration"
        recommendation_text = ""
        if entra_insights and entra_insights.get('available'):
            pim_metrics = entra_insights.get('pim_metrics', {})
            access_review_metrics = entra_insights.get('access_review_metrics', {})
            metrics = []
            if pim_metrics.get('eligible_admins_count', 0) > 0:
                metrics.append(f"{pim_metrics['eligible_admins_count']} eligible admin(s) configured with PIM")
            if access_review_metrics.get('total_active_reviews', 0) > 0:
                metrics.append(f"{access_review_metrics['total_active_reviews']} active access review(s)")
            if metrics:
                observation += ". " + ", ".join(metrics)
            _, recommendation_text = build_pim_metrics(entra_insights)

        recommendations.append(new_recommendation(
            service="Entra",
            feature=feature_name,
            observation=observation,
            recommendation=recommendation_text,
            link_text="Identity Governance for Copilot Admins",
            link_url="https://learn.microsoft.com/entra/id-governance/identity-governance-overview",
            status=status
        ))
        if entra_insights and entra_insights.get('available'):
            recommendations.extend(governance_recommendations(entra_insights, feature_name))
        return recommendations

    # Non-Success: license-check context only
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


def _features(features):
    if isinstance(features, dict):
        return {topic: features.get(topic) or features.get('default') or 'Microsoft Entra ID P2' for topic in TOPICS}
    return {topic: features or 'Microsoft Entra ID P2' for topic in TOPICS}


def source_state(entra_insights, name):
    """Return (complete, availability, note) for one retained Entra source."""
    states = entra_insights.get('collection_status') or {}
    state = states.get(name)
    if isinstance(state, dict) and state:
        availability = envelope_availability(state)
        code = state.get('status_code')
        note = availability.replace('_', ' ') + (f' (HTTP {code})' if code else '')
        return envelope_complete(state), availability, note
    # Older saves recorded only a per-dataset read flag.
    read = bool((entra_insights.get('data_sources') or {}).get(name))
    return read, 'available' if read else 'not_retained', 'available' if read else 'not retained'


def _structured(row, **fields):
    row.update({key: value for key, value in fields.items() if value is not None})
    return row


def governance_recommendations(entra_insights, features=None):
    """Technical identity governance rows; the license inventory is not an input."""
    feature = _features(features)
    recommendations = []
    pim_metrics = entra_insights.get('pim_metrics', {})
    risk_metrics = entra_insights.get('risk_metrics', {})

    # Observation 1: PIM configuration status
    permanent_count = pim_metrics.get('permanent_admins_count', 0)
    permanent_global_admins = pim_metrics.get('permanent_global_admins', 0)
    if permanent_count > 0:
        schedule_label = "schedule" if permanent_count == 1 else "schedules"
        admin_label = "assignment" if permanent_global_admins == 1 else "assignments"
        recommendations.append(new_recommendation(
            service="Entra",
            feature=feature['privileged'],
            observation=(
                f"{permanent_count} active directory role assignment {schedule_label} have no expiration"
                + (f", including {permanent_global_admins} Global Administrator {admin_label}" if permanent_global_admins else "")
            ),
            recommendation="Review the named principals, scopes, business need, account purpose and documented exceptions in Admin Role Detail. Agree whether to retain, reduce, time-bound or remove access; confirm PIM applicability and service dependencies before selecting treatment.",
            link_text="Configure PIM for Copilot Admins",
            link_url="https://learn.microsoft.com/entra/id-governance/privileged-identity-management/pim-configure",
            priority="High" if permanent_global_admins else "Medium",
            status="Action Required",
            finding_key="entra.pim.permanent_schedules",
            evidence_key="admin_role_detail",
            evidence_summary="See Admin Role Detail for the privileged assignments that support this PIM follow-up."
        ))
    elif not entra_source_was_read(entra_insights, 'role_assignment_schedules'):
        recommendations.append(new_recommendation(
            service="Entra",
            feature=feature['privileged'],
            observation="Privileged role assignment schedules could not be read, so standing-versus-time-bound administrative access is unverified",
            recommendation="Grant RoleManagement.Read.Directory and rerun, or review active and eligible assignments in Privileged Identity Management before broad AI rollout.",
            link_text="Review PIM Assignments",
            link_url="https://learn.microsoft.com/entra/id-governance/privileged-identity-management/pim-how-to-add-role-to-user",
            priority="Medium",
            status="Not Assessed",
            disposition="Coverage",
            finding_key="entra.pim.schedules_not_assessed",
            evidence_key="admin_role_detail",
            evidence_summary="Active role assignments were not treated as permanent without schedule-expiration evidence."
        ))
    else:
        recommendations.append(new_recommendation(
            service="Entra",
            feature=feature['privileged'],
            observation="No no-expiration active role assignment schedules were returned by the assessed PIM endpoint",
            recommendation="",
            link_text="PIM Best Practices",
            link_url="https://learn.microsoft.com/entra/id-governance/privileged-identity-management/pim-configure",
            status="Success",
            finding_key="entra.pim.permanent_schedules",
            evidence_key="admin_role_detail",
            evidence_summary="See Admin Role Detail for the eligible and scheduled assignments currently present in the tenant."
        ))

    # Observation 2: Identity Protection risk status
    high_risk = risk_metrics.get('high_risk_users', 0)
    medium_risk = risk_metrics.get('medium_risk_users', 0)
    if high_risk > 0 or medium_risk > 0:
        total_risky = high_risk + medium_risk
        risky_user_label = "risky user" if total_risky == 1 else "risky users"
        recommendations.append(new_recommendation(
            service="Entra",
            feature=feature['risk'],
            observation=f"{total_risky} {risky_user_label} detected in the tenant ({high_risk} high-risk, {medium_risk} medium-risk)",
            recommendation="Review the risky users and confirm remediation or dismissal in Identity Protection. Risky accounts can expose Microsoft 365 data and any connected AI services they are authorized to use. Use risk-based Conditional Access where appropriate to block or remediate risky sign-ins.",
            link_text="Investigate Risky Users",
            link_url="https://learn.microsoft.com/entra/id-protection/howto-identity-protection-investigate-risk",
            priority="High" if high_risk > 0 else "Medium",
            status="Action Required",
            finding_key="entra.identity_risk.users"
        ))
    elif not entra_source_was_read(entra_insights, 'risky_users', 'risk_detections'):
        # Do not report unread risk data as clean.
        source_states = entra_insights.get('collection_status', {}) or {}
        source_reasons = [str((source_states.get(key, {}) or {}).get('reason', '') or '') for key in ('risky_users', 'risk_detections')]
        p2_limited = any('Entra ID P2' in reason for reason in source_reasons)
        recommendations.append(new_recommendation(
            service="Entra",
            feature=feature['risk'],
            observation=(
                "Identity Protection risk data could not be retrieved because the tenant does not provide full risk reporting; account risk is unverified"
                if p2_limited else
                "Identity Protection risk data could not be retrieved, so account risk is unverified"
            ),
            recommendation=(
                "Microsoft Entra ID P1 provides limited risk information. Full risky-user and risk-detection assessment requires Entra ID P2 or another qualifying Entra entitlement. Treat this as a licensing coverage limit; no additional Graph permission is needed when the two IdentityRisk permissions already have admin consent."
                if p2_limited else
                "Grant the application permissions IdentityRiskyUser.Read.All and IdentityRiskEvent.Read.All with admin consent, then rerun. Meanwhile review Entra ID Protection > Risky users directly."
            ),
            link_text="Identity Protection Risk Reports",
            link_url="https://learn.microsoft.com/entra/id-protection/howto-identity-protection-investigate-risk",
            priority="Medium",
            status=NOT_ASSESSED_STATUS,
            finding_key="entra.identity_risk.not_assessed"
        ))
    else:
        recommendations.append(new_recommendation(
            service="Entra",
            feature=feature['risk'],
            observation="No risky users detected, Identity Protection is actively monitoring and protecting accounts",
            recommendation="",
            link_text="Identity Protection Overview",
            link_url="https://learn.microsoft.com/entra/id-protection/overview-identity-protection",
            status="Success",
            finding_key="entra.identity_risk.users"
        ))

    recommendations.extend(_guest_rows(entra_insights, feature))
    recommendations.extend(_consent_rows(entra_insights, feature))
    recommendations.extend(_access_review_rows(entra_insights, feature))
    return recommendations


def _guest_rows(entra_insights, feature):
    rows = []
    b2b_metrics = entra_insights.get('b2b_summary', {})
    total_guests = b2b_metrics.get('total_guests', 0)
    guests_with_licenses = b2b_metrics.get('guests_with_licenses', 0)
    guests_read, _, _ = source_state(entra_insights, 'guests')
    if guests_read and guests_with_licenses > 0:
        # An M365 license does not prove Copilot entitlement or inappropriate content
        # access. Surface this for scoped review without declaring a security failure.
        rows.append(_structured(new_recommendation(
            service="Entra",
            feature=feature['guests'],
            observation=f"{guests_with_licenses} of {total_guests} guest users have an M365 license; this does not by itself prove Copilot entitlement or inappropriate data access",
            recommendation=f"Verify that the {guests_with_licenses} licensed guest account(s) still need their assigned products and content permissions. Review actual site, team, and group access separately.",
            link_text="Manage Guest Access",
            link_url="https://learn.microsoft.com/entra/external-id/what-is-b2b",
            priority="Medium",
            status="Insight",
            disposition="Opportunity",
            finding_key="entra.guests.licensed",
            evidence_key="guest_access_detail",
            evidence_summary="See Guest Access Detail for the guest objects, licensing state, and external collaboration settings that support this finding."
        ), EvidenceSource='guests', DomainId='identity', SemanticCondition='guests=LicensedGuestsPresent',
            EvidenceScope='Guest accounts returned by the complete guest inventory'))
    elif guests_read and total_guests > 10:
        rows.append(_structured(new_recommendation(
            service="Entra",
            feature=feature['guests'],
            observation=f"{total_guests} guest users in tenant, none with M365 licenses - external collaboration isolated from Copilot access",
            recommendation="",
            link_text="External Identities Overview",
            link_url="https://learn.microsoft.com/entra/external-id/",
            status="Success",
            finding_key="entra.guests.licensed",
            evidence_key="guest_access_detail",
            evidence_summary="See Guest Access Detail for the guest inventory and current licensing state reviewed for this observation."
        ), EvidenceSource='guests', DomainId='identity', SemanticCondition='guests=NoLicensedGuests',
            EvidenceScope='Guest accounts returned by the complete guest inventory'))

    # Invitation eligibility is read from the tenant authorization policy.
    policy_read, _, _ = source_state(entra_insights, 'authorization_policy')
    guest_invite_setting = b2b_metrics.get('guest_invite_restrictions', 'Unknown')
    if not isinstance(guest_invite_setting, str):
        guest_invite_setting = str(guest_invite_setting)
    if not policy_read or guest_invite_setting == 'Unknown':
        pass  # No invitation conclusion without a completed authorization-policy read.
    elif 'admins' not in guest_invite_setting.lower() and 'limited' not in guest_invite_setting.lower() and guest_invite_setting.lower() != 'none':
        rows.append(_structured(new_recommendation(
            service="Entra",
            feature=feature['guest_invitations'],
            observation=f"Guest invitation eligibility is configured as '{guest_invite_setting}'; invited guests still require explicit resource access",
            recommendation="Confirm that the invitation model matches collaboration policy. If tighter sponsorship is required, limit invitation rights or add approval; validate actual SharePoint, Teams, and group permissions independently.",
            link_text="Configure External Collaboration",
            link_url="https://learn.microsoft.com/entra/external-id/external-collaboration-settings-configure",
            priority="Medium",
            status="Insight",
            disposition="Opportunity",
            finding_key="entra.guests.invitation",
            evidence_key="guest_access_detail",
            evidence_summary="See Guest Access Detail for the invitation restrictions and guest inventory associated with this external collaboration setting."
        ), EvidenceSource='authorization_policy', DomainId='identity',
            SemanticCondition=f'guest_invitations={guest_invite_setting}', EvidenceScope='Tenant authorization policy'))
    else:
        rows.append(_structured(new_recommendation(
            service="Entra",
            feature=feature['guest_invitations'],
            observation=f"Guest user invitations restricted to '{guest_invite_setting}', controlling external access to Copilot-searchable content",
            recommendation="",
            link_text="External Collaboration Settings",
            link_url="https://learn.microsoft.com/entra/external-id/external-collaboration-settings-configure",
            status="Success",
            finding_key="entra.guests.invitation",
            evidence_key="guest_access_detail",
            evidence_summary="See Guest Access Detail for the external collaboration settings and guest object inventory reviewed for this control."
        ), EvidenceSource='authorization_policy', DomainId='identity',
            SemanticCondition=f'guest_invitations={guest_invite_setting}', EvidenceScope='Tenant authorization policy'))

    # Cross-tenant access settings
    cross_read, _, cross_note = source_state(entra_insights, 'cross_tenant_policy')
    cross_tenant_configured = b2b_metrics.get('cross_tenant_access_configured', False)
    partner_count = b2b_metrics.get('partner_configurations', 0)
    if not cross_read:
        observation = (f"Cross-tenant access settings were not established: the cross-tenant policy read was {cross_note}. "
                       "No conclusion about default or partner-specific settings is drawn.")
        semantic = 'cross_tenant=SourceUnavailable'
    elif not cross_tenant_configured:
        observation = "No named cross-tenant partner configuration was returned; Entra default inbound and outbound settings apply"
        semantic = 'cross_tenant=DefaultSettings'
    elif partner_count == 0:
        observation = "Cross-tenant access uses the configured default settings; no partner-specific overrides were returned"
        semantic = 'cross_tenant=DefaultSettings'
    else:
        observation = f"Cross-tenant access policies configured with {partner_count} partner organization(s), governing external access to Copilot content"
        semantic = 'cross_tenant=PartnerSettings'
    rows.append(_structured(new_recommendation(
        service="Entra",
        feature=feature['cross_tenant'],
        observation=observation,
        recommendation="",
        link_text="Cross-Tenant Access Settings",
        link_url="https://learn.microsoft.com/entra/external-id/cross-tenant-access-overview",
        status="Success" if cross_read else "Not Assessed",
        disposition="Reference",
        finding_key="entra.guests.cross_tenant",
        evidence_key="guest_access_detail",
        evidence_summary="See Guest Access Detail for the cross-tenant configuration state and guest collaboration context behind this finding."
    ), EvidenceSource='cross_tenant_policy', DomainId='identity', SemanticCondition=semantic,
        EvidenceScope='Default cross-tenant access policy and partner configurations'))
    return rows


def _consent_rows(entra_insights, feature):
    rows = []
    consent_metrics = entra_insights.get('consent_summary', {})
    policy_read, _, policy_note = source_state(entra_insights, 'authorization_policy')
    policy_read = policy_read and bool(consent_metrics.get('consent_configuration_available', False))
    assigned = [str(value) for value in consent_metrics.get('assigned_user_consent_policies') or []]
    policy_ids = sorted({value.split('.', 1)[1] if '.' in value else value for value in assigned})
    common = dict(EvidenceSource='authorization_policy', ControlId='APPS.CONSENT', DomainId='applications',
                  EvidenceScope='Default user role permission-grant policy assignments', FindingCondition='consent.policy')
    closure = 'A dated authorization-policy export or admin-center record showing the assigned default-user consent policies and the reviewed approval boundary.'

    if not policy_read:
        rows.append(_structured(new_recommendation(
            service="Entra",
            feature=feature['consent'],
            observation=(f"The default-user consent boundary is not established: the authorization policy read was {policy_note} "
                         "and its consent policy assignments were not retained."),
            recommendation="Read the tenant authorization policy (Microsoft Graph Policy.Read.All) and rebuild, or record the default-user consent setting from the Microsoft Entra admin center with its date.",
            link_text="Configure User Consent",
            link_url="https://learn.microsoft.com/entra/identity/enterprise-apps/configure-user-consent",
            priority="Medium",
            status="Not Assessed",
            disposition="Coverage",
            finding_key="entra.apps.user_consent_assignment",
            evidence_key="app_consent_policy_detail",
            evidence_summary="The authorization-policy consent assignments were unavailable, so no consent-boundary conclusion is supported."
        ), SemanticCondition='consent=SourceUnavailable', ReadinessTreatment='EvidenceGap', CompletionEvidence=closure, **common))
    elif not policy_ids:
        rows.append(_structured(new_recommendation(
            service="Entra",
            feature=feature['consent'],
            observation="No self-consent policy is assigned to the default user role; users cannot grant application consent themselves and requests need an authorized administrator.",
            recommendation="",
            link_text="Admin Consent Workflow",
            link_url="https://learn.microsoft.com/entra/identity/enterprise-apps/configure-admin-consent-workflow",
            status="Success",
            finding_key="entra.apps.user_consent_assignment",
            evidence_key="app_consent_policy_detail",
            evidence_summary="See Application Consent Policy for the effective default-user consent assignments returned by the tenant authorization policy."
        ), SemanticCondition='consent=AdminOnly', ReadinessTreatment='Pass', **common))
    elif ALLOW_ALL_CONSENT_POLICY in policy_ids:
        rows.append(_structured(new_recommendation(
            service="Entra",
            feature=feature['consent'],
            observation=(f"The default user role is assigned the {ALLOW_ALL_CONSENT_POLICY} consent policy, which lets users consent to any "
                         "user-consentable permission for any application without administrator review."),
            recommendation="Replace the allow-all policy with a Microsoft-managed or custom consent policy that limits user consent to verified publishers and low-impact permissions, require administrator review for higher-impact permissions, and operate an admin-consent workflow. Review existing grants and recent activity before revoking access.",
            link_text="Configure User Consent",
            link_url="https://learn.microsoft.com/entra/identity/enterprise-apps/configure-user-consent",
            priority="High",
            status="Action Required",
            finding_key="entra.apps.user_consent_assignment",
            evidence_key="app_consent_policy_detail; app_access_detail",
            evidence_summary="See Application Consent Policy for the effective default-user consent assignments and App Access Detail for existing grants."
        ), SemanticCondition='consent=AnyApplication', ReadinessTreatment='Remediate', CompletionEvidence=closure, **common))
    else:
        managed = [MICROSOFT_CONSENT_POLICIES[value] for value in policy_ids if value in MICROSOFT_CONSENT_POLICIES]
        custom = [value for value in policy_ids if value not in MICROSOFT_CONSENT_POLICIES]
        described = ', '.join(policy_ids)
        observation = (f"Default users can grant application consent within {len(policy_ids)} assigned consent "
                       f"polic{'y' if len(policy_ids) == 1 else 'ies'} ({described}).")
        if managed:
            observation += f" Microsoft-managed or built-in conditions apply ({'; '.join(managed)}); they limit which publishers and permissions users can approve."
        if custom:
            observation += f" Custom policy conditions ({', '.join(custom)}) were not evaluated by this assessment."
        rows.append(_structured(new_recommendation(
            service="Entra",
            feature=feature['consent'],
            observation=observation,
            recommendation="Confirm that the assigned consent policies match the approved boundary for applications that can reach Microsoft 365 data, and that an admin-consent workflow handles requests outside it. Review existing grants separately.",
            link_text="Configure User Consent",
            link_url="https://learn.microsoft.com/entra/identity/enterprise-apps/configure-user-consent",
            priority="Medium",
            status="Action Required",
            finding_key="entra.apps.user_consent_assignment",
            evidence_key="app_consent_policy_detail; app_access_detail",
            evidence_summary="See Application Consent Policy for the assigned policies and their retained conditions."
        ), SemanticCondition='consent=PolicyBounded', ReadinessTreatment='ConfirmBeforePilot', CompletionEvidence=closure, **common))

    # Application grants
    high_privilege_apps = consent_metrics.get('high_privilege_apps') or 0
    unverified_publishers = consent_metrics.get('unverified_publishers') or 0
    data_sources = entra_insights.get('data_sources', {}) or {}
    grants_read = data_sources.get('oauth_grants', False) and data_sources.get('service_principals', False)
    grant_common = dict(EvidenceSource='oauth_grants', ControlId='APPS.CONSENT', DomainId='applications',
                        EvidenceScope='Delegated permission grants and enterprise applications returned by the tenant inventory')
    if not grants_read:
        grant_state = (entra_insights.get('collection_status', {}) or {}).get('oauth_grants', {}) or {}
        omitted = grant_state.get('availability_status') == 'not_requested'
        rows.append(_structured(new_recommendation(
            service="Entra",
            feature=feature['grants'],
            observation="Application grant inventory is not assessed. " + (grant_state.get('reason') or "Enterprise application grants or publisher metadata could not be read."),
            recommendation=("Review customer-provided application grant evidence separately; existing consent-policy evidence does not establish which grants are present." if omitted else "Verify Application.Read.All and Directory.Read.All with administrator consent, then rerun and review exact scopes and recent activity."),
            link_text="Review Enterprise Applications",
            link_url="https://learn.microsoft.com/entra/identity/enterprise-apps/overview",
            priority="Medium",
            status="Not Assessed",
            disposition="Coverage",
            evidence_key="app_access_detail",
            evidence_summary="The application inventory or delegated grant dataset was unavailable; no clean app-governance conclusion is supported.",
            finding_key="entra.app_consent.grant_inventory_not_assessed",
        ), SemanticCondition='grants=InventoryUnavailable', ReadinessTreatment='EvidenceGap', **grant_common))
    elif high_privilege_apps > 0:
        risk_details = [f"{high_privilege_apps} with high-privilege permissions"]
        if unverified_publishers > 0:
            risk_details.append(f"{unverified_publishers} from unverified publishers")
        rows.append(_structured(new_recommendation(
            service="Entra",
            feature=feature['grants'],
            observation=f"Application grants requiring review were detected: {', '.join(risk_details)}",
            recommendation="Review the flagged enterprise applications, their owners, grant type, exact scopes, publisher, business purpose, and recent activity. Remove unused grants and reduce scopes where the evidence shows access exceeds the documented need; do not revoke solely from the aggregate count.",
            link_text="App Governance",
            link_url="https://learn.microsoft.com/defender-cloud-apps/app-governance-manage-app-governance",
            priority="High",
            status="Action Required",
            finding_key="entra.app_consent.high_impact_grants",
            evidence_key="app_access_detail",
            evidence_summary="See App Access Detail for the exact risky applications, granted scopes, count logic, and available 30-day activity."
        ), SemanticCondition='grants=HighImpactGrantsPresent', ReadinessTreatment='Remediate', **grant_common))
    elif unverified_publishers > 0:
        rows.append(_structured(new_recommendation(
            service="Entra",
            feature=feature['grants'],
            observation=f"{unverified_publishers} externally owned application(s) with delegated grants did not return verified-publisher metadata",
            recommendation="Confirm the publisher, business owner, granted scopes, and recent use before expanding AI integrations. Publisher verification is a review signal, not proof that an application is malicious.",
            link_text="Publisher Verification",
            link_url="https://learn.microsoft.com/entra/identity-platform/publisher-verification-overview",
            priority="Medium",
            status="Insight",
            disposition="Opportunity",
            finding_key="entra.app_consent.unverified_publishers",
            evidence_key="app_access_detail",
            evidence_summary="See App Access Detail for the affected applications and exact delegated scopes."
        ), SemanticCondition='grants=UnverifiedPublisherGrants', **grant_common))
    else:
        rows.append(_structured(new_recommendation(
            service="Entra",
            feature=feature['grants'],
            observation="The assessed grant rules did not flag high-impact permissions or unverified publishers; this does not replace application-owner review",
            recommendation="",
            link_text="App Governance",
            link_url="https://learn.microsoft.com/defender-cloud-apps/app-governance-manage-app-governance",
            status="Success",
            finding_key="entra.app_consent.grants_reviewed",
            evidence_key="app_access_detail",
            evidence_summary="See App Access Detail for the application inventory and flagged permission review captured during the assessment."
        ), SemanticCondition='grants=NoFlaggedGrants', **grant_common))
    return rows


def _access_review_rows(entra_insights, feature):
    """One access-review row from the definitions inventory, qualified by its source state."""
    complete, availability, note = source_state(entra_insights, 'access_reviews')
    summary = entra_insights.get('access_review_summary', {}) or {}
    common = dict(EvidenceSource='access_reviews', DomainId='identity', FindingCondition='access_review.definitions',
                  EvidenceScope='Access review definitions returned by Microsoft Graph')
    link = dict(link_text="Configure Access Reviews", link_url="https://learn.microsoft.com/entra/id-governance/deploy-access-reviews")
    if not complete:
        semantic = {'partial': 'SourcePartial', 'not_requested': 'SourceNotRequested'}.get(availability, 'SourceUnavailable')
        return [_structured(new_recommendation(
            service="Entra",
            feature=feature['access_reviews'],
            observation=(f"Access review definitions were not established: the access review read was {note}. "
                         "No conclusion about review coverage or cadence is drawn."),
            recommendation="",
            status="Not Assessed",
            disposition="Reference",
            finding_key="entra.access_reviews.definitions",
            evidence_key="access_review_detail",
            evidence_summary="The access review inventory was not read completely.",
            **link
        ), SemanticCondition='access_reviews=' + semantic, **common)]
    total_reviews = summary.get('total_definitions', 0) or 0
    active_reviews = summary.get('active_reviews', 0) or 0
    group_reviews = summary.get('group_reviews', 0) or 0
    role_reviews = summary.get('role_reviews', 0) or 0
    guest_reviews = summary.get('guest_reviews', 0) or 0
    recurring_reviews = summary.get('recurring_reviews', 0) or 0
    if total_reviews == 0:
        # Optional lifecycle control unless a scoped requirement exists.
        return [_structured(new_recommendation(
            service="Entra",
            feature=feature['access_reviews'],
            observation="The completed access review read returned 0 access review definitions.",
            recommendation="Consider recurring reviews for privileged roles, guests, and AI entitlement groups where lifecycle risk or compliance requirements justify them. Do not create reviews solely to increase an assessment score.",
            priority="Medium",
            status="Insight",
            disposition="Opportunity",
            finding_key="entra.access_reviews.definitions",
            evidence_key="access_review_detail",
            evidence_summary="See Access Review Detail for the current review definitions and missing cadence relevant to this governance gap.",
            **link
        ), SemanticCondition='access_reviews=NoDefinitions', **common)]
    if recurring_reviews == 0:
        return [_structured(new_recommendation(
            service="Entra",
            feature=feature['access_reviews'],
            observation=f"{total_reviews} access review(s) configured, but none are recurring - Copilot access not continuously governed",
            recommendation=f"Convert {total_reviews} one-time access review(s) to recurring campaigns for continuous governance. One-time reviews: 1) Expire after completion (no ongoing oversight), 2) Require manual re-creation (administrative burden), 3) Create gaps in compliance coverage, 4) Miss new users added between reviews. Update review configurations to recur quarterly for group memberships, monthly for privileged roles, and semi-annually for guest access. Configure auto-apply results to automatically remove access for non-approved users. Set up notifications to reviewers 1 week before due date.",
            priority="Medium",
            status="Insight",
            disposition="Opportunity",
            finding_key="entra.access_reviews.definitions",
            evidence_key="access_review_detail",
            evidence_summary="See Access Review Detail for the one-time and recurring review definitions supporting this recommendation.",
            **link
        ), SemanticCondition='access_reviews=NoRecurringDefinitions', **common)]
    if group_reviews == 0:
        return [_structured(new_recommendation(
            service="Entra",
            feature=feature['access_reviews'],
            observation=f"{total_reviews} access review(s) active ({recurring_reviews} recurring), but no group membership reviews for Copilot license governance",
            recommendation=f"If Copilot license groups are used, consider recurring membership reviews where manager attestation adds value. Current reviews cover {role_reviews} role scope(s) and {guest_reviews} guest scope(s).",
            priority="Medium",
            status="Insight",
            disposition="Opportunity",
            finding_key="entra.access_reviews.definitions",
            evidence_key="access_review_detail",
            evidence_summary="See Access Review Detail for the review scopes currently present and where group-review coverage is missing.",
            **link
        ), SemanticCondition='access_reviews=NoGroupMembershipReviews', **common)]
    review_types = [f"{count} {label}" for count, label in ((group_reviews, 'group'), (role_reviews, 'role'), (guest_reviews, 'guest')) if count > 0]
    return [_structured(new_recommendation(
        service="Entra",
        feature=feature['access_reviews'],
        observation=f"{active_reviews} active access review(s) ({', '.join(review_types)}), {recurring_reviews} recurring - continuous governance for Copilot access",
        recommendation="",
        status="Success",
        finding_key="entra.access_reviews.definitions",
        evidence_key="access_review_detail",
        evidence_summary="See Access Review Detail for the active recurring reviews and review scopes already configured.",
        **link
    ), SemanticCondition='access_reviews=RecurringReviewsPresent', **common)]

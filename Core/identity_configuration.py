"""Identity technical findings that do not depend on the license inventory.

Entra license modules used to produce these rows only when a specific service
plan (for example AAD_PREMIUM_P2) was present, so identical tenant evidence
produced different conclusions under different licenses. This producer runs for
every tenant with a retained Entra client. Licensing remains capability context
in the license rows; it never decides whether an observed condition exists.
"""

# Plans whose modules emit identity conclusions. In the shared pipeline they
# contribute license context only; the technical rows come from this producer.
IDENTITY_LICENSE_PLANS = frozenset({
    'AAD_PREMIUM', 'AAD_PREMIUM_P1', 'AAD_PREMIUM_P2', 'AAD_GOVERNANCE',
    'AAD_PREMIUM_CONDITIONAL_ACCESS', 'AAD_PREMIUM_IDENTITY_PROTECTION', 'MFA_PREMIUM',
})

FEATURES = {
    'privileged': 'Privileged role assignment schedules',
    'risk': 'Identity risk',
    'guests': 'Guest access',
    'guest_invitations': 'Guest invitation settings',
    'cross_tenant': 'Cross-tenant access settings',
    'consent': 'Application consent',
    'grants': 'Application grants',
    'access_reviews': 'Access reviews',
}


def license_context(row, plan_name, status):
    """Keep a service-plan row as capability context, never a finding or an action."""
    row = dict(row)
    row.update(Disposition='Reference', Status='Insight', Priority='Low', Recommendation='',
               EvidenceBasis='License signal', EvidenceKey='service_plan_inventory', Confidence='Low',
               LicenseContext=True, LicensePlan=plan_name, LicenseStatus=status,
               Qualification='Service-plan inventory only. License availability does not establish or rule out any configured control.')
    return row


def assess_identity_configuration(entra_client, entra_insights=None):
    """Registration, sign-in, privileged, risk, guest, consent, grant and access-review rows."""
    if entra_client is None:
        return []
    if entra_insights is None:
        from Recommendations.entra.entra_insights import extract_entra_insights_from_client
        entra_insights = extract_entra_insights_from_client(entra_client)
    if not entra_insights or not entra_insights.get('available'):
        return []
    from Recommendations.entra.AAD_PREMIUM import authentication_recommendations, group_licensing_recommendations
    from Recommendations.entra.AAD_PREMIUM_P2 import governance_recommendations
    rows = [*authentication_recommendations(entra_insights, 'Multifactor authentication registration',
                                            passwordless_feature='Passwordless authentication registration'),
            *group_licensing_recommendations(entra_insights, 'Group-based licensing'),
            *governance_recommendations(entra_insights, FEATURES)]
    from Core.authentication_findings import qualify_legacy_finding
    from Core.signin_evidence import is_legacy_signin_finding
    return [qualify_legacy_finding(row, entra_client) if is_legacy_signin_finding(row)
            or 'No legacy authentication sign-ins' in row.get('Observation', '') else row for row in rows]

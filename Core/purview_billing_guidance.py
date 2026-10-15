"""Feature-specific guidance; collected licenses do not verify billing entitlement."""


def build_purview_billing_guidance():
    learn = 'https://learn.microsoft.com/en-us/purview/'
    rows = [
        ('Existing Microsoft 365 policy and posture evidence', 'Applicable per-user subscriptions',
         'Reuse existing audit, DLP, label and assessment results. Confirm the license for each advanced feature and the users in scope; viewing a dashboard does not establish entitlement.',
         'https://learn.microsoft.com/en-us/office365/servicedescriptions/microsoft-365-service-descriptions/microsoft-365-tenantlevel-services-licensing-guidance/microsoft-purview-service-description'),
        ('Microsoft 365 Copilot interaction protections', 'Applicable Copilot and Purview licenses',
         'Microsoft 365 Copilot experiences are excluded from the AI consumption charges in the billing guide. Confirm the subscription entitlement for the protection being used.',
         learn + 'purview-billing-models'),
        ('Other AI interaction capture, retention and protection', 'PAYG for applicable configurations',
         'Check the specific app and collection policy before activation. Purview management of Security Copilot interactions requires PAYG; Microsoft 365 Copilot has different billing treatment.',
         learn + 'ai-security-copilot'),
        ('Network and inline cloud-app protection in Edge', 'PAYG capabilities',
         'Confirm Azure billing and intended scope before activation. Existing licensed Endpoint DLP is a separate capability.',
         learn + 'purview-billing-models'),
        ('On-demand classification of historical content', 'Potentially metered scan',
         'This is separate from reviewing an existing DSPM assessment. The billing guide lists PAYG; current feature guidance allows PAYG, per-user licensing or both. Check the applicable entitlement, estimate and Usage center before initiating a scan.',
         learn + 'on-demand-classification'),
        ('Non-Microsoft 365 data-source protection and governance', 'PAYG for applicable capabilities',
         'Confirm the meters and assets in scope before extending protection to Fabric, Azure or third-party data sources.',
         learn + 'purview-billing-models'),
    ]
    return {
        'checked_on': '2026-10-12',
        'qualification': 'Billing entitlement and active meters were not verified by this assessment. '
                         'Review Purview Usage center > Pay-as-you-go and Premium usage with the service owner. '
                         'This assessment reads evidence; it does not activate billing, policies or scans.',
        'assessment_scope': 'The default Microsoft 365 DSPM assessment runs weekly over the top 100 SharePoint sites by usage. '
                            'Confirm that the assessed locations cover the intended AI content. A custom assessment and '
                            'an on-demand classification scan are separate operations; reuse completed results first.',
        'scope_reference': learn + 'data-security-posture-management-oversharing',
        'usage_reference': learn + 'purview-billing-usage',
        'rows': [{'Capability': feature, 'Billing basis': basis, 'Before requesting more evidence': action,
                  'Microsoft reference': reference, 'Guidance checked': '2026-10-12'}
                 for feature, basis, action, reference in rows],
    }

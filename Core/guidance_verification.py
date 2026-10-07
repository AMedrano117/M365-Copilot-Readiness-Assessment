"""Implementation-time Microsoft documentation checks; offline replay is static."""

VERIFIED_AT = '2026-10-02'
REFERENCES = {
 'copilot_requirements':('https://learn.microsoft.com/en-us/microsoft-365/copilot/microsoft-copilot-requirements','Prerequisites depend on the planned Copilot experience and app. Mailbox-grounded features require Exchange Online; app support, connected experiences and network access require separate review'),
 'copilot_licensing':('https://learn.microsoft.com/en-us/microsoft-365/copilot/microsoft-365-copilot-licensing','Paid Copilot capabilities require appropriate entitlement and a qualifying prerequisite plan. Available eligibility paths extend beyond Microsoft 365 E3/E5; actual user entitlements require validation'),
 'entra_recommendations':('https://learn.microsoft.com/en-us/graph/api/directory-list-recommendation?view=graph-rest-beta','Beta; DirectoryRecommendations.Read.All; supplemental configuration advice'),
 'cloud_discovery':('https://learn.microsoft.com/en-us/defender-cloud-apps/discovered-apps-api-graph','Beta; CloudApp-Discovery.Read.All; streams and discovered apps do not prove complete visibility'),
 'copilot_audit':('https://learn.microsoft.com/en-us/graph/api/security-auditcoreroot-post-auditlogqueries?view=graph-rest-1.0','Asynchronous saved audit query; AuditLogsQuery.Read.All; bounded records and service availability apply'),
 'compliance_settings':('https://learn.microsoft.com/en-us/graph/api/intune-deviceconfig-devicecompliancesettingstate-list?view=graph-rest-1.0','DeviceManagementConfiguration.Read.All; active Intune entitlement required'),
 'antivirus_health':('https://learn.microsoft.com/en-us/defender-endpoint/api/device-health-export-antivirus-health-report-api','Machine.Read.All; Defender for Endpoint Plan 2 or Defender for Business; health reporting is separate from onboarding'),
 'copilot_dlp':('https://learn.microsoft.com/en-us/purview/dlp-microsoft365-copilot-location-default-policy','Simulation reports do not establish blocking enforcement'),
 'kaizala':('https://learn.microsoft.com/en-us/lifecycle/announcements/kaizala-retirement-august-31-2023','Retired August 31, 2023'),
 'skype_online':('https://learn.microsoft.com/en-us/lifecycle/announcements/skype-for-business-online-retirement','Commercial Skype for Business Online retired July 31, 2021; Server and sovereign-cloud scope require separate review'),
 'staffhub':('https://learn.microsoft.com/en-us/connectors/staffhub/','StaffHub connector deprecated; corresponding operations moved to Teams Shifts. Exact service retirement date unverified here'),
}


def verification_rows():
    return [{'Claim':key,'Microsoft reference':url,'Verified at':VERIFIED_AT,'Verified scope':claim,
             'Qualification':'Documentation review establishes product guidance, not the tenant entitlement or operational behavior.'}
            for key,(url,claim) in REFERENCES.items()]


def qualify_guidance(row):
    if row.get('GuidanceVerifiedAt'):
        return
    url = row.get('LinkUrl') or row.get('Link') or ''
    match = next((item for item in verification_rows() if item['Microsoft reference']==url),None)
    row['GuidanceVerification'] = 'Verified documentation scope' if match else 'Unverified product, feature or licensing claims' if url else 'No product claim verification supplied'
    row['GuidanceVerifiedAt'] = match['Verified at'] if match else ''
    row['GuidanceVerificationScope'] = match['Verified scope'] if match else 'Confirm current Microsoft documentation and tenant entitlement before relying on product guidance.'


def legacy_service_reference(sku_name, status, service, key):
    from .new_recommendation import new_recommendation
    from .friendly_names import get_friendly_sku_name
    url, claim = REFERENCES[key]
    row = new_recommendation('M365', service,
        f'License inventory reports {service} with provisioning status {status} in {get_friendly_sku_name(sku_name)}. {claim}. A retained service plan does not establish current use or a migration gap.',
        '', link_text='Microsoft lifecycle or connector documentation', link_url=url, status='Reference',
        disposition='Reference', finding_key='legacy_service.'+key, evidence_key='service_plan_inventory',
        evidence_basis='License signal', confidence='Medium')
    row.update(GuidanceVerification='Verified documentation scope', GuidanceVerifiedAt=VERIFIED_AT,
               GuidanceVerificationScope=claim)
    return [row]

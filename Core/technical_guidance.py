"""Brief, verified technical fixes attached to findings for every deliverable.

Each entry was checked against the linked Microsoft Learn page on ``VERIFIED_ON``.
Offline rebuilds use this static catalog and perform no documentation lookups.
An actionable finding without an entry keeps its existing recommendation text and
is marked unverified rather than receiving an invented admin-center location.
"""

import re

VERIFIED_ON = '2026-10-05'

_ENTRA = 'Microsoft Entra admin center'
_LEARN = 'https://learn.microsoft.com/en-us/'
_STANDING_ADMIN = re.compile(r'role assignment schedules? have no expiration|permanent admin role assignment', re.IGNORECASE)


def _link(title, path):
    return {'title': title, 'url': _LEARN + path, 'verified_on': VERIFIED_ON}


def _keys(finding):
    return {key.strip() for key in str(finding.get('EvidenceKey') or '').split(';') if key.strip()}


def _legacy_events(finding):
    from .signin_evidence import is_legacy_signin_finding
    return is_legacy_signin_finding(finding)


def _standing_admin(finding):
    text = ' '.join(str(finding.get(name) or '') for name in ('OriginalObservation', 'Observation'))
    return 'admin_role_detail' in _keys(finding) and bool(_STANDING_ADMIN.search(text))


def _antivirus(finding):
    key = str(finding.get('FindingKey') or '')
    return 'antivirus_health' in key or ('windows_protection' in key and 'overdue' in key)


CATALOG = [
    {'id': 'legacy-authentication-events', 'predicate': _legacy_events,
     'change': 'Use the listed events to find each account, client and application still using legacy authentication, '
               'move them to modern authentication, then block legacy authentication with Conditional Access '
               '(Exchange ActiveSync clients and Other clients). Start in Report-only mode and exclude emergency access accounts.',
     'prerequisites': {'roles': ['Conditional Access Administrator (create the policy)',
                                 'Reports Reader or Security Reader (review sign-in logs)'],
                       'licensing': ['Microsoft Entra ID P1 for Conditional Access and for sign-in logs through Microsoft Graph. '
                                     'Tenants without Conditional Access can use security defaults, which block legacy authentication.'],
                       'other': ['Confirm business dependencies such as shared mailboxes, devices and line-of-business apps before enforcement.']},
     'where': _ENTRA + ' > Entra ID > Conditional Access > Policies > New policy (Conditions > Client apps: Exchange ActiveSync '
              'clients and Other clients; Grant: Block access)',
     'verify': 'Entra ID > Monitoring & health > Sign-in logs: filter Client app to the legacy clients on the interactive and '
               'non-interactive tabs. After enforcement, new legacy attempts show a Conditional Access failure (AADSTS53003). '
               'Re-run the assessment and confirm no new legacy events.',
     'evidence_needed': 'Individual events come from Microsoft Graph sign-in logs (AuditLog.Read.All; Microsoft Entra ID P1 or P2). '
                        'Run a new live collection, or export Entra ID > Monitoring & health > Sign-in logs filtered to legacy client apps.',
     'links': [_link('Block legacy authentication with Conditional Access', 'entra/identity/conditional-access/policy-block-legacy-authentication'),
               _link('Conditional Access client apps condition', 'entra/identity/conditional-access/concept-conditional-access-conditions'),
               _link('Microsoft Entra authentication error codes', 'entra/identity-platform/reference-error-codes')]},
    {'id': 'sharepoint-legacy-authentication', 'finding_keys': {'sharepoint.authentication.legacy_permitted'},
     'change': 'After confirming that no required clients depend on it, block apps that do not use modern authentication for SharePoint and OneDrive.',
     'prerequisites': {'roles': ['SharePoint Administrator'], 'licensing': [], 'other': []},
     'where': 'SharePoint admin center > Policies > Access control > Apps that don\'t use modern authentication > Block access',
     'verify': 'Access control shows Block access for apps that don\'t use modern authentication. Re-run the assessment to read the tenant setting again.',
     'evidence_needed': 'SharePoint tenant settings (SharePoint administrative read access).',
     'links': [_link('Control access from unmanaged devices and apps that do not use modern authentication', 'sharepoint/control-access-from-unmanaged-devices')]},
    {'id': 'conditional-access-baseline', 'finding_keys': {'baseline.identity.sign_in', 'baseline.identity.scope'},
     'controls': {'IDENTITY.AUTH'},
     'change': 'Require the Multifactor authentication strength for all users and all resources, and block legacy authentication. '
               'Exclude only emergency access and directory synchronization accounts. Start in Report-only mode, then turn the policies On.',
     'prerequisites': {'roles': ['Conditional Access Administrator'], 'licensing': ['Microsoft Entra ID P1'], 'other': []},
     'where': _ENTRA + ' > Entra ID > Conditional Access > Policies > New policy',
     'verify': 'Review the report-only results, then confirm in Entra ID > Monitoring & health > Sign-in logs that sign-ins show '
               'Conditional Access success with MFA. Re-run the assessment.',
     'evidence_needed': 'Conditional Access policy inventory (Policy.Read.All) and recent sign-in logs.',
     'links': [_link('Require MFA for all users with Conditional Access', 'entra/identity/conditional-access/policy-all-users-mfa-strength'),
               _link('Block legacy authentication with Conditional Access', 'entra/identity/conditional-access/policy-block-legacy-authentication')]},
    {'id': 'mfa-registration', 'predicate': lambda finding: 'mfa_registration' in str(finding.get('FindingKey') or ''),
     'controls': {'IDENTITY.MFA'},
     'change': 'Have each listed user register a strong method. Run a registration campaign for Microsoft Authenticator or passkeys, '
               'scoped to the affected users or groups, and follow up on accounts that remain unregistered.',
     'prerequisites': {'roles': ['Authentication Policy Administrator'],
                       'licensing': ['No license is stated in the registration campaign article; confirm entitlement for your tenant. '
                                     'Authentication methods usage insights require Microsoft Entra ID P1 or P2.'],
                       'other': ['The targeted method must be enabled for these users in the authentication methods policy.']},
     'where': _ENTRA + ' > Entra ID > Authentication methods > Registration campaign',
     'verify': 'Entra ID > Authentication methods > Activity > Registration (user registration details): the listed users show '
               'MFA Capable. Re-run the assessment.',
     'evidence_needed': 'User registration details (reports/authenticationMethods/userRegistrationDetails; AuditLog.Read.All).',
     'links': [_link('Run a registration campaign', 'entra/identity/authentication/how-to-mfa-registration-campaign'),
               _link('Authentication methods activity and registration details', 'entra/identity/authentication/howto-authentication-methods-activity')]},
    {'id': 'standing-admin-access', 'predicate': _standing_admin, 'controls': {'IDENTITY.ADMIN'},
     'change': 'Convert the listed standing (no-expiration) privileged role assignments to Privileged Identity Management eligible '
               'or time-bound active assignments. Keep documented emergency access accounts as the only permanent Global Administrators.',
     'prerequisites': {'roles': ['Privileged Role Administrator'],
                       'licensing': ['Microsoft Entra ID P2 or Microsoft Entra ID Governance for users with eligible or time-bound assignments'],
                       'other': []},
     'where': _ENTRA + ' > ID Governance > Privileged Identity Management > Microsoft Entra roles > Roles '
              '(Add assignments: Eligible; existing Active roles: Update or Remove)',
     'verify': 'Privileged Identity Management > Microsoft Entra roles > Assignments: the listed principals appear under Eligible '
               'assignments, or as Active assignments with an end date. Re-run the assessment.',
     'evidence_needed': 'Role assignment schedules (RoleManagement.Read.Directory).',
     'links': [_link('Assign Microsoft Entra roles in PIM', 'entra/id-governance/privileged-identity-management/pim-how-to-add-role-to-user'),
               _link('Microsoft Entra ID Governance licensing fundamentals', 'entra/id-governance/licensing-fundamentals')]},
    {'id': 'risky-users', 'finding_keys': {'entra.identity_risk.users'},
     'change': 'Investigate each listed risky user. Require a secure password change or reset for compromised accounts, confirm '
               'compromise or dismiss risk after investigation, and configure risk-based Conditional Access for self-remediation.',
     'prerequisites': {'roles': ['Security Operator (dismiss user risk)', 'User Administrator (reset passwords)',
                                 'Security Administrator (risk-based policies)'],
                       'licensing': ['Microsoft Entra ID P2 or Microsoft Entra Suite'], 'other': []},
     'where': _ENTRA + ' > Protection > Identity Protection > Risky users',
     'verify': 'Risky users shows each listed account as Remediated, Dismissed or Confirmed compromised with a completed response. '
               'Re-run the assessment.',
     'evidence_needed': 'Risky users and risk detections (IdentityRiskyUser.Read.All; Microsoft Entra ID P2).',
     'links': [_link('Remediate risks and unblock users', 'entra/id-protection/howto-identity-protection-remediate-unblock')]},
    {'id': 'user-consent', 'finding_keys': {'entra.apps.user_consent_assignment'},
     'change': 'Allow user consent only for apps from verified publishers and low-impact permissions, or turn user consent off, '
               'and enable the admin consent workflow so users can request approval.',
     'prerequisites': {'roles': ['Privileged Role Administrator (Global Administrator when using the admin center)',
                                 'Global Administrator (turn on the admin consent workflow)'],
                       'licensing': [], 'other': []},
     'where': _ENTRA + ' > Enterprise apps > Consent and permissions > User consent settings (and Admin consent settings)',
     'verify': 'GET https://graph.microsoft.com/v1.0/policies/authorizationPolicy returns only the intended policies in '
               'defaultUserRolePermissions.permissionGrantPoliciesAssigned. Re-run the assessment.',
     'evidence_needed': 'Authorization policy and permission grant policies (Policy.Read.All).',
     'links': [_link('Configure how users consent to applications', 'entra/identity/enterprise-apps/configure-user-consent'),
               _link('Configure the admin consent workflow', 'entra/identity/enterprise-apps/configure-admin-consent-workflow')]},
    {'id': 'application-grants', 'finding_keys': {'entra.app_consent.high_impact_grants'}, 'controls': {'APPS.CONSENT'},
     'change': 'Review each listed application\'s granted permissions with its owner. Revoke permissions that are not required and '
               'record the business justification for the rest.',
     'prerequisites': {'roles': ['Cloud Application Administrator or Application Administrator'], 'licensing': [], 'other': []},
     'where': _ENTRA + ' > Entra ID > Enterprise apps > All applications > (application) > Permissions (Admin consent and User consent tabs)',
     'verify': 'The application\'s Permissions page no longer lists the revoked permissions. User-consented grants are removed with '
               'Microsoft Graph or PowerShell. Re-run the assessment.',
     'evidence_needed': 'Service principals, delegated grants and application permissions (Application.Read.All, Directory.Read.All).',
     'links': [_link('Review permissions granted to enterprise applications', 'entra/identity/enterprise-apps/manage-application-permissions')]},
    {'id': 'sharing-settings', 'finding_key_prefixes': ('sharepoint.sharing.',), 'finding_keys': {'baseline.content.sharing'},
     'controls': {'CONTENT.SHARING'},
     'change': 'Set organization-level sharing for SharePoint and OneDrive to the most restrictive level your collaboration allows, '
               'make Specific people or Only people in your organization the default link, and require expiration for Anyone links '
               'where they remain allowed.',
     'prerequisites': {'roles': ['SharePoint Administrator'], 'licensing': [], 'other': []},
     'where': 'SharePoint admin center > Policies > Sharing (External sharing; File and folder links; Advanced settings for Anyone links)',
     'verify': 'The Sharing page shows the new levels and defaults. Re-run the assessment to read the tenant sharing settings again.',
     'evidence_needed': 'SharePoint tenant sharing settings (SharePointTenantSettings.Read.All) and site settings.',
     'links': [_link('Manage sharing settings for SharePoint and OneDrive', 'sharepoint/turn-external-sharing-on-or-off')]},
    {'id': 'oversharing', 'finding_key_prefixes': ('data_exposure.',), 'controls': {'DATA.EXPOSURE', 'CONTENT.PERMISSIONS'},
     'change': 'Run the Data access governance site permissions report, plus the sharing links and \'Everyone except external users\' '
               'reports. Remediate overshared sites with site access reviews or Restricted access control before broad Copilot rollout.',
     'prerequisites': {'roles': ['SharePoint Administrator'],
                       'licensing': ['SharePoint Advanced Management prerequisites apply. Microsoft 365 E5 provides Data access governance '
                                     'reports without snapshot reports or remedial actions.'],
                       'other': []},
     'where': 'SharePoint admin center > Reports > Data access governance',
     'verify': 'Re-run the reports after remediation and supply the exports with --sam-report to rebuild the assessment.',
     'evidence_needed': 'Site-, link- and permission-level detail requires the Data access governance report exports; summary '
                        'counts cannot identify individual files.',
     'links': [_link('Data access governance reports', 'sharepoint/data-access-governance-reports')]},
    {'id': 'security-incidents', 'finding_keys': {'defender.incidents.current', 'baseline.defender.incidents'},
     'controls': {'THREAT.INCIDENTS'},
     'change': 'Investigate each listed active incident, starting with high severity. Resolve it with a classification, or record the '
               'security owner\'s approved treatment.',
     'prerequisites': {'roles': ['A Microsoft Defender XDR role assigned through Defender XDR role-based access control that can manage incidents'],
                       'licensing': [], 'other': []},
     'where': 'Microsoft Defender portal > Investigation & response > Incidents & alerts > Incidents > Manage incident (Status: Resolved; Classification)',
     'verify': 'The incident queue, filtered to New and In progress, no longer lists the incidents. Re-run the assessment.',
     'evidence_needed': 'Security incidents (SecurityIncident.Read.All).',
     'links': [_link('Manage incidents in the Microsoft Defender portal', 'defender-xdr/manage-incidents'),
               _link('Prioritize incidents in the Microsoft Defender portal', 'defender-xdr/incident-queue')]},
    {'id': 'antivirus-updates', 'predicate': _antivirus,
     'change': 'Restore security intelligence updates on the listed devices. Confirm the update source order (for example Microsoft '
               'Update or WSUS first) and network access, then trigger an update.',
     'prerequisites': {'roles': ['Permission to change the device update policy in your management tool',
                                 'Defender for Endpoint View Data - Threat and vulnerability management (report access)'],
                       'licensing': [], 'other': []},
     'where': 'Group Policy: Computer configuration > Administrative templates > Windows components > Microsoft Defender Antivirus > '
              'Security Intelligence Updates (Define the order of sources for downloading security intelligence updates)',
     'verify': 'Microsoft Defender portal > Reports > Device health and compliance > Microsoft Defender Antivirus health: the listed '
               'devices show security intelligence Up to date. Re-run the assessment.',
     'evidence_needed': 'Defender Antivirus health export (Machine.Read.All).',
     'links': [_link('Manage where Microsoft Defender Antivirus receives updates', 'defender-endpoint/manage-protection-updates-microsoft-defender-antivirus'),
               _link('Microsoft Defender Antivirus health report', 'defender-endpoint/device-health-microsoft-defender-antivirus-health')]},
    {'id': 'data-loss-prevention', 'finding_key_prefixes': ('purview.dlp.', 'baseline.dlp.'), 'controls': {'DATA.DLP'},
     'change': 'Create or extend DLP policies for the locations Copilot can use. Run them in simulation mode, tune, then turn them on.',
     'prerequisites': {'roles': ['Compliance administrator, Compliance data administrator, Information Protection, Information '
                                 'Protection Admin or Security administrator role group'],
                       'licensing': ['See the Microsoft 365 security and compliance licensing guidance for each DLP location'], 'other': []},
     'where': 'Microsoft Purview portal > Data loss prevention > Policies > Create policy',
     'verify': 'The policy runs in simulation mode without unexpected matches, then shows as turned on. Re-run the assessment.',
     'evidence_needed': 'DLP policies and rules (Security & Compliance PowerShell read access).',
     'links': [_link('Create and deploy a data loss prevention policy', 'purview/dlp-create-deploy-policy')]},
    {'id': 'sensitivity-labels', 'finding_key_prefixes': ('purview.sensitivity_labels.', 'baseline.labels.'),
     'controls': {'DATA.LABELS', 'DATA.PUBLISHING'},
     'change': 'Create the sensitivity labels in your classification taxonomy and publish them with a label policy to the users who need them.',
     'prerequisites': {'roles': ['Information Protection role group (or a role group with Sensitivity Label Administrator)'],
                       'licensing': ['Per-user Microsoft Purview Information Protection licensing applies; see the Microsoft Purview service description'],
                       'other': []},
     'where': 'Microsoft Purview portal > Solutions > Information Protection > Sensitivity labels (create), then Publishing policies (publish)',
     'verify': 'The label policy lists the intended labels and users. Allow up to 24 hours to propagate, then re-run the assessment.',
     'evidence_needed': 'Sensitivity labels and label policies (InformationProtectionPolicy.Read.All or Security & Compliance PowerShell).',
     'links': [_link('Create and publish sensitivity labels', 'purview/create-sensitivity-labels'),
               _link('Permissions to create and manage sensitivity labels', 'purview/get-started-with-sensitivity-labels')]},
    {'id': 'audit', 'finding_key_prefixes': ('purview.audit.',), 'controls': {'DATA.AUDIT'},
     'change': 'Confirm that unified audit logging is on, and turn it on if it is off.',
     'prerequisites': {'roles': ['Audit Logs role in Exchange Online (Compliance Management or Organization Management role group)'],
                       'licensing': ['Auditing is not on by default for Microsoft 365 Business Basic, Business Standard, Business Premium or trial tenants'],
                       'other': []},
     'where': 'Microsoft Purview portal > Audit (Start recording user and admin activity)',
     'verify': 'Exchange Online PowerShell: Get-AdminAuditLogConfig | Format-List UnifiedAuditLogIngestionEnabled returns True. Re-run the assessment.',
     'evidence_needed': 'Audit configuration (Exchange Online PowerShell read access).',
     'links': [_link('Turn auditing on or off', 'purview/audit-log-enable-disable')]},
    {'id': 'retention', 'finding_key_prefixes': ('purview.retention', 'baseline.retention.'), 'controls': {'DATA.RETENTION'},
     'change': 'Create retention policies that meet your requirements for Copilot interactions and the content locations Copilot uses.',
     'prerequisites': {'roles': ['Permissions for retention policies described in the data lifecycle management guidance'],
                       'licensing': [], 'other': []},
     'where': 'Microsoft Purview portal > Solutions > Data Lifecycle Management > Policies > Retention policies > New retention policy '
              '(location: Microsoft Copilot experiences)',
     'verify': 'The policy shows a successful distribution status; it can take up to seven days to apply. Re-run the assessment.',
     'evidence_needed': 'Retention policies (Security & Compliance PowerShell read access).',
     'links': [_link('Create retention policies', 'purview/create-retention-policies')]},
    {'id': 'license-assignment', 'finding_keys': {'baseline.license.capacity'}, 'controls': {'LICENSE.ASSIGNMENT'},
     'change': 'Assign Microsoft 365 Copilot licenses to the intended users or groups.',
     'prerequisites': {'roles': ['License Administrator or User Administrator'], 'licensing': [], 'other': []},
     'where': 'Microsoft 365 admin center > Billing > Licenses > (product) > Assign licenses, or Users > Active users > Licenses and apps',
     'verify': 'The product\'s Licenses page lists the assignments with no Errors & issues. Re-run the assessment.',
     'evidence_needed': 'Subscribed SKUs and user license assignments (Organization.Read.All, User.Read.All).',
     'links': [_link('Assign or unassign licenses for users', 'microsoft-365/admin/manage/assign-licenses-to-users')]},
]

_ACTIONABLE = {'Action', 'Coverage'}


def guidance_for(finding):
    """Return the catalog entry for a finding, matched by content and never by positional ID."""
    key = str(finding.get('FindingKey') or '')
    control = str(finding.get('ControlId') or '')
    for entry in CATALOG:
        if key and key in entry.get('finding_keys', ()):
            return entry
        if entry.get('predicate') and entry['predicate'](finding):
            return entry
        if key and any(key.startswith(prefix) for prefix in entry.get('finding_key_prefixes', ())):
            return entry
    return next((entry for entry in CATALOG if control and control in entry.get('controls', ())), None)


def recommendation_detail(finding):
    """Structured fix for one finding row. Unmatched actionable rows stay visible as unverified."""
    entry = guidance_for(finding)
    actionable = finding.get('Disposition') in _ACTIONABLE
    if entry:
        return {'guidance_id': entry['id'], 'guidance_status': 'verified', 'verified_on': VERIFIED_ON,
                'change': entry['change'], 'prerequisites': entry['prerequisites'], 'where': entry['where'],
                'verify': entry['verify'], 'evidence_needed': entry.get('evidence_needed', ''),
                'links': list(entry['links']), 'existing_recommendation': finding.get('Recommendation') or ''}
    return {'guidance_id': None,
            'guidance_status': 'admin-center location not verified' if actionable else 'not required',
            'verified_on': None, 'change': finding.get('Recommendation') or '',
            'prerequisites': {'roles': [], 'licensing': [], 'other': []}, 'where': '', 'verify': '',
            'evidence_needed': '', 'links': ([{'title': finding.get('LinkText') or 'Microsoft documentation',
                                               'url': finding.get('LinkUrl'), 'verified_on': None}]
                                             if finding.get('LinkUrl') else []),
            'existing_recommendation': finding.get('Recommendation') or ''}


def attach_technical_guidance(payload):
    """Add recommendation_detail to every selected finding from its full source row."""
    rows = {row.get('RecommendationId'): row for row in payload.get('recommendations') or []}
    for finding in payload.get('findings') or []:
        row = rows.get(finding.get('finding_id')) or {}
        detail = recommendation_detail(row)
        finding['recommendation_detail'] = detail
        finding.setdefault('finding_key', row.get('FindingKey') or None)
        finding.setdefault('finding_fingerprint', row.get('FindingFingerprint') or None)
        if row:
            row['recommendation_detail'] = detail
    return payload


def verification_rows():
    """Documentation Verification rows for the catalog's Microsoft Learn references."""
    seen, rows = set(), []
    for entry in CATALOG:
        for link in entry['links']:
            if link['url'] in seen:
                continue
            seen.add(link['url'])
            rows.append({'Claim': 'technical_guidance.' + entry['id'], 'Microsoft reference': link['url'],
                         'Verified at': link['verified_on'],
                         'Verified scope': link['title'] + ': admin-center location, roles and prerequisites used in the technical fix',
                         'Qualification': 'Documentation review establishes product guidance, not the tenant entitlement or operational behavior.'})
    return rows

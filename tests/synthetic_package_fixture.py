"""Invented complete tenant data for offline acceptance previews, never customer data."""

import csv
import json
from pathlib import Path
from types import SimpleNamespace

from Core.offline_collection import empty_service_results, save_collection


SYNTHETIC_TENANT = '33333333-3333-4333-8333-333333333333'


def _source_state(records):
    return {'available': True, 'availability_status': 'available',
            'records_collected': records, 'pages_collected': 1,
            'truncated': False, 'complete': True, 'refresh_date': '2026-09-14'}


def _attach_synthetic_source_objects(results):
    """Back the reviewed findings with invented, dated source objects and states."""
    m365 = results['m365_result'][0]['_client']
    m365.external_connections = [
        {'id': f'fictional-connection-{index}', 'name': f'Fictional approved source {index}',
         'state': 'ready', 'description': 'Invented pilot source with reviewed permission boundaries.'}
        for index in range(1, 3)
    ]
    m365.collection_status['external_connections'] = _source_state(2)
    m365.sharepoint_governance = {
        'available': True, 'availability_status': 'available',
        'tenant': {'available': True, 'settings': {'SharingCapability': 'Disabled',
                   'OneDriveSharingCapability': 'Disabled', 'DefaultSharingLinkType': 'Direct'}},
        'sites': {'available': True, 'items': [{'Title': 'Fictional pilot',
                  'Url': 'https://synthetic.sharepoint.com/sites/pilot', 'SharingCapability': 'Disabled'}]},
    }
    registrations = [
        {'id': f'fictional-user-{index}', 'isMfaRegistered': True, 'isMfaCapable': True,
         'isPasswordlessCapable': True, 'methodsRegistered': ['fido2'],
         'lastUpdatedDateTime': '2026-09-14T12:00:00Z', 'userType': 'member', 'isAdmin': index <= 6}
        for index in range(1, 151)
    ]
    roles = [
        {'id': f'fictional-assignment-{index}', 'principalId': f'fictional-user-{index}',
         'roleDefinitionId': 'fictional-role', 'directoryScopeId': '/',
         'principal': {'displayName': f'Fictional operator {index}', '@odata.type': '#microsoft.graph.user'},
         'roleDefinition': {'displayName': 'Security Reader'},
         'scheduleInfo': {'expiration': {'type': 'afterDateTime', 'endDateTime': '2026-09-14T20:00:00Z'}}}
        for index in range(1, 7)
    ]
    results['entra_info']['_client'] = SimpleNamespace(
        available=True, auth_methods_registration=registrations,
        role_assignments=[], role_assignment_schedules=roles, role_eligibility_schedules=[],
        authorization_policy={'defaultUserRolePermissions': {'permissionGrantPoliciesAssigned': []}},
        collection_status={'auth_methods': _source_state(150), 'role_assignments': _source_state(0),
                           'role_assignment_schedules': _source_state(6), 'role_eligibility_schedules': _source_state(0),
                           'authorization_policy': _source_state(1)},
    )
    policy_names = [f'Fictional pilot DLP {index}' for index in range(1, 6)]
    labels = [{'Name': f'Fictional sensitivity label {index}', 'DisplayName': f'Fictional sensitivity label {index}'}
              for index in range(1, 4)]
    policies = [{'Name': name, 'Enabled': True, 'Mode': 'Enable',
                 'SharePointLocation': ['https://synthetic.sharepoint.com/sites/pilot'],
                 'OneDriveLocation': ['https://synthetic-my.sharepoint.com/personal/pilot']}
                for name in policy_names]
    rules = [{'Name': name + ' rule', 'ParentPolicyName': name, 'Disabled': False,
              'ContentContainsSensitiveInformation': 'Fictional sensitive information', 'BlockAccess': True}
             for name in policy_names]
    results['purview_info']['_client'] = SimpleNamespace(
        available=True, dlp_policies={**_source_state(5), 'policies': policies},
        dlp_rules={**_source_state(5), 'rules': rules},
        sensitivity_labels={**_source_state(3), 'labels': labels, 'total_labels': 3},
        label_policies={**_source_state(1), 'policies': [{'Name': 'Fictional pilot publishing', 'Enabled': True}], 'total_policies': 1},
        audit_config={**_source_state(1), 'unified_audit_enabled': True},
        retention_labels={**_source_state(1), 'labels': [{'Name': 'Fictional approved pilot retention'}]},
        comm_compliance={}, information_barriers={}, insider_risk={}, ediscovery_cases={},
        collection_status={name: _source_state(count) for name, count in (
            ('dlp_policies', 5), ('dlp_rules', 5), ('sensitivity_labels', 3),
            ('label_policies', 1), ('audit_config', 1), ('retention_policies', 1))},
    )
    results['defender_info']['_client'] = SimpleNamespace(
        available=True,
        security_incidents=[{'id': 'fictional-resolved-incident', 'title': 'Fictional resolved exercise',
                             'status': 'resolved', 'severity': 'low', 'createdDateTime': '2026-09-13T12:00:00Z'}],
        incident_summary={'total': 1, 'active': 0, 'high_severity': 0},
        defender_devices=[{'id': f'fictional-device-{index}', 'computerDnsName': f'Fictional pilot device {index}',
                           'riskScore': 'Low', 'healthStatus': 'Active', 'onboardingStatus': 'Onboarded',
                           'osPlatform': 'Windows11', 'lastSeen': '2026-09-14T12:00:00Z'}
                          for index in range(1, 151)],
        device_summary={'total': 150, 'high_risk': 0},
        collection_status={'incidents': _source_state(1), 'machines': _source_state(150)},
    )


def create_synthetic_package(directory, *, active_incident=False):
    directory = Path(directory)
    exports = directory / 'original_exports'
    exports.mkdir(parents=True, exist_ok=True)
    base = {'Tenant ID': SYNTHETIC_TENANT, 'Report Date': '2026-09-14',
            'Report scope': 'One fictional SharePoint pilot site and one OneDrive location', 'Primary Admin': 'owner@example.invalid'}
    permission_rows = []
    for workload, address in [('SharePoint', 'https://synthetic.sharepoint.com/sites/pilot'),
                              ('OneDrive', 'https://synthetic-my.sharepoint.com/personal/pilot')]:
        permission_rows.append({**base, 'Workload': workload, 'Site URL': address,
                               'Anyone link count': 0, 'Everyone permission count': 0,
                               'EEEU permission count': 0, 'Organization link count': 0,
                               'External user count': 0, 'Number of users having access': 150})
    dspm_rows = [{**base, 'Assessment Date': '2026-09-14', 'Site URL': 'https://synthetic.sharepoint.com/sites/pilot',
                  'Potentially overshared items': 0, 'Unlabeled sensitive item count': 0}]
    lifecycle_rows = [{**base, 'Site name': 'Fictional pilot', 'URL': 'https://synthetic.sharepoint.com/sites/pilot',
                       'Is inactive': 'False', 'Is ownerless': 'False',
                       'Email address of site owners': 'owner@example.invalid'}]
    for name, rows in [('permissions.csv', permission_rows), ('sensitive-data.csv', dspm_rows), ('lifecycle.csv', lifecycle_rows)]:
        with (exports / name).open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0])
            writer.writeheader()
            writer.writerows(rows)
    profile = directory / 'profile.json'
    profile.write_text(json.dumps({'version': '1', 'scope': {'agents': False, 'external_ai': False},
                                    'products': [{'provider': 'Microsoft', 'name': 'Microsoft 365 Copilot'}],
                                    'use_cases': [], 'readiness_review': {
        'version': '1.0', 'tenant_id': SYNTHETIC_TENANT,
        'pilot_scope': {'id': 'pilot', 'description': 'All 150 fictional users and approved pilot content',
                        'population_count': 150, 'reviewed_at': '2026-09-14',
                        'reviewer_role': 'Business sponsor', 'evidence_reference': 'Fictional pilot charter'},
        'pilot_plan': {'reviewed_at': '2026-09-14', 'reviewer_role': 'Business sponsor',
                       'business_owner': 'Fictional operations lead', 'use_cases': 'Draft approved project summaries',
                       'approved_data': 'Approved internal project documents', 'baseline': '40 minutes per draft',
                       'success_measures': 'Reduce drafting time with no sensitive-data incidents',
                       'stop_expand_criteria': 'Stop for a disclosure; expand only after sponsor review',
                       'evidence_reference': 'Fictional pilot charter'},
        'control_reviews': [{'control_id': 'DATA.EXPOSURE', 'scope_id': 'pilot', 'reviewed_at': '2026-09-14',
                             'reviewer_role': 'Information protection owner', 'result': 'pass',
                             'rationale': 'Reviewed sensitive fictional pilot content and its permissions.',
                             'evidence_reference': 'Fictional sensitive-content access review'},
                            {'control_id': 'IDENTITY.AUTH', 'scope_id': 'pilot', 'reviewed_at': '2026-09-14',
                             'reviewer_role': 'Identity security owner', 'result': 'pass',
                             'rationale': 'Reviewed effective sign-in policy assignments and exclusions for all 150 fictional pilot users; approved authentication requirements apply to every pilot account.',
                             'evidence_reference': 'Fictional pilot sign-in coverage and exclusion review'},
                            {'control_id': 'DATA.PUBLISHING', 'scope_id': 'pilot', 'reviewed_at': '2026-09-14',
                             'reviewer_role': 'Information protection owner', 'result': 'pass',
                             'rationale': 'Reviewed label publishing policy assignments and exclusions; all 150 fictional pilot users receive the three approved sensitivity labels.',
                             'evidence_reference': 'Fictional pilot label publication coverage review'},
                            {'control_id': 'DATA.DLP', 'scope_id': 'pilot', 'reviewed_at': '2026-09-14',
                             'reviewer_role': 'Information protection owner', 'result': 'pass',
                             'rationale': 'Reviewed effective DLP rules, enforcement mode and exclusions for the fictional SharePoint pilot site and OneDrive location; approved sensitive-content restrictions are enforced across both locations.',
                             'evidence_reference': 'Fictional pilot DLP enforcement and location coverage review'}],
    }}), encoding='utf-8')
    results = empty_service_results()
    results['m365_result'][0]['_client'] = SimpleNamespace(
        available=True, users_summary={'total': 150, 'copilot_licensed': 150, 'copilot_license_coverage': 100},
        users=[], external_connections=[], collection_status={},
        copilot_usage={'available': True, 'availability_status': 'available', 'refresh_date': '2026-09-14',
                       'source': 'Synthetic normalized Microsoft 365 Copilot usage',
                       'period': 'D28', 'selected_period': 'D28', 'records_collected': 150,
                       'scope': 'All 150 fictional pilot users', 'tenant_id': SYNTHETIC_TENANT,
                       'periods': {'D28': {'enabled_users': 150, 'active_users': 60,
                                           'active_rate': 40, 'unused_licenses': 90, 'apps': {}}},
                       'complete': True, 'truncated': False, 'freshness': 'Fresh', 'stale': False},
    )
    _attach_synthetic_source_objects(results)
    if active_incident:
        defender = results['defender_info']['_client']
        defender.security_incidents[0].update(status='active', severity='high', title='Fictional active exercise')
        defender.incident_summary.update(active=1, high_severity=1)
    controls = [
        ('entra_info', 'identity', 'Multifactor authentication coverage', 'authentication_detail', 'authentication', 'All 150 fictional pilot users have the approved sign-in policy and registered multifactor methods.'),
        ('entra_info', 'identity', 'Privileged role review', 'admin_role_detail', 'admin_role', 'All 6 fictional administrative role assignments have approved owners and time-limited activation.'),
        ('m365_result', 'content', 'Tenant sharing defaults', 'sharepoint_governance_detail', 'tenant_sharing', 'Default links require specified recipients; anonymous sharing is disabled for the fictional pilot sites.'),
        ('m365_result', 'content', 'Business content permission review', 'data_exposure_detail', 'permissions_snapshot', 'Permissions were reviewed for the fictional SharePoint pilot site and OneDrive location; no unjustified broad grants remain.'),
        ('m365_result', 'content', 'Content ownership and lifecycle review', 'sharepoint_lifecycle_detail', 'lifecycle', 'The fictional SharePoint pilot site has a named business owner and a recorded retention decision.'),
        ('purview_info', 'data_protection', 'Sensitivity label publication', 'purview_policy_detail', 'sensitivity_labels', 'Three fictional sensitivity labels are published to all 150 pilot users.'),
        ('purview_info', 'data_protection', 'Data loss prevention enforcement', 'purview_policy_detail', 'dlp', 'Five fictional data loss prevention policies are enforced for the agreed pilot locations.'),
        ('purview_info', 'data_protection', 'Audit and retention review', 'purview_policy_detail', 'audit', 'Audit logging and the approved retention requirements are enabled for the fictional pilot.'),
        ('entra_info', 'applications', 'Application consent review', 'app_consent_policy_detail', 'consent', 'The fictional tenant authorization policy assigns no self-consent policy to default users; application grants require an authorized administrator.'),
        ('m365_result', 'applications', 'Connected source access review', 'external_connection_detail', 'external_connection', 'Two fictional connected sources preserve the approved user permission boundaries.'),
        ('defender_info', 'endpoints', 'Pilot device protection', 'defender_device_detail', 'endpoint', 'All 150 fictional pilot devices meet the approved management, browser and threat-protection baseline.'),
        ('defender_info', 'endpoints', 'Active incident review', 'defender_incident_detail', 'incident', 'The dated fictional incident review found zero active incidents affecting the pilot.'),
        ('m365_result', 'licensing', 'Copilot license assignment', 'ai_usage_detail', 'copilot_license', 'All 150 fictional pilot users have the intended paid Microsoft 365 Copilot license assigned.'),
        ('m365_result', 'licensing', 'Application prerequisites', 'ai_usage_detail', 'm365_app_readiness', 'All 150 fictional pilot users meet the approved application and update-channel prerequisites.'),
        ('m365_result', 'adoption', 'Pilot usage baseline', 'ai_usage_detail', 'copilot_usage', 'The fictional pilot recorded 60 active users in 28 days; the sponsor approved the population and outcome measures.'),
    ]
    for service_key, domain, feature, evidence_key, finding_key, observation in controls:
        if active_incident and finding_key == 'incident':
            # Let the normal incident adapter create the action from the source.
            continue
        service = {'m365_result': 'M365', 'entra_info': 'Entra', 'purview_info': 'Purview', 'defender_info': 'Defender'}[service_key]
        recommendation = {'Service': service, 'Feature': feature, 'Observation': observation,
                          'Recommendation': '', 'Status': 'Success', 'Priority': 'Low', 'Disposition': 'Assurance',
                          'EvidenceBasis': 'Tenant evidence', 'EvidenceAvailable': 'Yes', 'Confidence': 'High',
                          'DomainId': domain, 'EvidenceKey': evidence_key, 'FindingKey': 'synthetic.' + finding_key,
                          'ObservationDate': '2026-09-14', 'EvidenceScope': 'All 150 fictional pilot users, one SharePoint site and one OneDrive location',
                          'TenantId': SYNTHETIC_TENANT, 'EvidenceComplete': True}
        if finding_key == 'endpoint':
            recommendation.update(ControlId='ENDPOINT.POSTURE', EvidenceBasis='Reviewed endpoint baseline')
        if service_key == 'm365_result':
            results[service_key][1].append(recommendation)
        else:
            results[service_key]['available'] = True
            results[service_key]['recommendations'].append(recommendation)
    results['m365_result'][1].append({
        'Service': 'M365', 'DomainId': 'adoption', 'ControlId': 'ADOPTION.BASELINE',
        'Feature': 'Reviewed pilot plan', 'FindingKey': 'synthetic.pilot_plan',
        'Observation': 'The fictional sponsor approved a 150-user pilot, a 28-day usage baseline and a target to reduce weekly meeting follow-up time by 20 percent.',
        'Recommendation': '', 'Status': 'Success', 'Priority': 'Low', 'Disposition': 'Assurance',
        'EvidenceKey': 'ai_usage_detail', 'EvidenceBasis': 'Reviewed pilot plan',
        'EvidenceAvailable': 'Yes', 'Confidence': 'High', 'EvidenceComplete': True,
        'ObservationDate': '2026-09-14', 'EvidenceScope': 'All 150 fictional pilot users', 'TenantId': SYNTHETIC_TENANT,
    })
    return save_collection(
        directory / 'tenant-collection.json', tenant_id=SYNTHETIC_TENANT,
        tenant_name='Synthetic validation tenant - invented data', service_results=results,
        collected_at='2026-09-14T12:00:00+00:00', evaluation_date='2026-09-15',
        enabled_collectors=['M365', 'Entra', 'Purview', 'Defender'],
        assessment_settings={'report_format': 'excel', 'data_exposure_enabled': True,
                             'provider_evidence_max_age_days': 90, 'sam_report_max_age_days': 35, 'dspm_report_max_age_days': 8},
        supplemental_inputs={'reports_dir': [exports], 'assessment_profile': profile},
    )

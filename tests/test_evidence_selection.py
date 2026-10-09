"""Shared evidence selection retains named findings and independently addressable source rows."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from Core.evidence_selection import build_evidence_selection


TENANT = '11111111-1111-4111-8111-111111111111'
OTHER_TENANT = '22222222-2222-4222-8222-222222222222'
DAY = '2026-09-15'


def dataset(records, **metadata):
    return [{'records': records, 'source': {
        'available': True, 'availability_status': 'available', 'complete': True,
        'collected_at': '2026-09-14T12:00:00Z',
        'window_start': '2026-09-07T12:00:00Z',
        'window_end': '2026-09-14T12:00:00Z',
        'scope': 'Invented tenant records', **metadata,
    }}]


def finding(identifier='ENT-005', **attributes):
    return {'RecommendationId': identifier, 'Feature': 'Close MFA registration gaps',
            'Observation': 'One returned registration record is not registered.',
            'Recommendation': 'Review the returned record.', 'Disposition': 'Action',
            'Priority': 'High', 'Service': 'Entra', 'DomainId': 'identity',
            'ControlId': 'IDENTITY-001', 'FindingKey': 'mfa_registration_gap',
            'EvidenceKey': 'mfa_registration_detail', 'Historical': 'No',
            'OwnerRole': 'Identity administrator', 'ObservedAt': '2026-09-14',
            'EvidenceLevel': 'configuration', 'PilotImpact': 'Blocks pilot',
            **attributes}


def assessment(rows=None, tenant=TENANT):
    rows = [finding()] if rows is None else rows
    return {'schema_version': '1.1.0', 'evidence_schema_version': '1.1.0',
            'methodology_version': '4.0.0', 'tenant_id': tenant,
            'evaluation_date': DAY, 'decision': 'Not ready for pilot',
            'rationale': 'Invented evidence requires review.',
            'recommendations': rows, 'actions': rows,
            'counts': {'actions': len(rows), 'remediation': len(rows)},
            'domain_coverage': [{'Check ID': 'IDENTITY.REGISTRATION', 'State': 'collected'}],
            'collection_thresholds': {'signin_days': 7, 'device_activity_days': 30},
            'source_windows': [], 'custom_retained_result': {'nested': [1, False, None]}}


def registration_bundle(**metadata):
    return {'assessment_sources': {
        'auth_methods': dataset([
            {'id': 'user-a', 'userPrincipalName': 'alex@example.invalid',
             'userDisplayName': 'Fictional Alex', 'isMfaRegistered': False,
             'defaultMfaMethod': 'voiceMobile', 'isAdmin': False},
            {'id': 'user-b', 'userPrincipalName': 'registered@example.invalid',
             'userDisplayName': 'Fictional Registered', 'isMfaRegistered': True},
            {'id': 'user-c', 'userPrincipalName': 'unknown@example.invalid',
             'userDisplayName': 'Fictional Unknown'},
        ], **metadata),
        'users': dataset([
            {'id': 'user-a', 'displayName': 'Fictional Alex',
             'userPrincipalName': 'alex@example.invalid',
             'assignedLicenses': [{'skuId': 'fictional-license'}],
             'signInActivity': {'lastSignInDateTime': '2026-09-13T16:00:00Z'}},
        ]),
    }}


class EvidenceSelectionTests(unittest.TestCase):
    def build(self, result=None, bundle=None, **options):
        return build_evidence_selection(
            result or assessment(), bundle if bundle is not None else registration_bundle(),
            tenant_name='Fictional tenant', generated_at='2026-09-15T12:00:00Z', **options)


    def test_normalized_findings_use_canonical_observation_date_and_nine_domain_assignment(self):
        row = finding('POLICY-001', ObservationDate='2026-09-13', ObservedAt='',
                      DomainId='data_protection', AssessmentDomainId='classification',
                      Feature='Review DLP enforcement', EvidenceKey='', FindingKey='dlp_review',
                      InvestigationStatus='Unavailable',
                      InvestigationQualification='No retained operational test records.')
        normalized = self.build(result=assessment([row]), bundle={})['findings'][0]
        self.assertEqual(normalized['observed_at'], '2026-09-13')
        self.assertEqual(normalized['domain_id'], 'classification')

    def test_mfa_named_records_are_selected_from_explicit_false_registration(self):
        exported = self.build()
        row = exported['recommendations'][0]
        self.assertEqual(row['record_count'], 1)
        record = row['records'][0]
        fields = record['fields']
        self.assertEqual(fields['upn'], 'alex@example.invalid')
        self.assertEqual(fields['displayName'], 'Fictional Alex')
        self.assertIs(fields['isMfaRegistered'], False)
        self.assertIs(fields['isAdmin'], False)
        self.assertIs(fields['isLicensed'], True)
        self.assertEqual(fields['defaultMfaMethod'], 'voiceMobile')
        self.assertEqual(fields['lastSignIn'], '2026-09-13T16:00:00Z')
        self.assertTrue(row['record_selection'])
        self.assertTrue(record['record_id'])
        self.assertEqual(exported['findings'][0]['records'], row['records'])
        self.assertNotIn('registered@example.invalid', json.dumps(row['records']))
        self.assertNotIn('unknown@example.invalid', json.dumps(row['records']))

    def test_every_named_record_links_to_the_exact_retained_native_rows(self):
        exported = self.build()
        evidence = {row['record_id']: row for row in exported['evidence_records']}
        sources = {row['dataset_id']: row for row in exported['sources']}
        self.assertEqual(len(evidence), len(exported['evidence_records']))
        for row in exported['recommendations']:
            self.assertEqual(row['record_count'], len(row['records']))
            self.assertEqual(len({record['record_id'] for record in row['records']}), len(row['records']))
            for record in row['records']:
                self.assertTrue(record['evidence_record_ids'])
                for identifier in record['evidence_record_ids']:
                    self.assertIn(identifier, evidence)
                for ref in record['source_refs']:
                    matches = [raw for raw in evidence.values()
                               if raw['dataset'] == ref['dataset']
                               and sources[raw['dataset_id']]['dataset_index'] == ref['dataset_index']
                               and raw['source_record_index'] == ref['record_index']]
                    self.assertEqual(len(matches), 1)
                    self.assertIn(matches[0]['record_id'], record['evidence_record_ids'])
        selected = exported['recommendations'][0]['records'][0]
        linked = [evidence[identifier] for identifier in selected['evidence_record_ids']]
        self.assertTrue(any(row['dataset'] == 'auth_methods' and row['raw']['id'] == 'user-a' for row in linked))
        self.assertTrue(any(row['dataset'] == 'users' and row['raw']['id'] == 'user-a' for row in linked))

    def test_source_metadata_and_complete_native_nested_fields_survive_export(self):
        bundle = registration_bundle(availability_status='partial', complete=False,
                                     pages_collected=2, partial_errors=['HTTP 403 on page three'])
        large = 'Retained Unicode \u03bb ' * 3000
        bundle['assessment_sources']['auth_methods'][0]['records'][0]['nested'] = {
            'details': {'longText': large, 'array': [False, 0, None, {'exact': 'value'}]}}
        exported = self.build(bundle=bundle)
        raw = next(row for row in exported['evidence_records']
                   if row['dataset'] == 'auth_methods' and row['source_record_index'] == 0)
        self.assertEqual(raw['raw']['nested']['details']['longText'], large)
        self.assertEqual(raw['raw']['nested']['details']['array'], [False, 0, None, {'exact': 'value'}])
        self.assertFalse(any('continuation' in key.lower() for key in raw['raw']))
        source = next(row for row in exported['sources'] if row['dataset_id'] == raw['dataset_id'])
        self.assertEqual(source['source']['window_start'], '2026-09-07T12:00:00Z')
        self.assertEqual(source['source']['window_end'], '2026-09-14T12:00:00Z')
        self.assertFalse(source['source']['complete'])
        self.assertEqual(source['source']['partial_errors'], ['HTTP 403 on page three'])
        self.assertTrue(exported['recommendations'][0]['record_limitations'])

    def test_duplicate_source_rows_are_retained_as_independent_occurrences(self):
        bundle = registration_bundle()
        raw = bundle['assessment_sources']['auth_methods'][0]['records'][0]
        bundle['assessment_sources']['auth_methods'][0]['records'] = [raw, copy.deepcopy(raw)]
        exported = self.build(bundle=bundle)
        raws = [row for row in exported['evidence_records'] if row['dataset'] == 'auth_methods']
        self.assertEqual(len(raws), 2)
        self.assertNotEqual(raws[0]['record_id'], raws[1]['record_id'])
        self.assertEqual(raws[0]['raw'], raws[1]['raw'])
        self.assertEqual([row['source_record_index'] for row in raws], [0, 1])
        named = exported['recommendations'][0]['records']
        self.assertEqual(len({record['record_id'] for record in named}), len(named))

    def test_ids_are_repeatable_and_tenant_scoped(self):
        first = self.build()
        again = build_evidence_selection(assessment(), registration_bundle(),
                                       generated_at='2026-10-01T00:00:00Z')
        different = self.build(result=assessment(tenant=OTHER_TENANT))
        self.assertEqual([row['record_id'] for row in first['evidence_records']],
                         [row['record_id'] for row in again['evidence_records']])
        self.assertTrue(set(row['record_id'] for row in first['evidence_records']).isdisjoint(
            row['record_id'] for row in different['evidence_records']))

    def test_missing_optional_fields_are_null_with_an_explanation(self):
        bundle = registration_bundle()
        del bundle['assessment_sources']['auth_methods'][0]['records'][0]['defaultMfaMethod']
        del bundle['assessment_sources']['users']
        exported = self.build(bundle=bundle)
        record = exported['recommendations'][0]['records'][0]
        for field in ('defaultMfaMethod', 'isLicensed', 'lastSignIn'):
            self.assertIsNone(record['fields'][field])
            self.assertTrue(record['field_status'][field]['status'])
            self.assertTrue(record['field_status'][field]['reason'])

    def test_no_records_distinguishes_successful_empty_query_from_missing_permission(self):
        empty = self.build(bundle={'assessment_sources': {'auth_methods': dataset([])}})
        unavailable = self.build(bundle={'assessment_sources': {'auth_methods': dataset(
            [], available=False, availability_status='unavailable', complete=False,
            reason='HTTP 403; missing Reports.Read.All permission')}})
        left, right = empty['recommendations'][0], unavailable['recommendations'][0]
        self.assertEqual(left['record_count'], 0)
        self.assertEqual(right['record_count'], 0)
        self.assertNotEqual(left['record_status'], right['record_status'])
        self.assertTrue(left['record_limitations'])
        self.assertIn('403', json.dumps(right['record_limitations']))
        self.assertNotEqual(right['record_status'], 'absence')

    def test_historical_and_planning_findings_do_not_acquire_current_names(self):
        historical = finding(Historical='Yes', InvestigationStatus='Historical finding',
                             InvestigationQualification='Earlier summary did not retain named registration records.')
        planning = finding('PLAN-001', Feature='Approve the pilot charter', EvidenceKey='',
                           FindingKey='planning', Disposition='Coverage',
                           InvestigationStatus='Planning input',
                           InvestigationQualification='A dated sponsor decision is required; no user population was asserted.')
        exported = self.build(result=assessment([historical, planning]))
        for row in exported['recommendations']:
            self.assertEqual(row['record_count'], 0)
            self.assertFalse(row['records'])
            self.assertTrue(row['record_limitations'])
        self.assertNotIn('alex@example.invalid', json.dumps(exported['findings']))

    def test_sensitive_content_is_removed_at_all_json_levels(self):
        bundle = registration_bundle()
        bundle['assessment_sources']['auth_methods'][0]['records'][0]['nested'] = {
            'clientSecret': 'DO_NOT_EXPORT_SECRET', 'access_token': 'DO_NOT_EXPORT_TOKEN',
            'token': 'DO_NOT_EXPORT_BEARER_TOKEN',
            'promptText': 'DO_NOT_EXPORT_PROMPT', 'responseContent': 'DO_NOT_EXPORT_RESPONSE',
            'events': [{'password': 'DO_NOT_EXPORT_PASSWORD', 'id': 'keep-event-id'}],
            'publicMetadata': {'operation': 'CopilotInteraction', 'when': '2026-09-14'}}
        bundle['assessment_sources']['auth_methods'][0]['source']['authorization'] = 'DO_NOT_EXPORT_HEADER'
        exported = self.build(bundle=bundle)
        text = json.dumps(exported)
        self.assertFalse('DO_NOT_EXPORT' in text, 'Secrets or prompt/response bodies survived JSON sanitization.')
        self.assertIn('keep-event-id', text)
        self.assertIn('CopilotInteraction', text)
        self.assertIn('alex@example.invalid', text)

    def test_selection_is_read_only_and_preserves_unicode_nested_records(self):
        result, bundle = assessment(), registration_bundle()
        bundle['assessment_sources']['auth_methods'][0]['records'][0]['userDisplayName'] = 'Fictional Jos?'
        originals = copy.deepcopy((result, bundle))
        selected = self.build(result, bundle)
        self.assertEqual((result, bundle), originals)
        self.assertIn('Fictional Jos?', json.dumps(selected, ensure_ascii=False))

    def test_other_findings_can_expose_exact_selected_policy_rows_without_cloning_the_inventory(self):
        policy = {'id': 'policy-a', 'displayName': 'Fictional policy',
                  'state': 'enabled', 'grantControls': {'operator': 'OR', 'builtInControls': ['mfa']}}
        bundle = {'assessment_sources': {'ca_policies': dataset([
            policy, {'id': 'policy-unselected', 'displayName': 'Unselected policy', 'state': 'disabled'}])},
            'sheets': {'conditional_access_detail': {'title': 'Conditional Access Detail', 'rows': [policy]}}}
        row = finding('CUSTOM-CA', Feature='Review the selected Conditional Access policy',
                      EvidenceKey='conditional_access_detail', FindingKey='ca_selected',
                      InvestigationRange="'Conditional Access Detail'!A2:D2",
                      InvestigationRanges=["'Conditional Access Detail'!A2:D2"],
                      InvestigationStatus='Records available', InvestigationCount=1,
                      InvestigationQualification='One policy selected by its retained ID.')
        exported = self.build(result=assessment([row]), bundle=bundle)
        detail = exported['recommendations'][0]
        self.assertEqual(detail['record_count'], 1)
        self.assertTrue(detail['records'][0]['evidence_record_ids'])
        self.assertIn('policy-a', json.dumps(detail['records']))
        self.assertNotIn('policy-unselected', json.dumps(detail['records']))

    def test_legacy_summary_evidence_is_labelled_without_manufacturing_named_rows(self):
        row = finding('LEGACY-COUNT', Feature='Review earlier registration summary',
                      EvidenceKey='', FindingKey='legacy_registration_summary',
                      InvestigationRange="'Authentication Summary'!A2:B2",
                      InvestigationStatus='Summary evidence',
                      InvestigationQualification='Only a metric row was retained; individual registration records were not saved.')
        bundle = {'assessment_sources': {}, 'sheets': {'legacy_auth_summary': {
            'title': 'Authentication Summary', 'rows': [{'Metric': 'Users not registered', 'Value': 42}]}}}
        detail = self.build(result=assessment([row]), bundle=bundle)['recommendations'][0]
        self.assertEqual(detail['record_status'], 'summary_only')
        self.assertEqual(detail['record_count'], 1)
        self.assertEqual(detail['records'][0]['fields']['Value'], 42)
        self.assertNotIn('upn', detail['records'][0]['fields'])
        self.assertTrue(detail['record_limitations'])


class NamedFindingDetailTests(unittest.TestCase):
    """Requested dashboard columns come from records, never from prose counts."""

    def export_row(self, identifier, sources, **attributes):
        row = finding(identifier, **attributes)
        return build_evidence_selection(assessment([row]), {'assessment_sources': sources},
                                      generated_at='2026-09-15T12:00:00Z')['recommendations'][0]

    def test_standing_role_assignments_join_principal_and_role_ids(self):
        row = self.export_row('ENT-011', {
            'role_assignment_schedules': dataset([{
                'id': 'assignment-a', 'principalId': 'user-a',
                'roleDefinitionId': 'role-a', 'directoryScopeId': '/',
                'assignmentType': 'Assigned',
                'status': 'Provisioned',
                'roleDefinition': {'id': 'role-a', 'displayName': 'Global Administrator'},
                'scheduleInfo': {'startDateTime': '2026-08-01T09:00:00Z',
                                 'expiration': {'type': 'noExpiration'}}}]),
            'users': dataset([{'id': 'user-a', 'displayName': 'Fictional operator',
                               'userPrincipalName': 'operator@example.invalid'}]),
        }, Feature='Reduce standing admin access', EvidenceKey='admin_role_detail',
           FindingKey='synthetic.standing_admin', Observation='1 active directory role assignment schedule have no expiration')
        self.assertEqual(row['record_count'], 1)
        fields = row['records'][0]['fields']
        self.assertEqual(fields['principalDisplayName'], 'Fictional operator')
        self.assertEqual(fields['upn'], 'operator@example.invalid')
        self.assertEqual(fields['roleName'], 'Global Administrator')
        self.assertEqual(fields['scope'], '/')
        self.assertTrue(fields['assignmentType'])
        self.assertEqual(fields['activatedSince'], '2026-08-01T09:00:00Z')

    def test_authenticator_user_preference_is_not_claimed_as_effective_default_when_system_preference_is_enabled(self):
        row = self.export_row('ENT-005', {'auth_methods': dataset([{
            'id': 'user-a', 'userPrincipalName': 'alex@example.invalid', 'isMfaRegistered': False,
            'isSystemPreferredAuthenticationMethodEnabled': True,
            'userPreferredMethodForSecondaryAuthentication': 'push',
            'systemPreferredAuthenticationMethods': ['fido2'],
            'methodsRegistered': ['microsoftAuthenticatorPush']}])})
        self.assertEqual(row['record_count'], 1)
        record = row['records'][0]
        self.assertIsNone(record['fields']['defaultMfaMethod'])
        self.assertTrue(record['field_status']['defaultMfaMethod']['reason'])
        self.assertNotIn('passwordless', str(record['fields']['defaultMfaMethod']).lower())

    def test_user_signin_activity_keeps_successful_and_attempt_dates_separate(self):
        row = self.export_row('ENT-005', {
            'auth_methods': dataset([{'id': 'user-a', 'userPrincipalName': 'alex@example.invalid',
                                      'isMfaRegistered': False}]),
            'user_signin_activity': dataset([{
                'id': 'user-a', 'userPrincipalName': 'alex@example.invalid',
                'signInActivity': {'lastSuccessfulSignInDateTime': '2026-09-13T10:00:00Z',
                                   'lastSignInDateTime': '2026-09-14T10:00:00Z'}},
                {'id': 'different-user', 'displayName': 'Fictional Alex',
                 'signInActivity': {'lastSignInDateTime': '2026-09-15T10:00:00Z'}}]),
        })
        self.assertEqual(row['record_count'], 1)
        fields = row['records'][0]['fields']
        self.assertEqual(fields['lastSignIn'], '2026-09-14T10:00:00Z')
        self.assertEqual(fields['lastSuccessfulSignIn'], '2026-09-13T10:00:00Z')
        self.assertEqual(fields['lastInteractiveSignInAttempt'], '2026-09-14T10:00:00Z')
        self.assertTrue(fields['lastSignInBasis'])

    def test_risky_users_keep_status_detail_and_join_activity_location(self):
        row = self.export_row('ENT-013', {
            'risky_users': dataset([{'id': 'user-a', 'userDisplayName': 'Fictional Alex',
                                    'userPrincipalName': 'alex@example.invalid',
                                    'riskLevel': 'medium', 'riskState': 'atRisk',
                                    'riskDetail': 'none',
                                    'riskLastUpdatedDateTime': '2026-09-13T11:00:00Z'},
                                   {'id': 'user-safe', 'userPrincipalName': 'safe@example.invalid',
                                    'riskLevel': 'none', 'riskState': 'dismissed'}]),
            'risk_detections': dataset([{'id': 'detection-a', 'userId': 'user-a',
                                        'activityDateTime': '2026-09-13T10:30:00Z',
                                        'location': {'city': 'Fictional city', 'countryOrRegion': 'US'}}]),
        }, Feature='Review risky user accounts', EvidenceKey='risky_user_detail', FindingKey='entra.identity_risk.users')
        self.assertEqual(row['record_count'], 1)
        fields = row['records'][0]['fields']
        self.assertEqual(fields['upn'], 'alex@example.invalid')
        self.assertEqual(fields['displayName'], 'Fictional Alex')
        self.assertEqual(fields['riskLevel'], 'medium')
        self.assertEqual(fields['riskState'], 'atRisk')
        self.assertEqual(fields['riskDetail'], 'none')
        self.assertTrue(fields['lastRiskyActivity'])
        self.assertIn('Fictional city', json.dumps(fields['location']))
        self.assertNotIn('safe@example.invalid', json.dumps(row['records']))

    def test_assigned_consent_policy_definitions_keep_include_and_exclude_conditions(self):
        row = self.export_row('ENT-017', {
            'authorization_policy': dataset([{'id': 'authorization', 'defaultUserRolePermissions': {
                'permissionGrantPoliciesAssigned': ['managePermissionGrantsForSelf.fictional-policy']}}]),
            'consent_policies': dataset([{'id': 'fictional-policy',
                                         'description': 'Synthetic bounded consent policy',
                                         'includes': [{'permissionType': 'delegated',
                                                       'clientApplicationsFromVerifiedPublisherOnly': True}],
                                         'excludes': [{'resourceApplication': 'excluded-resource'}]}]),
        }, Feature='Review user consent policy assignments', EvidenceKey='app_consent_policy_detail',
           FindingKey='entra.apps.user_consent_assignment')
        self.assertEqual(row['record_count'], 1)
        fields = row['records'][0]['fields']
        self.assertEqual(fields['policyId'], 'fictional-policy')
        self.assertEqual(fields['description'], 'Synthetic bounded consent policy')
        self.assertTrue(fields['includeConditions'])
        self.assertTrue(fields['excludeConditions'])
        self.assertIs(fields['publisherVerifiedRequired'], True)
        self.assertTrue(fields['scope'])

    def test_individual_application_grants_keep_exact_scopes_and_consent_principals(self):
        row = self.export_row('ENT-018', {
            'service_principals': dataset([
                {'id': 'client-object', 'appId': 'client-application',
                 'displayName': 'Fictional file app', 'publisherName': 'Fictional Publisher',
                 'verifiedPublisher': {'displayName': 'Fictional Publisher', 'verifiedPublisherId': 'verified'}},
                {'id': 'resource-object', 'appId': 'resource-application', 'displayName': 'Fictional API'}]),
            'oauth_grants': dataset([{'id': 'grant-a', 'clientId': 'client-object',
                                      'resourceId': 'resource-object', 'consentType': 'Principal',
                                      'principalId': 'user-a', 'scope': 'Files.ReadWrite.All User.Read'}]),
            'users': dataset([{'id': 'user-a', 'displayName': 'Fictional Alex',
                               'userPrincipalName': 'alex@example.invalid'}]),
        }, Feature='Review application grants and permissions', EvidenceKey='app_access_detail',
           FindingKey='entra.app_consent.high_impact_grants')
        self.assertEqual(row['record_count'], 1)
        fields = row['records'][0]['fields']
        self.assertEqual(fields['appDisplayName'], 'Fictional file app')
        self.assertEqual(fields['publisher'], 'Fictional Publisher')
        self.assertIs(fields['publisherVerified'], True)
        self.assertTrue(fields['grantType'])
        scopes = fields['exactScopes']
        if isinstance(scopes, str):
            scopes = scopes.split()
        self.assertEqual(set(scopes), {'Files.ReadWrite.All', 'User.Read'})
        self.assertIn('alex@example.invalid', json.dumps(fields['consentingPrincipals']))
        self.assertIsNone(fields['lastUsed'])
        self.assertTrue(row['records'][0]['field_status']['lastUsed']['reason'])

    def test_application_permissions_resolve_exact_resource_role_without_using_display_names(self):
        row = self.export_row('ENT-018', {
            'service_principals': dataset([
                {'id': 'client-object', 'appId': 'client-application', 'displayName': 'Fictional app',
                 'appOwnerOrganizationId': 'external-owner', 'publisherName': 'Unverified external publisher'},
                {'id': 'resource-object', 'displayName': 'Fictional API',
                 'appRoles': [{'id': 'role-readwrite', 'value': 'Files.ReadWrite.All', 'displayName': 'Read-write files'}]}]),
            'application_permissions': dataset([{'id': 'assignment-a', 'principalId': 'client-object',
                                                 'resourceId': 'resource-object', 'appRoleId': 'role-readwrite'}]),
        }, Feature='Review application grants and permissions', EvidenceKey='app_access_detail',
           FindingKey='entra.app_consent.high_impact_grants')
        self.assertEqual(row['record_count'], 1)
        fields = row['records'][0]['fields']
        self.assertEqual(fields['grantType'], 'application')
        self.assertEqual(fields['exactScopes'], ['Files.ReadWrite.All'])
        self.assertEqual(fields['appDisplayName'], 'Fictional app')
        self.assertIs(fields['publisherVerified'], False)
        self.assertIsNone(fields['approvedBy'])
        self.assertEqual(row['record_reconciliation']['distinct_applications'], 1)
        self.assertEqual(row['record_reconciliation']['high_privilege_applications'], 1)
        self.assertEqual(row['record_reconciliation']['unverified_publisher_applications'], 1)
        self.assertEqual(row['record_reconciliation']['application_overlap'], 1)

    def test_active_incidents_exclude_resolved_incidents_and_keep_owner_and_category(self):
        row = self.export_row('DEF-011', {
            'incidents': dataset([{'id': 'incident-a', 'severity': 'high', 'title': 'Fictional incident',
                                  'status': 'active', 'classification': 'truePositive',
                                  'createdDateTime': '2026-09-13T12:00:00Z',
                                  'assignedTo': 'response-owner@example.invalid', 'category': 'InitialAccess'},
                                 {'id': 'incident-resolved', 'status': 'resolved', 'severity': 'low'}]),
        }, Feature='Review active security incidents', EvidenceKey='defender_incident_detail',
           FindingKey='defender.incidents.current', Service='Defender')
        self.assertEqual(row['record_count'], 1)
        fields = row['records'][0]['fields']
        self.assertEqual(fields['incidentId'], 'incident-a')
        self.assertEqual(fields['severity'], 'high')
        self.assertEqual(fields['title'], 'Fictional incident')
        self.assertEqual(fields['status'], 'active')
        self.assertEqual(fields['classification'], 'truePositive')
        self.assertEqual(fields['firstSeen'], '2026-09-13T12:00:00Z')
        self.assertEqual(fields['assignedTo'], 'response-owner@example.invalid')
        self.assertIn('InitialAccess', json.dumps(fields['category']))

    def test_sharing_summary_rows_keep_count_units_without_inventing_file_records(self):
        row = self.export_row('DEX-001', {
            'content_exposure': dataset([{
                'Site URL': 'https://synthetic.sharepoint.com/sites/fictional',
                'Workload': 'SharePoint', 'Organization link count': 5,
                'EEEU permission count': 2, 'Number of users having access': 8,
                'Report Date': '2026-09-14', '_report_type': 'sam_permissions'}]),
        }, Feature='Review org-wide links and group permissions', EvidenceKey='data_exposure_detail',
           FindingKey='data_exposure.broad_internal_access', Service='DataExposure')
        self.assertEqual(row['record_count'], 2)
        records = row['records']
        for record in records:
            fields = record['fields']
            self.assertEqual(fields['siteUrl'], 'https://synthetic.sharepoint.com/sites/fictional')
            self.assertEqual(fields['workload'], 'SharePoint')
            self.assertEqual(fields['permissionedUserCount'], 8)
            self.assertEqual(fields['recordGranularity'], 'site_summary')
            self.assertIsNone(fields['lastModified'])
        self.assertEqual({record['fields']['countUnit'] for record in records}, {'links', 'permissions'})
        self.assertEqual(sorted(record['fields']['exposureCount'] for record in records), [2, 5])

    def test_external_sharing_keeps_site_settings_separate_from_tenant_defaults(self):
        row = self.export_row('M365-123', {
            'sharepoint_tenant_settings': dataset([{
                'SharingCapability': 'ExternalUserAndGuestSharing',
                'DefaultSharingLinkType': 'Direct', 'RequireAnonymousLinksExpireInDays': 5}]),
            'sharepoint_site_settings': dataset([{
                'Url': 'https://synthetic.sharepoint.com/sites/fictional',
                'SharingCapability': 'ExternalUserAndGuestSharing',
                'DefaultSharingLinkType': 'AnonymousAccess', 'AnonymousLinkExpirationInDays': 3}]),
        }, Feature='SharePoint and OneDrive external sharing', EvidenceKey='sharepoint_governance_detail',
           FindingKey='sharepoint.sharing.organization_default', Service='M365')
        site_records = [record for record in row['records'] if record['fields'].get('siteUrl')]
        self.assertEqual(len(site_records), 1)
        fields = site_records[0]['fields']
        self.assertEqual(fields['siteUrl'], 'https://synthetic.sharepoint.com/sites/fictional')
        self.assertEqual(fields['sharingSetting'], 'ExternalUserAndGuestSharing')
        self.assertEqual(fields['defaultLinkType'], 'AnonymousAccess')
        self.assertEqual(fields['anyoneLinkExpiryDays'], 3)
        self.assertIn('externalGuestsAllowed', fields)

    def test_antivirus_records_join_stable_device_ids_instead_of_display_names(self):
        row = self.export_row('REC-5F72F4103A', {
            'antivirus_health': dataset([{
                'machineId': 'device-a', 'avIsSignatureUpToDate': False,
                'avSignatureVersion': '1.2.3', 'avSignatureUpdateTime': '2026-09-01T12:00:00Z',
                'dataRefreshTimestamp': '2026-09-14T12:00:00Z'},
                {'machineId': 'device-healthy', 'avIsSignatureUpToDate': True}]),
            'machines': dataset([{'id': 'device-a', 'computerDnsName': 'fictional-device',
                                  'osPlatform': 'Windows11', 'lastSeen': '2026-09-14T10:00:00Z'},
                                 {'id': 'different-device', 'computerDnsName': 'fictional-device',
                                  'osPlatform': 'Linux', 'lastSeen': '2026-09-12T10:00:00Z'}]),
        }, Feature='Update observed antivirus signatures', EvidenceKey='defender_device_detail',
           FindingKey='expanded.antivirus_health.0.outdated.date0', Service='Defender')
        self.assertEqual(row['record_count'], 1)
        fields = row['records'][0]['fields']
        self.assertEqual(fields['deviceName'], 'fictional-device')
        self.assertEqual(fields['deviceId'], 'device-a')
        self.assertEqual(fields['osPlatform'], 'Windows11')
        self.assertEqual(fields['signatureVersion'], '1.2.3')
        self.assertEqual(fields['lastSeen'], '2026-09-14T10:00:00Z')
        self.assertEqual(fields['signatureAgeDays'], 13)

    def test_antivirus_detail_respects_the_findings_declared_observation_date_group(self):
        selected = {'machineId': 'device-a', 'avIsSignatureUpToDate': False,
                    'avSignatureVersion': 'older-signature',
                    'dataRefreshTimestamp': '2026-09-01T12:00:00Z'}
        other = {'machineId': 'device-b', 'avIsSignatureUpToDate': False,
                 'avSignatureVersion': 'newer-signature',
                 'dataRefreshTimestamp': '2026-09-14T12:00:00Z'}
        row = self.export_row('REC-AV-DATE-GROUP', {'antivirus_health': dataset([selected, other])},
                              Feature='Update observed antivirus signatures',
                              EvidenceKey='defender_device_detail',
                              FindingKey='expanded.antivirus_health.0.outdated.date0',
                              ObservationDate='2026-09-01',
                              InvestigationEvidence={'kind': 'records', 'records': [selected],
                                                     'reason': 'The dated finding selected this retained report row.'})
        self.assertEqual(row['record_count'], 1)
        self.assertEqual(row['records'][0]['fields']['deviceId'], 'device-a')
        self.assertEqual(row['records'][0]['fields']['signatureVersion'], 'older-signature')
        self.assertEqual(row['records'][0]['fields']['reportDate'], '2026-09-01T12:00:00Z')

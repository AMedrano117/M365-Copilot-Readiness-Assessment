import os
import io
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from tests.workbook_test_helpers import load_workbook_pair as load_workbook

from Core.evidence_layer import (
    _apply_explicit_evidence_fallbacks,
    _build_legacy_signin_sheet,
    build_evidence_bundle,
)
from Core.export_recommendations import export_to_excel, export_to_html
from Core.customer_report import _heading
from Core.saved_control_checks import qualify_saved_recommendations


class LegacySignInEvidenceTests(unittest.TestCase):
    def test_live_legacy_finding_is_named_for_the_issue_and_preserves_license_observation(self):
        from Recommendations.entra import AAD_PREMIUM, AAD_PREMIUM_P1
        for module in (AAD_PREMIUM, AAD_PREMIUM_P1):
            for count in (8, 0):
                with self.subTest(module=module.__name__, count=count), redirect_stdout(io.StringIO()):
                    rows = module.get_recommendation('SPE_E3', entra_insights={
                        'available': True, 'ca_metrics': {'total_policies': 1, 'require_mfa': 1},
                        'mfa_metrics': {'total_users': 1, 'mfa_enabled_users': 1},
                        'signin_metrics': {'legacy_auth_sign_ins': count},
                        'auth_summary': {'passwordless_adoption_rate': 100},
                    })
                    legacy = next(row for row in rows if 'legacy authentication sign-in' in row['Observation'])
                    self.assertEqual(legacy['Feature'], 'Legacy authentication sign-ins')
                    self.assertTrue(any(row['Feature'] == 'Microsoft Entra ID P1' for row in rows))
                    if count:
                        self.assertEqual(_heading(legacy), 'Review legacy authentication sign-ins')

    def test_saved_product_label_is_corrected_in_export_and_retained_as_original(self):
        client = self.client()
        old = {**self.finding(), 'Feature': 'Microsoft Entra ID P1', 'FindingKey': 'entra.signins.legacy_auth'}
        qualified = qualify_saved_recommendations([old], 'Entra', client)[0]
        self.assertEqual(qualified['Feature'], 'Legacy authentication sign-ins')
        self.assertEqual(qualified['OriginalFeature'], 'Microsoft Entra ID P1')
        self.assertEqual(old['Feature'], 'Microsoft Entra ID P1')
        self.assertEqual(_heading(old), 'Review legacy authentication sign-ins')
        bundle = self.bundle(client, [qualified])
        bundle['evaluation_date'] = '2026-09-30'
        bundle['collection_context'] = {'collected_at': '2026-09-29T08:00:00Z',
                                        'tenant_id': '11111111-1111-1111-1111-111111111111'}
        with TemporaryDirectory() as temporary:
            path = export_to_excel(bundle['recommendations'], filename='legacy.xlsx', evidence_bundle=bundle,
                                   output_dir=temporary)
            workbook = load_workbook(path)
            try:
                findings = list(workbook['Findings'].values)
                columns = {name: number for number, name in enumerate(findings[0])}
                row = next(row for row in findings[1:] if row[columns['ID']] == 'ENT-001')
                self.assertEqual(row[columns['Finding']], 'Review legacy authentication sign-ins')
            finally:
                workbook.close()
            html_path = export_to_html(bundle['recommendations'], filename='legacy.html', evidence_bundle=bundle,
                                       excel_path=path, output_dir=temporary)
            html = Path(html_path).read_text(encoding='utf-8')
            self.assertIn('<h3>Review legacy authentication sign-ins</h3>', html)

    def finding(self, count=8):
        return {
            'RecommendationId': 'ENT-001', 'Service': 'Entra', 'Feature': 'Legacy sign-in review',
            'Status': 'Action Required', 'Priority': 'High',
            'Observation': f'{count} legacy authentication sign-ins detected in the returned sign-in records.',
            'Recommendation': 'Investigate each matching sign-in event and its outcome.',
            'ObservationDate': '2026-09-29', 'EvidenceComplete': True,
            'EvidenceScope': 'The retained sign-in query population',
            'EvidenceSource': 'signin_logs', 'EvidenceBasis': 'Tenant evidence',
            'TenantId': '11111111-1111-1111-1111-111111111111',
        }

    def client(self):
        clients = ['IMAP4', 'POP3', 'Authenticated SMTP', 'Exchange ActiveSync',
                   'Other clients', 'Exchange Web Services', 'imap', 'SMTP']
        records = [{
            'id': f'event-{index}', 'userId': 'user-object-1', 'userPrincipalName': 'private-user@example.invalid',
            'userDisplayName': 'Private User', 'appId': 'application-object-1', 'appDisplayName': 'Private Application',
            'servicePrincipalId': 'service-principal-object-1', 'servicePrincipalName': 'Private SMTP Principal',
            'resourceId': 'resource-object-1', 'resourceDisplayName': 'Private Resource',
            'clientAppUsed': client, 'authenticationProtocol': 'ropc',
            'userAgent': 'Private SMTP Agent/1.0',
            'createdDateTime': '2026-09-28T07:30:00+02:00', 'ipAddress': '192.0.2.47',
            'location': {'city': 'Example City', 'state': 'Example State', 'countryOrRegion': 'US'},
            'deviceDetail': {'deviceId': 'device-object-1', 'displayName': 'Private Device',
                             'operatingSystem': 'Windows', 'browser': 'Legacy client'},
            'status': {'errorCode': 0, 'failureReason': 'None'},
            'conditionalAccessStatus': 'notApplied', 'isInteractive': False,
            'correlationId': f'correlation-{index}',
        } for index, client in enumerate(clients, 1)]
        records[1]['status'] = {'errorCode': 50076, 'failureReason': 'Additional authentication required',
                                'additionalDetails': 'A second-factor challenge was required'}
        records[1]['conditionalAccessStatus'] = 'failure'
        records[2]['status'] = {}
        records.append({**records[0], 'id': 'modern-event', 'clientAppUsed': 'Browser'})
        records.append({'id': 'unclassified-event'})
        state = {
            'available': True, 'availability_status': 'available', 'records_collected': 10,
            'pages_collected': 3, 'truncated': False,
            'source_api': 'https://graph.microsoft.com/v1.0/auditLogs/signIns',
            'source_file': 'original-collection.json', 'collected_at': '2026-09-29T08:00:00Z',
            'collection_window': 'createdDateTime >= 2026-09-22T08:00:00Z; no upper bound sent',
            'filters': {'$filter': 'createdDateTime ge 2026-09-22T08:00:00Z'},
            'scope': 'User sign-in events returned by the source endpoint',
            'pagination': 'All returned nextLink pages retained',
        }
        return SimpleNamespace(signin_logs=records, signin_summary={'legacy_auth_attempts': 8},
                               collection_status={'signin_logs': state}, data_sources={'signin_logs': True})

    def bundle(self, client, recommendations=None):
        return build_evidence_bundle(recommendations or [self.finding()], ({}, []), {'_client': client}, {}, {}, {}, {},
                                     collection_context={'source_file': 'saved-input.json', 'collected_at': '2026-09-29T08:00:00Z'})

    def test_eight_distinct_events_survive_repeated_users_and_apps_with_correct_outcomes(self):
        sheet = _build_legacy_signin_sheet(self.client(), [self.finding()])
        rows = sheet['rows']
        self.assertEqual(len(rows), 8)
        self.assertEqual({row['Sign-In ID'] for row in rows}, {f'event-{index}' for index in range(1, 9)})
        self.assertEqual(len({row['User ID'] for row in rows}), 1)
        self.assertEqual(len({row['Application ID'] for row in rows}), 1)
        self.assertEqual([row['Outcome'] for row in rows[:3]], ['Succeeded', 'Blocked', 'Unknown'])
        self.assertEqual(rows[0]['Error Code'], 0)
        self.assertEqual(rows[1]['Error Code'], 50076)
        self.assertEqual(rows[1]['Failure Reason'], 'Additional authentication required')
        self.assertEqual(rows[1]['Status Additional Details'], 'A second-factor challenge was required')
        self.assertEqual(rows[0]['User Agent'], 'Private SMTP Agent/1.0')
        self.assertEqual(rows[0]['Service Principal ID'], 'service-principal-object-1')
        self.assertEqual(rows[0]['Service Principal Name'], 'Private SMTP Principal')
        self.assertEqual(rows[0]['Location Country / Region'], 'US')
        self.assertEqual(rows[2]['Error Code'], 'Not returned')
        self.assertEqual(rows[0]['Created UTC'], '2026-09-28T07:30:00+02:00')
        self.assertEqual(rows[0]['Is Interactive'], 'No')
        for field in ('User Principal Name', 'User Display Name', 'Resource ID', 'Resource Name', 'IP Address',
                      'Device ID', 'Device Name', 'Device OS', 'Device Browser', 'Correlation ID', 'Authentication Protocol'):
            self.assertNotEqual(rows[0][field], 'Not returned', field)
        self.assertEqual(rows[1]['Conditional Access Status'], 'failure')
        self.assertEqual(rows[0]['Source File'], 'original-collection.json')
        self.assertIn('2026-09-22', rows[0]['Collection Window'])
        self.assertEqual(rows[0]['Pages Collected'], 3)
        self.assertEqual(sheet['reported_count'], 8)
        self.assertEqual(sheet['matched_count'], 8)
        self.assertTrue(sheet['reconciliation_note'].startswith('Matched:'))
        self.assertEqual(sheet['unavailability_reason'], '')
        self.assertTrue(sheet['restricted'])
        self.assertEqual(sheet['preview_columns'], [])

    def test_sdk_snake_case_fields_and_unknown_result_are_retained_without_assuming_success(self):
        event = SimpleNamespace(id='sdk-event', client_app_used='IMAP', user_id='sdk-user',
            user_principal_name='sdk-private@example.invalid', app_id='sdk-app', app_display_name='SDK App',
            created_date_time=datetime(2026, 9, 29, 7, tzinfo=timezone(timedelta(hours=-5))),
            status=SimpleNamespace(error_code=None, failure_reason='Source did not return an error code'),
            device_detail=SimpleNamespace(device_id='sdk-device', operating_system='Linux'), is_interactive=True)
        client = SimpleNamespace(signin_logs=[event], signin_summary={'legacy_auth_attempts': 1})
        row = _build_legacy_signin_sheet(client, [self.finding(1)],
                                      {'source_file': 'historical-source.json', 'collected_at': '2026-09-29T12:05:00Z'})['rows'][0]
        self.assertEqual(row['Sign-In ID'], 'sdk-event')
        self.assertEqual(row['User ID'], 'sdk-user')
        self.assertEqual(row['Device ID'], 'sdk-device')
        self.assertEqual(row['Created UTC'], '2026-09-29T12:00:00Z')
        self.assertEqual(row['Outcome'], 'Unknown')
        self.assertEqual(row['Is Interactive'], 'Yes')
        self.assertEqual(row['Source File'], 'historical-source.json')
        self.assertEqual(row['Collected At'], '2026-09-29T12:05:00Z')
        self.assertIn('query window not recorded', row['Collection Window'])
        self.assertNotIn('2026-09-29', row['Collection Window'])

    def test_count_mismatch_is_visible_and_missing_events_do_not_create_placeholder_rows(self):
        client = self.client()
        client.signin_logs = client.signin_logs[1:]
        sheet = _build_legacy_signin_sheet(client, [self.finding()])
        self.assertEqual(sheet['matched_count'], 7)
        self.assertIn('Count mismatch: 8', sheet['reconciliation_note'])
        missing = SimpleNamespace(signin_summary={'legacy_auth_attempts': 8})
        bundle = self.bundle(missing)
        detail = bundle['sheets']['legacy_signin_detail']
        self.assertEqual(detail['rows'], [])
        self.assertIn('did not retain signin_logs', detail['unavailability_reason'])
        self.assertEqual(bundle['recommendations'][0]['EvidenceAvailable'], 'No')
        self.assertEqual(bundle['recommendations'][0]['EvidenceSheet'], '')
        self.assertIn('did not retain signin_logs', bundle['recommendations'][0]['EvidenceSummary'])

    def test_collection_failure_reason_and_partial_scope_are_not_reported_as_a_clean_zero(self):
        client = self.client()
        client.signin_logs = []
        client.collection_status['signin_logs'].update(available=False, availability_status='unavailable',
                                                       reason='HTTP 403: source permission was denied')
        sheet = _build_legacy_signin_sheet(client, [self.finding()])
        self.assertEqual(sheet['rows'], [])
        self.assertIn('HTTP 403', sheet['unavailability_reason'])
        self.assertIn('Completeness', sheet['reconciliation_note'])

    def test_only_observed_event_counts_route_to_legacy_detail_not_blocking_policies(self):
        event = {**self.finding(), 'Feature': 'Conditional Access', 'EvidenceKey': 'authentication_detail'}
        policy = {**self.finding(), 'Observation': '8 Conditional Access policies include 2 blocking legacy authentication.',
                  'Feature': 'Conditional Access'}
        routed = _apply_explicit_evidence_fallbacks([event, policy], {
            'legacy_signin_detail': {}, 'conditional_access_detail': {}, 'authentication_detail': {},
        })
        self.assertEqual(routed[0]['EvidenceKey'], 'legacy_signin_detail')
        self.assertEqual(routed[1]['EvidenceKey'], 'conditional_access_detail')

    def test_zero_legacy_observation_does_not_create_an_empty_detail_tab(self):
        client = self.client()
        client.signin_logs = []
        client.signin_summary = {'legacy_auth_attempts': 0}
        healthy = {**self.finding(0), 'Status': 'Success', 'Disposition': 'Assurance'}
        bundle = self.bundle(client, [healthy])
        self.assertNotIn('legacy_signin_detail', bundle['sheets'])
        self.assertNotEqual(bundle['recommendations'][0].get('EvidenceKey'), 'legacy_signin_detail')

        # An explicitly keyed finding still retains metadata to explain its
        # discrepancy or unavailable source even when no events survive.
        explicit = {**healthy, 'FindingKey': 'entra.signins.legacy_auth'}
        detail = self.bundle(client, [explicit])['sheets']['legacy_signin_detail']
        self.assertEqual(detail['rows'], [])
        self.assertEqual(detail['reported_count'], 0)

    def test_registration_identities_are_exported_only_for_an_actionable_mfa_registration_finding(self):
        client = self.client()
        client.auth_methods_registration = [
            {'id': 'registered-account', 'userPrincipalName': 'registered@example.invalid', 'isMfaRegistered': True},
            {'id': 'affected-account', 'userPrincipalName': 'affected@example.invalid', 'isMfaRegistered': False},
        ]
        client.auth_summary = {'total_users': 2, 'mfa_registered': 1}
        client.collection_status['auth_methods'] = {'availability_status': 'available', 'available': True, 'truncated': False}
        unrelated = self.bundle(client)
        self.assertNotIn('mfa_registration_detail', unrelated['sheets'])
        self.assertNotIn('affected@example.invalid', str(unrelated['appendix_sections']))
        registration = {**self.finding(), 'Feature': 'MFA registration',
                        'Observation': 'Only 1 of 2 users (50.0%) were enrolled in MFA.',
                        'EvidenceSource': 'auth_methods', 'EvidenceKey': 'authentication_detail'}
        bundle = self.bundle(client, [registration])
        detail = bundle['sheets']['mfa_registration_detail']
        self.assertTrue(detail['restricted'])
        self.assertEqual(detail['preview_columns'], [])
        self.assertEqual(len(detail['rows']), 1)
        self.assertEqual(detail['rows'][0]['User ID'], 'affected-account')
        self.assertIs(detail['rows'][0]['MFA Registered'], False)
        self.assertEqual(set(bundle['recommendations'][0]['EvidenceKey'].split(';')), {'authentication_detail', 'mfa_registration_detail'})
        self.assertNotIn('mfa_registration_detail', {section['key'] for section in bundle['appendix_sections']})
        self.assertNotIn('affected@example.invalid', str(bundle['appendix_sections']))
        healthy = {**registration, 'Status': 'Success', 'Observation': '2 of 2 users (100%) enrolled in MFA.'}
        self.assertNotIn('mfa_registration_detail', self.bundle(client, [healthy])['sheets'])

    def test_restricted_event_fields_are_in_excel_but_absent_from_customer_html(self):
        bundle = self.bundle(self.client())
        self.assertNotIn('legacy_signin_detail', {section['key'] for section in bundle['appendix_sections']})
        bundle['evaluation_date'] = '2026-09-30'
        bundle['collection_context'] = {'collected_at': '2026-09-29T08:00:00Z',
                                        'tenant_id': '11111111-1111-1111-1111-111111111111'}
        previous = Path.cwd()
        with TemporaryDirectory() as temporary:
            try:
                os.chdir(temporary)
                path = export_to_excel(bundle['recommendations'], filename='legacy.xlsx', evidence_bundle=bundle)
                workbook = load_workbook(path)
                try:
                    rows = list(workbook.technical['Legacy Sign-In Detail'].values)
                    self.assertEqual(len(rows), 9)
                    self.assertIn('private-user@example.invalid', rows[1])
                    self.assertIn('event-1', rows[1])
                finally:
                    workbook.close()
                html = Path(export_to_html(bundle['recommendations'], filename='legacy.html', evidence_bundle=bundle,
                                           excel_path=path)).read_text(encoding='utf-8')
                for value in ('private-user@example.invalid', 'Private Application', 'Private Device', '192.0.2.47',
                              'correlation-1', 'Private SMTP Agent/1.0', 'Private SMTP Principal', 'Example City'):
                    self.assertNotIn(value, html)
            finally:
                os.chdir(previous)


if __name__ == '__main__':
    unittest.main()

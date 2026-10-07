"""The shared finding-evidence model: content-based adapters, legacy sign-in events, units and concerns."""

import copy
import unittest

from Core.dashboard_export import build_dashboard_export, evidence_record_ids
from Core.dashboard_records import expand_finding_records
from Core.evidence_layer import SHEET_DEFINITIONS
from Core.finding_evidence import DATASET_KINDS, EVIDENCE_KINDS, build_finding_evidence, concern_for, rows_for_finding
from Core.raw_evidence import KEY_SOURCES
from Core.signin_evidence import classify_signin_outcome, unmatched_legacy_client_type
from Core.technical_guidance import attach_technical_guidance, guidance_for

TENANT = '11111111-1111-4111-8111-111111111111'
REQUIRED_CONTROLS = ('IDENTITY.AUTH', 'IDENTITY.MFA', 'IDENTITY.ADMIN', 'CONTENT.SHARING', 'CONTENT.PERMISSIONS',
                     'CONTENT.OWNERSHIP', 'DATA.LABELS', 'DATA.PUBLISHING', 'DATA.DLP', 'DATA.AUDIT', 'DATA.RETENTION',
                     'DATA.EXPOSURE', 'APPS.CONSENT', 'APPS.CONNECTIONS', 'ENDPOINT.POSTURE', 'THREAT.INCIDENTS',
                     'LICENSE.ASSIGNMENT', 'LICENSE.APPS', 'ADOPTION.BASELINE', 'AGENTS.BOUNDARIES', 'EXTERNAL.SERVICES')


def event(number, client='IMAP4', code=0, access='notApplied', **extra):
    row = {'id': f'event-{number}', 'createdDateTime': f'2026-09-28T07:{number:02d}:00Z', 'userId': f'user-{number % 3}',
           'userPrincipalName': f'user{number % 3}@example.invalid', 'appId': f'app-{number % 2}',
           'appDisplayName': 'Fictional mail', 'clientAppUsed': client, 'ipAddress': '192.0.2.10',
           'conditionalAccessStatus': access, 'correlationId': f'correlation-{number}',
           'status': {} if code is None else {'errorCode': code, 'failureReason': 'reason' if code else None}}
    row.update(extra)
    return row


def source(**metadata):
    return {'available': True, 'availability_status': 'available', 'complete': True, 'collected_at': '2026-09-29T08:00:00Z',
            'window_start': '2026-09-22T08:00:00Z', 'window_end': '2026-09-29T08:00:00Z', **metadata}


def legacy_finding(identifier='ENT-018', **attributes):
    return {'RecommendationId': identifier, 'Service': 'Entra', 'Feature': 'Microsoft Entra ID P1', 'Disposition': 'Action',
            'Priority': 'High', 'FindingKey': 'entra.signins.legacy_auth', 'EvidenceKey': 'legacy_signin_detail',
            'Observation': '5 legacy authentication sign-in attempts detected in the returned sign-in records.',
            'Recommendation': 'Review.', **attributes}


def build(rows, sources):
    result = {'tenant_id': TENANT, 'evaluation_date': '2026-09-30', 'recommendations': rows, 'actions': rows,
              'decision': 'Not ready', 'counts': {}}
    payload = attach_technical_guidance(build_dashboard_export(result, {'assessment_sources': sources, 'sheets': {}},
                                                               tenant_name='Fictional', generated_at='2026-09-30T00:00:00Z'))
    return payload, build_finding_evidence(payload)


LOGS = [event(1), event(2, code=53003, access='failure'), event(3, code=50126), event(4, code=50053),
        event(5, client='POP3', code=None), event(6, client='MAPI Over HTTP'), event(7, client='Browser'),
        event(8, code=50076, access='failure', clientSecret='not-exported')]


class OutcomeClassificationTests(unittest.TestCase):
    def test_every_outcome_path_states_its_basis(self):
        cases = [({'status': {'errorCode': 0}, 'conditionalAccessStatus': 'notApplied'}, 'Succeeded'),
                 ({'status': {'errorCode': 53003}, 'conditionalAccessStatus': 'failure'}, 'Blocked'),
                 ({'status': {'errorCode': 53000}}, 'Blocked'),
                 ({'status': {'errorCode': 50053}}, 'Blocked'),
                 ({'status': {'errorCode': 50076}, 'conditionalAccessStatus': 'failure'}, 'Blocked'),
                 ({'status': {'errorCode': 50126, 'failureReason': 'Invalid username or password.'}}, 'Failed'),
                 ({'status': {}}, 'Unknown'),
                 ({'status': {'errorCode': 'not-a-code'}}, 'Unknown'),
                 ({'status': {'errorCode': 0}, 'conditionalAccessStatus': 'failure'}, 'Unknown')]
        for record, expected in cases:
            with self.subTest(record=record):
                outcome = classify_signin_outcome(record)
                self.assertEqual(outcome['outcome'], expected)
                self.assertIn('status.errorCode=', outcome['basis'])
                self.assertIn('conditionalAccessStatus=', outcome['basis'])
        self.assertIn('Conditional Access', classify_signin_outcome({'status': {'errorCode': 53003}})['detail'])
        self.assertIn('sign-in protection', classify_signin_outcome({'status': {'errorCode': 50053}})['detail'])
        self.assertIn('Invalid username', classify_signin_outcome(cases[5][0])['detail'])

    def test_unmatched_microsoft_legacy_clients_are_named_but_not_classified(self):
        self.assertEqual(unmatched_legacy_client_type({'clientAppUsed': 'MAPI Over HTTP'}), 'MAPI over HTTP')
        self.assertEqual(unmatched_legacy_client_type({'clientAppUsed': 'AutoDiscover'}), 'Autodiscover')
        self.assertIsNone(unmatched_legacy_client_type({'clientAppUsed': 'IMAP4'}))
        self.assertIsNone(unmatched_legacy_client_type({'clientAppUsed': 'Browser'}))


class LegacySignInModelTests(unittest.TestCase):
    def test_positionally_numbered_legacy_finding_gets_native_sign_in_events(self):
        payload, model = build([legacy_finding('ENT-018')], {'signin_logs': [{'records': LOGS, 'source': source()}]})
        finding = model['findings'][0]
        evidence = finding['evidence']
        self.assertEqual(finding['concern_id'], 'legacy-authentication')
        self.assertEqual((evidence['kind'], evidence['availability'], evidence['record_count']), ('observed_event', 'complete', 6))
        self.assertEqual(evidence['record_unit'], 'sign-in events')
        self.assertEqual((evidence['affected_entity_count'], evidence['entity_unit']), (3, 'accounts'))
        self.assertEqual(evidence['outcome_counts'], {'Succeeded': 1, 'Blocked': 3, 'Failed': 1, 'Unknown': 1})
        native = {entry['record_id']: entry for entry in payload['evidence_records']}
        rows = rows_for_finding(model, 'ENT-018')
        for row in rows:
            self.assertEqual(native[row['evidence_record_ids']]['dataset'], 'signin_logs')
            self.assertEqual(native[row['evidence_record_ids']]['raw']['id'], row['eventId'])
        self.assertEqual([row['eventId'] for row in rows], ['event-1', 'event-2', 'event-3', 'event-4', 'event-5', 'event-8'])
        self.assertIsNone(rows[0]['authenticationProtocol'])
        record = payload['findings'][0]['records'][0]
        self.assertEqual(record['field_status']['authenticationProtocol']['status'], 'unavailable')
        self.assertIn('v1.0', record['field_status']['authenticationProtocol']['reason'])
        self.assertTrue(any('MAPI over HTTP: 1' in note for note in evidence['limitations']))
        self.assertNotIn('not-exported', repr(model))

    def test_numbered_ids_do_not_choose_record_adapters(self):
        rows = [{'RecommendationId': 'ENT-011', 'Service': 'Entra', 'Disposition': 'Coverage', 'FindingKey': 'coverage.identity.admin',
                 'Observation': 'Privileged role assignment schedules could not be read.', 'EvidenceKey': 'admin_role_detail'},
                {'RecommendationId': 'ENT-005', 'Service': 'Entra', 'Disposition': 'Assurance', 'FindingKey': 'synthetic.other',
                 'Observation': 'Unrelated.'}]
        for row in rows:
            self.assertEqual(expand_finding_records(row, {})['record_type'], 'unsupported', row['RecommendationId'])
        standing = {'RecommendationId': 'X-1', 'EvidenceKey': 'admin_role_detail',
                    'Observation': '3 active directory role assignment schedules have no expiration'}
        self.assertEqual(expand_finding_records(standing, {})['record_type'], 'standing_admin_assignment')

    def test_no_matching_events_never_attach_the_whole_sign_in_dataset(self):
        logs = [event(1, client='Browser'), event(2, client='Mobile Apps and Desktop clients')]
        _, model = build([legacy_finding()], {'signin_logs': [{'records': logs, 'source': source()}]})
        evidence = model['findings'][0]['evidence']
        self.assertEqual((evidence['record_count'], evidence['availability'], evidence['kind']), (0, 'absent', 'none'))
        _, model = build([legacy_finding()], {'signin_logs': [{'records': logs, 'source': source(complete=False, truncated=True)}]})
        self.assertEqual(model['findings'][0]['evidence']['availability'], 'unavailable')
        _, model = build([legacy_finding()], {})
        evidence = model['findings'][0]['evidence']
        self.assertEqual(evidence['availability'], 'not_retained')
        self.assertIn('sign-in logs', evidence['missing_evidence_action'])

    def test_partial_sources_remain_visible(self):
        _, model = build([legacy_finding()], {'signin_logs': [{'records': LOGS, 'source': source(truncated=True)}]})
        self.assertEqual(model['findings'][0]['evidence']['availability'], 'partial')
        self.assertTrue(model['findings'][0]['evidence']['missing_evidence_action'])


class ConcernAndKindTests(unittest.TestCase):
    def test_every_evidence_key_and_required_control_maps_to_a_concern(self):
        for key in list(SHEET_DEFINITIONS) + list(KEY_SOURCES):
            self.assertNotEqual(concern_for({'EvidenceKey': key}), 'other', key)
        for control in REQUIRED_CONTROLS:
            self.assertNotEqual(concern_for({'ControlId': control}), 'other', control)
        self.assertEqual(concern_for({'FindingKey': 'sharepoint.authentication.legacy_permitted'}), 'legacy-authentication')
        self.assertEqual(concern_for({'ControlId': 'IDENTITY.AUTH', 'FindingKey': 'baseline.identity.sign_in'}), 'conditional-access')

    def test_every_source_dataset_has_an_evidence_kind(self):
        names = {name for values in KEY_SOURCES.values() for name in values}
        self.assertFalse(names - set(DATASET_KINDS))
        self.assertTrue(set(DATASET_KINDS.values()) <= set(EVIDENCE_KINDS))

    def test_record_and_entity_units_differ_for_grants(self):
        principals = [{'id': 'sp-1', 'appId': 'app-1', 'displayName': 'Fictional app', 'appOwnerOrganizationId': 'other-tenant'}]
        grants = [{'id': f'grant-{n}', 'clientId': 'sp-1', 'resourceId': 'resource', 'scope': 'Mail.ReadWrite Files.ReadWrite.All'}
                  for n in range(3)]
        row = {'RecommendationId': 'ENT-002', 'Service': 'Entra', 'Disposition': 'Action',
               'FindingKey': 'entra.app_consent.high_impact_grants', 'Observation': 'High-impact grants.',
               'InvestigationCount': 1, 'InvestigationSummary': '1 item to review'}
        _, model = build([row], {'service_principals': [{'records': principals, 'source': source()}],
                                 'oauth_grants': [{'records': grants, 'source': source()}]})
        evidence = model['findings'][0]['evidence']
        self.assertEqual((evidence['record_count'], evidence['record_unit']), (3, 'grant records'))
        self.assertEqual((evidence['affected_entity_count'], evidence['entity_unit']), (1, 'applications'))
        self.assertIn('worklist lists 1 item to review', evidence['count_relation'])

    def test_guidance_is_matched_by_content_and_unmatched_actions_stay_unverified(self):
        self.assertEqual(guidance_for(legacy_finding())['id'], 'legacy-authentication-events')
        self.assertEqual(guidance_for({'FindingKey': 'sharepoint.sharing.anyone_enabled'})['id'], 'sharing-settings')
        _, model = build([{'RecommendationId': 'X-9', 'Service': 'M365', 'Disposition': 'Action', 'FindingKey': 'novel.thing',
                           'Observation': 'Something new.', 'Recommendation': 'Do the existing thing.'}], {})
        detail = model['findings'][0]['recommendation_detail']
        self.assertEqual(detail['guidance_status'], 'admin-center location not verified')
        self.assertEqual(detail['change'], 'Do the existing thing.')


class SharedIdentifierTests(unittest.TestCase):
    def test_raw_sheet_evidence_ids_equal_the_dashboard_registry(self):
        from Core.raw_evidence import prepare_raw_details
        sources = {'signin_logs': [{'records': copy.deepcopy(LOGS), 'source': source()}]}
        bundle = {'assessment_sources': sources, 'sheets': {}}
        result = {'tenant_id': TENANT, 'recommendations': []}
        prepare_raw_details(bundle, result)
        raw_ids = [row['Evidence Record ID'] for row in bundle['sheets']['raw_source.signin_logs']['rows']]
        payload = build_dashboard_export(result, {'assessment_sources': copy.deepcopy(sources)})
        self.assertEqual(raw_ids, [entry['record_id'] for entry in payload['evidence_records']])
        self.assertEqual(raw_ids, list(evidence_record_ids(sources, TENANT).values()))

    def test_investigation_preparation_is_idempotent_for_the_exporters(self):
        from Core.investigation_details import prepare_investigation_details
        bundle = {'assessment_sources': {'signin_logs': [{'records': copy.deepcopy(LOGS), 'source': source()}]}, 'sheets': {}}
        result = {'tenant_id': TENANT, 'recommendations': [legacy_finding()], 'actions': []}
        prepare_investigation_details(bundle, result)
        before = copy.deepcopy((bundle['sheets'], result['recommendations']))
        prepare_investigation_details(bundle, result)
        self.assertEqual(before, (bundle['sheets'], result['recommendations']))


if __name__ == '__main__':
    unittest.main()

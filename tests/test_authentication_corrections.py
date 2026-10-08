"""Pass A regression gates: fictional inputs and mocked Graph only."""
import copy
import io
import unittest
from contextlib import redirect_stdout
from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import httpx

from Core import signin_evidence
from Core.authentication_methods import legacy_registration_summary, summarize_registrations
from Core.authentication_investigation import build_mfa_registration_investigation
from Core.evidence_records import _mfa, _legacy_signins
from Core.get_entra_client import _fetch_graph_collection_via_http
from Core.get_graph_client import GraphRestClient
from Core.offline_collection import _encode, _decode
from Core.operational_evidence import signin_record, operational_results
from Core.assessment_result import build_assessment_result
from Core.tenant_baseline import assess_identity_baseline
from Core.saved_control_checks import qualify_saved_recommendations

DAY = '2026-10-08'
TENANT = '11111111-1111-1111-1111-111111111111'


def event(code=0, ca='success', client='Browser', **values):
    return dict(id='fiction-event', userId='fiction-user', clientAppUsed=client,
                createdDateTime=DAY + 'T11:22:33.456Z', status={'errorCode': code},
                conditionalAccessStatus=ca, **values)


def source(**values):
    return dict({'available': True, 'availability_status': 'available', 'complete': True,
                 'truncated': False, 'scope': 'Returned user sign-ins', 'tenant_id': TENANT,
                 'collected_at': DAY, 'source_file': 'fiction.json',
                 'window_start': '2026-10-01T00:00:00Z', 'window_end': DAY + 'T23:59:59Z'}, **values)


def dataset(records, **state):
    return {'records': records, 'source': source(**state)}


def operation(records, **state):
    return operational_results({'assessment_sources': {'signin_logs': [dataset(records, **state)]}},
                               {}, evaluation_date=DAY)[0]['IDENTITY.AUTH']


MFA_STEP = {'succeeded': True, 'authenticationStepRequirement': 'multiFactorAuthentication',
            'authenticationStepResultDetail': 'MFA completed', 'authenticationMethod': 'Authenticator App'}


class CollectionEnvelopeTests(unittest.IsolatedAsyncioTestCase):
    async def fetch(self, bodies):
        bodies = iter(bodies)
        http = httpx.AsyncClient(base_url='https://graph.microsoft.com', transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=next(bodies))))
        with patch('Core.get_entra_client._get_graph_http_client', AsyncMock(return_value=http)):
            result = await _fetch_graph_collection_via_http('/v1.0/auditLogs/signIns')
        self.assertTrue(http.is_closed)
        return result

    async def test_valid_records_and_complete_empty(self):
        for records in ([], [event()]):
            with self.subTest(records=bool(records)):
                result = await self.fetch([{'value': records}])
                self.assertTrue(result['complete'])
                self.assertEqual(result['value'], records)
                self.assertEqual(result['availability_status'], 'available')

    async def test_malformed_200_is_never_complete_empty(self):
        for body in ({}, [], None, {'value': None}, {'value': {}}, {'value': 'wrong'},
                     {'value': [], '@odata.nextLink': 42}, {'value': [], '@odata.nextLink': ''},
                     {'value': [], '@odata.nextLink': 'https://untrusted.invalid/next'},
                     {'value': [], 'error': {'code': 'Forbidden'}}, {'value': [None]}):
            with self.subTest(body=body):
                result = await self.fetch([body])
                self.assertFalse(result['available'])
                self.assertFalse(result['complete'])
                self.assertNotEqual(result['availability_status'], 'available')
                self.assertEqual(result['pages_collected'], 0)
                self.assertEqual(_decode(_encode(result)), result)

    async def test_later_malformed_page_retains_first_page(self):
        first = event()
        result = await self.fetch([{'value': [first], '@odata.nextLink':
                                  'https://graph.microsoft.com/v1.0/auditLogs/signIns?$skiptoken=next'}, {}])
        self.assertEqual(result['value'], [first])
        self.assertEqual(result['pages_collected'], 1)
        self.assertEqual(result['availability_status'], 'partial')
        self.assertTrue(result['truncated'])
        self.assertFalse(result['complete'])

    async def test_graph_reader_uses_same_envelope_validation(self):
        client = GraphRestClient(NS())
        try:
            with patch.object(client, 'get_json', AsyncMock(return_value={'value': [], '@odata.nextLink': 123})):
                result = await client.get_collection('/users')
            self.assertFalse(result['complete'])
            self.assertNotEqual(result['availability_status'], 'available')
        finally:
            await client.aclose()


class EventClassifierTests(unittest.TestCase):
    def test_result_code_table_and_input_permutation(self):
        cases = [(0, 'Success'), ('0', 'Success'), (False, 'Unknown'), (True, 'Unknown'),
                 (0.0, 'Unknown'), (None, 'Unknown'), ('unreadable', 'Unknown'),
                 (50126, 'FailedOther'), (50057, 'FailedOther'), (53003, 'BlockedByConditionalAccess'),
                 (50053, 'FailedOther'), (50076, 'InterruptedOrChallenged'),
                 (50079, 'InterruptedOrChallenged'), (50140, 'InterruptedOrChallenged')]
        for code, expected in cases:
            with self.subTest(code=code):
                raw = event(code, ca='notApplied' if code != 53003 else 'failure')
                normalized = signin_record(raw)
                self.assertEqual(normalized['NormalizedSignInOutcome'], expected)
                self.assertEqual(signin_record(dict(reversed(list(raw.items())))), normalized)
                self.assertEqual(raw['status']['errorCode'], code)

    def test_explicit_status_without_supported_code_does_not_invent_success(self):
        for value in ('success', 'failure', False, {}):
            raw = event(None)
            raw['status'] = value
            self.assertEqual(signin_record(raw)['NormalizedSignInOutcome'], 'Unknown')

    def test_contradictory_success_is_unknown_everywhere(self):
        raw = event(0, 'failure')
        self.assertEqual(signin_record(raw)['NormalizedSignInOutcome'], 'Unknown')
        self.assertEqual(signin_evidence.classify_signin_outcome(raw)['normalized_outcome'], 'Unknown')
        self.assertTrue(signin_record(raw)['AuthenticationConflicts'])

    def test_generic_failure_with_ca_failure_is_not_automatically_ca_block(self):
        self.assertEqual(signin_record(event(50126, 'failure'))['NormalizedSignInOutcome'], 'FailedOther')

    def test_policy_status_conflict_and_report_only_are_separate(self):
        raw = event(appliedConditionalAccessPolicies=[{'id': 'p', 'result': 'failure'}])
        self.assertEqual(signin_record(raw)['NormalizedSignInOutcome'], 'Unknown')
        raw['appliedConditionalAccessPolicies'][0]['result'] = 'reportOnlyFailure'
        self.assertEqual(signin_record(raw)['NormalizedSignInOutcome'], 'Success')
        self.assertEqual(len(signin_record(raw)['ReportOnlyResults']), 1)

    def test_policy_detail_states(self):
        for value, expected in ((None, 'unavailable'), ([], 'available'), ({}, 'malformed'),
                                ([{'id': 'p', 'result': 'success'}], 'available')):
            with self.subTest(value=value):
                normalized = signin_record(event(appliedConditionalAccessPolicies=value))
                self.assertEqual(normalized['AppliedPoliciesState'], expected)
                self.assertEqual(normalized['AppliedPoliciesAvailable'], expected == 'available')

    def test_raw_context_and_full_timestamps_survive(self):
        raw = event(client='IMAP', authenticationProtocol='unknownFutureValue', isInteractive=False,
                    resourceId='fiction-resource', authenticationDetails=[MFA_STEP],
                    authenticationMethodsUsed=['Authenticator'], riskLevelDuringSignIn='hidden')
        original = copy.deepcopy(raw)
        normalized = signin_record(raw)
        for key, value in original.items():
            self.assertEqual(normalized[key], value)
        self.assertEqual(raw, original)
        self.assertEqual(_decode(_encode(normalized)), normalized)


class LegacyAndControlTests(unittest.TestCase):
    def test_legacy_event_meaning_table(self):
        cases = [(0, 'notApplied', 'SuccessfulLegacyAuthentication'),
                 (0, 'success', 'SuccessfulLegacyAuthentication'),
                 (53003, 'failure', 'LegacyAttemptBlockedByConditionalAccess'),
                 (50126, 'notApplied', 'LegacyAttemptFailedOther'),
                 (50076, 'failure', 'LegacyAttemptInterrupted'),
                 (None, 'unknown', 'LegacyOutcomeUnknown')]
        for code, ca, expected in cases:
            with self.subTest(code=code):
                self.assertEqual(signin_record(event(code, ca, 'IMAP'))['LegacyAuthenticationState'], expected)
        self.assertEqual(signin_record(event())['LegacyAuthenticationState'], 'NotClassifiedLegacy')

    def test_protocol_or_ca_status_alone_is_not_success(self):
        raw = event(None, 'notApplied', 'IMAP', authenticationProtocol='imap')
        self.assertEqual(signin_record(raw)['LegacyAuthenticationState'], 'LegacyOutcomeUnknown')
        self.assertNotEqual(operation([raw])['result'], 'pass')

    def test_successful_legacy_never_supports_pass_even_with_favorable_mfa(self):
        for ca in ('success', 'notApplied'):
            raw = event(0, ca, 'IMAP', authenticationRequirement='multiFactorAuthentication',
                        authenticationDetails=[MFA_STEP])
            self.assertEqual(operation([raw])['result'], 'fail')
            self.assertEqual(operation([raw], complete=False, availability_status='partial')['result'], 'fail')

    def test_blocked_other_failed_unknown_and_empty_are_not_tenant_passes(self):
        for records in ([], [event(53003, 'failure', 'IMAP')], [event(50126, 'notApplied', 'IMAP')],
                        [event(None, 'unknown', 'IMAP')]):
            with self.subTest(records=records):
                self.assertEqual(operation(records)['result'], 'unknown')

    def test_event_sample_mfa_is_not_unqualified_tenant_assurance(self):
        raw = event(authenticationRequirement='multiFactorAuthentication', authenticationDetails=[MFA_STEP])
        self.assertEqual(operation([raw])['result'], 'unknown')

    def test_full_builder_cannot_pass_legacy_blocking(self):
        policies = [{'displayName': 'MFA', 'state': 'enabled', 'conditions': {
            'users': {'includeUsers': ['All']}, 'applications': {'includeApplications': ['All']}},
            'grantControls': {'builtInControls': ['mfa']}},
            {'displayName': 'Block legacy', 'state': 'enabled', 'conditions': {
                'users': {'includeUsers': ['All']}, 'applications': {'includeApplications': ['All']},
                'clientAppTypes': ['exchangeActiveSync', 'other']}, 'grantControls': {'builtInControls': ['block']}}]
        client = NS(ca_policies=policies, security_defaults={}, collection_status={'ca_policies': source()})
        raw = event(0, 'success', 'IMAP', authenticationRequirement='multiFactorAuthentication',
                    authenticationDetails=[MFA_STEP])
        bundle = {'source_statuses': {'ca_policies': source()},
                  'collection_context': {'tenant_id': TENANT, 'collected_at': DAY, 'source_file': 'fiction.json'},
                  'assessment_sources': {'signin_logs': [dataset([raw])]}}
        result = build_assessment_result(assess_identity_baseline(client, DAY), bundle,
                                        evaluation_date=DAY, expected_tenant_id=TENANT)
        control = next(row for row in result['controls'] if row['control_id'] == 'IDENTITY.AUTH')
        self.assertEqual(control['operational_result'], 'fail')
        self.assertEqual(control['status'], 'Action required')

    def test_summary_coverage_and_all_occurrences(self):
        records = [event(0, 'notApplied', 'IMAP'), event(53003, 'failure', 'IMAP')]
        summary = signin_evidence.legacy_event_summary([dataset(records)])
        self.assertEqual(summary['counts']['Success'], 1)
        self.assertEqual(summary['total'], 2)
        self.assertFalse(summary['no_success_observed'])
        self.assertTrue(signin_evidence.legacy_event_summary([dataset(records[1:])])['no_success_observed'])
        for state in ({'complete': False, 'availability_status': 'partial'},
                      {'scope': ''}, {'window_start': ''}):
            self.assertFalse(signin_evidence.legacy_event_summary([dataset([] , **state)])['no_success_observed'])
        self.assertFalse(signin_evidence.legacy_event_summary([dataset([]), dataset([], complete=False)])['no_success_observed'])
        detail = _legacy_signins({'signin_logs': [dataset(records + records)]}, {})
        self.assertEqual(len(detail['records']), 4)


class MFAPopulationTests(unittest.TestCase):
    def population(self, records, **kwargs):
        return __import__('Core.authentication_methods', fromlist=['reconcile_registration_population']).reconcile_registration_population(records, **kwargs)

    def test_registered_unregistered_unknown_conflicting_and_excluded(self):
        records = [{'id': 'a', 'isMfaRegistered': True}, {'id': 'b', 'isMfaRegistered': False},
                   {'id': 'c'}, {'id': 'd', 'isMfaRegistered': True}, {'id': 'd', 'isMfaRegistered': False},
                   {'id': 'e', 'isMfaRegistered': False, 'accountEnabled': False}]
        population = self.population(records)
        self.assertEqual(population['counts'], {'ExplicitlyRegistered': 1, 'ExplicitlyNotRegistered': 1,
                         'Unknown': 1, 'Conflicting': 1, 'ExcludedWithReason': 1})
        self.assertEqual(population['denominator'], 2)
        self.assertEqual(population['percentage'], 50.0)
        self.assertEqual(sum(population['counts'].values()), population['total'])

    def test_unknown_nonboolean_conflict_and_duplicate_details_reconcile(self):
        for records in ([{'id': 'a', 'isMfaRegistered': True}, {'id': 'b'}],
                        [{'id': 'a', 'isMfaRegistered': False}] * 2,
                        [{'id': 'a', 'isMfaRegistered': True}, {'id': 'a', 'isMfaRegistered': False}],
                        [{'id': 'a', 'isMfaRegistered': 'false'}]):
            with self.subTest(records=records):
                client = NS(auth_methods_registration=records, collection_status={'auth_methods': source()}, auth_summary={})
                population = self.population(records)
                worklist = build_mfa_registration_investigation(client)
                details = _mfa({'auth_methods': [dataset(records)]})
                report = summarize_registrations(records, source())
                count = population['counts']['ExplicitlyNotRegistered']
                self.assertEqual(len(worklist['rows']), count)
                self.assertEqual(len(details['records']), count)
                self.assertEqual(report['registration_population']['denominator'], population['denominator'])
                self.assertEqual(_decode(_encode(population)), population)

    def test_enabled_guest_method_context_and_documented_exclusion(self):
        records = [{'id': 'guest', 'userType': 'guest', 'accountEnabled': True,
                    'isMfaRegistered': False, 'isMfaCapable': False, 'methodsRegistered': ['mobilePhone']}]
        entry = self.population(records)['entries'][0]
        self.assertEqual(entry['record'], records[0])
        population = self.population(records, exclusions={'guest': 'Documented external-provider review'})
        self.assertEqual(population['counts']['ExcludedWithReason'], 1)
        self.assertIsNone(population['percentage'])
        self.assertTrue(population['entries'][0]['exclusion_reason'])

    def test_recommendation_modules_use_known_denominator_and_no_unknown_remediation(self):
        from Recommendations.entra import AAD_PREMIUM, AAD_PREMIUM_P1
        for records in ([{'id': 'a', 'isMfaRegistered': True}, {'id': 'b'}], [{'id': 'a'}],
                        [{'id': 'a', 'isMfaRegistered': True}, {'id': 'a', 'isMfaRegistered': False}]):
            client = NS(auth_methods_registration=records, collection_status={'auth_methods': source()})
            from Recommendations.entra.entra_insights import extract_entra_insights_from_client
            client.available = True
            client.auth_summary = legacy_registration_summary(records)
            insights = extract_entra_insights_from_client(client)
            for module in (AAD_PREMIUM, AAD_PREMIUM_P1):
                with redirect_stdout(io.StringIO()):
                    rows = module.get_recommendation('AAD_PREMIUM', client=client, entra_insights=insights)
                registration = [r for r in rows if r.get('FindingKey') == 'entra.authentication.mfa_registration']
                self.assertTrue(registration)
                self.assertFalse(any(r.get('Disposition') == 'Action' for r in registration))
                self.assertFalse(any('remaining' in r.get('Recommendation', '') for r in registration))


class MFALayerTests(unittest.TestCase):
    def test_completion_denial_claim_external_and_strong_primary(self):
        cases = [([MFA_STEP], 'observed_success'),
                 ([dict(MFA_STEP, succeeded=False)], 'denied'),
                 ([dict(MFA_STEP, authenticationStepResultDetail='MFA requirement satisfied by claim in token')], 'previously_satisfied'),
                 ([dict(MFA_STEP, authenticationStepResultDetail='MFA requirement satisfied by external provider')], 'external_provider'),
                 ([{'succeeded': True, 'authenticationMethod': 'FIDO2 security key',
                    'authenticationStepRequirement': 'primaryAuthentication'}], 'strong_primary')]
        for details, expected in cases:
            with self.subTest(expected=expected):
                normalized = signin_record(event(authenticationDetails=details))
                self.assertEqual(normalized['MFASatisfaction'], expected)
                self.assertEqual(normalized['authenticationDetails'], details)
                self.assertEqual(operation([normalized])['result'], 'unknown')

    def test_missing_partial_and_conflicting_details_are_non_favorable(self):
        for details in (None, {}, [None], [dict(MFA_STEP, succeeded='true')],
                        [MFA_STEP, dict(MFA_STEP, succeeded=False)]):
            with self.subTest(details=details):
                row = signin_record(event(authenticationDetails=details))
                self.assertIn(row['MFASatisfaction'], {'unknown', 'conflict'})
                self.assertNotEqual(operation([row])['result'], 'pass')

    def test_registration_and_enforcement_layers_cannot_be_observed_authentication(self):
        self.assertEqual(operation([])['result'], 'unknown')
        registered = summarize_registrations([{'id': 'a', 'isMfaRegistered': True}], source())
        self.assertEqual(registered['metrics']['mfa_registered'], 1)
        self.assertIn('registration', registered['evidence_layer'])


class AuthenticationOutputTests(unittest.TestCase):
    def test_saved_registration_recommendations_are_reconciled(self):
        client = NS(auth_methods_registration=[{'id': 'a', 'isMfaRegistered': True}, {'id': 'b'}],
                    collection_status={'auth_methods': source()})
        old = {'Service': 'Entra', 'Feature': 'MFA', 'Observation': '1 of 2 users enrolled in MFA',
               'Recommendation': 'Register remaining 1 users', 'Status': 'Action Required', 'Disposition': 'Action'}
        row = qualify_saved_recommendations([old], 'Entra', client)[0]
        self.assertNotEqual(row['Disposition'], 'Action')
        self.assertEqual(row['MFARegistrationPopulation']['denominator'], 1)
        self.assertIn('unknown', row['Observation'].lower())

    def test_legacy_finding_uses_shared_outcomes_and_period(self):
        records = [event(0, 'notApplied', 'IMAP'), event(53003, 'failure', 'IMAP'),
                   event(50126, 'notApplied', 'IMAP'), event(50076, 'failure', 'IMAP'), event(None, 'unknown', 'IMAP')]
        client = NS(signin_logs=records, collection_status={'signin_logs': source()}, assessment_datasets={})
        old = {'Service': 'Entra', 'Feature': 'Legacy sign-ins', 'FindingKey': 'entra.signins.legacy_auth',
               'Observation': '5 legacy authentication sign-ins detected', 'Status': 'Action Required', 'Disposition': 'Action'}
        row = qualify_saved_recommendations([old], 'Entra', client)[0]
        self.assertEqual(sum(row['AuthenticationSummary']['counts'].values()), 5)
        for text in ('succeeded', 'Conditional Access', 'interrupted', 'unknown', '2026-10-01'):
            self.assertIn(text, row['Observation'])
        self.assertNotIn('bypass', row['Observation'].lower())

    def test_failed_secondary_window_remains_visible_and_no_zero_assurance(self):
        client = NS(signin_logs=[], collection_status={'signin_logs': source()},
                    assessment_datasets={'signin_logs': [dataset([], complete=False, availability_status='unavailable')]})
        old = {'Service': 'Entra', 'Observation': 'No legacy authentication sign-ins found', 'Status': 'Success'}
        row = qualify_saved_recommendations([old], 'Entra', client)[0]
        self.assertEqual(row['Disposition'], 'Coverage')
        self.assertFalse(row['AuthenticationSummary']['no_success_observed'])

    def test_no_ca_inventory_does_not_claim_unprotected_access(self):
        from Recommendations.entra.AAD_PREMIUM import get_recommendation
        with redirect_stdout(io.StringIO()):
            rows = get_recommendation('AAD_PREMIUM', entra_insights={'available': True,
                                      'ca_metrics': {}, 'mfa_metrics': {}, 'signin_metrics': {}})
        self.assertFalse(any('leaving Copilot access unprotected' in r['Observation'] for r in rows))


if __name__ == '__main__':
    unittest.main()

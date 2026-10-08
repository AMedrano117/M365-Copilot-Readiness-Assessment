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
        for state in ({'complete': False, 'availability_status': 'partial'}, {'available': False},
                      {'scope': ''}, {'window_start': ''}, {'window_start': 'invalid'},
                      {'window_start': '2026-10-09T00:00:00Z'}, {'window_start': '2026-10-01'}):
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


class CollectionFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_marker_and_page_cap_never_complete_zero(self):
        client = GraphRestClient(NS())
        self.addAsyncCleanup(client.aclose)
        for marker in ({'complete': False}, {'truncated': True}, {'@odata.partial': True},
                       {'availability_status': 'partial'}, {'@odata.nextLink': 'https://graph.microsoft.com/v1.0/users?next=opaque'}):
            with self.subTest(marker=marker), patch.object(client, 'get_json', AsyncMock(return_value={'value': [], **marker})):
                result = await client.get_collection('/users', max_pages=1)
                self.assertFalse(result['complete'])
                self.assertEqual(result['availability_status'], 'partial')
                self.assertEqual(result['pages_collected'], 1)

    async def test_invalid_json_and_later_http_failure_retain_records(self):
        client = GraphRestClient(NS())
        self.addAsyncCleanup(client.aclose)
        first = {'value': [event()], '@odata.nextLink': 'https://graph.microsoft.com/v1.0/users?next=opaque'}
        from Core.get_graph_client import GraphRequestError
        for error in (ValueError('Invalid JSON'), GraphRequestError(403, 'Fictional denied page')):
            with self.subTest(error=type(error).__name__), patch.object(client, 'get_json', AsyncMock(side_effect=[first, error])):
                result = await client.get_collection('/users', params={'$top': '9'})
                self.assertEqual(result['value'], [event()])
                self.assertEqual(result['availability_status'], 'partial')
                self.assertFalse(result['complete'])
                self.assertEqual(result['request_params'], {'$top': '9'})
                self.assertEqual(result['page_requests'][1]['params'], {})


class AuthenticationBoundaryTests(unittest.TestCase):
    def test_tenant_mismatch_and_future_events_cannot_determine_current_control(self):
        bundle = {'assessment_sources': {'signin_logs': [dataset([event(0, 'notApplied', 'IMAP')], tenant_id='other')]}}
        out = operational_results(bundle, {}, evaluation_date=DAY, tenant_id=TENANT)[0]['IDENTITY.AUTH']
        self.assertEqual(out['result'], 'unknown')
        future = event(0, 'notApplied', 'IMAP'); future['createdDateTime'] = '2027-01-01T00:00:00Z'
        self.assertEqual(operation([future])['result'], 'unknown')

    def test_conflicting_event_requires_confirmation_and_no_tenant_pass(self):
        self.assertEqual(operation([event(0, 'failure', 'IMAP')])['result'], 'conflict')

    def test_partial_secondary_window_blocks_review_pass_and_preserves_failed_window(self):
        from test_methodology_v4 import operational_profile
        bundle = {'assessment_sources': {'signin_logs': [dataset([event()]), dataset([], complete=False, availability_status='unavailable')]}}
        out = operational_results(bundle, operational_profile(day=DAY), evaluation_date=DAY, tenant_id=TENANT)[0]['IDENTITY.AUTH']
        self.assertEqual(out['result'], 'unknown')
        self.assertIn('review', out['reason'])
        self.assertFalse(signin_evidence.legacy_event_summary(bundle['assessment_sources']['signin_logs'])['no_success_observed'])

    def test_successful_legacy_conflicts_with_favorable_review_and_keeps_source(self):
        from test_methodology_v4 import operational_profile
        bundle = {'assessment_sources': {'signin_logs': [dataset([event(0, 'notApplied', 'IMAP')])]}}
        out = operational_results(bundle, operational_profile(day=DAY), evaluation_date=DAY, tenant_id=TENANT)[0]['IDENTITY.AUTH']
        self.assertEqual(out['result'], 'conflict')
        self.assertIn('successful legacy', out['reason'])
        self.assertTrue(any(row.get('AuthenticationSource') == source() for row in out['records']))

    def test_interactive_and_noninteractive_occurrences_are_not_deduplicated(self):
        records = [event(0, 'notApplied', 'IMAP', isInteractive=value) for value in (True, False)]
        summary = signin_evidence.legacy_event_summary([dataset(records)])
        self.assertEqual(summary['counts']['Success'], 2)
        self.assertEqual(len(_legacy_signins({'signin_logs': [dataset(records)]}, {})['records']), 2)

    def test_sdk_aliases_have_identical_semantics_and_preserve_step_context(self):
        raw = NS(client_app_used='IMAP', status=NS(error_code='0'), conditional_access_status='notApplied',
                 authentication_details=[NS(succeeded=True, authentication_method='FIDO2 security key',
                    authentication_step_requirement='primaryAuthentication')])
        out = signin_record(raw)
        self.assertEqual(out['NormalizedSignInOutcome'], 'Success')
        self.assertEqual(out['MFASatisfaction'], 'strong_primary')
        self.assertEqual(out['authentication_details'][0]['authentication_method'], 'FIDO2 security key')

    def test_sdk_enum_failure_cannot_become_success_during_plain_conversion(self):
        from enum import Enum
        class Access(Enum):
            FAILURE = 'failure'
        raw = NS(client_app_used='IMAP', status=NS(error_code=0), conditional_access_status=Access.FAILURE)
        self.assertEqual(signin_record(raw)['NormalizedSignInOutcome'], 'Unknown')
        self.assertEqual(signin_record(raw)['conditional_access_status'], 'failure')

    def test_single_factor_result_is_separate_from_observed_mfa_satisfaction(self):
        raw = event(authenticationRequirement='singleFactorAuthentication', authenticationDetails=[MFA_STEP])
        out = signin_record(raw)
        self.assertEqual(out['MFARequirement'], 'not_required')
        self.assertEqual(out['MFASatisfaction'], 'observed_success')
        self.assertEqual(operation([raw])['result'], 'unknown')

    def test_disabled_join_and_supplemental_conflict_share_all_population_consumers(self):
        from Core.authentication_methods import authentication_method_report
        records = [{'id': 'disabled', 'isMfaRegistered': False}, {'id': 'guest', 'isMfaRegistered': False, 'userType': 'guest'}]
        users = [{'id': 'disabled', 'accountEnabled': False}]
        supplemental = [dataset([{'id': 'guest', 'isMfaRegistered': True, 'userType': 'guest'}])]
        client = NS(auth_methods_registration=records, users=users, collection_status={'auth_methods': source()},
                    assessment_datasets={'auth_methods': supplemental})
        report = authentication_method_report(client)
        self.assertEqual(report['registration_population']['counts']['ExcludedWithReason'], 1)
        self.assertEqual(report['registration_population']['counts']['Conflicting'], 1)
        self.assertEqual(build_mfa_registration_investigation(client)['rows'], [])
        sources = {'auth_methods': [dataset(records)] + supplemental, 'users': [dataset(users)]}
        self.assertEqual(_mfa(sources)['records'], [])


class AuthenticationDeliverableTests(unittest.TestCase):
    def fixture(self, client=None):
        from Core.evidence_layer import build_evidence_bundle
        records = [event(0, 'notApplied', 'IMAP', userPrincipalName='fiction-private@example.invalid',
                         authenticationDetails=[MFA_STEP]), event(53003, 'failure', 'IMAP'),
                   event(50126, 'notApplied', 'IMAP'), event(50076, 'failure', 'IMAP'), event(None, 'unknown', 'IMAP')]
        client = client or NS(signin_logs=records, collection_status={'signin_logs': source(), 'auth_methods': source()},
                    auth_methods_registration=[{'id': 'a', 'isMfaRegistered': True}, {'id': 'b', 'isMfaRegistered': False}, {'id': 'c'}])
        old = {'Service': 'Entra', 'Feature': 'Legacy sign-ins', 'FindingKey': 'entra.signins.legacy_auth',
               'Observation': '5 legacy authentication sign-ins detected', 'Status': 'Action Required', 'Disposition': 'Action'}
        rows = qualify_saved_recommendations([old], 'Entra', client)
        bundle = build_evidence_bundle(rows, ({}, []), {'_client': client}, {}, {}, {}, {},
                  collection_context={'source_file': 'fiction.json', 'collected_at': DAY, 'tenant_id': TENANT})
        from Core.assessment_catalog import collect_assessment_sources
        bundle['assessment_sources'] = collect_assessment_sources([client], collected_at=DAY)
        bundle['evaluation_date'] = DAY
        result = build_assessment_result(bundle['recommendations'], bundle, evaluation_date=DAY, expected_tenant_id=TENANT)
        bundle['assessment_result'] = result
        return result, bundle, client

    def test_shared_layers_render_with_qualified_counts_and_no_identities(self):
        from Core.customer_report import render_customer_report
        from Core.pilot_summary import render_pilot_summary
        result, bundle, _ = self.fixture()
        before = copy.deepcopy(result)
        for html in (render_customer_report(result, bundle, 'Fictional organization'),
                     render_pilot_summary(result, bundle, 'Fictional organization')):
            for text in ('MFA registration', 'MFA enforcement', 'Observed authentication', 'BlockedByConditionalAccess', 'InterruptedOrChallenged'):
                self.assertIn(text, html)
            self.assertNotIn('fiction-private@example.invalid', html)
            self.assertNotIn('leaving Copilot access unprotected', html)
            self.assertNotIn('authenticationStepResultDetail', html)
        self.assertEqual(result, before)
        self.assertNotIn('governance', result)
        self.assertNotIn('ClosedByRemediation', str(result))

    def test_excel_retains_normalized_outcomes_timestamps_and_event_ids(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from Core.export_recommendations import export_to_excel
        from tests.workbook_test_helpers import load_workbook_pair
        result, bundle, _ = self.fixture()
        with TemporaryDirectory() as folder:
            filename = export_to_excel(result['recommendations'], filename='fiction.xlsx', evidence_bundle=bundle, output_dir=folder)
            workbook = load_workbook_pair(filename)
            try:
                values = list(workbook.technical['Legacy Sign-In Detail'].values)
                columns = {name: index for index, name in enumerate(values[0])}
                self.assertEqual({row[columns['Sign-In Result']] for row in values[1:]}, set(signin_evidence.NORMALIZED_SIGNIN_OUTCOMES))
                self.assertEqual(values[1][columns['Sign-In ID']], 'fiction-event')
                self.assertIn('11:22:33.456', values[1][columns['Created UTC']])
                self.assertTrue(all(row[columns['Source File']] == 'fiction.json' for row in values[1:]))
                self.assertIn('Authentication Details (raw)', columns)
            finally:
                workbook.close()

    def test_snapshot_and_replay_preserve_raw_normalized_findings_and_population(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from Core.assessment_serialization import write_assessment_result, read_assessment_result
        result, bundle, client = self.fixture()
        repeated, _, _ = self.fixture(_decode(_encode(client)))
        self.assertEqual(result['recommendations'], repeated['recommendations'])
        self.assertEqual(result['authentication_assessment'], repeated['authentication_assessment'])
        self.assertEqual(result['operational_results'], repeated['operational_results'])
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'snapshot.json'
            write_assessment_result(path, result)
            baseline_bytes = path.read_bytes()
            snapshot = read_assessment_result(path)
            row = snapshot['operational_results']['IDENTITY.AUTH']['records'][0]
            self.assertEqual(row['status']['errorCode'], 0)
            self.assertEqual(row['NormalizedSignInOutcome'], 'Success')
            self.assertEqual(row['authenticationDetails'], [MFA_STEP])
            self.assertEqual(snapshot['authentication_assessment'], result['authentication_assessment'])
            self.fixture(client)
            self.assertEqual(path.read_bytes(), baseline_bytes)

    def test_optional_html_evidence_uses_normalized_counts(self):
        from Core.finding_evidence import build_finding_evidence
        result, bundle, _ = self.fixture()
        from Core.evidence_selection import build_evidence_selection
        model = build_finding_evidence(build_evidence_selection(result, bundle))
        legacy = [row for row in model['findings'] if row['evidence']['record_type'] == 'legacy_signin_event']
        self.assertTrue(legacy)
        for row in legacy:
            outcomes = row['evidence']['normalized_outcome_counts']
            self.assertEqual(sum(outcomes.values()), row['evidence']['record_count'])
            self.assertEqual(outcomes['InterruptedOrChallenged'], 1)

    def test_supplemental_events_and_failed_windows_reconcile_workbook_and_finding(self):
        from Core.evidence_layer import _build_legacy_signin_sheet
        client = NS(signin_logs=[event(0, 'notApplied', 'IMAP')], collection_status={'signin_logs': source()},
                    assessment_datasets={'signin_logs': [dataset([event(53003, 'failure', 'IMAP')]), dataset([], complete=False, availability_status='unavailable')]})
        old = {'Service': 'Entra', 'Observation': '1 legacy authentication sign-in detected', 'Status': 'Action Required'}
        row = qualify_saved_recommendations([old], 'Entra', client)[0]
        sheet = _build_legacy_signin_sheet(client, [row])
        self.assertEqual(row['AuthenticationSummary']['total'], len(sheet['rows']))
        self.assertEqual(len(sheet['rows']), 2)
        self.assertFalse(row['AuthenticationSummary']['complete'])
        self.assertIn('incomplete', row['Observation'])

    def test_technical_event_timestamp_keeps_all_returned_fractional_digits(self):
        from Core.evidence_layer import _build_legacy_signin_sheet
        raw = event(0, 'notApplied', 'IMAP')
        for fraction in ('1234567', '0000007'):
            with self.subTest(fraction=fraction):
                raw['createdDateTime'] = DAY + 'T11:22:33.' + fraction + 'Z'
                client = NS(signin_logs=[raw], collection_status={'signin_logs': source()})
                row = _build_legacy_signin_sheet(client, [{'Service': 'Entra', 'FindingKey': 'entra.signins.legacy_auth'}])['rows'][0]
                self.assertEqual(row['Created UTC'], raw['createdDateTime'])

    def test_new_legacy_counts_have_no_inferred_delta_direction_or_automatic_closure(self):
        from Core.delta_metrics import compare_metric
        from test_delta_metrics import fact
        before = fact(4, 'identity.successful_legacy_events', 'events')
        after = fact(0, 'identity.successful_legacy_events', 'events')
        baseline = copy.deepcopy(before)
        for complete, availability in ((True, 'available'), (False, 'partial'), (False, 'unavailable')):
            after.update(complete=complete, availability=availability)
            delta = compare_metric(before, after)
            self.assertNotIn(delta['State'], {'Improved', 'Regressed', 'ResolvedByCurrentEvidence', 'ClosedByRemediation'})
            self.assertFalse(delta['ResolutionSupported'])
        self.assertEqual(before, baseline)


if __name__ == '__main__':
    unittest.main()

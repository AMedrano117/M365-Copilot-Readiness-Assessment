"""Stage 1 regression boundaries, using fictional in-memory evidence only."""

import copy
import io
import itertools
import unittest
from contextlib import redirect_stdout
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

from Core.assessment_result import _qualify_record, build_assessment_result
from Core.cross_provider_assessment import assess_provider_and_use_cases
from Core.evidence_contract import normalize_observation, reconcile_observations
from Core.evidence_layer import deduplicate_findings
from Core.get_graph_client import GraphRequestError, GraphRestClient, _Endpoint
from Core.get_purview_client import collection_state, extract_collection
from Core.offline_collection import _decode, _encode
from Core.source_evidence import source_availability, source_is_complete
from Recommendations.entra.AAD_GOVERNANCE import get_recommendation
from Recommendations.entra.entra_insights import extract_entra_insights_from_client

TENANT = '11111111-1111-1111-1111-111111111111'
DAY = '2026-09-10'


def fact(**changes):
    base = dict(tenant_id=TENANT, domain_id='content', control_id='CONTENT.PERMISSIONS',
                metric_id='permission_review', population='business sites', scope='all business sites',
                window='snapshot', value=0, unit='sites', availability='available', complete=True,
                observed_at=DAY, source_type='portal_export', source_file='fictional.csv')
    return dict(base, **changes)


def observation(**changes):
    row = {'Service': 'Entra', 'Feature': 'MFA registration', 'FindingKey': 'identity.mfa',
           'ControlId': 'IDENTITY.MFA', 'Observation': 'Two returned accounts lack MFA registration.',
           'Recommendation': 'Review the accounts.', 'Disposition': 'Action', 'Priority': 'High',
           'Status': 'Action Required', 'EvidenceSource': 'auth_methods',
           'EvidenceKey': 'mfa_registration_detail', 'EvidenceBasis': 'Tenant evidence'}
    return dict(row, **changes)


def source(**changes):
    state = {'availability_status': 'available', 'available': True, 'complete': True,
             'scope': 'all registration report users', 'tenant_id': TENANT,
             'collected_at': DAY, 'source_file': 'fictional-collection.json', 'records_collected': 2}
    return dict(state, **changes)


def evidence(states=None, **changes):
    bundle = {'source_statuses': {'auth_methods': source()} if states is None else states,
              'collection_context': {'tenant_id': TENANT, 'collected_at': DAY,
                                     'source_file': 'fictional-collection.json', 'mode': 'live'}}
    return dict(bundle, **changes)


class ContradictionTests(unittest.TestCase):
    def test_other_assurance_cannot_mask_unresolved_fact_conflict(self):
        inputs = [dict(fact(), control_result='pass', source_file='a.csv'),
                  dict(fact(), control_result='fail', source_file='b.csv')]
        row = observation(ControlId='CONTENT.PERMISSIONS', EvidenceKey='data_exposure_detail',
                          Disposition='Assurance', Observation='Broad content permissions were reviewed.')
        result = build_assessment_result([row], evidence(observations=inputs),
                                         evaluation_date=DAY, expected_tenant_id=TENANT)
        control = next(row for row in result['controls'] if row['control_id'] == 'CONTENT.PERMISSIONS')
        self.assertEqual(control['status'], 'Not established')

    def test_presentation_priority_cannot_discard_conflicting_or_qualified_records(self):
        base = observation(FindingKey='same-condition', ObservationDate=DAY, EvidenceScope='users')
        inputs = [base, dict(base, Priority='Low', Disposition='Assurance', control_result='pass'),
                  dict(base, EvidenceComplete=False), dict(base, Population='pilot')]
        for ordered in (inputs, list(reversed(inputs))):
            self.assertEqual(len(deduplicate_findings(ordered)), 4)

    def check(self, inputs):
        rows = reconcile_observations(inputs, evaluation_date=DAY, expected_tenant_id=TENANT)
        result = build_assessment_result([], {'observations': inputs},
                                         evaluation_date=DAY, expected_tenant_id=TENANT)
        control = next(row for row in result['controls'] if row['control_id'] == 'CONTENT.PERMISSIONS')
        self.assertEqual(len(rows), len(inputs))
        return rows, result, control

    def test_equal_measurements_opposite_results_remain_conflicts_in_both_orders(self):
        inputs = [dict(fact(), control_result='pass', source_file='a.csv'),
                  dict(fact(), control_result='fail', source_file='b.csv')]
        outputs = []
        for ordered in itertools.permutations(inputs):
            rows, result, control = self.check(list(ordered))
            self.assertEqual({row['selection'] for row in rows}, {'conflict'})
            self.assertEqual(control['status'], 'Not established')
            self.assertTrue(any(row['EvidenceStatus'] == 'conflict' for row in result['actions']))
            outputs.append(rows)
        self.assertEqual(outputs[0], outputs[1])

    def test_same_measurement_and_result_are_duplicates(self):
        rows, _, control = self.check([dict(fact(), control_result='pass', source_file='a.csv'),
                                      dict(fact(), control_result='pass', source_file='b.csv')])
        self.assertEqual({row['selection'] for row in rows}, {'selected', 'duplicate'})
        self.assertEqual(control['status'], 'Observed')

    def test_different_populations_windows_and_providers_remain_separate(self):
        for changed in ({'population': 'pilot sites'}, {'window': 'D30'}, {'provider': 'Other provider'}):
            with self.subTest(changed=changed):
                rows, _, control = self.check([dict(fact(), control_result='pass'),
                                              dict(fact(), control_result='fail', **changed)])
                self.assertEqual([row['selection'] for row in rows], ['selected', 'selected'])
                self.assertEqual(control['status'], 'Action required')

    def test_complete_snapshot_outranks_partial_without_losing_partial_failure(self):
        inputs = [dict(fact(), control_result='pass'),
                  dict(fact(), control_result='fail', complete=False, availability='partial',
                       observed_at='2026-09-11', source_file='partial.csv')]
        rows, _, control = self.check(inputs)
        self.assertEqual({row['selection'] for row in rows}, {'selected', 'superseded'})
        self.assertEqual(next(row for row in rows if not row['complete'])['control_result'], 'fail')
        self.assertEqual(control['status'], 'Observed')

    def test_material_qualifications_do_not_become_exact_duplicates(self):
        rows, _, _ = self.check([dict(fact(), control_result='pass', source_file='a.csv'),
                                 dict(fact(), control_result='pass', source_file='b.csv',
                                      qualifications=['Limited business population validation'])])
        self.assertNotIn('duplicate', [row['selection'] for row in rows])
        self.assertTrue(any('Limited business population' in row['qualification'] for row in rows))

    def test_equal_identity_with_different_qualifications_is_permutation_stable(self):
        inputs = [dict(fact(), control_result='pass', qualifications=['Scope review required']),
                  dict(fact(), control_result='pass', qualifications=['Owner review required'])]
        first, _, _ = self.check(inputs)
        second, _, _ = self.check(list(reversed(inputs)))
        self.assertEqual(first, second)
        self.assertNotIn('duplicate', [row['selection'] for row in first])


class ProvenanceTests(unittest.TestCase):
    def qualify(self, row=None, bundle=None):
        row = observation() if row is None else row
        bundle = evidence() if bundle is None else bundle
        qualified, normalized = _qualify_record(row, bundle, date.fromisoformat(DAY), TENANT)
        result = build_assessment_result([row], bundle, evaluation_date=DAY, expected_tenant_id=TENANT)
        control = next(item for item in result['controls'] if item['control_id'] == 'IDENTITY.MFA')
        return qualified, normalized, control

    def test_matched_source_has_exact_metadata_and_supported_control(self):
        row, fact, control = self.qualify()
        self.assertEqual((row['EvidenceComplete'], row['EvidenceScope'], fact['tenant_id'],
                          row['ObservationDate'], row['SourceFile']),
                         (True, source()['scope'], TENANT, DAY, 'fictional-collection.json'))
        self.assertEqual(row['EvidenceStatus'], 'supported')
        self.assertEqual(control['status'], 'Action required')

    def test_unmapped_missing_and_outcomeless_sources_cannot_use_global_defaults(self):
        cases = [{}, {'unrelated': source()}, {'auth_methods': {'complete': True}}]
        for states in cases:
            with self.subTest(states=states):
                row, fact, control = self.qualify(bundle=evidence(states))
                self.assertFalse(row['EvidenceComplete'])
                self.assertNotEqual(row['EvidenceStatus'], 'supported')
                self.assertEqual(control['status'], 'Not established')
                self.assertTrue(row['Qualification'])
                if 'auth_methods' not in states:
                    self.assertEqual((fact['tenant_id'], row['EvidenceScope'], row['ObservationDate'], row['SourceFile']),
                                     ('', '', '', ''))

    def test_explicit_source_alias_retains_original_date(self):
        row, fact, control = self.qualify(observation(EvidenceSource='entra_auth_methods'),
                                         evidence({'auth_methods': source(collected_at='2026-07-01')}))
        self.assertEqual(row['ObservationDate'], '2026-07-01')
        self.assertEqual(row['Freshness'], 'stale')
        self.assertEqual(row['SourceFile'], 'fictional-collection.json')
        self.assertEqual(control['status'], 'Not established')

    def test_ambiguous_alias_and_cache_order_never_choose_a_favorable_source(self):
        states = {'auth_methods': source(), 'entra_auth_methods': source(availability_status='unavailable', complete=False),
                  'entra_unrelated': source(source_type='purview_cache')}
        outputs = []
        for items in itertools.permutations(states.items()):
            row, fact, control = self.qualify(bundle=evidence(dict(items)))
            self.assertNotEqual(row['EvidenceStatus'], 'supported')
            self.assertEqual(control['status'], 'Not established')
            outputs.append((row, fact))
        self.assertTrue(all(item == outputs[0] for item in outputs))

    def test_unrelated_cache_is_not_a_source_association(self):
        row, fact, control = self.qualify(bundle=evidence({'entra_unrelated': source(source_type='purview_cache')}))
        self.assertEqual(row['ObservationDate'], '')
        self.assertFalse(row['EvidenceComplete'])
        self.assertEqual(control['status'], 'Not established')

    def test_matched_collection_inherits_only_through_explicit_association(self):
        state = {'availability_status': 'available', 'complete': True, 'scope': source()['scope']}
        row, fact, control = self.qualify(bundle=evidence({'auth_methods': state}))
        self.assertEqual((fact['tenant_id'], row['ObservationDate'], row['SourceFile']),
                         (TENANT, DAY, 'fictional-collection.json'))
        self.assertEqual(control['status'], 'Action required')

    def test_complete_empty_partial_and_failure_states_remain_distinct(self):
        for status in ['available', 'partial', 'failed', 'unavailable', 'unsupported', 'unlicensed', 'inaccessible', 'not_requested']:
            with self.subTest(status=status):
                usable = status in {'available', 'partial'}
                state = source(availability_status=status, available=usable, complete=status == 'available',
                               records_collected=0 if status == 'available' else 1 if status == 'partial' else 0)
                row, fact, control = self.qualify(bundle=evidence({'auth_methods': state}))
                self.assertEqual(fact['availability'], status)
                self.assertEqual(row['EvidenceComplete'], status == 'available')
                self.assertEqual(control['status'], 'Action required' if status == 'available' else 'Not established')
                if not usable:
                    self.assertIsNone(fact['value'])
                self.assertEqual(source_availability(SimpleNamespace(collection_status={'auth_methods': state}), 'auth_methods'), status)

    def test_failed_portal_action_is_not_promoted_as_positive_partial_evidence(self):
        row, _, control = self.qualify(observation(SourceType='portal_export', ObservationDate=DAY,
                                                   EvidenceScope='returned users'),
                                       evidence({'auth_methods': source(availability_status='unavailable', complete=False)}))
        self.assertNotEqual(row['EvidenceStatus'], 'supported')
        self.assertEqual(control['status'], 'Not established')

    def test_live_and_offline_roundtrip_preserve_matched_and_missing_provenance(self):
        for states in ({'auth_methods': source()}, {}):
            original = evidence(states)
            replay = _decode(_encode(original))
            replay['collection_context']['mode'] = 'offline'
            before = copy.deepcopy(original)
            self.assertEqual(self.qualify(bundle=original), self.qualify(bundle=replay))
            self.assertEqual(original, before)

    def test_unknown_does_not_become_measured_zero(self):
        for status in ['failed', 'unavailable', 'unsupported', 'unlicensed', 'not_requested', 'unknown']:
            row = normalize_observation(dict(fact(), availability=status), evaluation_date=DAY)
            self.assertIsNone(row['value'])
        self.assertEqual(normalize_observation(fact(), evaluation_date=DAY)['value'], 0)

    def test_retained_investigation_envelope_is_an_explicit_association(self):
        state = source(source_api='https://graph.microsoft.com/v1.0/fictional')
        row = observation()
        row.pop('EvidenceSource')
        row['InvestigationEvidence'] = {'records': [{'id': 'fictional'}], 'source': state}
        qualified, normalized, control = self.qualify(row, evidence({}))
        self.assertEqual(qualified['EvidenceStatus'], 'supported')
        self.assertEqual(qualified['ObservationDate'], DAY)
        self.assertEqual(qualified['EvidenceScope'], state['scope'])
        self.assertEqual(control['status'], 'Action required')

    def test_row_control_results_cannot_hide_conflict(self):
        rows = [observation(Disposition='Assurance', control_result='pass'),
                observation(Disposition='Assurance', control_result='fail', Priority='Low')]
        result = build_assessment_result(rows, evidence(), evaluation_date=DAY, expected_tenant_id=TENANT)
        control = next(row for row in result['controls'] if row['control_id'] == 'IDENTITY.MFA')
        self.assertEqual(control['status'], 'Not established')
        self.assertTrue(any(row['EvidenceStatus'] == 'conflict' for row in result['actions']))

    def test_unmatched_export_does_not_inherit_an_unrelated_report_date(self):
        row = observation(Service='Data Exposure', SourceType='portal_export', SourceFile='undated.csv',
                          EvidenceScope='returned sites', EvidenceComplete=False)
        row.pop('EvidenceSource')
        bundle = evidence({}, data_exposure={'details': [{'report_date': DAY, 'source_file': 'other.csv'}]})
        qualified, normalized = _qualify_record(row, bundle, date.fromisoformat(DAY), TENANT)
        self.assertEqual(qualified['ObservationDate'], '')
        self.assertNotEqual(qualified['EvidenceStatus'], 'supported')

    def test_explicit_control_result_without_retained_locator_is_unverified(self):
        rows = [fact(source_file='', control_result='pass')]
        result = build_assessment_result([], {'observations': rows}, evaluation_date=DAY, expected_tenant_id=TENANT)
        control = next(row for row in result['controls'] if row['control_id'] == 'CONTENT.PERMISSIONS')
        self.assertEqual(control['status'], 'Not established')


class AdapterTests(unittest.TestCase):
    def test_failed_or_partial_access_review_collection_never_claims_absence(self):
        for status in ['unavailable', 'failed', 'partial', 'unknown']:
            with self.subTest(status=status):
                client = SimpleNamespace(available=True, access_review_summary={'total_definitions': 0},
                    collection_status={'access_reviews': source(availability_status=status, complete=False)}, pim_summary={})
                insights = extract_entra_insights_from_client(client)
                with redirect_stdout(io.StringIO()):
                    rows = get_recommendation('Fictional', entra_insights=insights)
                self.assertFalse(any('No access reviews configured' in row['Observation'] for row in rows))

    def test_complete_empty_access_reviews_remain_measured_zero(self):
        client = SimpleNamespace(available=True, access_review_summary={'total_definitions': 0},
            collection_status={'access_reviews': source(records_collected=0)}, pim_summary={})
        insights = extract_entra_insights_from_client(client)
        with redirect_stdout(io.StringIO()):
            rows = get_recommendation('Fictional', entra_insights=insights)
        self.assertTrue(any('No access reviews configured' in row['Observation'] for row in rows))

    def test_purview_available_flags_follow_the_boolean_source_contract(self):
        true_values = [True, 'true', 'True', 1, '1']
        false_values = [False, 'false', 'False', 0, '0', None, '', [], {}]
        for value in true_values + false_values:
            with self.subTest(value=value):
                payload = {'labels': {'available': value, 'labels': []}}
                _, available = extract_collection(payload, 'labels', 'labels')
                state = collection_state(payload, 'labels')
                self.assertEqual(available, value in true_values)
                self.assertEqual(state['available'], available)
                self.assertEqual(state['records_collected'], 0)
                if not available:
                    self.assertNotEqual(state['availability_status'], 'available')

    def test_purview_legacy_empty_missing_and_reason_only_agree(self):
        for container, expected in [({'labels': []}, True), ({}, False), ({'reason': 'skipped'}, False)]:
            rows, available = extract_collection({'labels': container}, 'labels', 'labels')
            state = collection_state({'labels': container}, 'labels')
            self.assertEqual(rows, [])
            self.assertEqual(available, expected)
            self.assertEqual(state['available'], expected)
            self.assertEqual(state['records_collected'], 0)

    def test_source_helper_does_not_coerce_unknown_flags_to_complete(self):
        for field in ['available', 'complete']:
            for value in ['false', 'False', '0', None, '', [], {}]:
                with self.subTest(field=field, value=value):
                    state = source(**{field: value})
                    self.assertFalse(source_is_complete(SimpleNamespace(collection_status={'auth_methods': state}), 'auth_methods'))

    def test_explicit_empty_outcome_cannot_fall_back_to_a_legacy_success_flag(self):
        client = SimpleNamespace(collection_status={'auth_methods': {}}, data_sources={'auth_methods': True})
        self.assertFalse(source_is_complete(client, 'auth_methods'))
        self.assertEqual(source_availability(client, 'auth_methods'), 'unknown')
        self.assertEqual(source_availability(client, 'auth_methods', source()), 'unknown')

    def test_access_review_producer_value_reaches_assessment_without_false_zero(self):
        client = SimpleNamespace(available=True, access_review_summary={'total_definitions': 2, 'active_reviews': 1,
                                                                       'role_assignment_reviews': 1}, pim_summary={})
        insights = extract_entra_insights_from_client(client)
        with redirect_stdout(io.StringIO()):
            rows = get_recommendation('Fictional', entra_insights=insights)
        result = build_assessment_result(rows, evidence(), evaluation_date=DAY, expected_tenant_id=TENANT)
        observations = [row['Observation'] for row in result['recommendations']]
        self.assertTrue(any('2 access review definition(s)' in text for text in observations))
        self.assertFalse(any('No access reviews configured' in text for text in observations))

    def test_broader_adoption_retains_provider_and_use_case_readiness(self):
        use_case = dict(name='Summaries', business_owner='Sponsor', intended_users=['Group'],
                        data_classifications=['Public'], approved_data=['Public'], prohibited_data=['Restricted'],
                        required_outcome='Quality', risk_measurements=['Review'], expand_stop_decision='Approval',
                        provider='Fictional', product='AI', tier='Enterprise')
        profile = {'available': True, 'products': [use_case], 'use_cases': [use_case]}
        providers = {'rows': [{'Provider': 'Fictional', 'Product': 'AI', 'Tier': 'Enterprise',
                              'Approval State': 'Approved', 'Freshness': 'Fresh', 'Complete': 'Yes'}]}
        result = assess_provider_and_use_cases(profile, providers, 'Ready for broader adoption')
        self.assertEqual(result['foundation']['Conclusion'], 'Ready for broader adoption')
        self.assertEqual(result['use_case_conclusion'], 'Ready')


class LegacyGraphTests(unittest.IsolatedAsyncioTestCase):
    def client(self, responses):
        client = object.__new__(GraphRestClient)
        client.get_json = AsyncMock(side_effect=responses)
        return client

    async def test_compatibility_endpoint_reads_all_pages_and_retains_envelope(self):
        client = self.client([{'value': [{'id': 'one'}], '@odata.nextLink': '/next'}, {'value': [{'id': 'two'}]}])
        response = await _Endpoint(client, ['users']).get()
        self.assertEqual([row.id for row in response.value], ['one', 'two'])
        state = response['_collection_metadata']
        self.assertEqual((state['availability_status'], state['pages_collected']), ('available', 2))
        self.assertIn('collection_started_at', state)
        self.assertIn('source_api', state)

    async def test_interrupted_compatibility_endpoint_retains_partial_records(self):
        client = self.client([{'value': [{'id': 'one'}], '@odata.nextLink': '/next'}, GraphRequestError(403, 'Fictional denied')])
        response = await _Endpoint(client, ['users']).get()
        self.assertEqual([row.id for row in response.value], ['one'])
        self.assertEqual(response['_collection_metadata']['availability_status'], 'partial')
        self.assertTrue(response['_collection_metadata']['truncated'])

    async def test_malformed_collection_is_not_a_successful_zero(self):
        for payload in [{}, {'value': {}}, {'unexpected': 'body'}]:
            with self.subTest(payload=payload):
                result = await self.client([payload]).get_collection('/v1.0/users')
                self.assertNotEqual(result['availability_status'], 'available')

    async def test_recommendation_dispatch_retains_sources_through_offline_replay(self):
        from Core.get_recommendation import get_recommendation as dispatch
        from Core.assessment_catalog import collect_assessment_sources
        client = self.client([{'value': [{'id': 'one'}], '@odata.nextLink': '/next'},
                              {'value': [{'id': 'two'}]}])
        rows = await dispatch('m365', 'GRAPH_CONNECTORS_COPILOT', 'Fictional', client=client)
        replay = _decode(_encode(rows))
        sources = collect_assessment_sources([], recommendations=replay)
        self.assertEqual(sum(len(dataset['records']) for values in sources.values() for dataset in values), 2)
        states = [dataset['source'] for values in sources.values() for dataset in values]
        self.assertEqual(states[0]['pages_collected'], 2)
        self.assertEqual(states[0]['availability_status'], 'available')

    async def test_copilot_studio_deployment_retains_paginated_sources_for_replay(self):
        from Recommendations.copilot_studio.COPILOT_STUDIO_IN_COPILOT_FOR_M365 import get_deployment_status
        client = self.client([])
        async def fetch(path, **kwargs):
            if path.endswith('/next'):
                return {'value': [{'id': 'two', 'assignedLicenses': []}]}
            return {'value': [{'id': 'one', 'assignedLicenses': []}], '@odata.nextLink': path + '/next'}
        client.get_json.side_effect = fetch
        result = await get_deployment_status(client)
        self.assertEqual((result['knowledge_sources'], result['connector_count']), (4, 2))
        self.assertIn('collection_status', result)
        self.assertEqual(_decode(_encode(result)), result)
        self.assertTrue(result['assessment_datasets'])
        self.assertEqual(sum(len(dataset['records']) for values in result['assessment_datasets'].values()
                             for dataset in values), 6)


if __name__ == '__main__':
    unittest.main()

"""Invariants for control correlation, action lanes and customer-action consolidation.

Every case reuses a declared correlation scenario and production functions only.
"""
import copy
import html as html_module
import io
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

try:
    from tests import correlation_scenarios as harness
    from tests import identity_scenarios as identity_harness
    from tests.provenance_fixture import fictional_publication
except ImportError:  # unittest discover -s tests imports modules from the tests folder
    import correlation_scenarios as harness
    import identity_scenarios as identity_harness
    from provenance_fixture import fictional_publication

LEGACY = 'entra.signins.legacy_auth'
POLICY_SCENARIOS = ('C02_policy_present_no_legacy_events', 'C03_policy_blocks_legacy_events',
                    'C04_successful_events_excluded_accounts', 'C05_successful_events_nonexcluded_users',
                    'C06_correlation_unavailable', 'C07_policy_report_only', 'C08_enforcement_after_events',
                    'C09_complete_policy_partial_signins')


def scenario(name):
    return harness.run(harness.load(name))


def legacy_row(result):
    return next(row for row in result['recommendations'] if row.get('FindingKey') == LEGACY)


@fictional_publication
class CorrelationInvariantTests(unittest.TestCase):
    def test_existing_applicable_control_is_named_in_the_related_narrative(self):
        for name in POLICY_SCENARIOS:
            with self.subTest(scenario=name):
                row = legacy_row(scenario(name)[0])
                correlation = row['ControlCorrelation']
                self.assertTrue(correlation['controls'], 'the existing policy is omitted from the correlation')
                self.assertIn('Block legacy authentication', correlation['customer_narrative'])
                if row.get('RecommendationId') in {action['RecommendationId'] for action in scenario(name)[0]['actions']}:
                    self.assertIn('Block legacy authentication', harness.narrative(row))

    def test_configured_control_is_not_proof_of_operation(self):
        for name in ('C02_policy_present_no_legacy_events', 'C09_complete_policy_partial_signins'):
            with self.subTest(scenario=name):
                correlation = legacy_row(scenario(name)[0])['ControlCorrelation']
                self.assertEqual(correlation['configuration_state'], 'ControlConfigured')
                self.assertNotEqual(correlation['operation_state'], 'ControlOperatingAsExpected')

    def test_adverse_event_is_not_proof_of_control_failure(self):
        for name in ('C04_successful_events_excluded_accounts', 'C06_correlation_unavailable', 'C08_enforcement_after_events'):
            with self.subTest(scenario=name):
                state = legacy_row(scenario(name)[0])['ControlCorrelation']['state']
                self.assertNotIn(state, {'ControlBypassObserved', 'ControlIneffective', 'ControlAbsent'})

    def test_exclusions_remain_separate_from_general_policy_scope(self):
        correlation = legacy_row(scenario('C04_successful_events_excluded_accounts')[0])['ControlCorrelation']
        self.assertEqual(correlation['exclusions']['count'], 1)
        self.assertNotIn('exclusions', correlation['scope'])
        self.assertEqual(correlation['scope']['target'], 'All users')

    def test_partial_operation_neither_negates_nor_is_filled_by_configuration(self):
        result, _ = scenario('C09_complete_policy_partial_signins')
        control = identity_harness.control(result, 'IDENTITY.AUTH')
        self.assertEqual(control['configuration_result'], 'supported')
        row = legacy_row(result)
        self.assertEqual(row['Disposition'], 'Coverage')
        self.assertEqual(row['ControlCorrelation']['operation_state'], 'ControlOperationUnresolved')
        self.assertEqual(control['status'], 'Not established')

    def test_license_only_rows_never_become_actions_or_blockers(self):
        result, _ = scenario('C18_license_only_capability')
        self.assertEqual(result['actions'], [])
        self.assertEqual(result['blockers'], [])
        self.assertEqual(result.get('customer_actions'), [])
        for row in result['recommendations']:
            self.assertNotEqual(row.get('Status'), 'Action Required')
            self.assertIn(row.get('FindingClass'), {'Reference inventory', 'Adoption context'})

    def test_supporting_observations_do_not_inflate_counts(self):
        for name in harness.names():
            spec = harness.load(name)
            if spec['kind'] == 'outputs':
                continue
            with self.subTest(scenario=name):
                result, _ = harness.run(spec)
                members = [member['recommendation_id'] for action in result['customer_actions'] for member in action['members']]
                self.assertEqual(sorted(members), sorted(row['RecommendationId'] for row in result['actions']),
                                 'every open technical action belongs to exactly one customer action')
                self.assertEqual(result['counts']['customer_actions'], len(result['customer_actions']))
                outside = {row['RecommendationId'] for row in result['recommendations']
                           if row.get('Disposition') in {'Reference', 'Opportunity', 'Assurance'}}
                self.assertFalse(outside & set(members), 'a supporting observation was counted as an action')

    def test_one_condition_and_decision_create_one_customer_action(self):
        for name, keys in (('C15_multiple_measures_same_site', ['data_exposure.anonymous_links', 'data_exposure.broad_internal_access']),
                           ('C17_multiple_findings_one_action', ['purview.raw.sensitivity_labels', 'purview.raw.label_policies'])):
            with self.subTest(scenario=name):
                result, _ = scenario(name)
                self.assertEqual(len(harness.customer_actions_with(result, keys)), 1)
                for key in keys:
                    self.assertTrue(any(row.get('FindingKey') == key for row in result['actions']), 'supporting evidence was removed')

    def test_unrelated_findings_with_similar_wording_are_not_merged(self):
        result, _ = scenario('C19_planning_opportunities_excluded')
        rows = [harness._declared_row({'key': 'fixture.unrelated.one', 'feature': 'Confirm enforcement of retention settings',
                                       'observation': 'Retention settings are in simulation mode.'}),
                harness._declared_row({'key': 'purview.raw.dlp_mode', 'feature': 'Confirm enforcement of data loss prevention policies',
                                       'observation': 'All returned data loss prevention policies are in simulation mode.'})]
        from Core.assessment_result import build_assessment_result
        bundle = harness._bundle()
        result = build_assessment_result(rows, bundle, evaluation_date=harness.EVALUATION, expected_tenant_id=harness.TENANT)
        groups = [action for action in result['customer_actions']
                  if {'fixture.unrelated.one', 'purview.raw.dlp_mode'} <= {m['finding_key'] for m in action['members']}]
        self.assertEqual(groups, [])

    def test_renderers_do_not_recalculate_the_correlation(self):
        from Core.customer_report import render_customer_report
        result, bundle = scenario('C04_successful_events_excluded_accounts')
        result = copy.deepcopy(result)
        sentinel = 'Declared narrative sentinel for the renderer test.'
        for bucket in ('recommendations', 'actions'):
            for row in result[bucket]:
                if row.get('FindingKey') == LEGACY:
                    row['ControlCorrelation']['customer_narrative'] = sentinel
        for action in result['customer_actions']:
            for member in action['members']:
                if member['finding_key'] == LEGACY:
                    action['narrative'] = sentinel
        page = html_module.unescape(render_customer_report(result, bundle, 'Contoso'))
        self.assertIn(sentinel, page)

    def test_customer_and_technical_outputs_agree(self):
        from openpyxl import load_workbook
        from Core.assessment_serialization import read_assessment_result, write_assessment_result
        from Core.customer_report import render_customer_report
        from Core.export_recommendations import export_to_excel
        from Core.workbook_layout import technical_workbook_path
        spec = harness.load('C20_customer_technical_agree')
        result, bundle = harness.run(spec)
        row = legacy_row(result)
        fragment = re.sub(r'\s+', ' ', row['ControlCorrelation']['customer_narrative']).strip()[:80]
        page = re.sub(r'\s+', ' ', html_module.unescape(render_customer_report(result, bundle, 'Contoso')))
        self.assertIn(fragment, page)
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            path = export_to_excel(result['recommendations'], filename='fiction.xlsx', evidence_bundle=bundle, output_dir=directory)
            cells = []
            for workbook_path in (path, technical_workbook_path(path)):
                workbook = load_workbook(workbook_path, read_only=True)
                try:
                    cells.extend(str(value) for sheet in workbook for line in sheet.values for value in line if isinstance(value, str))
                finally:
                    workbook.close()
            snapshot = Path(directory) / 'result.json'
            write_assessment_result(snapshot, result)
            frozen = read_assessment_result(snapshot)
        workbook_text = re.sub(r'\s+', ' ', ' '.join(cells))
        self.assertIn(fragment, workbook_text)
        self.assertIn(row['ActionLane'], workbook_text.replace(' ', ''))
        frozen_row = legacy_row(frozen)
        self.assertEqual(frozen_row['ControlCorrelation'], row['ControlCorrelation'])
        self.assertEqual(frozen['customer_actions'], result['customer_actions'])

    def test_stable_ids_unchanged_when_semantics_do_not_change(self):
        spec = harness.load('C04_successful_events_excluded_accounts')
        first, _ = harness.run(spec)
        reordered = copy.deepcopy(spec)
        reordered['patch']['client']['signin_logs'].reverse()
        second, _ = harness.run(reordered)
        self.assertEqual(legacy_row(first)['RecommendationId'], legacy_row(second)['RecommendationId'])
        self.assertEqual([a['customer_action_id'] for a in first['customer_actions']],
                         [a['customer_action_id'] for a in second['customer_actions']])
        self.assertEqual(legacy_row(first)['ControlCorrelation']['state'], legacy_row(second)['ControlCorrelation']['state'])

    def test_offline_replay_preserves_the_correlated_conclusion(self):
        spec = harness.load('C04_successful_events_excluded_accounts')
        live, _ = harness.run(spec)
        tenant = identity_harness.tenant_for(spec)
        client = identity_harness.build_client(tenant)
        from Core.get_entra_info import build_entra_recommendations
        saved = build_entra_recommendations(tenant['license_profiles'][tenant['license_profile']], client)
        replay, _, _ = identity_harness.run(spec, saved_rows=copy.deepcopy(saved), client=client)
        self.assertEqual(legacy_row(live)['ControlCorrelation'], legacy_row(replay)['ControlCorrelation'])
        self.assertEqual(legacy_row(live)['RecommendationId'], legacy_row(replay)['RecommendationId'])


if __name__ == '__main__':
    unittest.main()

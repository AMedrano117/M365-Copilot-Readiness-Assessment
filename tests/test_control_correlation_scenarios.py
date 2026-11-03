"""Declared control-correlation and actionability scenarios.

Expectations live in tests/scenarios/correlation/*.json and were declared before
the production change. tests/correlation_scenarios.py runs them through the
production producers and the shared assessment result without adding semantics.
"""
import unittest

try:
    from tests import correlation_scenarios as harness
    from tests.provenance_fixture import fictional_publication
except ImportError:  # unittest discover -s tests imports modules from the tests folder
    import correlation_scenarios as harness
    from provenance_fixture import fictional_publication


@fictional_publication
class CorrelationScenarioTests(unittest.TestCase):
    def check_row(self, result, check):
        rows = harness.find_row(result, check)
        label = check.get('finding_key') or check.get('finding_key_prefix') or check.get('feature')
        self.assertEqual(len(rows), 1, f'expected exactly one {label} record, found {len(rows)}')
        row = rows[0]
        correlation = row.get('ControlCorrelation') or {}
        actions = {action.get('RecommendationId') for action in result['actions']}
        for field, actual in (('correlation_state', correlation.get('state')),
                              ('configuration_state', correlation.get('configuration_state')),
                              ('operation_state', correlation.get('operation_state')),
                              ('lane', row.get('ActionLane')), ('readiness_treatment', row.get('ReadinessTreatment')),
                              ('disposition', row.get('Disposition')), ('finding_class', row.get('FindingClass'))):
            if field in check:
                self.assertEqual(actual, check[field], f'{label} {field}')
        if 'lane_not' in check:
            self.assertNotEqual(row.get('ActionLane'), check['lane_not'], f'{label} lane')
        if 'disposition_not' in check:
            self.assertNotEqual(row.get('Disposition'), check['disposition_not'], f'{label} disposition')
        if 'status_not' in check:
            self.assertNotEqual(row.get('Status'), check['status_not'], f'{label} status')
        if 'open_action' in check:
            self.assertEqual(row.get('RecommendationId') in actions, check['open_action'], f'{label} open action')
        if 'blocks_readiness' in check:
            blockers = {item.get('RecommendationId') for item in result['blockers']}
            self.assertEqual(row.get('RecommendationId') in blockers, check['blocks_readiness'], f'{label} blocks readiness')
        if check.get('exclusions_separate'):
            exclusions = correlation.get('exclusions') or {}
            self.assertTrue(exclusions, f'{label} exclusions are not a separate component')
            self.assertNotIn('exclusions', correlation.get('scope') or {}, 'exclusions folded into general scope')
        if 'unvalidated_exclusions' in check:
            self.assertEqual((correlation.get('exclusions') or {}).get('unvalidated'), check['unvalidated_exclusions'])
        for name, expected in (check.get('components') or {}).items():
            self.assertEqual((correlation.get('components') or {}).get(name, {}).get('state'), expected, f'{label} component {name}')
        text = harness.narrative(row)
        for pattern in check.get('required', []):
            self.assertRegex(text, pattern, f'{label} required qualification')
        for pattern in check.get('prohibited', []):
            self.assertNotRegex(text, pattern, f'{label} prohibited conclusion')
        steps = ' '.join(correlation.get('next_steps') or [])
        for pattern in check.get('next_steps_required', []):
            self.assertRegex(steps, pattern, f'{label} next step')

    def check_customer_action(self, result, check):
        actions = harness.customer_actions_with(result, check['keys'])
        self.assertEqual(len(actions), check['count'], f"customer actions for {check['keys']}: "
                         + '; '.join(action.get('title', '') for action in actions))
        for action in actions:
            if 'lane' in check:
                self.assertEqual(action.get('lane'), check['lane'], 'customer action lane')
            if 'members' in check:
                self.assertEqual(len(action.get('members') or []), check['members'], 'customer action members')
            for pattern in check.get('prohibited', []):
                self.assertNotRegex(harness.customer_action_text(action), pattern, 'customer action prohibited text')

    def run_scenario(self, name):
        spec = harness.load(name)
        if spec['kind'] == 'outputs':
            self.skipTest('Output agreement is asserted in test_control_correlation_invariants.')
        result, _ = harness.run(spec)
        expected = spec['expected']
        for check in expected.get('rows', []):
            with self.subTest(row=check.get('finding_key') or check.get('feature') or check.get('finding_key_prefix')):
                self.check_row(result, check)
        for check in expected.get('customer_actions', []):
            with self.subTest(customer_action=check['keys']):
                self.check_customer_action(result, check)
        for check in expected.get('controls', []):
            with self.subTest(control=check['control']):
                control = next(row for row in result['controls'] if row['control_id'] == check['control'])
                for field, key in (('control_status', 'status'), ('configuration_result', 'configuration_result')):
                    if field in check:
                        self.assertEqual(control[key], check[field], f"{check['control']} {key}")
                if 'control_status_not' in check:
                    self.assertNotEqual(control['status'], check['control_status_not'])
        for check in expected.get('overlaps', []):
            overlaps = [item for item in result.get('population_overlaps') or [] if set(item.get('finding_keys') or []) == set(check['keys'])]
            self.assertEqual(len(overlaps), 1, 'population overlap not reported')
            self.assertEqual(overlaps[0]['overlapping_devices'], check['devices'])
        if 'customer_action_total' in expected:
            self.assertEqual(result['counts'].get('customer_actions'), expected['customer_action_total'])
        if 'planning_total' in expected:
            self.assertEqual(result['counts'].get('planning'), expected['planning_total'])


def _scenario_test(name):
    def test(self):
        self.run_scenario(name)
    test.__doc__ = harness.load(name)['description']
    return test


for _name in harness.names():
    setattr(CorrelationScenarioTests, 'test_' + _name, _scenario_test(_name))


if __name__ == '__main__':
    unittest.main()

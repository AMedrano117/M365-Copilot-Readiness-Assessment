"""Declared scenarios for materiality, consent governance and composite control status.

Expectations live in tests/scenarios/methodology/*.json and were declared before
the production change. Every scenario runs through the shared production path
(tests/identity_scenarios.py adds no semantics).
"""
import re
import unittest

try:
    from tests import identity_scenarios as harness
    from tests import test_identity_scenarios as identity_tests
    from tests.provenance_fixture import fictional_publication
except ImportError:  # unittest discover -s tests imports modules from the tests folder
    import identity_scenarios as harness
    import test_identity_scenarios as identity_tests
    from provenance_fixture import fictional_publication

DIRECTORY = harness.METHODOLOGY_DIR


def _component(result, key):
    for control in result['controls']:
        for item in control.get('component_results') or []:
            if item.get('finding_key') == key:
                return control, item
    return None, None


class MethodologyScenarioTests(identity_tests.DeclaredScenarioTests):
    def check(self, result, check):
        base = {key: value for key, value in check.items() if key not in {
            'action_type', 'pilot_impact', 'component_result', 'component_required', 'family_actions',
            'review_closure', 'control_qualification'}}
        super().check(result, base)
        key = check.get('finding_key')
        if key:
            row = next(item for item in result['recommendations'] if item.get('FindingKey') == key)
            action = next((item for item in result['actions'] if item.get('FindingKey') == key), None)
            if 'action_type' in check:
                self.assertIsNotNone(action, f'{key} has no action')
                self.assertEqual(action['ActionType'], check['action_type'], f'{key} action type')
            if 'pilot_impact' in check:
                self.assertEqual(action['PilotImpact'], check['pilot_impact'], f'{key} readiness impact')
            if 'family_actions' in check:
                self.assertEqual(sum(item.get('FindingKey') == key for item in result['actions']), check['family_actions'])
            if check.get('review_closure'):
                self.assertTrue(row.get('ReviewClosure'), f'{key} was not answered by the review')
                self.assertIsNone(action, f'{key} remained an open action after the review')
            if 'component_result' in check:
                _, component = _component(result, key)
                self.assertIsNotNone(component, f'{key} is not a component of any control')
                self.assertEqual(component['result'], check['component_result'], f'{key} component result')
                if 'component_required' in check:
                    self.assertEqual(component['required'], check['component_required'], f'{key} component required')
        if check.get('control') and check.get('control_qualification'):
            control = harness.control(result, check['control'])
            self.assertRegex(' '.join(control.get('qualifications') or []), check['control_qualification'])

    def run_scenario(self, name):
        spec = harness.load(name, DIRECTORY)
        result, _, _ = harness.run(spec)
        for check in spec['expected']['checks']:
            with self.subTest(check=check.get('finding_key') or check.get('control')):
                self.check(result, check)
        self.assert_one_action_per_condition(result)


for _name in harness.scenario_names(DIRECTORY, 'M*.json'):
    setattr(MethodologyScenarioTests, 'test_' + _name,
            (lambda name: lambda self: self.run_scenario(name))(_name))
# The inherited identity scenario methods belong to the identity module.
for _attribute in [name for name in dir(identity_tests.DeclaredScenarioTests) if name.startswith('test_')]:
    if not _attribute.startswith('test_M'):
        setattr(MethodologyScenarioTests, _attribute, None)


@fictional_publication
class CompositeOutputConsistencyTests(unittest.TestCase):
    """Every output renders the shared control and treatment; none recalculates it."""

    def test_outputs_and_snapshot_carry_the_shared_result(self):
        import html as html_module
        import io
        import tempfile
        from contextlib import redirect_stdout
        from pathlib import Path
        from openpyxl import load_workbook
        from Core.assessment_serialization import read_assessment_result, write_assessment_result
        from Core.customer_report import render_customer_report
        from Core.export_recommendations import export_to_excel
        from Core.pilot_summary import render_pilot_summary
        from Core.workbook_layout import technical_workbook_path
        for name, key, absent in (('M01_isolated_exposure_unknown_relevance', 'data_exposure.anonymous_links', 'Blocks readiness'),
                                  ('M06b_managed_recommended_policy_any_publisher', 'entra.apps.user_consent_assignment', 'without administrator review')):
            spec = harness.load(name, DIRECTORY)
            with self.subTest(scenario=name):
                result, bundle, _ = harness.run(spec)
                action = next(row for row in result['actions'] if row.get('FindingKey') == key)
                fragment = re.sub(r'\s+', ' ', action['Observation']).strip()[:60]
                customer = html_module.unescape(render_customer_report(result, bundle, 'Contoso'))
                summary = html_module.unescape(render_pilot_summary(result, bundle, 'Contoso', 'report.html', 'workbook.xlsx'))
                with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
                    path = export_to_excel(result['recommendations'], filename='fiction.xlsx', evidence_bundle=bundle, output_dir=directory)
                    cells = []
                    for workbook_path in (path, technical_workbook_path(path)):
                        workbook = load_workbook(workbook_path, read_only=True)
                        try:
                            cells.extend(str(value) for sheet in workbook for row in sheet.values for value in row if isinstance(value, str))
                        finally:
                            workbook.close()
                    snapshot = Path(directory) / 'result.json'
                    write_assessment_result(snapshot, result)
                    frozen = read_assessment_result(snapshot)
                workbook_text = re.sub(r'\s+', ' ', ' '.join(cells))
                self.assertIn(fragment, customer)
                self.assertIn(fragment, workbook_text)
                frozen_action = next(row for row in frozen['actions'] if row.get('FindingKey') == key)
                self.assertEqual((frozen_action['PilotImpact'], frozen_action['ReadinessTreatment'], frozen_action['ActionType']),
                                 (action['PilotImpact'], action['ReadinessTreatment'], action['ActionType']))
                self.assertEqual([c['status'] for c in frozen['controls']], [c['status'] for c in result['controls']])
                self.assertEqual(frozen['decision'], result['decision'])
                blocking = {row['RecommendationId'] for row in result['blockers']}
                self.assertNotIn(action['RecommendationId'], blocking)
                self.assertNotIn(action['Observation'][:60], ' '.join(row.get('Observation', '')[:60] for row in result['blockers']))
                self.assertNotIn(absent, re.sub(r'\s+', ' ', action['Observation'] + ' ' + action['Recommendation']))
                self.assertTrue(customer and summary)


if __name__ == '__main__':
    unittest.main()

"""Compact plan presentation preserves read-only checks and full diagnostics."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

from Core.console_reporting import configure_console
from Core.historical_import import NOTICE
from Core.run_plan import plan_json, plan_lines
from provenance_fixture import fictional_publication, fictional_revision
from test_run_plan import plan
from test_assessment_run_workflow import TENANT


@fictional_publication
class CompactPlanTests(unittest.TestCase):
    def test_initial_summary_omits_routine_diagnostics_without_changing_plan(self):
        value = plan()
        original = deepcopy(value)
        lines = plan_lines(value)
        self.assertLessEqual(len(lines), 9)
        self.assertIn('Run type: Initial', lines)
        self.assertIn('Mode: Live tenant collection', lines)
        self.assertIn('Scope: default selected collectors; standard permissions', lines)
        self.assertIn('Plan check: Passed', lines)
        text = '\n'.join(lines)
        for label in ('configuration hash', 'Effective configuration:', 'Internal catalog:',
                      'Optional imports absent:', 'Baseline:', 'Delta expected:', 'Dirty fingerprint:'):
            self.assertNotIn(label, text)
        self.assertEqual(value, original)
        self.assertEqual(json.loads(plan_json(value)), original)

    def test_verbose_keeps_configuration_hashes_versions_and_optional_inputs(self):
        value = plan()
        text = '\n'.join(plan_lines(value, verbose=True))
        for label in ('Run configuration hash:', 'Collector configuration hash:', 'Effective configuration:',
                      'Internal catalog:', 'Optional imports absent:', 'Source state: CleanCommitted'):
            self.assertIn(label, text)
        self.assertIn(value['catalog_version'], text)

    def test_blockers_and_unknown_warnings_stay_visible(self):
        value = plan(baseline_run_id='RUN-unexpected')
        value['warnings'].append('A future qualification must remain visible.')
        for verbose in (False, True):
            lines = plan_lines(value, verbose=verbose)
            self.assertIn('Error: initial_cannot_select_baseline', lines)
            self.assertIn('Warning: A future qualification must remain visible.', lines)
            self.assertNotIn('Publication: Eligible, subject to assessment validation', lines)
        self.assertIn('Plan check: Blocked', plan_lines(value))
        self.assertIn('Publication: Blocked', plan_lines(value))

    def test_historical_summary_retains_uncertainty_without_conflicting_qualification(self):
        value = plan(offline=True, import_historical='fictional.xlsx')
        value.update(source_revision_role='historical_importer', execution_eligible=True,
                     publication_eligible=False, validation_errors=[], warnings=[NOTICE],
                     historical_import={'source_count':1, 'recommendation_count':245,
                         'tenant_identity_basis':'operator-declared; not source-verified',
                         'evaluation_date_basis':'operator-declared'})
        value['effective_configuration']['evaluation_date'] = '2026-07-30'
        compact = '\n'.join(plan_lines(value))
        self.assertIn('Historical tenant identity: operator-declared; not source-verified', compact)
        self.assertIn('2026-07-30 (operator-declared)', compact)
        self.assertIn('Historical conclusions are unverified.', compact)
        self.assertIn('technical changes cannot be established', compact)
        self.assertIn('not a newly scored customer deliverable', compact)
        self.assertNotIn('Customer publication eligible', compact)
        detailed = '\n'.join(plan_lines(value, verbose=True))
        self.assertIn(NOTICE, detailed)
        self.assertNotIn('Output qualification: Customer publication eligible', detailed)

    def test_reassessment_combines_baseline_checks_but_keeps_comparison_conditional(self):
        value = plan()
        value['requested_run_type'] = 'Reassessment'
        value['baseline'].update(run_id='RUN-fictional', completion='completed', integrity='validated', ownership='validated')
        value['delta'].update(enabled=True, expected=True)
        lines = plan_lines(value)
        self.assertIn('Baseline: RUN-fictional (validated)', lines)
        self.assertIn('Comparison: Eligibility checked at completion', lines)
        value['baseline']['ownership'] = 'not_validated'
        value['delta']['expected'] = False
        self.assertIn('Baseline: RUN-fictional (not validated)', plan_lines(value))
        self.assertIn('Comparison: Blocked by plan validation', plan_lines(value))
        value['baseline']['technical_delta_eligible'] = False
        self.assertIn('Comparison: Not comparable; historical report baseline', plan_lines(value))
        value['delta']['enabled'] = False
        self.assertIn('Comparison: Disabled', plan_lines(value))

    def test_engineering_qualification_stays_visible_once(self):
        value = plan(source_policy='engineering-validation', assessment_purpose='Fictional check',
                     source_reason='Synthetic display verification', acknowledge_dirty_source=True)
        text = '\n'.join(plan_lines(value))
        self.assertEqual(text.count('NOT CUSTOMER-READY'), 1)
        self.assertIn('Plan check: Passed', text)

    def test_replay_keeps_original_engineering_qualification(self):
        value = plan()
        value.update(replay=True, requested_run_type='Replay', publication_eligible=False,
                     original_producing_revision=fictional_revision())
        value['sources']['tenant_collection'] = False
        lines = plan_lines(value)
        self.assertIn('Mode: Snapshot replay; no tenant collection or re-evaluation', lines)
        self.assertTrue(any('Publication: Snapshot replay;' in line and 'NOT CUSTOMER-READY' in line for line in lines))
        self.assertFalse(any(line.startswith('Scope:') for line in lines))

    def test_import_summary_does_not_dump_paths_or_unselected_imports(self):
        value = plan(sam_report=['private/customer/report.csv'])
        text = '\n'.join(plan_lines(value))
        self.assertIn('Imports: 1 selected input(s)', text)
        self.assertNotIn('private/customer', text)
        self.assertNotIn('dspm_report', text)

    def cli(self, *options, revision=None):
        output = io.StringIO()
        root = Path(__file__).resolve().parents[1]
        self.addCleanup(configure_console, verbose=False, color='auto')
        with patch('sys.argv', ['main.py', '--plan', '--run-type', 'Initial', '--tenant-id', TENANT,
                                '--color', 'never', *options]), \
             patch('Core.run_plan.capture_revision', return_value=revision or fictional_revision()), \
             patch('Core.console_setup.setup_console_encoding'), \
             patch('Core.check_dependencies.check_dependencies', side_effect=AssertionError('dependency check invoked')), \
             patch('Core.orchestrator.orchestrate', side_effect=AssertionError('collection invoked')), \
             redirect_stdout(output), self.assertRaises(SystemExit) as exit:
            runpy.run_path(str(root/'main.py'), run_name='__main__')
        return exit.exception.code, output.getvalue()

    def test_cli_honors_verbose_and_json_without_running_dependencies_or_collection(self):
        code, compact = self.cli()
        self.assertEqual(code, 0)
        self.assertIn('Plan check: Passed', compact)
        self.assertNotIn('Effective configuration:', compact)
        code, verbose = self.cli('--verbose')
        self.assertEqual(code, 0)
        self.assertIn('Effective configuration:', verbose)
        code, encoded = self.cli('--plan-format', 'json')
        self.assertEqual(code, 0)
        decoded = json.loads(encoded)
        self.assertIn('effective_configuration', decoded)
        self.assertIn('run_configuration_hash', decoded['producing_revision'])

    def test_cli_blocked_status_and_exit_code_are_preserved(self):
        revision = fictional_revision()
        revision['source_state'] = 'DirtyUnclassified'
        code, text = self.cli(revision=revision)
        self.assertEqual(code, 2)
        self.assertIn('Plan check: Blocked', text)
        self.assertIn('Error: production_requires_clean_committed_source', text)

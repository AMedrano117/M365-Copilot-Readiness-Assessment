"""Shortcut and folder acceptance checks with fictional evidence only."""
from provenance_fixture import fictional_publication
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from test_assessment_run_workflow import RunFixture, TENANT, OTHER, DAY, result
from test_assessment_run_outputs import parse
from Core.reassessment_cli import normalize_reassessment, resolve_reassessment, select_reassessment_baseline


@fictional_publication
class ReassessmentShortcutTests(RunFixture):
    def setUp(self):
        super().setUp()
        self.execution, _ = self.initial()

    def options(self, *extra):
        return parse('--reassess', self.history, *extra)

    def files(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def second(self):
        from Core.assessment_runs import prepare_run, complete_run
        execution = prepare_run('Reassessment', TENANT, evaluated_at=DAY,
            history_path=self.history, assessment_id=self.execution.identity['AssessmentId'],
            baseline_run_id=self.execution.identity['RunId'])
        complete_run(result(execution.identity), execution, self.root / 'second')
        return execution

    def test_customer_env_and_history_are_separate_inputs(self):
        args = self.options('--env-file', 'customer.env')
        self.assertEqual(args.env_file, 'customer.env')
        self.assertEqual(args.run_type, 'Reassessment')
        self.assertIsNone(args.assessment_id)
        self.assertIsNone(args.tenant_id)

    def test_conflicting_or_repeated_options_are_rejected(self):
        for extra in [('--run-type', 'Reassessment'), ('--replay-snapshot', 'snapshot.json'),
                ('--assessment-id', self.execution.identity['AssessmentId']),
                ('--assessment-history', self.history), ('--baseline', 'prior.xlsx'),
                ('--reassess', self.history), ('--reassess=',)]:
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                self.options(*extra)
        with self.assertRaises(SystemExit):
            parse('--reassess', ' ')

    def test_unattended_exact_baseline_resolves_without_writes_or_prompt(self):
        before = self.files()
        with patch('builtins.input', side_effect=AssertionError('prompt')):
            resolved = resolve_reassessment(self.options('--baseline-run-id', self.execution.identity['RunId']), TENANT)
        self.assertEqual(resolved.assessment_id, self.execution.identity['AssessmentId'])
        self.assertEqual(resolved.tenant_id, TENANT)
        self.assertEqual(before, self.files())

    def test_read_only_plan_resolves_history_ownership_and_exact_baseline(self):
        from Core.run_plan import build_run_plan
        from provenance_fixture import fictional_revision
        before = self.files()
        args = self.options('--tenant-id', TENANT, '--baseline-run-id', self.execution.identity['RunId'])
        with patch('builtins.input', side_effect=AssertionError('plan must not prompt')):
            plan = build_run_plan(args, revision=fictional_revision())
        self.assertEqual(plan['assessment_id'], self.execution.identity['AssessmentId'])
        self.assertEqual(plan['baseline']['run_id'], self.execution.identity['RunId'])
        self.assertEqual(plan['baseline']['integrity'], 'validated')
        self.assertIn(str(self.root / 'initial' / 'Runs'), plan['output_destination'])
        self.assertEqual(before, self.files())

    def test_read_only_plan_reports_pending_selection_without_choosing_or_prompting(self):
        from Core.run_plan import build_run_plan
        from provenance_fixture import fictional_revision
        before = self.files()
        with patch('builtins.input', side_effect=AssertionError('plan must not prompt')):
            plan = build_run_plan(self.options('--tenant-id', TENANT), revision=fictional_revision())
        self.assertIn('reassessment_baseline_selection_pending', plan['validation_errors'])
        self.assertIsNone(plan['baseline']['run_id'])
        self.assertFalse(plan['execution_eligible'])
        self.assertEqual(before, self.files())

    def test_shortcut_and_explicit_options_have_the_same_portable_configuration(self):
        from Core.effective_configuration import resolve_effective, portable_configuration
        baseline = self.execution.identity['RunId']
        shortcut, _, _ = resolve_effective(self.options('--tenant-id', TENANT, '--baseline-run-id', baseline))
        explicit, _, _ = resolve_effective(parse('--tenant-id', TENANT, '--run-type', 'Reassessment',
            '--assessment-id', self.execution.identity['AssessmentId'], '--assessment-history', self.history,
            '--baseline-run-id', baseline))
        self.assertEqual(portable_configuration(shortcut), portable_configuration(explicit))

    def test_single_baseline_requires_confirmation(self):
        before = self.files()
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='yes') as ask, redirect_stdout(io.StringIO()):
            resolved = resolve_reassessment(self.options(), TENANT)
        ask.assert_called_once()
        self.assertEqual(resolved.baseline_run_id, self.execution.identity['RunId'])
        self.assertEqual(before, self.files())

    def test_multiple_baselines_allow_explicit_older_selection(self):
        self.second()
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['1', 'y']), redirect_stdout(io.StringIO()):
            resolved = resolve_reassessment(self.options(), TENANT)
        self.assertEqual(resolved.baseline_run_id, self.execution.identity['RunId'])

    def test_cancel_eof_and_interrupt_leave_history_unchanged(self):
        before = self.files()
        for answer in ['', 'n', EOFError(), KeyboardInterrupt()]:
            with self.subTest(answer=type(answer).__name__), patch('sys.stdin.isatty', return_value=True), \
                    patch('builtins.input', side_effect=[answer]), redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, 'cancelled'):
                resolve_reassessment(self.options(), TENANT)
        self.assertEqual(before, self.files())

    def test_invalid_multiple_choice_never_defaults(self):
        self.second()
        for answer in ['0', '3', 'latest', '-1', '1.0', '']:
            with self.subTest(answer=answer), patch('sys.stdin.isatty', return_value=True), \
                    patch('builtins.input', return_value=answer), redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                resolve_reassessment(self.options(), TENANT)

    def test_noninteractive_missing_baseline_is_actionable(self):
        with patch('sys.stdin.isatty', return_value=False), self.assertRaisesRegex(ValueError, '--baseline-run-id'):
            resolve_reassessment(self.options(), TENANT)

    def test_foreign_missing_and_explicit_mismatched_environment_block_before_prompt(self):
        for tenant, extra in [(OTHER, ()), (None, ()), (TENANT, ('--primary-environment-id', 'ENV-wrong'))]:
            with self.subTest(tenant=tenant), patch('builtins.input', side_effect=AssertionError('prompt')), self.assertRaises(ValueError):
                resolve_reassessment(self.options('--baseline-run-id', self.execution.identity['RunId'], *extra), tenant)

    def test_invalid_empty_legacy_and_tampered_history_block(self):
        from Core.assessment_history import seal
        original = self.history.read_bytes()
        empty = json.loads(original)
        empty.update(Runs=[], InitialRunId=None)
        for content in ['{', '{"version":0,"runs":[]}', json.dumps(seal(empty)),
                original.decode().replace('"Integrity": "', '"Integrity": "bad')]:
            self.history.write_text(content, encoding='utf-8')
            with self.subTest(content=content[:20]), self.assertRaises(ValueError):
                resolve_reassessment(self.options('--baseline-run-id', self.execution.identity['RunId']), TENANT)
        self.history.write_bytes(original)

    def test_history_change_after_candidate_listing_blocks_selection(self):
        args, _ = normalize_reassessment(self.options('--tenant-id', TENANT))
        self.second()
        with self.assertRaisesRegex(ValueError, 'changed'):
            select_reassessment_baseline(args)

    def test_corrupt_snapshot_and_missing_baseline_block(self):
        with self.assertRaisesRegex(ValueError, 'not found'):
            resolve_reassessment(self.options('--baseline-run-id', 'RUN-missing'), TENANT)
        snapshot = next((self.root / 'initial' / 'assessment-results').glob('*.json'))
        snapshot.write_bytes(snapshot.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            resolve_reassessment(self.options('--baseline-run-id', self.execution.identity['RunId']), TENANT)

    def test_main_cancellation_stops_before_credentials_or_collection(self):
        import main
        env = self.root / 'customer.env'
        env.write_text('TENANT_ID=' + TENANT, encoding='utf-8')
        before = self.files()
        with patch.dict(os.environ), patch('sys.argv', ['main.py', '--env-file', str(env), '--reassess', str(self.history)]), \
                patch('Core.check_dependencies.check_dependencies'), patch('sys.stdin.isatty', return_value=True), \
                patch('builtins.input', return_value='n'), patch('Core.credentials_check.validate_credentials_or_exit', side_effect=AssertionError('credentials')), \
                redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            main.main()
        self.assertEqual(stopped.exception.code, 2)
        self.assertEqual(before, self.files())

    def test_numbered_runs_anchor_to_initial_package_with_external_history(self):
        from Core.export_paths import new_reassessment_directory
        second = self.second()
        from Core.assessment_runs import prepare_run
        execution = prepare_run('Reassessment', TENANT, evaluated_at=DAY,
            history_path=self.history, assessment_id=self.execution.identity['AssessmentId'],
            baseline_run_id=second.identity['RunId'])
        one = new_reassessment_directory(execution)
        (one / 'retained').write_text('original')
        two = new_reassessment_directory(execution)
        self.assertEqual(one, self.root / 'initial' / 'Runs' / '1')
        self.assertEqual(two, self.root / 'initial' / 'Runs' / '2')
        self.assertEqual((one / 'retained').read_text(), 'original')

    def test_shortcut_keeps_disabled_delta_option(self):
        args = resolve_reassessment(self.options('--baseline-run-id', self.execution.identity['RunId'], '--delta-mode', 'disabled'), TENANT)
        self.assertEqual(args.delta_mode, 'disabled')

    def test_nested_run_boundary_keeps_completed_packages_immutable(self):
        from Core.publication_io import validate_destination, validate_container
        anchor = self.root / 'initial'
        validate_container(anchor / 'Runs')
        validate_destination(anchor / 'Runs' / '1' / 'collection.json')
        for relative in ('collection-new.json', 'Runs/new.json', 'Runs/arbitrary/new.json', 'Runs/0/new.json'):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                validate_destination(anchor / relative)
        nested = anchor / 'Runs' / '1'
        nested.mkdir(parents=True)
        (nested / 'assessment-run.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            validate_destination(nested / 'new-collection.json')
        validate_destination(nested / 'Builds' / '1' / 'Assessment.xlsx')


class NestedReassessmentExecutionTests(RunFixture):
    def execute(self, *options):
        from Core.offline_report import run_offline_report
        original = Path.cwd()
        try:
            os.chdir(self.root)
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(run_offline_report(parse('--mode', 'offline', *options)), 0)
            return output.getvalue()
        finally:
            os.chdir(original)

    def test_initial_two_shortcuts_and_replay_preserve_evidence_and_append_once(self):
        from tests.synthetic_package_fixture import create_synthetic_package
        from Core.assessment_history import read_history
        from Core.offline_collection import load_collection
        source = create_synthetic_package(self.root / 'source')
        self.execute('--collection-input', source, '--run-type', 'Initial')
        self.history = next(self.root.glob('Reports/*/*/assessment-history.json'))
        initial = read_history(self.history)['Runs'][0]
        original = self.history.parent
        protected = {str(p.relative_to(original)): p.read_bytes() for p in original.rglob('*') if p.is_file()}
        for number in (1, 2):
            # Copy from the original package into its own Runs tree: this must
            # exclude Runs itself rather than recursively copying earlier runs.
            output = self.execute('--collection-input', original / 'collection.json', '--reassess', self.history,
                '--baseline-run-id', initial['RunId'])
            current = read_history(self.history)['Runs'][-1]
            folder = original / 'Runs' / str(number)
            self.assertEqual((self.history.parent / current['PackageLocator']).resolve(), folder)
            self.assertEqual(len(read_history(self.history)['Runs']), number + 1)
            self.assertTrue((folder / 'Builds' / '1').is_dir())
            self.assertFalse((folder / 'Runs').exists())
            self.assertFalse((folder / 'assessment-history.json').exists())
            self.assertEqual(load_collection(folder / 'collection.json')['identity']['RunId'], current['RunId'])
            self.assertIn('--reassess', output)
            self.assertEqual(read_history(self.history)['Runs'][0], initial)
        for name, data in protected.items():
            if name != 'assessment-history.json':
                self.assertEqual((original / name).read_bytes(), data, name)
        before = self.history.read_bytes()
        with patch('Core.processor.process_and_print_all_information', side_effect=AssertionError('replay evaluated')):
            self.execute('--collection-input', original / 'Runs' / '2' / 'collection.json')
        self.assertEqual(self.history.read_bytes(), before)
        import shutil
        moved = self.root / 'moved'
        shutil.move(str(original), moved)
        for row in read_history(moved / 'assessment-history.json')['Runs']:
            from Core.assessment_history import select_baseline
            select_baseline(moved / 'assessment-history.json', assessment_id=row['AssessmentId'],
                            environment_id=row['PrimaryEnvironmentId'], baseline_run_id=row['RunId'])

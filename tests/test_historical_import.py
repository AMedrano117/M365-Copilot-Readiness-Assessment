"""Historical import acceptance tests using fictional local reports only."""
from provenance_fixture import fictional_publication, fictional_revision
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from Core.cli_parser import parse_arguments
from Core.historical_import import inspect_historical, run_historical_import

TENANT = '11111111-1111-1111-1111-111111111111'
OTHER = '22222222-2222-2222-2222-222222222222'
DAY = '2025-01-15'


def arguments(*values):
    with patch('sys.argv', ['main.py', *map(str, values)]):
        return parse_arguments(None, [])


@fictional_publication
class HistoricalImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = self.root / 'private-synthetic-customer'
        self.sources.mkdir()
        self.workbook = self.sources / 'private-original.xlsx'
        self.html = self.sources / 'private-report.html'
        self.html.write_bytes(b'<html><a href="private-original.xlsx">Workbook</a><script>throw new Error("must never execute")</script>Original report</html>')
        self.output = self.root / 'imported'
        self.write_workbook()

    def write_workbook(self, *, tenant=TENANT, day=DAY, generation='2025-01-17T12:00:00Z', extra=None):
        from openpyxl import Workbook
        book = Workbook()
        sheet = book.active
        sheet.title = 'Recommendations'
        sheet.append(['Service', 'Feature', 'Recommendation', 'Status', 'Observation', 'RecommendationId'])
        sheet.append(['Entra', 'MFA', 'Require MFA', 'Warning', 'Original finding', 'old-42'])
        manifest = book.create_sheet('Run Manifest')
        manifest.append(['Item', 'Value'])
        for key, value in [('Tenant ID', tenant), ('Evaluation Date', day),
                           ('Generation Time (UTC)', generation), ('Methodology Version', 'old-methodology')]:
            if value: manifest.append([key, value])
        if extra: manifest.append(extra)
        book.save(self.workbook)
        book.close()

    def args(self, *extra):
        return arguments('--import-historical', self.workbook, '--historical-html', self.html,
                         '--historical-output-dir', self.output, *extra)

    def import_reports(self):
        before = self.workbook.read_bytes(), self.html.read_bytes()
        with redirect_stdout(StringIO()):
            imported = run_historical_import(self.args())
        self.assertEqual(before, (self.workbook.read_bytes(), self.html.read_bytes()))
        return imported

    def test_import_preserves_sources_dates_ids_and_no_raw_collection(self):
        from Core.assessment_history import read_history, select_baseline
        from Core.assessment_serialization import read_assessment_result
        imported = self.import_reports()
        saved = read_assessment_result(imported['snapshot'])
        meta = saved['identity']
        self.assertEqual(meta['EvaluatedAt'], DAY)
        self.assertNotEqual(meta['CreatedAt'][:10], DAY)
        self.assertEqual(meta['RunType'], 'Initial')
        self.assertTrue(meta['AssessmentId'].startswith('AST-'))
        self.assertIsNone(meta['BaselineRunId'])
        receipt = saved['historical_import']
        self.assertFalse(receipt['raw_collection_available'])
        self.assertFalse(receipt['technical_delta_eligible'])
        self.assertEqual(receipt['original_methodology_version'], 'old-methodology')
        self.assertEqual(receipt, saved['run_context']['HistoricalImport'])
        self.assertEqual(self.workbook.read_bytes(), (self.output / 'inputs/historical' / self.workbook.name).read_bytes())
        self.assertEqual(self.html.read_bytes(), (self.output / 'inputs/historical' / self.html.name).read_bytes())
        self.assertFalse((self.output / 'collection.json').exists())
        self.assertFalse((self.output / 'Builds').exists())
        self.assertFalse(saved['strengths'])
        historical = [row for row in saved['recommendations'] if row.get('OriginalRecommendationId') == 'old-42']
        self.assertTrue(historical)
        self.assertTrue(all(row['Historical'] == 'Yes' and row['EvidenceStatus'] != 'supported' for row in historical))
        history = read_history(self.output / 'assessment-history.json')
        self.assertEqual(history['InitialRunId'], meta['RunId'])
        self.assertEqual(len(history['Runs']), 1)
        self.assertIn('HistoricalImport', history['Runs'][0])
        baseline = select_baseline(self.output / 'assessment-history.json', assessment_id=meta['AssessmentId'],
            environment_id=meta['PrimaryEnvironmentId'], baseline_run_id=meta['RunId'])
        self.assertEqual(baseline['historical_import'], receipt)

    def test_folder_selects_one_workbook_and_archives_html(self):
        args = arguments('--import-historical', self.sources)
        info = inspect_historical(args)
        self.assertEqual(len(info['sources']), 2)
        (self.sources / 'second.xlsx').write_bytes(self.workbook.read_bytes())
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            inspect_historical(args)
        self.assertEqual(len(inspect_historical(self.args())['sources']), 2)

    def test_missing_metadata_requires_explicit_values_not_generation_time(self):
        self.write_workbook(tenant=None, day=None)
        with self.assertRaisesRegex(ValueError, '--evaluation-date'):
            inspect_historical(self.args())
        with self.assertRaisesRegex(ValueError, '--tenant-id'):
            inspect_historical(self.args('--evaluation-date', DAY))
        info = inspect_historical(self.args('--tenant-id', TENANT, '--evaluation-date', DAY))
        self.assertEqual(info['tenant_identity_basis'], 'operator-declared; not source-verified')
        self.assertEqual(info['date_basis'], 'operator-declared')

    def test_conflicting_and_future_metadata_rejected(self):
        for extra in [('--tenant-id', OTHER), ('--evaluation-date', '2025-02-01')]:
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, 'conflict'):
                inspect_historical(self.args(*extra))
        self.write_workbook(extra=('Tenant ID', OTHER))
        with self.assertRaisesRegex(ValueError, 'conflict'):
            inspect_historical(self.args())
        self.write_workbook(day='2999-01-01')
        with self.assertRaisesRegex(ValueError, 'future'):
            inspect_historical(self.args())

    def test_readonly_plan_does_not_create_files_ids_or_authenticate(self):
        from Core.run_plan import build_run_plan, plan_json
        before = set(self.root.rglob('*'))
        with patch('Core.assessment_identity.uuid4', side_effect=AssertionError('No ID allocation')), \
             patch('Core.credentials_check.read_env_file', side_effect=AssertionError('No credentials')):
            plan = build_run_plan(self.args('--plan'), revision=fictional_revision())
        self.assertTrue(plan['execution_eligible'], plan['validation_errors'])
        self.assertFalse(plan['sources']['tenant_collection'])
        self.assertFalse(plan['publication_eligible'])
        self.assertEqual(plan['source_revision_role'], 'historical_importer')
        self.assertFalse(plan['historical_import']['technical_delta_eligible'])
        self.assertEqual(before, set(self.root.rglob('*')))
        text = plan_json(plan)
        self.assertNotIn('private-original', text)
        self.assertNotIn('private-synthetic-customer', text)
        self.assertNotIn('Original finding', text)

    def test_missing_date_stays_invalid_after_repeated_resolution(self):
        from Core.effective_configuration import resolve_effective
        from Core.run_plan import build_run_plan
        self.write_workbook(day=None)
        args, _, errors = resolve_effective(self.args())
        self.assertIn('historical_import_invalid', errors)
        self.assertIsNone(args.evaluation_date)
        plan = build_run_plan(args, revision=fictional_revision())
        self.assertFalse(plan['execution_eligible'])
        self.assertTrue(any('--evaluation-date' in message for message in plan['warnings']))
        self.assertFalse(self.output.exists())

    def test_source_changed_after_preflight_and_existing_output_rejected(self):
        from Core.effective_configuration import resolve_effective
        args, _, _ = resolve_effective(self.args())
        self.html.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed after preflight'):
            run_historical_import(args)
        self.assertFalse(self.output.exists())
        self.output.mkdir()
        sentinel = self.output / 'keep.txt'
        sentinel.write_text('retained', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            run_historical_import(self.args())
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'retained')

    def test_output_inside_source_folder_fails_in_plan_and_execution(self):
        from Core.run_plan import build_run_plan
        args = arguments('--import-historical', self.workbook,
                         '--historical-output-dir', self.sources / 'new-package')
        plan = build_run_plan(args, revision=fictional_revision())
        self.assertIn('historical_output_inside_source_folder', plan['validation_errors'])
        with self.assertRaisesRegex(ValueError, 'outside the source'):
            run_historical_import(args)
        self.assertFalse((self.sources / 'new-package').exists())

    def test_archive_tampering_prevents_baseline_selection(self):
        from Core.assessment_history import select_baseline
        imported = self.import_reports()
        meta = imported['result']['identity']
        (self.output / 'inputs/historical' / self.html.name).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
            select_baseline(self.output / 'assessment-history.json', assessment_id=meta['AssessmentId'],
                environment_id=meta['PrimaryEnvironmentId'], baseline_run_id=meta['RunId'])

    def test_historical_snapshot_cannot_be_rendered_as_new_assessment(self):
        from Core.assessment_replay import render_snapshot
        from Core.run_plan import build_run_plan
        imported = self.import_reports()
        args = arguments('--replay-snapshot', imported['snapshot'])
        plan = build_run_plan(args, revision=fictional_revision())
        self.assertFalse(plan['execution_eligible'])
        with self.assertRaisesRegex(ValueError, 'archived original'):
            render_snapshot(imported['snapshot'], self.root / 'renders')
        self.assertFalse((self.root / 'renders').exists())

    def test_reassessment_retains_baseline_but_cannot_claim_technical_transitions(self):
        from Core.assessment_runs import prepare_run, complete_run
        from Core.assessment_result import build_assessment_result
        from Core.export_paths import new_reassessment_directory
        imported = self.import_reports()
        initial = imported['result']['identity']
        execution = prepare_run('Reassessment', TENANT, evaluated_at='2025-02-01',
            history_path=self.output / 'assessment-history.json', assessment_id=initial['AssessmentId'],
            baseline_run_id=initial['RunId'])
        current = build_assessment_result([], {'collection_context': {'identity': execution.identity}},
            evaluation_date='2025-02-01', expected_tenant_id=TENANT)
        package = new_reassessment_directory(execution)
        complete_run(current, execution, package)
        self.assertEqual(package.parent, self.output / 'Runs')
        self.assertEqual(current['run_context']['Comparability']['Outcome'], 'NotComparable')
        self.assertTrue(any(r['Code'] == 'historical_report_baseline'
            for r in current['run_context']['Comparability']['Reasons']))
        self.assertNotIn('historical_import', current)
        self.assertNotIn('HistoricalImport', current['run_context'])
        self.assertEqual(current['lifecycle']['Outcome'], 'NotComparable')
        self.assertTrue(all(r['State'] == 'NotComparable' for r in current['lifecycle']['Records']))
        self.assertTrue(all(r['Eligibility'] not in {'eligible', 'eligible with qualifications'}
            for r in current['run_context']['Comparability']['Items']))

        # The next comparison can use the later collected run, without inheriting
        # the report-only Initial's permanent technical qualification.
        next_execution = prepare_run('Reassessment', TENANT, evaluated_at='2025-03-01',
            history_path=self.output / 'assessment-history.json', assessment_id=initial['AssessmentId'],
            baseline_run_id=current['identity']['RunId'])
        next_result = build_assessment_result([], {'collection_context': {'identity': next_execution.identity}},
            evaluation_date='2025-03-01', expected_tenant_id=TENANT)
        complete_run(next_result, next_execution, new_reassessment_directory(next_execution))
        self.assertFalse(any(r['Code'] == 'historical_report_baseline'
            for r in next_result['run_context']['Comparability']['Reasons']))

    def test_matching_html_links_and_original_filenames_are_preserved(self):
        summary = self.sources / 'summary.html'
        summary.write_bytes(b'<a href="private-report.html">Details</a>')
        args = arguments('--import-historical', self.sources, '--historical-output-dir', self.output)
        with redirect_stdout(StringIO()):
            imported = run_historical_import(args)
        archive = self.output / 'inputs/historical'
        self.assertEqual({p.name for p in archive.iterdir()}, {self.workbook.name, self.html.name, summary.name})
        self.assertEqual((archive / summary.name).read_bytes(), summary.read_bytes())
        self.assertEqual((archive / self.html.name).read_bytes(), self.html.read_bytes())
        self.assertEqual(len(imported['result']['historical_import']['sources']), 3)

    def test_duplicate_names_and_source_links_are_rejected(self):
        other = self.root / 'other-build'
        other.mkdir()
        duplicate = other / self.html.name
        duplicate.write_bytes(self.html.read_bytes())
        with self.assertRaisesRegex(ValueError, 'duplicate filenames'):
            inspect_historical(self.args('--historical-html', duplicate))
        with patch('Core.publication_io.linked_path', side_effect=lambda path: path == self.workbook), \
             self.assertRaisesRegex(ValueError, 'symbolic links'):
            inspect_historical(self.args())
        self.assertFalse(self.output.exists())

    def test_original_source_revision_is_retained_without_claiming_authentication(self):
        self.write_workbook(extra=('Producing commit_sha', 'b' * 40))
        imported = self.import_reports()
        receipt = imported['result']['historical_import']
        self.assertEqual(receipt['original_producing_revision']['commit_sha'], 'b' * 40)
        self.assertIn('unverified', receipt['original_provenance_basis'])
        self.assertNotEqual(imported['result']['producing_revision']['commit_sha'], 'b' * 40)
        self.assertIn('historical_import', imported['result']['producing_revision']['import_adapters'])

    def test_malformed_missing_and_unrelated_inputs_fail_without_writing(self):
        for data in (b'not an XLSX', b''):
            self.workbook.write_bytes(data)
            with self.assertRaisesRegex(ValueError, 'supported Recommendations'):
                inspect_historical(self.args())
            self.assertFalse(self.output.exists())
        self.write_workbook(tenant='invalid')
        with self.assertRaisesRegex(ValueError, 'valid GUID'):
            inspect_historical(self.args())
        self.write_workbook(day='not-a-date')
        with self.assertRaisesRegex(ValueError, 'ISO date'):
            inspect_historical(self.args())
        with self.assertRaisesRegex(ValueError, 'existing XLSX'):
            inspect_historical(arguments('--import-historical', self.html))

    def test_source_parent_environment_cannot_supply_identity_or_date(self):
        self.write_workbook(tenant=None, day=None)
        with patch.dict('os.environ', {'TENANT_ID': TENANT, 'EVALUATION_DATE': DAY}):
            from Core.run_plan import build_run_plan
            plan = build_run_plan(self.args(), revision=fictional_revision())
        self.assertFalse(plan['execution_eligible'])
        self.assertIsNone(plan['effective_configuration']['evaluation_date'])
        self.assertFalse(self.output.exists())

    def test_archive_locator_traversal_is_rejected(self):
        from Core.historical_import import validate_archives
        result = {'historical_import': {'schema_version': '1.0.0',
            'sources': [{'locator': '../outside.html', 'sha256': '0' * 64}]}}
        with self.assertRaisesRegex(ValueError, 'locator is invalid'):
            validate_archives(result, self.output)

    def test_cli_main_dispatches_import_without_collection_or_rendering(self):
        import main
        from Core.repository_provenance import current_revision, set_execution_revision
        from Core.run_progress import RunProgress
        from Core.run_plan import build_run_plan
        previous = current_revision()
        self.addCleanup(set_execution_revision, previous)
        args = self.args()
        def plan(value):
            return build_run_plan(value, revision=fictional_revision())
        with patch('Core.cli_parser.parse_arguments', return_value=args), \
             patch('Core.check_dependencies.check_dependencies'), \
             patch('Core.credentials_check.load_env_file', side_effect=AssertionError('No environment access')), \
             patch('Core.run_plan.build_run_plan', side_effect=plan), \
             patch('Core.offline_report.run_offline_report', side_effect=AssertionError('No report renderer')), \
             redirect_stdout(StringIO()), self.assertRaises(SystemExit) as stopped:
            main.main()
        self.assertEqual(stopped.exception.code, 0)
        self.assertTrue((self.output / 'assessment-history.json').is_file())
        self.assertNotIn('Build reports', dict(RunProgress(offline=True, historical_import=True).stages).values())

    def test_selected_historical_baseline_has_explicit_preflight_limit(self):
        from Core.run_plan import build_run_plan
        imported = self.import_reports()
        args = arguments('--mode', 'offline', '--tenant-id', TENANT,
            '--reassess', self.output / 'assessment-history.json',
            '--baseline-run-id', imported['result']['identity']['RunId'],
            '--prior-report', self.workbook)
        plan = build_run_plan(args, revision=fictional_revision())
        self.assertTrue(plan['execution_eligible'], plan['validation_errors'])
        self.assertTrue(plan['delta']['enabled'])
        self.assertFalse(plan['delta']['expected'])
        self.assertIn('NotComparable', plan['delta']['reason'])

    def test_cli_rejects_mixed_intents_credentials_and_orphan_options(self):
        combinations = [('--env-file', 'secret.env'), ('--collection-input', 'raw.json'),
            ('--mode', 'live'), ('--reassess', 'history.json'), ('--run-type', 'Initial'),
            ('--baseline', 'old.xlsx'), ('--replay-snapshot', 'old.json'),
            ('--save-collection', 'raw.json'), ('--assessment-history', 'history.json')]
        for extra in combinations:
            with self.subTest(extra=extra), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                self.args(*extra)
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            arguments('--historical-html', self.html)

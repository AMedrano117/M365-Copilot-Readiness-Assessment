"""Stage A CLI, validation and renderer integration acceptance tests."""
import copy
import io
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_assessment_run_workflow import TENANT, DAY, result


def parse(*options):
    from Core.cli_parser import parse_arguments
    with patch('sys.argv', ['main.py', *map(str, options)]), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return parse_arguments(None, [])


class RunCliTests(unittest.TestCase):
    def test_modes_and_noninteractive_baseline_options(self):
        args = parse('--mode','offline','--run-type','Reassessment','--assessment-id','AST-example',
            '--baseline-run-id','RUN-example','--assessment-history','history.json')
        self.assertEqual(args.run_type, 'Reassessment')
        self.assertEqual(args.baseline_run_id, 'RUN-example')
        self.assertEqual(parse('--replay-snapshot','snapshot.json').mode, 'offline')

    def test_invalid_combinations_matrix(self):
        for options in [('--run-type','Automatic'), ('--run-type','Reassessment'),
                ('--run-type','Initial','--baseline-run-id','RUN-x'),
                ('--run-type','Standalone','--baseline-run-id','RUN-x'),
                ('--run-type','Initial','--replay-snapshot','result.json'),
                ('--replay-snapshot','result.json','--evaluation-date','2026-09-16'),
                ('--replay-snapshot','result.json','--collection-input','collection.json'),
                ('--run-type','Reassessment','--baseline','old.xlsx'),
                ('--assessment-id','AST-x'), ('--render-output-dir','output')]:
            with self.subTest(options=options), self.assertRaises(SystemExit):
                parse(*options)

    def test_legacy_cli_remains_accepted_and_help_explains_explicit_selection(self):
        self.assertIsNone(parse('--mode','offline').run_type)
        output = io.StringIO()
        with patch('sys.argv',['main.py','--help']), redirect_stdout(output), self.assertRaises(SystemExit):
            from Core.cli_parser import parse_arguments
            parse_arguments(None, [])
        self.assertIn('--baseline-run-id', output.getvalue())
        self.assertIn('explicit', output.getvalue().lower())


class RunValidationTests(unittest.TestCase):
    def test_invalid_context_is_blocked_before_snapshot_creation(self):
        from Core.assessment_runs import prepare_run
        from Core.assessment_serialization import write_assessment_result
        seed = prepare_run('Standalone', TENANT, evaluated_at=DAY).identity
        for field, value in [('RunType','Automatic'), ('RunId',None), ('AssessmentId',None),
                              ('BaselineRunId','RUN-unintended')]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                snapshot = result(seed)
                snapshot['run_context'][field] = value
                target = Path(directory) / 'missing' / 'result.json'
                with self.assertRaises(ValueError):
                    write_assessment_result(target, snapshot)
                self.assertFalse(target.parent.exists())

    def test_required_comparability_reasons_and_legacy_diagnostic(self):
        from Core.assessment_runs import prepare_run
        from Core.assessment_references import validate_assessment_references
        snapshot = result(prepare_run('Standalone', TENANT, evaluated_at=DAY).identity)
        snapshot['run_context']['Comparability'] = {'Outcome':'NotComparable','Reasons':[],'Items':[]}
        self.assertTrue(any(row['severity']=='error' for row in validate_assessment_references(snapshot)))
        snapshot.pop('run_context')
        snapshot['identity'].pop('RunContext')
        snapshot['identity']['RunType'] = None
        diagnostics = validate_assessment_references(snapshot)
        self.assertTrue(any(row['code']=='legacy_run_intent' and row['severity']=='compatibility_warning'
                            for row in diagnostics))


class RunRendererTests(unittest.TestCase):
    def test_shared_snapshot_replay_preserves_context_without_evaluation_or_history_append(self):
        from Core.assessment_runs import prepare_run
        from Core.assessment_result import build_assessment_result
        from Core.assessment_serialization import write_assessment_result, read_assessment_result
        from Core.assessment_replay import render_snapshot
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed = prepare_run('Standalone', TENANT, evaluated_at=DAY).identity
            bundle = {'collection_context':{'identity':seed,'evaluation_date':DAY}}
            snapshot = build_assessment_result([], bundle, evaluation_date=DAY, expected_tenant_id=TENANT)
            write_assessment_result(root / 'source.json', snapshot)
            context = copy.deepcopy(read_assessment_result(root / 'source.json')['run_context'])
            with (patch('Core.assessment_result.build_assessment_result', side_effect=AssertionError('Semantic evaluation')),
                  patch('Core.assessment_history.append_run', side_effect=AssertionError('History append'))):
                outputs = render_snapshot(root / 'source.json', root / 'rendered')
            self.assertEqual(read_assessment_result(root / 'source.json')['run_context'], context)
            self.assertTrue(Path(outputs['html_path']).is_file())
            self.assertTrue(Path(outputs['excel_path']).is_file())
            html = Path(outputs['html_path']).read_text(encoding='utf-8')
            self.assertIn(seed['RunId'], html)
            from openpyxl import load_workbook
            for name in ('excel_path','technical_excel_path'):
                book = load_workbook(outputs[name])
                self.addCleanup(book.close)
                values = [str(cell.value) for row in book['Run Manifest'] for cell in row]
                self.assertIn(seed['RunId'], values)
            self.assertNotIn('Improved', str(context))


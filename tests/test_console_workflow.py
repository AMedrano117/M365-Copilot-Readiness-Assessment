"""Operator handoffs remain copyable and presentation flags keep evidence intact."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from Core.cli_parser import parse_arguments
from Core.assessment_package import record_package_run, save_rebuild_recipe
from Core.offline_collection import empty_service_results, load_collection, save_collection
from Core.console_reporting import (
    configure_console, detail, display_path, is_verbose, print_collection_handoff, style,
)


class TerminalBuffer(io.StringIO):
    def isatty(self):
        return True


class ConsoleWorkflowTests(unittest.TestCase):
    def setUp(self):
        configure_console(verbose=False, color='never')
        self.addCleanup(configure_console, verbose=False, color='auto')

    def test_cli_defaults_and_explicit_presentation_options(self):
        for flags, verbose, color in (
            ([], False, 'auto'),
            (['-v'], True, 'auto'),
            (['--verbose', '--color', 'never'], True, 'never'),
            (['--color', 'always'], False, 'always'),
        ):
            with self.subTest(flags=flags), patch.object(sys, 'argv', ['main.py', '--mode', 'offline', *flags]):
                args = parse_arguments(None, [])
                self.assertEqual(args.verbose, verbose)
                self.assertEqual(args.color, color)
                self.assertEqual(args.mode, 'offline')

    def test_verbose_controls_detail_messages(self):
        quiet, verbose = io.StringIO(), io.StringIO()
        with redirect_stdout(quiet):
            detail('The export tenant identifier matches the assessed tenant.')
        configure_console(verbose=True, color='never')
        with redirect_stdout(verbose):
            detail('The export tenant identifier matches the assessed tenant.')
        self.assertEqual(quiet.getvalue(), '')
        self.assertTrue(is_verbose())
        self.assertIn('export tenant identifier', verbose.getvalue())

    def test_auto_color_is_plain_for_redirection_and_no_color(self):
        configure_console(color='auto')
        with redirect_stdout(io.StringIO()), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(style('Warning: incident evidence unavailable', 'warning'), 'Warning: incident evidence unavailable')
        with redirect_stdout(TerminalBuffer()), patch.dict(os.environ, {'NO_COLOR': '1'}, clear=True):
            self.assertEqual(style('Readiness unconfirmed', 'warning'), 'Readiness unconfirmed')

    def test_terminal_colors_and_explicit_override(self):
        configure_console(color='auto')
        with redirect_stdout(TerminalBuffer()), patch.dict(os.environ, {}, clear=True), patch('Core.console_reporting._windows_vt', True):
            self.assertIn('\x1b[', style('Collection complete', 'success'))
        configure_console(color='always')
        with redirect_stdout(io.StringIO()), patch.dict(os.environ, {'NO_COLOR': '1', 'TERM': 'dumb'}, clear=True):
            self.assertIn('\x1b[', style('Collection input', 'path'))
        configure_console(color='never')
        with redirect_stdout(TerminalBuffer()):
            self.assertNotIn('\x1b[', style('Collection input', 'path'))

    def test_handoff_preserves_long_paths_and_powershell_quotes(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "customer's exports with spaces"
            folder.mkdir()
            collection = folder / ('tenant-collection_' + 'a' * 85 + '.json')
            collection.write_text('{}', encoding='utf-8')
            output = io.StringIO()
            with redirect_stdout(output), patch('Core.console_reporting.shutil.get_terminal_size', return_value=os.terminal_size((60, 24))):
                print_collection_handoff(collection)
            lines = output.getvalue().splitlines()
            self.assertIn(display_path(collection), lines)
            quoted = "'" + display_path(collection).replace("'", "''") + "'"
            self.assertIn('  --collection-input ' + quoted + ' `', lines)
            self.assertTrue(any(line.startswith('& ') and '--mode offline `' in line for line in lines))
            self.assertIn('  --open-html-report', lines)
            self.assertTrue(collection.is_file())

    def test_absent_collection_has_no_misleading_handoff(self):
        output = io.StringIO()
        with redirect_stdout(output):
            print_collection_handoff(None)
        self.assertEqual(output.getvalue(), '')

    def test_live_receipt_handoff_and_verbose_detail_preserve_same_evidence(self):
        imports = [{'Source': 'permissions.csv', 'Status': 'selected', 'Reason': 'Exported objects only.'},
                   {'Source': 'permissions.csv', 'Status': 'identity_verified', 'Reason': 'Tenant matches.'}]
        bundle = {'import_receipt': imports, 'assessment_result': {
            'coverage': [{'ControlId': 'CONTENT.PERMISSIONS'}], 'decision': 'Readiness unconfirmed', 'counts': {'evidence_gaps': 1}}}
        with tempfile.TemporaryDirectory() as temp:
            collection = Path(save_collection(Path(temp) / 'saved-live.json', tenant_id='example', tenant_name='Example', service_results=empty_service_results()))
            package = collection.with_name(collection.stem + '_package')
            quiet, verbose = io.StringIO(), io.StringIO()
            for enabled, output in ((False, quiet), (True, verbose)):
                configure_console(verbose=enabled, color='never')
                with redirect_stdout(output):
                    record_package_run(package, mode='live', tenant_id='example', collected_at='2026-09-15', evaluation_date='2026-09-15', outputs={'evidence_bundle': bundle})
            input_path = package / 'collection.json'
            self.assertIn(display_path(input_path), quiet.getvalue().splitlines())
            self.assertIn('COLLECTION INPUT (--collection-input)', quiet.getvalue())
            self.assertNotIn('Input permissions.csv:', quiet.getvalue())
            self.assertIn('Input permissions.csv:', verbose.getvalue())
            self.assertEqual(load_collection(input_path)['tenant_id'], 'example')
            receipts = [json.loads(line) for line in (package / 'operator-log.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(len(receipts), 2)
            for receipt in receipts:
                self.assertEqual(receipt['import_receipt'], imports)
                self.assertEqual(receipt['remaining_requirements'], bundle['assessment_result']['coverage'])
                self.assertEqual(receipt['decision'], 'Readiness unconfirmed')

    def test_offline_only_receipt_hands_off_recipe_without_inventing_collection(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'offline-only'
            folder.mkdir()
            args = SimpleNamespace(evaluation_date='2026-09-15', report_format='excel')
            save_rebuild_recipe(folder, args, tenant_id='example', tenant_name='Example')
            output = io.StringIO()
            with redirect_stdout(output):
                record_package_run(folder, mode='offline', tenant_id='example', collected_at=None, evaluation_date='2026-09-15')
            recipe = folder / 'rebuild.json'
            self.assertIn(display_path(recipe), output.getvalue().splitlines())
            self.assertFalse((folder / 'collection.json').exists())
            self.assertFalse(load_collection(recipe)['has_tenant_collection'])

    def test_failed_render_still_hands_off_saved_collection(self):
        with tempfile.TemporaryDirectory() as temp:
            collection = Path(save_collection(Path(temp) / 'saved-live.json', tenant_id='example', tenant_name='Example', service_results=empty_service_results()))
            package = collection.with_name(collection.stem + '_package')
            output = io.StringIO()
            with redirect_stdout(output):
                record_package_run(package, mode='live', tenant_id='example', collected_at='2026-09-15', evaluation_date='2026-09-15', status='failed', error='Fixture report export failed')
            self.assertIn(display_path(package / 'collection.json'), output.getvalue().splitlines())
            receipt = json.loads((package / 'operator-log.jsonl').read_text(encoding='utf-8'))
            self.assertEqual(receipt['status'], 'failed')
            self.assertEqual(receipt['error'], 'Fixture report export failed')

    def test_default_receipt_keeps_rejected_inputs_visible(self):
        imports = [{'Source': 'good.csv', 'Status': 'accepted', 'Reason': 'Supported schema.'},
                   {'Source': 'rejected.csv', 'Status': 'rejected', 'Reason': 'The required headers are missing.'},
                   {'Source': 'unsupported.csv', 'Status': 'unsupported', 'Reason': 'This schema is not supported.'},
                   {'Source': 'empty.csv', 'Status': 'empty', 'Reason': 'No data rows.'},
                   {'Source': 'unverified.csv', 'Status': 'identity_unverified', 'Reason': 'Confirm the export tenant.'}]
        with tempfile.TemporaryDirectory() as temp:
            output = io.StringIO()
            with redirect_stdout(output):
                record_package_run(temp, mode='offline', tenant_id='example', collected_at=None, evaluation_date='2026-09-15', outputs={'evidence_bundle': {'import_receipt': imports}})
            text = output.getvalue()
            self.assertNotIn('Input good.csv', text)
            self.assertIn('Input rejected.csv: rejected', text)
            self.assertIn('The required headers are missing.', text)
            self.assertIn('Input unsupported.csv: unsupported', text)
            self.assertIn('Input empty.csv: empty', text)
            self.assertIn('Confirm the export tenant.', text)
            self.assertNotIn('\x1b[', text)
            receipt = json.loads((Path(temp) / 'operator-log.jsonl').read_text(encoding='utf-8'))
            self.assertEqual(receipt['import_receipt'], imports)


if __name__ == '__main__':
    unittest.main()

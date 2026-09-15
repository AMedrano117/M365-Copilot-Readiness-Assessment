import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from Core.assessment_package import record_package_run
from Core.console_reporting import configure_console, print_paragraph, print_source_gaps


class ConsoleReportingTests(unittest.TestCase):
    def test_old_pdf_package_notices_are_explained_without_rewriting_receipts(self):
        configure_console(verbose=False, color='never')
        self.addCleanup(configure_console)
        diagnostics = [
            {'role': 'reports_dir', 'source_file': 'Usage.pdf', 'status': 'unsupported',
             'reason': 'Not a supported data file; not copied.'},
            {'role': 'reports_dir', 'source_file': 'Security.pdf', 'status': 'reference_only',
             'reason': 'PDF capture'},
            {'role': 'reports_dir', 'source_file': 'other.txt', 'status': 'unsupported', 'reason': 'Unrecognized file'},
        ]
        with tempfile.TemporaryDirectory() as temp:
            output = io.StringIO()
            with redirect_stdout(output):
                record_package_run(temp, mode='offline', tenant_id='example', collected_at='2026-09-15',
                                   evaluation_date='2026-09-15', diagnostics=diagnostics)
            receipt = json.loads((Path(temp) / 'operator-log.jsonl').read_text(encoding='utf-8'))
        self.assertEqual(receipt['package_diagnostics'], diagnostics)
        text = ' '.join(output.getvalue().split())
        self.assertIn('PDF captures: 2 were not included', text)
        self.assertIn('--reports-dir for automatic import', text)
        self.assertIn('do not stop the report build', text)
        self.assertNotIn('Input Usage.pdf: unsupported', text)
        self.assertIn('Input other.txt: unsupported', text)

    def test_failed_read_is_explained_without_treating_skipped_or_preflight_as_failure(self):
        output = io.StringIO()
        with redirect_stdout(output):
            print_source_gaps({
                'defender_incidents': {'available': False, 'reason': 'Request rejected: Top limit is 50.'},
                'purview_ediscovery': {'availability_status': 'not_requested'},
                'connection_purview': {'availability_status': 'unavailable', 'reason': 'Browser sign-in pending'},
                'm365_users': {'availability_status': 'available', 'records_collected': 0},
                'm365_sites': {'availability_status': 'available', 'truncated': True},
            })
        text = output.getvalue()
        self.assertIn('Source collection gaps: 2', text)
        self.assertIn('Request rejected: Top limit is 50.', text)
        self.assertIn('m365_sites', text)
        for skipped in ('purview_ediscovery', 'connection_purview', 'm365_users'):
            self.assertNotIn(skipped, text)

    def test_wrapped_paragraph_preserves_word_boundaries_and_long_paths(self):
        message = 'Check for a recent completed scan before starting a new one. ' * 3
        message += 'output/one-long-original-export-name-that-must-remain-copyable.csv'
        output = io.StringIO()
        with patch('Core.console_reporting.shutil.get_terminal_size', return_value=type('Size', (), {'columns': 70})()):
            with redirect_stdout(output):
                print_paragraph(message, indent='  ')
        self.assertEqual(' '.join(message.split()), ' '.join(output.getvalue().split()))
        self.assertIn('output/one-long-original-export-name-that-must-remain-copyable.csv', output.getvalue())

    def test_console_groups_import_states_and_json_preserves_each_receipt(self):
        configure_console(verbose=True, color='never')
        self.addCleanup(configure_console)
        rows = [
            {'Source': 'permissions.csv', 'Status': 'selected', 'Reason': 'Exported objects only.'},
            {'Source': 'permissions.csv', 'Status': 'identity_verified', 'Reason': 'Tenant matches.'},
            {'Source': 'readiness.csv', 'Status': 'accepted', 'Reason': ''},
        ]
        with tempfile.TemporaryDirectory() as temp:
            output = io.StringIO()
            with redirect_stdout(output):
                record_package_run(temp, mode='offline', tenant_id='example', collected_at='2026-09-15',
                                   evaluation_date='2026-09-15', outputs={'evidence_bundle': {'import_receipt': rows}})
            receipt = json.loads((Path(temp) / 'operator-log.jsonl').read_text(encoding='utf-8'))
        text = output.getvalue()
        self.assertEqual(text.count('Input permissions.csv:'), 1)
        self.assertIn('selected, identity_verified', text)
        self.assertIn('Input readiness.csv: accepted\n', text)
        self.assertNotIn('accepted;', text)
        self.assertEqual(receipt['import_receipt'], rows)


if __name__ == '__main__':
    unittest.main()

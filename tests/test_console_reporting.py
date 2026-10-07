import io
import json
import copy
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

    def test_offline_source_gaps_are_saved_results_without_live_access_instructions(self):
        configure_console(verbose=False, color='never')
        self.addCleanup(configure_console)
        states = {
            'entra_recommendations': {'availability_status': 'unavailable', 'available': False,
                                     'reason': 'HTTP 403. Grant DirectoryRecommendations.Read.All now.'},
            'risky_users': {'availability_status': 'unavailable', 'reason': 'Tenant was not licensed.'},
            'shadow_ai_usage': {'availability_status': 'not_requested'},
            'connection_entra': {'availability_status': 'unavailable', 'reason': 'Preflight unavailable'},
        }
        before = copy.deepcopy(states)
        output = io.StringIO()
        with redirect_stdout(output):
            print_source_gaps(states, mode='offline', collected_at='2026-09-14T12:00:00Z')
        text = ' '.join(output.getvalue().split())
        self.assertIn('Saved collection gaps: 2', text)
        self.assertIn('from 2026-09-14T12:00:00Z', text)
        self.assertIn('offline rebuild made no tenant requests', text)
        self.assertIn('entra_recommendations: unavailable in saved evidence.', text)
        self.assertIn('still limit evidence coverage', text)
        self.assertNotIn('Source collection gaps', text)
        self.assertNotIn('Grant DirectoryRecommendations', text)
        self.assertNotIn('shadow_ai_usage', text)
        self.assertNotIn('connection_entra', text)
        self.assertEqual(states, before)

    def test_verbose_offline_preserves_original_service_response_as_saved_evidence(self):
        configure_console(verbose=True, color='never')
        self.addCleanup(configure_console)
        output = io.StringIO()
        with redirect_stdout(output):
            print_source_gaps({'risky_users': {'availability_status': 'unavailable',
                                             'reason': 'HTTP 403: tenant was not licensed.'}}, mode='offline')
        self.assertIn('Saved collection gaps: 1', output.getvalue())
        self.assertIn('HTTP 403: tenant was not licensed.', output.getvalue())
        self.assertIn('did not check current permissions or licensing', ' '.join(output.getvalue().split()))

    def test_offline_assessment_summary_uses_saved_gap_heading_and_counts_admin_pages(self):
        from Core.export_recommendations import print_recommendations_summary
        configure_console(verbose=False, color='never')
        self.addCleanup(configure_console)
        output = io.StringIO()
        with redirect_stdout(output):
            print_recommendations_summary([{'Service': 'Entra', 'Feature': 'Policy review',
                                           'Observation': 'No policy evidence retained',
                                           'Recommendation': 'Review policy evidence',
                                           'Status': 'Not Assessed', 'Priority': 'Medium'}], evidence_bundle={
                'collection_context': {'mode': 'offline', 'collected_at': '2026-09-14T12:00:00Z'},
                'source_statuses': {'entra_recommendations': {'availability_status': 'unavailable'}},
                'portal_review': {'captures': [{'previews': [{}, {}], 'extracted_pages': [{}, {}]},
                                               {'previews': [{}, {}, {}], 'extracted_pages': [{}, {}, {}]}]},
            })
        self.assertIn('Saved collection gaps: 1', output.getvalue())
        self.assertIn('Admin-center captures: 2 captures, 5 pages.', output.getvalue())

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

"""CLI timing survives failures and stays independent of assessment evidence."""

import io
from pathlib import Path
import runpy
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from provenance_fixture import fictional_revision
from Core import console_reporting as console
from Core.run_progress import RunProgress, format_duration, phase, record_connection_results


class RunProgressTests(unittest.TestCase):
    def setUp(self):
        console.configure_console(color='never')
        self.addCleanup(console.configure_console)
        self.now = 100.0
        self.clock = patch('Core.run_progress.time.monotonic', side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_duration_is_readable_for_short_and_long_runs(self):
        for seconds, expected in ((0.1, '<1s'), (9, '9s'), (65, '1m 05s'),
                                  (7384, '2h 03m 04s')):
            with self.subTest(seconds=seconds):
                self.assertEqual(format_duration(seconds), expected)

    def test_live_stages_record_elapsed_time_and_success_without_color(self):
        output = io.StringIO()
        with redirect_stdout(output), RunProgress():
            self.now += 2
            phase('access')
            self.now += 5
            phase('collection')
            self.now += 65
            phase('reports')
            self.now += 9
        text = output.getvalue()
        self.assertIn('[1/4] Check configuration and dependencies', text)
        self.assertIn('[3/4] Collect tenant evidence', text)
        self.assertIn('Elapsed: 1m 12s', text)
        self.assertIn('RUN COMPLETE', text)
        self.assertIn('Total elapsed: 1m 21s', text)
        self.assertIn('Collect tenant evidence: 1m 05s (done)', text)
        self.assertIn('Build reports: 9s (done)', text)
        self.assertIn('Started:', text)
        self.assertIn('Finished:', text)
        self.assertNotIn('\x1b[', text)

    def test_failure_retains_current_duration_and_does_not_complete_later_stages(self):
        output = io.StringIO()
        with self.assertRaisesRegex(ValueError, 'invalid input'), redirect_stdout(output):
            with RunProgress(offline=True):
                self.now += 2
                phase('inputs')
                self.now += 10
                raise ValueError('invalid input')
        text = output.getvalue()
        self.assertIn('RUN FAILED', text)
        self.assertIn('Total elapsed: 12s', text)
        self.assertIn('Check configuration and dependencies: 2s (done)', text)
        self.assertIn('Load saved evidence and exports: 10s (failed)', text)
        self.assertNotIn('Build reports', text)
        self.assertNotIn('RUN COMPLETE', text)

    def test_keyboard_interrupt_preserves_timings_and_restores_context(self):
        output = io.StringIO()
        with self.assertRaises(KeyboardInterrupt), redirect_stdout(output):
            with RunProgress():
                phase('access')
                self.now += 3
                raise KeyboardInterrupt
        self.assertIn('RUN INTERRUPTED', output.getvalue())
        self.assertIn('Prepare and verify access: 3s (interrupted)', output.getvalue())
        after = io.StringIO()
        with redirect_stdout(after):
            phase('collection')
        self.assertEqual(after.getvalue(), '')

    def test_preflight_has_two_stages_without_claiming_collection_or_reports(self):
        output = io.StringIO()
        with redirect_stdout(output), RunProgress(check_connections=True):
            phase('access')
            self.now += 4
        text = output.getvalue()
        self.assertIn('[2/2] Prepare and verify access', text)
        self.assertIn('Total elapsed: 4s', text)
        self.assertIn('CONNECTION CHECKS COMPLETE', text)
        self.assertNotIn('Collect tenant evidence', text)
        self.assertNotIn('Build reports', text)

    def test_preflight_nonzero_result_without_receipt_reports_issues_and_preserves_exit_code(self):
        for exit_code in (1, 2):
            with self.subTest(exit_code=exit_code):
                output = io.StringIO()
                with self.assertRaises(SystemExit) as exited, redirect_stdout(output):
                    with RunProgress(check_connections=True):
                        phase('access')
                        self.now += 42
                        raise SystemExit(exit_code)
                self.assertEqual(exited.exception.code, exit_code)
                text = output.getvalue()
                self.assertIn('CONNECTION CHECKS COMPLETE WITH ISSUES', text)
                self.assertIn('Prepare and verify access: 42s (done with issues)', text)
                self.assertNotIn('RUN FAILED', text)

    def test_expected_license_gap_and_planned_sign_in_have_a_final_warning_summary(self):
        from Core.connection_validation import print_connection_results, connection_exit_code
        results = [{'collector': f'Ready source {index}', 'status': 'Ready', 'reason': ''} for index in range(14)]
        results += [
            {'collector': 'Entra risk', 'status': 'License unavailable', 'reason': 'Risk evidence requires Entra ID P2.'},
            {'collector': 'SharePoint governance', 'status': 'Sign-in required',
             'reason': 'Browser sign-in will be requested during collection.'},
            {'collector': 'Optional disabled source', 'status': 'Optional source not selected', 'reason': ''},
        ]
        output = io.StringIO()
        with self.assertRaises(SystemExit) as exited, redirect_stdout(output):
            with RunProgress(check_connections=True):
                phase('access')
                print_connection_results(results)
                console.section('Evidence collection plan')
                self.now += 28
                raise SystemExit(connection_exit_code(results))
        text = output.getvalue()
        self.assertEqual(2, exited.exception.code)
        self.assertIn('CONNECTION CHECKS COMPLETE WITH WARNINGS', text)
        self.assertIn('Connection summary: 14 successful, 2 warnings, 0 failed.', text)
        self.assertIn('Warning - Entra risk: Risk evidence requires Entra ID P2.', text)
        self.assertIn('Warning - SharePoint governance: Sign-in planned during collection.', text)
        self.assertIn('Prepare and verify access: 28s (done with warnings)', text)
        self.assertIn('Next: continue with the full assessment', text)
        self.assertEqual(1, text.count('Connection summary:'))
        self.assertGreater(text.index('Connection summary:'), text.index('Evidence collection plan'))
        self.assertNotIn('NEED ATTENTION', text)
        self.assertNotIn('Setup required - SharePoint governance', text)

    def test_failed_read_stays_visible_even_when_its_exit_code_is_zero(self):
        results = [{'collector': 'Graph', 'status': 'Ready', 'reason': ''},
                   {'collector': 'Optional usage source', 'status': 'Connection failed', 'reason': 'Download failed.'}]
        output = io.StringIO()
        with redirect_stdout(output), RunProgress(check_connections=True):
            phase('access')
            record_connection_results(results)
        text = output.getvalue()
        self.assertIn('CONNECTION CHECKS COMPLETE WITH ISSUES', text)
        self.assertIn('1 successful, 0 warnings, 1 failed', text)
        self.assertIn('Failed - Optional usage source: Download failed.', text)
        self.assertNotIn('Next: continue with the full assessment', text)

    def test_unplanned_sign_in_and_missing_consent_remain_setup_issues(self):
        results = [
            {'collector': 'SharePoint', 'status': 'Sign-in required', 'reason': 'Browser authentication is disabled.'},
            {'collector': 'Labels', 'status': 'Permission missing', 'reason': 'Grant SensitivityLabels.Read.All.'},
        ]
        output = io.StringIO()
        with self.assertRaises(SystemExit) as exited, redirect_stdout(output):
            with RunProgress(check_connections=True):
                phase('access')
                record_connection_results(results)
                raise SystemExit(2)
        self.assertEqual(2, exited.exception.code)
        text = output.getvalue()
        self.assertIn('CONNECTION CHECKS COMPLETE WITH ISSUES', text)
        self.assertIn('0 successful, 0 warnings, 0 failed, 2 setup items', text)
        self.assertIn('Setup required - Labels: Grant SensitivityLabels.Read.All.', text)

    def test_all_ready_summary_does_not_include_warnings_or_disabled_sources(self):
        results = [{'collector': 'Graph', 'status': 'Ready', 'reason': ''},
                   {'collector': 'Optional source', 'status': 'Optional source not selected', 'reason': ''}]
        output = io.StringIO()
        with redirect_stdout(output), RunProgress(check_connections=True):
            phase('access')
            record_connection_results(results)
        text = output.getvalue()
        self.assertIn('CONNECTION CHECKS COMPLETE\n', text)
        self.assertIn('1 successful, 0 warnings, 0 failed', text)
        self.assertNotIn('WITH WARNINGS', text)

    def test_preflight_startup_failure_or_exception_still_reports_failure(self):
        for exc, in_access in ((SystemExit(1), False), (ValueError('unexpected'), True)):
            with self.subTest(exc=type(exc).__name__):
                output = io.StringIO()
                with self.assertRaises(type(exc)), redirect_stdout(output):
                    with RunProgress(check_connections=True):
                        if in_access:
                            phase('access')
                            record_connection_results([{'collector': 'Graph', 'status': 'Ready', 'reason': ''}])
                        raise exc
                self.assertIn('RUN FAILED', output.getvalue())
                self.assertNotIn('CONNECTION CHECKS NEED ATTENTION', output.getvalue())

    def test_main_reports_offline_exit_code_and_keeps_timing_without_bars(self):
        def build(args):
            self.assertTrue(args.no_progress)
            phase('inputs')
            self.now += 6
            phase('reports')
            self.now += 3
            return exit_code

        for exit_code, heading in ((0, 'RUN COMPLETE'), (1, 'RUN FAILED')):
            with self.subTest(exit_code=exit_code):
                output = io.StringIO()
                with patch('sys.argv', ['main.py', '--mode', 'offline', '--reports-dir', 'tests/fixtures/microsoft_reports', '--no-progress', '--color', 'never']), \
                        patch('Core.run_plan.capture_revision', return_value=fictional_revision()), \
                patch('Core.console_setup.setup_console_encoding'), \
                        patch('Core.check_dependencies.check_dependencies'), \
                        patch('Core.offline_report.run_offline_report', side_effect=build), \
                        redirect_stdout(output), self.assertRaises(SystemExit) as exited:
                    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'main.py'), run_name='__main__')
                self.assertEqual(exited.exception.code, exit_code)
                text = output.getvalue()
                self.assertIn('[2/3] Load saved evidence and exports', text)
                self.assertIn('[3/3] Build reports', text)
                self.assertIn(heading, text)
                self.assertIn('Total elapsed: 9s', text)

    def test_main_ctrl_c_exits_cleanly_after_interrupted_summary(self):
        output = io.StringIO()
        with patch('sys.argv', ['main.py', '--mode', 'offline', '--reports-dir', 'tests/fixtures/microsoft_reports', '--color', 'never']), \
                patch('Core.run_plan.capture_revision', return_value=fictional_revision()), \
                patch('Core.console_setup.setup_console_encoding'), \
                patch('Core.check_dependencies.check_dependencies'), \
                patch('Core.offline_report.run_offline_report', side_effect=KeyboardInterrupt), \
                redirect_stdout(output), self.assertRaises(SystemExit) as exited:
            runpy.run_path(str(Path(__file__).resolve().parents[1] / 'main.py'), run_name='__main__')
        self.assertEqual(exited.exception.code, 130)
        self.assertIn('RUN INTERRUPTED', output.getvalue())
        self.assertNotIn('RUN COMPLETE', output.getvalue())


if __name__ == '__main__':
    unittest.main()

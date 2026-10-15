"""Offline report troubleshooting with secret-safe output and separate downloads."""

import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('usage_report_diagnostic', ROOT / 'tools/diagnose-usage-report.py')
diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic)
CSV = 'Report Refresh Date,Office 365,Report Date,Report Period\n2026-10-01,2,2026-10-01,7\n'
SIGNED_URL = 'https://reports.office.com/data/download/private-report-token?signature=private-signature'


class ReportDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def run_diagnostic(self, handler, *, windows_success=False, credential_error=None):
        seen = []
        def respond(request):
            seen.append(request)
            return handler(request)
        def factory(**kwargs):
            return httpx.AsyncClient(transport=httpx.MockTransport(respond), **kwargs)
        windows_seen = []
        def windows_probe(url):
            windows_seen.append(str(url))
            return {'success': windows_success, 'status_code': 200 if windows_success else 0,
                    'error_type': 'WebException'}
        def get_token(scope):
            if credential_error:
                raise credential_error
            self.assertEqual(diagnostic.GRAPH_SCOPE, scope)
            return SimpleNamespace(token='private-access-token')
        output = io.StringIO()
        with redirect_stdout(output):
            code = await diagnostic.diagnose(SimpleNamespace(get_token=get_token),
                                              client_factory=factory, windows_probe=windows_probe)
        text = output.getvalue()
        for value in ('private-access-token', 'private-report-token', 'private-signature', 'secret-client-details'):
            self.assertNotIn(value, text)
        return code, text, seen, windows_seen

    async def test_redirect_success_does_not_forward_graph_authorization_or_show_rows(self):
        def handler(request):
            if request.url.host == 'graph.microsoft.com':
                return httpx.Response(302, headers={'Location': SIGNED_URL})
            return httpx.Response(200, text=CSV)
        code, text, seen, windows_seen = await self.run_diagnostic(handler)
        self.assertEqual(0, code)
        self.assertIn('Python report download: PASS', text)
        self.assertNotIn('2026-10-01,2', text)
        self.assertEqual('Bearer private-access-token', seen[0].headers['Authorization'])
        self.assertNotIn('Authorization', seen[1].headers)
        self.assertEqual([], windows_seen)

    async def test_transport_failure_compares_windows_and_sanitizes_the_exception_chain(self):
        for windows_success in (True, False):
            with self.subTest(windows_success=windows_success):
                def handler(request):
                    if request.url.host == 'graph.microsoft.com':
                        return httpx.Response(302, headers={'Location': SIGNED_URL})
                    try:
                        raise ConnectionResetError(10054, 'secret-client-details ' + SIGNED_URL)
                    except ConnectionResetError as exc:
                        raise httpx.ReadError('secret-client-details ' + SIGNED_URL, request=request) from exc
                code, text, _seen, windows_seen = await self.run_diagnostic(handler, windows_success=windows_success)
                self.assertEqual(1, code)
                self.assertIn('ReadError -> ConnectionResetError (errno=10054)', text)
                self.assertEqual([SIGNED_URL], windows_seen)
                self.assertIn('Windows downloaded the same report' if windows_success else 'Both Python and Windows failed', text)

    async def test_authentication_and_graph_errors_do_not_attempt_download(self):
        code, text, seen, windows_seen = await self.run_diagnostic(
            lambda request: httpx.Response(200), credential_error=ValueError('secret-client-details'))
        self.assertEqual(2, code)
        self.assertIn('Authentication: FAIL (ValueError)', text)
        self.assertEqual([], seen)
        self.assertEqual([], windows_seen)
        code, text, seen, windows_seen = await self.run_diagnostic(
            lambda request: httpx.Response(403, text='secret-client-details'))
        self.assertEqual(2, code)
        self.assertIn('Reports.Read.All', text)
        self.assertEqual(1, len(seen))
        self.assertEqual([], windows_seen)

    async def test_invalid_redirect_does_not_trigger_download(self):
        for location in ('', 'http://reports.office.com/data/private-report-token', 'https://user:password@reports.office.com/'):
            with self.subTest(location=location):
                code, text, seen, windows_seen = await self.run_diagnostic(
                    lambda request: httpx.Response(302, headers={'Location': location}))
                self.assertEqual(2, code)
                self.assertIn('missing or invalid HTTPS destination', text)
                self.assertEqual(1, len(seen))
                self.assertEqual([], windows_seen)

    async def test_success_status_with_proxy_html_is_not_treated_as_a_report(self):
        code, text, _seen, _windows_seen = await self.run_diagnostic(
            lambda request: httpx.Response(200, text='<html>secret-client-details proxy login</html>'))
        self.assertEqual(2, code)
        self.assertIn('not the expected aggregate report CSV', text)


class JsonReportDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def run_json_diagnostic(self, handler):
        seen = []
        def respond(request):
            seen.append(request)
            return handler(request)
        def factory(**kwargs):
            return httpx.AsyncClient(transport=httpx.MockTransport(respond), **kwargs)
        credential = SimpleNamespace(get_token=lambda scope: SimpleNamespace(token='private-access-token'))
        output = io.StringIO()
        with redirect_stdout(output):
            code = await diagnostic.diagnose_json(credential, client_factory=factory)
        text = output.getvalue()
        for secret in ('private-access-token', 'private-report-token', 'private-signature', 'secret-client-details'):
            self.assertNotIn(secret, text)
        return code, text, seen

    async def test_direct_json_uses_same_application_and_withholds_report_values(self):
        row = {'reportRefreshDate': '2026-10-01', 'reportDate': '2026-10-01',
               'reportPeriod': '7', 'office365': 987654321}
        code, text, seen = await self.run_json_diagnostic(lambda request: httpx.Response(200, json={'value': [row]}))
        self.assertEqual(0, code)
        self.assertIn('PASS (HTTP 200; 1 aggregate rows', text)
        self.assertNotIn('987654321', text)
        self.assertEqual(1, len(seen))
        self.assertEqual('graph.microsoft.com', seen[0].url.host)
        self.assertEqual('/beta/reports/getOffice365ActiveUserCounts(period=\'D7\')', seen[0].url.path)
        self.assertEqual('application/json', seen[0].url.params['$format'])
        self.assertEqual('Bearer private-access-token', seen[0].headers['Authorization'])

    async def test_redirect_is_not_followed_or_printed(self):
        code, text, seen = await self.run_json_diagnostic(
            lambda request: httpx.Response(302, headers={'Location': SIGNED_URL}))
        self.assertEqual(1, code)
        self.assertEqual(1, len(seen))
        self.assertIn('No redirect was followed', text)

    async def test_html_empty_or_wrong_schema_is_inconclusive(self):
        for response in (httpx.Response(200, text='<html>secret-client-details</html>'),
                         httpx.Response(200, json={'value': []}),
                         httpx.Response(200, json={'value': [{'unexpected': 'secret-client-details'}]})):
            with self.subTest(response=response):
                code, text, _seen = await self.run_json_diagnostic(lambda request: response)
                self.assertEqual(1, code)
                self.assertIn('inconclusive', text)

    async def test_transport_failure_does_not_echo_request_details(self):
        def handler(request):
            raise httpx.ReadError('secret-client-details ' + SIGNED_URL, request=request)
        code, text, _seen = await self.run_json_diagnostic(handler)
        self.assertEqual(1, code)
        self.assertIn('FAIL (ReadError)', text)


class ReportMatrixTests(unittest.IsolatedAsyncioTestCase):
    async def run_matrix(self, handler):
        seen = []
        def respond(request):
            seen.append(request)
            return handler(request)
        def factory(**kwargs):
            return httpx.AsyncClient(transport=httpx.MockTransport(respond), **kwargs)
        output = io.StringIO()
        spec = ('Active users D7', diagnostic.REPORT_URL,
                ('Report Refresh Date', 'Report Date', 'Report Period', 'Office 365'),
                ('reportRefreshDate', 'reportDate', 'reportPeriod', 'office365'), diagnostic.JSON_REPORT_URL)
        credential = SimpleNamespace(get_token=lambda scope: SimpleNamespace(token='private-access-token'))
        with patch.object(diagnostic, 'matrix_reports', return_value=[spec]), redirect_stdout(output):
            code = await diagnostic.diagnose_matrix(credential, client_factory=factory)
        text = output.getvalue()
        for secret in ('private-access-token', 'private-report-token', 'private-signature', 'secret-client-details'):
            self.assertNotIn(secret, text)
        return code, text, seen

    async def test_csv_reset_is_distinct_from_successful_json_and_token_is_not_forwarded(self):
        def handler(request):
            if request.url.host != 'graph.microsoft.com':
                raise httpx.ReadError('secret-client-details ' + SIGNED_URL, request=request)
            if request.url.params['$format'] == 'text/csv':
                return httpx.Response(302, headers={'Location': SIGNED_URL, 'request-id': 'secret-client-details'})
            return httpx.Response(200, json={'value': [{'reportRefreshDate': '2026-10-01',
                'reportDate': '2026-10-01', 'reportPeriod': '7', 'office365': 987654321}]})
        code, text, seen = await self.run_matrix(handler)
        self.assertEqual(1, code)
        self.assertIn('CSV | FAIL | Download reports.office.com | Graph HTTP 302 | ReadError', text)
        self.assertIn('JSON | PASS | Graph graph.microsoft.com | Graph HTTP 200', text)
        self.assertNotIn('987654321', text)
        self.assertNotIn('Authorization', seen[1].headers)
        self.assertIn('Authorization', seen[2].headers)
        self.assertEqual('/beta/reports/getOffice365ActiveUserCounts(period=\'D7\')', seen[2].url.path)

    async def test_empty_report_is_separate_from_connection_failure(self):
        def handler(request):
            return httpx.Response(200, text=CSV.splitlines()[0]) if request.url.params['$format'] == 'text/csv' else (
                httpx.Response(200, json={'value': []}))
        code, text, _seen = await self.run_matrix(handler)
        self.assertEqual(1, code)
        self.assertIn('CSV | EMPTY', text)
        self.assertIn('JSON | EMPTY', text)
        self.assertNotIn(' | PASS | ', text)

    async def test_json_redirect_is_not_downloaded_and_csv_response_is_validated(self):
        def handler(request):
            return httpx.Response(200, text=CSV) if request.url.params['$format'] == 'text/csv' else (
                httpx.Response(302, headers={'Location': SIGNED_URL}))
        code, text, seen = await self.run_matrix(handler)
        self.assertEqual(1, code)
        self.assertEqual(2, len(seen))
        self.assertIn('CSV | PASS', text)
        self.assertIn('JSON | FAIL', text)
        self.assertIn('redirect not followed', text)

    async def test_http_errors_and_html_do_not_expose_response_content(self):
        for response in (httpx.Response(403, text='secret-client-details'),
                         httpx.Response(200, text='<html>secret-client-details</html>')):
            with self.subTest(response=response):
                code, text, seen = await self.run_matrix(lambda request: response)
                self.assertEqual(1, code)
                self.assertEqual(2, len(seen))
                self.assertNotIn(' | PASS | ', text)

    def test_copilot_csv_summary_and_detail_do_not_require_a_report_date_column(self):
        cases = (
            ('Report Refresh Date,Report Period,Any App Active Users\n2026-10-01,7,2\n',
             ('Report Refresh Date', 'Report Period', 'Any App Active Users')),
            ('Report Refresh Date,Report Period,User Principal Name\n2026-10-01,28,secret-client-details\n',
             ('Report Refresh Date', 'Report Period', 'User Principal Name')),
        )
        for csv, fields in cases:
            status, detail = diagnostic.matrix_payload_summary(httpx.Response(200, text=csv), fields, ())
            self.assertEqual('PASS', status)
            self.assertNotIn('secret-client-details', detail)


class WindowsDiagnosticTests(unittest.TestCase):
    def test_temporary_url_is_sent_only_via_stdin_and_stderr_is_not_exposed(self):
        process = SimpleNamespace(stdout=json.dumps({'success': True, 'status_code': 200}),
                                  stderr='secret-client-details ' + SIGNED_URL, returncode=0)
        with patch.object(diagnostic.os, 'name', 'nt'), patch.object(diagnostic.shutil, 'which', return_value='powershell.exe'), \
                patch.object(diagnostic.subprocess, 'run', return_value=process) as run:
            result = diagnostic.windows_download(diagnostic.download_url(SIGNED_URL))
        self.assertTrue(result['success'])
        self.assertNotIn(SIGNED_URL, str(run.call_args.args))
        self.assertEqual(SIGNED_URL, json.loads(run.call_args.kwargs['input'])['url'])
        self.assertNotIn('secret-client-details', str(result))

    def test_http_200_with_an_unexpected_response_is_explained(self):
        output = io.StringIO()
        with redirect_stdout(output):
            diagnostic.show_result('Python report download', {'success': False, 'status_code': 200})
        self.assertIn('response is not the expected aggregate report CSV', output.getvalue())


if __name__ == '__main__':
    unittest.main()

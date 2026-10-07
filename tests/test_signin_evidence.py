"""Legacy-client reconciliation and exact, unchanged sign-in request provenance."""

import io
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from Core.get_entra_client import get_entra_client, _fetch_graph_collection_via_http
from Core.signin_evidence import is_legacy_signin, LEGACY_CLASSIFICATION_RULE


class SigninClassificationTests(unittest.TestCase):
    def test_historical_substring_classifier_is_unchanged_for_dict_and_sdk_shapes(self):
        markers = ['pop', 'imap', 'smtp', 'activesync', 'other clients', 'exchange web services']
        values = [None, '', 'Browser', 'Mobile Apps and Desktop clients', 'Authenticated SMTP',
                  'POP3', 'IMAP4', 'Exchange ActiveSync', 'Other clients', 'Exchange Web Services',
                  'PREFIX POP SUFFIX', 'popcorn', 'Other', 'Modern browser']
        for value in values:
            expected = any(marker in str(value or '').lower() for marker in markers)
            for record in ({'clientAppUsed': value}, {'client_app_used': value},
                           SimpleNamespace(clientAppUsed=value), SimpleNamespace(client_app_used=value)):
                with self.subTest(value=value, shape=type(record).__name__):
                    self.assertEqual(is_legacy_signin(record), expected)
        self.assertFalse(is_legacy_signin(None))
        self.assertFalse(is_legacy_signin({}))
        self.assertTrue(is_legacy_signin({'clientAppUsed': None, 'client_app_used': 'IMAP'}))

    def test_failed_and_blocked_events_are_classifications_not_successful_bypasses(self):
        for error_code, ca_status in ((0, 'notApplied'), (50076, 'failure'), (50126, 'notApplied')):
            self.assertTrue(is_legacy_signin({'clientAppUsed': 'SMTP',
                                            'status': {'errorCode': error_code},
                                            'conditionalAccessStatus': ca_status}))
        self.assertIn('does not establish successful authentication or a control bypass', LEGACY_CLASSIFICATION_RULE)


class SigninCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, *, fail_second=False):
        calls = []
        first_rows = [{'id': 'failed-smtp', 'clientAppUsed': 'Authenticated SMTP',
                       'status': {'errorCode': 50126}, 'conditionalAccessStatus': 'failure'},
                      {'id': 'modern-browser', 'clientAppUsed': 'Browser', 'status': {'errorCode': 0}}]
        last_rows = [{'id': 'imap', 'clientAppUsed': 'IMAP', 'status': {'errorCode': 0}},
                     {'id': 'unknown', 'clientAppUsed': None, 'status': {'errorCode': 0}}]

        def respond(request):
            if request.url.path == '/v1.0/auditLogs/signIns':
                calls.append(request.url)
                if request.url.params.get('$skiptoken'):
                    if fail_second:
                        return httpx.Response(403, json={'error': {'code': 'Forbidden', 'message': 'Continuation access denied'}})
                    return httpx.Response(200, json={'value': last_rows})
                return httpx.Response(200, json={'value': first_rows,
                    '@odata.nextLink': 'https://graph.microsoft.com/v1.0/auditLogs/signIns?$skiptoken=second'})
            return httpx.Response(200, json={'value': []})

        async def client():
            return httpx.AsyncClient(base_url='https://graph.microsoft.com', transport=httpx.MockTransport(respond))

        with patch('Core.get_entra_client._get_graph_http_client', side_effect=client), redirect_stdout(io.StringIO()):
            result = await get_entra_client(SimpleNamespace(), 'test-tenant')
        return result, calls, first_rows, last_rows

    async def test_all_pages_reconcile_with_summary_and_record_exact_bounded_window(self):
        client, calls, first_rows, last_rows = await self.collect()
        self.assertEqual(client.signin_logs, first_rows + last_rows)
        self.assertEqual(client.signin_summary['total_signins_sampled'], 4)
        self.assertEqual(client.signin_summary['legacy_auth_attempts'], 2)
        self.assertEqual(client.signin_summary['legacy_auth_attempts'], sum(map(is_legacy_signin, client.signin_logs)))
        state = client.collection_status['signin_logs']
        self.assertEqual(state['source_api'], 'https://graph.microsoft.com/v1.0/auditLogs/signIns')
        self.assertEqual(httpx.URL(state['request_url']), calls[0])
        self.assertEqual(dict(calls[0].params), state['request_params'])
        self.assertEqual(state['filters'], state['request_params'])
        self.assertEqual(state['filter'], 'createdDateTime ge ' + state['window_start'] + ' and createdDateTime le ' + state['window_end'])
        self.assertTrue(state['window_end'])
        self.assertIn(' le ', state['filter'])
        self.assertEqual(state['page_size'], 999)
        self.assertEqual(state['max_pages'], 100)
        self.assertEqual(state['pages_collected'], 2)
        self.assertEqual(state['records_collected'], 4)
        self.assertFalse(state['truncated'])
        self.assertEqual(state['availability_status'], 'available')
        started = datetime.fromisoformat(state['collection_started_at'])
        finished = datetime.fromisoformat(state['collection_completed_at'])
        self.assertIsNotNone(started.tzinfo)
        self.assertGreaterEqual(finished, started)
        self.assertIn('le', state['collection_window'])
        self.assertIn('failed and blocked attempts', state['limitations'])
        self.assertIn('AuditLog.Read.All', state['permissions'])

    async def test_failed_continuation_keeps_records_paging_and_request_provenance(self):
        client, calls, first_rows, _ = await self.collect(fail_second=True)
        state = client.collection_status['signin_logs']
        self.assertEqual(client.signin_logs, first_rows)
        self.assertEqual(client.signin_summary['legacy_auth_attempts'], 1)
        self.assertEqual(state['availability_status'], 'partial')
        self.assertEqual(state['pages_collected'], 1)
        self.assertEqual(state['records_collected'], 2)
        self.assertEqual(state['status_code'], 403)
        self.assertTrue(state['truncated'])
        self.assertIn('Continuation access denied', state['reason'])
        self.assertEqual(httpx.URL(state['request_url']), calls[0])

    async def test_page_safety_limit_is_reported_without_losing_first_page(self):
        http = httpx.AsyncClient(base_url='https://graph.microsoft.com', transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={'value': [{'id': 'one', 'clientAppUsed': 'POP'}],
                '@odata.nextLink': 'https://graph.microsoft.com/v1.0/auditLogs/signIns?$skiptoken=next'})))
        with patch('Core.get_entra_client._get_graph_http_client', AsyncMock(return_value=http)):
            result = await _fetch_graph_collection_via_http('/v1.0/auditLogs/signIns',
                params={'$filter': 'createdDateTime ge 2026-09-20T00:00:00Z', '$top': '999'}, max_pages=1)
        self.assertEqual(result['max_pages'], 1)
        self.assertEqual(result['pages_collected'], 1)
        self.assertEqual(result['records_collected'], 1)
        self.assertEqual(result['availability_status'], 'partial')
        self.assertTrue(result['truncated'])
        self.assertIn('Pagination safety limit', result['reason'])
        self.assertTrue(http.is_closed)


if __name__ == '__main__':
    unittest.main()

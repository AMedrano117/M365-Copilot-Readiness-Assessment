"""Offline retries and partial evidence tests; every retry sleep is mocked."""

import asyncio
from datetime import datetime, timezone
import io
from contextlib import redirect_stdout
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from Core.ai_usage import _get_all_json, _get_report_payload
from Core.get_defender_client import _collect_mde_machines
from Core.get_entra_client import _fetch_graph_collection_via_http, get_entra_client
from Core.get_graph_client import GraphRestClient
from Core.http_retry import RequestRetryError, request_with_retry, retry_after_seconds
from Core.source_evidence import source_is_complete


class RetryPolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        sleep_patch = patch('Core.http_retry.asyncio.sleep', new=AsyncMock())
        self.sleep = sleep_patch.start()
        self.addCleanup(sleep_patch.stop)
        jitter_patch = patch('Core.http_retry.random.uniform', return_value=0.2)
        jitter_patch.start()
        self.addCleanup(jitter_patch.stop)

    async def test_honors_retry_after_larger_than_old_delay_cap(self):
        send = AsyncMock(side_effect=[httpx.Response(429, headers={'Retry-After': '45'}), httpx.Response(200)])
        self.assertEqual((await request_with_retry(send)).status_code, 200)
        self.sleep.assert_awaited_once_with(45.0)
        self.assertEqual(send.await_count, 2)

    async def test_server_delay_above_budget_stops_without_premature_retry(self):
        send = AsyncMock(return_value=httpx.Response(429, headers={'Retry-After': '121'}))
        with self.assertRaisesRegex(RequestRetryError, 'remaining retry wait budget') as caught:
            await request_with_retry(send)
        self.assertEqual(caught.exception.status_code, 429)
        send.assert_awaited_once()
        self.sleep.assert_not_awaited()

    async def test_cumulative_wait_budget_stops_next_delay(self):
        send = AsyncMock(return_value=httpx.Response(503, headers={'Retry-After': '70'}))
        with self.assertRaisesRegex(RequestRetryError, r'\(50s\)'):
            await request_with_retry(send)
        self.assertEqual(send.await_count, 2)
        self.sleep.assert_awaited_once_with(70.0)

    async def test_numeric_and_date_retry_after(self):
        now = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
        self.assertEqual(retry_after_seconds('Thu, 17 Sep 2026 12:00:45 GMT', now=now), 45)
        self.assertEqual(retry_after_seconds('Thu, 17 Sep 2026 11:59:45 GMT', now=now), 0)
        for value in ('NaN', 'inf', 'invalid', None):
            self.assertIsNone(retry_after_seconds(value))
        self.assertEqual(retry_after_seconds('0'), 0)

    async def test_transport_errors_back_off_and_recover(self):
        send = AsyncMock(side_effect=[httpx.ConnectError('offline'), httpx.ReadTimeout('slow'), httpx.Response(200)])
        self.assertEqual((await request_with_retry(send)).status_code, 200)
        self.assertEqual([item.args[0] for item in self.sleep.await_args_list], [1.2, 2.2])

    async def test_transient_statuses_retry_but_count_is_bounded(self):
        for code in (429, 500, 502, 503, 504):
            with self.subTest(code=code):
                send = AsyncMock(return_value=httpx.Response(code))
                response = await request_with_retry(send)
                self.assertEqual(response.status_code, code)
                self.assertEqual(send.await_count, 5)

    async def test_nontransient_errors_and_mutations_do_not_retry(self):
        for code in (400, 401, 403, 404, 501):
            send = AsyncMock(return_value=httpx.Response(code))
            await request_with_retry(send)
            send.assert_awaited_once()
        send = AsyncMock(return_value=httpx.Response(503))
        await request_with_retry(send, method='POST')
        send.assert_awaited_once()
        self.sleep.assert_not_awaited()

    async def test_cancellation_is_not_retried_or_swallowed(self):
        send = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await request_with_retry(send)
        send.assert_awaited_once()
        self.sleep.assert_not_awaited()

    async def test_final_transport_failure_does_not_echo_sensitive_url(self):
        send = AsyncMock(side_effect=httpx.ReadTimeout('https://private.example?secret=hidden'))
        with self.assertRaisesRegex(RequestRetryError, '5 attempt') as caught:
            await request_with_retry(send)
        self.assertNotIn('hidden', str(caught.exception))
        self.assertEqual(send.await_count, 5)


class CollectorRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        sleep_patch = patch('Core.http_retry.asyncio.sleep', new=AsyncMock())
        self.sleep = sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def raw_client(self, responder, base='https://graph.microsoft.com'):
        return httpx.AsyncClient(base_url=base, transport=httpx.MockTransport(responder))

    async def test_defender_preflight_retries_temporary_throttling(self):
        from Core.connection_validation import _defender_probe, READY
        client = SimpleNamespace(get=AsyncMock(side_effect=[
            httpx.Response(429, headers={'Retry-After': '2'}), httpx.Response(200)]),
            aclose=AsyncMock())
        with patch('Core.connection_validation.get_api_client', AsyncMock(return_value=client)):
            result = await _defender_probe({'Machine.Read.All'})
        self.assertEqual(READY, result['status'])
        self.assertEqual(2, client.get.await_count)
        self.sleep.assert_awaited_once_with(2.0)
        client.aclose.assert_awaited_once()

    async def test_graph_transport_failure_preserves_completed_pages_even_empty(self):
        for rows in ([{'id': 'one'}], []):
            with self.subTest(rows=rows):
                calls = []
                def respond(request):
                    calls.append(request.url.path)
                    if request.url.path == '/first':
                        return httpx.Response(200, json={'value': rows, '@odata.nextLink': '/second'})
                    raise httpx.ReadTimeout('lost connection', request=request)
                graph = GraphRestClient.__new__(GraphRestClient)
                graph.credential = SimpleNamespace(get_token=lambda scope: SimpleNamespace(token='synthetic'))
                graph.max_retries = 4
                graph._http = self.raw_client(respond)
                try:
                    result = await graph.get_collection('/first')
                finally:
                    await graph.aclose()
                self.assertEqual(result['value'], rows)
                self.assertEqual(result['availability_status'], 'partial')
                self.assertEqual(result['pages_collected'], 1)
                self.assertTrue(result['truncated'])
                self.assertEqual(calls.count('/second'), 5)
                self.assertIn('retry limit', result['reason'])

    async def test_entra_transport_failure_keeps_pages_and_source_is_incomplete(self):
        calls = []
        def respond(request):
            calls.append(request.url.path)
            if request.url.path == '/first':
                return httpx.Response(200, json={'value': [{'id': 'one'}], '@odata.nextLink': '/second'})
            raise httpx.ConnectError('offline', request=request)
        http = self.raw_client(respond)
        with patch('Core.get_entra_client._get_graph_http_client', AsyncMock(return_value=http)):
            result = await _fetch_graph_collection_via_http('/first')
        self.assertEqual(result['value'], [{'id': 'one'}])
        self.assertEqual(result['availability_status'], 'partial')
        self.assertEqual(result['pages_collected'], 1)
        self.assertTrue(result['truncated'])
        self.assertEqual(calls.count('/second'), 5)
        self.assertTrue(http.is_closed)
        self.assertFalse(source_is_complete(SimpleNamespace(collection_status={'test': result}), 'test'))

    async def test_entra_retains_partial_mfa_rows_without_claiming_complete_registration(self):
        def respond(request):
            if request.url.path.endswith('userRegistrationDetails'):
                return httpx.Response(200, json={'value': [{'id': 'one', 'isMfaRegistered': True}],
                                                '@odata.nextLink': '/second'})
            if request.url.path == '/second':
                raise httpx.ReadTimeout('offline', request=request)
            return httpx.Response(200, json={'value': []})
        async def client():
            return self.raw_client(respond)
        with patch('Core.get_entra_client._get_graph_http_client', side_effect=client), \
             patch('Core.get_entra_client._get_graph_token_roles', return_value=set()), redirect_stdout(io.StringIO()):
            entra = await get_entra_client(SimpleNamespace(), 'fictional', permission_profile='restricted')
        self.assertEqual(entra.auth_methods_registration[0]['id'], 'one')
        self.assertEqual(entra.collection_status['auth_methods']['availability_status'], 'partial')
        self.assertFalse(entra.data_sources['auth_methods'])
        self.assertFalse(source_is_complete(entra, 'auth_methods'))
        self.assertEqual(entra.collection_status['groups']['availability_status'], 'not_requested')

    async def test_defender_transport_or_http_failure_preserves_pages_and_page_count(self):
        for failure in ('transport', 'http', 'budget'):
            with self.subTest(failure=failure):
                def respond(request):
                    if request.url.path == '/api/machines':
                        return httpx.Response(200, json={'value': [{'id': 'machine-one'}], '@odata.nextLink': '/second'})
                    if failure == 'transport':
                        raise httpx.ReadTimeout('offline', request=request)
                    return httpx.Response(503, json={'error': {'message': 'Service busy'}},
                                          headers={'Retry-After': '121'} if failure == 'budget' else {})
                http = self.raw_client(respond, 'https://api.security.microsoft.com')
                with patch('Core.get_defender_client.get_api_client', AsyncMock(return_value=http)):
                    result = await _collect_mde_machines()
                self.assertEqual(result['value'], [{'id': 'machine-one'}])
                self.assertEqual(result['availability_status'], 'partial')
                self.assertEqual(result['pages_collected'], 1)
                self.assertEqual(result['records_collected'], 1)
                self.assertTrue(result['truncated'])
                self.assertTrue(http.is_closed)

    async def test_usage_pages_retain_partial_metadata_after_transport_error(self):
        def respond(request):
            if request.url.path == '/first':
                return httpx.Response(200, json={'value': [{'id': 'one'}], '@odata.nextLink': '/second'})
            raise httpx.ReadTimeout('offline', request=request)
        async with self.raw_client(respond) as http:
            result, error = await _get_all_json(http, '/first')
        self.assertEqual(result['value'], [{'id': 'one'}])
        self.assertEqual(result['_collection_metadata']['availability_status'], 'partial')
        self.assertEqual(result['_collection_metadata']['pages_collected'], 1)
        self.assertTrue(result['_collection_metadata']['truncated'])
        self.assertIn('retry limit', error)

    async def test_usage_csv_reports_explain_over_budget_server_delay(self):
        async with self.raw_client(lambda request: httpx.Response(429, headers={'Retry-After': '121'})) as http:
            result, error = await _get_report_payload(http, '/report')
        self.assertIsNone(result)
        self.assertIn('retry wait budget', error)
        self.sleep.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()

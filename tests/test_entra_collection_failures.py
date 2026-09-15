"""Exercise Entra request shapes, paging and diagnostics with local HTTP responses."""

import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from Core.get_entra_client import (
    _fetch_graph_collection_via_http, _fetch_graph_object_via_http, get_entra_client,
)


class EntraCollectionFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_schedule_collection_uses_service_paging_and_risk_error_keeps_service_reason(self):
        schedule_calls = []
        def respond(request):
            path = request.url.path
            if path.endswith(('roleEligibilitySchedules', 'roleAssignmentSchedules')):
                schedule_calls.append(request.url)
                if '$top' in request.url.params:
                    return httpx.Response(400, json={'error': {'code': 'BadRequest', 'message': 'Unsupported query parameter'}})
                second = request.url.params.get('$skiptoken') == 'second'
                row = {'id': 'second' if second else 'first', 'roleDefinitionId': 'global-role',
                       'scheduleInfo': {'expiration': {'type': 'afterDateTime' if second else 'noExpiration'}}}
                data = {'value': [row]}
                if not second:
                    data['@odata.nextLink'] = 'https://graph.microsoft.com' + path + '?$skiptoken=second'
                return httpx.Response(200, json=data)
            if path.endswith('/roleDefinitions'):
                return httpx.Response(200, json={'value': [{'id': 'global-role', 'displayName': 'Global Administrator',
                                      'templateId': '62e90394-69f5-4237-9190-012177145e10'}]})
            if path.endswith('/riskyUsers'):
                return httpx.Response(403, json={'error': {'code': 'Forbidden', 'message': 'Request was blocked by the service.'}})
            return httpx.Response(200, json={'value': []})

        async def client():
            return httpx.AsyncClient(base_url='https://graph.microsoft.com', transport=httpx.MockTransport(respond))

        with patch('Core.get_entra_client._get_graph_http_client', side_effect=client), \
             patch('Core.get_entra_client._get_graph_token_roles', return_value={'IdentityRiskyUser.Read.All'}), \
             redirect_stdout(io.StringIO()):
            result = await get_entra_client(SimpleNamespace(), 'fictional')
        for source in ('role_eligibility_schedules', 'role_assignment_schedules'):
            self.assertTrue(result.collection_status[source]['available'])
            self.assertEqual(result.collection_status[source]['records_collected'], 2)
            self.assertEqual(result.collection_status[source]['pages_collected'], 2)
        self.assertEqual(len(schedule_calls), 4)
        self.assertEqual(result.pim_summary['eligible_assignments'], 2)
        self.assertEqual(result.pim_summary['permanent_global_admins'], 1)
        self.assertEqual(result.pim_summary['total_time_bound_assignments'], 1)
        reason = result.collection_status['risky_users']['reason']
        self.assertIn('does not establish the cause', reason)
        self.assertIn('Request was blocked by the service.', reason)

    async def test_failed_second_page_retains_partial_provenance_and_redacts_error(self):
        def respond(request):
            if '$skiptoken' not in request.url.params:
                return httpx.Response(200, json={'value': [{'id': 'one'}],
                    '@odata.nextLink': 'https://graph.microsoft.com/v1.0/test?$skiptoken=second'})
            return httpx.Response(400, json={'error': {'code': 'BadRequest',
                'message': 'Paging rejected. Bearer private-token password=private-password'}})
        http = httpx.AsyncClient(base_url='https://graph.microsoft.com', transport=httpx.MockTransport(respond))
        with patch('Core.get_entra_client._get_graph_http_client', AsyncMock(return_value=http)):
            result = await _fetch_graph_collection_via_http('/v1.0/test')
        self.assertFalse(result['available'])
        self.assertEqual(result['availability_status'], 'partial')
        self.assertEqual(result['status_code'], 400)
        self.assertEqual(result['records_collected'], 1)
        self.assertEqual(result['pages_collected'], 1)
        self.assertTrue(result['truncated'])
        self.assertIn('Paging rejected.', result['error'])
        self.assertNotIn('private-token', result['error'])
        self.assertNotIn('private-password', result['error'])
        self.assertTrue(http.is_closed)

    async def test_singleton_failure_preserves_graph_reason_and_ignores_non_json_body(self):
        for response in (httpx.Response(403, json={'error': {'code': 'Forbidden', 'message': 'Role missing'}}),
                         httpx.Response(503, text='<html>private diagnostic body</html>')):
            http = httpx.AsyncClient(base_url='https://graph.microsoft.com',
                                    transport=httpx.MockTransport(lambda request: response))
            with patch('Core.get_entra_client._get_graph_http_client', AsyncMock(return_value=http)):
                result = await _fetch_graph_object_via_http('/v1.0/test')
            self.assertFalse(result['available'])
            self.assertEqual(result['status_code'], response.status_code)
            self.assertNotIn('private diagnostic body', result['error'])
            if response.status_code == 403:
                self.assertIn('Role missing', result['error'])


if __name__ == '__main__':
    unittest.main()

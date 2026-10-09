"""Exercise the live Entra processing boundary with fictional, local Graph responses."""

import io
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from Core.get_graph_client import GraphRestClient
from Core.orchestrator_pipelines import create_pipelines
from Core.offline_collection import _encode, _decode
from Core.services_and_licenses import ServicesAndLicenses


class EntraPipelineIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.plan = 'AAD_PREMIUM'
        self.requests = []

        def respond(request):
            self.requests.append(request.url.path)
            if request.url.path.endswith('/organization'):
                values = [{'id': 'fictional-tenant', 'displayName': 'Fictional tenant',
                           'verifiedDomains': []}]
            elif request.url.path.endswith('/subscribedSkus'):
                values = [{'skuId': 'fictional-sku', 'skuPartNumber': 'SPE_E3',
                           'prepaidUnits': {'enabled': 5}, 'consumedUnits': 2,
                           'capabilityStatus': 'Enabled', 'appliesTo': 'User',
                           'servicePlans': [{'servicePlanName': self.plan,
                                             'provisioningStatus': 'Success'}]}]
            else:
                values = []
            return httpx.Response(200, json={'value': values})

        self.graph = object.__new__(GraphRestClient)
        self.graph.credential = SimpleNamespace(get_token=lambda *args: SimpleNamespace(token='fictional'))
        self.graph.scopes = ('https://graph.microsoft.com/.default',)
        self.graph.max_retries = 0
        self.graph._http = httpx.AsyncClient(base_url='https://graph.microsoft.com',
                                           transport=httpx.MockTransport(respond))
        self.source = {'available': True, 'availability_status': 'available', 'complete': True,
                       'truncated': False, 'collected_at': '2026-10-09T12:00:00Z',
                       'source_api': 'https://graph.microsoft.com/v1.0/auditLogs/signIns',
                       'scope': 'Returned user sign-ins',
                       'window_start': '2026-10-02T12:00:00Z',
                       'window_end': '2026-10-09T12:00:00Z'}
        self.collected = SimpleNamespace(
            available=True, signin_logs=[], assessment_datasets={},
            collection_status={'signin_logs': self.source},
            data_sources={'signin_logs': True}, signin_summary={'legacy_auth_attempts': 0})
        services = {f'run_{name}': name == 'entra' for name in
                    ('m365', 'entra', 'defender', 'purview', 'power_platform', 'copilot_studio')}
        self.pipeline = create_pipelines(self.graph, ServicesAndLicenses(), 'fictional-tenant', services)['entra']

    async def asyncTearDown(self):
        await self.graph.aclose()

    async def run_pipeline(self):
        with patch('Core.get_entra_client.get_entra_client', AsyncMock(return_value=self.collected)), \
             redirect_stdout(io.StringIO()):
            return await self.pipeline()

    async def test_real_graph_adapter_and_collected_evidence_are_not_interchanged(self):
        for plan in ('AAD_PREMIUM', 'AAD_PREMIUM_P1'):
            with self.subTest(plan=plan):
                self.plan = plan
                self.collected.signin_logs = [
                    {'id': 'success', 'userId': 'fictional-user', 'clientAppUsed': 'IMAP4',
                     'createdDateTime': '2026-10-08T11:22:33.1234567Z',
                     'status': {'errorCode': 0}, 'conditionalAccessStatus': 'notApplied'},
                    {'id': 'blocked', 'userId': 'fictional-user', 'clientAppUsed': 'IMAP4',
                     'createdDateTime': '2026-10-08T11:23:33.0000007Z',
                     'status': {'errorCode': 53003}, 'conditionalAccessStatus': 'failure'}]
                self.collected.signin_summary['legacy_auth_attempts'] = 2
                original = deepcopy(vars(self.collected))
                result = await self.run_pipeline()
                self.assertNotEqual(result.get('available'), False, result)
                self.assertIs(result['_client'], self.collected)
                row = next(row for row in result['recommendations']
                           if row.get('FindingKey') == 'entra.signins.legacy_auth')
                self.assertEqual(row['AuthenticationSummary']['counts']['Success'], 1)
                self.assertEqual(row['AuthenticationSummary']['counts']['BlockedByConditionalAccess'], 1)
                self.assertTrue(row['EvidenceComplete'])
                self.assertEqual(vars(self.collected), original)
                self.assertNotIn('/v1.0/auditLogs/signIns', self.requests)

    async def test_complete_empty_and_failed_or_partial_collection_remain_distinct(self):
        for state, complete in (('available', True), ('unavailable', False), ('partial', False)):
            with self.subTest(state=state):
                self.source.update(availability_status=state, available=state != 'unavailable',
                                   complete=complete, truncated=state == 'partial')
                result = await self.run_pipeline()
                self.assertNotEqual(result.get('available'), False, result)
                row = next(row for row in result['recommendations']
                           if row.get('FindingKey') == 'entra.signins.legacy_auth')
                self.assertEqual(row['AuthenticationSummary']['total'], 0)
                self.assertEqual(row['EvidenceComplete'], complete)
                self.assertEqual(row['Disposition'], 'Reference' if complete else 'Coverage')

    async def test_processing_failure_retains_collected_evidence_and_failure_status(self):
        self.collected.signin_logs = [{'id': 'retained-fictional-event'}]
        with patch('Core.get_entra_client.get_entra_client', AsyncMock(return_value=self.collected)), \
             patch('Core.get_entra_info.get_entra_info', AsyncMock(side_effect=ValueError('invalid evidence'))), \
             redirect_stdout(io.StringIO()):
            result = await self.pipeline()
        self.assertFalse(result['available'])
        self.assertEqual(result['availability_status'], 'unavailable')
        self.assertEqual(result['pipeline_error'], 'invalid evidence')
        self.assertIs(result['_client'], self.collected)
        self.assertEqual(result['_client'].collection_status['signin_logs'], self.source)
        self.assertEqual(result['recommendations'], [])
        replay = _decode(_encode(result))
        self.assertFalse(replay['available'])
        self.assertEqual(replay['pipeline_error'], 'invalid evidence')
        self.assertEqual(replay['_client'].signin_logs, self.collected.signin_logs)
        self.assertEqual(replay['_client'].collection_status, self.collected.collection_status)

    async def test_collection_failure_does_not_invent_a_collected_client(self):
        with patch('Core.get_entra_client.get_entra_client', AsyncMock(side_effect=ValueError('collection failed'))), \
             redirect_stdout(io.StringIO()):
            result = await self.pipeline()
        self.assertFalse(result['available'])
        self.assertEqual(result['availability_status'], 'unavailable')
        self.assertEqual(result['pipeline_error'], 'collection failed')
        self.assertNotIn('_client', result)


if __name__ == '__main__':
    unittest.main()

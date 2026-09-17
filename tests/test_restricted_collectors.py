"""Restricted runs retain useful evidence without turning intentional gaps into zeros."""

import contextlib
import importlib
import inspect
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from Core.get_entra_client import get_entra_client
from Core.get_m365_client import get_m365_client, extract_m365_insights_from_client
from Core.evidence_layer import _build_app_access_sheet, build_evidence_bundle
from Core.offline_collection import empty_service_results, load_collection, save_collection
from Recommendations.entra.entra_insights import extract_entra_insights_from_client
from Recommendations.m365.m365_insights import get_sites_observation


class RestrictedCollectorTests(unittest.IsolatedAsyncioTestCase):
    def licensed_graph(self, names):
        sku = SimpleNamespace(sku_part_number='SPE_E5', sku_id='synthetic-sku',
            prepaid_units=SimpleNamespace(enabled=1), consumed_units=1,
            service_plans=[SimpleNamespace(service_plan_name=name, provisioning_status='Success') for name in names])
        return SimpleNamespace(
            subscribed_skus=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(value=[sku]))),
            sites=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(value=[]))),
            groups=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(value=[]))),
            oauth2_permission_grants=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(value=[]))),
            users=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(value=[]))),
        )

    def assert_no_deployment_probes(self, graph):
        for field in ('sites', 'groups', 'oauth2_permission_grants', 'users'):
            getattr(graph, field).get.assert_not_called()

    async def m365(self, profile, site_status='available'):
        requests = []

        async def collection(path, **kwargs):
            requests.append(path)
            return {'value': [], 'available': site_status == 'available',
                    'availability_status': site_status if path == '/v1.0/sites' else 'available',
                    'truncated': site_status == 'partial' and path == '/v1.0/sites'}

        graph = SimpleNamespace(get_collection=AsyncMock(side_effect=collection),
                                get_csv=AsyncMock(return_value='Report Refresh Date\n2026-09-16\n'))
        with patch('Core.ai_usage.collect_ai_usage', AsyncMock(return_value={})), \
                patch('socket.create_connection', side_effect=AssertionError('No tenant access')), \
                contextlib.redirect_stdout(io.StringIO()):
            client = await get_m365_client(graph, permission_profile=profile)
        return client, requests

    async def entra(self, profile):
        requests = []

        async def collection(path, **kwargs):
            requests.append(path)
            rows = [{'id': 'synthetic', 'isMfaRegistered': True, 'isMfaCapable': True,
                     'methodsRegistered': ['fido2']}] if 'userRegistrationDetails' in path else []
            return {'value': rows, 'available': True, 'availability_status': 'available'}

        with patch('Core.get_entra_client._fetch_graph_collection_via_http', side_effect=collection), \
                patch('Core.get_entra_client._fetch_graph_object_via_http', AsyncMock(return_value={})), \
                patch('Core.get_entra_client._get_graph_token_roles', return_value=set()), \
                patch('socket.create_connection', side_effect=AssertionError('No tenant access')), \
                contextlib.redirect_stdout(io.StringIO()):
            client = await get_entra_client(SimpleNamespace(), 'synthetic', permission_profile=profile)
        return client, requests

    async def test_restricted_never_constructs_site_request_and_retains_usage(self):
        client, requests = await self.m365('restricted')
        self.assertNotIn('/v1.0/sites', requests)
        self.assertIn('/v1.0/users', requests)
        self.assertIn('/v1.0/external/connections', requests)
        self.assertEqual(client.collection_status['sites']['availability_status'], 'not_requested')
        self.assertFalse(client.collection_status['sites']['available'])
        self.assertNotIn('Sites.Read.All', client.missing_permissions)
        insights = extract_m365_insights_from_client(client)
        self.assertIsNone(insights['total_sites'])
        self.assertIn('not assessed', get_sites_observation(insights))
        self.assertTrue(insights['sharepoint_report_available'])

    async def test_standard_empty_sites_is_measured_but_partial_is_unknown(self):
        client, requests = await self.m365('standard')
        self.assertIn('/v1.0/sites', requests)
        self.assertEqual(extract_m365_insights_from_client(client)['total_sites'], 0)
        self.assertTrue(extract_m365_insights_from_client(client)['site_inventory_available'])
        partial, _ = await self.m365('standard', 'partial')
        self.assertIsNone(extract_m365_insights_from_client(partial)['total_sites'])
        self.assertFalse(extract_m365_insights_from_client(partial)['site_inventory_available'])

    async def test_restricted_skips_groups_and_grants_but_retains_identity_and_mfa(self):
        client, requests = await self.entra('restricted')
        for path in ('/v1.0/groups', '/v1.0/oauth2PermissionGrants'):
            self.assertNotIn(path, requests)
        for source in ('groups', 'oauth_grants'):
            self.assertEqual(client.collection_status[source]['availability_status'], 'not_requested')
            self.assertFalse(client.data_sources[source])
        self.assertTrue(client.data_sources['auth_methods'])
        self.assertTrue(client.data_sources['managed_devices'])
        self.assertTrue(client.data_sources['service_principals'])
        self.assertEqual(client.auth_summary['mfa_registered'], 1)
        self.assertIsNone(client.group_licensing_summary['total_groups_with_licenses'])
        self.assertIsNone(client.consent_summary['high_privilege_apps'])

    async def test_standard_still_reads_groups_and_grants(self):
        client, requests = await self.entra('standard')
        self.assertIn('/v1.0/groups', requests)
        self.assertIn('/v1.0/oauth2PermissionGrants', requests)
        self.assertTrue(client.data_sources['groups'])
        self.assertTrue(client.data_sources['oauth_grants'])
        self.assertEqual(client.group_licensing_summary['total_groups_with_licenses'], 0)

    async def test_restricted_licensed_pp_and_studio_do_not_bypass_collectors(self):
        from Core.get_power_platform_info import get_power_platform_info
        from Core.get_copilot_studio_info import get_copilot_studio_info
        graph = self.licensed_graph([
            'POWERAPPS_O365_P3', 'BI_AZURE_P2', 'FLOW_CCI_BOTS', 'DYN365_CDS_O365_P3',
            'COPILOT_STUDIO_IN_COPILOT_FOR_M365', 'POWER_VIRTUAL_AGENTS',
            'POWER_VIRTUAL_AGENTS_BASE', 'CCIBOTS_PRIVPREV_VIRAL', 'VIRTUAL_AGENT_USL',
            'CDS_VIRTUAL_AGENT_USL', 'FLOW_VIRTUAL_AGENT_USL',
        ])
        with patch('Core.get_power_platform_info.get_power_platform_client', AsyncMock()) as legacy, \
                patch('socket.create_connection', side_effect=AssertionError('No tenant access')), \
                contextlib.redirect_stdout(io.StringIO()):
            pp = await get_power_platform_info(graph, permission_profile='restricted')
            studio = await get_copilot_studio_info(graph, permission_profile='restricted')
        legacy.assert_not_called()
        self.assert_no_deployment_probes(graph)
        self.assertEqual(pp['availability_status'], 'not_requested')
        self.assertNotIn('Requires Power Platform admin', pp['reason'])
        for result in (pp, studio):
            self.assertTrue(result['licenses'])
            self.assertTrue(any(row['Status'] == 'Not Assessed' for row in result['recommendations']))
            observations = ' '.join(row['Observation'] for row in result['recommendations'])
            self.assertNotIn('No SharePoint sites', observations)
            self.assertNotIn('0 SharePoint sites', observations)
            self.assertNotIn('NoneType', observations)
            self.assertNotIn('Unable to check', observations)
            self.assertNotIn('could not be verified', observations)

    async def test_restricted_pp_export_inventory_remains_available(self):
        from Core.get_power_platform_info import get_power_platform_info
        from Core.power_platform_inventory import build_inventory_client
        inventory = build_inventory_client([
            {'type': 'Environment', 'id': 'exported-environment', 'displayName': 'Synthetic environment', 'environmentType': 'Production'},
            {'type': 'PowerApps', 'id': 'exported-app', 'displayName': 'Synthetic app'},
            {'type': 'Agent', 'id': 'exported-agent', 'displayName': 'Synthetic agent'},
        ], source='Synthetic customer export')
        graph = self.licensed_graph(['POWERAPPS_O365_P3'])
        with patch('Core.get_power_platform_info.get_power_platform_client', AsyncMock()) as legacy, \
                contextlib.redirect_stdout(io.StringIO()):
            result = await get_power_platform_info(graph, pp_client=inventory, permission_profile='restricted')
        legacy.assert_not_called()
        self.assert_no_deployment_probes(graph)
        self.assertTrue(result['available'])
        self.assertEqual(result['total_environments'], 1)
        self.assertEqual(result['environments'][0]['name'], 'Synthetic environment')
        self.assertTrue(inventory.power_platform_inventory['available'])
        self.assertEqual(len(inventory.apps), 1)
        self.assertEqual(len(inventory.agents), 1)
        ai_gap = next(row for row in result['recommendations'] if row['Feature'] == 'AI Builder - Assessment Needed')
        self.assertIn('Keep the Restricted profile', ai_gap['Recommendation'])
        self.assertNotIn('opt in', ai_gap['Recommendation'])
        dlp_gap = next(row for row in result['recommendations'] if row['Feature'] == 'DLP Governance - Verification Failed')
        self.assertEqual(dlp_gap['Status'], 'Not Assessed')

    async def test_restricted_project_license_does_not_read_groups(self):
        from Core.get_m365_info import get_m365_info
        graph = self.licensed_graph(['PROJECT_O365_P1'])
        with patch('socket.create_connection', side_effect=AssertionError('No tenant access')), \
                contextlib.redirect_stdout(io.StringIO()):
            licenses, rows = await get_m365_info(graph, m365_client=SimpleNamespace(
                available=False, permission_profile='restricted'))
        self.assert_no_deployment_probes(graph)
        self.assertTrue(licenses)
        self.assertTrue(any(row['Status'] == 'Not Assessed' for row in rows))
        self.assertFalse(any('0 users' in row['Observation'] for row in rows))

    async def test_site_dependent_modules_emit_coverage_instead_of_zero_site_findings(self):
        client, _ = await self.m365('restricted')
        insights = extract_m365_insights_from_client(client)
        for name in ('MICROSOFT_LOOP', 'PROJECT_O365_P3', 'M365_COPILOT_SHAREPOINT',
                     'GRAPH_CONNECTORS_SEARCH_INDEX', 'SHAREPOINTSTANDARD', 'GRAPH_CONNECTORS_COPILOT'):
            with self.subTest(module=name):
                module = importlib.import_module('Recommendations.m365.' + name)
                output = module.get_recommendation('SPE_E5', m365_insights=insights)
                rows = await output if inspect.isawaitable(output) else output
                self.assertTrue(any(row['Status'] == 'Not Assessed' for row in rows))
                self.assertFalse(any('0 SharePoint sites' in row['Observation'] or '0 sites' in row['Observation']
                                     or 'None sites' in row['Observation'] for row in rows))

    async def test_entra_recommendations_do_not_call_omissions_healthy_or_missing_permissions(self):
        client, _ = await self.entra('restricted')
        insights = extract_entra_insights_from_client(client)
        for name, marker in (('AAD_PREMIUM', 'Group-based licensing'),
                             ('AAD_PREMIUM_P2', 'Application grant inventory'),
                             ('AAD_GOVERNANCE', 'Application grant inventory')):
            with self.subTest(module=name), contextlib.redirect_stdout(io.StringIO()):
                module = importlib.import_module('Recommendations.entra.' + name)
                rows = module.get_recommendation('SPE_E5', entra_insights=insights)
                gap = next(row for row in rows if marker in row['Observation'])
                self.assertEqual(gap['Status'], 'Not Assessed')
                self.assertEqual(gap['Disposition'], 'Coverage')
                self.assertNotIn('Grant ', gap['Recommendation'])
                self.assertFalse(any('No high-risk applications detected' in row['Observation']
                                     or 'No group-based license assignments' in row['Observation'] for row in rows))

    async def test_saved_evidence_and_workbook_preserve_not_requested_without_zero_app_claims(self):
        from Core.customer_report import render_customer_report
        from Core.export_recommendations import export_to_excel
        from Core.new_recommendation import new_recommendation
        from openpyxl import load_workbook

        client, _ = await self.entra('restricted')
        sheet = _build_app_access_sheet(client, None)
        self.assertIn('not assessed', sheet['summary'])
        self.assertNotIn('0 applications', sheet['summary'])
        results = empty_service_results()
        results['entra_info'].update({'available': True, '_client': client})
        recommendation = new_recommendation(service='Entra', feature='Application grants',
            observation=sheet['summary'], recommendation='Review customer-provided grant evidence.',
            status='Not Assessed', disposition='Coverage', evidence_key='app_access_detail')
        bundle = build_evidence_bundle([recommendation], **results)
        bundle['collection_context'] = {'permission_profile': 'restricted'}
        bundle['source_statuses'] = {'entra_oauth_grants': client.collection_status['oauth_grants']}
        assessment = {'domains': [], 'actions': [], 'coverage': [recommendation],
                      'counts': {'evidence_gaps': 1}, 'decision': 'Readiness unconfirmed'}
        html = render_customer_report(assessment, bundle, 'Synthetic tenant')
        self.assertIn('Collection permission profile: restricted', html)
        self.assertIn('not_requested', html)
        self.assertNotIn('0 applications', html)
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            output = export_to_excel(bundle['recommendations'], filename=str(Path(directory) / 'evidence.xlsx'), evidence_bundle=bundle)
            book = load_workbook(output, read_only=True)
            try:
                values = str(list(book['App Access Detail'].values))
                self.assertIn('Not assessed', values)
                self.assertIn('not_requested', values)
                self.assertNotIn('0 applications', values)
            finally:
                book.close()
            collection = save_collection(Path(directory) / 'collection.json', tenant_id='synthetic',
                tenant_name='Synthetic tenant', service_results=results)
            saved = load_collection(collection)['service_results']['entra_info']['_client']
            self.assertEqual(saved.permission_profile, 'restricted')
            self.assertEqual(saved.collection_status['oauth_grants'], client.collection_status['oauth_grants'])
            self.assertEqual(_build_app_access_sheet(saved, None)['summary'], sheet['summary'])


if __name__ == '__main__':
    unittest.main()

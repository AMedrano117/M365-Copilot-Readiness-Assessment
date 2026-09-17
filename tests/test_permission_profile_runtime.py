"""Restricted access checks use only fabricated grants and local evidence."""

import base64
import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import NAMESPACE_URL, uuid5

from Core.cli_parser import parse_arguments, resolve_live_permission_profile
from Core.collector_registry import RESOURCE_APP_IDS, profile_permission_resources
from Core.connection_validation import PERMISSION, READY, NOT_SELECTED, run_connection_checks, connection_exit_code
from Core.credentials_check import load_env_file
from Core.offline_collection import collection_context, empty_service_results, load_collection, save_collection
from Core.orchestrator_setup import prepare_interactive_collection_plan
from Core.permission_audit import audit_restricted_access


APP_ID = '11111111-1111-1111-1111-111111111111'
PRINCIPAL_ID = '22222222-2222-2222-2222-222222222222'
TENANT_ID = '33333333-3333-3333-3333-333333333333'
SERVICES = dict(run_m365=True, run_entra=True, run_defender=True, run_purview=True,
                run_power_platform=False, run_copilot_studio=False)


def token(roles):
    payload = base64.urlsafe_b64encode(json.dumps({'roles': sorted(roles)}).encode()).decode().rstrip('=')
    return SimpleNamespace(token=f'header.{payload}.signature')


class AuditClient:
    def __init__(self):
        allowed = profile_permission_resources('restricted')
        self.resources = {}
        for app_id, names in allowed.items():
            all_names = names | ({'Sites.Read.All', 'Directory.Read.All'} if app_id == RESOURCE_APP_IDS['graph'] else set())
            self.resources[app_id] = {
                'appId': app_id, 'id': str(uuid5(NAMESPACE_URL, app_id)), 'displayName': app_id,
                'appRoles': [{'id': str(uuid5(NAMESPACE_URL, name)), 'value': name} for name in sorted(all_names)],
            }
        self.resources[APP_ID] = {'appId': APP_ID, 'id': PRINCIPAL_ID, 'appRoles': []}
        self.manifest = []
        self.assignments = []
        for app_id, names in allowed.items():
            roles = [role for role in self.resources[app_id]['appRoles'] if role['value'] in names]
            self.manifest.append({'resourceAppId': app_id,
                                  'resourceAccess': [{'id': role['id'], 'type': 'Role'} for role in roles]})
            self.assignments.extend({'resourceId': self.resources[app_id]['id'], 'appRoleId': role['id']} for role in roles)
        self.graph_roles = allowed[RESOURCE_APP_IDS['graph']]
        self.credential = SimpleNamespace(get_token=lambda scope: token(
            self.graph_roles if 'graph.microsoft.com' in scope else {'Machine.Read.All'}))
        self.calls = []
        self.incomplete = False

    async def get_collection(self, path, params=None):
        self.calls.append((path, params))
        if path == '/v1.0/applications':
            rows = [{'appId': APP_ID, 'requiredResourceAccess': self.manifest}]
        elif path == '/v1.0/servicePrincipals':
            app_id = params['$filter'].split("'")[1]
            rows = [self.resources[app_id]]
        elif path == f'/v1.0/servicePrincipals/{PRINCIPAL_ID}/appRoleAssignments':
            rows = self.assignments
        else:
            raise AssertionError(f'Unexpected endpoint: {path}')
        return {'value': copy.deepcopy(rows), 'available': True,
                'availability_status': 'partial' if self.incomplete else 'available',
                'truncated': self.incomplete}

    async def get_json(self, path, params=None):
        self.calls.append((path, params))
        return copy.deepcopy(next(resource for resource in self.resources.values()
                                  if path == f"/v1.0/servicePrincipals/{resource['id']}"))

    def add_consent(self, name):
        resource = self.resources[RESOURCE_APP_IDS['graph']]
        role = next(role for role in resource['appRoles'] if role['value'] == name)
        self.assignments.append({'resourceId': resource['id'], 'appRoleId': role['id']})


class ProfileSelectionTests(unittest.TestCase):
    def test_cli_then_selected_environment_then_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_live_permission_profile(), 'standard')
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / '.env.restricted'
                path.write_text('PERMISSION_PROFILE=restricted\n', encoding='utf-8')
                load_env_file(str(path))
                self.assertEqual(resolve_live_permission_profile(), 'restricted')
                self.assertEqual(resolve_live_permission_profile('standard'), 'standard')

    def test_invalid_or_conflicting_profile_fails(self):
        for profile, preview, legacy in [('typo', 'none', False), ('restricted', 'all', False), ('restricted', 'none', True)]:
            with self.subTest(profile=profile, preview=preview, legacy=legacy), self.assertRaises(ValueError):
                resolve_live_permission_profile(profile, preview, legacy)
        with patch.dict(os.environ, {'PERMISSION_PROFILE': 'restricted'}), self.assertRaises(ValueError):
            resolve_live_permission_profile(None, 'shadow-ai')

    def test_offline_cannot_relabel_collection_profile(self):
        with patch('sys.argv', ['main.py', '--mode', 'offline', '--permission-profile', 'standard']), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parse_arguments(None, [])

    def test_restricted_plan_does_not_inspect_modules_or_use_certificates(self):
        with patch.dict(os.environ, {'CERTIFICATE_PATH': 'existing.pfx',
                                    'SHAREPOINT_CERTIFICATE_THUMBPRINT': 'existing',
                                    'PURVIEW_CERTIFICATE_THUMBPRINT': 'existing'}), \
             patch('Core.orchestrator_setup.get_local_powershell_module_availability') as modules:
            plan = prepare_interactive_collection_plan(SERVICES, interactive_auth='fresh', permission_profile='restricted')
        modules.assert_not_called()
        for name in ('sharepoint', 'purview', 'power_platform'):
            self.assertFalse(plan[name]['will_attempt'])
            self.assertFalse(plan[name]['application_auth'])
        self.assertEqual(plan['modules'], {})

    def test_package_preserves_profile_and_legacy_is_unrecorded(self):
        results = empty_service_results()
        results['purview_info'].update(availability_status='not_requested', reason='Restricted permission profile')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'collection.json'
            save_collection(path, tenant_id=TENANT_ID, tenant_name='Synthetic', service_results=results,
                            assessment_settings={'permission_profile': 'restricted'})
            saved = load_collection(path)
            portable = load_collection(Path(saved['package_directory']) / 'collection.json')
            with patch.dict(os.environ, {'PERMISSION_PROFILE': 'standard'}):
                self.assertEqual(collection_context(portable)['permission_profile'], 'restricted')
            self.assertEqual(portable['service_results']['purview_info']['availability_status'], 'not_requested')
            portable['assessment_settings'].pop('permission_profile')
            self.assertEqual(collection_context(portable)['permission_profile'], 'unrecorded')


class RestrictedAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_matching_manifest_and_actual_grants_pass(self):
        client = AuditClient()
        result = await audit_restricted_access(client, APP_ID, client.graph_roles)
        self.assertTrue(result['verified'], result)

    async def test_removed_manifest_permission_does_not_hide_old_consent(self):
        client = AuditClient()
        client.add_consent('Sites.Read.All')
        original = copy.deepcopy(client.assignments)
        result = await audit_restricted_access(client, APP_ID, client.graph_roles)
        self.assertFalse(result['verified'])
        self.assertIn('Consented:', result['reason'])
        self.assertIn('Sites.Read.All', result['reason'])
        self.assertEqual(original, client.assignments)

    async def test_requested_excess_is_detected_before_consent(self):
        client = AuditClient()
        client.manifest[0]['resourceAccess'].append({'id': str(uuid5(NAMESPACE_URL, 'Sites.Read.All')), 'type': 'Role'})
        result = await audit_restricted_access(client, APP_ID)
        self.assertFalse(result['verified'])
        self.assertIn('Requested:', result['reason'])

    async def test_unknown_api_and_role_are_excess(self):
        client = AuditClient()
        unknown = '44444444-4444-4444-4444-444444444444'
        client.resources[unknown] = {'appId': unknown, 'id': unknown, 'appRoles': [], 'displayName': 'Unexpected API'}
        client.assignments.append({'resourceId': unknown, 'appRoleId': unknown})
        result = await audit_restricted_access(client, APP_ID)
        self.assertFalse(result['verified'])
        self.assertIn('Unexpected API', result['reason'])

    async def test_stale_token_broad_roles_are_excess(self):
        client = AuditClient()
        result = await audit_restricted_access(client, APP_ID, client.graph_roles | {'Directory.Read.All'})
        self.assertFalse(result['verified'])
        self.assertIn('Current Graph token: Directory.Read.All', result['reason'])

    async def test_incomplete_permission_inventory_fails_closed(self):
        client = AuditClient()
        client.incomplete = True
        result = await audit_restricted_access(client, APP_ID)
        self.assertFalse(result['verified'])
        self.assertIn('could not be verified', result['reason'])

    async def test_preflight_uses_restricted_requirements_and_skips_admin_checks(self):
        client = AuditClient()
        plan = {'modules': {}, 'sharepoint': {'application_auth': True}, 'purview': {'application_auth': True}}
        with patch.dict(os.environ, {'CLIENT_ID': APP_ID}), \
             patch('Core.connection_validation._powershell_certificate_probe', side_effect=AssertionError('No PowerShell')):
            results = await run_connection_checks(client, SERVICES, plan, probe_endpoints=False,
                                                 permission_profile='restricted')
        by_name = {row['collector_id']: row for row in results}
        self.assertEqual(connection_exit_code(results), 0)
        self.assertEqual(by_name['entra_controls']['status'], READY)
        self.assertEqual(by_name['graph_security']['status'], READY)
        for name in ('sharepoint_governance', 'purview'):
            self.assertEqual(by_name[name]['status'], NOT_SELECTED)
            self.assertIn('Restricted', by_name[name]['reason'])

    async def test_preflight_blocks_excess_consent(self):
        client = AuditClient()
        client.add_consent('Sites.Read.All')
        with patch.dict(os.environ, {'CLIENT_ID': APP_ID}):
            results = await run_connection_checks(client, SERVICES, {}, permission_profile='restricted')
        self.assertEqual(connection_exit_code(results), 2)
        self.assertTrue(results[0]['access_audit_failed'])

    async def test_alert_permission_is_required_without_authentication_method_permission(self):
        client = AuditClient()
        client.graph_roles = client.graph_roles - {'SecurityAlert.Read.All'}
        with patch.dict(os.environ, {'CLIENT_ID': APP_ID}):
            results = await run_connection_checks(client, SERVICES, {}, probe_endpoints=False,
                                                 permission_profile='restricted')
        security = next(row for row in results if row['collector_id'] == 'graph_security')
        self.assertEqual(security['status'], PERMISSION)
        self.assertIn('SecurityAlert.Read.All', security['reason'])
        self.assertNotIn('UserAuthenticationMethod', str(results))


class RestrictedOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        previous = Path.cwd()
        os.chdir(self.directory)
        self.stack.callback(os.chdir, previous)
        self.stack.enter_context(patch.dict(os.environ, {'CLIENT_ID': APP_ID, 'CERTIFICATE_PATH': 'old.pfx',
                                                       'PURVIEW_DATA_SOURCE': 'stdin'}, clear=True))
        self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.client = AuditClient()
        self.setup = self.stack.enter_context(patch('Core.orchestrator.setup_graph_and_licenses',
                                                   AsyncMock(return_value=(self.client, SimpleNamespace(), True))))
        self.stack.enter_context(patch('Core.orchestrator.load_modules_and_analyze', AsyncMock()))
        self.stack.enter_context(patch('Core.orchestrator.resolve_sharepoint_admin_url',
                                      side_effect=AssertionError('Restricted must not resolve or prompt for admin URL')))
        self.stack.enter_context(patch('Core.orchestrator_setup.get_local_powershell_module_availability',
                                      side_effect=AssertionError('Restricted must not probe or install modules')))
        self.stack.enter_context(patch('Core.orchestrator.collect_sharepoint_with_retry',
                                      side_effect=AssertionError('Restricted must not launch SharePoint')))
        self.render = self.stack.enter_context(patch('Core.orchestrator.process_and_print_all_information', return_value={}))
        results = empty_service_results()
        results['purview_info'].update(availability_status='not_requested', reason='Restricted permission profile')
        pipelines = {name: AsyncMock(return_value=results[key]) for name, key in (
            ('m365', 'm365_result'), ('entra', 'entra_info'), ('defender', 'defender_info'),
            ('purview', 'purview_info'), ('power_platform', 'power_platform_info'), ('copilot_studio', 'copilot_studio_info'))}
        self.pipelines = self.stack.enter_context(patch('Core.orchestrator.create_pipelines', return_value=pipelines))
        for target in ('socket.socket.connect', 'socket.create_connection', 'subprocess.Popen', 'builtins.input'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('Unexpected live operation')))

    async def test_live_profile_saves_without_admin_work_and_replay_is_stable(self):
        from Core.orchestrator import orchestrate
        result = await orchestrate(TENANT_ID, ['M365', 'Entra', 'Defender', 'Purview'], permission_profile='restricted')
        self.assertIsNone(result)
        self.assertEqual(self.pipelines.call_args.kwargs['permission_profile'], 'restricted')
        saved_path = next(Path('output/collections').glob('*.json'))
        saved = load_collection(saved_path)
        self.assertEqual(saved['assessment_settings']['permission_profile'], 'restricted')
        self.assertNotIn('Purview', saved['enabled_collectors'])
        context = self.render.call_args.kwargs['collection_context']
        self.assertEqual(context['permission_profile'], 'restricted')
        self.assertEqual(collection_context(saved)['permission_profile'], 'restricted')

    async def test_live_stops_on_excess_before_pipelines_or_saving(self):
        from Core.orchestrator import orchestrate
        self.client.add_consent('Sites.Read.All')
        result = await orchestrate(TENANT_ID, ['M365'], permission_profile='restricted')
        self.assertEqual(result, 2)
        self.assertFalse(self.setup.call_args.kwargs['collect_licenses'])
        self.pipelines.assert_not_called()
        self.render.assert_not_called()
        self.assertFalse(Path('output/collections').exists())

    async def test_excess_blocks_license_and_tenant_context_reads(self):
        from Core.orchestrator import orchestrate
        from Core.orchestrator_setup import setup_graph_and_licenses
        self.client.add_consent('Sites.Read.All')
        self.client.subscribed_skus = SimpleNamespace(get=AsyncMock(side_effect=AssertionError('No license read before audit')))
        self.setup.side_effect = setup_graph_and_licenses
        with patch('Core.orchestrator_setup.get_graph_client', AsyncMock(return_value=self.client)), \
             patch('Core.orchestrator.resolve_tenant_name', side_effect=AssertionError('No tenant context before audit')):
            result = await orchestrate(TENANT_ID, ['M365'], permission_profile='restricted')
        self.assertEqual(result, 2)
        self.client.subscribed_skus.get.assert_not_called()

    async def test_preflight_skips_admin_and_license_collection_and_creates_no_reports(self):
        from Core.orchestrator import orchestrate
        self.client.request = AsyncMock(return_value=None)
        result = await orchestrate(TENANT_ID, ['Purview'], permission_profile='restricted', check_connections=True)
        self.assertEqual(result, 0)
        self.assertFalse(self.setup.call_args.kwargs['collect_licenses'])
        self.pipelines.assert_not_called()
        self.render.assert_not_called()
        self.assertFalse(Path('output').exists())

    async def test_conflicting_options_rejected_before_authentication(self):
        from Core.orchestrator import orchestrate
        result = await orchestrate(TENANT_ID, ['M365'], permission_profile='restricted', preview_collectors='all')
        self.assertEqual(result, 1)
        self.setup.assert_not_called()

    async def test_purview_pipeline_does_not_reuse_ambient_stdin_or_launch_powershell(self):
        from Core.orchestrator_pipelines import create_pipelines
        with patch('Core.orchestrator_pipelines.collect_purview_data_via_powershell',
                   side_effect=AssertionError('Restricted must not launch Purview')):
            pipelines = create_pipelines(self.client, SimpleNamespace(), TENANT_ID, SERVICES,
                                         permission_profile='restricted',
                                         interactive_plan={'purview': {'will_attempt': True}})
            result = await pipelines['purview']()
        self.assertFalse(result['available'])
        self.assertEqual(result['availability_status'], 'not_requested')

    async def test_invalid_inventory_does_not_authorize_legacy_fetch(self):
        from Core.orchestrator_pipelines import create_pipelines
        services = dict(SERVICES, run_power_platform=True)
        inventory = Path(self.directory) / 'invalid.csv'
        inventory.write_text('unsupported,columns\nexample,example\n', encoding='utf-8')
        with patch('Core.get_power_platform_info.get_power_platform_info', AsyncMock(return_value={})) as information, \
             patch('Core.get_power_platform_client.get_power_platform_client',
                   side_effect=AssertionError('Export must not authorize administrative APIs')):
            pipelines = create_pipelines(self.client, SimpleNamespace(), TENANT_ID, services,
                                         permission_profile='restricted', power_platform_inventory=str(inventory),
                                         interactive_plan=prepare_interactive_collection_plan(services, permission_profile='restricted'))
            await pipelines['power_platform']()
        self.assertFalse(information.call_args.kwargs['allow_enrichment_fetch'])


if __name__ == '__main__':
    unittest.main()

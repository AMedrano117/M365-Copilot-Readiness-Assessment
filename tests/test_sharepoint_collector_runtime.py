"""SharePoint configuration and collector transport regressions; no tenant calls."""

import asyncio
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from Core.credentials_check import load_env_file
from Core.console_reporting import configure_console
from Core.orchestrator import (
    collect_sharepoint_with_retry, resolve_sharepoint_admin_url,
    resolve_purview_organization, resolve_workload_configuration,
)
from Core.connection_validation import CONFIGURATION, connection_exit_code, run_connection_checks
from Core.sharepoint_configuration import is_valid_sharepoint_admin_url
from Core.sharepoint_governance import build_sharepoint_recommendations
from Core.orchestrator_powershell import (
    SHAREPOINT_JSON_BEGIN, SHAREPOINT_JSON_END, _parse_sharepoint_payload,
    collect_sharepoint_governance_via_powershell,
)


def sample_payload(partial=False):
    return {
        'source': 'SharePoint Online Management Shell',
        'tenant': {'available': True, 'settings': {'SharingCapability': 'Disabled'}},
        'sites': {'available': not partial, 'items': []},
        'dag_reports': {'available': True, 'reports': []},
        'collection_status': {
            'sharepoint_tenant_settings': {'available': True, 'availability_status': 'available'},
            'sharepoint_site_settings': {'available': not partial, 'availability_status': 'unavailable' if partial else 'available', 'reason': 'Access denied' if partial else ''},
            'sharepoint_dag_reports': {'available': True, 'availability_status': 'available'},
        },
    }


def framed(payload):
    return f'\ufeffModule banner\n{SHAREPOINT_JSON_BEGIN}\n{json.dumps(payload)}\n{SHAREPOINT_JSON_END}\nModule warning\n'


class EnvAdminUrlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        configure_console(verbose=True, color='never')

    def tearDown(self):
        configure_console(verbose=False, color='auto')

    async def test_env_url_trailing_slash_bom_quotes_and_provenance(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True):
            path = Path(temporary) / '.env'
            path.write_text('\ufeffSHAREPOINT_ADMIN_URL="https://configured-admin.sharepoint.com/" # "verified" URL\nTEST_LITERAL=\'a$literal#part\\path\'\n', encoding='utf-8')
            load_env_file(path)
            output = io.StringIO()
            with redirect_stdout(output):
                result = await resolve_sharepoint_admin_url(SimpleNamespace(), interactive_auth='skip')
            self.assertEqual('https://configured-admin.sharepoint.com', result)
            self.assertIn(' '.join(str(path).split()), ' '.join(output.getvalue().split()))
            self.assertEqual('a$literal#part\\path', os.environ['TEST_LITERAL'])

    async def test_invalid_config_does_not_silently_use_initial_domain(self):
        domain = SimpleNamespace(name='initial.onmicrosoft.com', is_initial=True)
        organization = SimpleNamespace(value=[SimpleNamespace(verified_domains=[domain])])
        client = SimpleNamespace(organization=SimpleNamespace(get=AsyncMock(return_value=organization)))
        with patch.dict(os.environ, {'SHAREPOINT_ADMIN_URL': 'https://wrong.sharepoint.com', 'PURVIEW_ORGANIZATION': ''}, clear=True), redirect_stdout(io.StringIO()):
            self.assertEqual('', await resolve_sharepoint_admin_url(client, interactive_auth='skip'))
            self.assertEqual('', os.environ['PURVIEW_ORGANIZATION'])
        client.organization.get.assert_not_awaited()

    async def test_missing_url_never_uses_directory_domains_or_changes_purview(self):
        client = SimpleNamespace(organization=SimpleNamespace(get=AsyncMock()))
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(output):
            self.assertEqual('', await resolve_sharepoint_admin_url(client, interactive_auth='skip'))
            self.assertNotIn('PURVIEW_ORGANIZATION', os.environ)
        client.organization.get.assert_not_awaited()
        self.assertIn('SHAREPOINT_ADMIN_URL', output.getvalue())
        self.assertIn('not assessed', output.getvalue())

    async def test_operator_entry_is_used_only_when_interaction_is_allowed(self):
        for mode, interactive, expected in (
            ('auto', True, 'https://renamed-admin.sharepoint.com'),
            ('skip', True, ''), ('auto', False, ''),
        ):
            with self.subTest(mode=mode, interactive=interactive), \
                 patch.dict(os.environ, {}, clear=True), \
                 patch('Core.orchestrator.sys.stdin.isatty', return_value=interactive), \
                 redirect_stdout(io.StringIO()):
                prompt = Mock(return_value='https://renamed-admin.sharepoint.com/')
                self.assertEqual(expected, await resolve_sharepoint_admin_url(None, interactive_auth=mode, prompt=prompt))
                self.assertEqual(bool(expected), prompt.called)

    async def test_invalid_cli_does_not_fall_back_or_prompt(self):
        prompt = Mock(side_effect=AssertionError('Do not replace invalid explicit configuration'))
        with patch.dict(os.environ, {'SHAREPOINT_ADMIN_URL': 'https://env-admin.sharepoint.com'}, clear=True), \
             patch('Core.orchestrator.sys.stdin.isatty', return_value=True), redirect_stdout(io.StringIO()):
            self.assertEqual('', await resolve_sharepoint_admin_url(None, explicit_url='https://wrong.sharepoint.com', prompt=prompt))
        prompt.assert_not_called()

    async def test_purview_organization_resolution_is_independent(self):
        domain = SimpleNamespace(name='original.onmicrosoft.com', is_initial=True)
        client = SimpleNamespace(organization=SimpleNamespace(get=AsyncMock(
            return_value=SimpleNamespace(value=[SimpleNamespace(verified_domains=[domain])]))))
        with patch.dict(os.environ, {'SHAREPOINT_ADMIN_URL': 'https://renamed-admin.sharepoint.com'}, clear=True):
            self.assertEqual('original.onmicrosoft.com', await resolve_purview_organization(client))
            self.assertEqual('https://renamed-admin.sharepoint.com', os.environ['SHAREPOINT_ADMIN_URL'])
            self.assertEqual('original.onmicrosoft.com', await resolve_purview_organization(client))
        client.organization.get.assert_awaited_once()

    async def test_unselected_or_unavailable_sharepoint_never_prompts(self):
        for profile, selected, attempt in (
            ('standard', False, False), ('standard', True, False), ('restricted', True, True),
        ):
            with self.subTest(profile=profile, selected=selected, attempt=attempt), \
                 patch('Core.orchestrator.resolve_sharepoint_admin_url', AsyncMock()) as sharepoint, \
                 patch('Core.orchestrator.resolve_purview_organization', AsyncMock()) as purview:
                plan = {'sharepoint': {'selected': selected, 'will_attempt': attempt}}
                self.assertEqual('', await resolve_workload_configuration(None, plan, permission_profile=profile))
                sharepoint.assert_not_awaited()
                purview.assert_not_awaited()

    async def test_purview_certificate_only_does_not_resolve_sharepoint(self):
        plan = {'purview': {'selected': True, 'will_attempt': True, 'application_auth': True}}
        with patch('Core.orchestrator.resolve_sharepoint_admin_url', AsyncMock()) as sharepoint, \
             patch('Core.orchestrator.resolve_purview_organization', AsyncMock()) as purview:
            self.assertEqual('', await resolve_workload_configuration(None, plan))
        sharepoint.assert_not_awaited()
        purview.assert_awaited_once()

    async def test_missing_or_invalid_url_never_launches_admin_collection_or_retry(self):
        for value in ('', 'https://ordinary.sharepoint.com', 'https://-admin.sharepoint.com'):
            with self.subTest(value=value), \
                 patch.dict(os.environ, {'CERTIFICATE_PATH': 'existing.pfx'}, clear=True), \
                 patch('Core.orchestrator_powershell._launch_powershell', side_effect=AssertionError('No PowerShell')) as launch, \
                 patch('Core.orchestrator_powershell.tempfile.mkdtemp', side_effect=AssertionError('No download directory')), \
                 patch('Core.orchestrator.sys.stdin.isatty', return_value=True), redirect_stdout(io.StringIO()):
                prompt = Mock(side_effect=AssertionError('No second configuration prompt'))
                retry = await collect_sharepoint_with_retry(value, 'synthetic', prompt=prompt)
                direct = await collect_sharepoint_governance_via_powershell(value, 'synthetic')
                for result in (retry, direct):
                    self.assertFalse(result['available'])
                    self.assertEqual('SHAREPOINT_ADMIN_URL', result['configuration_required'])
                    recommendation = build_sharepoint_recommendations(result)[0]
                    self.assertEqual('Not Assessed', recommendation['Status'])
                    self.assertIn('SHAREPOINT_ADMIN_URL', recommendation['Recommendation'])
                    self.assertNotIn('Install', recommendation['Recommendation'])
                launch.assert_not_called()
                prompt.assert_not_called()

    async def test_preflight_reports_configuration_gap_without_certificate_probe(self):
        client = SimpleNamespace(credential=SimpleNamespace(get_token=lambda scope: SimpleNamespace(token='synthetic')))
        plan = {'modules': {'Microsoft.Online.SharePoint.PowerShell': True},
                'sharepoint': {'selected': True, 'will_attempt': True, 'application_auth': True}}
        for value in ('', 'https://ordinary.sharepoint.com'):
            with self.subTest(value=value), \
                 patch('Core.connection_validation._powershell_certificate_probe', side_effect=AssertionError('No PowerShell')):
                results = await run_connection_checks(client, {'run_m365': True}, plan,
                                                      sharepoint_admin_url=value, interactive_auth='skip')
                sharepoint = next(row for row in results if row['collector_id'] == 'sharepoint_governance')
                self.assertEqual(CONFIGURATION, sharepoint['status'])
                self.assertEqual(2, connection_exit_code([sharepoint]))
                self.assertIn('SHAREPOINT_ADMIN_URL', sharepoint['reason'])

    def test_admin_url_rejects_non_origin_and_lookalike_hosts(self):
        for value in ('https://-admin.sharepoint.com', 'https://nested.tenant-admin.sharepoint.com',
                      'https://tenant-admin.sharepoint.com/_layouts/15/online/AdminHome.aspx',
                      'https://tenant-admin.sharepoint.com?tenant=other',
                      'https://user@tenant-admin.sharepoint.com', 'https://tenant-admin.sharepoint.com:443'):
            with self.subTest(value=value):
                self.assertFalse(is_valid_sharepoint_admin_url(value))


class SharePointJsonTests(unittest.TestCase):
    def test_framed_banner_and_bom_do_not_hide_valid_evidence(self):
        parsed = _parse_sharepoint_payload(framed(sample_payload()))
        self.assertTrue(parsed['available'])
        self.assertEqual('available', parsed['availability_status'])

    def test_older_unframed_document_after_banner_is_supported(self):
        parsed = _parse_sharepoint_payload('Module notice\n' + json.dumps(sample_payload()))
        self.assertTrue(parsed['available'])

    def test_partial_results_do_not_become_clean_success(self):
        parsed = _parse_sharepoint_payload(framed(sample_payload(partial=True)))
        self.assertTrue(parsed['available'])
        self.assertEqual('partial', parsed['availability_status'])
        self.assertFalse(parsed['sites']['available'])

    def test_all_failed_sources_remain_unavailable(self):
        payload = sample_payload()
        for name in ('tenant', 'sites', 'dag_reports'):
            payload[name]['available'] = False
        for state in payload['collection_status'].values():
            state.update(available=False, availability_status='unavailable')
        parsed = _parse_sharepoint_payload(framed(payload))
        self.assertFalse(parsed['available'])
        self.assertEqual('unavailable', parsed['availability_status'])

    def test_empty_broken_duplicate_and_wrong_shape_json_are_rejected(self):
        invalid = [
            '', '{}', '[]', '{broken outer:\n' + json.dumps(sample_payload()),
            SHAREPOINT_JSON_BEGIN + '\n' + json.dumps(sample_payload()),
            framed(sample_payload()) + framed(sample_payload()),
            json.dumps(sample_payload()) + '\n{}',
            SHAREPOINT_JSON_BEGIN + '\n' + json.dumps(sample_payload()) + ' garbage\n' + SHAREPOINT_JSON_END,
        ]
        for value in invalid:
            with self.subTest(value=value[:30]), self.assertRaises(ValueError):
                _parse_sharepoint_payload(value)

    def test_non_object_site_and_report_records_are_rejected(self):
        for section, field in (('sites', 'items'), ('dag_reports', 'reports')):
            payload = sample_payload()
            payload[section][field] = ['not an object']
            with self.subTest(section=section), self.assertRaisesRegex(ValueError, 'non-object record'):
                _parse_sharepoint_payload(framed(payload))


class SharePointCollectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        configure_console(verbose=False, color='never')

    def tearDown(self):
        configure_console(verbose=False, color='auto')

    async def run_collector(self, stdout):
        process = Mock(returncode=0)
        process.communicate.return_value = (stdout, 'AUTH_COMPLETE:SharePoint\n')
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary, patch('Core.orchestrator_powershell._launch_powershell', return_value=process), patch('Core.orchestrator_powershell.tempfile.mkdtemp', return_value=temporary), patch('Core.orchestrator_powershell._record_collector_diagnostics') as log, redirect_stdout(output):
            result = await collect_sharepoint_governance_via_powershell('https://test-admin.sharepoint.com', 'test')
        return result, output.getvalue(), log

    async def test_partial_collection_prints_failed_dataset(self):
        result, output, log = await self.run_collector(framed(sample_payload(partial=True)))
        self.assertEqual('partial', result['availability_status'])
        self.assertIn('2/3 datasets complete; collection gaps remain', output)
        self.assertIn('sharepoint_site_settings: Access denied', output)
        log.assert_called_once()

    async def test_failed_sign_in_shows_http_error_instead_of_wrapped_error_identifier(self):
        process = Mock(returncode=1)
        process.communicate.return_value = ('', '\n'.join([
            'AUTH_PROMPT:SharePoint',
            '\x1b[31mConnect-SPOService : The remote server returned an error: (401) Unauthorized.\x1b[0m',
            'At C:\\assessment\\collector.ps1:106 char:5',
            '+ FullyQualifiedErrorId : System.Net.WebException,Microsoft.Online.SharePoint.PowerShell.Co',
            'nnectSPOService',
        ]))
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary, \
             patch('Core.orchestrator_powershell._launch_powershell', return_value=process), \
             patch('Core.orchestrator_powershell.tempfile.mkdtemp', return_value=temporary), \
             patch('Core.orchestrator_powershell._record_collector_diagnostics'), redirect_stdout(output):
            result = await collect_sharepoint_governance_via_powershell('https://test-admin.sharepoint.com', 'test')
        self.assertFalse(result['available'])
        self.assertIn('(401) Unauthorized', result['reason'])
        self.assertIn('(401) Unauthorized', output.getvalue())
        self.assertNotIn('\x1b', result['reason'])
        self.assertNotIn('unavailable: nnectSPOService', output.getvalue())

    async def test_default_keeps_browser_prompt_and_collection_outcome(self):
        with patch.dict(os.environ, {}, clear=True):
            result, output, _ = await self.run_collector(framed(sample_payload()))
        self.assertTrue(result['available'])
        self.assertIn('complete the browser prompt', output)
        self.assertNotIn('sign-in accepted', output)
        self.assertIn('3/3 datasets complete', output)

    async def test_verbose_adds_collector_success_details(self):
        configure_console(verbose=True, color='never')
        result, output, _ = await self.run_collector(framed(sample_payload()))
        self.assertTrue(result['available'])
        self.assertIn('sign-in accepted', output)
        self.assertIn('datasets complete', output)

    async def test_parse_failure_is_visible_and_raw_stdout_not_logged(self):
        result, output, log = await self.run_collector('sensitive tenant records that are not JSON')
        self.assertFalse(result['available'])
        self.assertEqual('output', result['failure_stage'])
        self.assertIn('returned unreadable output', output)
        self.assertIn('stdout characters=', str(log.call_args))
        self.assertNotIn('sensitive tenant records', str(log.call_args))

    async def test_same_verified_url_retries_once(self):
        collector = AsyncMock(side_effect=[{'available': False, 'failure_stage': 'output'}, {'available': True}])
        with patch('Core.orchestrator.collect_sharepoint_governance_via_powershell', collector), patch('Core.orchestrator.sys.stdin.isatty', return_value=True):
            result = await collect_sharepoint_with_retry('https://test-admin.sharepoint.com', 'test', prompt=lambda _: 'https://test-admin.sharepoint.com/')
        self.assertTrue(result['available'])
        self.assertEqual(2, collector.await_count)

    async def test_partial_usable_data_does_not_prompt_for_new_url(self):
        collector = AsyncMock(return_value={'available': True, 'availability_status': 'partial'})
        prompt = Mock(side_effect=AssertionError('Unexpected retry'))
        with patch('Core.orchestrator.collect_sharepoint_governance_via_powershell', collector), patch('Core.orchestrator.sys.stdin.isatty', return_value=True):
            result = await collect_sharepoint_with_retry('https://test-admin.sharepoint.com', 'test', prompt=prompt)
        self.assertEqual('partial', result['availability_status'])
        prompt.assert_not_called()


if __name__ == '__main__':
    unittest.main()

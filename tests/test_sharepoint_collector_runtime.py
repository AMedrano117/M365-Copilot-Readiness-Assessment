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
from Core.orchestrator import collect_sharepoint_with_retry, resolve_sharepoint_admin_url
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
            self.assertIn(str(path), output.getvalue())
            self.assertEqual('a$literal#part\\path', os.environ['TEST_LITERAL'])

    async def test_invalid_config_does_not_silently_use_initial_domain(self):
        domain = SimpleNamespace(name='initial.onmicrosoft.com', is_initial=True)
        organization = SimpleNamespace(value=[SimpleNamespace(verified_domains=[domain])])
        client = SimpleNamespace(organization=SimpleNamespace(get=AsyncMock(return_value=organization)))
        with patch.dict(os.environ, {'SHAREPOINT_ADMIN_URL': 'https://wrong.sharepoint.com', 'PURVIEW_ORGANIZATION': ''}, clear=True), redirect_stdout(io.StringIO()):
            self.assertEqual('', await resolve_sharepoint_admin_url(client, interactive_auth='skip'))
            self.assertEqual('initial.onmicrosoft.com', os.environ['PURVIEW_ORGANIZATION'])
        client.organization.get.assert_awaited_once()


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

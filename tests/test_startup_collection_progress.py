"""Buffered consoles receive startup and area progress before mocked authentication."""

import io
import os
from pathlib import Path
import runpy
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from Core import console_reporting as console
from Core.orchestrator_pipelines import _collection_outcome, create_pipelines
from Core.orchestrator_powershell import (
    collect_power_platform_data,
    collect_purview_data_via_powershell,
    collect_sharepoint_governance_via_powershell,
)


class BufferedConsole(io.StringIO):
    def __init__(self):
        super().__init__()
        self.visible = ''

    def flush(self):
        self.visible = self.getvalue()


class StartupCollectionProgressTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        console.configure_console(verbose=False, color='never')

    def tearDown(self):
        console.configure_console(verbose=False, color='auto')

    def test_live_banner_is_visible_before_dependency_checks(self):
        output = BufferedConsole()

        def stop_before_live_work(*, offline):
            self.assertFalse(offline)
            self.assertIn('M365 COPILOT READINESS', output.visible)
            self.assertIn('Mode: live', output.visible)
            raise SystemExit(73)

        with patch('sys.argv', ['main.py', '--mode', 'live', '--color', 'never']), \
                patch('Core.console_setup.setup_console_encoding'), \
                patch('Core.check_dependencies.check_dependencies', side_effect=stop_before_live_work), \
                patch('Core.credentials_check.load_env_file', side_effect=AssertionError('Must stop before credentials')), \
                redirect_stdout(output), self.assertRaises(SystemExit) as exited:
            runpy.run_path(str(Path(__file__).resolve().parents[1] / 'main.py'), run_name='__main__')
        self.assertEqual(73, exited.exception.code)

    async def test_purview_message_is_visible_before_process_launch(self):
        output = BufferedConsole()
        process = SimpleNamespace(stdout=io.StringIO('{}'), stderr=io.StringIO(''), returncode=0, wait=Mock())

        def launch(*args, **kwargs):
            self.assertIn('Purview: connecting to Security & Compliance and Exchange Online', output.visible)
            self.assertIn('browser sign-in may be required', output.visible)
            return process

        with patch.dict(os.environ, {}, clear=True), \
                patch('Core.orchestrator_powershell._inspect_purview_cache', return_value=None), \
                patch('Core.orchestrator_powershell._launch_powershell', side_effect=launch), \
                patch('Core.orchestrator_powershell._set_purview_runtime_payload', return_value={}), \
                patch('Core.orchestrator_powershell._save_purview_cache'), redirect_stdout(output):
            self.assertTrue(await collect_purview_data_via_powershell(tenant_id='invented'))

    async def test_legacy_power_platform_message_precedes_authentication(self):
        output = BufferedConsole()

        def stop_before_launch(*args, **kwargs):
            self.assertIn('Power Platform / Copilot Studio: collecting deployment evidence', output.visible)
            self.assertIn('browser sign-in may be required', output.visible)
            raise RuntimeError('Mocked stop before authentication')

        with patch.dict(os.environ, {}, clear=True), \
                patch('Core.orchestrator_powershell._launch_powershell', side_effect=stop_before_launch), \
                redirect_stdout(output), self.assertRaisesRegex(RuntimeError, 'Mocked stop'):
            await collect_power_platform_data('invented', True, True)

    async def test_sharepoint_message_is_visible_before_process_launch(self):
        output = BufferedConsole()
        process = SimpleNamespace(communicate=Mock(return_value=('{}', '')), returncode=0)

        def launch(*args, **kwargs):
            self.assertIn('SharePoint: collecting sharing settings', output.visible)
            self.assertIn('SharePoint sign-in: complete the browser prompt', output.visible)
            return process

        payload = {'available': True, 'collection_status': {'settings': {'available': True}}}
        with patch.dict(os.environ, {}, clear=True), \
                patch('Core.orchestrator_powershell.Path.mkdir'), \
                patch('Core.orchestrator_powershell.tempfile.mkdtemp', return_value='invented-download-folder'), \
                patch('Core.orchestrator_powershell._launch_powershell', side_effect=launch), \
                patch('Core.orchestrator_powershell._parse_sharepoint_payload', return_value=payload), redirect_stdout(output):
            await collect_sharepoint_governance_via_powershell('https://invented-admin.sharepoint.com', 'invented')
        self.assertIn('1/1 datasets complete.', output.visible)

    def test_partial_and_optional_statuses_are_reported_honestly(self):
        client = SimpleNamespace(collection_status={
            'users': {'available': True, 'availability_status': 'available'},
            'sites': {'available': True, 'availability_status': 'partial', 'truncated': True},
            'labels': {'available': False, 'availability_status': 'available'},
            'preview': {'available': False, 'availability_status': 'not_requested'},
        })
        output = BufferedConsole()
        with redirect_stdout(output):
            _collection_outcome('M365', client)
        self.assertIn('M365: partial collection; 1/3 datasets completely read.', output.visible)
        self.assertNotIn('collection complete;', output.visible)

    def test_successful_zero_row_reads_count_as_completed_reads(self):
        client = SimpleNamespace(collection_status={
            'users': {'available': True, 'availability_status': 'available', 'records_collected': 0},
        })
        output = BufferedConsole()
        with redirect_stdout(output):
            _collection_outcome('M365', client)
        self.assertIn('M365: collection complete; 1/1 datasets read.', output.visible)

    async def test_selected_area_start_precedes_collector_and_complete_follows_processing(self):
        output = BufferedConsole()
        selected = {f'run_{key}': key == 'entra' for key in ('m365', 'entra', 'purview', 'defender', 'power_platform', 'copilot_studio')}
        client = SimpleNamespace(collection_status={'users': {'available': True}})

        async def collect(*args, **kwargs):
            self.assertIn('Entra: collecting identity, access and device evidence...', output.visible)
            return client

        async def process(*args, **kwargs):
            self.assertNotIn('collection complete;', output.visible)
            return {'available': True}

        with patch('Core.get_entra_client.get_entra_client', side_effect=collect), \
                patch('Core.get_entra_info.get_entra_info', side_effect=process), redirect_stdout(output):
            pipelines = create_pipelines(None, None, 'invented', selected)
            await pipelines['entra']()
            await pipelines['m365']()
        self.assertIn('Entra: collection complete; 1/1 datasets read.', output.visible)
        self.assertNotIn('M365:', output.visible)

    async def test_selected_but_disabled_purview_has_visible_skip_without_auth(self):
        selected = {f'run_{key}': key == 'purview' for key in ('m365', 'entra', 'purview', 'defender', 'power_platform', 'copilot_studio')}
        output = BufferedConsole()
        with patch.dict(os.environ, {}, clear=True), \
                patch('Core.orchestrator_pipelines.collect_purview_data_via_powershell', AsyncMock(side_effect=AssertionError('Skipped source must not authenticate'))), \
                redirect_stdout(output):
            pipelines = create_pipelines(None, None, 'invented', selected, interactive_plan={'purview': {'will_attempt': False, 'skip_reason': 'browser authentication disabled'}})
            result = await pipelines['purview']()
        self.assertFalse(result['available'])
        self.assertIn('Purview: skipped; browser authentication disabled.', output.visible)
        self.assertNotIn('collection complete', output.visible)


if __name__ == '__main__':
    unittest.main()

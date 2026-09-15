"""Console messages remain readable when service collectors run concurrently."""

import asyncio
import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from Core.get_entra_client import get_entra_client
from Core.get_m365_client import get_m365_client
from Core.orchestrator_pipelines import create_pipelines
from Core.console_reporting import configure_console


class PipelineConsoleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        configure_console(verbose=True, color='never')

    def tearDown(self):
        configure_console(verbose=False, color='auto')

    async def test_concurrent_client_output_uses_complete_lines(self):
        graph = SimpleNamespace(
            get_collection=AsyncMock(return_value={'available': True, 'value': []}),
            get_object=AsyncMock(return_value={}),
            get_csv=AsyncMock(return_value=[]),
        )
        output = io.StringIO()
        with patch('Core.ai_usage.collect_ai_usage', AsyncMock(return_value={})), patch('Core.get_entra_client._fetch_graph_collection_via_http', AsyncMock(return_value={'available': True, 'value': []})), patch('Core.get_entra_client._fetch_graph_object_via_http', AsyncMock(return_value={'available': True, 'object': {}})), patch('Core.get_entra_client._get_graph_token_roles', return_value=set()), redirect_stdout(output):
            await asyncio.gather(get_m365_client(graph), get_entra_client(graph, 'test-tenant'))
        text = output.getvalue()
        self.assertNotIn('\r', text)
        self.assertNotIn('0%', text)
        self.assertNotIn('100%', text)
        self.assertIn('M365 data collection started.\n', text)
        self.assertIn('Entra data collection started.\n', text)
        self.assertIn('M365 data collection finished;', text)
        self.assertIn('Entra data collection finished;', text)

    async def test_failed_processing_does_not_print_success(self):
        configure_console(verbose=False, color='never')
        services = {f'run_{name}': name == 'entra' for name in ('m365', 'entra', 'defender', 'purview', 'power_platform', 'copilot_studio')}
        pipelines = create_pipelines(None, None, 'test', services)
        output = io.StringIO()
        with patch('Core.get_entra_client.get_entra_client', AsyncMock(return_value=SimpleNamespace())), patch('Core.get_entra_info.get_entra_info', AsyncMock(side_effect=ValueError('invalid evidence'))), redirect_stdout(output):
            result = await pipelines['entra']()
        text = output.getvalue()
        self.assertFalse(result['available'])
        self.assertNotIn('Data Processing started', text)
        self.assertIn('Entra pipeline failed: invalid evidence\n', text)
        self.assertNotIn('finished', text)

    async def test_default_shows_collection_progress_and_hides_processing_details(self):
        configure_console(verbose=False, color='never')
        services = {f'run_{name}': name == 'entra' for name in ('m365', 'entra', 'defender', 'purview', 'power_platform', 'copilot_studio')}
        pipelines = create_pipelines(None, None, 'test', services)
        output = io.StringIO()
        with patch('Core.get_entra_client.get_entra_client', AsyncMock(return_value=SimpleNamespace())), patch('Core.get_entra_info.get_entra_info', AsyncMock(return_value={'available': True})), redirect_stdout(output):
            await pipelines['entra']()
        self.assertIn('Entra: collecting identity, access and device evidence...', output.getvalue())
        self.assertIn('Entra: collection finished; dataset completeness was not recorded.', output.getvalue())
        self.assertNotIn('Data Processing', output.getvalue())

    async def test_successful_processing_starts_and_finishes_on_separate_lines(self):
        services = {f'run_{name}': name == 'entra' for name in ('m365', 'entra', 'defender', 'purview', 'power_platform', 'copilot_studio')}
        pipelines = create_pipelines(None, None, 'test', services)
        output = io.StringIO()
        with patch('Core.get_entra_client.get_entra_client', AsyncMock(return_value=SimpleNamespace())), patch('Core.get_entra_info.get_entra_info', AsyncMock(return_value={'available': True})), redirect_stdout(output):
            await pipelines['entra']()
        lines = [line for line in output.getvalue().splitlines() if 'Data Processing' in line]
        self.assertEqual(2, len(lines))
        self.assertTrue(lines[0].endswith('Entra Data Processing started.'))
        self.assertTrue(lines[1].endswith('Entra Data Processing finished.'))


if __name__ == '__main__':
    unittest.main()

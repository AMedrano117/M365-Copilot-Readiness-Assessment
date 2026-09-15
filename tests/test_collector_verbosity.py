"""Quiet collection retains authentication prompts and actionable failures."""

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from Core.console_reporting import configure_console
from Core.orchestrator_powershell import collect_purview_data_via_powershell


class CollectorVerbosityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        configure_console(verbose=False, color='never')

    def tearDown(self):
        configure_console(verbose=False, color='auto')

    async def test_default_keeps_streamed_signin_and_required_source_failure(self):
        payload = {'collection_summary': {'required_failures': [{
            'source': 'DLP policies', 'reason': 'Read access was denied.',
            'required_role': 'View-Only DLP Compliance Management',
        }]}}
        process = SimpleNamespace(
            stdout=io.StringIO(json.dumps(payload)),
            stderr=io.StringIO('AUTH_PROMPT:Security & Compliance:Compliance commands\nAUTH_COMPLETE:Security & Compliance\n'),
            returncode=0, wait=lambda: None,
        )
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), patch('Core.orchestrator_powershell._inspect_purview_cache', return_value=None), patch('Core.orchestrator_powershell._launch_powershell', return_value=process), patch('Core.orchestrator_powershell._set_purview_runtime_payload', return_value=payload), patch('Core.orchestrator_powershell._save_purview_cache'), redirect_stdout(output):
            self.assertTrue(await collect_purview_data_via_powershell(tenant_id='invented'))
        text = output.getvalue()
        self.assertIn('Browser sign-in requested for Security & Compliance', text)
        self.assertIn('Required Purview evidence unavailable: DLP policies', text)
        self.assertIn('Read access was denied.', text)
        self.assertIn('View-Only DLP Compliance Management', text)
        self.assertNotIn('sign-in accepted', text)
        self.assertNotIn('Launching PowerShell', text)
        self.assertNotIn('Finalizing secure', text)
        self.assertNotIn('\r', text)

    async def test_cache_age_is_verbose_only(self):
        for verbose in (False, True):
            configure_console(verbose=verbose, color='never')
            output = io.StringIO()
            with self.subTest(verbose=verbose), patch('Core.orchestrator_powershell._inspect_purview_cache', return_value={'usable': True, 'json_payload': '{}', 'age_seconds': 3600}), patch('Core.orchestrator_powershell._set_purview_runtime_payload'), patch('Core.orchestrator_powershell._launch_powershell', side_effect=AssertionError('Cache should avoid collector launch')), redirect_stdout(output):
                self.assertTrue(await collect_purview_data_via_powershell(tenant_id='invented'))
            if verbose:
                self.assertIn('Using cached Purview deployment data', output.getvalue())
                self.assertIn('1h 0m old', output.getvalue())
            else:
                self.assertEqual('', output.getvalue())


if __name__ == '__main__':
    unittest.main()

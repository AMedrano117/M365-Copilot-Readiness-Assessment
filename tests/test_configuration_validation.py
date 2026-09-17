"""Configuration mistakes fail locally, before any customer authentication."""

import io
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, patch

from Core import console_reporting as console
from Core.credentials_check import load_env_file, check_credentials, print_configuration_summary
from Core.get_graph_client import _load_env


ROOT = Path(__file__).resolve().parents[1]
FAKE_ENV = {'TENANT_ID': 'synthetic-tenant', 'CLIENT_ID': 'synthetic-client',
            'CLIENT_SECRET': 'secret-never-display'}


class ConfigurationValidationTests(unittest.TestCase):
    def test_missing_explicit_file_fails_despite_inherited_credentials(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, FAKE_ENV, clear=True):
            with self.assertRaisesRegex(ValueError, 'Environment file does not exist'):
                check_credentials(str(Path(directory) / 'missing.env'))
            self.assertNotIn('ASSESSMENT_ENV_FILE', os.environ)

    def test_directory_is_not_an_environment_file(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'not a file'):
                load_env_file(directory)

    def test_default_absent_file_keeps_environment_authentication(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, FAKE_ENV, clear=True):
            self.assertIsNone(load_env_file(base_path=directory))
            self.assertEqual([], check_credentials(load_environment=False))

    def test_invalid_file_does_not_partially_replace_identity_or_expose_secret(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, FAKE_ENV, clear=True):
            path = Path(directory) / 'invalid.env'
            path.write_text('TENANT_ID=other-tenant\nCLIENT_SECRET="secret-never-display\n', encoding='utf-8')
            with self.assertRaises(ValueError) as error:
                load_env_file(path)
            self.assertNotIn('secret-never-display', str(error.exception))
            self.assertEqual('synthetic-tenant', os.environ['TENANT_ID'])
            self.assertNotIn('ASSESSMENT_ENV_FILE', os.environ)

    def test_unreadable_file_has_actionable_redacted_error(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / 'unreadable.env'
            path.write_text('', encoding='utf-8')
            with patch('builtins.open', side_effect=PermissionError('private-detail')):
                with self.assertRaisesRegex(ValueError, 'Environment file cannot be read') as error:
                    load_env_file(path)
            self.assertNotIn('private-detail', str(error.exception))

    def test_graph_and_cli_share_literal_quoted_value_parser(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / 'quoted.env'
            path.write_text('export TENANT_ID="synthetic-tenant" # note\n'
                            'CLIENT_SECRET="literal # $value `text`"\n'
                            "CERTIFICATE_PATH='C:\\synthetic folder\\cert.pfx'\n", encoding='utf-8-sig')
            _load_env(path)
            self.assertEqual('synthetic-tenant', os.environ['TENANT_ID'])
            self.assertEqual('literal # $value `text`', os.environ['CLIENT_SECRET'])
            self.assertEqual(r'C:\synthetic folder\cert.pfx', os.environ['CERTIFICATE_PATH'])

    def test_summary_redacts_secrets_and_invalid_url_credentials(self):
        output = io.StringIO()
        configured = dict(FAKE_ENV, CERTIFICATE_PATH='private-key-file.pfx',
                          CERTIFICATE_PASSWORD='password-never-display',
                          SHAREPOINT_ADMIN_URL='https://user:url-password@contoso-admin.sharepoint.com')
        with patch.dict(os.environ, configured, clear=True), redirect_stdout(output):
            console.configure_console(color='never')
            print_configuration_summary(tenant_id='synthetic-tenant', permission_profile='standard',
                                        env_path='synthetic.env')
        rendered = output.getvalue()
        for expected in ('synthetic-tenant', 'synthetic-client', 'synthetic.env', 'standard',
                         'Graph authentication: certificate', 'SharePoint admin URL: invalid'):
            self.assertIn(expected, rendered)
        for secret in ('secret-never-display', 'password-never-display', 'url-password', 'private-key-file'):
            self.assertNotIn(secret, rendered)

    def test_restricted_summary_does_not_display_ignored_url(self):
        with patch.dict(os.environ, FAKE_ENV, clear=True), redirect_stdout(io.StringIO()) as output:
            print_configuration_summary(tenant_id='synthetic-tenant', permission_profile='restricted',
                                        sharepoint_admin_url='https://contoso-admin.sharepoint.com')
        self.assertIn('not requested by Restricted', output.getvalue())
        self.assertNotIn('contoso-admin', output.getvalue())

    def test_main_stops_before_orchestration_for_mistyped_environment_file(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, FAKE_ENV, clear=True), \
             patch('sys.argv', ['main.py', '--env-file', str(Path(directory) / 'missing.env')]), \
             patch('Core.console_setup.setup_console_encoding'), \
             patch('Core.check_dependencies.check_dependencies'), \
             patch('Core.orchestrator.orchestrate', new_callable=AsyncMock) as orchestrate, \
             redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit) as exited:
            runpy.run_path(str(ROOT / 'main.py'), run_name='__main__')
        self.assertEqual(1, exited.exception.code)
        self.assertIn('Configuration error:', output.getvalue())
        self.assertNotIn('Traceback', output.getvalue())
        orchestrate.assert_not_called()

    def test_main_prints_resolved_configuration_before_auth_and_applies_tenant_override(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / 'synthetic.env'
            path.write_text('\n'.join(f'{key}={value}' for key, value in FAKE_ENV.items()), encoding='utf-8')
            output = io.StringIO()

            async def inspect_configuration(*args, **kwargs):
                self.assertEqual('override-tenant', args[0])
                self.assertEqual('override-tenant', os.environ['TENANT_ID'])
                self.assertEqual('secret-never-display', os.environ['CLIENT_SECRET'])
                self.assertIn('Tenant: override-tenant', output.getvalue())
                self.assertIn('Graph authentication: client secret', output.getvalue())
                self.assertIn('https://contoso-admin.sharepoint.com', output.getvalue())
                self.assertNotIn('secret-never-display', output.getvalue())
                return 0

            with patch('sys.argv', ['main.py', '--env-file', str(path), '--tenant-id', 'override-tenant',
                                    '--sharepoint-admin-url', 'https://contoso-admin.sharepoint.com']), \
                 patch('Core.console_setup.setup_console_encoding'), \
                 patch('Core.check_dependencies.check_dependencies'), \
                 patch('Core.orchestrator.orchestrate', side_effect=inspect_configuration) as orchestrate, \
                 redirect_stdout(output):
                runpy.run_path(str(ROOT / 'main.py'), run_name='__main__')
            orchestrate.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()

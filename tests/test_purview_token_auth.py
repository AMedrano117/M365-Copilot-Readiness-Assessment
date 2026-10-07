"""Application tokens for Purview/Exchange PowerShell, delivered over stdin only."""

import asyncio
import base64
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from Core import orchestrator_powershell as launcher
from Core.orchestrator_powershell import _launch_powershell, _sanitize_collector_detail, powershell_environment
from Core.workload_tokens import MANAGE_AS_APP, purview_token_secrets


def jwt(roles=(), wids=()):
    body = base64.urlsafe_b64encode(json.dumps({"roles": list(roles), "wids": list(wids)}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{body}.signature-part-long-enough"


class FakeProcess:
    def __init__(self, args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.written = io.StringIO()
        self.stdin = self if kwargs.get("stdin") else None
        self.returncode = 0

    def write(self, text):
        self.written.write(text)

    def flush(self):
        pass

    def close(self):
        pass


class LaunchTests(unittest.TestCase):
    def launch(self, secrets):
        created = []

        def popen(args, **kwargs):
            created.append(FakeProcess(args, **kwargs))
            return created[-1]

        with patch("Core.orchestrator_powershell.shutil.which", return_value=r"C:\\pwsh\\pwsh.exe"), \
                patch("Core.orchestrator_powershell.subprocess.Popen", side_effect=popen):
            process = _launch_powershell("collector.ps1", ["-TenantId", "t"], secrets=secrets)
        return created[0], process

    def test_secrets_travel_on_stdin_never_in_arguments(self):
        token = jwt([MANAGE_AS_APP])
        created, process = self.launch({"exchange_access_token": token, "certificate_password": "p@ssw0rd-long"})
        argv = " ".join(created.args)
        self.assertNotIn(token, argv)
        self.assertNotIn("p@ssw0rd-long", argv)
        self.assertIn("-SecretsFromStdin", created.args)
        self.assertEqual({"exchange_access_token": token, "certificate_password": "p@ssw0rd-long"},
                         json.loads(created.written.getvalue()))
        self.assertIsNone(process.stdin)
        # Registered runtime secrets are redacted from diagnostics.
        self.assertNotIn("p@ssw0rd-long", _sanitize_collector_detail("failure p@ssw0rd-long"))

    def test_no_secrets_keeps_stdin_inherited_and_arguments_unchanged(self):
        created, _ = self.launch({})
        self.assertNotIn("-SecretsFromStdin", created.args)
        self.assertIsNone(created.kwargs.get("stdin"))


class EnvironmentTests(unittest.TestCase):
    def test_powershell7_core_modules_are_removed_for_windows_powershell(self):
        path = os.pathsep.join([
            r"C:\Users\me\Documents\PowerShell\Modules",
            r"C:\Program Files\PowerShell\Modules",
            r"C:\Program Files\PowerShell\7\Modules",
            r"c:\program files\windowsapps\microsoft.powershell_7.6.6.0_x64__8wekyb3d8bbwe\Modules",
            r"C:\Program Files\WindowsPowerShell\Modules",
            r"C:\WINDOWS\system32\WindowsPowerShell\v1.0\Modules",
        ])
        cleaned = powershell_environment(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", {"PSMODULEPATH": path})
        entries = cleaned["PSModulePath"].split(os.pathsep)
        self.assertIn(r"C:\Users\me\Documents\PowerShell\Modules", entries)
        self.assertIn(r"C:\WINDOWS\system32\WindowsPowerShell\v1.0\Modules", entries)
        self.assertNotIn(r"C:\Program Files\PowerShell\7\Modules", entries)
        self.assertFalse([entry for entry in entries if "microsoft.powershell_" in entry.lower()])
        self.assertNotIn("PSMODULEPATH", cleaned)

    def test_powershell7_environment_is_untouched(self):
        base = {"PSModulePath": r"C:\Program Files\PowerShell\7\Modules"}
        self.assertEqual(base, powershell_environment(r"C:\Program Files\PowerShell\7\pwsh.exe", base))


class TokenSecretTests(unittest.IsolatedAsyncioTestCase):
    async def secrets_for(self, exchange_roles, compliance_roles, wids=()):
        tokens = {
            "https://outlook.office365.com/.default": jwt(exchange_roles, wids),
            "https://ps.compliance.protection.outlook.com/.default": jwt(compliance_roles, wids),
        }
        credential = SimpleNamespace(get_token=lambda scope: SimpleNamespace(token=tokens[scope]))
        return await purview_token_secrets(credential)

    async def test_only_tokens_with_manage_as_app_are_passed(self):
        secrets, details = await self.secrets_for([MANAGE_AS_APP], [], wids=["5d6b6bb7-de71-4623-b4af-96380a352509"])
        self.assertEqual(["exchange_access_token"], list(secrets))
        self.assertIn("Microsoft Exchange Online Protection", details["reason"])
        self.assertIn("5d6b6bb7-de71-4623-b4af-96380a352509", details["directory_roles"])

    async def test_missing_consent_yields_no_secrets_and_an_unlock_reason(self):
        secrets, details = await self.secrets_for([], [])
        self.assertEqual({}, secrets)
        self.assertIn("Exchange.ManageAsApp is not consented", details["reason"])


class CollectorLaunchTests(unittest.IsolatedAsyncioTestCase):
    async def test_skip_without_application_path_never_launches_powershell(self):
        env = {"CLIENT_ID": "app", "CLIENT_SECRET": "secret-value", "PURVIEW_ORGANIZATION": "contoso.onmicrosoft.com"}
        with patch.dict(os.environ, env, clear=True), \
                patch("Core.orchestrator_powershell._inspect_purview_cache", return_value=None), \
                patch("Core.workload_tokens.purview_token_secrets", AsyncMock(return_value=({}, {"reason": "Exchange.ManageAsApp is not consented."}))), \
                patch("Core.orchestrator_powershell._launch_powershell", side_effect=AssertionError("must not launch")), \
                redirect_stdout(io.StringIO()):
            self.assertFalse(await launcher.collect_purview_data_via_powershell(auth_mode="skip", tenant_id="t"))
        self.assertIn("Exchange.ManageAsApp is not consented", launcher.LAST_PURVIEW_FAILURE["reason"])

    async def test_token_path_launches_with_skip_and_stdin_secrets(self):
        env = {"CLIENT_ID": "app", "CLIENT_SECRET": "secret-value", "PURVIEW_ORGANIZATION": "contoso.onmicrosoft.com"}
        calls = {}

        def fake_launch(script, args, prefer_windows=False, secrets=None):
            calls.update(args=args, secrets=secrets)
            raise RuntimeError("stop after launch arguments are captured")

        token = jwt([MANAGE_AS_APP])
        with patch.dict(os.environ, env, clear=True), \
                patch("Core.orchestrator_powershell._inspect_purview_cache", return_value=None), \
                patch("Core.workload_tokens.purview_token_secrets", AsyncMock(return_value=({"compliance_access_token": token}, {"reason": ""}))), \
                patch("Core.orchestrator_powershell._launch_powershell", side_effect=fake_launch), \
                redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                await launcher.collect_purview_data_via_powershell(auth_mode="skip", tenant_id="t")
        self.assertEqual("Skip", calls["args"][calls["args"].index("-AuthMode") + 1])
        self.assertEqual({"compliance_access_token": token}, calls["secrets"])
        self.assertNotIn(token, " ".join(calls["args"]))


class ConnectionProbeTests(unittest.IsolatedAsyncioTestCase):
    async def _probe(self, returncode, stdout, stderr=""):
        from Core import connection_validation as validation

        class Process:
            def __init__(self):
                self.returncode = returncode

            def communicate(self):
                return stdout, stderr

        with patch.object(validation, "_launch_powershell", return_value=Process()):
            return await validation._powershell_probe(
                "purview", "collect_purview_data.ps1", [], partial_hint="Assign Global Reader.")

    async def test_roles_exposing_some_datasets_are_ready_with_gaps(self):
        from Core.connection_validation import PARTIAL, connection_exit_code, connection_status_to_availability
        payload = json.dumps({"ready": True, "exposed": ["DLP policies", "sensitivity labels"],
                              "missing": ["retention policies", "organization configuration"]})
        result = await self._probe(0, "banner text\n" + payload + "\n")
        self.assertEqual(PARTIAL, result["status"])
        self.assertIn("Readable: DLP policies, sensitivity labels", result["reason"])
        self.assertIn("Not exposed by the current role: retention policies, organization configuration", result["reason"])
        self.assertIn("Assign Global Reader.", result["reason"])
        self.assertEqual(0, connection_exit_code([result]))
        self.assertEqual("available", connection_status_to_availability(PARTIAL))

    async def test_full_exposure_is_ready(self):
        from Core.connection_validation import READY
        result = await self._probe(0, json.dumps({"ready": True, "exposed": ["DLP policies"], "missing": []}))
        self.assertEqual(READY, result["status"])

    async def test_no_exposed_cmdlet_is_role_missing(self):
        from Core.connection_validation import ROLE
        result = await self._probe(2, "", "CONNECTION_ERROR:Purview:role_missing: the connected identity has no role")
        self.assertEqual(ROLE, result["status"])


class PurviewCacheTests(unittest.TestCase):
    def inspect(self, payload, **kwargs):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cache.json"
            with patch.object(launcher, "_get_purview_cache_path", return_value=path):
                launcher._save_purview_cache("tenant", json.dumps(payload))
                return launcher._inspect_purview_cache("tenant", **kwargs)

    def payload(self, **changes):
        base = {"dlp_rules": {"available": True, "rules": []},
                "label_policies": {"available": True, "policies": [{"Name": "Global", "ExchangeLocation": "All", "ExchangeLocationCount": 1}]},
                "collection_summary": {"required_failures": []}}
        base.update(changes)
        return base

    def test_complete_cache_is_reused(self):
        self.assertTrue(self.inspect(self.payload(), reuse_incomplete=False)["usable"])

    def test_cache_without_label_scope_is_recollected(self):
        payload = self.payload(label_policies={"available": True, "policies": [{"Name": "Global", "Mode": "Enforce"}]})
        self.assertEqual(self.inspect(payload)["reason"], "incompatible")

    def test_incomplete_cache_is_recollected_only_when_an_application_token_can(self):
        payload = self.payload(collection_summary={"required_failures": [{"source": "Retention Policies"}]})
        self.assertEqual(self.inspect(payload, reuse_incomplete=False)["reason"], "incomplete")
        # A delegated-only run reuses it rather than prompting again.
        self.assertTrue(self.inspect(payload, reuse_incomplete=True)["usable"])


class PurviewScriptTests(unittest.TestCase):
    def test_label_policy_assignments_exclusions_and_counts_are_retained(self):
        from pathlib import Path
        script = (Path(__file__).resolve().parents[1] / "collect_purview_data.ps1").read_text(encoding="utf-8-sig")
        block = script[script.index("$labelPolicies = @(Get-LabelPolicy"):script.index("$purviewData['label_policies'] = New-CollectionSuccess")]
        self.assertIn("ExchangeLocationCount", block)
        for field in ('ExchangeLocationException', 'ModernGroupLocationException', 'Labels', 'Identity'):
            self.assertIn(field, block)

    def test_connection_probe_reports_exposed_datasets_instead_of_failing(self):
        from pathlib import Path
        script = (Path(__file__).resolve().parents[1] / "collect_purview_data.ps1").read_text(encoding="utf-8-sig")
        probe = script[script.index("if ($ConnectionOnly) {"):script.index("# Step 2: Collect Purview data")]
        self.assertIn("Get-Command $cmdletName -ErrorAction SilentlyContinue", probe)
        self.assertIn("missing = @($missingDatasets)", probe)
        self.assertNotIn("$null = Get-OrganizationConfig -ErrorAction Stop", probe)

    def test_collector_tries_token_then_certificate_then_delegated(self):
        from pathlib import Path
        script = (Path(__file__).resolve().parents[1] / "collect_purview_data.ps1").read_text(encoding="utf-8-sig")
        token = script.index("Connect-IPPSSession -AccessToken $ComplianceAccessToken")
        certificate = script.index("Connect-IPPSSession @certificateParameters")
        delegated = script.index("Connect-IPPSSession -DisableWAM")
        self.assertLess(token, certificate)
        self.assertLess(certificate, delegated)
        self.assertIn('"Skip"', script)
        self.assertIn("[Console]::In.ReadLine()", script)
        self.assertIn("'workload_app_token'", script)


if __name__ == "__main__":
    unittest.main()

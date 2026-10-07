"""Hardening: access errors, tenant confirmation, report privacy and checkpoint recovery."""

import asyncio
import base64
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from azure.core.exceptions import HttpResponseError

from Core.access_errors import ACCESS_ERRORS, describe_access_failure, status_code_of
from Core.get_graph_client import GraphRequestError
from Core.report_privacy import build_report_privacy_recommendations, collect_report_settings
from Core.tenant_identity import TenantMismatchError, confirm_tenant_identity

TENANT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


def token_for(tenant):
    body = base64.urlsafe_b64encode(json.dumps({"tid": tenant}).encode()).decode().rstrip("=")
    return SimpleNamespace(token=f"header.{body}.signature")


class FakeOrgClient:
    def __init__(self, tenant=TENANT, domains=("contoso.onmicrosoft.com", "contoso.com"), token_tenant=None):
        self.tenant = tenant
        self.domains = domains
        self.credential = SimpleNamespace(get_token=lambda *scopes: token_for(token_tenant or tenant))

    async def get_json(self, path, params=None):
        return {"value": [{"id": self.tenant, "displayName": "Contoso", "verifiedDomains": [
            {"name": name, "isInitial": name.endswith(".onmicrosoft.com")} for name in self.domains]}]}


class AccessErrorTests(unittest.TestCase):
    def test_both_exception_types_are_classified(self):
        self.assertIn(GraphRequestError, ACCESS_ERRORS)
        self.assertIn(HttpResponseError, ACCESS_ERRORS)
        category, message = describe_access_failure("Entra information", GraphRequestError(403, "denied"))
        self.assertEqual("permission_denied", category)
        self.assertIn("application permission", message)
        self.assertNotIn("requires admin role", message)
        _, delegated = describe_access_failure("Limited mode", GraphRequestError(403, "denied"), delegated=True)
        self.assertIn("signed-in account", delegated)
        self.assertEqual(429, status_code_of(GraphRequestError(429, "slow")))

    def test_license_read_403_no_longer_fails_the_m365_pipeline(self):
        from Core.get_m365_info import get_m365_info

        class DeniedSkus:
            class subscribed_skus:
                @staticmethod
                async def get():
                    raise GraphRequestError(403, "Insufficient privileges")

        output = io.StringIO()
        with redirect_stdout(output):
            result = asyncio.run(get_m365_info(DeniedSkus()))
        self.assertEqual(([], []), result)
        self.assertIn("access denied (HTTP 403)", output.getvalue())

    def test_info_modules_do_not_catch_only_the_sdk_exception(self):
        root = Path(__file__).resolve().parents[1] / "Core"
        for name in ("get_m365_info.py", "get_entra_info.py", "get_purview_info.py", "get_defender_info.py",
                     "get_copilot_studio_info.py", "get_entra_client.py"):
            source = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("except HttpResponseError", source, name)
            self.assertNotIn("requires admin role", source, name)


class TenantIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def confirm(self, client, **kwargs):
        with redirect_stdout(io.StringIO()), patch.dict(os.environ, {}, clear=True):
            return await confirm_tenant_identity(client, TENANT, **kwargs)

    async def test_token_from_another_tenant_stops_collection(self):
        with self.assertRaises(TenantMismatchError):
            await self.confirm(FakeOrgClient(token_tenant=OTHER), interactive=False)

    async def test_expected_domain_must_belong_to_the_tenant(self):
        identity = await self.confirm(FakeOrgClient(), expected_domain="contoso.com", interactive=False)
        self.assertEqual("expected_domain", identity["confirmation"])
        self.assertEqual("contoso.onmicrosoft.com", identity["initial_domain"])
        with self.assertRaises(TenantMismatchError):
            await self.confirm(FakeOrgClient(), expected_domain="fabrikam.onmicrosoft.com", interactive=False)

    async def test_environment_expected_domain_is_used(self):
        with redirect_stdout(io.StringIO()), patch.dict(os.environ, {"EXPECTED_TENANT_DOMAIN": "fabrikam.com"}, clear=True):
            with self.assertRaises(TenantMismatchError):
                await confirm_tenant_identity(FakeOrgClient(), TENANT, interactive=False)

    async def test_interactive_operator_must_confirm(self):
        with self.assertRaises(TenantMismatchError):
            await self.confirm(FakeOrgClient(), interactive=True, prompt=lambda message: "n")
        identity = await self.confirm(FakeOrgClient(), interactive=True, prompt=lambda message: "yes")
        self.assertEqual("operator", identity["confirmation"])

    async def test_unattended_run_without_expectation_warns_and_continues(self):
        output = io.StringIO()
        with redirect_stdout(output), patch.dict(os.environ, {}, clear=True):
            identity = await confirm_tenant_identity(FakeOrgClient(), TENANT, interactive=False)
        self.assertEqual("unconfirmed", identity["confirmation"])
        self.assertIn("EXPECTED_TENANT_DOMAIN", output.getvalue())

    def test_no_built_in_tenant_fallback(self):
        import params
        self.assertEqual("", params.TENANT_ID)


class ReportPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, payload=None, error=None):
        class Client:
            async def get_json(self, path, params=None):
                if error:
                    raise error
                return payload
        with patch.dict(os.environ, {"CLIENT_SECRET": "x"}, clear=True):
            return await collect_report_settings(Client())

    async def test_concealed_names_create_a_reference_finding(self):
        state = await self.collect({"displayConcealedNames": True})
        self.assertIs(True, state["display_concealed_names"])
        rows = build_report_privacy_recommendations(state)
        self.assertEqual(["m365.reports.concealed_names"], [row["FindingKey"] for row in rows])
        self.assertEqual("Reference", rows[0]["Disposition"])

    async def test_visible_names_or_denied_access_create_no_finding(self):
        self.assertEqual([], build_report_privacy_recommendations(await self.collect({"displayConcealedNames": False})))
        denied = await self.collect(error=GraphRequestError(403, "denied"))
        self.assertFalse(denied["available"])
        self.assertIn("ReportSettings.Read.All", denied["reason"])
        self.assertEqual([], build_report_privacy_recommendations(denied))


class CheckpointRecoveryTests(unittest.TestCase):
    def test_failed_m365_pipeline_keeps_saved_sharepoint_evidence(self):
        from Core.collection_checkpoint import CollectionCheckpoint
        from Core.offline_collection import load_collection
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            checkpoint = CollectionCheckpoint(Path(folder) / "collection.json", service_config={"run_m365": True},
                                              tenant_id=TENANT, tenant_name="Contoso")
            checkpoint.save()
            checkpoint.record_sharepoint({"available": False, "reason": "Graph baseline only",
                                          "collection_status": {"sharepoint_tenant_settings_graph": {"available": True}}})
            checkpoint.record("m365", ([], []))
            saved = load_collection(checkpoint.path)
        m365 = saved["service_results"]["m365_result"]
        self.assertEqual("failed", saved["collection_progress"]["services"]["m365"]["status"])
        self.assertIn("sharepoint_governance", json.dumps(m365[0].get("_client", {}), default=str))
        self.assertTrue([row for row in m365[1] if row.get("FindingKey") == "sharepoint.governance.not_assessed"])


if __name__ == "__main__":
    unittest.main()

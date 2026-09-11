import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from Core.collector_registry import selected_collector_ids
from Core.connection_validation import (
    LICENSE,
    MODULE,
    NOT_SELECTED,
    PERMISSION,
    READY,
    _classify_http,
    connection_exit_code,
    connection_status_to_availability,
)
from Core.get_graph_client import GraphRestClient
from Core.orchestrator import is_valid_sharepoint_admin_url, resolve_sharepoint_admin_url
from Core.orchestrator_setup import prepare_interactive_collection_plan


ROOT = Path(__file__).resolve().parents[1]


class FakeToken:
    token = "header.eyJyb2xlcyI6IFtdfQ.signature"


class FakeCredential:
    def get_token(self, scope):
        return FakeToken()


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = text
        self.content = b"{}"
        self.is_error = status_code >= 400

    def json(self):
        return self._payload


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return self.responses.pop(0)


class GraphRestClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_pagination_consumes_every_next_link(self):
        client = GraphRestClient.__new__(GraphRestClient)
        client.credential = FakeCredential()
        client.max_retries = 0
        client._http = FakeHttp([
            FakeResponse(payload={"value": [{"id": "1"}], "@odata.nextLink": "https://graph.microsoft.com/v1.0/items?page=2"}),
            FakeResponse(payload={"value": [{"id": "2"}]}),
        ])
        result = await client.get_collection("/v1.0/items")
        self.assertEqual(["1", "2"], [row["id"] for row in result["value"]])
        self.assertEqual(2, result["pages_collected"])
        self.assertFalse(result["truncated"])

    async def test_throttle_retries_with_retry_after(self):
        client = GraphRestClient.__new__(GraphRestClient)
        client.credential = FakeCredential()
        client.max_retries = 1
        client._http = FakeHttp([
            FakeResponse(429, {"error": {"message": "slow"}}, {"Retry-After": "0"}),
            FakeResponse(200, {"value": []}),
        ])
        with patch("Core.get_graph_client.asyncio.sleep") as sleep:
            response = await client.request("GET", "/v1.0/test")
        self.assertEqual(200, response.status_code)
        sleep.assert_awaited_once()


class CollectorPolicyTests(unittest.TestCase):
    def _services(self):
        return {
            "run_m365": True, "run_entra": True, "run_defender": True,
            "run_purview": True, "run_power_platform": True,
            "run_copilot_studio": True,
        }

    def test_preview_and_legacy_collectors_are_opt_in(self):
        selected = selected_collector_ids(self._services(), "none", legacy=False)
        self.assertNotIn("power_platform", selected)
        self.assertNotIn("shadow_ai", selected)
        self.assertNotIn("network_access", selected)
        self.assertNotIn("legacy_power_platform", selected)
        self.assertIn("sharepoint_governance", selected)

    @patch("Core.orchestrator_setup.get_local_powershell_module_availability")
    def test_power_platform_is_not_gated_on_az_without_legacy_option(self, modules):
        modules.return_value = {
            "ExchangeOnlineManagement": True,
            "Microsoft.Online.SharePoint.PowerShell": True,
            "Az.Accounts": False,
        }
        plan = prepare_interactive_collection_plan(
            self._services(), legacy_power_platform_collector=False,
            install_missing_modules=False,
        )
        self.assertFalse(plan["power_platform"]["selected"])
        self.assertFalse(plan["power_platform"]["will_attempt"])

    def test_connection_exit_codes_treat_license_as_usable(self):
        self.assertEqual(0, connection_exit_code([{"status": LICENSE, "decision_effect": True}]))
        self.assertEqual(2, connection_exit_code([{"status": MODULE, "decision_effect": True}]))
        self.assertEqual(0, connection_exit_code([{"status": NOT_SELECTED, "decision_effect": False}]))

    def test_connection_labels_are_normalized_for_collection_coverage(self):
        self.assertEqual("available", connection_status_to_availability(READY))
        self.assertEqual("not_requested", connection_status_to_availability(NOT_SELECTED))
        self.assertEqual("unavailable", connection_status_to_availability(PERMISSION))

    def test_entra_risk_403_is_license_only_when_permission_is_present(self):
        licensed_limit = _classify_http(
            "entra_risk", 403, "Forbidden",
            {"IdentityRiskyUser.Read.All"}, {"IdentityRiskyUser.Read.All"},
        )
        missing_permission = _classify_http(
            "entra_risk", 403, "Forbidden", set(), {"IdentityRiskyUser.Read.All"},
        )
        conditional_access = _classify_http(
            "entra_controls", 403, "Forbidden", {"Policy.Read.All"}, {"Policy.Read.All"},
        )
        self.assertEqual(LICENSE, licensed_limit["status"])
        self.assertEqual(PERMISSION, missing_permission["status"])
        self.assertNotEqual(LICENSE, conditional_access["status"])


class SharePointResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_cli_url_precedes_environment_and_graph(self):
        client = SimpleNamespace()
        with patch.dict("os.environ", {"SHAREPOINT_ADMIN_URL": "https://env-admin.sharepoint.com"}):
            result = await resolve_sharepoint_admin_url(
                client, explicit_url="https://cli-admin.sharepoint.com", interactive_auth="skip"
            )
        self.assertEqual("https://cli-admin.sharepoint.com", result)

    async def test_initial_domain_is_derived(self):
        domain = SimpleNamespace(name="contoso.onmicrosoft.com", is_initial=True)
        response = SimpleNamespace(value=[SimpleNamespace(verified_domains=[domain])])
        client = SimpleNamespace(organization=SimpleNamespace(get=lambda: asyncio.sleep(0, result=response)))
        with patch.dict("os.environ", {}, clear=True):
            result = await resolve_sharepoint_admin_url(client, interactive_auth="skip")
        self.assertEqual("https://contoso-admin.sharepoint.com", result)

    def test_admin_url_validation(self):
        self.assertTrue(is_valid_sharepoint_admin_url("https://contoso-admin.sharepoint.com"))
        self.assertFalse(is_valid_sharepoint_admin_url("http://contoso-admin.sharepoint.com"))
        self.assertFalse(is_valid_sharepoint_admin_url("https://contoso.sharepoint.com"))


class StaticSetupContractTests(unittest.TestCase):
    def test_setup_reconciles_without_deleting_or_rotating_by_default(self):
        text = (ROOT / "setup-service-principal.ps1").read_text(encoding="utf-8")
        self.assertNotIn("Remove-MgApplication", text)
        self.assertIn("RotateCredential", text)
        self.assertIn("PruneUnusedPermissions", text)
        self.assertIn("PreviewCollectors", text)
        self.assertIn("Unattended", text)

    def test_removed_dependencies_and_permissions_stay_removed(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        setup = (ROOT / "setup-service-principal.ps1").read_text(encoding="utf-8")
        self.assertNotIn("msgraph-sdk", requirements)
        for unused in (
            "ThreatIndicators.Read.All", "ThreatHunting.Read.All", "Printer.Read.All",
            "People.Read.All", "OnlineMeetings.Read.All", "WorkplaceAnalytics-Reports.Read.All",
            "ActivityFeed.Read", "Files.Read.All",
        ):
            self.assertNotIn(unused, setup)

    def test_az_accounts_is_only_a_legacy_requirement(self):
        checks = (ROOT / "Check-PSModules.ps1").read_text(encoding="utf-8")
        setup = (ROOT / "setup-service-principal.ps1").read_text(encoding="utf-8")
        self.assertIn('"LegacyPowerPlatform"', checks)
        self.assertNotIn("Az.Accounts", setup)

    def test_specialized_purview_sources_are_not_queried_by_default(self):
        purview = (ROOT / "collect_purview_data.ps1").read_text(encoding="utf-8")
        launcher = (ROOT / "Core" / "orchestrator_powershell.py").read_text(encoding="utf-8")
        self.assertIn("[switch]$IncludeSpecialized", purview)
        self.assertIn("if ($IncludeSpecialized)", purview)
        self.assertIn("PURVIEW_INCLUDE_SPECIALIZED", launcher)


if __name__ == "__main__":
    unittest.main()

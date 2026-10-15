import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from Core.collector_registry import selected_collector_ids
from Core.connection_validation import (
    LICENSE,
    MODULE,
    NOT_SELECTED,
    PERMISSION,
    READY,
    FAILED,
    _classify_http,
    connection_exit_code,
    connection_status_to_availability,
    run_connection_checks,
)
from Core.get_graph_client import GraphRequestError, GraphRestClient
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


class UsageConnectionProbeTests(unittest.IsolatedAsyncioTestCase):
    async def check_usage(self, request, roles=None):
        client = SimpleNamespace(credential=FakeCredential(), request=AsyncMock(side_effect=request))
        with patch('Core.connection_validation.selected_collector_ids', return_value=['m365_usage']), \
                patch('Core.connection_validation._decode_roles', return_value=(
                    {'Reports.Read.All', 'Sites.Read.All'} if roles is None else roles)):
            results = await run_connection_checks(client, {}, {}, preview_collectors='none')
        return next(result for result in results if result['collector_id'] == 'm365_usage'), client

    async def test_unreadable_unused_active_users_does_not_fail_working_apps_connection(self):
        async def request(method, path, **kwargs):
            if 'getOffice365ActiveUserCounts' in path:
                raise GraphRequestError(0, 'ReadError', failure_stage='download', failure_host='reportsncu.office.com')
            return FakeResponse(text='Report Refresh Date,Report Date,Report Period,Word\n2026-10-12,2026-10-10,30,2\n')
        result, client = await self.check_usage(request)
        self.assertEqual(READY, result['status'])
        self.assertIn('Microsoft 365 Apps D30 CSV read succeeded', result['reason'])
        self.assertIn('verified during collection', result['reason'])
        client.request.assert_awaited_once_with(
            'GET', "/v1.0/reports/getM365AppUserCounts(period='D30')",
            params={'$format': 'text/csv'}, headers={'Accept': 'text/csv'})

    async def test_used_apps_download_failure_is_still_reported(self):
        async def request(method, path, **kwargs):
            raise GraphRequestError(0, 'ReadError', failure_stage='download', failure_host='reportsncu.office.com')
        result, client = await self.check_usage(request)
        self.assertEqual(FAILED, result['status'])
        self.assertEqual('download', result['failure_stage'])
        self.assertIn('reportsncu.office.com', result['reason'])
        self.assertNotIn('CSV read succeeded', result['reason'])
        self.assertEqual(1, client.request.await_count)

    async def test_missing_reports_permission_does_not_attempt_or_pass_probe(self):
        result, client = await self.check_usage(lambda *args, **kwargs: FakeResponse(), roles={'Sites.Read.All'})
        self.assertEqual(PERMISSION, result['status'])
        self.assertIn('Reports.Read.All', result['reason'])
        client.request.assert_not_awaited()


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

    def test_entra_risk_403_requires_a_licensing_response_to_confirm_license_status(self):
        licensed_limit = _classify_http(
            "entra_risk", 403, "Tenant is not licensed for premium risk data",
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
        unknown_denial = _classify_http('entra_risk', 403, 'Forbidden',
                                       {'IdentityRiskyUser.Read.All'}, {'IdentityRiskyUser.Read.All'})
        self.assertEqual('Connection failed', unknown_denial['status'])
        self.assertIn('does not confirm a licensing cause', unknown_denial['reason'])


class SharePointResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_cli_url_precedes_environment_and_graph(self):
        client = SimpleNamespace()
        with patch.dict("os.environ", {"SHAREPOINT_ADMIN_URL": "https://env-admin.sharepoint.com"}):
            result = await resolve_sharepoint_admin_url(
                client, explicit_url="https://cli-admin.sharepoint.com", interactive_auth="skip"
            )
        self.assertEqual("https://cli-admin.sharepoint.com", result)

    async def test_initial_domain_is_not_used_as_sharepoint_hostname(self):
        domain = SimpleNamespace(name="contoso.onmicrosoft.com", is_initial=True)
        response = SimpleNamespace(value=[SimpleNamespace(verified_domains=[domain])])
        client = SimpleNamespace(organization=SimpleNamespace(get=lambda: asyncio.sleep(0, result=response)))
        with patch.dict("os.environ", {}, clear=True):
            result = await resolve_sharepoint_admin_url(client, interactive_auth="skip")
        self.assertEqual("", result)

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

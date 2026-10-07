"""Preflight: application-token Purview path, optional delegated enrichment and the evidence plan."""

import base64
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from Core.collector_registry import collector_permissions
from Core.connection_validation import (
    DELEGATED_OPTIONAL, NOT_SELECTED, PERMISSION, READY, SIGN_IN, connection_exit_code, run_connection_checks,
    _result,
)

SERVICES = {"run_m365": True, "run_entra": False, "run_defender": False, "run_purview": True,
            "run_power_platform": False, "run_copilot_studio": False}
PLAN = {"modules": {"ExchangeOnlineManagement": True, "Microsoft.Online.SharePoint.PowerShell": True,
                    "module_details": {"ExchangeOnlineManagement": {"version": "3.9.0"}}},
        "sharepoint": {"application_auth": False}, "purview": {"application_auth": False}}
ENV = {"CLIENT_ID": "app", "CLIENT_SECRET": "secret-value", "TENANT_ID": "tenant",
       "PURVIEW_ORGANIZATION": "contoso.onmicrosoft.com"}


def graph_token(roles):
    body = base64.urlsafe_b64encode(json.dumps({"roles": sorted(roles)}).encode()).decode().rstrip("=")
    return SimpleNamespace(token=f"h.{body}.s")


def client(missing=(), collectors=()):
    roles = set()
    for collector in ("graph_core", "m365_usage", "report_settings", "external_connections",
                      "sharepoint_tenant_settings", "purview_labels_graph", "copilot_audit", "shadow_ai", *collectors):
        roles |= collector_permissions(collector)
    roles -= set(missing)
    return SimpleNamespace(credential=SimpleNamespace(get_token=lambda *scopes: graph_token(roles)))


class PreflightTests(unittest.IsolatedAsyncioTestCase):
    async def check(self, graph_client, secrets, *, interactive_auth="skip", delegated="auto", delegated_state="unavailable", services=None, probe_endpoints=False):
        plan = {}
        with patch.dict(os.environ, ENV, clear=True), redirect_stdout(io.StringIO()), \
                patch("Core.workload_tokens.purview_token_secrets", AsyncMock(return_value=(secrets, {"reason": "not consented", "exchange_roles": set(), "compliance_roles": set()}))):
            results = await run_connection_checks(
                graph_client, services or SERVICES, PLAN, probe_endpoints=probe_endpoints, sharepoint_admin_url="",
                interactive_auth=interactive_auth, delegated=delegated, delegated_state=delegated_state,
                auth_plan_out=plan)
        return {item["collector_id"]: item for item in results}, plan

    async def test_application_token_makes_purview_ready_without_sign_in(self):
        results, plan = await self.check(client(), {"exchange_access_token": "e", "compliance_access_token": "c"})
        self.assertEqual(READY, results["purview"]["status"])
        self.assertIn("application token", results["purview"]["reason"])
        self.assertEqual(READY, results["sharepoint_tenant_settings"]["status"])
        self.assertEqual(READY, results["report_settings"]["status"])
        self.assertEqual("graph_sharepoint_settings", plan["datasets"]["sharepoint_tenant_settings"]["selected_path"])

    async def test_missing_application_path_explains_the_unlock_step(self):
        results, _ = await self.check(client(), {})
        self.assertEqual(SIGN_IN, results["purview"]["status"])
        self.assertIn("not consented", results["purview"]["reason"])

    async def test_new_graph_permissions_are_named_when_missing(self):
        results, _ = await self.check(client(missing={"SharePointTenantSettings.Read.All"}), {})
        self.assertEqual(PERMISSION, results["sharepoint_tenant_settings"]["status"])
        self.assertIn("SharePointTenantSettings.Read.All", results["sharepoint_tenant_settings"]["reason"])

    async def test_directory_recommendations_are_checked_by_default_and_missing_consent_is_named(self):
        collectors = ('entra_controls', 'entra_risk', 'entra_recommendations')
        services = {**SERVICES, 'run_entra': True}
        ready, _ = await self.check(client(collectors=collectors), {}, services=services)
        self.assertEqual(READY, ready['entra_recommendations']['status'])
        missing, _ = await self.check(client(missing={'DirectoryRecommendations.Read.All'}, collectors=collectors),
                                      {}, services=services)
        self.assertEqual(PERMISSION, missing['entra_recommendations']['status'])
        self.assertIn('DirectoryRecommendations.Read.All', missing['entra_recommendations']['reason'])

    async def test_directory_recommendations_preflight_uses_the_supported_read_only_endpoint(self):
        async def probe(graph_client, collector_id, path, roles, permissions, **kwargs):
            return _result(collector_id, READY, 'Synthetic representative read')
        with patch('Core.connection_validation._graph_probe', AsyncMock(side_effect=probe)) as reader:
            await self.check(client(collectors=('entra_controls', 'entra_risk', 'entra_recommendations')), {},
                             services={**SERVICES, 'run_m365': False, 'run_purview': False, 'run_entra': True},
                             probe_endpoints=True)
        call = next(call for call in reader.await_args_list if call.args[1] == 'entra_recommendations')
        self.assertEqual(call.args[2], '/beta/directory/recommendations')
        self.assertEqual(call.args[4], {'DirectoryRecommendations.Read.All'})
        self.assertEqual(call.kwargs['params'], {'$select': 'id,status'})

    async def test_optional_delegated_enrichment_does_not_fail_preflight(self):
        results, _ = await self.check(client(), {"exchange_access_token": "e"}, delegated="auto")
        self.assertEqual(DELEGATED_OPTIONAL, results["copilot_admin_settings"]["status"])
        self.assertNotEqual(SIGN_IN, results["copilot_admin_settings"]["status"])
        off, _ = await self.check(client(), {"exchange_access_token": "e"}, delegated="off")
        self.assertEqual(NOT_SELECTED, off["copilot_admin_settings"]["status"])
        required, _ = await self.check(client(), {"exchange_access_token": "e"}, delegated="required", delegated_state="can_prompt")
        self.assertEqual(SIGN_IN, required["copilot_admin_settings"]["status"])

    async def test_ready_application_paths_exit_zero(self):
        results, _ = await self.check(client(), {"exchange_access_token": "e", "compliance_access_token": "c"},
                                      delegated="off")
        # SharePoint administration needs an admin URL or certificate in this synthetic
        # tenant; everything reachable with application permissions must be ready.
        application_only = [item for name, item in results.items() if name != "sharepoint_governance"]
        self.assertEqual(0, connection_exit_code(application_only),
                         {item["collector_id"]: item["status"] for item in application_only})


if __name__ == "__main__":
    unittest.main()

"""Auth-path resolution and evidence provenance."""

import os
import unittest
from unittest.mock import patch

from Core.auth_plan import (
    CERTIFICATE_MISSING, DELEGATED_OFF, INTERACTIVE_DISABLED, MODULE_TOO_OLD, PERMISSION_MISSING,
    PROFILE_EXCLUDED, WORKLOAD_ROLE_MISSING, annotate_source_statuses, build_auth_context,
    collected_with_label, next_better_option, plan_rows, resolve_auth_plan,
)
from Core.collector_registry import selected_collector_ids, validate_registry

SECRET_ENV = {"TENANT_ID": "tenant", "CLIENT_ID": "app", "CLIENT_SECRET": "secret-value"}
ALL = {"run_m365": True, "run_entra": True, "run_defender": True, "run_purview": True}
MODULES = {
    "ExchangeOnlineManagement": True, "Microsoft.Online.SharePoint.PowerShell": True,
    "module_details": {"ExchangeOnlineManagement": {"version": "3.9.0"}},
}


def context(**overrides):
    options = dict(permission_profile="standard", interactive_auth="skip", delegated="off", modules=MODULES,
                   graph_roles=None, workload_roles={"exchange": {"Exchange.ManageAsApp"}, "eop": {"Exchange.ManageAsApp"}},
                   sharepoint_admin_url="", purview_organization="contoso.onmicrosoft.com")
    options.update(overrides)
    return build_auth_context(**options)


class RegistryTests(unittest.TestCase):
    def test_registry_is_consistent(self):
        self.assertEqual([], validate_registry())


@patch.dict(os.environ, SECRET_ENV, clear=True)
class ResolverTests(unittest.TestCase):
    def plan(self, **overrides):
        return resolve_auth_plan(selected_collector_ids(ALL), context(**overrides))

    def test_client_secret_alone_reaches_graph_and_purview_token_paths(self):
        plan = self.plan()
        datasets = plan["datasets"]
        self.assertEqual("graph_app", datasets["graph_core"]["selected_path"])
        self.assertEqual("graph_app_secret", datasets["graph_core"]["credential_type"])
        self.assertEqual("graph_sharepoint_settings", datasets["sharepoint_tenant_settings"]["selected_path"])
        self.assertEqual("partial", datasets["sharepoint_tenant_settings"]["coverage"])
        self.assertEqual("purview_ps_token", datasets["purview_policies"]["selected_path"])
        self.assertEqual("workload_app_token", datasets["purview_policies"]["credential_type"])
        # Labels prefer the complete PowerShell path; Graph beta remains a fallback.
        self.assertEqual("purview_ps_token", datasets["sensitivity_labels"]["selected_path"])
        self.assertIn("graph_sensitivity_labels", datasets["sensitivity_labels"]["fallbacks"])
        self.assertEqual({"id": "app", "type": "service_principal", "client_id": "app",
                          "credential_type": "client_secret", "directory_roles": []}, plan["identities"][0])

    def test_skipped_paths_explain_how_to_unlock(self):
        datasets = self.plan(sharepoint_admin_url="https://contoso-admin.sharepoint.com")["datasets"]
        skipped = {item["path"]: item for item in datasets["sharepoint_site_settings"]["skipped"]}
        self.assertEqual(CERTIFICATE_MISSING, skipped["spo_admin_certificate"]["reason_code"])
        self.assertIn("-EnableSharePointAppOnly", skipped["spo_admin_certificate"]["unlock"])
        self.assertEqual(INTERACTIVE_DISABLED, skipped["spo_admin_delegated"]["reason_code"])
        self.assertEqual("", datasets["sharepoint_site_settings"]["selected_path"])
        limited = {item["path"]: item for item in datasets["copilot_limited_mode"]["skipped"]}
        self.assertEqual(DELEGATED_OFF, limited["graph_delegated"]["reason_code"])

    def test_missing_graph_permission_names_the_permission(self):
        plan = self.plan(graph_roles={"Organization.Read.All"})
        entry = plan["datasets"]["sharepoint_tenant_settings"]
        reasons = {item["path"]: item for item in entry["skipped"]}
        self.assertEqual(PERMISSION_MISSING, reasons["graph_sharepoint_settings"]["reason_code"])
        # With nothing usable, the cheapest fix (the Graph baseline) is recommended first.
        self.assertIn("SharePointTenantSettings.Read.All", next_better_option(entry))
        self.assertNotIn("EnableSharePointAppOnly", next_better_option(entry))

    def test_exchange_role_and_module_version_gate_the_token_path(self):
        no_role = self.plan(workload_roles={"exchange": set(), "eop": set()})["datasets"]["purview_policies"]
        self.assertEqual(WORKLOAD_ROLE_MISSING, no_role["skipped"][0]["reason_code"])
        old_module = dict(MODULES, module_details={"ExchangeOnlineManagement": {"version": "3.7.2"}})
        too_old = self.plan(modules=old_module)["datasets"]["purview_policies"]
        self.assertEqual(MODULE_TOO_OLD, too_old["skipped"][0]["reason_code"])

    def test_interactive_run_falls_back_to_delegated_only_after_app_paths(self):
        plan = self.plan(interactive_auth="auto", workload_roles={"exchange": set(), "eop": set()})
        purview = plan["datasets"]["purview_policies"]
        self.assertEqual("purview_ps_delegated", purview["selected_path"])
        self.assertEqual("delegated_user", purview["credential_type"])

    def test_restricted_profile_excludes_administration_and_delegation(self):
        plan = resolve_auth_plan(selected_collector_ids(ALL, permission_profile="restricted"),
                                 context(permission_profile="restricted", delegated="auto"))
        self.assertNotIn("purview_policies", plan["datasets"])
        self.assertEqual("graph_sharepoint_settings", plan["datasets"]["sharepoint_tenant_settings"]["selected_path"])
        # Every remaining path is an application path.
        kinds = {entry["credential_type"] for entry in plan["datasets"].values() if entry["selected_path"]}
        self.assertTrue(kinds <= {"graph_app_secret", "graph_app_certificate"}, kinds)

    def test_profile_excluded_datasets_have_no_unlock_suggestion(self):
        plan = resolve_auth_plan(["purview"], context(permission_profile="restricted"))
        entry = plan["datasets"]["purview_policies"]
        self.assertTrue(all(item["reason_code"] == PROFILE_EXCLUDED for item in entry["skipped"]))
        self.assertEqual("", next_better_option(entry))

    def test_plan_rows_are_readable(self):
        rows = {row["Dataset"]: row for row in plan_rows(self.plan())}
        self.assertEqual("Microsoft Graph SharePoint tenant settings", rows["sharepoint_tenant_settings"]["Will use"])
        self.assertIn("EnableSharePointAppOnly", rows["sharepoint_tenant_settings"]["Next better option"])
        self.assertEqual("Not available", rows["sharepoint_dag_reports"]["Will use"])


class ProvenanceTests(unittest.TestCase):
    PLAN = {"identities": [{"id": "app", "type": "service_principal", "credential_type": "client_secret"}],
            "datasets": {"sharepoint_site_settings": {"selected_path": "", "skipped": [
                {"path": "spo_admin_certificate", "reason_code": CERTIFICATE_MISSING, "reason": "No certificate.",
                 "unlock": "Run setup -EnableSharePointAppOnly."}]}}}

    def test_graph_states_record_the_application_identity(self):
        states = {"m365_users": {"available": True, "availability_status": "available"}}
        annotate_source_statuses(states, self.PLAN)
        self.assertEqual("graph_app_secret", states["m365_users"]["credential_type"])
        self.assertEqual("graph_app", states["m365_users"]["auth_path_id"])
        self.assertEqual("Application permission (client secret)", collected_with_label(states["m365_users"]))

    def test_recorded_workload_provenance_is_never_overwritten(self):
        states = {"purview_dlp_policies": {"available": True, "credential_type": "workload_app_token",
                                           "auth_path_id": "purview_ps_token"}}
        annotate_source_statuses(states, self.PLAN)
        self.assertEqual("workload_app_token", states["purview_dlp_policies"]["credential_type"])
        self.assertEqual("Purview and Exchange PowerShell with application token",
                         collected_with_label(states["purview_dlp_policies"]))

    def test_older_collections_show_unrecorded_identity(self):
        states = {"m365_users": {"available": True}, "sharepoint_tenant_settings": {"available": True}}
        annotate_source_statuses(states, {})
        self.assertEqual("unrecorded", states["m365_users"]["credential_type"])
        self.assertEqual("Not recorded (older collection)", collected_with_label(states["m365_users"]))

    def test_legacy_sharepoint_authentication_maps_to_credential_type(self):
        states = {"sharepoint_site_settings": {"available": False, "availability_status": "unavailable",
                                               "authentication": "delegated_browser"}}
        annotate_source_statuses(states, self.PLAN)
        self.assertEqual("delegated_user", states["sharepoint_site_settings"]["credential_type"])

    def test_unavailable_dataset_gets_unlock_guidance(self):
        states = {"sharepoint_site_settings": {"available": False, "availability_status": "unavailable"}}
        annotate_source_statuses(states, self.PLAN)
        self.assertIn("EnableSharePointAppOnly", states["sharepoint_site_settings"]["unlock"])

    def test_imports_and_preflight_rows_are_classified_correctly(self):
        states = {"data_exposure_sam": {"availability_status": "available"},
                  "connection_graph_core": {"availability_status": "available"}}
        annotate_source_statuses(states, self.PLAN)
        self.assertEqual("offline_import", states["data_exposure_sam"]["credential_type"])
        self.assertNotIn("credential_type", states["connection_graph_core"])

    def test_partial_preview_label_is_visible(self):
        label = collected_with_label({"credential_type": "graph_app_secret", "auth_path_id": "graph_sensitivity_labels",
                                      "coverage": "partial", "evidence_quality": "preview"})
        self.assertEqual("Application permission (client secret) [preview] (partial coverage)", label)


if __name__ == "__main__":
    unittest.main()

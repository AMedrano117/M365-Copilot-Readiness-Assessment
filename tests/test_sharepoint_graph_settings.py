"""SharePoint tenant settings through Microsoft Graph and merge with SharePoint administration."""

import os
import unittest
from unittest.mock import patch

from Core.get_graph_client import GraphRequestError
from Core.sharepoint_governance import (
    build_sharepoint_recommendations, merge_sharepoint_payloads, summarize_sharepoint_governance,
)
from Core.sharepoint_graph_settings import (
    STATUS_KEY, UNAVAILABLE_FROM_GRAPH, collect_sharepoint_tenant_settings_graph, map_graph_settings,
)

GRAPH_RESPONSE = {
    "sharingCapability": "externalUserAndGuestSharing",
    "sharingDomainRestrictionMode": "allowList",
    "sharingAllowedDomainList": ["contoso.com", "fabrikam.com"],
    "isResharingByExternalUsersEnabled": True,
    "isLegacyAuthProtocolsEnabled": True,
    "isSiteCreationEnabled": False,
    "idleSessionSignOut": {"isEnabled": True, "warnAfterInSeconds": 120, "signOutAfterInSeconds": 300},
}


class FakeGraph:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.paths = payload, error, []

    async def get_json(self, path, params=None):
        self.paths.append(path)
        if self.error:
            raise self.error
        return self.payload


def keys(rows):
    return {row.get("FindingKey") for row in rows}


class MappingTests(unittest.TestCase):
    def test_graph_fields_map_to_sharepoint_admin_names_with_inversions(self):
        settings, sources = map_graph_settings(GRAPH_RESPONSE)
        self.assertEqual("ExternalUserAndGuestSharing", settings["SharingCapability"])
        self.assertEqual("AllowList", settings["SharingDomainRestrictionMode"])
        self.assertEqual("contoso.com fabrikam.com", settings["SharingAllowedDomainList"])
        self.assertIs(False, settings["PreventExternalUsersFromResharing"])
        self.assertIs(True, settings["SelfServiceSiteCreationDisabled"])
        self.assertIs(True, settings["LegacyAuthProtocolsEnabled"])
        self.assertEqual("isResharingByExternalUsersEnabled", sources["PreventExternalUsersFromResharing"])
        # Settings Graph does not return are never invented.
        self.assertFalse(set(UNAVAILABLE_FROM_GRAPH) & set(settings))

    def test_value_wrapper_is_accepted(self):
        settings, _ = map_graph_settings({"value": GRAPH_RESPONSE})
        self.assertEqual("ExternalUserAndGuestSharing", settings["SharingCapability"])


@patch.dict(os.environ, {"CLIENT_SECRET": "secret-value", "CLIENT_ID": "app", "TENANT_ID": "tenant"}, clear=True)
class CollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_is_partial_coverage_with_application_provenance(self):
        payload = await collect_sharepoint_tenant_settings_graph(FakeGraph(GRAPH_RESPONSE))
        self.assertTrue(payload["available"])
        self.assertEqual("partial", payload["availability_status"])
        self.assertEqual("partial", payload["tenant"]["coverage"])
        state = payload["collection_status"][STATUS_KEY]
        self.assertEqual("available", state["availability_status"])
        self.assertEqual("graph_app_secret", state["credential_type"])
        self.assertEqual("graph_sharepoint_settings", state["auth_path_id"])
        self.assertFalse(payload["sites"]["available"])
        self.assertIn("SharePoint administration", payload["sites"]["reason"])

    async def test_denied_request_names_the_permission(self):
        payload = await collect_sharepoint_tenant_settings_graph(FakeGraph(error=GraphRequestError(403, "denied")))
        self.assertFalse(payload["available"])
        state = payload["collection_status"][STATUS_KEY]
        self.assertEqual(403, state["status_code"])
        self.assertIn("SharePointTenantSettings.Read.All", state["reason"])


def spo_payload(settings=None, available=True):
    settings = settings or {"SharingCapability": "ExternalUserSharingOnly", "DefaultSharingLinkType": "Direct"}
    return {
        "source": "SharePoint Online Management Shell", "authentication": "application_certificate",
        "tenant": {"available": available, "settings": settings if available else {}, "reason": "" if available else "denied"},
        "sites": {"available": available, "items": [], "reason": ""},
        "dag_reports": {"available": available, "reports": [], "reason": ""},
        "dag_activity_data": {"available": False, "items": [], "reason": ""},
        "collection_status": {"sharepoint_tenant_settings": {"available": available}},
        "available": available,
    }


class MergeTests(unittest.IsolatedAsyncioTestCase):
    async def graph(self):
        with patch.dict(os.environ, {"CLIENT_SECRET": "x", "CLIENT_ID": "a", "TENANT_ID": "t"}, clear=True):
            return await collect_sharepoint_tenant_settings_graph(FakeGraph(GRAPH_RESPONSE))

    async def test_administration_is_authoritative_and_conflicts_are_recorded(self):
        merged = merge_sharepoint_payloads(await self.graph(), spo_payload())
        self.assertEqual("SharePoint Online Management Shell", merged["source"])
        self.assertEqual("ExternalUserSharingOnly", merged["tenant"]["settings"]["SharingCapability"])
        self.assertEqual([{"setting": "SharingCapability", "sharepoint_admin": "ExternalUserSharingOnly",
                           "graph": "ExternalUserAndGuestSharing"}], merged["conflicts"])
        self.assertIn(STATUS_KEY, merged["collection_status"])
        self.assertIn("sharepoint_tenant_settings", merged["collection_status"])

    async def test_graph_baseline_is_used_when_administration_failed(self):
        failed = {"available": False, "reason": "Browser sign-in was skipped.", "configuration_required": ""}
        merged = merge_sharepoint_payloads(await self.graph(), failed)
        self.assertTrue(merged["available"])
        self.assertEqual("partial", merged["availability_status"])
        self.assertEqual("Browser sign-in was skipped.", merged["administration_reason"])
        self.assertEqual("ExternalUserAndGuestSharing", merged["tenant"]["settings"]["SharingCapability"])

    async def test_restricted_not_requested_administration_keeps_graph_settings(self):
        restricted = {"available": False, "availability_status": "not_requested", "reason": "Restricted profile."}
        merged = merge_sharepoint_payloads(await self.graph(), restricted)
        self.assertTrue(merged["tenant"]["available"])


class RecommendationTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_graph_evidence_keeps_defaults_open_and_reports_anyone_sharing(self):
        with patch.dict(os.environ, {"CLIENT_SECRET": "x", "CLIENT_ID": "a", "TENANT_ID": "t"}, clear=True):
            graph = await collect_sharepoint_tenant_settings_graph(FakeGraph(GRAPH_RESPONSE))
        rows = build_sharepoint_recommendations(merge_sharepoint_payloads(graph, {"available": False}))
        found = keys(rows)
        self.assertIn("sharepoint.sharing.partial_settings", found)
        self.assertIn("sharepoint.sharing.anyone_enabled", found)
        # Default link type is unknown from Graph, so the anonymous-default finding cannot fire.
        self.assertNotIn("sharepoint.sharing.permissive_anonymous_defaults", found)
        anyone = next(row for row in rows if row["FindingKey"] == "sharepoint.sharing.anyone_enabled")
        self.assertIn("Per-site sharing settings were not collected", anyone["Observation"])
        self.assertNotIn("0 collected site", anyone["Observation"])
        partial = next(row for row in rows if row["FindingKey"] == "sharepoint.sharing.partial_settings")
        self.assertIn("DefaultSharingLinkType", partial["Observation"])
        self.assertEqual("Not Assessed", partial["Status"])

    def test_unavailable_governance_points_to_graph_permission_first(self):
        rows = build_sharepoint_recommendations({"available": False, "reason": "no access"})
        self.assertEqual(["sharepoint.governance.not_assessed"], [row["FindingKey"] for row in rows])
        self.assertIn("SharePointTenantSettings.Read.All", rows[0]["Recommendation"])

    def test_copilot_content_controls_are_reference_rows(self):
        payload = spo_payload({"SharingCapability": "ExistingExternalUserSharingOnly", "EnableRestrictedAccessControl": False})
        payload["sites"]["items"] = [{"Url": "https://contoso.sharepoint.com/sites/hr", "RestrictContentOrgWideSearch": True},
                                     {"Url": "https://contoso.sharepoint.com/sites/all", "RestrictContentOrgWideSearch": False}]
        payload["copilot_content_controls"] = {"available": True, "restricted_search_mode": "Enabled",
                                               "restricted_search_allowed_sites": 12}
        rows = build_sharepoint_recommendations(payload)
        by_key = {row["FindingKey"]: row for row in rows}
        self.assertIn("with 12 allowed site(s)", by_key["sharepoint.copilot.restricted_search"]["Observation"])
        self.assertIn("1 of 2 collected site(s)", by_key["sharepoint.copilot.restricted_content_discovery"]["Observation"])
        self.assertIn("not enabled", by_key["sharepoint.copilot.restricted_access_control"]["Observation"])
        for key in ("sharepoint.copilot.restricted_search", "sharepoint.copilot.restricted_content_discovery",
                    "sharepoint.copilot.restricted_access_control"):
            self.assertEqual("Reference", by_key[key]["Disposition"])

    def test_absent_control_data_produces_no_control_rows(self):
        rows = build_sharepoint_recommendations(spo_payload())
        self.assertFalse({key for key in keys(rows) if key.startswith("sharepoint.copilot.")})
        self.assertEqual({}, summarize_sharepoint_governance(spo_payload())["copilot_content_controls"])


if __name__ == "__main__":
    unittest.main()

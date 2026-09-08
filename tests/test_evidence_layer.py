import unittest
from types import SimpleNamespace

from Core.evidence_layer import (
    _build_app_access_sheet,
    _build_app_consent_policy_sheet,
    _build_authentication_sheet,
    _build_identity_risk_sheet,
    _apply_explicit_evidence_fallbacks,
    _build_entra_license_context,
    _build_purview_policy_summary,
    build_evidence_bundle,
)
from Core.get_entra_client import _apply_authorization_policy, _get_attr


class EvidenceLayerTests(unittest.TestCase):
    def test_entra_p1_context_explains_p2_only_capabilities(self):
        context = _build_entra_license_context({"licenses": [{
            "sku_part_number": "SPE_E3",
            "service_plans": [{"name": "AAD_PREMIUM", "status": "Success"}],
        }]})

        self.assertEqual(context["detected_tier"], "Microsoft Entra ID P1")
        risk = next(row for row in context["rows"] if row["Capability"].startswith("Full Identity Protection"))
        self.assertEqual(risk["P1"], "Not included")
        self.assertIn("detected P1", risk["Tenant"])

    def test_purview_dlp_summary_includes_mode_and_locations(self):
        client = SimpleNamespace(dlp_policies={
            "available": True,
            "policies": [{
                "Name": "Protect financial data", "Enabled": True, "Mode": "Enable",
                "ExchangeLocation": ["All"], "SharePointLocation": ["All"], "OneDriveLocation": [],
            }],
        })

        summary = _build_purview_policy_summary(client)

        self.assertTrue(summary["available"])
        self.assertEqual(summary["enabled"], 1)
        self.assertEqual(summary["rows"][0]["Mode"], "Enable")
        self.assertEqual(summary["rows"][0]["Locations"], "Exchange, SharePoint")

    def test_entra_fallback_uses_measured_condition_not_remediation_wording(self):
        rows = [
            {"Service": "Entra", "Feature": "Microsoft Entra ID P1", "Observation": "Only 7 of 10 users were enrolled in MFA.", "Recommendation": "Use Conditional Access."},
            {"Service": "Entra", "Feature": "Microsoft Entra ID P2", "Observation": "Two risky users were detected.", "Recommendation": "Use risk-based Conditional Access."},
            {"Service": "Entra", "Feature": "Microsoft Entra ID P1", "Observation": "Five Conditional Access policies require MFA.", "Recommendation": "Maintain them."},
        ]
        sheets = {
            "authentication_detail": {},
            "identity_risk_detail": {},
            "conditional_access_detail": {},
        }

        resolved = _apply_explicit_evidence_fallbacks(rows, sheets)

        self.assertEqual(resolved[0]["EvidenceKey"], "authentication_detail")
        self.assertEqual(resolved[1]["EvidenceKey"], "identity_risk_detail")
        self.assertEqual(resolved[2]["EvidenceKey"], "conditional_access_detail")

    def test_authorization_policy_is_source_of_effective_user_consent_boundary(self):
        client = SimpleNamespace(
            authorization_policy={},
            auth_policy_summary={},
            b2b_summary={},
            consent_summary={
                "consent_configuration_available": False,
                "assigned_user_consent_policies": [],
                "user_consent_allowed": False,
                "admin_consent_required": False,
            },
        )
        policy = {
            "allowInvitesFrom": "adminsAndGuestInviters",
            "defaultUserRolePermissions": {
                "allowedToCreateApps": False,
                "permissionGrantPoliciesAssigned": [
                    "managePermissionGrantsForSelf.microsoft-user-default-low"
                ],
            },
        }

        _apply_authorization_policy(client, policy)

        self.assertTrue(client.consent_summary["consent_configuration_available"])
        self.assertTrue(client.consent_summary["user_consent_allowed"])
        self.assertFalse(client.consent_summary["admin_consent_required"])

    def test_empty_default_user_consent_assignments_are_evidence_of_admin_boundary(self):
        client = SimpleNamespace(
            authorization_policy={
                "defaultUserRolePermissions": {"permissionGrantPoliciesAssigned": []}
            },
            collection_status={
                "authorization_policy": {"availability_status": "available"}
            },
        )

        sheet = _build_app_consent_policy_sheet(client)

        self.assertEqual(sheet["rows"][0]["Value"], "No")
        self.assertIn("requires an administrator", sheet["summary"])

    def test_identity_action_evidence_uses_aggregate_auth_and_risk_details(self):
        client = SimpleNamespace(
            collection_status={
                "auth_methods": {"availability_status": "available"},
                "risky_users": {"availability_status": "available"},
                "risk_detections": {"availability_status": "available"},
            },
            auth_summary={
                "total_users": 10, "mfa_registered": 7, "mfa_capable": 8,
                "passwordless_enabled": 2,
            },
            risky_users=[{
                "id": "user-object", "userDisplayName": "Test User",
                "userPrincipalName": "test@example.com", "riskLevel": "medium",
                "riskState": "atRisk", "riskLastUpdatedDateTime": "2026-09-01",
            }],
            risk_detections=[],
        )

        auth = _build_authentication_sheet(client)
        risk = _build_identity_risk_sheet(client)

        self.assertEqual(auth["rows"][1]["Value"], 7)
        self.assertEqual(risk["rows"][0]["Object ID"], "user-object")
        self.assertNotIn("User Principal Name", risk["preview_columns"] if "preview_columns" in risk else [])

    def test_graph_field_reader_handles_http_and_sdk_shapes(self):
        self.assertTrue(_get_attr({"isMfaRegistered": True}, "isMfaRegistered", False))
        self.assertEqual(
            _get_attr(SimpleNamespace(is_mfa_registered=True), "isMfaRegistered", False),
            True,
        )

    def test_microsoft_owner_tenant_is_first_party_without_publisher_name(self):
        entra_client = SimpleNamespace(
            tenant_id="tenant-local",
            service_principals=[{
                "id": "sp-ms",
                "appId": "08e18876-6177-487e-b8b5-cf950c1e598c",
                "displayName": "SharePoint Online Web Client Extensibility",
                "publisherName": "",
                "appOwnerOrganizationId": "f8cdef31-a31e-4b4a-93e4-5f571e91255a",
            }],
            oauth_permission_grants=[{
                "clientId": "sp-ms",
                "consentType": "AllPrincipals",
                "scope": "Files.ReadWrite.All",
            }],
            app_activity_summary={"available": False, "by_app": {}},
        )

        row = _build_app_access_sheet(entra_client, SimpleNamespace(oauth_apps=[]))["rows"][0]

        self.assertEqual(row["Publisher Type"], "Microsoft first-party")
        self.assertNotIn("Unverified publisher", row["Flagged Because"])
        self.assertIn("Microsoft service tenant", row["Publisher Classification Basis"])

    def test_app_access_sheet_preserves_flag_count_and_activity_band(self):
        entra_client = SimpleNamespace(
            tenant_id="tenant-local",
            service_principals=[
                {
                    "id": "sp-1",
                    "appId": "app-1",
                    "displayName": "Risky App",
                    "publisherName": "",
                    "appOwnerOrganizationId": "tenant-external",
                }
            ],
            oauth_permission_grants=[
                {
                    "clientId": "sp-1",
                    "consentType": "AllPrincipals",
                    "scope": "Mail.ReadWrite Files.Read User.Read",
                }
            ],
            app_activity_summary={
                "available": True,
                "by_app": {
                    "app-1": {
                        "activity_count": 3,
                        "last_activity": "2026-04-01T10:00:00Z",
                    }
                },
            },
        )
        defender_client = SimpleNamespace(
            oauth_apps=[[{"clientId": "app-1", "scope": "Mail.ReadWrite Files.ReadWrite.All"}]]
        )

        sheet = _build_app_access_sheet(entra_client, defender_client)

        self.assertIsNotNone(sheet)
        self.assertEqual(len(sheet["rows"]), 1)
        row = sheet["rows"][0]
        self.assertEqual(row["App Display Name"], "Risky App")
        self.assertEqual(row["Flag Instance Count"], 2)
        self.assertEqual(row["Activity Band"], "Low")
        self.assertIn("High-privilege delegated permissions", row["Flagged Because"])
        self.assertIn("Unverified publisher", row["Flagged Because"])

    def test_app_access_sheet_marks_activity_unavailable(self):
        entra_client = SimpleNamespace(
            tenant_id="tenant-local",
            service_principals=[
                {
                    "id": "sp-1",
                    "appId": "app-1",
                    "displayName": "Risky App",
                    "publisherName": "",
                    "appOwnerOrganizationId": "tenant-external",
                }
            ],
            oauth_permission_grants=[
                {"clientId": "sp-1", "consentType": "AllPrincipals", "scope": "Mail.ReadWrite"}
            ],
            app_activity_summary={"available": False, "by_app": {}, "reason": "Forbidden"},
        )
        defender_client = SimpleNamespace(oauth_apps=[])

        sheet = _build_app_access_sheet(entra_client, defender_client)
        row = sheet["rows"][0]

        self.assertEqual(row["Activity Band"], "Unavailable")
        self.assertEqual(row["Activity Count (30d)"], "")

    def test_blank_publisher_without_a_grant_is_not_flagged(self):
        entra_client = SimpleNamespace(
            tenant_id="tenant-local",
            service_principals=[{
                "id": "sp-unused",
                "appId": "app-unused",
                "displayName": "Unused Local Object",
                "publisherName": "",
                "appOwnerOrganizationId": "tenant-local",
            }],
            oauth_permission_grants=[],
            app_activity_summary={"available": False, "by_app": {}},
        )

        sheet = _build_app_access_sheet(entra_client, SimpleNamespace(oauth_apps=[]))
        self.assertEqual(sheet["rows"], [])

    def test_routine_graph_access_alone_is_not_flagged(self):
        entra_client = SimpleNamespace(
            tenant_id="tenant-local",
            service_principals=[{
                "id": "sp-routine",
                "appId": "app-routine",
                "displayName": "Routine Graph Client",
                "publisherName": "Internal Publisher",
                "appOwnerOrganizationId": "tenant-local",
            }],
            oauth_permission_grants=[{
                "clientId": "sp-routine",
                "consentType": "Principal",
                "scope": "User.Read openid profile",
            }],
            app_activity_summary={"available": True, "by_app": {}},
        )

        sheet = _build_app_access_sheet(entra_client, SimpleNamespace(oauth_apps=[]))

        self.assertEqual(sheet["rows"], [])

    def test_build_evidence_bundle_links_multi_sheet_recommendation(self):
        recommendations = [
            {
                "Service": "Defender",
                "Feature": "Copilot Security Posture",
                "Status": "High",
                "Priority": "High",
                "Observation": "Risky apps and devices detected.",
                "Recommendation": "Follow up with engineering.",
                "LinkText": "",
                "LinkUrl": "",
                "RecommendationId": "",
                "EvidenceKey": "",
                "EvidenceSummary": "",
                "EvidenceSheet": "",
                "EvidenceAvailable": "No",
            }
        ]

        entra_info = {
            "_client": SimpleNamespace(
                tenant_id="tenant-local",
                service_principals=[
                    {
                        "id": "sp-1",
                        "appId": "app-1",
                        "displayName": "Risky App",
                        "publisherName": "",
                        "appOwnerOrganizationId": "tenant-external",
                    }
                ],
                oauth_permission_grants=[
                    {
                        "clientId": "sp-1",
                        "consentType": "AllPrincipals",
                        "scope": "Mail.ReadWrite Files.Read",
                    }
                ],
                app_activity_summary={"available": False, "by_app": {}, "reason": "Forbidden"},
            ),
            "recommendations": [],
        }
        defender_info = {
            "_client": SimpleNamespace(
                oauth_apps=[],
                security_incidents=[],
                defender_incidents=[],
                incident_summary={},
                defender_devices=[
                    {
                        "deviceName": "PC-01",
                        "riskScore": "High",
                        "exposureLevel": "High",
                        "healthStatus": "Active",
                    }
                ],
                device_summary={"total": 1, "high_risk": 1},
            ),
            "recommendations": [],
        }

        bundle = build_evidence_bundle(
            recommendations,
            ({}, []),
            entra_info,
            {},
            defender_info,
            {},
            {},
        )

        enriched = bundle["recommendations"][0]
        self.assertEqual(enriched["EvidenceAvailable"], "Yes")
        self.assertIn("App Access Detail", enriched["EvidenceSheet"])
        self.assertIn("Defender Device Detail", enriched["EvidenceSheet"])

        app_rows = bundle["sheets"]["app_access_detail"]["rows"]
        device_rows = bundle["sheets"]["defender_device_detail"]["rows"]
        self.assertIn("DEF-001", app_rows[0]["RecommendationId"])
        self.assertIn("DEF-001", device_rows[0]["RecommendationId"])


if __name__ == "__main__":
    unittest.main()

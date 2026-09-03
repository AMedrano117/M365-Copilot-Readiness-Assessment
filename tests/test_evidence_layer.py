import unittest
from types import SimpleNamespace

from Core.evidence_layer import _build_app_access_sheet, build_evidence_bundle


class EvidenceLayerTests(unittest.TestCase):
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

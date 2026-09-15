import unittest

from Core.sharepoint_governance import (
    build_sharepoint_recommendations,
    summarize_sharepoint_governance,
)


class SharePointGovernanceTests(unittest.TestCase):
    def test_permissive_anonymous_defaults_create_one_combined_action(self):
        payload = {
            "source": "SharePoint Online Management Shell",
            "tenant": {
                "available": True,
                "settings": {
                    "SharingCapability": "ExternalUserAndGuestSharing",
                    "OneDriveSharingCapability": "ExternalUserAndGuestSharing",
                    "DefaultSharingLinkType": "AnonymousAccess",
                    "FileAnonymousLinkType": "Edit",
                    "FolderAnonymousLinkType": "Edit",
                    "RequireAnonymousLinksExpireInDays": 0,
                    "LegacyAuthProtocolsEnabled": False,
                },
            },
            "sites": {"available": True, "items": []},
            "dag_reports": {"available": True, "reports": [{"Status": "Completed"}]},
        }
        recommendations = build_sharepoint_recommendations(payload)
        actions = [row for row in recommendations if row.get("FindingKey") == "sharepoint.sharing.permissive_anonymous_defaults"]
        self.assertEqual(1, len(actions))
        self.assertEqual("High", actions[0]["Priority"])
        self.assertIn("no tenant-wide expiration", actions[0]["Observation"])

    def test_missing_collection_is_coverage_not_clean_or_zero(self):
        recommendations = build_sharepoint_recommendations({"available": False, "reason": "module missing"})
        self.assertEqual(1, len(recommendations))
        self.assertEqual("Scan Coverage", recommendations[0]["Category"])
        self.assertEqual("Not Assessed", recommendations[0]["Status"])

    def test_report_inventory_counts_completed_and_running(self):
        summary = summarize_sharepoint_governance({
            "tenant": {"available": True, "settings": {}},
            "sites": {"available": True, "items": [
                {"SharingCapability": "ExternalUserAndGuestSharing"},
                {"SharingCapability": "ExternalUserSharingOnly"},
            ]},
            "dag_reports": {"available": True, "reports": [
                {"Status": "Completed"}, {"Status": "InProgress"},
            ]},
        })
        self.assertEqual(2, summary["site_count"])
        self.assertEqual(1, summary["anyone_site_count"])
        self.assertEqual(1, summary["dag_completed_count"])
        self.assertEqual(1, summary["dag_running_count"])


if __name__ == "__main__":
    unittest.main()

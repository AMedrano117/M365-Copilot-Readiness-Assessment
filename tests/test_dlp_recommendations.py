import unittest
from types import SimpleNamespace

from Recommendations.purview.COMMUNICATIONS_DLP import get_recommendation


class DlpRecommendationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_rule_access_is_coverage_not_zero_rules(self):
        client = SimpleNamespace(
            dlp_policies={"available": True, "policies": [{
                "Name": "Protect financial data", "Mode": "Enable",
            }]},
            dlp_rules={"available": False, "rules": []},
            collection_status={"dlp_rules": {
                "available": False,
                "reason": "The signed-in user is not authorized.",
                "required_role": "View-Only DLP Compliance Management",
            }},
        )

        rows = await get_recommendation("ENTERPRISEPACK", purview_client=client)
        finding = rows[1]

        self.assertEqual(finding["Disposition"], "Coverage")
        self.assertEqual(finding["Status"], "Not Assessed")
        self.assertIn("rule conditions and actions were not assessed", finding["Observation"])
        self.assertIn("View-Only DLP Compliance Management", finding["Recommendation"])

    async def test_successful_empty_policy_collection_is_an_action(self):
        client = SimpleNamespace(
            dlp_policies={"available": True, "policies": []},
            dlp_rules={"available": True, "rules": []},
            collection_status={},
        )

        rows = await get_recommendation("ENTERPRISEPACK", purview_client=client)
        finding = rows[1]

        self.assertEqual(finding["Disposition"], "")
        self.assertEqual(finding["Status"], "Action Required")
        self.assertIn("zero DLP policies", finding["Observation"])

    async def test_enforced_policy_and_rule_are_reported_as_reference(self):
        client = SimpleNamespace(
            dlp_policies={"available": True, "policies": [{
                "Name": "Protect financial data", "Mode": "Enable",
                "SharePointLocation": ["All"], "OneDriveLocation": ["All"],
            }]},
            dlp_rules={"available": True, "rules": [{
                "Name": "Block financial records", "Disabled": False,
                "BlockAccess": True,
            }]},
            collection_status={},
        )

        rows = await get_recommendation("ENTERPRISEPACK", purview_client=client)
        finding = rows[1]

        self.assertEqual(finding["Disposition"], "Reference")
        self.assertEqual(finding["EvidenceBasis"], "Tenant evidence")
        self.assertIn("1 enabled DLP policies and 1 enabled rules", finding["Observation"])


if __name__ == "__main__":
    unittest.main()

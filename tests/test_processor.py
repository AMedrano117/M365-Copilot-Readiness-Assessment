import unittest

from Core.processor import reconcile_overlapping_evidence


class ProcessorEvidenceReconciliationTests(unittest.TestCase):
    def test_entra_risk_evidence_prevents_duplicate_defender_coverage_gap(self):
        rows = reconcile_overlapping_evidence([
            {
                "Service": "Entra",
                "Observation": "7 risky users detected in the tenant (0 high-risk, 7 medium-risk)",
                "Status": "Action Required",
            },
            {
                "Service": "Defender",
                "Observation": (
                    "Microsoft Defender for Identity is active. Identity risk data could not be "
                    "retrieved, so account risk is unverified"
                ),
                "Recommendation": "Grant additional permissions.",
                "Status": "Not Assessed",
                "Disposition": "Coverage",
                "Priority": "Medium",
            },
        ])

        defender = rows[1]
        self.assertEqual(defender["Disposition"], "Reference")
        self.assertEqual(defender["Status"], "Reference")
        self.assertEqual(defender["Recommendation"], "")
        self.assertIn("collected through Entra ID Protection", defender["Observation"])

    def test_unread_entra_risk_keeps_defender_coverage_gap(self):
        rows = reconcile_overlapping_evidence([
            {
                "Service": "Entra",
                "Observation": "Identity Protection risk data could not be retrieved",
                "Status": "Not Assessed",
            },
            {
                "Service": "Defender",
                "Observation": "Identity risk data could not be retrieved",
                "Disposition": "Coverage",
            },
        ])

        self.assertEqual(rows[1]["Disposition"], "Coverage")


if __name__ == "__main__":
    unittest.main()

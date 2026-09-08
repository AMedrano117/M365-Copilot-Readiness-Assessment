import json
import os
import tempfile
import unittest
from pathlib import Path

from Core.cross_provider_assessment import (
    METHODOLOGY_VERSION,
    assess_provider_and_use_cases,
    compare_baseline,
    evaluate_controls,
    load_assessment_profile,
    load_provider_evidence,
    run_integrity_checks,
)
from Core.new_recommendation import new_recommendation


class CrossProviderAssessmentTests(unittest.TestCase):
    def test_missing_profile_leaves_provider_and_use_case_not_assessed(self):
        result = assess_provider_and_use_cases(
            load_assessment_profile(None), load_provider_evidence(None),
            "Ready for a controlled pilot",
        )
        self.assertEqual(result["provider_conclusion"], "Not assessed")
        self.assertEqual(result["use_case_conclusion"], "Not assessed")

    def test_matching_current_approved_provider_can_enable_use_case_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp) / "profile.json"
            provider_path = Path(tmp) / "providers.csv"
            profile_path.write_text(json.dumps({
                "version": "1.0",
                "ai_products": [{"provider": "Vendor", "product": "AI", "tier": "Enterprise"}],
                "use_cases": [{
                    "name": "Summaries", "business_owner": "Operations",
                    "provider": "Vendor", "product": "AI", "tier": "Enterprise",
                    "intended_users": ["Pilot"], "data_classifications": ["Internal"],
                    "approved_data": ["Internal"], "prohibited_data": ["Restricted"],
                    "required_outcome": "Reduce rework", "risk_measurements": ["Errors"],
                    "expand_stop_decision": "Expand if accepted",
                }],
            }), encoding="utf-8")
            provider_path.write_text(
                "Provider,Product,Tier,Owner,Approval State,Enterprise Identity and Lifecycle Controls,Training Use and Retention Terms,Residency Deletion Subprocessors and Privacy Review,Audit Investigation and Incident Response Capabilities,Connectors OAuth Applications MCP Servers Agents and Action Permissions,Data Egress Controls and Approved Classifications,Evidence Link,Reviewer,Review Date\n"
                "Vendor,AI,Enterprise,Owner,Approved,Reviewed,Reviewed,Reviewed,Reviewed,Reviewed,Reviewed,https://evidence.invalid/review,Reviewer,2099-01-01\n",
                encoding="utf-8",
            )
            result = assess_provider_and_use_cases(
                load_assessment_profile(profile_path), load_provider_evidence(provider_path),
                "Ready for a controlled pilot",
            )
        self.assertEqual(result["provider_conclusion"], "Approved")
        self.assertEqual(result["use_case_conclusion"], "Ready")

    def test_write_capable_use_case_requires_action_control(self):
        profile = {
            "available": True,
            "products": [],
            "use_cases": [{
                "name": "Update records", "business_owner": "Operations",
                "provider": "Vendor", "product": "AI", "tier": "Enterprise",
                "intended_users": ["Pilot"], "data_classifications": ["Internal"],
                "approved_data": ["Internal"], "prohibited_data": ["Restricted"],
                "required_outcome": "Reduce rework", "risk_measurements": ["Bad writes"],
                "expand_stop_decision": "Stop after a bad write", "can_write": True,
                "human_approval_required": True,
            }],
        }
        result = assess_provider_and_use_cases(profile, {"rows": []}, "Ready for a controlled pilot")
        self.assertIn("ACTIONS-001", result["use_cases"][0]["Applicable Controls"])

    def test_license_signal_cannot_pass_control(self):
        recommendation = new_recommendation(
            "M365", "Licensed capability", "The service plan is licensed.", status="Success",
        )
        recommendation.update({"Disposition": "Assurance", "EvidenceBasis": "License signal", "RecommendationId": "M365-001"})
        _, controls = evaluate_controls([recommendation])
        matching = next(row for row in controls if row["Control ID"] == "ADOPTION-001")
        self.assertEqual(matching["Result"], "not_assessed")

    def test_fingerprint_survives_wording_change(self):
        first = new_recommendation("Entra", "Conditional Access", "Old wording", "Fix it", finding_key="entra.ca.coverage")
        second = new_recommendation("Entra", "Conditional Access", "New wording", "Fix it differently", finding_key="entra.ca.coverage")
        first.update({"RecommendationId": "ENT-001", "Disposition": "Action"})
        second.update({"RecommendationId": "ENT-001", "Disposition": "Action"})
        enriched_first, _ = evaluate_controls([first])
        enriched_second, _ = evaluate_controls([second])
        self.assertEqual(enriched_first[0]["FindingFingerprint"], enriched_second[0]["FindingFingerprint"])

    def test_methodology_change_is_not_comparable(self):
        with tempfile.TemporaryDirectory() as tmp:
            baseline = Path(tmp) / "baseline.json"
            baseline.write_text(json.dumps({"methodology_version": "1.0", "recommendations": []}), encoding="utf-8")
            result = compare_baseline([], [], baseline)
        self.assertFalse(result["comparable"])
        self.assertEqual(result["rows"][0]["Classification"], "Unable to compare")

    def test_integrity_gate_detects_guid_in_name_field(self):
        bundle = {"sheets": {"apps": {"title": "Apps", "rows": [{"App Display Name": "11111111-1111-1111-1111-111111111111"}]}}, "source_statuses": {}}
        result = run_integrity_checks([], bundle)
        self.assertFalse(result["valid"])
        self.assertIn("do not use for deployment approval", result["banner"])


if __name__ == "__main__":
    unittest.main()

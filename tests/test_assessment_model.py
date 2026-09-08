"""Tests for the decision-oriented AI readiness classification layer."""

import os
import pathlib
import tempfile
import unittest

from Core.assessment_model import (
    DISPOSITION_ACTION,
    DISPOSITION_ASSURANCE,
    DISPOSITION_COVERAGE,
    DISPOSITION_OPPORTUNITY,
    enrich_assessment_record,
    summarize_readiness,
)
from Core.export_recommendations import export_to_html
from Core.new_recommendation import NOT_ASSESSED_STATUS, new_recommendation


class DispositionTests(unittest.TestCase):
    def test_healthy_license_observation_is_assurance_not_a_finding(self):
        rec = new_recommendation(
            "M365", "Exchange Online",
            "Exchange Online is active in Microsoft 365 E3.",
            status="Success",
        )
        enriched = enrich_assessment_record(rec)
        self.assertEqual(enriched["Disposition"], DISPOSITION_ASSURANCE)
        self.assertEqual(enriched["EvidenceBasis"], "License signal")
        self.assertEqual(enriched["Confidence"], "Low")

    def test_positive_deployment_idea_is_an_opportunity(self):
        rec = new_recommendation(
            "Copilot Studio", "Agent creation",
            "SharePoint knowledge sources are available for a pilot.",
            "Identify one bounded use case and measure its outcome.",
            priority="Medium", status="Success",
        )
        enriched = enrich_assessment_record(rec)
        self.assertEqual(enriched["Status"], "Attention Required")
        self.assertEqual(enriched["Disposition"], DISPOSITION_OPPORTUNITY)

    def test_absent_control_is_an_action_even_if_legacy_module_said_success(self):
        rec = new_recommendation(
            "Purview", "Endpoint DLP",
            "DLP policies exist but none target managed endpoints.",
            "Add tested endpoint coverage for sensitive data egress.",
            priority="High", status="Success",
        )
        enriched = enrich_assessment_record(rec)
        self.assertEqual(enriched["Disposition"], DISPOSITION_ACTION)
        self.assertEqual(enriched["ReadinessStage"], "Before broad rollout")

    def test_unread_data_is_coverage_not_a_tenant_failure(self):
        rec = new_recommendation(
            "Defender", "Incidents",
            "Incident data could not be retrieved.",
            "Grant permission and rerun.",
            priority="Medium", status=NOT_ASSESSED_STATUS,
        )
        enriched = enrich_assessment_record(rec)
        self.assertEqual(enriched["Disposition"], DISPOSITION_COVERAGE)
        self.assertEqual(enriched["EvidenceBasis"], "Not verified")
        self.assertEqual(enriched["Confidence"], "Unknown")

    def test_legacy_warning_wording_cannot_turn_unread_data_into_an_action(self):
        record = {
            "Service": "Defender",
            "Feature": "Data governance",
            "Status": "Warning",
            "SourceStatus": "Warning",
            "Priority": "High",
            "Observation": "Unable to assess data governance because Purview data was not available.",
            "Recommendation": "Rerun the collector.",
            "Category": "Tenant Finding",
        }
        self.assertEqual(enrich_assessment_record(record)["Disposition"], DISPOSITION_COVERAGE)

    def test_m365_usage_warning_is_an_opportunity_not_a_security_gate(self):
        record = {
            "Service": "M365",
            "Feature": "Teams activity",
            "Status": "Warning",
            "SourceStatus": "Warning",
            "Priority": "Medium",
            "Observation": "Low Teams activity was observed.",
            "Recommendation": "Run a measured collaboration pilot.",
            "Category": "Tenant Finding",
        }
        self.assertEqual(enrich_assessment_record(record)["Disposition"], DISPOSITION_OPPORTUNITY)

    def test_user_consent_is_classified_as_connected_app_security(self):
        record = new_recommendation(
            "Entra", "Application consent",
            "User consent is enabled for applications.",
            "Apply a risk-based permission grant policy.",
            priority="High", status="Action Required",
        )
        enriched = enrich_assessment_record(record)
        self.assertEqual(enriched["ImpactArea"], "Apps, connectors & agents")
        self.assertEqual(enriched["AIApplicability"], "AI agents and connected apps")


class ReadinessDecisionTests(unittest.TestCase):
    def test_high_action_blocks_broad_rollout_but_not_a_bounded_pilot(self):
        action = new_recommendation(
            "Entra", "MFA", "Only 40 of 100 users are registered for MFA.",
            "Enforce MFA.", priority="High", status="Action Required",
        )
        summary = summarize_readiness([action])
        self.assertEqual(summary["decision"], "Pilot only — remediation required")

    def test_coverage_only_never_produces_a_ready_claim(self):
        gap = new_recommendation(
            "Purview", "DLP", "DLP data could not be retrieved.",
            "Run the collector.", priority="Medium", status=NOT_ASSESSED_STATUS,
        )
        self.assertEqual(summarize_readiness([gap])["decision"], "Assessment incomplete")

    def test_optional_power_platform_coverage_does_not_block_core_readiness(self):
        gap = new_recommendation(
            "Power Platform", "Inventory", "Optional inventory was not supplied.",
            "Supply an export when extensibility is in scope.", priority="Medium",
            status=NOT_ASSESSED_STATUS,
        )
        summary = summarize_readiness([gap])
        self.assertEqual(summary["decision"], "Ready for a controlled pilot")
        self.assertEqual(len(summary["optional_coverage"]), 1)


class ReportLaneTests(unittest.TestCase):
    def test_html_only_renders_actions_as_primary_cards(self):
        records = [
            new_recommendation("Entra", "MFA", "Low MFA coverage.", "Enforce MFA.",
                               priority="High", status="Action Required"),
            new_recommendation("M365", "Exchange", "Exchange is active.", status="Success"),
            new_recommendation("M365", "Pilot", "A pilot group is available.",
                               "Run a measured pilot.", priority="Medium", status="Success"),
            new_recommendation("Purview", "DLP", "DLP data could not be retrieved.",
                               "Rerun collection.", priority="Medium", status=NOT_ASSESSED_STATUS),
        ]

        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                body = pathlib.Path(export_to_html(records, tenant_name="Contoso")).read_text(
                    encoding="utf-8"
                )
            finally:
                os.chdir(original_cwd)

        self.assertEqual(body.count('<article class="recommendation-card"'), 1)
        self.assertIn("Prioritized adoption &amp; value opportunities (1)", body)
        self.assertIn('<div class="label">Verified Strengths</div>', body)
        self.assertNotIn("Verified controls &amp; available capabilities", body)
        self.assertIn("Scan Coverage (1)", body)
        self.assertIn("Three readiness conclusions", body)


class RecommendationQualityGuardTests(unittest.TestCase):
    def test_priority_never_uses_status_vocabulary(self):
        repo_root = pathlib.Path(__file__).resolve().parent.parent
        offenders = []
        for path in (repo_root / "Recommendations").rglob("*.py"):
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if 'priority="Critical"' in line:
                    offenders.append(f"{path.name}:{line_number}")
        self.assertEqual(offenders, [], f"Critical is a status; priority must be High: {offenders}")

    def test_no_case_is_not_treated_as_an_ediscovery_failure(self):
        repo_root = pathlib.Path(__file__).resolve().parent.parent
        offenders = []
        for name in ("DATA_INVESTIGATIONS.py", "EDISCOVERY.py", "EQUIVIO_ANALYTICS.py",
                     "PURVIEW_DISCOVERY.py"):
            source = (repo_root / "Recommendations" / "purview" / name).read_text(encoding="utf-8")
            if "Create eDiscovery cases to prepare" in source or "NO eDiscovery cases" in source:
                offenders.append(name)
        self.assertEqual(offenders, [], f"absence of an active legal matter is not a gap: {offenders}")

    def test_ca_does_not_require_a_product_named_policy(self):
        repo_root = pathlib.Path(__file__).resolve().parent.parent
        for name in ("AAD_PREMIUM.py", "AAD_PREMIUM_P1.py"):
            source = (repo_root / "Recommendations" / "entra" / name).read_text(encoding="utf-8")
            self.assertNotIn("none specifically target Copilot applications", source)
            self.assertNotIn("Create Copilot-specific Conditional Access policies", source)


if __name__ == "__main__":
    unittest.main()

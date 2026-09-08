"""Regression tests for report signal-to-noise.

The assessment used to emit ~300 cards for a tenant with a few dozen real findings. Two thirds
carried no recommendation at all, one condition could appear as five separate cards, and card
titles named the licence rather than the finding. These cover the changes that fixed that:

* unmapped service plans no longer produce filler cards (they move to a workbook inventory tab)
* a plan disabled in one SKU but active in another is reported as active
* a retired product being switched off is not reported as a gap
* Status and Priority can no longer contradict each other
* cards describing one condition are collapsed
* card headings describe the finding, not the licence
* {placeholders} in recommendation text are rendered, not printed verbatim
"""

import ast
import os
import pathlib
import re
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class FillerCardSuppressionTests(unittest.TestCase):
    def test_unmapped_plans_produce_no_card(self):
        from Recommendations.m365 import get_feature_recommendation

        # A sample of the plans that produced boilerplate cards in real reports.
        for plan in ("EXCHANGE_STORAGE_50GB", "WINBIZ", "MCOSMS3", "CLOUD_PKI",
                     "SPECIALTY_DEVICES", "BPOS_S_DlpAddOn", "3_PARTY_APP_PATCH"):
            for status in ("Success", "Disabled", "PendingActivation"):
                with self.subTest(plan=plan, status=status):
                    self.assertIsNone(
                        get_feature_recommendation(plan, "ENTERPRISEPACK", status),
                        f"{plan} has no dedicated module and must not emit a filler card",
                    )

    def test_mapped_plans_still_produce_findings(self):
        from Recommendations.m365 import get_feature_recommendation

        for plan in ("SHAREPOINTENTERPRISE", "EXCHANGE_S_ENTERPRISE", "TEAMS1",
                     "M365_COPILOT_APPS", "SWAY"):
            with self.subTest(plan=plan):
                self.assertIsNotNone(
                    get_feature_recommendation(plan, "ENTERPRISEPACK", "Success"),
                    f"{plan} has a dedicated module and must still be assessed",
                )

    def test_suppressed_plans_are_preserved_in_the_inventory_sheet(self):
        from Core.evidence_layer import _build_service_plan_inventory_sheet

        sheet = _build_service_plan_inventory_sheet({
            "licenses": [{
                "sku_part_number": "ENTERPRISEPACK",
                "enabled": 100,
                "service_plans": [
                    {"name": "WINBIZ", "status": "Success"},
                    {"name": "SHAREPOINTENTERPRISE", "status": "Success"},
                ],
            }],
        })
        self.assertIsNotNone(sheet)
        by_plan = {row["Service Plan Name"]: row for row in sheet["rows"]}
        self.assertIn("WINBIZ", by_plan, "suppressed plans must still be inventoried")
        self.assertEqual(by_plan["WINBIZ"]["Assessed"], "No")
        self.assertEqual(by_plan["SHAREPOINTENTERPRISE"]["Assessed"], "Yes")

    def test_bookings_is_inventory_only_even_when_usage_is_high(self):
        from Recommendations.m365.MICROSOFTBOOKINGS import get_recommendation

        records = get_recommendation(
            "ENTERPRISEPACK",
            "PendingInput",
            {"email_report_available": True, "email_avg_sent_per_user": 25, "teams_total_meetings": 5000},
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["Disposition"], "Reference")
        self.assertEqual(records[0]["Recommendation"], "")
        self.assertNotEqual(records[0]["Disposition"], "Coverage")


class PlanStatusResolutionTests(unittest.TestCase):
    def test_plan_active_in_any_sku_is_reported_active(self):
        from Core.service_categorization import resolve_plan_statuses

        resolved = resolve_plan_statuses([
            {"sku_part_number": "Microsoft_Teams_Enterprise_New",
             "service_plans": [{"name": "ONEDRIVE_BASIC_P2", "status": "Disabled"}]},
            {"sku_part_number": "ENTERPRISEPACK",
             "service_plans": [{"name": "ONEDRIVE_BASIC_P2", "status": "Success"}]},
        ])
        self.assertEqual(resolved["ONEDRIVE_BASIC_P2"]["status"], "Success")
        self.assertEqual(resolved["ONEDRIVE_BASIC_P2"]["sku_name"], "ENTERPRISEPACK")

    def test_plan_disabled_everywhere_stays_disabled(self):
        from Core.service_categorization import resolve_plan_statuses

        resolved = resolve_plan_statuses([
            {"sku_part_number": "A", "service_plans": [{"name": "X", "status": "Disabled"}]},
            {"sku_part_number": "B", "service_plans": [{"name": "X", "status": "Disabled"}]},
        ])
        self.assertEqual(resolved["X"]["status"], "Disabled")

    def test_retired_products_are_only_flagged_while_provisioned(self):
        from Core.service_categorization import RETIRED_PLANS

        # Skype for Business, StaffHub and Kaizala are retired; disabled is the desired state.
        self.assertIn("MCOIMP", RETIRED_PLANS)
        self.assertIn("DESKLESS", RETIRED_PLANS)
        self.assertIn("KAIZALA_STANDALONE", RETIRED_PLANS)
        # Current products must not be in the set.
        self.assertNotIn("TEAMS1", RETIRED_PLANS)
        self.assertNotIn("SHAREPOINTENTERPRISE", RETIRED_PLANS)


class StatusPriorityCoherenceTests(unittest.TestCase):
    def test_success_cannot_carry_an_urgent_action(self):
        from Core.new_recommendation import new_recommendation

        high = new_recommendation(
            "Purview", "Customer Lockbox",
            "Customer Lockbox license active but DISABLED",
            "Enable Customer Lockbox.", "L", "https://e",
            priority="High", status="Success",
        )
        self.assertEqual(high["Status"], "Action Required")

        medium = new_recommendation(
            "Purview", "eDiscovery", "License active but NO cases are configured",
            "Create eDiscovery cases.", "L", "https://e",
            priority="Medium", status="Success",
        )
        self.assertEqual(medium["Status"], "Attention Required")

    def test_low_priority_tips_stay_successful(self):
        from Core.new_recommendation import new_recommendation

        rec = new_recommendation(
            "Purview", "Azure RMS", "Azure RMS is ENABLED",
            "Verify RMS covers Copilot scenarios.", "L", "https://e",
            priority="Low", status="Success",
        )
        self.assertEqual(rec["Status"], "Success")

    def test_existing_action_statuses_are_untouched(self):
        from Core.new_recommendation import new_recommendation, NOT_ASSESSED_STATUS

        for status in ("Critical", "Action Required", "Warning", "Disabled",
                       "Insight", NOT_ASSESSED_STATUS):
            with self.subTest(status=status):
                rec = new_recommendation(
                    "X", "F", "obs", "do something", "L", "https://e",
                    priority="High", status=status,
                )
                self.assertEqual(rec["Status"], status)

    def test_informational_cards_keep_success(self):
        from Core.new_recommendation import new_recommendation

        rec = new_recommendation("X", "F", "Feature is active", "", "L", "https://e",
                                 status="Success")
        self.assertEqual(rec["Status"], "Success")
        self.assertEqual(rec["Priority"], "")


class FindingDeduplicationTests(unittest.TestCase):
    def _rec(self, service, feature, obs, rec="", priority="Medium",
             status="Success", finding_key=""):
        from Core.new_recommendation import new_recommendation
        return new_recommendation(service, feature, obs, rec, "L", "https://e",
                                  priority=priority, status=status, finding_key=finding_key)

    def test_shared_finding_key_collapses_to_highest_severity(self):
        from Core.evidence_layer import deduplicate_findings

        merged = deduplicate_findings([
            self._rec("Purview", "Customer Lockbox (Enterprise A)",
                      "License active but DISABLED", "Enable it.", "High", "Success",
                      "purview.customer_lockbox.state"),
            self._rec("Purview", "Customer Lockbox (Enterprise)",
                      "License active but feature is DISABLED", "Enable it.", "Medium",
                      "Success", "purview.customer_lockbox.state"),
        ])
        self.assertEqual(len(merged), 1, "one condition must produce one card")
        self.assertEqual(merged[0]["Priority"], "High", "the more severe framing must win")
        self.assertIn("Customer Lockbox (Enterprise)", merged[0]["AlsoLicensedVia"])

    def test_identical_text_collapses_without_a_key(self):
        from Core.evidence_layer import deduplicate_findings

        observation = "Insider Risk Management is active in SPE E5, monitoring for exfiltration"
        merged = deduplicate_findings([
            self._rec("Purview", "Insider Risk Management (Base)", observation),
            self._rec("Purview", "Insider Risk Management", observation),
        ])
        self.assertEqual(len(merged), 1)

    def test_distinct_findings_are_never_merged(self):
        from Core.evidence_layer import deduplicate_findings

        merged = deduplicate_findings([
            self._rec("Entra", "Entra ID P1", "Only 33 of 131 users enrolled in MFA",
                      "Enforce MFA.", "High", "Action Required"),
            self._rec("Entra", "Entra ID P1", "No group-based licensing configured",
                      "Use group licensing.", "Medium", "Action Required"),
        ])
        self.assertEqual(len(merged), 2, "different findings must stay separate")

    def test_shared_finding_key_collapses_across_service_modules(self):
        from Core.evidence_layer import deduplicate_findings

        merged = deduplicate_findings([
            self._rec("Entra", "Enterprise app grants", "High-impact app grants found",
                      "Review exact scopes.", "High", "Action Required",
                      "ai.app_consent.high_impact_grants"),
            self._rec("Defender", "Cloud app governance", "High-impact app grants found",
                      "Review exact scopes.", "Medium", "Warning",
                      "ai.app_consent.high_impact_grants"),
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["Priority"], "High")

    def test_merged_card_keeps_every_evidence_bucket(self):
        from Core.evidence_layer import deduplicate_findings
        from Core.new_recommendation import new_recommendation

        merged = deduplicate_findings([
            new_recommendation("Purview", "A", "obs", "act", "L", "https://e",
                               priority="High", status="Action Required",
                               finding_key="k", evidence_key="purview_policy_detail"),
            new_recommendation("Purview", "B", "obs2", "act2", "L", "https://e",
                               priority="Low", status="Success",
                               finding_key="k", evidence_key="app_access_detail"),
        ])
        self.assertEqual(len(merged), 1)
        self.assertIn("purview_policy_detail", merged[0]["EvidenceKey"])
        self.assertIn("app_access_detail", merged[0]["EvidenceKey"])


class CardHeadingTests(unittest.TestCase):
    def test_heading_describes_the_finding_not_the_licence(self):
        from Core.export_recommendations import summarize_finding

        headings = [
            summarize_finding("12 Conditional Access policy(ies) configured, but none "
                              "specifically target Copilot applications"),
            summarize_finding("Only 33 of 131 users (25.2%) enrolled in MFA, exposing Copilot "
                              "to credential theft"),
            summarize_finding("No group-based licensing configured - Copilot licenses likely "
                              "assigned manually to individual users"),
        ]
        self.assertEqual(len(set(headings)), 3, "each finding needs a distinct heading")
        for heading in headings:
            self.assertNotEqual(heading, "Microsoft Entra ID P1")

    def test_markdown_and_paragraph_breaks_are_stripped(self):
        from Core.export_recommendations import summarize_finding

        heading = summarize_finding(
            "Copilot Security Posture: **NOT READY**\n\n\n**1 Critical Gaps:**\n  - Review apps"
        )
        self.assertEqual(heading, "Copilot Security Posture: NOT READY")
        self.assertNotIn("*", heading)

    def test_long_headings_are_truncated_on_a_word_boundary(self):
        from Core.export_recommendations import summarize_finding

        heading = summarize_finding("word " * 80)
        self.assertLessEqual(len(heading), 111)
        self.assertTrue(heading.endswith("…"))

    def test_empty_observation_is_handled(self):
        from Core.export_recommendations import summarize_finding
        self.assertEqual(summarize_finding(""), "")
        self.assertEqual(summarize_finding(None), "")


class RecommendationTextRenderingTests(unittest.TestCase):
    def test_no_recommendation_text_contains_unrendered_placeholders(self):
        """Plain strings with {placeholders} reach the customer verbatim."""
        placeholder = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*(?::[^}]*)?\}")
        offenders = []
        for path in sorted((REPO_ROOT / "Recommendations").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for kw in node.keywords or []:
                    if (kw.arg in ("observation", "recommendation")
                            and isinstance(kw.value, ast.Constant)
                            and isinstance(kw.value.value, str)
                            and placeholder.search(kw.value.value)):
                        offenders.append(f"{path.name}:{kw.value.lineno}")
        self.assertEqual(offenders, [], f"missing f-string prefix: {offenders}")

    def test_markdown_bold_is_rendered_not_printed(self):
        import pathlib as _pathlib
        from Core.new_recommendation import new_recommendation
        from Core.export_recommendations import export_to_html

        recs = [new_recommendation(
            "Defender", "Posture", "Posture: **NOT READY**", "Fix **1 gap**.",
            "L", "https://example.com", priority="High", status="Critical",
        )]
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                body = _pathlib.Path(
                    export_to_html(recs, tenant_name="T")
                ).read_text(encoding="utf-8")
            finally:
                os.chdir(original_cwd)

        card = body.split('<article class="recommendation-card"')[1].split("</article>")[0]
        visible = card.split('<div class="card-body">')[1]
        self.assertIn("<strong>NOT READY</strong>", visible)
        self.assertNotIn("**", visible)

    def test_escaping_still_wins_over_markdown_rendering(self):
        import pathlib as _pathlib
        from Core.new_recommendation import new_recommendation
        from Core.export_recommendations import export_to_html

        recs = [new_recommendation(
            "Defender", "Posture", "**<script>alert(1)</script>**", "plain",
            "L", "https://example.com", priority="High", status="Critical",
        )]
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                body = _pathlib.Path(
                    export_to_html(recs, tenant_name="T")
                ).read_text(encoding="utf-8")
            finally:
                os.chdir(original_cwd)

        self.assertNotIn("<script>alert", body)
        self.assertIn("&lt;script&gt;", body)


if __name__ == "__main__":
    unittest.main()

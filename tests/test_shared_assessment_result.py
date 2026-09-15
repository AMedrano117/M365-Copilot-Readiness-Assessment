"""Sanitized evidence contracts: scope, history, completeness and one decision."""

import copy
import unittest
from types import SimpleNamespace

from Core.assessment_result import QUESTIONS, build_assessment_result
from Core.evidence_contract import normalize_observation, reconcile_observations
from Core.evidence_layer import _build_m365_activity_sheet, _build_verified_strengths


TENANT = "11111111-1111-1111-1111-111111111111"
DAY = "2026-09-15"


def fact(**changes):
    base = {
        "tenant_id": TENANT, "domain_id": "content", "control_id": "CONTENT.PERMISSIONS",
        "metric_id": "sites.broad_access", "scope": "all sites", "population": "active sites",
        "value": 0, "unit": "sites", "availability": "available", "complete": True,
        "observed_at": "2026-09-10", "source_type": "portal_export", "source_file": "sites.csv",
    }
    return dict(base, **changes)


def rec(**changes):
    base = {
        "RecommendationId": "ENT-001", "Service": "Entra", "Feature": "Multifactor authentication",
        "Observation": "Two pilot accounts do not meet the authentication baseline.",
        "Recommendation": "Confirm and apply the authentication baseline.",
        "Status": "Action Required", "Priority": "High", "Disposition": "Action",
        "FindingKey": "identity.authentication", "EvidenceKey": "authentication_detail",
        "EvidenceBasis": "Tenant evidence", "EvidenceAvailable": "Yes",
    }
    return dict(base, **changes)


def bundle(**changes):
    base = {"collection_context": {"mode": "offline", "collected_at": "2026-09-10T12:00:00Z"}}
    return dict(base, **changes)


class EvidenceContractTests(unittest.TestCase):
    def reconcile(self, rows):
        return reconcile_observations(rows, evaluation_date=DAY, expected_tenant_id=TENANT)

    def test_missing_and_empty_stay_distinct_from_measured_zero(self):
        for state in ("missing", "not_requested", "inaccessible", "unsupported", "empty", "unknown"):
            with self.subTest(state=state):
                normalized = normalize_observation(fact(value=0, availability=state), evaluation_date=DAY)
                self.assertIsNone(normalized["value"])
                self.assertEqual(normalized["availability"], state)
        self.assertEqual(normalize_observation(fact(), evaluation_date=DAY)["value"], 0)

    def test_narrower_population_and_reporting_window_do_not_replace_whole_tenant(self):
        rows = self.reconcile([
            fact(), fact(scope="pilot sites", value=1, source_file="pilot.csv", observed_at="2026-09-14"),
            fact(window="2026-08", source_file="august.csv", value=3),
        ])
        self.assertEqual(sum(row["selection"] == "selected" for row in rows), 3)

    def test_newer_partial_evidence_never_replaces_complete_snapshot(self):
        rows = self.reconcile([fact(value=8), fact(value=2, source_file="partial.csv", complete=False,
                                                  truncated=True, observed_at="2026-09-14")])
        selected = next(row for row in rows if row["selection"] == "selected")
        self.assertEqual(selected["value"], 8)
        self.assertEqual(next(row for row in rows if row["source_file"] == "partial.csv")["selection"], "superseded")

    def test_duplicates_are_retained_and_not_summed(self):
        rows = self.reconcile([fact(value=4), fact(value=4, source_file="copy.csv")])
        self.assertEqual(sorted(row["selection"] for row in rows), ["duplicate", "selected"])
        self.assertEqual(next(row for row in rows if row["selection"] == "selected")["value"], 4)

    def test_same_date_or_unordered_values_conflict(self):
        for date in ("2026-09-10", ""):
            rows = self.reconcile([fact(value=4), fact(value=2, observed_at=date, source_file="second.csv")])
            self.assertTrue(all(row["selection"] == "conflict" for row in rows))

    def test_equal_integer_and_decimal_measurements_are_duplicates_with_source_values_retained(self):
        inputs = [fact(value=20, source_file="integer.json", source_hash="original-int"),
                  fact(value=20.0, source_file="decimal.csv", source_hash="original-float")]
        before = copy.deepcopy(inputs)
        rows = self.reconcile(inputs)
        self.assertEqual(sorted(row["selection"] for row in rows), ["duplicate", "selected"])
        by_file = {row["source_file"]: row for row in rows}
        self.assertIs(type(by_file["integer.json"]["value"]), int)
        self.assertIs(type(by_file["decimal.csv"]["value"]), float)
        self.assertEqual(by_file["integer.json"]["source_hash"], "original-int")
        self.assertEqual(inputs, before)

    def test_numeric_comparison_preserves_real_differences_without_float_rounding(self):
        for first, second in [(20, 20.01), (2**53 + 1, float(2**53)), (20, "20"), (1, True)]:
            with self.subTest(first=first, second=second):
                rows = self.reconcile([fact(value=first), fact(value=second, source_file="other.csv")])
                self.assertEqual({row["selection"] for row in rows}, {"conflict"})

    def test_distinct_metric_definition_and_unit_never_collapsed(self):
        rows = self.reconcile([fact(value=4), fact(value=4, unit="files", source_file="files.csv"),
                               fact(value=4, metric_definition="external sharing only", source_file="external.csv")])
        self.assertTrue(all(row["selection"] == "selected" for row in rows))

    def test_foreign_tenant_rejected_even_for_single_source(self):
        with self.assertRaisesRegex(ValueError, "tenant"):
            self.reconcile([fact(tenant_id="22222222-2222-2222-2222-222222222222")])

    def test_later_evaluation_changes_freshness_without_changing_observation_id(self):
        current = normalize_observation(fact(), evaluation_date=DAY)
        stale = normalize_observation(fact(), evaluation_date="2026-12-15")
        self.assertEqual(current["freshness"], "current")
        self.assertEqual(stale["freshness"], "stale")
        self.assertEqual(current["evidence_id"], stale["evidence_id"])

    def test_future_date_is_never_usable(self):
        result = self.reconcile([fact(observed_at="2026-10-01")])[0]
        self.assertEqual(result["freshness"], "future")
        self.assertEqual(result["selection"], "unavailable")


class SharedAssessmentTests(unittest.TestCase):
    def result(self, records=None, evidence=None, **kwargs):
        return build_assessment_result(records or [], evidence or bundle(), evaluation_date=DAY,
                                       expected_tenant_id=TENANT, **kwargs)

    def test_all_required_questions_have_a_result_or_specific_gap(self):
        result = self.result()
        self.assertEqual(len(result["domains"]), 7)
        self.assertEqual(len(result["controls"]), len(QUESTIONS))
        self.assertTrue(all(row["status"] == "Not established" for row in result["controls"]))
        self.assertEqual(result["decision"], "Readiness unconfirmed")
        self.assertIn("CONTENT.SHARING", {row["control_id"] for row in result["controls"]})
        self.assertIn("CONTENT.PERMISSIONS", {row["control_id"] for row in result["controls"]})

    def test_historical_risks_and_strengths_use_identical_date_qualification(self):
        prior = {"available": True, "generated_at": DAY, "tenant_id": TENANT, "source_file": "earlier.xlsx",
                 "sheets": {"Authentication Coverage": {"rows": [{"Metric": "Example", "Value": 2}]}},
                 "recommendations": [rec(), rec(RecommendationId="ENT-002", Feature="Privileged roles", FindingKey="identity.admin",
                                                  Disposition="Assurance", Status="Success", Recommendation="", EvidenceSheet="Authentication Coverage")]}
        result = self.result(evidence=bundle(prior_report=prior))
        action = next(row for row in result["actions"] if row.get("OriginalRecommendationId") == "ENT-001")
        strength = result["historical_strengths"][0]
        self.assertEqual(action["ActionType"], "Confirmation")
        self.assertEqual(action["ObservationDate"], "")
        self.assertEqual(strength["ObservationDate"], "")
        self.assertEqual(action["Freshness"], strength["Freshness"])
        self.assertFalse(result["strengths"])
        self.assertEqual(action["OriginalMethodologyVersion"], "")
        self.assertNotEqual(action["Status"], "Verified")

    def test_old_collection_gaps_and_marketing_remain_out_of_current_actions(self):
        originals = [rec(FindingKey="old.coverage", Disposition="Coverage", Status="Not Assessed"),
                     rec(FindingKey="old.adoption", Disposition="Opportunity", Status="Insight", Observation="Adopt another licensed tool.")]
        result = self.result(evidence=bundle(prior_report={"tenant_id": TENANT, "recommendations": originals}))
        self.assertFalse(any(row.get("FindingKey") == "old.coverage" for row in result["actions"]))
        self.assertFalse(result["opportunities"])
        self.assertTrue(any(row.get("FindingKey") == "old.coverage" for row in result["recommendations"]))
        self.assertFalse(any(row.get("FindingKey", "").startswith("old.") for domain in result["domains"] for row in domain["findings"]))

    def test_historical_usage_is_integrated_when_current_usage_absent(self):
        prior = {"tenant_id": TENANT, "source_file": "earlier.xlsx", "sheets": {"AI Adoption Usage": {"rows": [
            {"Metric": "Active users", "Value": 3, "Period": "D28", "Refresh Date": "2026-09-01", "Availability": "Available"},
            {"Metric": "Enabled users", "Value": 0, "Period": "D28", "Refresh Date": "2026-09-01", "Availability": "Available"},
        ]}}}
        result = self.result(evidence=bundle(prior_report=prior))
        metrics = result["adoption_metrics"]
        self.assertEqual({row["label"]: row["value"] for row in metrics}, {"Active users": 3, "Enabled users": 0})
        self.assertTrue(all(row["source_type"] == "prior_assessment" for row in metrics))
        adoption = next(row for row in result["domains"] if row["id"] == "adoption")
        self.assertEqual(adoption["metrics"], metrics)

    def test_raw_cache_risks_and_strengths_age_together(self):
        states = {"purview_dlp_policies": {"source_type": "purview_cache", "collected_at": "2026-06-01",
                                           "source_file": "policies.json", "available": True}}
        risk = rec(Service="Purview", Feature="DLP", EvidenceKey="purview_policy_detail", FindingKey="purview.dlp")
        assurance = {"Area": "Data loss prevention", "Strength": "DLP is configured.", "Key": "dlp-enforced", "EvidenceKey": "purview_policy_detail"}
        result = self.result([risk], bundle(source_statuses=states, verified_strengths=[assurance]))
        action = next(row for row in result["actions"] if row.get("FindingKey") == "purview.dlp")
        self.assertEqual(action["ActionType"], "Confirmation")
        self.assertEqual(action["Freshness"], "stale")
        self.assertEqual(result["historical_strengths"][0]["Freshness"], "stale")
        self.assertFalse(result["strengths"])

    def test_comparable_latest_condition_resolves_earlier_finding(self):
        prior = {"tenant_id": TENANT, "recommendations": [rec(EvidenceScope="Assessed tenant collection")]}
        current = rec(Disposition="Assurance", Status="Success", Recommendation="", Observation="Authentication baseline is enforced.")
        result = self.result([current], bundle(prior_report=prior))
        matching = [row for row in result["recommendations"] if row.get("FindingKey") == "identity.authentication"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["Disposition"], "Assurance")
        self.assertTrue(matching[0]["HistoricalReferences"])

    def test_partial_or_unknown_scope_does_not_resolve_historical_condition(self):
        prior = {"tenant_id": TENANT, "recommendations": [rec(EvidenceScope="all users")]}
        current = rec(EvidenceScope="pilot users", Disposition="Assurance", Status="Success", Recommendation="")
        result = self.result([current], bundle(prior_report=prior))
        self.assertTrue(any(row["ActionType"] == "Confirmation" for row in result["actions"]))

    def test_date_scope_and_counts_are_identical_for_live_and_offline(self):
        original = bundle()
        live = copy.deepcopy(original)
        live["collection_context"]["mode"] = "live"
        first = self.result([rec()], original)
        second = self.result([rec()], live)
        self.assertEqual(first, second)
        self.assertEqual(sum(domain["action_count"] for domain in first["domains"]), first["counts"]["actions"])
        self.assertEqual(sum(first["counts"][key] for key in ("remediation", "confirmation", "evidence_gaps")), first["counts"]["actions"])

    def test_inputs_are_unchanged_and_record_ids_are_unique(self):
        records = [rec(), rec(Feature="Privileged roles", FindingKey="identity.admin")]
        evidence = bundle()
        before = copy.deepcopy((records, evidence))
        result = self.result(records, evidence)
        self.assertEqual((records, evidence), before)
        ids = [row["RecommendationId"] for row in result["recommendations"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_one_dlp_strength_does_not_pass_sensitivity_or_audit_questions(self):
        row = rec(Service="Purview", Feature="DLP coverage", FindingKey="purview.dlp", EvidenceKey="purview_policy_detail",
                  Observation="DLP policies enforce restrictions.", Recommendation="", Disposition="Assurance", Status="Success")
        result = self.result([row], bundle(observations=[fact(domain_id="data_protection", control_id="DATA.DLP",
            metric_id="DATA.DLP", scope="Reviewed pilot users and approved content", value="Reviewed enforcement and coverage",
            unit="control result", control_result="pass")]))
        controls = {row["control_id"]: row["status"] for row in result["controls"]}
        self.assertEqual(controls["DATA.DLP"], "Observed")
        self.assertEqual(controls["DATA.LABELS"], "Not established")
        self.assertEqual(controls["DATA.AUDIT"], "Not established")

    def test_policy_existence_cannot_establish_scoped_coverage_or_enforcement(self):
        examples = (
            ("IDENTITY.AUTH", "Entra", "conditional_access_detail", "12 Conditional Access policies found; 7 require MFA and 10 block legacy authentication"),
            ("DATA.PUBLISHING", "Purview", "purview_policy_detail", "Sensitivity labels are defined and publishing policies are present."),
            ("DATA.DLP", "Purview", "purview_policy_detail", "DLP policies and their protection rules are enabled."),
        )
        for control, service, evidence_key, observation in examples:
            with self.subTest(control=control):
                row = rec(Service=service, Feature="Policy configuration", FindingKey=control.lower(), EvidenceKey=evidence_key,
                          Observation=observation, Recommendation="", Disposition="Assurance", Status="Success")
                result = self.result([row])
                check = next(item for item in result["controls"] if item["control_id"] == control)
                self.assertEqual(check["status"], "Not established")
                self.assertTrue(any(item["Observation"] == observation for item in result["strengths"]))
                self.assertTrue(any(item.get("ControlId") == control and item["ActionType"] == "Evidence" for item in result["actions"]))
                reviewed = self.result([row], bundle(observations=[fact(domain_id="identity" if service == "Entra" else "data_protection",
                    control_id=control, metric_id=control, scope="Reviewed pilot population and content", value="Coverage and effective protection reviewed",
                    unit="control result", control_result="pass")]))
                self.assertEqual(next(item["status"] for item in reviewed["controls"] if item["control_id"] == control), "Observed")

    def test_current_risky_user_action_and_milestone_use_specific_review_title(self):
        row = rec(RecommendationId="ENT-013", Feature="Microsoft Entra ID P2", FindingKey="", ControlId="IDENTITY-001",
                  EvidenceKey="identity_risk_detail", Observation="7 risky users detected in the tenant (0 high-risk, 7 medium-risk)")
        result = self.result([row])
        action = next(item for item in result["actions"] if item["RecommendationId"] == "ENT-013")
        self.assertEqual(action["Feature"], "Review and remediate risky user accounts")
        self.assertEqual(action["OriginalFeature"], "Microsoft Entra ID P2")
        requirement = next(item for stage in result["rollout_progress"]["stages"] for item in stage["requirements"]
                           if item["id"] == "pilot.action.ENT-013")
        self.assertEqual(requirement["title"], action["Feature"])

    def test_audit_observation_does_not_establish_retention(self):
        row = rec(Service="Purview", Feature="Audit", FindingKey="purview.audit", EvidenceKey="purview_policy_detail",
                  Observation="Audit logging is enabled.", Recommendation="", Disposition="Assurance", Status="Success")
        controls = {row["control_id"]: row["status"] for row in self.result([row])["controls"]}
        self.assertEqual(controls["DATA.AUDIT"], "Observed")
        self.assertEqual(controls["DATA.RETENTION"], "Not established")

    def test_different_historical_conditions_from_same_license_feature_are_retained(self):
        originals = [rec(FindingKey="", Feature="Example identity plan", RecommendationId=f"OLD-{index}",
                         Observation=observation) for index, observation in enumerate((
                             "Three accounts lack authentication registration.", "Privileged assignments have no expiry.",
                             "Two application grants have broad permissions."))]
        result = self.result(evidence=bundle(prior_report={"tenant_id": TENANT, "recommendations": originals}))
        self.assertEqual(result["counts"]["confirmation"], 3)

    def test_activity_data_is_not_an_agreed_pilot_plan(self):
        row = rec(Service="M365", Feature="Copilot usage", FindingKey="m365.copilot_usage", EvidenceKey="ai_usage_detail",
                  Observation="There were three active users.", Recommendation="", Disposition="Assurance", Status="Success")
        control = next(row for row in self.result([row])["controls"] if row["control_id"] == "ADOPTION.BASELINE")
        self.assertEqual(control["status"], "Not established")

    def test_complete_explicit_facts_can_establish_controlled_pilot(self):
        from tests.test_readiness_progress import review_profile
        observations = [fact(domain_id=domain, control_id=control, metric_id=control, value="Reviewed",
                             scope="pilot population", unit="control result", control_result="pass") for control, domain, *_ in QUESTIONS]
        result = self.result(evidence=bundle(observations=observations, assessment_profile=review_profile(complete=False)))
        self.assertEqual(result["decision"], "Ready for a controlled pilot")
        self.assertFalse(result["actions"])
        self.assertTrue(all(row["Status"] == "Pass" for row in result["control_results"]))

    def test_mode_or_unsupported_supplement_does_not_override_supported_controls(self):
        from tests.test_readiness_progress import review_profile
        observations = [fact(domain_id=domain, control_id=control, metric_id=control, value="Reviewed",
                             scope="pilot population", unit="control result", control_result="pass") for control, domain, *_ in QUESTIONS]
        rows = [rec(FindingKey=key, Disposition="Coverage", Status="Not Assessed") for key in
                ("offline.tenant_coverage", "portal.unrecognized.optional.csv")]
        result = self.result(rows, bundle(observations=observations, assessment_profile=review_profile(complete=False)))
        self.assertEqual(result["decision"], "Ready for a controlled pilot")
        self.assertFalse(result["actions"])

    def test_wrongly_labeled_entitlement_still_cannot_become_assurance(self):
        original = rec(Service="Purview", Feature="Power Automate Free", Disposition="Assurance", Status="Success",
                       Observation="Power Automate Free is active in Power Automate Free.", Recommendation="", EvidenceBasis="Tenant evidence")
        result = self.result([original])
        self.assertFalse(result["strengths"])
        self.assertFalse(result["historical_strengths"])
        self.assertFalse(any(row["Feature"] == "Power Automate Free" for domain in result["domains"] for row in domain["findings"]))

    def test_complete_evidence_with_conflict_requires_confirmation(self):
        observations = [fact(value=2), fact(value=4, source_file="conflicting.csv")]
        result = self.result(evidence=bundle(observations=observations))
        self.assertTrue(any(row["EvidenceStatus"] == "conflict" for row in result["actions"]))

    def test_raw_measurement_requires_control_interpretation_before_passing(self):
        result = self.result(evidence=bundle(observations=[fact(value=0)]))
        question = next(row for row in result["controls"] if row["control_id"] == "CONTENT.PERMISSIONS")
        self.assertEqual(question["status"], "Not established")
        result = self.result(evidence=bundle(observations=[fact(value=2, control_result="fail")]))
        question = next(row for row in result["controls"] if row["control_id"] == "CONTENT.PERMISSIONS")
        self.assertEqual(question["status"], "Action required")
        self.assertTrue(any(row["ActionType"] == "Remediation" for row in result["actions"]))

    def test_embedded_legacy_collection_rows_remain_historical(self):
        original = rec(SourceType="prior_assessment", PriorReportDate="2026-09-01", SourceFile="legacy.json",
                       MethodologyVersion="1.0", ObservationDate="2026-09-01")
        result = self.result([original])
        action = next(row for row in result["actions"] if row.get("FindingKey") == "identity.authentication")
        self.assertEqual(action["ActionType"], "Confirmation")
        self.assertEqual(action["SourceFile"], "legacy.json")
        self.assertEqual(action["PriorReportDate"], "2026-09-01")
        self.assertEqual(action["OriginalMethodologyVersion"], "1.0")

    def test_reusing_normalized_legacy_rows_does_not_refresh_or_rewrite_them_again(self):
        original = rec(SourceType="prior_assessment", PriorReportDate="2026-09-01", SourceFile="legacy.json")
        first = self.result([original])
        second = self.result(first["recommendations"])
        before = next(row for row in first["actions"] if row.get("FindingKey") == "identity.authentication")
        after = next(row for row in second["actions"] if row.get("FindingKey") == "identity.authentication")
        for key in ("SourceType", "SourceFile", "ObservationDate", "Feature", "Recommendation", "OriginalFeature"):
            self.assertEqual(before[key], after[key])

    def test_historical_apps_workload_and_included_chat_keep_their_units_and_populations(self):
        prior = {"tenant_id": TENANT, "source_file": "earlier.xlsx", "sheets": {
            "AI Adoption Usage": {"rows": [
                {"Metric": "Word active users", "Value": 2, "Period": "D28", "Refresh Date": "2026-09-01", "Availability": "Available"},
                {"Metric": "Copilot Chat (work) active users", "Value": 1, "Period": "D28", "Availability": "Available"},
                {"Metric": "Unlicensed Copilot Chat activity", "Value": 4, "Period": "D28", "Availability": "Available"},
            ]},
            "M365 Activity Detail": {"rows": [{"Workload": "SharePoint", "Metric": "Total Files", "Value": 10}]},
        }}
        result = self.result(evidence=bundle(prior_report=prior))
        metrics = {row["metric_id"]: row for row in result["adoption_metrics"]}
        self.assertEqual(metrics["copilot.apps.word.active_users"]["window_label"], "28 days")
        self.assertEqual(metrics["copilot.apps.copilot_chat_work.active_users"]["population"], "Paid Microsoft 365 Copilot")
        self.assertEqual(metrics["copilot_chat.unlicensed_activity"]["population"], "Included Copilot Chat")
        self.assertEqual(metrics["m365.sharepoint.total_files"]["unit"], "files")

    def test_optional_unscoped_services_do_not_expand_the_domain_list(self):
        optional = rec(Service="Power Platform", Feature="Agent inventory", Disposition="Coverage", Status="Not Assessed")
        result = self.result([optional])
        self.assertNotIn("agents", {row["id"] for row in result["domains"]})
        self.assertEqual(len(result["optional_coverage"]), 1)

    def test_current_inventory_missing_value_stays_none_in_workbook_evidence(self):
        sheet = _build_m365_activity_sheet(SimpleNamespace())
        users = next(row for row in sheet["rows"] if row["Metric"] == "Total Users")
        self.assertIsNone(users["Value"])
        measured = _build_m365_activity_sheet(SimpleNamespace(users_summary={"total": 0}))
        self.assertEqual(next(row for row in measured["rows"] if row["Metric"] == "Total Users")["Value"], 0)
        failed = _build_m365_activity_sheet(SimpleNamespace(users_summary={"total": 0, "error": "Read failed"},
                                                            teams_summary={"available": False, "active_users": 0}))
        self.assertIsNone(next(row for row in failed["rows"] if row["Metric"] == "Total Users")["Value"])
        self.assertIsNone(next(row for row in failed["rows"] if row["Workload"] == "Teams")["Value"])


if __name__ == "__main__":
    unittest.main()

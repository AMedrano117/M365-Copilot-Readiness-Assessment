"""Dated operator reviews close specific questions and support rollout milestones."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from Core.assessment_result import QUESTIONS, build_assessment_result
from Core.control_reviews import build_review_evidence, validate_readiness_review
from Core.cross_provider_assessment import load_assessment_profile


TENANT = "11111111-1111-1111-1111-111111111111"
DAY = "2026-09-15"


def review_profile(*, complete=True):
    profile = {"version": "1.0", "use_cases": [{
        "name": "Draft approved project summaries", "business_owner": "Operations lead",
        "intended_users": ["Fictional pilot"], "approved_data": ["Approved internal project documents"],
        "required_outcome": "Reduce drafting time without reducing accuracy",
        "risk_measurements": ["Sensitive-data incidents", "Unsupported statements"],
        "expand_stop_decision": "Stop for disclosure incidents; expand after a sponsor review",
    }]}
    profile["readiness_review"] = {
        "version": "1.0", "tenant_id": TENANT,
        "pilot_scope": {"id": "pilot", "description": "Ten fictional users and their approved internal content", "population_count": 10,
                        "reviewed_at": "2026-09-01", "reviewer_role": "Business sponsor", "evidence_reference": "Fictional pilot charter"},
        "pilot_plan": {"reviewed_at": "2026-09-01", "reviewer_role": "Business sponsor",
                       "baseline": "Forty minutes per first draft, with documented quality review",
                       "use_case_names": ["Draft approved project summaries"], "evidence_reference": "Fictional charter and baseline"},
        "control_reviews": [{"control_id": key, "scope_id": "pilot", "reviewed_at": "2026-09-14",
                             "reviewer_role": "Accountable control owner", "result": "pass",
                             "rationale": "The required control was reviewed for the stated pilot population.",
                             "evidence_reference": "Fictional reviewed evidence " + key}
                            for key, *_ in QUESTIONS if complete and key != "ADOPTION.BASELINE"],
    }
    return profile


def source_record(**changes):
    row = {"RecommendationId": "RISK-001", "FindingKey": "identity.mfa", "ControlId": "IDENTITY.MFA",
           "Service": "Entra", "Feature": "Register the remaining pilot accounts for multifactor authentication",
           "Observation": "Two accounts have not completed multifactor authentication registration.",
           "Recommendation": "Complete registration before those accounts join the pilot.",
           "Disposition": "Action", "Status": "Action Required", "Priority": "High",
           "EvidenceBasis": "Tenant evidence", "EvidenceAvailable": "Yes", "EvidenceKey": "authentication_detail",
           "EvidenceScope": "Ten fictional pilot users", "ObservationDate": "2026-09-10", "EvidenceComplete": True}
    return dict(row, **changes)


class ReviewSchemaTests(unittest.TestCase):
    def test_existing_profile_use_cases_supply_reviewed_charter_fields(self):
        profile = review_profile()
        self.assertFalse(validate_readiness_review(profile))
        review = build_review_evidence(profile, evaluation_date=DAY, expected_tenant_id=TENANT)
        self.assertTrue(review["pilot_plan"]["current"])
        self.assertEqual(review["pilot_plan"]["business_owner"], ["Operations lead"])
        self.assertEqual(review["observations"][0]["source_type"], "operator_attestation")

    def test_unknown_controls_na_results_and_incomplete_reviews_are_rejected(self):
        for field, value in (("control_id", "NOT-A-CONTROL"), ("result", "not_applicable"), ("rationale", ""),
                             ("evidence_reference", ""), ("scope_id", "other"), ("reviewed_at", "unknown")):
            with self.subTest(field=field):
                profile = review_profile()
                profile["readiness_review"]["control_reviews"][0][field] = value
                review = build_review_evidence(profile, evaluation_date=DAY, expected_tenant_id=TENANT)
                self.assertEqual(review["status"], "invalid")
                self.assertFalse(review["observations"])

    def test_malformed_nested_objects_produce_validation_messages(self):
        for key, value in (("pilot_scope", []), ("pilot_plan", "bad"), ("control_reviews", [{}]),
                           ("control_reviews", [{"scope_id": []}]), ("expansion_scope", "bad")):
            profile = review_profile()
            profile["readiness_review"][key] = value
            self.assertTrue(validate_readiness_review(profile))

    def test_tenant_mismatch_is_rejected_even_for_one_review(self):
        with self.assertRaisesRegex(ValueError, "tenant ID"):
            build_review_evidence(review_profile(), evaluation_date=DAY, expected_tenant_id="22222222-2222-2222-2222-222222222222")

    def test_future_stale_or_predating_scope_reviews_do_not_support_controls(self):
        for date in ("2026-09-16", "2026-01-01", "2026-08-31"):
            profile = review_profile()
            profile["readiness_review"]["control_reviews"][0]["reviewed_at"] = date
            review = build_review_evidence(profile, evaluation_date=DAY, expected_tenant_id=TENANT)
            self.assertFalse(review["control_reviews"][0]["current"])
            self.assertFalse(review["observations"][0]["complete"])

    def test_profile_loader_reports_review_schema_errors(self):
        profile = review_profile()
        profile["readiness_review"]["control_reviews"][0]["result"] = "not_applicable"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps(profile), encoding="utf-8")
            loaded = load_assessment_profile(path)
        self.assertFalse(loaded["available"])
        self.assertIn("result must be pass or fail", loaded["reason"])

    def test_profile_loader_rejects_malformed_use_case_references_without_crashing(self):
        for field in ("use_case_names", "use_cases"):
            with self.subTest(field=field):
                profile = review_profile()
                if field == "use_cases":
                    profile[field] = 12
                else:
                    profile["readiness_review"]["pilot_plan"][field] = 12
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "profile.json"
                    path.write_text(json.dumps(profile), encoding="utf-8")
                    loaded = load_assessment_profile(path)
                self.assertFalse(loaded["available"])
                review = build_review_evidence(loaded, evaluation_date=DAY, expected_tenant_id=TENANT)
                self.assertEqual(review["status"], "invalid")
                self.assertFalse(review["observations"])

    def test_json_raw_key_cannot_hide_invalid_review_in_loaded_profile(self):
        for extra_raw in ("invalid", {"unrelated": "x"}):
            with self.subTest(extra_raw=extra_raw):
                profile = review_profile()
                profile["raw"] = extra_raw
                profile["readiness_review"]["version"] = "999"
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "profile.json"
                    path.write_text(json.dumps(profile), encoding="utf-8")
                    loaded = load_assessment_profile(path)
                self.assertFalse(loaded["available"])
                self.assertIn("readiness_review.version must be 1.0", loaded["reason"])
                review = build_review_evidence(loaded, evaluation_date=DAY, expected_tenant_id=TENANT)
                self.assertEqual(review["status"], "invalid")
                self.assertFalse(review["observations"])

    def test_json_raw_key_does_not_change_valid_outer_review(self):
        profile = review_profile()
        profile["raw"] = "not an internal loader wrapper"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps(profile), encoding="utf-8")
            loaded = load_assessment_profile(path)
        self.assertTrue(loaded["available"])
        review = build_review_evidence(loaded, evaluation_date=DAY, expected_tenant_id=TENANT)
        self.assertEqual(review["status"], "available")
        self.assertEqual(len(review["observations"]), len(profile["readiness_review"]["control_reviews"]))


class ReadinessProgressTests(unittest.TestCase):
    def build(self, profile=None, records=None, extra=None):
        evidence = {"assessment_profile": profile or {}, "collection_context": {"mode": "offline", "collected_at": "2026-09-10"}}
        evidence.update(extra or {})
        return build_assessment_result(records or [], evidence, evaluation_date=DAY, expected_tenant_id=TENANT)

    def test_all_pilot_requirements_and_reviewed_plan_establish_pilot(self):
        result = self.build(review_profile())
        self.assertEqual(result["rollout_progress"]["current_stage"], "Ready for pilot")
        self.assertEqual(result["decision"], "Ready for a controlled pilot")
        self.assertEqual(result["rollout_progress"]["counts"]["pilot_blockers"], 0)
        self.assertFalse(result["actions"])

    def test_documented_example_is_valid_but_preserves_unfinished_reviews(self):
        profile = load_assessment_profile(Path(__file__).resolve().parents[1] / "examples" / "readiness-review.example.json")
        self.assertTrue(profile["available"], profile["reason"])
        result = self.build(profile)
        self.assertEqual(result["rollout_progress"]["current_stage"], "Preparing for pilot")
        self.assertGreater(result["rollout_progress"]["counts"]["pilot_blockers"], 0)
        controls = {row["control_id"]: row for row in result["controls"]}
        self.assertEqual(controls["LICENSE.APPS"]["status"], "Action required")
        self.assertEqual(controls["DATA.RETENTION"]["status"], "Observed")

    def test_technical_passes_without_reviewed_plan_do_not_authorize_pilot(self):
        profile = review_profile()
        del profile["readiness_review"]["pilot_plan"]
        result = self.build(profile)
        self.assertEqual(result["rollout_progress"]["current_stage"], "Preparing for pilot")
        self.assertEqual(result["decision"], "Readiness unconfirmed")

    def test_missing_control_reason_does_not_invent_an_earlier_finding(self):
        result = self.build()
        requirements = {row["id"]: row for stage in result["rollout_progress"]["stages"] for row in stage["requirements"]}
        self.assertNotIn("earlier", requirements["pilot.DATA.DLP"]["reason"])
        self.assertNotIn("historical", requirements["pilot.DATA.DLP"]["reason"])
        self.assertNotIn("profile", requirements["pilot.scope"]["reason"])
        self.assertNotIn("profile", requirements["pilot.plan"]["reason"])
        self.assertTrue(any(row["id"] == "pilot.plan" for row in result["rollout_progress"]["next_requirements"]))

    def test_first_completed_milestone_has_a_met_requirement_when_scope_missing(self):
        result = self.build(records=[source_record()])
        progress = result["rollout_progress"]
        self.assertEqual(progress["current_stage_id"], "preparing")
        first = progress["stages"][0]
        self.assertEqual(first["status"], "complete")
        self.assertTrue(all(row["status"] == "met" for row in first["requirements"]))

    def test_no_evidence_is_just_started_and_usage_never_proves_rollout(self):
        self.assertEqual(self.build()["rollout_progress"]["current_stage"], "Just started")
        result = self.build(extra={"ai_usage": {"copilot_usage": {"available": True, "refresh_date": DAY,
                                                                  "periods": {"D28": {"active_users": 100}}}}})
        self.assertNotIn(result["rollout_progress"]["current_stage_id"], {"pilot", "broader"})

    def test_manual_pass_answers_only_matching_coverage(self):
        gap = source_record(ControlId="DATA.RETENTION", Service="Purview", Feature="Retention review unavailable",
                            FindingKey="retention.gap", Disposition="Coverage", Status="Not Assessed", EvidenceKey="purview_policy_detail")
        result = self.build(review_profile(), [gap])
        saved = next(row for row in result["recommendations"] if row.get("FindingKey") == "retention.gap")
        self.assertEqual(saved["Disposition"], "Reference")
        self.assertTrue(saved["ReviewClosure"])
        self.assertFalse(result["actions"])

    def test_sensitive_access_gap_can_be_answered_without_a_licensed_export(self):
        gap = source_record(ControlId="DATA-001", Service="Data Exposure", Feature="Sensitive-data exposure evidence",
                            FindingKey="data_exposure.coverage.sensitive-data_exposure_evidence",
                            Disposition="Coverage", Status="Not Assessed", EvidenceKey="")
        result = self.build(review_profile(), [gap])
        self.assertFalse(result["actions"])
        self.assertEqual(result["rollout_progress"]["current_stage_id"], "pilot")

    def test_manual_pass_does_not_overwrite_observed_or_historical_risk(self):
        for changes in ({}, {"SourceType": "prior_assessment", "SourceFile": "earlier.json"}):
            with self.subTest(changes=changes):
                result = self.build(review_profile(), [source_record(**changes)])
                self.assertTrue(any(row.get("FindingKey") == "identity.mfa" for row in result["actions"]))
                self.assertEqual(result["rollout_progress"]["current_stage_id"], "preparing")

    def condition_profile(self, **changes):
        profile = review_profile()
        profile["readiness_review"]["pilot_conditions"] = [dict({
            "action_id": "RISK-001", "scope_id": "pilot", "reviewed_at": "2026-09-14",
            "reviewer_role": "Identity owner", "condition": "Exclude the two unregistered accounts until their registration is verified.",
            "evidence_reference": "Fictional pilot access condition",
        }, **changes)]
        return profile

    def test_dated_specific_pilot_condition_does_not_close_broader_issue(self):
        result = self.build(self.condition_profile(), [source_record()])
        self.assertEqual(result["rollout_progress"]["current_stage_id"], "pilot")
        self.assertEqual(result["counts"]["remediation"], 1)
        self.assertTrue(result["rollout_progress"]["pilot_conditions"])
        broader = next(row for row in result["rollout_progress"]["stages"] if row["id"] == "broader")
        self.assertEqual(next(row for row in broader["requirements"] if row["id"] == "broader.actions")["status"], "issue")

    def test_condition_cannot_bypass_critical_historical_or_newer_scope(self):
        for changes in ({"Status": "Critical"}, {"SourceType": "prior_assessment"}):
            self.assertEqual(self.build(self.condition_profile(), [source_record(**changes)])["rollout_progress"]["current_stage_id"], "preparing")
        profile = self.condition_profile(reviewed_at="2026-09-10")
        profile["readiness_review"]["pilot_scope"]["reviewed_at"] = "2026-09-11"
        profile["readiness_review"]["pilot_plan"]["reviewed_at"] = "2026-09-11"
        self.assertEqual(self.build(profile, [source_record()])["rollout_progress"]["current_stage_id"], "preparing")

    def test_same_date_opposite_review_results_conflict_even_with_same_rationale(self):
        profile = review_profile()
        opposed = dict(profile["readiness_review"]["control_reviews"][0], result="fail")
        profile["readiness_review"]["control_reviews"].append(opposed)
        result = self.build(profile)
        self.assertEqual(result["rollout_progress"]["current_stage_id"], "preparing")
        self.assertTrue(any(row.get("selection") == "conflict" for row in result["evidence"]))

    def expansion_profile(self):
        profile = review_profile()
        review = profile["readiness_review"]
        review.update({
            "expansion_scope": {"id": "expansion", "description": "Fifty fictional users and approved content", "population_count": 50,
                                "reviewed_at": "2026-09-13", "reviewer_role": "Business sponsor", "evidence_reference": "Fictional expansion scope"},
            "pilot_outcomes": {"scope_id": "pilot", "reviewed_at": "2026-09-13", "reviewer_role": "Business sponsor",
                               "period_start": "2026-09-02", "period_end": "2026-09-12", "outcome_summary": "Drafting time decreased with reviewed quality maintained.",
                               "success_measures_met": True, "risk_review": "No sensitive-data disclosure; reviewed quality exceptions were resolved.",
                               "evidence_reference": "Fictional sponsor outcome review"},
            "expansion_approval": {"scope_id": "expansion", "reviewed_at": "2026-09-15", "reviewer_role": "Business sponsor",
                                   "approved": True, "rationale": "The outcomes and larger-population control reviews meet the agreed criteria.",
                                   "evidence_reference": "Fictional expansion decision"},
        })
        review["control_reviews"].extend(dict(row, scope_id="expansion") for row in list(review["control_reviews"]))
        return profile

    def test_broader_adoption_requires_outcomes_approval_and_larger_scope_reviews(self):
        profile = self.expansion_profile()
        complete = self.build(profile)
        self.assertEqual(complete["rollout_progress"]["current_stage_id"], "broader")
        self.assertEqual(complete["decision"], "Ready for broader adoption")
        for missing in ("pilot_outcomes", "expansion_approval", "expansion_reviews"):
            partial = copy.deepcopy(profile)
            if missing == "expansion_reviews":
                partial["readiness_review"]["control_reviews"] = [row for row in partial["readiness_review"]["control_reviews"] if row["scope_id"] == "pilot"]
            else:
                del partial["readiness_review"][missing]
            result = self.build(partial)
            self.assertEqual(result["rollout_progress"]["current_stage_id"], "pilot")
            self.assertNotEqual(result["decision"], "Ready for broader adoption")

    def test_older_expansion_approval_or_unexpanded_population_does_not_advance(self):
        profile = self.expansion_profile()
        profile["readiness_review"]["expansion_approval"]["reviewed_at"] = "2026-09-13"
        self.assertEqual(self.build(profile)["rollout_progress"]["current_stage_id"], "pilot")
        profile = self.expansion_profile()
        profile["readiness_review"]["expansion_scope"]["population_count"] = 10
        self.assertEqual(self.build(profile)["rollout_progress"]["current_stage_id"], "pilot")

    def test_precise_controls_do_not_borrow_consent_or_license_assignment(self):
        result = self.build(records=[source_record(ControlId="APPS-001", Service="Entra", Feature="Application consent",
            EvidenceKey="app_consent_policy_detail", ImpactArea="Apps, connectors & agents", Observation="User application consent is restricted.",
            Disposition="Assurance", Recommendation="", Status="Success")])
        status = {row["control_id"]: row["status"] for row in result["controls"]}
        self.assertEqual(status["APPS.CONSENT"], "Observed")
        self.assertEqual(status["APPS.CONNECTIONS"], "Not established")
        self.assertTrue(any(row.get("ControlId") == "LICENSE.APPS" for row in result["actions"]))

    def test_counts_and_results_are_deterministic_without_mutating_review(self):
        profile = review_profile()
        original = copy.deepcopy(profile)
        first = self.build(profile)
        second = self.build(profile)
        self.assertEqual(profile, original)
        self.assertEqual(first, second)
        for stage in first["rollout_progress"]["stages"]:
            count = stage["counts"]
            self.assertEqual(count["total"], count["met"] + count["open"] + count["issues"] + count["conditions"])


if __name__ == "__main__":
    unittest.main()

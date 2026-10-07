"""Delegated enrichment, Copilot limited mode, the audit preview and adoption guidance."""

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from Core import delegated_auth
from Core.copilot_audit import build_copilot_audit_recommendations, collect_copilot_interaction_audit, summarize_records
from Core.copilot_settings import collect_copilot_limited_mode, limited_mode_value, not_collected
from Core.get_graph_client import GraphRequestError


class DelegatedStateTests(unittest.TestCase):
    def test_state_respects_mode_profile_cache_and_terminal(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(delegated_auth, "CACHE_DIRECTORY", Path(folder)), \
                patch.object(delegated_auth, "can_prompt", return_value=False):
            self.assertEqual("off", delegated_auth.delegated_state("t", "c", "off"))
            self.assertEqual("off", delegated_auth.delegated_state("t", "c", "auto", permission_profile="restricted"))
            self.assertEqual("unavailable", delegated_auth.delegated_state("t", "c", "auto"))
            self.assertEqual("can_prompt", delegated_auth.delegated_state("t", "c", "required"))
            (Path(folder) / f"{delegated_auth._tenant_key('t', 'c')}.json").write_text("{}", encoding="utf-8")
            self.assertEqual("cached", delegated_auth.delegated_state("t", "c", "auto"))

    def test_unavailable_session_is_reported_without_prompting(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(delegated_auth, "CACHE_DIRECTORY", Path(folder)), \
                patch.object(delegated_auth, "can_prompt", return_value=False), \
                patch.object(delegated_auth, "_build_credential", side_effect=AssertionError("must not build a credential")):
            session, reason = asyncio.run(delegated_auth.acquire_delegated_session("t", "c", mode="auto"))
        self.assertIsNone(session)
        self.assertIn("No cached delegated sign-in", reason)

    def test_token_from_another_tenant_is_rejected(self):
        import base64, json
        body = base64.urlsafe_b64encode(json.dumps({"tid": "other"}).encode()).decode().rstrip("=")
        credential = SimpleNamespace(authenticate=lambda scopes: SimpleNamespace(serialize=lambda: "{}"),
                                     get_token=lambda *scopes: SimpleNamespace(token=f"h.{body}.s"))
        with tempfile.TemporaryDirectory() as folder, patch.object(delegated_auth, "CACHE_DIRECTORY", Path(folder)), \
                patch.object(delegated_auth, "can_prompt", return_value=True), \
                patch.object(delegated_auth, "_build_credential", return_value=credential):
            session, reason = asyncio.run(delegated_auth.acquire_delegated_session("tenant", "c", mode="auto"))
        self.assertIsNone(session)
        self.assertIn("different tenant", reason)


class DelegatedReuseTests(unittest.TestCase):
    def test_saved_sign_in_is_reported_as_reused(self):
        import base64, json
        body = base64.urlsafe_b64encode(json.dumps({"tid": "tenant", "upn": "admin@contoso.com"}).encode()).decode().rstrip("=")
        credential = SimpleNamespace(authenticate=lambda **_: self.fail("must not prompt"),
                                     get_token=lambda *scopes: SimpleNamespace(token=f"h.{body}.s"))
        with tempfile.TemporaryDirectory() as folder, patch.object(delegated_auth, "CACHE_DIRECTORY", Path(folder)), \
                patch.object(delegated_auth, "_load_record", return_value=object()), \
                patch.object(delegated_auth, "can_prompt", return_value=False), \
                patch.object(delegated_auth, "_build_credential", return_value=credential):
            (Path(folder) / f"{delegated_auth._tenant_key('tenant', 'c')}.json").write_text("{}", encoding="utf-8")
            session, reason = asyncio.run(delegated_auth.acquire_delegated_session("tenant", "c", mode="auto"))
        self.assertEqual(reason, "")
        self.assertTrue(session.reused)
        self.assertEqual(session.identity["upn"], "admin@contoso.com")


class DelegatedCredentialTests(unittest.TestCase):
    def test_real_credential_builds_without_a_portless_redirect(self):
        # azure-identity rejects a redirect URI without a port (ValueError);
        # the credential must pick a free localhost port itself.
        try:
            import azure.identity  # noqa: F401
        except ImportError:
            self.skipTest("azure-identity is not installed")
        credential = delegated_auth._build_credential("tenant", "client", allow_prompt=False)
        self.assertIsNone(credential._parsed_url)

    def test_sign_in_failure_reason_includes_a_redacted_summary(self):
        def fail(**_):
            raise RuntimeError("AADSTS50011: redirect mismatch eyJhead.eyJbody.sig\ntrace")
        credential = SimpleNamespace(authenticate=fail, get_token=None)
        with tempfile.TemporaryDirectory() as folder, patch.object(delegated_auth, "CACHE_DIRECTORY", Path(folder)), \
                patch.object(delegated_auth, "can_prompt", return_value=True), \
                patch.object(delegated_auth, "_build_credential", return_value=credential):
            session, reason = asyncio.run(delegated_auth.acquire_delegated_session("tenant", "c", mode="required"))
        self.assertIsNone(session)
        self.assertIn("RuntimeError: AADSTS50011: redirect mismatch <token>", reason)
        self.assertNotIn("eyJbody", reason)
        self.assertNotIn("trace", reason)


class FakeSession:
    def __init__(self, payload=None, error=None):
        self.payload, self.error = payload, error

    def graph_client(self):
        session = self

        class Client:
            async def get_json(self, path, params=None):
                if session.error:
                    raise session.error
                return session.payload

            async def aclose(self):
                pass
        return Client()


class LimitedModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_limited_mode_is_collected_with_delegated_provenance(self):
        state = await collect_copilot_limited_mode(FakeSession({"isEnabledForGroup": True, "groupId": "g-1"}))
        self.assertTrue(state["available"])
        self.assertEqual("delegated_user", state["credential_type"])
        self.assertEqual("Enabled for group g-1", limited_mode_value(state))

    async def test_denied_or_missing_session_is_not_collected(self):
        denied = await collect_copilot_limited_mode(FakeSession(error=GraphRequestError(403, "denied")))
        self.assertFalse(denied["available"])
        self.assertIn("Global Reader", denied["reason"])
        missing = await collect_copilot_limited_mode(None)
        self.assertEqual("not_requested", missing["availability_status"])
        self.assertIsNone(limited_mode_value(missing))

    def test_admin_review_shows_limited_mode_row(self):
        from Core.copilot_admin_review import build_admin_review
        m365 = SimpleNamespace(copilot_limited_mode={"available": True, "is_enabled_for_group": False})
        review = build_admin_review(m365, None)
        row = next(item for item in review["rows"] if item["Topic"] == "Copilot limited mode")
        self.assertEqual("Not enabled", row["Value"])
        absent = build_admin_review(SimpleNamespace(copilot_limited_mode=not_collected("Delegated sign-in is off.")), None)
        row = next(item for item in absent["rows"] if item["Topic"] == "Copilot limited mode")
        self.assertIsNone(row["Value"])
        self.assertIn("Delegated sign-in is off.", row["Follow-up"])


class AuditGraph:
    """Fake Audit Log Query API: optional 400 on record-type filter, then states."""

    def __init__(self, statuses, records, reject_record_type=False, create_error=None):
        self.statuses = list(statuses)
        self.records = records
        self.reject_record_type = reject_record_type
        self.create_error = create_error
        self.bodies = []

    async def request(self, method, path, json=None, **kwargs):
        self.bodies.append(json)
        if self.create_error:
            raise self.create_error
        if self.reject_record_type and "recordTypeFilters" in json:
            raise GraphRequestError(400, "Invalid record type")
        return SimpleNamespace(content=b"x", json=lambda: {"id": "q-1", "status": "notStarted"})

    async def get_json(self, path, params=None):
        return {"status": self.statuses.pop(0) if self.statuses else "running"}

    async def get_collection(self, path, params=None, max_pages=1000):
        return {"available": True, "availability_status": "available", "value": self.records, "truncated": False}


RECORDS = [
    {"createdDateTime": "2026-09-20T10:00:00Z", "userPrincipalName": "a@contoso.com",
     "auditData": {"CopilotEventData": {"AppHost": "Teams"}}},
    {"createdDateTime": "2026-09-20T11:00:00Z", "userPrincipalName": "A@contoso.com",
     "auditData": {"CopilotEventData": {"AppHost": "Word"}}},
    {"createdDateTime": "2026-09-21T09:00:00Z", "userPrincipalName": "b@contoso.com",
     "auditData": {"CopilotEventData": {"AppHost": "Teams"}}},
]


class CopilotAuditTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, graph, **kwargs):
        async def no_sleep(seconds):
            return None
        with patch.dict(os.environ, {"CLIENT_SECRET": "x"}, clear=True):
            return await collect_copilot_interaction_audit(
                graph, sleep=no_sleep, clock=lambda: datetime(2026, 9, 22, tzinfo=timezone.utc), **kwargs)

    async def test_aggregates_and_user_metadata_by_default(self):
        evidence = await self.collect(AuditGraph(["running", "succeeded"], RECORDS), max_wait_minutes=5)
        self.assertTrue(evidence["available"])
        summary = evidence["summary"]
        self.assertEqual(3, summary["total_events"])
        self.assertEqual(2, summary["distinct_users"])
        self.assertEqual({"Teams": 2, "Word": 1}, summary["events_by_app_host"])
        self.assertEqual(summary["events_by_user"], {"a@contoso.com":2,"b@contoso.com":1})
        self.assertEqual("standard", evidence["evidence_quality"])
        rows = build_copilot_audit_recommendations(evidence)
        self.assertEqual("Reference", rows[0]["Disposition"])
        self.assertIn("3 Copilot interaction event(s) from 2 distinct user(s)", rows[0]["Observation"])

    async def test_operation_filter_is_used_when_record_type_is_rejected(self):
        graph = AuditGraph(["succeeded"], [], reject_record_type=True)
        evidence = await self.collect(graph, max_wait_minutes=5)
        self.assertEqual("operationFilters", evidence["filter_basis"])
        self.assertIn("no Copilot interaction events", build_copilot_audit_recommendations(evidence)[0]["Observation"])

    async def test_time_budget_records_partial_evidence(self):
        evidence = await self.collect(AuditGraph(["running"] * 50, RECORDS), max_wait_minutes=1)
        self.assertFalse(evidence["available"])
        self.assertEqual("partial", evidence["availability_status"])
        self.assertEqual("q-1", evidence["query_id"])
        self.assertEqual([], build_copilot_audit_recommendations(evidence))

    async def test_denied_query_names_the_permission(self):
        evidence = await self.collect(AuditGraph([], [], create_error=GraphRequestError(403, "denied")))
        self.assertIn("AuditLogsQuery.Read.All", evidence["reason"])

    def test_user_detail_only_when_requested(self):
        self.assertEqual({"a@contoso.com": 2, "b@contoso.com": 1},
                         summarize_records(RECORDS, include_user_detail=True)["events_by_user"])


class AdoptionGuidanceTests(unittest.TestCase):
    RESULT = {
        "decision": "Readiness unconfirmed",
        "actions": [
            {"RecommendationId": "R1", "Feature": "Require MFA", "Priority": "High", "SecurityGate": True,
             "ReadinessStage": "Before pilot", "DomainId": "identity"},
            {"RecommendationId": "R2", "Feature": "Expand DLP", "Priority": "Medium", "SecurityGate": True,
             "ReadinessStage": "Before broad rollout", "DomainId": "data_protection"},
            {"RecommendationId": "R3", "Feature": "Agree success measures", "Priority": "Low", "SecurityGate": False,
             "ReadinessStage": "", "DomainId": "adoption"},
        ],
        "controls": [
            {"control_id": "IDENTITY.MFA", "title": "Confirm MFA", "status": "Action required", "security_gate": True},
            {"control_id": "DATA.AUDIT", "title": "Confirm audit", "status": "Observed", "security_gate": True},
        ],
    }

    def test_actions_are_placed_by_rollout_stage_and_guidance_is_advisory(self):
        import copy
        from Core.adoption_guidance import OWNER_REVIEW, build_adoption_guidance, guidance_rows
        original = copy.deepcopy(self.RESULT)
        guidance = build_adoption_guidance(self.RESULT, {"R1": 1, "R2": 2, "R3": 3})
        self.assertEqual(original, self.RESULT)  # never mutates the assessment result
        phases = {phase["id"]: phase for phase in guidance["phases"]}
        self.assertEqual(["R1"], [item["recommendation_id"] for item in phases["days_0_30"]["actions"]])
        self.assertEqual(["R3"], [item["recommendation_id"] for item in phases["days_31_60"]["actions"]])
        self.assertEqual(["R2"], [item["recommendation_id"] for item in phases["days_61_90"]["actions"]])
        statuses = {row["control_id"]: row["status"] for row in guidance["baseline"]}
        self.assertEqual("Action needed", statuses["IDENTITY.MFA"])
        self.assertEqual("Evidence supports it", statuses["DATA.AUDIT"])
        self.assertEqual("Not assessed in this report", statuses["DATA.DLP"])
        self.assertEqual(19, len(guidance["baseline"]))
        self.assertTrue(all(item["status"] == OWNER_REVIEW for item in guidance["external_ai_checks"]))
        self.assertTrue(all(item["status"] == OWNER_REVIEW for phase in guidance["phases"] for item in phase["checklist"]))
        self.assertTrue(any(row["Type"] == "Adoption checklist" for row in guidance_rows(guidance)))

    def test_baseline_covers_every_required_question(self):
        from Core.adoption_guidance_content import BASELINE_CONTROLS
        from Core.assessment_result import QUESTIONS
        self.assertEqual({question[0] for question in QUESTIONS}, set(BASELINE_CONTROLS))

    def test_external_ai_checklist_is_shared(self):
        from Core.adoption_guidance_content import CROSS_PLATFORM_MANUAL_CHECKS as content
        from Core.assessment_model import CROSS_PLATFORM_MANUAL_CHECKS as model
        self.assertIs(content, model)


if __name__ == "__main__":
    unittest.main()

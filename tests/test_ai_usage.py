"""Evidence-model tests for aggregate AI usage and optional enrichment."""

import asyncio
import io
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from Core.ai_usage import (
    _get_all_json,
    _get_report_payload,
    _normalize_copilot_period,
    _request_with_retry,
    _set_freshness,
    collect_copilot_usage,
    collect_license_coverage,
    collect_m365_app_readiness,
    collect_shadow_ai_usage,
)
from Core.assessment_model import summarize_readiness
from Core.evidence_layer import build_evidence_bundle
from Core.export_recommendations import export_to_html
from Core.get_power_platform_client import _merge_environment_responses
from Core.get_m365_client import _parse_csv_report, extract_m365_insights_from_client, get_m365_client
from Core.new_recommendation import NOT_ASSESSED_STATUS, new_recommendation
from Recommendations.m365.m365_insights import (
    get_copilot_adoption_percentage,
    get_copilot_adoption_recommendation,
    get_copilot_licensed_count,
    get_users_observation,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.paths = []

    async def get(self, path, params=None):
        self.paths.append((path, params))
        return self.responses.pop(0)


def summary_payload(period, enabled, active, prompts=0, apps=None, refresh="2026-09-02"):
    row = {
        "reportPeriod": int(period[1:]),
        "microsoft365CopilotEnabledUsers": enabled,
        "microsoft365CopilotActiveUsers": active,
        "totalPromptsSubmitted": prompts,
        "averagePromptsSubmitted": prompts / active if active else 0,
    }
    row.update(apps or {})
    return {"value": [{"reportRefreshDate": refresh, "adoptionByProduct": [row]}]}


class CopilotUsageTests(unittest.TestCase):
    def test_legacy_graph_csv_report_parser_returns_rows(self):
        rows = _parse_csv_report(b"User Principal Name,Send Count\r\nuser@example.com,4\r\n")
        self.assertEqual(rows, [{"User Principal Name": "user@example.com", "Send Count": "4"}])

    def test_active_deployment_normalizes_activation_engagement_and_apps(self):
        result = _normalize_copilot_period(
            summary_payload("D28", 20, 12, 144, {
                "wordEnabledUsers": 20,
                "wordActiveUsers": 8,
                "copilotChatWorkEnabledUsers": 20,
                "copilotChatWorkActiveUsers": 6,
            }),
            "D28",
        )
        self.assertEqual(result["active_rate"], 60.0)
        self.assertEqual(result["unused_licenses"], 8)
        self.assertEqual(result["total_prompts"], 144)
        self.assertEqual(result["apps"]["Word"]["active_users"], 8)
        self.assertEqual(result["apps"]["Copilot Chat (work)"]["active_users"], 6)

    def test_no_license_tenant_is_valid_predeployment_evidence(self):
        result = _normalize_copilot_period(summary_payload("D28", 0, 0), "D28")
        self.assertEqual(result["enabled_users"], 0)
        self.assertIsNone(result["active_rate"])

    def test_missing_report_is_not_converted_to_zero_usage(self):
        responses = [FakeResponse(403, {}, "Forbidden") for _ in range(5)]
        evidence = asyncio.run(collect_copilot_usage(FakeClient(responses)))
        self.assertFalse(evidence["available"])
        self.assertEqual(evidence["periods"], {})
        self.assertIn("Permission", evidence["reason"])
        self.assertEqual(evidence["deployment_state"], "Unknown")

    def test_malformed_report_is_unavailable(self):
        responses = [FakeResponse(200, ValueError("bad json")) for _ in range(5)]
        evidence = asyncio.run(collect_copilot_usage(FakeClient(responses)))
        self.assertFalse(evidence["available"])
        self.assertIn("unreadable", evidence["reason"])

    def test_graph_usage_is_stale_after_seven_days(self):
        old = (datetime.now(timezone.utc) - timedelta(days=8)).date().isoformat()
        evidence = _set_freshness({"refresh_date": old}, 7)
        self.assertTrue(evidence["stale"])
        self.assertEqual(evidence["freshness"], "Stale")

    def test_user_detail_pagination_reads_every_page(self):
        client = FakeClient([
            FakeResponse(200, {"value": [{"id": "one"}], "@odata.nextLink": "https://graph.microsoft.com/page2"}),
            FakeResponse(200, {"value": [{"id": "two"}]}),
        ])
        payload, error = asyncio.run(_get_all_json(client, "/first"))
        self.assertEqual(error, "")
        self.assertEqual([row["id"] for row in payload["value"]], ["one", "two"])
        self.assertEqual(len(client.paths), 2)

    def test_report_csv_normalizes_microsoft_headers(self):
        csv_text = (
            "Report Refresh Date,Report Period,Microsoft 365 Copilot Enabled Users,"
            "Microsoft 365 Copilot Active Users,PowerPoint Active Users\n"
            "2026-09-02,28,10,6,3\n"
        )
        client = FakeClient([FakeResponse(200, {}, csv_text)])
        payload, error = asyncio.run(_get_report_payload(client, "/report"))
        self.assertEqual(error, "")
        self.assertEqual(payload["value"][0]["microsoft365CopilotActiveUsers"], "6")
        self.assertEqual(payload["value"][0]["powerPointActiveUsers"], "3")

    def test_version_one_payload_does_not_invent_zero_prompt_metrics(self):
        payload = {
            "value": [{
                "reportPeriod": 28,
                "anyAppEnabledUsers": 10,
                "anyAppActiveUsers": 4,
            }]
        }
        result = _normalize_copilot_period(payload, "D28")
        self.assertIsNone(result["total_prompts"])
        self.assertIsNone(result["average_prompts"])

    def test_missing_per_app_activity_is_not_converted_to_zero(self):
        result = _normalize_copilot_period(
            summary_payload("D28", 5, 1, apps={"wordEnabledUsers": 5}),
            "D28",
        )
        self.assertIsNone(result["apps"]["Word"]["active_users"])
        self.assertIsNone(result["apps"]["Word"]["active_rate"])

    def test_license_coverage_uses_all_pages_and_estimated_eligible_users(self):
        copilot = "639dec6b-bb19-468b-871c-c5c441c4b0cb"
        base = "11111111-1111-1111-1111-111111111111"
        client = FakeClient([
            FakeResponse(200, {"value": [
                {"accountEnabled": True, "assignedLicenses": [{"skuId": copilot}, {"skuId": base}]},
            ], "@odata.nextLink": "https://graph.microsoft.com/page2"}),
            FakeResponse(200, {"value": [
                {"accountEnabled": True, "assignedLicenses": [{"skuId": base}]},
                {"accountEnabled": False, "assignedLicenses": [{"skuId": base}]},
            ]}),
            FakeResponse(200, {"value": [
                {"skuId": base, "skuPartNumber": "SPB"},
                {"skuId": copilot, "skuPartNumber": "Microsoft_365_Copilot"},
            ]}),
        ])
        evidence = asyncio.run(collect_license_coverage(client))
        self.assertEqual(evidence["eligible_users"], 2)
        self.assertEqual(evidence["copilot_licensed_users"], 1)
        self.assertEqual(evidence["copilot_license_coverage"], 50.0)

    def test_paid_service_plans_recognize_new_business_and_bundle_skus(self):
        # The tenant supplies product/plan identities; newer GUIDs need not be
        # hardcoded. These fixture GUIDs are deliberately not production IDs.
        business, bundle, premium = "new-business-sku", "new-bundle-sku", "base-premium-sku"
        client = FakeClient([
            FakeResponse(200, {"value": [
                {"accountEnabled": True, "assignedLicenses": [{"skuId": business}, {"skuId": premium}]},
                {"accountEnabled": True, "assignedLicenses": [{"skuId": bundle}]},
                {"accountEnabled": True, "assignedLicenses": [{"skuId": premium}]},
            ]}),
            FakeResponse(200, {"value": [
                {"skuId": business, "servicePlans": [
                    {"servicePlanId": "a62f8878-de10-42f3-b68f-6149a25ceb97"},
                ]},
                {"skuId": bundle, "servicePlans": [
                    {"servicePlanName": "M365_COPILOT_BUSINESS_CHAT"},
                    {"servicePlanName": "EXCHANGE_S_STANDARD"},
                ]},
                {"skuId": premium, "skuPartNumber": "SPB", "servicePlans": [
                    {"servicePlanName": "Bing_Chat_Enterprise"},
                ]},
            ]}),
        ])
        evidence = asyncio.run(collect_license_coverage(client))
        self.assertEqual(evidence["copilot_licensed_users"], 2)
        self.assertEqual(evidence["eligible_users"], 3)
        self.assertEqual(evidence["copilot_license_coverage"], 66.67)
        self.assertEqual(evidence["availability_status"], "available")

    def test_chat_security_and_storage_addons_do_not_establish_base_eligibility(self):
        client = FakeClient([
            FakeResponse(200, {"value": [
                {"accountEnabled": True, "assignedLicenses": [{"skuId": "addon"}]},
            ]}),
            FakeResponse(200, {"value": [{
                "skuId": "addon", "skuPartNumber": "COPILOT_CHAT",
                "servicePlans": [
                    {"servicePlanName": "Bing_Chat_Enterprise"},
                    {"servicePlanName": "EXCHANGE_S_FOUNDATION"},
                    {"servicePlanName": "ATP_ENTERPRISE"},
                    {"servicePlanName": "SHAREPOINTSTORAGE"},
                    {"servicePlanName": "COPILOT_STUDIO_IN_COPILOT_FOR_M365"},
                ],
            }]}),
        ])
        evidence = asyncio.run(collect_license_coverage(client))
        self.assertEqual(evidence["eligible_users"], 0)
        self.assertEqual(evidence["copilot_licensed_users"], 0)
        self.assertIsNone(evidence["copilot_license_coverage"])
        self.assertEqual(evidence["users_without_recognized_base_license"], 1)

    def test_catalog_permission_failure_does_not_assert_zero_paid_licenses(self):
        client = FakeClient([
            FakeResponse(200, {"value": [
                {"accountEnabled": True, "assignedLicenses": [{"skuId": "new-business-sku"}]},
            ]}),
            FakeResponse(403, {}, "Forbidden"),
        ])
        evidence = asyncio.run(collect_license_coverage(client))
        self.assertEqual(evidence["availability_status"], "partial")
        self.assertIsNone(evidence["copilot_licensed_users"])
        self.assertIsNone(evidence["copilot_license_coverage"])
        self.assertIn("Subscription catalog", evidence["reason"])

    def test_unresolved_sku_preserves_known_count_but_marks_total_unknown(self):
        copilot = "639dec6b-bb19-468b-871c-c5c441c4b0cb"
        client = FakeClient([
            FakeResponse(200, {"value": [
                {"accountEnabled": True, "assignedLicenses": [{"skuId": copilot}, {"skuId": "premium"}]},
                {"accountEnabled": True, "assignedLicenses": [{"skuId": "unresolved"}, {"skuId": "premium"}]},
            ]}),
            FakeResponse(200, {"value": [{"skuId": "premium", "skuPartNumber": "SPB"}]}),
        ])
        evidence = asyncio.run(collect_license_coverage(client))
        self.assertEqual(evidence["known_copilot_licensed_users"], 1)
        self.assertIsNone(evidence["copilot_licensed_users"])
        self.assertIsNone(evidence["copilot_license_coverage"])
        self.assertEqual(evidence["unresolved_assigned_sku_count"], 1)

    def test_partial_user_pagination_does_not_publish_complete_license_count(self):
        copilot = "639dec6b-bb19-468b-871c-c5c441c4b0cb"
        client = FakeClient([
            FakeResponse(200, {"value": [
                {"accountEnabled": True, "assignedLicenses": [{"skuId": copilot}, {"skuId": "premium"}]},
            ], "@odata.nextLink": "https://graph.microsoft.com/page2"}),
            FakeResponse(403, {}, "Forbidden"),
            FakeResponse(200, {"value": [{"skuId": "premium", "skuPartNumber": "SPB"}]}),
        ])
        evidence = asyncio.run(collect_license_coverage(client))
        self.assertEqual(evidence["known_copilot_licensed_users"], 1)
        self.assertIsNone(evidence["copilot_licensed_users"])
        self.assertTrue(evidence["truncated"])
        self.assertEqual(evidence["availability_status"], "partial")

    def test_m365_apps_partial_usage_remains_available(self):
        client = FakeClient([
            FakeResponse(200, {"value": [{"reportRefreshDate": "2026-09-02", "reportDate": "2026-09-01", "word": 9, "excel": 0}]}),
            FakeResponse(200, {"value": [{"reportRefreshDate": "2026-09-02", "reportDate": "2026-09-01", "windows": 8, "web": 3}]}),
        ])
        evidence = asyncio.run(collect_m365_app_readiness(client))
        self.assertTrue(evidence["available"])
        self.assertEqual(evidence["app_user_counts"][0]["word"], 9)
        self.assertEqual(evidence["platform_user_counts"][0]["web"], 3)

    def test_m365_apps_documented_nested_user_counts_are_normalized(self):
        client = FakeClient([
            FakeResponse(200, {"value": [{
                "reportRefreshDate": "2026-09-02", "reportPeriod": 30,
                "userCounts": [{"reportDate": "2026-09-01", "word": 9, "excel": 2}],
            }]}),
            FakeResponse(200, {"value": [{
                "reportRefreshDate": "2026-09-02", "reportPeriod": 30,
                "userCounts": [{"reportDate": "2026-09-01", "windows": 8, "web": 3}],
            }]}),
        ])
        evidence = asyncio.run(collect_m365_app_readiness(client))
        self.assertEqual(evidence["app_user_counts"][0]["word"], 9)
        self.assertEqual(evidence["app_user_counts"][0]["reportRefreshDate"], "2026-09-02")
        self.assertEqual(evidence["platform_user_counts"][0]["windows"], 8)
        self.assertEqual(evidence["app_activity_summary"]["word"]["peak_daily_active_users"], 9)
        self.assertEqual(evidence["platform_activity_summary"]["web"]["active_days"], 1)

    def test_m365_apps_quiet_latest_day_does_not_erase_window_activity(self):
        client = FakeClient([
            FakeResponse(200, {"value": [{
                "reportRefreshDate": "2026-09-02", "reportPeriod": 30,
                "userCounts": [
                    {"reportDate": "2026-09-01", "word": 3},
                    {"reportDate": "2026-09-02", "word": 0},
                ],
            }]}),
            FakeResponse(200, {"value": [{
                "reportRefreshDate": "2026-09-02", "reportPeriod": 30,
                "userCounts": [
                    {"reportDate": "2026-09-01", "web": 2},
                    {"reportDate": "2026-09-02", "web": 0},
                ],
            }]}),
        ])
        evidence = asyncio.run(collect_m365_app_readiness(client))
        self.assertEqual(evidence["app_activity_summary"]["word"]["peak_daily_active_users"], 3)
        self.assertEqual(evidence["app_activity_summary"]["word"]["latest_activity_date"], "2026-09-01")
        self.assertIn("word 3", evidence["readiness_signal"])

    def test_report_request_retries_throttling(self):
        client = FakeClient([
            FakeResponse(429, {}, "Throttled", {"Retry-After": "0"}),
            FakeResponse(200, {"value": []}),
        ])
        with mock.patch("Core.ai_usage.asyncio.sleep", new=mock.AsyncMock()):
            response = asyncio.run(_request_with_retry(client, "/report"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(client.paths), 2)


class LicenseCoverageIntegrationTests(unittest.TestCase):
    def _collect(self, coverage=None, failure=None):
        async def collection(path, params=None):
            return {"available": True, "value": [
                {"accountEnabled": True, "assignedLicenses": [{
                    "skuId": "c28afa23-5a37-4837-938f-7cc48d0cca5c",
                }]},
            ] if path == "/v1.0/users" else []}

        graph = SimpleNamespace(
            get_collection=mock.AsyncMock(side_effect=collection),
            get_csv=mock.AsyncMock(return_value=[]),
        )
        collector = mock.AsyncMock(
            return_value={"license_coverage": coverage or {}}, side_effect=failure,
        )
        with mock.patch("Core.ai_usage.collect_ai_usage", collector), mock.patch("sys.stdout", new=io.StringIO()):
            return asyncio.run(get_m365_client(graph))

    def test_partial_license_evidence_survives_client_and_recommendations(self):
        coverage = {
            "available": True, "availability_status": "partial",
            "reason": "One assigned SKU could not be resolved",
            "total_users": 3, "enabled_users": 3, "eligible_users": 2,
            "copilot_licensed_users": None, "known_copilot_licensed_users": 1,
            "copilot_license_coverage": None, "coverage_population": "recognized base plans",
            "license_detection_basis": "paid service-plan identity",
            "subscription_catalog_available": True, "unresolved_assigned_sku_count": 1,
            "users_without_recognized_base_license": 1, "truncated": True,
        }
        client = self._collect(coverage)
        self.assertEqual(client.license_coverage, coverage)
        self.assertEqual(client.users_summary["availability_status"], "partial")
        self.assertEqual(client.users_summary["license_detection_basis"], "paid service-plan identity")
        self.assertEqual(client.users_summary["unresolved_assigned_sku_count"], 1)
        self.assertTrue(client.users_summary["sampled"])
        self.assertEqual(client.users_summary["total"], 1)  # Keep the complete primary inventory.
        insights = extract_m365_insights_from_client(client)
        self.assertIsNone(insights["copilot_licensed_users"])
        self.assertIsNone(insights["copilot_adoption_rate"])
        self.assertIsNone(get_copilot_licensed_count(insights))
        self.assertIsNone(get_copilot_adoption_percentage(insights))
        observation = get_users_observation(insights)
        self.assertIn("at least 1", observation)
        self.assertIn("could not be resolved", observation)
        self.assertNotIn("no Copilot licenses", observation)
        self.assertIn("subscription catalog", get_copilot_adoption_recommendation(insights))

    def test_unavailable_collection_does_not_fall_back_to_old_sku_guesses(self):
        client = self._collect({
            "available": False, "availability_status": "unavailable", "reason": "Authentication failed",
        })
        insights = extract_m365_insights_from_client(client)
        self.assertEqual(insights["total_users"], 1)
        self.assertIsNone(insights["copilot_licensed_users"])
        self.assertIsNone(insights["copilot_eligible_users"])
        self.assertEqual(insights["copilot_license_coverage_reason"], "Authentication failed")
        self.assertIn("not established", get_users_observation(insights))

    def test_collector_exception_keeps_license_count_unknown(self):
        client = self._collect(failure=RuntimeError("test failure"))
        insights = extract_m365_insights_from_client(client)
        self.assertIsNone(insights["copilot_licensed_users"])
        self.assertEqual(insights["copilot_license_coverage_status"], "unavailable")
        self.assertIn("RuntimeError", insights["copilot_license_coverage_reason"])

    def test_confirmed_zero_remains_distinct_from_unknown(self):
        client = self._collect({
            "available": True, "availability_status": "available", "reason": "",
            "total_users": 1, "enabled_users": 1, "eligible_users": 1,
            "copilot_licensed_users": 0, "known_copilot_licensed_users": 0,
            "copilot_license_coverage": 0,
        })
        insights = extract_m365_insights_from_client(client)
        self.assertEqual(get_copilot_licensed_count(insights), 0)
        self.assertEqual(get_copilot_adoption_percentage(insights), 0)
        self.assertIn("no Copilot licenses", get_users_observation(insights))
        self.assertIn("bounded pilot", get_copilot_adoption_recommendation(insights))

    def test_unavailable_client_exposes_unknown_licensing(self):
        insights = extract_m365_insights_from_client(None)
        self.assertIsNone(insights["copilot_licensed_users"])
        self.assertIsNone(insights["copilot_eligible_users"])
        self.assertIsNone(get_copilot_adoption_percentage(insights))
        self.assertEqual(insights["copilot_license_coverage_status"], "unavailable")


class ShadowAiTests(unittest.TestCase):
    def test_permission_missing(self):
        evidence = asyncio.run(collect_shadow_ai_usage(FakeClient([FakeResponse(403, {}, "Forbidden")])))
        self.assertFalse(evidence["available"])
        self.assertIn("Permission", evidence["reason"])

    def test_discovery_not_enabled(self):
        evidence = asyncio.run(collect_shadow_ai_usage(FakeClient([FakeResponse(200, {"value": []})])))
        self.assertFalse(evidence["available"])
        self.assertIn("Enable Defender", evidence["reason"])

    def test_no_generative_ai_activity_is_a_valid_observation(self):
        client = FakeClient([
            FakeResponse(200, {"value": [{"id": "stream"}]}),
            FakeResponse(200, {"value": [{"displayName": "CRM", "category": "business"}]}),
        ])
        evidence = asyncio.run(collect_shadow_ai_usage(client))
        self.assertTrue(evidence["available"])
        self.assertEqual(evidence["application_count"], 0)
        self.assertIn("no generative AI", evidence["reason"])

    def test_observed_generative_ai_is_aggregate_only(self):
        client = FakeClient([
            FakeResponse(200, {"value": [{"id": "stream"}]}),
            FakeResponse(200, {"value": [{
                "displayName": "ChatGPT", "category": "generativeAi", "riskScore": 8,
                "userCount": 4, "uploadNetworkTrafficInBytes": 100,
                "downloadNetworkTrafficInBytes": 50, "lastSeenDateTime": "2026-09-02T00:00:00Z",
            }]}),
        ])
        evidence = asyncio.run(collect_shadow_ai_usage(client))
        self.assertEqual(evidence["application_count"], 1)
        self.assertEqual(evidence["applications"][0]["active_users"], 4)
        self.assertEqual(evidence["applications"][0]["traffic_bytes"], 150)
        self.assertNotIn("users", evidence["applications"][0])


class PowerPlatformAggregationTests(unittest.TestCase):
    def test_multi_environment_results_are_aggregated(self):
        merged = _merge_environment_responses("apps", [
            ("env-a", FakeResponse(200, {"value": [{"id": "app-a", "properties": {"displayName": "A"}}]})),
            ("env-b", FakeResponse(200, {"value": [{"id": "app-b", "properties": {"displayName": "B"}}]})),
        ])
        self.assertEqual(merged.status_code, 200)
        rows = merged.json()["value"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["properties"]["environmentId"] for row in rows}, {"env-a", "env-b"})

    def test_one_environment_failure_does_not_discard_successes(self):
        merged = _merge_environment_responses("flows", [
            ("env-a", FakeResponse(403, {}, "Forbidden")),
            ("env-b", FakeResponse(200, {"value": [{"id": "flow-b"}]})),
        ])
        self.assertEqual(merged.status_code, 200)
        self.assertEqual(len(merged.json()["value"]), 1)
        self.assertIn("HTTP 403", merged.text)


class ReportPrivacyAndDecisionTests(unittest.TestCase):
    def test_optional_extensibility_gap_does_not_change_core_readiness(self):
        optional_gap = new_recommendation(
            "Power Platform", "Unified inventory", "Optional inventory was not supplied.",
            "Supply an export if extensibility is in scope.", priority="Medium",
            status=NOT_ASSESSED_STATUS,
        )
        summary = summarize_readiness([optional_gap])
        self.assertEqual(summary["decision"], "Ready for a controlled pilot")
        self.assertEqual(len(summary["optional_coverage"]), 1)

    def test_html_never_contains_user_usage_identities(self):
        client = SimpleNamespace(
            users_summary={"copilot_license_coverage": 50, "coverage_population": "estimated eligible users"},
            copilot_usage={
                "available": True,
                "source": "Microsoft Graph",
                "periods": {"D28": {"enabled_users": 2, "active_users": 1, "active_rate": 50,
                                      "unused_licenses": 1, "total_prompts": 4,
                                      "average_prompts": 4, "apps": {
                                          "Excel": {"enabled_users": 2, "active_users": 0, "active_rate": 0.0},
                                          "Word": {"enabled_users": 2, "active_users": None, "active_rate": None},
                                      }}},
                "trend": [], "refresh_date": "2026-09-02", "freshness": "Fresh",
                "user_detail": [{"userPrincipalName": "secret.user@contoso.com",
                                 "displayName": "Secret User", "lastActivityDate": "2026-09-01"}],
            },
            m365_app_readiness={"available": False, "reason": "not supplied"},
            copilot_dashboard={"available": False, "reason": "not supplied"},
            shadow_ai_usage={"available": False, "reason": "disabled"},
            email_summary={}, teams_summary={}, sharepoint_summary={}, onedrive_summary={},
            activations_summary={}, active_users_summary={},
        )
        m365_info = {"_client": client, "licenses": []}
        recommendation = new_recommendation("M365", "Pilot", "Usage is available.", status="Success")
        bundle = build_evidence_bundle(
            [recommendation], (m365_info, []), {}, {}, {}, {}, {}, {},
        )
        self.assertIn("copilot_user_usage_detail", bundle["sheets"])

        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                html_path = export_to_html(bundle["recommendations"], evidence_bundle=bundle)
                body = Path(html_path).read_text(encoding="utf-8")
            finally:
                os.chdir(original_cwd)
        self.assertNotIn("secret.user@contoso.com", body)
        self.assertNotIn("Secret User", body)
        self.assertIn("Pilot suitability and adoption", body)
        self.assertIn("Why it matters", body)
        self.assertIn("What the pilot should prove", body)
        self.assertIn("How to decide whether to expand", body)
        self.assertNotIn("What “last 28 days” means", body)
        self.assertNotIn("“D” means days", body)
        self.assertIn("28 days", body)
        self.assertNotIn("D28 active users", body)
        self.assertNotIn("1 user were", body)
        metrics = bundle['assessment_result']['adoption_metrics']
        excel = next(m for m in metrics if 'excel' in m['label'].lower())
        self.assertEqual(excel['value'], 0)
        self.assertIn('<td>0</td>', body)
        unknown = [m for m in bundle['assessment_result']['evidence'] if 'word' in m.get('label', '').lower()]
        self.assertTrue(unknown)
        self.assertTrue(all(m['value'] is None for m in unknown))
        for mechanical_plural in (
            "recommendation(s)", "condition(s)", "section(s)", "tab(s)", "idea(s)",
            "user(s)", "policy(ies)",
        ):
            self.assertNotIn(mechanical_plural, body)
        self.assertNotIn("How these affect the analysis", body)

    def test_main_value_section_is_bounded_and_has_no_roi_percentage_claims(self):
        repository = Path(__file__).resolve().parent.parent
        source = (repository / "Core" / "customer_report.py").read_text(encoding="utf-8")
        self.assertIn("What the pilot should prove", source)
        for unsupported in ("20-30%", "30-40%", "15-20%", "70% while", "80% of valuable"):
            self.assertNotIn(unsupported, source)

        report_sources = "\n".join(
            (repository / "Recommendations" / "entra" / filename).read_text(encoding="utf-8")
            for filename in ("AAD_PREMIUM.py", "AAD_PREMIUM_P1.py")
        )
        for mechanical_plural in ("user(s)", "policy(ies)", "sign-in(s)"):
            self.assertNotIn(mechanical_plural, report_sources)


if __name__ == "__main__":
    unittest.main()

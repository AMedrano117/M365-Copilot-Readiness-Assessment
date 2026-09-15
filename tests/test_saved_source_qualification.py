"""Failed, partial and skipped source outcomes survive report rebuilding."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from Core.assessment_result import build_assessment_result
from Core.evidence_layer import _build_defender_incident_sheet
from Core.get_defender_client import DefenderClient, get_defender_client
from Core.saved_control_checks import assess_defender_configuration, qualify_saved_recommendations
from Core.source_evidence import source_is_complete, source_availability
from Recommendations.defender.defender_insights import DefenderInsights

DAY = "2026-09-15"
TENANT = "11111111-1111-1111-1111-111111111111"


def state(status="available"):
    return {"available": status in {"available", "partial"}, "availability_status": status,
            "truncated": status == "partial"}


def assessment(rows, client=None):
    return build_assessment_result(rows, {"collection_context": {"collected_at": DAY},
        "source_statuses": {"defender_" + key: value for key, value in getattr(client, "collection_status", {}).items()}},
        evaluation_date=DAY, expected_tenant_id=TENANT)


class SavedSourceQualificationTests(unittest.TestCase):
    def test_alert_success_does_not_establish_incidents(self):
        from Recommendations.defender import WINDEFATP, MTP
        client = DefenderClient()
        client.graph_security_available = True
        client.collection_status = {"alerts": state(), "incidents": state("unavailable")}
        client.data_sources = {"alerts": True, "incidents": False}
        insights = DefenderInsights(client)
        for module in (WINDEFATP, MTP):
            row = module.get_recommendation("SPE_E5", defender_insights=insights)
            self.assertEqual(row["Status"], "Not Assessed")
            self.assertNotIn("No security incidents", row["Observation"])
        result = assessment(assess_defender_configuration(client, DAY), client)
        self.assertEqual(next(row for row in result["controls"] if row["control_id"] == "THREAT.INCIDENTS")["status"], "Not established")
        self.assertIn("not established", _build_defender_incident_sheet(client)["summary"])

    def test_partial_empty_incidents_and_old_service_availability_are_unknown(self):
        for status in ("unavailable", "partial", "not_requested"):
            client = DefenderClient()
            client.collection_status = {"incidents": state(status)}
            row = assess_defender_configuration(client, DAY)[0]
            self.assertEqual(row["Disposition"], "Coverage")
        self.assertFalse(source_is_complete(SimpleNamespace(available=True, graph_security_available=True), "incidents"))

    def test_contradictory_source_metadata_cannot_establish_completion(self):
        client = SimpleNamespace(collection_status={"incidents": {"available": False, "availability_status": "available"}})
        self.assertFalse(source_is_complete(client, "incidents"))
        self.assertEqual(source_availability(client, "incidents"), "unavailable")
        self.assertFalse(source_is_complete(SimpleNamespace(), "incidents", {"available": True, "availability_status": "partial"}))
        self.assertEqual(source_availability(SimpleNamespace(), "incidents", {"available": True, "availability_status": "available", "truncated": True}), "partial")

    def test_complete_empty_incidents_are_a_scoped_positive(self):
        client = DefenderClient()
        client.collection_status = {"incidents": state()}
        row = assess_defender_configuration(client, DAY)[0]
        self.assertEqual(row["Disposition"], "Assurance")
        result = assessment([row], client)
        self.assertEqual(next(row for row in result["controls"] if row["control_id"] == "THREAT.INCIDENTS")["status"], "Observed")
        self.assertEqual(next(row for row in result["controls"] if row["control_id"] == "ENDPOINT.POSTURE")["status"], "Not established")

    def test_partial_positive_incidents_remain_confirmable_findings(self):
        client = DefenderClient()
        client.collection_status = {"incidents": state("partial")}
        client.security_incidents = [{"id": "invented", "status": "active", "severity": "high"}]
        result = assessment(assess_defender_configuration(client, DAY), client)
        row = next(row for row in result["actions"] if row["FindingKey"] == "defender.incidents.current")
        self.assertEqual(row["ActionType"], "Confirmation")
        self.assertIn("1 active", row["Observation"])

    def test_saved_false_assurances_and_skipped_policy_failures_are_requalified(self):
        client = SimpleNamespace(collection_status={key: state("not_requested") for key in
            ("ediscovery_cases", "communication_compliance", "insider_risk_policies")},
            ediscovery_cases={"available": False, "total_cases": 0}, comm_compliance={"available": False}, insider_risk={"available": False})
        old = [
            {"Service": "Purview", "Feature": "eDiscovery Analytics - Analytics Usage", "Observation": "No eDiscovery cases were present", "Status": "Success", "Disposition": "Assurance"},
            {"Service": "Purview", "Feature": "Communication Compliance - Policy Status", "Observation": "NO policies configured", "Status": "Action Required", "Disposition": "Action"},
            {"Service": "Purview", "Feature": "Insider Risk Management - Policy Status", "Observation": "NO policies configured", "Status": "Action Required", "Disposition": "Action"},
        ]
        rows = qualify_saved_recommendations(old, "Purview", client)
        self.assertTrue(all(row["Disposition"] == "Reference" and row["EvidenceComplete"] is False for row in rows))
        self.assertTrue(all("not requested" in row["Observation"] for row in rows))
        self.assertEqual(old[0]["Disposition"], "Assurance")  # immutable input

    def test_purview_live_modules_do_not_evaluate_skipped_sources_as_zero(self):
        from Recommendations.purview import EQUIVIO_ANALYTICS, COMMUNICATIONS_COMPLIANCE, INSIDER_RISK_MANAGEMENT
        client = SimpleNamespace(collection_status={key: state("not_requested") for key in
            ("ediscovery_cases", "communication_compliance", "insider_risk_policies")},
            ediscovery_cases={"available": False, "total_cases": 0}, comm_compliance={"available": False}, insider_risk={"available": False})
        for module in (EQUIVIO_ANALYTICS, COMMUNICATIONS_COMPLIANCE, INSIDER_RISK_MANAGEMENT):
            rows = asyncio.run(module.get_recommendation("SPE_E5", purview_client=client))
            self.assertEqual(len(rows), 1)
            self.assertNotIn("NO policies", rows[0]["Observation"])

    def test_label_definitions_cannot_replace_unavailable_publication_evidence(self):
        client = SimpleNamespace(collection_status={"sensitivity_labels": state(), "label_policies": state("unavailable")},
                                 sensitivity_labels={"available": True, "total_labels": 3}, label_policies={"available": False})
        rows = qualify_saved_recommendations([{"Service": "Purview", "Feature": "Sensitivity label publication", "FindingKey": "purview.label_policies",
            "Observation": "Sensitivity labels are published to users.", "Disposition": "Assurance"}], "Purview", client)
        self.assertEqual(rows[0]["Disposition"], "Coverage")
        self.assertFalse(rows[0]["EvidenceComplete"])

    def test_device_count_cannot_establish_pilot_browser_baseline(self):
        client = SimpleNamespace(collection_status={"machines": state()}, defender_devices=[{"id": "invented-device"}])
        rows = qualify_saved_recommendations([{"Service": "Defender", "Feature": "Defender for Endpoint - Device Onboarding",
            "Observation": "1 device onboarded", "Status": "Success", "Disposition": "Assurance", "EvidenceKey": "defender_device_detail"}], "Defender", client)
        self.assertEqual(rows[0]["Disposition"], "Reference")
        result = assessment(rows, client)
        for control in ("ENDPOINT.POSTURE", "THREAT.INCIDENTS"):
            self.assertEqual(next(row for row in result["controls"] if row["control_id"] == control)["status"], "Not established")

    def test_measured_device_risk_survives_license_narrative_demotion(self):
        client = SimpleNamespace(collection_status={"machines": state(), "incidents": state("unavailable")},
                                 defender_devices=[{"id": "invented-device", "riskScore": "High"}])
        result = assessment(assess_defender_configuration(client, DAY), client)
        row = next(row for row in result["actions"] if row["FindingKey"] == "defender.devices.high_risk")
        self.assertEqual(row["ActionType"], "Remediation")
        self.assertIn("1 device(s)", row["Observation"])

    def test_product_narrative_and_opportunity_cannot_pass_controls(self):
        rows = [{"Service": "M365", "Feature": "Windows Update for Business", "Observation": "40 users. Device update management is available.",
                 "Status": "Success", "Disposition": "Assurance", "ControlId": "EGRESS-001"},
                {"Service": "Defender", "Feature": "Incident response", "Observation": "Review threat protection options.",
                 "Status": "Success", "Disposition": "Opportunity", "EvidenceKey": "defender_incident_detail"},
                {"Service": "M365", "Feature": "Mesh Avatars", "Observation": "40 users can use avatars.", "Status": "Success", "Disposition": "Opportunity"}]
        result = assessment(rows)
        self.assertFalse(result["strengths"])
        for control in ("ENDPOINT.POSTURE", "THREAT.INCIDENTS"):
            self.assertEqual(next(row for row in result["controls"] if row["control_id"] == control)["status"], "Not established")
        self.assertFalse(any(row["Feature"] == "Mesh Avatars" for row in result["customer_findings"]))

    def test_existing_source_gaps_map_to_the_correct_domain_and_question(self):
        rows = [{"Service": "M365", "Feature": "SharePoint sharing and oversharing assessment", "Observation": "Sharing settings were not collected.",
                 "FindingKey": "sharepoint.governance.not_assessed", "EvidenceKey": "sharepoint_governance_detail", "ImpactArea": "Identity and access", "Disposition": "Coverage"},
                {"Service": "Data Exposure", "Feature": "Confirm the scope of changed permission snapshots", "Observation": "Different site populations", "FindingKey": "data_exposure.snapshot_scope", "Disposition": "Coverage"},
                {"Service": "Entra", "Feature": "Microsoft Intune", "Observation": "The Intune query returned no managed devices.", "Disposition": "Coverage"}]
        result = assessment(rows)
        self.assertFalse(any(row.get("ControlId") in {"CONTENT.SHARING", "ENDPOINT.POSTURE"} and row.get("SourceType") == "assessment_requirement" for row in result["actions"]))
        scope = next(row for row in result["recommendations"] if row.get("FindingKey") == "data_exposure.snapshot_scope")
        self.assertEqual(scope["DomainId"], "content")

    def test_duplicate_configuration_summary_does_not_inflate_strength_count(self):
        rows = [{"Service": "Entra", "Feature": "Conditional Access configuration", "Observation": "12 Conditional Access policies found; verified controls include 7 requiring MFA",
                 "Disposition": "Assurance", "EvidenceKey": "conditional_access_detail"},
                {"Service": "Entra", "Feature": "Sign-in protection", "Observation": "Multifactor authentication is required by Conditional Access policies.",
                 "FindingKey": "conditional-access-mfa", "Disposition": "Assurance", "EvidenceKey": "conditional_access_detail"}]
        result = assessment(rows)
        self.assertEqual(len(result["strengths"]), 1)

    def test_signin_sample_and_label_definitions_keep_their_actual_scope(self):
        entra = SimpleNamespace(collection_status={"signin_logs": state()})
        rows = qualify_saved_recommendations([{"Feature": "Entra ID P1", "Observation": "No legacy authentication sign-ins detected, all access uses modern authentication with full security controls"}], "Entra", entra)
        self.assertIn("returned sign-in records", rows[0]["Observation"])
        self.assertNotIn("with full security controls", rows[0]["Observation"])
        purview = SimpleNamespace(collection_status={"sensitivity_labels": state()}, sensitivity_labels={"available": True, "total_labels": 26})
        rows = qualify_saved_recommendations([{"Feature": "Label Deployment", "Observation": "26 sensitivity labels configured, enabling automatic classification", "FindingKey": "purview.sensitivity_labels.deployed"}], "Purview", purview)
        self.assertIn("26 sensitivity label definitions", rows[0]["Observation"])
        self.assertIn("require separate evidence", rows[0]["Observation"])

    def test_incident_query_uses_supported_page_size_and_failure_counts_are_unknown(self):
        async def run():
            async def collection(path, params):
                if path.endswith("/incidents"):
                    self.assertEqual(params["$top"], "50")
                    return {**state("unavailable"), "value": [], "reason": "Fixture failure"}
                return {**state(), "value": [], "records_collected": 0}
            graph = SimpleNamespace(get_collection=AsyncMock(side_effect=collection))
            with patch("Core.get_defender_client._collect_mde_machines", new=AsyncMock(return_value={**state(), "value": []})), patch("builtins.print"):
                client = await get_defender_client(TENANT, graph)
            self.assertIsNone(client.incident_summary["total"])
            self.assertIsNone(client.incident_summary["active"])
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()

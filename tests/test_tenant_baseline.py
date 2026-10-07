"""Methodology 3.0: required controls are judged on tenant-wide configuration."""

import json
import unittest
from types import SimpleNamespace

from Core.assessment_result import build_assessment_result
from Core.pilot_summary import render_pilot_summary
from Core.tenant_baseline import (
    assess_data_protection_baseline, assess_identity_baseline, assess_m365_baseline,
    conditional_access_facts, dlp_facts, enforced_mfa_policy_count,
)

TENANT = "11111111-1111-1111-1111-111111111111"
AVAILABLE = {"available": True, "availability_status": "available", "truncated": False}


def policy(name, state="enabled", users=("All",), apps=("All",), grant=("mfa",), clients=("all",), groups=()):
    return {"displayName": name, "state": state,
            "conditions": {"users": {"includeUsers": list(users), "includeGroups": list(groups), "excludeUsers": []},
                           "applications": {"includeApplications": list(apps)}, "clientAppTypes": list(clients)},
            "grantControls": {"builtInControls": list(grant)}}


def entra(policies, defaults=None):
    return SimpleNamespace(ca_policies=policies, security_defaults=defaults or {"available": True, "is_enabled": False},
                           collection_status={"ca_policies": AVAILABLE})


LEGACY_BLOCK = policy("Block legacy", grant=("block",), clients=("exchangeActiveSync", "other"))


class ConditionalAccessTests(unittest.TestCase):
    def test_report_only_and_scoped_policies_do_not_count_as_all_user_mfa(self):
        policies = [policy("MFA all users (report-only)", state="enabledForReportingButNotEnforced"),
                    policy("MFA admins", users=(), groups=("admins",)), LEGACY_BLOCK,
                    policy("Old MFA", state="disabled")]
        facts = conditional_access_facts(policies)
        self.assertEqual(facts["enforced_mfa_all"], [])
        self.assertEqual(facts["report_only_mfa_all"], ["MFA all users (report-only)"])
        self.assertEqual(facts["enforced_mfa_scoped"], ["MFA admins"])
        self.assertEqual(enforced_mfa_policy_count(policies), 1)
        [row] = assess_identity_baseline(entra(policies))
        self.assertEqual(row["Disposition"], "Action")
        self.assertEqual(row["Priority"], "High")
        self.assertIn("report-only mode (not enforced)", row["Observation"])
        self.assertIn("switch the all-users MFA policy to On", row["Recommendation"])

    def test_enforced_all_user_mfa_and_legacy_block_pass(self):
        [row] = assess_identity_baseline(entra([policy("MFA all users"), LEGACY_BLOCK]))
        self.assertEqual(row["Disposition"], "Assurance")
        self.assertTrue(row["BaselineCheck"])
        self.assertEqual(row["ControlId"], "IDENTITY.AUTH")
        self.assertNotIn("excluded user or group assignment", row["Observation"])

    def test_missing_legacy_block_is_a_medium_condition(self):
        [row] = assess_identity_baseline(entra([policy("MFA all users")]))
        self.assertEqual((row["Disposition"], row["Priority"]), ("Action", "Medium"))

    def test_security_defaults_satisfy_the_baseline(self):
        [row] = assess_identity_baseline(entra([], {"available": True, "is_enabled": True}))
        self.assertEqual(row["Disposition"], "Assurance")
        self.assertIn("Security defaults", row["Observation"])

    def test_uncollected_policies_leave_the_question_to_the_gap_logic(self):
        client = entra([])
        client.collection_status = {"ca_policies": {"available": False, "availability_status": "unavailable"}}
        self.assertEqual(assess_identity_baseline(client), [])


def purview(dlp=None, labels=None, retention=None):
    status = {}
    client = SimpleNamespace(collection_status=status, cache_provenance={"collected_at": "2026-09-20"})
    for attribute, key, payload in (("dlp_policies", "dlp_policies", dlp), ("label_policies", "label_policies", labels),
                                    ("retention_labels", "retention_policies", retention)):
        if payload is None:
            setattr(client, attribute, {"available": False})
            status[key] = {"available": False, "availability_status": "unavailable"}
        else:
            setattr(client, attribute, {"available": True, **payload})
            status[key] = AVAILABLE
    return client


COPILOT_LOCATION = json.dumps([{"Workload": "Applications", "Location": "Copilot.M365",
                                "Inclusions": [{"Type": "Tenant", "Identity": "All"}]}])
ALL = [{"Name": "All", "DisplayName": "All"}]


class DataProtectionTests(unittest.TestCase):
    def test_enforced_tenant_wide_dlp_passes_and_copilot_location_is_recognised(self):
        policies = [{"Name": "Finance", "Mode": "Enable", "Enabled": True, "ExchangeLocation": ALL, "SharePointLocation": ALL},
                    {"Name": "Copilot labels", "Mode": "Enable", "Enabled": True, "Locations": COPILOT_LOCATION},
                    {"Name": "Test only", "Mode": "TestWithNotifications", "Enabled": True, "TeamsLocation": ALL}]
        facts = dlp_facts(policies)
        self.assertEqual(facts["copilot_enforced"], ["Copilot labels"])
        rows = assess_data_protection_baseline(purview(dlp={"policies": policies}))
        dlp = next(row for row in rows if row["ControlId"] == "DATA.DLP")
        self.assertEqual(dlp["Disposition"], "Assurance")
        self.assertIn("Microsoft 365 Copilot", dlp["Observation"])
        self.assertFalse(any(row["FindingKey"] == "baseline.dlp.copilot_location" for row in rows))

    def test_no_copilot_dlp_is_an_opportunity_not_a_failure(self):
        policies = [{"Name": "Finance", "Mode": "Enable", "Enabled": True, "ExchangeLocation": ALL}]
        rows = assess_data_protection_baseline(purview(dlp={"policies": policies}))
        opportunity = next(row for row in rows if row["FindingKey"] == "baseline.dlp.copilot_location")
        self.assertEqual(opportunity["Disposition"], "Opportunity")

    def test_scoped_dlp_needs_confirmation(self):
        policies = [{"Name": "HR only", "Mode": "Enable", "Enabled": True, "ExchangeLocation": [{"Name": "hr@contoso.com"}]}]
        [row] = assess_data_protection_baseline(purview(dlp={"policies": policies}))
        self.assertEqual((row["ControlId"], row["Disposition"]), ("DATA.DLP", "Coverage"))

    def test_label_publishing_scope(self):
        everyone = [{"Name": "Global", "Enabled": True, "Mode": "Enforce", "ExchangeLocation": ["All"]}]
        [row] = assess_data_protection_baseline(purview(labels={"policies": everyone}))
        self.assertEqual(row["Disposition"], "Assurance")
        scoped = [{"Name": "Legal", "Enabled": True, "Mode": "Enforce", "ExchangeLocation": ["legal@contoso.com"]}]
        [row] = assess_data_protection_baseline(purview(labels={"policies": scoped}))
        self.assertEqual(row["Disposition"], "Coverage")
        self.assertIn("specific users or groups", row["Observation"])
        unrecorded = [{"Name": "Old", "Enabled": True, "Mode": "Enforce"}]
        [row] = assess_data_protection_baseline(purview(labels={"policies": unrecorded}))
        self.assertIn("without their publishing scope", row["Observation"])
        pending = [{"Name": "Gone", "Enabled": True, "Mode": "PendingDeletion"}]
        [row] = assess_data_protection_baseline(purview(labels={"policies": pending}))
        self.assertEqual(row["Disposition"], "Action")

    def test_retention_none_is_a_non_blocking_condition(self):
        [row] = assess_data_protection_baseline(purview(retention={"labels": []}))
        self.assertEqual((row["ControlId"], row["Disposition"], row["Priority"]), ("DATA.RETENTION", "Action", "Medium"))
        [row] = assess_data_protection_baseline(purview(retention={"labels": [{"Name": "Keep 7y", "Enabled": True}]}))
        self.assertEqual(row["Disposition"], "Assurance")

    def test_uncollected_sources_emit_nothing(self):
        self.assertEqual(assess_data_protection_baseline(purview()), [])


class M365BaselineTests(unittest.TestCase):
    def client(self, connections, seats):
        return SimpleNamespace(external_connections=connections, collection_status={"external_connections": AVAILABLE},
                               license_coverage={"copilot_subscription_capacity": {"available": True, "enabled_seats": seats, "assigned_seats": 2}})

    def test_connectors_and_licenses(self):
        rows = {row["ControlId"]: row for row in assess_m365_baseline(self.client([], 10))}
        self.assertEqual(rows["APPS.CONNECTIONS"]["Disposition"], "Assurance")
        self.assertEqual(rows["LICENSE.ASSIGNMENT"]["Disposition"], "Assurance")
        self.assertIn("2 assigned and 8 unassigned", rows["LICENSE.ASSIGNMENT"]["Observation"])
        rows = {row["ControlId"]: row for row in assess_m365_baseline(self.client([{"name": "Wiki"}], 0))}
        self.assertEqual(rows["APPS.CONNECTIONS"]["Disposition"], "Coverage")
        self.assertEqual(rows["LICENSE.ASSIGNMENT"]["Disposition"], "Coverage")


class VerdictAndSummaryTests(unittest.TestCase):
    def build(self, rows):
        bundle = {"collection_context": {"mode": "offline", "collected_at": "2026-09-20"}}
        return build_assessment_result(rows, bundle, evaluation_date="2026-09-23", expected_tenant_id=TENANT), bundle

    def baseline_rows(self):
        rows = assess_identity_baseline(entra([policy("MFA all users (report-only)", state="enabledForReportingButNotEnforced"), LEGACY_BLOCK]), "2026-09-20")
        rows += assess_data_protection_baseline(purview(
            dlp={"policies": [{"Name": "Finance", "Mode": "Enable", "Enabled": True, "ExchangeLocation": ALL}]},
            labels={"policies": [{"Name": "Global", "Enabled": True, "Mode": "Enforce", "ExchangeLocation": ["All"]}]},
            retention={"labels": []}), "2026-09-20")
        for row in rows:
            row["TenantId"] = TENANT
        return rows

    def test_high_baseline_failure_blocks_the_pilot_and_summary_leads_with_it(self):
        result, bundle = self.build(self.baseline_rows())
        self.assertEqual(result["decision"], "Not ready for pilot")
        blocker = next(row for row in result["actions"] if row.get("ControlId") == "IDENTITY.AUTH")
        self.assertEqual((blocker["PilotImpact"], blocker["ReadinessStage"]), ("Blocks pilot", "Before pilot"))
        retention = next(row for row in result["actions"] if row.get("ControlId") == "DATA.RETENTION")
        self.assertEqual(retention["PilotImpact"], "Fix before broad rollout")
        statuses = {row["control_id"]: row["status"] for row in result["controls"]}
        self.assertEqual(statuses["DATA.DLP"], "Not established")
        self.assertEqual(statuses["DATA.PUBLISHING"], "Observed")
        html = render_pilot_summary(result, bundle, "Contoso", "report.html", "workbook.xlsx")
        self.assertIn("<h1>Not ready for a pilot yet</h1>", html)
        self.assertLess(html.index("Fix before the pilot"), html.index("Choosing the pilot group"))
        self.assertIn("Require MFA and block legacy sign-in for all users", html)
        self.assertIn('href="report.html#action-', html)
        self.assertIn("You do not need to name the pilot users", html)
        self.assertNotIn("pilot population", html)

    def test_other_supported_evidence_answers_a_baseline_confirm_row(self):
        rows = assess_m365_baseline(M365BaselineTests().client([{"name": "Wiki"}], 5), "2026-09-20")
        review = {"RecommendationId": "REV-1", "FindingKey": "connectors.reviewed", "ControlId": "APPS.CONNECTIONS",
                  "Service": "M365", "Feature": "Connector review", "Observation": "Both connectors were reviewed.",
                  "Disposition": "Assurance", "Status": "Success", "Priority": "Low", "EvidenceBasis": "Tenant evidence",
                  "EvidenceAvailable": "Yes", "EvidenceKey": "external_connection_detail", "EvidenceScope": "Assessed tenant",
                  "ObservationDate": "2026-09-20", "EvidenceComplete": True, "TenantId": TENANT}
        result, _ = self.build(rows + [review])
        self.assertFalse(any(row.get("ControlId") == "APPS.CONNECTIONS" for row in result["actions"]))


if __name__ == "__main__":
    unittest.main()

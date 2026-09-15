"""Regression tests for assessment integrity.

These cover three classes of defect that caused the assessment to report things that were not
true, rather than merely noisy:

1. Phantom insight keys - recommendation modules read metric names the client never produced,
   so the value was always 0. Observation text printed "0 users" for a 407-user tenant and,
   worse, every organization-size branch silently took the "small org" path.
2. Dict-vs-object access - Graph collections arrive as either SDK model objects or plain dicts.
   Code that assumed dicts raised AttributeError mid-loop, which the surrounding except
   swallowed, leaving counters at 0. That turned "we could not count standing admin roles" into
   the positive assertion "No permanent admin roles detected".
3. Absence reported as clean - a failed API query was indistinguishable from an empty result, so
   an unreadable incident feed was reported as "No security incidents detected".
"""

import ast
import asyncio
import pathlib
import re
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _literal_dict_keys(source_path, function_name):
    """Collect string keys from every sizeable dict literal inside a named function."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Dict):
                    keys.update(
                        k.value for k in sub.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                    )
    return keys


class InsightKeyContractTests(unittest.TestCase):
    """Every insight key a recommendation reads must actually be produced."""

    def _consumed_keys(self, accessor):
        pattern = re.compile(re.escape(accessor) + r"\.get\('([a-z0-9_]+)'")
        consumed = set()
        for path in (REPO_ROOT / "Recommendations").rglob("*.py"):
            consumed.update(pattern.findall(path.read_text(encoding="utf-8")))
        return consumed

    def test_m365_insight_keys_are_all_produced(self):
        produced = _literal_dict_keys(
            REPO_ROOT / "Core" / "get_m365_client.py",
            "extract_m365_insights_from_client",
        )
        consumed = self._consumed_keys("m365_insights")
        missing = sorted(consumed - produced)
        self.assertEqual(
            missing, [],
            f"Recommendation modules read m365_insights keys that are never produced, so they "
            f"always evaluate to 0: {missing}",
        )

    def test_previously_phantom_keys_are_present_in_both_shapes(self):
        """The available and unavailable branches must expose the same key set."""
        source = (REPO_ROOT / "Core" / "get_m365_client.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn = next(
            n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "extract_m365_insights_from_client"
        )
        big_dicts = []
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Dict) and len(sub.keys) > 10:
                big_dicts.append({
                    k.value for k in sub.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                })
        self.assertGreaterEqual(len(big_dicts), 2, "expected an available and an unavailable dict")

        for key in ("total_active_users", "sharepoint_total_sites", "sharepoint_page_views",
                    "teams_total_messages", "office_active_users"):
            for index, keys in enumerate(big_dicts):
                self.assertIn(key, keys, f"{key!r} missing from insights dict #{index}")


class GraphResponseShapeTests(unittest.TestCase):
    """Counters must survive both SDK model objects and plain dicts."""

    def _load_get_attr(self):
        source = (REPO_ROOT / "Core" / "get_entra_client.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn = next(
            n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_get_attr"
        )
        namespace = {}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "<_get_attr>", "exec"), namespace)
        return namespace["_get_attr"]

    def test_get_attr_reads_dicts_and_sdk_objects(self):
        get_attr = self._load_get_attr()

        class SdkModel:
            role_definition_id = "role-123"  # SDK exposes snake_case

        self.assertEqual(get_attr({"roleDefinitionId": "role-123"}, "roleDefinitionId"), "role-123")
        self.assertEqual(get_attr({"role_definition_id": "role-123"}, "roleDefinitionId"), "role-123")
        self.assertEqual(get_attr(SdkModel(), "roleDefinitionId"), "role-123")
        self.assertEqual(get_attr(None, "roleDefinitionId", "fallback"), "fallback")
        self.assertEqual(get_attr({}, "roleDefinitionId", "fallback"), "fallback")

    def test_permanent_global_admin_is_counted_in_both_shapes(self):
        """The exact regression: a standing Global Administrator must never be missed."""
        get_attr = self._load_get_attr()
        global_admin_role = "62e90394-69f5-4237-9190-012177145e10"

        class SdkAssignment:
            def __init__(self, role_definition_id):
                self.role_definition_id = role_definition_id

        def count(assignments):
            permanent = global_admins = 0
            for assignment in assignments:
                role_def_id = str(get_attr(assignment, "roleDefinitionId", "") or "")
                permanent += 1
                if global_admin_role in role_def_id:
                    global_admins += 1
            return permanent, global_admins

        as_dicts = [{"roleDefinitionId": global_admin_role}, {"roleDefinitionId": "other-role"}]
        as_objects = [SdkAssignment(global_admin_role), SdkAssignment("other-role")]

        self.assertEqual(count(as_dicts), (2, 1))
        self.assertEqual(count(as_objects), (2, 1))

    def test_no_raw_dict_access_on_graph_collection_items(self):
        """Guard against reintroducing `item.get(...)` on possibly-SDK collection items."""
        extractors = {"_extract_response_items", "_ensure_list"}
        offenders = []

        for path in (REPO_ROOT / "Core").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            suspect_names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                    func = node.value.func
                    name = (func.id if isinstance(func, ast.Name)
                            else func.attr if isinstance(func, ast.Attribute) else None)
                    if name in extractors:
                        suspect_names.update(
                            t.id for t in node.targets if isinstance(t, ast.Name)
                        )
            for node in ast.walk(tree):
                if not (isinstance(node, ast.For) and isinstance(node.iter, ast.Name)):
                    continue
                if node.iter.id not in suspect_names or not isinstance(node.target, ast.Name):
                    continue
                loop_var = node.target.id
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                            and sub.func.attr == "get"
                            and isinstance(sub.func.value, ast.Name)
                            and sub.func.value.id == loop_var):
                        offenders.append(f"{path.name}:{sub.lineno} ({loop_var}.get)")

        self.assertEqual(
            offenders, [],
            "Graph collection items may be SDK objects; use _get_attr instead of .get(): "
            f"{offenders}",
        )


class UnreadDataIsNotReportedAsCleanTests(unittest.TestCase):
    """A dataset that was never read must be reported as Not Assessed, not as clean."""

    def _defender_insights(self, data_sources):
        from Recommendations.defender.defender_insights import DefenderInsights

        class FakeClient:
            graph_security_available = True
            available = False
            oauth_risk_summary = {"total_apps": 0, "high_risk": 0, "over_privileged": 0}
            alert_summary = {"total": 0, "by_severity": {}, "by_category": {}}
            incident_summary = {"total": 0, "active": 0, "resolved": 0, "high_severity": 0}
            risky_users_summary = {"total": 0, "high": 0, "confirmed_compromised": 0}
            risky_sign_ins_summary = {"total": 0, "high_risk": 0}
            email_threat_summary = {"total": 0, "phishing": 0, "malware": 0}
            device_summary = {"total": 0, "high_risk": 0}

        client = FakeClient()
        client.data_sources = data_sources
        return DefenderInsights(client)

    def test_defender_reports_not_assessed_when_feed_unreadable(self):
        from Core.new_recommendation import NOT_ASSESSED_STATUS
        from Recommendations.defender import MTP, ATA, WINDEFATP, THREAT_INTELLIGENCE

        keys = ["incidents", "alerts", "risky_users", "risky_sign_ins", "email_threats"]
        unread = self._defender_insights(dict.fromkeys(keys, False))
        read_and_empty = self._defender_insights(dict.fromkeys(keys, True))

        for module in (MTP, ATA, WINDEFATP, THREAT_INTELLIGENCE):
            with self.subTest(module=module.__name__):
                unread_rec = module.get_recommendation(
                    "SPE_E5", "Success", defender_insights=unread
                )
                self.assertEqual(unread_rec["Status"], NOT_ASSESSED_STATUS)
                self.assertIn("could not be retrieved", unread_rec["Observation"])
                self.assertTrue(unread_rec["Recommendation"])

                clean_rec = module.get_recommendation(
                    "SPE_E5", "Success", defender_insights=read_and_empty
                )
                self.assertEqual(clean_rec["Status"], "Success")
                self.assertNotIn("could not be retrieved", clean_rec["Observation"])

    def test_mfa_coverage_is_not_invented_from_a_zero_denominator(self):
        from Core.new_recommendation import NOT_ASSESSED_STATUS
        from Recommendations.entra import AAD_PREMIUM

        def insights(total_users, mfa_enabled):
            return {
                "available": True,
                "data_sources": {"auth_methods": total_users > 0},
                "mfa_metrics": {"total_users": total_users, "mfa_enabled_users": mfa_enabled},
                "ca_metrics": {"total_policies": 12, "copilot_policies": 0},
                "signin_metrics": {},
                "ca_total": 12, "ca_target_m365": 0,
                "ca_require_mfa": 0, "ca_require_compliant_device": 0,
            }

        no_denominator = AAD_PREMIUM.get_recommendation(
            "SPE_E5", "Success", entra_insights=insights(0, 0)
        )
        statuses = {r["Status"] for r in no_denominator}
        self.assertIn(NOT_ASSESSED_STATUS, statuses)
        self.assertFalse(
            any("0 of 0 users" in r["Observation"] for r in no_denominator),
            "MFA coverage must not be reported from a zero denominator",
        )

        measured = AAD_PREMIUM.get_recommendation(
            "SPE_E5", "Success", entra_insights=insights(131, 33)
        )
        self.assertTrue(
            any("33 of 131 users" in r["Observation"] for r in measured),
            "real MFA coverage should still be reported normally",
        )

    def test_identity_risk_is_not_reported_clean_when_unread(self):
        from Core.new_recommendation import NOT_ASSESSED_STATUS
        from Recommendations.entra import AAD_PREMIUM_IDENTITY_PROTECTION as IDP

        def insights(sources):
            return {
                "available": True,
                "data_sources": sources,
                "risk_metrics": {
                    "total_risky_users": 0, "high_risk_users": 0, "at_risk_users": 0,
                },
                "risky_users_total": 0,
            }

        unread = IDP.get_recommendation(
            "SPE_E5", "Success",
            entra_insights=insights({"risky_users": False, "risk_detections": False}),
        )
        self.assertIn(NOT_ASSESSED_STATUS, {r["Status"] for r in unread})
        self.assertFalse(
            any("No risky users detected" in r["Observation"] for r in unread),
            "unread identity risk data must not be reported as no risky users",
        )

        read_and_empty = IDP.get_recommendation(
            "SPE_E5", "Success",
            entra_insights=insights({"risky_users": True, "risk_detections": True}),
        )
        self.assertTrue(
            any("No risky users detected" in r["Observation"] for r in read_and_empty),
            "a genuinely empty risk feed should still read as clean",
        )

    def test_defender_device_query_failure_is_coverage_not_zero_devices(self):
        from Recommendations.defender.DEFENDER_ENDPOINT_ONBOARDING import get_recommendation

        class FakeClient:
            available = True  # A Graph Security dataset may still have succeeded.
            defender_api_available = False
            data_sources = {"oauth_grants": True, "machines": False}
            device_summary = {"total": 0}

        result = asyncio.run(get_recommendation(None, defender_client=FakeClient()))
        self.assertEqual(result["Disposition"], "Coverage")
        self.assertEqual(result["Status"], "Not Assessed")
        self.assertIn("could not be read", result["Observation"])
        self.assertNotIn("no devices", result["Observation"].lower())

    def test_empty_but_read_defender_device_inventory_is_an_action(self):
        from Recommendations.defender.DEFENDER_ENDPOINT_ONBOARDING import get_recommendation

        class FakeClient:
            available = True
            defender_api_available = True
            data_sources = {"machines": True}
            device_summary = {"total": 0}

        result = asyncio.run(get_recommendation(None, defender_client=FakeClient()))
        self.assertEqual(result["Disposition"], "Action")
        self.assertIn("no devices are reporting", result["Observation"].lower())

    def test_unread_intune_inventory_is_coverage_not_byod_assertion(self):
        from Recommendations.entra.INTUNE_A import get_recommendation

        insights = {
            "available": True,
            "data_sources": {"managed_devices": False},
            "device_summary": {
                "total_managed_devices": 0,
                "compliant_devices": 0,
                "non_compliant_devices": 0,
                "ca_requires_compliance": False,
            },
        }
        results = get_recommendation("SPE_E5", "Success", entra_insights=insights)
        coverage = [item for item in results if item["Disposition"] == "Coverage"]
        self.assertEqual(len(coverage), 1)
        self.assertNotIn("No managed devices detected", coverage[0]["Observation"])

    def test_empty_intune_inventory_does_not_prove_uncontrolled_access(self):
        from Recommendations.entra.INTUNE_A import get_recommendation

        insights = {
            "available": True,
            "data_sources": {"managed_devices": True},
            "device_summary": {
                "total_managed_devices": 0,
                "compliant_devices": 0,
                "non_compliant_devices": 0,
                "ca_requires_compliance": False,
            },
        }
        results = get_recommendation("SPE_E5", "Success", entra_insights=insights)
        device_result = next(item for item in results if "returned no managed devices" in item["Observation"])
        self.assertEqual(device_result["Disposition"], "Coverage")
        self.assertEqual(device_result["Status"], "Not Assessed")
        self.assertIn("does not establish", device_result["Observation"])


class ScanCoverageSeparationTests(unittest.TestCase):
    """Assessment coverage limits must not inflate or pollute tenant findings."""

    def test_global_secure_access_permission_denials_are_coverage(self):
        from Recommendations.entra.ENTRA_INTERNET_ACCESS import (
            get_recommendation as get_internet_access_recommendation,
        )
        from Recommendations.entra.ENTRA_PRIVATE_ACCESS import (
            get_recommendation as get_private_access_recommendation,
        )

        insights = {
            "network_access_summary": {"status": "PermissionDenied"},
            "private_access_summary": {"status": "PermissionDenied"},
        }
        recommendations = (
            get_internet_access_recommendation("TEST_SKU", entra_insights=insights)
            + get_private_access_recommendation("TEST_SKU", entra_insights=insights)
        )
        permission_rows = [
            item for item in recommendations
            if item.get("Status") == "Permission Required"
        ]

        self.assertEqual(len(permission_rows), 2)
        self.assertTrue(all(item.get("Category") == "Scan Coverage" for item in permission_rows))
        self.assertTrue(all(item.get("Disposition") == "Coverage" for item in permission_rows))

    def test_global_secure_access_403_with_permission_is_not_mislabeled_missing_permission(self):
        from Recommendations.entra.ENTRA_INTERNET_ACCESS import (
            get_recommendation as get_internet_access_recommendation,
        )
        from Recommendations.entra.ENTRA_PRIVATE_ACCESS import (
            get_recommendation as get_private_access_recommendation,
        )

        insights = {
            "network_access_summary": {"status": "Unavailable"},
            "private_access_summary": {"status": "Unavailable"},
        }
        recommendations = (
            get_internet_access_recommendation("TEST_SKU", entra_insights=insights)
            + get_private_access_recommendation("TEST_SKU", entra_insights=insights)
        )
        coverage = [item for item in recommendations if item.get("Disposition") == "Coverage"]

        self.assertEqual(len(coverage), 2)
        self.assertTrue(all(item.get("Status") == "Not Assessed" for item in coverage))
        self.assertTrue(all("even though NetworkAccess.Read.All is present" in item["Observation"] for item in coverage))
        self.assertTrue(all("onboarded" in item["Recommendation"] for item in coverage))

    def test_missing_purview_collection_is_coverage_not_a_control_failure(self):
        from Recommendations.defender.COPILOT_DATA_GOVERNANCE import get_recommendation

        result = get_recommendation(purview_client=None)

        self.assertEqual(result["Status"], "Not Assessed")
        self.assertEqual(result["Category"], "Scan Coverage")
        self.assertEqual(result["Disposition"], "Coverage")
        self.assertIn("evidence gap", result["Observation"])
        self.assertIn("python main.py --interactive-auth fresh", result["Recommendation"])

    def test_missing_ai_builder_inventory_is_coverage_not_zero_models(self):
        from Recommendations.power_platform.AI_BUILDER_MODELS import get_recommendation

        result = asyncio.run(
            get_recommendation("TEST_SKU", pp_client=None, pp_insights=None)
        )[0]

        self.assertEqual(result["Status"], "Not Assessed")
        self.assertEqual(result["Category"], "Scan Coverage")
        self.assertEqual(result["Disposition"], "Coverage")
        self.assertIn("does not mean", result["Observation"])
        self.assertIn("Power Platform Reader", result["Recommendation"])
        self.assertIn("optional extensibility", result["Recommendation"])

    def test_read_and_empty_ai_builder_inventory_remains_an_opportunity(self):
        from Recommendations.power_platform.AI_BUILDER_MODELS import get_recommendation

        class FakeClient:
            ai_model_summary = {"total": 0}

        result = asyncio.run(
            get_recommendation(
                "TEST_SKU",
                pp_client=FakeClient(),
                pp_insights={"ai_models_total": 0},
            )
        )[0]

        self.assertNotEqual(result["Category"], "Scan Coverage")
        self.assertNotEqual(result["Disposition"], "Coverage")
        self.assertIn("No AI Builder models deployed", result["Observation"])

    def test_power_platform_permission_failures_survive_json_loading(self):
        import json
        import os
        from unittest import mock
        from Core.get_power_platform_client import load_power_platform_data_from_stdin

        payload = json.dumps({
            "environments": [{}],
            "ai_models": [],
            "dlp_policies": [],
            "permission_failures": ["AI Models", "DLP Policies"],
        })
        with mock.patch.dict(
            os.environ,
            {
                "POWER_PLATFORM_DATA_SOURCE": "subprocess",
                "POWER_PLATFORM_DATA_JSON": payload,
            },
            clear=False,
        ):
            client = load_power_platform_data_from_stdin()

        self.assertIn("error", client.ai_model_summary)
        self.assertIn("error", client.dlp_summary)

    def test_power_platform_fresh_auth_is_forwarded_to_az_collector(self):
        ps_source = (
            REPO_ROOT / "collect_power_platform_and_copilot_studio_data.ps1"
        ).read_text(encoding="utf-8")
        orchestrator_source = (
            REPO_ROOT / "Core" / "orchestrator_powershell.py"
        ).read_text(encoding="utf-8")

        self.assertIn('[ValidateSet("Auto", "Fresh")]', ps_source)
        self.assertIn('Clear-AzContext -Scope Process', ps_source)
        self.assertIn('"-AuthMode", ("Fresh" if auth_mode == "fresh" else "Auto")',
                      orchestrator_source)
        self.assertIn('for executable_name in ("pwsh", "powershell")', orchestrator_source)

    def test_setup_requests_permissions_required_by_exact_graph_endpoints(self):
        setup_source = (REPO_ROOT / "setup-service-principal.ps1").read_text(encoding="utf-8")

        self.assertIn(
            '9e640839-a198-48fb-8b9a-013fd6f6cbcd"; Name = "Policy.Read.PermissionGrant',
            setup_source,
        )
        self.assertIn(
            'e30060de-caa5-4331-99d3-6ac6c966a9a4"; Name = "NetworkAccess.Read.All',
            setup_source,
        )

    def test_permission_guidance_names_the_permission_used_by_each_call(self):
        consent_source = (
            REPO_ROOT / "Recommendations" / "entra" / "AAD_PREMIUM_P2.py"
        ).read_text(encoding="utf-8")
        internet_source = (
            REPO_ROOT / "Recommendations" / "entra" / "ENTRA_INTERNET_ACCESS.py"
        ).read_text(encoding="utf-8")
        private_source = (
            REPO_ROOT / "Recommendations" / "entra" / "ENTRA_PRIVATE_ACCESS.py"
        ).read_text(encoding="utf-8")

        self.assertIn("Policy.Read.All", consent_source)
        self.assertIn("authorization policy", consent_source.lower())
        self.assertIn("NetworkAccess.Read.All permission is not granted", internet_source)
        self.assertIn("NetworkAccess.Read.All permission is not granted", private_source)

    def test_power_platform_tokens_support_current_securestring_behavior(self):
        ps_source = (
            REPO_ROOT / "collect_power_platform_and_copilot_studio_data.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("function ConvertFrom-AzAccessToken", ps_source)
        self.assertIn("$Token -is [System.Security.SecureString]", ps_source)
        self.assertGreaterEqual(ps_source.count("Get-AzAccessToken -TenantId $TenantId"), 3)
        self.assertIn("COLLECTION_WARNING:Power Platform", ps_source)

    def test_purview_result_is_passed_into_defender_governance_analysis(self):
        orchestrator_source = (
            REPO_ROOT / "Core" / "orchestrator.py"
        ).read_text(encoding="utf-8")
        pipeline_source = (
            REPO_ROOT / "Core" / "orchestrator_pipelines.py"
        ).read_text(encoding="utf-8")

        self.assertIn("purview_task = asyncio.create_task(pipelines['purview']())", orchestrator_source)
        self.assertIn("pipelines['defender'](purview_task)", orchestrator_source)
        self.assertIn("purview_result.get('_client')", pipeline_source)
        self.assertIn(
            "get_defender_info(client, defender_client, services_and_licenses, purview_client_for_defender)",
            pipeline_source,
        )

    def test_collector_diagnostics_redact_tokens_and_secrets(self):
        import os
        from unittest import mock
        from Core.orchestrator_powershell import _sanitize_collector_detail

        with mock.patch.dict(os.environ, {"CLIENT_SECRET": "top-secret-value"}, clear=False):
            sanitized = _sanitize_collector_detail(
                "Authorization: Bearer eyJheader.payload.signature "
                "client_secret=top-secret-value password=hunter2"
            )

        self.assertNotIn("eyJheader.payload.signature", sanitized)
        self.assertNotIn("top-secret-value", sanitized)
        self.assertNotIn("hunter2", sanitized)
        self.assertIn("[REDACTED]", sanitized)

    def test_coverage_items_are_excluded_from_finding_counts(self):
        import os
        import tempfile
        from Core.new_recommendation import (
            new_recommendation, NOT_ASSESSED_STATUS, CATEGORY_SCAN_COVERAGE,
        )
        from Core.export_recommendations import export_to_html

        recommendations = [
            new_recommendation("Entra", "Entra ID P1", "Low MFA coverage", "Enforce MFA.",
                               "MFA", "https://example.com",
                               priority="High", status="Action Required"),
            new_recommendation("Defender", "Defender XDR", "Incident data could not be retrieved",
                               "Grant the permission.", "XDR", "https://example.com",
                               priority="Medium", status=NOT_ASSESSED_STATUS),
            new_recommendation("Entra", "Internet Access",
                               "NetworkAccessPolicy.Read.All is not granted",
                               "Grant it and rerun.", "Ref", "https://example.com",
                               priority="Low", status="Permission Required",
                               category=CATEGORY_SCAN_COVERAGE),
        ]

        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                html_path = export_to_html(recommendations, tenant_name="Contoso")
                body = pathlib.Path(html_path).read_text(encoding="utf-8")
            finally:
                os.chdir(original_cwd)

        from Core.assessment_result import build_assessment_result
        result = build_assessment_result(recommendations, {})
        self.assertEqual(result['counts']['remediation'], 0, 'Undated conditions require confirmation.')
        self.assertEqual(result['counts']['confirmation'], 1)
        self.assertGreaterEqual(result['counts']['evidence_gaps'], 2)
        self.assertEqual(body.count('<article class="action"'), result['counts']['actions'])
        self.assertIn('Remaining evidence and decisions', body)

    def test_not_assessed_is_a_first_class_status(self):
        from Core.export_recommendations import PREFERRED_STATUS_ORDER
        from Core.new_recommendation import NOT_ASSESSED_STATUS

        self.assertIn(NOT_ASSESSED_STATUS, PREFERRED_STATUS_ORDER)
        self.assertLess(
            PREFERRED_STATUS_ORDER.index(NOT_ASSESSED_STATUS),
            PREFERRED_STATUS_ORDER.index("Success"),
            "Not Assessed should sort ahead of Success so gaps surface before clean results",
        )


if __name__ == "__main__":
    unittest.main()

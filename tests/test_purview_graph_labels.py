"""Sensitivity labels through Microsoft Graph as a Purview baseline."""

import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from Core.get_purview_client import hydrate_purview_client
from Core.purview_graph_labels import (
    REQUIRED_POWERSHELL_SOURCES, collect_sensitivity_labels_graph, map_graph_label, merge_purview_payloads,
)
from Core.saved_control_checks import assess_purview_configuration, qualify_saved_recommendations

ROOT = Path(__file__).resolve().parents[1]
LABELS = [
    {"id": "1", "name": "General", "tooltip": "Internal", "isActive": True, "sensitivity": 1, "hasProtection": False,
     "contentFormats": ["file", "email"], "parent": None},
    {"id": "2", "name": "Confidential", "tooltip": "", "isActive": True, "sensitivity": 2, "hasProtection": True,
     "contentFormats": ["file"], "parent": None},
    {"id": "3", "name": "Old", "tooltip": "", "isActive": False, "sensitivity": 3, "parent": {"id": "2"}},
]


class FakeGraph:
    def __init__(self, result):
        self.result = result

    async def get_collection(self, path, params=None, headers=None, max_pages=1000):
        return self.result


ENV = {"CLIENT_SECRET": "x", "CLIENT_ID": "a", "TENANT_ID": "t"}


def graph_labels(values=LABELS):
    with patch.dict(os.environ, ENV, clear=True):
        return asyncio.run(collect_sensitivity_labels_graph(FakeGraph(
            {"available": True, "availability_status": "available", "value": values, "truncated": False})))


class GraphLabelTests(unittest.TestCase):
    def test_mapping_uses_powershell_label_shape(self):
        mapped = map_graph_label(LABELS[2])
        self.assertEqual({"Name": "Old", "DisplayName": "Old", "Enabled": False, "ParentId": "2", "Guid": "3"},
                         {key: mapped[key] for key in ("Name", "DisplayName", "Enabled", "ParentId", "Guid")})

    def test_collection_is_preview_partial_and_never_complete(self):
        state = graph_labels()
        self.assertTrue(state["available"])
        self.assertEqual(3, state["count"])
        self.assertEqual(2, state["active_count"])
        self.assertEqual("preview", state["evidence_quality"])
        self.assertIs(False, state["complete"])
        self.assertEqual("graph_app_secret", state["credential_type"])

    def test_denied_labels_name_the_permission(self):
        with patch.dict(os.environ, ENV, clear=True):
            state = asyncio.run(collect_sensitivity_labels_graph(FakeGraph(
                {"available": False, "availability_status": "unavailable", "value": [], "status_code": 403})))
        self.assertFalse(state["available"])
        self.assertIn("InformationProtectionPolicy.Read.All", state["reason"])


class MergeTests(unittest.TestCase):
    def test_graph_only_payload_marks_powershell_datasets_with_unlock_steps(self):
        merged = merge_purview_payloads(None, graph_labels(), powershell_reason="Browser sign-in disabled.")
        client = hydrate_purview_client(merged)
        self.assertTrue(client.sensitivity_labels["available"])
        for key in REQUIRED_POWERSHELL_SOURCES:
            state = client.collection_status[key]
            self.assertFalse(state["available"], key)
            self.assertEqual("no_auth_path", state["error_category"])
            self.assertIn("-WorkloadRbac GlobalReader", state["unlock"])
            self.assertIn("Browser sign-in disabled.", state["reason"])
        self.assertEqual("preview", client.collection_status["sensitivity_labels"]["evidence_quality"])

    def test_nothing_collected_returns_none(self):
        self.assertIsNone(merge_purview_payloads(None, None))
        self.assertIsNone(merge_purview_payloads(None, {"available": False}))

    def test_powershell_labels_win_and_keep_graph_cross_check(self):
        powershell = {"sensitivity_labels": {"available": True, "availability_status": "available", "count": 5,
                                             "labels": [{"Name": "PS"}]}}
        merged = merge_purview_payloads(powershell, graph_labels())
        self.assertEqual([{"Name": "PS"}], merged["sensitivity_labels"]["labels"])
        self.assertEqual(3, merged["sensitivity_labels"]["graph_label_count"])

    def test_graph_fills_labels_when_powershell_could_not_read_them(self):
        powershell = {"sensitivity_labels": {"available": False, "reason": "role missing", "labels": []}}
        merged = merge_purview_payloads(powershell, graph_labels())
        self.assertEqual("graph_sensitivity_labels", merged["sensitivity_labels"]["auth_path_id"])
        self.assertEqual("role missing", merged["sensitivity_labels"]["powershell_reason"])


class DecisionSafetyTests(unittest.TestCase):
    def test_preview_labels_become_a_coverage_row_with_the_count(self):
        client = hydrate_purview_client(merge_purview_payloads(None, graph_labels()))
        row = {"Service": "Purview", "Feature": "Information Protection for Office 365 - Standard - Label Deployment",
               "Observation": "placeholder", "Status": "Not Assessed", "Disposition": "Coverage"}
        qualified = qualify_saved_recommendations([row], "Purview", client)[0]
        self.assertIn("returned 3 sensitivity label definition(s) (2 active)", qualified["Observation"])
        self.assertEqual("Coverage", qualified["Disposition"])
        self.assertEqual("Preview API", qualified["EvidenceBasis"])

    def test_zero_preview_labels_never_create_a_tenant_finding(self):
        client = hydrate_purview_client(merge_purview_payloads(None, graph_labels([])))
        self.assertEqual([], [row for row in assess_purview_configuration(client)
                              if row["FindingKey"] == "purview.raw.sensitivity_labels"])

    def test_label_module_makes_no_graph_calls(self):
        source = (ROOT / "Recommendations/purview/MIP_S_CLP1.py").read_text(encoding="utf-8")
        self.assertNotIn("information_protection", source)
        self.assertNotIn("informationProtection/policy", source)
        from Recommendations.purview.MIP_S_CLP1 import get_recommendation

        class Forbidden:
            def __getattr__(self, name):
                raise AssertionError("The label module must not call Microsoft Graph.")

        client = hydrate_purview_client(merge_purview_payloads(None, graph_labels()))
        rows = asyncio.run(get_recommendation("SKU", "Success", client=Forbidden(), purview_client=client))
        self.assertEqual(2, len(rows))
        self.assertEqual("Not Assessed", rows[1]["Status"])

    def test_label_policies_are_reported_only_when_collected(self):
        from Recommendations.purview.MIP_S_CLP1 import get_recommendation
        payload = {
            "sensitivity_labels": {"available": True, "availability_status": "available", "count": 4,
                                   "labels": [{"Name": name} for name in ("Public", "General", "Confidential", "Secret")]},
            "label_policies": {"available": False, "availability_status": "unavailable", "policies": []},
        }
        client = hydrate_purview_client(payload)
        rows = asyncio.run(get_recommendation("SKU", "Success", purview_client=client))
        self.assertIn("label publishing policies were not collected", rows[1]["Observation"])
        self.assertNotIn("0 label policies", rows[1]["Observation"])

    def test_no_purview_client_returns_license_row_only(self):
        from Recommendations.purview.MIP_S_CLP1 import get_recommendation
        rows = asyncio.run(get_recommendation("SKU", "Success"))
        self.assertEqual(1, len(rows))


if __name__ == "__main__":
    unittest.main()

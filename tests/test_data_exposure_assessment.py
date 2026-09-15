import csv
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from Core.data_exposure_assessment import build_data_exposure_assessment
from Core.evidence_layer import build_evidence_bundle
from Core.export_recommendations import export_to_html


class DataExposureAssessmentTests(unittest.TestCase):
    def _csv(self, directory, name, rows):
        path = Path(directory) / name
        headers = list(rows[0].keys()) if rows else ["Report Date"]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
        return str(path)

    def test_missing_authoritative_scans_are_coverage_not_findings(self):
        result = build_data_exposure_assessment()

        self.assertFalse(result["available"])
        self.assertEqual(len(result["recommendations"]), 2)
        self.assertTrue(all(item["Disposition"] == "Coverage" for item in result["recommendations"]))
        self.assertTrue(all(item["Category"] == "Scan Coverage" for item in result["recommendations"]))
        self.assertTrue(any("optional" in item["Recommendation"].lower() for item in result["recommendations"]))
        self.assertTrue(any("No usable SharePoint" in message for message in result["operator_messages"]))

    def test_fresh_sam_and_dspm_exports_create_evidence_backed_actions(self):
        today = datetime.now(timezone.utc).date().isoformat()
        with tempfile.TemporaryDirectory() as directory:
            sam = self._csv(directory, "sam.csv", [{
                "Report Date": today,
                "Site Name": "Finance",
                "Site URL": "https://example.sharepoint.com/sites/finance",
                "Primary Admin": "owner@example.com",
                "Anyone link count": "2",
                "EEEU permission count": "1",
                "External user count": "4",
                "Sensitivity Label": "Confidential",
            }])
            dspm = self._csv(directory, "dspm.csv", [{
                "Assessment Date": today,
                "Site Name": "Finance",
                "Potentially overshared items": "3",
                "Unlabeled sensitive item count": "2",
            }])

            result = build_data_exposure_assessment([sam], [dspm])

        features = {item["Feature"] for item in result["recommendations"]}
        self.assertTrue(result["available"])
        self.assertIn("Review links that allow access without sign-in", features)
        self.assertIn("Review organization-wide links and group permissions", features)
        self.assertIn("External sharing exposure", features)
        self.assertIn("Potentially overshared content", features)
        self.assertIn("Sensitive content without labels", features)
        self.assertTrue(any(item["Feature"] == "Exported report coverage" for item in result["recommendations"]))
        self.assertTrue(all(item.get("EvidenceKey") == "data_exposure_detail" for item in result["recommendations"]))
        self.assertEqual(len(result["evidence_rows"]), 2)

    def test_stale_report_is_used_but_freshness_is_not_assumed(self):
        old_date = (datetime.now(timezone.utc) - timedelta(days=90)).date().isoformat()
        with tempfile.TemporaryDirectory() as directory:
            sam = self._csv(directory, "sam.csv", [{
                "Report Date": old_date,
                "Site URL": "https://example.sharepoint.com/sites/old",
                "Primary Admin": "owner@example.com",
                "Anyone link count": "1",
            }])
            result = build_data_exposure_assessment([sam], [])

        freshness = [item for item in result["recommendations"] if "freshness" in item["Feature"].lower()]
        risk = [item for item in result["recommendations"] if item["Feature"] == "Review links that allow access without sign-in"]
        self.assertEqual(result["sources"]["sam"]["freshness"], "stale")
        self.assertEqual(len(freshness), 1)
        self.assertEqual(freshness[0]["Disposition"], "Coverage")
        self.assertEqual(risk[0]["Confidence"], "Medium")
        self.assertTrue(any("Confirm its continued relevance or supply a newer completed report" in message for message in result["operator_messages"]))

    def test_fresh_narrow_clean_reports_keep_unreported_domains_unverified(self):
        today = datetime.now(timezone.utc).date().isoformat()
        with tempfile.TemporaryDirectory() as directory:
            sam = self._csv(directory, "sam.csv", [{
                "Report Date": today,
                "Site URL": "https://example.sharepoint.com/sites/clean",
                "Primary Admin": "owner@example.com",
                "Anyone link count": "0",
            }])
            dspm = self._csv(directory, "dspm.csv", [{
                "Assessment Date": today,
                "Site URL": "https://example.sharepoint.com/sites/clean",
                "Potentially overshared items": "0",
            }])
            result = build_data_exposure_assessment([sam], [dspm])

        self.assertTrue(result["recommendations"])
        self.assertTrue(all(item["Disposition"] == "Coverage" for item in result["recommendations"]))
        self.assertIn("Organization links", result["sources"]["sam"]["coverage"]["domains_missing"])

    def test_report_without_scan_date_requires_refresh_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            sam = self._csv(directory, "sam.csv", [{
                "Site URL": "https://example.sharepoint.com/sites/unknown",
                "Primary Admin": "owner@example.com",
                "Anyone link count": "0",
            }])
            result = build_data_exposure_assessment([sam], [])

        self.assertEqual(result["sources"]["sam"]["freshness"], "unknown")
        self.assertTrue(any(item["Feature"] == "SharePoint oversharing scan freshness" for item in result["recommendations"]))

    def test_unrelated_spreadsheet_cannot_produce_clean_assurance(self):
        today = datetime.now(timezone.utc).date().isoformat()
        with tempfile.TemporaryDirectory() as directory:
            unrelated = self._csv(directory, "inventory.csv", [{
                "Report Date": today,
                "Name": "Unrelated inventory row",
                "Status": "Healthy",
            }])
            result = build_data_exposure_assessment([unrelated], [unrelated])

        self.assertFalse(result["available"])
        self.assertTrue(all(item["Disposition"] == "Coverage" for item in result["recommendations"]))
        self.assertTrue(result["sources"]["sam"]["errors"])
        self.assertTrue(result["sources"]["dspm"]["errors"])

    def test_sensitive_content_without_exposure_is_not_a_failure(self):
        today = datetime.now(timezone.utc).date().isoformat()
        with tempfile.TemporaryDirectory() as directory:
            sam = self._csv(directory, "sam.csv", [{
                "Report Date": today,
                "Site URL": "https://example.sharepoint.com/sites/legal",
                "Primary Admin": "owner@example.com",
            }])
            dspm = self._csv(directory, "dspm.csv", [{
                "Assessment Date": today,
                "Site URL": "https://example.sharepoint.com/sites/legal",
                "Sensitive item count": "25",
                "Sensitivity Label": "Confidential",
            }])
            result = build_data_exposure_assessment([sam], [dspm])

        self.assertEqual(result["sources"]["dspm"]["signals"], {})
        self.assertFalse(any(item["Disposition"] == "Action" for item in result["recommendations"]))
        self.assertTrue(any(item["Disposition"] == "Coverage" for item in result["recommendations"]))

    def test_overlapping_sam_exports_do_not_double_count_the_same_site_signal(self):
        today = datetime.now(timezone.utc).date().isoformat()
        row = {
            "Report Date": today,
            "Site URL": "https://example.sharepoint.com/sites/shared",
            "Primary Admin": "owner@example.com",
            "Anyone link count": "7",
        }
        with tempfile.TemporaryDirectory() as directory:
            baseline = self._csv(directory, "baseline.csv", [row])
            detailed = self._csv(directory, "detailed.csv", [row])
            result = build_data_exposure_assessment([baseline, detailed], [])

        self.assertEqual(result["sources"]["sam"]["signals"]["Anyone links"], 7)
        self.assertEqual(len(result["sources"]["sam"]["risk_rows"]), 1)

    def test_risk_narrative_separates_permission_units_and_scopes_each_finding(self):
        with tempfile.TemporaryDirectory() as directory:
            sam = self._csv(directory, "permissions.csv", [{
                "Report Date": "2026-09-14",
                "Site URL": "https://example.sharepoint.com/sites/broad",
                "Anyone link count": "1", "Everyone permission count": "3",
                "EEEU permission count": "2", "People In Your Org link count": "7",
                "Guest user permissions": "0",
            }, {
                "Report Date": "2026-09-14",
                "Site URL": "https://example.sharepoint.com/sites/external",
                "Anyone link count": "0", "Everyone permission count": "0",
                "EEEU permission count": "0", "People In Your Org link count": "0",
                "Guest user permissions": "9",
            }])
            result = build_data_exposure_assessment([sam], [], evaluation_date="2026-09-15")

        rows = {row["FindingKey"]: row for row in result["recommendations"]}
        anyone = rows["data_exposure.anonymous_links"]
        internal = rows["data_exposure.broad_internal_access"]
        external = rows["data_exposure.external_exposure"]
        self.assertEqual(len(result["sources"]["sam"]["affected_sites"]), 2)
        self.assertIn("1 Anyone link", anyone["Observation"])
        self.assertIn("1 SharePoint site", anyone["Observation"])
        self.assertNotIn("2 identified sites", anyone["Observation"])
        self.assertIn("3 permissions granted to Everyone", internal["Observation"])
        self.assertIn("2 permissions granted to Everyone except external users", internal["Observation"])
        self.assertIn("7 links accessible to people in the organization", internal["Observation"])
        self.assertNotIn("12", internal["Observation"])
        self.assertIn("can overlap", internal["Observation"])
        self.assertIn("1 SharePoint site", internal["Observation"])
        self.assertNotIn("9 external-user or external-link", external["Observation"])
        self.assertNotEqual(anyone["Feature"], internal["Feature"])

    def test_data_exposure_evidence_is_linked_into_the_workbook_bundle(self):
        today = datetime.now(timezone.utc).date().isoformat()
        with tempfile.TemporaryDirectory() as directory:
            sam = self._csv(directory, "sam.csv", [{
                "Report Date": today,
                "Site URL": "https://example.sharepoint.com/sites/hr",
                "Primary Admin": "owner@example.com",
                "Anyone link count": "1",
            }])
            exposure = build_data_exposure_assessment([sam], [])

        bundle = build_evidence_bundle(
            exposure["recommendations"],
            ({}, []), {}, {}, {}, {}, {}, exposure,
        )

        self.assertIn("data_exposure_detail", bundle["sheets"])
        risk = next(item for item in bundle["recommendations"] if item["Feature"] == "Review links that allow access without sign-in")
        self.assertEqual(risk["EvidenceAvailable"], "Yes")
        self.assertEqual(risk["EvidenceSheet"], "Data Exposure Detail")

    def test_missing_scan_instructions_are_rendered_in_html_output(self):
        exposure = build_data_exposure_assessment()
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                report_path = export_to_html(exposure["recommendations"], tenant_name="Example")
                body = Path(report_path).read_text(encoding="utf-8")
            finally:
                os.chdir(original_cwd)

        self.assertIn("Confirm who can access sensitive content", body)
        self.assertIn("Complete the content access review", body)
        self.assertIn("Responsible role", body)
        self.assertNotIn("DSPM_REPORT_PATHS", body)
        self.assertNotIn("--dspm-report", body)


if __name__ == "__main__":
    unittest.main()

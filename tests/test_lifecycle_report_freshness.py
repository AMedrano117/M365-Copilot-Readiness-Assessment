"""Lifecycle dates are explicit and their acceptance window is independent."""

import csv
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from Core.data_exposure_assessment import build_data_exposure_assessment


FIXTURES = Path(__file__).parent / "fixtures" / "microsoft_reports"
DAY = "2026-09-15"


class LifecycleFreshnessTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        with (FIXTURES / "content_management_assessment.csv").open(encoding="utf-8-sig", newline="") as handle:
            self.row = next(csv.DictReader(handle))

    def write(self, name="lifecycle.csv", **changes):
        path = self.directory / name
        row = {**self.row, **changes}
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
        return path

    def assess(self, paths, **settings):
        return build_data_exposure_assessment([str(path) for path in paths], [],
            evaluation_date=DAY, **settings)

    def confirmed(self, path, day="2026-07-16"):
        return {hashlib.sha256(path.read_bytes()).hexdigest(): day}

    def test_confirmed_sixty_one_day_export_is_accepted_within_ninety_days(self):
        path = self.write()
        original = path.read_bytes()
        result = self.assess([path], lifecycle_max_age_days=90,
            lifecycle_report_dates=self.confirmed(path), sam_max_age_days=35, dspm_max_age_days=8)
        summary = result["lifecycle_summary"]
        self.assertEqual(summary["freshness"], "fresh")
        self.assertEqual(summary["max_age_days"], 90)
        self.assertEqual(summary["age_days"], 61)
        self.assertEqual(summary["date_basis"], "Operator-confirmed report date")
        self.assertEqual(summary["operator_confirmed_dates"], ["2026-07-16T00:00:00+00:00"])
        row = result["lifecycle_evidence_rows"][0]
        self.assertEqual(row["Report Date Basis"], "Operator-confirmed report date")
        self.assertIn(row["Source Hash"], self.confirmed(path))
        report = result["sources"]["sam"]["reports"][0]
        self.assertEqual((report["freshness"], report["max_age_days"]), ("fresh", 90))
        lifecycle_findings = [r for r in result["recommendations"] if r.get("EvidenceKey") == "sharepoint_lifecycle_detail"]
        self.assertTrue(lifecycle_findings)
        self.assertTrue(all(r["EvidenceMaxAgeDays"] == 90 for r in lifecycle_findings))
        self.assertTrue(all(r["EvidenceDateBasis"] == "Operator-confirmed report date" for r in lifecycle_findings))
        self.assertFalse(any(r.get("FindingKey") == "data_exposure.lifecycle.freshness" for r in lifecycle_findings))
        self.assertEqual(Path(path).read_bytes(), original)
        self.assertEqual(result["sources"]["sam"]["max_age_days"], 35)
        self.assertEqual(result["sources"]["dspm"]["max_age_days"], 8)

    def test_day_ninety_is_accepted_but_day_ninety_one_is_stale(self):
        path = self.write()
        for day, expected in (("2026-06-17", "fresh"), ("2026-06-16", "stale")):
            with self.subTest(day=day):
                result = self.assess([path], lifecycle_max_age_days=90,
                    lifecycle_report_dates=self.confirmed(path, day))
                self.assertEqual(result["lifecycle_summary"]["freshness"], expected)
                self.assertEqual(result["sources"]["sam"]["reports"][0]["freshness"], expected)

    def test_unknown_date_is_not_inferred_from_filename_or_site_dates(self):
        path = self.write("Report_20260716.csv")
        result = self.assess([path], lifecycle_max_age_days=180)
        self.assertEqual(result["lifecycle_summary"]["freshness"], "unknown")
        self.assertEqual(result["lifecycle_summary"]["date_basis"], "Unknown")
        self.assertEqual(result["lifecycle_summary"]["operator_confirmed_dates"], [])
        self.assertEqual(result["lifecycle_evidence_rows"][0]["Report Date"], "")

    def test_internal_report_date_wins_over_operator_fallback(self):
        path = self.write(**{"Report date": "2026-09-12"})
        result = self.assess([path], lifecycle_report_dates=self.confirmed(path))
        self.assertEqual(result["lifecycle_summary"]["oldest_report_date"], "2026-09-12T00:00:00+00:00")
        self.assertEqual(result["lifecycle_summary"]["date_basis"], "Report date field")
        self.assertEqual(result["lifecycle_summary"]["operator_confirmed_dates"], [])

    def test_confirmation_is_scoped_to_exact_file_bytes(self):
        original = self.write()
        confirmations = self.confirmed(original)
        copied = self.directory / "renamed.csv"
        copied.write_bytes(original.read_bytes())
        self.assertEqual(self.assess([copied], lifecycle_report_dates=confirmations)["lifecycle_summary"]["freshness"], "fresh")
        self.write(**{"Site name": "Changed report"})
        result = self.assess([original], lifecycle_report_dates=confirmations)
        self.assertEqual(result["lifecycle_summary"]["freshness"], "unknown")
        other = self.write("other.csv", **{"URL": "https://example.sharepoint.com/sites/another"})
        self.assertEqual(self.assess([other], lifecycle_report_dates=confirmations)["lifecycle_summary"]["freshness"], "unknown")

    def test_confirmation_does_not_date_permission_reports(self):
        path = self.directory / "snapshot.csv"
        with (FIXTURES / "permission_snapshot.csv").open(encoding="utf-8-sig", newline="") as handle:
            row = next(csv.DictReader(handle))
        row["Report date"] = ""
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
        result = self.assess([path], lifecycle_report_dates=self.confirmed(path))
        self.assertEqual(result["sources"]["sam"]["freshness"], "unknown")
        self.assertEqual(result["sources"]["sam"]["reports"][0]["report_date"], "")

    def test_lifecycle_environment_window_is_independent_of_sam(self):
        path = self.write(**{"Report date": "2026-07-16"})
        with patch.dict(os.environ, {"SAM_REPORT_MAX_AGE_DAYS": "1", "LIFECYCLE_REPORT_MAX_AGE_DAYS": "90"}):
            result = self.assess([path])
        self.assertEqual(result["lifecycle_summary"]["freshness"], "fresh")
        self.assertEqual(result["sources"]["sam"]["max_age_days"], 1)
        result = self.assess([path], lifecycle_max_age_days=30)
        self.assertEqual(result["lifecycle_summary"]["freshness"], "stale")

    def test_future_and_invalid_confirmed_dates_remain_unknown(self):
        path = self.write()
        for day in ("2026-09-16", "not a date"):
            with self.subTest(day=day):
                result = self.assess([path], lifecycle_report_dates=self.confirmed(path, day))
                self.assertEqual(result["lifecycle_summary"]["freshness"], "unknown")


if __name__ == "__main__":
    unittest.main()

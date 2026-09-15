"""Portal-schema regression fixtures contain invented tenant data only."""

import csv
import os
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from Core.data_exposure_assessment import (
    _classify_record, _matches_authoritative_schema, build_data_exposure_assessment,
    detect_sharepoint_report,
)
from Core.evidence_contract import reconcile_observations


FIXTURES = Path(__file__).parent / "fixtures" / "microsoft_reports"


class MicrosoftReportImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.today = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        self.old = (datetime.now(timezone.utc) - timedelta(days=90)).replace(microsecond=0).isoformat()

    def rows(self, name):
        with (FIXTURES / name).open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def write(self, name, rows, headers=None):
        path = self.directory / name
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers or list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return str(path)

    def snapshot(self, **changes):
        row = self.rows("permission_snapshot.csv")[0]
        row["Report date"] = self.today
        row.update(changes)
        return row

    def test_actual_snapshot_aliases_and_public_label(self):
        path = self.write("snapshot.csv", [self.snapshot()])
        result = build_data_exposure_assessment([path], [])
        scan = result["sources"]["sam"]
        self.assertEqual(scan["signals"], {"Organization links": 8, "External exposure": 6, "EEEU permissions": 2, "Everyone permissions": 1})
        row = result["evidence_rows"][0]
        self.assertEqual(row["Sensitivity Label"], "Public")
        self.assertEqual(row["Severity"], "Medium")
        self.assertEqual(row["Owner"], "owner@example.com")
        self.assertEqual(row["Report Date"], self.today)
        self.assertEqual(row["Report Type"], "permission_snapshot")
        self.assertEqual(row["Tenant ID"], "example-tenant")
        self.assertEqual(row["Workload"], "SharePoint")

    def test_detailed_recipient_and_eeeu_do_not_imply_external_access(self):
        rows = self.rows("detailed_eeeu.csv")
        rows[0]["ReportDate"] = self.today
        path = self.write("detailed.csv", rows)
        result = build_data_exposure_assessment([path], [])
        self.assertEqual(result["sources"]["sam"]["signals"], {"EEEU permissions": 1})
        self.assertEqual(result["evidence_rows"][0]["Site ID"], "example-site")
        self.assertEqual(result["evidence_rows"][0]["Report Type"], "special_group_permissions")
        legacy = _classify_record({"Site URL": "https://example.sharepoint.com/sites/team", "Permission Recipient": "Everyone except external users"}, "sam")
        self.assertEqual(legacy["_signals"], {"EEEU permissions": 1})

    def test_narrower_snapshot_retains_unconfirmed_older_sites_with_dates(self):
        old_rows = [self.snapshot(**{"Report date": self.old, "Anyone link count": "99"}),
                    self.snapshot(**{"Report date": self.old, "Site ID": "removed", "Site URL": "https://example.sharepoint.com/sites/removed", "Anyone link count": "100"})]
        old = self.write("old.csv", old_rows)
        recent = self.write("recent.csv", [self.snapshot(**{"Anyone link count": "1"})])
        for paths in ([old, recent], [recent, old]):
            result = build_data_exposure_assessment(paths, [])
            scan = result["sources"]["sam"]
            self.assertEqual(scan["signals"]["Anyone links"], 101)
            self.assertEqual(scan["records_read"], 3)
            self.assertEqual(scan["records_selected"], 3)
            self.assertEqual(scan["freshness"], "stale")
            self.assertIn("populations differ", next(r for r in scan["reports"] if r["source_file"] == "old.csv")["scope_note"])
            self.assertTrue(any("removed" in r["Site URL"] and r["Report Date"] == self.old for r in result["evidence_rows"]))

    def test_stable_site_id_replaces_renamed_url_and_retains_original_lineage(self):
        old = self.write("old.csv", [self.snapshot(**{"Report date": self.old, "Anyone link count": "99"})])
        recent = self.write("recent.csv", [self.snapshot(**{
            "Site URL": "https://example.sharepoint.com/sites/renamed", "Anyone link count": "1"})])
        original = Path(old).read_bytes()
        for paths in ([old, recent], [recent, old]):
            result = build_data_exposure_assessment(paths, [])
            self.assertEqual(result["sources"]["sam"]["signals"]["Anyone links"], 1)
            self.assertEqual(len(result["evidence_rows"]), 1)
            self.assertEqual(result["evidence_rows"][0]["Source File"], "recent.csv")
            reports = {row["source_file"]: row for row in result["sources"]["sam"]["reports"]}
            self.assertEqual(reports["old.csv"]["status"], "superseded")
            self.assertFalse(any("populations differ" in row["scope_note"] for row in reports.values()))
            facts = reconcile_observations(result["observations"], evaluation_date=self.today)
            self.assertEqual({row["selection"] for row in facts if row["source_file"] == "old.csv"}, {"superseded"})
            old_fact = next(row for row in facts if row["source_file"] == "old.csv")
            self.assertEqual(old_fact["site_url"], self.snapshot()["Site URL"])
            self.assertEqual(old_fact["site_id"], self.snapshot()["Site ID"])
            self.assertTrue(old_fact["source_hash"])
        self.assertEqual(Path(old).read_bytes(), original)

    def test_shared_id_in_changed_population_does_not_keep_superseded_risk_rows(self):
        old = self.write("old.csv", [
            self.snapshot(**{"Report date": self.old, "Anyone link count": "99"}),
            self.snapshot(**{"Report date": self.old, "Site ID": "absent", "Site URL": "https://example.sharepoint.com/sites/absent", "Anyone link count": "2"}),
        ])
        recent = self.write("recent.csv", [self.snapshot(**{
            "Site URL": "https://example.sharepoint.com/sites/renamed", "Anyone link count": "1"})])
        result = build_data_exposure_assessment([old, recent], [])
        self.assertEqual(result["sources"]["sam"]["signals"]["Anyone links"], 3)
        risks = result["evidence_rows"]
        self.assertEqual(len(risks), 2)
        self.assertEqual({row["Site URL"] for row in risks}, {
            "https://example.sharepoint.com/sites/absent", "https://example.sharepoint.com/sites/renamed"})

    def test_url_fallback_matches_unique_known_id_but_not_a_reused_url(self):
        old = self.write("old.csv", [self.snapshot(**{
            "Report date": self.old, "Site ID": "", "Anyone link count": "99"})])
        recent = self.write("recent.csv", [self.snapshot(**{
            "Site URL": self.snapshot()["Site URL"].upper() + "/", "Anyone link count": "1"})])
        result = build_data_exposure_assessment([old, recent], [])
        self.assertEqual(result["sources"]["sam"]["signals"]["Anyone links"], 1)
        prior = self.write("prior.csv", [self.snapshot(**{
            "Site ID": "different-site", "Report date": self.old, "Anyone link count": "2"})])
        result = build_data_exposure_assessment([old, recent, prior], [])
        self.assertEqual(result["sources"]["sam"]["signals"]["Anyone links"], 102)
        self.assertEqual(len({row["scope"] for row in result["observations"]}), 3)

    def test_same_site_id_does_not_merge_other_tenants_or_workloads(self):
        old = self.write("old.csv", [self.snapshot(**{"Report date": self.old, "Anyone link count": "2"})])
        for changes in ({"Tenant ID": "another-tenant"}, {"Workload": "OneDrive"}):
            with self.subTest(changes=changes):
                recent = self.write("recent.csv", [self.snapshot(**{**changes, "Anyone link count": "1"})])
                result = build_data_exposure_assessment([old, recent], [])
                self.assertEqual(result["sources"]["sam"]["signals"]["Anyone links"], 3)
                self.assertEqual(len(result["evidence_rows"]), 2)

    def test_corrupt_file_does_not_contribute_url_aliases(self):
        old = self.write("old.csv", [self.snapshot(**{"Report date": self.old, "Site ID": "", "Anyone link count": "99"})])
        recent = self.write("recent.csv", [self.snapshot(**{"Anyone link count": "1"})])
        archive_path = self.directory / "corrupt.zip"
        conflicting = self.write("conflicting.csv", [self.snapshot(**{"Site ID": "another-site"})])
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("valid.csv", Path(conflicting).read_bytes())
            archive.writestr("invalid.csv", b"\xff\xff\xff")
        result = build_data_exposure_assessment([old, recent, str(archive_path)], [])
        self.assertTrue(result["sources"]["sam"]["errors"])
        self.assertEqual(result["sources"]["sam"]["signals"]["Anyone links"], 1)
        self.assertFalse(any(row["source_file"].startswith("corrupt.zip") for row in result["observations"]))

    def test_future_snapshot_cannot_erase_current_risk_after_site_rename(self):
        old = self.write("current.csv", [self.snapshot(**{"Report date": "2026-09-14", "Anyone link count": "20"})])
        future = self.write("future.csv", [self.snapshot(**{
            "Report date": "2026-09-16", "Site URL": "https://example.sharepoint.com/sites/renamed", "Anyone link count": "0"})])
        result = build_data_exposure_assessment([old, future], [], evaluation_date="2026-09-15")
        self.assertEqual(result["sources"]["sam"]["signals"]["Anyone links"], 20)
        self.assertEqual({row["Source File"] for row in result["evidence_rows"]}, {"current.csv"})
        reports = {row["source_file"]: row for row in result["sources"]["sam"]["reports"]}
        self.assertEqual(reports["current.csv"]["status"], "selected")
        self.assertEqual(reports["future.csv"]["status"], "future_date")
        facts = reconcile_observations(result["observations"], evaluation_date="2026-09-15")
        self.assertTrue(all(row["source_file"] == "current.csv" for row in facts if row["selection"] == "selected"))
        self.assertTrue(any("after the evaluation date" in message for message in result["operator_messages"]))

    def test_old_other_workload_is_not_hidden_by_recent_sharepoint_date(self):
        old = self.write("onedrive.csv", [self.snapshot(**{"Report date": self.old, "Site URL": "https://example-my.sharepoint.com/personal/example", "Anyone link count": "3"})])
        recent = self.write("sharepoint.csv", [self.snapshot(**{"Anyone link count": "1"})])
        result = build_data_exposure_assessment([old, recent], [])
        scan = result["sources"]["sam"]
        self.assertEqual(scan["signals"]["Anyone links"], 4)
        self.assertEqual(scan["freshness"], "stale")
        self.assertEqual(scan["oldest_report_date"], self.old)
        self.assertEqual(scan["latest_report_date"], self.today)
        self.assertEqual({r["Workload"] for r in result["evidence_rows"]}, {"SharePoint", "OneDrive"})

    def test_unknown_date_remains_unknown_alongside_recent_report(self):
        unknown = self.write("unknown.csv", [self.snapshot(**{"Report date": ""})])
        recent = self.write("recent.csv", [self.snapshot()])
        result = build_data_exposure_assessment([unknown, recent], [])
        self.assertEqual(result["sources"]["sam"]["freshness"], "unknown")
        self.assertTrue(all(r["status"] == "selected" for r in result["sources"]["sam"]["reports"]))

    def test_my_site_root_does_not_replace_personal_onedrive_snapshot(self):
        drive = self.write("onedrive.csv", [self.snapshot(**{"Site URL": "https://example-my.sharepoint.com/personal/example", "Report date": self.old})])
        root = self.write("sharepoint.csv", [self.snapshot(**{"Site URL": "https://example-my.sharepoint.com/"})])
        result = build_data_exposure_assessment([drive, root], [])
        scan = result["sources"]["sam"]
        self.assertEqual(scan["records_selected"], 2)
        self.assertEqual(scan["files_loaded"], 2)
        self.assertEqual(scan["coverage"]["workloads_missing"], [])

    def test_cma_lifecycle_flags_and_empty_owner_fields_remain_distinct(self):
        path = str(FIXTURES / "content_management_assessment.csv")
        result = build_data_exposure_assessment([path], [])
        summary = result["lifecycle_summary"]
        self.assertTrue(result["available"])
        self.assertEqual(summary["site_count"], 3)
        self.assertEqual(summary["inactive_site_count"], 2)
        self.assertEqual(summary["ownerless_site_count"], 1)
        self.assertEqual(summary["missing_owner_contact_count"], 2)
        self.assertEqual(summary["unknown_owner_status_count"], 1)
        self.assertEqual(summary["freshness"], "unknown")
        self.assertEqual(result["sources"]["sam"]["files_loaded"], 0)
        self.assertEqual(result["evidence_rows"], [])
        self.assertTrue(all(not row["Report Date"] for row in result["lifecycle_evidence_rows"]))
        actions = [r for r in result["recommendations"] if r["Disposition"] == "Action"]
        self.assertTrue(all(r["EvidenceKey"] == "sharepoint_lifecycle_detail" for r in actions))
        self.assertTrue(any(r["Feature"] == "SharePoint oversharing evidence" for r in result["recommendations"]))

    def test_cma_and_sensitivity_labels_alone_are_not_dspm_assessments(self):
        path = str(FIXTURES / "content_management_assessment.csv")
        result = build_data_exposure_assessment([], [path])
        self.assertEqual(result["sources"]["dspm"]["files_loaded"], 0)
        self.assertFalse(result["available"])
        self.assertFalse(_matches_authoritative_schema({"Site URL": "https://example.sharepoint.com", "Sensitivity Label": "Confidential", "Report Date": self.today}, "dspm"))

    def test_empty_label_report_is_recognized_without_date_or_assurance(self):
        path = self.directory / "Sites_with_files_labeled_Public_2026-09-14.csv"
        path.write_bytes((FIXTURES / "label_inventory_empty.csv").read_bytes())
        result = build_data_exposure_assessment([str(path)], [])
        scan = result["sources"]["sam"]
        self.assertEqual(scan["files_recognized"], 1)
        self.assertEqual(scan["files_loaded"], 0)
        self.assertEqual(scan["records_read"], 0)
        self.assertEqual(scan["errors"], [])
        self.assertEqual(scan["reports"][0]["report_type"], "label_inventory")
        self.assertEqual(scan["reports"][0]["status"], "empty")
        self.assertEqual(scan["reports"][0]["report_date"], "")
        self.assertFalse(any(r["Disposition"] == "Assurance" for r in result["recommendations"]))

    def test_downloaded_zip_is_read_without_extracting_member_paths(self):
        path = self.directory / "detail.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("nested/../../example.csv", (FIXTURES / "detailed_eeeu.csv").read_bytes())
        result = build_data_exposure_assessment([str(path)], [])
        self.assertEqual(result["sources"]["sam"]["signals"], {"EEEU permissions": 1})
        self.assertIn("detail.zip!nested/../../example.csv", result["evidence_rows"][0]["Source File"])
        self.assertEqual(list(self.directory.iterdir()), [path])

    def test_snapshot_and_matching_detail_keep_evidence_without_adding_counts(self):
        snapshot = self.write("snapshot.csv", [self.snapshot()])
        rows = self.rows("detailed_eeeu.csv")
        rows[0]["ReportDate"] = self.today
        detail = self.write("detail.csv", rows)
        result = build_data_exposure_assessment([snapshot, detail], [])
        self.assertEqual(result["sources"]["sam"]["signals"]["EEEU permissions"], 2)
        self.assertEqual(len(result["evidence_rows"]), 2)

    def test_new_detail_exposure_is_not_hidden_by_older_zero_snapshot(self):
        snapshot = self.write("snapshot.csv", [self.snapshot(**{"Report date": self.old, "EEEU permission count": "0"})])
        rows = self.rows("detailed_eeeu.csv")
        rows[0]["ReportDate"] = self.today
        detail = self.write("detail.csv", rows)
        result = build_data_exposure_assessment([snapshot, detail], [])
        self.assertEqual(result["sources"]["sam"]["signals"]["EEEU permissions"], 1)
        self.assertTrue(any(r["Report Type"] == "special_group_permissions" for r in result["evidence_rows"]))

    def test_complete_zero_fields_only_provide_assurance_for_exported_scope(self):
        row = self.snapshot(**{field: "0" for field in [
            "Anyone link count", "Everyone permission count", "EEEU permission count",
            "PeopleInYourOrg link count", "Guest user permissions", "External participant permissionst",
        ]})
        sam = self.write("sam.csv", [row, {**row, "Site URL": "https://example-my.sharepoint.com/personal/example"}])
        dspm = self.write("dspm.csv", [{"Assessment Date": self.today, "Site URL": "https://example.sharepoint.com/sites/team", "Potentially overshared items": "0", "Unlabeled sensitive item count": "0"}])
        result = build_data_exposure_assessment([sam], [dspm])
        assurance = [r for r in result["recommendations"] if r["Disposition"] == "Assurance"]
        self.assertEqual(len(assurance), 1)
        self.assertIn("tenant-wide completeness is not verified", assurance[0]["Observation"])
        row["Anyone link count"] = "not available"
        sam = self.write("sam.csv", [row, {**row, "Site URL": "https://example-my.sharepoint.com/personal/example"}])
        result = build_data_exposure_assessment([sam], [dspm])
        self.assertIn("Anyone links", result["sources"]["sam"]["coverage"]["domains_unknown"])
        self.assertFalse(any(r["Disposition"] == "Assurance" for r in result["recommendations"]))

    def test_dspm_specific_metrics_can_include_generic_sharing_context(self):
        row = {"Site URL": "https://example.sharepoint.com/sites/team", "Potentially overshared items": "1", "Link Type": "Anyone", "Assessment Date": self.today}
        self.assertTrue(_matches_authoritative_schema(row, "dspm"))

    def test_empty_xlsx_sheet_retains_report_schema_without_a_scan_date(self):
        from openpyxl import Workbook
        path = self.directory / "labels.xlsx"
        with (FIXTURES / "label_inventory_empty.csv").open(encoding="utf-8", newline="") as handle:
            headers = next(csv.reader(handle))
        workbook = Workbook()
        workbook.active.append(headers)
        workbook.save(path)
        workbook.close()
        result = build_data_exposure_assessment([str(path)], [])
        self.assertEqual(result["sources"]["sam"]["reports"][0]["status"], "empty")
        self.assertEqual(result["sources"]["sam"]["records_read"], 0)

    def test_explicit_empty_paths_do_not_import_environment_reports(self):
        path = str(FIXTURES / "permission_snapshot.csv")
        with patch.dict(os.environ, {"SAM_DAG_REPORT_PATHS": path, "DSPM_REPORT_PATHS": path}):
            result = build_data_exposure_assessment([], [])
        self.assertEqual(result["sources"]["sam"]["files_requested"], 0)
        self.assertEqual(result["sources"]["dspm"]["files_requested"], 0)

    def test_header_detection_has_no_filename_dependency(self):
        with (FIXTURES / "label_inventory_empty.csv").open(newline="", encoding="utf-8") as handle:
            headers = next(csv.reader(handle))
        self.assertEqual(detect_sharepoint_report(headers), "label_inventory")
        self.assertEqual(detect_sharepoint_report(self.snapshot()), "permission_snapshot")
        self.assertEqual(detect_sharepoint_report({"Name": "Other", "Status": "Healthy"}), "")


if __name__ == "__main__":
    unittest.main()

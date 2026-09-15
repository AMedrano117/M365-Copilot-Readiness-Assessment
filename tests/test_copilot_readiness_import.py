"""Sanitized offline fixtures for Microsoft 365 Copilot readiness exports."""

import csv
import json
import tempfile
import unittest
from pathlib import Path

from Core.copilot_readiness_import import (
    FLAG_COLUMNS,
    KNOWN_COLUMNS,
    REQUIRED_COLUMNS,
    load_copilot_readiness_export,
)


class CopilotReadinessImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "readiness.csv"

    def row(self, identity="person@example.test", **overrides):
        row = {name: "False" for name in FLAG_COLUMNS.values()}
        row.update({
            "Report Refresh Date": "2026-09-12",
            "User Principal Name": identity,
            "Report Period": "30",
        })
        row.update(overrides)
        return row

    def write(self, rows, headers=KNOWN_COLUMNS, encoding="utf-8-sig"):
        with self.path.open("w", encoding=encoding, newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            for row in rows:
                writer.writerow([row.get(name, "") for name in headers])
        return self.path

    def test_aggregates_only_exported_rows_and_preserves_real_period_and_date(self):
        rows = [self.row(f"person{index}@example.test") for index in range(3)]
        rows[0]["Has Copilot license assigned"] = "True"
        rows[1]["Suggested candidate for Copilot"] = "True"
        rows[1]["Uses Outlook email"] = "True"
        result = load_copilot_readiness_export(self.write(rows))

        self.assertTrue(result["available"])
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["total_rows"], 3)
        self.assertEqual(result["report_date"], "2026-09-12")
        self.assertEqual(result["report_period"], "30")
        self.assertEqual(result["metrics"]["license_assigned"], {"true": 1, "false": 2, "unknown": 0})
        self.assertEqual(result["metrics"]["suggested_candidate"]["true"], 1)
        self.assertEqual(result["metrics"]["outlook_email"]["true"], 1)
        self.assertIn("exported rows only", result["summary"])
        self.assertIn("do not establish actual Copilot usage", result["summary"])
        self.assertNotIn("active_users", result)
        self.assertNotIn("user_details", result)
        self.assertNotIn("@example.test", json.dumps(result))
        self.assertEqual(result["source_file"], "readiness.csv")
        self.assertNotIn(self.temp.name, json.dumps(result))

    def test_false_unknown_and_unrecognized_values_are_distinct(self):
        rows = [
            self.row("one@example.test", **{"Uses Teams chat": " false "}),
            self.row("two@example.test", **{"Uses Teams chat": "TRUE"}),
            self.row("three@example.test", **{"Uses Teams chat": ""}),
            self.row("four@example.test", **{"Uses Teams chat": "unknown"}),
            self.row("five@example.test", **{"Uses Teams chat": "private-value@example.test"}),
        ]
        result = load_copilot_readiness_export(self.write(rows), include_user_details=True)
        self.assertTrue(result["available"])
        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["metrics"]["teams_chat"], {"true": 1, "false": 1, "unknown": 3})
        self.assertEqual([row["teams_chat"] for row in result["user_details"]], [False, True, None, None, None])
        self.assertEqual(result["user_details"][0]["user_principal_name"], "one@example.test")
        self.assertNotIn("private-value", json.dumps(result))
        self.assertIn("1 unrecognized", " ".join(result["warnings"]))

    def test_nine_column_export_keeps_candidate_unknown_and_other_readiness_metrics(self):
        from Core.portal_report_import import route_portal_reports
        rows = [self.row('one@example.test', **{'Has Copilot license assigned': 'True'}),
                self.row('two@example.test', **{'Uses eligible update channel': 'True'})]
        self.write(rows, headers=REQUIRED_COLUMNS)
        routed = route_portal_reports([self.path.parent])
        self.assertEqual(routed['readiness'], [str(self.path)])
        value = load_copilot_readiness_export(self.path, include_user_details=True)
        self.assertTrue(value['available'])
        self.assertEqual(value['metrics']['suggested_candidate'], {'true': 0, 'false': 0, 'unknown': 2})
        self.assertEqual(value['metrics']['license_assigned'], {'true': 1, 'false': 1, 'unknown': 0})
        self.assertEqual(value['metrics']['eligible_update_channel']['true'], 1)
        self.assertEqual(value['report_date'], '2026-09-12')
        self.assertEqual(value['report_period'], '30')
        self.assertTrue(all(row['suggested_candidate'] is None for row in value['user_details']))
        self.assertIn('not included in this export', ' '.join(value['warnings']))

    def test_missing_required_fields_still_rejects_incomplete_exports(self):
        for field in REQUIRED_COLUMNS:
            with self.subTest(field=field):
                self.write([self.row()], headers=tuple(name for name in KNOWN_COLUMNS if name != field))
                value = load_copilot_readiness_export(self.path)
                self.assertFalse(value['available'])
                self.assertEqual(value['status'], 'invalid_headers')
                self.assertEqual(value['metrics'], {})

    def test_headers_are_whitespace_and_case_insensitive(self):
        self.write([self.row()])
        content = self.path.read_text(encoding="utf-8-sig")
        header, rest = content.split("\n", 1)
        self.path.write_text(
            ",".join(f"  {value.upper()}  " for value in header.split(",")) + "\n" + rest,
            encoding="utf-8-sig",
        )
        self.assertTrue(load_copilot_readiness_export(self.path)["available"])

    def test_utf16_export_is_supported(self):
        result = load_copilot_readiness_export(self.write([self.row()], encoding="utf-16"))
        self.assertTrue(result["available"])

    def test_usage_export_is_rejected_without_converting_to_zero_readiness(self):
        self.path.write_text("User Principal Name,Last Activity Date\nperson@example.test,2026-09-12\n")
        result = load_copilot_readiness_export(self.path)
        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "invalid_headers")
        self.assertEqual(result["metrics"], {})
        self.assertIn("different schema", result["error"])

    def test_duplicate_and_empty_headers_are_rejected(self):
        for headers in [(*REQUIRED_COLUMNS, "User Principal Name"), (*REQUIRED_COLUMNS, "")]:
            with self.subTest(headers=headers):
                result = load_copilot_readiness_export(self.write([self.row()], headers=headers))
                self.assertEqual(result["status"], "invalid_headers")

    def test_extra_columns_are_ignored_without_exposing_their_values(self):
        row = self.row(**{"Other column": "private@example.test"})
        result = load_copilot_readiness_export(self.write([row], (*REQUIRED_COLUMNS, "Other column")))
        self.assertTrue(result["available"])
        self.assertEqual(result["status"], "warning")
        self.assertNotIn("private@example.test", json.dumps(result))

    def test_mixed_snapshots_are_rejected(self):
        for column, value in [("Report Refresh Date", "2026-09-13"), ("Report Period", "90")]:
            with self.subTest(column=column):
                rows = [self.row("one@example.test"), self.row("two@example.test", **{column: value})]
                result = load_copilot_readiness_export(self.write(rows), include_user_details=True)
                self.assertEqual(result["status"], "invalid_data")
                self.assertFalse(result["available"])
                self.assertEqual(result["metrics"], {})
                self.assertNotIn("user_details", result)

    def test_duplicate_users_are_not_double_counted_or_disclosed(self):
        rows = [self.row("Person@example.test"), self.row("person@example.test")]
        result = load_copilot_readiness_export(self.write(rows), include_user_details=True)
        self.assertFalse(result["available"])
        self.assertIn("duplicate user", result["error"])
        self.assertEqual(result["metrics"], {})
        self.assertNotIn("@example.test", json.dumps(result))

    def test_invalid_metadata_does_not_use_filename_or_other_rows_as_a_fallback(self):
        rows = [self.row("one@example.test"), self.row("two@example.test", **{
            "Report Refresh Date": "2026-02-30",
            "Report Period": "D28",
        })]
        result = load_copilot_readiness_export(self.write(rows))
        self.assertTrue(result["available"])
        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["report_date"], "")
        self.assertEqual(result["report_period"], "")
        self.assertEqual(result["report_dates"], ["2026-09-12"])
        self.assertEqual(result["report_periods"], ["30"])

    def test_missing_user_names_keep_counts_explicitly_scoped_to_rows(self):
        result = load_copilot_readiness_export(self.write([self.row("")]))
        self.assertTrue(result["available"])
        self.assertEqual(result["total_rows"], 1)
        self.assertIn("uniqueness cannot be verified", " ".join(result["warnings"]))

    def test_empty_or_malformed_csv_is_not_successful_zero_evidence(self):
        result = load_copilot_readiness_export(self.write([]))
        self.assertEqual(result["status"], "invalid_data")
        self.write([self.row()])
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write("extra,columns\n")
        result = load_copilot_readiness_export(self.path)
        self.assertFalse(result["available"])
        self.assertEqual(result["metrics"], {})

    def test_blank_lines_are_ignored(self):
        self.write([self.row()])
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write("\n,,,,,,,,,\n")
        result = load_copilot_readiness_export(self.path)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["total_rows"], 1)

    def test_missing_path_and_unreadable_or_wrong_format_are_reported_without_exception(self):
        self.assertEqual(load_copilot_readiness_export(None)["status"], "not_supplied")
        self.assertEqual(load_copilot_readiness_export(self.path)["status"], "error")
        self.assertEqual(load_copilot_readiness_export(self.path.with_suffix(".xlsx"))["status"], "error")
        self.path.write_bytes(b"\x80\x81\x82")
        result = load_copilot_readiness_export(self.path)
        self.assertEqual(result["status"], "invalid_data")
        self.assertNotIn(self.temp.name, json.dumps(result))


if __name__ == "__main__":
    unittest.main()

"""The dashboard folder is small to read and lossless to reconstruct."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from Core.dashboard_package import read_dashboard_package, write_dashboard_package


def dashboard_payload():
    """Use realistic cross references, repeated views, and untouched native values."""
    evidence = []
    records = []
    for position in range(7):
        evidence_id = f"EVD-{position:04d}"
        evidence.append({
            "record_id": evidence_id,
            "dataset_id": "SRC-registration",
            "dataset": "auth_methods",
            "source_record_index": position,
            # Native IDs can repeat, while retained occurrences stay distinct.
            "source_record_id": f"user-{position // 2}",
            "raw": {
                "id": f"user-{position // 2}",
                "userPrincipalName": f"person{position}@example.invalid",
                "displayName": f"Fictional person {position}",
                "retainedOccurrenceMarker": f"native-row-{position}-retained-once",
                "isMfaRegistered": False,
                "nested": {"bool": False, "zero": 0, "unknown": None,
                           "methods": ["phone", {"name": "Authenticator"}]},
            },
        })
        records.append({
            "record_id": f"DET-{position:04d}",
            "fields": {"upn": f"person{position}@example.invalid",
                       "displayName": f"Fictional person {position}",
                       "lastSignIn": None, "isMfaRegistered": False},
            "field_status": {"lastSignIn": {"status": "unavailable",
                                             "reason": "Not returned by collection."}},
            "evidence_record_ids": [evidence_id],
            "source_refs": [{"dataset": "auth_methods", "dataset_index": 0,
                             "record_index": position}],
        })
    finding = {
        "finding_id": "ENT-005", "finding_uid": "FND-tenant-mfa",
        "title": "Close MFA registration gaps", "domain_id": "identity",
        "control_id": "IDENTITY-001", "service": "Entra",
        "disposition": "Action", "priority": "High",
        "record_count": len(records), "record_status": "records_available",
        "record_type": "user_registration", "records": records,
        "evidence_record_ids": [row["record_id"] for row in evidence],
        "record_selection": "Only explicitly false registration results.",
        "record_limitations": ["Last sign-in was not retained."],
        "record_reconciliation": {"exported_records": len(records)},
    }
    legacy = {
        "RecommendationId": "ENT-005", "Feature": finding["title"],
        "Priority": "High", "Disposition": "Action",
        "Observation": "Seven registration records require review.",
        "records": records, "record_count": len(records),
        "evidence_record_ids": finding["evidence_record_ids"],
    }
    return {
        "format": "m365-readiness-assessment",
        "dashboard_schema_version": "1.0.0", "evidence_schema_version": "1.1.0",
        "methodology_version": "4.0.0", "tenant_id": "invented-tenant-id",
        "tenant_name": "Invented tenant", "evaluation_date": "2026-10-02",
        "generated_at": "2026-10-02T17:46:27Z", "decision": "Not ready for pilot",
        "rationale": "Operational evidence remains unresolved.",
        "counts": {"actions": 1, "remediation": 1},
        "findings": [finding], "recommendations": [legacy],
        "sources": [{"dataset_id": "SRC-registration", "dataset": "auth_methods",
                     "dataset_index": 0, "record_count": len(evidence),
                     "source": {"complete": False, "pages": 2,
                                "collected_at": "2026-10-01T12:00:00Z",
                                "window_start": "2026-09-24T12:00:00Z",
                                "window_end": "2026-10-01T12:00:00Z",
                                "errors": [{"status": 429, "retryAfter": 3}]}}],
        "evidence_records": evidence,
        "assessment_result": {
            "decision": "Not ready for pilot", "recommendations": [legacy],
            "actions": [legacy], "source_windows": [{"source": "auth_methods",
                                                        "end": "2026-10-01T12:00:00Z"}],
            "domain_coverage": [{"check_id": "IDENTITY.REGISTRATION", "collected": True}],
            "custom_nested": {"unknown": None, "empty": [], "flags": [False, True]},
        },
        "custom_top_level": {"keep_this": "Original unsupported extension survives."},
    }


class DashboardPackageTests(unittest.TestCase):
    def write(self, folder, payload=None, **options):
        return Path(write_dashboard_package(
            folder, dashboard_payload() if payload is None else payload, **options))

    def test_default_layout_has_a_small_entry_point_and_navigation_indexes(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "assessment"
            index = self.write(folder)
            self.assertEqual(index, folder / "index.json")
            for relative in ("index.json", "summary.json", "findings/index.json",
                             "sources/index.json", "evidence/index.json", "assessment/index.json"):
                self.assertTrue((folder / relative).is_file(), relative)
            self.assertLess(index.stat().st_size, 65536)
            entry = json.loads(index.read_text(encoding="utf-8"))
            self.assertEqual(entry["format"], "m365-readiness-assessment-package")
            self.assertEqual(entry["package_schema_version"], "1.0.0")
            self.assertEqual(entry["entry_points"], {
                "summary": "summary.json", "findings": "findings/index.json",
                "sources": "sources/index.json", "evidence": "evidence/index.json",
                "assessment": "assessment/index.json",
            })
            for path in folder.rglob("*.json"):
                with path.open(encoding="utf-8") as stream:
                    json.load(stream)

    def test_record_pages_respect_row_limits_and_put_each_row_on_its_own_line(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "assessment"
            self.write(folder, max_bytes=4096, max_records=2)
            pages_checked = 0
            for path in folder.rglob("*.json"):
                text = path.read_text(encoding="utf-8")
                page = json.loads(text)
                if not isinstance(page, dict) or not isinstance(page.get("records"), list):
                    continue
                self.assertLessEqual(len(page["records"]), 2, str(path.relative_to(folder)))
                decoded_rows = []
                for line in text.splitlines():
                    try:
                        row = json.loads(line.strip().removesuffix(","))
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if isinstance(row, dict):
                        decoded_rows.append(row)
                self.assertEqual(decoded_rows, page["records"], str(path.relative_to(folder)))
                pages_checked += 1
            self.assertGreater(pages_checked, 3)

    def test_entire_export_round_trips_without_changing_input(self):
        payload = dashboard_payload()
        original = copy.deepcopy(payload)
        with tempfile.TemporaryDirectory() as temporary:
            index = self.write(Path(temporary) / "assessment", payload)
            self.assertEqual(read_dashboard_package(index), original)
        self.assertEqual(payload, original)

    def test_record_ids_and_duplicate_native_occurrences_are_preserved(self):
        payload = dashboard_payload()
        with tempfile.TemporaryDirectory() as temporary:
            index = self.write(Path(temporary) / "assessment", payload, max_records=2)
            restored = read_dashboard_package(index)
        self.assertEqual(restored["evidence_records"], payload["evidence_records"])
        native_ids = [row["source_record_id"] for row in restored["evidence_records"]]
        self.assertLess(len(set(native_ids)), len(native_ids))
        evidence = {row["record_id"]: row for row in restored["evidence_records"]}
        for record in restored["findings"][0]["records"]:
            self.assertEqual(len(record["evidence_record_ids"]), 1)
            source = evidence[record["evidence_record_ids"][0]]
            self.assertEqual(source["raw"]["userPrincipalName"], record["fields"]["upn"])
            self.assertEqual(source["source_record_index"], record["source_refs"][0]["record_index"])

    def test_raw_rows_and_shared_detail_arrays_are_written_once(self):
        payload = dashboard_payload()
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "assessment"
            self.write(folder, payload)
            text = "\n".join(path.read_text(encoding="utf-8") for path in folder.rglob("*.json"))
            for row in payload["evidence_records"]:
                self.assertEqual(text.count(row["raw"]["retainedOccurrenceMarker"]), 1)
            for record in payload["findings"][0]["records"]:
                self.assertEqual(text.count('"record_id":' + json.dumps(record["record_id"])), 1)

    def test_large_unicode_fields_nested_values_and_large_arrays_are_not_truncated(self):
        payload = dashboard_payload()
        raw = payload["evidence_records"][0]["raw"]
        raw["long_text"] = "保留 full original \"quoted\" detail 😃\n" * 5000
        raw["large_array"] = [{"id": number, "value": "café " * 20} for number in range(150)]
        raw["large_keys"] = {"key_" + str(number): "αβ " * 15 for number in range(150)}
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "assessment"
            index = self.write(folder, payload, max_bytes=4096, max_records=2)
            paths = list(folder.rglob("*.json"))
            self.assertGreater(len(paths), 15)
            for path in paths:
                self.assertLessEqual(path.stat().st_size, 4096, str(path.relative_to(folder)))
                with path.open(encoding="utf-8") as stream:
                    json.load(stream)
            self.assertEqual(read_dashboard_package(index), payload)

    def test_unknown_top_level_and_assessment_extensions_survive(self):
        payload = dashboard_payload()
        payload["custom_top_level"] = {"list": [None, False, 0, ""],
                                        "quoted": "A newline\nwith a quote: \""}
        payload["assessment_result"]["custom_nested"]["another"] = {"values": [1.5, -7]}
        with tempfile.TemporaryDirectory() as temporary:
            index = self.write(Path(temporary) / "assessment", payload)
            restored = read_dashboard_package(index)
        self.assertEqual(restored["custom_top_level"], payload["custom_top_level"])
        self.assertEqual(restored["assessment_result"]["custom_nested"],
                         payload["assessment_result"]["custom_nested"])

    def test_native_reserved_marker_is_preserved_as_native_content(self):
        payload = dashboard_payload()
        marker = {"$json_package_node": {"kind": "array", "parts": [
            {"path": "native-data-is-not-a-file.json", "sha256": "source-value"}]}}
        payload["evidence_records"][0]["raw"]["native_marker"] = marker
        payload["custom_top_level"]["$json_package_node"] = "ordinary native string"
        with tempfile.TemporaryDirectory() as temporary:
            index = self.write(Path(temporary) / "assessment", payload, max_bytes=4096)
            self.assertEqual(read_dashboard_package(index), payload)

    def test_identical_payload_and_limits_produce_identical_relative_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, second = root / "first", root / "second"
            self.write(first, max_bytes=4096, max_records=2)
            self.write(second, max_bytes=4096, max_records=2)
            first_files = {str(path.relative_to(first)): path.read_bytes()
                           for path in first.rglob("*.json")}
            second_files = {str(path.relative_to(second)): path.read_bytes()
                            for path in second.rglob("*.json")}
            self.assertEqual(first_files, second_files)

    def test_empty_findings_and_sources_remain_empty(self):
        payload = dashboard_payload()
        for field in ("findings", "recommendations", "sources", "evidence_records"):
            payload[field] = []
        payload["assessment_result"]["recommendations"] = []
        payload["assessment_result"]["actions"] = []
        with tempfile.TemporaryDirectory() as temporary:
            index = self.write(Path(temporary) / "assessment", payload)
            self.assertEqual(read_dashboard_package(index), payload)

    def test_untrusted_finding_identifiers_cannot_escape_package_directory(self):
        payload = dashboard_payload()
        payload["findings"][0]["finding_id"] = "../../outside"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "assessment"
            index = self.write(folder, payload)
            self.assertFalse((root / "outside.json").exists())
            self.assertEqual(read_dashboard_package(index), payload)
            for path in folder.rglob("*.json"):
                self.assertTrue(path.resolve().is_relative_to(folder.resolve()))

    def test_long_identifiers_have_distinct_short_paths_and_round_trip_unchanged(self):
        payload = dashboard_payload()
        common = 'very_long_source_identifier_' * 12
        for number, row in enumerate(payload['evidence_records']):
            row['dataset'] = common + ('first' if number % 2 else 'second')
        first = payload['findings'][0]
        first['finding_id'] = common + 'first'
        second = copy.deepcopy(first)
        second.update(finding_id=common + 'second', finding_uid='FND-second')
        payload['findings'].append(second)
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / 'JSON'
            index = self.write(folder, payload, max_bytes=4096, max_records=2)
            self.assertEqual(read_dashboard_package(index), payload)
            finding_paths = list((folder / 'findings').glob('*.json'))
            self.assertEqual(len(finding_paths), 3)
            projected_root = Path(__file__).resolve().parents[1] / 'Reports' / ('Customer' + 'x' * 32) / '2026-10-06' / 'Builds' / '1' / 'JSON'
            for path in folder.rglob('*.json'):
                self.assertNotIn('_', path.name)
                self.assertLessEqual(len(str(projected_root / path.relative_to(folder))), 255)
            record_paths = list((folder / 'evidence' / 'records').glob('*.json'))
            self.assertGreater(len({path.name for path in record_paths}), 1)

    def test_reader_rejects_a_reference_outside_the_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self.write(root / "assessment")
            manifest = json.loads(index.read_text(encoding="utf-8"))
            manifest["entry_points"]["assessment"] = "../outside.json"
            index.write_text(json.dumps(manifest), encoding="utf-8")
            (root / "outside.json").write_text('{"payload": {"stolen": true}}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "package folder"):
                read_dashboard_package(index)

    def test_changed_referenced_evidence_is_rejected_by_checksum(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "assessment"
            index = self.write(folder)
            page = next((folder / "evidence" / "records").glob("*.json"))
            original = page.read_text(encoding="utf-8")
            page.write_text(original + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "checksum"):
                read_dashboard_package(index)

    def test_writing_to_an_existing_package_preserves_original_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "assessment"
            self.write(folder)
            original = {str(path.relative_to(folder)): path.read_bytes()
                        for path in folder.rglob("*.json")}
            with self.assertRaisesRegex(ValueError, "empty"):
                self.write(folder)
            self.assertEqual(original, {str(path.relative_to(folder)): path.read_bytes()
                                        for path in folder.rglob("*.json")})


if __name__ == "__main__":
    unittest.main()

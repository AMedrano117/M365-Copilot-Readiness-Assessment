"""Upload archives preserve the bounded assessment, not adjacent tenant files."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from Core.dashboard_package import (
    read_dashboard_package,
    write_dashboard_archive,
    write_dashboard_package,
)


def upload_payload():
    raw = {"id": "fictional-user", "displayName": "Jalapeño 雲",
           "isMfaRegistered": False,
           "path": "../../native-location-is-data.json",
           "url": "https://fictional.example.invalid/item",
           "nested": {"long": "Keep ünicode. " * 900, "zero": 0,
                      "unknown": None, "methods": ["Authenticator", "phone"]}}
    evidence = {"record_id": "EVD-fictional", "dataset_id": "SRC-fictional",
                "dataset": "auth_methods", "source_record_index": 0,
                "source_record_id": "fictional-user", "raw": raw}
    detail = {"record_id": "DET-fictional", "fields": {"displayName": raw["displayName"],
              "lastSignIn": None}, "evidence_record_ids": [evidence["record_id"]]}
    finding = {"finding_id": "ENT-005", "finding_uid": "FND-fictional",
               "title": "Close MFA registration gaps", "domain_id": "identity",
               "disposition": "Action", "priority": "High", "record_count": 1,
               "record_status": "records_available", "records": [detail],
               "evidence_record_ids": [evidence["record_id"]]}
    return {"format": "m365-readiness-assessment", "dashboard_schema_version": "1.0.0",
            "evidence_schema_version": "1.1.0", "methodology_version": "4.0.0",
            "tenant_id": "invented-tenant", "tenant_name": "Fictional tenant",
            "evaluation_date": "2026-10-03", "decision": "Not ready for pilot",
            "counts": {"actions": 1}, "findings": [finding],
            "recommendations": [{"RecommendationId": "ENT-005", "records": [detail]}],
            "sources": [{"dataset_id": "SRC-fictional", "dataset": "auth_methods",
                         "dataset_index": 0, "record_count": 1,
                         "source": {"complete": True, "collected_at": "2026-10-02T12:00:00Z"}}],
            "evidence_records": [evidence],
            "assessment_result": {"decision": "Not ready for pilot", "extension": raw}}


class DashboardArchiveTests(unittest.TestCase):
    def make_package(self, root, payload=None):
        return Path(write_dashboard_package(root / "json" / "fictional_assessment",
                                            payload or upload_payload(),
                                            max_bytes=4096, max_records=2))

    def test_archive_at_root_extracts_to_the_exact_complete_assessment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = upload_payload()
            original = copy.deepcopy(payload)
            index = self.make_package(root, payload)
            archive = Path(write_dashboard_archive(index, root / "upload.zip"))
            source_files = {path.relative_to(index.parent).as_posix(): path.read_bytes()
                            for path in index.parent.rglob("*.json")}
            with zipfile.ZipFile(archive) as package:
                self.assertEqual(set(package.namelist()), {*source_files, "Read First.txt"})
                for name, body in source_files.items():
                    self.assertEqual(package.read(name), body, name)
                    self.assertLessEqual(len(package.read(name)), 4096, name)
                self.assertIn("index.json", package.namelist())
                self.assertIn("findings/index.json", package.namelist())
                self.assertIn("evidence/index.json", package.namelist())
                package.extractall(root / "extracted")
            self.assertEqual(read_dashboard_package(root / "extracted" / "index.json"), payload)
            self.assertEqual(payload, original)

    def test_default_archive_is_a_single_file_beside_report_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self.make_package(root)
            archive = Path(write_dashboard_archive(index))
            self.assertEqual(archive, root / "fictional_assessment_json.zip")
            self.assertTrue(zipfile.is_zipfile(archive))

    def test_generic_json_folder_uses_short_archive_name_beside_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = upload_payload()
            index = write_dashboard_package(root / 'JSON', payload, max_bytes=4096, max_records=2)
            archive = Path(write_dashboard_archive(index))
            self.assertEqual(archive, root / 'Dashboard JSON.zip')
            with zipfile.ZipFile(archive) as package:
                package.extractall(root / 'extracted')
            self.assertEqual(read_dashboard_package(root / 'extracted' / 'index.json'), payload)

    def test_non_zip_output_is_rejected_instead_of_mislabeling_an_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self.make_package(root)
            target = root / "upload.json"
            with self.assertRaisesRegex(ValueError, "zip"):
                write_dashboard_archive(index, target)
            self.assertFalse(target.exists())

    def test_guide_explains_selective_reading_and_does_not_include_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = write_dashboard_archive(self.make_package(root), root / "upload.zip")
            with zipfile.ZipFile(archive) as package:
                guide = package.read("Read First.txt").decode("utf-8")
            self.assertIn("index.json", guide)
            self.assertIn("summary.json", guide)
            self.assertIn("finding", guide.lower())
            self.assertIn("only", guide.lower())
            self.assertNotIn("Jalapeño", guide)

    def test_unrelated_files_are_excluded_and_all_original_files_stay_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self.make_package(root)
            extra = index.parent / "secrets.json"
            extra.write_text('{"clientSecret":"do-not-package"}', encoding="utf-8")
            (index.parent / "notes.txt").write_text("private unrelated notes", encoding="utf-8")
            (index.parent / "notes").mkdir()
            (index.parent / "notes" / "index.json").write_text("{}", encoding="utf-8")
            monolithic = root / "original.json"
            monolithic.write_text(json.dumps(upload_payload()), encoding="utf-8")
            before = {str(path.relative_to(root)): path.read_bytes()
                      for path in root.rglob("*") if path.is_file()}
            archive = Path(write_dashboard_archive(index, root / "upload.zip"))
            with zipfile.ZipFile(archive) as package:
                for unrelated in ("secrets.json", "notes.txt", "notes/index.json", "original.json"):
                    self.assertNotIn(unrelated, package.namelist())
                self.assertNotIn(b"do-not-package", b"".join(package.read(name) for name in package.namelist()))
            for relative, body in before.items():
                self.assertEqual((root / relative).read_bytes(), body, relative)
            self.assertEqual(read_dashboard_package(index), upload_payload())

    def test_existing_archive_is_preserved_instead_of_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self.make_package(root)
            archive = root / "upload.zip"
            archive.write_bytes(b"previous assessment archive")
            with self.assertRaises(FileExistsError):
                write_dashboard_archive(index, archive)
            self.assertEqual(archive.read_bytes(), b"previous assessment archive")

    def test_reference_outside_package_is_rejected_without_creating_an_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self.make_package(root)
            manifest = json.loads(index.read_text(encoding="utf-8"))
            manifest["entry_points"]["assessment"] = "../../outside.json"
            index.write_text(json.dumps(manifest), encoding="utf-8")
            (root / "outside.json").write_text('{"payload":{"private":true}}', encoding="utf-8")
            target = root / "upload.zip"
            with self.assertRaises(ValueError):
                write_dashboard_archive(index, target)
            self.assertFalse(target.exists())

    def test_changed_referenced_evidence_is_rejected_before_archive_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self.make_package(root)
            page = next((index.parent / "evidence" / "records").glob("*.json"))
            page.write_text(page.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            target = root / "upload.zip"
            with self.assertRaisesRegex(ValueError, "checksum"):
                write_dashboard_archive(index, target)
            self.assertFalse(target.exists())

    def test_missing_referenced_page_does_not_produce_an_incomplete_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self.make_package(root)
            next((index.parent / "evidence" / "records").glob("*.json")).unlink()
            target = root / "upload.zip"
            with self.assertRaises((ValueError, FileNotFoundError)):
                write_dashboard_archive(index, target)
            self.assertFalse(target.exists())

    def test_missing_navigation_index_is_rejected_even_when_reconstruction_still_works(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self.make_package(root)
            (index.parent / "findings" / "index.json").unlink()
            # The shared assessment tree can reconstruct without reading this
            # navigation entry. An agent-facing archive must still include it.
            self.assertEqual(read_dashboard_package(index), upload_payload())
            target = root / "upload.zip"
            with self.assertRaises((ValueError, FileNotFoundError)):
                write_dashboard_archive(index, target)
            self.assertFalse(target.exists())

    def test_external_linked_file_is_rejected_without_copying_its_contents(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = self.make_package(root)
            page = next((index.parent / "evidence" / "records").glob("*.json"))
            outside = root / "outside.json"
            outside.write_bytes(page.read_bytes())
            page.unlink()
            try:
                page.symlink_to(outside)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"Symlinks unavailable in this environment: {error}")
            target = root / "upload.zip"
            with self.assertRaises(ValueError):
                write_dashboard_archive(index, target)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()

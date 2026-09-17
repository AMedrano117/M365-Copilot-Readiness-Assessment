"""Exercise destructive local cleanup only in isolated synthetic directories."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


@unittest.skipUnless(POWERSHELL, "PowerShell is required for local cleanup tests")
class LocalCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "checkout"
        self.root.mkdir()
        shutil.copyfile(ROOT / "cleanup-local-assessment.ps1", self.root / "cleanup-local-assessment.ps1")
        (self.root / "tools").mkdir()
        shutil.copyfile(
            ROOT / "tools" / "cleanup-reparse-points.ps1",
            self.root / "tools" / "cleanup-reparse-points.ps1",
        )
        self.selected = self.root / "output" / "customer" / "assessment"
        self.selected.mkdir(parents=True)
        (self.selected / "collection.json").write_text('{"synthetic":true}', encoding="utf-8")
        self.keep = self.root / "output" / "another-customer.json"
        self.keep.write_text("retain", encoding="utf-8")
        (self.root / ".env").write_text("CLIENT_SECRET=synthetic-never-log", encoding="utf-8")

    def run_cleanup(self, paths=None, what_if=False, confirm=False, cwd=None):
        (self.root / "selection.json").write_text(json.dumps(paths), encoding="utf-8")
        harness = """
$ErrorActionPreference = 'Stop'
function Connect-MgGraph { throw 'Local cleanup must never authenticate.' }
$paths = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'selection.json') -Raw | ConvertFrom-Json
$options = @{}
if ($null -ne $paths) { $options.Path = @($paths) }
"""
        if confirm is False:
            harness += "$options.Confirm = $false\n"
        if what_if:
            harness += "$options.WhatIf = $true\n"
        harness += "& (Join-Path $PSScriptRoot 'cleanup-local-assessment.ps1') @options\n"
        (self.root / "harness.ps1").write_text(harness, encoding="utf-8")
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(self.root / "harness.ps1")],
            cwd=cwd or self.root, capture_output=True, text=True, timeout=30,
        )
        self.assertNotIn("synthetic-never-log", result.stdout + result.stderr)
        return result

    def test_whatif_keeps_selected_evidence(self):
        result = self.run_cleanup([str(self.selected)], what_if=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("No files were removed", result.stdout)
        self.assertTrue(self.selected.exists())

    def test_confirmed_cleanup_removes_only_selected_literal_path(self):
        literal = self.selected / "report[1].json"
        literal.write_text("fixture", encoding="utf-8")
        result = self.run_cleanup([str(literal)])
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(literal.exists())
        self.assertTrue((self.selected / "collection.json").exists())
        self.assertTrue(self.keep.exists())
        self.assertTrue((self.root / ".env").exists())

    def test_confirmed_cleanup_removes_explicit_subdirectory_and_repeat_is_safe(self):
        for _ in range(2):
            result = self.run_cleanup([str(self.selected)])
            self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(self.selected.exists())
        self.assertTrue(self.keep.exists())

    def test_whatif_blocks_recursive_removal(self):
        result = self.run_cleanup([str(self.selected)], what_if=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(self.selected.exists())

    def test_multiple_selected_locations_and_hidden_files(self):
        report = self.root / "Reports" / "customer.html"
        report.parent.mkdir()
        report.write_text("synthetic report", encoding="utf-8")
        cache = self.root / ".cache" / "purview" / "customer.json"
        cache.parent.mkdir(parents=True)
        cache.write_text("{}", encoding="utf-8")
        result = self.run_cleanup([str(report), str(cache)])
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(report.exists())
        self.assertFalse(cache.exists())
        self.assertTrue(self.selected.exists())

    def test_all_inputs_validated_before_any_deletion(self):
        for unsafe in (self.root / "output", self.root / "Reports", self.root / ".cache", self.root / ".env", self.root.parent, self.root / "output" / ".." / ".env"):
            with self.subTest(unsafe=unsafe):
                result = self.run_cleanup([str(self.selected), str(unsafe)])
                self.assertNotEqual(0, result.returncode)
                self.assertTrue(self.selected.exists())
                self.assertTrue((self.root / ".env").exists())

    def test_overlapping_paths_are_rejected_before_deletion(self):
        result = self.run_cleanup([str(self.selected), str(self.selected / "collection.json")])
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Overlapping selections", result.stderr)
        self.assertTrue(self.selected.exists())

    def test_relative_path_and_sibling_prefix_boundaries(self):
        result = self.run_cleanup(["output/customer/assessment"], what_if=True)
        self.assertEqual(0, result.returncode, result.stderr)
        outside = self.root / "output-extra"
        outside.mkdir()
        result = self.run_cleanup([str(outside)])
        self.assertNotEqual(0, result.returncode)
        self.assertTrue(outside.exists())

    def make_default_artifacts(self):
        names = (
            "output/collections/tenant-a.json",
            "output/collections/tenant-b_package/collection.json",
            "output/assessments/offline-assessment/rebuild.json",
            "output/portal-reviews/pdf-review/portal-review.json",
            "Reports/tenant-a.html",
            ".cache/purview/tenant-a.json",
            ".cache/sharepoint_dag/tenant-a/export.csv",
        )
        artifacts = []
        for name in names:
            artifact = self.root / name
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("synthetic evidence", encoding="utf-8")
            artifacts.append(artifact)
        return artifacts

    def test_no_arguments_discovers_standard_outputs_from_any_working_directory(self):
        artifacts = self.make_default_artifacts()
        unrelated_cache = self.root / ".cache" / "development.log"
        unrelated_cache.write_text("keep unrelated cache", encoding="utf-8")
        result = self.run_cleanup(cwd=self.root.parent)
        self.assertEqual(0, result.returncode, result.stderr)
        for artifact in artifacts:
            self.assertFalse(artifact.exists(), artifact)
        for location in ("output/collections", "output/assessments", "output/portal-reviews", "Reports", ".cache/purview", ".cache/sharepoint_dag"):
            self.assertTrue((self.root / location).is_dir(), location)
        self.assertTrue(self.keep.exists())
        self.assertTrue(self.selected.exists())
        self.assertTrue(unrelated_cache.exists())
        self.assertTrue((self.root / ".env").exists())

    def test_default_whatif_lists_all_and_simulates_one_batch_without_removal(self):
        artifacts = self.make_default_artifacts()
        result = self.run_cleanup(what_if=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(7, result.stdout.count("[SELECTED]"))
        self.assertEqual(1, result.stdout.count("What if:"))
        for artifact in artifacts:
            self.assertTrue(artifact.exists(), artifact)

    def test_normal_invocation_requires_confirmation_after_listing_every_item(self):
        artifacts = self.make_default_artifacts()
        # No -Confirm override: the noninteractive host cannot approve deletion.
        result = self.run_cleanup(confirm=None)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(7, result.stdout.count("[SELECTED]"))
        self.assertNotIn("[REMOVED]", result.stdout)
        for artifact in artifacts:
            self.assertTrue(artifact.exists(), artifact)

    def test_explicit_path_overrides_default_discovery(self):
        artifacts = self.make_default_artifacts()
        result = self.run_cleanup([str(self.selected)])
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(self.selected.exists())
        for artifact in artifacts:
            self.assertTrue(artifact.exists(), artifact)

    def test_missing_and_empty_standard_folders_need_no_confirmation(self):
        (self.root / "Reports").mkdir()
        (self.root / ".cache" / "purview").mkdir(parents=True)
        result = self.run_cleanup(confirm=None)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("No local assessment artifacts were found", result.stdout)
        self.assertTrue(self.selected.exists())
        self.assertTrue(self.keep.exists())

    def run_reparse_probe(self, body):
        harness = """
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'tools/cleanup-reparse-points.ps1')
$item = [pscustomobject]@{ FullName='synthetic-placeholder'; Attributes=[IO.FileAttributes]::ReparsePoint }
""" + body
        (self.root / "reparse-probe.ps1").write_text(harness, encoding="utf-8")
        return subprocess.run(
            [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.root / "reparse-probe.ps1")],
            cwd=self.root, capture_output=True, text=True, timeout=30,
        )

    def test_cloud_placeholders_are_allowed_but_other_reparse_types_are_not(self):
        result = self.run_reparse_probe("""
function Get-CleanupReparseTag { return $script:testTag }
# All 16 tags assigned to the Windows Cloud Files filter are supported.
foreach ($variant in 0..15) {
    $script:testTag = [Convert]::ToUInt32(('9000{0:X}01A' -f $variant), 16)
    Assert-SafeCleanupReparsePoint -Item $item
}
# Symlink, junction, unknown, adjacent, unused OneDrive, and a cloud-looking
# name surrogate must all fail closed; none is a supported placeholder.
foreach ($hex in @('A000000C', 'A0000003', '00000000', '9001001A', '9000001B', '80000021', 'B000001A')) {
    $script:testTag = [Convert]::ToUInt32($hex, 16)
    $refused = $false
    try { Assert-SafeCleanupReparsePoint -Item $item } catch { $refused = $true }
    if (-not $refused) { throw "Unsafe reparse type was accepted: $hex" }
}
Write-Output 'Verified cloud allowlist and rejected unsupported types.'
""")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Verified cloud allowlist", result.stdout)

    def test_reparse_tag_query_failure_stops_cleanup(self):
        result = self.run_reparse_probe("""
function Get-CleanupReparseTag { throw 'Synthetic metadata access denied' }
Assert-SafeCleanupReparsePoint -Item $item
""")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Cannot safely verify reparse point", result.stderr)
        self.assertTrue(self.selected.exists())

    def test_junction_is_rejected_without_following_or_deleting_target(self):
        target = self.root.parent / "outside-evidence"
        target.mkdir()
        retained = target / "keep.json"
        retained.write_text("retain", encoding="utf-8")
        junction = self.selected / "linked-data"
        self.make_junction(junction, target)
        result = self.run_cleanup([str(self.selected)])
        self.assertNotEqual(0, result.returncode)
        self.assertIn("reparse point", result.stderr)
        self.assertTrue(retained.exists())
        self.assertTrue(self.selected.exists())

    def test_default_discovery_root_junction_stops_entire_cleanup(self):
        retained = self.root.parent / "outside-evidence"
        retained.mkdir()
        (retained / "report.html").write_text("retain", encoding="utf-8")
        legitimate = self.root / "output" / "collections" / "tenant.json"
        legitimate.parent.mkdir()
        legitimate.write_text("retain on any validation failure", encoding="utf-8")
        self.make_junction(self.root / "Reports", retained)
        result = self.run_cleanup()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("reparse point", result.stderr)
        self.assertTrue((retained / "report.html").exists())
        self.assertTrue(legitimate.exists())

    def make_junction(self, junction, target):
        # Creating a directory junction needs no elevated Windows privilege.
        harness = """
$ErrorActionPreference = 'Stop'
$paths = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'junction.json') -Raw | ConvertFrom-Json
New-Item -ItemType Junction -Path $paths.link -Target $paths.target | Out-Null
"""
        (self.root / "junction.json").write_text(json.dumps({"link": str(junction), "target": str(target)}), encoding="utf-8")
        (self.root / "junction.ps1").write_text(harness, encoding="utf-8")
        created = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.root / "junction.ps1")], capture_output=True, text=True, timeout=30)
        if created.returncode:
            self.skipTest("Directory junctions are unavailable in this PowerShell environment")


if __name__ == "__main__":
    unittest.main()

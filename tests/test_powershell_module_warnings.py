"""Offline module validation: packaging notices must not hide compatibility problems."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from Core.orchestrator_powershell import powershell_environment


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which('powershell') or shutil.which('pwsh')
HARNESS = r"""
param([string]$Scenario)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Check-PSModules.ps1')
function Find-ModuleManifest { param($ModuleName) return (Join-Path $PSScriptRoot 'OfflineModule.psd1') }
if ($Scenario -eq 'other_warning') {
    function Test-ModuleManifest {
        [CmdletBinding()]
        param($Path)
        Write-Warning 'Unexpected compatibility warning'
        return [pscustomobject]@{Version=[version]'1.0.0'}
    }
}
$minimum = if ($Scenario -eq 'old_version') { [version]'2.0.0' } else { [version]'1.0.0' }
$installed = Test-ModuleInstalled -ModuleName 'OfflineModule' -MinimumVersion $minimum
Write-Host ('RESULT: ' + (@{installed=$installed} | ConvertTo-Json -Compress))
"""


@unittest.skipUnless(POWERSHELL, 'PowerShell is required for offline module validation')
class ModuleWarningTests(unittest.TestCase):
    def run_check(self, scenario):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copyfile(ROOT / 'Check-PSModules.ps1', root / 'Check-PSModules.ps1')
            (root / 'harness.ps1').write_text(HARNESS, encoding='utf-8')
            (root / 'OfflineModule.psm1').write_text('', encoding='utf-8')
            version = 'invalid-version' if scenario == 'invalid_manifest' else '1.0.0'
            (root / 'OfflineModule.psd1').write_text(
                "@{ ModuleToProcess = 'OfflineModule.psm1'; ModuleVersion = '" + version + "'; "
                "GUID = '98bd141c-e3ef-4c4d-a1b9-094478361199' }", encoding='utf-8')
            result = subprocess.run([POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                     '-File', str(root / 'harness.ps1'), '-Scenario', scenario],
                                    capture_output=True, text=True, timeout=30, env=powershell_environment(POWERSHELL))
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            payload = next(line.removeprefix('RESULT: ') for line in result.stdout.splitlines()
                           if line.startswith('RESULT: '))
            return json.loads(payload)['installed'], result.stdout + result.stderr

    def test_legacy_manifest_notice_is_explained_without_marking_module_missing(self):
        installed, output = self.run_check('legacy_manifest')
        self.assertTrue(installed)
        self.assertIn('[INFO] OfflineModule uses the older ModuleToProcess manifest field', output)
        self.assertNotIn('WARNING:', output)

    def test_legacy_manifest_still_enforces_the_minimum_version(self):
        installed, output = self.run_check('old_version')
        self.assertFalse(installed)
        self.assertIn('minimum-version checks still apply', output)

    def test_invalid_manifest_still_fails_validation(self):
        installed, _output = self.run_check('invalid_manifest')
        self.assertFalse(installed)

    def test_other_warnings_remain_visible(self):
        installed, output = self.run_check('other_warning')
        self.assertTrue(installed)
        self.assertIn('WARNING: Unexpected compatibility warning', output)


@unittest.skipUnless(POWERSHELL, 'PowerShell is required for offline manifest repair')
class ManifestRepairTests(unittest.TestCase):
    def run_repair(self, path, *arguments):
        return subprocess.run([POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                               str(ROOT / 'tools/repair-sharepoint-module-manifest.ps1'),
                               '-ManifestPath', str(path), *arguments],
                              capture_output=True, text=True, timeout=30, env=powershell_environment(POWERSHELL))

    def fixture(self, root, *, encoding='utf-8', extra='', version='1.0.0'):
        path = root / 'OfflineModule.psd1'
        text = ("@{\r\nModuleToProcess = 'OfflineModule.psm1'\r\nModuleVersion = '" + version
                + "'\r\nGUID = '98bd141c-e3ef-4c4d-a1b9-094478361199'\r\n" + extra + '}\r\n')
        path.write_bytes(text.encode(encoding))
        (root / 'OfflineModule.psm1').write_text('', encoding='utf-8')
        return path, text

    def test_repair_preserves_encoding_line_endings_and_exact_backup_and_is_repeatable(self):
        for encoding in ('utf-8', 'utf-8-sig', 'utf-16'):
            with self.subTest(encoding=encoding), tempfile.TemporaryDirectory() as folder:
                path, text = self.fixture(Path(folder), encoding=encoding)
                original = path.read_bytes()
                result = self.run_repair(path)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertNotIn('WARNING:', result.stdout)
                self.assertEqual(text.replace('ModuleToProcess', 'RootModule').encode(encoding), path.read_bytes())
                self.assertEqual(original, Path(str(path) + '.pre-rootmodule.bak').read_bytes())
                again = self.run_repair(path)
                self.assertEqual(0, again.returncode, again.stdout + again.stderr)
                self.assertIn('no repair is needed', again.stdout)

    def test_whatif_makes_no_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path, _text = self.fixture(root)
            original = path.read_bytes()
            result = self.run_repair(path, '-WhatIf')
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertEqual(original, path.read_bytes())
            self.assertFalse(Path(str(path) + '.pre-rootmodule.bak').exists())

    def test_signed_conflicting_and_invalid_manifests_are_not_modified(self):
        for scenario in ('signed', 'conflicting', 'invalid'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                extra = "RootModule = 'different.psm1'\r\n" if scenario == 'conflicting' else ''
                path, _text = self.fixture(root, extra=extra, version='invalid' if scenario == 'invalid' else '1.0.0')
                if scenario == 'signed':
                    with path.open('a', encoding='utf-8') as handle:
                        handle.write('# SIG # Begin signature block\n')
                original = path.read_bytes()
                result = self.run_repair(path)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual(original, path.read_bytes())
                self.assertFalse(Path(str(path) + '.pre-rootmodule.bak').exists())
                self.assertFalse(list(root.glob('.rootmodule-*.psd1')))


if __name__ == '__main__':
    unittest.main()

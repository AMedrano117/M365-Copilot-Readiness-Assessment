"""Setup summaries stay brief while missing modules and warnings stay visible."""
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
if ($Scenario -eq 'missing') {
    function Test-ModuleInstalled { param($ModuleName,$MinimumVersion,[switch]$Quiet) return $false }
}
if ($Scenario -eq 'warning') {
    function Test-ModuleManifest {
        [CmdletBinding()]
        param($Path)
        Write-Warning 'Unexpected compatibility warning'
        return [pscustomobject]@{Version=[version]'1.0.0'}
    }
}
function Install-Module { throw 'Unexpected installation in offline test' }
$installed = Test-RequiredModules -ScriptType 'Setup' -Summary:($Scenario -ne 'full')
Write-Host ('RESULT: ' + (@{installed=$installed} | ConvertTo-Json -Compress))
"""


@unittest.skipUnless(POWERSHELL, 'PowerShell is required for offline setup checks')
class SetupModuleSummaryTests(unittest.TestCase):
    def run_check(self, scenario):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copyfile(ROOT/'Check-PSModules.ps1',root/'Check-PSModules.ps1')
            (root/'harness.ps1').write_text(HARNESS,encoding='utf-8')
            (root/'OfflineModule.psm1').write_text('',encoding='utf-8')
            (root/'OfflineModule.psd1').write_text(
                "@{ ModuleToProcess = 'OfflineModule.psm1'; ModuleVersion = '1.0.0'; "
                "GUID = '98bd141c-e3ef-4c4d-a1b9-094478361199' }",encoding='utf-8')
            result=subprocess.run([POWERSHELL,'-NoProfile','-ExecutionPolicy','Bypass','-File',
                str(root/'harness.ps1'),'-Scenario',scenario],capture_output=True,text=True,timeout=30,
                env=powershell_environment(POWERSHELL))
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            value=next(line.removeprefix('RESULT: ') for line in result.stdout.splitlines() if line.startswith('RESULT: '))
            return json.loads(value)['installed'],result.stdout+result.stderr

    def test_summary_hides_success_lists_and_known_packaging_notice(self):
        installed,output=self.run_check('summary')
        self.assertTrue(installed)
        self.assertNotIn('[OK]',output)
        self.assertNotIn('Checking PowerShell module dependencies',output)
        self.assertNotIn('ModuleToProcess',output)

    def test_missing_modules_and_install_commands_remain_visible(self):
        installed,output=self.run_check('missing')
        self.assertFalse(installed)
        self.assertIn('[MISSING]',output)
        self.assertIn('Install-Module Microsoft.Graph.Authentication',output)
        self.assertIn('ERROR: Missing required PowerShell modules',output)

    def test_unexpected_warnings_remain_visible_in_summary(self):
        installed,output=self.run_check('warning')
        self.assertTrue(installed)
        self.assertIn('WARNING: Unexpected compatibility warning',output)

    def test_full_module_output_is_still_available(self):
        installed,output=self.run_check('full')
        self.assertTrue(installed)
        self.assertIn('[OK]',output)
        self.assertIn('Checking PowerShell module dependencies',output)

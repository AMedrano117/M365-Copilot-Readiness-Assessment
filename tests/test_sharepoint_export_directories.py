"""Execute the real local export block with a stub; never load or connect to SPO."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


POWERSHELL = shutil.which('powershell') or shutil.which('pwsh')
ROOT = Path(__file__).resolve().parents[1]
FIRST = '11111111-1111-4111-8111-111111111111'
SECOND = '22222222-2222-4222-8222-222222222222'


@unittest.skipUnless(POWERSHELL, 'A local PowerShell host is required')
class SharePointExportDirectoryTests(unittest.TestCase):
    def run_export(self, directory, report_ids, failed_id=''):
        directory = Path(directory).resolve()
        source = (ROOT / 'collect_sharepoint_governance.ps1').read_text(encoding='utf-8-sig')
        start = source.index('if ($DownloadPath -and $dagRows.Count -gt 0) {')
        end = source.index("$payload['available']", start)
        block = source[start:end]
        self.assertNotIn('Connect-SPOService', block)
        script = Path(directory) / 'test-export.ps1'
        script.write_text(r'''
param([string]$DownloadPath, [string]$InputPath, [string]$FailedId)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$dagRows = @(Get-Content -LiteralPath $InputPath -Raw | ConvertFrom-Json)
$payload = @{ exported_files = @(); collection_status = @{} }
function Export-SPODataAccessGovernanceInsight {
    param([string]$ReportID, [string]$DownloadPath)
    if ($ReportID -eq $FailedId) { throw 'Synthetic download failure' }
    $target = Join-Path $DownloadPath 'same-microsoft-filename.csv'
    if (Test-Path -LiteralPath $target) { throw 'A file with the same name already exists in this directory.' }
    [IO.File]::WriteAllText($target, ('ReportId' + [Environment]::NewLine + $ReportID))
}
''' + block + '\n$payload | ConvertTo-Json -Depth 10 -Compress\n', encoding='utf-8')
        inputs = Path(directory) / 'reports.json'
        inputs.write_text(json.dumps([{'Status': 'Completed', 'ReportID': value} for value in report_ids]), encoding='utf-8')
        download = Path(directory) / 'downloads'
        download.mkdir()
        unrelated = download / 'existing.csv'
        unrelated.write_text('preserve this original', encoding='utf-8')
        result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                 '-File', str(script), '-DownloadPath', str(download), '-InputPath', str(inputs),
                                 '-FailedId', failed_id], capture_output=True, text=True, encoding='utf-8',
                                errors='replace', timeout=20, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(unrelated.read_text(encoding='utf-8'), 'preserve this original')
        return json.loads(result.stdout), download

    def test_distinct_reports_with_the_same_filename_are_both_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            result, download = self.run_export(directory, [FIRST, SECOND, FIRST])
            self.assertEqual(result['collection_status']['sharepoint_dag_exports']['availability_status'], 'available')
            files = result['exported_files']
            self.assertEqual(len(files), 2)
            self.assertEqual({row['report_id'] for row in files}, {FIRST, SECOND})
            self.assertEqual(len({Path(row['path']).parent for row in files}), 2)
            for row in files:
                path = Path(row['path'])
                self.assertEqual(path.name, 'same-microsoft-filename.csv')
                self.assertTrue(path.is_relative_to(download))
                self.assertIn(row['report_id'], path.read_text(encoding='utf-8'))

    def test_failed_and_invalid_report_ids_keep_successful_export_and_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            result, download = self.run_export(directory, [FIRST, SECOND, '../outside'], failed_id=SECOND)
            status = result['collection_status']['sharepoint_dag_exports']
            self.assertEqual(status['availability_status'], 'partial')
            self.assertIn(SECOND + ': Synthetic download failure', status['reason'])
            self.assertIn('../outside: Completed report ID is not a valid GUID', status['reason'])
            self.assertEqual(len(result['exported_files']), 1)
            self.assertEqual(result['exported_files'][0]['report_id'], FIRST)
            self.assertFalse((download.parent / 'outside').exists())


if __name__ == '__main__':
    unittest.main()

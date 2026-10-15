<#
.SYNOPSIS
  Repairs the legacy ModuleToProcess field in the selected SharePoint module manifest.
.DESCRIPTION
  Selects the newest installed SharePoint Online module by default. Changes only
  the legacy manifest key to RootModule, preserving encoding and line endings.
  Validates the replacement before installing it and keeps the original as a
  .pre-rootmodule.bak file. Signed manifests are left untouched.
#>
#Requires -Version 5.1
[CmdletBinding(SupportsShouldProcess=$true)]
param([string]$ManifestPath = '')

$ErrorActionPreference = 'Stop'
if (-not $ManifestPath) {
    . (Join-Path (Split-Path -Parent $PSScriptRoot) 'Check-PSModules.ps1')
    $ManifestPath = Find-ModuleManifest -ModuleName 'Microsoft.Online.SharePoint.PowerShell'
    if (-not $ManifestPath) { throw 'The SharePoint Online PowerShell module was not found. Install it before running this repair.' }
}
$ManifestPath = (Resolve-Path -LiteralPath $ManifestPath -ErrorAction Stop).Path
if ([IO.Path]::GetExtension($ManifestPath) -ne '.psd1') { throw 'ManifestPath must identify a PowerShell module manifest (.psd1).' }

$reader = [IO.StreamReader]::new($ManifestPath, [Text.UTF8Encoding]::new($false, $true), $true)
try {
    $original = $reader.ReadToEnd()
    $encoding = $reader.CurrentEncoding
} finally { $reader.Dispose() }
$data = Import-PowerShellDataFile -LiteralPath $ManifestPath
if (-not $data.ContainsKey('ModuleToProcess')) {
    Write-Host '[OK] The manifest does not use ModuleToProcess; no repair is needed.' -ForegroundColor Green
    return
}
if ($original -match '(?m)^# SIG # Begin signature block') { throw 'The manifest is signed. Use a publisher-provided update; this repair does not modify signed manifests.' }
if ($data.ContainsKey('RootModule')) { throw 'The manifest contains both RootModule and ModuleToProcess. Review the conflicting fields manually; no change was made.' }

$pattern = '(?m)^([ \t]*)ModuleToProcess([ \t]*=)'
if ([regex]::Matches($original, $pattern).Count -ne 1) { throw 'Expected exactly one standalone ModuleToProcess field; no change was made.' }
$replacement = [regex]::Replace($original, $pattern, '${1}RootModule${2}')
$backupPath = $ManifestPath + '.pre-rootmodule.bak'
if (Test-Path -LiteralPath $backupPath) { throw "The backup already exists: $backupPath. Retain or move it before repairing this manifest again." }
if (-not $PSCmdlet.ShouldProcess($ManifestPath, "Rename ModuleToProcess to RootModule; preserve the original in $backupPath")) { return }

# Keep the candidate beside the installed module so relative DLL and assembly
# paths are validated against the correct folder. File.Replace installs it
# atomically and preserves the original bytes in the backup.
$candidatePath = Join-Path (Split-Path -Parent $ManifestPath) ('.rootmodule-' + [guid]::NewGuid().ToString('N') + '.psd1')
try {
    [IO.File]::WriteAllText($candidatePath, $replacement, $encoding)
    $candidateData = Import-PowerShellDataFile -LiteralPath $candidatePath
    if ($candidateData.RootModule -ne $data.ModuleToProcess -or $candidateData.ModuleVersion -ne $data.ModuleVersion) {
        throw 'The candidate changed the module entry point or version; no repair was applied.'
    }
    $null = Test-ModuleManifest -Path $candidatePath -ErrorAction Stop
    [IO.File]::Replace($candidatePath, $ManifestPath, $backupPath)
} finally {
    if (Test-Path -LiteralPath $candidatePath) { Remove-Item -LiteralPath $candidatePath -Force }
}
Write-Host "[OK] Repaired SharePoint module manifest $ManifestPath" -ForegroundColor Green
Write-Host "[OK] Kept the same module entry point and version ($($data.ModuleVersion))." -ForegroundColor Green
Write-Host "Backup: $backupPath"
Write-Host 'Future module installs can restore the publisher manifest; rerun this repair if the warning returns.'

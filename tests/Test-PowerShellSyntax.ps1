$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path $PSScriptRoot -Parent
$scripts = @(
    'setup-service-principal.ps1',
    'collect_purview_data.ps1',
    'collect_sharepoint_governance.ps1',
    'Check-PSModules.ps1'
)

$failed = $false
foreach ($relativePath in $scripts) {
    $tokens = $null
    $parseErrors = $null
    $path = Join-Path $repositoryRoot $relativePath
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $path, [ref]$tokens, [ref]$parseErrors
    )
    if ($parseErrors.Count -gt 0) {
        $failed = $true
        foreach ($parseError in $parseErrors) {
            Write-Error "${relativePath}:$($parseError.Extent.StartLineNumber): $($parseError.Message)" -ErrorAction Continue
        }
    }
}
if ($failed) { exit 1 }
Write-Host "PowerShell syntax validated for $($scripts.Count) scripts."

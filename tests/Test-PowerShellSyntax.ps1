$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path $PSScriptRoot -Parent
$scripts = @(
    Get-ChildItem -LiteralPath $repositoryRoot -Filter '*.ps1' -File
    Get-ChildItem -LiteralPath (Join-Path $repositoryRoot 'tools') -Filter '*.ps1' -File -Recurse
    Get-ChildItem -LiteralPath (Join-Path $repositoryRoot 'tests') -Filter '*.ps1' -File -Recurse
)

$failed = $false
foreach ($script in $scripts) {
    $tokens = $null
    $parseErrors = $null
    $path = $script.FullName
    $relativePath = $path.Substring($repositoryRoot.Length + 1)
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

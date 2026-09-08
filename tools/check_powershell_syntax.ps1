param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string[]]$Path
)

$failed = $false
foreach ($candidate in $Path) {
    $resolved = Resolve-Path -LiteralPath $candidate -ErrorAction Stop
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $resolved.Path,
        [ref]$tokens,
        [ref]$errors
    )
    if ($errors.Count -gt 0) {
        $failed = $true
        Write-Host "Syntax errors in $($resolved.Path):"
        foreach ($parseError in $errors) {
            Write-Host "  Line $($parseError.Extent.StartLineNumber): $($parseError.Message)"
        }
    }
    else {
        Write-Host "OK: $($resolved.Path)"
    }
}

if ($failed) {
    exit 1
}

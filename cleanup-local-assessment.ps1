<#
.SYNOPSIS
  Finds local assessment artifacts and confirms their removal.
.DESCRIPTION
  Defaults to the contents of this checkout's output/collections,
  output/assessments, output/portal-reviews, Reports, .cache/purview, and
  .cache/sharepoint_dag folders.
  Lists all discovered assessments, then asks once before removing them. The
  standard storage folders are retained. Use -WhatIf for a preview, or -Path
  to select individual files or subdirectories instead. The output, Reports, and
  .cache roots, paths outside them, and symbolic links/junctions are refused. Verified Windows
  Cloud Files placeholders (including OneDrive) are supported; other reparse
  types are refused. No tenant connection is made. Archive required evidence and
  verify offline replay first. Deletion in synced folders also syncs to the cloud.
.EXAMPLE
  .\cleanup-local-assessment.ps1
.EXAMPLE
  .\cleanup-local-assessment.ps1 -WhatIf
.EXAMPLE
  .\cleanup-local-assessment.ps1 -Path '.\output\customer\completed-assessment'
#>
#Requires -Version 5.1
[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='High')]
param(
    [ValidateNotNullOrEmpty()][string[]]$Path
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'tools/cleanup-reparse-points.ps1')
$repositoryRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
$allowedRoots = @('output', 'Reports', '.cache' | ForEach-Object {
    [IO.Path]::GetFullPath((Join-Path $repositoryRoot $_))
})
$defaultLocations = @('output/collections', 'output/assessments', 'output/portal-reviews', 'Reports', '.cache/purview', '.cache/sharepoint_dag')

function Test-DescendantPath {
    param([string]$Candidate, [string]$Parent)
    return $Candidate.StartsWith($Parent.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
}

function Assert-CleanupAncestors {
    param([string]$AbsolutePath)
    # Check ancestors before enumerating children, including discovery roots.
    # Resolve-Path alone does not prove a junction stays inside the checkout.
    $ancestor = $AbsolutePath
    while ($ancestor -and ($ancestor -eq $repositoryRoot -or (Test-DescendantPath -Candidate $ancestor -Parent $repositoryRoot))) {
        if (Test-Path -LiteralPath $ancestor) {
            Assert-SafeCleanupReparsePoint -Item (Get-Item -LiteralPath $ancestor -Force -ErrorAction Stop)
        }
        if ($ancestor -eq $repositoryRoot) { break }
        $ancestor = [IO.Path]::GetDirectoryName($ancestor)
    }
}

function Get-DefaultArtifactPaths {
    foreach ($relativeLocation in $defaultLocations) {
        $locationPath = [IO.Path]::GetFullPath((Join-Path $repositoryRoot $relativeLocation))
        Assert-CleanupAncestors -AbsolutePath $locationPath
        if (-not (Test-Path -LiteralPath $locationPath)) { continue }
        $locationItem = Get-Item -LiteralPath $locationPath -Force -ErrorAction Stop
        if (-not $locationItem.PSIsContainer) { throw "Expected an assessment storage directory at '$locationPath'. Review this location manually." }
        foreach ($child in @(Get-ChildItem -LiteralPath $locationPath -Force -ErrorAction Stop)) {
            # Only children are selected; keep the standard storage folders.
            $child.FullName
        }
    }
}

function Get-ValidatedArtifact {
    param([string]$Candidate)
    if ([string]::IsNullOrWhiteSpace($Candidate)) { throw 'An empty artifact path is not allowed.' }
    if ($Candidate -match '^[^:]+::') { throw 'Provider-qualified paths are not supported. Use a filesystem path.' }
    if ([IO.Path]::IsPathRooted($Candidate)) {
        $absolutePath = [IO.Path]::GetFullPath($Candidate)
    } else {
        $location = Get-Location
        if ($location.Provider.Name -ne 'FileSystem') { throw 'Run local cleanup from a filesystem directory.' }
        $absolutePath = [IO.Path]::GetFullPath((Join-Path $location.ProviderPath $Candidate))
    }
    $absolutePath = $absolutePath.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    # Alternate data streams are not ordinary evidence files.
    if ($absolutePath.Substring([IO.Path]::GetPathRoot($absolutePath).Length).Contains(':')) {
        throw 'Alternate data stream paths are not supported.'
    }
    $allowed = @($allowedRoots | Where-Object { Test-DescendantPath -Candidate $absolutePath -Parent $_ })
    if ($allowed.Count -ne 1) {
        throw "Refusing '$absolutePath'. Select a specific file or subdirectory inside this checkout's output, Reports, or .cache; their roots cannot be removed."
    }

    Assert-CleanupAncestors -AbsolutePath $absolutePath
    if (-not (Test-Path -LiteralPath $absolutePath)) {
        return [pscustomobject]@{ Path=$absolutePath; Exists=$false; Files=0; Bytes=0 }
    }

    $pending = New-Object 'System.Collections.Generic.Stack[string]'
    $pending.Push($absolutePath)
    $fileCount = 0
    [long]$byteCount = 0
    while ($pending.Count -gt 0) {
        $item = Get-Item -LiteralPath $pending.Pop() -Force -ErrorAction Stop
        Assert-SafeCleanupReparsePoint -Item $item
        if ($item.PSIsContainer) {
            foreach ($child in @(Get-ChildItem -LiteralPath $item.FullName -Force -ErrorAction Stop)) {
                Assert-SafeCleanupReparsePoint -Item $child
                $pending.Push($child.FullName)
            }
        } else {
            $fileCount++
            $byteCount += $item.Length
        }
    }
    return [pscustomobject]@{ Path=$absolutePath; Exists=$true; Files=$fileCount; Bytes=$byteCount }
}

Write-Host 'Local assessment cleanup.'
$selectedPaths = @($Path)
if (-not $PSBoundParameters.ContainsKey('Path')) {
    Write-Host 'Discovering all assessments in the standard storage locations for this checkout:'
    foreach ($relativeLocation in $defaultLocations) {
        Write-Host "  $(Join-Path $repositoryRoot $relativeLocation)"
    }
    $selectedPaths = @(Get-DefaultArtifactPaths)
}

# Validate the entire selection before any removal, including overlapping paths.
$artifacts = @($selectedPaths | ForEach-Object { Get-ValidatedArtifact -Candidate $_ } | Sort-Object -Property Path -Unique)
foreach ($artifact in $artifacts) {
    foreach ($other in $artifacts) {
        if ($artifact.Path -ne $other.Path -and (Test-DescendantPath -Candidate $artifact.Path -Parent $other.Path)) {
            throw "Overlapping selections: '$($artifact.Path)' is inside '$($other.Path)'. Select the parent or its children, not both."
        }
    }
}

foreach ($artifact in $artifacts) {
    if (-not $artifact.Exists) {
        Write-Host "[ABSENT] $($artifact.Path)"
        continue
    }
    Write-Host "[SELECTED] $($artifact.Path) ($($artifact.Files) files; $($artifact.Bytes) bytes)"
}
$selected = @($artifacts | Where-Object { $_.Exists })
if ($selected.Count -eq 0) {
    Write-Host 'No local assessment artifacts were found in the selected locations. Nothing was removed.'
    return
}
Write-Host 'Retain any required evidence before confirming. Use -WhatIf to preview or -Path to narrow the selection.'
if (-not $PSCmdlet.ShouldProcess("$($selected.Count) listed item(s) in '$repositoryRoot'", 'Permanently remove the listed local assessment artifacts')) {
    Write-Host 'No files were removed.'
    return
}

# Revalidate the whole batch after confirmation, before deleting its first item.
$validatedArtifacts = @($selected | ForEach-Object { Get-ValidatedArtifact -Candidate $_.Path })
foreach ($artifact in $validatedArtifacts) {
    # Recheck the exact absolute target and tree immediately before removal.
    $validated = Get-ValidatedArtifact -Candidate $artifact.Path
    if ($validated.Exists) {
        Remove-Item -LiteralPath $validated.Path -Recurse -Force -Confirm:$false -ErrorAction Stop
        Write-Host "[REMOVED] $($validated.Path)"
    }
}

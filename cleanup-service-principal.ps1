<#
.SYNOPSIS
  Previews or removes the dedicated assessment application's tenant access.
.DESCRIPTION
  Preview is the default. -Apply requires confirmation before any removal;
  -WhatIf never invokes removal cmdlets, including workload cmdlets whose own
  WhatIf behavior is unsupported. TenantId and ClientId must match the selected
  environment file. No customer evidence, certificates, shared groups, or role
  definitions are deleted. See docs/CLEANUP.md before applying this teardown.
#>
#Requires -Version 5.1
[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='High')]
param(
    [Parameter(Mandatory=$true)][guid]$TenantId,
    [Parameter(Mandatory=$true)][guid]$ClientId,
    [ValidateSet('Standard','Restricted')][string]$PermissionProfile = 'Standard',
    [string]$EnvFile = '',
    [guid]$ServicePrincipalObjectId = [guid]::Empty,
    [switch]$IncludeWorkloadRbac,
    [switch]$Apply,
    [switch]$RemoveEnvironmentFile
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'tools\cleanup-reparse-points.ps1')
$expectedName = 'M365 Copilot Readiness Assessment Tool'
if ($PermissionProfile -eq 'Restricted') { $expectedName += ' - Restricted' }
if (-not $EnvFile) {
    $fileName = if ($PermissionProfile -eq 'Restricted') { '.env.restricted' } else { '.env' }
    $EnvFile = Join-Path $PSScriptRoot $fileName
}
if ($TenantId -eq [guid]::Empty -or $ClientId -eq [guid]::Empty) {
    throw 'TenantId and ClientId must be nonempty GUIDs.'
}
if ($IncludeWorkloadRbac -and $PermissionProfile -eq 'Restricted') {
    throw 'Restricted does not provision workload RBAC. Select Standard to clean up Standard workload access.'
}

function Get-CleanupEnvironmentValue {
    param([string]$Path, [string]$Name)
    $pattern = '^\s*(?:export\s+)?' + [regex]::Escape($Name) + '\s*='
    $matches = @(Get-Content -LiteralPath $Path -Encoding UTF8 | Where-Object { $_ -match $pattern })
    if ($matches.Count -gt 1) { throw "The selected environment file contains duplicate $Name entries; resolve them before cleanup." }
    if ($matches.Count -eq 0) { return '' }
    $value = ($matches[0] -split '=', 2)[1].Trim()
    if ($value.StartsWith('"') -or $value.StartsWith("'")) {
        $quoted = [regex]::Match($value, '^([''"])(.*?)\1\s*(?:#.*)?$')
        if (-not $quoted.Success) { throw "Invalid quoted $Name in the selected environment file." }
        return $quoted.Groups[2].Value
    }
    return ($value -split '\s+#', 2)[0].TrimEnd()
}

function Assert-CleanupEnvironment {
    param([string]$Path)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or $item.PSProvider.Name -ne 'FileSystem') {
        throw 'EnvFile must identify a regular filesystem file.'
    }
    # Cloud Files placeholders are allowed; symbolic links/junctions are refused.
    $ancestor = $item
    while ($ancestor) {
        Assert-SafeCleanupReparsePoint -Item $ancestor
        $ancestor = if ($ancestor -is [IO.FileInfo]) { $ancestor.Directory } else { $ancestor.Parent }
    }
    foreach ($entry in @(@('TENANT_ID', $TenantId), @('CLIENT_ID', $ClientId))) {
        $saved = Get-CleanupEnvironmentValue -Path $item.FullName -Name $entry[0]
        $parsed = [guid]::Empty
        if (-not [guid]::TryParse($saved, [ref]$parsed) -or $parsed -ne $entry[1]) {
            throw "$($entry[0]) in the selected environment file does not match the explicitly confirmed identity. No cleanup was performed."
        }
    }
    $savedProfile = Get-CleanupEnvironmentValue -Path $item.FullName -Name 'PERMISSION_PROFILE'
    if ($savedProfile -and $savedProfile -ne $PermissionProfile) {
        throw 'PERMISSION_PROFILE in the selected environment file does not match PermissionProfile.'
    }
    return $item.FullName
}

function Get-CleanupGraphObjects {
    $applications = @(Get-MgApplication -Filter "appId eq '$ClientId'" -All -Property Id,AppId,DisplayName,PasswordCredentials,KeyCredentials,RequiredResourceAccess -ErrorAction Stop)
    $principals = @(Get-MgServicePrincipal -Filter "appId eq '$ClientId'" -All -Property Id,AppId,DisplayName -ErrorAction Stop)
    if ($applications.Count -gt 1 -or $principals.Count -gt 1) {
        throw 'Ambiguous application or service-principal matches. Resolve duplicates manually; no object was selected.'
    }
    foreach ($candidate in @($applications) + @($principals)) {
        $objectGuid = [guid]::Empty
        if ([string]$candidate.AppId -ne [string]$ClientId -or
            [string]$candidate.DisplayName -cne $expectedName -or
            -not [guid]::TryParse([string]$candidate.Id, [ref]$objectGuid) -or $objectGuid -eq [guid]::Empty) {
            throw 'The returned application identity or display name does not match the dedicated assessment application. Review it manually.'
        }
    }
    return [pscustomobject]@{ Application = @($applications) | Select-Object -First 1; Principal = @($principals) | Select-Object -First 1 }
}

function Close-CleanupWorkloadConnection {
    # Disconnecting this script's session is local cleanup, including in WhatIf.
    $WhatIfPreference = $false
    if ($script:cleanupWorkloadConnection) {
        foreach ($connection in @($script:cleanupWorkloadConnection)) {
            if ($connection.ConnectionId) {
                Disconnect-ExchangeOnline -ConnectionId $connection.ConnectionId -Confirm:$false -ErrorAction Stop | Out-Null
            }
        }
        $script:cleanupWorkloadConnection = $null
    }
}

function Open-CleanupWorkloadConnection {
    param([ValidateSet('Purview','Exchange')][string]$Workload)
    # Session initialization must load its local proxy functions during preview.
    # This function-scoped preference never reaches the outer removal gate.
    $WhatIfPreference = $false
    $prefix = 'Cleanup' + [guid]::NewGuid().ToString('N').Substring(0, 10)
    if ($Workload -eq 'Purview') {
        Connect-IPPSSession -Prefix $prefix -DisableWAM -ErrorAction Stop | Out-Null
    } else {
        Connect-ExchangeOnline -Prefix $prefix -DisableWAM -ShowBanner:$false -ErrorAction Stop | Out-Null
    }
    $connections = @(Get-ConnectionInformation -ModulePrefix $prefix -ErrorAction Stop)
    $script:cleanupWorkloadConnection = $connections
    if ($connections.Count -ne 1) { throw "Cannot uniquely verify the $Workload cleanup session. No workload changes were made." }
    if ([string]$connections[0].TenantID -ne [string]$TenantId -or
        [string]$connections[0].State -ne 'Connected' -or
        [bool]$connections[0].IsEopSession -ne ($Workload -eq 'Purview') -or
        -not $connections[0].ConnectionId) {
        throw "The $Workload session does not match the confirmed tenant and workload. No workload changes were made."
    }
    return $prefix
}

function Get-CleanupWorkloadPlan {
    param([string]$Workload, [string]$Prefix, [guid]$ObjectId)
    $groupName = if ($Workload -eq 'Purview') { 'AI Readiness Purview Read-Only' } else { 'AI Readiness Exchange Read-Only' }
    # A nonexistent -Identity can return all principals. Always validate results.
    $allPrincipals = @(& "Get-${Prefix}ServicePrincipal" -ErrorAction Stop)
    $related = @($allPrincipals | Where-Object { [string]$_.AppId -eq [string]$ClientId -or [string]$_.ObjectId -eq [string]$ObjectId })
    if ($related.Count -gt 1 -or @($related | Where-Object {
        [string]$_.AppId -ne [string]$ClientId -or [string]$_.ObjectId -ne [string]$ObjectId
    }).Count -gt 0) { throw "The $Workload principal identity is ambiguous or mismatched; resolve it manually." }
    $principal = $related | Select-Object -First 1
    $groups = @(& "Get-${Prefix}RoleGroup" -ResultSize Unlimited -ErrorAction Stop | Where-Object { [string]$_.Name -ceq $groupName })
    if ($groups.Count -gt 1) { throw "Multiple $Workload assessment role groups match; resolve them manually." }
    $member = $false
    if ($groups.Count -eq 1) {
        if (-not $groups[0].Identity) { throw "The $Workload role group has no usable identity." }
        $members = @(& "Get-${Prefix}RoleGroupMember" -Identity $groups[0].Identity -ResultSize Unlimited -ErrorAction Stop)
        $matchingMembers = @($members | Where-Object {
            [string]$_.ExternalDirectoryObjectId -eq [string]$ObjectId -or
            [string]$_.Identity -eq [string]$ObjectId -or
            ($principal -and $principal.Identity -and [string]$_.Identity -eq [string]$principal.Identity)
        })
        if ($matchingMembers.Count -gt 1) { throw "The $Workload assessment membership is ambiguous; resolve it manually." }
        $member = $matchingMembers.Count -eq 1
    }
    return [pscustomobject]@{
        Workload=$Workload; GroupName=$groupName; GroupIdentity=if ($groups.Count) { [string]$groups[0].Identity } else { '' }
        ObjectId=[string]$ObjectId; RemoveMember=$member; RemovePrincipal=[bool]$principal
    }
}

# Validate the explicit confirmation against local identity BEFORE imports/authentication.
$environmentPath = Assert-CleanupEnvironment -Path $EnvFile
$graphConnected = $false
$script:cleanupWorkloadConnection = $null
try {
    Import-Module Microsoft.Graph.Authentication -ErrorAction Stop
    Import-Module Microsoft.Graph.Applications -ErrorAction Stop
    $scope = if ($Apply) { 'Application.ReadWrite.All' } else { 'Application.Read.All' }
    Connect-MgGraph -TenantId $TenantId -ContextScope Process -Scopes $scope -NoWelcome -ErrorAction Stop | Out-Null
    $graphConnected = $true
    if ([string](Get-MgContext).TenantId -ne [string]$TenantId) { throw 'Graph authenticated to a different tenant; no cleanup was performed.' }
    $objects = Get-CleanupGraphObjects
    $app = $objects.Application
    $sp = $objects.Principal
    if ($sp -and $ServicePrincipalObjectId -ne [guid]::Empty -and $ServicePrincipalObjectId -ne [guid]$sp.Id) {
        throw 'ServicePrincipalObjectId does not match the live enterprise application.'
    }
    if ($sp) { $ServicePrincipalObjectId = [guid]$sp.Id }
    Write-Host "Tenant: $TenantId; client: $ClientId; profile: $PermissionProfile"
    Write-Host "Dedicated application: $expectedName"
    if ($app) {
        $passwordCount = @($app.PasswordCredentials | Where-Object { $null -ne $_ }).Count
        $certificateCount = @($app.KeyCredentials | Where-Object { $null -ne $_ }).Count
        Write-Host "Application object: $($app.Id); password credentials: $passwordCount; certificate credentials: $certificateCount"
        $requestedPermissions = @($app.RequiredResourceAccess | ForEach-Object { $_.ResourceAccess } | Where-Object { $null -ne $_ })
        $resourceCount = @($app.RequiredResourceAccess | Where-Object { $null -ne $_ }).Count
        Write-Host "Requested API resources: $resourceCount; requested permissions: $($requestedPermissions.Count)"
    } else { Write-Host 'Application registration: already absent.' }
    if ($sp) {
        $grants = @(Get-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $sp.Id -All -ErrorAction Stop)
        Write-Host "Enterprise application object: $($sp.Id); application grants: $($grants.Count)"
        Write-Host 'Retain this enterprise application object ID for a workload-cleanup retry.'
    } else { Write-Host 'Enterprise application: already absent.' }

    $workloadPlans = @()
    if ($IncludeWorkloadRbac) {
        if ($ServicePrincipalObjectId -eq [guid]::Empty) {
            throw 'The enterprise application is absent. Supply its previously recorded -ServicePrincipalObjectId to verify workload cleanup.'
        }
        $module = Get-Module -ListAvailable -Name ExchangeOnlineManagement | Where-Object { $_.Version -ge [version]'3.7.2' } | Sort-Object Version -Descending | Select-Object -First 1
        if (-not $module) { throw 'Workload cleanup requires installed ExchangeOnlineManagement 3.7.2 or later. Install it separately, then retry.' }
        Import-Module ExchangeOnlineManagement -MinimumVersion 3.7.2 -ErrorAction Stop
        # Discover both plans before changing any workload or Graph object.
        foreach ($workload in @('Purview', 'Exchange')) {
            try {
                $prefix = Open-CleanupWorkloadConnection -Workload $workload
                $plan = Get-CleanupWorkloadPlan -Workload $workload -Prefix $prefix -ObjectId $ServicePrincipalObjectId
                $workloadPlans += $plan
                Write-Host "$workload reference $ServicePrincipalObjectId`: remove assessment-group membership=$($plan.RemoveMember); remove workload reference=$($plan.RemovePrincipal)"
            } finally { Close-CleanupWorkloadConnection }
        }
    } else {
        Write-Host 'Workload RBAC was not inspected. Standard unattended setups require -IncludeWorkloadRbac or manual workload cleanup before deleting the application.'
    }

    $actions = @()
    foreach ($plan in $workloadPlans) {
        if ($plan.RemoveMember) { $actions += [pscustomobject]@{ Target="$($plan.Workload): $($plan.GroupName), member $($plan.ObjectId)"; Action='Remove only the assessment principal membership' } }
        if ($plan.RemovePrincipal) { $actions += [pscustomobject]@{ Target="$($plan.Workload): principal $($plan.ObjectId)"; Action='Remove the assessment workload principal reference' } }
    }
    if ($sp) { $actions += [pscustomobject]@{ Target="Tenant $TenantId, enterprise application $($sp.Id)"; Action='Delete the dedicated enterprise application and its grants' } }
    if ($app) { $actions += [pscustomobject]@{ Target="Tenant $TenantId, application $($app.Id)"; Action='Delete the dedicated application registration and all its credentials' } }
    if ($RemoveEnvironmentFile) { $actions += [pscustomobject]@{ Target=$environmentPath; Action='Delete the selected environment file after remote cleanup succeeds' } }
    foreach ($action in $actions) { Write-Host "Planned: $($action.Action) [$($action.Target)]" }
    if (-not $Apply) { Write-Host 'Preview only. Review the identities, then rerun with -Apply to request these removals.'; return }
    # Gate EVERY destructive call ourselves: workload -WhatIf is not supported.
    $approved = $true
    foreach ($action in $actions) {
        if (-not $PSCmdlet.ShouldProcess($action.Target, $action.Action)) { $approved = $false }
    }
    if (-not $approved) { Write-Host 'One or more actions were declined or simulated. No removals were performed.'; return }

    foreach ($plan in $workloadPlans) {
        if (-not $plan.RemoveMember -and -not $plan.RemovePrincipal) { continue }
        try {
            $prefix = Open-CleanupWorkloadConnection -Workload $plan.Workload
            $current = Get-CleanupWorkloadPlan -Workload $plan.Workload -Prefix $prefix -ObjectId $ServicePrincipalObjectId
            if ($current.GroupIdentity -ne $plan.GroupIdentity -or $current.RemoveMember -ne $plan.RemoveMember -or $current.RemovePrincipal -ne $plan.RemovePrincipal) {
                throw 'Workload state changed after preview; rerun cleanup to review the current plan.'
            }
            if ($plan.RemoveMember) { & "Remove-${prefix}RoleGroupMember" -Identity $plan.GroupIdentity -Member $plan.ObjectId -Confirm:$false -ErrorAction Stop }
            if ($plan.RemovePrincipal) { & "Remove-${prefix}ServicePrincipal" -Identity $plan.ObjectId -Confirm:$false -ErrorAction Stop }
            $after = Get-CleanupWorkloadPlan -Workload $plan.Workload -Prefix $prefix -ObjectId $ServicePrincipalObjectId
            if ($after.RemoveMember -or $after.RemovePrincipal) {
                throw 'The workload membership or principal reference is still visible. Allow removal to propagate, then rerun; Graph objects and the environment file were retained.'
            }
        } finally { Close-CleanupWorkloadConnection }
    }
    if ($sp) { Remove-MgServicePrincipal -ServicePrincipalId $sp.Id -Confirm:$false -ErrorAction Stop }
    if ($app) { Remove-MgApplication -ApplicationId $app.Id -Confirm:$false -ErrorAction Stop }
    $remaining = Get-CleanupGraphObjects
    if ($remaining.Application -or $remaining.Principal) {
        throw 'The application or enterprise application is still visible. Allow deletion to propagate and rerun; the environment file was retained.'
    }
    if ($RemoveEnvironmentFile) {
        $verifiedPath = Assert-CleanupEnvironment -Path $environmentPath
        if ($verifiedPath -cne $environmentPath) { throw 'The selected environment path changed; the file was retained.' }
        Remove-Item -LiteralPath $verifiedPath -Force -Confirm:$false -ErrorAction Stop
        Write-Host 'Removed the selected environment file.'
    }
    Write-Host 'Requested remote cleanup completed. Other profiles, shared groups, certificates, and saved evidence were retained.'
} finally {
    try { Close-CleanupWorkloadConnection } finally {
        if ($graphConnected) { Disconnect-MgGraph -ErrorAction Stop | Out-Null }
    }
}

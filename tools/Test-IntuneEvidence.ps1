#Requires -Version 5.1
<#
Read-only, bounded Graph probe. No assessment, files, device/user names or tokens.
Optional beta requests are diagnostic only and do not change collector settings.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][guid]$TenantId,
    [guid]$ManagedDeviceId = [guid]::Empty,
    [switch]$IncludeBeta
)
$ErrorActionPreference = 'Stop'

function Get-ProbeValue($Object, [string]$Name) {
    if ($null -eq $Object) { return $null }
    if ($Object -is [System.Collections.IDictionary]) { return $Object[$Name] }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -ne $property) { return $property.Value }
    return $null
}

function Invoke-ProbeGet([string]$Path) {
    $uri = if ($Path.StartsWith('https://')) { [uri]$Path } else { [uri]('https://graph.microsoft.com'+$Path) }
    if ($uri.Scheme -ne 'https' -or $uri.Host -ne 'graph.microsoft.com' -or $uri.UserInfo) {
        throw 'Unexpected Graph response URL.'
    }
    Invoke-MgGraphRequest -Method GET -Uri $uri.AbsoluteUri -OutputType PSObject -ErrorAction Stop
}

function Get-ProbeRows([string]$Path) {
    $rows = @(); $next = $Path; $pages = 0
    do {
        $response = Invoke-ProbeGet $next
        $rows += @(Get-ProbeValue $response 'value' | Where-Object { $null -ne $_ })
        $next = Get-ProbeValue $response '@odata.nextLink'
        $pages++
    } while ($next -and $pages -lt 5)
    [pscustomobject]@{ Rows=@($rows); Complete=(-not $next); Pages=$pages }
}

function Get-ProbeFailure($Record) {
    $status = $null
    try { $status = [int]$Record.Exception.Response.StatusCode } catch { }
    # Exception messages can include identifiers/URLs; do not publish them.
    [pscustomobject]@{ Status='Request failed'; HttpStatus=$status; ErrorType=$Record.Exception.GetType().Name }
}

function New-SettingsProbeRow([string]$Label, $Object) {
    $settings = Get-ProbeValue $Object 'settings'
    [pscustomobject]@{
        Request=$Label; Status='Returned'; SettingsObjectReturned=($null -ne $settings)
        SecureByDefault=(Get-ProbeValue $settings 'secureByDefault')
        ComplianceValidityDays=(Get-ProbeValue $settings 'deviceComplianceCheckinThresholdDays')
    }
}

function Invoke-IntuneEvidenceProbe([guid]$ExpectedTenantId, [guid]$DeviceId = [guid]::Empty, [switch]$Beta) {
    if (-not (Get-Command Connect-MgGraph -ErrorAction SilentlyContinue)) {
        throw 'Install Microsoft.Graph.Authentication first. Microsoft.Graph.DeviceManagement is optional for the SDK comparison.'
    }
    $scopes = @('DeviceManagementConfiguration.Read.All','DeviceManagementManagedDevices.Read.All')
    $context = Get-MgContext
    if ($null -eq $context -or [string]$context.TenantId -ne [string]$ExpectedTenantId -or
        @($scopes | Where-Object { $_ -notin @($context.Scopes) }).Count -gt 0) {
        Connect-MgGraph -TenantId $ExpectedTenantId -Scopes $scopes -ContextScope Process -NoWelcome | Out-Null
        $context = Get-MgContext
    }
    if ($null -eq $context -or [string]$context.TenantId -ne [string]$ExpectedTenantId) {
        throw 'Sign-in tenant does not match the explicitly selected tenant. No data requests were made.'
    }
    $settingsResults = @()
    $requests = @(
        @{Label='REST v1.0 selected'; Path='/v1.0/deviceManagement?$select=settings'},
        @{Label='REST v1.0 full'; Path='/v1.0/deviceManagement'}
    )
    if ($Beta) {
        $requests += @{Label='REST beta selected (diagnostic)'; Path='/beta/deviceManagement?$select=settings'}
        $requests += @{Label='REST beta full (diagnostic)'; Path='/beta/deviceManagement'}
    }
    foreach ($request in $requests) {
        try { $settingsResults += New-SettingsProbeRow $request.Label (Invoke-ProbeGet $request.Path) }
        catch { $settingsResults += [pscustomobject]@{Request=$request.Label; Failure=(Get-ProbeFailure $_)} }
    }
    if (Get-Command Get-MgDeviceManagement -ErrorAction SilentlyContinue) {
        try { $settingsResults += New-SettingsProbeRow 'SDK v1.0 selected' (Get-MgDeviceManagement -Property settings -ErrorAction Stop) }
        catch { $settingsResults += [pscustomobject]@{Request='SDK v1.0 selected'; Failure=(Get-ProbeFailure $_)} }
    } else {
        $settingsResults += [pscustomobject]@{Request='SDK v1.0 selected'; Status='Skipped; Microsoft.Graph.DeviceManagement not installed'}
    }
    $deviceResult = $null; $policyResults = @(); $settingResults = @()
    if ($DeviceId -ne [guid]::Empty) {
        try {
            $device = Invoke-ProbeGet ('/v1.0/deviceManagement/managedDevices/'+$DeviceId+'?$select=id,complianceState,lastSyncDateTime')
            $deviceResult = [pscustomobject]@{OverallCompliance=(Get-ProbeValue $device 'complianceState'); LastSync=(Get-ProbeValue $device 'lastSyncDateTime')}
            $policies = Get-ProbeRows ('/v1.0/deviceManagement/managedDevices/'+$DeviceId+'/deviceCompliancePolicyStates')
            $index = 0
            foreach ($policy in @($policies.Rows | Select-Object -First 5)) {
                $index++; $reference = 'Policy '+$index
                $policyResults += [pscustomobject]@{Policy=$reference; State=(Get-ProbeValue $policy 'state'); SettingCount=(Get-ProbeValue $policy 'settingCount')}
                $version = if ($Beta) { 'beta' } else { 'v1.0' }
                try {
                    $id = [uri]::EscapeDataString([string](Get-ProbeValue $policy 'id'))
                    if (-not $id) { throw 'Policy state identifier was not returned.' }
                    $details = Get-ProbeRows ('/'+$version+'/deviceManagement/managedDevices/'+$DeviceId+'/deviceCompliancePolicyStates/'+$id+'/settingStates')
                    foreach ($setting in $details.Rows) {
                        $settingResults += [pscustomobject]@{
                            Policy=$reference; ApiVersion=$version
                            Check=(Get-ProbeValue $setting 'settingName'); SettingKey=(Get-ProbeValue $setting 'setting')
                            State=(Get-ProbeValue $setting 'state'); DiagnosticCode=(Get-ProbeValue $setting 'errorCode')
                            DiagnosticTextReturned=($null -ne (Get-ProbeValue $setting 'errorDescription') -and [string](Get-ProbeValue $setting 'errorDescription') -ne '')
                            ContributingPolicyCount=@(Get-ProbeValue $setting 'sources' | Where-Object { $null -ne $_ }).Count
                        }
                    }
                    $policyResults[-1] | Add-Member NoteProperty SettingReadComplete $details.Complete
                } catch { $policyResults[-1] | Add-Member NoteProperty SettingReadFailure (Get-ProbeFailure $_) }
            }
            $deviceResult | Add-Member NoteProperty PolicyReadComplete ($policies.Complete -and $policies.Rows.Count -le 5)
        } catch { $deviceResult = Get-ProbeFailure $_ }
    }
    [pscustomobject]@{
        TenantConfirmed=$true; BetaDiagnostic=[bool]$Beta
        TenantSettings=@($settingsResults); Device=$deviceResult
        PolicyStates=@($policyResults); SettingStates=@($settingResults)
        Note='Only GET requests. Device/user/policy names, IDs, diagnostic text and tokens are excluded. Policy labels are local aliases. At most five policies and five pages per collection.'
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    Invoke-IntuneEvidenceProbe -ExpectedTenantId $TenantId -DeviceId $ManagedDeviceId -Beta:$IncludeBeta | ConvertTo-Json -Depth 8
}

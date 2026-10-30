$ErrorActionPreference = 'Stop'
$fakeTenant = [guid]'11111111-1111-4111-8111-111111111111'
$fakeDevice = [guid]'22222222-2222-4222-8222-222222222222'
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'tools/Test-IntuneEvidence.ps1') -TenantId $fakeTenant
$script:wrongTenant = $false
$script:requests = @()
$script:loopPages = $false
function Assert($Condition, $Message) { if (-not $Condition) { throw $Message } }
function Get-MgContext {
    $id = if ($script:wrongTenant) { '33333333-3333-4333-8333-333333333333' } else { [string]$fakeTenant }
    [pscustomobject]@{TenantId=$id; Scopes=@('DeviceManagementConfiguration.Read.All','DeviceManagementManagedDevices.Read.All')}
}
function Connect-MgGraph { param($TenantId,$Scopes,$ContextScope,[switch]$NoWelcome) }
function Get-MgDeviceManagement { param($Property,$ErrorAction)
    @{settings=@{secureByDefault=$false;deviceComplianceCheckinThresholdDays=45}}
}
function Invoke-MgGraphRequest { param($Method,$Uri,$OutputType,$ErrorAction)
    Assert ($Method -eq 'GET') 'Probe attempted a mutation.'
    $script:requests += $Uri
    if ($script:loopPages) { return @{value=@(@{state='error'});'@odata.nextLink'='https://graph.microsoft.com/v1.0/deviceManagement'} }
    if ($Uri -match '/settingStates$') {
        return @{value=@(@{settingName='RequireRemainContact';state='error';errorCode=42;
            errorDescription='PRIVATE diagnostic';sources=@(@{displayName='PRIVATE policy'})})}
    }
    if ($Uri -match '/deviceCompliancePolicyStates$') {
        return @{value=@(@{id='private-policy-id';displayName='PRIVATE policy';state='error';settingCount=1})}
    }
    if ($Uri -match '/managedDevices/') {
        return @{id=[string]$fakeDevice;deviceName='PRIVATE device';complianceState='noncompliant';lastSyncDateTime='2026-01-01T00:00:00Z'}
    }
    if ($Uri -match '\?\$select=settings$') { return @{settings=$null} }
    return @{settings=@{secureByDefault=$false;deviceComplianceCheckinThresholdDays=45}}
}
$result = Invoke-IntuneEvidenceProbe -ExpectedTenantId $fakeTenant -DeviceId $fakeDevice
Assert (-not ($script:requests -match '/beta/')) 'Default probe requested beta.'
Assert ($result.TenantSettings[0].SettingsObjectReturned -eq $false) 'Null settings were not preserved.'
Assert ($result.TenantSettings[1].SecureByDefault -ceq $false) 'False tenant setting was lost.'
Assert ($result.TenantSettings[1].ComplianceValidityDays -eq 45) 'Validity days were lost.'
Assert ($result.Device.OverallCompliance -eq 'noncompliant') 'Overall state was changed.'
Assert ($result.SettingStates[0].State -eq 'error') 'Error was converted to noncompliant.'
Assert ($result.SettingStates[0].DiagnosticCode -eq 42) 'Diagnostic code was lost.'
Assert ($result.SettingStates[0].ContributingPolicyCount -eq 1) 'Contributing policy count was lost.'
$json = $result | ConvertTo-Json -Depth 8
Assert (-not ($json -match 'PRIVATE|private-policy-id|22222222')) 'Private names/IDs or diagnostic text leaked.'
$script:requests = @()
$betaResult = Invoke-IntuneEvidenceProbe -ExpectedTenantId $fakeTenant -Beta
Assert (@($script:requests -match '/beta/').Count -eq 2) ('Explicit beta comparison mismatch: '+($script:requests -join ', '))
Assert ($null -eq $betaResult.Device) 'Device query ran without a selected device.'
$script:wrongTenant = $true; $script:requests = @(); $rejected = $false
try { Invoke-IntuneEvidenceProbe -ExpectedTenantId $fakeTenant | Out-Null } catch { $rejected = $true }
Assert $rejected 'Wrong tenant was not rejected.'
Assert ($script:requests.Count -eq 0) 'Data was read from the wrong tenant.'
$rejected = $false
try { Invoke-ProbeGet 'https://example.invalid/private' | Out-Null } catch { $rejected = $true }
Assert $rejected 'Unexpected pagination host was accepted.'
$script:loopPages = $true; $script:requests = @()
$bounded = Get-ProbeRows '/v1.0/deviceManagement'
Assert ($bounded.Pages -eq 5 -and $bounded.Complete -eq $false) 'Pagination limit was not qualified.'
Assert ($script:requests.Count -eq 5) 'Pagination exceeded its request limit.'
Write-Output 'Intune evidence probe: mocked read-only and tenant-isolation checks passed.'

<#
.SYNOPSIS
  Reconciles the application used by the AI readiness assessment.

.DESCRIPTION
  Standard installs the supported PowerShell modules and configures the stable,
  least-privileged collector permissions. Unattended additionally validates and
  attaches a certificate and adds the SharePoint/Purview app-only permission packs.
  Preview permission packs are added only when explicitly selected.
#>

#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Standard','Unattended')][string]$Mode = 'Standard',
    [ValidateSet('None','PowerPlatform','ShadowAI','NetworkAccess','All')]
    [string]$PreviewCollectors = 'None',
    [string]$CertificatePath = '',
    [string]$CertificateThumbprint = '',
    [switch]$ConfirmBroadSharePointAccess,
    [switch]$RotateCredential,
    [switch]$PruneUnusedPermissions
)

$ErrorActionPreference = 'Stop'
$AppName = 'M365 Copilot Readiness Assessment Tool'
$SecretExpirationDays = 90

function Write-Success { param($Message) Write-Host "[OK] $Message" -ForegroundColor Green }
function Write-Info { param($Message) Write-Host "[INFO] $Message" -ForegroundColor Cyan }
function Write-Warn { param($Message) Write-Host "[WARN] $Message" -ForegroundColor Yellow }
function Write-Fail { param($Message) Write-Host "[FAIL] $Message" -ForegroundColor Red }

function Get-ResourceAccessByName {
    param(
        [Parameter(Mandatory=$true)][string]$ResourceAppId,
        [Parameter(Mandatory=$true)][string[]]$PermissionNames
    )
    $resource = Get-MgServicePrincipal -Filter "appId eq '$ResourceAppId'" -Property Id,AppId,DisplayName,AppRoles -ErrorAction Stop | Select-Object -First 1
    if (-not $resource) { throw "Resource service principal $ResourceAppId was not found." }
    $access = @()
    foreach ($name in $PermissionNames) {
        $role = @($resource.AppRoles | Where-Object { $_.Value -eq $name -and $_.AllowedMemberTypes -contains 'Application' }) | Select-Object -First 1
        if (-not $role) { throw "Application permission '$name' was not found on $($resource.DisplayName)." }
        $access += @{ Id = $role.Id; Type = 'Role' }
    }
    return @{
        ResourceAppId = $ResourceAppId
        ResourceObjectId = $resource.Id
        ResourceAccess = $access
        PermissionNames = $PermissionNames
    }
}

function Merge-ResourceAccess {
    param([object[]]$Existing, [object[]]$Desired, [switch]$Prune)
    if ($Prune) {
        return @($Desired | ForEach-Object {
            @{ ResourceAppId = $_.ResourceAppId; ResourceAccess = @($_.ResourceAccess) }
        })
    }
    $byResource = [ordered]@{}
    foreach ($entry in @($Existing) + @($Desired)) {
        if (-not $entry.ResourceAppId) { continue }
        $resourceId = [string]$entry.ResourceAppId
        if (-not $byResource.Contains($resourceId)) {
            $byResource[$resourceId] = [ordered]@{ ResourceAppId = $resourceId; ResourceAccess = @() }
        }
        foreach ($permission in @($entry.ResourceAccess)) {
            $permissionId = [string]$permission.Id
            if (-not @($byResource[$resourceId].ResourceAccess | Where-Object { [string]$_.Id -eq $permissionId })) {
                $byResource[$resourceId].ResourceAccess += @{ Id = $permission.Id; Type = [string]$permission.Type }
            }
        }
    }
    return @($byResource.Values)
}

function Get-DotEnvValue {
    param([string]$Path, [string]$Name)
    if (-not (Test-Path -LiteralPath $Path)) { return '' }
    $line = Get-Content -LiteralPath $Path | Where-Object { $_ -match "^\s*$([regex]::Escape($Name))=" } | Select-Object -Last 1
    if ($line) { return ($line -split '=', 2)[1].Trim() }
    return ''
}

function Set-DotEnvValue {
    param([string]$Path, [string]$Name, [string]$Value)
    $lines = if (Test-Path -LiteralPath $Path) { @(Get-Content -LiteralPath $Path) } else { @('# AI readiness assessment credentials', '# Never commit this file.') }
    $found = $false
    $updated = foreach ($line in $lines) {
        if ($line -match "^\s*$([regex]::Escape($Name))=") {
            $found = $true
            "${Name}=${Value}"
        } else { $line }
    }
    if (-not $found) { $updated += "${Name}=${Value}" }
    $updated | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Get-SetupCertificate {
    param([string]$Path, [string]$Thumbprint)
    if ($Thumbprint) {
        $certificate = Get-ChildItem -Path "Cert:\CurrentUser\My\$Thumbprint" -ErrorAction SilentlyContinue
        if (-not $certificate) { throw "Certificate thumbprint $Thumbprint was not found in Cert:\CurrentUser\My." }
        if (-not $certificate.HasPrivateKey) { throw "Certificate $Thumbprint does not have a private key in the CurrentUser certificate store." }
        return $certificate
    }
    if ($Path) {
        $resolved = Resolve-Path -LiteralPath $Path -ErrorAction Stop
        try {
            $certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($resolved.Path)
            if (-not $certificate.HasPrivateKey) { throw 'The certificate file does not contain a private key.' }
            return $certificate
        }
        catch { throw "CertificatePath must be a readable certificate. Password-protected PFX files should be imported into CurrentUser\My and supplied with -CertificateThumbprint." }
    }
    return $null
}

function Get-MissingApplicationConsent {
    param([object[]]$Assignments, [object[]]$Desired)
    $missing = @()
    foreach ($resource in $Desired) {
        foreach ($permission in @($resource.ResourceAccess)) {
            if (-not @($Assignments | Where-Object {
                [string]$_.ResourceId -eq [string]$resource.ResourceObjectId -and
                [string]$_.AppRoleId -eq [string]$permission.Id
            })) {
                $missing += "$($resource.ResourceAppId):$($permission.Id)"
            }
        }
    }
    return @($missing)
}

function Set-ApplicationReadOnlyRoleGroup {
    param(
        [ValidateSet('Purview','Exchange')][string]$Connection,
        [string]$RoleGroupName,
        [string[]]$Cmdlets,
        [string]$AppId,
        [string]$ServicePrincipalObjectId,
        [string]$DisplayName
    )
    try {
        if ($Connection -eq 'Purview') {
            Connect-IPPSSession -DisableWAM -ErrorAction Stop -WarningAction SilentlyContinue | Out-Null
        } else {
            Connect-ExchangeOnline -DisableWAM -ShowBanner:$false -ErrorAction Stop -WarningAction SilentlyContinue | Out-Null
        }

        $workloadPrincipal = Get-ServicePrincipal -Identity $ServicePrincipalObjectId -ErrorAction SilentlyContinue
        if (-not $workloadPrincipal) {
            New-ServicePrincipal -AppId $AppId -ObjectId $ServicePrincipalObjectId -DisplayName $DisplayName -ErrorAction Stop | Out-Null
            $workloadPrincipal = Get-ServicePrincipal -Identity $ServicePrincipalObjectId -ErrorAction Stop
        }

        $roleNames = @()
        foreach ($cmdletName in $Cmdlets) {
            $candidateRoles = @(Get-ManagementRole -Cmdlet $cmdletName -ErrorAction SilentlyContinue)
            $preferred = $candidateRoles | Sort-Object @{
                Expression = { if ($_.Name -match '(?i)view-only|read') { 0 } else { 1 } }
            }, @{ Expression = { $_.Name.Length } } | Select-Object -First 1
            if (-not $preferred) { throw "No management role exposes $cmdletName in $Connection PowerShell." }
            $roleNames += $preferred.Name
        }
        $roleNames = @($roleNames | Sort-Object -Unique)

        $roleGroup = Get-RoleGroup -Identity $RoleGroupName -ErrorAction SilentlyContinue
        if (-not $roleGroup) {
            $roleGroup = New-RoleGroup -Name $RoleGroupName -Roles $roleNames -Description 'Read-only cmdlets required by the AI readiness assessment application.' -ErrorAction Stop
        } else {
            $existingRoles = @($roleGroup.Roles | ForEach-Object { [string]$_ })
            foreach ($roleName in $roleNames) {
                if ($existingRoles -notcontains $roleName) {
                    New-ManagementRoleAssignment -Role $roleName -SecurityGroup $RoleGroupName -ErrorAction Stop | Out-Null
                }
            }
        }

        $alreadyMember = @(Get-RoleGroupMember -Identity $RoleGroupName -ResultSize Unlimited -ErrorAction Stop | Where-Object {
            [string]$_.ExternalDirectoryObjectId -eq $ServicePrincipalObjectId -or
            [string]$_.Identity -eq [string]$workloadPrincipal.Identity
        }).Count -gt 0
        if (-not $alreadyMember) {
            Add-RoleGroupMember -Identity $RoleGroupName -Member $workloadPrincipal.Identity -ErrorAction Stop
        }
        Write-Success "$Connection application role group '$RoleGroupName' is configured."
        return $true
    } catch {
        Write-Warn "$Connection read-only application role group could not be configured: $($_.Exception.Message)"
        Write-Warn 'Run python main.py --check-connections after role propagation; a Role missing result identifies this gap.'
        return $false
    } finally {
        try { Disconnect-ExchangeOnline -Confirm:$false -ErrorAction SilentlyContinue | Out-Null } catch { }
    }
}

Write-Host "`n=============================================================================" -ForegroundColor Cyan
Write-Host " AI Readiness Assessment - Application Reconciliation" -ForegroundColor Cyan
Write-Host "=============================================================================" -ForegroundColor Cyan

. "$PSScriptRoot\Check-PSModules.ps1"
if (-not (Test-RequiredModules -ScriptType 'Setup' -InstallMissing)) { exit 1 }
$windowsPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (Test-Path -LiteralPath $windowsPowerShell) {
    & $windowsPowerShell -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\Check-PSModules.ps1" -ScriptType SharePoint
    if ($LASTEXITCODE -ne 0) { throw 'SharePoint Online module setup failed in Windows PowerShell.' }
} elseif (-not (Test-RequiredModules -ScriptType 'SharePoint' -InstallMissing)) {
    throw 'SharePoint Online module setup failed in the available PowerShell host.'
}

Import-Module Microsoft.Graph.Authentication -Force
Import-Module Microsoft.Graph.Applications -Force
Import-Module Microsoft.Graph.Identity.DirectoryManagement -Force

Write-Info 'Connecting to Microsoft Graph for application administration...'
Connect-MgGraph -Scopes @(
    'Application.ReadWrite.All','Directory.Read.All',
    'RoleManagement.ReadWrite.Directory','AppRoleAssignment.ReadWrite.All'
) -ContextScope Process -NoWelcome
$context = Get-MgContext
if (-not $context -or -not $context.TenantId) { throw 'Microsoft Graph authentication did not return a tenant.' }

$existingApps = @(Get-MgApplication -Filter "displayName eq '$AppName'" -Property Id,AppId,DisplayName,RequiredResourceAccess,PasswordCredentials,KeyCredentials)
if ($existingApps.Count -gt 1) {
    Write-Warn "Multiple applications named '$AppName' exist. Reconciling the oldest returned object; review duplicates manually."
}
$app = $existingApps | Select-Object -First 1
if (-not $app) {
    $app = New-MgApplication -DisplayName $AppName -SignInAudience 'AzureADMyOrg' -PublicClient @{ RedirectUris = @('http://localhost') }
    Write-Success "Created application $($app.AppId)."
} else {
    Write-Success "Reusing application $($app.AppId); it will not be deleted or recreated."
}

$graphResourceId = '00000003-0000-0000-c000-000000000000'
$defenderResourceId = 'fc780465-2017-40d4-a0c5-307022471b92'
$sharePointResourceId = '00000003-0000-0ff1-ce00-000000000000'
$exchangeResourceId = '00000002-0000-0ff1-ce00-000000000000'
$eopResourceId = '00000007-0000-0ff1-ce00-000000000000'

# Stable permission identifiers used by endpoint contract tests and setup audits.
# Runtime resolution still uses permission names so the manifest is readable.
$permissionReference = @(
    @{ Id = "9e640839-a198-48fb-8b9a-013fd6f6cbcd"; Name = "Policy.Read.PermissionGrant" },
    @{ Id = "e30060de-caa5-4331-99d3-6ac6c966a9a4"; Name = "NetworkAccess.Read.All" }
)

$registryPath = Join-Path $PSScriptRoot 'collector-registry.json'
if (-not (Test-Path -LiteralPath $registryPath)) { throw "Collector registry not found: $registryPath" }
$collectorRegistry = (Get-Content -LiteralPath $registryPath -Raw | ConvertFrom-Json).collectors
$enabledCollectors = @($collectorRegistry | Where-Object {
    $_.default_setup -or
    ($Mode -eq 'Unattended' -and $_.setup_when -eq 'Unattended') -or
    ($_.preview_pack -and ($PreviewCollectors -eq 'All' -or $_.preview_pack -eq $PreviewCollectors))
})
$resourceIds = @{
    graph = $graphResourceId
    defender = $defenderResourceId
    sharepoint = $sharePointResourceId
    exchange = $exchangeResourceId
    eop = $eopResourceId
}
$permissionsByResource = @{}
foreach ($collector in $enabledCollectors) {
    foreach ($property in @($collector.permission_resources.PSObject.Properties)) {
        if (-not $resourceIds.ContainsKey($property.Name)) { continue }
        if (-not $permissionsByResource.ContainsKey($property.Name)) { $permissionsByResource[$property.Name] = @() }
        $permissionsByResource[$property.Name] += @($property.Value)
    }
}

$graphPermissions = @($permissionsByResource.graph | Sort-Object -Unique)
$desired = @()
foreach ($resourceName in @($permissionsByResource.Keys | Sort-Object)) {
    $permissionNames = @($permissionsByResource[$resourceName] | Sort-Object -Unique)
    if ($permissionNames.Count -gt 0) {
        $desired += Get-ResourceAccessByName -ResourceAppId $resourceIds[$resourceName] -PermissionNames $permissionNames
    }
}

$certificate = Get-SetupCertificate -Path $CertificatePath -Thumbprint $CertificateThumbprint
if ($Mode -eq 'Unattended') {
    if (-not $certificate) { throw 'Unattended mode requires -CertificatePath or -CertificateThumbprint.' }
    Write-Warn 'Unattended mode adds SharePoint Sites.FullControl.All because SharePoint administrative PowerShell does not support client-secret authentication.'
    if (-not $ConfirmBroadSharePointAccess) {
        $answer = Read-Host 'Type YES to approve Sites.FullControl.All for this app; the assessment collector uses it only for read operations'
        if ($answer -cne 'YES') { throw 'SharePoint app-only permission was not approved. Rerun Standard mode for delegated browser authentication.' }
    }
}

$requiredResourceAccess = Merge-ResourceAccess -Existing $app.RequiredResourceAccess -Desired $desired -Prune:$PruneUnusedPermissions
Update-MgApplication -ApplicationId $app.Id -RequiredResourceAccess $requiredResourceAccess
Write-Success "Reconciled permissions for $($enabledCollectors.Count) selected collectors from collector-registry.json."

if ($certificate) {
    $thumbprint = $certificate.Thumbprint
    $alreadyAttached = @($app.KeyCredentials | Where-Object {
        $_.CustomKeyIdentifier -and ([Convert]::ToBase64String($_.CustomKeyIdentifier) -eq [Convert]::ToBase64String($certificate.GetCertHash()))
    }).Count -gt 0
    if (-not $alreadyAttached) {
        $newKey = @{
            Type = 'AsymmetricX509Cert'; Usage = 'Verify'; Key = $certificate.RawData
            DisplayName = 'AI readiness assessment'; StartDateTime = $certificate.NotBefore.ToUniversalTime()
            EndDateTime = $certificate.NotAfter.ToUniversalTime()
        }
        Update-MgApplication -ApplicationId $app.Id -KeyCredentials @(@($app.KeyCredentials) + $newKey)
        Write-Success "Attached certificate $thumbprint to the application."
    } else { Write-Success "Certificate $thumbprint is already attached." }
}

$servicePrincipal = Get-MgServicePrincipal -Filter "appId eq '$($app.AppId)'" -Property Id,AppId,DisplayName
if (-not $servicePrincipal) { $servicePrincipal = New-MgServicePrincipal -AppId $app.AppId }

$envPath = Join-Path $PSScriptRoot '.env'
$existingSecret = Get-DotEnvValue -Path $envPath -Name 'CLIENT_SECRET'
$hasUsableAppPassword = @($app.PasswordCredentials | Where-Object {
    $_.EndDateTime -and ([datetime]$_.EndDateTime).ToUniversalTime() -gt (Get-Date).ToUniversalTime().AddDays(1)
}).Count -gt 0
$secretValue = ''
if ($RotateCredential -or (-not $certificate -and (-not $existingSecret -or -not $hasUsableAppPassword))) {
    $end = (Get-Date).AddDays($SecretExpirationDays)
    $secret = Add-MgApplicationPassword -ApplicationId $app.Id -PasswordCredential @{
        DisplayName = 'AI Readiness Tool Secret'; EndDateTime = $end
    }
    $secretValue = $secret.SecretText
    Write-Success "Created a new client secret expiring $($end.ToString('yyyy-MM-dd')). Existing credentials were not deleted."
} elseif ($existingSecret -and $hasUsableAppPassword) {
    Write-Success 'Kept the existing .env client secret; no credential was rotated.'
} else {
    Write-Success 'Certificate authentication is configured; no client secret was created.'
}

$assignments = @(Get-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $servicePrincipal.Id -All)
$missingConsent = Get-MissingApplicationConsent -Assignments $assignments -Desired $desired
if ($missingConsent.Count -gt 0) {
    Write-Info "Opening tenant admin consent for $($missingConsent.Count) missing application permission(s)..."
    $adminConsentUrl = "https://login.microsoftonline.com/$($context.TenantId)/adminconsent?client_id=$($app.AppId)"
    Start-Process $adminConsentUrl
    $null = Read-Host 'Accept the requested permissions in the browser, then press ENTER'
    Start-Sleep -Seconds 2
    $assignments = @(Get-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $servicePrincipal.Id -All)
    $missingConsent = Get-MissingApplicationConsent -Assignments $assignments -Desired $desired
} else {
    Write-Success 'All requested application permissions were already consented; no consent browser was opened.'
}
if ($missingConsent.Count -gt 0) {
    Write-Fail "$($missingConsent.Count) requested application permission(s) are still missing tenant-wide consent."
    Write-Warn 'Rerun setup after consent propagation or inspect Enterprise applications > Permissions.'
} else { Write-Success 'Verified every requested application role on the service principal.' }

$purviewRoleConfigurationComplete = $true
if ($Mode -eq 'Unattended' -and $missingConsent.Count -eq 0) {
    Write-Info 'Configuring least-privileged application role groups for the core Purview and Exchange read operations...'
    $purviewRoleConfigurationComplete = Set-ApplicationReadOnlyRoleGroup `
        -Connection Purview `
        -RoleGroupName 'AI Readiness Purview Read-Only' `
        -Cmdlets @('Get-DlpCompliancePolicy','Get-DlpComplianceRule','Get-Label','Get-LabelPolicy','Get-RetentionCompliancePolicy') `
        -AppId $app.AppId `
        -ServicePrincipalObjectId $servicePrincipal.Id `
        -DisplayName "$AppName - Purview"
    $exchangeRoleConfigurationComplete = Set-ApplicationReadOnlyRoleGroup `
        -Connection Exchange `
        -RoleGroupName 'AI Readiness Exchange Read-Only' `
        -Cmdlets @('Get-OrganizationConfig','Get-IRMConfiguration','Get-AdminAuditLogConfig') `
        -AppId $app.AppId `
        -ServicePrincipalObjectId $servicePrincipal.Id `
        -DisplayName "$AppName - Exchange"
    $purviewRoleConfigurationComplete = $purviewRoleConfigurationComplete -and $exchangeRoleConfigurationComplete
}

$organization = Get-MgOrganization -Property VerifiedDomains | Select-Object -First 1
$initialDomain = @($organization.VerifiedDomains | Where-Object { $_.IsInitial })[0].Name
Set-DotEnvValue -Path $envPath -Name 'TENANT_ID' -Value $context.TenantId
Set-DotEnvValue -Path $envPath -Name 'CLIENT_ID' -Value $app.AppId
if ($secretValue) { Set-DotEnvValue -Path $envPath -Name 'CLIENT_SECRET' -Value $secretValue }
if ($CertificatePath) { Set-DotEnvValue -Path $envPath -Name 'CERTIFICATE_PATH' -Value (Resolve-Path -LiteralPath $CertificatePath).Path }
if ($initialDomain) { Set-DotEnvValue -Path $envPath -Name 'PURVIEW_ORGANIZATION' -Value $initialDomain }
if ($CertificateThumbprint) {
    Set-DotEnvValue -Path $envPath -Name 'SHAREPOINT_CERTIFICATE_THUMBPRINT' -Value $CertificateThumbprint
    Set-DotEnvValue -Path $envPath -Name 'PURVIEW_CERTIFICATE_THUMBPRINT' -Value $CertificateThumbprint
}

$gitignorePath = Join-Path $PSScriptRoot '.gitignore'
if (-not (Test-Path -LiteralPath $gitignorePath)) { New-Item -ItemType File -Path $gitignorePath | Out-Null }
$gitignore = Get-Content -LiteralPath $gitignorePath -Raw
if ($gitignore -notmatch '(?m)^\.env$') { Add-Content -LiteralPath $gitignorePath -Value "`n.env" }
if ($gitignore -notmatch '(?m)^Reports/$') { Add-Content -LiteralPath $gitignorePath -Value "`nReports/" }

if ($PreviewCollectors -in @('PowerPlatform','All')) {
    Write-Warn 'Power Platform API RBAC is preview and was not assigned by this Graph setup session.'
    Write-Host '  Assignment authority required: Power Platform Administrator or Power Platform RBAC Administrator'
    Write-Host "  Role: Power Platform Reader (c886ad2e-27f7-4874-8381-5849b8d8a090)"
    Write-Host "  Principal object ID: $($servicePrincipal.Id)"
    Write-Host "  Scope: /tenants/$($context.TenantId)"
    Write-Host '  Verify the result with: python main.py --preview-collectors power-platform --check-connections'
    Write-Host '  If the caller lacks assignment authority, use a Manage > Inventory CSV export instead.'
}

Write-Host "`nSetup result" -ForegroundColor Cyan
Write-Host "  Application: $($app.DisplayName)"
Write-Host "  Application ID: $($app.AppId)"
Write-Host "  Mode: $Mode"
Write-Host "  Preview collectors: $PreviewCollectors"
Write-Host "  Environment file: $envPath"
if ($missingConsent.Count -gt 0) { Write-Host '  Consent: incomplete' -ForegroundColor Yellow } else { Write-Host '  Consent: verified' -ForegroundColor Green }
if ($Mode -eq 'Unattended') {
    if ($purviewRoleConfigurationComplete) { Write-Host '  Purview application roles: configured' -ForegroundColor Green }
    else { Write-Host '  Purview application roles: incomplete' -ForegroundColor Yellow }
}
Write-Host "`nNext: python main.py --check-connections"
Disconnect-MgGraph | Out-Null
if ($missingConsent.Count -gt 0 -or -not $purviewRoleConfigurationComplete) { exit 2 }

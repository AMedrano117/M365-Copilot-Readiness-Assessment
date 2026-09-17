<#
.SYNOPSIS
  Reconciles the application used by the AI readiness assessment.

.DESCRIPTION
  Standard installs the supported PowerShell modules and configures the stable
  collector permissions. Restricted uses an isolated application and omits
  SharePoint/Purview administration and directory-wide grant inventory.
  Unattended additionally validates and
  attaches a certificate and adds the SharePoint/Purview app-only permission packs.
  Preview permission packs are added only when explicitly selected.
#>

#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Standard','Unattended')][string]$Mode = 'Standard',
    [ValidateSet('Standard','Restricted')][string]$PermissionProfile = 'Standard',
    [ValidateSet('None','PowerPlatform','ShadowAI','NetworkAccess','All')]
    [string]$PreviewCollectors = 'None',
    [string]$CertificatePath = '',
    [string]$CertificateThumbprint = '',
    [string]$SharePointAdminUrl = '',
    [switch]$ConfirmBroadSharePointAccess,
    [switch]$RotateCredential,
    [switch]$PruneUnusedPermissions
)

$ErrorActionPreference = 'Stop'
$profileName = $PermissionProfile.ToLowerInvariant()
if ($profileName -eq 'restricted' -and ($Mode -ne 'Standard' -or $PreviewCollectors -ne 'None')) {
    throw 'Restricted rejects Unattended mode and preview collectors. Use Standard mode with -PreviewCollectors None.'
}
function ConvertTo-SharePointAdminUrl {
    param([string]$Value)
    $normalized = $Value.Trim()
    if ($normalized -notmatch '^https://[a-z0-9](?:[a-z0-9-]{0,55}[a-z0-9])?-admin\.sharepoint\.com/?$') {
        throw 'SharePointAdminUrl must be the commercial HTTPS SharePoint admin-center origin, for example https://contoso-admin.sharepoint.com, without a path, query, fragment, credentials, or explicit port.'
    }
    return $normalized.TrimEnd('/').ToLowerInvariant()
}
$explicitSharePointAdminUrl = $PSBoundParameters.ContainsKey('SharePointAdminUrl')
if ($explicitSharePointAdminUrl) {
    if ($profileName -eq 'restricted') {
        throw 'Restricted excludes SharePoint administration and does not accept -SharePointAdminUrl.'
    }
    # Validate operator input before module installation, authentication, or changes.
    $SharePointAdminUrl = ConvertTo-SharePointAdminUrl -Value $SharePointAdminUrl
}
$AppName = 'M365 Copilot Readiness Assessment Tool'
if ($profileName -eq 'restricted') { $AppName += ' - Restricted' }
$envFileName = if ($profileName -eq 'restricted') { '.env.restricted' } else { '.env' }
$envPath = Join-Path $PSScriptRoot $envFileName
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
    $pattern = '^\s*(?:export\s+)?' + [regex]::Escape($Name) + '\s*='
    $line = Get-Content -LiteralPath $Path -Encoding UTF8 | Where-Object { $_ -match $pattern } | Select-Object -Last 1
    if ($line) {
        $value = ($line -split '=', 2)[1].Trim()
        if ($value.StartsWith('"') -or $value.StartsWith("'")) {
            $quoted = [regex]::Match($value, '^([''"])(.*?)\1\s*(?:#.*)?$')
            if (-not $quoted.Success) { throw "Invalid quoted value for $Name in the selected environment file." }
            return $quoted.Groups[2].Value
        }
        return ($value -split '\s+#', 2)[0].TrimEnd()
    }
    return ''
}

function ConvertTo-DotEnvSnapshot {
    param([string[]]$Lines, [System.Collections.IDictionary]$Values)
    $found = @{}
    $updated = @(foreach ($line in $Lines) {
        $match = [regex]::Match($line, '^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*)$')
        if (-not $match.Success -or -not $Values.Contains($match.Groups[2].Value)) { $line; continue }
        $name = $match.Groups[2].Value
        $found[$name] = $true
        $value = [string]$Values[$name]
        if ($value -match '[\r\n]') { throw "Cannot save a multiline value for $name in the environment file." }
        # Keep operator comments and all unrelated settings. Quote literal values
        # when necessary; interpolation must not change a saved credential.
        if ($value -match '[\s#$"'']') {
            if ($value.Contains("'")) { throw "Cannot safely encode the value for $name in the environment file." }
            $value = "'" + $value + "'"
        }
        $oldValue = $match.Groups[4].Value
        $comment = [regex]::Match($oldValue, '^(?:"[^"]*"|''[^'']*''|[^''"]*?)(\s+#.*)$')
        $suffix = if ($comment.Success) { $comment.Groups[1].Value } else { '' }
        $match.Groups[1].Value + $name + $match.Groups[3].Value + $value + $suffix
    })
    foreach ($name in $Values.Keys) {
        if ($found.ContainsKey($name)) { continue }
        $value = [string]$Values[$name]
        if ($value -match '[\r\n]') { throw "Cannot save a multiline value for $name in the environment file." }
        if ($value -match '[\s#$"'']') {
            if ($value.Contains("'")) { throw "Cannot safely encode the value for $name in the environment file." }
            $value = "'" + $value + "'"
        }
        $updated += "${name}=${value}"
    }
    return ($updated -join [Environment]::NewLine) + [Environment]::NewLine
}

function New-CredentialStagingFile {
    param([string]$EnvironmentPath)
    $path = $EnvironmentPath + '.recovery.' + [guid]::NewGuid().ToString('N')
    $stream = [System.IO.File]::Open($path, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    $stream.Dispose()
    try {
        if ($env:OS -eq 'Windows_NT') {
            $acl = [System.Security.AccessControl.FileSecurity]::new()
            $acl.SetAccessRuleProtection($true, $false)
            $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
            $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($identity, 'FullControl', 'Allow'))
            $system = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18')
            $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($system, 'FullControl', 'Allow'))
            Set-Acl -LiteralPath $path -AclObject $acl -ErrorAction Stop
        } else {
            # PowerShell 7 on non-Windows uses .NET's user-only permissions.
            [System.IO.File]::SetUnixFileMode($path, [System.IO.UnixFileMode]::UserRead -bor [System.IO.UnixFileMode]::UserWrite)
        }
        return $path
    } catch {
        [System.IO.File]::Delete($path)
        throw 'Unable to prepare a private credential recovery file. Correct local file permissions before rerunning setup; no new credential has been created.'
    }
}

function Write-CredentialSnapshot {
    param([string]$Path, [string]$Contents)
    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($Contents)
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Truncate, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    try {
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    } finally { $stream.Dispose() }
}

function Save-CredentialEnvironment {
    param([string]$Path, [string]$StagingPath, [string]$Contents)
    try {
        Write-CredentialSnapshot -Path $StagingPath -Contents $Contents
    } catch {
        # A share/OneDrive/disk failure must not discard the one-time secret.
        # Keep recovery material private and outside console/error output.
        try {
            $fallback = New-CredentialStagingFile -EnvironmentPath (Join-Path ([System.IO.Path]::GetTempPath()) '.env.assessment')
            Write-CredentialSnapshot -Path $fallback -Contents $Contents
        } catch {
            throw 'Credential persistence failed in both the assessment folder and the local temporary folder. Setup stopped before consent. Do not continue collection; restore writable storage, inspect the application credentials, and rerun with -RotateCredential. Existing credentials were not revoked.'
        }
        throw "Environment save failed. The complete credential configuration is preserved in the private recovery file '$fallback'. Restore it to '$Path' before rerunning setup. Setup stopped before consent; no credential was revoked."
    }
    try {
        if ([System.IO.File]::Exists($Path)) {
            [System.IO.File]::Replace($StagingPath, $Path, [System.Management.Automation.Language.NullString]::Value)
        } else {
            [System.IO.File]::Move($StagingPath, $Path)
        }
    } catch {
        throw "Environment replacement failed; the previous file is unchanged. The complete credential configuration is preserved in the private recovery file '$StagingPath'. Restore it to '$Path' before rerunning setup. Setup stopped before consent; no credential was revoked."
    }
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
        catch { throw 'CertificatePath must be a readable, unprotected PFX containing its private key. A CurrentUser certificate thumbprint only supports workload PowerShell; Graph additionally requires a usable certificate file or client secret.' }
    }
    return $null
}

function Get-UniqueApplicationMatch {
    param([object[]]$Applications, [string]$DisplayName)
    if (@($Applications).Count -gt 1) {
        throw "Multiple applications named '$DisplayName' exist. Resolve the duplicate applications manually before rerunning setup; no application was selected."
    }
    return @($Applications) | Select-Object -First 1
}

function Get-SavedCredentialState {
    param([string]$Path, [string]$TenantId, [object]$Application, [switch]$SkipSecretValidation)
    $identityMatches = $Application -and
        (Get-DotEnvValue -Path $Path -Name 'TENANT_ID') -eq $TenantId -and
        (Get-DotEnvValue -Path $Path -Name 'CLIENT_ID') -eq [string]$Application.AppId
    $secret = ''
    $certificatePath = ''
    $certificatePassword = ''
    if ($identityMatches) {
        $secret = Get-DotEnvValue -Path $Path -Name 'CLIENT_SECRET'
        $certificatePath = Get-DotEnvValue -Path $Path -Name 'CERTIFICATE_PATH'
        $certificatePassword = Get-DotEnvValue -Path $Path -Name 'CERTIFICATE_PASSWORD'
    }
    $keyId = ''
    $expiresAt = ''
    $hasUsableSecret = $false
    if ($secret -and -not $SkipSecretValidation) {
        $now = (Get-Date).ToUniversalTime()
        $activePasswords = @($Application.PasswordCredentials | Where-Object {
            $_.EndDateTime -and ([datetime]$_.EndDateTime).ToUniversalTime() -gt $now -and
                (-not $_.StartDateTime -or ([datetime]$_.StartDateTime).ToUniversalTime() -le $now)
        })
        $savedKeyId = Get-DotEnvValue -Path $Path -Name 'CLIENT_SECRET_KEY_ID'
        $matchingPassword = $null
        if ($savedKeyId) {
            $matchingPassword = @($activePasswords | Where-Object { [string]$_.KeyId -eq $savedKeyId }) | Select-Object -First 1
            if (-not $matchingPassword) { Write-Warn 'The saved client secret key is expired, revoked, or not yet valid. Another valid application secret does not validate the saved credential.' }
        } elseif ($activePasswords.Count -eq 1) {
            $matchingPassword = $activePasswords[0]
        }
        if (($savedKeyId -and $matchingPassword) -or (-not $savedKeyId -and $activePasswords.Count -gt 0)) {
            $hasUsableSecret = Test-SavedClientSecret -TenantId $TenantId -ClientId $Application.AppId -Secret $secret
            if ($hasUsableSecret -and $matchingPassword) {
                $keyId = [string]$matchingPassword.KeyId
                $expiresAt = ([datetime]$matchingPassword.EndDateTime).ToUniversalTime().ToString('o')
                if (([datetime]$matchingPassword.EndDateTime).ToUniversalTime() -le $now.AddDays(14)) {
                    Write-Warn "The saved client secret expires $expiresAt. Rerun setup with -RotateCredential before that date."
                }
            } elseif ($hasUsableSecret) {
                Write-Warn 'The legacy saved secret authenticated, but multiple active keys prevent identifying its exact key ID or expiry. Rerun setup with -RotateCredential to record both values. No expiry is inferred from another credential.'
            }
        }
    }
    return @{
        IdentityMatches = [bool]$identityMatches
        Secret = $secret
        HasUsableSecret = [bool]$hasUsableSecret
        SecretKeyId = $keyId
        SecretExpiresAt = $expiresAt
        CertificatePath = $certificatePath
        CertificatePassword = $certificatePassword
    }
}

function Test-SavedClientSecret {
    param([string]$TenantId, [string]$ClientId, [string]$Secret)
    try {
        $response = Invoke-RestMethod -Method Post -Uri ('https://login.microsoftonline.com/' + [uri]::EscapeDataString($TenantId) + '/oauth2/v2.0/token') `
            -ContentType 'application/x-www-form-urlencoded' -TimeoutSec 30 -ErrorAction Stop -Body @{
                client_id = $ClientId; client_secret = $Secret
                grant_type = 'client_credentials'; scope = 'https://graph.microsoft.com/.default'
            }
        if (-not $response.access_token) { throw 'Token response contained no access token.' }
        return $true
    } catch {
        # Never emit the HTTP body/exception: providers and proxies can echo secrets.
        $errorDetails = $_.ErrorDetails.Message
        if (-not $errorDetails -and $_.Exception.Response) {
            try {
                $reader = [System.IO.StreamReader]::new($_.Exception.Response.GetResponseStream())
                try { $errorDetails = $reader.ReadToEnd() } finally { $reader.Dispose() }
            } catch { $errorDetails = '' }
        }
        try { $failure = $errorDetails | ConvertFrom-Json -ErrorAction Stop } catch { $failure = $null }
        if (@($failure.error_codes | Where-Object { $_ -in @(7000215, 7000222, 7000218) }).Count -gt 0) {
            Write-Warn 'Microsoft Entra rejected the saved client secret as invalid or expired; it will not be reused.'
            return $false
        }
        throw 'Could not verify the saved client secret with Microsoft Entra. Setup stopped before changing the application. Check network access and tenant authentication policy, then retry; use -RotateCredential only if a new credential is intended.'
    } finally { $response = $null; $Secret = $null }
}

function Get-ExcessApplicationAccess {
    param([object[]]$Requested, [object[]]$Assignments, [object[]]$Desired)
    $excess = @()
    foreach ($resource in @($Requested)) {
        foreach ($permission in @($resource.ResourceAccess)) {
            $allowed = @($Desired | Where-Object { [string]$_.ResourceAppId -eq [string]$resource.ResourceAppId } | ForEach-Object { $_.ResourceAccess } | Where-Object {
                [string]$_.Id -eq [string]$permission.Id -and [string]$_.Type -eq [string]$permission.Type
            })
            if (-not $allowed) { $excess += "Requested $($resource.ResourceAppId):$($permission.Id) ($($permission.Type))" }
        }
    }
    foreach ($assignment in @($Assignments)) {
        $allowed = @($Desired | Where-Object { [string]$_.ResourceObjectId -eq [string]$assignment.ResourceId } | ForEach-Object { $_.ResourceAccess } | Where-Object {
            [string]$_.Id -eq [string]$assignment.AppRoleId -and $_.Type -eq 'Role'
        })
        if (-not $allowed) { $excess += "Consented $($assignment.ResourceId):$($assignment.AppRoleId)" }
    }
    return $excess
}

function Get-GraphCertificateMetadata {
    param([string]$Path, [string]$Password)
    $validatorPath = Join-Path $PSScriptRoot 'Core\certificate_validation.py'
    if (-not (Test-Path -LiteralPath $validatorPath -PathType Leaf)) { throw 'The local Graph certificate validator is missing. Restore Core/certificate_validation.py before setup.' }
    $pythonCandidates = @()
    if ($env:VIRTUAL_ENV) { $pythonCandidates += Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe' }
    $pythonCandidates += Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if ($env:LOCALAPPDATA) { $pythonCandidates += Join-Path $env:LOCALAPPDATA 'venvs\m365-copilot-assessment\Scripts\python.exe' }
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) { $pythonCandidates += $pythonCommand.Source }
    foreach ($pythonPath in @($pythonCandidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { continue }
        $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = $pythonPath
        $startInfo.Arguments = '"' + $validatorPath + '"'
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardInput = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $startInfo.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
        $process = [System.Diagnostics.Process]::new()
        $process.StartInfo = $startInfo
        try {
            if (-not $process.Start()) { continue }
            # BaseStream works on Windows PowerShell 5.1, whose ProcessStartInfo
            # does not expose StandardInputEncoding. Encode explicitly for passwords
            # and paths containing characters outside the local console code page.
            $inputBytes = [System.Text.Encoding]::UTF8.GetBytes((@{path=$Path;password=$Password} | ConvertTo-Json -Compress) + "`n")
            $process.StandardInput.BaseStream.Write($inputBytes, 0, $inputBytes.Length)
            $process.StandardInput.BaseStream.Flush()
            $process.StandardInput.Close()
            $output = $process.StandardOutput.ReadToEnd()
            $null = $process.StandardError.ReadToEnd()
            $process.WaitForExit()
            # A missing Python dependency produces no JSON. Try another local environment
            # without printing Python diagnostics or any secret-bearing input.
            try { $metadata = $output | ConvertFrom-Json -ErrorAction Stop } catch { continue }
            if (-not $metadata) { continue }
            if (-not $metadata.valid) { throw $metadata.reason }
            return $metadata
        } finally { $process.Dispose() }
    }
    throw 'Graph certificate validation requires Python with the assessment dependencies. Activate the assessment environment or install requirements.txt, then rerun setup.'
}

function Assert-GraphCredentialConfiguration {
    param([object]$Certificate, [string]$GraphCertificatePath, [string]$GraphCertificatePassword, [object]$Application, [bool]$HasUsableSecret, [switch]$Rotate)
    if ($Certificate -and -not $GraphCertificatePath -and -not $HasUsableSecret -and -not $Rotate) {
        throw 'A certificate thumbprint alone cannot authenticate Python Graph collectors. Supply -CertificatePath with a usable private-key PFX or -RotateCredential to create a Graph client secret.'
    }
    if ($GraphCertificatePath) {
        $metadata = Get-GraphCertificateMetadata -Path $GraphCertificatePath -Password $GraphCertificatePassword
        if ($Certificate) {
            if ($metadata.thumbprint -ne $Certificate.Thumbprint) {
                throw 'The Graph certificate file does not match the explicitly selected setup certificate. Use matching certificate file/thumbprint values before setup.'
            }
        } else {
            $registered = @($Application.KeyCredentials | Where-Object {
                Test-RegisteredCertificate -KeyCredential $_ -Thumbprint $metadata.thumbprint
            })
            if (-not $registered) {
                throw 'The saved Graph certificate is not registered as a currently valid key on the matching application. Supply the matching -CertificateThumbprint or -CertificatePath to attach it, or correct the environment file.'
            }
        }
    }
}

function Test-RegisteredCertificate {
    param([object]$KeyCredential, [string]$Thumbprint)
    if (-not $KeyCredential.Key) { return $false }
    try {
        # Inspect the actual public certificate. A caller-supplied CustomKeyIdentifier
        # alone does not prove that the registered key matches the private key file.
        $bytes = if ($KeyCredential.Key -is [string]) { [Convert]::FromBase64String($KeyCredential.Key) } else { [byte[]]$KeyCredential.Key }
        $publicCertificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new([byte[]]$bytes)
        $matches = $publicCertificate.Thumbprint -eq $Thumbprint
        $publicCertificate.Dispose()
        $now = (Get-Date).ToUniversalTime()
        return $matches -and $KeyCredential.StartDateTime -and ([datetime]$KeyCredential.StartDateTime).ToUniversalTime() -le $now -and
            $KeyCredential.EndDateTime -and ([datetime]$KeyCredential.EndDateTime).ToUniversalTime() -gt $now
    } catch { return $false }
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

function Set-ApplicationWorkloadRoleGroup {
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
        Write-Warn "$Connection grants complete workload roles: $($roleNames -join ', '). These roles may include write cmdlets; the role-group name does not restrict their capabilities."

        $roleGroup = Get-RoleGroup -Identity $RoleGroupName -ErrorAction SilentlyContinue
        if (-not $roleGroup) {
            $roleGroup = New-RoleGroup -Name $RoleGroupName -Roles $roleNames -Description 'Workload roles exposing assessment read cmdlets; complete roles may include write capabilities.' -ErrorAction Stop
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
        Write-Warn "$Connection application role group could not be configured: $($_.Exception.Message)"
        Write-Warn 'Run python main.py --check-connections after role propagation; a Role missing result identifies this gap.'
        return $false
    } finally {
        try { Disconnect-ExchangeOnline -Confirm:$false -ErrorAction SilentlyContinue | Out-Null } catch { }
    }
}

Write-Host "`n=============================================================================" -ForegroundColor Cyan
Write-Host " AI Readiness Assessment - Application Reconciliation" -ForegroundColor Cyan
Write-Host "=============================================================================" -ForegroundColor Cyan

$certificate = Get-SetupCertificate -Path $CertificatePath -Thumbprint $CertificateThumbprint
if ($certificate -and ($certificate.NotAfter.ToUniversalTime() -le (Get-Date).ToUniversalTime() -or $certificate.NotBefore.ToUniversalTime() -gt (Get-Date).ToUniversalTime())) {
    throw 'The selected certificate is expired or not yet valid.'
}
if ($CertificatePath -and $CertificateThumbprint) {
    $fileCertificate = Get-SetupCertificate -Path $CertificatePath
    if ($fileCertificate.Thumbprint -ne $certificate.Thumbprint) { throw 'CertificatePath and CertificateThumbprint must identify the same certificate.' }
}
if ($Mode -eq 'Unattended' -and -not $certificate) {
    throw 'Unattended mode requires -CertificatePath or -CertificateThumbprint.'
}

. "$PSScriptRoot\Check-PSModules.ps1"
if (-not (Test-RequiredModules -ScriptType 'Setup' -InstallMissing)) { exit 1 }
if ($profileName -eq 'standard') {
    if (-not (Test-RequiredModules -ScriptType 'Purview' -InstallMissing)) { exit 1 }
    $windowsPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (Test-Path -LiteralPath $windowsPowerShell) {
        & $windowsPowerShell -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\Check-PSModules.ps1" -ScriptType SharePoint
        if ($LASTEXITCODE -ne 0) { throw 'SharePoint Online module setup failed in Windows PowerShell.' }
    } elseif (-not (Test-RequiredModules -ScriptType 'SharePoint' -InstallMissing)) {
        throw 'SharePoint Online module setup failed in the available PowerShell host.'
    }
}

Import-Module Microsoft.Graph.Authentication -Force
Import-Module Microsoft.Graph.Applications -Force
Import-Module Microsoft.Graph.Identity.DirectoryManagement -Force

Write-Info 'Connecting to Microsoft Graph for application administration...'
Connect-MgGraph -Scopes @(
    'Application.ReadWrite.All','Organization.Read.All'
) -ContextScope Process -NoWelcome
$context = Get-MgContext
if (-not $context -or -not $context.TenantId) { throw 'Microsoft Graph authentication did not return a tenant.' }

$configuredSharePointAdminUrl = ''
if ($profileName -eq 'standard') {
    if ($explicitSharePointAdminUrl) {
        $configuredSharePointAdminUrl = $SharePointAdminUrl
    } elseif ((Get-DotEnvValue -Path $envPath -Name 'TENANT_ID') -eq $context.TenantId) {
        try {
            $savedSharePointAdminUrl = Get-DotEnvValue -Path $envPath -Name 'SHAREPOINT_ADMIN_URL'
            if ($savedSharePointAdminUrl) {
                $configuredSharePointAdminUrl = ConvertTo-SharePointAdminUrl -Value $savedSharePointAdminUrl
            }
        } catch {
            Write-Warn 'The saved SHAREPOINT_ADMIN_URL is invalid and will be cleared. Configure the actual SharePoint admin-center URL for this tenant.'
        }
    }
}

$existingApps = @(Get-MgApplication -Filter "displayName eq '$AppName'" -Property Id,AppId,DisplayName,RequiredResourceAccess,PasswordCredentials,KeyCredentials)
$app = Get-UniqueApplicationMatch -Applications $existingApps -DisplayName $AppName
$savedCredential = Get-SavedCredentialState -Path $envPath -TenantId $context.TenantId -Application $app -SkipSecretValidation:$RotateCredential
$graphCertificatePath = if ($CertificatePath) { (Resolve-Path -LiteralPath $CertificatePath).Path } else { $savedCredential.CertificatePath }
$graphCertificatePassword = if ($CertificatePath) { '' } else { $savedCredential.CertificatePassword }
$certificateApplication = $app
if (($graphCertificatePath -or $certificate) -and $app) {
    # Graph returns raw public keys only when keyCredentials is selected for a
    # single application, rather than the preceding filtered collection lookup.
    $certificateApplication = Get-MgApplication -ApplicationId $app.Id -Property Id,AppId,KeyCredentials
}
Assert-GraphCredentialConfiguration -Certificate $certificate -GraphCertificatePath $graphCertificatePath -GraphCertificatePassword $graphCertificatePassword -Application $certificateApplication -HasUsableSecret $savedCredential.HasUsableSecret -Rotate:$RotateCredential

# Stable permission identifiers used by endpoint contract tests and setup audits.
# Runtime resolution still uses permission names so the manifest is readable.
$permissionReference = @(
    @{ Id = "9e640839-a198-48fb-8b9a-013fd6f6cbcd"; Name = "Policy.Read.PermissionGrant" },
    @{ Id = "e30060de-caa5-4331-99d3-6ac6c966a9a4"; Name = "NetworkAccess.Read.All" }
)

$registryPath = Join-Path $PSScriptRoot 'collector-registry.json'
if (-not (Test-Path -LiteralPath $registryPath)) { throw "Collector registry not found: $registryPath" }
$registryDocument = Get-Content -LiteralPath $registryPath -Raw | ConvertFrom-Json
$collectorRegistry = $registryDocument.collectors
$profile = $registryDocument.permission_profiles.$profileName
if (-not $profile) { throw "Permission profile $PermissionProfile was not found in the collector registry." }
$enabledCollectors = @($collectorRegistry | Where-Object {
    ($profile.excluded_collectors -notcontains $_.id) -and (
    $_.default_setup -or
    ($Mode -eq 'Unattended' -and $_.setup_when -eq 'Unattended') -or
    ($_.preview_pack -and ($PreviewCollectors -eq 'All' -or $_.preview_pack -eq $PreviewCollectors)))
})
$resourceIds = @{}
foreach ($property in $registryDocument.resource_app_ids.PSObject.Properties) {
    $resourceIds[$property.Name] = $property.Value
}
$permissionsByResource = @{}
foreach ($collector in $enabledCollectors) {
    foreach ($property in @($collector.permission_resources.PSObject.Properties)) {
        if (-not $resourceIds.ContainsKey($property.Name)) { continue }
        if (-not $permissionsByResource.ContainsKey($property.Name)) { $permissionsByResource[$property.Name] = @() }
        $excluded = @($profile.excluded_permissions.($property.Name))
        $permissionsByResource[$property.Name] += @($property.Value | Where-Object { $excluded -notcontains $_ })
    }
}

$graphPermissions = @($permissionsByResource.graph | Sort-Object -Unique)
if ($graphPermissions -contains 'Directory.Read.All') {
    Write-Warn 'Standard includes Directory.Read.All for delegated consent-grant inventory. Existing applications may require new administrator consent; Restricted omits this inventory and permission.'
}
$desired = @()
foreach ($resourceName in @($permissionsByResource.Keys | Sort-Object)) {
    $permissionNames = @($permissionsByResource[$resourceName] | Sort-Object -Unique)
    if ($permissionNames.Count -gt 0) {
        $desired += Get-ResourceAccessByName -ResourceAppId $resourceIds[$resourceName] -PermissionNames $permissionNames
    }
}

if ($Mode -eq 'Unattended') {
    Write-Warn 'Unattended mode adds SharePoint Sites.FullControl.All because SharePoint administrative PowerShell does not support client-secret authentication.'
    if (-not $ConfirmBroadSharePointAccess) {
        $answer = Read-Host 'Type YES to approve Sites.FullControl.All for this app; the assessment collector uses it only for read operations'
        if ($answer -cne 'YES') { throw 'SharePoint app-only permission was not approved. Rerun Standard mode for delegated browser authentication.' }
    }
}

$servicePrincipal = $null
$assignments = @()
if ($app) {
    $principals = @(Get-MgServicePrincipal -Filter "appId eq '$($app.AppId)'" -Property Id,AppId,DisplayName)
    if ($principals.Count -gt 1) { throw 'Multiple service principals match the application ID; resolve the duplicate objects before setup.' }
    $servicePrincipal = $principals | Select-Object -First 1
    if ($servicePrincipal) {
        $assignments = @(Get-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $servicePrincipal.Id -All)
    }
    $excess = @(Get-ExcessApplicationAccess -Requested $app.RequiredResourceAccess -Assignments $assignments -Desired $desired)
    if ($excess.Count -gt 0) {
        Write-Warn "Existing access outside the selected setup permissions: $($excess -join '; ')"
        Write-Warn 'Review App registrations > API permissions AND Enterprise applications > Permissions. Manifest pruning does not revoke prior admin consent. Remove excess requests and grants manually, or use a clean dedicated application.'
        if ($profileName -eq 'restricted') { throw 'Restricted setup stopped before changing the application: excess requested or consented permissions remain.' }
    }
    Write-Success "Reusing application $($app.AppId); it will not be deleted or recreated."
} else {
    $app = New-MgApplication -DisplayName $AppName -SignInAudience 'AzureADMyOrg' -PublicClient @{ RedirectUris = @('http://localhost') }
    Write-Success "Created application $($app.AppId)."
}

if ($PruneUnusedPermissions) {
    Write-Warn 'PruneUnusedPermissions changes only requested API permissions. Existing service-principal grants are not revoked.'
}
$requiredResourceAccess = Merge-ResourceAccess -Existing $app.RequiredResourceAccess -Desired $desired -Prune:$PruneUnusedPermissions
Update-MgApplication -ApplicationId $app.Id -RequiredResourceAccess $requiredResourceAccess
Write-Success "Reconciled permissions for $($enabledCollectors.Count) selected collectors from collector-registry.json."

if ($certificate) {
    $thumbprint = $certificate.Thumbprint
    $existingKeyCredentials = if ($certificateApplication) { @($certificateApplication.KeyCredentials) } else { @($app.KeyCredentials) }
    $alreadyAttached = @($existingKeyCredentials | Where-Object {
        Test-RegisteredCertificate -KeyCredential $_ -Thumbprint $thumbprint
    }).Count -gt 0
    if (-not $alreadyAttached) {
        $newKey = @{
            Type = 'AsymmetricX509Cert'; Usage = 'Verify'; Key = $certificate.RawData
            DisplayName = 'AI readiness assessment'; StartDateTime = $certificate.NotBefore.ToUniversalTime()
            EndDateTime = $certificate.NotAfter.ToUniversalTime()
        }
        Update-MgApplication -ApplicationId $app.Id -KeyCredentials @($existingKeyCredentials + $newKey)
        Write-Success "Attached certificate $thumbprint to the application."
    } else { Write-Success "Certificate $thumbprint is already attached." }
}

if (-not $servicePrincipal) { $servicePrincipal = New-MgServicePrincipal -AppId $app.AppId }

$organization = Get-MgOrganization -Property VerifiedDomains | Select-Object -First 1
$initialDomain = @($organization.VerifiedDomains | Where-Object { $_.IsInitial })[0].Name
$environmentValues = [ordered]@{}
if (-not $savedCredential.IdentityMatches) {
    foreach ($credentialName in @('CLIENT_SECRET','CLIENT_SECRET_KEY_ID','CLIENT_SECRET_EXPIRES_AT','CERTIFICATE_PATH','CERTIFICATE_PASSWORD','SHAREPOINT_CERTIFICATE_PATH','SHAREPOINT_CERTIFICATE_PASSWORD','SHAREPOINT_CERTIFICATE_THUMBPRINT','PURVIEW_CERTIFICATE_PATH','PURVIEW_CERTIFICATE_PASSWORD','PURVIEW_CERTIFICATE_THUMBPRINT')) {
        if (Get-DotEnvValue -Path $envPath -Name $credentialName) { $environmentValues[$credentialName] = '' }
    }
}
$environmentValues['TENANT_ID'] = $context.TenantId
$environmentValues['CLIENT_ID'] = $app.AppId
$environmentValues['PERMISSION_PROFILE'] = $profileName
if ($profileName -eq 'standard') { $environmentValues['SHAREPOINT_ADMIN_URL'] = $configuredSharePointAdminUrl }
$environmentValues['CLIENT_SECRET'] = if ($savedCredential.HasUsableSecret) { $savedCredential.Secret } else { '' }
$environmentValues['CLIENT_SECRET_KEY_ID'] = $savedCredential.SecretKeyId
$environmentValues['CLIENT_SECRET_EXPIRES_AT'] = $savedCredential.SecretExpiresAt
if ($CertificatePath) {
    $environmentValues['CERTIFICATE_PATH'] = $graphCertificatePath
    $environmentValues['CERTIFICATE_PASSWORD'] = ''
}
if ($initialDomain) { $environmentValues['PURVIEW_ORGANIZATION'] = $initialDomain }
if ($CertificateThumbprint) {
    $environmentValues['SHAREPOINT_CERTIFICATE_THUMBPRINT'] = $CertificateThumbprint
    $environmentValues['PURVIEW_CERTIFICATE_THUMBPRINT'] = $CertificateThumbprint
}
# Prepare local persistence before issuing a one-time secret. The whole environment
# is staged privately and atomically installed before consent or workload RBAC.
$environmentLines = if (Test-Path -LiteralPath $envPath) { @(Get-Content -LiteralPath $envPath -Encoding UTF8) } else { @('# AI readiness assessment credentials', '# Never commit this file.') }
$null = ConvertTo-DotEnvSnapshot -Lines $environmentLines -Values $environmentValues
$gitignorePath = Join-Path $PSScriptRoot '.gitignore'
if (-not (Test-Path -LiteralPath $gitignorePath)) { New-Item -ItemType File -Path $gitignorePath | Out-Null }
$gitignore = [string](Get-Content -LiteralPath $gitignorePath -Raw)
foreach ($ignorePattern in @('.env', '.env.restricted', '.env.*.recovery.*', 'Reports/')) {
    if ($gitignore -notmatch ('(?m)^' + [regex]::Escape($ignorePattern) + '$')) { Add-Content -LiteralPath $gitignorePath -Value "`n$ignorePattern" }
}
# The Standard recovery name is .env.recovery.<id>; Restricted is
# .env.restricted.recovery.<id>. Both stay excluded even in a new checkout.
if ($gitignore -notmatch '(?m)^\.env\.recovery\.\*$') { Add-Content -LiteralPath $gitignorePath -Value "`n.env.recovery.*" }
$stagingPath = New-CredentialStagingFile -EnvironmentPath $envPath
if ($RotateCredential -or (-not $graphCertificatePath -and -not $savedCredential.HasUsableSecret)) {
    $end = (Get-Date).ToUniversalTime().AddDays($SecretExpirationDays)
    try {
        $secret = Add-MgApplicationPassword -ApplicationId $app.Id -PasswordCredential @{
            DisplayName = 'AI Readiness Tool Secret'; EndDateTime = $end
        }
    } catch {
        [System.IO.File]::Delete($stagingPath)
        throw
    }
    $environmentValues['CLIENT_SECRET'] = $secret.SecretText
    $environmentValues['CLIENT_SECRET_KEY_ID'] = [string]$secret.KeyId
    $environmentValues['CLIENT_SECRET_EXPIRES_AT'] = if ($secret.EndDateTime) { ([datetime]$secret.EndDateTime).ToUniversalTime().ToString('o') } else { $end.ToString('o') }
    # Never reread local configuration after Graph returns the one-time secret.
    # A transient file/OneDrive failure cannot prevent creating its recovery copy.
    $snapshot = ConvertTo-DotEnvSnapshot -Lines $environmentLines -Values $environmentValues
    Save-CredentialEnvironment -Path $envPath -StagingPath $stagingPath -Contents $snapshot
    Write-Success "Created a new client secret expiring $($end.ToString('yyyy-MM-dd')). Existing credentials were not deleted."
} else {
    $snapshot = ConvertTo-DotEnvSnapshot -Lines $environmentLines -Values $environmentValues
    Save-CredentialEnvironment -Path $envPath -StagingPath $stagingPath -Contents $snapshot
    if ($savedCredential.HasUsableSecret) {
        Write-Success "Verified and kept the existing $envFileName client secret for the matching tenant/application; no credential was rotated."
    } else { Write-Success 'Certificate authentication is configured; no client secret was created.' }
}
Write-Success "Saved the complete configuration atomically to $envFileName before consent and workload role setup. If setup is interrupted, rerun with the same profile/mode without -RotateCredential to reuse the saved secret and finish consent/workload roles."

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
$excessAfterConsent = @(Get-ExcessApplicationAccess -Requested $requiredResourceAccess -Assignments $assignments -Desired $desired)
if ($profileName -eq 'restricted' -and $excessAfterConsent.Count -gt 0) {
    throw 'Restricted setup detected excess application grants after consent. Review Enterprise applications > Permissions and remove excess grants manually; setup did not revoke any access.'
}

$purviewRoleConfigurationComplete = $true
if ($Mode -eq 'Unattended' -and $missingConsent.Count -eq 0) {
    Write-Warn 'Configuring workload roles that expose assessment read cmdlets. Complete roles can grant write capabilities; review their role entries before customer use.'
    $purviewRoleConfigurationComplete = Set-ApplicationWorkloadRoleGroup `
        -Connection Purview `
        -RoleGroupName 'AI Readiness Purview Read-Only' `
        -Cmdlets @('Get-DlpCompliancePolicy','Get-DlpComplianceRule','Get-Label','Get-LabelPolicy','Get-RetentionCompliancePolicy') `
        -AppId $app.AppId `
        -ServicePrincipalObjectId $servicePrincipal.Id `
        -DisplayName "$AppName - Purview"
    $exchangeRoleConfigurationComplete = Set-ApplicationWorkloadRoleGroup `
        -Connection Exchange `
        -RoleGroupName 'AI Readiness Exchange Read-Only' `
        -Cmdlets @('Get-OrganizationConfig','Get-IRMConfiguration','Get-AdminAuditLogConfig') `
        -AppId $app.AppId `
        -ServicePrincipalObjectId $servicePrincipal.Id `
        -DisplayName "$AppName - Exchange"
    $purviewRoleConfigurationComplete = $purviewRoleConfigurationComplete -and $exchangeRoleConfigurationComplete
}

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
Write-Host "  Service principal object ID: $($servicePrincipal.Id)"
Write-Host "  Mode: $Mode"
Write-Host "  Permission profile: $PermissionProfile"
Write-Host "  Preview collectors: $PreviewCollectors"
Write-Host "  Environment file: $envPath"
if ($profileName -eq 'standard') {
    if ($configuredSharePointAdminUrl) {
        Write-Host "  SharePoint admin URL: $configuredSharePointAdminUrl (syntax checked; tenant connection not verified)"
    } else {
        Write-Warn 'SHAREPOINT_ADMIN_URL is blank. SharePoint administrative checks need an operator-confirmed URL; core identity and security assessment can still run.'
        Write-Host '  Open Microsoft 365 admin center > Admin centers > SharePoint and copy the origin of the actual admin-center URL.'
        Write-Host '  Set SHAREPOINT_ADMIN_URL in the selected environment file, or rerun setup with -SharePointAdminUrl https://<actual-prefix>-admin.sharepoint.com.'
    }
}
if ($missingConsent.Count -gt 0) { Write-Host '  Consent: incomplete' -ForegroundColor Yellow } else { Write-Host '  Consent: verified' -ForegroundColor Green }
if ($Mode -eq 'Unattended') {
    if ($purviewRoleConfigurationComplete) { Write-Host '  Purview application roles: configured' -ForegroundColor Green }
    else { Write-Host '  Purview application roles: incomplete' -ForegroundColor Yellow }
}
if ($profileName -eq 'restricted') {
    Write-Host "`nNext: python main.py --env-file .env.restricted --permission-profile restricted --check-connections"
} else {
    Write-Host "`nNext: python main.py --check-connections"
}
Write-Host "`nAfter the engagement, follow docs/CLEANUP.md to archive evidence and retire dedicated assessment access."
$cleanupPreview = ".\cleanup-service-principal.ps1 -PermissionProfile $PermissionProfile -TenantId $($context.TenantId) -ClientId $($app.AppId)"
if ($Mode -eq 'Unattended') { $cleanupPreview += " -IncludeWorkloadRbac -ServicePrincipalObjectId $($servicePrincipal.Id)" }
Write-Host "  Cleanup preview (no deletions): $cleanupPreview"
Disconnect-MgGraph | Out-Null
if ($missingConsent.Count -gt 0 -or -not $purviewRoleConfigurationComplete) { exit 2 }

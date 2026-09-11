# PowerShell data collector for Purview compliance endpoints
# Collects deployment data via Security & Compliance cmdlets and pipes to main.py
# Usage: .\collect_purview_data.ps1 [-DataOnly]

param(
    [switch]$DataOnly,  # If set, outputs JSON only (for Python subprocess invocation)
    [ValidateSet("Auto", "Fresh", "Force", "Prompt")]
    [string]$AuthMode = "Auto",
    [string]$TenantId = "",
    [string]$ClientId = "",
    [string]$Organization = "",
    [string]$CertificatePath = "",
    [string]$CertificatePassword = "",
    [string]$CertificateThumbprint = "",
    [switch]$IncludeSpecialized,
    [switch]$ConnectionOnly
)

# Check required PowerShell modules
. "$PSScriptRoot\Check-PSModules.ps1"
if (-not (Test-RequiredModules -ScriptType "Purview" -Quiet:$DataOnly)) {
    exit 1
}

# Helper function to write output only in interactive mode
function Write-Progress2 {
    param([string]$Message, [string]$Color = "White", [switch]$NoNewline)
    if (-not $DataOnly) {
        if ($NoNewline) {
            Write-Host $Message -ForegroundColor $Color -NoNewline
        } else {
            Write-Host $Message -ForegroundColor $Color
        }
    }
}

function New-CollectionFailure {
    param(
        [System.Management.Automation.ErrorRecord]$ErrorRecord,
        [string]$RequiredRole,
        [bool]$Optional = $false
    )

    $technicalError = [string]$ErrorRecord.Exception.Message
    $category = "collection_error"
    $reason = "Microsoft Purview did not return this data source."

    if ($technicalError -match '(?i)access.*denied|unauthori[sz]ed|forbidden|permission|not authorized') {
        $category = "permission_denied"
        $reason = "The signed-in user is not authorized to read this Microsoft Purview data source."
    } elseif ($technicalError -match '(?i)not recognized|not available|could not find|cmdlet') {
        $category = "feature_or_cmdlet_unavailable"
        $reason = "The Purview cmdlet was unavailable. The feature might not be licensed or enabled in this tenant, or the installed ExchangeOnlineManagement module might not expose it."
    } elseif ($technicalError -match '(?i)licen[cs]e|subscription|not enabled|not provisioned') {
        $category = "feature_unavailable"
        $reason = "The Purview feature might not be licensed, enabled, or provisioned in this tenant."
    }

    return @{
        available = $false
        availability_status = "unavailable"
        error_category = $category
        reason = $reason
        required_role = $RequiredRole
        optional = $Optional
        technical_error = $technicalError
    }
}

function New-CollectionSuccess {
    param(
        [int]$Count,
        [bool]$Optional = $false
    )

    return @{
        available = $true
        availability_status = "available"
        count = $Count
        optional = $Optional
    }
}

if (-not $DataOnly) {
    Write-Progress2 "================================================================" -ForegroundColor Cyan
    Write-Progress2 "Purview Data Collection + M365 Readiness Assessment" -ForegroundColor Cyan
    Write-Progress2 "================================================================" -ForegroundColor Cyan
    Write-Progress2 ""
}

# Step 1: Connect to M365 Services
if (-not $DataOnly) {
    Write-Progress2 "[1/4] Connecting to M365 services..." -ForegroundColor Yellow
    Write-Progress2 "      A browser window will open for authentication" -ForegroundColor Gray
}
try {
    Import-Module ExchangeOnlineManagement -ErrorAction Stop

    function Write-AuthStatus {
        param(
            [string]$Type,
            [string]$Service,
            [string]$Details = ""
        )

        $payload = "${Type}:$Service"
        if ($Details) {
            $payload = "${payload}:$Details"
        }

        [Console]::Error.WriteLine($payload)
    }

    function Disconnect-PurviewSessions {
        try {
            Disconnect-ExchangeOnline -Confirm:$false -ErrorAction SilentlyContinue -InformationAction SilentlyContinue | Out-Null
        } catch {
            # Ignore disconnect errors - a fresh connection attempt will validate the state.
        }
    }

    function Get-ExistingPurviewConnectionState {
        $state = @{
            IPPS = $false
            EXO  = $false
        }

        try {
            if (Get-Command Get-ConnectionInformation -ErrorAction SilentlyContinue) {
                $connections = @(Get-ConnectionInformation -ErrorAction SilentlyContinue)
                foreach ($connection in $connections) {
                    $connectionUri = [string]$connection.ConnectionUri
                    $connectionState = [string]$connection.State

                    if ($connectionState -and $connectionState -ne 'Connected') {
                        continue
                    }

                    if ($connectionUri -match 'compliance\.protection\.outlook\.com') {
                        $state.IPPS = $true
                    } elseif ($connectionUri -match 'outlook\.office365\.com|outlook\.com') {
                        $state.EXO = $true
                    }
                }

                if ($state.IPPS -or $state.EXO) {
                    return $state
                }
            }
        } catch {
            # Fall back to lightweight cmdlet probes below.
        }

        try {
            Get-DlpCompliancePolicy -ErrorAction Stop | Out-Null
            $state.IPPS = $true
        } catch {
            # Not connected, or the probe cmdlet is unavailable in this session.
        }

        try {
            Get-OrganizationConfig -ErrorAction Stop | Out-Null
            $state.EXO = $true
        } catch {
            # Not connected, or the probe cmdlet is unavailable in this session.
        }

        return $state
    }
    
    # Check if already connected to avoid duplicate prompts
    $ippsConnected = $false
    $exoConnected = $false
    $reuseExistingConnections = $true
    $forceFreshAuthentication = $AuthMode -in @("Fresh", "Force")
    $appOnlyAuthentication = $ClientId -and $Organization -and ($CertificatePath -or $CertificateThumbprint)

    if ($appOnlyAuthentication) {
        $certificateParameters = @{ AppId = $ClientId; Organization = $Organization; ErrorAction = 'Stop'; WarningAction = 'SilentlyContinue' }
        if ($CertificateThumbprint) {
            $certificateParameters['CertificateThumbprint'] = $CertificateThumbprint
        } else {
            $certificateParameters['CertificateFilePath'] = $CertificatePath
            if ($CertificatePassword) {
                $certificateParameters['CertificatePassword'] = ConvertTo-SecureString $CertificatePassword -AsPlainText -Force
            }
        }
        Connect-IPPSSession @certificateParameters | Out-Null
        Connect-ExchangeOnline @certificateParameters -ShowBanner:$false | Out-Null
        $ippsConnected = $true
        $exoConnected = $true
        Write-AuthStatus -Type "AUTH_REUSED" -Service "Purview application certificate"
    }

    if ($forceFreshAuthentication -and -not $appOnlyAuthentication) {
        Write-Progress2 "      - Auth mode: forcing a fresh Microsoft 365 sign-in" -ForegroundColor Yellow
        Disconnect-PurviewSessions
    }

    if (-not $appOnlyAuthentication) {
        $existingConnections = Get-ExistingPurviewConnectionState
        $ippsConnected = [bool]$existingConnections.IPPS
        $exoConnected = [bool]$existingConnections.EXO
    }

    if ($ippsConnected) {
        Write-Progress2 "      - Security & Compliance: Already connected" -ForegroundColor Cyan
    }

    if ($exoConnected) {
        Write-Progress2 "      - Exchange Online: Already connected" -ForegroundColor Cyan
    }

    if (($ippsConnected -or $exoConnected) -and -not $forceFreshAuthentication) {
        Write-Progress2 "      - Reusing existing authenticated Microsoft 365 session(s)" -ForegroundColor Cyan
    }
    
    # Connect only if not already connected
    if ($ippsConnected -and $reuseExistingConnections) {
        Write-AuthStatus -Type "AUTH_REUSED" -Service "Security & Compliance"
    }

    if (-not $ippsConnected) {
        Write-AuthStatus -Type "AUTH_PROMPT" -Service "Security & Compliance" -Details "Purview portal and compliance cmdlets"
        Write-Progress2 "      - Connecting to Security & Compliance..." -NoNewline
        Connect-IPPSSession -DisableWAM -ErrorAction Stop -WarningAction SilentlyContinue | Out-Null
        Write-Progress2 " OK" -ForegroundColor Green
        # Output to stderr so Python can display in real-time
        Write-AuthStatus -Type "AUTH_COMPLETE" -Service "Security & Compliance"
    }
    
    if ($exoConnected -and $reuseExistingConnections) {
        Write-AuthStatus -Type "AUTH_REUSED" -Service "Exchange Online"
    }

    if (-not $exoConnected) {
        # Re-check if Exchange was auto-connected by IPPSSession
        try {
            Get-OrganizationConfig -ErrorAction Stop | Out-Null
            $exoConnected = $true
            # If auto-connected, send message immediately
            Write-AuthStatus -Type "AUTH_COMPLETE" -Service "Exchange Online"
        } catch {
            # Not auto-connected, need to connect manually
            Write-AuthStatus -Type "AUTH_PROMPT" -Service "Exchange Online" -Details "Exchange Online cmdlets and organization settings"
            Write-Progress2 "      - Connecting to Exchange Online..." -NoNewline
            Connect-ExchangeOnline -DisableWAM -ShowBanner:$false -ErrorAction Stop -WarningAction SilentlyContinue | Out-Null
            Write-Progress2 " OK" -ForegroundColor Green
            # Output to stderr so Python can display in real-time
            Write-AuthStatus -Type "AUTH_COMPLETE" -Service "Exchange Online"
        }
    }
    
    if ($ippsConnected -and $exoConnected) {
        Write-Progress2 "      OK Using existing connections" -ForegroundColor Green
    }
} catch {
    $connectionError = $_.Exception.Message
    if ($DataOnly) {
        [Console]::Error.WriteLine("AUTH_ERROR:Purview:$connectionError")
    }
    Write-Progress2 "      ERROR Connection failed: $connectionError" -ForegroundColor Red
    Write-Progress2 ""
    Write-Progress2 "Please ensure you have:" -ForegroundColor Yellow
    Write-Progress2 "  - ExchangeOnlineManagement module installed" -ForegroundColor Yellow
    Write-Progress2 "  - Appropriate permissions for Security & Compliance" -ForegroundColor Yellow
    exit 1
}

if ($ConnectionOnly) {
    try {
        $null = Get-DlpCompliancePolicy -ErrorAction Stop | Select-Object -First 1
        $null = Get-OrganizationConfig -ErrorAction Stop
        @{ ready = $true; source = 'Security and Compliance PowerShell' } | ConvertTo-Json -Compress
        Disconnect-PurviewSessions
        exit 0
    } catch {
        [Console]::Error.WriteLine("CONNECTION_ERROR:Purview:$($_.Exception.Message)")
        Disconnect-PurviewSessions
        exit 2
    }
}

# Step 2: Collect Purview data
if (-not $DataOnly) {
    Write-Progress2 ""
    Write-Progress2 "[2/4] Collecting Purview compliance data..." -ForegroundColor Yellow
}

$purviewData = @{}

# Collect DLP Policies
Write-Progress2 "      - DLP Compliance Policies..." -NoNewline
try {
    $dlpPolicies = @(Get-DlpCompliancePolicy -ErrorAction Stop | Select-Object Name, DisplayName, Mode, Enabled, Workload, ExchangeLocation, SharePointLocation, OneDriveLocation, TeamsLocation, EndpointDlpLocation, PowerBILocation, PolicyRBACScopes, Comment)
    $purviewData['dlp_policies'] = New-CollectionSuccess -Count $dlpPolicies.Count
    $purviewData['dlp_policies']['policies'] = $dlpPolicies
    Write-Progress2 " $($dlpPolicies.Count) found" -ForegroundColor Green
} catch {
    Write-Progress2 " Unavailable" -ForegroundColor Red
    $purviewData['dlp_policies'] = New-CollectionFailure -ErrorRecord $_ -RequiredRole "View-Only DLP Compliance Management or Compliance Administrator"
    $purviewData['dlp_policies']['policies'] = @()
}

# Collect DLP Rules. Policies alone do not show what data is detected or what happens on a match.
Write-Progress2 "      - DLP Compliance Rules..." -NoNewline
try {
    $dlpRules = @(Get-DlpComplianceRule -ErrorAction Stop | Select-Object Name, DisplayName, ParentPolicyName, Disabled, Priority, Mode, Workload, ContentContainsSensitiveInformation, ContentIsShared, AccessScope, BlockAccess, BlockAccessScope, NotifyUser, NotifyAllowOverride, GenerateAlert, GenerateIncidentReport, ReportSeverityLevel, RestrictWebGrounding, EndpointDlpRestrictions, EncryptRMSTemplate, Quarantine, AdvancedRule)
    $purviewData['dlp_rules'] = New-CollectionSuccess -Count $dlpRules.Count
    $purviewData['dlp_rules']['rules'] = $dlpRules
    Write-Progress2 " $($dlpRules.Count) found" -ForegroundColor Green
} catch {
    Write-Progress2 " Unavailable" -ForegroundColor Red
    $purviewData['dlp_rules'] = New-CollectionFailure -ErrorRecord $_ -RequiredRole "View-Only DLP Compliance Management or Compliance Administrator"
    $purviewData['dlp_rules']['rules'] = @()
}

# Collect Sensitivity Labels
Write-Progress2 "      - Sensitivity Labels..." -NoNewline
try {
    $labels = @(Get-Label -ErrorAction Stop | Select-Object Name, DisplayName, Tooltip, Enabled)
    $purviewData['sensitivity_labels'] = New-CollectionSuccess -Count $labels.Count
    $purviewData['sensitivity_labels']['labels'] = $labels
    Write-Progress2 " $($labels.Count) found" -ForegroundColor Green
} catch {
    Write-Progress2 " Unavailable" -ForegroundColor Red
    $purviewData['sensitivity_labels'] = New-CollectionFailure -ErrorRecord $_ -RequiredRole "Information Protection or Compliance Administrator"
    $purviewData['sensitivity_labels']['labels'] = @()
}

# Collect Retention Policies
Write-Progress2 "      - Retention Compliance Policies..." -NoNewline
try {
    $retentionPolicies = @(Get-RetentionCompliancePolicy -ErrorAction Stop | Select-Object Name, Enabled, Type)
    $purviewData['retention_policies'] = New-CollectionSuccess -Count $retentionPolicies.Count
    $purviewData['retention_policies']['policies'] = $retentionPolicies
    Write-Progress2 " $($retentionPolicies.Count) found" -ForegroundColor Green
} catch {
    Write-Progress2 " Unavailable" -ForegroundColor Red
    $purviewData['retention_policies'] = New-CollectionFailure -ErrorRecord $_ -RequiredRole "View-Only Retention Management or Compliance Administrator"
    $purviewData['retention_policies']['policies'] = @()
}

# Collect Label Policies
Write-Progress2 "      - Sensitivity Label Policies..." -NoNewline
try {
    $labelPolicies = @(Get-LabelPolicy -ErrorAction Stop -WarningAction SilentlyContinue | Select-Object Name, Enabled, Mode)
    $purviewData['label_policies'] = New-CollectionSuccess -Count $labelPolicies.Count
    $purviewData['label_policies']['policies'] = $labelPolicies
    Write-Progress2 " $($labelPolicies.Count) found" -ForegroundColor Green
} catch {
    Write-Progress2 " Unavailable" -ForegroundColor Red
    $purviewData['label_policies'] = New-CollectionFailure -ErrorRecord $_ -RequiredRole "Information Protection or Compliance Administrator"
    $purviewData['label_policies']['policies'] = @()
}

# Specialized Purview workloads use separate roles and can create misleading
# permission warnings during a normal DLP/governance assessment. They are
# collected only when explicitly requested.
if ($IncludeSpecialized) {
# Collect Insider Risk Policies
Write-Progress2 "      - Insider Risk Policies..." -NoNewline
try {
    $insiderRiskPolicies = @(Get-InsiderRiskPolicy -ErrorAction Stop | Select-Object Name, Enabled, InsiderRiskScenario)
    $purviewData['insider_risk_policies'] = New-CollectionSuccess -Count $insiderRiskPolicies.Count -Optional $true
    $purviewData['insider_risk_policies']['policies'] = $insiderRiskPolicies
    Write-Progress2 " $($insiderRiskPolicies.Count) found" -ForegroundColor Green
} catch {
    Write-Progress2 " Unavailable (optional)" -ForegroundColor Yellow
    $purviewData['insider_risk_policies'] = New-CollectionFailure -ErrorRecord $_ -RequiredRole "Insider Risk Management or Compliance Administrator" -Optional $true
    $purviewData['insider_risk_policies']['policies'] = @()
}

# Collect Communication Compliance Policies
Write-Progress2 "      - Communication Compliance..." -NoNewline
try {
    $commCompPolicies = @(Get-SupervisoryReviewPolicyV2 -ErrorAction Stop | Select-Object Name, Enabled, SamplingRate)
    $purviewData['communication_compliance'] = New-CollectionSuccess -Count $commCompPolicies.Count -Optional $true
    $purviewData['communication_compliance']['policies'] = $commCompPolicies
    Write-Progress2 " $($commCompPolicies.Count) found" -ForegroundColor Green
} catch {
    Write-Progress2 " Unavailable (optional)" -ForegroundColor Yellow
    $purviewData['communication_compliance'] = New-CollectionFailure -ErrorRecord $_ -RequiredRole "Communication Compliance or Compliance Administrator" -Optional $true
    $purviewData['communication_compliance']['policies'] = @()
}

# Collect Information Barriers
Write-Progress2 "      - Information Barriers..." -NoNewline
try {
    $ibPolicies = @(Get-InformationBarrierPolicy -ErrorAction Stop | Select-Object Name, State, AssignedSegment)
    $purviewData['information_barriers'] = New-CollectionSuccess -Count $ibPolicies.Count -Optional $true
    $purviewData['information_barriers']['policies'] = $ibPolicies
    Write-Progress2 " $($ibPolicies.Count) found" -ForegroundColor Green
} catch {
    Write-Progress2 " Unavailable (optional)" -ForegroundColor Yellow
    $purviewData['information_barriers'] = New-CollectionFailure -ErrorRecord $_ -RequiredRole "IB Compliance Management or Compliance Administrator" -Optional $true
    $purviewData['information_barriers']['policies'] = @()
}

# Collect eDiscovery Cases
Write-Progress2 "      - eDiscovery Cases..." -NoNewline
if ($appOnlyAuthentication) {
    Write-Progress2 " Not requested (app-only)" -ForegroundColor Cyan
    $purviewData['ediscovery_cases'] = @{
        available = $false
        availability_status = "not_requested"
        error_category = "not_supported_app_only"
        reason = "eDiscovery case collection is optional and is not requested during app-only collection."
        required_role = "eDiscovery Administrator with delegated authentication"
        optional = $true
        cases = @()
    }
} else { try {
    $cases = @(Get-ComplianceCase -ErrorAction Stop | Select-Object Name, Status, CaseType)
    $purviewData['ediscovery_cases'] = New-CollectionSuccess -Count $cases.Count -Optional $true
    $purviewData['ediscovery_cases']['cases'] = $cases
    Write-Progress2 " $($cases.Count) found" -ForegroundColor Green
} catch {
    Write-Progress2 " Unavailable (optional)" -ForegroundColor Yellow
    $purviewData['ediscovery_cases'] = New-CollectionFailure -ErrorRecord $_ -RequiredRole "eDiscovery Administrator for tenant-wide case visibility" -Optional $true
    $purviewData['ediscovery_cases']['cases'] = @()
} }

} else {
    foreach ($source in @(
        @{ Key = 'insider_risk_policies'; Items = 'policies' },
        @{ Key = 'communication_compliance'; Items = 'policies' },
        @{ Key = 'information_barriers'; Items = 'policies' },
        @{ Key = 'ediscovery_cases'; Items = 'cases' }
    )) {
        $purviewData[$source.Key] = @{
            available = $false
            availability_status = 'not_requested'
            error_category = 'optional_not_requested'
            reason = 'Specialized Purview workload collection was not selected.'
            required_role = ''
            optional = $true
        }
        $purviewData[$source.Key][$source.Items] = @()
    }
}

# Collect Organization Configuration
Write-Progress2 "      - Organization Config..." -NoNewline
try {
    $orgConfig = Get-OrganizationConfig -ErrorAction Stop | Select-Object CustomerLockBoxEnabled, AuditDisabled, IsDehydrated
    $purviewData['org_config'] = @{ available = $true; availability_status = "available"; data = $orgConfig; optional = $false }
    $lockboxStatus = if ($orgConfig.CustomerLockBoxEnabled) { "Lockbox: Enabled" } else { "Lockbox: Disabled" }
    Write-Progress2 " $lockboxStatus" -ForegroundColor Green
} catch {
    Write-Progress2 " Failed" -ForegroundColor Red
    $purviewData['org_config'] = New-CollectionFailure -ErrorRecord $_ -RequiredRole "View-Only Organization Management or Compliance Administrator"
}

# Collect IRM Configuration
Write-Progress2 "      - Azure RMS Configuration..." -NoNewline
try {
    $irmConfig = Get-IRMConfiguration -ErrorAction Stop | Select-Object AzureRMSLicensingEnabled, InternalLicensingEnabled
    $purviewData['irm_config'] = @{ available = $true; availability_status = "available"; data = $irmConfig; optional = $false }
    $rmsStatus = if ($irmConfig.AzureRMSLicensingEnabled) { "RMS: Enabled" } else { "RMS: Disabled" }
    Write-Progress2 " $rmsStatus" -ForegroundColor Green
} catch {
    Write-Progress2 " Failed" -ForegroundColor Red
    $purviewData['irm_config'] = New-CollectionFailure -ErrorRecord $_ -RequiredRole "View-Only Organization Management or Compliance Administrator"
}

# Collect Audit Configuration
Write-Progress2 "      - Audit Configuration..." -NoNewline
try {
    $auditConfig = Get-AdminAuditLogConfig -ErrorAction Stop | Select-Object UnifiedAuditLogIngestionEnabled, AdminAuditLogEnabled
    $purviewData['audit_config'] = @{ available = $true; availability_status = "available"; data = $auditConfig; optional = $false }
    $auditStatus = if ($auditConfig.UnifiedAuditLogIngestionEnabled) { "Unified Audit: Enabled" } else { "Unified Audit: Disabled" }
    Write-Progress2 " $auditStatus" -ForegroundColor $(if ($auditConfig.UnifiedAuditLogIngestionEnabled) { "Green" } else { "Yellow" })
} catch {
    Write-Progress2 " Failed" -ForegroundColor Red
    $purviewData['audit_config'] = New-CollectionFailure -ErrorRecord $_ -RequiredRole "Audit Reader or Compliance Administrator"
}

$sourceLabels = [ordered]@{
    dlp_policies = "DLP Policies"
    dlp_rules = "DLP Rules"
    sensitivity_labels = "Sensitivity Labels"
    retention_policies = "Retention Policies"
    label_policies = "Sensitivity Label Policies"
    insider_risk_policies = "Insider Risk Policies"
    communication_compliance = "Communication Compliance"
    information_barriers = "Information Barriers"
    ediscovery_cases = "eDiscovery Cases"
    org_config = "Organization Configuration"
    irm_config = "Rights Management Configuration"
    audit_config = "Audit Configuration"
}
$requiredFailures = @()
$optionalFailures = @()
foreach ($entry in $sourceLabels.GetEnumerator()) {
    $state = $purviewData[$entry.Key]
    if ($state -and -not $state.available -and $state.availability_status -ne 'not_requested') {
        $failure = @{
            source = $entry.Value
            category = $state.error_category
            reason = $state.reason
            required_role = $state.required_role
        }
        if ($state.optional) { $optionalFailures += $failure } else { $requiredFailures += $failure }
    }
}
$purviewData['collection_summary'] = @{
    required_failures = $requiredFailures
    optional_failures = $optionalFailures
}

# Step 3: Serialize and output data
if (-not $DataOnly) {
    Write-Progress2 ""
    if ($requiredFailures.Count -gt 0) {
        Write-Progress2 "WARNING: Required Purview evidence unavailable: $(($requiredFailures | ForEach-Object { $_.source }) -join ', ')" -ForegroundColor Yellow
        Write-Progress2 "    The report will identify these specific evidence gaps." -ForegroundColor Gray
        Write-Progress2 ""
    }
    if ($optionalFailures.Count -gt 0) {
        Write-Progress2 "INFO: Optional Purview enrichment unavailable: $(($optionalFailures | ForEach-Object { $_.source }) -join ', ')" -ForegroundColor Cyan
        Write-Progress2 "    This does not mean DLP collection failed and does not by itself limit core AI readiness." -ForegroundColor Gray
        Write-Progress2 ""
    }
    Write-Progress2 "[3/4] Running Python assessment tool..." -ForegroundColor Yellow
    Write-Progress2 ""
    Write-Progress2 ""
}

# Convert to JSON
$jsonData = $purviewData | ConvertTo-Json -Depth 10 -Compress

if ($DataOnly) {
    # DataOnly mode: Output JSON to stdout (for Python subprocess)
    Write-Output $jsonData
} else {
    # Interactive mode: Pass data to Python via stdin
    $env:PURVIEW_DATA_SOURCE = "stdin"
    $jsonData | python main.py
    
    Write-Progress2 ""
    Write-Progress2 "================================================================" -ForegroundColor Cyan
    Write-Progress2 "Assessment Complete!" -ForegroundColor Green
    Write-Progress2 "================================================================" -ForegroundColor Cyan
}

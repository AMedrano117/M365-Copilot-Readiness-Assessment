# PowerShell data collector for Purview compliance endpoints
# Collects deployment data via Security & Compliance cmdlets and pipes to main.py
# Usage: .\collect_purview_data.ps1 [-DataOnly]

param(
    [switch]$DataOnly,  # If set, outputs JSON only (for Python subprocess invocation)
    [ValidateSet("Auto", "Fresh", "Force", "Prompt", "Skip")]
    [string]$AuthMode = "Auto",
    [string]$TenantId = "",
    [string]$ClientId = "",
    [string]$Organization = "",
    [string]$CertificatePath = "",
    [string]$CertificatePassword = "",
    [string]$CertificateThumbprint = "",
    [switch]$IncludeSpecialized,
    [switch]$ConnectionOnly,
    # Read secrets (certificate password, workload access tokens) as one JSON
    # line from stdin so they never appear in the process command line.
    [switch]$SecretsFromStdin
)

$ExchangeAccessToken = ''
$ComplianceAccessToken = ''
if ($SecretsFromStdin) {
    $secretLine = [Console]::In.ReadLine()
    if ($secretLine) {
        $secretPayload = $secretLine | ConvertFrom-Json
        if ($secretPayload.certificate_password) { $CertificatePassword = [string]$secretPayload.certificate_password }
        if ($secretPayload.exchange_access_token) { $ExchangeAccessToken = [string]$secretPayload.exchange_access_token }
        if ($secretPayload.compliance_access_token) { $ComplianceAccessToken = [string]$secretPayload.compliance_access_token }
    }
    Remove-Variable -Name secretLine, secretPayload -ErrorAction SilentlyContinue
}

# Check required PowerShell modules
. "$PSScriptRoot\Check-PSModules.ps1"
if (-not (Test-RequiredModules -ScriptType "Purview" -Quiet:$DataOnly)) {
    exit 1
}

# Live progress for the Python orchestrator, on stderr in DataOnly runs:
# PROGRESS:TOTAL:<n>, PROGRESS:STEP:<dataset starting>, PROGRESS:NOTE:<text>, PROGRESS:END.
function Write-CollectionStep {
    param([string]$Kind, [string]$Value = '')
    if ($DataOnly) { [Console]::Error.WriteLine("PROGRESS:${Kind}:$Value") }
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
    $applicationSession = $script:ippsAuthentication -in @('application_token', 'application_certificate')

    if ($technicalError -match '(?i)access.*denied|unauthori[sz]ed|forbidden|permission|not authorized|is not recognized as (?:the |a )?name of a cmdlet' -and $applicationSession -and $technicalError -notmatch '(?i)licen[cs]e') {
        # In an application session a missing cmdlet usually means the app has
        # no workload role exposing it, not that the feature is unlicensed.
        $category = "role_missing"
        $reason = "The assessment application's workload role does not expose this cmdlet. Security Reader covers DLP, sensitivity labels and audit configuration; assign a broader read-only role such as Global Reader (setup-service-principal.ps1 -WorkloadRbac GlobalReader) or View-Only role groups (-WorkloadRbac RoleGroups), then rerun."
    } elseif ($technicalError -match '(?i)access.*denied|unauthori[sz]ed|forbidden|permission|not authorized') {
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

    Write-CollectionStep NOTE 'connecting to Security & Compliance and Exchange Online'
    # Authentication tiers, applied per session (Security & Compliance and
    # Exchange Online): 1) application access token acquired by the Python
    # orchestrator from the existing app credential, 2) application
    # certificate, 3) delegated browser sign-in unless AuthMode is Skip.
    $ippsConnected = $false
    $exoConnected = $false
    $script:ippsAuthentication = ''
    $script:exoAuthentication = ''
    $reuseExistingConnections = $true
    $forceFreshAuthentication = $AuthMode -in @("Fresh", "Force")
    $allowDelegated = $AuthMode -ne 'Skip'
    $certificateConfigured = $ClientId -and $Organization -and ($CertificatePath -or $CertificateThumbprint)

    if ($Organization -and $ComplianceAccessToken) {
        try {
            Connect-IPPSSession -AccessToken $ComplianceAccessToken -Organization $Organization -ErrorAction Stop -WarningAction SilentlyContinue | Out-Null
            $ippsConnected = $true
            $script:ippsAuthentication = 'application_token'
        } catch {
            [Console]::Error.WriteLine("AUTH_FALLBACK:Security & Compliance:application token:$($_.Exception.Message)")
        }
    }
    if ($Organization -and $ExchangeAccessToken) {
        try {
            Connect-ExchangeOnline -AccessToken $ExchangeAccessToken -Organization $Organization -ShowBanner:$false -ErrorAction Stop -WarningAction SilentlyContinue | Out-Null
            $exoConnected = $true
            $script:exoAuthentication = 'application_token'
        } catch {
            [Console]::Error.WriteLine("AUTH_FALLBACK:Exchange Online:application token:$($_.Exception.Message)")
        }
    }
    if ($script:ippsAuthentication -eq 'application_token' -or $script:exoAuthentication -eq 'application_token') {
        Write-AuthStatus -Type "AUTH_REUSED" -Service "Purview application token"
    }

    if ($certificateConfigured -and (-not $ippsConnected -or -not $exoConnected)) {
        $certificateParameters = @{ AppId = $ClientId; Organization = $Organization; ErrorAction = 'Stop'; WarningAction = 'SilentlyContinue' }
        if ($CertificateThumbprint) {
            $certificateParameters['CertificateThumbprint'] = $CertificateThumbprint
        } else {
            $certificateParameters['CertificateFilePath'] = $CertificatePath
            if ($CertificatePassword) {
                $certificateParameters['CertificatePassword'] = ConvertTo-SecureString $CertificatePassword -AsPlainText -Force
            }
        }
        try {
            if (-not $ippsConnected) {
                Connect-IPPSSession @certificateParameters | Out-Null
                $ippsConnected = $true
                $script:ippsAuthentication = 'application_certificate'
            }
            if (-not $exoConnected) {
                Connect-ExchangeOnline @certificateParameters -ShowBanner:$false | Out-Null
                $exoConnected = $true
                $script:exoAuthentication = 'application_certificate'
            }
            Write-AuthStatus -Type "AUTH_REUSED" -Service "Purview application certificate"
        } catch {
            if (-not $allowDelegated) { throw }
            [Console]::Error.WriteLine("AUTH_FALLBACK:Purview:application certificate:$($_.Exception.Message)")
        }
    }

    $appOnlyAuthentication = $script:ippsAuthentication -in @('application_token', 'application_certificate')

    if ((-not $ippsConnected -or -not $exoConnected) -and -not $allowDelegated) {
        if (-not $ippsConnected -and -not $exoConnected) {
            throw 'Application authentication was not available for Purview (no usable application token or certificate) and delegated sign-in is disabled (-AuthMode Skip).'
        }
    }

    if ($forceFreshAuthentication -and -not $appOnlyAuthentication -and $allowDelegated) {
        Write-Progress2 "      - Auth mode: forcing a fresh Microsoft 365 sign-in" -ForegroundColor Yellow
        Disconnect-PurviewSessions
    }

    if (-not $ippsConnected -and -not $exoConnected -and $allowDelegated) {
        $existingConnections = Get-ExistingPurviewConnectionState
        $ippsConnected = [bool]$existingConnections.IPPS
        $exoConnected = [bool]$existingConnections.EXO
        if ($ippsConnected) { $script:ippsAuthentication = 'delegated_browser' }
        if ($exoConnected) { $script:exoAuthentication = 'delegated_browser' }
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

    if (-not $ippsConnected -and $allowDelegated) {
        Write-AuthStatus -Type "AUTH_PROMPT" -Service "Security & Compliance" -Details "Purview portal and compliance cmdlets"
        Write-Progress2 "      - Connecting to Security & Compliance..." -NoNewline
        Connect-IPPSSession -DisableWAM -ErrorAction Stop -WarningAction SilentlyContinue | Out-Null
        $ippsConnected = $true
        $script:ippsAuthentication = 'delegated_browser'
        Write-Progress2 " OK" -ForegroundColor Green
        # Output to stderr so Python can display in real-time
        Write-AuthStatus -Type "AUTH_COMPLETE" -Service "Security & Compliance"
    }

    if ($exoConnected -and $reuseExistingConnections) {
        Write-AuthStatus -Type "AUTH_REUSED" -Service "Exchange Online"
    }

    if (-not $exoConnected -and $allowDelegated) {
        # Re-check if Exchange was auto-connected by IPPSSession
        try {
            Get-OrganizationConfig -ErrorAction Stop | Out-Null
            $exoConnected = $true
            $script:exoAuthentication = $script:ippsAuthentication
            # If auto-connected, send message immediately
            Write-AuthStatus -Type "AUTH_COMPLETE" -Service "Exchange Online"
        } catch {
            # Not auto-connected, need to connect manually
            Write-AuthStatus -Type "AUTH_PROMPT" -Service "Exchange Online" -Details "Exchange Online cmdlets and organization settings"
            Write-Progress2 "      - Connecting to Exchange Online..." -NoNewline
            Connect-ExchangeOnline -DisableWAM -ShowBanner:$false -ErrorAction Stop -WarningAction SilentlyContinue | Out-Null
            $exoConnected = $true
            $script:exoAuthentication = 'delegated_browser'
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
    # Exchange and Purview sessions import only the cmdlets the connected
    # identity's roles allow, so report which datasets are exposed instead of
    # failing on the first missing cmdlet.
    $representativeCmdlets = [ordered]@{
        'Get-DlpCompliancePolicy' = 'DLP policies'
        'Get-DlpComplianceRule' = 'DLP rules'
        'Get-Label' = 'sensitivity labels'
        'Get-LabelPolicy' = 'label policies'
        'Get-RetentionCompliancePolicy' = 'retention policies'
        'Get-AdminAuditLogConfig' = 'audit configuration'
        'Get-OrganizationConfig' = 'organization configuration'
        'Get-IRMConfiguration' = 'rights management configuration'
    }
    $exposedDatasets = @()
    $missingDatasets = @()
    $probeCmdlet = ''
    foreach ($cmdletName in $representativeCmdlets.Keys) {
        if (Get-Command $cmdletName -ErrorAction SilentlyContinue) {
            $exposedDatasets += $representativeCmdlets[$cmdletName]
            if (-not $probeCmdlet) { $probeCmdlet = $cmdletName }
        } else {
            $missingDatasets += $representativeCmdlets[$cmdletName]
        }
    }
    try {
        if (-not $probeCmdlet) {
            throw 'role_missing: the connected identity has no role that exposes the assessment cmdlets.'
        }
        $null = & $probeCmdlet -ErrorAction Stop -WarningAction SilentlyContinue | Select-Object -First 1
        @{
            ready = $true
            source = 'Security and Compliance PowerShell'
            authentication = $script:ippsAuthentication
            exchange_authentication = $script:exoAuthentication
            exposed = @($exposedDatasets)
            missing = @($missingDatasets)
        } | ConvertTo-Json -Compress
        Disconnect-PurviewSessions
        exit 0
    } catch {
        [Console]::Error.WriteLine("CONNECTION_ERROR:Purview:$($_.Exception.Message)")
        Disconnect-PurviewSessions
        exit 2
    }
}

# Step 2: Collect Purview data
Write-CollectionStep TOTAL $(if ($IncludeSpecialized) { 12 } else { 8 })
if (-not $DataOnly) {
    Write-Progress2 ""
    Write-Progress2 "[2/4] Collecting Purview compliance data..." -ForegroundColor Yellow
}

$purviewData = @{
    collected_at = [DateTime]::UtcNow.ToString('o')
    authentication = $script:ippsAuthentication
    exchange_authentication = $script:exoAuthentication
}

# Collect DLP Policies
Write-CollectionStep STEP 'DLP policies'
Write-Progress2 "      - DLP Compliance Policies..." -NoNewline
try {
    $dlpPolicies = @(Get-DlpCompliancePolicy -ErrorAction Stop | Select-Object Name, DisplayName, Identity, Guid, Mode, Enabled, Workload, Locations, EnforcementPlanes, ExchangeLocation, ExchangeLocationException, SharePointLocation, SharePointLocationException, OneDriveLocation, OneDriveLocationException, TeamsLocation, TeamsLocationException, EndpointDlpLocation, PowerBILocation, PolicyRBACScopes, Comment, WhenCreatedUTC, WhenChangedUTC)
    $purviewData['dlp_policies'] = New-CollectionSuccess -Count $dlpPolicies.Count
    $purviewData['dlp_policies']['policies'] = $dlpPolicies
    Write-Progress2 " $($dlpPolicies.Count) found" -ForegroundColor Green
} catch {
    Write-Progress2 " Unavailable" -ForegroundColor Red
    $purviewData['dlp_policies'] = New-CollectionFailure -ErrorRecord $_ -RequiredRole "View-Only DLP Compliance Management or Compliance Administrator"
    $purviewData['dlp_policies']['policies'] = @()
}

# Collect DLP Rules. Policies alone do not show what data is detected or what happens on a match.
Write-CollectionStep STEP 'DLP rules'
Write-Progress2 "      - DLP Compliance Rules..." -NoNewline
try {
    $dlpRules = @(Get-DlpComplianceRule -ErrorAction Stop | Select-Object Name, DisplayName, Identity, ParentPolicyName, ParentPolicyId, Disabled, Priority, Mode, Workload, ContentContainsSensitiveInformation, ContentIsShared, AccessScope, BlockAccess, BlockAccessScope, NotifyUser, NotifyAllowOverride, GenerateAlert, GenerateIncidentReport, ReportSeverityLevel, RestrictAccess, RestrictWebGrounding, EndpointDlpRestrictions, EncryptRMSTemplate, Quarantine, AdvancedRule)
    $purviewData['dlp_rules'] = New-CollectionSuccess -Count $dlpRules.Count
    $purviewData['dlp_rules']['rules'] = $dlpRules
    Write-Progress2 " $($dlpRules.Count) found" -ForegroundColor Green
} catch {
    Write-Progress2 " Unavailable" -ForegroundColor Red
    $purviewData['dlp_rules'] = New-CollectionFailure -ErrorRecord $_ -RequiredRole "View-Only DLP Compliance Management or Compliance Administrator"
    $purviewData['dlp_rules']['rules'] = @()
}

# Collect Sensitivity Labels
Write-CollectionStep STEP 'sensitivity labels'
Write-Progress2 "      - Sensitivity Labels..." -NoNewline
try {
    $labels = @(Get-Label -ErrorAction Stop | Select-Object Name, DisplayName, Identity, Guid, ImmutableId, ParentId, Tooltip, Enabled, ContentType, Priority, LabelActions, Settings, WhenCreatedUTC, WhenChangedUTC)
    $purviewData['sensitivity_labels'] = New-CollectionSuccess -Count $labels.Count
    $purviewData['sensitivity_labels']['labels'] = $labels
    Write-Progress2 " $($labels.Count) found" -ForegroundColor Green
} catch {
    Write-Progress2 " Unavailable" -ForegroundColor Red
    $purviewData['sensitivity_labels'] = New-CollectionFailure -ErrorRecord $_ -RequiredRole "Information Protection or Compliance Administrator"
    $purviewData['sensitivity_labels']['labels'] = @()
}

# Collect Retention Policies
Write-CollectionStep STEP 'retention policies'
Write-Progress2 "      - Retention Compliance Policies..." -NoNewline
try {
    $retentionPolicies = @(Get-RetentionCompliancePolicy -ErrorAction Stop | Select-Object Name, Identity, Guid, Enabled, Type, Mode, Workload, DistributionStatus, Applications, Locations, ExchangeLocation, ExchangeLocationException, SharePointLocation, SharePointLocationException, OneDriveLocation, OneDriveLocationException, ModernGroupLocation, ModernGroupLocationException, TeamsChannelLocation, TeamsChannelLocationException, TeamsChatLocation, TeamsChatLocationException, PolicyRBACScopes, RestrictiveRetention, WhenCreatedUTC, WhenChangedUTC)
    $purviewData['retention_policies'] = New-CollectionSuccess -Count $retentionPolicies.Count
    $purviewData['retention_policies']['policies'] = $retentionPolicies
    Write-Progress2 " $($retentionPolicies.Count) found" -ForegroundColor Green
} catch {
    Write-Progress2 " Unavailable" -ForegroundColor Red
    $purviewData['retention_policies'] = New-CollectionFailure -ErrorRecord $_ -RequiredRole "View-Only Retention Management or Compliance Administrator"
    $purviewData['retention_policies']['policies'] = @()
}

# Collect Label Policies
Write-CollectionStep STEP 'label publishing policies'
Write-Progress2 "      - Sensitivity Label Policies..." -NoNewline
try {
    $labelPolicies = @(Get-LabelPolicy -ErrorAction Stop -WarningAction SilentlyContinue | Select-Object Name, Identity, Guid, Enabled, Mode, Labels, Settings, ExchangeLocation, ExchangeLocationException, ModernGroupLocation, ModernGroupLocationException, PolicyRBACScopes, WhenCreatedUTC, WhenChangedUTC,
        # Retain assignments and exclusions for investigation, with compatible counts.
        @{ Name = 'ExchangeLocationCount'; Expression = { @($_.ExchangeLocation | Where-Object { $_ }).Count } },
        @{ Name = 'ExchangeLocationExceptionCount'; Expression = { @($_.ExchangeLocationException | Where-Object { $_ }).Count } },
        @{ Name = 'ModernGroupLocationCount'; Expression = { @($_.ModernGroupLocation | Where-Object { $_ }).Count } },
        @{ Name = 'LabelCount'; Expression = { @($_.Labels | Where-Object { $_ }).Count } })
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
Write-CollectionStep STEP 'insider risk policies'
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
Write-CollectionStep STEP 'communication compliance'
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
Write-CollectionStep STEP 'information barriers'
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
Write-CollectionStep STEP 'eDiscovery cases'
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
Write-CollectionStep STEP 'organization configuration'
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
Write-CollectionStep STEP 'rights management configuration'
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
Write-CollectionStep STEP 'audit configuration'
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

Write-CollectionStep END
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
$credentialTypes = @{
    application_token = 'workload_app_token'
    application_certificate = 'workload_app_certificate'
    delegated_browser = 'delegated_user'
}
$exchangeSources = @('org_config', 'irm_config', 'audit_config')
foreach ($key in @($sourceLabels.Keys)) {
    $state = $purviewData[$key]
    if (-not $state) { continue }
    $sessionAuthentication = if ($exchangeSources -contains $key) { $script:exoAuthentication } else { $script:ippsAuthentication }
    if ($sessionAuthentication -and $credentialTypes.ContainsKey($sessionAuthentication)) {
        $state['credential_type'] = $credentialTypes[$sessionAuthentication]
        $state['auth_path_id'] = switch ($sessionAuthentication) {
            'application_token' { 'purview_ps_token' }
            'application_certificate' { 'purview_ps_certificate' }
            default { 'purview_ps_delegated' }
        }
    }
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

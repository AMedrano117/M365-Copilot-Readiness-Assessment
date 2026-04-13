# PowerShell data collector for Purview compliance endpoints
# Collects deployment data via Security & Compliance cmdlets and pipes to main.py
# Usage: .\collect_purview_data.ps1 [-DataOnly]

param(
    [switch]$DataOnly,  # If set, outputs JSON only (for Python subprocess invocation)
    [ValidateSet("Auto", "Fresh", "Force", "Prompt")]
    [string]$AuthMode = "Auto"
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

    if ($forceFreshAuthentication) {
        Write-Progress2 "      → Auth mode: forcing a fresh Microsoft 365 sign-in" -ForegroundColor Yellow
        Disconnect-PurviewSessions
    }

    $existingConnections = Get-ExistingPurviewConnectionState
    $ippsConnected = [bool]$existingConnections.IPPS
    $exoConnected = [bool]$existingConnections.EXO

    if ($ippsConnected) {
        Write-Progress2 "      → Security & Compliance: Already connected" -ForegroundColor Cyan
    }

    if ($exoConnected) {
        Write-Progress2 "      → Exchange Online: Already connected" -ForegroundColor Cyan
    }

    if (($ippsConnected -or $exoConnected) -and -not $forceFreshAuthentication) {
        Write-Progress2 "      → Reusing existing authenticated Microsoft 365 session(s)" -ForegroundColor Cyan
    }
    
    # Connect only if not already connected
    if ($ippsConnected -and $reuseExistingConnections) {
        Write-AuthStatus -Type "AUTH_REUSED" -Service "Security & Compliance"
    }

    if (-not $ippsConnected) {
        Write-AuthStatus -Type "AUTH_PROMPT" -Service "Security & Compliance" -Details "Purview portal and compliance cmdlets"
        Write-Progress2 "      → Connecting to Security & Compliance..." -NoNewline
        Connect-IPPSSession -ErrorAction Stop -WarningAction SilentlyContinue | Out-Null
        Write-Progress2 " ✓" -ForegroundColor Green
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
            Write-Progress2 "      → Connecting to Exchange Online..." -NoNewline
            Connect-ExchangeOnline -ShowBanner:$false -ErrorAction Stop -WarningAction SilentlyContinue | Out-Null
            Write-Progress2 " ✓" -ForegroundColor Green
            # Output to stderr so Python can display in real-time
            Write-AuthStatus -Type "AUTH_COMPLETE" -Service "Exchange Online"
        }
    }
    
    if ($ippsConnected -and $exoConnected) {
        Write-Progress2 "      ✓ Using existing connections" -ForegroundColor Green
    }
} catch {
    Write-Progress2 "      ✗ Connection failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Progress2 ""
    Write-Progress2 "Please ensure you have:" -ForegroundColor Yellow
    Write-Progress2 "  - ExchangeOnlineManagement module installed" -ForegroundColor Yellow
    Write-Progress2 "  - Appropriate permissions for Security & Compliance" -ForegroundColor Yellow
    exit 1
}

# Step 2: Collect Purview data
if (-not $DataOnly) {
    Write-Progress2 ""
    Write-Progress2 "[2/4] Collecting Purview compliance data..." -ForegroundColor Yellow
}

$purviewData = @{}
$permissionFailures = @()

# Collect DLP Policies
Write-Progress2 "      → DLP Compliance Policies..." -NoNewline
try {
    $dlpPolicies = @(Get-DlpCompliancePolicy -ErrorAction Stop | Select-Object Name, Mode, Enabled, ExchangeLocation, SharePointLocation, OneDriveLocation)
    $purviewData['dlp_policies'] = @{
        count = $dlpPolicies.Count
        policies = $dlpPolicies
    }
    Write-Progress2 " $($dlpPolicies.Count) found" -ForegroundColor Green
} catch {
    Write-Progress2 " Permission denied" -ForegroundColor Red
    $purviewData['dlp_policies'] = @{ count = 0; policies = @(); permission_denied = $true }
    $permissionFailures += "DLP Policies"
}

# Collect Sensitivity Labels
Write-Progress2 "      → Sensitivity Labels..." -NoNewline
try {
    $labels = @(Get-Label -ErrorAction Stop | Select-Object Name, DisplayName, Tooltip, Enabled)
    $purviewData['sensitivity_labels'] = @{
        count = $labels.Count
        labels = $labels
    }
    Write-Progress2 " $($labels.Count) found" -ForegroundColor Green
} catch {
    Write-Progress2 " Permission denied" -ForegroundColor Red
    $purviewData['sensitivity_labels'] = @{ count = 0; labels = @(); permission_denied = $true }
    $permissionFailures += "Sensitivity Labels"
}

# Collect Retention Policies
Write-Progress2 "      → Retention Compliance Policies..." -NoNewline
try {
    $retentionPolicies = @(Get-RetentionCompliancePolicy -ErrorAction Stop | Select-Object Name, Enabled, Type)
    $purviewData['retention_policies'] = @{
        count = $retentionPolicies.Count
        policies = $retentionPolicies
    }
    Write-Progress2 " $($retentionPolicies.Count) found" -ForegroundColor Green
} catch {
    Write-Progress2 " Permission denied" -ForegroundColor Red
    $purviewData['retention_policies'] = @{ count = 0; policies = @(); permission_denied = $true }
    $permissionFailures += "Retention Policies"
}

# Collect Label Policies
Write-Progress2 "      → Sensitivity Label Policies..." -NoNewline
try {
    $labelPolicies = @(Get-LabelPolicy -ErrorAction Stop -WarningAction SilentlyContinue | Select-Object Name, Enabled, Mode)
    $purviewData['label_policies'] = @{
        count = $labelPolicies.Count
        policies = $labelPolicies
    }
    Write-Progress2 " $($labelPolicies.Count) found" -ForegroundColor Green
} catch {
    Write-Progress2 " Permission denied" -ForegroundColor Red
    $purviewData['label_policies'] = @{ count = 0; policies = @(); permission_denied = $true }
    $permissionFailures += "Label Policies"
}

# Collect Insider Risk Policies
Write-Progress2 "      → Insider Risk Policies..." -NoNewline
try {
    $insiderRiskPolicies = @(Get-InsiderRiskPolicy -ErrorAction Stop | Select-Object Name, Enabled, InsiderRiskScenario)
    $purviewData['insider_risk_policies'] = @{
        count = $insiderRiskPolicies.Count
        policies = $insiderRiskPolicies
    }
    Write-Progress2 " $($insiderRiskPolicies.Count) found" -ForegroundColor Green
} catch {
    Write-Progress2 " Permission denied" -ForegroundColor Red
    $purviewData['insider_risk_policies'] = @{ count = 0; policies = @(); permission_denied = $true }
    $permissionFailures += "Insider Risk Policies"
}

# Collect Communication Compliance Policies
Write-Progress2 "      → Communication Compliance..." -NoNewline
try {
    $commCompPolicies = @(Get-SupervisoryReviewPolicyV2 -ErrorAction Stop | Select-Object Name, Enabled, SamplingRate)
    $purviewData['communication_compliance'] = @{
        count = $commCompPolicies.Count
        policies = $commCompPolicies
    }
    Write-Progress2 " $($commCompPolicies.Count) found" -ForegroundColor Green
} catch {
    Write-Progress2 " Permission denied" -ForegroundColor Red
    $purviewData['communication_compliance'] = @{ count = 0; policies = @(); permission_denied = $true }
    $permissionFailures += "Communication Compliance"
}

# Collect Information Barriers
Write-Progress2 "      → Information Barriers..." -NoNewline
try {
    $ibPolicies = @(Get-InformationBarrierPolicy -ErrorAction Stop | Select-Object Name, State, AssignedSegment)
    $purviewData['information_barriers'] = @{
        count = $ibPolicies.Count
        policies = $ibPolicies
    }
    Write-Progress2 " $($ibPolicies.Count) found" -ForegroundColor Green
} catch {
    Write-Progress2 " Permission denied" -ForegroundColor Red
    $purviewData['information_barriers'] = @{ count = 0; policies = @(); permission_denied = $true }
    $permissionFailures += "Information Barriers"
}

# Collect eDiscovery Cases
Write-Progress2 "      → eDiscovery Cases..." -NoNewline
try {
    $cases = @(Get-ComplianceCase -ErrorAction Stop | Select-Object Name, Status, CaseType)
    $purviewData['ediscovery_cases'] = @{
        count = $cases.Count
        cases = $cases
    }
    Write-Progress2 " $($cases.Count) found" -ForegroundColor Green
} catch {
    Write-Progress2 " Permission denied" -ForegroundColor Red
    $purviewData['ediscovery_cases'] = @{ count = 0; cases = @(); permission_denied = $true }
    $permissionFailures += "eDiscovery Cases"
}

# Collect Organization Configuration
Write-Progress2 "      → Organization Config..." -NoNewline
try {
    $orgConfig = Get-OrganizationConfig -ErrorAction Stop | Select-Object CustomerLockBoxEnabled, AuditDisabled, IsDehydrated
    $purviewData['org_config'] = $orgConfig
    $lockboxStatus = if ($orgConfig.CustomerLockBoxEnabled) { "Lockbox: Enabled" } else { "Lockbox: Disabled" }
    Write-Progress2 " $lockboxStatus" -ForegroundColor Green
} catch {
    Write-Progress2 " Failed" -ForegroundColor Red
    $purviewData['org_config'] = @{}
}

# Collect IRM Configuration
Write-Progress2 "      → Azure RMS Configuration..." -NoNewline
try {
    $irmConfig = Get-IRMConfiguration -ErrorAction Stop | Select-Object AzureRMSLicensingEnabled, InternalLicensingEnabled
    $purviewData['irm_config'] = $irmConfig
    $rmsStatus = if ($irmConfig.AzureRMSLicensingEnabled) { "RMS: Enabled" } else { "RMS: Disabled" }
    Write-Progress2 " $rmsStatus" -ForegroundColor Green
} catch {
    Write-Progress2 " Failed" -ForegroundColor Red
    $purviewData['irm_config'] = @{}
}

# Collect Audit Configuration
Write-Progress2 "      → Audit Configuration..." -NoNewline
try {
    $auditConfig = Get-AdminAuditLogConfig -ErrorAction Stop | Select-Object UnifiedAuditLogIngestionEnabled, AdminAuditLogEnabled
    $purviewData['audit_config'] = $auditConfig
    $auditStatus = if ($auditConfig.UnifiedAuditLogIngestionEnabled) { "Unified Audit: Enabled" } else { "Unified Audit: Disabled" }
    Write-Progress2 " $auditStatus" -ForegroundColor $(if ($auditConfig.UnifiedAuditLogIngestionEnabled) { "Green" } else { "Yellow" })
} catch {
    Write-Progress2 " Failed" -ForegroundColor Red
    $purviewData['audit_config'] = @{}
}

# Step 3: Serialize and output data
if (-not $DataOnly) {
    Write-Progress2 ""
    if ($permissionFailures.Count -gt 0) {
        Write-Progress2 "⚠️  Permission denied for $($permissionFailures.Count) data source(s): $($permissionFailures -join ', ')" -ForegroundColor Yellow
        Write-Progress2 "    License recommendations will still be generated. Deployment recommendations limited." -ForegroundColor Gray
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

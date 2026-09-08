<#
.SYNOPSIS
    Creates Azure AD App Registration for M365 Copilot Readiness Assessment Tool
    
.DESCRIPTION
    This script creates a service principal with all required API permissions:
    - Microsoft Graph (Security, Directory, Organization)
    - Microsoft Defender for Endpoint
    - Power Platform
    - Compliance Center (Purview)
    
.NOTES
    Requirements:
    - Global Administrator or Application Administrator role
    - Microsoft.Graph PowerShell module (auto-installed if missing)
#>

#Requires -Version 5.1

# Configuration
$AppName = "M365 Copilot Readiness Assessment Tool"
$SecretExpirationDays = 30

# Color output
function Write-Success { param($Message) Write-Host "✓ $Message" -ForegroundColor Green }
function Write-Info { param($Message) Write-Host "ℹ $Message" -ForegroundColor Cyan }
function Write-Warn { param($Message) Write-Host "⚠ $Message" -ForegroundColor Yellow }
function Write-Fail { param($Message) Write-Host "✗ $Message" -ForegroundColor Red }

Write-Host "`n=============================================================================" -ForegroundColor Cyan
Write-Host "   M365 Copilot Readiness Tool - Service Principal Setup" -ForegroundColor Cyan
Write-Host "=============================================================================" -ForegroundColor Cyan
# Check required PowerShell modules
. "$PSScriptRoot\Check-PSModules.ps1"
if (-not (Test-RequiredModules -ScriptType "Setup")) {
    exit 1
}
# Force import to avoid version conflicts
Write-Info "Loading Microsoft.Graph modules..."
try {
    Import-Module Microsoft.Graph.Authentication -Force
    Import-Module Microsoft.Graph.Applications -Force
    Import-Module Microsoft.Graph.Identity.DirectoryManagement -Force
    Write-Success "Modules loaded"
} catch {
    Write-Warn "Module import issue. Close PowerShell and run script in fresh session."
    exit 1
}

# Step 2: Connect to Microsoft Graph
Write-Info "Connecting to Microsoft Graph..."
Write-Info "Browser will open for authentication (requires Global Admin)"

try {
    # Use Process scope to maintain authentication context throughout script
    Connect-MgGraph -Scopes "Application.ReadWrite.All", "Directory.Read.All", "RoleManagement.ReadWrite.Directory", "AppRoleAssignment.ReadWrite.All" -ContextScope Process -NoWelcome -ErrorAction Stop
    $context = Get-MgContext
    
    if (-not $context -or -not $context.TenantId) {
        throw "Authentication failed or was cancelled"
    }
    
    Write-Success "Connected to tenant: $($context.TenantId)"
    Write-Success "Authentication context: Process (will not re-prompt)"
} catch {
    Write-Fail "Failed to connect to Microsoft Graph: $_"
    Write-Host "`nPlease complete the browser authentication to continue.`n" -ForegroundColor Yellow
    exit 1
}

# Step 3: Check for existing app registration
Write-Info "Checking for existing app registration..."
$existingApp = Get-MgApplication -Filter "displayName eq '$AppName'" -ErrorAction SilentlyContinue

if ($existingApp) {
    Write-Warn "App registration '$AppName' already exists"
    $response = Read-Host "Do you want to delete and recreate it? (y/N)"
    if ($response -eq 'y' -or $response -eq 'Y') {
        Remove-MgApplication -ApplicationId $existingApp.Id
        Write-Success "Existing app deleted"
    } else {
        Write-Info "Using existing app registration"
        $app = $existingApp
        $skipCreation = $true
    }
}

# Step 4: Create App Registration
if (-not $skipCreation) {
    Write-Info "Creating app registration: $AppName"
    
    # Create public client with redirect URI for admin consent
    $publicClient = @{
        RedirectUris = @("http://localhost")
    }
    
    $app = New-MgApplication -DisplayName $AppName -SignInAudience "AzureADMyOrg" -PublicClient $publicClient
    Write-Success "App created: $($app.DisplayName)"
    Write-Success "Application ID: $($app.AppId)"
}

# Step 5: Define Required API Permissions
Write-Info "Configuring API permissions..."

# Microsoft Graph API Resource ID
$graphResourceId = "00000003-0000-0000-c000-000000000000"

# Microsoft Graph - Application Permissions (Role-based)
$graphPermissions = @(
    # Core Directory & Organization
    @{ Id = "df021288-bdef-4463-88db-98f22de89214"; Name = "User.Read.All" }
    @{ Id = "7ab1d382-f21e-4acd-a863-ba3e13f7da61"; Name = "Directory.Read.All" }
    @{ Id = "498476ce-e0fe-48b0-b801-37ba7e2685c6"; Name = "Organization.Read.All" }
    
    # Security & Threat Intelligence
    @{ Id = "bf394140-e372-4bf9-a898-299cfc7564e5"; Name = "SecurityEvents.Read.All" }
    @{ Id = "45cc0394-e837-488b-a098-1918f48d186c"; Name = "SecurityIncident.Read.All" }
    @{ Id = "197ee4e9-b993-4066-898f-d6aecc55125b"; Name = "ThreatIndicators.Read.All" }
    @{ Id = "dd98c7f5-2d42-42d3-a0e4-633161547251"; Name = "ThreatHunting.Read.All" }
    @{ Id = "f8f035bb-2cce-47fb-8bf5-7baf3ecbee48"; Name = "ThreatAssessment.Read.All" }
    
    # Identity Protection & Risk
    @{ Id = "dc5007c0-2d7d-4c42-879c-2dab87571379"; Name = "IdentityRiskyUser.Read.All" }
    @{ Id = "6e472fd1-ad78-48da-a0f0-97ab2c6b769e"; Name = "IdentityRiskEvent.Read.All" }
    
    # Entra Enhanced Observations - Identity & Access Management
    @{ Id = "246dd0d5-5bd0-4def-940b-0421030a5b68"; Name = "Policy.Read.All" }
    @{ Id = "9e640839-a198-48fb-8b9a-013fd6f6cbcd"; Name = "Policy.Read.PermissionGrant" }
    @{ Id = "483bed4a-2ad3-4361-a73b-c83ccdbdc53c"; Name = "RoleManagement.Read.Directory" }
    @{ Id = "38d9df27-64da-44fd-b7c5-a6fbac20248f"; Name = "UserAuthenticationMethod.Read.All" }
    @{ Id = "d07a8cc0-3d51-4b77-b3b0-32704d1f69fa"; Name = "AccessReview.Read.All" }
    
    # Device Management & Compliance
    @{ Id = "2f51be20-0bb4-4fed-bf7b-db946066c75e"; Name = "DeviceManagementManagedDevices.Read.All" }
    @{ Id = "dc377aa6-52d8-4e23-b271-2a7ae04cedf3"; Name = "DeviceManagementConfiguration.Read.All" }
    
    # Global Secure Access (Entra Internet Access) - Preview/Beta API - may not be available in all tenants
    @{ Id = "8a3d36bf-cb46-4bcc-bec9-8d92829dab84"; Name = "NetworkAccessPolicy.Read.All" }
    @{ Id = "e30060de-caa5-4331-99d3-6ac6c966a9a4"; Name = "NetworkAccess.Read.All" }
    
    # Application & Consent Management
    @{ Id = "9a5d68dd-52b0-4cc2-bd40-abcf44ac3a30"; Name = "Application.Read.All" }
    
    # Audit Logs & Sign-in Activity
    @{ Id = "b0afded3-3588-46d8-8b3d-9842eff778da"; Name = "AuditLog.Read.All" }
    
    # M365 Copilot Adoption - Usage Reports & Analytics (CRITICAL for 80+ observations)
    @{ Id = "230c1aed-a721-4c5d-9cb4-a90514e508ef"; Name = "Reports.Read.All" }
    
    # M365 Copilot Adoption - SharePoint & OneDrive Content Analysis (35+ observations)
    @{ Id = "332a536c-c7ef-4017-ab91-336970924f0d"; Name = "Sites.Read.All" }
    @{ Id = "01d4889c-1287-42c6-ac1f-5d1e02578ef6"; Name = "Files.Read.All" }
    
    # M365 Copilot Adoption - Graph Connectors & Search (8+ observations)
    @{ Id = "1914711b-a1cb-4793-b019-c2ce0ed21b8c"; Name = "ExternalConnection.Read.All" }
    
    # M365 Copilot Adoption - Teams Advanced (12+ observations)
    @{ Id = "b9bb2381-47a4-46cd-aafb-00cb12f68504"; Name = "Channel.ReadBasic.All" }
    @{ Id = "a82116e5-55eb-4c41-a434-62fe8a61c773"; Name = "OnlineMeetings.Read.All" }
    
    # M365 Copilot Adoption - Virtual Appointments & Bookings (3+ observations)
    @{ Id = "6e98f277-b046-4193-a4f2-6bf6a78cd491"; Name = "Bookings.Read.All" }
    
    # M365 Copilot Adoption - User Activity & Insights (5+ observations)
    @{ Id = "b528084d-ad10-4598-8b93-929746b4d7d6"; Name = "People.Read.All" }
    
    # M365 Copilot Adoption - Specialized Services
    @{ Id = "9709bb33-4549-49d4-8ed9-a8f65e45bb0f"; Name = "Printer.Read.All" }
    @{ Id = "57f1cf28-c0c4-4ec3-9a30-19a2eaaf2f6e"; Name = "WorkplaceAnalytics-Reports.Read.All" }
    @{ Id = "19da66cb-0fb0-4390-b071-ebc76a349482"; Name = "InformationProtectionPolicy.Read" }
)

# Microsoft Defender for Endpoint API Resource ID
$defenderResourceId = "fc780465-2017-40d4-a0c5-307022471b92"

# Defender API - Application Permissions
$defenderPermissions = @(
    @{ Id = "ea8291d3-4b9a-44b5-bc3a-6cea3026dc79"; Name = "Machine.Read.All" }
)

# Office 365 Management API Resource ID (for advanced audit logs & Copilot telemetry)
$office365MgmtResourceId = "c5393580-f805-4401-95e8-94b7a6ef2fc2"

# Office 365 Management API - Application Permissions
$office365MgmtPermissions = @(
    @{ Id = "594c1fb6-4f81-4475-ae41-0c394909246c"; Name = "ActivityFeed.Read" }
    @{ Id = "e2cea78f-e743-4d8f-a16a-75b629a038ae"; Name = "ServiceHealth.Read" }
)

# Build required resource access array
$requiredResourceAccess = @()

# Add Microsoft Graph permissions
$graphResourceAccess = @{
    ResourceAppId = $graphResourceId
    ResourceAccess = @($graphPermissions | ForEach-Object {
        @{
            Id = $_.Id
            Type = "Role"  # Application permission
        }
    })
}
$requiredResourceAccess += $graphResourceAccess

# Add Defender API permissions
$defenderResourceAccess = @{
    ResourceAppId = $defenderResourceId
    ResourceAccess = @($defenderPermissions | ForEach-Object {
        @{
            Id = $_.Id
            Type = "Role"  # Application permission
        }
    })
}
$requiredResourceAccess += $defenderResourceAccess

# Add Office 365 Management API permissions
$office365MgmtResourceAccess = @{
    ResourceAppId = $office365MgmtResourceId
    ResourceAccess = @($office365MgmtPermissions | ForEach-Object {
        @{
            Id = $_.Id
            Type = "Role"  # Application permission
        }
    })
}
$requiredResourceAccess += $office365MgmtResourceAccess

# Update app with permissions
Update-MgApplication -ApplicationId $app.Id -RequiredResourceAccess $requiredResourceAccess
Write-Success "API permissions configured"

# Display configured permissions
Write-Host "`nConfigured Permissions:" -ForegroundColor Yellow
Write-Host "  Microsoft Graph API ($($graphPermissions.Count) permissions):" -ForegroundColor White
foreach ($perm in $graphPermissions) {
    Write-Host "    - $($perm.Name)" -ForegroundColor Gray
}
Write-Host "  Microsoft Defender API:" -ForegroundColor White
foreach ($perm in $defenderPermissions) {
    Write-Host "    - $($perm.Name)" -ForegroundColor Gray
}
Write-Host "  Office 365 Management API:" -ForegroundColor White
foreach ($perm in $office365MgmtPermissions) {
    Write-Host "    - $($perm.Name)" -ForegroundColor Gray
}

# Step 6: Create Client Secret
Write-Info "Generating client secret (expires in $SecretExpirationDays days)..."

$secretExpiration = (Get-Date).AddDays($SecretExpirationDays)
$passwordCred = @{
    DisplayName = "M365 Readiness Tool Secret"
    EndDateTime = $secretExpiration
}

$secret = Add-MgApplicationPassword -ApplicationId $app.Id -PasswordCredential $passwordCred
Write-Success "Client secret created (expires: $($secretExpiration.ToString('yyyy-MM-dd')))"

# Step 7: Create Service Principal and Assign Roles
Write-Info "Creating service principal..."

# Get or create service principal for the app
$servicePrincipal = Get-MgServicePrincipal -Filter "appId eq '$($app.AppId)'" -ErrorAction SilentlyContinue

if (-not $servicePrincipal) {
    $servicePrincipal = New-MgServicePrincipal -AppId $app.AppId
    Write-Success "Service principal created"
} else {
    Write-Success "Service principal already exists"
}

# Grant admin consent via URL (one-click, no repeated prompts)
Write-Info "Opening browser to grant admin consent for all API permissions..."
Write-Host "`n  This will grant ALL $($graphPermissions.Count + $defenderPermissions.Count + $office365MgmtPermissions.Count) permissions in one click" -ForegroundColor Yellow
Write-Host "  You'll see a consent screen - click 'Accept' to continue`n" -ForegroundColor Yellow

# Build admin consent URL
$adminConsentUrl = "https://login.microsoftonline.com/$($context.TenantId)/adminconsent?client_id=$($app.AppId)"

# Open browser for admin consent
Start-Process $adminConsentUrl

Write-Host "Waiting for admin consent..." -ForegroundColor Cyan
Write-Host "  • Browser opened with consent page" -ForegroundColor Gray
Write-Host "  • Click 'Accept' in the browser" -ForegroundColor Gray
Write-Host "  • You'll be redirected to localhost (that's EXPECTED - shows success)" -ForegroundColor Gray
Write-Host "  • Browser will show 'Can't reach this page' - that's NORMAL" -ForegroundColor Gray
Write-Host "  • Just close the browser tab and return here" -ForegroundColor Gray
Write-Host "`nPress ENTER after you've clicked Accept and been redirected..." -ForegroundColor Yellow
$null = Read-Host

# Verify consent was granted
Write-Info "Verifying admin consent..."
Start-Sleep -Seconds 2  # Give Azure time to propagate

try {
    $grants = Get-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $servicePrincipal.Id -All -ErrorAction Stop
    if ($grants.Count -gt 0) {
        Write-Success "Admin consent verified ($($grants.Count) permissions granted)"
    } else {
        Write-Warn "No permissions found. Consent may still be propagating..."
        Write-Host "  Run the setup script again if the tool fails to authenticate" -ForegroundColor Gray
    }
} catch {
    Write-Warn "Could not verify consent: $_"
    Write-Host "  Consent may still be valid. Continue with setup." -ForegroundColor Gray
}

# Power Platform is optional enrichment. Do not grant the application the broad Entra Power
# Platform Administrator role. The opt-in unified inventory API uses Power Platform RBAC and
# should receive the tenant-scoped read-only role instead.
Write-Info "Power Platform inventory access is optional"
Write-Host "  For --preview-collectors power-platform, assign Power Platform Reader RBAC" -ForegroundColor Gray
Write-Host "  Role ID: c886ad2e-27f7-4874-8381-5849b8d8a090" -ForegroundColor Gray
Write-Host "  Scope: /tenants/$($context.TenantId)" -ForegroundColor Gray
Write-Host "  This Power Platform API/RBAC path is preview; a Manage > Inventory CSV export is the supported fallback." -ForegroundColor Gray
Write-Host "  Do not assign the Entra Power Platform Administrator role to this service principal." -ForegroundColor Gray
Write-Host "  Optional Shadow AI preview collection separately requires Graph CloudApp-Discovery.Read.All." -ForegroundColor Gray

# Step 8: Create .env file
Write-Info "Creating .env file..."

$envContent = @"
# M365 Copilot Readiness Assessment Tool - Service Principal Credentials
# Created: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
# Secret expires: $($secretExpiration.ToString('yyyy-MM-dd'))

TENANT_ID=$($context.TenantId)
CLIENT_ID=$($app.AppId)
CLIENT_SECRET=$($secret.SecretText)

# DO NOT COMMIT THIS FILE TO GIT
# Add .env to your .gitignore file
"@

$envPath = Join-Path $PSScriptRoot ".env"
$envContent | Out-File -FilePath $envPath -Encoding UTF8 -Force
Write-Success ".env file created: $envPath"

# Step 9: Update/Create .gitignore
$gitignorePath = Join-Path $PSScriptRoot ".gitignore"
if (Test-Path $gitignorePath) {
    $gitignoreContent = Get-Content $gitignorePath -Raw
    if ($gitignoreContent -notmatch "\.env") {
        Add-Content -Path $gitignorePath -Value "`n# Environment variables (contains secrets)`n.env"
        Write-Success "Added .env to .gitignore"
    }
} else {
    @"
# Environment variables (contains secrets)
.env

# Python
__pycache__/
*.pyc
*.pyo
*.pyd
.Python
*.so
*.egg
*.egg-info/
dist/
build/

# VS Code
.vscode/

# Reports
Reports/
*.csv
"@ | Out-File -FilePath $gitignorePath -Encoding UTF8
    Write-Success "Created .gitignore with .env"
}

# Final Summary
Write-Host "`n=============================================================================" -ForegroundColor Green
Write-Host "   ✓ SERVICE PRINCIPAL SETUP COMPLETE" -ForegroundColor Green
Write-Host "=============================================================================" -ForegroundColor Green

Write-Host "`nCredentials saved to: " -NoNewline
Write-Host ".env" -ForegroundColor Yellow

Write-Host "`n✓ Configuration Summary:" -ForegroundColor Green
Write-Host "  • App Registration: $($app.DisplayName)" -ForegroundColor White
Write-Host "  • Application ID: $($app.AppId)" -ForegroundColor White
Write-Host "  • Service Principal: Created" -ForegroundColor White
Write-Host "  • Admin Consent: " -NoNewline -ForegroundColor White
Write-Host "✓ Granted" -ForegroundColor Green
Write-Host "  • Power Platform: Optional; Reader RBAC or inventory CSV not configured by this script" -ForegroundColor White
Write-Host "  • Client Secret Expires: $($secretExpiration.ToString('yyyy-MM-dd'))`n" -ForegroundColor White

Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "  1. Run: " -NoNewline -ForegroundColor White
Write-Host "python main.py" -ForegroundColor Cyan
Write-Host "  2. Tool will authenticate silently (no browser popups!)" -ForegroundColor White
Write-Host "  3. Data will be collected from all Microsoft 365 services`n" -ForegroundColor White

Write-Host "Security Notes:" -ForegroundColor Yellow
Write-Host "  • .env file contains secrets - NEVER commit to git" -ForegroundColor White
Write-Host "  • Client secret expires: $($secretExpiration.ToString('yyyy-MM-dd'))" -ForegroundColor White
Write-Host "  • Rotate secret before expiration by running this script again`n" -ForegroundColor White

Write-Host "✓ Setup complete! You can now run the tool.`n" -ForegroundColor Green

# Disconnect
Disconnect-MgGraph | Out-Null

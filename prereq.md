# Prerequisites

## Authentication Requirements

The tool uses **browser authentication** to ensure you always get the required Security API permissions.

### What Happens When You Run

**First Time:**
```powershell
python main.py
```
→ Browser opens → Sign in → Review and accept permissions → Done! ✅

**Subsequent Runs:**
```powershell
python main.py
```
→ Token cached → No browser popup → Works immediately! ✅

### Authentication Behavior

- **Graph/Security APIs (M365, Entra, Defender):** Always uses browser authentication to ensure proper Security scopes
- **Power Platform:** Optional enrichment. Prefer a unified inventory CSV; the preview inventory API is opt-in.
- **Token Caching:** Your authentication is cached - only need to sign in once, not every run

### Why Browser Auth?

Browser authentication guarantees you explicitly consent to Security API permissions:
- ✅ **Always works:** No missing scope issues
- ✅ **Secure:** Delegated permissions (no tenant-wide changes)
- ✅ **Consistent:** Same experience for all admins
- ✅ **Cached:** Only prompts first time, then reuses token

### Troubleshooting Browser Authentication

**If browser opens multiple tabs or hangs:**
1. Close all browser tabs that opened
2. Allow Python through Windows Firewall when prompted
3. Run again - should work on second attempt

**If "couldn't start HTTP server" error:**
- Ensure port 8400 isn't blocked by firewall or another application
- Windows may prompt to allow Python network access - click "Allow"

### Required Permissions

When authenticating (either CLI or browser), you need:
- **For M365/Entra/Defender license data:** Any user account
- **For Defender API enrichment:** Security Reader, Security Administrator, or Global Administrator
- **For Power Platform:** No role is required for core readiness. A supplied inventory CSV needs no live tenant role; the opt-in preview API uses tenant-scoped Power Platform Reader RBAC.
- **For Purview:** Compliance Administrator or Global Administrator (requires interactive authentication via PowerShell)

**Least Privilege Roles for PowerShell Collectors:**

| Service | Minimum Role | What it Accesses |
|---------|--------------|------------------|
| **Purview** | Compliance Administrator | DLP policies, sensitivity labels, retention policies via Connect-IPPSSession cmdlets |
| **Power Platform legacy collector** | Power Platform Administrator (delegated user) | Per-environment DLP, apps, flows, and AI Builder details when the preferred inventory source is not used |
| **Power Platform inventory API (preview)** | Power Platform Reader RBAC assigned to the service principal at `/tenants/{tenantId}` | Tenant-wide inventory of environments, apps, flows, agents, owners, regions, managed state, and connectors |

**Note:** Purview PowerShell still requires delegated user authentication. Power Platform is supplemental: missing inventory is an extensibility coverage gap and cannot lower the core readiness result. The Power Platform inventory API and its Reader RBAC support are preview; use an exported inventory CSV when preview use is not approved.

### Service-Specific Authentication Notes

**Power Platform & Purview:**
- Purview cmdlets (Exchange Online Management) require delegated authentication.
- The preferred Power Platform path is an inventory CSV, or the opt-in preview inventory API with tenant-scoped Power Platform Reader RBAC.
- The legacy Power Platform collector remains available for delegated per-environment collection and now aggregates every readable environment.

**To use Power Platform or Purview:**

Run the respective PowerShell collector script first:
```powershell
# Power Platform legacy collector
.\collect_power_platform_and_copilot_studio_data.ps1

# Purview
.\collect_purview_data.ps1
```

The Purview and legacy Power Platform collectors will:
1. Open a browser for authentication
2. Collect the required data
3. Automatically pass it to Python
4. Generate recommendations.

For the preferred Power Platform inventory paths, use either:

```powershell
python main.py --power-platform-inventory .\exports\power-platform-inventory.csv
python main.py --preview-collectors power-platform
```

The preview API requires Power Platform Reader role ID
`c886ad2e-27f7-4874-8381-5849b8d8a090` at `/tenants/{tenantId}`. Do not assign the
Entra Power Platform Administrator directory role to the application.

**Alternative**: Remove "Power Platform" or "Purview" from the `SERVICES` array in [params.py](params.py) to skip these services.


## Microsoft Defender XDR Activation (One-Time Manual Setup)

**⚠️ Important:** If your tenant has Microsoft Defender XDR licenses (M365 E5, Microsoft 365 Defender plan) but has never accessed the Defender portal, you must **manually activate** Defender XDR before the tool can retrieve security data via APIs.

### Why Manual Activation Required?

Microsoft intentionally requires portal-based activation for Defender XDR to ensure:
- **Security governance:** Explicit admin acknowledgment before enabling tenant-wide monitoring
- **Data residency:** Selection of geographic region for security data storage (EU, US, UK, etc.)
- **Compliance review:** Opportunity to review data retention policies and compliance boundaries
- **License validation:** Verification of proper licensing before provisioning infrastructure

This is a **one-time setup** - after activation, all data access is API-driven.

### How to Activate

1. **Visit the Defender portal:** https://security.microsoft.com
2. **Sign in** as Global Administrator or Security Administrator
3. **Look for activation prompt:** Banner saying "Turn on Microsoft Defender" or "Get started"
4. **Click to activate** and follow the setup wizard
5. **Wait 10-15 minutes** for provisioning to complete
6. **Verify:** Navigate to **Incidents** - if it loads (even if empty), activation is complete ✅

### What Happens During Activation

The portal will show: *"Hang on! We're preparing new spaces for your data and connecting them"*

- Initial workspace creation: **~10-15 minutes**
- Security API endpoints become accessible: **~10-15 minutes**
- Full data connector initialization: **Few hours**

### After Activation

Once activated, the tool will:
- ✅ Successfully call Graph Security API (`/security/alerts_v2`, `/security/incidents`)
- ✅ Access Microsoft 365 Defender API (`/api/incidents`, `/api/machines`)
- ✅ Enrich recommendations with real security metrics (active alerts, incidents, secure score)
- ✅ No longer show the "Microsoft Defender XDR Activation" high-priority recommendation

### Lower SKUs (Business Premium, E3)

If you have **lower SKUs without XDR**, the tool will:
- Still work fine with license-based Defender recommendations
- Show recommendation to upgrade to M365 E5 or add Defender plan for XDR capabilities
- No activation required (XDR features not available in your license)

## Python Dependencies
```bash
pip install msgraph-sdk azure-identity httpx
```

## PowerShell Modules

### Microsoft.Graph (for Service Principal setup)
```powershell
# Install module
Install-Module -Name Microsoft.Graph -Force -AllowClobber
```

**Defender API Permission Handling:**

The `setup-service-principal.ps1` script **automatically detects** Defender API permissions from your tenant's WindowsDefenderATP service principal. No manual GUID configuration required!

**What happens during script execution:**
- ✅ Script connects to Microsoft Graph
- ✅ Looks up WindowsDefenderATP service principal
- ✅ Dynamically fetches the 6 permission GUIDs (Machine.Read.All, Vulnerability.Read.All, etc.)
- ✅ Adds them to your app registration

**How WindowsDefenderATP Service Principal Gets Created:**

Microsoft automatically creates the WindowsDefenderATP service principal in your tenant through:

1. **Portal Activation (Recommended):** Navigate to https://security.microsoft.com → Enable Defender for Endpoint
   - Microsoft provisions backend infrastructure (~10-15 minutes)
   - Creates WindowsDefenderATP service principal automatically
   - API endpoints become functional immediately after provisioning

2. **Admin Consent Auto-Provisioning:** Granting admin consent triggers service principal creation
   - Azure AD auto-creates WindowsDefenderATP service principal if missing
   - Permissions get granted successfully ✅
   - **However:** Backend API infrastructure is NOT provisioned ❌
   - API calls return 403 Forbidden until portal activation

**If the script detects WindowsDefenderATP doesn't exist:**

The script will skip Defender API permissions and show a message. **Important:** Running the script and granting admin consent would auto-create the WindowsDefenderATP service principal, but this only creates the Azure AD identity - not the backend API infrastructure.

**Recommended workflow:**
1. Navigate to https://security.microsoft.com first
2. Activate Defender for Endpoint (provisions service principal + backend infrastructure)
3. Re-run the setup script (detects service principal and adds permissions)

**What happens if you grant consent without portal activation:**
- ✅ WindowsDefenderATP service principal auto-created by Azure AD
- ✅ Permissions granted to your app
- ❌ Backend API not provisioned → API calls return 403 Forbidden
- ❌ Must still activate via portal for data collection to work

**Troubleshooting only:** If you encounter "Permission not found" errors during admin consent, manually verify GUIDs:

```powershell
# Connect and check
Connect-MgGraph -Scopes "Application.Read.All"
$defenderSP = Get-MgServicePrincipal -Filter "appId eq 'fc780465-2017-40d4-a0c5-307022471b92'"
$defenderSP.AppRoles | Where-Object { $_.AllowedMemberTypes -contains 'Application' } | 
    Select-Object Value, Id | Format-Table -AutoSize
```

### Exchange Online Management (for Purview/Compliance data)
```powershell
# Install module
Install-Module -Name ExchangeOnlineManagement -Force -AllowClobber
```

## Running the Tool

### Option 1: Basic Mode (License-based recommendations only)
```bash
python main.py
```
Provides 30+ recommendations based on license data and Graph API (audit logs only).

### Option 2: Enhanced Mode with Purview Deployment Data
```powershell
.\collect_purview_data.ps1
```

This PowerShell data collector:
1. Connects to Security & Compliance PowerShell (`Connect-IPPSSession`)
2. Collects DLP policies, sensitivity labels, retention policies via cmdlets
3. Pipes data as JSON to Python via stdin (no cache files)
4. Runs `python main.py` (services controlled by `params.py`)
5. Python reads stdin data for deployment-specific recommendations

**Enhanced recommendations include:**
- ✓ "3 DLP policies configured (2 enabled): Policy-A, Policy-B, Policy-C"
- ✓ "12 sensitivity labels deployed: Confidential, Highly Confidential, ..."
- ✓ "5 retention policies active, protecting SharePoint and Exchange"

**Note:** For backward compatibility, `.\run_with_purview.ps1` still works but is deprecated.

### Why Two Modes?

**Technical limitation:** Purview cmdlets (`Get-DlpCompliancePolicy`, `Get-Label`, etc.) are dynamically loaded after `Connect-IPPSSession` into temporary PowerShell modules (e.g., `tmpEXO_xxx`). These temporary modules are NOT accessible from Python subprocesses.

**Solution:** The PowerShell wrapper runs cmdlets in the connected session, caches results to JSON, then runs Python. This hybrid approach provides deployment-specific observations without subprocess limitations.

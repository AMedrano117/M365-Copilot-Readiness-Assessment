# Run Automated Readiness Assessment

Use the guidance below to run the Automated Readiness Assessment for Microsoft 365 Copilot and Agents. Refer to [Automated Readiness Assessment](README.md) to learn more about the assessment capabilities before running the tool.

## Prerequisites

- **Microsoft 365 Tenant**: Active M365 tenant with licenses
- **Admin Access**: Appropriate role assignments based on services to assess ([see table below](#minimum-admin-roles))
- **Python Environment**: Python 3.8 or later installed
- **Network Access**: Connectivity to Microsoft Graph, Defender, Power Platform, and Purview APIs
- **Repository Clone**: Local copy of this repository

**Minimum Admin Roles:**

| Service Area | Minimum Role | Authentication Method |
|--------------|--------------|----------------------|
| Service Principal Setup | Global Administrator or Application Administrator | One-time setup |
| M365 + Entra Licenses | Any user account (read-only) | Service Principal |
| Defender Security Data | Security Reader | Service Principal |
| SharePoint governance | SharePoint Administrator; SAM reports can additionally require SharePoint Advanced Management Administrator | Browser or application certificate |
| Purview core configuration | Compliance Administrator or the applicable read-only Purview role groups | Browser or application certificate |
| Power Platform inventory export | Global Reader or another inventory-supported role | Portal export |

**Note:** It is recommended that Microsoft 365 Administrators run this assessment. Alternatively, assign the appropriate roles listed above to designated users who will perform the assessment.

Permissions and role assignments are tenant-specific. When assessing a different tenant, the
app registration identified by that tenant's `CLIENT_ID` must have its own application permissions
and admin consent. The user completing Purview or Power Platform interactive authentication must
also hold the delegated role in that target tenant. Local PowerShell modules and cached sign-ins are
machine-specific.

## Data Collection Details

The following table shows what data is collected for each service and the APIs/cmdlets used:

| Service | Authentication | APIs / PowerShell Cmdlets Used | Data Collected |
|---------|----------------|--------------------------------|----------------|
| **M365** | Service Principal | Microsoft Graph tenant, users, groups, sites, reporting, Copilot reporting, and `/external/connections` APIs | License coverage, Copilot activation/engagement, M365 app pilot fit, sites, and connected grounding sources |
| **Entra** | Service Principal | Microsoft Graph API:<br/>- `/identityProtection/riskyUsers`<br/>- `/identityProtection/riskDetections`<br/>- `/identity/conditionalAccess/policies`<br/>- `/policies/authorizationPolicy`<br/>- `/organization` | Risky users and risk detections when the tenant has a qualifying P2-level entitlement; conditional access policies, MFA enforcement, and external collaboration settings |
| **Defender** | Service Principal | Graph Security alerts, incidents, Secure Score, and Defender for Endpoint `/api/machines` | Active security evidence plus device onboarding and risk. Ordinary Office traffic is never presented as AI usage. |
| **SharePoint** | Browser or application certificate | `Get-SPOTenant`, `Get-SPOSite`, `Get-SPODataAccessGovernanceInsight`, and activity-data status cmdlets | Tenant/site sharing defaults, anonymous link behavior, legacy auth, site deviations, and existing DAG/SAM report readiness and freshness. No scan is started. |
| **Purview** | Browser or application certificate | DLP policy/rule, sensitivity-label, retention, organization, rights-management, and audit cmdlets | Core data protection policy configuration. Specialized workloads are not queried by default. |
| **Power Platform / Copilot Studio** | Export, or service principal for opt-in preview API | Unified Power Platform Inventory export or API | Agents, apps, flows, environments, owners, regions, managed state, and connectors. The Az.Accounts collector runs only with `--legacy-power-platform-collector`. |

## Data exposure and oversharing reports

Microsoft completes SharePoint Advanced Management and Purview DSPM scans asynchronously. First
check for a recent completed result and export it. Start a new Microsoft scan only when no current
result exists, wait for completion, and pass the export to the assessment:

```powershell
python main.py --sam-report "C:\Reports\SAM" --dspm-report "C:\Reports\DSPM\assessment.csv"
```

Repeat either option to load multiple files. A directory loads supported CSV, TSV, JSON, and XLSX
files. You can also set `SAM_DAG_REPORT_PATHS` and `DSPM_REPORT_PATHS` in the selected environment
file. Missing, stale, or undated results are recorded in the report as Coverage items with the
steps required to enable and run the corresponding Microsoft capability.

Default freshness thresholds are 35 days for SAM and 8 days for DSPM. Override them with
`SAM_REPORT_MAX_AGE_DAYS` and `DSPM_REPORT_MAX_AGE_DAYS` when your governance cadence differs.

## Deployment Steps

### 1. Install Python Dependencies

Navigate to the repository folder and install required packages:

```powershell
cd <path-to-repository>
pip install -r requirements.txt
```

**Required packages:**
- `azure-identity` and `httpx` - lightweight Microsoft Graph and service API authentication/client
- `openpyxl` - Excel report generation

The generated Microsoft Graph SDK is intentionally not installed. The assessment uses only the
required REST endpoints, which avoids the SDK's very long generated paths on Windows.

### 2. Create Service Principal

Run the service principal setup script to create Azure AD app registration with required permissions:

```powershell
.\setup-service-principal.ps1
```

Use `-Mode Standard` (the default) for client-secret Graph collection with browser fallback for
SharePoint and Purview. Use `-Mode Unattended -CertificateThumbprint <thumbprint>` (or
`-CertificatePath`) to attach and validate certificate authentication for those PowerShell
collectors. Unattended setup requires an explicit confirmation before adding SharePoint
`Sites.FullControl.All`; automation can supply `-ConfirmBroadSharePointAccess`. Preview packs are
explicit, for example `-PreviewCollectors ShadowAI`. Existing
credentials and permissions are retained unless `-RotateCredential` or
`-PruneUnusedPermissions` is supplied.

**What the script does:**
1. Opens browser for admin authentication (Global Administrator or Application Administrator required)
2. Creates or reconciles the existing app registration without deleting it
3. Builds the requested application permission set from `collector-registry.json`:
   
   **Microsoft Graph API:**
   - User.Read.All - Read user profiles
   - Organization.Read.All - Read organization info
   - SecurityEvents.Read.All - Read security events
   - SecurityIncident.Read.All - Read security incidents
   - IdentityRiskyUser.Read.All - Read risky users
   - IdentityRiskEvent.Read.All - Read risk events
   - Policy.Read.All - Read policies
   - Policy.Read.PermissionGrant - Read permission grant policies used by tenant consent settings
   - RoleManagement.Read.Directory - Read directory roles
   - UserAuthenticationMethod.Read.All - Read authentication methods
   - AccessReview.Read.All - Read access reviews
   - DeviceManagementManagedDevices.Read.All - Read managed devices
   - DeviceManagementConfiguration.Read.All - Read device configurations
   - Application.Read.All - Read applications
   - AuditLog.Read.All - Read audit logs
   - Reports.Read.All - Read usage reports
   - Sites.Read.All - Read SharePoint sites
   - ExternalConnection.Read.All - Read Graph connectors
   Preview permissions are deliberately excluded unless `-PreviewCollectors` selects their pack.
   `CloudApp-Discovery.Read.All` is used only for Shadow AI; `NetworkAccess.Read.All` and
   `NetworkAccessPolicy.Read.All` are used only for Network Access.
   
   **Microsoft Defender for Endpoint API:**
   - Machine.Read.All - Read machine data
   
4. Opens admin consent and then verifies the resulting service-principal role assignments
5. Keeps an existing credential; a 90-day secret is created only when needed or when `-RotateCredential` is supplied
6. Creates `.env` file with credentials (TENANT_ID, CLIENT_ID, CLIENT_SECRET)
   - **Note:** `.env` file is excluded from git - CLIENT_SECRET is never checked into source control

### 3. Configure Assessment Scope

Choose **Option A** or **Option B** to configure which services to assess:

**Option A: Configure in params.py**

Edit `params.py` to specify your tenant and services:

```python
TENANT_ID = "contoso.onmicrosoft.com"  # or Azure AD tenant GUID

# Services to analyze - valid values: "M365", "Entra", "Defender", "Purview", "Power Platform", "Copilot Studio"
# Empty array = analyze all services
SERVICES = []  # e.g., ["M365", "Entra"], ["Defender", "Purview"], or [] for all
```

Then run:
```powershell
python main.py
```

**Option B: Pass command-line switches**

Override configuration using command-line arguments:

```powershell
# Specific services
python main.py --services M365 Defender Entra

# Services with spaces require double quotes
python main.py --services "Power Platform" "Copilot Studio" Purview

# Different tenant with specific services
python main.py --tenant-id "12345678-1234-1234-1234-123456789abc" --services Purview

# All services for specific tenant (empty --services flag)
python main.py --tenant-id "contoso.onmicrosoft.com" --services

# Aggregate usage is automatic. Add restricted user detail to the Excel workbook only.
python main.py --include-user-usage-detail --report-format both

# Import optional supplemental exports (the tool does not start long-running exports).
python main.py --copilot-dashboard-export .\exports\copilot-dashboard.csv
python main.py --power-platform-inventory .\exports\power-platform-inventory.csv

# Preview collectors are disabled by default.
python main.py --preview-collectors shadow-ai
python main.py --preview-collectors power-platform
python main.py --preview-collectors all

# Validate selected collectors without creating reports or starting scans.
python main.py --check-connections

# Explicit compatibility fallback only; this is the only runtime path that needs Az.Accounts.
python main.py --legacy-power-platform-collector
```

Connection checks return `0` when the selected configuration is usable (including legitimate
license limitations), `2` for actionable authentication/permission/role/module gaps, and `1` for
invalid configuration or an unexpected required-collector failure. They do not create assessment
reports or launch Microsoft scans.

**Note:** Service names with spaces (`"Power Platform"`, `"Copilot Studio"`) must be enclosed in double quotes.

**Configuration Examples:**

- **Full Assessment**: `SERVICES = []` or `--services` (analyzes all six service areas)
- **Targeted Assessment**: `SERVICES = ["M365", "Defender", "Entra"]` or `--services M365 Defender Entra`
- **Security Focus**: `SERVICES = ["Defender", "Entra"]` or `--services Defender Entra`
- **Compliance Focus**: `SERVICES = ["Purview"]` or `--services Purview`
- **Power Platform & Copilot**: `SERVICES = ["Power Platform", "Copilot Studio"]` or `--services "Power Platform" "Copilot Studio"`

### SharePoint sharing and oversharing collection

The default M365 run also checks SharePoint and OneDrive tenant sharing settings, site-level
sharing configuration, and the status of existing SharePoint Advanced Management Data Access
Governance reports. `setup-service-principal.ps1` installs the required SharePoint module, and
`main.py` also attempts a current-user installation if the module is missing. The manual fallback is:

```powershell
Install-Module Microsoft.Online.SharePoint.PowerShell -Scope CurrentUser -Force
```

Microsoft Graph does not expose these SharePoint administrative settings. With the normal
client-secret configuration, the SharePoint-only portion therefore requests a browser sign-in
from a SharePoint Administrator. To keep this portion unattended, configure a `.pfx` certificate
with `SHAREPOINT_CERTIFICATE_PATH` (or its certificate-store thumbprint) and the existing
`CLIENT_ID` and `TENANT_ID`. SharePoint app-only administrative access also requires the
**Office 365 SharePoint Online** application permission `Sites.FullControl.All` and tenant admin
consent. This broad permission is added only by `setup-service-principal.ps1 -Mode Unattended`
after a certificate is supplied; Standard mode continues to use browser fallback.

The client secret remains in use for Microsoft Graph. `Connect-SPOService` supports app-only
authentication with a certificate or managed identity, but not with a client secret.

### 4. Run the Assessment

The default `python main.py` run assesses all configured service areas and automatically starts the
core Purview collector. No separate DLP command is required. Purview results may be reused
for eight hours; use `--interactive-auth fresh` when you need a new sign-in and collection. Using
`--interactive-auth skip` or excluding Purview intentionally leaves these checks not assessed.

**Execution Flow:**

1. **Service Principal Authentication**:
   - Tool reads credentials from `.env` file (created in step 2)
   - Authenticates silently using CLIENT_ID and CLIENT_SECRET
   - No browser popup - authentication is automatic

2. **Data Collection Progress**:
   - **M365, Entra, Defender**: Silent authentication via service principal
   - **SharePoint and Purview**: Certificate first; otherwise a clearly announced browser sign-in
   - **Power Platform & Copilot Studio**: Imported inventory by default; preview API only when selected
   
   See [Data Collection Details](#data-collection-details) for specific APIs and cmdlets used per service.
   
   ```
   [2026-01-06 14:30:52] 🚀 Starting orchestration for: M365, Entra, Defender...
   [2026-01-06 14:30:53] ✅ M365 licenses retrieved: 5 SKUs found
   [2026-01-06 14:30:54] ✅ Entra identity protection: 12 risky users detected
   [2026-01-06 14:30:56] ✅ Defender security evidence collected
   [2026-01-06 14:31:20] 🔐 Purview: Exchange Online authentication required
   [2026-01-06 14:31:35] ✅ Purview: 12 DLP policies retrieved
   ```

3. **Report Generation**:
   ```
   [2026-01-06 14:31:05] 📊 Generating recommendations report...
   [2026-01-06 14:31:06] ✅ Report saved: Reports/m365_recommendations_20260106_143106.csv
   ```

**Estimated Execution Time:**
- M365 + Entra only: ~10-15 seconds
- All services (without Power Platform/Purview): ~30-45 seconds
- Comprehensive (all services + PowerShell collectors): ~2-3 minutes

## Post-Execution Steps

### 1. Review Assessment Report

Open the generated report from the `Reports/` folder (available in CSV and Excel formats):

```
Reports/m365_recommendations_20260106_143106.csv
Reports/m365_recommendations_20260106_143106.xlsx
```

**Report Structure:**

| Column | Description | Example Values |
|--------|-------------|----------------|
| Service | Service area assessed | M365, Entra, Defender, Purview, Power Platform, Copilot Studio |
| Feature | Specific capability or control | Copilot in Apps, Conditional Access, Security Posture |
| Status | Current state | Success, Disabled, Warning, PendingInput |
| Priority | Implementation urgency | High, Medium, Low |
| Observation | What was detected | "247 users with 12,450 files across 15 sites" |
| Recommendation | Actionable next step | "Deploy Copilot training for document-heavy teams" |
| LinkText | Reference title | "Copilot Adoption Framework" |
| LinkUrl | Microsoft Learn link | https://learn.microsoft.com/... |

### 2. Filter and Prioritize Recommendations

**In Excel/CSV viewer:**
1. Sort by **Priority** column (High → Medium → Low)
2. Filter by **Service** to focus on specific areas
3. Group by **Status** to identify gaps (Disabled, Warning)

**Priority Definitions:**
- **High**: Critical for Copilot security/compliance - address before deployment
- **Medium**: Important for optimal experience - implement during deployment
- **Low**: Enhancement opportunities - consider for future optimization

### 3. Implement Recommendations

For each recommendation:

1. **Read the Observation**: Understand current state (e.g., "12 compromised accounts detected")
2. **Review the Recommendation**: Specific action to take (e.g., "Revoke access, enforce MFA")
3. **Follow the LinkUrl**: Microsoft Learn documentation for implementation steps
4. **Track Progress**: Mark as completed in your project management system

**Example Implementation Workflow:**

| Recommendation | Owner | Due Date | Status |
|----------------|-------|----------|--------|
| Address 12 critical Defender recommendations | SecOps Team | Week 1 | In Progress |
| Implement Copilot conditional access policy | Identity Team | Week 2 | Not Started |
| Deploy sensitivity labels to SharePoint | Compliance Team | Week 3 | Not Started |

### 4. Re-run Assessment

After implementing recommendations, re-run the assessment to validate changes:

```powershell
python main.py
```

Compare new report with baseline to measure progress. Timestamped filenames preserve history:
- Baseline: `m365_recommendations_20260106_143106.csv`
- Post-remediation: `m365_recommendations_20260113_091523.csv`

## Special Scenarios

### Defender XDR Activation (First-Time Setup)

If your tenant has Defender licenses but has never accessed the portal:

1. Navigate to [Microsoft Defender Portal](https://security.microsoft.com)
2. Sign in with Security Administrator or Global Administrator account
3. Accept the "Turn on Microsoft Defender XDR" prompt
4. Select data residency region (EU, US, UK, etc.)
5. Wait 2-3 minutes for provisioning
6. Verify activation: Dashboard should display devices, incidents, recommendations
7. Run the assessment tool

**Why manual activation required:**
- Microsoft requires explicit admin consent before enabling tenant-wide monitoring
- Data residency selection cannot be changed after provisioning
- Ensures compliance/governance review before security data collection

## Troubleshooting

### Authentication Issues

**Problem:** Authentication prompt or browser popup is hidden behind other windows

**Solution:**
1. Press **Windows + D** to minimize all windows and show desktop
2. Press **Windows key** again to restore windows - the authentication popup should now be highlighted/visible
3. Alternatively, check taskbar for flashing browser icon or new window notification
4. Look for authentication popup minimized or behind VS Code/terminal windows
5. Click the browser icon in taskbar to bring popup to front
6. Complete the authentication in the popup window
7. If timeout occurs, re-run the assessment - popup should appear again

### Defender API Issues

**Problem:** Defender API returns empty data or "403 Forbidden"

**Solution:**
1. Verify Defender XDR is activated via [security.microsoft.com](https://security.microsoft.com)
2. Confirm user has Security Reader role (or higher) in Defender portal
3. Check licenses: Requires M365 E5, Microsoft 365 E5 Security, or Microsoft Defender P2
4. Wait 10-15 minutes after first Defender XDR activation for APIs to propagate

**Problem:** "DEFENDER_XDR_ACTIVATION" recommendation shows "Warning - Not Activated"

**Solution:** Follow "Defender XDR Activation" steps above (manual portal activation required)

### Purview Issues

**Problem:** Purview collector fails with "Connect-IPPSSession not recognized"

**Solution:**
```powershell
Install-Module -Name ExchangeOnlineManagement -Force
Import-Module ExchangeOnlineManagement
```

Rerun only the Purview collection with a new sign-in:

```powershell
python main.py --env-file .env --services Purview --interactive-auth fresh --report-format both
```

The signed-in user needs Compliance Administrator or equivalent read access in the target tenant.
The collector uses both Security & Compliance PowerShell and Exchange Online, so two authentication
events may be shown.

Global Reader does not grant the Purview compliance PowerShell role groups used by these cmdlets.
For the simplest complete run, use a delegated account assigned Compliance Administrator in the
target tenant. A least-privilege operator can instead use the relevant read-only Purview role groups,
including View-Only DLP Compliance Management for DLP policies and rules, Information Protection
for labels, View-Only Retention Management for retention, and Audit Reader for audit configuration.
Role changes can take time to propagate.

Insider Risk, Communication Compliance, Information Barriers, and eDiscovery require their own
specialized Purview roles and, in some tenants, matching licenses or enabled workloads. They are
not queried by a normal assessment. Set `PURVIEW_INCLUDE_SPECIALIZED=true` only when this optional
context is approved and the operator has the appropriate roles.

### Power Platform Issues

**Problem:** AI Builder inventory is reported as not assessed

**Preferred solution:** In Power Platform admin center, open **Manage > Inventory**, enable the
inventory feature if necessary, allow Microsoft to complete its inventory, export the tenant-wide
CSV, and supply it without waiting for the assessment to run:

```powershell
python main.py --env-file .env --power-platform-inventory .\exports\power-platform-inventory.csv --report-format both
```

The alternative preview path is:

```powershell
python main.py --env-file .env --preview-collectors power-platform --report-format both
```

Assign the assessment service principal the tenant-scoped **Power Platform Reader** RBAC role
(role ID `c886ad2e-27f7-4874-8381-5849b8d8a090`) at `/tenants/{tenantId}` first. This inventory API
and its RBAC support are preview. Do not grant the application the Entra Power Platform
Administrator directory role. The legacy delegated collector remains a fallback and aggregates
all readable environments; Power Platform availability never changes the core readiness decision.

### AI Usage and Shadow AI Issues

**Problem:** Copilot usage or Microsoft 365 Apps readiness says permission missing

**Solution:** Confirm the app registration identified by `CLIENT_ID` has the Microsoft Graph
application permission `Reports.Read.All`, grant admin consent in the target tenant, obtain a fresh
application token, and rerun. Unavailable data is reported as not assessed, never as zero usage.

**Problem:** Shadow AI discovery is not assessed, has no stream, or returns HTTP 403

**Solution:** This source is optional and preview. Add and consent the Microsoft Graph application
permission `CloudApp-Discovery.Read.All`, then enable a Defender for Cloud Apps discovery source:
Defender for Endpoint continuous report forwarding, a Cloud Discovery log stream, or Global Secure
Access Shadow AI discovery. Allow Microsoft to populate the stream and rerun with
`--preview-collectors shadow-ai`. Purview DSPM does not substitute for this source because DSPM
measures data and prompt risk, not aggregate adoption of external AI services.

### Cross-Tenant Permission Issues

**Problem:** `risky_users` or `risk_detections` returns HTTP 403 even though the matching
`IdentityRiskyUser.Read.All` or `IdentityRiskEvent.Read.All` application permission has admin
consent.

**Solution:** Check the Entra license before changing permissions again. Microsoft Entra ID P1
provides limited risk information; full risky-user and risk-detection reporting requires Entra ID
P2 or another qualifying Entra entitlement. The assessment reports this as a licensing coverage
limit and does not interpret unread risk data as zero risky users.

**Problem:** `NetworkAccess.Read.All permission is not granted to the service principal`

**Solution:** In the target tenant, add Microsoft Graph **application** permission
`NetworkAccess.Read.All` to the app registration matching `CLIENT_ID`, and grant tenant-wide admin
consent. The list operations used for filtering policies and forwarding profiles require this broad
read permission; `NetworkAccessPolicy.Read.All` by itself does not authorize them. Consent granted
in a different tenant is not reused. The setup script includes both permissions for target-tenant
deployments.

**Problem:** Application consent policy settings could not be read even though `Policy.Read.All`
is granted.

**Solution:** Add Microsoft Graph **application** permission `Policy.Read.PermissionGrant` and grant
tenant-wide admin consent. `Policy.Read.All` can read the authorization policy, while the separate
permission is required for the permission grant policy inventory used by this assessment.

**Problem:** Global Secure Access still returns HTTP 403 after `NetworkAccess.Read.All` is present
in a fresh application token.

**Solution:** Do not add progressively broader Graph permissions. Confirm that the tenant is
explicitly onboarded to Global Secure Access and has the required Entra Suite or standalone
licensing. If the organization does not plan to use this optional control, retain the result as an
assessment coverage limitation. The tool distinguishes this condition from a missing permission.

Collector authentication and partial-data warnings are also written to
`Reports\collector_diagnostics.log`. The log is ignored by Git and redacts bearer tokens, JWTs, and
common secret values so it can be used for local troubleshooting without placing credentials in
the repository.

### General Issues

**Problem:** "ModuleNotFoundError: No module named 'azure.identity'"

**Solution:**
```powershell
pip install -r requirements.txt
```

**Problem:** "429 Too Many Requests" error

**Solution:**
- API throttling triggered - wait 60 seconds and retry
- Reduce scope: Assess fewer services at once
- For large tenants (>10,000 users): Run during off-peak hours

## Next Steps

- **Implement Recommendations**: Prioritize High-priority items before Copilot deployment
- **Establish Baseline**: Save first assessment report as readiness baseline
- **Track Progress**: Re-run monthly to measure improvement
- **Share Results**: Review with stakeholders, security team, compliance officers
- **Plan Deployment**: Use assessment insights to build Copilot rollout plan

## Additional Resources

- [Microsoft 365 Copilot Setup Guide](https://learn.microsoft.com/microsoft-365-copilot/microsoft-365-copilot-setup)
- [Copilot Adoption Framework](https://learn.microsoft.com/microsoft-365-copilot/microsoft-365-copilot-adoption)
- [Data, Privacy, and Security for Copilot](https://learn.microsoft.com/microsoft-365-copilot/microsoft-365-copilot-privacy)
- [Defender for Endpoint Documentation](https://learn.microsoft.com/microsoft-365/security/defender-endpoint/)
- [Purview Information Protection](https://learn.microsoft.com/purview/information-protection)
- [Power Platform Admin Center](https://admin.powerplatform.microsoft.com)

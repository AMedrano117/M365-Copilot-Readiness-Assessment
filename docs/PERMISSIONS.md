# Permissions and restricted collection

[Documentation index](README.md) | [Project overview](../README.md)

The assessment runs on **application permissions first**. Every dataset lists the ways it can be collected, in order, and uses the best one that is available for the run. A signed-in administrator (delegated access) is used only to add data that Microsoft exposes exclusively to signed-in administrators, or as a fallback when no application path is configured. The implementation source of truth is [collector-registry.json](../collector-registry.json). Microsoft references below were checked on **September 22, 2026**.

Use **Restricted** when the customer wants the smallest supported application permission set while retaining tenant, user, usage, identity, device, security and external-connection evidence. Standard remains the default. Both profiles collect read operations; permissions granted to an application can authorize more than the operations this tool performs.

## How each dataset is collected

Preflight (`--check-connections`) prints an **evidence collection plan**: the path each dataset will use, its coverage, and the single most useful step to unlock more. The saved collection records the plan, and the workbook's Collection Coverage tab and the report appendix show which identity collected every source.

| Dataset | 1st choice (application) | 2nd choice (application) | Last resort (delegated) |
|---|---|---|---|
| Tenant, users, licensing, usage, Entra, Defender, external connections | Microsoft Graph / Defender API application permissions (client secret or certificate) | none | none |
| SharePoint tenant sharing settings | SharePoint admin PowerShell with certificate (full) | Microsoft Graph `SharePointTenantSettings.Read.All` (partial: no default link type, anonymous link expiry or per-site data) | SharePoint administrator browser sign-in |
| SharePoint site settings and Data Access Governance reports | SharePoint admin PowerShell with certificate | none | SharePoint administrator browser sign-in |
| Sensitivity label definitions | Purview PowerShell (application token or certificate) | Microsoft Graph beta `InformationProtectionPolicy.Read.All` (preview; definitions only) | Purview browser sign-in |
| DLP, retention, label policies, organization/IRM/audit configuration | Purview and Exchange PowerShell with an **application token** from the existing credential | Purview and Exchange PowerShell with a certificate | Purview browser sign-in |
| Report privacy (concealed user names) | Microsoft Graph `ReportSettings.Read.All` | none | none |
| Copilot interaction audit (opt-in preview) | Audit Log Query API `AuditLogsQuery.Read.All` | none | none |
| Copilot limited mode | none (Microsoft supports delegated access only) | none | Delegated Graph `CopilotSettings-LimitedMode.Read` |

**Client secret only.** With just `CLIENT_SECRET`, everything in the first column works once its permission is consented, including Purview and Exchange configuration: the tool acquires Exchange and Security & Compliance access tokens from the same credential and passes them to PowerShell over stdin (ExchangeOnlineManagement 3.8.0 or later). SharePoint administrative PowerShell is the only workload that requires a certificate; without one, tenant sharing settings come from Microsoft Graph with partial coverage.

**Run without any sign-in** with `--interactive-auth skip --delegated off`. Datasets without an application path are reported as not assessed with the step that unlocks them; the tool never opens a browser.

## Choose and run a profile

| Profile | Setup application and environment | Coverage |
|---|---|---|
| Standard | `M365 Copilot Readiness Assessment Tool`; `.env` or `-EnvironmentFile` | All application paths above, Global Reader workload role by default, optional SharePoint certificate, optional delegated enrichment. |
| Restricted | `M365 Copilot Readiness Assessment Tool - Restricted`; `.env.restricted` | Stable Graph reads only, including tenant sharing settings and report settings through Graph; excludes site/group/consent-grant inventory, labels, all administrative PowerShell, directory roles and delegated access. |

```powershell
.\setup-service-principal.ps1 -PermissionProfile Restricted
python main.py --mode live --env-file .env.restricted --check-connections
python main.py --mode live --env-file .env.restricted --reports-dir .\exports
# Use the exact COLLECTION INPUT path printed by the live run.
python main.py --mode offline --collection-input "<collection-input>" --reports-dir .\exports
```

Create `exports` before using it, or omit `--reports-dir` until exports are available. Live selection is `--permission-profile standard|restricted`, then `PERMISSION_PROFILE` from the selected environment, then `standard`. Setup writes `PERMISSION_PROFILE=restricted` to `.env.restricted`; selecting a profile on the assessment command does not create an app or remove existing consent. Use the dedicated Restricted application and run preflight before collecting.

Restricted rejects Unattended setup, preview packs, `-WorkloadRbac` roles, `-EnableSharePointAppOnly`, delegated sign-in and legacy administrative collection. Its exclusions apply even when the environment already contains SharePoint/Purview certificate settings. Excluded sources are recorded as `not_requested` with the profile reason and remain unassessed; preflight does not request missing permissions for them. The collection and portable package preserve this profile and its exclusions during offline replay. Omit both `--permission-profile` and `--env-file` offline; the CLI rejects those live-only options. Older collections without this metadata show the profile as unrecorded.

`--services` reduces the requests made during a run. It does **not** reduce or revoke the application's existing permissions.

## Runtime application permissions

All rows in this table are **application** permissions. `Both` means Standard and Restricted. `Opt-in` means a setup switch adds it. Graph permissions use the Microsoft Graph resource (`00000003-0000-0000-c000-000000000000`). The purpose column describes the tool's use, not every operation the permission can authorize.

| API resource | Permission | Purpose and capability | Profiles | Microsoft reference |
|---|---|---|---|---|
| Graph | `Organization.Read.All` | Tenant identity and subscription inventory | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#organizationreadall) |
| Graph | `User.Read.All` | Users and assigned-license evidence | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#userreadall) |
| Graph | `Application.Read.All` | Enterprise application inventory and assessment application's permission audit | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#applicationreadall) |
| Graph | `Reports.Read.All` | Microsoft 365 and Copilot usage | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#reportsreadall) |
| Graph | `ReportSettings.Read.All` | Whether usage reports conceal user names | Both | [Get report settings](https://learn.microsoft.com/en-us/graph/api/adminreportsettings-get?view=graph-rest-1.0) |
| Graph | `SharePointTenantSettings.Read.All` | Tenant sharing capability, domain restrictions, resharing, legacy authentication | Both | [Get SharePoint settings](https://learn.microsoft.com/en-us/graph/api/sharepointsettings-get?view=graph-rest-1.0) |
| Graph | `Policy.Read.All` | Conditional Access, authentication and authorization policies | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#policyreadall) |
| Graph | `Policy.Read.PermissionGrant` | Permission-grant policy definitions | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#policyreadpermissiongrant) |
| Graph | `RoleManagement.Read.Directory` | Privileged directory-role assignments and definitions; Restricted audit of the application's own directory roles | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#rolemanagementreaddirectory) |
| Graph | `AccessReview.Read.All` | Access-review definitions and outcomes | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#accessreviewreadall) |
| Graph | `DeviceManagementManagedDevices.Read.All` | Managed-device inventory and compliance state | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#devicemanagementmanageddevicesreadall) |
| Graph | `DeviceManagementConfiguration.Read.All` | Device configuration/compliance policies | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#devicemanagementconfigurationreadall) |
| Graph | `AuditLog.Read.All` | Sign-ins, audits and MFA registration details | Both | [Registration report](https://learn.microsoft.com/en-us/graph/api/authenticationmethodsroot-list-userregistrationdetails?view=graph-rest-1.0) |
| Graph | `IdentityRiskyUser.Read.All` | Risky-user evidence | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#identityriskyuserreadall) |
| Graph | `IdentityRiskEvent.Read.All` | Identity risk detections | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#identityriskeventreadall) |
| Graph | `ExternalConnection.Read.All` | External grounding-connection inventory | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#externalconnectionreadall) |
| Graph | `SecurityEvents.Read.All` | Secure Score evidence | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#securityeventsreadall) |
| Graph | `SecurityAlert.Read.All` | Security alerts v2 | Both | [Alerts API](https://learn.microsoft.com/en-us/graph/api/security-list-alerts_v2?view=graph-rest-1.0) |
| Graph | `SecurityIncident.Read.All` | Defender incident evidence | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#securityincidentreadall) |
| WindowsDefenderATP (`fc780465-2017-40d4-a0c5-307022471b92`) | `Machine.Read.All` | Defender for Endpoint onboarding and risk inventory | Both | [Machines API](https://learn.microsoft.com/en-us/defender-endpoint/api/get-machines) |
| Graph | `InformationProtectionPolicy.Read.All` | Sensitivity label definitions (beta API; preview-quality evidence) | Standard | [List sensitivity labels](https://learn.microsoft.com/en-us/graph/api/security-informationprotection-list-sensitivitylabels?view=graph-rest-beta) |
| Graph | `Sites.Read.All` | Graph site inventory; no live site count in Restricted | Standard | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#sitesreadall) |
| Graph | `Group.Read.All` | Group licensing inventory; not assessed in Restricted | Standard | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#groupreadall) |
| Graph | `Directory.Read.All` | `/oauth2PermissionGrants` inventory; consent policies remain available in Restricted, but grant inventory does not | Standard | [Grant inventory API](https://learn.microsoft.com/en-us/graph/api/oauth2permissiongrant-list?view=graph-rest-1.0) |
| Exchange Online (`00000002-0000-0ff1-ce00-000000000000`) | `Exchange.ManageAsApp` | App-only Exchange configuration reads (organization, IRM, audit), subject to a workload role | Standard | [App-only authentication](https://learn.microsoft.com/en-us/powershell/exchange/app-only-auth-powershell-v2) |
| Exchange Online Protection (`00000007-0000-0ff1-ce00-000000000000`) | `Exchange.ManageAsApp` | App-only Purview configuration reads (DLP, labels, retention), subject to a workload role | Standard | [App-only authentication](https://learn.microsoft.com/en-us/powershell/exchange/app-only-auth-powershell-v2) |
| SharePoint Online (`00000003-0000-0ff1-ce00-000000000000`) | `Sites.FullControl.All` | App-only tenant/site sharing and existing DAG report administration; broad full-control permission | Opt-in (`-EnableSharePointAppOnly`) or Unattended | [SharePoint app-only connection](https://learn.microsoft.com/en-us/powershell/sharepoint/sharepoint-online/connect-sharepoint-online) |
| Graph | `AuditLogsQuery.Read.All` | Copilot interaction audit (aggregates only) | Opt-in preview (`-PreviewCollectors CopilotAudit`) | [Audit Log Query API](https://learn.microsoft.com/en-us/graph/api/security-auditcoreroot-post-auditlogqueries?view=graph-rest-1.0) |

`Exchange.ManageAsApp` is now requested by default in Standard. On its own it grants nothing: the application also needs a workload role (below). Licensing and provisioning still apply; consent does not guarantee a dataset is available (for example, full identity-risk evidence requires Entra ID P2). Inspect source coverage after collection.

**Existing applications need updated administrator consent** for `SharePointTenantSettings.Read.All`, `ReportSettings.Read.All`, `InformationProtectionPolicy.Read.All` (Standard) and `Exchange.ManageAsApp` (Standard). Until then preflight reports those collectors as *Permission missing* and exits with code 2; the rest of the assessment is unaffected. Removing a permission from the manifest does not revoke an earlier grant.

Optional Standard packs add Graph application permissions `CloudApp-Discovery.Read.All` (Shadow AI), `NetworkAccess.Read.All` and `NetworkAccessPolicy.Read.All` (Global Secure Access), or `AuditLogsQuery.Read.All` (Copilot audit). Power Platform preview uses tenant-scoped Power Platform Reader RBAC instead of a Graph application permission. Legacy Power Platform uses delegated administrative access. Restricted does not support these live collectors; a supported Power Platform inventory export remains usable.

## Workload roles for app-only Purview and Exchange

`Exchange.ManageAsApp` authenticates the application; a role decides what it can read. When `-WorkloadRbac` is omitted, the Standard permission profile assigns Global Reader in both Standard and Unattended modes. This grants broad tenant read access, including Graph access beyond the application's consented permissions. Restricted defaults to `None` and rejects the other role options. Override Standard's default with `setup-service-principal.ps1 -WorkloadRbac`:

| Option | What setup does | Trade-off |
|---|---|---|
| `GlobalReader` (Standard profile default) | Assigns the Global Reader directory role | Broad read-only coverage, expected to add retention, organization and rights management configuration; verify actual coverage with preflight and the completed collection. Also grants broad additional Graph read access. |
| `SecurityReader` (explicit alternative) | Assigns the Security Reader directory role to the application | Verified: DLP policies and rules, sensitivity labels, label publishing policies and audit configuration. Not exposed: retention policies, organization configuration (`Get-OrganizationConfig`) and rights management configuration (`Get-IRMConfiguration`); preflight reports **Ready with gaps** and lists them. Also lets the application read some directory and security data through Graph beyond its consented permissions. |
| `RoleGroups` (explicit alternative) | Creates the "AI Readiness Purview/Exchange Read-Only" role groups from management roles that expose the collected cmdlets | Scoped to workload RBAC, but whole management roles can include write cmdlets; review the actual assignments. |
| `None` (Restricted default; explicit Standard alternative) | Skips workload role assignment; Standard still requests `Exchange.ManageAsApp` | Does not remove existing roles. Assign a role separately for app-only reads. A successful application connection can still lack cmdlets, so this option does not guarantee browser fallback. |

Directory-role assignment needs an administrator with Privileged Role Administrator. Setup requests `RoleManagement.ReadWrite.Directory` for `GlobalReader` or `SecurityReader`, including the Standard profile's default Global Reader assignment. Microsoft lists the supported roles in [App-only authentication, step 5](https://learn.microsoft.com/en-us/powershell/exchange/app-only-auth-powershell-v2#step-5-assign-role-permissions-to-the-application). A failed read under an application session is reported as *Role missing* with the setup command that fixes it. eDiscovery cmdlets are not supported for app-only access and are never requested in an application session.

Remove directory roles after the engagement with `cleanup-service-principal.ps1 -IncludeWorkloadRbac`. The Restricted permission audit fails if **any** directory role is assigned to the Restricted application.

## SharePoint administration without a browser

SharePoint administrative PowerShell supports app-only access only with a certificate and `Sites.FullControl.All`; Microsoft offers no narrower application permission for it. Opt in with:

```powershell
.\setup-service-principal.ps1 -EnableSharePointAppOnly
```

Setup asks you to type `YES` (or pass `-ConfirmBroadSharePointAccess`), creates a non-exportable CSP certificate in `Cert:\CurrentUser\My` (valid 12 months; supply `-CertificateThumbprint` or `-CertificatePath` to use your own), attaches it to the application, requests `Sites.FullControl.All`, and writes `SHAREPOINT_CERTIFICATE_THUMBPRINT` and `PURVIEW_CERTIFICATE_THUMBPRINT`. Graph keeps using the client secret. If you decline, tenant sharing settings still come from Microsoft Graph with partial coverage.

## Delegated enrichment

Standard setup adds the delegated scope `CopilotSettings-LimitedMode.Read` and the `http://localhost` public-client redirect (public client flows stay disabled). At run time `--delegated auto` (default) uses a cached administrator sign-in or prompts once, before collection starts, only in an interactive terminal; `--delegated off` never signs in; `--delegated required` fails if no sign-in completes. The sign-in account needs at least Global Reader. The token cache is protected by Windows DPAPI under `.cache/delegated/`; the collection records the signed-in UPN as the identity that read delegated datasets. Pass `-NoDelegatedEnrichment` to setup to skip the scope and redirect. Restricted never uses delegated access.

## Setup, delegated access and workload roles

Setup signs the administrator into Graph with delegated `Application.ReadWrite.All` to manage the application and delegated `Organization.Read.All` to establish tenant identity. It also requests `RoleManagement.ReadWrite.Directory` for `SecurityReader` or `GlobalReader`, including Standard's default role assignment. These setup scopes are separate from the assessment application's runtime grants. Runtime consent continues through the administrator consent flow. Creating an application and consenting Graph application permissions are different privileges: arrange Privileged Role Administrator, Global Administrator or an appropriate custom consent role. [Microsoft consent requirements](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/grant-admin-consent).

For multiple customers keep one environment file per customer and target it explicitly: `.\setup-service-principal.ps1 -EnvironmentFile .\contoso.env`. Setup then signs in to that file's tenant, reconciles that file's application (never creating a replacement), and writes `EXPECTED_TENANT_DOMAIN`, which live runs verify before reading any evidence. `-TenantId` and `-ApplicationId` override the saved values.

Standard delegated SharePoint collection requires the customer's SharePoint Administrator access; applicable detailed DAG reports can need additional SAM access. Standard delegated Purview/Exchange collection needs roles exposing the collected DLP, label, retention, rights-management and audit commands. Portal reader access alone does not establish all PowerShell access. Export creators retain their own portal roles; those roles are not granted to the Restricted application. See [per-report roles and licensing](PORTAL_REPORTS_AND_OFFLINE.md#permissions-and-licensing-to-arrange).

Standard SharePoint administration also needs the actual admin-center HTTPS origin, configured with setup's `-SharePointAdminUrl`, runtime `--sharepoint-admin-url`, or the selected environment's `SHAREPOINT_ADMIN_URL`. The tool does not infer this hostname from the initial tenant domain. A missing URL in noninteractive collection is a configuration gap, not a missing permission; SharePoint administration remains unassessed, while Graph tenant settings are still collected. See [how to confirm and configure the URL](prereq.md#sharepoint-admin-url).

## Existing applications and credentials

Setup refuses multiple applications matching its target name and only reuses saved credentials when both tenant and client identity match. Resolve duplicate registrations with the tenant administrator; do not guess which registration to alter. Restricted setup and live preflight compare requested permissions, actual service-principal application grants and directory-role assignments against the profile and stop on excess access.

If excess access is reported, have an authorized administrator review the target app registration and its Enterprise application permissions and roles, remove unwanted requested permissions, and separately revoke the unwanted grants. Then repeat setup and preflight. The tool never revokes consent automatically, and manifest reconciliation alone is not evidence of revocation. [Review and revoke granted permissions](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/manage-application-permissions).

Graph requires a readable `CERTIFICATE_PATH` containing the private key or a usable `CLIENT_SECRET`. A certificate-store thumbprint alone works for workload PowerShell but cannot authenticate the Python Graph client. Certificate passwords and workload access tokens are passed to PowerShell over stdin, never on the command line, and are redacted from diagnostics. For final removal of a **dedicated** assessment application, use [CLEANUP.md](CLEANUP.md).

## Verified against a test tenant

| Behavior | Status on September 22-23, 2026 |
|---|---|
| Graph baseline reads with a client secret; tenant confirmation; evidence plan; no browser with `--interactive-auth skip --delegated off` | Verified live |
| Sensitivity labels through Graph beta with `InformationProtectionPolicy.Read.All` | Verified live (26 definitions returned) |
| Exchange and Security & Compliance access tokens acquired with a client secret; missing `Exchange.ManageAsApp` detected from the token and reported before PowerShell starts | Verified live |
| `SharePointTenantSettings.Read.All`, `ReportSettings.Read.All`, `AuditLogsQuery.Read.All` responses | Endpoints reached (HTTP 403 until consented); field mapping follows Microsoft's documented schema |
| `Connect-ExchangeOnline` and `Connect-IPPSSession -AccessToken` with client-secret tokens (`Exchange.ManageAsApp` on both resources) | Verified live in Windows PowerShell 5.1 and PowerShell 7 (ExchangeOnlineManagement 3.9 and 3.10) |
| Purview cmdlets exposed to Security Reader | Verified live: `Get-DlpCompliancePolicy`, `Get-DlpComplianceRule`, `Get-Label`, `Get-LabelPolicy`, `Get-AdminAuditLogConfig`, `Get-DlpSensitiveInformationType`, `Get-AutoSensitivityLabelPolicy`. Not exposed: `Get-RetentionCompliancePolicy`, `Get-OrganizationConfig`, `Get-IRMConfiguration`, `Get-InsiderRiskPolicy`, `Get-ComplianceCase` |
| Full app-only collection with Security Reader (no browser) | Verified live: 5 of 8 core Purview datasets read with an application token |
| Restricted SharePoint Search and Restricted Content Discovery properties | Read only when the installed module exposes them; not yet verified live |
| Delegated limited-mode sign-in | Consent and the `http://localhost` public-client redirect verified; the interactive sign-in itself is not yet verified live |

## Evidence gaps and offline replay

| Supplemental input | What it can establish | What it does not replace |
|---|---|---|
| Supported SAM/DAG permission and sharing exports | Exported scope's access, sharing and exposure evidence | Live tenant/site configuration or the Graph site inventory |
| Supported Purview DSPM exports | Dated, scoped sensitive-data exposure evidence | DLP/label/retention/rights-management/audit configuration |
| Previously saved collection or explicit historical report/Purview cache | Only the evidence actually preserved, with original dates and qualifications | New collection, current access validation, or missing raw evidence |
| Portal PDFs and reviewed notes | Visible context and separately recorded owner reviews | Automatic control passes or structured metrics unsupported by the capture |

Restricted does not turn skipped checks into zero findings or a healthy result. Review the remaining unassessed controls and their owners before making a rollout decision. See [supported export schemas and replay](PORTAL_REPORTS_AND_OFFLINE.md).

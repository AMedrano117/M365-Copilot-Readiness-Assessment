# Permissions and restricted collection

[Documentation index](README.md) | [Project overview](../README.md)

Use **Restricted** when the customer wants the smallest supported application permission set while retaining tenant, user, usage, identity, device, security and external-connection evidence. Standard remains the default. Both profiles collect read operations; permissions granted to an application can authorize more than the operations this tool performs. The implementation source of truth is [collector-registry.json](../collector-registry.json). Microsoft references below were checked on **September 16, 2026**.

## Choose and run a profile

| Profile | Setup application and environment | Coverage |
|---|---|---|
| Standard | `M365 Copilot Readiness Assessment Tool`; `.env` | Stable API collection plus delegated SharePoint/Purview administration, or certificate administration with Unattended setup. |
| Restricted | `M365 Copilot Readiness Assessment Tool - Restricted`; `.env.restricted` | Stable reads listed below; excludes Graph site inventory, group licensing inventory, delegated consent-grant inventory and all administrative PowerShell. Use supported customer exports or previously saved evidence for additional coverage. |

```powershell
.\setup-service-principal.ps1 -PermissionProfile Restricted
python main.py --mode live --env-file .env.restricted --check-connections
python main.py --mode live --env-file .env.restricted --reports-dir .\exports
# Use the exact COLLECTION INPUT path printed by the live run.
python main.py --mode offline --collection-input "<collection-input>" --reports-dir .\exports
```

Create `exports` before using it, or omit `--reports-dir` until exports are available. Live selection is `--permission-profile standard|restricted`, then `PERMISSION_PROFILE` from the selected environment, then `standard`. Setup writes `PERMISSION_PROFILE=restricted` to `.env.restricted`; selecting a profile on the assessment command does not create an app or remove existing consent. Use the dedicated Restricted application and run preflight before collecting.

Restricted rejects Unattended setup, preview packs and legacy administrative collection. Its exclusions apply even when the environment already contains SharePoint/Purview certificate settings. Excluded sources are recorded as `not_requested` with the profile reason and remain unassessed; preflight does not request missing permissions for them. The collection and portable package preserve this profile and its exclusions during offline replay. Omit both `--permission-profile` and `--env-file` offline; the CLI rejects those live-only options. Older collections without this metadata show the profile as unrecorded.

`--services` reduces the requests made during a run. It does **not** reduce or revoke the application's existing permissions.

## Runtime application permissions

All rows in this table are **application** permissions. `Both` means Standard and Restricted. `Unattended` means Standard with `-Mode Unattended`; those grants are not part of normal Standard setup. Graph permissions use the Microsoft Graph resource (`00000003-0000-0000-c000-000000000000`). The purpose column describes the tool's use, not every operation the permission can authorize.

| API resource | Permission | Purpose and capability | Profiles | Microsoft reference |
|---|---|---|---|---|
| Graph | `Organization.Read.All` | Tenant identity and subscription inventory | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#organizationreadall) |
| Graph | `User.Read.All` | Users and assigned-license evidence | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#userreadall) |
| Graph | `Application.Read.All` | Enterprise application inventory and assessment application's permission audit | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#applicationreadall) |
| Graph | `Reports.Read.All` | Microsoft 365 and Copilot usage | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#reportsreadall) |
| Graph | `Policy.Read.All` | Conditional Access, authentication and authorization policies | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#policyreadall) |
| Graph | `Policy.Read.PermissionGrant` | Permission-grant policy definitions | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#policyreadpermissiongrant) |
| Graph | `RoleManagement.Read.Directory` | Privileged directory-role assignments and definitions | Both | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#rolemanagementreaddirectory) |
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
| Graph | `Sites.Read.All` | Graph site inventory; no live site count in Restricted | Standard | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#sitesreadall) |
| Graph | `Group.Read.All` | Group licensing inventory; not assessed in Restricted | Standard | [Reference](https://learn.microsoft.com/en-us/graph/permissions-reference#groupreadall) |
| Graph | `Directory.Read.All` | `/oauth2PermissionGrants` inventory; consent policies remain available in Restricted, but grant inventory does not | Standard | [Grant inventory API](https://learn.microsoft.com/en-us/graph/api/oauth2permissiongrant-list?view=graph-rest-1.0) |
| SharePoint Online (`00000003-0000-0ff1-ce00-000000000000`) | `Sites.FullControl.All` | App-only tenant/site sharing and existing DAG report administration; broad full-control permission | Unattended | [SharePoint app-only connection](https://learn.microsoft.com/en-us/powershell/sharepoint/sharepoint-online/connect-sharepoint-online) |
| Exchange Online (`00000002-0000-0ff1-ce00-000000000000`) | `Exchange.ManageAsApp` | App-only Exchange configuration reads, subject to workload RBAC | Unattended | [App-only authentication](https://learn.microsoft.com/en-us/powershell/exchange/app-only-auth-powershell-v2) |
| Exchange Online Protection (`00000007-0000-0ff1-ce00-000000000000`) | `Exchange.ManageAsApp` | App-only Purview configuration reads, subject to workload RBAC | Unattended | [App-only authentication](https://learn.microsoft.com/en-us/powershell/exchange/app-only-auth-powershell-v2) |

Licensing and provisioning still apply. Consent does not guarantee a dataset is available; for example, full identity-risk evidence requires Entra ID P2. Inspect source coverage after collection.

**Existing Standard applications need updated administrator consent** for `Directory.Read.All` and `SecurityAlert.Read.All` if not already granted. `UserAuthenticationMethod.Read.All` is no longer requested in either profile: the registration report uses `AuditLog.Read.All`. These are permission corrections for existing endpoints, not new authentication-method collection. Removing a permission from the manifest does not revoke an earlier grant.

Optional Standard packs add Graph application permissions `CloudApp-Discovery.Read.All` (Shadow AI), or `NetworkAccess.Read.All` and `NetworkAccessPolicy.Read.All` (Global Secure Access). Their capabilities and provisioning requirements are supplemental; see the [Graph reference](https://learn.microsoft.com/en-us/graph/permissions-reference). Power Platform preview uses tenant-scoped Power Platform Reader RBAC instead of a Graph application permission. Legacy Power Platform uses delegated administrative access. Restricted does not support these live collectors; a supported Power Platform inventory export remains usable.

## Setup, delegated access and workload roles

Setup signs the administrator into Graph with delegated `Application.ReadWrite.All` to manage the application and delegated `Organization.Read.All` to establish tenant identity. These setup scopes are separate from the assessment application's runtime grants. Runtime consent continues through the administrator consent flow. Creating an application and consenting Graph application permissions are different privileges: arrange Privileged Role Administrator, Global Administrator or an appropriate custom consent role. [Microsoft consent requirements](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/grant-admin-consent).

Standard delegated SharePoint collection requires the customer's SharePoint Administrator access; applicable detailed DAG reports can need additional SAM access. Standard delegated Purview/Exchange collection needs roles exposing the collected DLP, label, retention, rights-management and audit commands. Portal reader access alone does not establish all PowerShell access. Export creators retain their own portal roles; those roles are not granted to the Restricted application. See [per-report roles and licensing](PORTAL_REPORTS_AND_OFFLINE.md#permissions-and-licensing-to-arrange).

Standard SharePoint administration also needs the actual admin-center HTTPS origin, configured with setup's `-SharePointAdminUrl`, runtime `--sharepoint-admin-url`, or the selected environment's `SHAREPOINT_ADMIN_URL`. The tool does not infer this hostname from the initial tenant domain. A missing URL in noninteractive collection is a configuration gap, not a missing permission; SharePoint administration remains unassessed. Restricted does not use this setting. See [how to confirm and configure the URL](prereq.md#sharepoint-admin-url).

Unattended setup selects entire existing Exchange/Purview management roles containing the required `Get-*` commands, preferring roles with read/view-only names. It does not create roles limited to those individual commands or to selected customer data. A selected role can include write commands and wider workload capabilities; an existing role group may also retain older assignments. The names `AI Readiness Purview Read-Only` and `AI Readiness Exchange Read-Only` do not guarantee that the effective access is read-only or narrowly scoped. Have the workload administrator review the actual assignments. [Exchange permissions](https://learn.microsoft.com/en-us/exchange/permissions-exo/permissions-exo), [Purview permissions](https://learn.microsoft.com/en-us/purview/purview-permissions).

## Existing applications and credentials

Setup refuses multiple applications matching its target name and only reuses saved credentials when both tenant and client identity match. Resolve duplicate registrations with the tenant administrator; do not guess which registration to alter. Restricted setup and live preflight compare requested permissions and actual service-principal application grants against the profile and stop on excess access.

If excess access is reported, have an authorized administrator review the target app registration and its Enterprise application permissions, remove unwanted requested permissions, and separately revoke the unwanted grants. Review workload RBAC separately if the application previously had administrative access. Then repeat setup and preflight. The tool never revokes consent automatically, and manifest reconciliation alone is not evidence of revocation. [Review and revoke granted permissions](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/manage-application-permissions).

Graph requires a readable `CERTIFICATE_PATH` containing the private key or a usable `CLIENT_SECRET`. A certificate-store thumbprint alone works for supported administrative PowerShell paths but cannot authenticate the Python Graph client. Initial setup's `-CertificatePath` accepts an unprotected private-key PFX. Encrypted saved Graph files also require `CERTIFICATE_PASSWORD` and are reusable only for the matching existing tenant/client identity and registered certificate. A fresh thumbprint configuration can explicitly use `-RotateCredential` to create a separate Graph secret; otherwise setup stops if no usable Graph credential exists. See [certificate setup](prereq.md#unattended-sharepoint-and-purview).

For final removal of a **dedicated** assessment application, use [CLEANUP.md](CLEANUP.md), rather
than treating manifest changes, secret expiry or local file deletion as access teardown. Its cloud
script previews by default and deletes only the verified target app and enterprise application
when explicitly applied. Standard Unattended workload references are cleaned up first when selected.
Shared applications and extra/manual assignments require separate administrator review.

## Evidence gaps and offline replay

| Supplemental input | What it can establish | What it does not replace |
|---|---|---|
| Supported SAM/DAG permission and sharing exports | Exported scope's access, sharing and exposure evidence | Live tenant/site configuration or the Graph site inventory |
| Supported Purview DSPM exports | Dated, scoped sensitive-data exposure evidence | DLP/label/retention/rights-management/audit configuration |
| Previously saved collection or explicit historical report/Purview cache | Only the evidence actually preserved, with original dates and qualifications | New collection, current access validation, or missing raw evidence |
| Portal PDFs and reviewed notes | Visible context and separately recorded owner reviews | Automatic control passes or structured metrics unsupported by the capture |

Restricted does not turn skipped checks into zero findings or a healthy result. Review the remaining unassessed controls and their owners before making a rollout decision. See [supported export schemas and replay](PORTAL_REPORTS_AND_OFFLINE.md).

# Prerequisites

[Documentation index](README.md) | [Project overview](../README.md)

These tenant permissions and network requirements apply to connected collection. Follow [RUN.md](RUN.md) for the canonical workflow. Every live assessment automatically saves a collection JSON and portable package; `--save-collection PATH` only chooses a custom destination. Preflight does not save a collection, and offline builds do not create or overwrite one. Rebuilding with `--mode offline --collection-input PATH` needs local Python dependencies and the full package, but no tenant credentials, network calls or administrative PowerShell. [The portal guide](PORTAL_REPORTS_AND_OFFLINE.md) covers report requests and sample compatibility.

## Local requirements

- Windows 10/11 or Windows Server with Windows PowerShell 5.1.
- Python 3.10 or later.
- Network access to Microsoft sign-in, Microsoft Graph, Defender, SharePoint Online, Exchange Online, and Purview endpoints used by the selected collectors.
- An Entra administrator who can create or update an application registration, plus an administrator authorized to grant Microsoft Graph application consent. Application Administrator alone cannot grant Graph application permissions; arrange Privileged Role Administrator, Global Administrator, or an appropriate custom consent role. [Microsoft consent guidance](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/grant-admin-consent).

Create the Python environment and install only the packages used by the tool:

If the repository is in OneDrive, use the [environment setup outside OneDrive](PYTHON_ENVIRONMENT.md) instead. `.gitignore` does not stop OneDrive from syncing a local `.venv`.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.lock.txt
```

The runtime Graph client uses `azure-identity` and `httpx`; it does not require the generated `msgraph-sdk` package.

## Recommended setup

Standard mode is the normal operator-assisted configuration:

```powershell
.\setup-service-principal.ps1 -Mode Standard -SharePointAdminUrl "https://<actual-prefix>-admin.sharepoint.com"
python main.py --mode live --check-connections
python main.py --mode live
```

Replace the URL placeholder with the actual origin copied from the customer's **Microsoft 365 admin center > Admin centers > SharePoint**. Setup saves it in `.env` for preflight and collection; see [SharePoint URL requirements](#sharepoint-admin-url).

For customers seeking the least-privilege supported profile, configure Restricted instead:

```powershell
.\setup-service-principal.ps1 -PermissionProfile Restricted
python main.py --mode live --env-file .env.restricted --check-connections
python main.py --mode live --env-file .env.restricted --reports-dir .\exports
python main.py --mode offline --collection-input "<collection-input>" --reports-dir .\exports
```

Create `exports` first or omit `--reports-dir`; replace `<collection-input>` with the path printed by the live run. Restricted uses its own application, `M365 Copilot Readiness Assessment Tool - Restricted`, and `.env.restricted`. Live profile precedence is `--permission-profile`, then `PERMISSION_PROFILE` from the selected environment, then `standard`. Restricted omits Graph site/group/consent-grant inventory and all SharePoint/Purview administrative PowerShell, while retaining stable identity/device/security/usage reads. It rejects Unattended setup, preview packs and legacy administrative collection before setup changes or collection. See [PERMISSIONS.md](PERMISSIONS.md) for the exact access matrix and evidence gaps.

The setup script reuses an unambiguous application with the configured display name, reconciles its permission manifest, and installs or updates modules for the selected profile. Multiple matching applications cause setup to stop. It keeps an existing usable credential only when its saved tenant and client identity match, unless `-RotateCredential` is supplied. Standard mode uses the client secret or certificate for application APIs and requests browser sign-in for SharePoint and Purview when no certificate is configured. Restricted neither installs/checks workload modules nor starts administrative PowerShell.

Setup verifies a reused secret with a token request and checks its recorded `CLIENT_SECRET_KEY_ID` against the application's active credentials. It saves that ID and `CLIENT_SECRET_EXPIRES_AT`, warning when expiry is within 14 days. An older secret can still be verified without metadata; if several active keys exist, setup cannot infer which expiry belongs to it and recommends `-RotateCredential`. A network or inconclusive authentication failure stops setup before credential changes.

Microsoft documents these fields in [passwordCredential](https://learn.microsoft.com/en-us/graph/api/resources/passwordcredential?view=graph-rest-1.0); a newly created secret value is returned only once by [addPassword](https://learn.microsoft.com/en-us/graph/api/application-addpassword?view=graph-rest-1.0). References verified September 17, 2026.

New credentials and their complete environment configuration are saved atomically before the consent browser and workload RBAC steps. If those later steps fail, rerun setup with the same profile and mode, omitting `-RotateCredential` so the saved credential is reused. If replacement of the environment file fails, setup identifies a private recovery file holding the new configuration; follow that message to restore the selected environment before rerunning. Treat the recovery file as a credential, keep it out of deliverables, and remove the recovery copy after the normal file is restored and verified. Existing application credentials are not automatically revoked during rotation.

Standard setup installs:

- Microsoft Graph PowerShell modules used only to configure the application.
- ExchangeOnlineManagement 3.7.2 or later (stable) for Purview and Exchange configuration. This is the minimum supported stable version for the scripts' `-DisableWAM` use. [Microsoft parameter documentation](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/connect-ippssession?view=exchange-ps#-disablewam).
- SharePoint Online Management Shell in Windows PowerShell, including the Data Access Governance cmdlets. Item-level Everyone/EEEU coverage requires version 16.0.27215.12000 or later.

`Az.Accounts` is not installed or used by a normal assessment. It is needed only when `--legacy-power-platform-collector` is explicitly selected.

## Authentication and permissions

The [canonical permissions guide](PERMISSIONS.md) distinguishes setup administrator scopes, runtime application permissions, delegated customer roles and workload RBAC. Setup uses delegated Graph `Application.ReadWrite.All` and `Organization.Read.All`, then the administrator consent flow for runtime access. Standard apps missing `Directory.Read.All` (delegated grant inventory) or `SecurityAlert.Read.All` (alerts v2) require updated consent. `UserAuthenticationMethod.Read.All` is not requested: `AuditLog.Read.All` covers the registration report.

Restricted setup and live preflight stop on excess requested or granted application permissions. Have an authorized administrator remove unwanted manifest entries and separately revoke unwanted service-principal grants before retrying. The tool does not automatically revoke access. `--services` changes collection scope without reducing granted access.

| Collector | Default authentication | Required access | Licensing or provisioning notes |
|---|---|---|---|
| Microsoft Graph tenant, licensing, usage, identity, consent, devices, security, and external connections | Application certificate or client secret | Application permissions reconciled by `setup-service-principal.ps1` | Individual workloads can still be unavailable when the tenant is not licensed or provisioned. |
| Defender for Endpoint device inventory | Application certificate or client secret | `Machine.Read.All` on WindowsDefenderATP | Requires Defender for Endpoint provisioning. |
| SharePoint tenant, sites, sharing, and existing DAG reports (Standard only) | Delegated browser sign-in, or application certificate | SharePoint Administrator for delegated use; `Sites.FullControl.All` for app-only administrative PowerShell | Snapshot reports require SharePoint Advanced Management entitlement. Detailed Everyone/EEEU reports additionally require the SharePoint Advanced Management Administrator role. |
| Purview DLP, labels, retention, rights management, and audit configuration (Standard only) | Delegated browser sign-in, or supported application certificate | Appropriate delegated Purview/Exchange roles, or the application role groups configured by Unattended setup | Feature availability depends on the tenant’s Purview and Exchange subscriptions. |
| Power Platform inventory export | No live sign-in | An exported Manage > Inventory CSV | Supplemental only. |
| Power Platform inventory API | Application credential | Tenant-scoped Power Platform Reader RBAC | Preview and opt-in. |
| Defender Cloud Apps discovery | Application credential | `CloudApp-Discovery.Read.All` | Preview and opt-in; requires a populated discovery data stream. |
| Global Secure Access | Application credential | `NetworkAccess.Read.All` and `NetworkAccessPolicy.Read.All` | Preview and opt-in; requires the feature to be provisioned. |

The stable Graph permission sets and allowed sources are defined in `collector-registry.json`. Restricted retains organization, user, application, reporting, Entra policy, directory-role, MFA registration, access-review, managed-device, audit, identity-risk, external-connection, alert, incident and Secure Score reads. Its site, group-licensing and delegated consent-grant inventories are deliberately unassessed; excluded sources must not be treated as empty inventories or healthy controls.

Microsoft Entra ID Protection is checked separately from Conditional Access. When the risk permission is present but the tenant only has Entra ID P1, unavailable risk evidence is reported as a licensing limitation rather than a missing permission.

Portal report roles are separate from application API permissions. Confirm current SharePoint entitlement and the content-metadata access granted by the additional SAM administrator role against [SAM prerequisites](https://learn.microsoft.com/en-us/sharepoint/sharepoint-advanced-management-prerequisites). DSPM assessment readers and creators have different permissions; viewing file details needs additional Content Explorer roles. Check [DSPM permissions](https://learn.microsoft.com/en-us/purview/data-security-posture-management-permissions) and [the per-report access table](PORTAL_REPORTS_AND_OFFLINE.md#permissions-and-licensing-to-arrange). Microsoft references were checked on September 15, 2026.

## Unattended SharePoint and Purview

A client secret is sufficient for Graph, Defender, and selected preview REST APIs. SharePoint administrative PowerShell requires a certificate or delegated session. Supported Purview app-only cmdlets also require a certificate and workload role assignments. Unattended setup is available only with the Standard permission profile.

For initial certificate setup, use a readable, unprotected PFX containing its private key. Setup attaches the certificate to the assessment application:

```powershell
.\setup-service-principal.ps1 `
  -Mode Unattended `
  -PermissionProfile Standard `
  -SharePointAdminUrl "https://<actual-prefix>-admin.sharepoint.com" `
  -CertificatePath "C:\Certificates\assessment.pfx"
```

A store thumbprint alone cannot authenticate the Python Graph client: Graph also needs a usable `CERTIFICATE_PATH` or `CLIENT_SECRET`. For an existing application, setup can reuse an encrypted Graph certificate file configured with `CERTIFICATE_PATH` and `CERTIFICATE_PASSWORD` only when the saved tenant and client IDs match that application. The file must contain a valid private key matching the application's registered certificate; when a setup certificate is explicitly selected, it must match that certificate too. Administrative PowerShell can use a matching certificate imported into `Cert:\CurrentUser\My` and selected by `-CertificateThumbprint`.

For fresh setup using only a store certificate, explicitly add `-RotateCredential` to `-CertificateThumbprint YOUR_CERTIFICATE_THUMBPRINT` to create a separate Graph client secret. Without a usable saved Graph credential or this explicit option, thumbprint-only setup stops. Do not pass an encrypted PFX as the initial `-CertificatePath`; that setup parameter does not accept a password. Protect certificate files, passwords and secrets, and keep them outside assessment deliverables.

Unattended setup:

- Requires explicit confirmation before requesting broad SharePoint `Sites.FullControl.All` access.
- Requests the two documented `Exchange.ManageAsApp` application roles.
- Creates or reconciles application role groups using whole existing management roles that contain the required commands. These roles can include write capabilities; read-only group names do not guarantee read-only or narrowly scoped effective access. Review actual role entries and prior assignments with the workload administrator. See [workload role limitations](PERMISSIONS.md#setup-delegated-access-and-workload-roles).
- Verifies application-role consent after the consent step.

Specialized Purview workloads such as eDiscovery, Insider Risk, Communication Compliance, and Information Barriers are not queried by default. Set `PURVIEW_INCLUDE_SPECIALIZED=true` only when those additional workloads and roles are intentionally in scope.

## Optional permission packs

Standard setup does not request preview access unless selected. Restricted rejects these packs:

```powershell
.\setup-service-principal.ps1 -PreviewCollectors ShadowAI
.\setup-service-principal.ps1 -PreviewCollectors NetworkAccess
.\setup-service-principal.ps1 -PreviewCollectors PowerPlatform
```

Power Platform Reader is a tenant-scoped Power Platform RBAC role, not the Entra Power Platform Administrator directory role. If the setup operator cannot assign it, use the Power Platform Admin Center inventory export instead.

## SharePoint admin URL

Standard SharePoint administrative collection requires an operator-confirmed URL. Sign into the customer's **Microsoft 365 admin center**, verify the tenant, then select **Admin centers > SharePoint**. Copy only the final HTTPS origin from the browser, such as `https://<actual-prefix>-admin.sharepoint.com`; omit the page path (including `/_layouts/...`), query and fragment. The supported form is a commercial `*-admin.sharepoint.com` origin without credentials or an explicit port; a trailing slash is accepted. [Microsoft admin-center navigation](https://learn.microsoft.com/en-us/microsoft-365/admin/admin-overview/admin-center-overview?view=o365-worldwide).

The initial `.onmicrosoft.com` domain is not a verified SharePoint hostname. A SharePoint rename leaves the old initial domain attached to the tenant, and changing the fallback domain does not rename SharePoint. The tool therefore does not infer a SharePoint URL from either domain. [SharePoint rename FAQ](https://learn.microsoft.com/en-us/troubleshoot/sharepoint/administration/domain-rename-faq), [fallback-domain behavior](https://learn.microsoft.com/en-us/microsoft-365/admin/setup/add-or-replace-your-onmicrosoftcom-domain?view=o365-worldwide). These URL references were checked on **September 17, 2026**.

Configure the URL with setup's `-SharePointAdminUrl`, which writes `SHAREPOINT_ADMIN_URL` into `.env`, or set it in the environment file selected for this customer. Standard setup without the parameter preserves a valid saved URL only when the tenant identity matches; otherwise it writes a blank setting and explains the missing coverage. URL syntax validation does not verify tenant ownership or a successful SharePoint connection. Runtime precedence is:

1. `--sharepoint-admin-url`
2. `SHAREPOINT_ADMIN_URL` in the selected environment file
3. A prompt for the actual URL when SharePoint collection is selected and interactive authentication is allowed

The prompt is offered only when no URL is configured. Correct any configured URL with invalid syntax; it is not silently replaced by an environment fallback or interactive entry.

With `--interactive-auth skip` or unattended execution, a missing or invalid URL leaves SharePoint administration unassessed with a configuration reason; no SharePoint administrative PowerShell is launched. This is not a permission failure, and the other selected sources can still run. Verify the subsequent dataset results even after a successful browser sign-in.

Restricted skips SharePoint administration, ignores this setting and does not prompt for a URL; Restricted setup rejects `-SharePointAdminUrl`. `PURVIEW_ORGANIZATION` still uses its separate initial `.onmicrosoft.com` organization domain and is not a SharePoint URL.

## Microsoft-managed reports and scans

The assessment never starts a Data Access Governance report, site review, DSPM assessment, or other long-running Microsoft scan. It checks for recent completed DAG results and reuses them when available. If evidence is missing or stale, the HTML report explains which report to run, prerequisites, expected timing, and how to rerun the assessment.

Existing exports can be supplied directly:

```powershell
python main.py `
  --sam-report .\exports\sharepoint-dag.csv `
  --dspm-report .\exports\dspm.csv `
  --power-platform-inventory .\exports\power-platform-inventory.csv
```

The first SharePoint permission-state report can take up to five days. DSPM remains Microsoft-generated evidence; the tool does not attempt to recreate it from indirect tenant signals.

## Connection troubleshooting

Run the lightweight preflight before a full assessment:

```powershell
python main.py --check-connections
```

It creates no reports and starts no scans. Exit code `0` means the configuration is usable, including legitimate license limitations; `2` identifies an authentication, permission, role, or module action; `1` indicates invalid configuration or an unexpected failure.

To diagnose without opening a browser:

```powershell
python main.py --check-connections --interactive-auth skip
```

Use the result wording to distinguish a missing application permission from a missing workload role, subscription limitation, unprovisioned feature, incompatible local module, or required delegated sign-in.

## End-of-assessment cleanup

Plan an owner and date for removing the dedicated application's access. [CLEANUP.md](CLEANUP.md)
provides cloud preview/apply commands and local discovery with one deletion confirmation, their separate administrator requirements,
and the Standard Unattended workload cleanup sequence. Cleanup installs no modules automatically.
Retain and verify the agreed evidence package first. A client secret's expiry or deletion of a local
environment file does not remove application grants; certificates and shared resources need a separate
ownership review.

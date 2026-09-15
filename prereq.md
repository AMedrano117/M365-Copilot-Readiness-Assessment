# Prerequisites

These tenant permissions and network requirements apply to connected collection. Follow [RUN.md](RUN.md) for the canonical workflow. Every live assessment automatically saves a collection JSON and portable package; `--save-collection PATH` only chooses a custom destination. Preflight does not save a collection, and offline builds do not create or overwrite one. Rebuilding with `--mode offline --collection-input PATH` needs local Python dependencies and the full package, but no tenant credentials, network calls or administrative PowerShell. [The portal guide](PORTAL_REPORTS_AND_OFFLINE.md) covers report requests and sample compatibility.

## Local requirements

- Windows 10/11 or Windows Server with Windows PowerShell 5.1.
- Python 3.10 or later.
- Network access to Microsoft sign-in, Microsoft Graph, Defender, SharePoint Online, Exchange Online, and Purview endpoints used by the selected collectors.
- An Entra administrator who can create or update an application registration, plus an administrator authorized to grant Microsoft Graph application consent. Application Administrator alone cannot grant Graph application permissions; arrange Privileged Role Administrator, Global Administrator, or an appropriate custom consent role. [Microsoft consent guidance](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/grant-admin-consent).

Create the Python environment and install only the packages used by the tool:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

The runtime Graph client uses `azure-identity` and `httpx`; it does not require the generated `msgraph-sdk` package.

## Recommended setup

Standard mode is the normal operator-assisted configuration:

```powershell
.\setup-service-principal.ps1 -Mode Standard
python main.py --mode live --check-connections
python main.py --mode live
```

The setup script reuses the existing application with the configured display name, reconciles its permission manifest, and installs or updates the supported PowerShell modules. It keeps an existing usable credential unless `-RotateCredential` is supplied. Standard mode uses the client secret or certificate for application APIs and requests browser sign-in for SharePoint and Purview when no certificate is configured.

The setup script installs:

- Microsoft Graph PowerShell modules used only to configure the application.
- Exchange Online Management 3.2.0 or later for Purview and Exchange configuration.
- SharePoint Online Management Shell in Windows PowerShell, including the Data Access Governance cmdlets. Item-level Everyone/EEEU coverage requires version 16.0.27215.12000 or later.

`Az.Accounts` is not installed or used by a normal assessment. It is needed only when `--legacy-power-platform-collector` is explicitly selected.

## Authentication and permissions

| Collector | Default authentication | Required access | Licensing or provisioning notes |
|---|---|---|---|
| Microsoft Graph tenant, licensing, usage, identity, consent, devices, security, and external connections | Application certificate or client secret | Application permissions reconciled by `setup-service-principal.ps1` | Individual workloads can still be unavailable when the tenant is not licensed or provisioned. |
| Defender for Endpoint device inventory | Application certificate or client secret | `Machine.Read.All` on WindowsDefenderATP | Requires Defender for Endpoint provisioning. |
| SharePoint tenant, sites, sharing, and existing DAG reports | Delegated browser sign-in, or application certificate | SharePoint Administrator for delegated use; `Sites.FullControl.All` for app-only administrative PowerShell | Snapshot reports require SharePoint Advanced Management entitlement. Detailed Everyone/EEEU reports additionally require the SharePoint Advanced Management Administrator role. |
| Purview DLP, labels, retention, rights management, and audit configuration | Delegated browser sign-in, or supported application certificate | Appropriate delegated Purview/Exchange read roles, or the application role groups configured by Unattended setup | Feature availability depends on the tenant’s Purview and Exchange subscriptions. |
| Power Platform inventory export | No live sign-in | An exported Manage > Inventory CSV | Supplemental only. |
| Power Platform inventory API | Application credential | Tenant-scoped Power Platform Reader RBAC | Preview and opt-in. |
| Defender Cloud Apps discovery | Application credential | `CloudApp-Discovery.Read.All` | Preview and opt-in; requires a populated discovery data stream. |
| Global Secure Access | Application credential | `NetworkAccess.Read.All` and `NetworkAccessPolicy.Read.All` | Preview and opt-in; requires the feature to be provisioned. |

The stable Graph permission set is defined in `collector-registry.json` and includes the permissions actually used for organization, user, group, application, reporting, site, Entra policy, directory-role, authentication-method, access-review, managed-device, audit, identity-risk, external-connection, alert, incident, and Secure Score reads.

Microsoft Entra ID Protection is checked separately from Conditional Access. When the risk permission is present but the tenant only has Entra ID P1, unavailable risk evidence is reported as a licensing limitation rather than a missing permission.

Portal report roles are separate from application API permissions. Confirm current SharePoint entitlement and the content-metadata access granted by the additional SAM administrator role against [SAM prerequisites](https://learn.microsoft.com/en-us/sharepoint/sharepoint-advanced-management-prerequisites). DSPM assessment readers and creators have different permissions; viewing file details needs additional Content Explorer roles. Check [DSPM permissions](https://learn.microsoft.com/en-us/purview/data-security-posture-management-permissions) and [the per-report access table](PORTAL_REPORTS_AND_OFFLINE.md#permissions-and-licensing-to-arrange). Microsoft references were checked on September 15, 2026.

## Unattended SharePoint and Purview

A client secret is sufficient for Graph, Defender, and selected preview REST APIs. SharePoint administrative PowerShell requires a certificate or delegated session. Supported Purview app-only cmdlets also require a certificate and workload role assignments.

Use a certificate with a private key that is attached to the assessment application:

```powershell
.\setup-service-principal.ps1 `
  -Mode Unattended `
  -CertificateThumbprint YOUR_CERTIFICATE_THUMBPRINT
```

You can use `-CertificatePath` for a readable certificate file. Password-protected PFX files should be imported into `Cert:\CurrentUser\My` and selected by thumbprint.

Unattended setup:

- Requires explicit confirmation before requesting broad SharePoint `Sites.FullControl.All` access.
- Requests the two documented `Exchange.ManageAsApp` application roles.
- Creates or reconciles narrowly scoped application role groups for the core Purview and Exchange read cmdlets.
- Verifies application-role consent after the consent step.

Specialized Purview workloads such as eDiscovery, Insider Risk, Communication Compliance, and Information Barriers are not queried by default. Set `PURVIEW_INCLUDE_SPECIALIZED=true` only when those additional workloads and roles are intentionally in scope.

## Optional permission packs

Setup does not request preview access unless selected:

```powershell
.\setup-service-principal.ps1 -PreviewCollectors ShadowAI
.\setup-service-principal.ps1 -PreviewCollectors NetworkAccess
.\setup-service-principal.ps1 -PreviewCollectors PowerPlatform
```

Power Platform Reader is a tenant-scoped Power Platform RBAC role, not the Entra Power Platform Administrator directory role. If the setup operator cannot assign it, use the Power Platform Admin Center inventory export instead.

## SharePoint admin URL

`SHAREPOINT_ADMIN_URL` is optional. The tool resolves it in this order:

1. `--sharepoint-admin-url`
2. `SHAREPOINT_ADMIN_URL` in the selected environment file
3. Automatic derivation from the tenant’s initial `.onmicrosoft.com` domain
4. A prompt when resolution failed and interactive authentication is allowed

Renamed or multi-geo tenants should set the verified administration URL explicitly.

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

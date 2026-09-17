# Close out an assessment

[Documentation index](README.md) | [Project overview](../README.md)

After the customer review, retain the agreed deliverables and complete portable package, verify that the retained copy rebuilds offline, and obtain the customer's retention/deletion authorization. Then remove assessment access and local working copies as separate operations. Cloud cleanup defaults to preview and requires `-Apply`; local cleanup lists saved artifacts and asks once before removal. Microsoft references below were verified on **September 16, 2026**.

Run these commands from the repository folder. Replace any angle-bracket placeholders with the reviewed IDs and paths before running those examples.

If setup reported a credential recovery file, restore and verify the normal environment configuration first, then remove the recovery copy explicitly. Recovery files contain credentials and are not assessment artifacts discovered by local cleanup. Secret rotation leaves older application credentials in place; whole-application cleanup below removes them when retiring a dedicated application.

## 1. Retain and verify the final evidence

Copy the complete assessment folder, including original inputs, `collection.json` or `rebuild.json`, `rebuilds/`, deliverables and the operator log, to the customer's approved storage. Verify that copy before selecting local artifacts for removal:

```powershell
python main.py --mode offline --collection-input "<retained-package>\collection.json"
```

Use the retained `rebuild.json` instead when that is the package's replay entry point. Offline replay needs local Python dependencies but no application access, environment file or administrative PowerShell. Omit `--permission-profile` and `--env-file`. Treat retained packages, logs, original exports, PDFs and workbook detail as confidential tenant evidence. See [portable package and replay instructions](RUN.md#portable-assessment-folder).

## 2. Remove the dedicated application's access

Use `cleanup-service-principal.ps1` only for an application dedicated to this assessment. The selected enterprise application/service principal and app registration are deleted, including every credential attached to that registration. Confirm that no other automation, customer service or assessment still uses it. Shared applications need an administrator-led review of individual grants and credentials instead of this whole-application cleanup.

Replace every angle-bracket placeholder below. `-TenantId` and `-ClientId` are required nonempty GUIDs and must match the selected environment file's `TENANT_ID` and `CLIENT_ID`; a tenant domain alone is insufficient. `-ClientId` is the application/client ID, not the enterprise application's object ID. Restricted defaults to `.env.restricted`; Standard defaults to `.env`. Use `-EnvFile ".\.env.customer"` on **every** command when selecting another file. If present, its `PERMISSION_PROFILE` must also match. The script verifies the IDs and exact setup application name; renamed or mismatched applications require manual review.

```powershell
# Read-only preview of this dedicated Restricted application's cleanup.
.\cleanup-service-principal.ps1 -PermissionProfile Restricted `
  -TenantId "<tenant-guid>" -ClientId "<application-client-guid>"

# Show the deletion decisions without removing anything.
.\cleanup-service-principal.ps1 -PermissionProfile Restricted `
  -TenantId "<tenant-guid>" -ClientId "<application-client-guid>" -Apply -WhatIf

# Apply the reviewed plan; confirm the identified removals when prompted.
.\cleanup-service-principal.ps1 -PermissionProfile Restricted `
  -TenantId "<tenant-guid>" -ClientId "<application-client-guid>" -Apply
```

The same commands with `-PermissionProfile Standard` target the Standard application. Preview displays the verified object IDs, credential counts, application-grant count and proposed removals. `-Apply` requests PowerShell confirmation for every planned removal before any deletion begins; declining any action leaves all targets intact. `-WhatIf` prevents removal calls. Identity or permission failures stop cleanup rather than selecting another application. If execution fails after approved deletions have begun, retain the environment file and IDs, resolve the error, and rerun preview to inspect what remains.

| Access used by cleanup | Requirement |
|---|---|
| Graph preview | Delegated `Application.Read.All` for the signed-in administrator. |
| Graph apply | Delegated `Application.ReadWrite.All`, plus an administrator authorized to delete the target application and service principal. Cloud Application Administrator or Application Administrator supports the documented delegated delete operations; ownership/custom-role cases require administrator verification. [Delete application](https://learn.microsoft.com/en-us/graph/api/application-delete?view=graph-rest-1.0), [delete service principal](https://learn.microsoft.com/en-us/graph/api/serviceprincipal-delete?view=graph-rest-1.0). |
| Optional Exchange/Purview cleanup | Delegated workload access to inspect and remove the target membership and workload service-principal reference. Role-group manager restrictions can also apply. Review actual RBAC with the workload administrator; Graph scopes alone do not grant this access. [Remove role-group member](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/remove-rolegroupmember?view=exchange-ps), [remove workload service principal](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/remove-serviceprincipal?view=exchange-ps). |

Cleanup scopes belong to the signed-in administrator's session. They are separate from setup's administrator scopes and the assessment application's runtime permissions in [PERMISSIONS.md](PERMISSIONS.md). Cleanup does not revoke the shared Microsoft Graph PowerShell application's consent. The cleanup script does not install modules. Prepare its Microsoft Graph dependencies first; optional workload cleanup also requires stable ExchangeOnlineManagement **3.7.2 or later**.

If these modules are missing, install them separately in the PowerShell environment used for cleanup:

```powershell
Install-Module -Name Microsoft.Graph.Authentication,Microsoft.Graph.Applications -Scope CurrentUser
# Needed only for -IncludeWorkloadRbac:
Install-Module -Name ExchangeOnlineManagement -MinimumVersion 3.7.2 -Scope CurrentUser
```

Cleanup does not require the complete Microsoft Graph module bundle or the SharePoint management module. Follow the customer's approved module-installation process where PowerShell Gallery installation is restricted.

### Standard Unattended workload assignments

For an app configured by Standard Unattended setup, include `-IncludeWorkloadRbac` in preview, `-Apply -WhatIf`, and apply.

Run workload cleanup in a fresh PowerShell session. The script closes its verified connections, but Exchange's disconnect command can also close legacy remote PowerShell sessions in the same host. [Disconnect behavior](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/disconnect-exchangeonline?view=exchange-ps).

```powershell
.\cleanup-service-principal.ps1 -PermissionProfile Standard `
  -TenantId "<tenant-guid>" -ClientId "<application-client-guid>" -IncludeWorkloadRbac

.\cleanup-service-principal.ps1 -PermissionProfile Standard `
  -TenantId "<tenant-guid>" -ClientId "<application-client-guid>" -IncludeWorkloadRbac -Apply
```

The script verifies the target's identity in both workloads before removing its membership from `AI Readiness Purview Read-Only` and `AI Readiness Exchange Read-Only`, and then removes the matching workload service-principal references. These steps precede Entra deletion while the target IDs are available. Shared role groups and management-role definitions remain intact. Restricted omits workload cleanup and does not support this flag.

Record the enterprise application's **service-principal object ID** printed by preview. If Entra deletion already occurred and a workload retry is needed, supply that exact retained ID with `-ServicePrincipalObjectId "<enterprise-application-object-guid>"`. It must match the live enterprise application when one exists. Do not substitute the client ID or an ID from another tenant.

Additional manually assigned Entra roles, other workload role groups, custom assignments, preview Power Platform RBAC and resources outside the known setup groups require an administrator's separate review. The script is not a tenant-wide permission cleanup. If optional workload cleanup cannot be verified or completed, resolve that failure before deleting the Entra objects.

### Credentials, consent and deletion limits

Optionally add `-RemoveEnvironmentFile` to the reviewed apply command. That file's removal is separately confirmed with the plan, then performed only after successful remote deletion and verification that both Entra objects are absent. Keep the file until identity checks and any cleanup retries are complete. Deleting a local environment file alone does not revoke consent, and application grants do not expire when a client secret expires.

Application deletion does not remove local certificate files, private keys in certificate stores, other environment files, modules, Python environments, backups or copied evidence. Review ownership and reuse of certificates manually before deleting any private key; a certificate may serve another application. Review any additional registrations or service principals in other tenants separately. [Microsoft application removal guidance](https://learn.microsoft.com/en-us/entra/identity-platform/howto-remove-app).

If the environment file is unavailable or the application was renamed, use that Microsoft admin-center removal procedure after verifying the tenant, client ID and object IDs against the engagement record. Complete workload cleanup first, then verify both the app registration and enterprise application are absent. Shared applications require removal of only the reviewed assessment credentials and actual consent grants; changing the requested-permissions manifest alone is insufficient.

Do not interpret deletion as an immediate guarantee that every previously issued access token is unusable; token lifetimes vary by resource and policy. Follow the customer's access-revocation process and verify the final state with the responsible administrators. [Microsoft access-token guidance](https://learn.microsoft.com/en-us/entra/identity-platform/access-tokens). Deleted applications remain recoverable during Microsoft's soft-delete retention period; this script does not permanently purge deleted objects. Restoration can restore configuration and access, so repeat the permission/grant review before reusing a restored application. [Restore an application](https://learn.microsoft.com/en-us/entra/identity-platform/howto-restore-app).

## 3. Remove local working copies

After verifying the retained copy, run:

```powershell
.\cleanup-local-assessment.ps1
```

The script runs offline, lists the saved assessment artifacts it found, and asks for **one confirmation for the whole listed batch**. No path or `-Apply` is required. The default includes **all customers and assessments** stored directly within these locations in the script's checkout, regardless of the current working directory:

| Location | Discovered artifacts |
|---|---|
| `output/collections/` | Saved live collections and their portable package folders |
| `output/assessments/` | Offline assessment package folders |
| `output/portal-reviews/` | Imported PDF review folders, previews and generated review manifests |
| `Reports/` | Generated reports and diagnostics |
| `.cache/purview/` | Purview evidence cache files and subfolders |
| `.cache/sharepoint_dag/` | Downloaded SharePoint DAG evidence files and subfolders |

Discovery selects each location's direct children; removal includes the contents of selected subfolders. The storage folders themselves remain. Missing or empty locations require no confirmation. Keep the approved retained copy outside the selected locations, and review the full list before confirming. Custom export folders and artifacts saved elsewhere are not part of default discovery.

To list the default selection without deleting or prompting:

```powershell
.\cleanup-local-assessment.ps1 -WhatIf
```

To select only particular working copies, supply optional literal paths instead of the default discovery:

```powershell
.\cleanup-local-assessment.ps1 -Path ".\output\collections\<collection-stem>_package"
```

`-Path` accepts multiple files or subfolders inside this checkout's `output/`, `Reports/` or `.cache/`; relative explicit paths use the current directory. The script rejects those three roots themselves, outside paths and overlapping parent/child selections. Standard PowerShell `-Confirm:$false` is available for an already authorized automated run.

Both cleanup helpers support ordinary Windows cloud placeholders, including OneDrive, only after verifying their known cloud reparse tag. Symbolic links, junctions, unrecognized reparse tags and failed tag checks are refused. Local cleanup changes no application access and does not search other locations for environment files, certificates, modules or Python environments. Deletion of selected OneDrive files can propagate through synchronization; it does not securely erase storage, cloud versions, recycle bins or backups. Those copies follow the customer's storage retention process.

Record the retained package location, application/client and service-principal IDs, preview/apply outcomes, workload exceptions, and local paths removed in the engagement closeout record. Confirm that remaining access and retained evidence match the customer's agreement.

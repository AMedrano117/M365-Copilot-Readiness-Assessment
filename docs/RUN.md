# Operator runbook

[Documentation index](README.md) | [Project overview](../README.md)

This is the canonical workflow for Microsoft 365 Copilot readiness: **prepare → collect and save → add exports → rebuild and review**. Use one tenant per assessment folder. Collection reads supported configuration and existing reports; it does not start Microsoft scans or change tenant policies. Application setup is a separate operation.

For another customer, start with the [new-tenant assessment checklist](NEW_TENANT_CHECKLIST.md), including access, portal captures, structured exports and pilot-owner reviews.

## 1. Prepare

Run commands from the repository folder with Python 3.10 or later. Install dependencies before working offline:

**Repository in OneDrive?** Use the [environment setup outside OneDrive](PYTHON_ENVIRONMENT.md#recommended-setup-for-a-repository-in-onedrive) to avoid syncing dependency files and hitting path-length limits. The local `.venv` commands below remain supported for other locations; that guide shows the equivalent executable path.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
```

For connected collection, complete [prereq.md](prereq.md). Reuse an existing configured application where possible. For Standard SharePoint administration, obtain the actual URL from the customer's **Microsoft 365 admin center > Admin centers > SharePoint** and copy only its HTTPS origin. When setup or permission reconciliation is needed, have the authorized administrator run this command, replacing the URL placeholder:

```powershell
.\setup-service-principal.ps1 -Mode Standard -SharePointAdminUrl "https://<actual-prefix>-admin.sharepoint.com"
```

For customers choosing reduced application access, use the [Restricted profile](PERMISSIONS.md) instead:

```powershell
.\setup-service-principal.ps1 -PermissionProfile Restricted
```

This creates `M365 Copilot Readiness Assessment Tool - Restricted` and writes `.env.restricted`. It retains stable identity/device/security/usage reads and skips Graph site inventory, group licensing, delegated consent-grant inventory and all SharePoint/Purview administrative PowerShell. Plan supported exports or saved evidence for the resulting coverage gaps. Restricted rejects Unattended setup, live preview packs and legacy administrative collection. Standard setup continues writing `.env`.

The setup administrator uses delegated `Application.ReadWrite.All` and `Organization.Read.All`; these are separate from the runtime application permissions. Standard apps missing `Directory.Read.All` or `SecurityAlert.Read.All` need updated administrator consent. The MFA report needs `AuditLog.Read.All`, not `UserAuthenticationMethod.Read.All`. See the [permission matrix and consent cleanup](PERMISSIONS.md).

Creating an application and granting Microsoft Graph application consent require different access. Application Administrator alone cannot grant the Graph application permissions used here; arrange Privileged Role Administrator, Global Administrator, or a suitable custom consent role. [Microsoft consent requirements](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/grant-admin-consent).

Start the [portal report requests](PORTAL_REPORTS_AND_OFFLINE.md#reports-to-obtain-next) early. Reuse recent completed results where suitable. Record their tenant, scope, filters, reporting period and completion date; generation can take several days.

Create the folder for downloaded exports:

```powershell
New-Item -ItemType Directory -Force -Path ".\output\customer\exports"
```

## 2. Check access, then collect and save

These Standard commands use the `SHAREPOINT_ADMIN_URL` saved by setup in `.env`. For another environment, select it with `--env-file` on both commands and configure the URL there, or pass `--sharepoint-admin-url` on each command. See [URL configuration](#sharepoint-url-and-collection-diagnostics) for missing-URL behavior.

An explicitly selected environment file must exist and be readable. A typo stops the run before authentication, even when credentials are already present in the process environment. The default `.env` remains optional for operators configuring process environment variables directly. Before connecting, the console shows the configuration file, effective tenant/application, permission profile, Graph authentication method and configured SharePoint URL without credential values. `--tenant-id` applies consistently to the shared workload credentials.

```powershell
.\.venv\Scripts\python.exe main.py --mode live --check-connections
.\.venv\Scripts\python.exe main.py --mode live
```

For Restricted, select the dedicated environment on both commands:

```powershell
.\.venv\Scripts\python.exe main.py --mode live --env-file .env.restricted --check-connections
.\.venv\Scripts\python.exe main.py --mode live --env-file .env.restricted `
  --reports-dir ".\output\customer\exports"
```

Live selection is `--permission-profile standard|restricted`, then `PERMISSION_PROFILE` in the selected environment, then `standard`. A CLI override does not change the app's grants. Restricted preflight audits both requested permissions and actual application grants and stops on excess access; an administrator must review and clean up unwanted grants separately. No automatic revocation occurs. Omitted sources show `not_requested` with the profile reason and do not request additional permissions. Existing certificates do not enable excluded administrative collection.

Preflight checks access without producing an assessment or saving a collection. Its exit codes are `0` for usable configuration (including legitimate license limitations), `2` for an authentication/permission/role/module or workload-configuration action, and `1` for invalid startup configuration or an unexpected failure. Review each result even when the overall check passes.

Preflight verifies selected permissions and representative requests, not every dataset request.
Successful consent and preflight do not guarantee access to every licensed feature or a successful
browser sign-in. The live run's per-service dataset totals and **Source collection gaps** show
what was actually collected. If an interactive retry succeeds, use its final dataset result;
the first failed attempt does not mean that workload is still missing.

The full live run automatically saves a unique collection under `output/collections/` and its adjacent portable package. At the end, find **COLLECTION INPUT (`--collection-input`)**: it gives the exact file to reuse, followed by a copyable offline command. No save switch is required. `--save-collection PATH` remains an optional custom destination:

```powershell
.\.venv\Scripts\python.exe main.py --mode live `
  --save-collection ".\output\customer\tenant-collection.json"
```

If exports are already available, include `--reports-dir ".\output\customer\exports"` in the live command. They are preserved for replay. Use `--interactive-auth fresh` when deliberately refreshing the interactive/cached collection path. In normal mode, Graph uses application authentication; SharePoint/Purview use a configured certificate or announce a browser sign-in. See [authentication requirements](prereq.md#authentication-and-permissions).

`--reports-dir` accepts supported structured exports and PDF captures. PDFs in that folder or
its subfolders are automatically read using local text extraction/OCR and included with JSON
and page previews. See [PDF import](PORTAL_REVIEW.md) for its qualifications and limits.
The live command already produces HTML and Excel. An offline rebuild is needed only to add
evidence or regenerate those deliverables; an immediate second run is optional.

### Interrupted collection and temporary service failures

The live run saves progress before workload collection and updates it as each service finishes. If collection is interrupted, use the saved path with `--mode offline --collection-input "<collection-input>"`. Completed service evidence remains available; unfinished services remain **not assessed** in report coverage. This rebuild does not resume live requests. Start a new live run when fresh or remaining collection is required. A completed pipeline can still contain failed or partial sources; inspect source coverage rather than treating completion as full access.

Graph, Entra, Defender and AI usage read requests retry transient failures up to four times. Retry sleeps total at most 120 seconds per request, excluding HTTP request timeouts. Server `Retry-After` values are honored; a delay beyond the remaining budget stops the read with an explanation. Successfully collected pages are retained as partial evidence, never represented as a complete inventory.

### Read the console and copy the next command

The default console shows collection progress, warnings or failures, the deployment decision and action counts, report locations, and the final **COLLECTION INPUT** handoff. Copy its entire path into `--collection-input`, or copy the offline command printed immediately below it and add `--reports-dir` for new exports. Use the file shown there: a live package has `collection.json`; a build made only from exports or earlier reports has `rebuild.json`. Neither the HTML report, Excel workbook nor an assessment snapshot is a collection input. Preflight-only runs do not create one.

Use `--verbose` (or `-v`) when troubleshooting to also see authentication/configuration provenance, processing details, actions by assessment area, and input receipt details:

```powershell
.\.venv\Scripts\python.exe main.py --mode live --verbose
.\.venv\Scripts\python.exe main.py --mode offline --collection-input "<collection-input>" --verbose
```

Replace `<collection-input>` with the exact path from the handoff. Verbosity changes terminal messages only; the technical workbook and structured package `operator-log.jsonl` still retain detailed results.

Colors distinguish headings, successful steps, warnings and errors. `--color auto` is the default: color is used in an interactive terminal and disabled for redirected output or when `NO_COLOR` is set. Use `--color never` for plain output, or `--color always` to explicitly force ANSI colors. Status labels remain readable without color.

### SharePoint URL and collection diagnostics

The live run loads the repository's `.env`, or the file selected by `--env-file`. The SharePoint URL precedence is `--sharepoint-admin-url`, then `SHAREPOINT_ADMIN_URL`. The tool does not construct a SharePoint hostname from a tenant domain. Open the customer's **Microsoft 365 admin center > Admin centers > SharePoint**, verify the tenant, and copy only the actual HTTPS origin, such as `https://<actual-prefix>-admin.sharepoint.com`. Omit any `/_layouts/...` path, query, fragment, credentials or explicit port. A trailing slash and quoted `.env` values are supported. See [the URL requirements and Microsoft references](prereq.md#sharepoint-admin-url).

Standard setup accepts `-SharePointAdminUrl` and saves it in `.env`. Without the parameter, setup preserves a valid URL only when the saved tenant identity matches; otherwise it leaves the setting blank. Setup checks URL syntax, not successful connection to that tenant. Use `--verbose` to see the selected URL and its source, then review the actual collection outcome.

If no URL is configured, the live workflow can request it when SharePoint collection is selected and interactive authentication is allowed. An explicitly configured URL with invalid syntax must be corrected; the tool does not silently replace it or prompt over it. With `--interactive-auth skip` or unattended execution, a missing URL leaves SharePoint administration unassessed with a configuration reason, and SharePoint administrative PowerShell is not launched. Missing URL configuration is not a missing permission. Other selected sources can still run. Restricted does not use or prompt for this URL. Purview's initial `.onmicrosoft.com` organization-domain discovery is separate and does not establish the SharePoint hostname.

Accepted browser sign-in confirms authentication only. Review the subsequent SharePoint dataset summary for collection gaps. Unreadable collector output is reported separately from authentication failures, with sanitized details in `Reports/collector_diagnostics.log`. An interactive retry accepts the same verified URL. Adding portal exports offline supplements saved evidence; it does not refresh sharing settings or repair a failed live collection.

## 3. Add completed portal exports and rebuild

Put the completed exports into the folder created above. Replace `<collection-input>` with the full path shown under **COLLECTION INPUT** at the end of the live run:

```powershell
.\.venv\Scripts\python.exe main.py --mode offline `
  --collection-input "<collection-input>" `
  --reports-dir ".\output\customer\exports" `
  --open-html-report
```

Offline mode needs local dependencies and evidence files. It uses no tenant sign-in, live API calls, prompts or administrative PowerShell. It does not save or overwrite the collection it reads. A new build does not refresh original observations.

`--reports-dir` discovers structured exports directly inside a directory; structured discovery is not recursive. Repeat it for separate structured-export folders. PDF discovery includes subfolders. Omit paths for unavailable reports. Retain original headers: recognition uses schemas, not filenames. [The compatibility matrix](PORTAL_REPORTS_AND_OFFLINE.md#sample-compatibility-matrix) identifies supplied real samples, fixture-only support and unsupported variants.

### Lifecycle report dates and freshness

Content lifecycle reports use a **90-day** freshness window. SAM permission/sharing reports keep their **35-day** window, and DSPM assessments keep **8 days**. Change only the lifecycle window with `--lifecycle-report-max-age-days DAYS`; the default comes from the saved package setting, then `LIFECYCLE_REPORT_MAX_AGE_DAYS`, then 90 days.

If a lifecycle export has no report date, confirm its generation date from the original report or the person who generated it. Supply that date for the exact file, as in this invented customer example:

```powershell
.\.venv\Scripts\python.exe main.py --mode offline `
  --collection-input "<collection-input>" `
  --reports-dir ".\output\contoso\exports" `
  --lifecycle-report-date ".\output\contoso\exports\content-lifecycle.csv=2026-07-16" `
  --lifecycle-report-max-age-days 90 `
  --evaluation-date 2026-09-15 `
  --open-html-report
```

Repeat `--lifecycle-report-date "PATH=YYYY-MM-DD"` for each undated lifecycle file. The confirmation is a fallback for a missing source date; it does not replace a date already present in the report or establish permission coverage. The tool does not infer dates from filenames or file timestamps. Unknown or future source dates remain qualified; a confirmed date later than the evaluation date is rejected.

The package saves the lifecycle window and each confirmed date against the file's SHA-256 hash. Copied or renamed files with identical contents retain their confirmation on replay; changed contents require a new confirmation. These settings are restored with the package's evaluation date, so replay does not make an older report fresh again.

### Admin-center PDFs and screenshots

Run live collection first. Paid Copilot activity aggregates, subscription seats and returned
Copilot DLP details are collected automatically with existing access. Request screenshots only
for relevant remaining portal details or optional visual context; see the [coverage table](COPILOT_AUTOMATIC_COLLECTION.md).

Include PDFs in `--reports-dir`; PDF subfolders are included and duplicate contents are imported once. The tool automatically creates JSON, extracts text (using Windows OCR for image pages), and creates previews. Extracted text and originals appear in the HTML and workbook. These captures do not automatically pass controls or close actions. See [PDF import and optional reviewed manifests](PORTAL_REVIEW.md).

```powershell
.\.venv\Scripts\python.exe main.py --mode offline `
  --collection-input "<collection-input>" `
  --reports-dir ".\output\customer\exports"
```

The saved package includes the generated JSON and assets, so subsequent rebuilds restore them without OCR. Review the embedded contents before distributing the HTML. `--portal-review` remains available for a curated JSON manifest and accepts a PDF folder for compatibility. Input validation and PDF preparation happen before live authentication. If later packaging fails, the error prints the saved collection path for offline recovery.

### More than one Copilot readiness export

A saved collection restores its packaged reports automatically. `--reports-dir` adds another
folder; it does not replace the saved inputs. If the saved package already has everything
needed, rebuild with just `--collection-input` and omit `--reports-dir`.

The error lists the competing CSV paths. First check that every folder belongs to the assessed
customer. If there are multiple legitimate snapshots for that same tenant, select one with
`--copilot-readiness-export "PATH\readiness.csv"`. Identical file contents are deduplicated
automatically. Do not select a snapshot merely to bypass an accidental mix of customer folders.

PDF import is separate from this error. Older saved packages may retain an "unsupported" PDF
diagnostic without the original PDF. Supply its original folder with `--reports-dir` to include
it. A PDF skipped warning does not stop other imports; consult the warning and import log.

## Portable assessment folder

A live collection has an adjacent `<collection-stem>_package/` folder. The top-level collection refers to it with a relative path. To move the assessment, copy the whole package and replay the copy's `collection.json`:

```text
<collection-stem>_package/
  collection.json          saved service evidence and versioned manifest/settings
  inputs/                  original reports and supplemental inputs, grouped by source
  rebuild.json             latest successful offline build's inputs/settings
  rebuilds/                retained build recipes and subsequently added input files
  deliverables/            generated report artifacts
  operator-log.jsonl        build receipts and output locations
```

```powershell
.\.venv\Scripts\python.exe main.py --mode offline `
  --collection-input ".\output\customer-copy\collection.json" `
  --open-html-report
```

Copy the folder contents to `customer-copy` before using that example. Preserve `inputs/` and the relative layout. A collection JSON from before package support still works, but its external reports/settings must be supplied explicitly.

The manifest validates original files with SHA-256 hashes. Do not edit, remove or add files inside recorded input directories; place revised exports in a separate directory and pass `--reports-dir` again. Version 2 collections and rebuild recipes require the same methodology version as the running tool, except for the explicit **2.0.0 → 2.1.0 compatibility migration**. That transition retains the unchanged raw collection schema, collected facts and base findings, then applies the new control matching and reviewed pilot criteria. Readiness conclusions can change. The offline build prints a migration warning and records the original/effective methodology in source context, the workbook and operator receipt. Original collection files, source hashes and evidence dates remain unchanged; a successful derived rebuild recipe records the migration. No new tenant connection is needed. Other version mismatches still require the matching tool version or a separately supported migration. Version 1 compatibility preserves precomputed conclusions as historical.

Replay restores packaged reports, assessment profile, provider evidence, baseline and relevant settings. Repeated report arguments add evidence; explicit single-file supplemental arguments replace their packaged counterpart. Successful offline builds copy later external inputs into `rebuilds/` and update `rebuild.json`, so a copied package retains the latest build's evidence. The original `collection.json` stays unchanged. Downloaded DAG files are preserved when the collector supplies their local paths; a URL or summary count cannot reconstruct a missing export. The builder records the import/selection results and report locations. Review the workbook's source statuses for selected, superseded, unsupported or unreadable inputs.

The package records an **evaluation date**, which is reused to make freshness assessments repeatable. Deliberately change it when asking whether old evidence is still suitable today:

```powershell
.\.venv\Scripts\python.exe main.py --mode offline `
  --collection-input ".\output\customer-copy\collection.json" `
  --evaluation-date 2026-09-15
```

Later evaluation dates can change conclusions about freshness. Equal supported evidence, settings, methodology and evaluation date should yield equal assessment results; report filenames and build timestamps may differ. A legacy workbook preserves conclusions made under its original methodology as historical observations.

User-level Copilot workbook detail requires `--include-user-usage-detail` on every run; HTML remains aggregate-only. The package contains original confidential evidence even when user detail is excluded from generated deliverables. Keep credentials, `.env` files and private certificates outside it. Preserve the complete package, including `rebuilds/`, after adding exports; see the receipt for the exact replay inputs.

The saved collection's permission profile and exclusion reasons are preserved during offline replay and shown in report coverage. Rebuild a Restricted collection with the same offline command as any collection; omit `--env-file` and `--permission-profile` offline. Older collections remain compatible and show the profile as unrecorded. Supported exports do not replace every skipped administrative check; review [the evidence gap table](PERMISSIONS.md#evidence-gaps-and-offline-replay).

## 4. Review the deliverables

HTML and the default Excel workbook are generated in `Reports/`; packaged runs also preserve deliverables in the package. `--report-format both` adds CSV, while `--report-format csv` selects CSV output.

The navy and teal executive dashboard includes stacked action counts by assessment area, drawn from the current assessment. Use **Evidence workbook** to open the companion workbook and **Print report** to print the report with expanded action and evidence details. Keep the HTML and workbook together so the local link works.

Review the customer report in this order:

1. **Executive assessment:** supported deployment recommendation, original evidence period, concerns and strengths.
2. **Action plan:** remediation and confirmation of unresolved historical findings, with responsible roles, rollout stage and completion evidence.
3. **Assessment areas:** identity/access; content access/ownership; data protection; applications/connectors; endpoints/threat protection; licensing/prerequisites; pilot suitability/adoption.
4. **Rollout conditions and remaining evidence:** what must be addressed or confirmed and who can close each question.

Use the technical workbook to verify source dates, selected/superseded files, coverage, detailed objects and prior evidence. Missing and unknown values differ from measured zero. A complete report can still conclude that readiness is unconfirmed. Adoption opportunities and unavailable optional capabilities do not become security blockers.

For an integrity warning, review the workbook and `Reports/collector_diagnostics.log` before using the report for deployment approval. After remediation, a **new live collection** verifies changed tenant settings; replaying old evidence cannot do that. Use `--baseline` to compare with a prior workbook or assessment snapshot.

## Preview and recovery workflows

### Synthetic preview without a tenant

```powershell
.\.venv\Scripts\python.exe main.py --mode offline `
  --reports-dir ".\tests\fixtures\microsoft_reports" `
  --tenant-name "Sample tenant" --open-html-report
```

This preview uses invented data and intentionally leaves tenant controls unassessed. For portal-only customer review, substitute the customer's exports directory.

### Recover an earlier workbook and Purview cache

```powershell
.\.venv\Scripts\python.exe main.py --mode offline `
  --prior-report ".\Reports\prior-assessment.xlsx" `
  --purview-cache ".\.cache\purview\saved-cache.json" `
  --reports-dir ".\output\customer\exports" `
  --open-html-report
```

Use actual same-tenant files; either historical input may be omitted. The cache supplies Purview configuration only. Historical findings retain their source dates and require confirmation when their underlying facts cannot be reassessed. The workbook preserves the complete original register. A partial Purview cache cannot overwrite a full `--collection-input`. [Recovery details](PORTAL_REPORTS_AND_OFFLINE.md#combine-an-existing-tenant-report-with-new-exports).

A successful build without a collection automatically creates a portable assessment under `output/assessments/<tenant>_<UTC>_<id>/`. It contains the original inputs, deliverables, an operator log and `rebuild.json`. The tool prints its location. No tenant collection is invented or saved. Copy that whole folder, then replay the recipe:

```powershell
.\.venv\Scripts\python.exe main.py --mode offline `
  --collection-input ".\output\customer-recovery-copy\rebuild.json" `
  --open-html-report
```

Use the actual copied folder in that example. The same recipe workflow applies to portal-only previews. Original dates and historical qualifications remain unchanged, and user-level detail still requires a new opt-in. After this first successful recovery build, the package restores its inputs automatically.

## Close out the assessment

Follow [CLEANUP.md](CLEANUP.md) after customer review and retention authorization. Retain the complete
portable package in approved storage and verify offline replay before removing working copies.
Preview access removal using the exact tenant/client GUIDs from the selected customer's environment:

```powershell
.\cleanup-service-principal.ps1 -PermissionProfile Restricted `
  -TenantId "<tenant-guid>" -ClientId "<application-client-guid>"
```

Replace all placeholders. Use `-PermissionProfile Standard` for the Standard app and add `-EnvFile`
when its configuration uses another filename. Standard Unattended cleanup also needs the reviewed
`-IncludeWorkloadRbac` path, completed before Entra deletion. The cloud command previews access
removal; add `-Apply` only for the reviewed targets and confirm the removal prompts.

For local cleanup, run:

```powershell
.\cleanup-local-assessment.ps1
```

This lists direct children of `output/collections`, `output/assessments`, `output/portal-reviews`,
`Reports`, `.cache/purview` and `.cache/sharepoint_dag` in this checkout, covering all customers stored there, then asks once
before removing the listed batch. The storage folders remain. No `-Apply` or path is needed;
`-WhatIf` lists without deletion or prompting, and optional `-Path` selects particular artifacts.
Keep the retained copy outside the selection. The [cleanup guide](CLEANUP.md) covers retries,
selected-environment removal, certificates and retention limits.

## Supported options

Run `.\.venv\Scripts\python.exe main.py --help` for the implemented CLI. Keep `--mode live|offline` explicit in operator commands. `--offline` remains an alias. Omitting mode preserves live behavior unless `--collection-input`, `--prior-report` or `--purview-cache` is supplied. Saved-evidence inputs conflict with explicit live mode. Offline mode rejects `--env-file`, `--permission-profile`, `--check-connections`, `--save-collection` and live preview collectors.

| Option | Use |
|---|---|
| `--verbose`, `-v` | Show detailed processing, source provenance and input receipts in the console. |
| `--color auto\|always\|never` | Select terminal color behavior. Auto respects `NO_COLOR` and disables color when output is redirected. |
| `--tenant-id ID`, `--env-file PATH` | Choose the connected tenant/configuration. Consent and roles belong to that target tenant. |
| `--permission-profile standard\|restricted` | Select live permission/collection policy; overrides `PERMISSION_PROFILE` in the selected environment. Defaults to Standard. |
| `--services M365 Entra Defender Purview` | Restrict live service collection. An empty `--services` selects all configured service areas. Scope restrictions remain visible in assessment coverage. This does not revoke application consent. |
| `--interactive-auth auto\|fresh\|skip` | Use normal authentication, deliberately refresh, or avoid delegated browser sign-in. |
| `--sam-report PATH`, `--dspm-report PATH` | Import supported exposure exports; repeat or use directories. |
| `--portal-review PATH` | Optional curated JSON manifest, or PDF folder for compatibility. Normally put PDFs in `--reports-dir`; see [PDF import](PORTAL_REVIEW.md). |
| `--lifecycle-report-max-age-days DAYS` | Set the lifecycle freshness window independently of permission/sharing and DSPM reports; default 90 days. |
| `--lifecycle-report-date "PATH=YYYY-MM-DD"` | Confirm an undated lifecycle export's generation date; repeat per file. The package binds each confirmation to the file hash. |
| `--copilot-readiness-export PATH` | Import a Copilot Readiness CSV. Readiness is separate from actual usage. |
| `--copilot-dashboard-export PATH` | Import a supported Copilot Dashboard CSV; arbitrary portal Usage CSV variants need schema validation. |
| `--power-platform-inventory PATH` | Import an optional Manage > Inventory CSV. |
| `--assessment-profile PATH`, `--provider-evidence PATH` | Scope use cases and agents/external providers. The assessment profile also accepts dated control-owner reviews and pilot/expansion records; see [Readiness reviews](READINESS_REVIEWS.md). |
| `--baseline PATH`, `--snapshot-json PATH` | Compare with an earlier result and optionally write a result snapshot. A snapshot is not a replayable collection. |
| `--preview-collectors power-platform\|shadow-ai\|network-access\|all` | Standard only: enable supplemental preview APIs after arranging their permission packs. They do not change the core foundation decision. |
| `--sharepoint-admin-url URL` | Actual SharePoint admin-center HTTPS origin; overrides the selected environment's `SHAREPOINT_ADMIN_URL`. No tenant-domain inference. |
| `--legacy-power-platform-collector` | Standard only: explicit compatibility fallback requiring `Az.Accounts`. |

## Troubleshooting

| Symptom | Next check |
|---|---|
| Missing Python module | Install `requirements.txt` into the same environment used to run `main.py`. |
| Hidden browser sign-in | Check the taskbar/browser window, finish sign-in, then read the collector result. |
| Missing PowerShell cmdlet/module | Use the supported module setup in [prereq.md](prereq.md), then repeat preflight. |
| Restricted reports excess application permissions | Have the tenant administrator inspect the dedicated app's requested permissions and actual Enterprise application grants. Removing manifest entries alone does not revoke consent. Follow [cleanup guidance](PERMISSIONS.md#existing-applications-and-credentials), then repeat preflight. |
| HTTP 403 | Distinguish application consent, workload role, missing entitlement and unprovisioned service in preflight/Collection Coverage. Adding a delegated portal role does not grant an application permission. |
| Risk inventory unavailable despite consent | Confirm a qualifying Entra ID Protection entitlement; Conditional Access access alone does not establish risk-report access. |
| Purview query unavailable | Check both Purview and Exchange read roles. Portal access alone does not cover all administrative cmdlets. `--interactive-auth fresh` refreshes the existing path when needed. |
| Copilot usage unavailable | Confirm `Reports.Read.All` application consent, the returned reporting period, and the selected scope. An assigned license does not prove use. |
| Import rejected | Keep the original file. Compare its headers with the compatibility matrix and inspect tenant/date/scope checks. Do not rename columns to force acceptance. |
| HTTP 429 | Respect the reported retry delay and inspect diagnostics. Reduce collector scope only if the resulting coverage meets the agreed assessment. |

Diagnostic logs redact common token/secret values but still contain operational tenant context. Treat logs and all assessment evidence as confidential. See [prereq.md](prereq.md) for detailed authentication and [the portal guide](PORTAL_REPORTS_AND_OFFLINE.md) for role, license, delay and unavailable-report guidance.

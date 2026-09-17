# Portal reports and offline assessment

[Documentation index](README.md) | [Project overview](../README.md)

Preview existing portal exports immediately, or combine a saved tenant collection with Microsoft-generated portal reports without signing into the tenant again. The report builder does not start Microsoft scans or change sharing or compliance policies. Application setup is a separate operation that configures permissions and credentials.

Microsoft portal references were checked on September 15, 2026. Navigation and entitlements can differ between the current DSPM experience and DSPM for AI (classic).

## Preview now with distributed fixtures

No tenant collection is needed to see the HTML and Excel output. Use the repository's synthetic reports:

```powershell
.\.venv\Scripts\python.exe main.py --mode offline `
  --reports-dir ".\tests\fixtures\microsoft_reports" `
  --tenant-name "Sample tenant" --open-html-report
```

These fixtures contain invented tenant data and intentionally show incomplete tenant coverage. Customer exports in local `output/` directories are not distributed with the repository. Use a customer's own exports only for that customer's assessment.

Existing HTML files in `Reports/` can also be opened directly. Markdown documents contain instructions, and `examples/` contains assessment profile/provider review examples; those are not portal exports.

## Combine an existing tenant report with new exports

An older assessment can provide a fuller offline review even when no collection JSON was saved. Use `--prior-report` for its XLSX workbook or assessment snapshot JSON, and `--purview-cache` for a previously saved Purview collector cache:

```powershell
python main.py --mode offline `
  --prior-report ".\Reports\prior-assessment.xlsx" `
  --purview-cache ".\.cache\purview\saved-cache.json" `
  --reports-dir ".\output\customer\exports" `
  --tenant-name "Combined offline review" --open-html-report
```

Substitute the actual filenames; either historical input can be omitted. Use a prior report from the same tenant. The newest file is not always the most complete: inspect its Recommendations, Run Manifest, Collection Coverage and evidence worksheets.

| Existing file | How it is reused |
|---|---|
| Prior assessment XLSX | Retains the original findings, aggregate usage, coverage and evidence sheets. HTML integrates qualified observations into their relevant domains and confirmation actions; the combined workbook preserves `Prior ...` tabs and a source mapping. |
| Assessment snapshot JSON | Retains the conclusions and coverage actually saved in that snapshot. It may have fewer details than the workbook. |
| `.cache/purview/*.json` | Reads the saved Purview policy/configuration payload and original cache timestamp. This cache does not contain M365, Entra or Defender collections and is not a DSPM assessment export. |
| Collection JSON saved by a live assessment (automatically or to a custom `--save-collection` path) | Replays all service data saved during that collection through `--collection-input`. |

The importer does not reconstruct missing raw collector data from a workbook or promote old conclusions into current control results. Historical findings join one action register when they need confirmation; their full original evidence remains in the workbook. Original dates are retained; a historical report's generation date does not refresh its underlying evidence. User-level Copilot evidence requires `--include-user-usage-detail` for the workbook and remains excluded from HTML.

Tenant GUIDs are compared wherever saved sources record them. Older workbooks may contain only a display name, so their tenant identity cannot be verified automatically. Cache input is explicit: offline mode never searches for or silently reuses another tenant's cache. A partial Purview cache cannot be combined with `--collection-input` to overwrite a full saved collection.

A successful recovery build automatically creates `output/assessments/<tenant>_<UTC>_<id>/` containing the original inputs, `rebuild.json`, deliverables and an operator log. Copy the whole folder and replay with `--mode offline --collection-input PATH\rebuild.json`; resupplying the original workbook/cache/export paths is unnecessary after the first successful build. Portal-only builds use this recipe format too. This preserves the evidence that exists without creating a tenant collection or promoting historical conclusions.

## Choose the execution mode

| Switch | Behavior |
|---|---|
| `--mode offline` | Build HTML and workbook outputs from local exports, a saved collection or `rebuild.json`. Preserve inputs for replay without saving a tenant collection. No tenant sign-in, live API calls, or administrative PowerShell. |
| `--mode live` | Collect tenant evidence using configured access, automatically save a reusable collection and portable package, optionally add portal exports, and generate reports. `--save-collection PATH` overrides the default destination. |

Use one `--mode` value per command. For compatibility, `--offline` remains an alias. `--collection-input`, `--prior-report`, or `--purview-cache` without a mode selects offline automatically; these options conflict with explicit `--mode live`. If neither a mode nor a saved-evidence input is specified, existing commands retain their live behavior. The console announces the selected mode before collection or report generation.

## Collect once, rebuild offline later

Follow [RUN.md](RUN.md) for the canonical prepare, collect, add exports and review workflow. The normal commands are:

```powershell
.\.venv\Scripts\python.exe main.py --mode live --check-connections
.\.venv\Scripts\python.exe main.py --mode live
# Replace <saved-file> with the collection filename printed by the live run.
.\.venv\Scripts\python.exe main.py --mode offline `
  --collection-input ".\output\collections\<saved-file>.json" `
  --reports-dir ".\output\customer\exports" --open-html-report
```

Create the exports folder before the offline command. If exports already exist, include `--reports-dir` in the live run. Every live assessment automatically saves a unique collection plus an adjacent portable package containing service evidence, original supplemental files, settings, outputs and an operator log. Preflight does not save a collection; offline mode does not create or overwrite one. `--save-collection PATH` only overrides the live collection destination.

Copy the entire `<collection-stem>_package` folder to move the evidence, then use its `collection.json` with `--collection-input`. Packaged report/profile/provider inputs restore automatically. Repeated report options add inputs; explicit single-file options override their packaged counterpart. Successful offline builds preserve new external inputs in `rebuilds/` and save the latest replay recipe in `rebuild.json`, leaving the original collection unchanged. Legacy collections that predate packaging still require their original exports and settings. See [package layout and replay rules](RUN.md#portable-assessment-folder).

Offline building requires local Python dependencies but no `.env`, credential, certificate, browser sign-in, API call or administrative PowerShell. Saved sources retain their original dates. The evaluation date is recorded and reused; `--evaluation-date YYYY-MM-DD` deliberately reassesses freshness. Offline execution alone does not establish or prevent readiness.

Structured-export discovery is not recursive. Repeat `--reports-dir` for separate structured-export folders and omit unavailable report paths. PDF discovery includes subfolders. Use one tenant per build. Select one suitable Copilot readiness snapshot; if it is discovered in a report directory, the explicit readiness option is unnecessary.

Without a saved collection, `--mode offline --reports-dir PATH` produces a portal-only assessment with missing tenant controls identified. `--prior-report` and `--purview-cache` are recovery inputs; `--snapshot-json` and `--baseline` are assessment comparison inputs, not raw collection replacements.

HTML and Excel appear in `Reports/`, with packaged deliverables also retained in the assessment folder. `--report-format both` adds CSV. User-level Copilot detail requires `--include-user-usage-detail` for each workbook build and remains excluded from HTML. Protect the full package, including original exports, as confidential evidence; keep credentials and private certificates outside it.

At closeout, retain the complete agreed package and verify its offline replay before removing local
working copies. [CLEANUP.md](CLEANUP.md) separates dedicated application removal from local artifact
cleanup. Cloud removal uses preview/`-Apply`; local cleanup lists saved artifacts and asks once before
deletion, with `-WhatIf` available for a preview. Offline reporting remains available after application
access is removed; retained copies and backups follow the customer's agreed retention process.

### Restricted collection with customer exports

Use the [Restricted permission profile](PERMISSIONS.md) for stable identity/device/security/usage
reads without Graph site/group/consent-grant inventory or administrative PowerShell:

```powershell
.\setup-service-principal.ps1 -PermissionProfile Restricted
python main.py --mode live --env-file .env.restricted --check-connections
python main.py --mode live --env-file .env.restricted --reports-dir .\exports
python main.py --mode offline --collection-input "<collection-input>" --reports-dir .\exports
```

Create the export directory first; substitute the live run's exact COLLECTION INPUT path.
Setup creates a separate application and writes `PERMISSION_PROFILE=restricted` in `.env.restricted`.
Live `--permission-profile` overrides that environment setting; the default remains Standard.
Restricted rejects Unattended setup, preview packs and legacy administrative collection.
Existing administrative certificates do not bypass the exclusions.

Supported SAM/DAG exports establish only their exported permission/sharing scope; DSPM exports
provide scoped sensitive-data exposure evidence. Neither replaces a tenant sharing-settings
inventory or Purview policy/retention/audit configuration. A prior collection, report or explicit
Purview cache preserves only its existing evidence and original dates. PDF context does not
automatically pass those controls. See the [coverage limitations](PERMISSIONS.md#evidence-gaps-and-offline-replay).

Replay preserves the collection's profile and `not_requested` reasons. Older collections remain
usable with an unrecorded profile. Intentional exclusions remain unassessed and must never be
read as zero exposure or a healthy result. Customer administrators still need the portal roles
below to produce exports; those roles are separate from the assessment application's access.

## Reviewed visual context

Admin-center PDFs can supplement the report through `--reports-dir`, including PDF subfolders. JSON, local text/OCR and page previews are created automatically; originals and previews are retained in the portable package and embedded in the HTML. Extracted context remains separate from structured metrics and scored controls. `--portal-review` supports optional curated notes. See [PDF import](PORTAL_REVIEW.md) for details.

## What the importer understands

Report recognition uses column headers, not filenames. Keep the original exports and headers. Explicit `--sam-report PATH` and `--dspm-report PATH` options remain available and can be repeated. SAM/DSPM readers support CSV, TSV, JSON, XLSX and supported ZIP contents; automatic `--reports-dir` discovery scans CSV, TSV, XLSX and ZIP. Copilot readiness requires CSV. Power Platform inventory and Copilot Dashboard exports use their separate existing switches.

### Sample compatibility matrix

Checked against supplied local CSVs and repository fixtures on September 15, 2026, including readiness exports with nine and ten columns. No customer filenames, identities or rows are copied into this guide.

| Report family | Distinguishing fields | Validation and interpretation |
|---|---|---|
| SharePoint/OneDrive permission snapshot | `Site URL`, `Number of users having access`, `Anyone link count`, `EEEU permission count`, `Everyone permission count`, `Report Date` | **Supplied real exports:** three SharePoint/OneDrive permission snapshots, including repeated snapshots. Counts describe exported permission evidence; zeroes in one domain do not prove every exposure domain was assessed. |
| Content management assessment | Site identity, `Is inactive`, `Is ownerless`; may include activity dates, owner contact, storage and policy fields | **Supplied real export:** content management assessment CSV. Lifecycle findings remain separate from oversharing. A blank owner contact is not equivalent to `Is ownerless = True`. |
| Sensitivity-label inventory | `Labeled files` plus site sensitivity/label fields | **Supplied real export, empty only:** the header-only CSV was recognized; populated inventory values are not validated against a real sample. It provides no populated findings and does not establish DSPM exposure coverage. A label named `Public` is not proof of anonymous access. |
| Copilot readiness user export | `Report Refresh Date`, `User Principal Name`, `Has Copilot license assigned`, `Report Period`, `Uses eligible update channel`, `Uses Teams meetings`, `Uses Teams chat`, `Uses Outlook email`, `Uses Office docs`; optional `Suggested candidate for Copilot` | **Supplied real exports:** nine- and ten-column Copilot Readiness CSVs. An absent candidate column remains unknown for every exported row; other required columns must be present. True, false and unknown remain distinct; the actual report period is preserved. Exported licensed rows are not a tenant-wide license total. |
| Detailed Everyone/EEEU permissions | Recipient plus item identity/type or role-definition fields | **Documented/synthetic fixture only:** supported with Microsoft-documented headers; a representative tenant export is still needed to confirm that variant. |
| Sharing activity/details and DSPM exposure | Explicit sharing-link/recipient fields or sensitive/overshared-item counts | **Synthetic fixture only:** supported schemas and regression tests exist. Representative exports are still needed; a successful file read alone does not establish full coverage. |

| Copilot Usage portal export; other unrecognized exports | Varies by portal experience | **Unsupported pending a sample/schema review:** do not route arbitrary Usage exports into the Readiness or Dashboard importer. Keep the file for review and record the unmet evidence question. |

ZIP/XLSX handling has been tested with synthetic reports using supported headers. This does not promise compatibility with every Microsoft export variant. Unrecognized or missing evidence remains visible as a coverage limitation. Save a representative export for review if its headers differ; do not rename columns merely to make it import.

Keep each report's actual generation/as-of date and selected scope. Download time, a date in the filename, and a site's last activity date are not report generation dates. SAM/DSPM default freshness windows are 35/8 days; these are assessment policies, not Microsoft licensing rules. Undated results remain undated. For permission snapshots, newer comparable reports supersede older copies; verify per-file status in the workbook rather than summing snapshots from different dates.

## Permissions and licensing to arrange

For application API grants, setup scopes and profile differences, use [PERMISSIONS.md](PERMISSIONS.md).
The roles below govern customer export access. Restricted does not ask for SharePoint/Purview
administrative sign-in during collection, and `--services` never revokes application consent.

| Task | Access and licensing to confirm |
|---|---|
| Application setup and live APIs | An administrator who can manage the app plus **Privileged Role Administrator or Global Administrator** for Microsoft Graph application consent. Application Administrator alone cannot consent Graph application permissions. Portal roles and app consent are separate. [Microsoft consent guidance](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/grant-admin-consent). |
| SharePoint baseline and DAG reports | SharePoint Administrator and qualifying SAM entitlement. Supported routes include a qualifying base subscription with an assigned paid Microsoft Copilot license, the applicable SharePoint subscription plus SAM Plan 1, or the Microsoft 365 E7 suite route listed in current prerequisites. Confirm the exact subscription; Copilot Chat availability alone is insufficient evidence of entitlement. [SAM prerequisites](https://learn.microsoft.com/en-us/sharepoint/sharepoint-advanced-management-prerequisites). |
| Detailed Everyone/EEEU item report | **SharePoint Advanced Management Administrator**, including its additional content-metadata access, plus a completed organization permission baseline. Microsoft's instructions require a Global Administrator to assign this role. [Detailed report requirements](https://learn.microsoft.com/en-us/sharepoint/data-access-governance-detailed-eeeu-everyone-permissions-report). |
| Purview data risk assessments | Compliance Administrator can create assessments; applicable reader roles can view them. File details additionally require Content Explorer List Viewer/Content Viewer access. Verify the capabilities and scoped users against Purview licensing; Copilot alone does not establish entitlement to every advanced feature. Qualifying E5/Purview Suite subscriptions are relevant to advanced Purview capabilities. [DSPM permissions](https://learn.microsoft.com/en-us/purview/data-security-posture-management-permissions), [Purview service description](https://learn.microsoft.com/en-us/office365/servicedescriptions/microsoft-365-service-descriptions/microsoft-365-tenantlevel-services-licensing-guidance/microsoft-purview-service-description). |
| Copilot portal reports | Reports Reader or another role permitted for these reports, such as AI Administrator. A summary-only role does not provide user-detail access. Keep the tenant's existing report privacy settings unless an approved change is needed. [Usage report access](https://learn.microsoft.com/en-us/microsoft-365/admin/activity-reports/activity-reports). |

Microsoft 365 E5 without SAM entitlement provides DAG activity reporting limited to 10,000 sites; permission snapshot reports are unavailable under that route. If using that route, complete the required activity-data collection setup; allow up to 24 hours and expect history to accumulate from enablement. [DAG access and limitations](https://learn.microsoft.com/en-us/sharepoint/data-access-governance-reports). Microsoft 365 Business Premium is a base subscription and is distinct from the paid Microsoft 365 Copilot add-on.

For Standard's Purview configuration collection, arrange access to DLP, labels, retention, and Exchange organization, rights-management and audit settings. Purview portal access alone does not establish all Exchange permissions. Unattended setup assigns entire management roles containing the required commands; those roles may include write capabilities despite read-only group names. Review actual RBAC with the workload administrator. Use preflight and the final Collection Coverage worksheet to identify gaps; see [Purview permissions](https://learn.microsoft.com/en-us/purview/purview-permissions). Advanced features can require additional licensing even when authentication succeeds. Report an unavailable entitlement to the services team before considering a purchase.

## Reports to obtain next

1. **DSPM sensitive-data exposure assessment.** In Microsoft Purview, open **DSPM > Discover > Data risk assessments > Microsoft 365**. Check the latest completed default/custom result and use its Export option; retain assessment scope and date. The default assessment covers the top 100 SharePoint sites, not the whole tenant. Initial default results can take four days; custom results can take at least 48 hours. Choose a custom scope when the default omits the pilot's sites. Optional item-level scanning requires additional setup and permissions and currently has narrower SharePoint scope; arrange that separately with the team. [Assessment instructions](https://learn.microsoft.com/en-us/purview/data-security-posture-management-oversharing).

2. **Sharing-link activity reports.** In the SharePoint admin center, open **Reports > Data access governance > Sharing links**. Run/export the Anyone, People in the organization, and Specific people shared externally reports. Wait for completion, which can take 24 hours. The portal reports SharePoint; OneDrive activity reporting uses PowerShell. These show recent activity and complement the permission baseline. [Sharing-link instructions](https://learn.microsoft.com/en-us/sharepoint/data-access-governance-sharing-links-report).

3. **Everyone/EEEU detail and activity.** Once the organization baseline has completed, request **Sites and files shared via special SharePoint groups** for exact item-level permissions. The detailed report covers SharePoint and OneDrive together and can be rerun every 30 days. Also retain available EEEU activity reports for recent changes. The services team can assist if the additional SAM administrator role or report option is unavailable. [Detailed Everyone/EEEU instructions](https://learn.microsoft.com/en-us/sharepoint/data-access-governance-detailed-eeeu-everyone-permissions-report).

4. **Paid Microsoft Copilot usage, if deployed.** In the Microsoft 365 admin center, open **Reports > Usage > Microsoft Copilot > Copilot > Usage**, select the reporting window, and export the relevant usage tables. Usage is normally available within 48 hours of the end of the activity day in UTC. Keep paid Copilot and included Copilot Chat scope identifiable. The tool collects actual usage through its live API collector and preserves it in the saved collection. A new portal Usage CSV needs schema validation; do not pass it to the Readiness or Dashboard importer. [Usage export instructions](https://learn.microsoft.com/en-us/microsoft-365/admin/activity-reports/microsoft-365-copilot-usage).

5. **Copilot Readiness.** Open the **Readiness** tab beside Usage and export the user table. It describes technical eligibility and Microsoft 365 app activity over the last 28 days. Initial availability and data latency can each be up to 72 hours. Preserve the refresh date and period columns and existing privacy settings. A readiness flag, Teams activity or an assigned license does not prove actual Copilot usage. [Readiness instructions](https://learn.microsoft.com/en-us/microsoft-365/admin/activity-reports/microsoft-365-copilot-readiness).

Refresh the baseline when needed: **Reports > Data access governance > Site permissions across your organization > View reports > Create report/Run reports**. Export both the SharePoint and OneDrive results. The first baseline can take five days; later runs usually complete within 24 hours and are subject to a 30-day rerun interval. Record excluded or unavailable scope, including archived/NoAccess sites. [Baseline instructions](https://learn.microsoft.com/en-us/sharepoint/data-access-governance-site-permissions-report).

For a lifecycle refresh, use **SharePoint admin center > Advanced Management > Content Management Assessment** and review/export completed results. Start assessment only when a new report is required and the administrator has authorized it. Microsoft gives a 2–72 hour completion window and a 30-day reassessment cadence. Record the completion date separately when the export has no report-date column. Inactive/ownerless findings concern ownership and lifecycle; they do not establish sensitive-data exposure. [Content management instructions](https://learn.microsoft.com/en-us/sharepoint/content-management-assessment).

For label distribution, use **Reports > Data access governance > Sensitivity label applied to files**, choose labels with File scope, wait for completion, then download each report's CSV. Allow up to 24 hours; source data may lag generation by up to 120 hours. The detailed instructions currently limit creation to SharePoint even though the page introduction mentions OneDrive: record actual exported workload rather than assuming OneDrive coverage. A selected-label inventory does not assess unlabeled sensitive content. [Sensitivity-label report instructions](https://learn.microsoft.com/en-us/sharepoint/data-access-governance-sensitivity-label-report).

For recent EEEU changes, use **Data access governance > Activity reports > Shared with 'Everyone except external users' > View reports**. Retain filters for site template, privacy, sensitivity and report type. Allow up to 24 hours; the portal covers SharePoint, with OneDrive available through the documented PowerShell path. This activity evidence is distinct from the item-permission snapshot. [EEEU activity instructions](https://learn.microsoft.com/en-us/sharepoint/data-access-governance-everyone-except-external-user-report).

### When a report is unavailable

Record the exact report and question it would answer, affected users/sites/workloads, latest completed date if any, and the portal's reason: missing role, unavailable entitlement, unprovisioned feature, still running, export failure or unsupported schema. Have the workload owner confirm the reason and any alternative evidence. Do not relabel a lifecycle report as DSPM, infer exposure from sharing defaults, or enter zero for an unavailable metric. Retain completed empty exports with their scope/status; a header-only file cannot establish assurance. Unavailable required evidence qualifies the readiness conclusion; it is not itself a confirmed control failure or a purchase recommendation.

## Customer email draft

**Subject: Copilot readiness assessment — remaining reports and access checks**

Hi [Name],

Thank you for completing the initial assessment. To finish the tenant readiness review, we need to confirm access and add the remaining Microsoft-generated reports. Please reuse recent completed results where available and let us know about any reports that are unavailable under your subscription.

For Standard collection, please have your SharePoint administrator and Purview/Exchange administrator available for the access check. For Restricted, arrange those administrators only for the export requests and review of remaining configuration evidence. If the assessment app needs additional Microsoft Graph consent, a Privileged Role Administrator or Global Administrator must complete that step. Existing app credentials can be reused when tenant and app identity match; please do not send us passwords, client secrets or private certificates. [Consent requirements](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/grant-admin-consent).

From the assessment folder, run:

```powershell
python main.py --mode live --check-connections
python main.py --mode live --interactive-auth fresh
```

For an agreed Restricted assessment, use `--env-file .env.restricted` on both commands and omit
`--interactive-auth fresh`. The application's access and resulting coverage are described in
[the permissions guide](PERMISSIONS.md).

The assessment saves the collection JSON and adjacent portable package automatically in `output/collections/` and prints their locations. No save option is required. The access check alone does not create a collection.

Please collect the following using the instructions above:

- **SharePoint and OneDrive permission baselines**, plus Sharing links and Everyone/EEEU reports. Use SharePoint admin center > Reports > Data access governance. Item-level Everyone/EEEU detail needs the additional SharePoint Advanced Management Administrator role.
- **Purview data risk assessment**, including the completed result, scope and date. Use Purview > DSPM > Discover > Data risk assessments. Viewing an assessment and viewing its file details can require different permissions.
- **Copilot Readiness** and, if paid Copilot is already deployed, **Copilot Usage**, from Microsoft 365 admin center > Reports > Usage > Microsoft Copilot. Please export them separately; readiness does not measure actual usage.

SAM and advanced Purview features depend on your licensing. We can review your existing subscriptions and any unavailable options before recommending changes. Missing reports will be recorded as limitations so that we can distinguish an access issue, a licensing gap and a finding that needs remediation.

Please upload the entire portable assessment folder and any later original portal exports through [secure transfer location]. Include the report date, selected users/sites/workloads, any filters, and completion status. If an export is empty, include its completion/scope details; if an operation fails, provide the error with secrets removed. Initial Microsoft scans can take several days, so please start missing reports ahead of our review session.

With the saved collection and exports, we can rebuild the HTML assessment and evidence workbook offline without another tenant sign-in. We will identify the original collection date and any remaining evidence gaps in the updated report.

Thanks,

[Name]

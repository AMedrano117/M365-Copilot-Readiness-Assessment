# App Builder export

[Documentation index](README.md) | [Operator runbook](RUN.md) | [Dashboard JSON](DASHBOARD_JSON.md)

App Builder exports are optional and omitted from default report builds. Add `--extra-exports app-builder` to a live or offline command to create the flat folder of small files beside the HTML and workbooks. This preserves the export for consumers that can accept its file set; splitting files does not guarantee an app can upload every part. Use the Excel workbooks for the standard evidence handoff.

```text
Reports/<customer>/<date>/Builds/<build>/App Builder/
  00-upload-guide.md            which files to upload together, units, limits, starter prompt
  00-starter-prompt.txt         copy-paste prompts: one generic, one per concern
  01-overview.json              decision, counts, priority findings, concerns and their upload sets
  02-finding-catalog.json       one entry per finding: IDs, title, concern, evidence kind, availability, counts and files
  03-manifest.json              every file with purpose, finding IDs, rows, bytes, SHA-256 and part number
  NN-<concern>-findings.json    issue, evidence summary, limitations, reconciliation and technical fix per finding
  NN-<concern>-<table>-pNN.csv  evidence rows, one homogeneous table per numbered series
```

Every file is smaller than 1,000,000 bytes, measured on the bytes written to disk. Parts target about 250 KB; a table that would need more than eight parts uses larger parts (up to 900 KB) so an upload set stays small. No folder upload, ZIP extraction or linked JSON traversal is needed. The same files are produced by live and offline builds from the saved evidence, keeping each source's original collection date and window.

## What to upload together

Open `00-upload-guide.md`. Each concern lists its **required** files and any **optional supporting context**:

1. `01-overview.json` and `02-finding-catalog*.json`.
2. The concern's `NN-<concern>-findings*.json`.
3. Every `-pNN` part of the concern's evidence tables, and any `-oversized-values-pNN.csv` files.

Then paste the matching prompt from `00-starter-prompt.txt`. Microsoft notes that App Builder cannot add data to lists it created after the app is built, so upload the full set when you create the app.

**A findings or overview file alone gives the app no records.** Each part of a table holds different rows; only all parts together hold every record. The manifest's `upload_sets` repeats the required and optional files for each concern.

Supplied PDFs also produce optional `04-pdf-highlights*.json` files. The overview's `portal_report_context` lists their files and excerpt count, and every upload set includes them as supporting context. Upload all parts to show report highlights with their capture dates, source pages, reporting qualifications and follow-up. Treat these as quoted portal context; do not convert OCR values into verified measurements, combine their counts with API reports, or use them to pass a readiness control.

Concerns are groups of related findings: legacy authentication, MFA registration, Conditional Access, admin access, identity risk, application consent, connectors and agents, devices, security incidents, content sharing, data protection, licensing and adoption, and external AI.

## How rows connect to findings

| Column | Meaning |
|---|---|
| `finding_id` | Finding key in the catalog, findings JSON, HTML evidence pages and workbook. Context files list `finding_ids` instead. |
| `detail_record_id` (`DET-`) | One exported evidence record for one finding. Identical in the workbook evidence sheets and HTML pages. |
| `evidence_record_ids` (`EVD-`) | The original retained source rows, matching the workbook Raw sheets' `Evidence Record ID` and the dashboard JSON. |
| `dataset`, `dataset_id` (`SRC-`) | The source dataset and its collection metadata. |
| `collected_at`, `window_start`, `window_end`, `source_state` | The source's original collection date, observation window and completeness. |

Finding IDs such as `ENT-005` are assigned per build. `finding_key` and `finding_fingerprint` are stable references across builds.

## Counts and units

- `record_count` counts exported rows in `record_unit` (for example sign-in events or grant records).
- `affected_entity_count` counts unique entities in `entity_unit` (accounts, applications, principals, devices, sites or incidents), using stable identifiers. It is null when no stable identifier exists.
- `worklist_count` is the workbook worklist count; a worklist item can group several records (`count_relation` explains differences).
- `context_record_count` counts unique supporting-context records.

Do not add counts across findings: one source row can support several findings.

## Evidence kind and availability

`evidence_kind` is `observed_event`, `entity_state`, `configuration`, `supporting_context`, `aggregate_only` or `none`. `evidence_availability` is `complete`, `partial`, `unavailable`, `not_retained`, `absent`, `historical`, `planning` or `unknown`. Findings without rows remain in the catalog and findings JSON with their limitations and `missing_evidence_action`, which states what collection or export would supply the records. Aggregate counts are never expanded into invented rows.

## Legacy authentication

The legacy-authentication evidence table holds one row per retained sign-in event matched by the existing classification rule, with no deduplication. Each row keeps the event ID, time, account, application, reported client type (`clientAppUsed`), `authenticationProtocol` when returned, IP address, location, correlation ID, Conditional Access status, applied policies when retained, error code, failure reason and an outcome:

| Outcome | Rule |
|---|---|
| Succeeded | `status.errorCode` 0 and Conditional Access did not fail |
| Blocked | Conditional Access status `failure` or AADSTS 53000–53003 (Conditional Access); 50053 (Entra sign-in protection) |
| Failed | Any other numeric error code, kept with its failure reason |
| Unknown | No readable error code, or conflicting fields |

`outcomeBasis` records the fields used. A succeeded request describes the token request, not the data accessed. Limits:

- `clientAppUsed` is the reported client type; it does not establish the exact protocol. Microsoft Graph v1.0 does not return `authenticationProtocol`.
- The collector reads v1.0 interactive user sign-ins for seven days (up to 100 pages); non-interactive and service-principal sign-ins and older events are not included.
- The classification rule does not match some Microsoft-listed legacy client types (for example MAPI over HTTP and Autodiscover). Retained events of those types are counted in the finding's limitations, not added to it.
- Settings that *allow* legacy authentication, such as the SharePoint legacy-authentication setting, are separate configuration findings and tables.

## Limits and handling

- Rows are never sampled or shortened. A value too large for one file moves unchanged into `NN-<concern>-oversized-values-pNN.csv`; the original cell names the series, `detail_record_id`, field and chunk count. Join chunks in `chunk_index` order to rebuild it. `03-manifest.json` lists them under `oversized_records`.
- CSV files are UTF-8 without a BOM. Values are exact; some may begin with `=`, `+`, `-` or `@`. Open CSV files in Excel through **Data > From Text/CSV**, or use the workbook, so no value runs as a formula. The manifest counts such cells.
- Files contain named users, applications, devices and IP addresses. Credentials, tokens and prompt/response content are excluded. Handle the folder like the evidence workbook.

## Rebuild the files from an existing package

To create App Builder files from an existing dashboard JSON package without rebuilding or tenant access:

```powershell
.\.venv\Scripts\python.exe tools\build_app_builder_export.py "Reports\<customer>\<date>\Builds\<build>\JSON\index.json"
```

The default destination is `App Builder` beside that build's report and `JSON` folder. Add `--out` to choose another new or empty folder. Files created by a report build already occupy the default folder; choose a new folder when regenerating them. The helper also accepts older nested JSON packages.

The `workbook` deliverable identifies the assessment workbook; `technical_workbook` identifies its sibling technical evidence workbook. Keep both beside the HTML report. Existing investigation/raw/lineage ranges describe the technical evidence model; new assessment evidence ranges describe the readable finding blocks.

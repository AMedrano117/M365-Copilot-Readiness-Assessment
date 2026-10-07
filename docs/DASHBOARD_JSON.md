# Dashboard JSON for agents and imports

Dashboard JSON packages are optional. Add `--extra-exports dashboard-json` to a live or offline command to write `JSON/` inside its `Reports/<customer>/<date>/Builds/<build>/` folder and the sibling company-named ZIP. Start with `index.json`; it links to the summary, finding catalog, source catalog, retained evidence, and full assessment components. Findings and raw records are split into small files so an agent can fetch the relevant detail without loading the entire assessment. Use the exact dashboard JSON path printed by the console. Default builds omit this package.

```text
Reports/<customer>/<date>/Builds/<build>/JSON/
  index.json          start here: assessment identity and navigation
  summary.json        readiness decision, counts, scope, and dates
  findings/index.json finding catalog and individual finding links
  sources/index.json  source catalog and collection metadata
  evidence/index.json retained record ID lookup and evidence page links
  assessment/index.json complete assessment component links
```

Each JSON file is limited to 64 KiB by default, and record pages contain at most 50 records. Record pages place each record on its own line. A long native record, nested object, or string is split through linked JSON components; values are retained without truncation. Follow the published links to read those components rather than treating a reference as the original value. Every published `path` is relative to the package root, the folder containing the main `index.json`.

The root index has `format: "m365-readiness-assessment-package"` and `package_schema_version: "1.0.0"`. Its `entry_points` object contains `summary`, `findings`, `sources`, `evidence`, and `assessment` paths. Catalog indexes expose `pages`; each page descriptor includes its root-relative `path`, `record_count`, and `sha256`. Catalog pages contain `records` arrays.

When PDF captures are supplied, `entry_points.portal_reports` points to `portal/index.json`. Its catalog pages retain every highlighted source excerpt with `highlight_id`, capture ID, source PDF hash, page, extraction method, capture date, report refresh date, follow-up and qualification. `summary.json` includes selected highlights in `portal_reports`; the reconstructed assessment preserves the complete `portal_report_highlights` array. These are quoted review context, not verified numeric metrics or scored findings. Missing dates remain null, and PDF reporting windows are not merged with API evidence.

Finding files use the familiar IDs, such as `findings/ENT-005.json`. Their `detail` property contains or references the normalized finding, including its paged records. Evidence locator records identify the `record_id`, `dataset_id`, `dataset`, evidence page `path`, and the record's zero-based `record_index` within that page.

A `$json_package_node` value identifies a linked array, object, string, or shared value. Its `kind` and `parts` explain how to follow the component paths; component pages contain `items`, `entries`, or `value`, respectively. A `value` reference selects one shared value using `value_key` and, when present, the zero-based `record_index`. The `parts` list can itself be split through the same mechanism. The reconstruction helper resolves these links and verifies the contents. An original source property with the same marker name is escaped and restored rather than mistaken for a package reference.

To review a finding, read the root index and summary, locate its ID in the finding catalog, then open that finding's metadata and relevant record pages. Resolve its `evidence_record_ids` through the evidence catalog to read exact retained source rows. Read source metadata for collection windows, failures, and qualifications. The folder retains one raw occurrence per evidence ID; several findings can point to the same evidence row.

`--snapshot-json <path>` writes the single-file dashboard contract for consumers that need it, without enabling split JSON or other extra packages. An optional split folder includes the same information and can be reconstructed locally without collecting tenant data:

```python
from Core.dashboard_package import read_dashboard_package

assessment = read_dashboard_package("Reports/<customer>/<date>/Builds/<build>/JSON/index.json")
findings = assessment["findings"]
evidence = {row["record_id"]: row for row in assessment["evidence_records"]}
```

```powershell
python main.py --mode offline --collection-input "Reports/<customer>/<date>/collection.json" --evaluation-date 2026-10-02 --snapshot-json "output/<customer>/dashboard-import.json"
```

Offline builds use saved evidence and supplied files. They do not query the tenant. A rebuild can expose details already retained in an older collection; it cannot recover fields or records that collection never saved. Keep the saved collection for future assessment rebuilding. The dashboard assessment export is a different format and is not an input to `--collection-input`.

## Uploading to an app builder

For Microsoft 365 App Builder (Frontier), upload the files from `App Builder/` beside the report in the same build folder. They are small, individually uploadable CSV and JSON files grouped by concern, with a manifest, upload guide and starter prompt; see [App Builder export](APP_BUILDER.md). They use the same finding, `DET-`, `EVD-` and `SRC-` identifiers as this package.

When `--extra-exports dashboard-json` is selected, the build also includes an upload file, `Dashboard JSON - <company>.zip` beside the report, for builders that accept a ZIP. The ZIP contains the same small JSON files with `index.json` at the archive root, plus `Read First.txt` with reading instructions. Finding, source, and evidence links retain their relative paths. The archive preserves the complete assessment; it does not add the original large JSON, saved tenant collection, or unrelated files. Keep the split folder for local review.

Upload the ZIP only when the app builder supports ZIP files and extracts their contents for its agent. Accepting file uploads does not establish ZIP support. If the builder accepts JSON files only, upload its supported individual JSON files or use a format prepared for that builder; renaming a ZIP to `.json` will not work. The original `--snapshot-json` single-file export remains available but still requires the consumer to handle the complete assessment.

After extraction, instruct the agent to start with `index.json` and `summary.json`, then read only the finding and evidence pages needed for its task. Extraction should preserve the directory layout. A ZIP provides one file to upload; it does not reduce the total evidence or change the per-page limits.

To archive an existing split assessment locally without another collection or rebuild:

```python
from Core.dashboard_package import write_dashboard_archive

archive = write_dashboard_archive("Reports/<customer>/<date>/Builds/<build>/JSON/index.json")
```

The helper validates referenced contents before writing and preserves an existing archive rather than overwriting it. Supply a new second argument to choose another ZIP path.

## Reconstructed and single-file import contract

The following fields describe the reconstructed assessment and the optional `--snapshot-json` file. Folder indexes expose navigation instead of embedding large record arrays. Use the reconstruction helper when a dashboard needs the complete existing contract.

| Field | Meaning |
|---|---|
| `format` | `m365-readiness-assessment` |
| `dashboard_schema_version` | Dashboard import contract, currently `1.0.0` |
| `evidence_schema_version` | Evidence interpretation contract, currently `1.1.0` |
| `methodology_version` | Assessment methodology, currently `4.0.0` |
| `tenant_id`, `tenant_name` | Assessed tenant |
| `evaluation_date` | Date used to evaluate evidence freshness and readiness |
| `generated_at` | Export generation timestamp; does not change the observation dates |
| `decision`, `counts` | Shared assessment conclusion and summary counts |
| `findings` | Dashboard entries with normalized field names and detail records |
| `recommendations` | Existing recommendation columns preserved, plus the same detail records |
| `evidence_records` | Complete retained source rows, stored once and addressable by record ID |
| `sources` | Dataset identifiers, collection metadata, windows, limitations, and row counts |
| `assessment_result` | Existing complete shared assessment result, with enriched recommendations |

Use `findings` as the dashboard's finding table. `finding_id` is the familiar ID, such as `ENT-005`; `finding_uid` also incorporates the tenant so two tenants do not collide. Each entry includes its title, domain, control, priority, owner role, readiness effect, observation date, evidence level, and operational result when established.

Do not add `findings` and `recommendations` together: these are two views of the same findings. Similarly, a raw row can support several findings. Store `evidence_records` once, then resolve each detail record's `evidence_record_ids` to `evidence_records[].record_id`.

## Named records

In the reconstructed assessment, each finding contains the fields below. In the default folder, finding metadata links to its paged `records` instead of repeating them in every finding view.

| Field | Meaning |
|---|---|
| `records` | Selected named records or qualified supporting detail rows |
| `record_count` | Number of exported detail rows; may differ from affected people, applications, files, or devices |
| `record_type` | Record unit, such as registration, role assignment, permission grant, or site summary |
| `record_status` | `records_available`, `supporting_context`, `summary_only`, `absence`, `unavailable`, `historical`, or `planning` |
| `record_selection` | The actual selection applied to the retained source rows |
| `record_limitations` | Missing fields, incomplete reads, unresolved joins, and scope qualifications |
| `record_reconciliation` | Reconciliation between exported rows and the finding's measures, including record units |
| `evidence_record_ids` | Supporting retained raw rows associated with the finding |

Each entry in `records` has a stable `record_id`, a `fields` object for dashboard columns, `field_status` describing availability or derivation, and `source_refs` identifying exact retained dataset occurrences. Source and record indexes are zero-based. The associated `evidence_record_ids` provide direct access to native JSON rather than Excel row ranges.

Use the `DET-` detail `record_id` as the exported record key. `source_record_id` preserves the original native record/entity identifier when available; it is a separate attribute and can repeat across returned source occurrences. A finding or source row's original ID does not replace the tenant-scoped export key.

Missing values remain `null`; `field_status` explains why. A false MFA-registration flag, a reported zero count, and an unavailable field are different states. A finding with zero exported rows does not establish zero affected entities. Check its status, selection, reconciliation, and limitations before interpreting the count.

`supporting_context` means retained inventory or configuration rows support the finding without asserting an affected subset. `summary_only` means the detail consists of metrics or aggregate site records, rather than named affected entities. Both retain useful rows and links; neither recreates an individual population from a count.

| Finding | Detail fields |
|---|---|
| `ENT-005` — MFA registration gaps | `upn`, `displayName`, `isMfaRegistered`, `defaultMfaMethod`, `isAdmin`, `isLicensed`, `lastSignIn` |
| `ENT-011` — Standing admin access | `principalDisplayName`, `upn`, `roleName`, `scope`, `assignmentType`, `activatedSince`, plus retained principal/role/assignment identifiers |
| `ENT-013` — Risky users | `upn`, `displayName`, `riskLevel`, `riskState`, `riskDetail`, `lastRiskyActivity`, `location` |
| `ENT-017` — Consent policy assignments | `policyId`, `description`, `includeConditions`, `excludeConditions`, `publisherVerifiedRequired`, `scope` |
| `ENT-018` — Application grants | `appDisplayName`, `publisher`, `publisherVerified`, `grantType`, `exactScopes`, `consentingPrincipals`, `lastUsed` |
| Legacy authentication sign-ins | `eventId`, `createdDateTime`, `userPrincipalName`, `userId`, `appDisplayName`, `appId`, `clientAppUsed` (reported client type), `authenticationProtocol` (null when not returned), `outcome`, `outcomeDetail`, `outcomeBasis`, `errorCode`, `failureReason`, `conditionalAccessStatus`, `appliedConditionalAccessPolicies`, `ipAddress`, `location`, `correlationId` |
| `DEF-011` — Active incidents | `incidentId`, `severity`, `title`, `status`, `classification`, `firstSeen`, `assignedTo`, `category` |
| `DEX-001` — Broad sharing | `siteUrl`, `workload`, `exposureType`, `exposureCount`, `countUnit`, `permissionedUserCount`, `lastModified`, `recordGranularity` |
| `M365-123` — External sharing configuration | `siteUrl`, `sharingSetting`, `defaultLinkType`, `anyoneLinkExpiryDays`, `externalGuestsAllowed`, with scope qualifications |
| Antivirus signature findings | `deviceName`, `deviceId`, `osPlatform`, `signatureVersion`, `signatureAgeDays`, `lastSeen` |

The IDs in this table are examples from one build. Finding IDs are assigned per build, so record adapters are chosen from finding content (FindingKey, evidence key and wording), never from the ID. Legacy sign-in records are the retained `signin_logs` events matched by the classification rule, each linked to its native evidence record. `outcome` is Succeeded, Blocked, Failed or Unknown from `status.errorCode` and `conditionalAccessStatus`; see [App Builder export](APP_BUILDER.md#legacy-authentication) for the rule and its limits. Each finding also carries `recommendation_detail` (what to change, prerequisites, where to configure, how to verify and verified documentation links), plus `finding_key` and `finding_fingerprint` for cross-build reference.

Other findings also expose retained supporting detail when an exact source selection or investigation range is available. This includes configuration inventories and operational or owner-review records. The export states whether those rows describe an affected population, a configuration, a summary, or supporting context.

## Evidence and interpretation

Join entities with stable identifiers. Matching display names alone does not establish that a directory user, application, device, or reporting record is the same entity. Unresolved or conflicting joins remain visible in qualifications.

Registration demonstrates enrollment, not MFA enforcement. An Authenticator method does not independently establish passwordless use. Application consent records identify granted permissions; they do not identify the human approver unless that principal was retained. An application's last use is limited to suitable returned sign-in or usage evidence and the recorded observation window.

Keep sharing units separate. A site summary with five organization-wide links is a summary row, not five named files. Link counts, permission counts, and unique files are different measures and may overlap. `recordGranularity` and `countUnit` identify the available units; the exporter does not invent item records from aggregate counts.

External-sharing settings establish allowed configuration. They do not independently prove guest access or actual links. Endpoint health dates describe endpoint reporting, not the time the report was rebuilt. Dated configuration and owner reviews remain separate from observed operation.

Older SharePoint bulk site inventories can contain default-valued properties. Retain their reported link and expiry settings as raw attributes, but use the qualified `effectiveDefaultLinkType` and `effectiveAnyoneLinkExpiryDays` fields for effective site behavior. These stay `null` when the saved query does not establish detailed site settings and relevant inheritance or override behavior. A tenant default is not silently substituted as a confirmed site setting. Query identity, collection date, and the recorded documentation qualification explain the available evidence.

Historical summaries cannot recreate raw records. Planning findings identify the missing decision or review. Failed, partial, and unavailable reads remain usable only within their stated limits. Source metadata retains original dates, request windows, paging details, and failures when supplied by collection.

## Raw detail, identity, and handling

`evidence_records[].raw` retains native nested objects, arrays, identifiers, dates, and long strings. Folder pages split oversized values through lossless components; reconstruction restores the original values. JSON detail is not shortened to fit Excel cells and does not use worksheet continuation columns. Duplicate returned source rows remain separate occurrences. Evidence IDs are deterministic for the same tenant and retained source occurrence; generation timestamps do not change them. Reordered or changed source occurrences can have different IDs.

Named user, device, application, and site details are included by default. Credentials, authentication tokens, and prompt/response content are excluded. The main HTML report remains aggregate-only; named records appear in the workbooks and, when requested, linked technical evidence pages. Treat the JSON with the same access controls as the detailed workbook and saved tenant collection.

For the underlying evidence standard and collection sources, see [Assessment evidence and outputs](ASSESSMENT_EVIDENCE.md). For saved collection handling and replay, see the [operator runbook](RUN.md).

The `workbook` deliverable identifies the assessment workbook; `technical_workbook` identifies its sibling technical evidence workbook. Keep both beside the HTML report. Existing investigation/raw/lineage ranges describe the technical evidence model; new assessment evidence ranges describe the readable finding blocks.

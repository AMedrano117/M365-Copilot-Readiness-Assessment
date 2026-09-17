# Assessment redesign validation

> Historical redesign record, retained for context. Its instructions, paths and validation counts describe the work at that time. Use the [current documentation](../README.md) for operating the assessment.

Validated on 2026-09-15 against `ASSESSMENT_REDESIGN_PLAN.md`.

## Baseline and preservation

The starting working tree already contained the offline importer, automatic collection saving, portal parsers, and report changes. Those changes were retained and extended. The baseline was **251 passing unittest tests**. Original local reports, workbooks, cache, and portal exports were preserved. Customer artifacts remain in ignored `Reports/` and `output/` locations; tracked fixtures use invented identities and data.

## Acceptance evidence

| Criteria | Result and evidence |
|---|---|
| 1–3: narrative, domains, consistent actions | One assessment result supplies the decision, seven core domains, action register, counts, chart, console summary, and workbook. Confirmation actions also satisfy the corresponding request for evidence without creating a duplicate task. Report completeness and evidence completeness are separate from readiness. |
| 4–6: missing values and source qualification | Unknown measurements stay distinct from zero. Source dates are retained; a rebuild does not refresh them. Raw policy checks run on both collection and replay. Historical risks require confirmation; positive historical observations require actual linked evidence to appear in the customer narrative. Original conclusions and collection gaps remain in the workbook. |
| 7–9: portable, isolated, equivalent replay | Tests copy complete packages, remove original paths, block authentication, sockets, prompts, subprocesses, and collection writes, then compare the entire assessment result. A copied real local legacy/cache/export recipe was also replayed under these blocks and produced the same result. Evaluation date and freshness thresholds are recorded. Tenant comparison is shared; incompatible methodology versions are rejected. |
| 10–11: input outcomes and privacy | Receipts preserve accepted, selected, superseded, unsupported, rejected, empty, and identity-unverified outcomes. Raw observations retain conflicts, source hashes, units and scope. User usage detail remains an explicit workbook opt-in and is absent from HTML. Imported HTML is escaped; workbook formulas remain inert text. |
| 12: representative cases | Complete synthetic saved collection plus reports; existing local workbook/cache/export combination; portal-only evidence; stale, future, conflicting, duplicate, empty, missing and superseded sources; narrower snapshots; foreign-tenant inputs; and legacy collection compatibility are covered. |
| 13: visual and editorial review | Rendered HTML reviewed in installed Edge through Playwright at 1440 px desktop and 390 px narrow widths. Reviewed opening assessment, action register, domain navigation, and integrated historical usage. Narrow page width equals viewport width; all HTML anchors resolve. Workbook ZIP/XML, table/filter integrity and internal evidence links were verified; no formula cells were present. |
| 14: operator handoff | README, RUN, portal guide, prerequisites and methodology describe the same workflow. **35 documented main.py examples** were validated against the argument parser. Microsoft report/role/licensing guidance was checked against linked official Microsoft sources. The compatibility matrix distinguishes supplied schemas from fixture-only and unsupported variants. |

## Validation commands

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
git diff --check
```

Initial automated result: **303 tests passed**. After reviewing a subsequently supplied live run, the expanded suite passes **336 tests**. The complete synthetic example reaches a controlled-pilot recommendation; incomplete evidence remains unconfirmed. Agent-run live collection was exercised with mocked collectors, never a tenant connection.

Local generated paths and customer-specific validation counts are recorded in ignored `output/redesign/final-artifacts.json`. Workbook checks are in `output/redesign/workbook-validation.json`; visual review images are under `output/playwright/redesign-final-*.png`. The canonical operator commands are in [RUN.md](../RUN.md).

## Follow-up: supplied live run and offline exports

The user's subsequent live run exposed defects not covered by the initial fixtures. Corrections now apply to both future collections and replay of the saved evidence:

- Defender incidents use a page size accepted by the observed API response. Pagination remains delegated to the shared Graph client, following [Microsoft's incident pagination guidance](https://learn.microsoft.com/en-us/graph/api/security-list-incidents?view=graph-rest-1.0). Failed, partial and skipped feeds cannot become measured zero or an incident pass through successful alert/device reads.
- Unrequested Purview workloads no longer become missing-policy failures or positive eDiscovery findings. Product marketing and inventory narratives remain workbook context. Sampled sign-in observations are limited to their returned records; label definitions do not imply automatic labeling or publication coverage.
- Device inventory alone cannot establish the pilot's endpoint baseline. Actual device risks remain actionable. Shared evidence tasks and duplicate configuration strengths are consolidated, and sharing findings map to content access.
- Local verification confirmed the repository `.env` supplies the SharePoint admin URL. URL/source logging, quoted/BOM configuration parsing, same-URL retry, framed UTF-8 JSON, schema validation and explicit failure/partial messages address the collector path. The exact historical stdout corruption is unknown because its raw content was not retained.
- Concurrent collection progress uses complete stage messages. The console explains failed reads, separates action types, groups by assessment domain and combines each export's selection/identity receipts without dropping the original structured records.

The final real offline rebuild was copied and replayed with the original evidence paths, sockets, authentication, prompts, administrative subprocesses and collection writes blocked. Its entire assessment result matched. The original collection hash stayed unchanged. Final HTML/workbook counts agree; **84 internal workbook links** resolve, no formula cells were present, and all HTML anchors resolve. Desktop and 390-pixel mobile review passed without document overflow.

Latest customer-specific artifacts and counts are in ignored `output/live-review/validation.json`; the corrected console is `output/live-review/corrected-console.log`. These outputs retain the source failures from the original live run. A future authorized collection is needed to obtain the missing incident and SharePoint evidence; rebuilding cannot retry those reads.

## Follow-up: automatic PDF import and failed-run recovery

`--reports-dir` now discovers PDFs recursively, deduplicates identical contents, extracts text
locally and uses Windows OCR for image pages. It creates a qualified JSON manifest, page
previews and original PDF copies before live authentication. `--portal-review` continues to
support curated manifests and accepts a PDF folder for compatibility. OCR text remains
unscored context; a neighboring card's number is never attached to a detected caption.

Validation on 2026-09-15:

- **473 tests passed**, including early manifest rejection, nested PDF discovery, duplicate
  files, image-only extraction, unavailable OCR, malformed PDFs, mixed manual/automatic
  captures, inert HTML/Excel content, unchanged readiness, and repeated portable recovery.
- A real saved collection from the interrupted live run was recovered offline with four
  PDFs and nine pages. Every page produced local OCR text. All original source hashes stayed
  unchanged. JSON text and the workbook's **PDF Extracted Text** tab match.
- The recovered package was copied and rebuilt with network connections, process launches
  and prompts blocked. Its complete assessment result matched both the first recovered build
  and the same assessment without PDF context. Replay retained originals and previews.
- Edge checks at desktop and 390-pixel widths verified four PDF downloads, nine loaded
  previews, nine text sections, working anchors, no horizontal overflow, and unclipped print
  text. The existing navy/teal report styling is preserved.
- Missing input manifests are rejected before authentication. A later packaging failure now
  prints the already-saved collection path and offline recovery command.

Customer-specific receipts and paths remain in ignored `output/` subfolders;
test output is retained locally. No new tenant collection
or external AI service was used. New image-only PDF intake may launch the local Windows OCR
worker; copied-package replay needs neither OCR nor administrative PowerShell.

## Remaining evidence limitations

- Historical workbook conclusions cannot be recomputed as newly observed tenant facts when the underlying collection is absent. Their original methodology and dates remain available for confirmation.
- Exported rows establish their own population only. Different snapshot populations do not prove that omitted sites were removed or remediated. Unknown source identity, filters, dates and completeness remain explicit.
- The supplied sample set does not validate a populated DSPM risk export or every Microsoft sharing/activity/dashboard variant. See the [compatibility matrix](../PORTAL_REPORTS_AND_OFFLINE.md).
- A sensitivity inventory with headers but no rows establishes a recognized schema, not absence of sensitive content.
- A configured safeguard is evidence of configuration, not proof of effective coverage across all pilot users or content. Licensing, actual activity, application prerequisites, content permissions, and security controls remain separate questions.
- No new live tenant collection, Microsoft scan, policy change, license purchase, message sending, or external runtime AI service was used.

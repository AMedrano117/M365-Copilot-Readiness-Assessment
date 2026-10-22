# Import an earlier assessment when collection JSON is unavailable

[Run guide](RUN.md) | [Assessment runs](ASSESSMENT_RUNS.md)

Use `--import-historical` to register an existing assessment workbook and its HTML
reports as a **qualified historical Initial**. Run this locally; no customer files
need to be shared. The import is offline and performs no authentication or tenant
collection. Do not use an environment file for the import.

```powershell
python main.py --import-historical 'C:\Private\EarlierAssessment' --plan
python main.py --import-historical 'C:\Private\EarlierAssessment'
```

A selected folder must contain exactly one `.xlsx` workbook at its top level.
The workbook needs a `Recommendations` sheet with `Service`, `Feature`, and
`Recommendation` columns. Top-level `.html`/`.htm` companions are archived too.
No subfolders are searched and no latest build is guessed. With multiple builds,
select one workbook explicitly and list only its matching HTML reports:

```powershell
python main.py --import-historical 'C:\Private\Build2\assessment.xlsx' `
  --historical-html 'C:\Private\Build2\summary.html' `
  --historical-html 'C:\Private\Build2\details.html'
```

One import represents one customer's assessment. Do not select a directory mixing
customers or unrelated reports. HTML is preserved as bytes; the importer never
opens a browser, executes scripts, fetches links, or uses HTML as scored evidence.
The originals can still contain active HTML; protect them as original customer
files. Exact workbook copies retain all original worksheets, including any user
information; user-level Copilot tabs are excluded from semantic interpretation.

The original **evaluation date** and **tenant GUID** are required. They are read
from explicit workbook metadata when present. If unrecorded, add
`--evaluation-date YYYY-MM-DD` and/or `--tenant-id '<tenant-GUID>'`. Declared values
are marked as operator supplied, not independently verified. Conflicting tenant
IDs/dates, malformed metadata, future dates, unsupported workbooks and symbolic
links stop the import. Generation dates, file names, folder names, file modification
times, credentials and process environment never supply the original evaluation
date or tenant identity. Preflight reads files to validate them but writes no
outputs, history or identities.

Optional `--customer-name` controls the customer Reports folder.
`--historical-output-dir '<new-package-directory>'` selects an entirely new package
outside the original source folders; an existing destination is rejected. Without
it, the normal new dated Reports package is allocated using the import date.
Original evaluation date and actual import/creation time are stored separately.
Normal source publication policy still applies; production requires clean,
committed source. Import flags cannot be combined with collection/replay inputs,
other run intents or `--env-file`.

The new package contains:

- `inputs/historical/` with original workbook/HTML filenames and bytes preserved.
  Relative links between matching reports are retained; duplicate filenames are rejected.
- `import-receipt.json`: source SHA-256 hashes, dates, identity basis, original
  recorded versions, new assessment/run IDs and explicit evidence limitations.
- `HISTORICAL-IMPORT.txt`: the limitations and a ready-to-edit reassessment command.
- Generated `assessment-history.json`, identity seeds, `assessment-run.json`, and
  an immutable qualified snapshot under `assessment-results/`.

Do **not** populate the history JSON yourself. No collection JSON is invented and
no newly scored customer workbook or HTML report is generated. Original reports
remain the historical deliverables. Importer methodology/source versions describe
the import operation; original versions are retained separately or marked
unrecorded. The historical snapshot cannot be rendered as a fresh assessment.

For the next connected run, use the command in `HISTORICAL-IMPORT.txt` with the
correct customer environment file. `--reassess` uses the generated history and
requires explicit baseline selection (interactive confirmation, or
`--baseline-run-id` for unattended execution). Reassessments are placed beneath
the imported Initial package in separate `Runs` subfolders. Source snapshot and
archived report hashes are checked before baseline selection.

A comparison against this report-only Initial is **NotComparable** for technical
delta: it cannot establish improvement, regression or resolution. Missing findings
are not proof of remediation. A later run with sufficient collected evidence can
be selected explicitly as the baseline for a subsequent technical comparison;
the historical Initial and original reports remain available in history.

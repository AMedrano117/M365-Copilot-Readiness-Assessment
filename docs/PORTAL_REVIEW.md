# Import admin-center PDFs

[Documentation index](README.md) | [Project overview](../README.md)

Put the PDFs alongside the customer's exports and use **`--reports-dir`**. No hand-written JSON or separate PDF option is required. PDF subfolders are included, and identical files are imported once.

```powershell
.\.venv\Scripts\python.exe main.py --mode offline `
  --collection-input "<collection-input>" `
  --reports-dir "C:\Customer\Admin Reports" `
  --open-html-report
```

The same `--reports-dir` option works in live mode. The tool prepares PDFs before tenant authentication, then retains the generated JSON, original PDFs and page previews in the assessment package.

## What happens automatically

- Selectable PDF text is extracted locally. Image-only pages use installed Windows OCR; no external AI service or portal connection is used.
- The importer creates `output/portal-reviews/<run>/portal-review.json`, page previews, original copies and an import log. The console prints the JSON location.
- The HTML includes expandable captures, source excerpts with page numbers, full extracted text, page previews and original PDF downloads. The workbook contains **Portal Review** and **PDF Extracted Text** tabs.
- Source hashes deduplicate identical PDFs. Rebuilding the saved package restores its JSON and assets without repeating OCR or needing the original reports folder.
- Unreadable or password-protected PDFs are skipped with a warning. If OCR is unavailable, readable previews and originals are still included, with an explicit text-extraction limitation.

This is automatic text extraction, not a human or AI interpretation of every chart. OCR can mix the reading order of dashboard cards and misread numbers. Extracted content does not pass controls, close actions or change readiness. Confirm values against the original pages; structured CSV/API evidence still drives measurements. The selected assessment tenant is recorded as context, not independently verified from the PDF. Creation metadata supplies the capture date when present; missing dates remain unknown, and a capture date does not refresh the underlying report.

Install dependencies once using `.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt`. PDF import uses PyMuPDF. Windows OCR requires an installed recognition language; the local PowerShell worker uses image APIs only and requires no tenant permissions. PDF limits are 50 files per import, 20 pages per file, 50 MiB per original, and 100 MiB total reviewed assets. Structured data exports are read from the top level of each reports folder; PDF discovery also includes subfolders.

The HTML embeds the full original PDFs and previews. Use customer-appropriate material and review it before sharing; text extraction does not redact content.

## Optional human-reviewed manifest

`--portal-review PATH` remains available when you want to supply curated notes, confirmed dates and manually prepared previews using the schema below. A PDF folder is also accepted for compatibility, but `--reports-dir` is the normal workflow.

### Prepare a human review

Start with the live assessment and its [automatic Copilot coverage](COPILOT_AUTOMATIC_COLLECTION.md).
Request captures only for relevant details it cannot collect or for visual context the customer
wants. Paid prompt aggregates, subscription seats and returned Copilot DLP details do not require PDFs.

1. Keep the original PDFs in a customer-specific review folder. Export the pages needed for the report as PNG or JPEG previews.
2. Review each capture and record what is visible, its limits, the capture date, and any report date actually displayed. A capture date does not refresh the underlying report.
3. Remove credentials and user-level activity details from the material selected for the customer HTML. The referenced images and original PDFs are embedded in that HTML; their contents are visible to anyone who receives it.
4. Write `portal-review.json` using the schema below. All asset paths are relative to the manifest and must stay inside its folder. Calculate each original PDF's SHA-256 hash using `Get-FileHash -Algorithm SHA256`.

```powershell
Get-FileHash -Algorithm SHA256 ".\output\customer\review\originals\usage.pdf"

.\.venv\Scripts\python.exe main.py --mode offline `
  --collection-input "<collection-input>" `
  --portal-review ".\output\customer\review\portal-review.json"
```

Replace `<collection-input>` with the existing collection or rebuild recipe. If no saved assessment exists, omit `--collection-input`; the review remains context alongside the explicit unassessed controls. A live assessment may also include `--portal-review`.

## Manifest schema

Replace the example tenant GUID, filenames, dates, and hash with the reviewed source values. The hash below is a placeholder.

```json
{
  "schema_version": 1,
  "tenant_id": "11111111-1111-4111-8111-111111111111",
  "captures": [
    {
      "id": "copilot-usage-overview",
      "title": "Copilot usage overview",
      "domain_id": "adoption",
      "captured_at": "2026-09-14",
      "report_date": "2026-09-12",
      "source_file": "originals/usage.pdf",
      "source_sha256": "REPLACE_WITH_THE_64_CHARACTER_PDF_SHA256_HASH",
      "previews": ["previews/usage-page-1.png"],
      "summary": "The reviewed page displays an aggregate usage overview.",
      "limitations": ["The visible page does not establish policy enforcement or complete tenant coverage."],
      "review_notes": ["The reviewer compared the visible reporting period with the saved export."],
      "coverage": [
        {
          "area": "Copilot adoption",
          "assessment_coverage": "Visual context for the displayed population and period.",
          "next_step": "Retain the structured usage export for reproducible metric assessment."
        }
      ]
    }
  ]
}
```

Required top-level fields are `schema_version`, `tenant_id`, and `captures`. Every capture field shown is required except `report_date`. Omit `report_date` when it is not visible. Capture IDs must be unique. Supported domain IDs are `identity`, `content`, `data_protection`, `applications`, `endpoints`, `licensing`, `adoption`, `agents`, and `external_ai`.

Dates use `YYYY-MM-DD` or an ISO timestamp with a timezone. Capture dates cannot be in the future, and a report date cannot follow its capture date. The importer validates the tenant GUID, PDF signature and hash, image format, and local path boundaries. Unknown fields and unavailable assets produce an actionable error. Only PNG/JPEG previews and PDF originals are accepted; limits are 50 MiB per asset, 100 MiB total, and 40 million pixels per image.

## Output and replay

### When to refresh the PDFs

- Rebuilding the same saved assessment does not require new PDF exports. The package restores the reviewed captures and their original dates.
- A live run refreshes supported structured sources, such as Copilot usage and policy configuration. It does not refresh the supplied PDFs, reproduce every Copilot admin-center card, or collect the full Optimize checklist.
- Export and review new captures only for remaining relevant portal details or when you want the customer report to show the current portal state. Configuration changes or a new reporting period can warrant refreshed captures; PDF captures are optional supporting context.
- A new live collection does not automatically inherit a previous collection's captures. Include their folder with `--reports-dir`, or supply a reviewed JSON using `--portal-review`; reused captures retain their original dates.

The customer HTML includes reviewed notes, previews, and original PDF download data. The workbook's **Portal Review** tab contains source names, hashes, dates, qualifications, and review coverage without image bytes. Review content remains separate from the **Rollout Progress** and **Readiness Reviews** tabs that explain assessment requirements and dated control reviews.

The portable package retains the manifest and every referenced asset with its relative layout and hashes. Later offline builds restore `--portal-review` from the saved recipe, so the original source folder is unnecessary. Add a revised manifest using `--portal-review` rather than editing immutable packaged originals.

Older packages may contain a reference-only PDF notice without the PDF itself. Supply the original folder once with `--reports-dir` to add those captures automatically. Use the **COLLECTION INPUT** path printed after that successful build for later replay.

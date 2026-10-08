# Supported outputs

The supported assessment deliverables cover executive and domain assessment reports,
the readiness roadmap, customer action plan, executive presentation, technical evidence
workbook, HTML Evidence Portal, and the shared machine-readable assessment result.
This retirement does not add new renderers for those deliverable categories.

A normal build continues to produce the HTML assessment and readiness summary plus
assessment and technical evidence workbooks. Use `--extra-exports evidence-pages` for
linked HTML evidence pages. Keep companion files together so local links resolve.

## Shared-result snapshots

`--snapshot-json PATH` now serializes the existing shared assessment result directly.
It includes recommendations, actions, domains, controls, evidence qualifications and
counts. It does not migrate to the future schema in `assessment-templates/`.
Snapshots also retain the additive `identity` registers and structured
`identity_validation` diagnostics. Blocking semantic errors prevent publication;
unresolved legacy references remain visible non-blocking diagnostics. See
[Persistent identity](PERSISTENT_IDENTITY.md) for execution persistence, typed
aliases, legacy snapshot loading and replay behavior.
The snapshot is not a replayable collection: retain the portable assessment folder,
its collection or rebuild recipe, supplemental inputs and integrity metadata for replay.

## Retirement compatibility

Standalone dashboard JSON folders, indexes and ZIP archives, and App Builder exports,
have been retired. Their `--extra-exports` choices are rejected. The snapshot no longer
contains the former projection wrapper or its view-model fields. Consumers needing
native records should use the retained evidence workbook or collection package.

Microsoft Copilot Dashboard CSV imports remain supported input evidence through
`--copilot-dashboard-export`. References to Microsoft admin-center dashboards and
PDF dashboard reading order describe supplied evidence, not an assessment output.

## Retained shared capabilities

`Core/evidence_selection.py` selects retained source and detail occurrences for Excel
and HTML. `Core/evidence_records.py` contains the existing finding-record adapters.
`Core/assessment_serialization.py` writes the shared-result snapshot and normalizes
JSON-compatible values while excluding sensitive fields.

Existing `SRC-`, `EVD-`, `DET-` and source-row identifiers remain compatibility locators
for evidence traceability. Their existing positional algorithms are preserved; they
are not new persistent finding identities. The retired `FND-` finding UID is removed.
Persistent identity and [scope-aware reconciliation](SCOPE_RECONCILIATION.md) are
additive shared-result metadata. Renderer consolidation and storage streaming
remain separate work. Current evaluation meaning follows METHODOLOGY.md.

Explicit assessment runs add shared run context, completed semantic snapshots,
portable history and comparison eligibility. [Assessment deltas](ASSESSMENT_DELTAS.md)
add shared lifecycle records and qualified reassessment sections to retained outputs.
See [Assessment runs](ASSESSMENT_RUNS.md) for CLI, package replay and compatibility.

## Reference audit

These are all remaining runtime and documentation occurrences of the retired term,
including retained input names. Test references assert retirement or exercise those inputs.
There are no output-writer imports, retired output choices, output environment settings
or CI jobs for the removed subsystem. Line numbers refer to this revision.

| File | Lines | Retained purpose |
|---|---|---|
| `main.py` | 65, 104 | Microsoft/Viva CSV input, supplemental metrics, input routing or saved-input compatibility. |
| `README.md` | 172 | Microsoft/Viva CSV input, supplemental metrics, input routing or saved-input compatibility. |
| `Core/ai_usage.py` | 719, 720, 724, 725, 731, 736, 892, 921 | Microsoft/Viva CSV input, supplemental metrics, input routing or saved-input compatibility. |
| `Core/assessment_package.py` | 21 | Microsoft/Viva CSV input, supplemental metrics, input routing or saved-input compatibility. |
| `Core/auth_plan.py` | 342 | Microsoft/Viva CSV input, supplemental metrics, input routing or saved-input compatibility. |
| `Core/cli_parser.py` | 36, 157, 161 | Microsoft/Viva CSV input, supplemental metrics, input routing or saved-input compatibility. |
| `Core/copilot_admin_review.py` | 178 | Supplied Microsoft admin-center/PDF context and limits on interpreting its metrics. |
| `Core/customer_report.py` | 625, 688 | Supplied Microsoft admin-center/PDF context and limits on interpreting its metrics. |
| `Core/evidence_layer.py` | 610, 2915, 2917, 2920, 2924 | Microsoft/Viva CSV input, supplemental metrics, input routing or saved-input compatibility. |
| `Core/get_m365_client.py` | 32, 103, 105, 106, 184, 558, 680, 793 | Microsoft/Viva CSV input, supplemental metrics, input routing or saved-input compatibility. |
| `Core/offline_collection.py` | 240 | Microsoft/Viva CSV input, supplemental metrics, input routing or saved-input compatibility. |
| `Core/offline_report.py` | 12, 134 | Microsoft/Viva CSV input, supplemental metrics, input routing or saved-input compatibility. |
| `Core/orchestrator.py` | 168, 420, 528, 591 | Microsoft/Viva CSV input, supplemental metrics, input routing or saved-input compatibility. |
| `Core/orchestrator_pipelines.py` | 58, 148 | Microsoft/Viva CSV input, supplemental metrics, input routing or saved-input compatibility. |
| `Core/pdf_report_import.py` | 99 | Supplied Microsoft admin-center/PDF context and limits on interpreting its metrics. |
| `Core/portal_insights.py` | 4, 12 | Supplied Microsoft admin-center/PDF context and limits on interpreting its metrics. |
| `Core/processor.py` | 145, 192, 213, 214, 217, 223, 226, 377, 465 | Microsoft/Viva CSV input, supplemental metrics, input routing or saved-input compatibility. |
| `docs/archive/ASSESSMENT_REDESIGN_VALIDATION.md` | 82 | Supplied Microsoft admin-center/PDF context and limits on interpreting its metrics. |
| `docs/NEW_TENANT_CHECKLIST.md` | 55 | Supplied Microsoft admin-center/PDF context and limits on interpreting its metrics. |
| `docs/OUTPUTS.md` | 22, 27, 28, 29 | Migration notice and distinction between supported input evidence and retired output. |
| `docs/PORTAL_REPORTS_AND_OFFLINE.md` | 125, 140, 172 | Microsoft/Viva CSV input, supplemental metrics, input routing or saved-input compatibility. |
| `docs/PORTAL_REVIEW.md` | 26 | Supplied Microsoft admin-center/PDF context and limits on interpreting its metrics. |
| `docs/RUN.md` | 376 | Microsoft/Viva CSV input, supplemental metrics, input routing or saved-input compatibility. |

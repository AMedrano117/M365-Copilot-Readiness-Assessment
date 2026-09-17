# Prompt for the coordinated implementation

> Historical redesign record, retained for context. Its instructions, paths and validation counts describe the work at that time. Use the [current documentation](../README.md) for operating the assessment.

Use the following as the next implementation request in this repository. The associated scope and acceptance criteria are in `ASSESSMENT_REDESIGN_PLAN.md`.

```text
Treat this as one coordinated implementation effort for the Microsoft 365 Copilot Readiness Assessment repository. Read ASSESSMENT_REDESIGN_PLAN.md and use it as the working specification.

Goal: Deliver a cohesive Microsoft 365 Copilot readiness report that business leaders and IT owners can understand and act on, backed by traceable evidence and a repeatable live-collection-plus-offline-reporting workflow.

The audience and scope are settled: business leaders and IT owners first; Microsoft 365 Copilot first; agents and external AI only when explicitly in scope. The customer HTML and technical workbook must serve those different reading needs while using one assessment result.

The current combined report is a working preview, not the desired final design. It contains disconnected historical/current sections, mechanical prose, contradictory usage availability, missing inventory rendered as zero, and inconsistent treatment of cached risks and strengths. Fix the underlying evidence and decision model before polishing the presentation. Do not solve this by adding more independent sections or hiding valid findings.

Start by inspecting the repository, existing uncommitted changes, current tests, generated reports, prior assessment workbooks, the explicit Purview cache, and supplied portal-export samples. Preserve the existing work. Produce a concise milestone plan tied to the written acceptance criteria, then carry the implementation through validation and regenerated deliverables. Do not stop after the plan or after isolated cosmetic edits.

Implement the following as a coherent whole:

1. A shared evidence and assessment model for live data, saved collections, portal reports, and legacy evidence. Preserve tenant identity, original dates, scope, units, reporting windows, source lineage, confidence, completeness and methodology. Reconcile comparable observations, duplicates and conflicts. Keep missing/unknown separate from measured zero. Treat cached strengths and risks consistently.

2. One customer narrative: executive assessment, prioritized action plan, consistent domain findings, conditions for the next rollout stage, and the remaining evidence or decisions. Integrate historical observations into their relevant topics with their qualification; retain the complete original evidence in the workbook. Avoid a second historical report competing with an empty current report. Use plain, professional language that explains what we found, why it matters, and what the customer should do.

3. One canonical operator workflow using --mode live and --mode offline: validate access, collect and save tenant evidence, add completed portal exports, rebuild offline, and review the result. Preserve the implemented default: every live assessment automatically saves a unique collection under output/collections/ and prints its path plus an offline rebuild command; --save-collection PATH is only a custom destination override. Preflight does not save, and offline mode does not save or overwrite a collection. Extend that behavior into a portable assessment package preserving required source files, settings, dates and schema/methodology versions. Today original portal exports and relevant options must still be resupplied; removing that burden is part of this planned work. Operators should not need to know hidden cache behavior or resupply forgotten inputs. Keep legacy report/cache import as a supported fallback. Enforce the same tenant identity checks in both modes.

4. One consistent decision and count model across the summary, domain sections, action register, charts and workbook. A complete report may conclude that readiness is unconfirmed because evidence is incomplete. Offline execution alone must not determine readiness. Do not infer licensing, permission effectiveness, sensitive-data exposure, or actual Copilot usage from unrelated signals.

5. Updated operator instructions, report-request guidance, sample compatibility matrix and methodology. Check current Microsoft portal, role and licensing guidance against official sources. Clearly distinguish existing validated export schemas from unsupported variants still needing samples.

Use meaningful regression and integration tests. Prove that equal evidence, settings, methodology and evaluation date produce equivalent live/offline conclusions; that a copied package can rebuild without original source paths; and that offline execution performs no authentication, tenant calls, prompts or administrative PowerShell. Cover historical evidence, missing/zero values, dates, duplicates, conflicts, empty files, user-detail privacy and tenant mismatches.

Regenerate the report from the existing local tenant workbook, Purview cache and portal exports. Also exercise a complete synthetic collection and incomplete/edge cases. Review the rendered HTML visually and editorially, verify workbook evidence links, and correct contradictions or mechanical wording before finishing. Passing tests alone is not sufficient.

Do not perform a new live tenant collection, initiate Microsoft scans, modify tenant settings, send messages, purchase licenses or add an external runtime AI service. Do not commit customer evidence or credentials. Use invented/sanitized data for tracked fixtures. Preserve supported commands and existing uncommitted changes.

Use parallel review where it helps, with one shared scope and one final integration review. Proceed autonomously through routine implementation choices. Ask only about a material ambiguity or a necessary action outside this scope, and continue independent work while waiting.

Finish with the generated HTML/workbook links, the canonical operator commands, validation results, and any genuine remaining evidence limitations. Evaluate completion against ASSESSMENT_REDESIGN_PLAN.md rather than the number of changes made.
```

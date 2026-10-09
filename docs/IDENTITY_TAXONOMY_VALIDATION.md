# 1. Implementation summary

A versioned shared identity taxonomy separates condition titles from capability, licensing context and source Feature. The correction is additive: technical evaluation, PFI allocation, reconciliation, counts, lifecycle and governance run before title projection. No global string replacement or canonical-contract migration is performed.

# 2. Git baseline

- Repository remote: `github`, [AMedrano117/M365-Copilot-Readiness-Assessment](https://github.com/AMedrano117/M365-Copilot-Readiness-Assessment); Azure origin untouched.
- Pass B [PR 13](https://github.com/AMedrano117/M365-Copilot-Readiness-Assessment/pull/13) merged; all six PR/push checks succeeded.
- Pass B merge and fetched main: `4a9e26af653712ba5f9659d460058111f8f9351a`.
- Post-merge [run 37950441815](https://github.com/AMedrano117/M365-Copilot-Readiness-Assessment/actions/runs/37950441815) succeeded on Python 3.10, 3.12 and 3.14.
- Starting branch `fix/entra-evidence-client`, working tree clean. New branch `fix/identity-finding-taxonomy` starts at exact merged main.
- Pass A, Pass B, persistent identity, reconciliation, runs, lifecycle and governance were present; retired output components absent before implementation.
- The separate live Entra adapter fix, [PR 14](https://github.com/AMedrano117/M365-Copilot-Readiness-Assessment/pull/14), remained open and is not included.

# 3. Reproduced projection defects

Read-only engineering-workbook inspection confirmed generic P1/P2 subjects in Recommendations and Evidence Index for licensing, Conditional Access, MFA registration, passwordless registration, privilege and access reviews. Findings Lineage independently renamed only some of these; Investigation Items retained generic licensing labels. Authentication Coverage, Conditional Access Detail, Identity Risk Detail and Application Consent Policy used generic license-oriented Flagged By values. Admin Role Detail had empty Flagged By values. Source workbook SHA-256 was identical before and after review.

The distinct causes were `evidence_layer._link_sheet_rows_to_recommendations` choosing Feature, `investigation_details.prepare_investigation_details` preferring OriginalFeature, and `customer_report._heading` using renderer-specific interpretation. `export_recommendations.export_to_excel` copied Feature into multiple registers. These paths now consume the shared title.

# 4. Taxonomy model

`identity_finding_taxonomy.resolve_identity_finding` uses explicit condition, scoped type, stable finding key, narrow control/scoped assignment context, then unique legacy signatures. It records version 1.0.0, authority, condition, naming type/evidence layer, capability, original label and diagnostics. Observed condition, required outcome, period, scope, limitation, action and state fields are copied only when already declared.

`project_identity_result` copies finding projections and summary containers without duplicating large evidence graphs. `validate_identity_taxonomy` returns structured errors/warnings for malformed containers, version/title/type/capability/layer inconsistency, unqualified ambiguity and missing documented dependencies. `assessment_references.require_valid_assessment` blocks invalid structured publication. Legacy ambiguity is not silently repaired.

# 5. Identity condition mappings

The complete versioned registry and stable-key mapping are in [Identity finding taxonomy](IDENTITY_FINDING_TAXONOMY.md).

| Evidence subject | Condition title or family |
|---|---|
| Actual P1/P2/MFA service-plan inventory | Named license availability |
| MFA registration | Coverage, explicitly false, unknown, conflicting |
| Authentication methods | Passwordless, phishing-resistant, phone, phone-only, system-preferred |
| MFA enforcement | Coverage, tenant/admin not established, report-only |
| Conditional Access | Policy coverage, exclusions, report-only, event results |
| Legacy authentication | Aggregate review, successful, CA-blocked, other failure, unknown, blocking coverage |
| SSPR | Registration, enablement, capability |
| Privileged identity | Scope/purpose, permanent, disabled, unregistered MFA, unknown/inactive activity, unresolved/eligible, emergency/service purpose and observed activity |
| Access reviews | Coverage, definitions, privileged review not established |
| Identity risk | Risky users, detections, disposition not established, risk policy |
| Guests | Lifecycle, stale candidates, administrator access, invitation/cross-tenant policy |
| Application consent | Policy, high-privilege grants, publisher, owner, grant inventory |

No titles depend on customer names, workbook positions or display IDs. The actual reproduced generic workbook rows mapped uniquely through evidence signatures.

# 6. Licensing representation

`_license_metadata` separates `AssociatedLicenseFeatures` from `LicensingDependencies`. P1/P2 source association does not establish a universal prerequisite. Dependencies use existing guidance and qualified documented capability routes; access-review scenarios need confirmation. Privileged review does not automatically require PIM or P2.

Only retained successful relevant service-plan inventory supports LicenseObserved. Missing/unknown evidence remains unknown, not absent or unlicensed. Inventory does not establish user entitlement, configuration, enforcement or protection. Original EvidenceLevel remains intact; ConditionEvidenceLevel describes the naming category. Official licensing sources are linked in the taxonomy document.

# 7. Flagged By compatibility

`project_identity_sheet_links` retains Flagged By as a list of linked condition titles and adds Capability, Licensing Dependency, Associated License Feature and qualified Source Signal. All 14 identity detail families are covered, including authentication/preferences/populations, roles, privilege, risk, guests, consent, reviews and legacy sign-ins. Explicitly false MFA detail adds a record-specific condition title.

Existing record selectors, RecommendationId and native fields remain authoritative. Sheet-wide context does not assert every row is affected. Duplicate self-aliases are deduplicated; ambiguous aliases do not fan out. Service Plan Inventory and raw/native evidence are not renamed.

# 8. Persistent identity and lifecycle behavior

`assessment_identity.attach_identity`, `finding_reconciliation.group_findings` and delta/governance logic are unchanged. Projection leaves Feature and semantic keys intact. Actual delta comparison verifies title-only projection produces Unchanged and leaves baseline, identity and governance unchanged. Distinct condition keys remain distinct even when the old license label was shared.

No decisions are created, approved, activated, revoked or reopened by taxonomy/rendering. ResolvedByCurrentEvidence remains separate from ClosedByRemediation. Existing default-deny and conditional-closure safeguards pass their regression suites.

# 9. Legacy compatibility

Unique legacy signatures produce clearer titles while retaining OriginalCompatibilityLabel and version. Ambiguous labels retain their original title and structured warning. The broad suite exposed a historical-label regression and malformed-snapshot coercion; both were corrected with focused tests and existing assertions preserved.

`assessment_serialization.read_assessment_result` enriches runtime copies without writing source files. `prior_report_import._recommendations` recognizes new workbook metadata and restores Compatibility Feature before legacy fingerprint calculation, retaining the displayed title and licensing context. Original worksheet records and bytes remain unchanged. Historical generic targets are not split or assigned to multiple current findings.

# 10. Output impact

Shared titles now feed Recommendations, Evidence Index, Findings Lineage, Investigation Items, Technical Recommendations, identity detail, executive/domain summaries, HTML evidence models, readiness summary, snapshot JSON and replay. Packages retain snapshot bytes and existing integrity processing. Baseline control wording remains visible under its condition title.

Excel Recommendations retains original column positions, projects FindingTitle into Feature and appends compatibility, capability, condition, licensing, evidence-state and version columns. Nonidentity recommendation labels retain prior behavior. Links, locators, restricted identity detail and technical counts remain intact.

# 11. Files changed

- New authority: `Core/identity_finding_taxonomy.py`.
- Shared result/gates/snapshots: `Core/assessment_result.py`, `Core/assessment_references.py`, `Core/assessment_serialization.py`.
- Outputs/evidence projection: `Core/customer_report.py`, `Core/pilot_summary.py`, `Core/export_recommendations.py`, `Core/evidence_layer.py`, `Core/investigation_details.py`, `Core/workbook_evidence.py`.
- Workbook import compatibility: `Core/prior_report_import.py`.
- Fictional tests: `tests/test_identity_taxonomy.py`, `tests/test_identity_taxonomy_outputs.py`.
- Documentation: `docs/IDENTITY_FINDING_TAXONOMY.md`, `docs/OUTPUTS.md`, this report.

# 12. Tests and actual results

Python 3.12 with existing .assessment-deps, no dependency installation. The guard runner is unchanged: tenant sockets/subprocesses and repository/customer writes are blocked; fictional generated artifacts stay under an isolated OS-temp root. Every table row is separately invoked; overlapping totals must not be added. Counts are unittest methods, with matrix cases inside methods.

| Category | Passed | Failed | Errors | Skipped | Excluded | Guard blocks |
|---|---:|---:|---:|---:|---:|---:|
| 1. New taxonomy | 42 | 0 | 0 | 0 | 0 | 0 |
| 2. Licensing versus condition | 4 | 0 | 0 | 0 | 0 | 0 |
| 3. MFA taxonomy | 2 | 0 | 0 | 0 | 0 | 0 |
| 4. Conditional Access taxonomy | 3 | 0 | 0 | 0 | 0 | 0 |
| 5. Privileged taxonomy | 2 | 0 | 0 | 0 | 0 | 0 |
| 6. Risk, guest, consent | 1 | 0 | 0 | 0 | 0 | 0 |
| 7. Projection | 10 | 0 | 0 | 0 | 0 | 0 |
| 8. Legacy compatibility | 4 | 0 | 0 | 0 | 0 | 0 |
| 9. Lifecycle stability | 5 | 0 | 0 | 0 | 0 | 0 |
| 10. Pass B | 87 | 0 | 0 | 0 | 0 | 0 |
| 11. Pass A | 80 | 0 | 0 | 0 | 0 | 0 |
| 12. Closure override | 19 | 0 | 0 | 0 | 0 | 0 |
| 13. Governance | 61 | 0 | 0 | 0 | 0 | 0 |
| 14. Delta state | 49 | 0 | 0 | 0 | 0 | 0 |
| 15. Run workflow | 37 | 0 | 0 | 0 | 0 | 0 |
| 16. Reconciliation | 41 | 0 | 0 | 0 | 0 | 0 |
| 17. Persistent identity | 62 | 0 | 0 | 0 | 0 | 0 |
| 18. Stage 1 | 35 | 0 | 0 | 0 | 0 | 0 |
| 19. Dashboard retirement | 8 | 0 | 0 | 0 | 0 | 0 |
| 20. Serialization/replay | 30 | 0 | 0 | 0 | 0 | 0 |
| 21. Package integrity | 44 | 0 | 0 | 0 | 0 | 0 |
| 22. Excel | 36 | 0 | 0 | 0 | 0 | 0 |
| 23. HTML | 22 | 0 | 0 | 0 | 0 | 0 |
| 24. Guarded regression | 79 | 0 | 0 | 0 | 0 | 0 |
| 25. Final broader safe suite | 1426 | 0 | 0 | 0 | 102 | 0 |
| 26. Skipped tests | — | — | — | 0 across completed runs | — | — |
| 27. Excluded tests | — | — | — | — | 102 in safe discovery only | — |
| 28. Guard-blocked operations | — | — | — | — | — | 0 across completed runs |

Exact commands for categories 1–25, in table order:

```powershell
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_identity_taxonomy test_identity_taxonomy_outputs
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_identity_taxonomy.LicensingConditionTests
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_identity_taxonomy.MFATaxonomyTests
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_identity_taxonomy.ConditionalAccessTaxonomyTests
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_identity_taxonomy.PrivilegedTaxonomyTests
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_identity_taxonomy.RiskGuestConsentTaxonomyTests
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_identity_taxonomy_outputs.TaxonomyProjectionTests
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_identity_taxonomy.LegacyTaxonomyTests
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_identity_taxonomy.TaxonomyLifecycleTests test_identity_taxonomy_outputs.TaxonomyRealLifecycleTests
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_privileged_identity test_privileged_boundaries test_privileged_outputs test_privileged_lifecycle
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_authentication_corrections test_authentication_methods test_signin_evidence test_legacy_signin_evidence test_entra_collection_failures
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_closure_override_validation
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_governance_decisions test_governance_semantics test_governance_outputs
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_delta_lifecycle test_delta_metrics test_delta_validation test_delta_outputs
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_assessment_run_workflow test_assessment_run_execution test_assessment_run_outputs
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_scope_reconciliation test_safe_finding_deduplication test_reconciliation_outputs_and_validation
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_persistent_identity
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_stage1_semantics
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_dashboard_retirement
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_persistent_identity.IdentitySerializationTests test_offline_report test_privileged_outputs.PrivilegedOutputTests.test_snapshot_and_offline_replay_preserve_normalized_identity
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_assessment_package test_assessment_integrity
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_export_recommendations test_workbook_evidence test_workbook_layout test_investigation_workbook_audit test_privileged_outputs.PrivilegedOutputTests.test_excel_keeps_scopes_identities_and_qualified_activity
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_customer_report_guidance test_customer_progress_render test_html_evidence_pages test_report_readability test_privileged_outputs.PrivilegedOutputTests.test_html_and_readiness_are_identity_free_and_do_not_recalculate
python -B tests/run_guarded_stage1.py --allow-temp --quiet --regression
python -B tests/run_guarded_stage1.py --allow-temp --quiet --safe-discover
```

Additional checks: `python -B tools/check_docs.py` (215 links, zero errors before this report), `powershell -NoProfile -ExecutionPolicy Bypass -File tests/Test-PowerShellSyntax.ps1` (11 scripts pass), `git diff github/main --check` (pass).

Safe discovery excludes 102 existing methods in subprocess-dependent setup/cleanup, SharePoint probe/export, optional import, certificate subprocess and retained delegated-cache categories. These are excluded, not passed or skipped. The exclusion lists and guard runner were unchanged.

Initial new-module tests were red before the authority existed. Intermediate focused checks exposed naming-layer/type alignment, fixture provenance/call requirements and duplicate self-alias handling. These were corrected in new code/fixtures; no existing assertions were weakened.

Initial broad invocation: 1423 run, 1420 passed, three failed test methods and one errored test method (failure/error sets overlap on malformed-input subcases), zero skipped/guard blocks, 102 excluded. Product regressions were lost historical title, coercion of malformed recommendations, and lost baseline-control wording. Focused existing regressions passed after corrections. A subsequent broad run passed 1425; the final source/test revision adds workbook import compatibility and passes 1426 in 448.550 seconds. Earlier runs are not added to final totals.

# 13. Compatibility impact

Recommendations Feature is now a display title in Excel, with Compatibility Feature appended for consumers needing original source input. Direct runtime Feature is unchanged by taxonomy; the workbook import adapter restores its compatibility input. Consumers should adopt FindingTitle and dedicated licensing columns. No source API, CLI option, evidence classification, threshold, closure rule, PFI algorithm, package integrity rule or history storage format was replaced.

Retired dashboard/App Builder output modules, packages, indexes, wrappers, ZIPs, standalone JSON, CLI choices and finding_uid remain absent. Existing Microsoft dashboard CSV input is still supported input evidence.

# 14. Remaining canonical-contract implications

Assessment templates and schemas/assessment-results.schema.json remain the target contract. This additive shared-result stage does not claim full schema migration or create new renderers. Later migration must represent condition, required outcome, capability, dependency, licensing evidence, collection/evidence layer, customer action and compatibility references separately.

No methodology threshold or automated outcome is introduced. Applicable licensing and ambiguous historical mappings still require confirmation. A current condition cannot manufacture baseline continuity or broaden a governance decision. The open Entra adapter correction must be considered separately before a controlled live engineering rerun.

# 15. GitHub result

Branch fix/identity-finding-taxonomy is pushed to GitHub. [PR 15: Separate identity findings from Entra licensing labels](https://github.com/AMedrano117/M365-Copilot-Readiness-Assessment/pull/15) is open against main and is not merged. Source/test commits: 5362eb6, da54318, 1ceb821, dfc2281; validation report commit: a6be239, followed by this publication-state update. Current PR CI is pending; the six prerequisite Pass B checks and post-merge CI passed.

# 16. Completion status

Implementation, all required local validation categories, branch publication and PR creation are complete. The PR remains unmerged pending CI and independent review. No tenant collection occurred, no customer evidence was modified, supplied workbook hash matches the original, and no generated customer artifacts or secrets were committed. Next: independent review, then an authorized merge and controlled engineering rerun before canonical-contract migration. The separate live adapter fix in PR 14 remains open and excluded from this branch.


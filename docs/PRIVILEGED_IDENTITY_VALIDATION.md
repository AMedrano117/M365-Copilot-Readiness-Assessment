# Pass B validation record

Implementation source commit: `718f8c112af5a0b1a9f75743730029b649b2b079`. Python 3.12, existing `.assessment-deps`; no dependency installation or tenant collection.

## Baseline and workflow

- GitHub remote: `github`, `AMedrano117/M365-Copilot-Readiness-Assessment`; Azure origin untouched.
- PR [12](https://github.com/AMedrano117/M365-Copilot-Readiness-Assessment/pull/12) merged; all six push/PR checks passed.
- Merged base and fetched main: `35893f8ff5aad1af577aa5878049bd70cdce0a33`. Post-merge [run 37934225258](https://github.com/AMedrano117/M365-Copilot-Readiness-Assessment/actions/runs/37934225258) passed all three Python jobs.
- Initial branch `refactor/authentication-semantics`, working tree clean; new branch `refactor/privileged-identity-activity` created from the exact merged base.
- Pass A rule 1.0.0, envelope validation, separate registration/enforcement/authentication, persistent identity, reconciliation, runs, lifecycle and governance verified before implementation. Retired dashboard/App Builder modules remained absent.
- Test-first commit `850065b` initially reproduced 38 missing-module errors. Implementation commit `76f1080` and provenance correction `718f8c1` implement the shared model. Existing assertions were retained; three older role fixtures gained explicit status/start/end/evaluation timestamps.

## Separately run validation categories

Each row is an independent invocation; totals overlap and must not be added. Counts below are unittest methods, including any matrix subtests within a method. Every listed final run has zero failures, errors, skips and guard blocks. The [guard runner](../tests/run_guarded_stage1.py) was not modified.

| Category | Passed | Failed/errors | Skipped | Excluded |
|---|---:|---:|---:|---:|
| 1. Schedule classification | 6 | 0 | 0 | 0 |
| 2. Assignment identity | 10 | 0 | 0 | 0 |
| 3. Privileged population | 12 | 0 | 0 | 0 |
| 4. Group-mediated privilege | 7 | 0 | 0 | 0 |
| 5. Activity and inactivity | 11 | 0 | 0 | 0 |
| 6. Special-purpose accounts | 11 | 0 | 0 | 0 |
| 7. Combined risk and authentication correlation | 16 | 0 | 0 | 0 |
| 8. New outputs | 9 | 0 | 0 | 0 |
| 9. Pass A and collection envelopes | 80 | 0 | 0 | 0 |
| 10. Closure overrides | 19 | 0 | 0 | 0 |
| 11. Governance | 67 | 0 | 0 | 0 |
| 12. Delta state | 49 | 0 | 0 | 0 |
| 13. Run workflow | 37 | 0 | 0 | 0 |
| 14. Reconciliation | 41 | 0 | 0 | 0 |
| 15. Persistent identity | 62 | 0 | 0 | 0 |
| 16. Stage 1 | 35 | 0 | 0 | 0 |
| 17. Dashboard retirement | 8 | 0 | 0 | 0 |
| 18. Serialization and replay | 30 | 0 | 0 | 0 |
| 19. Package integrity | 44 | 0 | 0 | 0 |
| 20. Excel | 36 | 0 | 0 | 0 |
| 21. HTML | 22 | 0 | 0 | 0 |
| 22. Guarded regression | 79 | 0 | 0 | 0 |
| 23. Final broader safe suite | 1,384 | 0 | 0 | 102 |
| 24. Skipped tests | — | — | 0 in completed runs | — |
| 25. Excluded tests | — | — | — | 102 in safe discovery only |
| 26. Guard-blocked operations | — | 0 in completed runs | — | — |

Exact commands for categories 1–22:

```powershell
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_privileged_identity.AssignmentTemporalTests test_privileged_boundaries.AssignmentBoundaryTests.test_timezone_equivalence_end_exclusive_and_invalid_expiration
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_privileged_identity.AssignmentIdentityTests test_privileged_boundaries.AssignmentBoundaryTests
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_privileged_identity.PrivilegedPopulationTests test_privileged_boundaries.PopulationBoundaryTests
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_privileged_identity.GroupPrivilegeTests test_privileged_boundaries.GroupBoundaryTests
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_privileged_identity.PrivilegedActivityTests test_privileged_boundaries.ActivityBoundaryTests
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_privileged_identity.SpecialPurposeTests test_privileged_boundaries.PurposeBoundaryTests
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_privileged_identity.CombinedRiskTests test_privileged_identity.PrivilegedAuthenticationTests test_privileged_boundaries.AuthenticationBoundaryTests test_privileged_identity.PrivilegedValidationTests test_privileged_boundaries.PublicationBoundaryTests
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_privileged_outputs
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_authentication_corrections test_authentication_methods test_signin_evidence test_legacy_signin_evidence test_entra_collection_failures
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_closure_override_validation
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_governance_decisions test_governance_semantics test_governance_outputs test_privileged_lifecycle
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
```

Category 23:

```powershell
python -B tests/run_guarded_stage1.py --allow-temp --quiet --safe-discover
```

Additional focused final check:

```powershell
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_privileged_identity test_privileged_boundaries test_privileged_lifecycle test_privileged_outputs
```

Actual: 87 run, 87 passed, 0 failures/errors/skips/guard blocks. The 148-test focused integration invocation added `test_shared_assessment_result test_evidence_selection`; all 148 passed. These overlap with other rows.

Strict, no-write check:

```powershell
python -B tests/run_guarded_stage1.py --strict --quiet test_privileged_identity test_privileged_boundaries
```

Actual: 72 run, 72 passed, 0 failures/errors/skips/guard blocks. The allow-temp mode permits writes only under one OS-temp root; it rejects tenant/network/subprocess activity and repository/customer writes. Mocked live workflows use fictional evidence and are not tenant access.

The 102 exclusions are the runner's existing subprocess and retained-cache prefixes, not additional Pass B exclusions. They are neither passed nor skipped tests. CI runs ordinary full discovery and the existing synthetic report auditor separately; local guarded totals do not claim those excluded operations were exercised.

Other checks:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tests/Test-PowerShellSyntax.ps1
python -B tools/check_docs.py
git diff --check
```

PowerShell parsing passed for 11 scripts. Direct script invocation encountered the local execution policy; the rerun changes only the child process policy. Documentation and whitespace checks are recorded after the final report update.

Final broader result at the recorded implementation source commit: 1,384 run,
1,384 passed, 0 failed tests/assertions, 0 errors, 0 skipped and 0 guard blocks.
Local documentation check passed 214 links with 0 errors; `git diff --check`
passed. GitHub feature-PR CI is a separate result and must be checked before merge.

## Intermediate failures and limitations

- Initial broad discovery: 1,343 run, 1,340 passed, two readiness assertion failures and one Windows temporary-directory rename error. Readiness regressions came from overwriting supported baseline assurances and creating current activity findings from historical/future grants; both were corrected without weakening assertions. The PDF replay test passed in the subsequent targeted and broad runs.
- A later separate delta run: 49 run, 48 passed, one WinError 5 while atomically replacing a temporary assessment-history file. An unchanged rerun of the exact category-12 command passed all 49. The OS locking condition remains an intermittent local risk, not a suppressed test or a product fix.
- Intermediate broader reruns passed 1,379, 1,382 and 1,383 tests respectively, each with zero failures/errors/skips/guard blocks. These were prior test/source revisions and are not added to the final totals.
- Native-source provenance correction was regression-tested: role-population completeness cannot promote incomplete, failed, undated or unproven authentication evidence. No invented file/API locator supplies missing provenance.
- No tenant access or collection occurred; no customer evidence was modified. Fictional generated artifacts stayed in OS temporary roots and are not staged. No retired dashboard export or finding_uid dependency was added.

## Acceptance scenario coverage

| Requested scenarios | Principal coverage |
|---|---|
| A: 1–15 temporal semantics | AssignmentTemporalTests, AssignmentBoundaryTests, output clock-isolation test |
| B: 16–25 scope/identity | AssignmentIdentityTests, AssignmentBoundaryTests, GroupBoundaryTests |
| C: 26–38 population | PrivilegedPopulationTests, PopulationBoundaryTests, snapshot/replay output test |
| D: 39–44 groups | GroupPrivilegeTests, GroupBoundaryTests |
| E: 45–56 authentication | PrivilegedAuthenticationTests, AuthenticationBoundaryTests, preserved Pass A tests |
| F: 57–72 activity | PrivilegedActivityTests, ActivityBoundaryTests, snapshot replay |
| G: 73–80 emergency | SpecialPurposeTests, PurposeBoundaryTests, qualified findings/output tests |
| H: 81–88 service/sync | SpecialPurposeTests, PurposeBoundaryTests, workload/authentication boundary tests |
| I: 89–100 combinations | CombinedRiskTests, AuthenticationBoundaryTests, PublicationBoundaryTests, output evidence-selection tests |
| J: 101–117 output | PrivilegedOutputTests, existing Excel/HTML/package/retirement tests |
| K: 118–124 lifecycle/governance | PrivilegedLifecycleTests, existing governance/delta/run tests |

Coverage is implemented through individual methods and matrix subtests, not 124 standalone methods. New tests total 87 methods across four modules.

## Decisions and next work

Model/source details and official Microsoft references are in [Privileged identity interpretation](PRIVILEGED_IDENTITY.md). No default inactivity threshold, new directional/resolution rule, automated remediation, beta dependency, governance event, baseline mutation or canonical-contract migration is introduced. Purpose inputs and expected CA mappings remain explicit. Source freshness retains the existing 35-day boundary; date-only evaluation uses end-of-UTC-day.

Absent/partial group rosters, activation windows, purpose attestations, workload activity and account-change evidence remain qualified gaps. Next: independent review of this exact implementation, then explicit methodology decisions and narrowly scoped collection additions. Preserve lifecycle/governance separation, original timestamp precision, native evidence, restricted identity details and aggregate executive outputs. The feature PR must not be merged automatically.


# Configuration grouping and remaining taxonomy corrections

# 1. Implementation summary

Repeated equivalent SharePoint configuration results are grouped in the shared presentation model. Native evidence, selected support, persistent identity and decision evaluation remain separate. Authentication metric labels and assignment finding titles now describe the assessed subject.

# 2. Git baseline

Remote: `github` (`AMedrano117/M365-Copilot-Readiness-Assessment`). Base: exact `main` commit `0005fd2d573db6838dffaded256365b535f65c69`. Feature branch: `fix/configuration-grouping-and-taxonomy`. Working tree was clean before implementation. Pass B PR #13 merged at `4a9e26af653712ba5f9659d460058111f8f9351a`; taxonomy PR #15 merged at `033c47e1496ba263f1daaa0e4d7915ccb80c5740`; adapter PR #14 merged at the current base. All six taxonomy PR checks and the prerequisite post-merge jobs passed. The prior local branch and its user commit were preserved; unrelated work was not brought into this branch.

# 3. Reproduced Configuration defect

Read-only engineering workbook inspection confirmed repeated visible SharePoint configuration results with distinct evidence records, assignment findings labeled P1/P2 availability, and Authentication Coverage rows labeled with combined parent conditions. Both engineering workbook SHA-256 hashes were recorded and verified unchanged. Customer identities, locators, counts and artifacts are omitted from this public report.

The duplication was in [Core/workbook_layout.py](../Core/workbook_layout.py), `split_workbook_layout`: native properties expanded to settings, then `_projection` omitted object identity and displayed one evidence identifier. The native collector records were valid.

# 4. Configuration grouping model

[Core/configuration_groups.py](../Core/configuration_groups.py), `attach_configuration_groups`, enrolls only SharePoint findings and the three retained SharePoint settings datasets. `build_configuration_groups` constructs object-setting observations; `group_observations` groups them.

Grouping includes finding ID, structured configuration key and display name, normalized observed/recommended values, assessment, object type, environment, provider, tenant/site scope, capture/window/historical period, collection state, qualification, conflict state and established setting origin. Meets, Change, Review and Conflict remain separate. SharePoint, OneDrive, established Teams/channel types and tenant observations remain separate. Unknown origin remains unknown; expiry override information is not used to invent default-link or sharing inheritance. Unverified list-query defaults and incomplete collections remain qualified Review observations.

No new sharing thresholds were introduced. The existing `_sharing_check` recommendations are reused centrally. Broader grouping of devices, policies, applications and role assignments requires separate semantic enrollment and tests.

# 5. Site-level evidence preservation

Every membership retains the object ID/name/locator, raw and normalized value, result, recommendation, configuration key, source capture/refs, original timestamps, collection qualification/conflicts, detail record ID, primary native evidence ID and all selected support IDs. Native registries and Sites & Sharing remain available.

A new technical Configuration Detail register is necessary: the existing wide native/Sites & Sharing/governance registers do not hold per-setting result, origin and group membership together. It adds those relationships without replacing the existing native registers. Authorized technical HTML membership pages are paginated. Customer HTML uses aggregate groups and excludes object locators and evidence IDs.

# 6. Group identity and count reconciliation

`CFG-` is a SHA-256-derived presentation identity over all grouping dimensions. It is independent of row order, coordinates and the first EVD ID. It replaces no PFI, finding, observation, recommendation or evidence identity.

Affected counts use distinct known objects. Total objects assessed is the distinct returned population for the same configuration/object type/environment/provider/scope/period, never an invented tenant inventory. Duplicate captures retain memberships but do not inflate counts. Unknown and contradictory objects are counted separately; unidentified records are explicitly counted, not assigned fabricated object IDs. Conflicting groups can overlap in objects, so their counts must not be summed as disjoint populations. `validate_configuration_groups` recomputes groups/membership/counts; reference validation and workbook export block mismatches.

# 7. ENT-008 correction

[Core/identity_finding_taxonomy.py](../Core/identity_finding_taxonomy.py), `_assignment_condition` and `_resolve`, map retained group-assignment conditions ahead of broad license context. Titles are Group-based license assignment coverage, or Copilot group-based license assignment coverage when the observation establishes that scope. Neither display ID nor recommendation text is the authority. Existing planning/operational disposition is preserved. Read-only verification confirmed the reproduced assignment observation resolves to the Copilot-specific title.

# 8. ENT-014 correction

The same resolver maps licensed-guest/product-assignment observations to Guest license assignment review. It remains separate from guest lifecycle, administrative access, content permission and Copilot entitlement. No exposure conclusion, removal requirement or confirmed entitlement is introduced. The reproduced observation was verified against the corrected resolver.

# 9. Authentication Coverage taxonomy

[Core/authentication_methods.py](../Core/authentication_methods.py), `summarize_registrations`, emits stable Metric ID values without changing calculations. [Core/authentication_metric_taxonomy.py](../Core/authentication_metric_taxonomy.py), `resolve_metric`, distinguishes population, explicit registration states, MFA capability, SSPR registration/enablement/capability, passwordless and phishing-resistant registration/capability, phone/recovery methods, preferences, system-preferred mode and classification gaps.

Known legacy metric labels are a compatibility fallback only when no ID exists. Unknown IDs remain classification gaps. `project_identity_sheet_links` assigns metric conditions to Flagged By and retains Parent Finding Context separately. Nonmetric legacy detail rows retain their linked finding context. Metric labels create no customer findings or governance targets.

# 10. Licensing-context normalization

[Core/licensing_projection.py](../Core/licensing_projection.py), `normalize_licensing`, keeps Capability, LicensingDependencies, AssociatedServicePlans, LicenseInventoryEvidenceState, LicenseSource, EntitlementVerificationState and OriginalCompatibilityLabel distinct. Actual returned plan names and provisioning records are retained, including disabled plans; a returned disabled plan is inventory evidence, not active entitlement.

Normalized inventory states support Observed, NotObserved, NotCollected, Incomplete, Conflicting, NotApplicable and NeedsScenarioConfirmation. Absence requires a complete available/empty collection envelope. Missing/failed/partial collections cannot prove absence. Plan observation, explicit assignment observation, scenario confirmation and not-established entitlement remain distinct; this task infers no confirmed scenario entitlement.

Existing `LicenseEvidenceState` present/unknown remains a compatibility availability signal. The additive `LicenseInventoryEvidenceState` is the normalized collection/inventory authority. Associated License Feature retains the original supporting feature context and never replaces capability or dependency. `licensing_columns` supplies consistent additive register columns. License availability titles require an actual licensing subject; declared license conditions conflicting with recognized technical/assignment subjects block publication.

# 11. Identity and lifecycle behavior

Identity generation, reconciliation keys, native IDs, baseline history and decision mutation APIs were not changed. Actual delta comparison tests verify grouping leaves lifecycle records unchanged; existing governance and identity tests verify preserved targets. Taxonomy 1.1.0 accepts valid 1.0.0 projections for read-only runtime enrichment, validates them first, and never repairs invalid structured records silently. Prior workbook import restores compatibility features for both versions.

# 12. Output impact

Configuration now exposes grouped results, distinct counts, material scope/source boundaries, concise evidence summaries and links covering contiguous group membership. Native evidence IDs remain native IDs; multiple-ID summaries point to complete detail instead of selecting an arbitrary first record.

Sites & Sharing and native technical records remain intact. Findings Lineage, Investigation Items, Recommendations, Evidence Index and Technical Recommendations retain condition titles and normalized supporting context. [Core/customer_report.py](../Core/customer_report.py), `_settings` and `_sharing_changes`, consume the same groups. [Core/html_evidence_pages.py](../Core/html_evidence_pages.py), `write_html_evidence_pages`, exposes authorized paginated membership.

[Core/evidence_selection.py](../Core/evidence_selection.py) now retains FindingKey in the selected finding metadata and carries serialized groups; [Core/finding_evidence.py](../Core/finding_evidence.py) reuses them. Completed snapshot rendering consumes persisted membership without recollection.

# 13. Files changed

New shared modules: `Core/configuration_groups.py`, `Core/authentication_metric_taxonomy.py`, `Core/licensing_projection.py`. Integration: assessment builder/reference validation, authentication summary, evidence selection/model, Excel layout/export/guidance, customer/technical HTML, taxonomy and prior workbook import. New tests: `tests/test_configuration_grouping.py`, `tests/test_configuration_outputs.py`, `tests/test_taxonomy_refinements.py`. This document records the directly affected design and validation. No templates or canonical schema were changed.

# 14. Tests and actual results

Fictional fixtures only. Tests ran with the unchanged Stage 1 guard, prohibiting external sockets, subprocesses inside tests, and writes outside isolated OS temporary directories. Collection calls in tests are mocked. Results below are overlapping categories and must not be added together. Final added-test inventory: 41 methods. Final combined projection/HTML run: 117 passed, 0 failures/errors/skips/guard blocks. Exact command: `python -B tests/run_guarded_stage1.py --allow-temp --quiet tests.test_configuration_grouping tests.test_taxonomy_refinements tests.test_configuration_outputs tests.test_identity_taxonomy tests.test_identity_taxonomy_outputs tests.test_workbook_layout tests.test_customer_report_guidance tests.test_customer_progress_render tests.test_html_evidence_pages tests.test_report_readability`.

Each exact command below ran successfully; all listed categories had zero failures, errors, skipped tests and guard blocks.

| Category | Passed | Exact command |
|---|---:|---|
| Configuration grouping | 8 | `python -B tests/run_guarded_stage1.py --quiet --strict tests.test_configuration_grouping.ConfigurationGroupingTests` |
| SharePoint grouping | 5 | `python -B tests/run_guarded_stage1.py --quiet --strict tests.test_configuration_grouping.SharePointGroupingTests` |
| Evidence preservation | 2 | `python -B tests/run_guarded_stage1.py --quiet --allow-temp tests.test_configuration_grouping.SharePointGroupingTests.test_native_lineage_and_raw_precision_survive_serialization tests.test_configuration_outputs.ConfigurationOutputTests.test_members_have_all_native_evidence_and_precision` |
| ENT-008 | 2 | `python -B tests/run_guarded_stage1.py --quiet --strict tests.test_taxonomy_refinements.AssignmentTaxonomyTests.test_group_assignment_beats_generic_license_signal tests.test_taxonomy_refinements.AssignmentTaxonomyTests.test_copilot_assignment_is_specific_but_not_a_required_control` |
| ENT-014 | 2 | `python -B tests/run_guarded_stage1.py --quiet --strict tests.test_taxonomy_refinements.AssignmentTaxonomyTests.test_guest_license_is_not_guest_access_or_entitlement tests.test_taxonomy_refinements.AssignmentTaxonomyTests.test_guest_lifecycle_stays_distinct` |
| Authentication metric taxonomy | 4 | `python -B tests/run_guarded_stage1.py --quiet --strict tests.test_taxonomy_refinements.MetricTaxonomyTests` |
| Licensing normalization | 11 | `python -B tests/run_guarded_stage1.py --quiet --strict tests.test_taxonomy_refinements.LicensingNormalizationTests` |
| License title restrictions | 2 | `python -B tests/run_guarded_stage1.py --quiet --strict tests.test_taxonomy_refinements.AssignmentTaxonomyTests.test_all_technical_signatures_beat_license_basis tests.test_taxonomy_refinements.AssignmentTaxonomyTests.test_explicit_license_with_technical_subject_is_blocked` |
| Identity lifecycle stability | 4 | `python -B tests/run_guarded_stage1.py --quiet --allow-temp tests.test_configuration_outputs.ConfigurationLifecycleTests tests.test_identity_taxonomy_outputs.TaxonomyRealLifecycleTests` |
| Output navigation | 6 | `python -B tests/run_guarded_stage1.py --quiet --allow-temp tests.test_configuration_outputs.ConfigurationOutputTests` |
| Earlier identity taxonomy | 42 | `python -B tests/run_guarded_stage1.py --quiet --allow-temp tests.test_identity_taxonomy tests.test_identity_taxonomy_outputs` |
| Pass B | 87 | `python -B tests/run_guarded_stage1.py --quiet --allow-temp tests.test_privileged_identity tests.test_privileged_boundaries tests.test_privileged_outputs tests.test_privileged_lifecycle` |
| Pass A and collection envelopes | 80 | `python -B tests/run_guarded_stage1.py --quiet --allow-temp tests.test_authentication_corrections tests.test_authentication_methods tests.test_signin_evidence tests.test_legacy_signin_evidence tests.test_entra_collection_failures` |
| Closure overrides | 19 | `python -B tests/run_guarded_stage1.py --quiet --allow-temp tests.test_closure_override_validation` |
| Governance | 61 | `python -B tests/run_guarded_stage1.py --quiet --allow-temp tests.test_governance_decisions tests.test_governance_semantics tests.test_governance_outputs` |
| Delta states | 49 | `python -B tests/run_guarded_stage1.py --quiet --allow-temp tests.test_delta_lifecycle tests.test_delta_metrics tests.test_delta_validation tests.test_delta_outputs` |
| Run workflow | 37 | `python -B tests/run_guarded_stage1.py --quiet --allow-temp tests.test_assessment_run_workflow tests.test_assessment_run_execution tests.test_assessment_run_outputs` |
| Reconciliation | 41 | `python -B tests/run_guarded_stage1.py --quiet --allow-temp tests.test_scope_reconciliation tests.test_safe_finding_deduplication tests.test_reconciliation_outputs_and_validation` |
| Persistent identity | 62 | `python -B tests/run_guarded_stage1.py --quiet --allow-temp tests.test_persistent_identity` |
| Stage 1 | 35 | `python -B tests/run_guarded_stage1.py --quiet --allow-temp tests.test_stage1_semantics` |
| Dashboard retirement | 8 | `python -B tests/run_guarded_stage1.py --quiet --allow-temp tests.test_dashboard_retirement` |
| Serialization replay | 31 | `python -B tests/run_guarded_stage1.py --quiet --allow-temp tests.test_persistent_identity.IdentitySerializationTests tests.test_offline_report tests.test_privileged_outputs.PrivilegedOutputTests.test_snapshot_and_offline_replay_preserve_normalized_identity tests.test_configuration_outputs.ConfigurationOutputTests.test_snapshot_replay_preserves_model_without_recollection` |
| Packages integrity | 44 | `python -B tests/run_guarded_stage1.py --quiet --allow-temp tests.test_assessment_package tests.test_assessment_integrity` |
| Excel | 36 | `python -B tests/run_guarded_stage1.py --quiet --allow-temp tests.test_export_recommendations tests.test_workbook_evidence tests.test_workbook_layout tests.test_investigation_workbook_audit tests.test_privileged_outputs.PrivilegedOutputTests.test_excel_keeps_scopes_identities_and_qualified_activity` |
| HTML | 22 | `python -B tests/run_guarded_stage1.py --quiet --allow-temp tests.test_customer_report_guidance tests.test_customer_progress_render tests.test_html_evidence_pages tests.test_report_readability tests.test_privileged_outputs.PrivilegedOutputTests.test_html_and_readiness_are_identity_free_and_do_not_recalculate` |
| Guarded regression | 79 | `python -B tests/run_guarded_stage1.py --quiet --allow-temp --regression` |

Final broader safe discovery: 1,471 passed, 0 failed, 0 errored. Exact command: `python -B tests/run_guarded_stage1.py --allow-temp --quiet --safe-discover`. This run completed against the final production/test changes in 411.793 seconds. Its total overlaps the focused categories and is not added to them.

| Separate validation category | Actual result |
|---|---|
| Broader safe suite | 1,471 passed |
| Skipped tests | 0 |
| Excluded tests | 102 existing subprocess/cache tests; unchanged exclusion policy |
| Guard-blocked operations | 0 |

Exclusions are not skips and were not claimed as locally passed. External socket/subprocess and workspace/customer writes were guarded; no tenant access or collection occurred. Both supplied engineering workbook hashes remained unchanged. No supplied workbook or generated customer artifact was staged or committed. Dashboard/App Builder output modules and finding_uid dependencies remain absent.

`powershell -NoProfile -ExecutionPolicy Bypass -File tests/Test-PowerShellSyntax.ps1`: 11 scripts passed. `python -B tools/check_docs.py`: 227 local links passed. `git diff github/main --check`: passed. Final staging is restricted to the listed source modules, fictional tests and this technical report; customer workbooks, caches and generated deliverables are excluded.

Test-first runs exposed the requested taxonomy defects (including five technical-subject subtest failures) and absent normalization fields. Intermediate fixture/API errors were corrected; existing assertions were not weakened.

# 15. Compatibility impact

FindingId, RecommendationId, PFI/observation/native EvidenceId algorithms, selected support/source occurrences, original timestamp strings, raw records, workbook navigation, archived baseline bytes, completed runs, offline replay and packages remain compatible. Additional columns and the technical membership register are intentional output changes. No graph endpoint, beta dependency, tenant collection, customer policy threshold or automatic decision was added.

# 16. Remaining canonical-contract implications

The existing assessment templates and [schemas/assessment-results.schema.json](assessment-templates/schemas/assessment-results.schema.json) remain the target contract. This is an additive runtime presentation extension, not canonical-contract migration. Migration must formally represent configuration observation/group membership, scoped distinct counts/coverage, normalized licensing context and metric condition metadata, and retain evidence references. Confirm customer methodology and licensing scenarios separately; unknown inheritance/entitlement cannot be promoted to established facts. Grouping other object classes remains deferred.

# 17. GitHub result

Feature branch: `fix/configuration-grouping-and-taxonomy`. Target: GitHub `main`. Exact PR title: Group configuration results and complete taxonomy fixes. Test commit: `3470d7b`; the implementation/report commit is the subsequent branch head. The published PR URL, final head hash and current CI status are reported in the accompanying chat summary. This stage does not authorize merging the PR.

# 18. Completion status

Implementation and all required local validation are complete. The authorized publication step pushes this feature branch and opens its review PR; final-head GitHub CI is reported separately and is not inferred from prerequisite checks. No customer artifact, credential, retained cache, dashboard output or App Builder component belongs in this change. The next stage is a controlled engineering assessment after review/merge, followed by canonical-contract migration.

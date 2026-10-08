# Scope-aware reconciliation and finding deduplication

This additive stage retains methodology 4.0.0 and uses reconciliation model 2.0.0.
The [assessment templates](assessment-templates/README.md) remain future contract
direction. Explicit run selection and baselines are described in
[Assessment runs](ASSESSMENT_RUNS.md). Deltas, resolution and closure remain deferred.

## Entry points

`evidence_layer.deduplicate_findings` delegates to
`finding_reconciliation.group_findings(early=True)`. Explicit scoped keys permit
conservative grouping; measurements, native IDs, captures and collection quality
cannot be discarded by priority. Text without grouping authority remains separate.

`assessment_result._qualify_record` retains declared comparison fields after the
existing EV hash. A declared measurement or control conclusion supplies semantic
measurement; a legacy narrative cannot establish interchangeable measurements.
`_deduplicate_records` uses the same finding engine. `allocate_display_ids`
prevents RecommendationId collisions from overwriting findings. The builder
attaches reconciliation once, before `assessment_identity.attach_identity`.

`observation_reconciliation.reconcile_evidence` returns a structured graph plus
an occurrence-preserving `rows` adapter. `evidence_contract.reconcile_observations`
still returns every source row. The adapter also serves adoption metrics and
existing readiness-progress checks. `evidence_selection.build_evidence_selection`
expands all retained declarations for the shared Excel/HTML evidence model.
Renderers consume the shared result and do not reconcile independently.

## Comparison and grouping

Material dimensions include tenant/assessment/run/environment, provider/workload,
control/version, metric/definition/unit, population/definition, scope/objects,
applicability/stage, evidence level, product/tier, reporting basis and window.
Native IDs are material when supplied. Exact comparison additionally includes
capture instant/precision, conclusion, availability, completeness, truncation,
qualifications, numerator and denominator. Capture/native references remain in
each original occurrence. Full SHA-256 comparison keys are internal keys, not
new POB/PFI identities.

Missing tenant, control, metric, population, scope or unit prevents cross-source
equivalence. Other missing legacy declarations are diagnosed and cannot match a
different known value. Existing snapshot comparison for legacy facts lacking a
window remains qualified; no period is fabricated. Percentage denominators are
additional comparison boundaries. Nothing is summed or averaged here.

Complete evidence precedes newer partial evidence, then the latest orderable
capture wins. Original timestamps and offsets remain intact. Equivalent instants
compare equally; dates cannot order timestamps within their day, and uncertain
timezones cannot establish offset ordering. Undated candidates stay in the
conflict pool. Future/unavailable values cannot establish favorable support.
Comparable incompatible measurements or conclusions create structured conflicts
whose result remains `Not established`. Every occurrence remains retained.

Finding authority is a persistent declaration or explicit scoped FindingKey rule.
Controls, provider coverage, populations, scopes, applicability, rollout stages,
customer decisions and closure requirements remain distinguishable. Several
captures/evidence levels may support one issue without collapsing observations.
Historical declarations remain qualified, without new reassessment transitions.

`SourceOccurrences`, `RelatedEvidenceIds` and `FindingMembership` preserve members.
`reconciliation` contains observations, occurrences, exact groups, conflicts,
non-comparable boundaries, support relationships, members, display selection,
missing dimensions and reasons. Unknown legacy dimensions do not gain persistent
certainty. Execution IDs are inherited only from a complete persisted seed;
direct builders still do not mint UUIDs.

## Identity and compatibility

Tests reproduced two old boundary collisions before implementation: opposite
conclusions sharing a POB, and incompatible closure requirements sharing a PFI.
Prefixes and canonical hash algorithms remain unchanged. Generated POBs receive
optional `semantic_variant` only when the old boundary actually collides. Unique
old boundaries keep their IDs. Exact generated declarations are interned with
all occurrence/capture references; explicit duplicate producer declarations still
fail validation. PFI optional boundaries now include population definition,
control version, applicability, stage, customer decision and closure requirements.
Absent new fields keep old IDs; a newly declared material boundary can change PFI.

One RecommendationId collision anchor stays available; additional findings receive
stable suffixes and preserve `CompatibilityRecommendationIds`. Scoped typed aliases
and structured diagnostics list the allocations. Generated ambiguous EV aliases
remain unresolved with candidate targets, never first-match resolution. Explicit
producer declarations are not repaired.

Existing assessment `counts` are unchanged. Separate neutral `evidence_counts`
report set-based evidence/native record counts and support relationships. Native
counts use declared provider/dataset boundaries; unknown coverage stays source-local.
These are not unique affected entities. Specialized projections do not duplicate
already-covered native rows. Additional declarations, truncation and aggregate/detail
qualifications remain available.

## Validation, serialization and renderers

`reconciliation_validation.validate_reconciliation` extends
`assessment_references.validate_assessment_references`. Errors include cross-boundary
membership/support, duplicate/lost occurrences, dangling support, invalid exact
groups/counts, incompatible aggregates, missing reasons, overwritten navigation
IDs, persistent finding mismatches, invalid registers and favorable conflicts.
Missing legacy comparison/grouping detail is a compatibility warning. Existing
typed-reference and PSR validation stays in force.

Snapshot, Excel and HTML writers validate before creating deliverables. Navigation
annotation and worksheet splitting skip immutable semantic/evidence registers.
Serialization retains the graph losslessly; old snapshots receive a qualified
legacy block without invented grouping. Equivalent replay inputs preserve the model.

SRC/EVD/DET/ROW/native algorithms, unique visible IDs, workbook registers/links,
HTML anchors, historical imports and package integrity remain supported. Positional
locators can still change on source reordering. Raw declarations increase snapshot
size; streaming and full schema migration remain deferred. No retired dashboard,
App Builder or finding_uid functionality returns.

## Verification

Forty-one new fictional tests cover reconciliation, grouping, collisions, validation,
serialization, replay, shared records and renderers. The 35 Stage 1, eight retirement
and 62 persistent-identity tests are unchanged. One older text-only deduplication
assertion now requires independent legacy declarations because wording is not authority.

Use `python -B tests/run_guarded_stage1.py --quiet` with `--strict` for pure
fixtures or `--allow-temp` for temporary outputs. Network/subprocesses and
repository/customer-artifact writes are blocked. Broad `--safe-discover` explicitly
excludes 102 tests: 97 requiring subprocesses and five requiring retained-cache
writes. Exclusions are not skips. Group counts overlap and are not additive.

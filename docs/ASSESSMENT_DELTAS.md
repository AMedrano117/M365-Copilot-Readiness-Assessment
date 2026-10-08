# Evidence-derived reassessment deltas

[Assessment runs](ASSESSMENT_RUNS.md) | [Methodology](METHODOLOGY.md) | [Outputs](OUTPUTS.md)

Delta engine and metric-rule version `1.0.0` are additive shared-result metadata.
Methodology `4.0.0` still determines current readiness and control status. The
assessment templates and target schema remain future direction; this is not a
target-contract migration. No governance approval is created.

## Execution boundary

`Core/assessment_runs.py::complete_run` first evaluates Stage A comparability,
then calls `Core/assessment_delta.py::evaluate_delta` with the baseline loaded by
`prepare_run` through `assessment_history.select_baseline`. It attaches one
`lifecycle` object, validates it, and persists it before rendering. The existing
`run_context.Comparability.Items` is preserved. Typed alias and retained-lifecycle
matching use the same eligibility evaluator for supplemental continuity; they
do not turn a failed collection into eligible evidence.

Reassessment defaults to enabled delta calculation. To omit classifications:

```text
python main.py --mode offline --collection-input '<collection.json>' --run-type Reassessment --assessment-id '<AST>' --assessment-history '<assessment-history.json>' --baseline-run-id '<RUN>' --delta-mode disabled
```

`--delta-mode enabled` is also accepted. These options require explicit
Reassessment intent. The Python API accepts `prepare_run(..., delta_enabled=False)`.
Disabled runs still evaluate comparability, append history, and record the omission.
Initial and Standalone persist NotEvaluated context and no classified records.
Legacy execution does not acquire invented lifecycle states. Legacy `--baseline`
remains a separate compatibility path; it cannot establish this lifecycle model.

Replay consumes stored lifecycle records without calling the delta engine,
minting UUIDs, appending history, selecting a new baseline, or refreshing evidence.
Validation checks saved calculations against the versioned metric rules; it never
repairs or changes a state. There is no recalculation operation in this stage.

## States

| State | Required interpretation |
| --- | --- |
| NotEvaluated | No requested/enabled baseline comparison; no item classifications |
| NotComparable | Ownership or run semantics cannot support comparison; reasons retained |
| New | Unmatched current issue, with sufficient current proof and positive baseline absence evidence for the same condition |
| Unchanged | Material measurements equivalent; wording, severity, row order and locators do not decide equality |
| Continuing | Same open condition, without a defensible single direction, or metric not bound to that issue |
| Changed | Non-directional metric or supported evidence-level change; reasons explain the change |
| Improved | Explicit registered direction, comparable complete support; condition can remain open |
| Regressed | Explicit registered direction or supported Met-to-NotMet categorical control transition |
| NotReassessed | Baseline condition remains unresolved because current evidence/control verification was not obtained |
| Indeterminate | Conflict, partial/ambiguous evidence, incompatible item boundaries, or unsupported absence proof |
| ResolvedByCurrentEvidence | Retained current proof establishes the baseline condition absent under a supported resolution rule |
| Reopened | Same persistent issue returns with sufficient comparable evidence after a retained proven resolution |

NotReassessed and Indeterminate retain unresolved baseline conditions. Counts are
separate classifications, not open-risk totals or a security/readiness score.

## Matching and evidence

PFI controls finding continuity; PCT plus compatible definition controls control
continuity. Existing POB cross-run eligibility preserves original capture IDs.
PAC comparison requires its finding links; supported action states follow linked
findings. Metrics retain their parent POB identity and explicit MetricId; their
counts never add to finding counts.

Unique typed aliases can establish compatibility only when namespace, value,
type, artifact, assessment and originating run are unambiguous, and material
provider/population/scope/decision boundaries agree. RecommendationId aliases,
titles, severity, owners, dates and positional locations never establish lifecycle
matching. Ambiguous aliases remain unresolved in comparability diagnostics.

A resolved finding can be absent from the baseline's current register. Its retained
EntityBoundary must reproduce the same PFI under the identity algorithm and
assessment/environment ownership before it can support later reopening. The
baseline's original bytes and conclusions remain unchanged.

Complete current coverage is required for favorable/directional transitions.
Run-level coverage gating remains deliberately conservative: an unrelated incomplete
source can prevent a transition. Finer source-to-control coverage authority is deferred.
Measured zero is distinct from null/missing evidence; an empty finding register
does not prove absence. Superseded/duplicate facts never replace selected support.
Resolution cannot follow conflict, partial collection, changed applicability,
smaller scope/population, unknown unit, unrequested collection, or disappearance alone.

## Explicit metric rules

`Core/delta_metrics.py::RULES` is an opt-in normalized metric contract, not an adapter
that guesses semantics from existing strings. Existing adapters that emit other
metric IDs stay non-directional. The registry currently supports:

| MetricId | Unit / direction | Evidence and resolution |
| --- | --- | --- |
| identity.mfa_registration_percent | % / higher toward 100 | Inventory/configuration; cannot resolve an enforcement finding |
| identity.policy_exceptions | count / target zero | Policy enforcement/observed operation; mapped policy-exception condition may resolve |
| endpoint.unsupported_devices | devices / lower toward zero | Configuration/observed operation; mapped unsupported-device condition may resolve |
| endpoint.validated_coverage_percent | % / full coverage | Configuration/observed operation; no automatic resolution |

Each rule declares control IDs, condition keys, unit, target, zero meaning,
population/scope requirements, evidence levels, complete/current selected evidence,
percentage/count support, tolerance (zero), resolution permission, human-review
qualification and version. Conditions outside the explicit binding cannot be
resolved by a metric for another issue on the same control.

An explicit categorical control-status rule supports NotMet-to-Met resolution
and Met-to-NotMet regression/reopening, using complete compatible enforcement,
operation, attestation or portal-review evidence. Inventory alone is insufficient.
NotVerified-to-Met is Changed; current NotVerified remains NotReassessed.
A still-failed control is never resolved merely because one metric reached its target.
Additional free-text closure criteria require their own proof; a metric reaching
its target can leave the finding Improved and open. An explicit metric-only
closure requirement may use `{"MetricId": "identity.policy_exceptions", "Target": 0}`.
Changed applicability or another material successor boundary cannot resolve an
unmatched baseline issue automatically.

Percentages require known valid numerators/positive denominators, consistent reported
values and recorded population definitions. Denominator/membership changes block
automatic direction and remain qualified. Exact known population identity supports
comparison; no population/provider equivalence mapping is guessed. Pilot/all users,
administrators/all users, licensed/all users and selected/all sites remain distinct.

Full periods/timestamps and source bases remain in metric records. Different periods
are qualified; different duration or unknown duration blocks metric direction.
Stage A's stricter item-period gate can still prevent a lifecycle transition even
when a metric supports qualified different-period comparison. No qualified-mode
override or control-version migration is introduced here.

Unknown directions produce Changed, Continuing or Indeterminate. Severity is never
a direction rule. Unknown values remain null; no zero substitution occurs. Absolute
and percentage-point changes require compatible support; relative percentage change
is omitted for a zero baseline. Evidence-level/source-basis changes remain qualified.

## Persistence, validation and presentation

`Core/lifecycle_validation.py::validate_lifecycle` returns structured blocking
diagnostics for invalid ownership, intent, baseline, states, matching, references,
calculations, direction, resolution, reopening, coverage and summary reconciliation.
The existing publication gate rejects errors before a snapshot/output is written.
Old snapshots without lifecycle records remain readable with compatibility diagnostics
and no fabricated conclusions. Unresolved typed aliases retain the existing legacy
diagnostics. Unsupported metrics are recorded as qualifications, not favorable claims.

The `lifecycle` object contains versioned records, run/baseline identity, baseline
coverage metadata, state/reasons/qualifications, statuses, semantic boundaries,
metric changes, evidence references, prior resolution proof and closure requirements.
Only measurement/comparison metadata is retained; raw evidence is referenced.
GeneratedAt uses the persisted execution timestamp for deterministic calculation
metadata; it never changes the evidence dates. Finding population counts and per-type
state counts are derived from unique records and validated against the details.

History remains schema `1.0.0`. Only the new run entry receives optional `Lifecycle`
metadata: engine/rule versions, enabled flag, baseline, summary, generated timestamp,
validation outcome and a relative detailed-records snapshot locator. Prior entries
are not rewritten. Move the common package/history tree to preserve relative locators.

`Core/lifecycle_presentation.py` projects recorded data only. Both workbooks add
Reassessment and Lifecycle Records sheets; the technical workbook retains every
record field. The main HTML report and readiness summary show concise qualified
finding counts and explicit baseline. The HTML technical view exposes full records,
and matched finding detail shows metrics, periods, proof and remaining closure work.
Immutable lifecycle references are excluded from worksheet-range remapping and
investigation record enumeration. Existing workbook/HTML destinations are unchanged.

Larger snapshots retain compact comparison metadata per entity; reused support does
not inflate unique counts. Chunked target-contract exports and renderer consolidation
remain future work. Snapshot replay cannot recreate raw records that were not saved.

## Deferred decisions

ResolvedByCurrentEvidence is not formal customer acceptance. ClosedByRemediation,
AcceptedRisk, ApprovedException and NoLongerApplicable require accountable authority,
rationale, scope, date/review date and supporting artifacts in a later governance pass.
Existing imported fields stay separate. Provider/population equivalence, metric adapter
expansion, finer coverage mapping, control-version governance, target-schema migration,
renderer consolidation and broader presentation improvements remain deferred.

## Safe tests

```text
python -B tests/run_guarded_stage1.py --strict --quiet test_delta_metrics test_delta_lifecycle test_delta_validation
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_delta_outputs
```

Fixtures are fictional. The guard denies tenant/network/subprocess access and
confines output fixtures to new OS-temp directories. Retain every prior Stage 1,
identity, reconciliation, run-workflow, retirement and output safeguard test.

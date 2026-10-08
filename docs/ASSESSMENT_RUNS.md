# Assessment runs, explicit baselines and comparison eligibility

[Documentation index](README.md) | [Methodology](METHODOLOGY.md) | [Persistent identity](PERSISTENT_IDENTITY.md)

Stage A records run intent and comparison eligibility. The additive
[delta engine](ASSESSMENT_DELTAS.md) now calculates evidence-derived changes for
explicit reassessments. Current methodology remains the readiness authority;
the assessment templates remain future direction. Formal closure stays deferred.

## Execution and rendering

`Core/assessment_runs.py::prepare_run` creates execution identity at an explicit
execution boundary. `complete_run` persists the completed semantic result before
renderers publish. The shared builder consumes the persisted identity without
generating UUIDs. Excel and HTML consume the same `run_context`.

| Intent | Assessment identity | Run identity | Baseline | History |
|---|---|---|---|---|
| Initial | New AST | New RUN | None | New history; one reserved Initial |
| Reassessment | Explicit existing AST | New RUN | Explicit completed RUN | Validate and append |
| Standalone | New AST or explicit owned AST | New RUN | None | None by default; continuation is explicit |
| Snapshot render | Preserve existing identity | Preserve existing RUN | Preserve recorded value | No append |

Commands without run intent retain the legacy unclassified compatibility path
and emit a diagnostic. They do not infer Initial intent or a baseline-run identity.
The narrow direct identity API retains its prior diagnostics interface. Shared
builder results and serialized snapshots carry `run_workflow_diagnostics`.

## Noninteractive examples

Use fictional placeholders below with an explicitly verified tenant GUID.

```powershell
python main.py --mode offline --collection-input '<saved collection.json>' --run-type Initial --tenant-id '11111111-1111-1111-1111-111111111111'
python main.py --mode offline --collection-input '<saved collection.json>' --run-type Reassessment --assessment-id '<AST identity>' --assessment-history '<assessment-history.json>' --baseline-run-id '<completed RUN identity>'
python main.py --mode offline --collection-input '<saved collection.json>' --run-type Standalone
python main.py --replay-snapshot '<assessment-results/RUN identity.json>' --render-output-dir '<render parent>'
```

`--run-type` also applies to live execution after tenant preflight. Initial history
defaults to the new package's `assessment-history.json`. `--assessment-history`
selects another explicit location. Reassessment requires that location, the AST,
and the baseline RUN. Standalone continuation requires both AST and history to
validate ownership; customer names, report titles and folders never establish it.
`--primary-environment-id` optionally checks an ENV against the selected tenant GUID.
`--assessment-purpose` records purpose/family; continuation inherits the recorded
purpose unless explicitly changed.

The older `--baseline PATH` remains a legacy change-tracking compatibility
interface. It cannot be combined with explicit Stage A run intent. Stage A never
calls that legacy comparison path or emits its delta rows.

New explicit runs create a new package. Saved collections and original inputs
are retained without rewriting their evidence. Supplemental recipe references
and hashes remain portable. A completed explicit package's ordinary
`--collection-input` path renders its retained snapshot without control evaluation.
Adding evidence or changing its evaluation date requires explicit new intent.
Old packages retain their existing rebuild behavior with a compatibility diagnostic.

## Persisted records

`Core/assessment_history.py` owns history schema `1.0.0` and baseline validation.

- `assessment-history.json`: AST, verified ENV, purpose, methodology/catalog,
  InitialRunId, completed Runs, update timestamp, and canonical SHA-256 integrity.
- Each entry: sequence, AST/RUN/ENV, RunType, BaselineRunId, evaluated/created
  times, relative package and snapshot locators, snapshot SHA-256, coverage,
  semantic versions, run boundaries, comparability outcome, reasons and eligibility.
- `run-seeds/RUN identity.json`: reserved execution seeds alongside history.
- Package `run-seed.json`: execution seed retained before render publication.
- Package `assessment-results/RUN identity.json`: completed shared semantic result.
- Package `assessment-run.json`: snapshot locator/hash and execution metadata.

History references snapshots rather than duplicating raw evidence. Snapshot
`run_context` contains the selected history reference and comparability metadata,
not the entire history. Existing typed identities and source hashes remain intact.
Operator render receipts remain separate from semantic run history.

Updates use an exclusive lock, optimistic integrity check, complete candidate
validation, fsync and atomic replacement. Existing entries are immutable. Duplicate
RUNs, foreign ownership, invalid Initial/baseline combinations, changed history and
invalid locators are rejected. A failed renderer may leave a completed semantic
snapshot/history entry with a failed artifact receipt; rendering is a separate operation.
A failed history append can leave an unreferenced new snapshot, while prior history
remains intact. Do not silently repair it or reinterpret it as an appended run.

Locks are never automatically removed as stale. After an interrupted writer,
an operator must verify no writer remains before repairing a lock. Relative locators
remain valid when their common package/history tree moves. If an external history
references several packages, move that common tree or explicitly update its locators
and reseal integrity after verifying ownership and hashes. Snapshot rendering does
not need to load or modify external history. Legacy history is readable with unresolved
continuity diagnostics and cannot authorize reassessment until metadata is verified.

## Baseline gate

`select_baseline` loads exactly the requested RUN. It verifies selected-history
integrity, AST/ENV ownership, completed status, ordering, package containment,
snapshot readability/hash and snapshot identity. A missing, self, foreign, later or
unreadable baseline blocks reassessment publication. No newest/previous/Initial
baseline selection or search exists.

`BaselineValidation` records the selected ownership and integrity metadata in
run context. `validate_run_context` extends semantic publication gating with
run identity, intent, baseline, context-copy and comparability diagnostics. Prepared
reassessments must finish comparability evaluation before publication.

## Comparability

`Core/run_comparability.py::evaluate_comparability` is a pure deterministic
function. It returns Comparable, ComparableWithQualifications, NotComparable,
or NotEvaluated. Comparable is eligibility to compare, not evidence of improvement.
NotComparable is publishable with reasons; it blocks directional interpretation.

Versions must match unless an explicit catalog mapping is supplied to the comparison
API. No methodology or reconciliation-version compatibility is guessed. Catalog
version defaults to a hash of the current domain specifications; methodology-version
discipline remains necessary for evaluation-rule changes. Purpose, scope, providers,
population definitions, resource scopes, coverage and legacy gaps qualify comparison.

Controls, findings, observations and customer actions receive eligible, eligible with
qualifications, not eligible, or unresolved legacy metadata states with reasons.
Observation matching compares recorded semantic boundaries across runs while preserving
both capture-specific POB IDs. Finding and action matching preserves persistent IDs.
Ambiguous or unbound aliases do not establish continuity. Missing current items never
mean resolution. Action eligibility also checks its linked finding support.

Current missing, failed, unavailable, unlicensed, inaccessible, not-requested, partial
or unknown collection cannot establish unqualified item eligibility. Complete current
evidence against a partial baseline requires qualifications. Empty results require
known population and collection semantics. Conflicts, null measurements and Not
established remain non-favorable. Unit, period, metric definition and material
population/scope changes prevent direct item comparison. Configuration and observed
effectiveness remain distinct evidence levels.

Coverage qualification is deliberately conservative: incomplete run coverage can
make otherwise matching items ineligible. Finer provider/control coverage mappings,
explicit methodology compatibility policies and catalog mapping governance remain
methodology decisions for subsequent work.

## Retained outputs and verification

Both workbooks display run context in Run Manifest; HTML displays it in the technical
appendix. The executive summary exposes only intent/comparability and points to the
qualifications. Snapshots and package manifests preserve context. Snapshot replay
performs rendering without collection, control evaluation, new UUIDs or history append.
It cannot reconstruct raw source records that the snapshot did not retain; retain the
collection package for those records. Existing evidence locators remain compatibility
references, not durable cross-run identity.

Acceptance tests are in `tests/test_assessment_run_workflow.py`,
`tests/test_assessment_run_outputs.py` and `tests/test_assessment_run_execution.py`.
Run them with `python -B tests/run_guarded_stage1.py --allow-temp --quiet` followed
by the three module names. Preserve all earlier semantic, identity, reconciliation,
retirement and output tests. The Windows CI setup resolves TEMP/TMP to one physical
path spelling; package fixture cleanup supports Python 3.10 without changing assertions.

Evidence-derived states are documented in [Assessment deltas](ASSESSMENT_DELTAS.md).
Formal closure, accepted risk, exceptions, automatic baseline convenience,
target-schema migration and renderer consolidation remain deferred.
Retired dashboard and App Builder outputs remain absent.

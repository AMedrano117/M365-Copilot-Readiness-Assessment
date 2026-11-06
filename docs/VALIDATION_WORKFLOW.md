# Validation workflow

This guide is for developers. Start each assessment or report change with the fastest layer
that can prove it, and run a live collection only as the final validation.

Every layer reuses the production code:
- declared scenario suites;
- `run_offline_report`, for rebuilds;
- `render_snapshot`, for render-only output;
- the shared assessment-result serializer.

`tools/validate.py` blocks and records any tenant access attempt. That includes non-loopback
network connections, credential token requests and PowerShell launches.

Outputs are written to a new engineering folder. By default this is a system temporary folder
named `assessment-validation-*`. Each output folder:
- is labelled `ENGINEERING VALIDATION ONLY — NOT CUSTOMER-READY`;
- is never eligible for customer publication;
- never creates assessment history, a baseline or a lifecycle delta;
- is never written inside a retained package or the repository's `Reports` folder.

Retained packages are read, copied and hash-verified, never changed.

## Choose a layer

| Change | Layer | Duration |
| --- | --- | --- |
| A rule, classification or calculation | 1. Unit and invariant tests, then 2. Scenarios | Seconds to minutes |
| Methodology or decision logic | 2. Scenarios, then 3. Offline rebuild | About 10 minutes |
| HTML, Excel, wording or layout only | 4. Render-only | About 5 minutes |
| A collector or importer | 5. Focused diagnostic, then 3. Offline rebuild | Varies |
| A new permission, endpoint or authentication path | 6. Full live collection | About 30 minutes |

## 1. Unit and invariant tests

```powershell
python -m unittest discover -s tests -p "test_<area>*.py"
python -m unittest discover -s tests
```

- **Tenant access:** none; collectors are mocked.
- **Proves:** each rule and invariant, across the full protected suite.
- **Cannot prove:** behaviour on real tenant data shapes.

## 2. Scenario validation

```powershell
python tools/validate.py scenarios
python tools/validate.py scenarios --name "License evidence versus entitlement"
```

- **Inputs:** fictional declared scenarios, listed in
  [tests/scenarios/workflow_manifest.json](../tests/scenarios/workflow_manifest.json).
  - Each scenario declares its expectations before evaluation: source states, control results,
    evidence sufficiency, readiness treatment, findings, action count and prohibited conclusions.
  - The fixtures live with their existing suites under `tests/scenarios/`.
- **Outputs:** a pass/fail test summary and whether tenant access was attempted.
- **Proves:** the declared decision behaviour of the production code.
- **Cannot prove:** real export formats or the completeness of the full report.

The groups cover:
- unread versus complete-empty sources;
- Conditional Access policy and legacy-authentication events;
- license evidence versus entitlement;
- device records versus distinct devices;
- SharePoint Advanced Management unavailable, with equivalent evidence;
- a supported component plus an unread required component;
- duplicate evidence supporting one action;
- a presentation-only render change.

## 3. Offline package validation

```powershell
python tools/validate.py rebuild --package "Reports\Cavehouse Brewery\<package>"
python tools/validate.py rebuild --package "<package>" --fail-on-semantic-change
```

- **Inputs:** a retained completed package, containing `collection.json` and `assessment-run.json`.
  The Cavehouse Brewery test-tenant package is the engineering reference. Never use a customer
  package.
- **Steps:**
  1. Hash the original package.
  2. Copy it to the engineering folder.
  3. Run a Standalone offline rebuild of the copy with the production code. It keeps the retained
     evaluation and observation dates.
  4. Compare the rebuilt result with the retained result.
  5. Render the rebuilt snapshot again as a replay check.
  6. Verify that the original package's hashes are unchanged.
- **Outputs:** `comparison.md` and `comparison.json`, plus the rebuilt deliverables. See
  [Comparison output](#comparison-output).
- **Tenant access:** none.
- **Proves:** how the current code changes conclusions on real retained evidence, and whether each
  change is explained by a declared transition.
- **Cannot prove:** anything about evidence the collection did not retain.

The source gate blocks a rebuild if the checkout changes while it runs, so don't edit files
during a rebuild. A rebuild with expected semantic differences is a successful validation;
`--fail-on-semantic-change` makes it exit 1 instead.

## 4. Render-only validation

```powershell
python tools/validate.py render --snapshot "<package>\assessment-results\RUN-<id>.json"
python tools/validate.py render --snapshot "<snapshot>" --compare-to "<earlier engineering folder>\render"
```

- **Behaviour:** reads an existing assessment-result snapshot and renders HTML and both workbooks
  without re-evaluating evidence.
- **Failure rule:** any semantic difference fails, including decision, controls, findings,
  actions, identities or lifecycle. `--compare-to` lists presentation-only differences against an
  earlier render.
- **Tenant access:** none.
- **Proves:** a presentation change leaves conclusions intact, and every output opens.
- **Cannot prove:** that the conclusions themselves are right. Use layers 2 and 3 for that.

## 5. Focused diagnostics

```powershell
python tools/validate.py diagnostics
```

This lists the existing focused diagnostics and whether each one accesses a tenant:

| Diagnostic | Tenant access |
| --- | --- |
| `--check-connections` preflight | Read-only probes; no assessment output |
| `tools/diagnose-usage-report.py` and `tools/diagnose-usage-report-download.ps1` | Read-only |
| `tools/Test-IntuneEvidence.ps1` | Read-only GET requests |
| `tools/validate_offline_report.py` (synthetic package audit), `tools/audit_report_signal.py` and `tools/audit_workbook.py` | None |

## 6. Full live collection

Run a full live collection only as final validation:
- after a collector, permission, endpoint, authentication or module change;
- when a new Microsoft data shape must be observed;
- before release.

Use the Cavehouse Brewery test tenant. Offline layers cannot prove live permissions, throttling,
licensing responses, or data that was never collected.

## Comparing two snapshots

```powershell
python tools/validate.py compare BEFORE.json AFTER.json
python tools/validate.py compare BEFORE.json AFTER.json --mode render
```

In `render` mode, any semantic difference is reported as an unexpected regression.

## Outcome model

Each run reports two statuses. The **execution status** says whether validation ran:

| Execution status | Meaning |
| --- | --- |
| `Completed` | The rebuild or render ran, without tenant access, and the source package is unchanged. |
| `FailedToExecute` | The run crashed, was blocked by the source gate, produced no result, attempted tenant access, changed the source package, or wrote an output that does not open. |
| `InvalidPackage` | The package or snapshot is missing, malformed or fails its integrity check. |
| `IncompatiblePackage` | The package is intact, but its methodology or taxonomy major version differs from the current one, with no declared compatibility transition, or its result lacks controls or findings. |

The **comparison status** says what a completed run found. When several apply, the most severe
one is reported:

| Comparison status | Meaning |
| --- | --- |
| `ExactMatch` | No semantic or presentation difference. |
| `PresentationOnlyDifferences` | HTML or Excel output changed, or a finding carries a new display ID, and every conclusion is unchanged. |
| `ExpectedSemanticDifferences` | Conclusions changed, and a declared transition explains every change. |
| `UnexpectedSemanticRegression` | At least one change is unexplained, or a render-only run changed a conclusion. |
| `DisplayIdentityConflict` | A display ID now names a different semantic finding. This is blocking. |

### Exit codes

| Exit code | Outcome |
| --- | --- |
| 0 | `Completed` with `ExactMatch`, `PresentationOnlyDifferences` or `ExpectedSemanticDifferences` |
| 1 | `UnexpectedSemanticRegression`, including any render-only semantic difference, or expected differences with `--fail-on-semantic-change` |
| 2 | `InvalidPackage`, or invalid command arguments |
| 3 | `DisplayIdentityConflict` |
| 4 | `FailedToExecute` |
| 5 | `IncompatiblePackage` |

`scenarios` exits 0 when every scenario passes and 1 otherwise.

## Expected and unexpected semantic changes

A semantic difference is expected only when an entry in
[tools/semantic_transitions.json](../tools/semantic_transitions.json) explains it. A difference
is never expected just because the current code produced it. Each entry declares:
- its kind: a changed methodology rule, a changed taxonomy rule, a documented control
  requirement, a corrected evidence interpretation, a changed required-control flag, or a
  declared compatibility transition;
- the reason, and the rule or code path responsible;
- the commit that introduced it, and optionally the effective methodology and taxonomy versions;
- exact matches: scope, field, previous values and current values, optionally limited to an
  ID, a control, or a condition on the current finding.

An entry applies only when the current source contains the introducing commit and the retained
package's producing commit does not, as checked with `git merge-base --is-ancestor`. A match with
no previous value must be `previously_absent`. That is verified from the retained result itself:
the field or section must not exist there.

Some differences are always unexpected, whatever the register says:
- a changed evaluation date;
- an observation date later than the retained collection;
- an unresolved identity relationship;
- any difference in render-only mode.

A changed summary count is expected only when every underlying change is expected.

Each expected change records:
- the previous and current values;
- the affected control or finding;
- the source evidence references;
- the original, introducing and current commits;
- the methodology and taxonomy versions;
- the reason, and the rule or code path.

## Display identity

`PersistentFindingId` is the lifecycle identity. Display IDs (`ENT-006`, `REC-…`) are
customer-facing navigation aliases. Findings are paired across the two results by:
1. `PersistentFindingId`;
2. semantic key: the production grouping identity (`finding_reconciliation.finding_key`) when the
   finding has a `FindingKey`, otherwise the production fingerprint and scope. Where several
   findings share a key, the evidence record qualifies it;
3. current display ID, but only when the semantic key is also equal. A display ID alone never
   pairs two findings;
4. previous display IDs and compatibility aliases (`CompatibilityRecommendationIds`).

Each display-ID change is classified:
- **DisplayIdMigration:** the same finding has a new display ID. This is a warning, because no
  existing contract requires stable customer navigation. The comparison records
  `PreviousDisplayIds`, `CurrentDisplayId`, `PersistentFindingId`, `SemanticKey`, `MatchedBy`,
  `ChangeReason`, `EffectiveMethodologyVersion` and `EffectiveTaxonomyVersion`.
- **DisplayIdReuseConflict:** the same display ID refers to different semantic findings. This
  blocks the comparison (exit 3) until the ID assignment is corrected. An explained removal or
  addition does not resolve it.
- **DisplayIdRetired:** a previous display ID has no current finding, and its semantic finding is
  no longer reported. The removal itself is expected only if a transition explains it.
- **DisplayIdAdded:** a new display ID represents a genuinely new semantic finding.

A relationship that cannot be established, for example two indistinguishable findings under new
IDs, stays **unresolved**. No alias is invented.

Contract limitations:
- `PersistentFindingId` is assessment-scoped. A Standalone rebuild allocates a new AssessmentId,
  so rebuilt findings are paired by semantic key.
- The result contract records within-run compatibility aliases, but not display IDs from an
  earlier run. Migrations are reported in the comparison only; completed packages are never
  rewritten.

## Comparison output

`comparison.md` starts with a summary:
- execution and comparison status, and the exit code;
- counts of expected changes, unexpected regressions and presentation-only differences;
- display-ID migrations, reuse conflicts, retired, added and unresolved IDs;
- findings added and removed;
- whether tenant access was performed, and whether the source package is unchanged.

Technical detail follows:
- the source package, its hash, and the original and current producing commits;
- methodology and taxonomy versions;
- reuse conflicts, unexpected regressions, and expected changes grouped by transition;
- display-ID changes, changed controls and presentation differences;
- which transitions applied.

Markdown lists show at most 25 items per group. `comparison.json` keeps every item, so display
limits never change counts.

Presentation fingerprints ignore timestamps, commit IDs and run-scoped identifiers (`RUN-`,
`AST-`, `PGA-` and similar digests). They also ignore row order within a workbook sheet, because
some sheets are ordered by run-scoped identifiers. A layout change that only reorders rows is
therefore not detected.

## Concurrency and temporary outputs

Each `rebuild` and `render` run writes to its own new folder, named `assessment-validation-*` in
the system temporary folder. An explicit `--output` folder must be new or empty. Workbooks and
files are closed before the run returns, so the folder can be deleted straight away. The tool
never retries a locked file.

A rebuild changes the process working directory, so run one rebuild per process at a time. Run
the final full regression suite and any retained-package rebuild sequentially, not
concurrently. Concurrent runs share the system temporary folder and the source gate's checkout
state, so they can contend for files.

# Reproducible assessment execution

New CLI executions record the producing commit and tree, normalized public
repository identifier, branch/upstream divergence, source state, configuration
hashes, runtime versions and generation timestamp. Remote credentials and local
paths are omitted. Methodology and taxonomy use their existing versions; the
internal catalog retains its existing digest. A benchmark registry is not
implemented. Importers without a declared version retain `null` and a separate
implementation hash; no semantic version is invented.

Run configuration hashes cover effective non-secret options and input selection
counts. They exclude physical evidence paths so an unchanged portable package
retains the same configuration. Existing package input hashes bind evidence bytes;
the configuration hash alone is not an evidence-integrity guarantee.
Repeated directory/CSV selections are counted after byte-hash deduplication,
including a restored packaged copy and an explicitly repeated original export.

## Read-only plan

```powershell
python -B main.py --plan --run-type Initial --tenant-id <tenant-guid>
python -B main.py --plan --plan-format json --run-type Reassessment `
  --tenant-id <tenant-guid> --assessment-id <AST-id> `
  --assessment-history <history.json> --baseline-run-id <RUN-id>
python -B main.py --plan --mode offline --collection-input <collection.json>
```

`--plan` reads Git and selected history/snapshot metadata. It does not authenticate,
collect, parse portal imports, allocate identities, create packages or write
history. Exit status is 0 for an executable plan, 2 for a blocked plan. The JSON
is sorted and stable except for generation times. Operator output destinations
are visible in the plan; do not distribute a plan containing private paths.
Live planning requires an explicit tenant selection; it does not load credentials
from an environment file or resolve a tenant through authentication.

Initial creates an assessment and run after preflight, rejects existing history
and baseline selection, and performs no delta. Standalone has no implicit
baseline; a supplied assessment requires its owned history. Reassessment requires
an exact completed baseline RunId and valid assessment/environment ownership and
snapshot hash. Delta defaults to enabled, or `--delta-mode disabled` explicitly
omits calculation. The plan predicts delta execution; completion still evaluates
comparability and computes delta before immutable snapshot persistence. Legacy
intent remains explicitly unclassified. No latest-baseline selection is added.

Snapshot replay and a completed saved package retain identities, conclusions,
lifecycle and original producing provenance. They do not recalculate delta or
append assessment history. The plan validates the stored snapshot and shows its
retained assessment and run identities separately from current tool provenance.

## Publication policy

The CLI defaults to `--source-policy production`. Customer publication requires
`CleanCommitted` source with identifiable commit/tree and a valid plan. Classified
local product changes are `DirtyReproducible`; unknown, unreadable, conflicted,
symlinked or unsafe material is `DirtyUnclassified`. Detached/unowned checkouts and
unavailable Git metadata are blocked for new semantic execution.

Controlled validation supports:

```powershell
python -B main.py --run-type Initial --tenant-id <tenant-guid> `
  --source-policy engineering-validation --assessment-purpose "Engineering validation" `
  --source-reason "Validate representative source facts" --acknowledge-dirty-source
```

This permits only clean or classified, fingerprinted source. Purpose, reason,
acknowledgment and timestamp are recorded. The option does not establish actor
authority, governance approval or permission to bypass an unknown source state.
Do not put secrets or private paths in free-text annotations. Engineering outputs
remain explicitly **NOT CUSTOMER-READY** in both HTML reports and workbook guides,
with full qualification in snapshots, recipes, manifests and operator receipts.
Keep CSV/other extracted records with the qualified package; they are data views,
not independent publication authorization. Source is checked again immediately
before run completion and rendering; changes since preflight block publication.

Ordinary library tests do not query Git implicitly. Missing programmatic or
historical provenance is explicitly unverified rather than attributed to the
current checkout. The synthetic report validator constructs only invented input,
records the real checkout state and marks outputs fictional and not customer-ready;
its internal fixture context is not a customer CLI override.

## Fingerprint and preservation

`sha256-source-state-v1` sorts paths/statuses and hashes current source bytes,
HEAD/index blob identities, modes and rename origins. It covers staged and
unstaged tracked changes and source-like untracked files. It excludes timestamps,
ignored caches and ignored customer artifacts. Unknown untracked material remains
a policy blocker even when its bytes are excluded from the source fingerprint.
The fingerprint supplements the commit; it is not a retained copy of dirty source
and does not prove that an independent operator can reconstruct it. Preserve the
matching changes externally when using engineering validation.

New snapshots and completed-run manifests agree on producing provenance. Snapshot
hashes include that metadata. Replay adds separate `replay_tool_revision` and a
`render-provenance.json` receipt to new numbered derivative outputs. Older packages
remain readable with explicit unrecorded-source qualification. A commit difference
alone does not change technical delta; recorded taxonomy/adapter semantics qualify
comparisons through existing comparability rules.

`/AssessmentHistories/` is the existing generated history directory and is ignored
only at the repository root. This rule does not hide source JSON or documentation.
Inventory helpers never stage, restore, move or delete entries. Customer evidence,
local environments and uncertain work must be preserved independently; cleanup
requires a proven disposition and is not a run-preflight side effect.

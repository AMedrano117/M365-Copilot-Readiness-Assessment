# Reproducible assessment execution

New CLI executions record the producing commit and tree, normalized public
repository identifier, branch/upstream divergence, source state, configuration
hashes, runtime versions and generation timestamp. Remote credentials and local
paths are omitted. Methodology and taxonomy use their existing versions; the
internal catalog retains its existing digest. A benchmark registry is not
implemented. Importers without a declared version retain `null` and a separate
implementation hash; no semantic version is invented.

Run configuration hashes cover effective assessment date, freshness limits,
operator-confirmed lifecycle dates, authentication method and settings, selected
and recorded collectors, import selections, source windows, and policy versions.
Planning and execution share the same read-only settings resolver. Hashes exclude
secrets and physical evidence/output paths so an unchanged portable package
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
Live planning reads the selected environment file using the same parser as
execution, without changing process environment variables or authenticating.
It exposes no credential values. An explicit tenant overrides environment defaults.
Saved-collection tenant conflicts and empty offline executions block the plan.
Effective settings, import counts, source policy and both configuration hashes are
shown in human and JSON plans. Existing explicit output destinations are rejected
before identity allocation, then checked again by the writers.

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
CSV and JSON extracts and each HTML evidence page also retain qualification.
Replay warnings describe both the original producer and the current replay tool.
Source is checked again at every publication boundary; changes since preflight
block publication, including direct library calls and compatibility wrappers.
Repeated checks reuse Git index/flag queries only while controlling metadata bytes
remain unchanged. HEAD, tree, remote ownership and status are queried again;
source bytes and loaded bindings are always inspected. This uses memory only.

Pure library assessment builders do not query Git implicitly. Publishing library
callers must establish a captured producing revision with a valid publication
policy; missing context blocks before opening files or allocating directories.
Historical provenance with missing fields, malformed values or unsupported
versions remains readable and explicitly unverified. It is never attributed to
the current checkout. Tests mock repository verification inside declared fictional
contexts; there is no test policy accepted by production. The synthetic report
validator uses the real engineering policy with invented evidence.
CI checks out the exact event/PR head and binds it to a local validation branch.
The detached-source rule remains enforced. The CI budget includes source checks
at every publication boundary in all three synthetic report audits.

New reports, snapshots, manifests and derivative receipts use exclusive creation.
Existing paths, normalized aliases, symbolic links and Windows reparse points are
rejected. Creation uses a pinned directory handle on Windows and POSIX. Completed
packages accept new numbered derivative builds, renders and recipes; original
snapshots and input files remain immutable. Owned raw collection checkpoints,
append-only history/governance metadata, legacy recipe pointers and operator logs
retain their specific update contracts. They cannot replace a semantic snapshot,
change ownership or rewrite prior history entries. Governance CLI publications use
the same source policy; actor authority remains independently required.

## Fingerprint and preservation

`sha256-source-state-v2` sorts paths/statuses and hashes current source bytes,
HEAD/index blob identities, normalized executable modes and rename origins.
Product paths are inspected independently of Git status and ignore rules. Hidden
loadable source is included; assume-unchanged/skip-worktree flags, nested source
repositories, symlinks, inaccessible source and foreign/stale loaded product code
block qualification. It excludes timestamps, customer evidence, generated reports,
caches and preserved archives. Unknown untracked material remains
a policy blocker even when its bytes are excluded from the source fingerprint.
The fingerprint supplements the commit; it is not a retained copy of dirty source
and does not prove that an independent operator can reconstruct it. Preserve the
matching changes externally when using engineering validation.

New snapshots and completed-run manifests agree on producing provenance. Snapshot
hashes include that metadata. Replay adds separate `replay_tool_revision` and a
`render-provenance.json` receipt to new numbered derivative outputs. Older packages
remain readable with explicit unrecorded-source qualification. A commit difference
alone does not change technical delta. Established adapter version changes and
taxonomy changes qualify comparisons through existing comparability rules.
Implementation-byte changes carry a separate review qualification and do not
prove a semantic change.

`/AssessmentHistories/` is the existing generated history directory and is ignored
only at the repository root. This rule does not hide source JSON or documentation.
Inventory helpers never stage, restore, move or delete entries. Customer evidence,
local environments and uncertain work must be preserved independently; cleanup
requires a proven disposition and is not a run-preflight side effect.

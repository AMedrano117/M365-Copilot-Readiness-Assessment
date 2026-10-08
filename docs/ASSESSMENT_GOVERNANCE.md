# Explicit assessment governance

Governance records human decisions separately from current technical status,
evidence-derived lifecycle state, recommendation priority and customer action
status. `ResolvedByCurrentEvidence` never creates formal closure. Accepted risk
does not remediate a condition, exceptions apply only to their exact recorded
population and scope, and an applicability decision does not count as remediation.

This is additive to the shared runtime result. The assessment templates and
[canonical contract](assessment-templates/schemas/assessment-results.schema.json)
remain the migration target; this change does not claim target-contract compliance.

## Modules and ownership

- `Core/governance.py`: `new_log`, `create_draft`, `transition`, `project`,
  `effective_for` and `attach`. These return new values; they never alter source
  snapshots, baselines, raw evidence or lifecycle calculations.
- `Core/governance_contract.py`: required decision fields, compact retained run
  context, scope validation, expiration boundaries and current-run qualifications.
- `Core/governance_authority.py`: `authorize` checks configured actor/role grants.
- `Core/governance_validation.py`: semantic publication diagnostics and unresolved
  legacy context, with no silent repair.
- `Core/governance_history.py`: atomic, locked, compare-and-swap log updates and
  explicit additive assessment-history reference revisions.
- `Core/governance_cli.py`: noninteractive local operations, without tenant access.
- `Core/governance_presentation.py`: common recorded projections for both workbook
  and HTML renderers; no decision transitions or approval authority.

Decision IDs use `GOV-` plus a persisted UUID; each event has a `GVE-` UUID.
They are separate registers, not new finding identities or identity-schema changes.
Targets must resolve to persistent PFI, PCT or PAC entities of the same assessment
and environment. Display IDs, workbook rows, filenames and anchors never establish
decision ownership. PAC targets require persistent finding relationships.

## Decisions, states and workflow

Primary treatment types are `ClosedByRemediation`, `AcceptedRisk`,
`ApprovedException` and `NoLongerApplicable`. Terminal events explicitly record
`DecisionRevoked`, `DecisionExpired` and `ClosureReopened`, referencing the stable
original DecisionId and retaining its treatment type and complete approval history.
Event type and decision workflow state are separate fields.

Workflow states are Open (draft), PendingReview, Approved, Active, Rejected,
Revoked, Expired, Superseded and Reopened. Draft creation, submission, approval
and activation are distinct operations. PendingReview and Approved alone have no
active treatment effect. Rejected and terminal records remain in the log.

Only the draft author can amend or submit an Open decision. Amendment records
evidence or authority changes as another event, without replacing prior records.
After submission, material changes require a new draft and explicit supersession.
Review records actor, time and reason but cannot silently renew or approve.
Revocation, expiration and reopening require an explicit reason and authority.
Reopening records the original closure, current RunId and current persistent
evidence references; a renamed display ID has no effect on continuity. This stage
requires the same persistent target, rather than guessing ambiguous successors.

Supersession atomically activates an approved replacement and removes the earlier
effect for the exact same target and scope. Both decisions remain retained.
Overlapping active decisions for an exact target/population/scope/resource set
produce a blocking conflict. No methodology currently permits combined active
risk acceptance, exception, applicability and closure at that exact boundary.
Resource-set order does not alter scope equality.

Historical active effect and effect for the current run are separate projection
fields. Missing targets, changed action-to-finding relationships and incompatible
scope have no current effect and require review. Contradictory later closure
evidence (including Reopened or missing reassessment proof) requires explicit
review and has no current closed effect; it does not create a reopening event.

## Explicit authority policy

Default behavior denies every approval. Configure a trusted local JSON policy
with SchemaVersion `1.0.0`, PolicyId, Version, AssessmentId, PrimaryEnvironmentId,
an Actors mapping from actor IDs to role names, and a Roles mapping. Each role
must explicitly list DecisionTypes and Operations it may perform. Roles are
configurable: naming one `risk-owner` gives it no authority by itself.

Example grant shape (supply actual approved assessment ownership separately):

```json
{
  "Actors": {"approved-reviewer-id": ["risk-authority"]},
  "Roles": {
    "risk-authority": {
      "DecisionTypes": ["AcceptedRisk"],
      "Operations": ["approve", "activate", "reject", "revoke", "expire", "supersede"],
      "AllowNoCompensatingControls": false,
      "AllowClosureOverride": false,
      "AllowScreenshots": false
    }
  }
}
```

The actor supplied for approval must equal DecisionAuthority, hold AuthorityRole,
and have the exact decision/operation grant. Operator identity, ownership of a
workbook, authorship of a file or suggested responsible role conveys no approval
authority. Every privileged event retains the exact policy used at that time.
Later policy edits do not rewrite earlier grants; revoke old decisions explicitly.

Policy files are an explicit trust boundary, not authentication or signatures.
Protect them with the organization's normal access controls. SHA-256 chains
detect unresealed corruption; they cannot authenticate an actor or resist someone
authorized to replace and reseal the entire policy and log. Electronic signatures,
identity-provider integration and approver discovery are deferred.

## Decision and evidence contract

Structured drafts accept target ID/type, decision type, accountable owner,
authority/role, rationale, population, resource scope, effective/review/expiration
dates, explicit expiration policy, conditions, residual risk, compensating controls,
reviewed evidence references, supporting artifact references, validation result,
evaluated closure criteria and applicability rule where required. Schema and
methodology versions, originating RunId/BaselineRunId, actor/time and identities
are recorded at the workflow boundary. Do not enter credentials or raw payloads.

Each evidence reference requires a retained PEV ID, AssessmentId, RunId, portable
SourceLocator, timezone-qualified ObservedAt, Population, ResourceScope,
Period `{Start, End}`, ObservedOutcome, Exceptions (explicit list), AccountableOwner,
Qualification and Interpretation. Evidence must have a retained relationship to
the target or its linked findings. Supporting artifacts reference retained PFL IDs
and relative locators. Neither arbitrary display IDs nor an unbound file alone
constitutes evidence. Source metadata is retained without duplicating raw records.

Closure requires sufficient reviewed evidence, an explicit validation result and
confirmation that closure criteria were evaluated. Normally its target and linked
findings must be ResolvedByCurrentEvidence. A conditional override additionally
requires residual risk, conditions, rationale, authority and review date, plus a
role grant permitting overrides. Control resolution alone does not close an
unproven finding. Missing-current targets must be reviewed using their retained
semantic run; this stage does not invent a new target from a disappearance.

Every `ClosureOverride=true` record requires this complete justification, even
when the target is already `ResolvedByCurrentEvidence`. Use `ClosureOverride=false`
for normal evidence-supported closure; override-only residual risk, conditions
and review date are not required on that path. The flag must be a Boolean.
Required `ResidualRisk` and `ValidationResult` statements must be nonblank strings;
Booleans, numbers, nulls and collections are rejected without string conversion.
New draft and amendment inputs trim surrounding whitespace in these two fields.
Retained event text and hashes are not normalized during replay.

Incomplete drafts remain available for inspection and amendment, but submission,
approval and activation enforce the complete requirements. Previously accepted
invalid closure overrides produce blocking semantic diagnostics during replay,
snapshot loading, serialization and rendering. No invalid history is silently
repaired or rewritten. Retain such history for explicit review; this correction
does not provide a migration or repair mechanism for invalid historical approvals.
Policy configuration does not authenticate the supplied actor.

Risk acceptance requires a risk owner, rationale, residual risk, supporting
evidence, exact scope, conditions and review or expiration. Exceptions additionally
record explicit parent population/scope when excluding a subset of a finding;
`ResourceIds` can retain exact excluded identifiers. If a target declares affected
resource IDs, exclusions must stay within them. Neither a subset exception nor
`effective_for` extends its approval to an unrecorded broader scope. Compensating
controls can be omitted only under explicit policy permission with a rationale.
Applicability requires an explicit business or methodology rule, scope, authority,
owner, date, rationale and supporting evidence. Missing collection cannot supply it.

Screenshots require all reviewed metadata plus SourceType `screenshot`, an explicit
MethodologyRule and policy permission. Validation checks identity, relationships,
metadata and integrity, without fetching evidence or deciding whether a human
interpretation is factually correct. Retained source files must remain available
under the recorded portable locators for independent review.

## Dates and expiration

Every event and projection uses an explicit timezone-qualified time. Events are
append-ordered; a projection cannot precede recorded events. Future-effective
approvals have no active effect yet. An explicit hard ExpirationAt always removes
active effect once reached, even without an expiration event. ReviewAt produces
a visible review requirement; it expires effect only with `expire_on_review`.
`review_only` does not silently renew, close or expire a decision. Record
`expire_on_expiration` or `expire_on_review` with its corresponding date.

An explicit `expire` operation appends DecisionExpired, returns treatment to
PendingReview and preserves technical and lifecycle history. Read-only expiration
qualification is labeled ExpirationDue, never fabricated as a recorded event.
Rendering uses the persisted AsOf, not the wall clock. Produce a new explicit
overlay to inspect later expiry, and record expiration with an authorized command.

## CLI and portable overlays

Use `python -B -m Core.governance_cli --help`. The module is intentionally separate
from collection/report commands, so a renderer has no approval options.

```powershell
python -B -m Core.governance_cli draft --snapshot package/snapshot.json --log package/governance/decisions.json --input draft.json --actor operator-id --at 2026-10-08T12:00:00Z
python -B -m Core.governance_cli validate --snapshot package/snapshot.json --log package/governance/decisions.json --decision-id GOV-ID --at 2026-10-08T12:01:00Z
python -B -m Core.governance_cli submit --snapshot package/snapshot.json --log package/governance/decisions.json --decision-id GOV-ID --actor operator-id --at 2026-10-08T12:02:00Z
python -B -m Core.governance_cli approve --snapshot package/snapshot.json --log package/governance/decisions.json --decision-id GOV-ID --actor reviewer-id --policy authority.json --at 2026-10-08T12:03:00Z
python -B -m Core.governance_cli activate --snapshot package/snapshot.json --log package/governance/decisions.json --decision-id GOV-ID --actor reviewer-id --policy authority.json --at 2026-10-08T12:04:00Z
python -B -m Core.governance_cli attach --snapshot package/snapshot.json --log package/governance/decisions.json --output package/governance-view.json --at 2026-10-08T12:05:00Z
python main.py --replay-snapshot package/governance-view.json
```

Replace GOV-ID with the persisted ID returned by draft creation. `amend`, `reject`,
`revoke`, `supersede`, `expire`, `reopen` and `review` take structured JSON input.
Rejection/revocation/expiration/review require Reason; supersession also requires
SupersededDecisionId; reopening requires ResultingState (Open or PendingReview)
and current evidence references when using a later run. `review-expired` lists
review requirements without changing anything. `export --output NEW.json` writes
a register. There is no bulk approval command or approval by status editing.

Keep the log and overlay within the portable package tree. LogReference is relative
to the overlay's directory; source evidence locators are relative to the retained
package root. Locators cannot escape through absolute paths or parent traversal.
New overlays serialize the validated log plus compact treatment views; completed
run snapshots and assessment-run manifests remain unchanged. Replay the overlay
explicitly: loading an original completed package still renders its original run,
without implicitly loading newer governance or changing its package integrity.

`link-history --history assessment-history.json` explicitly appends a top-level
GovernanceReferences revision and current Governance pointer. History schema
remains 1.0.0; all Runs, run snapshot hashes and conclusions remain unchanged.
Log and history updates are individually atomic, not one distributed transaction.
If linking fails after a successful event, retry only the link. A history reference
is a recorded log revision, not an assertion that a subsequently edited log is
automatically synchronized. Future run preparation still uses the sealed history
compare-and-swap guard; an intervening governance link requires an explicit retry.

## Validation, compatibility and outputs

Blocking diagnostics cover malformed records, dangling/foreign/type-invalid
targets and evidence, dates, missing fields, unauthorized grants, insufficient
closure support, unaudited state changes, conflicts and integrity mismatches.
Legacy display values produce unresolved_legacy_governance_reference diagnostics;
unsafe qualified overlays remain blocking. Contradictory later closure evidence
produces a warning requiring explicit review. Existing legacy values remain
readable without destructive migration or invented approval. A reviewed legacy
decision can be entered through the same draft/approval workflow, preserving
explicit source provenance rather than automatically upgrading old text.

Snapshots retain governance_diagnostics separately from lifecycle and identity
diagnostics. Summary counts reconcile to detailed records and separately count
workflow states, treatment types, historical/current effects, pending, expired,
reopened and unresolved legacy context. Current finding and action populations
are separate; decision counts are not a readiness score.

Both workbooks gain Governance and Decision Register when there is governance
context. The technical workbook adds Decision Audit. Finding and action-plan
views show qualified treatment, scope, review, evidence and remaining work without
removing technical actions. The HTML report and readiness summary share the same
concise governance projection; the technical appendix retains the complete audit.
No dashboard, App Builder or retired finding UID feature returns.

The separate governance tests cover workflow, authority, semantics, serialization,
portable movement, outputs and guarded CLI behavior. Use the repository guarded
runner: strict for pure tests and allow-temp for test_governance_outputs.
Earlier semantic, identity, reconciliation, delta, run, package and renderer suites
remain unchanged. Raw tenant collection is unnecessary for these checks.

Deferred: notifications, tickets, signatures, customer authority setup, automatic
approval or approver discovery, canonical-contract migration, renderer consolidation,
reusable deliverable alignment and selective retrieval/scale work. Audit logs grow
with decision events; overlay copies include the complete governance log for
self-contained validation, but do not duplicate raw source evidence or place
decision history into every original run snapshot.

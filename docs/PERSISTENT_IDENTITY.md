# Persistent identity and semantic references

The [scope reconciliation stage](SCOPE_RECONCILIATION.md) documents the reproduced
POB/PFI collisions, narrow boundary extensions, compatibility aliases and source
occurrence validation.

[Documentation index](README.md) | [Supported outputs](OUTPUTS.md) | [Methodology](METHODOLOGY.md)

Identity schema `1.0.0` is an additive internal boundary. The current methodology,
evidence precedence, customer decisions and renderer locators remain authoritative.
The assessment templates, target schema and synthetic target example remain future
direction; this change does not migrate the production result to that schema.

## Execution identity

`Core/assessment_identity.py::new_identity` requires a verified, nonzero Microsoft
365 tenant GUID, methodology version and evaluation date/time. It generates
`AST-` (assessment) and `RUN-` (execution) UUIDs once and records creation time.
`ENV-` is a full SHA-256 digest of the verified tenant in the
`microsoft-365-tenant` namespace. Names, paths, report titles and output formats do
not establish engagement or environment equivalence. Two new engagements in the
same environment receive different assessment IDs.

`new_run` explicitly preserves an existing assessment only for its verified
primary environment and creates a new run. It is a library primitive, not a CLI
reassessment workflow. `RunType` and `BaselineRunId` remain null. No baseline is
selected and no run is automatically classified as initial, reassessment or delta.
The explicit [assessment-run workflow](ASSESSMENT_RUNS.md) builds on these primitives
and records RunType, selected baseline, history and comparison eligibility. Primitive
callers and legacy packages retain the unclassified compatibility path.

Metadata lives in the shared result's `identity` block:

| Field | Meaning |
|---|---|
| `SchemaVersion`, `State` | Identity contract version and complete/incomplete/legacy state |
| `AssessmentId`, `RunId`, `PrimaryEnvironmentId` | Engagement, execution and verified environment |
| `MethodologyVersion`, `CatalogVersion` | Execution methodology; independent catalog version when explicitly known |
| `EvaluatedAt`, `CreatedAt` | Original evaluation date/time and distinct execution creation timestamp |
| `RunType`, `BaselineRunId` | Unset placeholders, not lifecycle decisions |
| `Entities`, `Aliases`, `References` | Additive semantic registers |

New saved collections persist an execution seed before rendering. Checkpoints
reuse it through the existing checkpoint package. `collection_context` transports
it into the shared builder. The offline entry point creates one seed for a first legacy or
portal-only evaluation with a verified environment. Successful rebuild recipes
retain the seed via `execution_metadata`, excluding derived registers; replay
rebuilds those registers from retained evidence and preserves both IDs. Existing
receipt/build-folder run identifiers remain independent artifact locators.

A builder/processor invocation without a persisted execution seed remains **incomplete**:
it cannot establish an engagement merely from tenant evidence. Unverified tenant
labels likewise remain unbound. Production collection/report entry points supply
the seed when the primary environment is verified at that entry point; consumers invoking the builder directly must explicitly supply it in
`bundle.collection_context.identity`. The builder is still pure and does not
modify input evidence or mint a new random run during rendering.

Changing the evaluation date or effective methodology while replaying an existing
run produces a warning or compatibility warning. Original identity metadata is
retained. Deciding whether that operation creates a new run, and control-version
compatibility, requires the later workflow design.

## Deterministic semantic boundaries

`semantic_id` hashes canonical JSON with the identity schema, entity type and
explicit boundary using full SHA-256. Required values cannot be missing,
ambiguous markers, booleans or non-string identifiers. Structured population,
scope and window declarations are allowed. Dictionary order and unrelated list
insertions are irrelevant. Mutable wording, severity, display RecommendationId,
selection, owner, dates, status and action membership are not identity inputs.

| Type / prefix | Required semantic boundary |
|---|---|
| Control / `PCT-` | Registered namespace and authoritative base control code |
| Observation / `POB-` | Assessment, run, environment, provider, control, metric, population, resource scope, window, capture and evidence level |
| Finding / `PFI-` | Assessment, environment, provider, control, condition key, population and resource scope |
| Recommendation catalog / `PRC-` | Declared catalog namespace and item key |
| Customer action / `PAC-` | Assessment, environment and independently declared customer work-item key |
| Dataset / `PDS-` | Environment, provider, workload and dataset key |
| Capture / `PCP-` | Dataset, full capture timestamp and capture key |
| Retained source file / `PFL-` | Environment and retained file SHA-256 |
| Native record / `PNR-` | Namespaced dataset and native record ID |
| Normalized evidence record / `PEV-` | Capture, stable record key and content digest |
| Selected support / `PSR-` | Finding, evidence record, selection role and selector version |

Applicable optional dimensions include product/tier, metric definition, unit,
reporting basis, native record, and dataset population/scope. Explicit affected
resource IDs are treated as a set. Capture records without a declared capture key
can use the canonical, sorted retained-record digests with their timestamp and
dataset boundary. That identifies retained capture content, not cross-run closure.
No observation is merged. Repeated observations remain records and duplicate
persistent IDs are diagnosed.

Controls preserve their original code and carry `DefinitionVersion` separately.
Wording-only changes do not alter their base identity. Catalog recommendation and
action identities are attached only when producers supply
`RecommendationCatalogNamespace`/`RecommendationCatalogKey` and
`CustomerActionKey`. Legacy display IDs and recommendation text cannot substitute
for those declarations.

`build_assessment_result` calls `attach_identity` once after catalog attachment.
The layer attaches controls, sufficiently scoped facts/findings/source records,
selected-support relationships and explicit producer declarations. Producers may
also supply `identity_entities`, `identity_aliases` and `identity_references`;
those declarations are retained and validated, not repaired or deduplicated.
Foreign source tenants remain distinct in boundaries and trigger blocking
cross-environment diagnostics. Missing provider, population, scope or capture
inputs leave legacy references unresolved. This is expected for current producers
that do not yet declare the future semantic boundary; it does not weaken existing
evidence qualification or turn missing evidence into favorable assurance.

Hash collisions compare entity type, identity key **and actual boundary** in
`IdentityRegistry`. A conflicting hash raises `IdentityCollisionError`; incomplete
legacy inputs raise `MissingIdentityInputs`. Only the latter permits an unresolved
compatibility projection. Neither overwrites an existing semantic entity.

## Typed compatibility aliases

Existing `RecommendationId`, control codes, `EV-`, `SRC-`, `EVD-`, `DET-`, `ROW-`,
`record-` and native references retain their algorithms and values. The neutral
selection model now additionally retains `compatibility_record_id` before assigning
the existing detail locator. No renderer constructs persistent IDs.

Aliases contain `Value`, `Namespace`, `TargetType`, `AssessmentId`, `OriginRunId`,
`OriginArtifact` and nullable `TargetId`. Separate RecommendationId namespaces
(`RecommendationId:issue`, `:catalog`, `:action`) distinguish legacy purposes.
Other namespaces include `control_id`, `EV`, `SRC`, `EVD`, `DET`, `ROW`, `record`,
`source_file` and `native:<dataset>`. Origin artifacts distinguish row occurrences
without turning row position into semantic identity.

`resolve_alias` requires the complete typed context and exactly one owned target.
It never returns the first of multiple candidates. Multiple-source detail records
are not arbitrarily bound to one support relationship. Unresolved targets remain
null with diagnostics, while the original rows and locators remain available.

## Validation and serialization

`Core/assessment_references.py::validate_assessment_references` returns sorted,
structured `{severity, code, subject, message}` diagnostics without modifying its
input. Checks include duplicate IDs and cross-type reuse; type/prefix/semantic-key
consistency; required ownership; dangling or ambiguous owners/targets; declared
relationship types; implicit boundary dependencies; registered controls; alias
context, duplicates, ambiguity and missing targets; cross-assessment/environment/
run ownership; display-only persistent IDs; and prohibited retired identity fields.

`require_valid_assessment` blocks on `error`. The processor calls it before retained
renderers publish output; the serializer calls it before creating the destination
directory/file. Errors do not drop records, repair references or change readiness.
`warning`, `compatibility_warning` and `unresolved_legacy_reference` are non-blocking
and persist in `identity_validation`. Duplicate entirely unbound legacy alias
occurrences are compatibility warnings; duplicate/ambiguous bound semantic alias
contexts are errors.

`write_assessment_result` continues to serialize the sanitized shared result
directly. It recomputes diagnostics without replacing identities or modifying the
input. `read_assessment_result` loads without raw evidence, preserves fields and
IDs, and validates. Old snapshots receive a legacy block with null engagement,
run, environment and lifecycle fields in memory plus compatibility diagnostics;
the original file remains untouched. Historical snapshot imports retain this
block without using it as the current execution identity or selecting a baseline.

Full original observation timestamps survive as `source_observed_at`, added after
the compatibility `EV-` hash. The existing `observed_at` date and selection rules
remain unchanged. Original collection timestamps remain intact in saved evidence.

## Safe validation and deferred work

Use `python -B tests/run_guarded_stage1.py --allow-temp --quiet test_persistent_identity`
for focused unit, contract, serialization and synthetic render/replay coverage.
The existing Stage 1 tests are unchanged. The guarded runner prohibits tenant
access, subprocesses and writes outside a new temporary fixture directory.

The broad safe suite excludes the existing 97 subprocess tests and five retained
cache-write tests; exclusions are not skips and overlapping group totals must not
be added. Existing Excel, HTML, summary, selected-record, package integrity and
retirement tests remain required.

Later work must supply missing producer boundaries before reconciliation, finding
deduplication, cross-run continuity, action history or evidence-based closure can
use these identities comprehensively. Existing positional locators, mixed-purpose
RecommendationId values and baseline comparison remain compatibility risks.
This stage does not change baseline comparison, assign lifecycle states, infer
resolution, consolidate renderer semantics, migrate the full target schema or
introduce streaming. The additive registers and diagnostics can increase snapshot
size; no broad artifact performance redesign is included.

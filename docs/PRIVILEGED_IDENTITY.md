# Privileged identity and activity interpretation

Pass B adds a recorded directory-privilege model to the existing shared result.
[METHODOLOGY.md](METHODOLOGY.md) remains authoritative. The
[templates](assessment-templates/README.md), target
[schema](assessment-templates/schemas/assessment-results.schema.json), and
[example](assessment-templates/examples/assessment-results.example.json) remain
future-state direction; this pass does not migrate the full canonical contract.

## Shared flow

1. `Core/assessment_catalog.py::collect_assessment_sources` retains records and
   original collection envelopes. Existing Graph v1.0 user selection adds
   `onPremisesSyncEnabled` and `createdDateTime`.
2. `Core/privileged_assignments.py::normalize_assignments` applies the shared
   `classify_assignment`, preserves scope and reconciles unambiguous grants.
3. `Core/privileged_identity.py::build_privileged_assessment` builds distinct
   identities, expands supported group membership and correlates account state,
   purpose, authoritative MFA registration and Pass A authentication events.
4. `evaluate_activity` applies an explicit policy. `_observations` retains
   components, qualified combinations and gaps with exact native references.
5. `Core/assessment_result.py::build_assessment_result` stores additive
   `privileged_assessment` and `privileged_diagnostics` records;
   `Core/privileged_presentation.py::privileged_findings` creates findings.
6. Excel and HTML present the stored model. `assessment_references.py::require_valid_assessment`
   includes `privileged_validation.py` in the existing publication gate.
   Validation checks recorded calculations; renderers choose no new clock or policy.

New PGA/PGI/PGV/PGO content keys identify grants, identities, observations and
occurrences within this model. They do not replace typed persistent PFI/POB/PEV
identities, RecommendationId aliases or governance targets. Grant keys preserve
assessment, environment, provider, principal/type, role, directory/application/AU
scope, grant kind, source identity, member type and temporal boundaries. Input
permutation and unrelated insertion do not change them. Matching never relies on
names or workbook row positions. Contradictory variants retain evidence and diagnostics.

Explicit foreign assessment/environment/tenant/provider sources cannot join.
Legacy unrecorded ownership remains qualified within the supplied bundle.
Failed, unavailable, partial, complete-empty and unknown sources remain distinct.
Native evidence bindings retain original locator, source, identity and safe digest.

## Temporal and population rules

The stored evaluation timestamp controls classification. Explicit offset timestamps
are preferred. The existing date-only API evaluates through the end of that UTC
day, `23:59:59.999999999Z`; this is a repository decision. Decimal comparisons retain
all returned fractional digits and original Graph timestamp strings remain unchanged.

| State | Meaning |
|---|---|
| ActivePermanent / ActiveTimeBound | Supported current active schedule, start, status and expiration |
| EligiblePermanent / EligibleTimeBound | Valid eligibility; eligibility does not establish activation |
| FuturePending / Expired | Future start or reached end; excluded from current counts |
| UnknownTemporalState | Missing, invalid, conflicting or unsupported required semantics |
| DirectActiveDurationUnverified | Direct active grant without a unique matching schedule; permanence unverified |

Schedule statuses Provisioned/Granted are supported; missing/other statuses cannot
establish active permanence. Explicit ISO day/hour/minute/second durations are
supported. End time is exclusive. Conflicting native-ID scope/time variants do not
become two confirmed active grants. Future schedules cannot suppress independent
direct active evidence. Raw memberType and activatedUsing relationships remain available.

Counts separate assignments from distinct users, and active from eligible privilege.
Activity/purpose/MFA distributions cover all retained identities; explicit active
and eligible user counts describe current populations, which can overlap.
Historical/future records do not create current activity findings. Groups, workloads
and unresolved principals remain distinct from human users. Disabled retained
active or eligible privilege requires review.

Group expansion requires a compatible, orderable latest roster at least as recent
as the grant. Partial rosters retain returned users and qualify completeness;
stale/conflicting/unavailable rosters cannot establish a complete user population.
Latest empty rosters supersede older membership. Newer empty user enumerations
cannot resurrect old account metadata. Native membership references preserve
indirect scope. No new live membership or activation collector is added.

## Authentication and activity rules

`signin_evidence.normalize_signin_event`, rule version 1.0.0, remains authoritative.
Registration, enforcement, observed MFA completion, single-factor success,
successful legacy authentication and CA-blocked attempts remain separate.
Correlation requires exact user IDs, compatible ownership, qualified timestamps,
capture and recorded window. Conflicting native event variants never select success
by order. Combinations reference only supporting events and retain their components.

Expected CA coverage requires an explicit principal/policy mapping to current,
complete retained policy evidence. `notApplied` alone does not prove a bypass.
An absent/malformed returned policy list cannot establish the comparison.

Qualified lastSuccessfulSignInDateTime and successful Pass A events can establish
activity; interactive and noninteractive success both matter. Attempt timestamps
stay separate. Failed attempts, null success, unavailable history, license/permission
limits, activity predating account creation and future timestamps do not establish
inactivity. Existing separate `user_signin_activity` records join by native user ID.
Source qualification uses the existing 35-day freshness boundary, not a default
inactivity threshold or a guarantee of continuous coverage.

## Explicit configuration and purpose

Configure `assessment_context.privileged_inactivity_policy` with `policy_id`,
`rule_version`, `methodology_reference`, positive numeric `threshold`, and
`threshold_unit: "days"`. No customer threshold is supplied by default.
Optional `privileged_evaluation_timestamp` requires an explicit timezone.

Conservative policy treatments require successful activity, include both interactive
and noninteractive use, require complete source coverage and preserve missing data
as unknown. Emergency/synchronization accounts receive separate review; service
accounts require purpose/dependency review, disabled privilege requires review,
eligible-only privilege requires activation review, and permanent privilege requires
necessity review. Weakening these treatments invalidates the policy.
`activity_fields` selects lastSuccessfulSignInDateTime and/or successful_signin_events;
`exclusions` lists explicit principal IDs. Invalid structured context is not applied.

The stored policy signature excludes the evaluation clock. Threshold/version/treatment
changes alter it and activity findings' ControlDefinitionVersion. No privileged
directional or automatic resolution metric is registered.

`account_purposes` maps principal_id, purpose (ordinary/emergency/service/synchronization),
owner, source_type and evidence_refs. Native refs contain dataset, dataset_index and
record_index, with zero-based indices. Explicit reviewed configuration, customer
declaration and account metadata can support purpose. Name patterns create candidates
only; synchronization does not prove service purpose. Conflicting purposes cannot
support ordinary inactivity.

Governance-backed declarations also require decision_id and retained evidence containing
DecisionLog. The existing log must replay with valid integrity and authority, match
assessment/environment ownership, and project an active, not-due-for-review
ApprovedException with exact ResourceIds, owner and an explicit condition
`Account purpose: emergency` (or service/synchronization/ordinary). This is a reviewed
mapping to an existing decision, not a new decision type. Labels, raw approval flags,
draft/revoked/expired decisions and forged projections are insufficient. Evaluation
and rendering create no decision events.

Emergency activity, attempts and retained account-change evidence create separate
review items. Permanent emergency access is not automatically improper. Service
and synchronization accounts do not enter ordinary inactivity cleanup; recent
noninteractive success establishes use, and interactive service use remains reviewable.
Human activity and MFA-registration rules do not classify workload principals.

## Output, validation and limits

Legacy PIM callouts preserve routes but use scope/purpose review wording. Selected
assignment keys reconcile permanent/duration-unverified worklists. Existing explicit
baseline assurances and reviewed results are preserved. Review candidates do not
assert abandonment, compromise, misuse or unjustified permanent access.

Admin Role Detail retains application/AU scope and temporal states. A restricted
Privileged Identity Review sheet adds account, purpose, activity, authentication,
policy and native evidence details. Executive HTML/readiness views use aggregates
without identity names or user IDs. Snapshot JSON retains the model; original
collections/packages remain replay inputs. A snapshot alone is not a collection recipe.

Publication rejects inconsistent temporal/source labels, keys, scopes, counts,
native refs, principal treatment, missing component evidence, invented inactivity,
unverified purpose, policy signatures and ownership. Existing lifecycle/governance
gates remain. Evidence resolution does not create ClosedByRemediation or accepted
risk. No remediation, tenant mutation, baseline rewrite, dashboard/App Builder export
or finding_uid dependency is introduced.

Finding support also requires the contributing native sources, not only the role
population, to have complete/current envelopes and retained API/file provenance.
An incomplete or unproven authentication source cannot inherit supported status
from a complete assignment population. Missing provenance is never replaced by
an invented source locator.

Remaining gaps include absent/partial group rosters, activation periods, workload
activity, account-change evidence and purpose attestations. Customers must choose
thresholds, owners, purpose evidence, expected CA scope, review periods and governance
decisions explicitly. Canonical extraction/chunking and new collectors are later
work. See [validation results](PRIVILEGED_IDENTITY_VALIDATION.md).

## Microsoft documentation checked

These document source semantics; thresholds, conservative qualifications and
date-only evaluation above are repository decisions:

- [Assignments and scopes](https://learn.microsoft.com/en-us/graph/api/resources/unifiedroleassignment?view=graph-rest-1.0),
  [active schedules](https://learn.microsoft.com/en-us/graph/api/resources/unifiedroleassignmentschedule?view=graph-rest-1.0),
  [eligibility schedules](https://learn.microsoft.com/en-us/graph/api/resources/unifiedroleeligibilityschedule?view=graph-rest-1.0),
  [instances](https://learn.microsoft.com/en-us/graph/api/resources/unifiedroleeligibilityscheduleinstance?view=graph-rest-1.0),
  [scheduleInfo](https://learn.microsoft.com/en-us/graph/api/resources/requestschedule?view=graph-rest-1.0)
  and [expiration](https://learn.microsoft.com/en-us/graph/api/resources/expirationpattern?view=graph-rest-1.0).
- [signInActivity semantics and backfill limits](https://learn.microsoft.com/en-us/graph/api/resources/signinactivity?view=graph-rest-1.0),
  [user fields/selection](https://learn.microsoft.com/en-us/graph/api/resources/user?view=graph-rest-1.0),
  [inactivity evidence and P1/P2/AuditLog.Read.All/User.Read.All requirements](https://learn.microsoft.com/en-us/entra/identity/monitoring-health/howto-manage-inactive-user-accounts).
- [Role assignment permissions](https://learn.microsoft.com/en-us/graph/api/rbacapplication-list-roleassignments?view=graph-rest-1.0),
  [active schedule permissions](https://learn.microsoft.com/en-us/graph/api/rbacapplication-list-roleassignmentschedules?view=graph-rest-1.0),
  [eligibility permissions](https://learn.microsoft.com/en-us/graph/api/rbacapplication-list-roleeligibilityschedules?view=graph-rest-1.0),
  [group membership limitations/permissions](https://learn.microsoft.com/en-us/graph/api/group-list-members?view=graph-rest-1.0).
- [PIM activation/eligibility, workload and licensing boundaries](https://learn.microsoft.com/en-us/entra/id-governance/privileged-identity-management/pim-deployment-plan),
  [emergency-access design and monitoring](https://learn.microsoft.com/en-us/entra/identity/role-based-access-control/security-emergency-access).

Existing supported Graph v1.0 collectors and permissions remain; no beta API becomes
a required dependency and no additional permission is granted.

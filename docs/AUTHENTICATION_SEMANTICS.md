# Authentication semantics and collection correctness (Pass A)

[Methodology](METHODOLOGY.md) | [MFA methods](MFA_METHODS.md) | [Documentation index](README.md)

This pass corrects authentication interpretation in the existing shared assessment
model. It does not migrate the runtime model to the future
[assessment-results contract](assessment-templates/templates/08-assessment-results-data-contract.md)
or change identity, reassessment, lifecycle, governance or closure authority.

## Collection boundary

`Core.graph_collection.collection_page` validates both Graph readers' collection
envelopes. HTTP 200 alone cannot establish a completed query. A usable envelope
requires an object, a `value` array of record objects, no error envelope, and a
valid continuation when present. Continuations retain their opaque query; absolute
links must use Microsoft Graph HTTPS, and root-relative links resolve against the
fixed Graph origin. Foreign origins, protocol-relative links, credentials, fragments,
unexpected ports and malformed links are rejected.

Readers retain earlier valid pages after malformed JSON, invalid envelopes, HTTP
or transport failures. Such results are unavailable before any usable page, or
partial after a usable page; neither is complete-empty. Explicit partial markers
and pagination caps remain partial. A valid final `value: []` can be complete-empty
for its query, including an empty page after earlier populated pages. A complete
query is not proof of complete tenant history. No new endpoint or beta default is
introduced. See [Graph paging](https://learn.microsoft.com/en-us/graph/paging).

## Event outcomes and legacy authentication

`Core.signin_evidence.classify_signin_outcome` is the authoritative classifier.
`normalize_signin_event` retains raw event properties and adds rule version
`1.0.0`, result, legacy meaning, MFA satisfaction, policy-detail state and
qualification. `Core.operational_evidence.signin_record` delegates to it.

| Normalized outcome | Meaning |
| --- | --- |
| `Success` | Integer zero or a digit-string zero, without contradictory CA fields; token request succeeded, downstream access unknown |
| `BlockedByConditionalAccess` | A supported CA blocking code, or nonzero result corroborated by both overall CA failure and an applied-policy failure |
| `FailedOther` | Other readable failure; includes 50053 sign-in protection, which does not establish a CA block |
| `InterruptedOrChallenged` | Supported interruption/challenge codes 50072, 50074, 50076, 50078, 50079 or 50140; final access unknown |
| `Unknown` | Missing, nonnumeric, boolean, floating-point or contradictory result fields |

Generic CA failure does not turn every credential failure into a confirmed CA
block. Report-only failure is descriptive and cannot establish enforcement.
Unavailable or malformed applied-policy details remain distinct from an available
empty policy array. Error messages provide context; they cannot invent success.
Mappings are versioned because Microsoft error codes can evolve. See
[sign-in status](https://learn.microsoft.com/en-us/graph/api/resources/signinstatus?view=graph-rest-1.0)
and [AADSTS codes](https://learn.microsoft.com/en-us/entra/identity-platform/reference-error-codes).

The historical client-name predicate remains unchanged for compatibility; it
classifies clients, not exact protocols. Its known unmatched client types remain
explicit limitations. Each retained legacy event receives one of
`SuccessfulLegacyAuthentication`, `LegacyAttemptBlockedByConditionalAccess`,
`LegacyAttemptFailedOther`, `LegacyAttemptInterrupted`, or `LegacyOutcomeUnknown`.
All source occurrences remain traceable; events are not consolidated by user or app.

The existing four-way `outcome`/workbook `Outcome` values remain compatibility
fields. In particular, historical `Blocked` includes some challenges and Entra
protection; it is not the authoritative CA result. New decisions, customer
breakdowns and technical `Sign-In Result` use the five-way normalized outcome.

`legacy_event_summary` includes every primary/supplemental window, including
failed windows. A bounded "no success observed" statement requires all windows
complete, known scope and explicit start/end, and no unknown outcomes. It never
certifies tenant-wide blocking. Retained events can still prove a positive
condition when coverage is partial, but cannot prove absence.

A current successful legacy request cannot support `IDENTITY.AUTH` passage, even
with favorable MFA or CA fields. Event failures retain source provenance instead
of being mislabeled owner attestations. Unresolved contradictions remain conflicts.
Wrong-tenant, stale and future events cannot establish current operational failure.
A sample of modern/MFA sign-ins or CA-blocked attempts alone cannot establish
tenant-wide passage. Dated, scoped reviews of tested behavior remain a separate
input; an incomplete retained sign-in window cannot silently become an unqualified
pass through a favorable review.

## MFA population and evidence layers

`reconcile_registration_population` supplies report aggregates, recommendations,
registration worklists and `_mfa` evidence selection. Stable object ID is preferred,
then UPN when ID is missing; display name is never an identity key. Anonymous rows
remain separate. Duplicate source positions are retained. Conflicting registration,
capability, method, account-state or report-update values yield `Conflicting`.

The exclusive states are `ExplicitlyRegistered`, `ExplicitlyNotRegistered`,
`Unknown`, `Conflicting`, and `ExcludedWithReason`. Their sum is the returned unique
population. Only literal true/false eligible registration flags enter the rate:
registered / (registered + explicitly unregistered). No known denominator means
unknown, never 0%. Worklist/remediation selection requires explicit unregistered
state; unknown and conflicting rows cannot become unregistered by subtraction.

An explicitly disabled account is excluded from registration remediation, with
its reason retained. An ID-matched retained user inventory can supply that flag;
conflicting enabled states become conflict. Missing enabled state does not imply
disabled. Guests remain in the bounded report population with qualified context;
guest status alone is not an exception. The helper accepts explicit documented
exclusion reasons; it does not infer provider coverage from missing Microsoft
registration or automatically authorize an exception. Aggregate-only historical
summaries remain coverage observations and cannot identify affected users.

Aggregate customer views omit identity-bearing population entries. Technical
evidence preserves registration flags, methods, capability, user type, account
state, report update dates and original source references. Source completeness and
dates qualify findings; rebuilding does not refresh them. The current Graph report
usually excludes disabled/deleted accounts; explicit saved flags remain useful for
older or supplemental evidence. See
[registration details](https://learn.microsoft.com/en-us/graph/api/resources/userregistrationdetails?view=graph-rest-1.0).

The shared `authentication_assessment` keeps three layers:

- Registration: known returned flags and the reconciled denominator.
- Enforcement: configured policies, scope and operational conclusion; registration
  and policy inventory alone do not prove effective tenant-wide enforcement.
- Observed authentication: event-scoped required/not-required/unknown MFA plus
  observed completion, denial, prior token/claim satisfaction, external-provider
  satisfaction, strong primary authentication, unknown and conflicting detail.

`authenticationRequirement` alone does not establish actual MFA use. Strong
primary authentication and previous claims can satisfy requirements without a new
MFA step; provider satisfaction is not proof of Microsoft registration. Missing,
delayed or contradictory steps cannot become favorable assurance. See
[MFA reporting](https://learn.microsoft.com/en-us/entra/identity/authentication/howto-mfa-reporting).

## Outputs, safeguards and remaining decisions

Main HTML and readiness summary render the same identity-free layer summary.
Legacy Excel detail and optional technical HTML show normalized outcomes alongside
retained source values, event IDs, timestamps and scope. Legacy Excel `Created UTC`
retains raw Graph `createdDateTime` strings verbatim, including fractional digits
and timezone suffixes. SDK datetime objects retain the existing UTC conversion;
missing timestamps retain the existing unavailable marker. Existing sheet/link and
RecommendationId routes remain. Snapshots persist shared/raw normalized values;
offline replay uses the same evaluators. Existing snapshot validation, package
ownership, typed identities, evidence references, run history, immutable baselines,
conservative delta classification and explicit deny-by-default governance remain.
Rendering cannot create, approve, revoke or reopen decisions; evidence resolution
does not become remediation closure.

No new legacy count is registered as a directional delta metric in this pass.
Comparable population definitions, provider/scope, observation duration and source
coverage must be explicitly designed and validated before "fewer successes" is
treated as improvement. Existing MFA percentage comparisons retain their quality
gates and cannot imply closure from registration alone.

Pass B must address effective privileged populations, assignment schedules,
privileged MFA/sign-in correlation, inactivity candidates, emergency/service
accounts and combined privileged risks. Per-user enforcement and provider-side
coverage remain uncollected unless explicit reviewed evidence supplies them.
Full canonical migration and report redesign remain deferred. Retired dashboard
and App Builder generation must remain absent.

Regression fixtures use fictional rows and mocked HTTP. Run focused gates with:

```text
python -B tests/run_guarded_stage1.py --allow-temp --quiet test_authentication_corrections
python -B tests/run_guarded_stage1.py --allow-temp --quiet --regression
python -B tests/run_guarded_stage1.py --allow-temp --quiet --safe-discover
```

The guard blocks tenant/network access, subprocesses and writes outside its OS
temporary directory. Safe discovery explicitly excludes existing subprocess/cache
tests; exclusion is not a passing or skipped result. GitHub CI retains its full
offline discovery and synthetic artifact audit.

# Component evidence and customer projections

These corrections preserve the existing assessment controls, scoring, run workflow
and workbook layout. Returned component records do not close an unresolved control.
The shared result records their presence and qualification before projection.

## Partial component observations

- Copilot usage and license-capacity signals remain observed values, with original
  source qualifications. Usage is not proof of assigned entitlement. Authoritative
  inventory, the intended pilot's assigned entitlement and Microsoft 365 Apps
  versions/channels remain separate evidence requirements.
- Sensitivity-label definitions and publication policies are separate observed
  components. Their presence does not establish effective publication targets,
  content coverage or operational use.
- Audit configuration records establish only their explicit reported settings.
  Returned events establish activity within their query window. Neither establishes
  complete tenant configuration, retention coverage, or review/response ownership.

The optional `ObservedEvidence` metadata on unresolved recommendations retains
component counts and provenance. The recommendation remains an evidence request;
component presence does not become a control pass. An unavailable default response
does not become an observed licensing signal.

## Privileged-access units

`privileged_assessment.metric_reconciliation` distinguishes native assignment and
schedule records, reconciled active/eligible assignments, distinct human users,
groups, service principals, active effective identities, Global Administrator
identities and unresolved principals. Raw rows and reconciled assignments are
unchanged. Counts cover retained evidence, not an inferred complete tenant.
Effective identity counts exclude eligibility-only records and do not treat a
group as a human user; group-mediated humans require retained membership evidence.

Legacy numerical user claims without a reconciled principal population are marked
`unresolved_legacy_user_aggregate`. Customer observations use the typed model;
the original observation is retained for technical review. An unsupported legacy
assurance is downgraded rather than published as a confirmed user count. The
reported 140-user aggregate could not be reproduced from a current producer or
the available reference projections; no replacement 140-user population is inferred.

## Authentication registration metrics

The [Microsoft Graph userRegistrationDetails resource](https://learn.microsoft.com/en-us/graph/api/resources/userregistrationdetails?view=graph-rest-1.0)
defines registration, capability and registered-method inventory separately.
Capability includes policy allowance; registered inventory alone does not establish
policy enforcement or actual sign-in use. Report refresh dates are not method
registration timestamps.

Each summary metric retains its source field, known population, numerator and
denominator. Explicit false registration flags accompanied by resistant methods
or true capability flags produce cross-field diagnostics. Original flags, counts
and inventories are retained; they are not normalized to agree. Known field
denominators differ from the full returned population when fields are absent.
Duplicate-record conflicts and documented exclusions retain their existing rules.
The aggregate registration report and reconciliation metadata persist in
`authentication_assessment.registration` for HTML and saved-result replay.

## Lineage and publication

Identity Risk findings link to retained risky-user/risk-detection source ranges,
selected evidence IDs, Investigation Items, Evidence Index and the Technical
Evidence workbook. Customer drilldown rows retain evidence links but replace
identity-level risk details with restricted-record placeholders. Native identity
details remain in the authorized technical workbook.

Endpoint compliance-setting findings declare their selected setting records and
correct source key. These are settings, not a distinct device population. Existing
finding identifiers and source rows are preserved.

A GUID display fallback with a valid separate identifier produces an unresolved-name
diagnostic, not automatic corruption. Missing names, malformed display values and
GUID display values without valid identifying fields remain distinguishable.
High-confidence Action/Assurance conclusions need actual supporting declarations
or linked source-qualified evidence. Unsupported conclusions are qualified during
assessment or rejected at publication; renderers do not change conclusions.

## Coverage and operator instructions

`dataset_coverage` retains one customer summary per logical dataset and every
native request in a technical request register, including empty responses. Summary
counts distinguish successful, zero-result, failed, partial, unknown and unrequested
queries. Zero-result queries are a subset of successful queries. Returned records
are not deduplicated entities. Completeness refers to recorded request scope;
unknown counts and observation windows stay unknown. Parent object identifiers and
request URLs remain technical detail. Customer rows link to that register.

Customer projections replace collector commands and environment-variable instructions
with dated, scoped evidence requests while preserving separate factual sentences.
Technical originals and [operator import instructions](PORTAL_REPORTS_AND_OFFLINE.md)
remain available. No collector, permission, import workflow or tenant access is added.

Missing DSPM, Dashboard, Power Platform, current DAG, Apps prerequisites, specialized
Purview, group ownership/membership, application owners, privileged purpose, pilot
goals, policy/governance decisions, network and unmanaged-device evidence remain
unresolved. Rebuild and compare retained evidence in engineering review before
approving a customer package; historical output files are not rewritten.

## Follow-up verification

The evidence bundle now retains paid-seat capacity separately from user usage:
enabled subscription seats, assigned seats and their unassigned remainder are
capacity signals, not proof of entitlement for the intended pilot. Publishing
policy active flags do not establish the intended population or broader taxonomy.
Current investigation qualifications use the reconciled component observation;
superseded collector wording remains technical history rather than the current gap.

The registration comparison records `isMfaRegistered` separately from recognized
passwordless `methodsRegistered` inventory and `isPasswordlessCapable`. Each uses
its own known denominator. The same explanation appears in HTML, the readiness
summary and workbook guidance, and is persisted for replay.

Colliding device confirmation titles identify the specific protection condition and
whether retained reports are current or older/undated; an already unique title stays
stable. Blocker explanations join existing
recommendation IDs to failed required controls; one action can address multiple
controls. Missing mapping is qualified rather than inferred. No control outcome,
privileged-access count or retained assignment is changed by these projections.
Failed controls with non-blocker actions retain those actions' rollout timing rather
than being relabeled as pilot blockers. Unavailable response envelopes do not count
as retained label definitions or audit configuration records.

Coverage reconciles service-prefixed status mirrors with the same native request.
An unmatched failed or partial request is retained and counted; mirrored metadata
remains in the technical register without inflating request counts. Parent identities
stay out of customer coverage. Technical request indexing does not discard zero rows.

Outputs produced by merged main before this correction PR retain their original
wording and producing revision. Updating the unmerged feature branch does not
retroactively change existing assessment files or root main.

Intune noncompliance detail now retains the failed setting and explicitly associated
policy, last device sync, and reconciled activity. The default-policy **Is active**
failure is reporting/lifecycle review, including its Graph setting identifier
`DefaultDeviceCompliancePolicy.RequireRemainContact`. It does not establish a
BitLocker or other security-setting failure. Assignment and enrolled-user checks
also remain distinct from security settings. Missing reasons remain unknown.

Explicit security-setting failures with recent device reports remain actionable;
older, undated or ambiguously joined states require confirmation. Recent Entra or
Defender activity can establish activity without refreshing an old Intune setting.
Stale reporting neither proves retirement nor makes a device safe or compliant.
Assessment activity/reporting windows do not establish the tenant's configured
Intune compliance validity period. Raw compliance states and selected source rows
are preserved, and setting-summary parent IDs cannot be inferred as device or
policy IDs. Findings, workbook details and snapshot replay use the retained model.

References: [Intune default-policy evaluation](https://learn.microsoft.com/intune/device-security/compliance/monitor-policy#how-intune-evaluates-the-default-compliance-policy),
[compliance policy settings](https://learn.microsoft.com/intune/device-security/compliance/overview#compliance-policy-settings),
and [Graph setting-state properties](https://learn.microsoft.com/graph/api/resources/intune-deviceconfig-devicecompliancesettingstate?view=graph-rest-1.0#properties).

A separate device finding lists retained failed **Has a compliance policy assigned**
checks, including `DefaultDeviceCompliancePolicy.RequireDeviceCompliancePolicyAssigned`.
It includes devices whose overall Intune state is compliant, preserves the raw
assignment check, and shows the linked device, user and reporting date. Assignment
status is reported missing, check passed, conflicting or unknown. Counts describe
retained managed-device records, with unresolved identities counted separately.
An empty/partial response, `notAssigned` for an individual setting, or an empty
policy inventory does not prove that a device has no applicable compliance policy.
A passing check does not establish policy names or effective enforcement.
Stale and conflicting assignment checks require current targeting confirmation.
This is a bounded retained-evidence worklist, not a replacement for Intune's
[Devices without compliance policy report](https://learn.microsoft.com/en-us/mem/intune/fundamentals/reports#devices-without-compliance-policy-organizational).

Future live Entra collection also reads `GET /v1.0/deviceManagement?$select=settings`
as a singleton using the existing `DeviceManagementConfiguration.Read.All` setup
permission. The Configuration sheet reports **Mark devices with no compliance
policy assigned as** (`secureByDefault`: true means Noncompliant, false means
Compliant) and **Compliance status validity period (days)**
(`deviceComplianceCheckinThresholdDays`). The raw object, source metadata and
reporting date survive saved collection, technical evidence and snapshot replay.
Omitted, denied, invalid and conflicting values remain qualified; older collections
cannot supply settings they did not retain. These configuration rows neither prove
effective enforcement nor change the separate assessment activity/reporting windows.
See [Graph settings](https://learn.microsoft.com/graph/api/resources/intune-deviceconfig-devicemanagementsettings?view=graph-rest-1.0)
and [supported read permissions](https://learn.microsoft.com/powershell/module/microsoft.graph.devicemanagement/get-mgdevicemanagement?view=graph-powershell-1.0).

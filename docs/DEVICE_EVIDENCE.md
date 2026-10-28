# Device evidence interpretation

Endpoint findings use retained evidence only. Intune device inventory, individual
setting results, Windows protection reads and Defender machine inventory have
different units. Counts must not be added across overlapping findings.

## Identity and activity

`Core/device_evidence.py` supplies the shared stable-ID interpretation used by
compliance reasons, operational reconciliation and finding counts. Intune,
Microsoft Entra and Defender IDs occupy separate namespaces. Hostnames and serial
numbers are retained as context; they never establish a join. Contradictory stable
IDs leave records unresolved. Summary `ParentId` is not a managed-device ID.

Identity projections retain source rows and native EVD IDs, serial number,
protected display name, primary user, management state, platform, enrollment,
ownership, Intune sync, Defender last-seen and retirement/wipe context.

Activity classifications are `CurrentActiveDevice`, `StaleDeviceRecord`,
`RetiredOrDecommissioned`, `DuplicateOrSupersededRecord`, `ActivityUnknown` and
`MalformedTimestamp`. Missing activity is unknown. Explicit retirement and
duplication evidence is independent of freshness; pending wipe or retire actions
do not prove completed decommissioning. The existing assessment defaults are a
30-day activity window and a 7-day reporting window, configurable through
`assessment_context.device_activity_days` and `endpoint_reporting_days`.
These are not the tenant's Intune compliance validity period.

## Findings and counts

Current, named security-setting failures remain separate from default-policy
activity, enrollment and assignment failures. Error, conflict, unknown and
not-evaluated results are reporting gaps, not confirmed security failures.
Older or unresolved security-setting rows require confirmation. Overall Intune
compliance remains an inventory state; a compliant aggregate cannot erase a
retained failed setting or establish access or Copilot use.

Every device finding carries `DeviceEvidenceCounts`: selected source records,
distinct Intune/Entra/Defender IDs, reconciled device identities, unresolved
identity records, active/stale identities, pilot-relevant/unknown identities,
pilot-unknown records, valid/malformed/missing observation timestamps, and a
qualified evidence period. Namespace ID counts can overlap and are never summed.
Timestamp counters describe the selected observation date (or explicitly qualified
Intune sync fallback). Other malformed source fields have separate diagnostics.

`0001-01-01`, pre-1900 dates, numeric date serials, unparseable values and dates
after the evaluation day do not establish age, inactivity, current risk or pilot
blocking. Invalid present report dates do not fall back to sync or collection
time. `0.0.0.0` is not a meaningful version. Raw values survive in Technical
Evidence with structured diagnostics and EVD links.

RTP-off observations are divided into recent and older/undated populations by
their endpoint report date. An off observation does not prove an active
unprotected device. Each technical interpretation retains available RTP, tamper,
signature, AV-mode, onboarding, approved third-party provider, platform, source
completeness and pilot context. Missing fields leave protection effectiveness
unestablished. A handful of returned Defender machine records is not a fleet
coverage denominator. Microsoft documents the distinction between
[AV modes and other providers](https://learn.microsoft.com/en-us/defender-endpoint/microsoft-defender-antivirus-compatibility)
and the [Windows protection report fields](https://learn.microsoft.com/en-us/graph/api/resources/intune-devices-windowsprotectionstate?view=graph-rest-1.0).

Conditional Access findings describe configuration and unresolved effective
targeting. Intune configuration, compliance evaluation, access enforcement and
observed sign-in outcomes remain separate. No successful access is inferred from
noncompliance or a policy grant.

## Pilot correlation

Without an explicit dated pilot membership record, device findings have
`PilotRelevanceUnknown`. The existing assessment-profile
`readiness_review.pilot_scope` can optionally include:

```json
{
  "id": "fictional-pilot",
  "description": "Approved fictional pilot population",
  "reviewed_at": "2026-10-27",
  "reviewer_role": "Endpoint owner",
  "evidence_reference": "fictional-approved-roster",
  "intune_device_ids": ["fictional-managed-device"],
  "entra_device_ids": [],
  "defender_device_ids": [],
  "user_ids": [],
  "membership_complete": true
}
```

The optional ID lists match only their own namespace. A current review is no more
than 35 days old. `membership_complete` must be explicitly true to classify an
unmatched reconciled device `OutsideKnownPilotScope`; an incomplete roster cannot
prove exclusion. This does not make a roster mandatory for collecting or
assessing the tenant baseline. Tenant-wide device counts are never a pilot roster.

Only confirmed pilot-relevant, active, current security failures may block the
pilot. Unknown relevance or posture requires confirmation. Verified active
failures outside a known pilot remain tenant hardening. Stale, duplicate and
retired populations are inventory hygiene; insufficient identity correlation is
an evidence gap. Tenant severity remains visible independently of pilot impact.
A broad endpoint review does not silently close specific device-reporting,
Conditional Access or pilot-correlation gaps.

## Outputs and verification

The customer workbook contains one device population summary per finding.
Per-device/per-setting projections move to Technical Evidence `Device Detail`;
native raw records and source EVD IDs remain intact. Technical `Device Identity`,
`Device Finding Counts` and `Device Diagnostics` expose reconciliation and
qualification. A mismatch between a finding's selected source-record count and
its linked detail fails workbook validation. Concise customer findings retain
record and distinct-device counts, evidence period, qualification, pilot treatment,
recommended action and completion evidence. Rendering uses the frozen result.

Fictional regressions cover identity conflicts, overlapping units, stale and
malformed values, reporting errors, third-party AV, pilot correlation, raw-row
preservation and summary/technical separation. Existing detail assertions remain
at their technical destination with additional customer privacy checks.

To evaluate these corrections locally from this checkout, use an existing saved
collection with `--mode offline --collection-input PATH --run-type Standalone`.
Add `--plan` first to inspect preflight. A normal rebuild of a completed assessment
may replay its frozen conclusions; Standalone explicitly evaluates a new package.
Keep companion deliverables together for evidence links. No tenant access or
collection is needed to evaluate already retained sources.

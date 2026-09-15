# Readiness milestones and owner reviews

The report distinguishes observed Copilot adoption from verified rollout readiness. Assigned
licenses or active users can show that adoption has begun while the next readiness milestone
still needs evidence. No percentage or usage threshold advances readiness automatically.

## Milestones

| Stage | Required evidence |
|---|---|
| Just started | Initial state before collected evidence or a current reviewed scope is available. |
| Preparing for pilot | Assessment evidence or a current reviewed scope is available; one or more pilot requirements remain open. |
| Ready for pilot | Current reviewed pilot scope and plan, all applicable pilot controls established, and remaining eligible pilot conditions documented. Unresolved historical findings and missing required evidence still block this stage. |
| Ready for broader adoption | Pilot requirements met, successful reviewed pilot outcomes, a distinct larger population with current control reviews, current expansion approval, and no remaining required remediation or confirmation actions. |

The report lists exact requirements and related action IDs. Its counts distinguish established
controls, observed issues and unanswered controls; `pilot_blockers` counts open or failed pilot
requirements, including the scope and plan. These counts measure different things and are not
a readiness score. A completed report can still contain unanswered readiness questions.

## Add reviews through the existing assessment profile

Add `readiness_review` to the JSON supplied by `--assessment-profile`. The
[sanitized example](examples/readiness-review.example.json) uses a fictional tenant, scope,
dates and references. It intentionally includes an application prerequisite failure and leaves
other controls unanswered. Replace its content with reviews actually performed for the tenant.
Never copy the example's pass results as tenant evidence.

```powershell
.\.venv\Scripts\python.exe main.py --mode offline `
  --collection-input "<collection-input>" `
  --assessment-profile ".\tenant-readiness-profile.json"
```

Use the collection path printed by a previous successful run. The same profile also works with
live collection. Rebuilding does not change original evidence dates. Review results appear in
the shared evidence and control results, the report's milestone requirements and the workbook's
review records. A review is an operator attestation, identified separately from collected facts.

### Required scope and optional reviewed plan

`readiness_review` requires `version: "1.0"`, the assessed tenant's GUID in `tenant_id`, and
`pilot_scope`. A different tenant is rejected. The scope must include:

```json
{
  "id": "pilot",
  "description": "The named users, approved content and devices included in this pilot",
  "population_count": 10,
  "reviewed_at": "2026-09-14",
  "reviewer_role": "Business sponsor and Microsoft 365 administrator",
  "evidence_reference": "Approved charter record PILOT-001"
}
```

`population_count` is optional for pilot readiness and required to establish that a later
expansion population is larger. IDs and descriptions must identify the actual assessed scope;
the tool does not verify group membership or the accuracy of the attestation itself.

To establish the plan requirement, add `pilot_plan` with `reviewed_at`, `reviewer_role`,
`evidence_reference` and `baseline`. It also needs `business_owner`, `use_cases`, `approved_data`,
`success_measures` and `stop_expand_criteria`, each nonempty text or a list of nonempty text.
These five fields can be derived from existing profile `use_cases`:

| Plan field | Existing use-case field |
|---|---|
| business_owner | business_owner |
| use_cases | name |
| approved_data | approved_data |
| success_measures | required_outcome |
| stop_expand_criteria | expand_stop_decision |

Use `pilot_plan.use_case_names` to select named use cases; omit it to use all supplied use cases.
The measured or documented baseline must be supplied explicitly. Usage counts alone do not
establish an approved charter or success measures.

### Close a specific unanswered control

Add a row to `readiness_review.control_reviews` for each completed review:

```json
{
  "control_id": "DATA.RETENTION",
  "scope_id": "pilot",
  "reviewed_at": "2026-09-14",
  "reviewer_role": "Information governance owner",
  "result": "pass",
  "rationale": "The owner reviewed the applicable retention requirements and their coverage for the pilot content.",
  "evidence_reference": "Retention review RET-014, rows 4-12"
}
```

Every field above is required. `result` accepts only `pass` or `fail`. The evidence reference may
be a workbook section, approved record ID or other identifiable reviewed material; no separately
uploaded artifact is required. The reference's existence and the reviewer's authority are not
independently authenticated by this tool.

| Control ID | Review question |
|---|---|
| IDENTITY.AUTH | Sign-in policy coverage for the pilot |
| IDENTITY.MFA | Multifactor authentication registration |
| IDENTITY.ADMIN | Privileged access and administrative roles |
| CONTENT.SHARING | Tenant sharing defaults |
| CONTENT.PERMISSIONS | Broad permissions to business content |
| CONTENT.OWNERSHIP | Accountable owners and inactive-site decisions |
| DATA.LABELS | Sensitivity label definitions |
| DATA.PUBLISHING | Label publication to intended users |
| DATA.DLP | Data loss prevention coverage and enforcement |
| DATA.AUDIT | Audit coverage for investigation |
| DATA.RETENTION | Content retention requirements and coverage |
| DATA.EXPOSURE | Effective access to sensitive pilot content |
| APPS.CONSENT | Application consent and granted permissions |
| APPS.CONNECTIONS | Connected sources and access boundaries |
| ENDPOINT.POSTURE | Pilot device and browser protection baseline |
| THREAT.INCIDENTS | Relevant active incidents |
| LICENSE.ASSIGNMENT | Copilot and prerequisite license assignment for the named population |
| LICENSE.APPS | Application prerequisites for pilot users |
| ADOPTION.BASELINE | Pilot plan; the structured `pilot_plan` is also required for the milestone |

When agents or external AI are explicitly scoped, the catalog also accepts `AGENTS.BOUNDARIES`
and `EXTERNAL.SERVICES` as optional-domain reviews. They do not establish provider certification.
There is no blanket not-applicable override for core questions. For example, a verified absence
of connected sources can support a passed `APPS.CONNECTIONS` review with its scope and rationale.
A dated sensitive-content access review can support `DATA.EXPOSURE`; a DSPM license is not required.

### Dates, disagreements and existing findings

- Scope, plan and review dates use ISO dates such as `2026-09-14`. They must be no later than
  the evaluation date and no more than 35 days old to establish current readiness.
- The plan and each control review must be dated on or after the current scope review.
- A current complete passed review may close the matching generated missing-evidence question.
  It cannot close an observed failure or an unresolved historical finding. Collect supported,
  current evidence for the affected condition and rebuild; an old finding remains confirmation
  work until it can be reconciled with suitable evidence. No manual finding-resolution override
  is implemented.
- Contradictory reviews for the same control, scope and date remain a conflict. A favorable
  result is never selected solely because it is favorable.
- Invalid review schema contributes no review facts and is reported with validation errors.
  A malformed unrelated profile section does not invalidate a separately complete review.

### Document an eligible pilot condition

`pilot_conditions` can record a controlled pilot exception for an existing action. Each row
requires `action_id`, `scope_id`, `reviewed_at`, `reviewer_role`, `condition` and
`evidence_reference`. The action ID must match the current shared register. A condition must
be current, refer to the pilot scope, and be dated on or after both the scope and the observed
condition. Eligibility is limited to supported noncritical remediation already categorized
`Before broad rollout`, `Pilot condition` or `Planned improvement`.

Conditions cannot bypass critical issues, missing evidence, before-pilot remediation or historical
confirmation. Accepted conditions remain visible and the action stays open. They do not establish
broader adoption readiness.

## Review pilot outcomes before expanding

Broader adoption requires all of these records under the same `readiness_review`:

1. **`pilot_outcomes`:** `scope_id` matching the pilot, `reviewed_at`, `reviewer_role`,
   `period_start`, `period_end`, `outcome_summary`, `success_measures_met: true`, `risk_review`
   and `evidence_reference`. Record the business sponsor's reviewed outcome. The period must
   start on or after the reviewed pilot plan and end on or before the outcomes review.
2. **`expansion_scope`:** the same fields as `pilot_scope`, a different ID and a larger
   `population_count`. Both populations must be recorded and the scope must be current.
3. **Expansion `control_reviews`:** a passed review for every required control using the
   expansion scope ID, current and dated on or after the expansion scope and outcomes review.
   Pilot-population evidence alone does not establish these larger-population reviews.
4. **`expansion_approval`:** the expansion `scope_id`, `reviewed_at`, `reviewer_role`,
   `approved: true`, `rationale` and `evidence_reference`. Record accountable sponsor approval
   on or after the outcomes review, expansion scope and latest expanded control review.
5. **No open required actions:** unresolved risks, confirmations or evidence gaps prevent this
   stage, including actions previously accepted as pilot conditions.

All attestations must still be current at the evaluation date. The records are separate reviews;
they may share a calendar date if their ordering requirements are met. Active-user counts,
licenses, security controls alone or a generic approval do not establish this milestone.

## Portal screenshots and PDFs

[`--portal-review`](PORTAL_REVIEW.md) records reviewed visual context and preserves its original
files. It produces no scoring facts and cannot close controls. A control owner who performs an
actual dated scoped review can separately record that attestation through `readiness_review`.

See [the methodology](METHODOLOGY.md#deployment-decision) for how these milestones relate to the
deployment recommendation and [RUN.md](RUN.md) for collection and rebuild commands.

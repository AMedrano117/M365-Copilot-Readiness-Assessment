# Microsoft 365 Copilot Readiness Assessment Methodology

## Purpose

This tool evaluates the Microsoft 365 foundation for a controlled Copilot deployment. Business
leaders and IT owners receive one customer narrative, supported by an evidence workbook.
Agents and the Microsoft 365 side of external AI providers are assessed when explicitly scoped.

It does not certify an AI provider, prove regulatory compliance, or guarantee that data cannot
be exposed. Provider-side settings and contracts require separate validation.

## Completeness, evidence and readiness

These are separate conclusions:

- **Report completeness:** the agreed domains, findings, actions and qualifications are covered.
- **Evidence completeness:** required sources cover the agreed population and period sufficiently
  to assess the applicable controls. A source can be missing even when the report is complete.
- **Readiness:** the deployment recommendation supported by the evidence and remaining questions.

Offline execution is not a control failure. Original observation dates, scope, reliability and
coverage determine what saved evidence can support. A build timestamp does not refresh evidence.
The recorded evaluation date controls freshness. A later evaluation date can legitimately change
the decision; equivalent supported evidence, settings, methodology and evaluation date should
produce equivalent results in live and offline modes.

## Shared evidence contract and reconciliation

Evidence schema `1.0.0` and reconciliation version `1.0.0` describe normalized facts independently
of collection or rendering. Each fact retains tenant, domain/control/metric identity, definition,
population/scope, affected objects, value/unit, numerator/denominator, reporting window/basis,
original date, source type/file/schema/hash, completeness/truncation, freshness, selection state
and qualifications where the source supplies them. Missing metadata remains qualified; it is
not inferred from filenames or the date a file was copied.

Availability distinguishes available, partial, missing, not requested, inaccessible, unsupported,
empty, unknown, unavailable and not applicable. Unavailable measurements use a null value.
Measured zero is retained only when supported by available evidence.

Reconciliation follows these rules:

1. Compare sources only when tenant, metric definition, scope/population, affected objects,
   unit, reporting basis and window match. Unknown tenant/scope stays specific to its source.
2. Prefer complete evidence over a newer incomplete comparable snapshot. Within suitable
   comparable sources, use the latest dated observation.
3. Keep different values for the same date, or dates that cannot be ordered, as conflicts with
   a confirmation action. Do not silently choose the most favorable result.
4. Retain repeated snapshots as duplicates or superseded evidence; never add them together.
5. Qualify undated, stale and future-dated facts. Apply the same support and freshness rules to
   strengths and risks. Future-dated evidence cannot establish current assurance.
6. Treat tenant sharing settings, item permissions, content lifecycle, label inventory and DSPM
   sensitive-data findings as separate questions. One source cannot fill an unrelated gap.

Portal importers also retain per-file schema and selection diagnostics. The supported source
adapters feed one assessment result, from which the report, action register, domain counts and
workbook derive. [The compatibility matrix](PORTAL_REPORTS_AND_OFFLINE.md#sample-compatibility-matrix)
states which schemas have real-export validation and which have only documented/synthetic tests.

## Historical evidence and portable replay

Supported saved service facts are evaluated through the same assessment rules as live facts.
A prior workbook or result snapshot cannot recreate raw data that was never saved. Its original
findings remain historical and retain original methodology/date context. Unresolved historical
risks become confirmation work in their relevant domains and the shared action register; an old
recommendation is not presented as a newly verified tenant condition. Historical strengths have
the same date/support qualifications. The workbook preserves the full original register.

The portable assessment folder records versioned collection/manifest data, original supplemental
inputs and hashes, assessment settings, evaluation date, deliverables and operator receipts.
Package validation rejects changed/missing declared files and source references that escape the
folder. Successful offline additions are preserved in a separate rebuild recipe, leaving the
original collection unchanged. Version 2 collections and rebuild recipes require the recorded
methodology to match the running tool, with one explicit compatibility transition: **2.0.0 → 2.1.0**.
The raw schema, collected facts and base findings remain usable; current control matching and
reviewed pilot criteria are applied, so deployment conclusions can change. The migration preserves
original files, source hashes and evidence dates, and records the source/effective methodology and
reason in report context, the workbook's Collection Coverage sheet and the operator receipt. Offline builds display
a warning; no new tenant collection is required. Other mismatches still require the matching tool
version or a separately supported migration. Version 1
compatibility retains precomputed conclusions as historical. Download results can be packaged
only when the collector supplies an existing local file. Credentials remain outside the package.
See [RUN.md](RUN.md#portable-assessment-folder).

Historical-workbook/cache and portal-only builds preserve a portable `m365-readiness-rebuild`
recipe with their original inputs and outputs. They create no raw tenant collection. Replaying
the recipe restores the same evidence limitations and original dates through the shared rules.

## Report lanes

Every observation is placed in exactly one lane:

| Lane | Meaning | Included in deployment decision |
| --- | --- | --- |
| Action | A measured or configured tenant condition needs remediation | Yes |
| Opportunity | Optional adoption, value, or hardening work | No |
| Assurance | A healthy control or available capability was observed | No |
| Coverage | The assessment could not verify a source or applicable control | Required in-scope gaps qualify the conclusion; optional gaps do not block the foundation decision |
| Reference | Useful inventory that does not fit the other lanes | No |

A licensed feature is not proof that the feature is configured, scoped correctly, or effective.
License-only observations are low-confidence assurance or opportunities unless deployment data
shows a concrete gap.

## Deployment decision

Assessment methodology `2.1.0` uses the shared result to choose the deployment recommendation
and rollout milestone. The underlying risk recommendation is evaluated in this order:

1. A supported critical remediation action produces **Not ready for pilot**.
2. An unresolved required evidence question or confirmation action produces **Readiness unconfirmed**.
3. Supported high-priority remediation produces **Pilot only — remediation required**.
4. Supported medium-priority remediation produces **Controlled pilot with conditions**.
5. Covered required checks without critical/high/medium remediation support **Ready for a controlled pilot**
   only when the reviewed pilot scope and plan are established.

The rollout stages are **Just started**, **Preparing for pilot**, **Ready for pilot** and
**Ready for broader adoption**. Each stage lists its exact requirements and open action IDs.
Initial assessment evidence or a current reviewed scope establishes Preparing for pilot. Pilot
readiness requires a current reviewed scope and plan, all required checks established, and any
eligible pilot conditions recorded. Missing requirements keep the recommendation **Readiness
unconfirmed**, except that a supported critical issue retains **Not ready for pilot**.

Broader adoption additionally requires reviewed pilot outcomes, a defined larger population,
current reviews of every required control for that population, dated expansion approval and no
open required actions. Both the stage and deployment recommendation then become **Ready for
broader adoption**. Existing paid licenses or active usage never advance these milestones alone.

The existing assessment profile accepts dated owner attestations in `readiness_review`. A valid
current pass can answer a specific missing-evidence question but cannot erase an observed failure
or unresolved historical finding. Scope and reviews must be current within 35 days, belong to
the assessed tenant and identify a reviewer role and evidence reference. Review dates must be
on or after the relevant scope definition. Opposite results for the same control, scope and date
remain a conflict. See [Readiness reviews](READINESS_REVIEWS.md) for the exact schema, allowed
control IDs, date requirements, pilot conditions and expansion requirements.

Manually reviewed portal screenshots and PDFs remain visual context. They do not generate
control facts; a separate dated scoped owner attestation is required to answer a control through
this review workflow.

“Ready” never means risk-free or provider-certified. It means no blocking condition was found in
the assessed Microsoft 365 evidence.

## Evidence and confidence

| Evidence basis | Confidence | Permitted conclusion |
| --- | --- | --- |
| Tenant evidence | High | Named configuration or object detail supports the observation |
| Tenant observation | Medium | A collector returned an aggregate or state without linked object detail |
| License signal | Low | The capability may be available; implementation is not proven |
| Not verified | Unknown | No positive or negative tenant conclusion is permitted |

When an API or collector cannot be read, the report must say **Coverage** or **Not Assessed**. A
failed query must never become “zero,” “none detected,” or “healthy.”

## Control domains

The primary report prioritizes controls with a plausible causal relationship to AI risk or value:

- Identity and access: MFA, Conditional Access, privileged access, risky identities.
- Content access and grounding: SharePoint/OneDrive permissions, external sharing, stale or
  ownerless content, and oversharing.
- Data protection and compliance: sensitivity labels, DLP, retention, audit, and investigation
  readiness where applicable.
- Applications and connectors: application consent, scopes, publisher trust and activity;
  agent identities, tools, actions and least privilege when agents are in scope.
- Endpoints and threat protection: managed-device coverage, active incidents, compromised
  accounts, risky devices and malicious apps; external-AI egress when explicitly scoped.
- Adoption and value: bounded use cases, pilot cohorts, training, quality, and outcome measures.
- Licensing and prerequisites: availability only; never a substitute for configuration evidence.

The current core register contains 19 questions: 18 foundation control checks and a pilot-plan
question. The reviewed plan is also an explicit pilot-readiness milestone requirement.

| Domain | Questions |
|---|---|
| Identity and access | Sign-in policies; MFA registration; privileged and administrative roles |
| Content access and ownership | Tenant sharing defaults; broad permissions; owners and inactive-site decisions |
| Data protection | Label definitions; label publication; DLP coverage/enforcement; audit coverage; content retention requirements; access to sensitive pilot content |
| Applications and connectors | App consent/grants; connected sources and access boundaries |
| Endpoints and threat protection | Pilot device/browser baseline; relevant active incidents |
| Copilot licensing and prerequisites | License assignment; application prerequisites for the pilot population |
| Pilot suitability and adoption | Reviewed pilot population, use cases, baseline and success measures; required to establish pilot readiness separately from foundation security checks |

When explicitly supplied canonical facts determine a control result, they require `control_result`
set to `pass` or `fail`, plus dated, complete tenant/scope metadata. An arbitrary count does not
pass a control. Sign-in policies cannot establish MFA registration, and label definitions cannot
establish publication. A lifecycle or DSPM artifact in a shared evidence tab cannot establish
business-content permissions. Activity counts cannot establish an agreed pilot plan: pilot
population and success measures need an explicit review. Explicitly scoped agents and external
AI add their own questions; the core register above stays unchanged.

Missing required evidence creates a specific question for the responsible role. Source coverage
means the question has assessable evidence; it does not certify policy effectiveness for every
possible user or scenario. The operator still confirms the intended pilot population and business
requirements. Supported dated raw control evidence uses a 35-day freshness window; aggregate
usage uses seven days. Report-specific thresholds below retain their separate purposes.

## Conditions that are not universal failures

The following can be worthwhile, but their absence alone does not block AI deployment:

- group-based licensing;
- a particular passwordless adoption percentage when effective MFA/Conditional Access exists;
- open eDiscovery cases when there is no active legal or investigation matter;
- Information Barriers without a documented ethical-wall requirement;
- Customer Lockbox without a contractual or regulatory requirement;
- optional Viva, Places, Mesh, Bookings, Forms, Sway, analytics, or similar service plans;
- arbitrary usage targets, numbers of agents, or assumed return-on-investment percentages.

These belong in Opportunity, Assurance, or inventory—not Action.

## External AI boundary

Microsoft 365 telemetry can show controls around M365 identities, content, app grants, devices,
and data egress. It cannot confirm the configuration of an external AI workspace. For every
provider, validate:

1. sanctioned service inventory and accountable owner;
2. enterprise identity, SSO, provisioning/deprovisioning, MFA, and administrator roles;
3. contracted training use, retention, residency, subprocessors, deletion, and abuse monitoring;
4. endpoint/browser/network egress controls for prompts, paste, and uploads;
5. connectors, OAuth grants, MCP servers, agent identities, action permissions, and human
   approval for consequential writes;
6. approved use cases, prohibited data, human-review requirements, and responsible owners;
7. audit export, retention, eDiscovery, incident response, and privacy processes;
8. pilot baselines, quality measures, risk thresholds, and stop/expand decisions.

Provider controls change frequently. Validate them against the current contract and primary
provider documentation rather than relying on product-name assumptions.

## Adoption, engagement, and optional extensibility

The report keeps these measures distinct:

- **License coverage:** assigned Copilot licenses divided by enabled users with at least one
  assigned non-Copilot base license. This is a transparent tenant-derived eligibility estimate,
  because Graph does not expose one authoritative Copilot-eligibility flag.
- **Activation:** active Copilot users divided by Copilot-enabled users in the returned report.
- **Engagement:** prompts, active days, application distribution, and daily trends.
- **M365 app readiness:** active Word, Excel, PowerPoint, Outlook, Teams, web, desktop, and mobile
  usage. These signals help select a pilot cohort but do not prove Copilot value.
- **Extensibility:** optional Power Platform inventory, agents, connectors, and environments.
- **External AI:** aggregate Defender for Cloud Apps discovery when the preview collector is
  explicitly enabled. Purview DSPM remains data-risk evidence, not proof of external-AI adoption.

Graph usage evidence is marked stale after seven days. Uploaded supplemental evidence is marked
stale after 30 days. The provider's actual refresh date is always retained; missing, unauthorized,
or malformed evidence is shown as unavailable and never converted to zero usage. Adoption and
extensibility evidence cannot raise or lower the security and governance readiness score.

## Primary references

- [Microsoft secure and governed data foundation for Copilot](https://learn.microsoft.com/microsoft-365/copilot/secure-govern-copilot-foundational-deployment-guidance)
- [Configure a secure and governed foundation](https://learn.microsoft.com/microsoft-365/copilot/configure-secure-governed-data-foundation-microsoft-365-copilot)
- [Microsoft Copilot data protection architecture](https://learn.microsoft.com/copilot/microsoft-365/microsoft-365-copilot-architecture-data-protection-auditing)
- [Microsoft Purview protections for other AI apps](https://learn.microsoft.com/purview/ai-other-apps)
- [Microsoft Purview Data Security Posture Management](https://learn.microsoft.com/purview/data-security-posture-management-learn-about)
- [Conditional Access target resources](https://learn.microsoft.com/entra/identity/conditional-access/concept-conditional-access-cloud-apps)
- [OpenAI app administration and security](https://help.openai.com/en/articles/11509118-admin-controls-security-and-compliance-in-connectors-enterprise-edu-and-team)

## Quality rules

- One tenant condition should produce one action, even when several licenses expose it.
- Status and priority must agree.
- Recommendations must identify the observed condition, affected scope, next action, and evidence.
- Exact objects belong in the engineer workbook; the HTML report should remain decision-oriented.
- Marketing language, invented percentages, and unsupported causal claims are not acceptable
  evidence.
- A control should be scored only when its applicability is known or clearly stated as conditional.
- Each required in-scope control needs a supported result or an explicit evidence gap. Unknown
  applicability is a remaining decision, not a pass. Optional capabilities cannot make the whole
  foundation assessment incomplete solely because they are unused.
- Do not combine overlapping users, sites, files, links or permissions into one exposure total.
- User-level Copilot evidence is opt-in for each workbook build and absent from HTML. Original
  packaged inputs may still contain that detail and require restricted handling.

## Data exposure and oversharing assessment

Oversharing conclusions require authoritative object- or site-level evidence. SharePoint usage,
site counts, and file counts describe adoption but do not prove that content is safely permissioned.

The dedicated Data Exposure domain consumes completed exports from:

- SharePoint Advanced Management Data Access Governance, including site-permission baselines,
  Anyone/organization-wide sharing links, EEEU/Everyone permissions, and external access; and
- Microsoft Purview Data Security Posture Management data-risk assessments, including potentially
  overshared items, sensitive content, and unlabeled sensitive content.

Microsoft runs these scans asynchronously. The tool does not attempt to reproduce or wait for a
long-running Microsoft scan. It evaluates whether a completed result was supplied, checks the
report date, reuses a recent result, and asks the operator to check for an existing current result
before starting a new scan. It emits a Coverage item when the result is missing, stale,
unreadable, or has no verifiable scan date. Coverage items include optional enablement, run,
export, and rerun instructions.

Default freshness thresholds are 35 days for SAM permission/sharing reports, 8 days for DSPM
assessments, and an independent 90 days for content lifecycle reports. Environment defaults are
`SAM_REPORT_MAX_AGE_DAYS`, `DSPM_REPORT_MAX_AGE_DAYS` and `LIFECYCLE_REPORT_MAX_AGE_DAYS`;
`--lifecycle-report-max-age-days` explicitly overrides the lifecycle window. Packages freeze
these settings with the evaluation date. Findings from stale reports may still be shown, but
with reduced confidence and without implying current coverage.

An operator may confirm an undated lifecycle export's generation date with
`--lifecycle-report-date "PATH=YYYY-MM-DD"`. The package stores that fallback against the
original file's SHA-256 hash, preserving it across copies and renames without applying it to
changed contents. The source's own report date takes precedence; filenames and file timestamps
are never date evidence. Unknown and future source dates remain qualified, and operator
confirmations later than the evaluation date are rejected. Lifecycle freshness does not
establish item permissions or replace the separate SAM/DSPM coverage requirements.

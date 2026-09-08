# Enterprise AI Readiness Assessment Methodology

## Purpose

This tool evaluates whether a Microsoft 365 data estate has a defensible foundation for
enterprise AI. It covers Microsoft 365 Copilot, agents that use Microsoft 365 data, and the M365
side of external AI services such as ChatGPT, Claude, Cursor, and other LLM applications.

It does not certify an AI provider, prove regulatory compliance, or guarantee that data cannot
be exposed. Provider-side settings and contracts require separate validation.

## Report lanes

Every observation is placed in exactly one lane:

| Lane | Meaning | Included in deployment decision |
| --- | --- | --- |
| Action | A measured or configured tenant condition needs remediation | Yes |
| Opportunity | Optional adoption, value, or hardening work | No |
| Assurance | A healthy control or available capability was observed | No |
| Coverage | The assessment could not verify the required source | Prevents a complete conclusion |
| Reference | Useful inventory that does not fit the other lanes | No |

A licensed feature is not proof that the feature is configured, scoped correctly, or effective.
License-only observations are low-confidence assurance or opportunities unless deployment data
shows a concrete gap.

## Deployment decision

The headline is intentionally conservative:

1. A critical action produces **Not ready for pilot**.
2. A high-priority action produces **Pilot only — remediation required**.
3. Medium-priority actions produce **Controlled pilot with conditions**.
4. Coverage gaps with no actions produce **Assessment incomplete**.
5. Only a fully read assessment with no critical/high/medium actions produces **Ready for a
   controlled pilot**.

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
- Apps, connectors, and agents: application consent, scopes, publisher trust, activity, agent
  identities, tools, actions, and least privilege.
- Endpoint and browser controls: managed-device coverage and sensitive-data egress to external
  AI applications.
- Threat protection: active incidents, compromised accounts, risky devices, and malicious apps.
- Adoption and value: bounded use cases, pilot cohorts, training, quality, and outcome measures.
- Licensing and prerequisites: availability only; never a substitute for configuration evidence.

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

Default freshness thresholds are 35 days for SAM reports and 8 days for DSPM assessments. They
can be changed with `SAM_REPORT_MAX_AGE_DAYS` and `DSPM_REPORT_MAX_AGE_DAYS`. Findings from stale
reports may still be shown, but with reduced confidence and without implying current coverage.

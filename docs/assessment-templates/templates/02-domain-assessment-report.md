# {{CUSTOMER_NAME}} AI Readiness Domain Assessment

## How to use this report

Start with the executive decision, review domains that affect the decision, then use finding and evidence references for technical validation. A missing signal is recorded as an evidence gap, not a pass.

## Assessment basis

- **Assessment date:** {{ASSESSMENT_DATE}}
- **Evidence boundary:** {{EVIDENCE_DATE_OR_PERIOD}}
- **Collector version:** {{COLLECTOR_VERSION}}
- **Finding set version:** {{FINDING_SET_VERSION}}
- **Known collection limitations:** {{LIMITATIONS}}

---

# Domain: {{DOMAIN_NAME}}

## Domain question

{{Plain-language question this domain answers, such as: Can the right people securely access AI and the information it can reach?}}

## Why this matters for AI

{{Explain how this domain affects safe adoption, data exposure, business trust, or operational success.}}

## Domain decision

**Status:** {{READY | READY_WITH_PREREQUISITES | NOT_READY | NOT_VERIFIED}}  
**Confidence:** {{HIGH | MEDIUM | LOW}}

{{Two to four sentence domain conclusion grounded in findings.}}

## Current-state capabilities

| Capability | Observed state | Status | Evidence reference |
|---|---|---|---|
| {{CAPABILITY}} | {{OBSERVED_FACT}} | {{STATUS}} | {{EVIDENCE_ID}} |

## Strengths

### {{STRENGTH_TITLE}}

- **Observed fact:** {{FACT}}
- **Readiness value:** {{WHY_IT_HELPS}}
- **Source:** `[Finding: {{FINDING_ID}}]`

## Findings requiring action

### {{FINDING_ID}}: {{FINDING_TITLE}}

- **Classification:** {{CONFIRMED_GAP | CONDITIONAL | INVESTIGATION_REQUIRED | IMPROVEMENT}}
- **Priority:** {{CRITICAL | HIGH | MEDIUM | LOW}}
- **Observed fact:** {{FACT_ONLY}}
- **Interpretation:** {{WHAT_THE_FACT_MEANS}}
- **Business impact:** {{CUSTOMER_RELEVANT_IMPACT}}
- **Population and boundary:** {{AFFECTED_SCOPE_AND_TIME_PERIOD}}
- **Limitations:** {{WHAT_THIS_EVIDENCE_DOES_NOT_PROVE}}
- **Recommendation:** {{ACTION}}
- **Closure evidence:** {{PROOF_REQUIRED_TO_CLOSE}}
- **Dependencies:** {{DEPENDENCIES}}
- **Owner:** {{OWNER_OR_TBD}}
- **Target horizon:** {{IMMEDIATE | NEAR_TERM | STRATEGIC}}
- **Evidence references:** {{EVIDENCE_IDS}}

## Evidence gaps and validation questions

### {{GAP_ID}}: {{GAP_TITLE}}

- **Missing evidence:** {{MISSING_DATA}}
- **Why it matters:** {{DECISION_IMPACT}}
- **Validation method:** {{COLLECTION_OR_WORKSHOP_ACTION}}
- **Do not conclude:** {{UNSUPPORTED_CONCLUSION_TO_AVOID}}

## Recommended domain actions

| Sequence | Action | Outcome | Closure evidence | Owner | Horizon |
|---:|---|---|---|---|---|
| 1 | {{ACTION}} | {{OUTCOME}} | {{CLOSURE_EVIDENCE}} | {{OWNER}} | {{HORIZON}} |

## Domain summary

{{Summarize the strongest evidence, unresolved question, and condition for readiness.}}

---

## Repeat for the standard domains

1. Identity and access
2. Content and collaboration
3. Information protection and data lifecycle
4. Applications, agents, connectors, and consent
5. Devices, endpoint security, and telemetry
6. AI governance and acceptable use
7. External and shadow AI visibility
8. Operational readiness, support, and adoption
9. Pilot readiness and measurement

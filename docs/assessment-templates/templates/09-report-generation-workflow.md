# AI Readiness Report Generation Workflow

## 1. Collect

**Inputs**
- Automated tenant and platform evidence
- Customer discovery notes
- Customer-provided policies and architecture
- Explicit scope, exclusions, and licensing assumptions

**Outputs**
- Raw evidence archive
- Collection manifest
- Collection coverage report

**Gate**
- Every collector records success, partial success, failure, unavailable, or not requested.

## 2. Normalize

Convert source-specific records into consistent domain objects. Preserve original values and attach source metadata.

**Outputs**
- Normalized datasets
- Source-to-normalized mapping
- Integrity and reconciliation checks

## 3. Evaluate controls

Apply deterministic checks where possible. Keep observed facts separate from interpretations.

**Outputs**
- Control results
- Metrics with populations and boundaries
- Candidate findings
- Evidence gaps

## 4. Curate findings

Review candidate findings for significance, duplication, evidence quality, and customer context.

**Required finding components**
- Stable ID
- Observed fact
- Interpretation
- Business impact
- Confidence and limitations
- Recommendation
- Closure evidence
- Source references

**Gate**
- No customer-facing statement without a finding or evidence-gap reference.

## 5. Build the canonical assessment result

Generate and validate `assessment-results.json` against the repository schema.

**Gate**
- IDs are unique.
- Cross-references resolve.
- Required fields are populated.
- Counts reconcile.

## 6. Generate customer deliverables

Render from the canonical result:
- Executive Assessment Report
- Domain Assessment Report
- AI Readiness Roadmap
- Executive Presentation
- Customer Action Plan
- Technical Evidence Workbook
- Evidence Portal

AI may improve narrative, prioritization language, and audience fit. AI must not invent missing evidence, owners, dates, commitments, or licensing entitlement.

## 7. Quality review

### Technical review
- Facts match evidence.
- Populations and time boundaries are stated.
- Product and licensing dependencies are marked for verification.
- Missing telemetry is not treated as low risk.

### Consultative review
- The decision is clear.
- Strengths, risks, and unknowns are balanced.
- Actions are prioritized and outcome-oriented.
- Pilot prerequisites are distinct from strategic maturity work.

### Editorial review
- Customer-facing language is direct and non-accusatory.
- Acronyms are expanded on first use.
- Internal codes are omitted unless useful to the customer.
- Tables support comparison rather than replacing the narrative.

## 8. Customer review and decision capture

Track:
- Customer corrections
- Business context
- Accepted risks and exceptions
- Owners and target dates only when agreed
- Additional evidence required

Do not overwrite observed facts with discussion notes. Record decisions separately and retain an audit trail.

## 9. Reassess

Re-run collection after closure evidence is available. Compare by stable control and finding IDs, record state changes, and regenerate all outputs from the updated canonical result.

## Suggested pipeline

```text
collect -> normalize -> evaluate -> curate -> validate
       -> render workbook and portal
       -> render reports, roadmap, and presentation
       -> review -> publish -> reassess
```

# Evidence Portal Specification

## Purpose

The HTML portal provides searchable technical drill-down. It should make evidence easy to locate without pretending to be the final customer narrative.

## Landing page

Display:
- Customer and tenant identifier suitable for the intended audience
- Assessment and collection dates
- Collector and schema versions
- Readiness decision with a link to the decision basis
- Collection coverage and limitations
- Links to findings, action plan, evidence library, and raw evidence manifest

## Primary navigation

1. Readiness decision
2. Domain results
3. Findings explorer
4. Customer action plan
5. Investigation register
6. Evidence library
7. Collection coverage
8. Method and definitions

## Findings explorer

Support filtering by:
- Domain
- Classification
- Severity
- Confidence
- Priority horizon
- Owner
- Status
- Evidence availability

Each result should show:
- Finding ID and title
- Observed fact
- Interpretation
- Business impact
- Recommendation
- Closure evidence
- Limitations
- Source links

## Finding detail page

```text
Finding ID and title
Status, severity, confidence, and domain
Observed fact
Interpretation
Business impact
Measured details and assessed population
Evidence limitations
Recommendation and dependency
Closure evidence
Related findings and workstreams
Source references and retained evidence
```

## Evidence library

Organize by domain and data source. Preserve:
- Evidence ID
- Source workload
- Collection command or endpoint
- Collection time
- Collection result
- Record count when known
- Raw file or response reference
- Related finding IDs

## Display rules

- Never color `Not verified` as green.
- Label zero records with the collection status and population boundary.
- Show a visible warning when collection was partial, failed, unavailable, or not requested.
- Keep exact values and units from evidence.
- Do not expose secrets, tokens, unnecessary personal data, or hidden administrative data.
- Provide print views for executive summary and finding detail, but keep raw tables optimized for screen use.

## Machine-readable output

Publish a versioned `assessment-results.json` alongside the portal. The portal should render from, or validate against, the same canonical finding set used by reports and presentations.

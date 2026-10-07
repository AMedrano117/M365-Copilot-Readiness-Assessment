# Technical Evidence Workbook Specification

## Purpose

The workbook is the technical evidence and traceability layer. It supports validation, investigation, customer follow-up, and reassessment. It is not the primary executive narrative.

## Required reading order

1. `Workbook Guide`
2. `Customer Readiness Review`
3. `Assessment Summary`
4. `Domain Results`
5. `Customer Action Plan`
6. `Finding Detail` or `Investigation Register`
7. `Evidence Index`
8. Technical and raw evidence sheets

## Recommended sheets

### Customer-facing and decision views

- `Workbook Guide`
- `Customer Readiness Review`
- `Assessment Summary`
- `Domain Results`
- `Customer Action Plan`
- `Rollout Progress`
- `Adoption Guidance`

### Investigation and traceability views

- `Finding Detail`
- `Investigation Register`
- `Discussion Exceptions`
- `Recommendation Trace`
- `Evidence Index`
- `Collection Coverage`
- `Run Manifest`
- `Integrity Checks`

### Technical detail

Create normalized sheets by source area, for example:

- `Identity Detail`
- `Conditional Access Detail`
- `Authentication Coverage`
- `Application Consent Detail`
- `SharePoint Governance`
- `Purview Policy Detail`
- `Device and Endpoint Detail`
- `AI Application Visibility`
- `Incident Detail`
- `Licensing and Service Plans`

### Raw evidence

- Retain raw source responses in separate sheets or an accompanying evidence archive.
- Preserve original values as collected.
- Record source, collection time, command or endpoint, status, and error details.

## Master findings columns

| Column | Requirement |
|---|---|
| Finding ID | Stable and unique across outputs |
| Domain | Canonical assessment domain |
| Title | Short customer-readable title |
| Classification | Confirmed gap, conditional, investigation required, improvement, strength |
| Observed fact | Evidence-only statement |
| Interpretation | Meaning of the fact, clearly separated |
| Business impact | Decision-relevant impact |
| Severity | Critical, high, medium, low, informational |
| Confidence | High, medium, low |
| Evidence gap | Missing data or limitation |
| Recommendation ID | Stable link to action plan |
| Recommendation | Outcome-oriented action |
| Closure evidence | What proves completion |
| Priority horizon | Immediate, near term, strategic |
| Effort | Low, medium, high, unknown |
| Owner | Named owner or TBD |
| Evidence references | One or more evidence IDs |
| Status | Open, accepted, in progress, closed, exception |

## Quality checks

- Every customer-facing claim maps to a finding or evidence gap.
- Every finding has at least one evidence reference or is explicitly marked as discovery-only.
- Counts reconcile across summary and detail sheets.
- Collection failures are not treated as zero values.
- `Not verified`, `Not applicable`, and `No records returned` remain distinct.
- Customer discussion notes are separated from observed technical facts.
- Closure requires evidence, not only a status change.

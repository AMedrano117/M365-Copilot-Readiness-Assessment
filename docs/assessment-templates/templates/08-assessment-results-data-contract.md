# Assessment Results Data Contract

## Goal

Provide AI agents and report generators with a compact, canonical finding set so they do not need to ingest the full workbook, portal, or raw evidence archive.

## File-level structure

```json
{
  "schemaVersion": "1.0.0",
  "assessment": {},
  "domains": [],
  "findings": [],
  "evidenceGaps": [],
  "recommendations": [],
  "evidenceIndex": []
}
```

## Required principles

- Findings use stable IDs across reassessments where the underlying control remains the same.
- `observedFact` contains only source-supported facts.
- `interpretation` explains meaning and must not introduce unsupported facts.
- `businessImpact` is customer-relevant and does not overstate certainty.
- `limitations` records population, time, collection, and licensing boundaries.
- A collection failure creates an evidence gap, not a passing finding.
- Recommendations reference findings and define closure evidence.
- Raw records remain outside the canonical file and are linked through evidence IDs.

## Suggested agent input strategy

1. Load assessment metadata and domain summaries.
2. Load only decision-significant findings.
3. Retrieve full finding objects on demand by ID.
4. Retrieve evidence metadata only when validating a claim.
5. Retrieve raw evidence only when the summarized fact is disputed or incomplete.

## Suggested chunking

Create separate, deterministic files when the canonical file becomes large:

```text
assessment-summary.json
findings/ID-001.json
domains/identity-and-access.json
recommendations/action-plan.json
evidence/evidence-index.json
```

Maintain a manifest with hashes and schema versions so agents can retrieve the smallest valid unit and verify consistency.

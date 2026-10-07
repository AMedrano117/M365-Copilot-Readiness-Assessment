# AI Readiness Assessment Deliverable Templates

Reusable, customer-neutral templates for converting AI readiness evidence into a consultative assessment package.

## Design principles

- Lead with the readiness decision, business impact, and next actions.
- Separate observed facts, interpretations, evidence gaps, and recommendations.
- Treat the workbook and HTML portal as evidence layers, not the primary customer story.
- Do not interpret missing telemetry as a pass or as proof that risk is absent.
- Keep customer-facing reports concise and link every material statement to a finding or evidence reference.
- Distinguish prerequisites for a controlled pilot from longer-term maturity improvements.

## Package contents

| File | Purpose | Primary audience |
|---|---|---|
| `templates/01-executive-assessment-report.md` | Concise readiness decision and priorities | Executives and sponsors |
| `templates/02-domain-assessment-report.md` | Detailed domain-by-domain narrative | IT, security, compliance, data owners |
| `templates/03-ai-readiness-roadmap.md` | Sequenced workstreams and closure criteria | Program owners and technical leads |
| `templates/04-executive-presentation-storyboard.md` | Slide-by-slide presentation content | Assessment presenter |
| `templates/05-technical-evidence-workbook-spec.md` | Workbook design and traceability requirements | Engineers and reviewers |
| `templates/06-evidence-portal-spec.md` | HTML portal structure and behavior | Engineers and technical reviewers |
| `templates/07-customer-action-plan.md` | Assignable, trackable customer workstreams | Customer and delivery team |
| `templates/08-assessment-results-data-contract.md` | Canonical findings contract | Developers and AI agents |
| `templates/09-report-generation-workflow.md` | End-to-end report workflow | Developers and assessment owners |
| `schemas/assessment-results.schema.json` | JSON Schema for structured findings | Developers and validation pipelines |
| `examples/assessment-results.example.json` | Synthetic example payload | Developers and template authors |

## Recommended repository layout

```text
docs/
  assessment-templates/
    README.md
    templates/
    schemas/
    examples/
```

## Template conventions

- Replace tokens in double braces, such as `{{CUSTOMER_NAME}}`.
- Omit sections that do not apply. Do not leave empty customer-facing headings.
- Use `Not verified` when evidence is insufficient.
- Use `Not applicable` only when the capability is demonstrably outside scope.
- Keep exact values in the evidence layer. Summarize only when the source population and time boundary remain clear.
- Customer statements and workshop notes do not close technical findings without agreed supporting evidence.

## Canonical statuses

- `Ready`
- `Ready with prerequisites`
- `Not ready for pilot`
- `Not verified`

## Canonical time horizons

- `Immediate`: 0-30 days
- `Near term`: 1-3 months
- `Strategic`: 3-12 months

These horizons are planning defaults, not delivery commitments.

# Validation workflow

This guide is for developers. Start each assessment or report change with the fastest layer
that can prove it, and run a live collection only as the final validation.

Every layer reuses the production code:
- declared scenario suites;
- `run_offline_report`, for rebuilds;
- `render_snapshot`, for render-only output;
- the shared assessment-result serializer.

`tools/validate.py` blocks and records any tenant access attempt. That includes non-loopback
network connections, credential token requests and PowerShell launches.

Outputs are written to a new engineering folder. By default this is a system temporary folder
named `assessment-validation-*`. Each output folder:
- is labelled `ENGINEERING VALIDATION ONLY — NOT CUSTOMER-READY`;
- is never eligible for customer publication;
- never creates assessment history, a baseline or a lifecycle delta;
- is never written inside a retained package or the repository's `Reports` folder.

Retained packages are read, copied and hash-verified, never changed.

## Choose a layer

| Change | Layer | Duration |
| --- | --- | --- |
| A rule, classification or calculation | 1. Unit and invariant tests, then 2. Scenarios | Seconds to minutes |
| Methodology or decision logic | 2. Scenarios, then 3. Offline rebuild | About 10 minutes |
| HTML, Excel, wording or layout only | 4. Render-only | About 5 minutes |
| A collector or importer | 5. Focused diagnostic, then 3. Offline rebuild | Varies |
| A new permission, endpoint or authentication path | 6. Full live collection | About 30 minutes |

## 1. Unit and invariant tests

```powershell
python -m unittest discover -s tests -p "test_<area>*.py"
python -m unittest discover -s tests
```

- **Tenant access:** none; collectors are mocked.
- **Proves:** each rule and invariant, across the full protected suite.
- **Cannot prove:** behaviour on real tenant data shapes.

## 2. Scenario validation

```powershell
python tools/validate.py scenarios
python tools/validate.py scenarios --name "License evidence versus entitlement"
```

- **Inputs:** fictional declared scenarios, listed in
  [tests/scenarios/workflow_manifest.json](../tests/scenarios/workflow_manifest.json).
  - Each scenario declares its expectations before evaluation: source states, control results,
    evidence sufficiency, readiness treatment, findings, action count and prohibited conclusions.
  - The fixtures live with their existing suites under `tests/scenarios/`.
- **Outputs:** a pass/fail test summary and whether tenant access was attempted.
- **Proves:** the declared decision behaviour of the production code.
- **Cannot prove:** real export formats or the completeness of the full report.

The groups cover:
- unread versus complete-empty sources;
- Conditional Access policy and legacy-authentication events;
- license evidence versus entitlement;
- device records versus distinct devices;
- SharePoint Advanced Management unavailable, with equivalent evidence;
- a supported component plus an unread required component;
- duplicate evidence supporting one action;
- a presentation-only render change.

## 3. Offline package validation

```powershell
python tools/validate.py rebuild --package "Reports\Cavehouse Brewery\<package>"
python tools/validate.py rebuild --package "<package>" --fail-on-semantic-change
```

- **Inputs:** a retained completed package, containing `collection.json` and `assessment-run.json`.
  The Cavehouse Brewery test-tenant package is the engineering reference. Never use a customer
  package.
- **Steps:**
  1. Hash the original package.
  2. Copy it to the engineering folder.
  3. Run a Standalone offline rebuild of the copy with the production code. It keeps the retained
     evaluation and observation dates.
  4. Compare the rebuilt result with the retained result.
  5. Render the rebuilt snapshot again as a replay check.
  6. Verify that the original package's hashes are unchanged.
- **Outputs:** `comparison.md` and `comparison.json`, plus the rebuilt deliverables. The report
  records:
  - the source package, its hash and the producing commits;
  - methodology and taxonomy versions;
  - changes to controls, findings, actions, customer actions, priorities and readiness treatment;
  - changes to stable IDs and evidence references;
  - HTML and Excel differences, and the replay result;
  - semantic and presentation-only differences, and any unexpected regressions.

  Unexpected regressions include a reassigned stable ID, a dropped open finding, a changed
  evaluation date, and an observation date later than the retained collection.
- **Tenant access:** none.
- **Proves:** how the current code changes conclusions on real retained evidence.
- **Cannot prove:** anything about evidence the collection did not retain.

The source gate blocks a rebuild if the checkout changes while it runs, so don't edit files
during a rebuild.

## 4. Render-only validation

```powershell
python tools/validate.py render --snapshot "<package>\assessment-results\RUN-<id>.json"
python tools/validate.py render --snapshot "<snapshot>" --compare-to "<earlier engineering folder>\render"
```

- **Behaviour:** reads an existing assessment-result snapshot and renders HTML and both workbooks
  without re-evaluating evidence.
- **Failure rule:** any semantic difference fails, including decision, controls, findings,
  actions, identities or lifecycle. `--compare-to` lists presentation-only differences against an
  earlier render.
- **Tenant access:** none.
- **Proves:** a presentation change leaves conclusions intact, and every output opens.
- **Cannot prove:** that the conclusions themselves are right. Use layers 2 and 3 for that.

## 5. Focused diagnostics

```powershell
python tools/validate.py diagnostics
```

This lists the existing focused diagnostics and whether each one accesses a tenant:

| Diagnostic | Tenant access |
| --- | --- |
| `--check-connections` preflight | Read-only probes; no assessment output |
| `tools/diagnose-usage-report.py` and `tools/diagnose-usage-report-download.ps1` | Read-only |
| `tools/Test-IntuneEvidence.ps1` | Read-only GET requests |
| `tools/validate_offline_report.py` (synthetic package audit), `tools/audit_report_signal.py` and `tools/audit_workbook.py` | None |

## 6. Full live collection

Run a full live collection only as final validation:
- after a collector, permission, endpoint, authentication or module change;
- when a new Microsoft data shape must be observed;
- before release.

Use the Cavehouse Brewery test tenant. Offline layers cannot prove live permissions, throttling,
licensing responses, or data that was never collected.

## Comparing two snapshots

```powershell
python tools/validate.py compare BEFORE.json AFTER.json
python tools/validate.py compare BEFORE.json AFTER.json --mode render
```

In `render` mode, any semantic difference is reported as an unexpected regression.

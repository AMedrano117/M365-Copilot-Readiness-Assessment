# Assessment repository review (2026-11-02)

Review of commit `a2240f8` on `main`. Read-only: no production code, tests, schemas,
outputs or customer data were changed. Findings are labelled by how they were established:

- **Probe**: reproduced by calling production functions with invented inputs (scratch scripts
  outside the repository).
- **Retained output**: observed in locally retained result snapshots (counts and keys only).
- **Code reading**: traced in source, not executed.

## A. Executive assessment

**The architecture is viable and worth keeping.** The newer layers — source envelopes,
`Core/tenant_baseline.py`, `Core/assessment_result.py`, the device identity model, the
investigation contract, the evidence catalog and snapshot replay — encode most of the
intended methodology, often better than typical assessment tooling. The main weakness is
that this newer model sits on top of an older licence-plan-driven recommendation layer
(`Recommendations/**`, `Core/get_*_info.py`) whose text is corrected afterwards by regular
expressions. Most misleading behaviour found in this review comes from that seam, or from
severity being hard-coded per rule without regard to scope or count.

**Can it support the intended AI-readiness use case today?** Partly. It reliably separates
"could not read" from "configured" for most Purview, device and SharePoint sources, and its
evidence traceability is strong. It is not yet reliable enough to let the headline verdict
stand without consultant review, because several common, legitimate tenant configurations
produce High "blocks readiness" actions.

**Highest confidence:** Purview source-state handling, device identity reconciliation,
SharePoint tenant sharing configuration, legacy-authentication event classification,
risky-user requalification, snapshot replay and output integrity checks.

**Lowest confidence:** Conditional Access interpretation beyond the simple "All users / All
apps / mfa" case, licence-gated identity findings (consent, guests, access reviews),
data-exposure severity, any legacy module row without a `FindingKey`, Teams and Exchange
coverage (largely absent).

**Biggest risks of misleading findings**

1. Legitimate Conditional Access designs reported as High failures (probe-confirmed).
2. Findings generated only when a licence plan is present, so the same configuration is
   assessed differently by SKU (code reading; consistent with retained outputs).
3. Presence-based High severity: one Anyone link, one organization link or one failed
   device setting can set the tenant verdict (retained output and code reading).
4. An unread source sometimes described as an absent or disabled configuration
   (probe-confirmed for security defaults and the legacy Conditional Access row).

## B. Architecture and data flow

```text
Entry: main.py -> Core/cli_parser.py -> Core/run_plan.py
  live:    Core/orchestrator.orchestrate
  offline: Core/offline_report.run_offline_report  (same processing path)
  render:  --replay-snapshot -> Core/assessment_replay.render_snapshot (no re-evaluation)

COLLECTION (Core/orchestrator_pipelines.create_pipelines)
  Graph:      Core/get_m365_client, get_entra_client, get_defender_client, ai_usage,
              sharepoint_graph_settings, purview_graph_labels
  PowerShell: collect_purview_data.ps1 (Purview + Exchange org/audit/IRM config),
              collect_sharepoint_governance.ps1, collect_power_platform_*.ps1
  Imports:    data_exposure_assessment (SAM/DAG, DSPM), portal_report_import,
              copilot_readiness_import, ai_baseline_import, purview_cache_import
        |
        |  *Findings are already produced here*: get_entra_info / get_purview_info /
        |  get_defender_info / get_m365_info loop over licensed service plans and call
        |  Recommendations/<service>/<PLAN>.py; m365_pipeline also builds SharePoint,
        |  report-privacy and Copilot-audit recommendations.
        v
RAW EVIDENCE + COLLECTION-TIME FINDINGS -> collection.json (Core/collection_checkpoint,
  offline_collection). Live mode reloads this file, so live and offline share one path.
        v
PROCESSING (Core/processor.process_and_print_all_information)
  SharePoint findings rebuilt from payload (processor.py:163-169)
  Data exposure from imported reports (build_data_exposure_assessment)
  Regex requalification of saved findings (saved_control_checks.qualify_saved_recommendations,
    identity_investigation.qualify_identity_recommendation)
  Raw-evidence checks (assess_purview_configuration, assess_defender_configuration)
  Tenant-wide baseline (tenant_baseline.assess_tenant_baseline)
  reconcile_overlapping_evidence (string matching on Observation)
        v
NORMALIZATION (Core/evidence_layer.build_evidence_bundle)
  assessment_model.enrich_assessment_records (text-derived Disposition / ImpactArea)
  _build_verified_strengths (second strength classifier)
  assessment_catalog.collect_assessment_sources (native records, ~90 catalog checks)
  expanded_findings (device setting / protection findings from native records)
        v
DECISION (Core/assessment_result.build_assessment_result)
  device_findings / privileged_presentation requalification
  _qualify_record: provenance, freshness, EvidenceStatus supported|limited|gap|historical
  finding_reconciliation.group_findings (dedupe by FindingKey + scope boundary)
  18 required questions (QUESTIONS) -> _matches_question / _supports_question / _gap
  operational_evidence (4 controls need dated operational review to pass)
  verdict: blockers -> answered*2 < required -> conditions -> ready
        v
OUTPUTS (frozen assessment_result)
  workbook_layout / export_recommendations.export_to_excel (customer + technical xlsx)
  customer_report / export_to_html, pilot_summary (readiness summary)
  html_evidence_pages (optional), assessment_serialization (--snapshot-json)
```

**Material differences from the intended concept**

- Finding generation happens before normalization, inside collection-time "info" modules,
  and those findings are persisted in `collection.json`. Replay re-applies current regex
  qualifiers to old text instead of re-deriving the finding from evidence (exceptions:
  SharePoint findings and some device families are rebuilt).
- There is no single rule registry. Decisions are spread over legacy plan modules,
  `tenant_baseline`, `saved_control_checks`, `evidence_layer._build_verified_strengths`,
  `expanded_findings`, `data_exposure_assessment` and `assessment_result`.
- Several routing decisions are keyword-based on human-readable text
  (`assessment_model.NEGATIVE_CONDITION`, `infer_impact_area`, `assessment_result.domain_for`,
  `_matches_question`, `_action` topics, `processor.reconcile_overlapping_evidence`).

## C. Domain review

| Domain | Current implementation | Strengths | Weaknesses / gaps | FP risk | FN risk |
|---|---|---|---|---|---|
| Identity / CA / MFA | `tenant_baseline.conditional_access_facts`, `assess_identity_baseline`; legacy `AAD_PREMIUM*.py`; `authentication_findings`, `authentication_methods` | Report-only and disabled policies never count; registration is kept separate from enforcement; legacy sign-in outcomes strictly classified | Policies judged one at a time; group targeting and OR grants become High failures; unread security defaults read as "off"; duplicate legacy CA row | **High** | Medium |
| Privileged access | `privileged_identity`, `privileged_assignments`, `privileged_presentation` | Temporal states (permanent, eligible, expired) modelled; legacy P2 "permanent GA" High row demoted to Opportunity | Break-glass accounts not recognised as such; role analysis triggered partly through P2 module text | Low–Medium | Medium |
| App consent / grants | Legacy `AAD_PREMIUM_P2.py`, `AAD_GOVERNANCE.py`; `qualify_saved_recommendations` rewrites consent text | Consent wording corrected to assigned-policy semantics | **Only produced when the P2 or Governance plan exists**; otherwise APPS.CONSENT gap says settings "were not collected" | Medium | **High** |
| Guests / access reviews | Same P2/Governance modules | Licensed-guest wording is careful | Licence-gated, same as consent | Low | High |
| SharePoint / OneDrive sharing | `sharepoint_governance.build_sharepoint_recommendations`, Graph partial settings | Partial Graph coverage explicitly kept open; enum normalisation; rebuilt on replay | Organization default link is a Medium Action; remediation text prescriptive | Medium | Low |
| Content exposure (SAM/DSPM) | `data_exposure_assessment._findings_from_scan` | Freshness windows, scope findings, overlapping counts not summed, tenant ID checks | Any count ≥1 is High and blocks readiness; partial exports promoted to "supported" (`assessment_result.py:266-274`) | **High** | Low |
| Teams | Usage reports only | — | No Teams policy, guest/external access, meeting/transcript or app-permission evidence | n/a | **High** |
| Exchange | Org/audit/IRM config via Purview PowerShell | Audit disabled detected from explicit Boolean | No mailbox-auditing, transport, external-forwarding evidence | Low | Medium |
| Sensitivity labels | Graph beta definitions, PowerShell labels and label policies; `assess_data_protection_baseline` | Preview evidence cannot pass; publishing evaluated separately from definitions; group-scoped publishing is "confirm" | Default label, mandatory labelling, auto-labelling not collected; legacy "≥4 labels" threshold (High) | Low–Medium | Medium |
| DLP | `dlp_facts`, `assess_purview_configuration`, legacy DLP modules | Simulation/disabled never counts; Copilot location recognised; operational review required to pass | One workload at "All" (for example Exchange only) satisfies the configuration component; rule content not assessed | Low | Medium |
| Retention / audit | Baseline retention rows; audit Boolean | Audit `False` only from explicit value | "No retention policy" is an Action though described as planning; attribute named `retention_labels` holds policies; retention labels never collected | Medium | Low |
| Devices / Intune | `device_evidence`, `device_compliance`, `device_findings`, `expanded_findings`, `INTUNE_A.py` | Namespaced stable-ID joins, no name joins, conflicts unresolved, stale/retired/duplicate classes, overall noncompliance is not a blocker, empty inventory is not "unmanaged" | One current failed "security" setting on one device → High blocker; any recognised setting not in the activity list is "security"; Entra dual-state/duplicate objects without shared IDs count as separate identities | Medium | Low |
| Defender | `assess_defender_configuration`, device health findings | Incident query incomplete → Coverage; RTP-off is an Opportunity | High-risk devices → High Action with pilot wording; exposure score default 0 (unused) | Medium | Medium |
| Shadow AI | `ai_usage.collect_shadow_ai_usage` (preview), catalog `EXTERNAL_AI.*` | No streams → unknown; candidates labelled as candidates; optional domain | Keyword list classification; no governance conclusion possible (correctly) | Low | n/a |
| AI governance / process | `assessment_catalog` owner-review checks, readiness reviews | Owner reviews cannot erase observed failures; 35-day freshness | No explicit AI acceptable-use, training or ownership checks in the required set | Low | Medium |
| Licensing | `services_and_licenses`, `service_categorization.resolve_plan_statuses`, license rows → Reference | "is active in" rows become low-confidence Reference | Licence plans decide whether findings exist; disabled plan + High → Action "upgrade required"; zero Copilot seats → Coverage | Medium | **High** |

## D. Finding quality examples

**Strong**

- `defender.incidents.current` (`saved_control_checks.py:265-303`): incomplete query →
  Coverage; complete and empty → Assurance with scope; active → Action with counts.
- Risky users (`identity_investigation.qualify_identity_recommendation`): risk state vs
  level separated; unknown state qualified; incomplete source never yields "no risk".
- Customer Lockbox (`saved_control_checks._lockbox_investigation`): refuses to treat a legacy
  defaulted `False` as a measured disabled state.
- Device noncompliance: downgraded to an Opportunity explaining that "Is active" failures are
  reporting conditions, not encryption failures.

**Weak**

- Legacy P1 "No Conditional Access policies were returned by this inventory" (High) emitted
  when the read failed (probe S7) and duplicating the baseline row when it succeeded (S8).
- "Only N sensitivity label(s) configured — insufficient granularity" (High) from an invented
  four-label threshold (`MIP_S_CLP1.py`).

**Ambiguous**

- Retention: "This is a planning condition; it does not block a pilot" inside an Action row.
- Connectors present → Coverage "Review Copilot connectors" (a manual governance review shown
  as an evidence gap).
- Zero Copilot seats → Coverage "Acquire Copilot licenses" (procurement shown as a gap).

**Potentially misleading**

- "No enforced Conditional Access policy requires MFA for all users… and security defaults
  are off" for a tenant whose all-users policy requires *MFA or a compliant device* (probe S3),
  whose MFA policy targets an all-employees group (S4), or whose security-defaults read failed
  (S5).
- `data_exposure.broad_internal_access` as a readiness blocker from 3 Everyone, 34 EEEU and 14
  organization-link permissions (retained output).
- Action titles "Microsoft Entra ID P1" / "Microsoft Entra ID P2" for MFA-registration and
  consent conditions (retained output, probe).

## E. Test quality

Suite: 2,113 tests, all passing, 922 s (Python 3.12, this workstation).
`tools/validate_offline_report.py`: three synthetic cases (complete, active incident,
evidence drill-down) passed integrity, cross-output and Excel checks in 866 s. Together
the two CI validation steps take about 30 minutes locally.

| Category | Examples | What it proves |
|---|---|---|
| Unit (decision) | `test_tenant_baseline`, `test_device_evidence_interpretation`, `test_methodology_v4`, `test_data_exposure_assessment` | Individual fact extractors behave as written |
| Invariant-style | `test_assessment_integrity`, `test_shared_assessment_result`, `test_evidence_reconciliation` | Unknown ≠ zero, provenance never changes IDs, reconciliation rules |
| Regression | `test_authentication_corrections`, `test_publication_corrections`, `test_remaining_evidence_defects` | Previously reported defects stay fixed |
| Integration | 45 files call `build_assessment_result`; 10 call `process_and_print_all_information` | Pipeline composes |
| Presentation / rendering | `test_report_readability`, `test_workbook_layout`, `test_offline_report` (248 s alone) | Layout, links, privacy, consistency |
| Collection (mocked) | `test_entra_collection_failures`, `test_purview_collection`, `test_http_retry` | Envelope and retry behaviour |
| Infrastructure | `test_repository_provenance` (80), `test_permission_profile_setup` (57), `test_run_plan` (42), `test_persistent_identity` (39) | Packaging, identity, provenance |

**What the tests do not prove**

- No direct tests of `infer_disposition` / `NEGATIVE_CONDITION` (0 test files), although they
  decide Action vs Opportunity for every legacy row.
- `test_methodology_v4.test_exclusions_or_grant_alternatives_cannot_establish_tenant_wide_mfa`
  checks only that the fact is not "enforced_mfa_all"; it never checks the resulting finding,
  which is a High Action.
- No test exercises legacy plan modules and the baseline together for one condition.
- No scenario asserts that a tenant without P2 gets the same consent/guest conclusions.
- Expected values in many integration tests are tied to current wording.
- Test modules cannot run individually with `python -m unittest tests.<module>`; mixed import
  styles require `discover -s tests -p <file>`.
- One fully synthetic tenant (`tests/synthetic_package_fixture.py`) in two variants; no
  scenario library with independently declared expectations.

## F. Development and performance

- Fast logic tests exist (0.2–10 s per decision module) but are not separated from rendering
  tests; the full suite is ~15 minutes, CI allows 75.
- Offline rebuild from `collection.json` and `--replay-snapshot` already provide
  "no recollection" and "render only" paths. These are strengths.
- Missing: a focused diagnostic ("explain control X / finding Y for this collection"), a
  scenario-fixture runner, and a small reference dataset per domain.

## G. Prioritized improvement plan

### P0 — could materially mislead an assessment (required corrections)

1. **Conditional Access semantic gaps** (`Core/tenant_baseline.py`). OR-grant alternatives and
   group-targeted MFA must yield an explicit "confirm effective coverage" outcome, as
   `docs/METHODOLOGY.md` already states, not a High Action. Unread security defaults must not
   be described as "off".
2. **Licence-gated finding generation for tenant settings.** Consent, app grants, guests and
   access reviews must be evaluated from collected evidence regardless of the P2/Governance
   plan; licence state should only qualify capability (for example PIM availability).
3. **Unread source → absent configuration.** Legacy P1 CA row (`AAD_PREMIUM.py:95-106`) must
   not emit "No Conditional Access policies were returned" when `ca_policies` was not
   completely read, and must not duplicate `baseline.identity.sign_in` when it was.
4. **Presence-based High severity sets the verdict** (`data_exposure_assessment.py:962-998`,
   `expanded_findings.py:164`). Decide explicitly whether one link, one EEEU grant or one
   device setting should block tenant readiness. This is a methodology decision for you;
   the review recommends scope/proportion-aware severity and a distinct "exposure to review"
   outcome.

### P1 — validation weaknesses

5. Scenario library with independently declared expectations (Section 15/16 of the review).
6. Direct tests for text-driven classifiers, then replace keyword routing with explicit
   `Disposition`/`ControlId`/`FindingKey` on every producer.
7. "One condition → one action" invariant test across legacy and baseline producers.
8. Make individual test modules runnable and split fast logic tests from rendering tests.
9. Fix the latent key mismatch in `AAD_PREMIUM_CONDITIONAL_ACCESS.py` (reads
   `policies_requiring_mfa` / `policies_targeting_m365`, which `entra_insights` never emits)
   or delete the module; it appears unreachable in retained outputs.

### P2 — report and readability

10. Collection coverage is ~22,300 of ~44,000 words in the latest retained customer HTML
    (2026-10-28 build); move it to the technical workbook or an appendix.
11. Evidence gaps carry "High priority" badges and sort with remediation; show evidence gaps
    without remediation priority.
12. Replace licence-plan names as action titles.
13. Mark prescriptive remediation as "one option" where alternatives exist.

### P3 — maintainability and performance

14. Single rule registry (rule ID, version, control, severity basis, evidence inputs).
15. Retire legacy plan modules progressively behind the registry.
16. Output organisation clean-up (see Section 19 of the review).

## H. Proposed next implementation pass

**Identity evidence-state correctness pass** (bounded to IDENTITY.AUTH and APPS.CONSENT):

1. Add `tests/scenarios/identity/` fixtures with expectations declared before running:
   S2 break-glass exclusions, S3 OR grant, S4 group-targeted MFA, S5 unread security
   defaults, S7 CA read failure, S8 zero policies, consent-allowed without P2,
   consent-allowed with P2.
2. Confirm each fails for the documented reason.
3. Smallest fixes in `tenant_baseline.assess_identity_baseline`, the legacy P1 CA branch and a
   licence-independent consent producer.
4. Add invariants: unread source never yields an Action or "absent" wording; one condition
   produces one action; licence presence does not change a configuration conclusion.
5. Rebuild the Cavehouse package offline (`--run-type Standalone`) and compare verdict and
   identity actions before and after.

Out of scope for that pass: data-exposure severity (needs your methodology decision first),
report layout, rule registry.

# Content and sharing: audit and validation

This page is for developers. It records the bounded audit of the Content & Sharing assessment
path, the Microsoft product behavior it relies on, the defects found, and the gaps deferred to a
later pass. All test data is fictional. No customer package was used.

## Evidence path

| Stage | Code |
| --- | --- |
| Acquisition | `collect_sharepoint_governance.ps1` (SharePoint admin), `Core/sharepoint_graph_settings.py` (Graph, partial fields), `--reports-dir` / `--sam-report` / `--dspm-report` exports, the governance import |
| Normalization | `Core/sharepoint_governance.py` (`merge_sharepoint_payloads`, `summarize_sharepoint_governance`, `setting_name`), `Core/data_exposure_assessment.py` (`_scan_reports`), `Core/sharepoint_governance_evidence.py` (`load_governance_evidence`), `Core/evidence_records.py` (`_sharing`) |
| Evaluation and findings | `build_sharepoint_recommendations`, `_findings_from_scan`, `assess_sharepoint_governance`, `evidence_layer._build_verified_strengths` |
| Control result | `Core/assessment_result.py` (`_matches_question`, `component_result`, `aggregate_control_status`) |
| Actions | `Core/customer_actions.py` |
| Rendering | `Core/customer_report.py` (`_settings`, `_governance_outcomes`), `Core/configuration_groups.py`, `Core/evidence_layer.py` (SharePoint Governance sheet) |

## Controls reviewed

The repository's canonical control taxonomy is reused: `Observed`, `Action required` and
`Not established` (with `not_applicable` components). No new control was added.

| Control | Security question | Evidence | Evaluation | Freshness | Coverage classification |
| --- | --- | --- | --- | --- | --- |
| `CONTENT.SHARING` | Do tenant sharing defaults limit who new links reach? | Tenant settings from SharePoint admin (full) or Graph (partial: no OneDrive, default link, Anyone expiry or link permission) | `sharepoint.sharing.*` findings and the `sharepoint-sharing-restricted` strength | Collection time; no setting-level timestamp | See defects D1–D4, D8, D9 |
| `CONTENT.PERMISSIONS` | Who can already reach business content? | DAG permission snapshots, special-group and sharing-link reports; governance import | `data_exposure.*` findings (presence-only unless sensitivity and audience are retained); governance outcomes | Report date, 35-day threshold | Working as designed, except D8 and D10 |
| `CONTENT.OWNERSHIP` | Do sites have accountable owners and inactive-site decisions? | Content Management Assessment, governance import | Governance outcomes | Report date, 90-day lifecycle threshold | Working as designed; the SharePoint admin site inventory's `Owner` is collected but not evaluated (deferred) |
| `DATA.EXPOSURE` | Who can reach sensitive content? | Purview DSPM exports | DSPM findings; a missing export is a Coverage gap | Report date, 8-day threshold | Working as designed |

Concept coverage:

| Concept | Current handling |
| --- | --- |
| Tenant external-sharing boundary | Evaluated (`SharingCapability`) |
| Site-specific external sharing | Collected and normalized; the configured value was counted without the tenant ceiling (D5) |
| OneDrive external sharing | Evaluated with SharePoint for "permits Anyone"; not read through Graph |
| Default sharing-link type | Evaluated; `None` was mis-evaluated (D1, D2) |
| Anyone link configuration | Evaluated; expiry and edit permission only stated when Anyone was the default (D3) |
| Existing Anyone links, organization links, Everyone, EEEU, external recipients | DAG/DSPM exports, presence-only review unless materiality is retained; units kept separate |
| Guest expiration, guest resharing | Collected and shown in the sharing table; **normalized but not evaluated** in a control (deferred) |
| Site ownership, inactive sites | Lifecycle export and governance import |
| Sensitive-content exposure | DSPM; missing evidence stays a Coverage gap |
| DAG evidence | Report inventory (collector) and parsed exports; activity reports are 28-day, top-site results |
| Freshness | Report dates; undated and future-dated evidence stays qualified |
| Access review and remediation lifecycle | Governance import outcomes (`owner_review_process`, `exceptions`) |

## Documented Microsoft product behavior

Each item is the basis for a semantic rule. Sources are Microsoft Learn.

- `DefaultSharingLinkType = None` uses the widest scope permitted: Anyone when Anyone links are
  enabled, otherwise organization links.
  ([Set-SPOTenant](https://learn.microsoft.com/powershell/module/microsoft.online.sharepoint.powershell/set-spotenant))
- A site's sharing setting must be the same as or more restrictive than the organization setting;
  OneDrive can be more restrictive than SharePoint but not more permissive. If external sharing is
  turned off for the organization, it becomes unavailable on sites and existing shared links stop
  working; turning it back on restores the site setting and the links.
  ([Manage sharing settings](https://learn.microsoft.com/sharepoint/turn-external-sharing-on-or-off),
  [Change the sharing settings for a site](https://learn.microsoft.com/sharepoint/change-external-sharing-site))
- `RequireAnonymousLinksExpireInDays` is an upper bound for new Anyone links; `0` removes the
  requirement. If the expiry changes, existing links keep their expiry when the new limit is
  longer, and update when it is shorter.
- `ExternalUserExpirationRequired`: disabling the policy does not clear expiry values already set
  on guests.
- DAG sharing-link and EEEU activity reports cover links or sharing created in the last 28 days for
  the top sites. They are not a complete inventory. Permission-state reports are snapshots that
  capture data up to 48 hours before generation.
  ([Data access governance reports](https://learn.microsoft.com/sharepoint/data-access-governance-reports),
  [DAG with PowerShell](https://learn.microsoft.com/sharepoint/powershell-for-data-access-governance))
- Creating an Anyone or organization link does not by itself make content searchable or reachable
  through Copilot; the link must be redeemed.
  ([How shareable links work](https://learn.microsoft.com/sharepoint/shareable-links-anyone-specific-people-organization))

Methodology choices (not product behavior): Specific people as the default link, no Anyone
links (or Anyone links that expire within 30 days and are view-only), guest expiry and preventing
guest resharing. The last two are the recommended hardening baseline shown in the sharing table;
they are not required components of `CONTENT.SHARING`.

## Defects found before correction

| ID | Defect | Classification |
| --- | --- | --- |
| D1 | `DefaultSharingLinkType = None` with Anyone permitted is an effective Anyone default, but it was not treated as one. With no expiry, the High finding became the Medium one. | Evaluated incorrectly |
| D2 | `None` with Anyone prohibited is an effective organization-link default, but no finding or strength was produced. The control gap then said the settings "were not collected". | Evaluated incorrectly |
| D3 | The Medium Anyone finding omitted a missing expiry and edit permission unless Anyone was the default. | Evaluated but not stated |
| D4 | The `sharepoint-sharing-restricted` strength asserted that Anyone sharing is not enabled when `SharingCapability` was not returned, or when OneDrive permitted Anyone. It carried no source association, scope, expected rule or methodology reference, so it could never support the control. | Apparently positive with insufficient evidence; positive provenance missing |
| D5 | The site count "permit Anyone sharing" counted configured site values even when the tenant boundary prohibits Anyone. | Configured and effective state conflated |
| D6 | In investigation records, a site whose expiry was not retained was given the tenant expiry as its effective value, although the override state was unknown. | Missing site evidence filled from tenant evidence |
| D7 | `sharing` Action rows have no source association (`sharepoint_governance_detail` has no source mapping), so a measured sharing condition is always a confirmation. | Source collected but provenance not normalized |
| D8 | `sharepoint.dag.not_assessed` (missing DAG reports) was a component of `CONTENT.SHARING`. DAG reports answer existing access (`CONTENT.PERMISSIONS`), so an unrelated evidence gap kept the sharing-defaults control unresolved. | Evaluated against the wrong control |
| D9 | When Graph and SharePoint admin disagreed on a setting, the conflict was retained but never reached evaluation; a positive strength could be produced from one source. | Conflict silently resolved |
| D10 | The DAG report inventory treated completed reports as recent for 30 days, while the methodology threshold for SAM reports is 35 days. | Freshness rule inconsistent |
| D11 | `setting_name` read the numeric value `0` as empty, so SharePoint PowerShell's `0` (`Disabled`, `None`) became "not returned". Found by the fail-first tests. | Normalization defect |

## Corrections

All corrections reuse the production path; the tests call the same functions.

| Defect | Correction |
| --- | --- |
| D1, D2 | `effective_default_link` resolves `None` to the link type it produces. Without the boundary, the default stays not established. |
| D3 | The Anyone finding states a missing expiry and edit permission. Every sharing finding carries `SharingSettingResults`: for each setting, the observed and effective value, expected rule, result (`meets`, `does_not_meet`, `not_applicable`, `not_established`), requirement (`CONTENT.SHARING` or `hardening`) and basis. Results establish configuration only. |
| D4 | `sharing_strength` emits the strength only when every `CONTENT.SHARING` requirement meets or is not applicable, from a full (not Graph-partial), unconflicted source. It carries `EvidenceSource`, `EvidenceScope`, `ObservedValues`, `ExpectedRule`, `MethodologyRule` and `SharingSettingResults`, so the shared qualifier can support it. |
| D5 | The summary keeps `configured_anyone_site_count` and `effective_anyone_site_count` (the tenant ceiling applied; unknown without the tenant value). |
| D6 | A site's effective expiry stays unknown unless the site value or `OverrideTenantAnonymousLinkExpirationPolicy` establishes it. Site records add `effectiveSharingSetting` beside the configured value. |
| D8 | `sharepoint.dag.*` rows are components of `CONTENT.PERMISSIONS` only. |
| D9 | Graph and SharePoint admin values are compared after normalization (enum numbers versus names are not conflicts). A remaining conflict on a sharing setting prevents the strength and adds `sharepoint.sharing.conflicting_sources`, with both values and sources retained. |
| D10 | The inventory uses the SAM threshold (35 days, or the frozen `sam_report_max_age_days`). |
| D11 | Only `None` is treated as absent. |

The Anyone exposure finding now says the report records links as of its date and does not show
whether each link still works.

## Customer scorecard

`Core/content_scorecard.py` projects the shared result into four sections at the top of the
Content domain in the customer report: What's working, Needs improvement, Exposure to review and
Not established. It shows counts of controls by their existing status and no score, percentage or
grade. Established items show their observation date, scope and source. When the sharing defaults
are established and exposure evidence exists, the summary states that both are reported and that
neither changes the other. The scorecard is computed at render time and never changes a
conclusion.

## Conditional Anyone-link settings and sub-control strengths

**Root cause.** `evaluate_tenant_sharing` already returned `not_applicable` for Anyone-link
settings when Anyone links were off. The customer settings table, the grouped configuration rows
and the workbook used a separate presentation rule, `customer_report._sharing_recommendation`.
That rule returned `ok` ("Meets recommendation") for Anyone expiry and file and folder permission
whenever Anyone links were not detected. That included a boundary that was not returned at all. A
stored View and edit value under a disabled boundary was therefore shown as meeting a View-only
recommendation.

**Applicability.** `RequireAnonymousLinksExpireInDays`, `FileAnonymousLinkType` and
`FolderAnonymousLinkType` (`ANYONE_DEPENDENT_SETTINGS`) apply only where the effective boundary
permits Anyone links. `anyone_links_permitted` resolves that boundary as True, False or not
established. At site scope the tenant setting is the ceiling, plus the OneDrive setting for a
personal site. `anyone_applicability` maps the boundary to `applicable`, `not_applicable` or
`not_established`.

| Effective boundary | Setting result | Customer state |
| --- | --- | --- |
| Anyone not permitted | `not_applicable`; the configured value is kept | Not applicable while Anyone links are off |
| Anyone permitted | Evaluated against the methodology rule | Meets, Change or Review |
| Not established or conflicting | `not_established` | Review |

An inactive value is never counted as meeting or failing a rule. It never produces a finding,
remediation or strength, and it is evaluated normally if Anyone links become permitted. Site
evidence records carry `anyoneLinkSettingsApplicability`. It is `not_established` when the site
read was not verified.

**Sub-control strengths.** `sharing_sub_strengths` derives one strength for each protection whose
returned, unconflicted value meets its rule. Each strength carries the observed value, expected
rule, scope, source, corroborating sources and methodology rule. Strengths are qualified with the
same rule as other findings (`_qualify_record`). They are kept in `content_strengths`, outside the
records, so they are never control components and never change a conclusion. The scorecard shows
only current, supported strengths. When `CONTENT.SHARING` is Observed, only hardening strengths
are added, because the control already states the rest.

| Key | Established when |
| --- | --- |
| `content.sharing.anyone_creation_restricted` | SharePoint and OneDrive external sharing meet the rule (no Anyone links) |
| `content.sharing.sharepoint_guest_boundary` / `onedrive_guest_boundary` | Only that workload's setting meets the rule |
| `content.sharing.default_link_specific_people` | The effective default link is Specific people |
| `content.sharing.anyone_link_expiry_required` / `anyone_links_view_only` | Anyone links are permitted and the setting meets the rule |
| `content.sharing.guest_expiration_required` / `guest_resharing_prevented` | The hardening setting is enabled (hardening only) |

When SharePoint administration and Graph return the same value, a single strength is produced and
both sources are listed. A strength describes configuration only. It never states that existing
links are absent or that access is effectively governed.

**Validation.** The fail-first tests are in `tests/test_content_sharing_conditional.py`. Before the
correction, 31 of 32 failed. The one that passed is an invariant that already held: a dormant
value creates no finding. One existing expectation in
`tests/test_customer_sharing_settings.py` asserted that a moot expiry was `ok`, which is the
defect, so it now expects `not_applicable`. The scenario layer passed: 16 groups and 50 tests.
Render-only from the retained Cavehouse snapshot was an ExactMatch.

The offline Cavehouse rebuild showed presentation-only differences: 0 semantic changes, 0
display-ID conflicts and no findings added or removed. Control results and the decision are
unchanged. Cavehouse permits Anyone links, so its Anyone-link settings are applicable, and Graph
does not return their values, so they stay unresolved. The scorecard adds one supported hardening
strength, that guests cannot reshare.

## Validation

| Layer | Result |
| --- | --- |
| Unit and invariant tests (`tests/test_content_sharing_rules.py`) | Fail-first: 24 of 30 failed before correction, plus 2 for D11; all pass after |
| Declared scenarios (`tests/test_content_sharing_scenarios.py`, harness `tests/content_sharing_scenarios.py`) | Fail-first: 27 of 31 failed; all pass after. Six groups are registered in `tests/scenarios/workflow_manifest.json` |
| Render-only on the retained Cavehouse snapshot | ExactMatch; no semantic difference |
| Offline Cavehouse rebuild | PresentationOnlyDifferences; 0 semantic differences, 0 display-ID conflicts, source package unchanged |

The Cavehouse collection uses Graph tenant settings only, so the corrected rules leave its control
results unchanged. The DAG coverage gap moves from `CONTENT.SHARING` to `CONTENT.PERMISSIONS`;
both stay Not established.

Suspected issues found to be working as designed: zero rows in an activity report are reported as
"no activity" and not as absence; overlapping exposure measures are never summed; superseded
snapshots are excluded from counts; repeated rows from the same export merge on their identity;
missing DSPM evidence is a Coverage gap; "no exposure signal" is a limited, not supported,
assurance because exports are not complete; undated DAG reports are not recent.

## Deferred coverage gaps

- **No semantic transitions are declared yet.** A retained package with full SharePoint admin
  settings, `None` default links, numeric `0` values or Graph/admin conflicts can show semantic
  differences on rebuild. Declare them in `tools/semantic_transitions.json` against the introducing
  commit when such a package is validated.

- **D7 is deferred.** Supplying a scope to the existing `sharepoint.sharing.*` Action rows changes
  `EvidenceScope`, which is a material field of the finding's semantic key. Their display IDs are
  allocated sequentially, so a retained package would report a `DisplayIdReuseConflict` for the
  same display ID. The correction needs a declared identity transition first. When it is made, the
  organization-default finding should carry `ReadinessTreatment: ConfirmBeforePilot`, matching its
  existing customer lane.
- Guest expiration and guest resharing are evaluated as hardening results but do not change a
  control. Making them required components needs a methodology decision.
- The methodology text "Anyone links expire or are disabled" and the evaluation (Anyone permitted
  is a Medium action) differ; the evaluation is unchanged in this pass.
- Site `Owner` from the SharePoint admin inventory is not evaluated for `CONTENT.OWNERSHIP`.
- The SharePoint Governance workbook sheet summarizes DAG inventory without the evaluation date, so
  its "Recent" labels depend on the build date.
- Graph does not return OneDrive sharing, default link, Anyone expiry or link permission; these
  remain unverified on the Graph-only path.
- There is no strength for a site with a more restrictive effective boundary than the tenant. It
  needs verified per-site reads and a population statement.
- When Anyone links are permitted, the settings table shows an Anyone expiry of 31 to 730 days as
  Review. The setting evaluator records `does_not_meet`. Neither is shown as Meets, and the
  difference is unchanged in this pass.
- The workbook evidence projection resolves the boundary from each record's own values. Site
  records do not carry the tenant-level Anyone-link settings, so this has no effect today.

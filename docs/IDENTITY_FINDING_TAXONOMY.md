# Identity finding taxonomy and licensing separation

The shared naming authority is `Core/identity_finding_taxonomy.py`, version 1.0.0. Semantic findings are built, reconciled and assigned persistent identity before `project_identity_result` adds display metadata. `Feature` is retained as compatibility/source input. `FindingTitle` is the condition title used by retained deliverables.

## Authority and fields

`resolve_identity_finding` selects an explicit known `FindingCondition`, a registry condition in scoped `FindingType`, stable `FindingKey`, a narrow control condition, scoped privileged grant context, or a unique documented legacy observation signature. Recommendation IDs, severity, sheet names, row order, counts and the first source record do not select the condition. Unknown or ambiguous legacy mappings retain their label with `TaxonomyState=unresolved` and a structured compatibility warning. No finding split or governance target is inferred.

`FindingCondition`, `FindingType`, `Capability`, `ConditionEvidenceLevel`, `TaxonomyAuthority`, `FindingTaxonomyVersion`, `OriginalCompatibilityLabel` and `TaxonomyDiagnostics` are additive projection fields. `ObservedCondition`, `RequiredOutcome`, `Scope`, `ObservationPeriod`, `Limitation`, `CustomerAction`, configuration, enforcement and observed-operation states are copied only from existing declared values. Missing semantic fields are not synthesized. Original `EvidenceLevel` and all conclusions remain unchanged; `ConditionEvidenceLevel` describes the naming category.

`validate_identity_taxonomy` checks the registry/version/type/title/capability/evidence-layer agreement in recommendations, action buckets and domains. Missing documented route dependencies and inconsistent structured names block publication through `require_valid_assessment`. Qualified legacy labels remain readable. Identity, reconciliation, lifecycle and governance validators retain their existing responsibilities.

## Licensing

`AssociatedLicenseFeatures` preserves the original P1/P2 feature as source context, independently of `LicensingDependencies`. An associated feature is not a universal prerequisite. Dependencies use existing technical guidance plus documented capability routes: standard Conditional Access uses P1; risk-based Conditional Access uses P2; access reviews require scenario-specific P2 or Governance confirmation. Privileged access does not automatically imply PIM or a P2 requirement. MFA registration does not imply a premium license requirement.

`LicenseEvidenceState` remains unknown without retained service-plan inventory. Only a returned successful relevant service-plan row supports `LicenseObserved`; unknown statuses remain qualified. License availability does not establish per-user entitlement, configuration, enforcement, observed operation or effectiveness. Service Plan Inventory and raw/native evidence fields retain their original names.

Primary Microsoft references: [Conditional Access requirements](https://learn.microsoft.com/en-us/entra/identity/conditional-access/overview), [Governance licensing by scenario](https://learn.microsoft.com/en-us/entra/id-governance/licensing-fundamentals), [MFA licensing](https://learn.microsoft.com/en-us/entra/identity/authentication/concept-mfa-licensing).

## Projection and compatibility

`Flagged By` remains a compatibility list of linked condition titles. Separate `Capability`, `Licensing Dependency`, `Associated License Feature` and `Source Signal` columns describe supporting context. Sheet-wide context does not identify an affected population; existing selectors and retained evidence remain authoritative. Duplicate self-aliases do not create false ambiguity; truly ambiguous aliases do not fan out. MFA explicitly-false detail rows add `Record Condition=User explicitly not registered for MFA` without changing their aggregate finding or IDs.

`build_assessment_result`, snapshot serialization/read, Excel/HTML export, customer report, pilot summary, technical recommendation rows and investigation preparation share the resolver. Recommendations keeps its existing column order and projects the title in Feature, appending original compatibility and licensing columns. Findings Lineage, Evidence Index, Investigation Items and technical detail use the shared title. Nonidentity recommendation labels retain their existing behavior. Baseline control wording remains visible as supporting control context in the pilot summary.

Projection copies only finding records and summary containers; it does not duplicate large evidence graphs or mutate raw source records. Snapshot reads enrich runtime copies without rewriting files. Completed baselines, counts, semantic IDs, source occurrences, evidence references, compatibility aliases, history, closure rules and governance logs are preserved. Title-only projection does not enter persistent-ID or delta calculation. Packages retain sanitized snapshot bytes and existing integrity checks.

`prior_report_import._recommendations` recognizes the new workbook taxonomy marker and restores the retained Compatibility Feature as the semantic/source Feature before legacy fingerprint calculation. It retains the displayed FindingTitle, condition and licensing context. Archived worksheet rows and workbook bytes are unchanged. Legacy workbooks without the marker keep their existing import behavior.

## Condition registry

| Stable condition | Finding title | Capability | Condition evidence layer |
|---|---|---|---|
| `mfa.registration` | MFA registration coverage | Multifactor authentication registration | `registration_state` |
| `mfa.not_registered` | User explicitly not registered for MFA | Multifactor authentication registration | `registration_state` |
| `mfa.registration_unknown` | MFA registration state unknown | Multifactor authentication registration | `registration_state` |
| `mfa.registration_conflicting` | MFA registration state conflicting | Multifactor authentication registration | `registration_state` |
| `mfa.enforcement` | MFA enforcement coverage | Multifactor authentication enforcement | `policy_enforcement` |
| `mfa.enforcement_not_established` | Tenant-wide MFA enforcement not established | Multifactor authentication enforcement | `evidence_gap` |
| `mfa.enforcement_report_only` | MFA policy in report-only mode | Conditional Access | `configuration` |
| `mfa.admin_enforcement_not_established` | Administrative MFA enforcement not established | Conditional Access | `evidence_gap` |
| `methods.passwordless` | Passwordless authentication registration | Authentication methods | `registration_state` |
| `methods.phishing_resistant` | Phishing-resistant method registration | Authentication methods | `registration_state` |
| `methods.phone` | Phone-based method registration | Authentication methods | `registration_state` |
| `methods.phone_only` | Phone-only MFA registration | Authentication methods | `registration_state` |
| `methods.system_preferred` | System-preferred authentication configuration | Authentication methods | `configuration` |
| `sspr.registration` | SSPR registration coverage | Self-service password reset | `registration_state` |
| `sspr.enablement` | SSPR enablement coverage | Self-service password reset | `configuration` |
| `sspr.capability` | SSPR capability coverage | Self-service password reset | `registration_state` |
| `conditional_access.policy_coverage` | Conditional Access policy coverage | Conditional Access | `configuration` |
| `conditional_access.report_only` | Conditional Access policies in report-only mode | Conditional Access | `configuration` |
| `conditional_access.exclusions` | Conditional Access exclusions requiring review | Conditional Access | `configuration` |
| `conditional_access.observed_outside_coverage` | Sign-ins outside expected Conditional Access coverage | Conditional Access | `observed_authentication` |
| `conditional_access.observed_result` | Observed Conditional Access sign-in results | Conditional Access | `observed_authentication` |
| `authentication.observed_mfa` | Observed MFA during successful sign-ins | Multifactor authentication | `observed_authentication` |
| `authentication.single_factor` | Successful single-factor sign-ins | Authentication | `observed_authentication` |
| `authentication.protection` | MFA and legacy-authentication enforcement coverage | Authentication protection | `policy_enforcement` |
| `authentication.operation` | Authentication protection in observed operation | Authentication protection | `observed_authentication` |
| `legacy.attempts` | Review legacy authentication sign-ins | Legacy authentication | `observed_authentication` |
| `legacy.success` | Successful legacy-authentication sign-ins | Legacy authentication | `observed_authentication` |
| `legacy.ca_blocked` | Legacy attempts blocked by Conditional Access | Legacy authentication | `observed_authentication` |
| `legacy.failed_other` | Legacy attempts failed for another reason | Legacy authentication | `observed_authentication` |
| `legacy.unknown` | Legacy-authentication outcome unknown | Legacy authentication | `evidence_gap` |
| `legacy.blocking_coverage` | Legacy-authentication blocking coverage | Conditional Access | `policy_enforcement` |
| `privileged.scope_purpose` | Privileged access scope and purpose review | Privileged access | `entity_state` |
| `privileged.permanent` | Permanent privileged access requiring business-purpose review | Privileged access | `entity_state` |
| `privileged.mfa_unregistered` | Privileged identities explicitly not registered for MFA | Privileged authentication registration | `registration_state` |
| `privileged.disabled` | Disabled privileged identities retaining access | Privileged access | `entity_state` |
| `privileged.activity_unknown` | Privileged activity not established | Privileged activity | `evidence_gap` |
| `privileged.inactivity` | Potentially inactive privileged identities | Privileged activity | `observed_operation` |
| `privileged.unresolved` | Unresolved privileged identities | Privileged access | `evidence_gap` |
| `privileged.eligible` | Eligible privilege requiring review | Privileged access | `entity_state` |
| `privileged.emergency_purpose` | Emergency-access purpose requiring validation | Emergency access | `entity_state` |
| `privileged.emergency_activity` | Emergency-access activity requiring review | Emergency access | `observed_authentication` |
| `privileged.emergency_attempt` | Emergency-access authentication attempt requiring review | Emergency access | `observed_authentication` |
| `privileged.emergency_change` | Emergency-access configuration change requiring review | Emergency access | `configuration` |
| `privileged.service_purpose` | Service-account purpose requiring validation | Service and synchronization account purpose | `entity_state` |
| `privileged.service_interactive` | Interactive service-account activity requiring review | Service-account authentication | `observed_authentication` |
| `privileged.legacy` | Privileged identities with successful legacy authentication | Privileged authentication | `observed_authentication` |
| `privileged.single_factor` | Privileged identities with successful single-factor authentication | Privileged authentication | `observed_authentication` |
| `privileged.ca` | Privileged sign-ins outside expected Conditional Access coverage | Privileged authentication | `observed_authentication` |
| `access_review.coverage` | Access review coverage | Access reviews | `configuration` |
| `access_review.definitions` | Access review definitions | Access reviews | `configuration` |
| `access_review.privileged` | Privileged access review not established | Access reviews | `evidence_gap` |
| `risk.users` | Risky users requiring investigation | Identity Protection | `risk_detection` |
| `risk.detections` | Identity risk detections requiring review | Identity Protection | `risk_detection` |
| `risk.disposition` | Risk disposition not established | Identity Protection | `evidence_gap` |
| `risk.policy` | Identity risk policy coverage | Identity Protection | `configuration` |
| `guest.lifecycle` | Guest access lifecycle review | External collaboration | `entity_state` |
| `guest.stale` | Stale guest review candidates | External collaboration | `entity_state` |
| `guest.administrator` | Guest administrator access requiring review | External collaboration | `entity_state` |
| `guest.invitation` | Guest invitation policy coverage | External collaboration | `configuration` |
| `guest.cross_tenant` | Cross-tenant access policy coverage | External collaboration | `configuration` |
| `consent.policy` | User consent policy coverage | Application consent | `configuration` |
| `consent.high_privilege` | High-privilege application grants requiring review | Application consent | `configuration` |
| `consent.unverified_publisher` | Unverified publisher grants | Application consent | `configuration` |
| `consent.owner_unknown` | Application owner not established | Application consent | `evidence_gap` |
| `consent.inventory` | Application grant inventory coverage | Application consent | `configuration` |
| `licensing.group_assignment` | Group-based license assignment coverage | Group-based licensing | `license_inventory` |

License subjects additionally use `license.<service-plan label>` and `<service-plan label> license availability` when inventory is the actual observed subject.

## Stable-key mappings

| Finding key | Condition |
|---|---|
| `entra.authentication.mfa_registration` | `mfa.registration` |
| `entra.signins.legacy_auth` | `legacy.attempts` |
| `baseline.identity.sign_in` | `authentication.protection` |
| `baseline.identity.scope` | `conditional_access.exclusions` |
| `coverage.identity.auth` | `authentication.protection` |
| `coverage.operation.identity.auth` | `authentication.operation` |
| `operation.identity.auth` | `authentication.operation` |
| `coverage.identity.mfa` | `mfa.registration` |
| `coverage.identity.admin` | `privileged.scope_purpose` |
| `entra.identity_risk.users` | `risk.users` |
| `entra.app_consent.high_impact_grants` | `consent.high_privilege` |
| `entra.app_consent.grant_inventory_not_assessed` | `consent.inventory` |
| `entra.group_licensing.not_assessed` | `licensing.group_assignment` |
| `entra.privileged.activeprivilegewithoutmfaregistration` | `privileged.mfa_unregistered` |
| `entra.privileged.activeprivilege.privilegedsuccessfullegacyauthentication` | `privileged.legacy` |
| `entra.privileged.activeprivilege.privilegedobservedsinglefactorsuccess` | `privileged.single_factor` |
| `entra.privileged.activeprivilege.privilegedsuccessfulsigninoutsideexpectedcacoverage` | `privileged.ca` |
| `entra.privileged.disabledidentitywithcurrentprivilege` | `privileged.disabled` |
| `entra.privileged.permanentprivilegeneedsreview` | `privileged.permanent` |
| `entra.privileged.activeprivilege.potentialinactivity` | `privileged.inactivity` |
| `entra.privileged.privilegedactivityunknown` | `privileged.activity_unknown` |
| `entra.privileged.unresolvedprivilegedidentity` | `privileged.unresolved` |
| `entra.privileged.emergencyaccesscandidateunverified` | `privileged.emergency_purpose` |
| `entra.privileged.emergencyaccessrecentlyused` | `privileged.emergency_activity` |
| `entra.privileged.emergencyaccessauthenticationattemptneedsreview` | `privileged.emergency_attempt` |
| `entra.privileged.emergencyaccessconfigurationneedsreview` | `privileged.emergency_change` |
| `entra.privileged.eligibleprivilegewithoutrecentactivationevidence` | `privileged.eligible` |
| `entra.privileged.servicepurposeunverified` | `privileged.service_purpose` |
| `entra.privileged.interactiveuseobservedforserviceaccount` | `privileged.service_interactive` |

## Deferred contract work

This is an additive correction to the existing shared assessment result. The assessment templates and `schemas/assessment-results.schema.json` remain the future canonical-output contract. Full schema migration must align condition, capability, licensing, evidence layers and customer action fields explicitly; this stage does not claim schema conformance. No collection endpoints, beta Graph API, technical thresholds, license purchasing decisions, automatic decisions, ticketing, dashboard or App Builder exports are introduced.

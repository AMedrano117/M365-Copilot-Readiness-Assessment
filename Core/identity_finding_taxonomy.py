"""Versioned identity condition naming, independent of evidence evaluation and PFI.

Feature remains the compatibility/source feature. FindingTitle is the display
authority. This module never alters semantic keys, conclusions or raw evidence.
"""
from copy import deepcopy
import re

VERSION = '1.0.0'

# condition: (title, capability, condition evidence level)
CONDITIONS = {
    'mfa.registration': ('MFA registration coverage', 'Multifactor authentication registration', 'registration_state'),
    'mfa.not_registered': ('User explicitly not registered for MFA', 'Multifactor authentication registration', 'registration_state'),
    'mfa.registration_unknown': ('MFA registration state unknown', 'Multifactor authentication registration', 'registration_state'),
    'mfa.registration_conflicting': ('MFA registration state conflicting', 'Multifactor authentication registration', 'registration_state'),
    'mfa.enforcement': ('MFA enforcement coverage', 'Multifactor authentication enforcement', 'policy_enforcement'),
    'mfa.enforcement_not_established': ('Tenant-wide MFA enforcement not established', 'Multifactor authentication enforcement', 'evidence_gap'),
    'mfa.enforcement_report_only': ('MFA policy in report-only mode', 'Conditional Access', 'configuration'),
    'mfa.admin_enforcement_not_established': ('Administrative MFA enforcement not established', 'Conditional Access', 'evidence_gap'),
    'methods.passwordless': ('Passwordless authentication registration', 'Authentication methods', 'registration_state'),
    'methods.phishing_resistant': ('Phishing-resistant method registration', 'Authentication methods', 'registration_state'),
    'methods.phone': ('Phone-based method registration', 'Authentication methods', 'registration_state'),
    'methods.phone_only': ('Phone-only MFA registration', 'Authentication methods', 'registration_state'),
    'methods.system_preferred': ('System-preferred authentication configuration', 'Authentication methods', 'configuration'),
    'sspr.registration': ('SSPR registration coverage', 'Self-service password reset', 'registration_state'),
    'sspr.enablement': ('SSPR enablement coverage', 'Self-service password reset', 'configuration'),
    'sspr.capability': ('SSPR capability coverage', 'Self-service password reset', 'registration_state'),
    'conditional_access.policy_coverage': ('Conditional Access policy coverage', 'Conditional Access', 'configuration'),
    'conditional_access.report_only': ('Conditional Access policies in report-only mode', 'Conditional Access', 'configuration'),
    'conditional_access.exclusions': ('Conditional Access exclusions requiring review', 'Conditional Access', 'configuration'),
    'conditional_access.observed_outside_coverage': ('Sign-ins outside expected Conditional Access coverage', 'Conditional Access', 'observed_authentication'),
    'conditional_access.observed_result': ('Observed Conditional Access sign-in results', 'Conditional Access', 'observed_authentication'),
    'authentication.observed_mfa': ('Observed MFA during successful sign-ins', 'Multifactor authentication', 'observed_authentication'),
    'authentication.single_factor': ('Successful single-factor sign-ins', 'Authentication', 'observed_authentication'),
    'authentication.protection': ('MFA and legacy-authentication enforcement coverage', 'Authentication protection', 'policy_enforcement'),
    'authentication.operation': ('Authentication protection in observed operation', 'Authentication protection', 'observed_authentication'),
    'legacy.attempts': ('Review legacy authentication sign-ins', 'Legacy authentication', 'observed_authentication'),
    'legacy.success': ('Successful legacy-authentication sign-ins', 'Legacy authentication', 'observed_authentication'),
    'legacy.ca_blocked': ('Legacy attempts blocked by Conditional Access', 'Legacy authentication', 'observed_authentication'),
    'legacy.failed_other': ('Legacy attempts failed for another reason', 'Legacy authentication', 'observed_authentication'),
    'legacy.unknown': ('Legacy-authentication outcome unknown', 'Legacy authentication', 'evidence_gap'),
    'legacy.blocking_coverage': ('Legacy-authentication blocking coverage', 'Conditional Access', 'policy_enforcement'),
    'privileged.scope_purpose': ('Privileged access scope and purpose review', 'Privileged access', 'entity_state'),
    'privileged.permanent': ('Permanent privileged access requiring business-purpose review', 'Privileged access', 'entity_state'),
    'privileged.mfa_unregistered': ('Privileged identities explicitly not registered for MFA', 'Privileged authentication registration', 'registration_state'),
    'privileged.disabled': ('Disabled privileged identities retaining access', 'Privileged access', 'entity_state'),
    'privileged.activity_unknown': ('Privileged activity not established', 'Privileged activity', 'evidence_gap'),
    'privileged.inactivity': ('Potentially inactive privileged identities', 'Privileged activity', 'observed_operation'),
    'privileged.unresolved': ('Unresolved privileged identities', 'Privileged access', 'evidence_gap'),
    'privileged.eligible': ('Eligible privilege requiring review', 'Privileged access', 'entity_state'),
    'privileged.emergency_purpose': ('Emergency-access purpose requiring validation', 'Emergency access', 'entity_state'),
    'privileged.emergency_activity': ('Emergency-access activity requiring review', 'Emergency access', 'observed_authentication'),
    'privileged.emergency_attempt': ('Emergency-access authentication attempt requiring review', 'Emergency access', 'observed_authentication'),
    'privileged.emergency_change': ('Emergency-access configuration change requiring review', 'Emergency access', 'configuration'),
    'privileged.service_purpose': ('Service-account purpose requiring validation', 'Service and synchronization account purpose', 'entity_state'),
    'privileged.service_interactive': ('Interactive service-account activity requiring review', 'Service-account authentication', 'observed_authentication'),
    'privileged.legacy': ('Privileged identities with successful legacy authentication', 'Privileged authentication', 'observed_authentication'),
    'privileged.single_factor': ('Privileged identities with successful single-factor authentication', 'Privileged authentication', 'observed_authentication'),
    'privileged.ca': ('Privileged sign-ins outside expected Conditional Access coverage', 'Privileged authentication', 'observed_authentication'),
    'access_review.coverage': ('Access review coverage', 'Access reviews', 'configuration'),
    'access_review.definitions': ('Access review definitions', 'Access reviews', 'configuration'),
    'access_review.privileged': ('Privileged access review not established', 'Access reviews', 'evidence_gap'),
    'risk.users': ('Risky users requiring investigation', 'Identity Protection', 'risk_detection'),
    'risk.detections': ('Identity risk detections requiring review', 'Identity Protection', 'risk_detection'),
    'risk.disposition': ('Risk disposition not established', 'Identity Protection', 'evidence_gap'),
    'risk.policy': ('Identity risk policy coverage', 'Identity Protection', 'configuration'),
    'guest.lifecycle': ('Guest access lifecycle review', 'External collaboration', 'entity_state'),
    'guest.stale': ('Stale guest review candidates', 'External collaboration', 'entity_state'),
    'guest.administrator': ('Guest administrator access requiring review', 'External collaboration', 'entity_state'),
    'guest.invitation': ('Guest invitation policy coverage', 'External collaboration', 'configuration'),
    'guest.cross_tenant': ('Cross-tenant access policy coverage', 'External collaboration', 'configuration'),
    'consent.policy': ('User consent policy coverage', 'Application consent', 'configuration'),
    'consent.high_privilege': ('High-privilege application grants requiring review', 'Application consent', 'configuration'),
    'consent.unverified_publisher': ('Unverified publisher grants', 'Application consent', 'configuration'),
    'consent.owner_unknown': ('Application owner not established', 'Application consent', 'evidence_gap'),
    'consent.inventory': ('Application grant inventory coverage', 'Application consent', 'configuration'),
    'licensing.group_assignment': ('Group-based license assignment coverage', 'Group-based licensing', 'license_inventory'),
}

KEYS = {
    'entra.authentication.mfa_registration': 'mfa.registration',
    'entra.signins.legacy_auth': 'legacy.attempts',
    'baseline.identity.sign_in': 'authentication.protection',
    'baseline.identity.scope': 'conditional_access.exclusions',
    'coverage.identity.auth': 'authentication.protection',
    'coverage.operation.identity.auth': 'authentication.operation',
    'operation.identity.auth': 'authentication.operation',
    'coverage.identity.mfa': 'mfa.registration',
    'coverage.identity.admin': 'privileged.scope_purpose',
    'entra.identity_risk.users': 'risk.users',
    'entra.app_consent.high_impact_grants': 'consent.high_privilege',
    'entra.app_consent.grant_inventory_not_assessed': 'consent.inventory',
    'entra.group_licensing.not_assessed': 'licensing.group_assignment',
}
PRIVILEGED_KEYS = {
    'ActivePrivilegeWithoutMFARegistration': 'privileged.mfa_unregistered',
    'ActivePrivilege.PrivilegedSuccessfulLegacyAuthentication': 'privileged.legacy',
    'ActivePrivilege.PrivilegedObservedSingleFactorSuccess': 'privileged.single_factor',
    'ActivePrivilege.PrivilegedSuccessfulSignInOutsideExpectedCACoverage': 'privileged.ca',
    'DisabledIdentityWithCurrentPrivilege': 'privileged.disabled',
    'PermanentPrivilegeNeedsReview': 'privileged.permanent',
    'ActivePrivilege.PotentialInactivity': 'privileged.inactivity',
    'PrivilegedActivityUnknown': 'privileged.activity_unknown',
    'UnresolvedPrivilegedIdentity': 'privileged.unresolved',
    'EmergencyAccessCandidateUnverified': 'privileged.emergency_purpose',
    'EmergencyAccessRecentlyUsed': 'privileged.emergency_activity',
    'EmergencyAccessAuthenticationAttemptNeedsReview': 'privileged.emergency_attempt',
    'EmergencyAccessConfigurationNeedsReview': 'privileged.emergency_change',
    'EligiblePrivilegeWithoutRecentActivationEvidence': 'privileged.eligible',
    'ServicePurposeUnverified': 'privileged.service_purpose',
    'InteractiveUseObservedForServiceAccount': 'privileged.service_interactive',
}
KEYS.update({'entra.privileged.' + key.lower(): value for key, value in PRIVILEGED_KEYS.items()})

# Compatibility signatures describe the original condition, not the sheet,
# display ID, severity or the first native evidence record. Multiple distinct
# matches are diagnosed; no priority ordering hides ambiguity.
LEGACY = {
    'mfa.registration': r'known registration flags|mfa registration|(?:mfa registered|enrolled in mfa|mfa enrolled)',
    'methods.passwordless': r'passwordless.*(?:registered|registration|method)|(?:use|using) passwordless authentication',
    'methods.phone_only': r'phone.only (?:mfa|authentication|method|registration)',
    'methods.phishing_resistant': r'phishing.resistant.*registration',
    'methods.phone': r'phone.based method registration',
    'methods.system_preferred': r'system.preferred authentication',
    'sspr.registration': r'sspr registration',
    'sspr.enablement': r'sspr enablement',
    'sspr.capability': r'sspr capabil',
    'conditional_access.policy_coverage': r'conditional access polic(?:y|ies).*(?:found|returned|configured|coverage|inventory)|conditional access policies:',
    'conditional_access.report_only': r'^conditional access policies in report.only',
    'mfa.enforcement_not_established': r'^(?:no tenant.wide mfa|tenant.wide mfa enforcement not established)',
    'legacy.blocking_coverage': r'^legacy.authentication blocking',
    'legacy.attempts': r'legacy.authentication (?:sign.ins|sign.in|attempts)|legacy authentication sign.in',
    'privileged.scope_purpose': r'current permanent or duration.unverified active assignments|privileged.*scope and purpose|role assignment schedules|permanent admin role assignment|eligible admin role assignment',
    'access_review.definitions': r'access review definitions',
    'access_review.coverage': r'access reviews? (?:configured|active|monitoring)|active access reviews|access review\(s\)|access governance active|no access reviews configured',
    'risk.users': r'risky users?|at.risk user|confirmed.compromised user',
    'risk.detections': r'risk detections?',
    'risk.policy': r'risk.based conditional access polic',
    'guest.lifecycle': r'guest users? (?:have|in|with)|guest access lifecycle',
    'guest.invitation': r'guest invitation|guest user invitations',
    'guest.cross_tenant': r'cross.tenant (?:access|partner)|partner.specific overrides',
    'consent.policy': r'user.role consent|user consent|user.consent|admin consent|consent policy|consent boundary|self.consent',
    'consent.high_privilege': r'^application grants requiring review|^risky applications detected',
    'consent.unverified_publisher': r'externally owned application.*verified.publisher|unverified publisher grants',
    'consent.inventory': r'^application grant inventory|^the assessed grant rules',
    'licensing.group_assignment': r'group.based licens|groups?.*(?:use license assignment|configured for copilot licens|manage copilot license)|groups?\(s\).*licens|license assignment errors',
}
LICENSES = {'Microsoft Entra ID P1': {'AAD_PREMIUM', 'AAD_PREMIUM_P1'},
            'Microsoft Entra ID P2': {'AAD_PREMIUM_P2'},
            'Microsoft Entra MFA': {'MFA_PREMIUM'}}


def _identity_row(row):
    return (str(row.get('Service', '')).lower() == 'entra'
            or str(row.get('ControlId', '')).startswith('IDENTITY.')
            or str(row.get('FindingKey', '')).startswith('entra.')
            or row.get('FindingCondition') in CONDITIONS
            or str(row.get('Feature', '')).startswith('Microsoft Entra ID P'))


def _resolve(row):
    explicit = row.get('FindingCondition')
    if explicit:
        known = isinstance(explicit, str) and (explicit in CONDITIONS or
            explicit.startswith('license.') and explicit[len('license.'):] in LICENSES)
        return explicit if known else None, 'explicit_condition'
    if row.get('FindingType') in CONDITIONS:
        return row['FindingType'], 'scoped_finding_type'
    key = str(row.get('FindingKey') or '').lower()
    if key in KEYS:
        return KEYS[key], 'condition_key'
    if row.get('ControlId') == 'IDENTITY.MFA':
        return 'mfa.registration', 'control_condition'
    text = str(row.get('OriginalObservation') or row.get('Observation') or '').lower()
    # Pass B rewrites the current supported scope; the preserved old declaration
    # must not replace that current scope/purpose condition in presentation.
    if row.get('PrivilegedAssignmentKeys') is not None:
        return 'privileged.scope_purpose', 'scoped_privileged_grants'
    label = str(row.get('OriginalCompatibilityLabel') or row.get('OriginalFeature') or row.get('Feature') or '')
    if label in LICENSES and (row.get('EvidenceBasis') == 'License signal'
            or re.match(r'^' + re.escape(label.lower()) + r' is (?:active in|included in|licensed|pendingactivation|disabled)', text)):
        return 'license.' + label, 'license_inventory_subject'
    candidates = {condition for condition, pattern in LEGACY.items() if re.search(pattern, text)}
    return (next(iter(candidates)) if len(candidates) == 1 else None), 'legacy_condition_signature'


def _license_metadata(row, bundle, condition=None):
    labels = [str(row.get(field) or '') for field in ('OriginalCompatibilityLabel', 'OriginalFeature', 'Feature')]
    associated = sorted(set(row.get('AssociatedLicenseFeatures') or []) | {label for label in labels if label in LICENSES})
    dependencies = deepcopy(row.get('LicensingDependencies') or [])
    prerequisites = (row.get('TechnicalGuidance') or {}).get('prerequisites') or {}
    dependencies = sorted(set(dependencies) | set(prerequisites.get('licensing') or []))
    if condition and condition.startswith('conditional_access.'):
        dependencies = sorted(set(dependencies) | {'Microsoft Entra ID P1 (Conditional Access route)'})
    elif condition == 'risk.policy':
        dependencies = sorted(set(dependencies) | {'Microsoft Entra ID P2 (risk-based Conditional Access route)'})
    elif condition and condition.startswith('access_review.'):
        dependencies = sorted(set(dependencies) | {'Microsoft Entra ID P2 or Microsoft Entra ID Governance (confirm access-review scenario)'})
    state, present = 'unknown', False
    inventory = (((bundle or {}).get('sheets') or {}).get('service_plan_inventory') or {}).get('rows') or []
    for name in associated:
        matched = [item for item in inventory if item.get('Service Plan Name') in LICENSES[name]]
        statuses = {str(item.get('Provisioning Status') or '').lower() for item in matched}
        # Resolve across SKUs exactly as license inventory does: one active plan
        # remains available even if a different SKU disabled it. Unknown is qualified.
        if 'unknown' in statuses or '' in statuses:
            continue
        if 'success' in statuses:
            state, present = 'present', True
    metadata = {'AssociatedLicenseFeatures': associated, 'LicensingDependencies': dependencies,
                'LicenseEvidenceState': row.get('LicenseEvidenceState') or state}
    if associated:
        metadata['LicenseContextQualification'] = 'Associated source feature; applicable licensing prerequisites and entitlement require separate confirmation.'
    if present:
        metadata.update(LicenseObserved=True, LicenseSource='retained service-plan inventory')
    return metadata


def resolve_identity_finding(row, bundle=None):
    """Return an additive copy; an existing versioned projection is validated separately."""
    result = dict(row)
    if not _identity_row(row) or row.get('FindingTaxonomyVersion'):
        return result
    condition, authority = _resolve(row)
    original = str(row.get('OriginalCompatibilityLabel') or row.get('OriginalFeature') or row.get('Feature') or '')
    result.update(OriginalCompatibilityLabel=original, FindingTaxonomyVersion=VERSION,
                  TaxonomyAuthority=authority, TaxonomyState='resolved' if condition else 'unresolved',
                  TaxonomyDiagnostics=[])
    result.update(_license_metadata(row, bundle, condition))
    if condition and condition.startswith('license.'):
        title = condition[len('license.'):] + ' license availability'
        capability, level, kind = 'Service-plan inventory', 'license_inventory', 'licensing_context'
    elif condition:
        title, capability, level = CONDITIONS[condition]
        kind = 'evidence_gap' if level == 'evidence_gap' or condition in {'mfa.registration_unknown', 'mfa.registration_conflicting'} else 'control_condition'
    else:
        result['FindingTitle'] = str(row.get('FindingTitle') or original or 'Identity condition not established')
        result['TaxonomyDiagnostics'] = [{'code': 'identity_taxonomy_unresolved', 'severity': 'compatibility_warning',
            'recommendation_id': row.get('RecommendationId'),
            'reason': 'No unique condition mapping; legacy label is retained without assigning a new condition or governance target.'}]
        return result
    result.update(FindingTitle=title, FindingCondition=condition, FindingType=kind,
                  Capability=capability, ConditionEvidenceLevel=level)
    for target, source in (('ObservedCondition', 'Observation'), ('CustomerAction', 'Recommendation'),
                           ('Scope', 'EvidenceScope'), ('ObservationPeriod', 'ObservationWindow'),
                           ('RequiredOutcome', 'ControlRequiredOutcome'), ('Limitation', 'Qualification'),
                           ('ConfigurationState', 'ConfigurationResult'), ('EnforcementState', 'EnforcementResult'),
                           ('ObservedOperationState', 'OperationalResult')):
        if source in row and target not in row:
            result[target] = deepcopy(row[source])
    return result


def finding_title(row):
    """One title authority, including conservative read-only legacy compatibility."""
    resolved = resolve_identity_finding(row)
    return str(resolved.get('FindingTitle') or row.get('Feature') or 'Review this condition')


BUCKETS = ('recommendations', 'actions', 'customer_findings', 'strengths', 'historical_strengths',
           'opportunities', 'coverage', 'decision_coverage', 'optional_coverage', 'critical', 'high',
           'medium', 'blockers', 'pilot_conditions', 'superseded_findings')


def _records(value):
    """Do not coerce malformed snapshot objects into empty or iterable findings."""
    if not isinstance(value, (list, tuple)) or any(not isinstance(row, dict) for row in value):
        raise ValueError('Identity taxonomy projection requires a list of record objects.')
    return value


def project_identity_result(result, bundle=None):
    """Copy finding projections only; never copy or modify large evidence/identity graphs."""
    projected = dict(result)
    for bucket in BUCKETS:
        if bucket in result:
            projected[bucket] = [resolve_identity_finding(row, bundle) for row in _records(result[bucket])]
    for bucket in ('domains', 'assessment_domains'):
        if bucket in result:
            projected[bucket] = []
            for domain in _records(result[bucket]):
                copy = dict(domain)
                for name in ('findings', 'actions', 'strengths', 'historical_strengths', 'opportunities', 'coverage'):
                    if name in domain:
                        copy[name] = [resolve_identity_finding(row, bundle) for row in _records(domain[name])]
                projected[bucket].append(copy)
    projected['identity_taxonomy'] = {'version': VERSION,
        'diagnostics': validate_identity_taxonomy(projected)}
    if 'executive_summary' in result:
        executive = dict(result['executive_summary'])
        for name, bucket in (('Established strengths', 'strengths'), ('Critical unknowns', 'decision_coverage'),
                             ('Pilot blockers', 'blockers')):
            executive[name] = [finding_title(row) for row in projected.get(bucket, [])]
        executive['Confirmed gaps'] = [finding_title(row) for row in projected.get('actions', []) if row.get('ActionType') == 'Remediation']
        projected['executive_summary'] = executive
    return projected


def validate_identity_taxonomy(result):
    """Invalid structured names block publication; unresolved legacy labels are warnings."""
    diagnostics = []
    try:
        records = [row for bucket in BUCKETS if bucket in result for row in _records(result[bucket])]
        records += [row for bucket in ('domains', 'assessment_domains') if bucket in result
                    for domain in _records(result[bucket])
                    for name in ('findings', 'actions', 'strengths', 'historical_strengths', 'opportunities', 'coverage')
                    if name in domain for row in _records(domain[name])]
    except ValueError:
        return [{'code': 'identity_taxonomy_records_invalid', 'severity': 'error',
                 'reason': 'Finding projections must be lists of record objects; no coercion or silent repair.'}]
    for row in records:
        if not _identity_row(row):
            continue
        diagnostics.extend(deepcopy(row.get('TaxonomyDiagnostics') or []))
        if not row.get('FindingTaxonomyVersion'):
            diagnostics.append({'code': 'identity_taxonomy_legacy', 'severity': 'compatibility_warning',
                'recommendation_id': row.get('RecommendationId'), 'reason': 'Legacy record has no structured taxonomy.'})
            continue
        condition = row.get('FindingCondition')
        expected = CONDITIONS.get(condition) if isinstance(condition, str) else None
        if isinstance(condition, str) and condition.startswith('license.') and condition[len('license.'):] in LICENSES:
            expected = (condition[len('license.'):] + ' license availability', 'Service-plan inventory', 'license_inventory')
        error = None
        if row['FindingTaxonomyVersion'] != VERSION:
            error = 'unsupported_identity_taxonomy_version'
        elif row.get('TaxonomyState') not in {'resolved', 'unresolved'}:
            error = 'identity_taxonomy_state_invalid'
        elif row.get('TaxonomyState') == 'resolved' and (not expected or
                (row.get('FindingTitle'), row.get('Capability'), row.get('ConditionEvidenceLevel')) != expected):
            error = 'identity_taxonomy_condition_mismatch'
        elif row.get('TaxonomyState') == 'unresolved' and (expected or not row.get('TaxonomyDiagnostics')):
            error = 'identity_taxonomy_ambiguity_unqualified'
        elif expected:
            kind = ('licensing_context' if condition.startswith('license.') else 'evidence_gap'
                    if expected[2] == 'evidence_gap' or condition in {'mfa.registration_unknown', 'mfa.registration_conflicting'}
                    else 'control_condition')
            if row.get('FindingType') != kind:
                error = 'identity_taxonomy_type_mismatch'
            required = _license_metadata({}, None, condition)['LicensingDependencies']
            if not set(required).issubset(row.get('LicensingDependencies') or []):
                error = 'identity_taxonomy_licensing_dependency_missing'
        if error:
            diagnostics.append({'code': error, 'severity': 'error',
                'recommendation_id': row.get('RecommendationId'), 'reason': 'Structured taxonomy does not match the versioned condition registry; no silent repair.'})
    unique = {(str(row.get('recommendation_id') or ''), row['code']): row for row in diagnostics}
    return [unique[key] for key in sorted(unique)]


def require_valid_identity_taxonomy(result):
    diagnostics = validate_identity_taxonomy(result)
    if any(row['severity'] == 'error' for row in diagnostics):
        raise ValueError('Identity taxonomy validation blocked publication.')
    return diagnostics


def ensure_identity_result(result, bundle=None):
    """Reuse current projections; enrich legacy runtime copies without rewriting files."""
    if (result.get('identity_taxonomy') or {}).get('version') == VERSION:
        return result
    return project_identity_result(result, bundle)


IDENTITY_SHEETS = frozenset({'authentication_detail', 'mfa_registration_detail', 'conditional_access_detail',
    'authentication_methods_detail', 'authentication_preferences_detail', 'authentication_populations_detail',
    'privileged_identity_detail', 'admin_role_detail', 'identity_risk_detail', 'guest_access_detail',
    'app_consent_policy_detail', 'app_access_detail', 'access_review_detail', 'legacy_signin_detail'})


def project_identity_sheet_links(sheets, recommendations):
    """Flagged By is a compatibility list of linked conditions, not a license signal.

    No native fields, recommendation IDs or selection populations are changed.
    Existing row-specific links take precedence; sheet-wide links are supporting
    context only and do not assert that every record meets every condition.
    """
    by_id = {}
    for row in recommendations:
        for identifier in dict.fromkeys([row.get('RecommendationId'), *(row.get('CompatibilityRecommendationIds') or [])]):
            if identifier:
                by_id.setdefault(identifier, []).append(row)
    for key, sheet in sheets.items():
        if key not in IDENTITY_SHEETS:
            continue
        linked = [row for row in recommendations if key in {part.strip() for part in str(row.get('EvidenceKey') or '').split(';')}]
        for detail in sheet.get('rows') or []:
            identifiers = [part.strip() for part in str(detail.get('RecommendationId') or '').split(';') if part.strip()]
            matches = []
            for identifier in identifiers:
                candidates = by_id.get(identifier, [])
                if len(candidates) == 1:
                    matches.extend(candidates)
            if not identifiers:
                matches = linked
            resolved = [resolve_identity_finding(row) for row in matches]
            if not resolved:
                continue
            detail['Flagged By'] = '; '.join(sorted({finding_title(row) for row in resolved}))
            detail['Capability'] = '; '.join(sorted({row['Capability'] for row in resolved if row.get('Capability')}))
            detail['Licensing Dependency'] = '; '.join(sorted({value for row in resolved for value in row.get('LicensingDependencies') or []}))
            detail['Associated License Feature'] = '; '.join(sorted({value for row in resolved for value in row.get('AssociatedLicenseFeatures') or []}))
            detail['Source Signal'] = 'Linked finding context; record-level applicability requires the retained selector and evidence.'
            if key == 'mfa_registration_detail' and detail.get('MFA Registered') is False:
                detail['Record Condition'] = CONDITIONS['mfa.not_registered'][0]


def licensing_context(row):
    """Compact supporting context; never use it as a condition title or assurance."""
    row = resolve_identity_finding(row)
    if not row.get('FindingTaxonomyVersion'):
        return ''
    parts = []
    if row.get('Capability'):
        parts.append('Capability: ' + row['Capability'])
    if row.get('LicensingDependencies'):
        parts.append('Licensing dependency: ' + '; '.join(row['LicensingDependencies']))
    if row.get('AssociatedLicenseFeatures'):
        parts.append('Supporting license context: ' + '; '.join(row['AssociatedLicenseFeatures']))
    parts.append('License inventory evidence: ' + str(row.get('LicenseEvidenceState') or 'unknown') + '; entitlement requires separate confirmation')
    return '. '.join(parts) + '.'

"""Structured diagnostics for privileged semantics; never silently repair records."""
from collections import Counter
from .privileged_assignments import (ACTIVE_STATES, ELIGIBLE_STATES, ASSIGNMENT_SOURCES,
    assignment_boundary, stable_key, classify_assignment, compatible_source, instant, get)


def validate_privileged_assessment(model):
    if model is None:
        return []
    try:
        return _validate(model)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return [{'code': 'privileged_model_shape', 'severity': 'error', 'subject': 'privileged_assessment'}]


def _validate(model):
    diagnostics = []
    def error(code, subject):
        diagnostics.append({'code': code, 'severity': 'error', 'subject': str(subject)})
    assignments = model.get('assignments') or []
    identities = model.get('identities') or []
    observations = model.get('observations') or []
    bindings = {stable_key('', b['source_ref']): b for b in model.get('evidence_bindings') or []}
    def refs_valid(refs, subject):
        for ref in refs or []:
            if (not isinstance(ref, dict) or set(ref) != {'dataset', 'dataset_index', 'record_index'}
                    or type(ref['dataset_index']) is not int or ref['dataset_index'] < 0
                    or type(ref['record_index']) is not int or ref['record_index'] < 0
                    or stable_key('', ref) not in bindings):
                error('privileged_native_reference_invalid', subject)
    for binding in bindings.values():
        refs_valid([binding['source_ref']], 'evidence_bindings')
        if not compatible_source(binding['source'], model.get('boundary')):
            error('privileged_evidence_ownership', binding['source_ref'])
        ref = binding['source_ref']
        captures = model.get('collection_sources', {}).get(ref['dataset'], [])
        if ref['dataset_index'] >= len(captures) or ref['record_index'] >= captures[ref['dataset_index']]['record_count']:
            error('privileged_native_reference_invalid', ref)
        elif binding['source'] != captures[ref['dataset_index']]['source']:
            error('privileged_capture_reference_mismatch', ref)
    from .privileged_identity import _governance_purpose_supported
    for declaration in model.get('purpose_inputs') or []:
        if declaration.get('source_type') != 'governance': continue
        support = {}; mapped = dict(declaration, evidence_refs=[])
        for index, ref in enumerate(declaration.get('evidence_refs') or []):
            binding = bindings.get(stable_key('', ref), {})
            name = 'purpose-' + str(index)
            support[name] = [{'records': [binding.get('purpose_record')], 'source': binding.get('source', {})}]
            mapped['evidence_refs'].append({'dataset': name, 'dataset_index': 0, 'record_index': 0})
        verified = bool(declaration.get('owner') and declaration.get('purpose') and declaration.get('principal_id')
            and _governance_purpose_supported(mapped, support, model.get('boundary'), model['evaluation_timestamp']))
        if verified != declaration.get('governance_verified'):
            error('privileged_governance_purpose_unverified', declaration.get('principal_id'))
    grants = {a.get('assignment_key'): a for a in assignments}
    observation_ids = {o.get('observation_id') for o in observations}
    if len(grants) != len(assignments): error('duplicate_assignment_identity', 'assignments')
    if len({i.get('identity_id') for i in identities}) != len(identities): error('duplicate_privileged_identity', 'identities')
    if len(observation_ids) != len(observations): error('duplicate_privileged_observation', 'observations')
    for row in assignments:
        subject = row.get('assignment_key')
        state = row.get('temporal_state')
        if bool(row.get('active')) != (state in ACTIVE_STATES): error('noncurrent_assignment_active', subject)
        if bool(row.get('eligible')) != (state in ELIGIBLE_STATES): error('eligible_state_mismatch', subject)
        boundary = row.get('identity_boundary') or {}
        if 'app_scope_id' not in boundary or boundary.get('app_scope_id') != row.get('app_scope_id'):
            error('application_scope_identity_missing', subject)
        if boundary != assignment_boundary(row, model.get('boundary')):
            error('assignment_identity_boundary_mismatch', subject)
        if not row.get('evidence_refs') or not row.get('source_occurrences'): error('assignment_evidence_missing', subject)
        refs_valid(row.get('evidence_refs'), subject)
        expected_key = stable_key('PGA-', boundary)
        if subject != expected_key and subject != stable_key('PGA-', [boundary, row['source_occurrences'][0]['raw']]):
            error('assignment_semantic_identity_mismatch', subject)
        # Validate against its retained primary source, not the derived labels.
        primary = next((o for o in row.get('source_occurrences') or []
            if ASSIGNMENT_SOURCES.get(o['source_ref']['dataset']) == (row['kind'], row['source_kind'])
            and get(o['raw'], 'id') == row['source_id']), None)
        if primary:
            computed = classify_assignment(primary['raw'], row['kind'], model['evaluation_timestamp'], source_kind=row['source_kind'])
            conflict = any(d['code'] in {'conflicting_assignment_occurrence', 'conflicting_native_assignment'}
                and d.get('subject') == row['source_id'] for d in model.get('diagnostics') or [])
            captured = instant(primary['source'].get('collected_at'))
            evaluated = instant(model['evaluation_timestamp'])
            if conflict or evaluated is None or (captured is not None and captured > evaluated): computed = 'UnknownTemporalState'
            if computed != state: error('assignment_temporal_source_mismatch', subject)
            raw = primary['raw']
            if get(raw, 'appScopeId') != row.get('app_scope_id'): error('application_scope_identity_missing', subject)
            raw_type = str(get(get(raw, 'principal', {}) or {}, '@odata.type') or get(raw, 'principalType') or '').split('.')[-1].lower()
            if raw_type in {'user', 'group', 'serviceprincipal'} and row.get('principal_type') not in {raw_type, 'unresolved'}:
                error('privileged_principal_type_mismatch', subject)
        else:
            error('assignment_primary_evidence_missing', subject)
        for occurrence in row.get('source_occurrences') or []:
            binding = bindings.get(stable_key('', occurrence['source_ref']))
            if not binding or binding['raw_digest'] != stable_key('', occurrence['raw']): error('assignment_source_mismatch', subject)
            if occurrence['source_ref'] not in row['evidence_refs']: error('assignment_occurrence_reference_missing', subject)
    for identity in identities:
        subject = identity.get('identity_id')
        if subject != stable_key('PGI-', [model.get('boundary'), identity['principal_id'], identity['principal_type']]):
            error('privileged_identity_boundary_mismatch', subject)
        for key in identity.get('active_assignments') or []:
            if key not in grants or not grants[key].get('active'): error('identity_nonactive_grant', subject)
        for key in identity.get('eligible_assignments') or []:
            if key not in grants or not grants[key].get('eligible'): error('identity_noneligible_grant', subject)
        for key in identity.get('assignment_refs') or []:
            if key not in grants:
                error('identity_grant_reference_missing', subject); continue
            assignment = grants[key]
            if assignment['principal_id'] != identity['principal_id']:
                memberships = [bindings.get(stable_key('', ref), {}) for ref in identity.get('membership_refs') or []]
                if assignment['principal_type'] != 'group' or not any(b.get('principal_id') == identity['principal_id']
                    and b.get('source', {}).get('group_id') == assignment['principal_id'] for b in memberships):
                    error('privileged_group_lineage_missing', subject)
            elif assignment['principal_type'] != identity['principal_type']:
                error('privileged_principal_type_mismatch', subject)
        if identity.get('principal_type') in {'serviceprincipal', 'unresolved', 'group'} and (identity.get('activity') or {}).get('state') in {'ActivePrivilegedAccount', 'PotentiallyInactivePrivilegedAccount'}:
            error('unsupported_principal_activity', subject)
        activity = identity.get('activity') or {}
        if activity.get('state') == 'PotentiallyInactivePrivilegedAccount':
            if not activity.get('last_qualified_success') or not activity.get('source_complete'):
                error('missing_activity_interpreted_inactive', subject)
            if not activity.get('policy_id') or not activity.get('threshold') or not activity.get('policy_signature'):
                error('inactivity_policy_missing', subject)
            if (identity.get('purpose') or {}).get('purpose') == 'emergency': error('emergency_ordinary_inactivity', subject)
            if (identity.get('purpose') or {}).get('purpose') in {'service', 'synchronization'}: error('service_ordinary_inactivity', subject)
            if not identity.get('active_assignments'): error('nonactive_identity_inactive', subject)
        purpose = identity.get('purpose') or {}
        if purpose.get('validated') and purpose.get('purpose') != 'workload' and not purpose.get('evidence_refs'):
            error('unverified_account_purpose', subject)
        from .privileged_identity import evaluate_activity, classify_purpose
        if evaluate_activity(identity, model['inactivity_policy'], model['evaluation_timestamp']) != activity:
            error('privileged_activity_source_mismatch', subject)
        if classify_purpose(identity, model.get('purpose_inputs')) != purpose:
            error('unverified_account_purpose', subject)
        auth = identity['authentication']
        refs_valid(identity.get('account_refs', []) + identity.get('activity_refs', []) + identity.get('membership_refs', [])
            + auth.get('registration_refs', []) + auth.get('event_refs', []) + purpose.get('evidence_refs', []), subject)
        from .signin_evidence import normalize_signin_event
        from .authentication_methods import reconcile_registration_population
        entries = [bindings.get(stable_key('', ref), {}).get('registration_record', {}) for ref in auth.get('registration_refs', [])]
        registration = reconcile_registration_population(entries)
        expected_registration = next((e['state'] for e in registration['entries'] if get(e['record'], 'id') == identity['principal_id']), 'Unknown')
        if auth['registration_state'] != expected_registration: error('privileged_registration_source_mismatch', subject)
        for event in auth.get('events') or []:
            normalized = normalize_signin_event(event, event.get('AuthenticationSource'))
            if any(event.get(field) != normalized[field] for field in ('NormalizedSignInOutcome', 'AuthenticationRuleVersion',
                'LegacyAuthenticationState', 'MFARequirement', 'MFASatisfaction', 'ConditionalAccessResult', 'AuthenticationConflicts')):
                error('privileged_authentication_source_mismatch', subject)
            refs_valid(event.get('source_refs'), subject)
            if get(event, 'userId') != identity['principal_id']: error('privileged_event_identity_mismatch', subject)
            if event.get('correlation_qualified') and (instant(get(event, 'createdDateTime')) is None
                    or instant(get(event, 'createdDateTime')) > instant(model['evaluation_timestamp'])
                    or event.get('AuthenticationConflicts')):
                error('privileged_event_qualification_mismatch', subject)
        for condition, refs in auth.get('condition_refs', {}).items():
            refs_valid(refs, subject)
            if not any(ref['dataset'] == 'signin_logs' for ref in refs): error('privileged_authentication_event_missing', subject)
        from .privileged_identity import correlate_authentication
        event_entries = [(event, ref, bindings.get(stable_key('', ref), {}).get('source', {}))
            for event in auth.get('events') or [] for ref in event.get('source_refs') or []]
        collections = {name: [{'source': d['source'], 'records': []} for d in captures]
            for name, captures in model.get('collection_sources', {}).items()}
        recomputed = correlate_authentication(identity, collections, model.get('boundary'), registration,
            model.get('expected_ca_inputs'), instant(model['evaluation_timestamp']), event_entries)
        if any(auth.get(field) != recomputed[field] for field in ('observations', 'condition_refs', 'source_complete')):
            error('privileged_authentication_conditions_mismatch', subject)
    for observation in observations:
        subject = observation.get('observation_id')
        if any(key not in observation_ids for key in observation.get('component_ids') or []): error('missing_combined_component', subject)
        if observation.get('condition') == 'combined' and not observation.get('component_ids'): error('missing_combined_component', subject)
        if observation.get('classification') != 'evidence_gap' and not observation.get('evidence_refs'): error('privileged_observation_evidence_missing', subject)
        refs_valid(observation.get('evidence_refs'), subject)
        if observation.get('condition', '').startswith('ActivePrivilege.') and len(observation.get('component_ids') or []) < 2:
            error('missing_combined_component', subject)
    from .privileged_identity import _observations, normalize_inactivity_policy
    expected_observations = {o['observation_id']: o for o in _observations(identities, assignments, model.get('boundary'))}
    if any(expected_observations.get(o['observation_id']) != o for o in observations) or set(expected_observations) != observation_ids:
        error('privileged_observation_components_mismatch', 'observations')
    policy = normalize_inactivity_policy(model['inactivity_policy'], model['evaluation_timestamp'])
    if policy != model['inactivity_policy'] or policy['signature'] != model['policy_signature']:
        error('privileged_policy_signature_mismatch', 'inactivity_policy')
    counts = model.get('counts') or {}
    expected = {'assignments': len(assignments), 'identities': len(identities),
        'active_assignments': sum(a.get('active') is True for a in assignments),
        'eligible_assignments': sum(a.get('eligible') is True for a in assignments),
        'active_user_identities': sum(i.get('principal_type') == 'user' and bool(i.get('active_assignments')) for i in identities),
        'eligible_user_identities': sum(i.get('principal_type') == 'user' and bool(i.get('eligible_assignments')) for i in identities),
        'unresolved_identities': sum(i['principal_type'] == 'unresolved' for i in identities),
        'group_assignments': sum(a['principal_type'] == 'group' for a in assignments),
        'workload_identities': sum(i['principal_type'] == 'serviceprincipal' for i in identities),
        'disabled_current_identities': sum('DisabledIdentityWithCurrentPrivilege' in i['states'] for i in identities),
        'temporal_states': dict(Counter(a['temporal_state'] for a in assignments)),
        'activity_states': dict(Counter(i['activity']['state'] for i in identities)),
        'purpose_states': dict(Counter(i['purpose']['classification'] for i in identities)),
        'mfa_states': dict(Counter(i['authentication']['registration_state'] for i in identities if i['principal_type'] == 'user'))}
    if any(counts.get(k) != v for k, v in expected.items()): error('privileged_count_mismatch', 'counts')
    if model.get('population_complete') and any(d.get('code') == 'group_membership_incomplete' for d in model.get('diagnostics') or []):
        error('incomplete_group_population_claimed_complete', 'population')
    if model.get('population_complete') and (model.get('diagnostics') or any(not a['qualified_complete'] or a['temporal_state'] == 'UnknownTemporalState' for a in assignments)
            or any(i['principal_type'] == 'unresolved' or i['principal_type'] == 'user' and (not i['account_source_complete'] or i['account_conflicts']) for i in identities)):
        error('incomplete_privileged_population_claimed_complete', 'population')
    return diagnostics


def require_valid_privileged_assessment(result):
    diagnostics = validate_privileged_assessment(result.get('privileged_assessment'))
    model = result.get('privileged_assessment')
    if isinstance(model, dict):
        boundary = model.get('boundary') or {}; identity = result.get('identity') or {}
        for field, owner in (('assessment_id', 'AssessmentId'), ('environment_id', 'PrimaryEnvironmentId')):
            if boundary.get(field) and boundary[field] != identity.get(owner):
                diagnostics.append({'code': 'privileged_result_ownership', 'severity': 'error', 'subject': field})
        for finding in result.get('recommendations') or []:
            if not str(finding.get('FindingKey', '')).startswith('entra.privileged.'): continue
            matched = [o for o in model.get('observations') or [] if o['observation_id'] in finding.get('PrivilegedObservationIds', [])]
            if not matched or len(matched) != len(finding.get('PrivilegedObservationIds') or []):
                diagnostics.append({'code': 'privileged_finding_observation_missing', 'severity': 'error', 'subject': finding.get('RecommendationId')})
            if any(not any(ref['dataset'] in ASSIGNMENT_SOURCES for ref in o.get('evidence_refs') or []) for o in matched):
                diagnostics.append({'code': 'privileged_finding_assignment_missing', 'severity': 'error', 'subject': finding.get('RecommendationId')})
    if diagnostics:
        raise ValueError('Privileged assessment validation blocked publication: ' + ', '.join(sorted({d['code'] for d in diagnostics})))
    return diagnostics

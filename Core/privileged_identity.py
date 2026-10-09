"""Shared privileged population, activity candidates and component observations."""
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal
import re

from .privileged_assignments import (ACTIVE_STATES, ELIGIBLE_STATES, get, instant, stable_key,
    normalize_assignments, source_rows, source_coverage, source_current, compatible_source, evaluation_timestamp as evaluation_time)
from .source_evidence import envelope_complete

RULE_VERSION = '1.0.0'
QUALIFICATION = ('Counts describe retained directory privilege and its recorded scopes, not proof of business need, '
                 'compromise or complete effective access. Registration, enforcement, observed authentication and '
                 'successful activity remain separate. Review purpose and dependencies before changing access.')


def _unique_refs(refs):
    return sorted({stable_key('', ref): deepcopy(ref) for ref in refs}.values(),
                  key=lambda ref: (ref['dataset'], ref['dataset_index'], ref['record_index']))


def normalize_inactivity_policy(policy, evaluated_at):
    """An absent or invalid explicit threshold cannot silently classify inactivity."""
    raw = deepcopy(policy) if isinstance(policy, dict) else {}
    defaults = {'activity_fields': ['lastSuccessfulSignInDateTime', 'successful_signin_events'],
        'successful_activity_required': True, 'interactive_handling': 'include_success',
        'noninteractive_handling': 'include_success', 'emergency_treatment': 'separate_review',
        'service_treatment': 'purpose_and_dependency_review', 'synchronization_treatment': 'separate_review',
        'guest_treatment': 'review_candidate', 'disabled_treatment': 'retained_privilege_review',
        'eligible_only_treatment': 'activation_review', 'permanent_treatment': 'necessity_review',
        'missing_data_behavior': 'unknown', 'exclusions': [], 'minimum_source_coverage': 'complete'}
    defaults.update(raw)
    defaults['evaluation_timestamp'] = evaluation_time(evaluated_at)
    defaults['valid'] = (bool(raw.get('policy_id')) and bool(raw.get('rule_version'))
        and bool(raw.get('methodology_reference')) and type(raw.get('threshold')) in (int, float)
        and 0 < raw['threshold'] < 100000 and raw.get('threshold_unit') == 'days'
        and instant(defaults['evaluation_timestamp']) is not None
        and defaults['successful_activity_required'] is True
        and defaults['interactive_handling'] == 'include_success'
        and defaults['noninteractive_handling'] == 'include_success'
        and defaults['missing_data_behavior'] == 'unknown'
        and defaults['minimum_source_coverage'] == 'complete'
        and defaults['emergency_treatment'] == 'separate_review'
        and defaults['service_treatment'] == 'purpose_and_dependency_review'
        and defaults['synchronization_treatment'] == 'separate_review'
        and defaults['eligible_only_treatment'] == 'activation_review'
        and defaults['disabled_treatment'] == 'retained_privilege_review'
        and defaults['permanent_treatment'] == 'necessity_review'
        and defaults['guest_treatment'] == 'review_candidate'
        and isinstance(defaults['exclusions'], list)
        and isinstance(defaults['activity_fields'], list)
        and all(isinstance(field, str) for field in defaults['activity_fields'])
        and set(defaults['activity_fields']) <= {'lastSuccessfulSignInDateTime', 'successful_signin_events'}
        and bool(defaults['activity_fields']))
    # Evaluation time is not a policy change; the same policy can compare later runs.
    defaults['signature'] = stable_key('PGP-', {k: v for k, v in defaults.items() if k not in {'evaluation_timestamp', 'signature'}})
    if not isinstance(defaults['activity_fields'], list) or not all(isinstance(field, str) for field in defaults['activity_fields']):
        defaults['activity_fields'] = []
    return defaults


def _current_rows(sources, names, boundary, now):
    """Retain the newest orderable capture per native object; conflicts stay unresolved."""
    grouped = defaultdict(list)
    captures = defaultdict(list)
    for name in names:
        for index, dataset in enumerate(sources.get(name, []) or []):
            source = dataset.get('source') or {}
            stamp = instant(source.get('collected_at'))
            if compatible_source(source, boundary) and (stamp is None or now is None or stamp <= now):
                captures[(name, source.get('scope'), source.get('provider'))].append((index, stamp))
    retained = set()
    for (name, _, _), entries in captures.items():
        latest = max((stamp for _, stamp in entries if stamp is not None), default=None)
        # A complete newest empty enumeration supersedes older objects. A
        # partial latest capture does not resurrect objects omitted from it.
        retained.update((name, index) for index, stamp in entries if stamp is None or stamp == latest)
    for raw, ref, source in source_rows(sources, names, boundary):
        if (ref['dataset'], ref['dataset_index']) not in retained: continue
        captured = instant(source.get('collected_at'))
        if captured is not None and now is not None and captured > now:
            continue
        identifier = get(raw, 'id')
        if identifier:
            grouped[str(identifier)].append((raw, ref, source))
    selected = {}
    for identifier, entries in grouped.items():
        stamps = [instant(s.get('collected_at')) for _, _, s in entries]
        if all(stamp is not None for stamp in stamps):
            latest = max(stamps)
            entries = [entry for entry, stamp in zip(entries, stamps) if stamp == latest]
        selected[identifier] = entries
    return selected


def _account(entries):
    result, conflicts = {}, []
    for field in ('accountEnabled', 'userType', 'onPremisesSyncEnabled', 'displayName', 'signInActivity', 'createdDateTime'):
        values = [get(raw, field) for raw, _, _ in entries if get(raw, field) is not None]
        distinct = {stable_key('', value) for value in values}
        if len(distinct) == 1:
            result[field] = deepcopy(values[0])
        elif len(distinct) > 1:
            conflicts.append(field)
    return result, conflicts


def classify_purpose(identity, declarations):
    account = identity['account']
    classification, validated, evidence, purpose = 'UnknownPurpose', False, [], None
    matching = [row for row in declarations or [] if row.get('principal_id') == identity['principal_id']]
    supported = [row for row in matching if row.get('owner') and row.get('evidence_refs')
                 and (row.get('source_type') in {'configuration', 'customer_declaration', 'account_metadata'}
                      or row.get('source_type') == 'governance' and row.get('governance_verified') is True)
                 and row.get('purpose') in {'ordinary', 'emergency', 'service', 'synchronization'}]
    if supported and len({r['purpose'] for r in supported}) == 1:
        purpose = supported[0]['purpose']; validated = True
        classification = {'ordinary': 'OrdinaryHumanAdministrator', 'emergency': 'EmergencyAccessValidated',
                          'service': 'ServicePurposeValidated', 'synchronization': 'SynchronizationPurposeValidated'}[purpose]
        evidence = _unique_refs([ref for row in supported for ref in row['evidence_refs']])
    if not validated:
        name = str(account.get('displayName') or identity.get('display_reference') or '').lower()
        if re.search(r'break[ _-]?glass|emergency', name):
            classification, purpose = 'EmergencyAccessCandidate', 'emergency'
        elif re.search(r'\bsvc[ _-]|service|automation', name):
            classification, purpose = 'ServiceOrAutomationAccountCandidate', 'service'
        elif account.get('onPremisesSyncEnabled') is True:
            classification, purpose = 'SynchronizationAccountCandidate', 'synchronization'
        elif str(account.get('userType') or '').lower() == 'guest':
            classification = 'GuestAdministrator'
    if identity['principal_type'] == 'serviceprincipal':
        classification, purpose, validated = 'WorkloadIdentity', 'workload', True
    elif account.get('accountEnabled') is False:
        classification = 'DisabledPrivilegedAccount'
    return {'classification': classification, 'validated': validated, 'purpose': purpose, 'evidence_refs': evidence,
            'conflicting': len({r['purpose'] for r in supported}) > 1,
            'qualification': 'Name patterns only identify candidates. Confirm owner, purpose, dependencies and supported evidence.'}


def _governance_purpose_supported(declaration, sources, boundary, evaluated_at):
    """Validate an explicitly mapped purpose without creating a governance event.

    The existing governance contract has no account-purpose decision type. An
    operator can map an ApprovedException whose conditions explicitly record
    ``Account purpose: emergency`` (or service/synchronization) to a principal.
    A display-only approval label or unverified projection cannot establish it.
    """
    from .governance import project, validate_log
    for ref in declaration.get('evidence_refs') or []:
        raw = sources[ref['dataset']][ref['dataset_index']]['records'][ref['record_index']]
        log = raw.get('DecisionLog') if isinstance(raw, dict) else None
        if not log or validate_log(log): continue
        if (log.get('AssessmentId') != boundary.get('assessment_id') or
                log.get('PrimaryEnvironmentId') != boundary.get('environment_id')): continue
        try:
            records = project(log, as_of=evaluation_time(evaluated_at))['Records']
        except ValueError:
            continue
        for record in records:
            if (record['DecisionId'] == declaration.get('decision_id') and record['EffectiveActive']
                    and not record['RequiresReview'] and record['DecisionType'] == 'ApprovedException'
                    and declaration['principal_id'] in (record.get('ResourceIds') or [])
                    and declaration['owner'] == record.get('AccountableOwner')
                    and 'Account purpose: ' + declaration['purpose'] in (record.get('Conditions') or [])):
                return True
    return False


def correlate_authentication(identity, sources, boundary, registration, expected_ca, now, event_entries=None):
    from . import signin_evidence
    identifier = identity['principal_id']
    entry = next((entry for entry in registration['entries'] if get(entry['record'], 'id') == identifier), None)
    state = entry['state'] if entry and identity['principal_type'] == 'user' else 'Unknown'
    registration_refs = [ref for row, ref, _ in source_rows(sources, ['auth_methods'], boundary)
                         if get(row, 'id') == identifier and identity['principal_type'] == 'user']
    events, observations, refs = [], [], []
    condition_refs = defaultdict(list)
    seen = {}
    for raw, ref, source in (event_entries if event_entries is not None else source_rows(sources, ['signin_logs'], boundary)):
        if get(raw, 'userId') != identifier or identity['principal_type'] != 'user':
            continue
        event = signin_evidence.normalize_signin_event(raw, source)
        event['source_refs'] = [ref]
        stamp = instant(get(event, 'createdDateTime'))
        capture = instant(source.get('collected_at'))
        start, end = instant(source.get('window_start')), instant(source.get('window_end'))
        event['correlation_qualified'] = (stamp is not None and now is not None and stamp <= now
            and (start is None or stamp >= start) and (end is None or stamp <= end)
            and (capture is None or stamp <= capture <= now) and not event['AuthenticationConflicts'])
        marker = get(event, 'id') or stable_key('', raw)
        prior_events = seen.setdefault(marker, [])
        if prior_events:
            same = next((prior for prior in prior_events if stable_key('', raw) == prior['_raw_key']), None)
            if same:
                same['source_refs'].append(ref)
                same['correlation_qualified'] = same['correlation_qualified'] and event['correlation_qualified']
                continue
            # Divergent payloads with one native event ID remain conflicts, even
            # if a third occurrence matches one variant. Never select by order.
            for prior in prior_events: prior['correlation_qualified'] = False
            event['correlation_qualified'] = False
        event['_raw_key'] = stable_key('', raw)
        events.append(event); prior_events.append(event)
    for event in events:
        event.pop('_raw_key', None)
        refs.extend(event['source_refs'])
        if not event['correlation_qualified']:
            continue
        event_conditions = []
        if event['LegacyAuthenticationState'] == 'SuccessfulLegacyAuthentication':
            event_conditions.append('PrivilegedSuccessfulLegacyAuthentication')
        elif event['LegacyAuthenticationState'] == 'LegacyAttemptBlockedByConditionalAccess':
            event_conditions.append('PrivilegedLegacyAttemptBlockedByCA')
        if event['NormalizedSignInOutcome'] == 'Success':
            if event['MFASatisfaction'] == 'observed_success': event_conditions.append('PrivilegedObservedMFACompletion')
            if event['MFARequirement'] == 'not_required' and event['MFASatisfaction'] == 'unknown':
                event_conditions.append('PrivilegedObservedSingleFactorSuccess')
            expectations = [r for r in expected_ca or [] if r.get('principal_id') == identifier and r.get('policy_id') and r.get('evidence_refs')]
            if expectations and event['ConditionalAccessResult'].lower() == 'notapplied' and event['AppliedPoliciesAvailable']:
                for expected in expectations:
                    applied = event.get('appliedConditionalAccessPolicies') or []
                    if not any(get(p, 'id') == expected['policy_id'] and str(get(p, 'result') or '').lower() == 'success' for p in applied):
                        event_conditions.append('PrivilegedSuccessfulSignInOutsideExpectedCACoverage')
                        condition_refs['PrivilegedSuccessfulSignInOutsideExpectedCACoverage'].extend(expected['evidence_refs'])
                        refs.extend(expected['evidence_refs'])
        observations.extend(event_conditions)
        for condition in event_conditions: condition_refs[condition].extend(event['source_refs'])
    return {'registration_state': state, 'registration_refs': _unique_refs(registration_refs),
            'events': events, 'event_refs': _unique_refs(refs), 'observations': sorted(set(observations)),
            'condition_refs': {key: _unique_refs(value) for key, value in sorted(condition_refs.items())},
            'source_complete': source_coverage(sources, ['signin_logs'], boundary) and all(
                    now is not None and instant(d.get('source', {}).get('collected_at')) is not None
                    and 0 <= now - instant(d['source']['collected_at']) <= 35 * 86400 for d in sources.get('signin_logs', [])),
            'qualification': 'Event-scoped Pass A outcomes; no matching events do not establish inactivity or protection.'}


def evaluate_activity(identity, policy, evaluated_at):
    now = instant(evaluation_time(evaluated_at))
    account, auth, purpose = identity['account'], identity['authentication'], identity['purpose']
    raw_fields = deepcopy(identity.get('signin_activity') or account.get('signInActivity') or {})
    fields = {**raw_fields, **{field: get(raw_fields, field) for field in
        ('lastSignInDateTime', 'lastNonInteractiveSignInDateTime', 'lastSuccessfulSignInDateTime')}}
    qualified = identity['activity_source_complete'] and not identity['account_conflicts']
    candidates, refs = [], []
    successful = fields.get('lastSuccessfulSignInDateTime')
    success_stamp = instant(successful)
    created = instant(account.get('createdDateTime'))
    activity_captures = [instant(o['source'].get('collected_at')) for o in identity.get('activity_source_occurrences') or []]
    if ('lastSuccessfulSignInDateTime' in policy['activity_fields'] and success_stamp is not None
            and now is not None and success_stamp <= now and qualified
            and (created is None or created <= success_stamp)
            and all(capture is not None and success_stamp <= capture for capture in activity_captures)):
        candidates.append((success_stamp, successful, 'lastSuccessfulSignInDateTime'))
        refs.extend(identity['activity_refs'])
    for event in auth['events']:
        if event['NormalizedSignInOutcome'] == 'Success' and event['correlation_qualified'] and 'successful_signin_events' in policy['activity_fields']:
            stamp = instant(get(event, 'createdDateTime'))
            candidates.append((stamp, get(event, 'createdDateTime'), 'successful_signin_event'))
            refs.extend(event['source_refs'])
    latest = max(candidates, key=lambda value: value[0]) if candidates else None
    state, reason = 'PrivilegedActivityUnknown', 'Qualified successful activity or an explicit applicable policy is not established.'
    current = bool(identity['active_assignments'] or identity['eligible_assignments'])
    if account.get('accountEnabled') is False and current:
        state, reason = 'DisabledAccountRetainingPrivilege', 'Disabled account retains active or eligible privilege; review continuing need.'
    elif purpose['purpose'] == 'emergency':
        state = 'EmergencyAccessValidated' if purpose['validated'] else 'EmergencyAccessValidationRequired'
        reason = 'Emergency-purpose activity and monitoring require separate validation; expected inactivity is not ordinary staleness.'
    elif purpose['purpose'] in {'service', 'synchronization', 'workload'}:
        recent = bool(policy['valid'] and latest and now - latest[0] <= Decimal(str(policy['threshold'])) * 86400)
        state = 'ActivePrivilegedAccount' if recent else 'ServicePurposeValidationRequired' if not purpose['validated'] else 'PrivilegedActivityUnknown'
        reason = 'Review purpose, dependencies and noninteractive activity separately; ordinary inactivity does not determine service or synchronization account treatment.'
    elif policy['valid'] and latest and identity['active_assignments']:
        age = now - latest[0]
        if age <= Decimal(str(policy['threshold'])) * 86400:
            state, reason = 'ActivePrivilegedAccount', 'Recent qualified successful activity; this does not establish necessity of privilege.'
        elif (identity['principal_type'] == 'user' and account.get('accountEnabled') is True
              and qualified and identity['account_source_complete'] and auth['source_complete'] and not purpose['conflicting']):
            state, reason = 'PotentiallyInactivePrivilegedAccount', 'Qualified successful activity predates the explicit threshold; confirm owner, purpose and dependencies before changing access.'
    if identity['principal_id'] in policy['exclusions']:
        state, reason = 'PrivilegedActivityUnknown', 'Explicit policy exclusion; activity was retained without an inactivity conclusion.'
    if identity['principal_type'] != 'user' and state not in {'EmergencyAccessValidated', 'EmergencyAccessValidationRequired'}:
        state, reason = 'PrivilegedActivityUnknown', 'Human signInActivity rules do not establish workload or unresolved principal activity.'
    return {'state': state, 'reason': reason, 'fields': fields, 'last_qualified_success': latest[1] if latest else None,
            'successful_activity_basis': latest[2] if latest else None, 'evidence_refs': _unique_refs(refs),
            'source_complete': qualified, 'policy_id': policy.get('policy_id'), 'policy_signature': policy['signature'],
            'threshold': policy.get('threshold'), 'threshold_unit': policy.get('threshold_unit'),
            'evaluation_timestamp': evaluation_time(evaluated_at),
            'qualification': 'Attempt timestamps include failures. Null successful activity is not inactivity; successful history is not backfilled.'}


def _observations(identities, assignments, boundary):
    output = []
    grants = {a['assignment_key']: a for a in assignments}
    def add(identity, condition, refs, classification='confirmed_observation', components=()):
        row = {'observation_id': stable_key('PGV-', [boundary, identity['identity_id'], condition]),
               'identity_id': identity['identity_id'], 'condition': condition, 'classification': classification,
               'component_ids': list(components), 'evidence_refs': _unique_refs(refs), 'qualification': QUALIFICATION}
        output.append(row)
        return row['observation_id']
    for identity in identities:
        assignments_refs = [ref for key in identity['active_assignments'] + identity['eligible_assignments'] for ref in grants[key]['evidence_refs']] + identity['membership_refs']
        if not identity['active_assignments'] and not identity['eligible_assignments']:
            if identity['principal_type'] == 'unresolved' or any(grants[k]['temporal_state'] == 'UnknownTemporalState' for k in identity['assignment_refs']):
                add(identity, 'UnresolvedPrivilegedIdentity', [ref for key in identity['assignment_refs'] for ref in grants[key]['evidence_refs']], 'evidence_gap')
            continue
        active_id = add(identity, 'CurrentActivePrivilege', assignments_refs) if identity['active_assignments'] else None
        if identity['eligible_assignments']: add(identity, 'CurrentEligiblePrivilege', assignments_refs)
        auth = identity['authentication']
        state = auth['registration_state']
        registration_id = add(identity, 'MFARegistration.' + state, auth['registration_refs'],
            'evidence_gap' if state in {'Unknown', 'Conflicting'} else 'confirmed_observation')
        if active_id and state == 'ExplicitlyNotRegistered':
            add(identity, 'ActivePrivilegeWithoutMFARegistration', assignments_refs + auth['registration_refs'], components=[active_id, registration_id])
        for condition in auth['observations']:
            event_refs = auth['condition_refs'][condition]
            event_id = add(identity, condition, event_refs)
            if active_id and condition in {'PrivilegedSuccessfulLegacyAuthentication', 'PrivilegedObservedSingleFactorSuccess', 'PrivilegedSuccessfulSignInOutsideExpectedCACoverage'}:
                add(identity, 'ActivePrivilege.' + condition, assignments_refs + event_refs, components=[active_id, event_id])
        activity = identity['activity']
        activity_id = add(identity, activity['state'], activity['evidence_refs'] + assignments_refs,
            'review_candidate' if activity['state'] in {'PotentiallyInactivePrivilegedAccount', 'DisabledAccountRetainingPrivilege', 'EmergencyAccessValidationRequired', 'ServicePurposeValidationRequired'} else
            'evidence_gap' if activity['state'] == 'PrivilegedActivityUnknown' else 'confirmed_observation')
        if active_id and activity['state'] == 'PotentiallyInactivePrivilegedAccount':
            add(identity, 'ActivePrivilege.PotentialInactivity', assignments_refs + activity['evidence_refs'], 'review_candidate', [active_id, activity_id])
        if identity['account'].get('accountEnabled') is False and (active_id or identity['eligible_assignments']):
            add(identity, 'DisabledIdentityWithCurrentPrivilege', assignments_refs + identity['account_refs'], 'review_candidate', [activity_id] + ([active_id] if active_id else []))
        if any(grants[k]['temporal_state'] == 'ActivePermanent' for k in identity['active_assignments']):
            add(identity, 'PermanentPrivilegeNeedsReview', assignments_refs, 'review_candidate')
        if identity['principal_type'] == 'unresolved':
            add(identity, 'UnresolvedPrivilegedIdentity', assignments_refs, 'evidence_gap')
        for state in identity['review_states']:
            add(identity, state, assignments_refs + auth['event_refs'] + identity['purpose']['evidence_refs'], 'review_candidate')
    return output


def build_privileged_assessment(sources, *, evaluation_timestamp=None, boundary=None,
                               inactivity_policy=None, account_purposes=None, expected_ca=None):
    """Build once from retained sources; rendering and governance cannot change it."""
    from .authentication_methods import reconcile_registration_population
    from .raw_evidence import safe_record
    boundary = deepcopy(boundary or {})
    input_diagnostics = []
    def supported_refs(declarations):
        retained = []
        for declaration in declarations or []:
            if not isinstance(declaration, dict): continue
            valid = isinstance(declaration.get('evidence_refs') or [], list)
            for ref in declaration.get('evidence_refs') or []:
                try:
                    if (not isinstance(ref, dict) or type(ref.get('dataset_index')) is not int
                            or type(ref.get('record_index')) is not int):
                        valid = False; continue
                    dataset = sources[ref['dataset']][ref['dataset_index']]
                    dataset['records'][ref['record_index']]
                    valid = valid and ref['dataset_index'] >= 0 and ref['record_index'] >= 0 and compatible_source(dataset.get('source') or {}, boundary)
                except (KeyError, IndexError, TypeError):
                    valid = False
            if valid:
                row = deepcopy(declaration)
                row.pop('governance_verified', None)
                if row.get('source_type') == 'governance':
                    row['governance_verified'] = bool(row.get('owner') and row.get('purpose') and row.get('principal_id')
                        and _governance_purpose_supported(row, sources, boundary, evaluation_timestamp))
                retained.append(row)
            else:
                input_diagnostics.append({'code': 'privileged_input_reference_invalid', 'severity': 'qualification',
                                          'subject': str(declaration.get('principal_id') or 'Unrecorded principal')})
        return retained
    account_purposes = supported_refs(account_purposes)
    expected_ca = [row for row in supported_refs(expected_ca) if row.get('policy_id') and row.get('principal_id')
        and any(ref['dataset'] == 'ca_policies' and get(sources[ref['dataset']][ref['dataset_index']]['records'][ref['record_index']], 'id') == row['policy_id']
            and envelope_complete(sources[ref['dataset']][ref['dataset_index']]['source'])
            and source_current(sources[ref['dataset']][ref['dataset_index']]['source'], evaluation_timestamp)
            for ref in row.get('evidence_refs') or [])]
    policy = normalize_inactivity_policy(inactivity_policy, evaluation_timestamp)
    now = instant(evaluation_time(evaluation_timestamp))
    assignments, diagnostics = normalize_assignments(sources, evaluation_timestamp=evaluation_timestamp, boundary=boundary)
    diagnostics.extend(input_diagnostics)
    users = _current_rows(sources, ['users'], boundary, now)
    activities = _current_rows(sources, ['user_signin_activity'], boundary, now)
    registrations = [row for row, _, _ in source_rows(sources, ['auth_methods'], boundary)]
    population = reconcile_registration_population(registrations, users=[row for entries in users.values() for row, _, _ in entries])
    event_index = defaultdict(list)
    for raw, ref, source in source_rows(sources, ['signin_logs'], boundary):
        event_index[get(raw, 'userId')].append((raw, ref, source))
    identities = {}
    complete = source_coverage(sources, ['role_assignments', 'role_assignment_schedules', 'role_eligibility_schedules', 'users'], boundary)
    complete = complete and all(source_current(d.get('source') or {}, evaluation_timestamp)
        for name in ('role_assignments', 'role_assignment_schedules', 'role_eligibility_schedules', 'users') for d in sources.get(name, []))
    complete = bool(complete and now is not None and not diagnostics and all(a['temporal_state'] != 'UnknownTemporalState'
        and a['qualified_complete'] for a in assignments))
    def identity_for(identifier, kind, display=None):
        key = stable_key('PGI-', [boundary, identifier, kind])
        if key not in identities:
            entries = users.get(str(identifier), []) if kind == 'user' else []
            account, conflicts = _account(entries)
            activity_entries = activities.get(str(identifier), []) if kind == 'user' else []
            activity_account, activity_conflicts = _account(activity_entries)
            activity_field = activity_account.get('signInActivity') if activity_entries else account.get('signInActivity')
            if activity_entries:
                conflicts.extend('activity.' + field for field in activity_conflicts)
            selected_activity_entries = activity_entries or entries
            identities[key] = {'identity_id': key, 'principal_id': identifier, 'principal_type': kind,
                'identity_boundary': deepcopy(boundary), 'display_reference': display or account.get('displayName'),
                'account': account, 'account_conflicts': conflicts,
                'account_refs': _unique_refs([ref for _, ref, _ in entries]),
                'account_source_complete': bool(entries) and all(envelope_complete(s) and source_current(s, evaluation_timestamp) for _, _, s in entries),
                'signin_activity': deepcopy(activity_field),
                'activity_refs': _unique_refs([ref for _, ref, _ in selected_activity_entries]),
                'activity_source_complete': bool(selected_activity_entries) and all(envelope_complete(s) and
                    source_current(s, evaluation_timestamp) for _, _, s in selected_activity_entries),
                'account_source_occurrences': [{'source_ref': ref, 'source': deepcopy(s), 'raw': deepcopy(raw)} for raw, ref, s in entries],
                'activity_source_occurrences': [{'source_ref': ref, 'source': deepcopy(s), 'raw': deepcopy(raw)} for raw, ref, s in selected_activity_entries],
                'active_assignments': [], 'eligible_assignments': [], 'assignment_refs': [],
                'scopes': [], 'membership_refs': [], 'source_occurrences': [], 'states': [], 'review_states': []}
        return identities[key]
    def attach(identity, assignment):
        key = assignment['assignment_key']
        identity['assignment_refs'].append(key)
        if assignment['active']: identity['active_assignments'].append(key)
        if assignment['eligible']: identity['eligible_assignments'].append(key)
        identity['scopes'].append({k: assignment[k] for k in ('directory_scope_id', 'app_scope_id', 'administrative_unit_scope')})
        identity['source_occurrences'].extend(deepcopy(assignment['source_occurrences']))
        if assignment['member_type'] == 'Group': identity['states'].append('GroupMediatedPrivilegedIdentity')
    for assignment in assignments:
        identifier = assignment['principal_id'] or assignment['assignment_key']
        kind = assignment['principal_type']
        identity = identity_for(identifier, kind, assignment['principal_display_reference'])
        attach(identity, assignment)
        if kind == 'group' and (assignment['active'] or assignment['eligible']):
            datasets = [d for name in ('group_members', 'privileged_group_members') for d in sources.get(name, [])
                        if get(d.get('source') or {}, 'group_id') == identifier and compatible_source(d.get('source') or {}, boundary)]
            dated = [(instant(d.get('source', {}).get('collected_at')), d) for d in datasets]
            dated = [(stamp, d) for stamp, d in dated if stamp is not None and now is not None and stamp <= now]
            latest = max((stamp for stamp, _ in dated), default=None)
            datasets = [d for stamp, d in dated if stamp == latest]
            captures = [instant(o['source'].get('collected_at')) for o in assignment['source_occurrences']]
            reliable = bool(datasets) and all(stamp is not None and latest >= stamp for stamp in captures)
            content = {stable_key('', d.get('records', [])) for d in datasets}
            reliable = reliable and len(content) == 1
            if not reliable:
                complete = False
                diagnostics.append({'code': 'group_membership_incomplete', 'severity': 'qualification', 'subject': identifier})
                continue
            if not all(envelope_complete(d['source']) for d in datasets):
                complete = False
                diagnostics.append({'code': 'group_membership_incomplete', 'severity': 'qualification', 'subject': identifier})
            selected = datasets[0]
            for name in ('group_members', 'privileged_group_members'):
                for index, original in enumerate(sources.get(name, [])):
                    if original is not selected: continue
                    for position, member in enumerate(selected.get('records') or []):
                        member_id = get(member, 'id')
                        member_kind = str(get(member, '@odata.type') or '').split('.')[-1].lower()
                        if not member_id or member_kind != 'user':
                            complete = False; continue
                        indirect = identity_for(member_id, 'user', get(member, 'displayName'))
                        attach(indirect, assignment)
                        indirect['membership_refs'].append({'dataset': name, 'dataset_index': index, 'record_index': position})
                        indirect['states'].append('GroupMediatedPrivilegedIdentity')
    for identity in identities.values():
        active, eligible = bool(identity['active_assignments']), bool(identity['eligible_assignments'])
        identity['states'].append('ActiveAndEligiblePrivilegedIdentity' if active and eligible else
            'ActivePrivilegedIdentity' if active else 'EligiblePrivilegedIdentity' if eligible else
            'FuturePrivilegedIdentity' if any(a['assignment_key'] in identity['assignment_refs'] and a['temporal_state'] == 'FuturePending' for a in assignments) else
            'HistoricalExpiredPrivilege' if any(a['assignment_key'] in identity['assignment_refs'] and a['temporal_state'] == 'Expired' for a in assignments) else 'UnresolvedPrivilegedIdentity')
        if identity['principal_type'] == 'serviceprincipal': identity['states'].append('WorkloadOrServicePrincipalPrivilege')
        if identity['principal_type'] == 'unresolved': identity['states'].append('UnresolvedPrivilegedIdentity'); complete = False
        if identity['principal_type'] == 'user' and (not identity['account_source_complete'] or identity['account_conflicts']): complete = False
        if identity['account'].get('accountEnabled') is False and (active or eligible): identity['states'].append('DisabledIdentityWithCurrentPrivilege')
        identity['purpose'] = classify_purpose(identity, account_purposes)
        identity['authentication'] = correlate_authentication(identity, sources, boundary, population, expected_ca, now,
            event_index.get(identity['principal_id'], []))
        identity['activity'] = evaluate_activity(identity, policy, evaluation_timestamp)
        purpose = identity['purpose']['purpose']
        successes = [e for e in identity['authentication']['events'] if e['NormalizedSignInOutcome'] == 'Success' and e['correlation_qualified']]
        if purpose == 'emergency' and (active or eligible):
            identity['review_states'].append('EmergencyAccessValidated' if identity['purpose']['validated'] else 'EmergencyAccessCandidateUnverified')
            if successes: identity['review_states'].append('EmergencyAccessRecentlyUsed')
            elif identity['authentication']['events']:
                identity['review_states'].append('EmergencyAccessAuthenticationAttemptNeedsReview')
            else: identity['review_states'].append('EmergencyAccessEvidenceUnavailable')
            for raw, ref, state in source_rows(sources, ['privileged_account_changes'], boundary):
                if get(raw, 'principalId') == identity['principal_id'] and instant(get(raw, 'activityDateTime')) is not None and instant(get(raw, 'activityDateTime')) <= now:
                    identity['review_states'].append('EmergencyAccessConfigurationNeedsReview')
                    identity['authentication']['event_refs'] = _unique_refs(identity['authentication']['event_refs'] + [ref])
        if purpose in {'service', 'synchronization'} and (active or eligible):
            identity['review_states'].append('ServicePurposeValidated' if identity['purpose']['validated'] else 'ServicePurposeUnverified')
            if any(get(e, 'isInteractive') is True for e in successes): identity['review_states'].append('InteractiveUseObservedForServiceAccount')
            if any(get(e, 'isInteractive') is False for e in successes): identity['review_states'].append('NonInteractiveUseObserved')
        if not active and eligible:
            activation = sources.get('role_assignment_schedule_instances') or []
            adequate = source_coverage(sources, ['role_assignment_schedule_instances'], boundary) and all(
                instant(d.get('source', {}).get('window_start')) is not None and
                instant(d.get('source', {}).get('window_end')) is not None for d in activation)
            if adequate and not any(get(raw, 'principalId') == identity['principal_id'] and get(raw, 'assignmentType') == 'Activated'
                for raw, _, _ in source_rows(sources, ['role_assignment_schedule_instances'], boundary)):
                identity['activation_coverage'] = [deepcopy(d['source']) for d in activation]
                identity['review_states'].append('EligiblePrivilegeWithoutRecentActivationEvidence')
        identity['states'] = sorted(set(identity['states']))
        for field in ('active_assignments', 'eligible_assignments', 'assignment_refs'): identity[field] = sorted(set(identity[field]))
    identities = sorted(identities.values(), key=lambda i: i['identity_id'])
    if not complete:
        for identity in identities: identity['states'].append('UnknownBecauseCollectionIncomplete')
    counts = {'assignments': len(assignments), 'identities': len(identities),
              'active_assignments': sum(a['active'] for a in assignments), 'eligible_assignments': sum(a['eligible'] for a in assignments),
              'active_user_identities': sum(i['principal_type'] == 'user' and bool(i['active_assignments']) for i in identities),
              'eligible_user_identities': sum(i['principal_type'] == 'user' and bool(i['eligible_assignments']) for i in identities),
              'unresolved_identities': sum(i['principal_type'] == 'unresolved' for i in identities),
              'group_assignments': sum(a['principal_type'] == 'group' for a in assignments),
              'workload_identities': sum(i['principal_type'] == 'serviceprincipal' for i in identities),
              'disabled_current_identities': sum('DisabledIdentityWithCurrentPrivilege' in i['states'] for i in identities)}
    counts['temporal_states'] = dict(Counter(a['temporal_state'] for a in assignments))
    counts['activity_states'] = dict(Counter(i['activity']['state'] for i in identities))
    counts['purpose_states'] = dict(Counter(i['purpose']['classification'] for i in identities))
    counts['mfa_states'] = dict(Counter(i['authentication']['registration_state'] for i in identities if i['principal_type'] == 'user'))
    refs = [ref for row in assignments for ref in row['evidence_refs']]
    for identity in identities:
        refs.extend(identity['account_refs'] + identity['activity_refs'] + identity['membership_refs'])
        refs.extend(identity['authentication']['event_refs'] + identity['authentication']['registration_refs'])
    refs.extend(ref for row in account_purposes + expected_ca for ref in row.get('evidence_refs') or [])
    bindings = []
    for ref in _unique_refs(refs):
        dataset = sources[ref['dataset']][ref['dataset_index']]
        raw = dataset['records'][ref['record_index']]
        binding = {'source_ref': ref, 'source': deepcopy(dataset['source']),
            'record_id': get(raw, 'id'), 'principal_id': get(raw, 'userId') or get(raw, 'principalId') or get(raw, 'id'),
            'raw_digest': stable_key('', safe_record(raw))}
        # Purpose support is small and explicit; sign-in payloads stay in the
        # correlated event instead of being copied into this locator register.
        if any(ref in row.get('evidence_refs', []) for row in account_purposes): binding['purpose_record'] = safe_record(raw)
        if ref['dataset'] == 'auth_methods': binding['registration_record'] = safe_record(raw)
        bindings.append(binding)
    return {'rule_version': RULE_VERSION, 'evaluation_timestamp': evaluation_time(evaluation_timestamp),
            'boundary': boundary, 'inactivity_policy': policy, 'policy_signature': policy['signature'],
            'assignments': assignments, 'identities': identities, 'observations': _observations(identities, assignments, boundary),
            'purpose_inputs': account_purposes, 'expected_ca_inputs': expected_ca,
            'evidence_bindings': bindings,
            'collection_sources': {name: [{'source': deepcopy(d.get('source') or {}), 'record_count': len(d.get('records') or [])}
                for d in datasets] for name, datasets in sources.items()},
            'counts': counts, 'population_complete': complete, 'diagnostics': diagnostics,
            'qualification': QUALIFICATION, 'directional_metrics': [],
            'limitations': ([] if complete else ['Effective privileged-user population is incomplete or unresolved; counts are retained-evidence counts.'])}


def privileged_context(bundle, evaluated_at=None, tenant_id=None):
    """Use recorded assessment context; no render time, guessed identity or threshold."""
    from .control_reviews import _profile_raw
    context = bundle.get('collection_context') or {}
    metadata = context.get('identity') or {}
    settings = (_profile_raw(bundle.get('assessment_profile') or {}).get('assessment_context') or {})
    from .assessment_catalog import context_validation
    if context_validation(bundle.get('assessment_profile') or {}): settings = {}
    boundary = {'assessment_id': metadata.get('AssessmentId'), 'environment_id': metadata.get('PrimaryEnvironmentId'),
                'tenant_id': tenant_id or bundle.get('expected_tenant_id') or context.get('tenant_id'), 'provider': 'Microsoft Graph directory'}
    stamp = settings.get('privileged_evaluation_timestamp') or evaluated_at or context.get('evaluation_date') or context.get('collected_at')
    return dict(evaluation_timestamp=evaluation_time(stamp), boundary=boundary,
        inactivity_policy=settings.get('privileged_inactivity_policy'), account_purposes=settings.get('account_purposes'),
        expected_ca=settings.get('privileged_expected_ca'))


def client_privileged_assessment(client, evaluated_at=None):
    from .assessment_catalog import collect_assessment_sources
    sources = collect_assessment_sources([client])
    dates = [d.get('source', {}).get('collected_at') for datasets in sources.values() for d in datasets
             if instant(d.get('source', {}).get('collected_at')) is not None]
    stamp = evaluated_at or (max(dates, key=instant) if dates else None)
    return build_privileged_assessment(sources, evaluation_timestamp=stamp, boundary={'provider': 'Microsoft Graph directory'})

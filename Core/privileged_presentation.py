"""Present recorded privilege semantics; no source interpretation or wall clock."""
from collections import defaultdict
from copy import deepcopy
from html import escape

from .privileged_assignments import instant, source_current
from .source_evidence import envelope_complete

LABELS = {'ActivePermanent': 'Permanent Active', 'ActiveTimeBound': 'Time-Bound Active',
          'EligiblePermanent': 'Eligible Permanent', 'EligibleTimeBound': 'Eligible Time-Bound',
          'FuturePending': 'Future Pending', 'Expired': 'Expired', 'UnknownTemporalState': 'Unknown Temporal State',
          'DirectActiveDurationUnverified': 'Active (Duration Unverified)'}
REVIEW_ACTION = ('Confirm business need, owner, purpose, dependencies, assignment scope and supported exceptions. '
                 'Agree whether to retain, reduce, time-bound or remove access. Validate PIM applicability separately '
                 'for human, emergency, service, synchronization and workload identities before choosing treatment.')


def privilege_summary(model):
    counts = model.get('counts') or {}
    states = counts.get('temporal_states') or {}
    return (f"{counts.get('active_user_identities', 0)} unique users with current active privilege; "
            f"{counts.get('eligible_user_identities', 0)} unique users with current eligibility (populations can overlap). "
            f"{counts.get('assignments', 0)} semantic assignments: {counts.get('active_assignments', 0)} active, "
            f"{counts.get('eligible_assignments', 0)} eligible, {states.get('FuturePending', 0)} future, "
            f"{states.get('Expired', 0)} expired, {states.get('UnknownTemporalState', 0)} temporal state unknown. "
            f"{states.get('ActivePermanent', 0)} permanent active; {states.get('DirectActiveDurationUnverified', 0)} active duration unverified. "
            + ('Population coverage is complete for the retained source scope. ' if model.get('population_complete') else
               'Effective privileged-user population completeness is not established. ') + model.get('qualification', ''))


def privileged_summary_html(result):
    model = result.get('privileged_assessment') or {}
    if not model or not model.get('assignments'):
        return ''
    counts = model['counts']
    activity = counts.get('activity_states') or {}
    purpose = counts.get('purpose_states') or {}
    text = privilege_summary(model)
    detail = (f"{counts.get('disabled_current_identities', 0)} disabled identities retain current privilege; "
              f"{counts.get('group_assignments', 0)} group grants; {counts.get('workload_identities', 0)} workload identities; "
              f"{counts.get('unresolved_identities', 0)} unresolved principals. "
              f"{activity.get('PotentiallyInactivePrivilegedAccount', 0)} inactivity review candidates; "
              f"{activity.get('PrivilegedActivityUnknown', 0)} activity unknown; "
              f"{purpose.get('EmergencyAccessCandidate', 0)} emergency candidates; "
              f"{purpose.get('ServiceOrAutomationAccountCandidate', 0)} service candidates. "
              'MFA registration states: ' + ', '.join(f'{k}: {v}' for k, v in counts.get('mfa_states', {}).items()) + '. '
              'Inactivity candidates require an explicit policy and qualified successful activity. Special-purpose candidates require validation.')
    return '<section id="privileged-identity-observations"><h2>Privileged identity review</h2><p>' + escape(text) + '</p><p>' + escape(detail) + '</p></section>'


def assignment_sheet(model):
    rows = []
    for assignment in model['assignments']:
        rows.append({'RecommendationId': '', 'Flagged By': '',
            'Principal Display Name': assignment['principal_display_reference'] or ('Unresolved principal' if assignment['principal_type'] == 'unresolved' else 'Display reference unavailable'),
            'Principal Type': assignment['principal_type'], 'Role Name': assignment['role_name'] or 'Unresolved role definition',
            'Assignment Type': LABELS[assignment['temporal_state']], 'Temporal State': assignment['temporal_state'],
            'Assignment ID': assignment['source_id'], 'Assignment Key': assignment['assignment_key'],
            'Principal ID': assignment['principal_id'], 'Role Definition ID': assignment['role_definition_id'],
            'Role Template ID': assignment['role_template_id'], 'Directory Scope ID': assignment['directory_scope_id'],
            'Application Scope ID': assignment['app_scope_id'], 'Administrative Unit Scope': assignment['administrative_unit_scope'],
            'Start Date': assignment['start'], 'End Date': assignment['end'], 'Expiration Type': assignment['expiration_type'],
            'Duration': assignment['duration'], 'Member Type': assignment['member_type'],
            'Activated Using': assignment['activated_using'], 'Source Occurrences': assignment['source_occurrences'],
            'Identity Resolution': 'Unresolved' if assignment['principal_type'] == 'unresolved' else 'Resolved',
            'Evaluation Timestamp': model['evaluation_timestamp'], 'Evidence References': assignment['evidence_refs'],
            'Reason Flagged': REVIEW_ACTION})
    return {'rows': rows, 'summary': privilege_summary(model), 'details': [REVIEW_ACTION, *model['limitations']],
            'restricted': True, 'preview_columns': []}


def identity_sheet(model):
    rows = []
    for identity in model['identities']:
        auth, activity, purpose = identity['authentication'], identity['activity'], identity['purpose']
        rows.append({'RecommendationId': '', 'Flagged By': '', 'Identity ID': identity['identity_id'],
            'Principal ID': identity['principal_id'], 'Principal Type': identity['principal_type'],
            'Display Reference': identity['display_reference'], 'Account Enabled': identity['account'].get('accountEnabled'),
            'User Type': identity['account'].get('userType'), 'Synchronization State': identity['account'].get('onPremisesSyncEnabled'),
            'Privilege States': identity['states'], 'Active Assignment Keys': identity['active_assignments'],
            'Eligible Assignment Keys': identity['eligible_assignments'], 'Assignment Scopes': identity['scopes'],
            'Group Membership References': identity['membership_refs'], 'MFA Registration State': auth['registration_state'],
            'Observed Authentication': auth['observations'], 'Sign-In Evidence References': auth['event_refs'],
            'Activity State': activity['state'], 'Last Qualified Successful Activity': activity['last_qualified_success'],
            'Last Interactive Attempt': activity['fields'].get('lastSignInDateTime'),
            'Last Noninteractive Attempt': activity['fields'].get('lastNonInteractiveSignInDateTime'),
            'Purpose Classification': purpose['classification'], 'Purpose Validated': purpose['validated'],
            'Review States': identity['review_states'], 'Inactivity Policy ID': activity['policy_id'],
            'Policy Signature': model['policy_signature'], 'Threshold': activity['threshold'],
            'Evaluation Timestamp': model['evaluation_timestamp'], 'Qualification': activity['reason'],
            'Source Occurrences': identity['source_occurrences']})
    return {'key': 'privileged_identity_detail', 'title': 'Privileged Identity Review',
            'appendix_title': 'Restricted privileged identity review', 'rows': rows, 'restricted': True,
            'preview_columns': [], 'default_note': 'See Privileged Identity Review for scoped assignment, authentication and activity evidence.',
            'summary': privilege_summary(model), 'details': [REVIEW_ACTION, *model['limitations']]}


FINDING_CONDITIONS = {
 'ActivePrivilegeWithoutMFARegistration': ('Active privilege with explicitly unregistered MFA', 'Action'),
 'ActivePrivilege.PrivilegedSuccessfulLegacyAuthentication': ('Active privilege with successful legacy authentication', 'Action'),
 'ActivePrivilege.PrivilegedObservedSingleFactorSuccess': ('Active privilege with observed single-factor success', 'Opportunity'),
 'ActivePrivilege.PrivilegedSuccessfulSignInOutsideExpectedCACoverage': ('Active privilege with sign-in outside expected Conditional Access', 'Action'),
 'DisabledIdentityWithCurrentPrivilege': ('Disabled identity retaining current privilege', 'Opportunity'),
 'PermanentPrivilegeNeedsReview': ('Permanent privilege needs business-purpose review', 'Opportunity'),
 'ActivePrivilege.PotentialInactivity': ('Potentially inactive privileged-account review', 'Opportunity'),
 'PrivilegedActivityUnknown': ('Privileged successful activity not established', 'Coverage'),
 'UnresolvedPrivilegedIdentity': ('Privileged principal resolution required', 'Coverage'),
 'EmergencyAccessCandidateUnverified': ('Emergency-access purpose validation required', 'Opportunity'),
 'EmergencyAccessRecentlyUsed': ('Emergency-access activity needs review', 'Opportunity'),
 'EmergencyAccessAuthenticationAttemptNeedsReview': ('Emergency-access authentication attempt needs review', 'Opportunity'),
 'EmergencyAccessConfigurationNeedsReview': ('Emergency-access configuration change needs review', 'Opportunity'),
 'EligiblePrivilegeWithoutRecentActivationEvidence': ('Eligibility activation evidence needs review', 'Opportunity'),
 'ServicePurposeUnverified': ('Service or synchronization purpose validation required', 'Opportunity'),
 'InteractiveUseObservedForServiceAccount': ('Interactive service-account activity needs review', 'Opportunity')}


def privileged_findings(model, sources):
    from .new_recommendation import new_recommendation
    grouped = defaultdict(list)
    for observation in model['observations']:
        if observation['condition'] in FINDING_CONDITIONS: grouped[observation['condition']].append(observation)
    output = []
    for condition, observations in sorted(grouped.items()):
        feature, disposition = FINDING_CONDITIONS[condition]
        refs = [ref for o in observations for ref in o['evidence_refs']]
        states = [sources[r['dataset']][r['dataset_index']]['source'] for r in refs]
        complete = model['population_complete'] and bool(states) and all(envelope_complete(s)
            and source_current(s, model['evaluation_timestamp']) and (s.get('source_file') or s.get('source_api')) for s in states)
        dates = [s.get('collected_at') for s in states if instant(s.get('collected_at')) is not None]
        observed_at = min(dates, key=instant) if dates else ''
        row = new_recommendation(service='Entra', feature=feature,
            observation=f'{len(observations)} retained privileged identities: {feature.lower()}. ' + model['qualification'],
            recommendation=('Collect or validate successful activity, scope, account-purpose and assignment evidence before selecting treatment. ' if disposition == 'Coverage' else '') + REVIEW_ACTION,
            priority='High' if disposition == 'Action' else 'Medium',
            status='Action Required' if disposition == 'Action' else 'Not Assessed' if disposition == 'Coverage' else 'Insight',
            disposition=disposition, finding_key='entra.privileged.' + condition,
            evidence_key='privileged_identity_detail;admin_role_detail')
        row.update(ControlId='IDENTITY.ADMIN', DomainId='identity',
            EvidenceSource='privileged_identity.' + condition,
            EvidenceScope='Retained directory privileged identities and assignment scopes; qualified activity and authentication evidence',
            EvidenceComplete=bool(complete), ObservationDate=observed_at,
            Provider=model['boundary'].get('provider'), PrivilegedObservationIds=[o['observation_id'] for o in observations],
            PrivilegedIdentityIds=[o['identity_id'] for o in observations],
            Population='Current privileged identities for this condition', PopulationDefinition=condition,
            PrivilegedPolicySignature=model['policy_signature'], ControlDefinitionVersion='privileged.' + model['rule_version'] +
                ('.' + model['policy_signature'] if 'Inactivity' in condition or 'Activity' in condition else ''),
            EvidenceLevel='observed_operation' if 'Authentication' in condition or 'Activity' in condition else 'entity_state')
        identities = {i['identity_id']: i for i in model['identities']}
        row.update(MeasuredValue=len(observations), Unit='identities',
                   AffectedObjectIds=sorted({identities[o['identity_id']]['principal_id'] for o in observations}))
        row['collection_status'] = {row['EvidenceSource']: {'available': True,
            'availability_status': 'available' if complete else 'partial',
            'complete': bool(complete), 'truncated': not complete,
            'collected_at': observed_at, 'source_type': 'derived_evidence',
            'source_file': '; '.join(sorted({s.get('source_file') or s.get('source_api') for s in states if s.get('source_file') or s.get('source_api')})),
            'tenant_id': model['boundary'].get('tenant_id'), 'scope': row['EvidenceScope']}}
        output.append(row)
    return output


def qualify_privileged_recommendations(rows, model):
    """Keep legacy routes while replacing blanket or stale derived privilege claims."""
    output = []
    for original in rows or []:
        row = deepcopy(original)
        keys = str(row.get('EvidenceKey') or '')
        if ('admin_role_detail' in keys and not row.get('PrivilegedObservationIds')
                and row.get('Historical') != 'Yes' and not row.get('BaselineCheck')
                and row.get('Disposition') not in {'Assurance', 'Reference'}):
            row.setdefault('OriginalObservation', row.get('Observation'))
            row.setdefault('OriginalRecommendation', row.get('Recommendation'))
            selected = [a['assignment_key'] for a in model['assignments'] if a['temporal_state'] in {'ActivePermanent', 'DirectActiveDurationUnverified'}]
            row.update(Observation=f'{len(selected)} current permanent or duration-unverified active assignments require scope and purpose review. ' + privilege_summary(model),
                       Recommendation=REVIEW_ACTION, PrivilegedAssignmentKeys=selected,
                       Disposition='Opportunity' if selected else 'Reference' if model['population_complete'] else 'Coverage',
                       Status='Insight' if selected or model['population_complete'] else 'Not Assessed',
                       EvidenceComplete=model['population_complete'])
        output.append(row)
    return output


def prepare_privileged_sheets(bundle, result):
    model = result.get('privileged_assessment')
    if not model: return
    if model.get('assignments'):
        existing = (bundle.get('sheets') or {}).get('admin_role_detail') or {}
        sheet = assignment_sheet(model)
        sheet.update({k: existing[k] for k in ('key', 'title', 'appendix_title', 'default_note') if k in existing})
        sheet.setdefault('key', 'admin_role_detail'); sheet.setdefault('title', 'Admin Role Detail')
        sheet.setdefault('appendix_title', 'Restricted administrative role detail'); sheet.setdefault('default_note', REVIEW_ACTION)
        bundle.setdefault('sheets', {})['admin_role_detail'] = sheet
        bundle['sheets']['privileged_identity_detail'] = identity_sheet(model)
    # Previous previews may contain names; only protected technical sheets retain them.
    bundle['appendix_sections'] = [s for s in bundle.get('appendix_sections', []) if s.get('key') != 'admin_role_detail']

"""Correlate an observed condition with the retained controls that may mitigate it.

A correlation is a structured result, computed once from retained evidence and
stored on the finding as ControlCorrelation. Recommendation text and renderers
never decide these states; they display them. Unknown relationships stay
unknown: a configured control is not proof of operation, an adverse event is
not proof of control failure, and exclusions stay separate from general scope.

Each lane maps to an existing readiness treatment (see LANES), so the action
plan adds no parallel state system.
"""
from copy import deepcopy
from datetime import datetime

VERSION = '1.0.0'
STATES = ('ControlAbsent', 'ControlConfigured', 'ControlScopeUnresolved', 'ControlOperationUnresolved',
          'ControlOperatingAsExpected', 'ControlBypassObserved', 'ControlIneffective', 'ControlConflict',
          'ControlNotApplicable')
# Customer action lane: (label, existing ReadinessTreatment it represents).
LANES = {
    'FixNow': ('Fix now', 'Remediate'),
    'ValidateExistingControl': ('Validate existing control', 'ConfirmBeforePilot'),
    'InvestigateAffectedObjects': ('Investigate affected objects', 'ScopeReviewRequired'),
    'CollectMissingEvidence': ('Collect missing evidence', 'EvidenceGap'),
    'PlanImprovement': ('Plan improvement', None),
}
LEGACY_KEY = 'entra.signins.legacy_auth'
# Rights Management makes encryption available to labels; it never shows that content is protected.
RMS_ENABLED_OBSERVATION = ('Azure Rights Management is enabled. This makes encryption available to sensitivity labels and '
                           'rights-management policies; it does not establish that content is protected, which depends on '
                           'published labels and their application to content.')
NARRATIVE_FIELDS = ('Observed condition', 'Existing control', 'Control configuration result', 'Control scope',
                    'Observed operation', 'Exclusions or exceptions', 'Residual risk', 'Evidence completeness',
                    'Affected population', 'Required decision', 'Next technical step', 'Closure evidence')


def _time(value):
    try:
        parsed = datetime.fromisoformat(str(value or '').replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _plural(count, singular, plural=None):
    return f"{count} {singular if count == 1 else plural or singular + 's'}"


def narrative_fields(correlation):
    """The required control narrative; unknown fields stay None rather than generic prose."""
    controls = correlation.get('controls') or []
    exclusions = correlation.get('exclusions') or {}
    return {
        'Observed condition': correlation.get('observed_condition'),
        'Existing control': '; '.join(item['name'] for item in controls) or None,
        'Control configuration result': correlation.get('configuration_state'),
        'Control scope': (correlation.get('scope') or {}).get('description'),
        'Observed operation': correlation.get('operation_state'),
        'Exclusions or exceptions': exclusions.get('description'),
        'Residual risk': correlation.get('residual_risk'),
        'Evidence completeness': correlation.get('evidence_completeness'),
        'Affected population': (correlation.get('affected_population') or {}).get('description'),
        'Required decision': correlation.get('required_decision'),
        'Next technical step': (correlation.get('next_steps') or [None])[0],
        'Closure evidence': correlation.get('closure_evidence'),
    }


def _finish(correlation):
    correlation['narrative_fields'] = narrative_fields(correlation)
    correlation['lane_label'] = LANES[correlation['lane']][0] if correlation.get('lane') else None
    return correlation


# --- Legacy authentication ------------------------------------------------------------

def _legacy_policies(client):
    from .tenant_baseline import _get, _list, _policy_terms
    policies = []
    for policy in _list(getattr(client, 'ca_policies', []) or []):
        terms = _policy_terms(policy, {})
        if not (terms['legacy_block'] and terms['all_apps'] and not terms['restricted']):
            continue
        mode = 'enforced' if terms['enforced'] else 'report_only' if terms['report_only'] else 'disabled'
        policies.append({'policy_id': str(_get(policy, 'id', '') or ''), 'name': str(terms['name']).strip(), 'mode': mode,
                         'include_all': terms['include_all'], 'include_users': sorted(terms['include_users']),
                         'include_groups': sorted(terms['include_groups']), 'include_roles': terms['include_roles'],
                         'include_guests': terms['include_guests'],
                         'excluded_users': sorted(terms['exclude_users']), 'excluded_groups': sorted(terms['exclude_groups']),
                         'excluded_roles': terms['exclude_roles'], 'excluded_guests': terms['exclude_guests'],
                         'created': _get(policy, 'createdDateTime'), 'modified': _get(policy, 'modifiedDateTime')})
    return policies


def _membership(user, policy, members):
    if not user:
        return 'unknown'
    unknown = policy['excluded_roles'] or policy['excluded_guests']
    if user in policy['excluded_users']:
        return 'excluded'
    for group in policy['excluded_groups']:
        if group not in members:
            unknown = True
        elif user in members[group]:
            return 'excluded'
    if unknown:
        return 'unknown'
    if policy['include_all'] or user in policy['include_users']:
        return 'included'
    for group in policy['include_groups']:
        if group not in members:
            return 'unknown'
        if user in members[group]:
            return 'included'
    return 'unknown' if policy['include_roles'] or policy['include_guests'] else 'not_targeted'


def _timing(when, policy):
    event, created, modified = _time(when), _time(policy['created']), _time(policy['modified'])
    if event is None or (created is None and modified is None):
        return 'unknown'
    if created is not None and event < created:
        return 'before_creation'
    return 'after_last_change' if event >= (modified or created) else 'during_change_window'


def _applied(raw, policy):
    applied = raw.get('appliedConditionalAccessPolicies') if isinstance(raw, dict) else None
    if not isinstance(applied, list):
        return 'unavailable'
    for item in applied:
        if not isinstance(item, dict):
            continue
        if (policy['policy_id'] and str(item.get('id') or '') == policy['policy_id']) or (
                not policy['policy_id'] and str(item.get('displayName') or '').strip() == policy['name']):
            return str(item.get('result') or 'unknown').lower()
    return 'not_listed'


def _event_category(facts, enforced):
    results = [facts['policies'][policy['name']] for policy in enforced]
    if any(item['applied'] == 'failure' for item in results):
        return 'conflict'
    if results and all(item['membership'] == 'excluded' for item in results):
        return 'excluded'
    if results and all(item['timing'] == 'before_creation' for item in results):
        return 'preceded_enforcement'
    if any(item['membership'] == 'included' and item['timing'] == 'after_last_change'
           and item['applied'] in {'notapplied', 'not_listed'} for item in results):
        return 'bypass'
    if results and all(item['membership'] == 'not_targeted' for item in results):
        return 'outside_scope'
    return 'unresolved'


def correlate_legacy_authentication(client, account_purposes=None):
    """Correlate retained legacy-client sign-ins with legacy-blocking policies and their exclusions."""
    from .authentication_findings import signin_datasets
    from .signin_evidence import classify_signin_outcome, is_legacy_signin, legacy_event_summary
    from .source_evidence import source_is_complete
    from .tenant_baseline import group_members, security_defaults_state, validated_emergency_accounts
    datasets = signin_datasets(client)
    summary = legacy_event_summary(datasets)
    events = [raw for dataset in datasets for raw in dataset.get('records') or [] if isinstance(raw, dict) and is_legacy_signin(raw)]
    defaults = security_defaults_state(client)
    complete_policies = source_is_complete(client, 'ca_policies')
    correlation = {'version': VERSION, 'condition': 'legacy_authentication.sign_in_events',
                   'observed_condition': (f"{summary['total']} legacy authentication sign-in attempts in the retained sample: "
                                          f"{summary['counts']['Success']} succeeded."),
                   'controls': [], 'scope': {}, 'exclusions': {}, 'event_correlation': [],
                   'observation_period': deepcopy(summary['windows']), 'source_complete': summary['complete']}
    sample = ('complete for the recorded query' if summary['complete'] else
              'incomplete' + (f" ({summary['windows'][0].get('reason')})" if summary['windows'] and summary['windows'][0].get('reason') else ''))
    if not complete_policies and defaults['state'] != 'enabled':
        correlation.update(state='ControlScopeUnresolved', configuration_state='ControlScopeUnresolved',
                           operation_state='ControlOperationUnresolved', lane=None, configuration_unresolved=True,
                           evidence_completeness=f'Conditional Access inventory not complete; sign-in sample {sample}.',
                           customer_narrative='Whether a Conditional Access policy blocks legacy authentication was not established, '
                                              'because the Conditional Access inventory was not read completely.',
                           next_steps=['Read the complete Conditional Access policy inventory and rebuild the assessment.'])
        return _finish(correlation)
    policies = _legacy_policies(client) if complete_policies else []
    enforced = [policy for policy in policies if policy['mode'] == 'enforced']
    report_only = [policy for policy in policies if policy['mode'] == 'report_only']
    active = enforced or report_only
    members = group_members(client)
    validated, _ = validated_emergency_accounts(client, account_purposes)
    excluded = set()
    unresolved_groups = []
    for policy in active:
        excluded |= set(policy['excluded_users'])
        for group in policy['excluded_groups']:
            if group in members:
                excluded |= members[group]
            else:
                unresolved_groups.append(group)
    for policy in policies:
        correlation['controls'].append({
            'control_type': 'preventive', 'name': policy['name'], 'policy_id': policy['policy_id'], 'mode': policy['mode'],
            'configuration_state': 'ControlConfigured' if policy['mode'] != 'disabled' else 'ControlAbsent',
            'created': policy['created'], 'last_modified': policy['modified'],
            'enforcement_date': ('not retained' if not (policy['created'] or policy['modified']) else
                                 'current definition in effect from ' + str(policy['modified'] or policy['created']))})
    if defaults['state'] == 'enabled' and not active:
        correlation['controls'].append({'control_type': 'preventive', 'name': 'Security defaults', 'mode': 'enforced',
                                        'configuration_state': 'ControlConfigured'})
    target = ('All users' if active and all(policy['include_all'] for policy in active) else
              'Specific users or groups' if active else 'Not applicable')
    correlation['scope'] = {'target': target, 'applications': 'All cloud apps' if active else 'Not applicable',
                            'client_app_types': 'Exchange ActiveSync and other clients' if active else 'Not applicable',
                            'description': (target + (f' except {_plural(len(excluded), "account")}' if excluded else '')
                                            if active else 'No legacy-blocking policy applies')}
    outcomes = {}
    for raw in events:
        normalized = classify_signin_outcome(raw)['normalized_outcome']
        user = str(raw.get('userId') or '') or None
        facts = {'event_id': raw.get('id'), 'created': raw.get('createdDateTime'), 'user_id': user,
                 'client_app': raw.get('clientAppUsed'), 'resource': raw.get('resourceDisplayName') or raw.get('appDisplayName'),
                 'conditional_access_status': raw.get('conditionalAccessStatus'),
                 'error_code': (raw.get('status') or {}).get('errorCode') if isinstance(raw.get('status'), dict) else None,
                 'outcome': normalized, 'policies': {}}
        for policy in active:
            facts['policies'][policy['name']] = {'membership': _membership(user, policy, members),
                                                 'timing': _timing(raw.get('createdDateTime'), policy),
                                                 'applied': _applied(raw, policy)}
        if normalized == 'Success':
            facts['category'] = (_event_category(facts, enforced) if enforced else
                                 'report_only' if report_only else 'no_policy')
        elif normalized == 'BlockedByConditionalAccess' and any(
                item['applied'] == 'failure' for name, item in facts['policies'].items()
                if name in {policy['name'] for policy in enforced}):
            facts['category'] = 'blocked_by_policy'
        else:
            facts['category'] = 'other_outcome'
        outcomes[facts['category']] = outcomes.get(facts['category'], 0) + 1
        correlation['event_correlation'].append(facts)
    correlation['event_correlation'].sort(key=lambda item: (str(item['created'] or ''), str(item['event_id'] or '')))
    successes = [item for item in correlation['event_correlation'] if item['outcome'] == 'Success']
    success_users = {item['user_id'] for item in successes if item['user_id']}
    involved = success_users & excluded
    unvalidated = excluded - validated
    if active:
        correlation['exclusions'] = {
            'count': len(excluded), 'validated': len(excluded & validated), 'unvalidated': len(unvalidated),
            'unresolved_groups': sorted(set(unresolved_groups)), 'accounts_with_successful_events': len(involved),
            'state': 'none' if not excluded and not unresolved_groups else 'validated' if not unvalidated and not unresolved_groups else 'review_required',
            'purpose_basis': 'Retained account-purpose declarations; display names never validate purpose.',
            'description': (f"{_plural(len(excluded), 'excluded account')}; {len(unvalidated)} without a validated emergency-access or service purpose"
                            if excluded else 'No excluded accounts')}
    correlation['affected_population'] = {
        'successful_events': len(successes), 'accounts': len(success_users),
        'excluded_accounts_involved': len(involved), 'events_without_user': sum(not item['user_id'] for item in successes),
        'description': (f"{_plural(len(successes), 'successful legacy sign-in')} from {_plural(len(success_users), 'account')}"
                        + (f"; {sum(not item['user_id'] for item in successes)} without a retained user" if any(not item['user_id'] for item in successes) else '')
                        if successes else 'No successful legacy sign-in in the retained sample')}
    correlation['evidence_completeness'] = f'Conditional Access inventory complete; sign-in sample {sample}.'
    _legacy_state(correlation, summary, enforced, report_only, defaults, outcomes, successes, excluded, involved, unvalidated)
    return _finish(correlation)


def _legacy_state(correlation, summary, enforced, report_only, defaults, outcomes, successes, excluded, involved, unvalidated):
    count = len(successes)
    names = ', '.join(f"'{policy['name']}'" for policy in (enforced or report_only))
    blocked, other = outcomes.get('blocked_by_policy', 0), outcomes.get('other_outcome', 0)
    complete = summary['complete']
    success_text = f"The retained sign-in sample contains {_plural(count, 'successful legacy-client event')}"
    if enforced:
        lead = (f"A legacy authentication blocking policy ({names}) is enabled and targets {correlation['scope']['target'].lower()}"
                + (f" except {_plural(len(excluded), 'account')}" if excluded else '') + '.')
        configuration = 'ControlConfigured'
    elif report_only:
        lead = (f"A legacy authentication blocking policy ({names}) exists but is report-only, so it records what it would "
                'block without blocking it.')
        configuration = 'ControlConfigured'
    elif defaults['state'] == 'enabled':
        lead = 'Security defaults are enabled, and they block legacy authentication.'
        configuration = 'ControlConfigured'
    else:
        lead = 'No enforced Conditional Access policy blocks legacy authentication' + (
            ', and security defaults are disabled.' if defaults['state'] == 'disabled' else '.')
        configuration = 'ControlAbsent'
    categories = {item['category'] for item in successes}
    steps, sentences = [], [lead]
    exclusion_step = (f"Validate each excluded account's purpose, owner, authentication design, monitoring and review"
                      + (f" ({len(involved)} of the {len(excluded)} excluded accounts used legacy authentication successfully)." if involved else '.'))
    fresh = 'Obtain a fresh complete sign-in sample for the assessment period and rebuild the assessment.'
    decision = closure = risk = None
    operation = 'ControlOperationUnresolved'
    if configuration == 'ControlAbsent':
        state = 'ControlAbsent'
        lane = 'FixNow' if count else None
        if count:
            sentences.append(success_text + '.')
        steps = ['Create or enable a Conditional Access policy that blocks legacy authentication (Exchange ActiveSync and other clients) '
                 'for all users, excluding only documented emergency-access accounts.',
                 'Identify the accounts and clients behind the successful legacy sign-ins and move them to modern authentication before enforcement.',
                 'Use report-only mode first to confirm the user impact, then enforce the policy.']
        decision = 'Approve enforcement of legacy-authentication blocking for all users.'
        closure = 'A dated Conditional Access export showing the enforced policy and its exclusions, and a sign-in sample with no successful legacy authentication outside approved exclusions.'
        risk = 'Legacy authentication is not blocked, and legacy protocols cannot require MFA.'
    elif not enforced and report_only:
        state = 'ControlIneffective'
        lane = 'FixNow' if count else None
        if count:
            would = sum(any(item['applied'].startswith('reportonly') for item in event['policies'].values()) for event in successes)
            sentences.append(success_text + (f'; the policy reported that it would have blocked {would} of them.' if would else '.'))
        steps = [f"Review the report-only results of {names} for the successful legacy sign-ins and their accounts.",
                 'Move the clients that still need legacy authentication to modern authentication, or document an approved exception.',
                 f"Switch the existing policy {names} from report-only to On; do not create a duplicate policy."]
        decision = 'Approve enforcement of the existing legacy-authentication policy.'
        closure = 'A dated Conditional Access export showing the policy switched On, and a sign-in sample with no successful legacy authentication outside approved exclusions.'
        risk = 'The policy does not block legacy authentication while it is report-only.'
    elif 'conflict' in categories:
        state, lane = 'ControlConflict', 'ValidateExistingControl'
        operation = 'ControlConflict'
        sentences.append(success_text + ', and for some of them the retained applied-policy result reports a block that conflicts with the successful outcome.')
        steps = ['Correlate each successful event with its applied-policy result and the sign-in status, and resolve the conflicting records.',
                 'Retest with a nonexcluded test identity.', fresh]
        decision = 'Decide, after resolving the conflicting records, whether the policy blocks legacy authentication as intended.'
        closure = 'Reconciled sign-in records with consistent applied-policy results for the affected events.'
    elif 'bypass' in categories:
        state, lane = 'ControlBypassObserved', 'FixNow'
        operation = 'ControlBypassObserved'
        bypass = [item for item in successes if item['category'] == 'bypass']
        clients = ', '.join(sorted({str(item['client_app']) for item in bypass if item['client_app']})) or 'client not retained'
        resources = ', '.join(sorted({str(item['resource']) for item in bypass if item['resource']})) or 'resource not retained'
        sentences.append(f"{success_text}. For {_plural(len(bypass), 'event')} from accounts the policy targets, after its last change, "
                         'the applied-policy result shows that the existing policy did not apply.')
        steps = [f"Compare the policy's client-app conditions and target resources with the successful events ({clients}; {resources}).",
                 f"Correct the conditions of the existing policy {names} so they apply to these clients, then retest with a nonexcluded test identity.",
                 'Confirm with a complete sign-in sample that no successful legacy sign-in remains outside the approved exclusions.']
        decision = 'Approve the correction of the existing policy conditions.'
        closure = 'A dated policy export showing the corrected conditions and a sign-in sample in which the policy applies to legacy clients.'
        risk = 'Legacy authentication succeeds for accounts the policy is intended to cover.'
    elif categories & {'unresolved', 'outside_scope'}:
        state, lane = 'ControlOperationUnresolved', 'ValidateExistingControl'
        unresolved = [item for item in successes if item['category'] in {'unresolved', 'outside_scope'}]
        explained = count - len(unresolved)
        sentences.append(f"{success_text}, but the assessment did not establish whether "
                         + ('those events' if not explained else f"{len(unresolved)} of those events")
                         + ' preceded enforcement, involved excluded accounts, or fell outside the effective policy conditions. '
                         + ('Validate the exclusions and correlate' if excluded else 'Correlate')
                         + ' each successful event with the applied Conditional Access policy before treating this as a policy failure.')
        facts = [item for event in unresolved for item in event['policies'].values()]
        if any(item['timing'] in {'unknown', 'during_change_window'} for item in facts):
            steps.append(f"Validate the policy state and its enforcement date (creation and last-change dates) for {names} against the event times.")
        steps.append(f"Validate the client-app conditions and resource scope of {names} against the clients and resources in the events.")
        if any(item['applied'] == 'unavailable' for item in facts):
            steps.append('Correlate each successful event with its applied-policy result for the blocking policy.')
        if any(not event['user_id'] for event in unresolved) or excluded:
            steps.append('Compare the accounts in the successful events with the excluded accounts.')
        if unvalidated:
            steps.append(exclusion_step)
        steps.append('Retest with a nonexcluded test identity if the correlation remains inconclusive.')
        decision = 'Decide, after correlation, whether the successful events are explained by exclusions, timing or policy conditions, or show a policy gap.'
        closure = 'Each successful legacy sign-in correlated with its applied-policy result, account scope and timing, with any gap treated.'
        risk = 'Not established.'
    elif 'excluded' in categories:
        state = 'ControlScopeUnresolved' if unvalidated & {item['user_id'] for item in successes} else 'ControlOperatingAsExpected'
        lane = 'InvestigateAffectedObjects' if state == 'ControlScopeUnresolved' else None
        excluded_events = [item for item in successes if item['category'] == 'excluded']
        after = all(all(policy['timing'] == 'after_last_change' for policy in item['policies'].values()) for item in excluded_events)
        not_applied = all(all(policy['applied'] == 'notapplied' for policy in item['policies'].values()) for item in excluded_events)
        prefix = (f"All {_plural(count, 'successful legacy-client event')} in the retained sign-in sample" if len(excluded_events) == count
                  else f"{len(excluded_events)} of the {count} successful legacy-client events in the retained sign-in sample")
        sentences.append(prefix + f" came from {_plural(len(involved), 'excluded account')}"
                         + (', the policy reported that it did not apply to them' if not_applied else '')
                         + (', and they occurred after the policy was last changed' if after else '') + '.')
        if state == 'ControlScopeUnresolved':
            sentences.append('The policy did not fail for these events. The remaining exposure is in the excluded accounts, '
                             'whose emergency-access or service purpose is not validated.')
            steps = [exclusion_step,
                     'Decide whether those accounts still need legacy authentication (for example SMTP AUTH for a device or application), '
                     'and move them to modern authentication or a narrower, documented exception.']
            if not complete:
                steps.append(fresh)
            steps.append('Retest with a nonexcluded test identity if needed.')
            decision = 'Decide whether each excluded account still requires an exclusion and legacy authentication.'
            closure = ("A dated record of each excluded account's purpose, owner, authentication design, monitoring and review, "
                       'and a complete sign-in sample with no successful legacy authentication outside the approved exclusions.')
            risk = 'Excluded accounts can still authenticate with legacy protocols, which cannot require MFA.'
        else:
            operation = 'ControlOperatingAsExpected'
    elif successes and categories <= {'preceded_enforcement'}:
        state, lane = 'ControlConfigured', None
        sentences.append(f"The {_plural(count, 'successful legacy-client event')} in the retained sign-in sample occurred before the policy was "
                         'created, so they do not show the policy failing. No later legacy sign-in was returned.')
    elif blocked and complete:
        state, lane, operation = 'ControlOperatingAsExpected', None, 'ControlOperatingAsExpected'
        sentences.append(f"The policy blocked {_plural(blocked, 'legacy-client attempt')} in the retained sign-in sample, and no successful "
                         'legacy sign-in was returned.')
    elif not complete or summary['counts']['Unknown'] or not summary['known_population_and_period']:
        state, lane = 'ControlOperationUnresolved', 'CollectMissingEvidence'
        sentences.append('The retained sign-in sample is incomplete or has unknown results, so whether legacy sign-ins succeed outside '
                         'the policy is not established.')
        steps = [fresh]
        decision = None
        closure = 'A complete, dated sign-in sample for the assessment period.'
    else:
        state, lane = 'ControlConfigured', None
        sentences.append('No successful legacy-client sign-in was returned in the retained sample, so the policy was not observed '
                         'blocking an attempt; this is configuration evidence.')
    if other:
        sentences.append(f"{_plural(other, 'other legacy attempt')} failed for reasons other than Conditional Access.")
    if not complete and lane not in {None, 'CollectMissingEvidence'}:
        sentences.append('The sign-in sample is incomplete, so other successful legacy sign-ins cannot be ruled out.')
        if fresh not in steps:
            steps.append(fresh)
    correlation.update(state=state, configuration_state=configuration, operation_state=operation, lane=lane,
                       customer_narrative=' '.join(sentences), next_steps=steps, required_decision=decision,
                       closure_evidence=closure, residual_risk=risk)


def apply_legacy_correlation(row, client, account_purposes=None):
    """Attach the correlation to a legacy sign-in finding and derive its treatment from it."""
    correlation = correlate_legacy_authentication(client, account_purposes)
    row = dict(row)
    row['ControlCorrelation'] = correlation
    if correlation.get('configuration_unresolved'):
        return row
    events = row.get('LegacyEventObservation') or row.get('Observation') or ''
    row['LegacyEventObservation'] = events
    lane = correlation['lane']
    row['Observation'] = correlation['customer_narrative'] + ' ' + events
    row['SemanticCondition'] = f"legacy_correlation={correlation['state']}"
    if lane is None:
        row.update(Disposition='Reference', Status='Insight', Recommendation='', ReadinessTreatment='Pass')
    else:
        row.update(Disposition='Coverage' if lane == 'CollectMissingEvidence' else 'Action',
                   Status='Not Assessed' if lane == 'CollectMissingEvidence' else 'Action Required',
                   Recommendation=' '.join(correlation['next_steps']), ReadinessTreatment=LANES[lane][1],
                   CompletionEvidence=correlation['closure_evidence'] or row.get('CompletionEvidence'))
        if lane == 'FixNow':
            # A legacy-only blocking gap is tenant remediation (the baseline uses the same severity).
            row['Priority'] = 'Medium'
    return row


# --- Audit and licensing components (used by required-control gaps) --------------------

def _component(state, detail=None):
    return {'state': state, **({'detail': detail} if detail else {})}


def correlate_audit(settings, events, windows, operation_result=None):
    """Separate audit configuration, observed operation and the components evidence cannot answer.

    Operation is observed only when the shared operational evaluation passed;
    an event count alone does not establish it.
    """
    flags = {}
    for setting in settings:
        for key, name in (('unified_audit_enabled', 'unified'), ('UnifiedAuditLogIngestionEnabled', 'unified'),
                          ('admin_audit_enabled', 'admin'), ('AdminAuditLogEnabled', 'admin')):
            if type(setting.get(key)) is bool:
                flags.setdefault(name, set()).add(setting[key])
        if setting.get('retention_days') not in (None, ''):
            flags.setdefault('retention', set()).add(str(setting['retention_days']))
    unified = flags.get('unified')
    configuration = ('observed' if unified == {True} else 'not_enabled' if unified == {False} else 'unresolved')
    window = '; '.join(sorted(set(windows))) if windows else ''
    components = {
        'configuration': _component(configuration, 'Unified audit ' + ('enabled' if unified == {True} else 'disabled' if unified == {False} else 'not established')
                                    + ('; admin audit enabled' if flags.get('admin') == {True} else '')),
        'operation': _component('observed' if events and window and operation_result == 'pass' else 'unresolved',
                                f'{events} Copilot audit events for {window}' if events and window else None),
        'workload_coverage': _component('unresolved'),
        'retention_coverage': _component('observed' if flags.get('retention') else 'unresolved',
                                         ('Retention: ' + ', '.join(sorted(flags['retention'])) + ' days') if flags.get('retention') else None),
        'monitoring_ownership': _component('unresolved'),
        'investigation_process': _component('unresolved'),
    }
    if configuration != 'observed':
        return None
    labels = {'workload_coverage': 'workload coverage for the services Copilot uses',
              'retention_coverage': 'the audit retention period against investigation needs',
              'monitoring_ownership': 'who monitors audit events', 'investigation_process': 'the investigation and response process'}
    open_items = [labels[name] for name in labels if components[name]['state'] == 'unresolved']
    if components['operation']['state'] == 'unresolved':
        open_items.insert(0, 'identified, dated Copilot audit activity')
    admin = flags.get('admin')
    enabled = ('Unified audit logging' + (' and admin audit logging are' if admin == {True} else ' is') + ' enabled'
               + (', and admin audit logging is disabled' if admin == {False} else ''))
    period = f' for {window}' if window else ''
    operation = (f", and {_plural(events, 'Copilot audit event')} were retained{period}. Audit is configured and was observed "
                 'recording Copilot activity in that window.' if components['operation']['state'] == 'observed' else
                 f"; {_plural(events, 'Copilot audit record')} {'was' if events == 1 else 'were'} retained{period}, but observed operation "
                 'was not established from them.' if events else '; Copilot audit events were not established for a known window.')
    narrative = enabled + operation + (' Still unresolved: ' + ', '.join(open_items[:-1]) + (' and ' if len(open_items) > 1 else '')
                                       + open_items[-1] + '.' if open_items else '')
    return _finish({'version': VERSION, 'condition': 'audit.coverage', 'observed_condition': narrative,
                    'controls': [{'control_type': 'detective', 'name': 'Microsoft 365 unified audit log', 'configuration_state': 'ControlConfigured'}],
                    'components': components, 'state': 'ControlScopeUnresolved' if open_items else 'ControlConfigured',
                    'configuration_state': 'ControlConfigured',
                    'operation_state': 'ControlOperatingAsExpected' if components['operation']['state'] == 'observed' else 'ControlOperationUnresolved',
                    'lane': 'ValidateExistingControl', 'customer_narrative': narrative,
                    'scope': {'description': 'Tenant audit configuration; workload coverage not established'},
                    'evidence_completeness': 'Audit configuration and a bounded Copilot audit query were retained.',
                    'next_steps': (['Confirm ' + ', '.join(open_items) + ', and record the result.'] if open_items else []),
                    'required_decision': 'Confirm that audit retention, coverage and response ownership meet investigation requirements.' if open_items else None,
                    'closure_evidence': 'A dated record of the audit retention period, covered workloads, monitoring owner and investigation and response process.',
                    'residual_risk': None})


def correlate_license_assignment(capacity, licensed_users, enabled_users, period, active_users=None):
    """Separate subscription capacity, assigned seats and usage populations; differences are a reconciliation question."""
    seats = capacity or {}
    paid, assigned = seats.get('enabled_seats'), seats.get('assigned_seats')
    if not (seats.get('available') is True and type(paid) is int and type(assigned) is int and 0 <= assigned <= paid):
        return None
    values = {'assigned seats': assigned}
    components = {'subscription_capacity': _component('observed', f'{paid} paid seats, {assigned} assigned, {paid - assigned} unassigned')}
    sentences = [f'Microsoft 365 Copilot subscription capacity was retained: {paid} paid seats, {assigned} assigned and {paid - assigned} unassigned.']
    if type(licensed_users) is int:
        components['licensed_users'] = _component('observed', f'{licensed_users} users with an assigned Copilot license')
        values['licensed users'] = licensed_users
        sentences.append(f"The license inventory identifies {_plural(licensed_users, 'user')} with an assigned Copilot license.")
    else:
        components['licensed_users'] = _component('unresolved')
    if type(enabled_users) is int:
        components['usage_enabled_population'] = _component('observed', f'{enabled_users} usage-enabled users ({period})')
        values['usage-enabled users'] = enabled_users
        sentences.append(f'Usage reporting covers {enabled_users} enabled users ({period}); usage is not proof of assigned entitlement.')
    else:
        components['usage_enabled_population'] = _component('unresolved')
    if type(active_users) is int:
        components['active_usage_population'] = _component('observed', f'{active_users} active users ({period})')
    reconciled = len(set(values.values())) <= 1
    components['population_reconciliation'] = _component('observed' if reconciled else 'unresolved')
    components['pilot_assignment'] = _component('not_applicable', 'Tenant readiness does not require a pilot assignment list.')
    components['apps_prerequisites'] = _component('separate_control', 'LICENSE.APPS')
    if not reconciled:
        sentences.append('These counts come from different sources and do not match, so the difference is a reconciliation question, '
                         'not evidence that licenses are missing.')
        others = ' and '.join(f'the {value} {name}' for name, value in values.items() if name != 'assigned seats')
        steps = [f'Reconcile the {assigned} assigned seats with {others} (for example seats assigned to disabled or deleted accounts, '
                 'group-based assignment errors or reporting delay).']
    else:
        steps = ['Confirm the license assignment method and record the reconciled counts.']
    steps.append('A pilot assignment list is not required for tenant readiness.')
    narrative = ' '.join(sentences)
    return _finish({'version': VERSION, 'condition': 'license.assignment', 'observed_condition': narrative,
                    'controls': [{'control_type': 'entitlement', 'name': 'Microsoft 365 Copilot subscription', 'configuration_state': 'ControlConfigured'}],
                    'components': components, 'state': 'ControlConfigured' if reconciled else 'ControlScopeUnresolved',
                    'configuration_state': 'ControlConfigured', 'operation_state': 'ControlNotApplicable',
                    'lane': 'ValidateExistingControl', 'customer_narrative': narrative, 'next_steps': steps,
                    'scope': {'description': 'Tenant Copilot subscriptions and assigned users'},
                    'affected_population': {'description': '; '.join(f'{value} {name}' for name, value in values.items())},
                    'evidence_completeness': 'Subscription capacity and usage populations were retained from separate sources.',
                    'required_decision': None if reconciled else 'Confirm which population is authoritative after reconciliation.',
                    'closure_evidence': 'A dated license inventory reconciling assigned seats, licensed users and usage-enabled users.',
                    'residual_risk': None})


def correlate_device_protection(row):
    """Defender real-time protection off: the possible compensating antivirus is unresolved, not absent."""
    return _finish({'version': VERSION, 'condition': 'endpoint.realtime_protection_off',
                    'observed_condition': 'Microsoft Defender Antivirus real-time protection reports off on the returned records.',
                    'controls': [{'control_type': 'compensating', 'name': 'Approved third-party antivirus or Defender passive mode',
                                  'configuration_state': 'ControlOperationUnresolved'}],
                    'state': 'ControlOperationUnresolved', 'configuration_state': 'ControlOperationUnresolved',
                    'operation_state': 'ControlOperationUnresolved', 'lane': None,
                    'customer_narrative': ('Defender real-time protection reports off on these records. Whether an approved third-party antivirus '
                                           'is active, or Defender runs in passive mode alongside one, was not retained, so this is not a '
                                           'confirmed unprotected device.'),
                    'scope': {'description': 'Returned Windows protection records'},
                    'evidence_completeness': 'Antivirus provider and Defender operating mode were not retained.',
                    'next_steps': ['Confirm the active antivirus provider and Defender operating mode on the listed devices before treating '
                                   'them as unprotected.'],
                    'required_decision': None, 'closure_evidence': row.get('CompletionEvidence'), 'residual_risk': 'Not established.'})

"""Shared, historical client-name classification and outcome rules for sign-in evidence."""

import re
from collections import Counter
from copy import deepcopy
from datetime import datetime
from enum import Enum

LEGACY_CLIENT_MARKERS = ('pop', 'imap', 'smtp', 'activesync', 'other clients', 'exchange web services')
LEGACY_CLASSIFICATION_RULE = (
    'Case-insensitive substring match of clientAppUsed against: '
    + ', '.join(LEGACY_CLIENT_MARKERS)
    + '. This classifies the reported client; it does not establish successful authentication or a control bypass.'
)

# Microsoft's Conditional Access client-apps condition lists these legacy authentication
# clients. The historical predicate above does not match them; retained events using them
# are reported as a limitation and are never added to a legacy finding.
UNMATCHED_LEGACY_CLIENT_TYPES = (
    ('autodiscover', 'Autodiscover'),
    ('exchange online powershell', 'Exchange Online PowerShell'),
    ('mapi', 'MAPI over HTTP'),
    ('offline address book', 'Offline Address Book'),
    ('outlook anywhere', 'Outlook Anywhere (RPC over HTTP)'),
    ('rpc over http', 'Outlook Anywhere (RPC over HTTP)'),
    ('outlook service', 'Outlook Service'),
    ('reporting web services', 'Reporting Web Services'),
)
LEGACY_CLIENT_TYPES_SOURCE = 'https://learn.microsoft.com/en-us/entra/identity/conditional-access/concept-conditional-access-conditions'

# Documented AADSTS codes whose meaning is a block rather than a credential failure.
BLOCKING_ERROR_CODES = {
    53000: ('Conditional Access', 'DeviceNotCompliant: a compliant device is required'),
    53001: ('Conditional Access', 'DeviceNotDomainJoined: a domain-joined device is required'),
    53002: ('Conditional Access', 'ApplicationUsedIsNotAnApprovedApp: an approved client app is required'),
    53003: ('Conditional Access', 'BlockedByConditionalAccess: a policy does not allow token issuance'),
    50053: ('Entra sign-in protection', 'IdsLocked: locked account, malicious IP address or a built-in high-confidence-risk block'),
}
ERROR_CODES_SOURCE = 'https://learn.microsoft.com/en-us/entra/identity-platform/reference-error-codes'
SIGNIN_OUTCOMES = ('Succeeded', 'Blocked', 'Failed', 'Unknown')
NORMALIZED_SIGNIN_OUTCOMES = ('Success', 'BlockedByConditionalAccess', 'FailedOther', 'InterruptedOrChallenged', 'Unknown')
AUTHENTICATION_RULE_VERSION = '1.0.0'
CHALLENGE_ERROR_CODES = {50072, 50074, 50076, 50078, 50079, 50140}
OUTCOME_RULE = (
    'Sign-In Result is authoritative: strict errorCode 0 is Success unless fields conflict; '
    'documented CA codes or corroborating applied-policy failure establish BlockedByConditionalAccess; '
    'other failures are FailedOther, documented challenges are InterruptedOrChallenged, and '
    'missing, nonnumeric, boolean or conflicting results are Unknown. Success establishes a token request, '
    'not downstream access. Outcome retains historical coarse compatibility labels Succeeded/Blocked/Failed/Unknown; '
    'its Blocked label includes challenges and Entra protection and does not independently establish a CA block.'
)


LEGACY_SIGNIN_COUNT = re.compile(r'\b(\d[\d,]*)\s+legacy(?:\s+authentication|\s+auth)?\s+sign[- ]?ins?\b', re.IGNORECASE)


def _field(record, *names):
    for name in names:
        value = record.get(name) if isinstance(record, dict) else getattr(record, name, None)
        if value is not None:
            return getattr(value, 'value', value)
    return None


def _client_app(record):
    return str(_field(record, 'clientAppUsed', 'client_app_used') or '').lower()


def is_legacy_signin(record):
    """Use exactly the collector's historical substring predicate.

    Accept raw Graph dictionaries or SDK model attributes. Missing client data
    does not match; a non-match alone is not evidence of modern authentication.
    Authentication outcome and Conditional Access status are independent fields.
    """
    client_app = _client_app(record)
    return any(marker in client_app for marker in LEGACY_CLIENT_MARKERS)


def unmatched_legacy_client_type(record):
    """Name a Microsoft-listed legacy client the historical predicate does not match."""
    client_app = _client_app(record)
    if not client_app or is_legacy_signin(record):
        return None
    return next((name for marker, name in UNMATCHED_LEGACY_CLIENT_TYPES if marker in client_app), None)


def is_legacy_signin_finding(record):
    """An observed event count is distinct from a legacy-blocking policy finding."""
    if str(record.get('Service') or '') != 'Entra':
        return False
    if record.get('FindingKey') == 'entra.signins.legacy_auth':
        return True
    count = LEGACY_SIGNIN_COUNT.search(str(record.get('Observation') or ''))
    return bool(count and int(count.group(1).replace(',', '')) > 0)


def signin_error_code(record):
    """Return a numeric status.errorCode, or None when it was not returned or is unreadable."""
    code = _field(_field(record, 'status') or {}, 'errorCode', 'error_code')
    if code is None or isinstance(code, bool) or not re.fullmatch(r'\d+', str(code).strip()):
        return None
    return int(str(code).strip())


def classify_signin_outcome(record):
    """One authoritative result classifier, with the retained coarse compatibility label.

    Decisions and customer outcome breakdowns use normalized_outcome. The old
    four-way outcome is retained for saved evidence consumers; it is not an
    independent classifier or proof that a generic block was Conditional Access.
    """
    code = signin_error_code(record)
    access = str(_field(record, 'conditionalAccessStatus', 'conditional_access_status') or '').strip()
    reason = str(_field(_field(record, 'status') or {}, 'failureReason', 'failure_reason') or '').strip()
    basis = (f"status.errorCode={code if code is not None else 'not returned'}; "
             f"conditionalAccessStatus={access or 'not returned'}")
    failed_access = access.lower() == 'failure'
    policies = _field(record, 'appliedConditionalAccessPolicies', 'applied_conditional_access_policies')
    policy_failure = isinstance(policies, list) and any(
        str(_field(policy, 'result') or '').lower() == 'failure' for policy in policies)
    conflicts = []
    normalized = 'Unknown'
    detail = 'No readable status.errorCode was returned.'
    if code is None:
        pass
    elif code == 0:
        if failed_access or policy_failure:
            conflicts.append('errorCode 0 conflicts with returned Conditional Access failure.')
            detail = 'Conflicting fields: errorCode 0 with Conditional Access failure.'
        else:
            normalized = 'Success'
            detail = 'The token request succeeded (errorCode 0); downstream data access is not established.'
    elif code in CHALLENGE_ERROR_CODES:
        normalized = 'InterruptedOrChallenged'
        detail = f'Authentication was interrupted or challenged (errorCode {code}); final access is not established.'
    elif code in BLOCKING_ERROR_CODES:
        source, meaning = BLOCKING_ERROR_CODES[code]
        normalized = 'BlockedByConditionalAccess' if source == 'Conditional Access' else 'FailedOther'
        detail = f'Blocked by {source}: {meaning}.'
        if source == 'Conditional Access' and access.lower() in {'success', 'notapplied'}:
            conflicts.append('Conditional Access blocking code conflicts with the overall policy status.')
            normalized = 'Unknown'
    elif failed_access and policy_failure:
        normalized = 'BlockedByConditionalAccess'
        detail = f'Returned Conditional Access failure and applied policy failure support blocking (errorCode {code}).'
    else:
        normalized = 'FailedOther'
        detail = f'Failed with errorCode {code}' + (f': {reason}' if reason else '') + '; Conditional Access blocking is not established.'
    # Historical labels are compatibility fields only. Rich semantics distinguish
    # challenges and Entra sign-in protection from a confirmed CA block.
    compatibility = ('Unknown' if normalized == 'Unknown' else 'Succeeded' if normalized == 'Success'
                     else 'Blocked' if code in BLOCKING_ERROR_CODES or failed_access else 'Failed')
    return {'outcome': compatibility, 'normalized_outcome': normalized,
            'error_code': code, 'raw_error_code': _field(_field(record, 'status') or {}, 'errorCode', 'error_code'),
            'detail': detail, 'basis': basis, 'conflicts': conflicts, 'rule_version': AUTHENTICATION_RULE_VERSION}


def _retained_signin_value(value):
    """Keep SDK enum values before object serialization removes private fields."""
    if isinstance(value, Enum):
        return _retained_signin_value(value.value)
    if isinstance(value, dict):
        return {str(key): _retained_signin_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_retained_signin_value(item) for item in value]
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if hasattr(value, '__dict__'):
        return {key: _retained_signin_value(item) for key, item in vars(value).items() if not key.startswith('_')}
    return value


def normalize_signin_event(record, source=None):
    """Retain source properties and add separate event, CA and authentication facts."""
    raw = _retained_signin_value(record) if record is not None else {}
    raw = raw if isinstance(raw, dict) else {}
    for field in ('clientAppUsed', 'createdDateTime', 'authenticationRequirement', 'authenticationDetails',
                  'appliedConditionalAccessPolicies', 'conditionalAccessStatus'):
        snake = re.sub(r'(?<!^)(?=[A-Z])', '_', field).lower()
        if field not in raw and snake in raw:
            raw[field] = deepcopy(raw[snake])
    # Derive from an alias-normalized view; retain original nested source values.
    view = deepcopy(raw)
    for field, sequence in (('authenticationDetails', ('authenticationMethod', 'authenticationStepRequirement',
                              'authenticationStepResultDetail', 'authenticationStepDateTime')),
                            ('appliedConditionalAccessPolicies', ('displayName',))):
        if isinstance(view.get(field), list):
            for item in view[field]:
                if isinstance(item, dict):
                    for name in sequence:
                        snake = re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()
                        if name not in item and snake in item:
                            item[name] = deepcopy(item[snake])
    result = classify_signin_outcome(view)
    policies = raw.get('appliedConditionalAccessPolicies')
    policy_state = ('unavailable' if policies is None else 'available'
                    if isinstance(policies, list) and all(isinstance(p, dict) for p in policies) else 'malformed')
    requirement = str(raw.get('authenticationRequirement') or '').lower()
    details = view.get('authenticationDetails')
    valid_details = isinstance(details, list) and bool(details) and all(isinstance(s, dict) for s in details)
    steps = details if valid_details else []
    conflicts = list(result['conflicts'])
    # Duplicate representations of the same step must not favor its true flag.
    step_states = {}
    for step in steps:
        key = tuple(str(step.get(k) or '') for k in ('authenticationMethod', 'authenticationStepRequirement',
                    'authenticationStepResultDetail', 'authenticationStepDateTime'))
        if type(step.get('succeeded')) is bool:
            step_states.setdefault(key, set()).add(step['succeeded'])
    step_conflict = any(len(states) > 1 for states in step_states.values())
    if step_conflict:
        conflicts.append('Conflicting success flags for the same retained authentication step.')
    mfa_steps = [s for s in steps if 'multifactor' in str(s.get('authenticationStepRequirement', '')).lower()
                 or 'mfa' in str(s.get('authenticationStepResultDetail', '')).lower()]
    satisfied = [s for s in mfa_steps if s.get('succeeded') is True]
    external = any('external' in str(s.get('authenticationStepResultDetail', '')).lower() for s in satisfied)
    prior = any(any(word in str(s.get('authenticationStepResultDetail', '')).lower()
                    for word in ('claim', 'previously', 'token')) for s in satisfied)
    strong_primary = any(s.get('succeeded') is True and str(s.get('authenticationStepRequirement', '')).lower() == 'primaryauthentication'
                         and any(method in str(s.get('authenticationMethod', '')).lower()
                                 for method in ('fido2', 'passkey', 'windows hello')) for s in steps)
    satisfaction = ('conflict' if step_conflict else 'external_provider' if external else 'previously_satisfied' if prior
                    else 'observed_success' if satisfied else 'denied' if any(s.get('succeeded') is False for s in mfa_steps)
                    else 'strong_primary' if strong_primary else 'unknown')
    legacy = is_legacy_signin(raw)
    legacy_state = {'Success': 'SuccessfulLegacyAuthentication', 'BlockedByConditionalAccess': 'LegacyAttemptBlockedByConditionalAccess',
                    'FailedOther': 'LegacyAttemptFailedOther', 'InterruptedOrChallenged': 'LegacyAttemptInterrupted',
                    'Unknown': 'LegacyOutcomeUnknown'}[result['normalized_outcome']] if legacy else 'NotClassifiedLegacy'
    qualification = ('Returned event only; client classification does not prove the exact protocol or a control bypass. '
                     'Authentication requirement, steps, prior claims, provider satisfaction and policy results are separate facts. '
                     'Missing or delayed authentication detail remains unknown; event samples do not prove tenant-wide protection.')
    return dict(deepcopy(raw), SignInOutcome='success' if result['normalized_outcome'] == 'Success' else
                'unknown' if result['normalized_outcome'] == 'Unknown' else 'failure',
                NormalizedSignInOutcome=result['normalized_outcome'], RawResultStatus=deepcopy(raw.get('status')),
                RawErrorCode=result['raw_error_code'], NormalizedErrorCode=result['error_code'],
                SignInOutcomeBasis=result['basis'], SignInOutcomeDetail=result['detail'],
                AuthenticationRuleVersion=AUTHENTICATION_RULE_VERSION, LegacyAuthenticationState=legacy_state,
                MFARequirement='required' if requirement == 'multifactorauthentication' else
                'not_required' if requirement == 'singlefactorauthentication' else 'unknown',
                MFASatisfaction=satisfaction, AuthenticationDetailsAvailable=valid_details,
                RootAuthenticationMethods=[s.get('authenticationMethod') for s in steps
                    if str(s.get('authenticationStepRequirement', '')).lower() == 'primaryauthentication'],
                ConditionalAccessResult=str(raw.get('conditionalAccessStatus') or 'unknown'),
                AppliedPoliciesAvailable=policy_state == 'available', AppliedPoliciesState=policy_state,
                ReportOnlyResults=[p for p in (policies if policy_state == 'available' else [])
                                   if str(p.get('result', '')).lower().startswith('reportonly')],
                AuthenticationConflicts=conflicts, AuthenticationQualification=qualification,
                AuthenticationSource=deepcopy(source or {}))


def legacy_event_summary(datasets):
    """One bounded outcome summary; unavailable collections do not invent events or zero assurance."""
    from .source_evidence import envelope_complete
    counts = Counter({name: 0 for name in NORMALIZED_SIGNIN_OUTCOMES})
    windows = []
    complete = bool(datasets)
    for dataset in datasets or []:
        source = dataset.get('source') or {}
        windows.append({key: deepcopy(source.get(key)) for key in
                        ('source_api', 'source_file', 'scope', 'window_start', 'window_end', 'collection_window',
                         'availability_status', 'complete', 'truncated', 'pages_collected', 'reason')})
        state = {**source, 'availability_status': 'available' if source.get('availability_status') == 'empty' else source.get('availability_status')}
        complete = complete and source.get('complete') is True and envelope_complete(state)
        for raw in dataset.get('records') or []:
            if is_legacy_signin(raw):
                counts[classify_signin_outcome(raw)['normalized_outcome']] += 1
    def known_window(window):
        try:
            start = datetime.fromisoformat(str(window.get('window_start') or '').replace('Z', '+00:00'))
            end = datetime.fromisoformat(str(window.get('window_end') or '').replace('Z', '+00:00'))
            return isinstance(window.get('scope'), str) and bool(window['scope'].strip()) and start.tzinfo is not None and end.tzinfo is not None and start <= end
        except (TypeError, ValueError):
            return False
    known = bool(windows) and all(known_window(window) for window in windows)
    return {'counts': dict(counts), 'total': sum(counts.values()), 'complete': bool(complete),
            'known_population_and_period': known, 'windows': windows,
            'no_success_observed': bool(complete and known and counts['Success'] == 0 and counts['Unknown'] == 0),
            'rule_version': AUTHENTICATION_RULE_VERSION,
            'qualification': 'Counts describe returned client-classified events within recorded source boundaries; zero is not proof of tenant-wide blocking.'}

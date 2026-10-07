"""Shared, historical client-name classification and outcome rules for sign-in evidence."""

import re

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
OUTCOME_RULE = (
    'Outcome uses status.errorCode and conditionalAccessStatus: errorCode 0 is Succeeded unless Conditional Access '
    'reports failure; Conditional Access failure or AADSTS 53000-53003 is Blocked (Conditional Access); 50053 is '
    'Blocked (Entra sign-in protection); any other numeric code is Failed; a missing or unreadable code, or '
    'conflicting fields, is Unknown. Succeeded describes the token request only, not the data that was accessed.'
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
    if code is None or isinstance(code, bool) or not re.fullmatch(r'-?\d+', str(code).strip()):
        return None
    return int(str(code).strip())


def classify_signin_outcome(record):
    """Classify one sign-in event as Succeeded, Blocked, Failed or Unknown, with the basis used."""
    code = signin_error_code(record)
    access = str(_field(record, 'conditionalAccessStatus', 'conditional_access_status') or '').strip()
    reason = str(_field(_field(record, 'status') or {}, 'failureReason', 'failure_reason') or '').strip()
    basis = (f"status.errorCode={code if code is not None else 'not returned'}; "
             f"conditionalAccessStatus={access or 'not returned'}")
    failed_access = access.lower() == 'failure'
    if code is None:
        return {'outcome': 'Unknown', 'detail': 'No readable status.errorCode was returned.', 'basis': basis}
    if code == 0:
        if failed_access:
            return {'outcome': 'Unknown', 'detail': 'Conflicting fields: errorCode 0 with Conditional Access failure.', 'basis': basis}
        return {'outcome': 'Succeeded', 'detail': 'The token request succeeded (errorCode 0).', 'basis': basis}
    if code in BLOCKING_ERROR_CODES:
        source, meaning = BLOCKING_ERROR_CODES[code]
        return {'outcome': 'Blocked', 'detail': f'Blocked by {source}: {meaning}.', 'basis': basis}
    if failed_access:
        return {'outcome': 'Blocked', 'detail': f'Blocked by Conditional Access: the evaluation failed (errorCode {code}'
                + (f', {reason}' if reason else '') + ').', 'basis': basis}
    return {'outcome': 'Failed', 'detail': f'Failed with errorCode {code}' + (f': {reason}' if reason else '') + '.', 'basis': basis}

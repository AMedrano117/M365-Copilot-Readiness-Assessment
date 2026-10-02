"""Workbook-only investigation rows for an explicit MFA registration finding.

The population and conflicting-snapshot handling match authentication_methods.
Only an explicit false registration flag identifies an affected user; missing
flags never become an inferred registration failure.
"""

from datetime import date, datetime

from .authentication_methods import FIELDS, _get, summarize_registrations


SOURCE_API = 'https://graph.microsoft.com/v1.0/reports/authenticationMethods/userRegistrationDetails'


def _cell(value):
    if value is None:
        return 'Not returned'
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return '; '.join(str(getattr(item, 'value', item)) for item in value) if value else '(none returned)'
    return value


def build_mfa_registration_investigation(client):
    """Return selected users plus identity-free reconciliation and limitations.

    Repeated ID/UPN rows are one user, using exactly the aggregate report's
    comparison fields. Conflicting snapshots count as unknown and are excluded
    from the worklist. Names are never identity keys. Anonymous returned rows
    remain separate, with their original source position available to reviewers.
    """
    raw = _get(client, 'auth_methods_registration')
    records = list(raw) if isinstance(raw, (list, tuple)) else []
    states = _get(client, 'collection_status') or {}
    state = states.get('auth_methods', {}) if isinstance(states, dict) else {}
    state = state if isinstance(state, dict) else {}
    report = summarize_registrations(records, state)
    unique, conflicts = {}, set()
    for number, record in enumerate(records, 1):
        key = _get(record, 'id') or _get(record, 'userPrincipalName') or ('anonymous', number - 1)
        normalized = {field: _get(record, field) for field in FIELDS}
        if key in unique:
            previous = unique[key]
            previous['positions'].append(number)
            if normalized != previous['normalized']:
                conflicts.add(key)
        else:
            unique[key] = {'normalized': normalized, 'record': record, 'positions': [number]}

    total = report['total_users']
    registered = report['metrics'].get('mfa_registered', 0)
    unknown = total - report['metrics'].get('mfa_registered_known', 0)
    matched = total - registered - unknown
    source_state = report['source_state']
    source_api = state.get('source_api') or SOURCE_API
    rows = []
    for key, entry in unique.items():
        if key in conflicts or entry['normalized']['isMfaRegistered'] is not False:
            continue
        record = entry['record']
        rows.append({
            'User ID': _cell(_get(record, 'id')),
            'User Principal Name': _cell(_get(record, 'userPrincipalName')),
            'User Display Name': _cell(_get(record, 'userDisplayName')),
            'User Type': _cell(_get(record, 'userType')),
            'Is Admin': _cell(_get(record, 'isAdmin')),
            'MFA Registered': False,
            'MFA Capable': _cell(_get(record, 'isMfaCapable')),
            'Methods Registered': _cell(_get(record, 'methodsRegistered')),
            'Last Updated UTC': _cell(_get(record, 'lastUpdatedDateTime')),
            'Source Row': '; '.join(str(number) for number in entry['positions']),
            'Source State': source_state,
            'Source API': source_api,
            'Pages Collected': _cell(state.get('pages_collected')),
            'Truncated': _cell(state.get('truncated')),
            'Collection Started UTC': _cell(state.get('collection_started_at')),
            'Collection Completed UTC': _cell(state.get('collection_completed_at')),
            'Collection Window': 'Registration report snapshot; per-user report update time is shown separately',
        })

    reconciliation = (
        f'{matched} users explicitly not MFA registered + {registered} registered + '
        f'{unknown} unknown = {total} users in {len(records)} returned registration records. '
        f'{len(rows)} investigation rows reconcile to the explicit false flags.'
    )
    if len(records) != total:
        reconciliation += (
            f" Repeated ID/UPN records are consolidated using the aggregate report's fields; "
            f'{len(conflicts)} users with conflicting snapshots count as unknown and are excluded.'
        )
    summary = _get(client, 'auth_summary') or {}
    if isinstance(summary, dict):
        expected_total = summary.get('total_users')
        expected_registered = summary.get('mfa_registered')
        comparisons = [('total users', expected_total, total), ('registered users', expected_registered, registered)]
        for label, expected, actual in comparisons:
            if type(expected) is int and expected != actual:
                reconciliation += f' Saved summary mismatch: {label} {expected}; retained records reproduce {actual}.'
    if source_state != 'available':
        reconciliation += ' Source completeness is not established; counts describe retained records only.'

    unavailable = ''
    if not records:
        if raw is None:
            unavailable = 'The collection did not retain auth_methods_registration records; an aggregate cannot identify affected users.'
        elif source_state == 'available':
            if total == 0 and isinstance(summary, dict) and type(summary.get('total_users')) is int and summary['total_users'] > 0:
                unavailable = 'No registration records were retained although the saved summary reports users; affected identities cannot be reconstructed.'
            else:
                unavailable = 'The completed registration report returned zero user records.'
        else:
            status_code = state.get('status_code')
            status = f' (HTTP {status_code})' if type(status_code) is int else ''
            unavailable = 'No registration records were retained and successful source collection is not established' + status + '.'
    elif not rows and unknown:
        unavailable = f'No user has an unambiguous false MFA registration flag; {unknown} users have unknown or conflicting registration data.'

    details = [
        reconciliation,
        'Selection: isMfaRegistered is the boolean false. True, missing, non-boolean and conflicting registration flags are excluded. Names are never used to consolidate users.',
        'Scope: users returned by the registration report, including members and guests. Confirm account scope and exceptions before arranging registration; registration does not establish Conditional Access enforcement.',
        'Last Updated UTC is the report update timestamp, not the method registration date or last sign-in. This is a registration snapshot, not a sign-in collection window.',
        'Pagination and truncation are copied from collection_status.auth_methods when retained. Missing metadata is unknown, including for older saved collections; no live lookup is performed.',
        'Permissions, licensing and retention are not independently verified by this offline worklist. It cannot recover omitted, inaccessible or expired source records.',
    ]
    if unavailable:
        details.append(unavailable)
    return {
        'rows': rows, 'matched_count': matched, 'unknown_count': unknown,
        'total_count': total, 'registered_count': registered,
        'reconciliation_note': reconciliation, 'unavailability_reason': unavailable,
        'details': details,
    }

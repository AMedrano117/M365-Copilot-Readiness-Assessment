"""Finding-specific identity worklists from retained collection records only."""

import re

from .source_evidence import source_availability, source_is_complete


DEVICE_API = 'https://graph.microsoft.com/v1.0/deviceManagement/managedDevices'


def _get(record, field, default=None):
    snake = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', field)).lower()
    if isinstance(record, dict):
        value = record.get(field, record.get(snake, default))
    else:
        value = getattr(record, field, getattr(record, snake, default))
    return getattr(value, 'value', value)


def _cell(value):
    return value.isoformat() if hasattr(value, 'isoformat') else ('' if value is None else value)


def is_noncompliant_device_finding(record):
    if record.get('FindingKey') == 'entra.devices.noncompliant':
        return True
    observation = str(record.get('OriginalObservation') or record.get('Observation') or '')
    return bool(re.search(r'\b\d+ of \d+ (?:returned )?managed devices?.*?\bnon[ -]?compliant\b', observation, re.I))


def build_intune_device_investigation(client):
    """One retained managed-device record per explicit noncompliant state."""
    if client is None:
        return None
    raw = _get(client, 'managed_devices')
    devices = list(raw) if isinstance(raw, (list, tuple)) else []
    status = (_get(client, 'collection_status', {}) or {}).get('managed_devices', {}) or {}
    availability = source_availability(client, 'managed_devices')
    rows = []
    for number, device in enumerate(devices, 1):
        if str(_get(device, 'complianceState', '')).lower() != 'noncompliant':
            continue
        rows.append({
            'RecommendationId': '', 'Flagged By': '',
            'Device ID': _cell(_get(device, 'id')),
            'Device Name': _cell(_get(device, 'deviceName')),
            'Entra Device ID': _cell(_get(device, 'azureADDeviceId')),
            'User ID': _cell(_get(device, 'userId')),
            'User Principal Name': _cell(_get(device, 'userPrincipalName')),
            'User Display Name': _cell(_get(device, 'userDisplayName')),
            'Compliance State': _cell(_get(device, 'complianceState')),
            'OS': _cell(_get(device, 'operatingSystem')),
            'OS Version': _cell(_get(device, 'osVersion')),
            'Ownership': _cell(_get(device, 'managedDeviceOwnerType')),
            'Management Agent': _cell(_get(device, 'managementAgent')),
            'Enrolled UTC': _cell(_get(device, 'enrolledDateTime')),
            'Last Sync UTC': _cell(_get(device, 'lastSyncDateTime')),
            'Compliance Grace Period Expires UTC': _cell(_get(device, 'complianceGracePeriodExpirationDateTime')),
            'Source Row': number, 'Source State': availability,
            'Source API': status.get('source_api') or DEVICE_API,
            'Pages Collected': status.get('pages_collected', 'Unknown'),
            'Truncated': status.get('truncated', 'Unknown'),
            'Collection Started UTC': status.get('collection_started_at') or 'Not retained',
            'Collection Completed UTC': status.get('collection_completed_at') or 'Not retained',
            'Collection Window': 'Managed-device snapshot; last sync is reported separately',
        })
    compliant = sum(str(_get(item, 'complianceState', '')).lower() == 'compliant' for item in devices)
    other = len(devices) - compliant - len(rows)
    reconciliation = (f'{len(rows)} explicitly noncompliant + {compliant} compliant + {other} other or unknown '
                      f'= {len(devices)} retained managed-device records. {len(rows)} investigation rows are exported.')
    expected = (_get(client, 'device_summary', {}) or {}).get('non_compliant')
    if type(expected) is int and expected != len(rows):
        reconciliation += f' Saved summary mismatch: {expected} noncompliant; retained records reproduce {len(rows)}.'
    unavailable = ''
    if raw is None:
        unavailable = 'The collection did not retain managed_devices records; device identities cannot be reconstructed from the aggregate.'
    elif not devices and availability != 'available':
        unavailable = 'No managed-device records were retained and a complete source collection is not established.'
        if status.get('reason'):
            unavailable += ' ' + str(status['reason'])
    elif not rows:
        unavailable = 'No retained device record has complianceState=noncompliant.'
    if availability != 'available':
        reconciliation += ' Counts describe retained records only; source completeness is not established.'
    return {'rows': rows, 'restricted': True, 'matched_count': len(rows), 'total_count': len(devices),
            'summary': reconciliation, 'reconciliation_note': reconciliation, 'unavailability_reason': unavailable,
            'details': [reconciliation,
                'Selection: complianceState equals noncompliant. Grace-period, error, unknown and compliant states are not counted as noncompliant.',
                'Scope is the managed-device inventory returned by Intune, not all tenant endpoints or confirmed Copilot devices. Compliance state does not prove successful access or identify the failed compliance rule.',
                'Open the device in Intune to review individual compliance-policy failures and the assigned user; those per-policy results were not collected.',
                'Pagination, truncation and timestamps are copied only when retained. Missing metadata remains unknown. Permissions, licensing and retention were not independently revalidated during offline export.',
                *([unavailable] if unavailable else [])]}


def qualify_identity_recommendation(record, client):
    """Correct legacy derived wording from the exact saved identity records."""
    row = dict(record)
    observation = str(row.get('Observation') or '')
    if is_noncompliant_device_finding(row):
        report = build_intune_device_investigation(client)
        if report and report['total_count']:
            count, total = report['matched_count'], report['total_count']
            row.setdefault('OriginalObservation', observation)
            row.update(FindingKey='entra.devices.noncompliant', EvidenceKey='entra_device_detail',
                EvidenceSource='managed_devices', EvidenceComplete=source_is_complete(client, 'managed_devices'),
                EvidenceScope='Managed-device records retained from Intune; explicit noncompliant state',
                Observation=f'{count} of {total} returned managed devices ({count / total * 100:.1f}%) are explicitly noncompliant. '
                            'The inventory does not establish Copilot use or successful access. Other compliance states are excluded from this count.',
                Recommendation='Review the linked device records in Intune, confirm the affected compliance rules and intended pilot users, '
                               'then remediate or agree treatment. Validate Conditional Access targeting and exclusions before changing access.')
            if not count:
                row.update(Disposition='Reference', Status='Insight', Recommendation='')
        elif report:
            row.update(Observation=report['unavailability_reason'] or report['reconciliation_note'],
                EvidenceSource='managed_devices', EvidenceComplete=False, Disposition='Coverage', Status='Not Assessed')
    elif observation.lower().startswith('no risky users detected'):
        users = list(_get(client, 'risky_users', []) or [])
        complete = source_is_complete(client, 'risky_users')
        high_medium = sum(str(_get(user, 'riskLevel', '')).lower() in {'high', 'medium'} for user in users)
        low_at_risk = sum(str(_get(user, 'riskLevel', '')).lower() == 'low'
                          and str(_get(user, 'riskState', '')).lower() == 'atrisk' for user in users)
        unresolved = sum(str(_get(user, 'riskState', '')).lower() in {'atrisk', 'confirmedcompromised'} for user in users)
        row.setdefault('OriginalObservation', observation)
        row.update(Observation=f'The retained risky-user records contain {high_medium} high/medium-risk user(s) and '
                   f'{low_at_risk} low-risk user(s) with state atRisk, among {len(users)} returned records. '
                   'Risk state and level are distinct; this is not a statement that no account risk exists.'
                   + ('' if complete else ' The source is incomplete or its completeness is unknown.'),
                   FindingKey='entra.identity_risk.users', EvidenceKey='identity_risk_detail',
                   EvidenceSource='risky_users', EvidenceComplete=complete,
                   EvidenceScope='All retained riskyUsers records; counts separated by risk level and state',
                   Disposition='Reference', Status='Insight', Recommendation='')
        if unresolved:
            row.update(Disposition='Action', Status='Attention Required',
                ReportedRiskyUserCount=unresolved,
                Observation=f'{unresolved} unresolved risky user record(s) have state atRisk or confirmedCompromised. ' + row['Observation'],
                Recommendation='Review the linked unresolved risky-user records, including lower-risk atRisk accounts, '
                               'and confirm investigation or remediation in Entra ID Protection.')
        elif not complete:
            row.update(Disposition='Coverage', Status='Not Assessed',
                Recommendation='Collect or review the current risky-user report before deciding whether unresolved account risk remains.')
    return row


def select_identity_item(key, row, rec):
    """Return a worklist item only when this row matches the finding's population."""
    from .investigation_details import _details, _first, _item, _text, _tokens, _yes
    finding = _text(rec, 'FindingKey')
    observation = (_text(rec, 'OriginalObservation') or _text(rec, 'Observation')).lower()
    if key == 'entra_device_detail':
        if not is_noncompliant_device_finding(rec) or _text(row, 'Compliance State').lower() != 'noncompliant':
            return None
        return _item('Managed device', _first(row, 'Device Name', 'Device ID'), _text(row, 'Device ID'),
            'Intune explicitly reports this device as noncompliant', _text(row, 'Compliance State'),
            _details(row, 'Entra Device ID', 'User ID', 'User Principal Name', 'OS', 'OS Version', 'Ownership', 'Management Agent'),
            _text(row, 'Last Sync UTC'), 'Intune admin center > Devices > All devices > Device compliance',
            'Review the failed compliance rules, assigned user and intended pilot scope. Confirm Conditional Access results before concluding that access succeeded.')
    if key == 'guest_access_detail':
        licensed_finding = finding == 'entra.guests.licensed' or bool(re.search(r'guest users? have (?:an? )?(?:m365|microsoft 365) licen[cs]e|licensed guest', observation))
        if not licensed_finding or _text(row, 'Object Type') != 'Guest User' or not _yes(row.get('Licensed')):
            return None
        return _item('Guest user', _first(row, 'User Principal Name', 'Display Name'), _text(row, 'User Object ID'),
            'Guest account has an assigned license; entitlement and business need require review',
            _details(row, 'Account Enabled', 'External User State'),
            _details(row, 'Assigned License SKU IDs', 'Disabled Service Plan IDs', 'User Principal Name'),
            _first(row, 'External State Changed UTC', 'Created Date'), 'Entra admin center > Users > All users > Licenses',
            'Confirm the guest sponsor, assigned SKU and business need. An assigned license alone does not establish Copilot entitlement or inappropriate access.')
    if key == 'access_review_detail':
        if not (finding == 'entra.access_reviews.nonrecurring' or 'none are recurring' in observation or 'one-time' in observation):
            return None
        if _text(row, 'Is Recurring').lower() != 'no' or not _first(row, 'Review ID', 'Review Name'):
            return None
        return _item('Access review definition', _text(row, 'Review Name'), _text(row, 'Review ID'),
            'Returned review definition has no recurring pattern', _text(row, 'Status'),
            _details(row, 'Review Type', 'Scope Query', 'Recurrence Type', 'Recurrence Interval', 'Recurrence Range'),
            _first(row, 'Modified Date', 'Created Date'), 'Entra admin center > Identity governance > Access reviews',
            'Confirm the review scope, reviewer ownership and desired cadence before creating or changing the recurring schedule.')
    if key == 'conditional_access_detail':
        if _text(row, 'Object Type') == 'Security Defaults':
            return None
        state = _text(row, 'State').lower().replace('_', '')
        report_only = state == 'enabledforreportingbutnotenforced'
        reason = ''
        if finding == 'baseline.identity.scope':
            if state == 'enabled' and any(_tokens(row.get(field)) for field in ('Exclude Users','Exclude Groups','Exclude Roles','Exclude Applications')):
                reason = 'Assignments or exclusions require effective coverage review'
            elif report_only and _yes(row.get('Blocks Legacy Auth')):
                reason = 'Legacy-client block policy is report-only and requires coverage review'
        elif finding == 'baseline.identity.sign_in':
            from .tenant_baseline import OFFICE_365_APP_IDS
            apps = _tokens(row.get('Include Applications'))
            covers_m365 = _yes(row.get('Targets All Apps')) or _yes(row.get('Targets M365')) or bool(apps & {str(value).lower() for value in OFFICE_365_APP_IDS})
            mfa_gap = 'no enforced conditional access policy requires mfa' in observation or 'mfa' in observation and 'specific users or groups' in observation
            legacy_gap = 'no enforced policy blocks legacy authentication' in observation
            includes = _tokens(row.get('Include Users')) | _tokens(row.get('Include Groups')) | _tokens(row.get('Include Roles'))
            if mfa_gap and _yes(row.get('Requires MFA')) and covers_m365:
                if report_only or state == 'disabled':
                    reason = 'This MFA policy is not enforced'
                elif state == 'enabled' and includes and 'all' not in _tokens(row.get('Include Users')):
                    reason = 'MFA policy targets a limited user population; confirm the required scope'
            if legacy_gap and _yes(row.get('Blocks Legacy Auth')) and 'all' in _tokens(row.get('Include Users')) and (report_only or state == 'disabled'):
                reason = 'Legacy-client block policy is not enforced'
        elif 'report-only' in observation and report_only:
            reason = 'Policy is in report-only mode'
        elif 'disabled' in observation and state == 'disabled':
            reason = 'Policy is disabled'
        if not reason:
            return None
        return _item('Conditional Access policy', _text(row, 'Policy Name'), _text(row, 'Policy ID'), reason,
            _text(row, 'State'), _details(row, 'Grant Controls', 'Grant Operator', 'Authentication Strength ID', 'Authentication Strength Name',
                'Include Applications', 'Exclude Applications', 'Include Users', 'Include Groups', 'Include Roles', 'Exclude Users', 'Exclude Groups', 'Exclude Roles', 'Client App Types'),
            _text(row, 'Modified Date'), 'Entra admin center > Protection > Conditional Access',
            'Review targeting and exclusions, validate impact, then confirm the intended enforcement state.')
    return None

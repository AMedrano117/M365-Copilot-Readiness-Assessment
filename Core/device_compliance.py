"""Explain retained Intune noncompliance without equating inactivity with risk.

No transport calls, name-based joins, inferred tenant validity period or changes
to raw compliance states. Setting-summary ParentId is not a device identity.
"""

import re

from .evidence_contract import parse_date
from .operational_evidence import reconcile_devices, rows


def value(record, *names):
    keys = {re.sub('[^a-z0-9]', '', str(key).lower()): item for key, item in record.items()}
    return next((keys[re.sub('[^a-z0-9]', '', name.lower())] for name in names
                 if keys.get(re.sub('[^a-z0-9]', '', name.lower())) not in (None, '')), None)


def setting_label(record, summaries=None):
    summary = (summaries or {}).get(str(value(record, 'ParentId') or '').casefold(), {})
    label = value(record, 'settingName', 'setting') or value(summary, 'settingName', 'setting') or 'Setting name not retained'
    key = re.sub('[^a-z0-9]', '', str(label).lower())
    # Graph can retain the built-in setting key rather than its portal label.
    builtin = str(value(record, 'setting') or value(record, 'ParentId') or '')
    if label == 'Setting name not retained' and builtin.lower().startswith('defaultdevicecompliancepolicy.'):
        key = re.sub('[^a-z0-9]', '', builtin.lower())
    if key in {'isactive', 'defaultdevicecompliancepolicyrequireremaincontact'}:
        return 'Is active', 'activity'
    if key in {'hasacompliancepolicyassigned', 'defaultdevicecompliancepolicyrequirecompliancepolicyassigned'}:
        return 'Has a compliance policy assigned', 'assignment'
    if key in {'enrolleduserexists', 'defaultdevicecompliancepolicyrequireuserexistence'}:
        return 'Enrolled user exists', 'identity'
    return str(label), 'unknown' if label == 'Setting name not retained' else 'security'


def _unambiguous_objects(records):
    groups = {}
    for record in records:
        key = str(value(record, 'id') or '').casefold()
        if key:
            groups.setdefault(key, []).append(record)
    return {key: group[0] for key, group in groups.items() if all(record == group[0] for record in group)}


def build_device_compliance(bundle, *, profile=None, evaluation_date=None):
    """Return per-device reasons and per-setting triage, retaining row references."""
    day = parse_date(evaluation_date or bundle.get('evaluation_date') or
                     (bundle.get('collection_context') or {}).get('collected_at'))
    reconciliation = reconcile_devices(bundle, profile, evaluation_date=day) if day else {'records': [], 'reporting_days': 7, 'active_days': 30}
    activity = {row['Device identity']: row for row in reconciliation['records']}
    devices = rows(bundle, 'managed_devices')
    summaries = _unambiguous_objects(rows(bundle, 'compliance_setting_summaries'))
    summary_ids = {str(value(row, 'id') or '').casefold() for row in rows(bundle, 'compliance_setting_summaries')}
    policies = _unambiguous_objects(rows(bundle, 'compliance_policies'))
    index = {}
    output = []
    for position, device in enumerate(devices):
        local = str(value(device, 'id') or '')
        aad = str(value(device, 'azureADDeviceId') or '').casefold()
        identity = aad if aad and aad != '00000000-0000-0000-0000-000000000000' else f'unmatched:managed_devices:{local or position}'
        signals = activity.get(identity, {})
        item = {'device_id': local, 'device': device, 'activity': signals.get('Activity', 'unknown'),
                'last_observed': signals.get('Last observed', ''), 'conflict': signals.get('Conflict', False),
                'last_sync': value(device, 'lastSyncDateTime'), 'settings': [], 'reason': 'Reason not retained'}
        output.append(item)
        for key in {local.casefold(), aad} - {'', '00000000-0000-0000-0000-000000000000'}:
            index.setdefault(key, []).append(item)
    settings = []
    for dataset_index, dataset in enumerate((bundle.get('assessment_sources') or {}).get('compliance_setting_states', [])):
        source = dataset.get('source') or {}
        for record_index, record in enumerate(dataset.get('records') or []):
            label, kind = setting_label(record, summaries)
            explicit = [str(value(record, key) or '').casefold() for key in ('managedDeviceId', 'deviceId')]
            identifiers = [key for key in explicit if key]
            parent = str(value(record, 'ParentId') or '').casefold()
            # Compatibility for saved per-device reads; summary-derived parents
            # must never join devices, even when IDs happen to collide.
            if not identifiers and parent and parent not in summary_ids and 'devicecompliancepolicysettingstatesummaries' not in str(source.get('source_api', '')).lower():
                identifiers = [parent]
            matches = [index.get(key, []) for key in identifiers]
            device = matches[0][0] if matches and all(len(match) == 1 and match[0] is matches[0][0] for match in matches) else None
            policy = value(record, 'policyName', 'compliancePolicyName')
            if not policy:
                policy = value(policies.get(str(value(record, 'policyId', 'compliancePolicyId') or '').casefold(), {}), 'displayName')
            if not policy and kind in {'activity', 'assignment', 'identity'}:
                policy = 'Default Device Compliance Policy'
            observed = value(record, 'lastReportedDateTime') or (device or {}).get('last_sync')
            date = parse_date(observed)
            current = bool(day and date and 0 <= (day-date).days <= reconciliation['reporting_days'])
            failed = str(value(record, 'state') or '').lower() == 'noncompliant'
            triage = ('reporting_review' if kind in {'activity', 'assignment', 'identity'} else
                      'current_security_failure' if kind == 'security' and current and device and device['activity'] == 'active' and not device['conflict'] else
                      'historical_or_unknown_review')
            reason = f'{label}: {value(record, "state") or "Unknown"}; policy: {policy or "not retained"}'
            qualification = ('Activity/reporting failure; it does not establish an encryption or other security-setting failure.' if kind == 'activity' else
                             'Assignment or enrolled-user check; review enrollment and policy targeting.' if kind in {'assignment', 'identity'} else
                             'Explicit setting failure with a recent device report; confirm remediation.' if triage == 'current_security_failure' else
                             'Retained setting state requires confirmation; current security posture is not established.')
            item = {'record': record, 'dataset_index': dataset_index, 'record_index': record_index,
                    'device_id': (device or {}).get('device_id'), 'setting': label, 'policy': policy or 'not retained',
                    'failed': failed, 'triage': triage, 'reason': reason, 'qualification': qualification,
                    'last_reported': observed or '', 'date_basis': 'lastReportedDateTime' if value(record, 'lastReportedDateTime') else 'managed device last sync' if device else 'not retained',
                    'activity': (device or {}).get('activity', 'unknown')}
            settings.append(item)
            if device is not None:
                device['settings'].append(item)
    for device in output:
        failed = [item for item in device['settings'] if item['failed']]
        if failed:
            device['reason'] = '; '.join(dict.fromkeys(item['reason'] for item in failed))
        device['qualification'] = ('Activity is based on retained Intune, Entra and Defender timestamps; stale reporting does not establish retirement or safety. '
                                   'Missing setting details do not establish a cause. The raw Intune compliance state is unchanged.')
    return {'devices': output, 'settings': settings, 'evaluation_date': day.isoformat() if day else '',
            'reporting_days': reconciliation['reporting_days'], 'activity_days': reconciliation['active_days'],
            'qualification': 'Assessment reporting/activity windows are not the tenant Intune compliance validity period; that setting is not inferred.'}


def client_compliance(client):
    """Adapt already retained client values; never collect or use the wall clock."""
    from .assessment_catalog import collect_assessment_sources
    from .identity_investigation import _get, _cell
    sources = collect_assessment_sources([client])
    # SDK fixtures and older saves may expose snake_case attributes.
    fields = ('id', 'deviceName', 'azureADDeviceId', 'complianceState', 'lastSyncDateTime')
    sources['managed_devices'] = [{'records': [{key: _cell(_get(row, key)) for key in fields}
                                             for row in (_get(client, 'managed_devices', []) or [])]}]
    dates = [(_get(client, 'collection_status', {}) or {}).get('managed_devices', {}).get('collection_completed_at'), _get(client, 'collected_at')]
    return build_device_compliance({'assessment_sources': sources}, evaluation_date=next((date for date in dates if date), None))


def device_fields(device):
    return {'Compliance reason': device['reason'], 'Device activity': device['activity'],
            'Latest activity UTC': device['last_observed'], 'Compliance qualification': device['qualification']}


def prepare_device_sheets(bundle, result):
    """Copy frozen decision-model detail into the existing investigation sheet."""
    model = result.get('device_compliance')
    sheet = (bundle.get('sheets') or {}).get('entra_device_detail')
    if not model or not sheet:
        return
    by_id = {}
    for device in model['devices']:
        by_id.setdefault(device['device_id'].casefold(), []).append(device)
    for row in sheet.get('rows', []):
        matches = by_id.get(str(row.get('Device ID') or '').casefold(), [])
        if len(matches) == 1:
            row.update(device_fields(matches[0]))


def qualify_device_aggregate(record):
    from .identity_investigation import is_noncompliant_device_finding
    row = dict(record)
    if not is_noncompliant_device_finding(row):
        return row
    row.setdefault('OriginalFeature', row.get('Feature', ''))
    row.update(Feature='Review Intune device noncompliance reasons', Priority='Medium')
    if row.get('Disposition') not in {'Coverage', 'Reference'}:
        row.update(Disposition='Opportunity', Status='Attention Required')
    if row.get('Disposition') != 'Reference':
        row['Recommendation'] = ('Review the retained failing settings and device reporting dates. An Is active failure is an activity/reporting condition; '
                                 'it does not establish an encryption failure. Confirm current posture, ownership and Conditional Access targeting before changing access.')
    return row

"""Explain retained Intune noncompliance without equating inactivity with risk.

No transport calls, name-based joins, inferred tenant validity period or changes
to raw compliance states. Setting-summary ParentId is not a device identity.
"""

import re

from .evidence_contract import parse_date
from .operational_evidence import rows
from .device_evidence import (value, build_device_identities, interpret_records, timestamp)
from .device_compliance_text import requirement_result, setting_key, status_key, group_settings, group_state


def setting_label(record, summaries=None):
    summary = (summaries or {}).get(str(value(record, 'ParentId') or '').casefold(), {})
    label = value(record, 'settingName', 'setting') or value(summary, 'settingName', 'setting') or 'Setting name not retained'
    if not isinstance(label, str) or not label.strip() or label.strip().casefold() in {'unknown','not available','n/a'}:
        label = 'Setting name not retained'
    label = label.strip()
    key = re.sub('[^a-z0-9]', '', str(label).lower())
    # Graph can retain the built-in setting key rather than its portal label.
    builtin = str(value(record, 'setting') or value(record, 'ParentId') or '')
    if label == 'Setting name not retained' and builtin.lower().startswith('defaultdevicecompliancepolicy.'):
        key = re.sub('[^a-z0-9]', '', builtin.lower())
    if key in {'isactive', 'requireremaincontact', 'defaultdevicecompliancepolicyrequireremaincontact'}:
        return 'Is active', 'activity'
    if key in {'hasacompliancepolicyassigned', 'defaultdevicecompliancepolicyrequirecompliancepolicyassigned',
               'defaultdevicecompliancepolicyrequiredevicecompliancepolicyassigned', 'requiredevicecompliancepolicyassigned', 'requirecompliancepolicyassigned'}:
        return 'Has a compliance policy assigned', 'assignment'
    if key in {'enrolleduserexists', 'requireuserexistence', 'defaultdevicecompliancepolicyrequireuserexistence'}:
        return 'Enrolled user exists', 'identity'
    if any(term in key for term in ('lastcheckin', 'checkin', 'lastsync', 'synchronization', 'enrollment', 'enrolleduser')):
        return str(label), 'activity'
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
    identities = build_device_identities(bundle, profile=profile, evaluation_date=day)
    reconciliation = identities
    devices = rows(bundle, 'managed_devices')
    summaries = _unambiguous_objects(rows(bundle, 'compliance_setting_summaries'))
    policies = _unambiguous_objects(rows(bundle, 'compliance_policies'))
    by_identity = {}
    output = []
    inventory = interpret_records(identities, 'managed_devices', (bundle.get('assessment_sources') or {}).get('managed_devices', []))
    for position, device in enumerate(devices):
        local = str(value(device, 'id') or '')
        signals = inventory[position]['identity'] or {}
        relevance = signals.get('relevance', 'ActivityUnknown')
        item = {'device_id': local, 'device': device, 'activity': {'CurrentActiveDevice':'active', 'StaleDeviceRecord':'stale'}.get(relevance, 'unknown'),
                'last_observed': signals.get('last_observed', ''), 'conflict': signals.get('conflict', inventory[position]['unresolved']),
                'identity': signals, 'relevance': relevance, 'evidence': inventory[position],
                'last_sync': value(device, 'lastSyncDateTime'), 'settings': [], 'reason': 'Reason not retained'}
        output.append(item)
        if signals:
            by_identity.setdefault(signals['identity_key'], []).append(item)
    interpreted = interpret_records(identities, 'compliance_setting_states', (bundle.get('assessment_sources') or {}).get('compliance_setting_states', []))
    interpreted = {(item['dataset_index'], item['record_index']): item for item in interpreted}
    settings = []
    for dataset_index, dataset in enumerate((bundle.get('assessment_sources') or {}).get('compliance_setting_states', [])):
        for record_index, record in enumerate(dataset.get('records') or []):
            label, kind = setting_label(record, summaries)
            evidence = interpreted[(dataset_index, record_index)]
            matches = by_identity.get((evidence.get('identity') or {}).get('identity_key'), [])
            device = matches[0] if len(matches) == 1 and not evidence['unresolved'] else None
            policy = value(record, 'policyName', 'compliancePolicyName')
            if not policy:
                policy = value(policies.get(str(value(record, 'policyId', 'compliancePolicyId') or '').casefold(), {}), 'displayName')
            if not policy and setting_key(label) in {'requireremaincontact', 'requireuserexistence', 'requiredevicecompliancepolicyassigned'}:
                policy = 'Default Device Compliance Policy'
            observed = value(record, 'lastReportedDateTime') or (device or {}).get('last_sync')
            observed_date = timestamp(observed, day)
            date = parse_date(observed_date['date'])
            current = bool(date and observed_date['age_days'] <= reconciliation['reporting_days'])
            state = status_key(value(record, 'state'))
            failed = state == 'noncompliant'
            diagnostic = state in {'error', 'conflict', 'unknown', 'notevaluated'}
            assignment_missing = kind == 'assignment' and failed
            triage = ('reporting_review' if diagnostic or kind in {'activity', 'assignment', 'identity'} else
                      'current_security_failure' if failed and kind == 'security' and current and device and device['activity'] == 'active' and not device['conflict'] else
                      'historical_or_unknown_review')
            result_text = requirement_result(label, state)
            description = f'{result_text}: {value(record, "state") or "Unknown"}; policy: {policy or "not retained"}'
            qualification = ('Unresolved diagnostic result; it does not establish an unmet requirement or a noncompliance cause.' if diagnostic else
                             'Activity/reporting failure; it does not establish an encryption or other security-setting failure.' if kind == 'activity' else
                             'Assignment check reports no applicable compliance policy. Confirm current user/device targeting and check-in; this does not establish a security-setting failure.' if assignment_missing else
                             'Assignment or enrolled-user check; review enrollment and policy targeting.' if kind in {'assignment', 'identity'} else
                             'Explicit setting failure with a recent device report; confirm remediation.' if triage == 'current_security_failure' else
                             'Retained setting state requires confirmation; current security posture is not established.')
            item = {'record': record, 'dataset_index': dataset_index, 'record_index': record_index,
                    'device_id': (device or {}).get('device_id'), 'setting': label, 'policy': policy or 'not retained',
                    'failed': failed, 'kind': kind, 'assignment_missing': assignment_missing,
                    'triage': triage, 'reason': description if failed else '',
                    'result': result_text, 'diagnostic': description if diagnostic else '', 'qualification': qualification,
                    'last_reported': observed or '', 'date_basis': 'lastReportedDateTime' if value(record, 'lastReportedDateTime') else 'managed device last sync' if device else 'not retained',
                    'activity': (device or {}).get('activity', 'unknown'), 'evidence': evidence,
                    'classification': ('ReportingErrorOrConflict' if diagnostic else
                                       'ActivityOrEnrollmentFailure' if failed and kind in {'activity','identity','assignment'} else
                                       'ExplicitSecuritySettingFailure' if failed and kind == 'security' and current and evidence['evidence_id'] else
                                       'UnconfirmedSettingState' if failed else 'RetainedSettingState')}
            settings.append(item)
            if device is not None:
                device['settings'].append(item)
    for device in output:
        groups = group_settings(device['settings'])
        for group in groups:
            if group_state(group) == 'conflictingResults':
                for item in group:
                    if item['failed']:
                        item.update(triage='historical_or_unknown_review', classification='UnconfirmedSettingState',
                            qualification='Different results were retained for the same device/setting; confirm the current result before treating it as an unmet requirement.')
        failed = [group for group in groups if group_state(group) == 'noncompliant']
        if failed:
            device['reason'] = '; '.join(dict.fromkeys(item['reason'] for group in failed for item in group))
        unresolved = [group for group in groups if status_key(group_state(group)) in {'error','conflict','unknown','notevaluated','conflictingresults'}]
        device['diagnostics'] = '; '.join(dict.fromkeys(
            requirement_result(group[0]['setting'], group_state(group)) + ': ' + group_state(group)
            for group in unresolved))
        checks = [item for item in device['settings'] if item['kind'] == 'assignment']
        assignment_groups = group_settings(checks)
        missing = any(group_state(group) == 'noncompliant' for group in assignment_groups)
        passed = any(group_state(group) in {'compliant', 'remediated'} for group in assignment_groups)
        conflicting = any(group_state(group) == 'conflictingResults' for group in assignment_groups)
        device['policy_assignment'] = ('Conflicting assignment evidence' if conflicting or (missing and passed) else
                                       'Reported missing' if missing else 'Assignment check passed' if passed else 'Unknown')
        device['qualification'] = ('Activity is based on retained Intune, Entra and Defender timestamps; stale reporting does not establish retirement or safety. '
                                   'Missing setting details do not establish a cause. The raw Intune compliance state is unchanged.')
    counts = {state: sum(device['policy_assignment'] == state for device in output)
              for state in ('Reported missing', 'Assignment check passed', 'Conflicting assignment evidence', 'Unknown')}
    from .intune_compliance_settings import tenant_compliance_settings
    model = {'devices': output, 'settings': settings, 'evaluation_date': day.isoformat() if day else '',
            'identity_model': identities,
            'inventory': inventory,
            'tenant_settings': tenant_compliance_settings(bundle),
            'policy_assignment': {'counts': counts, 'retained_managed_devices': len(output),
                'unresolved_missing_records': sum(item['assignment_missing'] and not item['device_id'] for item in settings),
                'qualification': 'Counts cover retained managed-device records only. Missing or partial checks do not establish policy coverage. '
                                 'A passing assignment check does not establish policy names or effective enforcement. Reporting dates and conflicts require review.'},
            'reporting_days': reconciliation['reporting_days'], 'activity_days': reconciliation['active_days'],
            'qualification': 'Assessment reporting/activity windows are not the tenant Intune compliance validity period; that setting is not inferred.'}
    from .device_summary import compliance_activity, compliance_overview
    for device in output:
        device['compliance_activity'] = compliance_activity(device, model)
        if device['compliance_activity'] in {'inactive', 'overdue', 'retired'}:
            for item in device['settings']:
                if item['classification'] == 'ExplicitSecuritySettingFailure':
                    item.update(triage='historical_or_unknown_review', classification='UnconfirmedSettingState',
                        qualification='Intune compliance reporting is inactive, overdue or retired; confirm the setting before treating it as a current active-device failure.')
    model['summary'] = compliance_overview(model)
    return model


def client_compliance(client):
    """Adapt already retained client values; never collect or use the wall clock."""
    from .assessment_catalog import collect_assessment_sources
    from .identity_investigation import _get, _cell
    sources = collect_assessment_sources([client])
    # SDK fixtures and older saves may expose snake_case attributes.
    fields = ('id', 'deviceName', 'azureADDeviceId', 'complianceState', 'lastSyncDateTime',
              'userId', 'userPrincipalName', 'serialNumber', 'managementState', 'operatingSystem',
              'deviceEnrollmentType', 'managedDeviceOwnerType', 'deviceActionResults')
    sources['managed_devices'] = [{'records': [{key: _cell(_get(row, key)) for key in fields}
                                             for row in (_get(client, 'managed_devices', []) or [])]}]
    dates = [(_get(client, 'collection_status', {}) or {}).get('managed_devices', {}).get('collection_completed_at'), _get(client, 'collected_at')]
    return build_device_compliance({'assessment_sources': sources}, evaluation_date=next((date for date in dates if date), None))


def device_fields(device):
    return {'Compliance reason': device['reason'], 'Compliance diagnostics': device.get('diagnostics', ''), 'Device activity': device['activity'],
            'Compliance policy assignment': device.get('policy_assignment', 'Unknown'),
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

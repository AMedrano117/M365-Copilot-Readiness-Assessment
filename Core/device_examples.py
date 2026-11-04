"""Presentation of selected device evidence; no inference of missing causes."""
import re
from datetime import datetime, timezone

from .device_evidence import value
from .operational_evidence import reported_boolean
from .device_compliance_text import requirement_name, requirement_result, group_settings, group_state


def reported_time(raw, observed):
    if raw in (None, ''):
        return 'Not returned'
    if observed.get('state') != 'valid':
        return 'Invalid date; see workbook'
    try:
        parsed = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        if len(str(raw).strip()) == 10:
            return parsed.date().isoformat()+' (date only)'
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        return parsed.strftime('%Y-%m-%d %H:%M:%S')+' (timezone not retained)'
    except (TypeError, ValueError):
        return 'Invalid date; see workbook'


def example_timing(record, device, interpretations, settings, days):
    """Keep device activity, observation age and source report time independent."""
    from .endpoint_observations import reporting_subset
    entries = [(item['record'], item['evidence']['timestamp'], item['evidence'].get('date_basis', '')) for item in settings]
    if not entries:
        entries = [(record, item.get('timestamp') or {}, item.get('date_basis', '')) for item in interpretations]
    if not entries:
        entries = [(record, {'state': 'missing'}, '')]
    ages = list(dict.fromkeys({'current':'Current', 'stale':'Stale', 'undated':'Undated', 'invalid':'Invalid date'}[
        reporting_subset(observed, days)] for _, observed, _ in entries))
    times = list(dict.fromkeys(reported_time(value(raw, 'lastReportedDateTime', 'dataRefreshTimestamp'), observed)
                               for raw, observed, _ in entries))
    relevance = {item.get('record_relevance') for item in interpretations}
    activity = {'CurrentActiveDevice':'Current', 'StaleDeviceRecord':'Stale', 'RetiredOrDecommissioned':'Retired',
                'DuplicateOrSupersededRecord':'Duplicate/superseded'}.get(next(iter(relevance)), 'Unknown') if len(relevance) == 1 else 'Unknown'
    if any(item.get('identity_unresolved') for item in interpretations):
        activity = 'Unknown'
    elif device.get('compliance_activity') in {'inactive', 'overdue', 'retired'}:
        activity = {'inactive':'Inactive (Intune contact check)', 'overdue':'Stale (Intune check-in)', 'retired':'Retired'}[device['compliance_activity']]
    return {'activity': activity, 'recency': ages[0] if len(ages) == 1 else 'Mixed: '+' / '.join(ages),
            'reported': '\n'.join(times[:2])+ ('\nAdditional times in workbook' if len(times)>2 else ''),
            'date_basis': 'Recency uses device check-in; setting report time was not returned.'
                          if any('managed device' in basis for _, _, basis in entries) else ''}


def setting_name(label):
    return requirement_name(label)


def status_name(state):
    text = str(state or 'Not retained')
    return {'noncompliant': 'Not compliant', 'compliant': 'Compliant',
            'notevaluated': 'Not evaluated', 'error': 'Error', 'conflict': 'Conflict',
            'unknown': 'Unknown', 'remediated': 'Remediated', 'conflictingresults': 'Conflicting results'}.get(
                re.sub('[^a-z]', '', text.casefold()), text)


def error_detail(record):
    """Only report diagnostics actually retained by the source."""
    code = value(record, 'errorCode')
    message = value(record, 'errorDescription', 'failureReason', 'errorMessage')
    parts = (['Code: ' + str(code)] if code not in (None, '', 0, '0') else [])
    if message:
        parts.append(str(message))
    return '; '.join(parts)


def device_example(record, device, selected_settings, kind):
    """Use exact selected setting occurrences, or the resolved inventory device."""
    if kind in {'DefenderRealTimeProtectionObservation', 'DefenderSignatureObservation'}:
        if kind == 'DefenderRealTimeProtectionObservation':
            observed = reported_boolean(value(record, 'realTimeProtectionEnabled'))
            status = 'Off' if observed is False else 'On' if observed is True else 'Not retained'
            setting = 'Microsoft Defender real-time protection'
        else:
            overdue = reported_boolean(value(record, 'signatureUpdateOverdue'))
            current = reported_boolean(value(record, 'avIsSignatureUpToDate'))
            status = 'Update overdue' if overdue is True else 'Not current' if current is False else 'Not retained'
            setting = 'Antivirus signatures'
        mode = value(record, 'avMode', 'defenderAvMode', 'antivirusMode')
        provider = value(record, 'thirdPartyAntivirus', 'antivirusProvider')
        context = ('Mode: ' + str(mode) if mode else 'Defender mode not retained') + '; '
        context += 'Reported provider: ' + str(provider) if provider else 'Other antivirus provider not verified'
        return {'setting': setting, 'status': status, 'detail': context}

    items = selected_settings
    if kind == 'InventoryComplianceState':
        items = [item for group in group_settings(device.get('settings', [])) if group_state(group) == 'noncompliant'
                 for item in group]
    if items:
        # A device can have multiple settings. Keep the example bounded and make
        # any omitted details explicit; the complete rows stay in the workbook.
        policies = list(dict.fromkeys(item['policy'] if item['policy'] != 'not retained'
                                     else 'Not retained' for item in items))
        groups = group_settings(items)
        labels = list(dict.fromkeys(requirement_result(group[0]['setting'], group_state(group)) for group in groups))
        if kind == 'UnconfirmedSettingState':
            labels = ['Reported: ' + label + '; current result unconfirmed' for label in labels]
        states = list(dict.fromkeys(status_name(group_state(group)) for group in groups))
        diagnostics = list(dict.fromkeys(error_detail(item['record']) for item in items if error_detail(item['record'])))
        detail = '; '.join(diagnostics[:2])
        if len(labels) > 2:
            detail += ('; ' if detail else '') + 'Additional settings in workbook'
        if not detail:
            detail = ('No diagnostic detail returned' if kind == 'ReportingErrorOrConflict' else
                      'Overall inventory state; setting causes shown separately' if kind == 'InventoryComplianceState' else
                      'Retained setting result')
        if any(group_state(group) == 'conflictingResults' for group in groups):
            returned = list(dict.fromkeys(status_name(value(item['record'], 'state')) for item in items))
            detail += '; returned statuses: ' + '; '.join(returned)
        if kind == 'InventoryComplianceState':
            overall = status_name(value(record, 'complianceState'))
            states = [overall] if states == [overall] else ['Overall: ' + overall + '; setting: ' + '; '.join(states)]
        return {'policy': '; '.join(policies[:2]), 'setting': '; '.join(labels[:2]),
                'status': '; '.join(states), 'detail': detail}
    return {'policy': value(record, 'policyName', 'compliancePolicyName') or 'Not retained',
            'setting': requirement_result(value(record, 'settingName', 'setting'), value(record, 'state')) if kind != 'InventoryComplianceState'
                       else 'Specific noncompliance reason not retained',
            'status': status_name(value(record, 'complianceState', 'state', 'status')),
            'detail': error_detail(record) or ('Overall inventory state; not an additional setting failure'
                       if kind == 'InventoryComplianceState' else 'No diagnostic message retained')}

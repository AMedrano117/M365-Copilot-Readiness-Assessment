"""Presentation of selected device evidence; no inference of missing causes."""
import re

from .device_evidence import value
from .operational_evidence import reported_boolean


def setting_name(label):
    short = str(label or 'Setting name not retained').rsplit('.', 1)[-1]
    return re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', short).replace('_', ' ').replace('Bit Locker', 'BitLocker')


def status_name(state):
    text = str(state or 'Not retained')
    return {'noncompliant': 'Not compliant', 'compliant': 'Compliant',
            'notevaluated': 'Not evaluated', 'error': 'Error', 'conflict': 'Conflict',
            'unknown': 'Unknown', 'remediated': 'Remediated'}.get(
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
        items = [item for item in device.get('settings', []) if item['failed'] or item['assignment_missing']]
    if items:
        # A device can have multiple settings. Keep the example bounded and make
        # any omitted details explicit; the complete rows stay in the workbook.
        policies = list(dict.fromkeys(item['policy'] if item['policy'] != 'not retained'
                                     else 'Not retained' for item in items))
        labels = list(dict.fromkeys(setting_name(item['setting']) for item in items))
        states = list(dict.fromkeys(status_name(value(item['record'], 'state')) for item in items))
        diagnostics = list(dict.fromkeys(error_detail(item['record']) for item in items if error_detail(item['record'])))
        detail = '; '.join(diagnostics[:2])
        if len(labels) > 2:
            detail += ('; ' if detail else '') + 'Additional settings in workbook'
        if not detail:
            detail = ('No error code or diagnostic message retained' if kind == 'ReportingErrorOrConflict' else
                      'Overall inventory state; setting causes shown separately' if kind == 'InventoryComplianceState' else
                      'Retained setting result')
        if kind == 'InventoryComplianceState':
            overall = status_name(value(record, 'complianceState'))
            states = [overall] if states == [overall] else ['Overall: ' + overall + '; setting: ' + '; '.join(states)]
        return {'policy': '; '.join(policies[:2]), 'setting': '; '.join(labels[:2]),
                'status': '; '.join(states), 'detail': detail}
    return {'policy': value(record, 'policyName', 'compliancePolicyName') or 'Not retained',
            'setting': setting_name(value(record, 'settingName', 'setting')) if kind != 'InventoryComplianceState'
                       else 'Specific noncompliance reason not retained',
            'status': status_name(value(record, 'complianceState', 'state', 'status')),
            'detail': error_detail(record) or ('Overall inventory state; not an additional setting failure'
                       if kind == 'InventoryComplianceState' else 'No diagnostic message retained')}

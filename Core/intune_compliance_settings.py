"""Retained tenant configuration, independent of assessment activity windows."""

from .evidence_contract import parse_date
from .operational_evidence import reported_boolean


SETTING_LABELS = {
    'secureByDefault': 'Mark devices with no compliance policy assigned as',
    'deviceComplianceCheckinThresholdDays': 'Compliance status validity period (days)',
}


def missing_settings(record):
    settings = record.get('settings') if isinstance(record.get('settings'), dict) else record
    missing = []
    for key in SETTING_LABELS:
        value = settings.get(key)
        valid = reported_boolean(value) is not None if key == 'secureByDefault' else type(value) is int and 1 <= value <= 120
        if not valid:
            missing.append(key)
    return missing


def tenant_compliance_settings(bundle):
    datasets = (bundle.get('assessment_sources') or {}).get('device_compliance_settings', [])
    fields = {}
    for key, label in SETTING_LABELS.items():
        observations, dates = [], []
        for dataset in datasets:
            source = dataset.get('source') or {}
            if source.get('available') is not True or source.get('availability_status', 'available') not in {'available', 'partial'}:
                continue
            for raw in dataset.get('records') or []:
                settings = raw.get('settings') if isinstance(raw.get('settings'), dict) else raw
                value = settings.get(key)
                if key == 'secureByDefault':
                    value = reported_boolean(value)
                elif type(value) is not int or not 1 <= value <= 120:
                    value = None
                if value is not None:
                    observations.append(value)
                    date = parse_date(source.get('refresh_date') or source.get('collected_at'))
                    if date:
                        dates.append(date.isoformat())
        values = set(observations)
        state = 'conflict' if len(values) > 1 else 'observed' if values else 'unknown'
        value = next(iter(values)) if len(values) == 1 else None
        display = ('Conflicting retained values' if state == 'conflict' else 'Unknown' if value is None else
                   ('Noncompliant' if value else 'Compliant') if key == 'secureByDefault' else str(value))
        fields[key] = {'label': label, 'value': value, 'display': display, 'state': state,
                       'observed_at': min(dates) if dates else '',
                       'reason': ('The API returned no usable value for this setting.' if state == 'unknown' and datasets else
                                  'The setting was not collected.' if state == 'unknown' else
                                  'Saved responses contain different values.' if state == 'conflict' else '')}
    null_response = any(raw.get('settings', object()) is None for dataset in datasets for raw in dataset.get('records') or [])
    return {'fields': fields, 'source_state': 'retained' if datasets else 'not_collected',
            'collection_issue': 'The saved API response did not include tenant compliance settings.' if null_response else '',
            'qualification': 'Retained tenant configuration only. Missing or conflicting values remain unknown; no Intune default is assumed. '
                             'These settings do not establish device policy assignment, enforcement or current activity. '
                             'The tenant validity period is separate from the assessment activity and endpoint-reporting windows.'}

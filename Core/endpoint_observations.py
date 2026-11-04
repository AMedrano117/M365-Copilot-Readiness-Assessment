"""Reporting-age subsets within one endpoint condition; never device activity."""
from .device_evidence import population


LABELS = {'current': 'Current observations', 'stale': 'Stale observations — confirm current state',
          'undated': 'Undated observations — confirm report date and current state',
          'invalid': 'Invalid-dated observations — confirm report date and current state'}


def reporting_subset(observed, days):
    if observed.get('state') == 'missing':
        return 'undated'
    if observed.get('state') != 'valid' or observed.get('age_days') is None:
        return 'invalid'
    return 'current' if 0 <= observed['age_days'] <= days else 'stale'


def observation_subsets(items, days):
    groups = {key: [] for key in LABELS}
    for item in items:
        groups[reporting_subset(item['timestamp'], days)].append(item)
    return {key: {'label': LABELS[key], 'counts': population(entries),
                  'evidence_ids': [item['evidence_id'] for item in entries],
                  'requires_confirmation': key != 'current'} for key, entries in groups.items()}


def subset_lines(subsets, days):
    lines = [f'Reporting window: {days} days. Reporting age is separate from device activity.']
    for key in LABELS:
        subset = subsets[key]
        counts = subset['counts']
        if not counts['source_record_count']:
            continue
        period = counts['evidence_period']
        dates = ('; report date ' + period['start'] if period['start'] == period['end'] else
                 '; reports ' + period['start'] + ' to ' + period['end']) if period['start'] else ''
        device_unit = 'identified device' if counts['resolved_device_count'] == 1 else 'identified devices'
        record_unit = 'source record' if counts['source_record_count'] == 1 else 'source records'
        lines.append(f"{subset['label']}: {counts['resolved_device_count']} {device_unit}, "
                     f"{counts['source_record_count']} {record_unit}" + dates +
                     (f"; {counts['unresolved_identity_count']} records have unresolved device identity"
                      if counts['unresolved_identity_count'] else '') + '.')
    lines.append('A device may have observations in multiple subsets; do not add subset device counts. Stale and undated results do not establish current protection.')
    return lines

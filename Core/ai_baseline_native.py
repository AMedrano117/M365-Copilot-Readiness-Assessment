"""Reconcile the native Compliance Manager export without applying its update sheet.

Action Update, Improvement Action Details and Customer Managed describe the same
customer actions. Microsoft Managed is a separate responsibility population.
Instructions and action descriptions are source data, never executable steps.
"""
from collections import defaultdict
from datetime import datetime, timezone
import re


def _key(value):
    return re.sub(r'[^a-z0-9]', '', str(value or '').lower())


def _text(value):
    return value.isoformat() if hasattr(value, 'isoformat') else str(value).strip() if value is not None else ''


def _baseline(value):
    return bool(re.search(r'\bai[\s_-]*baseline\b', _text(value), re.I))


def _records(sheet, required):
    iterator = sheet.iter_rows(values_only=True)
    headers = next(iterator, ())
    keys = [_key(header) for header in headers]
    if not required <= set(keys) or len([key for key in keys if key]) != len(set(keys) - {''}):
        raise ValueError(f'AI Baseline {sheet.title} has missing or ambiguous native columns.')
    rows = []
    for number, values in enumerate(iterator, 2):
        if not any(_text(value) for value in values):
            continue
        if len(rows) >= 10000:
            raise ValueError('AI Baseline native sheet exceeds 10,000 rows; narrow the export scope.')
        original = {str(header): _text(value) for header, value in zip(headers, values) if header is not None}
        rows.append({'fields': dict(zip(keys, values)), 'original': original,
                     'sheet': sheet.title, 'row': number})
    return rows


def _field(record, *keys):
    return next((_text(record['fields'].get(key)) for key in keys if _text(record['fields'].get(key))), '')


def _scope_key(record):
    return (_field(record, 'improvementactionname').casefold(),
            _field(record, 'servicescope').casefold(), _field(record, 'group').casefold(),
            _field(record, 'service', 'applicableto').casefold())


def _date(value):
    """Native export uses US month/day text; year 0001 is an unset sentinel."""
    text = _text(value)
    if not text or text.startswith(('01/01/0001', '0001-')):
        return ''
    for pattern in ('%m/%d/%Y %H:%M:%S %z', '%m/%d/%Y %H:%M:%S'):
        try:
            return datetime.strptime(text, pattern).isoformat()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).isoformat()
    except ValueError:
        return ''


def _reference(record):
    return {'Sheet': record['sheet'], 'Row': record['row'], 'Fields': record['original']}


def _statuses(records, *keys):
    values = list(dict.fromkeys(_field(record, *keys) for record in records if _field(record, *keys)))
    return values[0] if len(values) == 1 else '', values if len(values) > 1 else []


def _test_date(records):
    dates = list(dict.fromkeys(_date(_field(record, 'testdate')) for record in records
                              if _date(_field(record, 'testdate'))))
    if not dates:
        return '', '', False
    parsed = [datetime.fromisoformat(value) for value in dates]
    aware = [value for value in parsed if value.tzinfo is not None]
    preferred = aware[0] if aware else parsed[0]
    # The update sheet omits the offset present in Customer Managed. Retain that
    # original, and prefer the explicit timestamp when the printed clock matches.
    conflict = any((value.astimezone(timezone.utc) != preferred.astimezone(timezone.utc)
                    if value.tzinfo is not None and preferred.tzinfo is not None else
                    value.replace(tzinfo=None) != preferred.replace(tzinfo=None)) for value in parsed)
    qualification = ('Offset-bearing source date retained; other sheet omits the timezone.'
                     if aware and any(value.tzinfo is None for value in parsed) else '')
    return '' if conflict else preferred.isoformat(), qualification, conflict


def read_native_actions(workbook, path):
    sheets = {_key(sheet.title): sheet for sheet in workbook}
    details = sheets.get('improvementactiondetails')
    if details is None:
        return None
    # This discriminator is specific to the supplied native workbook schema.
    first = next(details.iter_rows(values_only=True), ())
    if not {'improvementactionname', 'assessments', 'regulations'} <= {_key(value) for value in first}:
        return None
    detail_rows = _records(details, {'improvementactionname', 'assessments', 'regulations', 'teststatus'})
    updates = _records(sheets['actionupdate'], {'actionid', 'improvementactionname', 'teststatus'}) if 'actionupdate' in sheets else []
    customers = _records(sheets['customermanaged'], {'actiontitle', 'control', 'testresult'}) if 'customermanaged' in sheets else []
    microsoft = _records(sheets['microsoftmanaged'], {'actiontitle', 'microsoftcontrol', 'testresult', 'status'}) if 'microsoftmanaged' in sheets else []
    index, names, customer_index = {}, defaultdict(list), defaultdict(list)
    for record in detail_rows:
        if not _baseline(_field(record, 'regulations')) or not _baseline(_field(record, 'assessments')):
            raise ValueError('Native action details include a non-AI Baseline assessment; use one assessment scope per export.')
        identity = _scope_key(record)
        if not identity[0] or identity in index:
            raise ValueError('AI Baseline native action details contain missing or duplicate scoped action names.')
        index[identity] = record
        names[identity[0]].append(identity)
    update_index = {}
    for record in updates:
        identity = _scope_key(record)
        if identity not in index or identity in update_index:
            raise ValueError('Action Update cannot be matched uniquely to the native action details.')
        update_index[identity] = record
    for record in customers:
        name = _field(record, 'actiontitle').casefold()
        if len(names.get(name, [])) != 1:
            raise ValueError('Customer Managed cannot be matched uniquely to the native action details.')
        customer_index[name].append(record)
    if microsoft and not detail_rows:
        raise ValueError('Microsoft Managed needs AI Baseline assessment identity from populated action details.')
    rows = []
    for identity, detail in index.items():
        update = update_index.get(identity)
        mappings = customer_index.get(identity[0], [])
        records = [detail] + ([update] if update else []) + mappings
        test, conflicts = _statuses(records, 'teststatus', 'testresult')
        implementation, implementation_conflicts = _statuses(records, 'implementationstatus')
        test_date, date_qualification, date_conflict = _test_date(records)
        conflict_notes = []
        if conflicts:
            conflict_notes.append('Test statuses differ between sheets: ' + ', '.join(conflicts))
        if implementation_conflicts:
            conflict_notes.append('Implementation statuses differ between sheets: ' + ', '.join(implementation_conflicts))
        if date_conflict:
            conflict_notes.append('Test dates differ between sheets; confirm the applicable observation')
        row = {'Improvement action': _field(detail, 'improvementactionname'),
               'Action ID': _field(update, 'actionid') if update else '',
               'Assessment': _field(detail, 'assessments'), 'Regulation': _field(detail, 'regulations'),
               'Responsibility': 'Customer', 'Action type': _field(detail, 'actiontype'),
               'Solution': _field(detail, 'solution'), 'Categories': _field(detail, 'categories'),
               'Service': _field(detail, 'service'), 'Service scope': _field(detail, 'servicescope'),
               'Group': _field(detail, 'group'), 'Points achieved': _field(detail, 'pointsacheived', 'pointsachieved'),
               'Implementation status': implementation, 'Test result': test,
               'Test date': test_date, 'Date qualification': date_qualification,
               'Testing type': _field(detail, 'testingtype'),
               'Owner': next((_field(record, 'assignedto', 'actionowner') for record in records
                              if _field(record, 'assignedto', 'actionowner')), ''),
               'Control ID': '; '.join(dict.fromkeys(_field(record, 'control') for record in mappings)),
               'Control': '; '.join(dict.fromkeys(_field(record, 'title') for record in mappings)),
               'Source file': path.name, 'Source sheet': detail['sheet'], 'Source row': detail['row'],
               'Original fields': detail['original'], 'Source records': [_reference(record) for record in records],
               'Import conflicts': '; '.join(conflict_notes)}
        rows.append(row)
    for record in microsoft:
        if not _field(record, 'actiontitle'):
            continue
        rows.append({'Improvement action': _field(record, 'actiontitle'), 'Action ID': '',
                     'Assessment': _field(detail_rows[0], 'assessments'), 'Regulation': _field(detail_rows[0], 'regulations'),
                     'Responsibility': 'Microsoft', 'Action type': _field(record, 'actiontype'),
                     'Solution': '', 'Points achieved': '', 'Microsoft action points': _field(record, 'compliancescore'),
                     'Control ID': _field(record, 'microsoftcontrol'), 'Control': _field(record, 'controltitle'),
                     'Implementation status': _field(record, 'status'), 'Test result': _field(record, 'testresult'),
                     'Test date': _date(_field(record, 'testdate')), 'Owner': _field(record, 'actionowner'),
                     'Source file': path.name, 'Source sheet': record['sheet'], 'Source row': record['row'],
                     'Original fields': record['original'], 'Source records': [_reference(record)], 'Import conflicts': ''})
    return rows

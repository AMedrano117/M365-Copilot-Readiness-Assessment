"""Service-independent evidence contract for every recommended action.

Recommendation producers supply the records used by their calculation, or an
explicit configuration/absence/unavailable/planning explanation. Exporters do
not need to know the service, API, object type or the wording of the finding.
Legacy adapters may fill old recommendations, but may never leave a silent gap.
"""

from collections.abc import Mapping
from hashlib import sha256
import json
import re


STATUSES = {'Records available', 'Configuration evidence', 'Evidence unavailable',
            'Planning decision', 'Detail mapping missing', 'Count mismatch', 'Historical evidence'}


def recommended_actions(result):
    """Include next steps outside the primary Action Plan, irrespective of service."""
    output, seen = [], set()
    for row in [*(result.get('actions') or []), *(result.get('recommendations') or [])]:
        if not isinstance(row, dict):
            continue
        if not row.get('Recommendation') and row not in (result.get('actions') or []):
            continue
        identifier = row.get('RecommendationId')
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        output.append(row)
    return output


def _text(value):
    if value is None:
        return 'Not supplied'
    if isinstance(value, (list, tuple)):
        return '; '.join(_text(item) for item in value)
    if isinstance(value, Mapping):
        return '; '.join(f'{key}: {_text(item)}' for key, item in value.items())
    return str(value)


def _serializable(value):
    """Keep source timestamps and nested attributes exportable without dropping them."""
    if isinstance(value, Mapping):
        return {str(key): _serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serializable(item) for item in value]
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if isinstance(value, float) and (value != value or abs(value) == float('inf')):
        return str(value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def _field(record, explicit, candidates):
    if explicit:
        return _text(record.get(explicit))
    return next((_text(record[name]) for name in candidates if record.get(name) is not None), 'Not supplied')


def _title(proposed, used):
    title = re.sub(r'[\\/*?:\[\]]', ' ', str(proposed or 'Action Evidence')).strip(" '")[:31] or 'Action Evidence'
    base, suffix = title, 2
    while title.casefold() in used:
        ending = f' {suffix}'
        title, suffix = base[:31 - len(ending)] + ending, suffix + 1
    used.add(title.casefold())
    return title


def _measure(records, reconciliation):
    operation = reconciliation.get('operation', 'count')
    field = reconciliation.get('field')
    if operation == 'count':
        return len(records)
    if not field or any(field not in record or record[field] is None for record in records):
        raise ValueError('The reconciliation field is missing from one or more records.')
    if operation == 'distinct':
        return len({json.dumps(record[field], sort_keys=True, default=str) for record in records})
    if operation == 'sum':
        if any(isinstance(record[field], bool) for record in records):
            raise ValueError('Boolean flags cannot be summed as measured counts.')
        values = [float(record[field]) for record in records]
        if any(value != value or abs(value) == float('inf') for value in values):
            raise ValueError('Non-finite values cannot be reconciled.')
        return sum(values)
    raise ValueError(f'Unsupported reconciliation operation: {operation}.')


def materialize_declared_evidence(bundle, recommendations):
    """Export any service's explicitly supplied evidence using one implementation."""
    from .investigation_details import _ref
    sheets = bundle.setdefault('sheets', {})
    for key in list(sheets):
        if key.startswith('declared_investigation.'):
            del sheets[key]
    used = {str(sheet.get('title') or '').casefold() for sheet in sheets.values()}
    output = {}
    for rec in recommendations:
        contract = rec.get('InvestigationEvidence')
        if contract is None:
            continue
        identifier = rec['RecommendationId']
        if not isinstance(contract, Mapping):
            output[identifier] = _unavailable('Detail mapping missing', 'The recommendation supplied an invalid investigation evidence declaration; its supporting records cannot be exported.')
            continue
        kind = contract.get('kind', 'records')
        invalid_fields = [key for key in ('record_id_field', 'entity_field', 'timestamp_field', 'status_field') if contract.get(key) is not None and not isinstance(contract[key], str)]
        if not isinstance(kind, str) or invalid_fields:
            output[identifier] = _unavailable('Detail mapping missing', 'The evidence declaration has an invalid kind or field selector; each must be a string.')
            continue
        records = contract.get('records', [])
        source = contract.get('source') or {}
        try:
            source = _serializable(source) if isinstance(source, Mapping) else {'name': str(source)}
        except (TypeError, ValueError, RecursionError):
            output[identifier] = _unavailable('Detail mapping missing', 'The source declaration could not be serialized; provide plain metadata without circular references.')
            continue
        source_note = ' '.join(f'{name.replace("_", " ").capitalize()}: {_text(value)}.'
                              for name, value in source.items() if value is not None and value != '')
        reason = str(contract.get('reason') or '').strip()
        if kind in {'unavailable', 'absence', 'planning'}:
            if not reason:
                output[identifier] = _unavailable('Detail mapping missing', f'The {kind} evidence declaration does not explain why individual supporting records are unavailable.')
                continue
            status = 'Planning decision' if kind == 'planning' else 'Configuration evidence' if kind == 'absence' else 'Evidence unavailable'
            if kind == 'absence' and (source.get('complete') is False or source.get('truncated') is True):
                status = 'Evidence unavailable'
                reason += ' The source was incomplete or truncated, so absence of the control or affected records is not established.'
            output[identifier] = _unavailable(status, ' '.join(filter(None, [reason, source_note])))
            output[identifier]['InvestigationPublicQualification'] = reason
            continue
        if kind not in {'records', 'configuration'} or not isinstance(records, (list, tuple)) or any(not isinstance(row, Mapping) for row in records):
            output[identifier] = _unavailable('Detail mapping missing', 'The evidence declaration must supply records as a list of field/value objects or an explicit absence, unavailable or planning reason.')
            continue
        if not records:
            output[identifier] = _unavailable('Evidence unavailable' if reason else 'Detail mapping missing',
                ' '.join(filter(None, [reason or 'The recommendation did not supply the individual records supporting its action.', source_note])))
            output[identifier]['InvestigationPublicQualification'] = reason or 'The recommendation did not supply the individual records supporting its action.'
            continue
        try:
            records = [_serializable(record) for record in records]
        except (TypeError, ValueError, RecursionError):
            output[identifier] = _unavailable('Detail mapping missing', 'The supplied evidence records cannot be serialized; supply plain fields, timestamps and nested attributes without circular references.')
            continue
        title = _title(contract.get('sheet_name') or f"Evidence {identifier}", used)
        rows = []
        for position, record in enumerate(records, 1):
            record_id = _field(record, contract.get('record_id_field'), ('id', 'Id', 'ID', 'Record ID', 'Object ID'))
            fingerprint = json.dumps({'source': source, 'record': record}, sort_keys=True, default=str)
            evidence_id = 'INV-' + sha256(fingerprint.encode('utf-8')).hexdigest()[:20]
            row = {
                'RecommendationId': identifier, 'Evidence ID': evidence_id, 'Source Record': position,
                'Record ID': record_id,
                'Entity': _field(record, contract.get('entity_field'), ('userPrincipalName', 'displayName', 'name', 'Name')),
                'Observed At': _field(record, contract.get('timestamp_field'), ('createdDateTime', 'timestamp', 'lastUpdatedDateTime')),
                'Record Status': _field(record, contract.get('status_field'), ('status', 'state', 'Status')),
                'Source API / File': _text(source.get('api') or source.get('file') or source.get('name')),
                'Collected At': _text(source.get('collected_at')), 'Collection Window': _text(source.get('window')),
                'Selection / Scope': _text(source.get('filter') or source.get('scope')),
            }
            # All supplied investigation attributes survive; reserved fields
            # cannot overwrite provenance. The Excel writer handles nesting
            # and continuation columns without silently losing long values.
            for name, value in record.items():
                header = str(name)
                while header in row:
                    header = 'Record.' + header
                row[header] = value
            rows.append(row)
        reconciliation = contract.get('reconciliation') or {'operation': 'count'}
        notes, mismatch = [], False
        try:
            measured = _measure(records, reconciliation)
            expected = reconciliation.get('expected')
            if expected is not None and (isinstance(expected, bool) or float(expected) != measured):
                mismatch = True
                notes.append(f'Count mismatch: the finding reports {expected}; the supplied records reproduce {measured:g}.')
            else:
                notes.append(f'{len(rows)} detailed rows exported; {reconciliation.get("operation", "count")} reproduces {measured:g}' +
                             (f' against the reported {expected}.' if expected is not None else '. No expected measure was supplied.'))
        except (TypeError, ValueError, AttributeError) as exc:
            mismatch = True
            notes.append('Reconciliation could not be established: ' + str(exc))
        if reason:
            notes.append(reason)
        if source.get('complete') is False or source.get('truncated') is True:
            notes.append('The source was incomplete or truncated. These rows reproduce the retained evidence only; they do not establish the full population.')
        public_note = ' '.join(notes)
        missing_metadata = [name for name in ('filter', 'scope', 'window', 'pages', 'complete', 'truncated', 'permissions', 'licensing', 'retention') if name not in source]
        if missing_metadata:
            notes.append('Source metadata not retained: ' + ', '.join(missing_metadata) + '. These limits were not independently verified during export.')
        if source_note:
            notes.append(source_note)
        note = ' '.join(notes)
        from .export_recommendations import _excel_safe_rows
        rows = _excel_safe_rows(rows, title)
        sheets['declared_investigation.' + identifier] = {
            'title': title, 'rows': rows, 'restricted': True, 'preview_columns': [],
            'summary': reason or str(rec.get('Feature') or 'Recommendation evidence'), 'details': notes,
        }
        width = len(dict.fromkeys(field for row in rows for field in row))
        output[identifier] = {
            'InvestigationCount': len(rows), 'InvestigationRange': _ref(title, 2, len(rows) + 1, width),
            'InvestigationSummary': f'{len(rows)} supporting record{"s" if len(rows) != 1 else ""}',
            'InvestigationNote': note, 'InvestigationQualification': note, 'InvestigationPublicQualification': public_note,
            'InvestigationStatus': 'Count mismatch' if mismatch else 'Configuration evidence' if kind == 'configuration' else 'Records available',
        }
    return output


def _unavailable(status, reason):
    return {'InvestigationCount': 0, 'InvestigationRange': '', 'InvestigationSummary': status,
            'InvestigationNote': reason, 'InvestigationQualification': reason, 'InvestigationPublicQualification': reason, 'InvestigationStatus': status}


def validate_investigation_coverage(result, bundle):
    """Every next step must have a usable record range or a visible explanation."""
    from openpyxl.utils.cell import range_boundaries
    titles = {sheet.get('title'): sheet for sheet in (bundle.get('sheets') or {}).values()}
    issues = []
    for rec in recommended_actions(result):
        identifier = rec['RecommendationId']
        status = rec.get('InvestigationStatus')
        if status not in STATUSES:
            issues.append(f'{identifier}: missing investigation status.')
        location = str(rec.get('InvestigationRange') or '')
        if location:
            match = re.fullmatch(r"'((?:[^']|'')+)'!([A-Z]+[1-9][0-9]*:[A-Z]+[1-9][0-9]*)", location)
            if not match or match.group(1).replace("''", "'") not in titles:
                issues.append(f'{identifier}: supporting record range does not resolve.')
                continue
            sheet = titles[match.group(1).replace("''", "'")]
            _, first, last_column, last = range_boundaries(match.group(2))
            rows = sheet.get('rows') or []
            width = len(dict.fromkeys(field for row in rows for field in row))
            if first < 2 or last < first or last > len(rows) + 1 or last_column > width:
                issues.append(f'{identifier}: supporting range is outside the exported records.')
        elif rec.get('InvestigationCount') or not rec.get('InvestigationQualification'):
            issues.append(f'{identifier}: no records and no visible missing-evidence explanation.')
    return issues

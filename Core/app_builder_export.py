"""Small, individually uploadable files for Microsoft 365 App Builder (Frontier).

Every file is flat and self-describing: CSV for evidence rows, small JSON for finding
metadata, recommendations and context, plus a manifest, upload guide and starter prompt.
File sizes are measured on the encoded bytes written to disk. Records are never sampled
or shortened: rows are split into numbered parts, and a value too large for any single
file moves losslessly into numbered oversized-value chunk files.
"""

from collections import Counter, OrderedDict
import csv
from hashlib import sha256
import io
import json
from pathlib import Path
import re

APP_BUILDER_SCHEMA_VERSION = '1.0.0'
HARD_LIMIT_BYTES = 1_000_000          # every file must be strictly smaller than this
TARGET_BYTES = 250_000                # preferred part size
EXPANDED_TARGET_BYTES = 900_000       # used only when a series would otherwise need many parts
PREFERRED_MAX_PARTS = 8
OVERSIZED_COLUMNS = ('detail_record_id', 'table_id', 'field', 'chunk_index', 'chunk_count', 'chunk_text')
FORMULA_PREFIXES = ('=', '+', '-', '@')
MAX_FILENAME_LENGTH = 72

UNITS = {
    'record_count': 'Number of exported evidence rows for the finding, counted in record_unit (for example sign-in events or grant records).',
    'affected_entity_count': 'Number of unique accounts, applications, devices, sites or incidents behind those rows (entity_unit). '
                             'Several rows can belong to one entity, so this is usually smaller than record_count. Null means no stable identifier exists.',
    'worklist_count': 'The workbook worklist count. A worklist item can group several detail records.',
    'context_record_count': 'Unique supporting-context records. They support the finding without asserting an affected population.',
}
KIND_TEXT = {
    'observed_event': 'Observed events: individual retained events such as sign-ins or incidents.',
    'entity_state': 'Entity records: the retained state of named users, applications, devices or sites.',
    'configuration': 'Configuration settings: policies or settings, not observed activity.',
    'supporting_context': 'Supporting context: retained inventory that supports the finding without naming an affected subset.',
    'aggregate_only': 'Aggregate-only evidence: summary counts that cannot be expanded into individual records.',
    'none': 'No records were exported; see evidence_availability and missing_evidence_action.',
}
AVAILABILITY_TEXT = {
    'complete': 'Every contributing source reported complete collection.',
    'partial': 'At least one contributing source was incomplete, truncated or failed; rows describe only what was retained.',
    'unavailable': 'The source was collected but returned no usable records for this finding, or collection failed.',
    'not_retained': 'The saved collection did not retain the source needed for this finding.',
    'absent': 'A complete source returned no matching records.',
    'historical': 'Historical evidence only; named records cannot be recreated.',
    'planning': 'A planning decision; no record population is asserted.',
    'unknown': 'Source completeness was not recorded.',
}


def _csv_value(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


def _csv_line(values):
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator='\r\n').writerow([_csv_value(value) for value in values])
    return buffer.getvalue().encode('utf-8')


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=1, allow_nan=False, default=str) + '\n').encode('utf-8')


def _chunks(text, max_bytes):
    chunks, current, size = [], [], 0
    for character in text:
        width = len(character.encode('utf-8'))
        if current and size + width > max_bytes:
            chunks.append(''.join(current))
            current, size = [], 0
        current.append(character)
        size += width
    chunks.append(''.join(current))
    return chunks


def _bounded_filename(name):
    """Keep extensions and part numbers visible without repeating long table IDs."""
    name = str(name)
    extension = Path(name).suffix
    stem = name[:-len(extension)] if extension else name
    match = re.search(r'-p\d+$', stem)
    part = match.group() if match else ''
    original = stem[:-len(part)] if part else stem
    readable = re.sub(r'[^A-Za-z0-9.-]+', '-', original).strip('.-') or 'file'
    if readable.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{n}' for n in range(1, 10)), *(f'LPT{n}' for n in range(1, 10))}:
        readable = 'file-' + readable
    available = MAX_FILENAME_LENGTH - len(extension) - len(part)
    if len(readable) > available:
        digest = sha256(original.encode('utf-8')).hexdigest()[:8]
        readable = readable[:available - 9].rstrip('.-') + '-' + digest
    return readable + part + extension


class _Writer:
    def __init__(self, folder, *, target_bytes, hard_limit, expanded_target, max_parts):
        self.folder = Path(folder)
        if self.folder.exists() and any(self.folder.iterdir()):
            raise FileExistsError(f'App Builder export folder is not empty: {self.folder}')
        self.folder.mkdir(parents=True, exist_ok=True)
        if not 1_000 <= target_bytes < hard_limit or not target_bytes <= expanded_target < hard_limit:
            raise ValueError('Part targets must be smaller than the hard file-size limit.')
        self.target, self.limit, self.expanded, self.max_parts = target_bytes, hard_limit, expanded_target, max_parts
        self.files = []
        self.oversized = {}

    def write(self, name, data, **entry):
        name = _bounded_filename(name)
        if len(data) >= self.limit:
            raise ValueError(f'{name} would be {len(data)} bytes; the limit is below {self.limit} bytes.')
        path = self.folder / name
        if path.exists():
            raise FileExistsError(f'Duplicate App Builder file name: {name}')
        path.write_bytes(data)
        size = path.stat().st_size
        if size != len(data) or size >= self.limit:
            raise ValueError(f'{name} was written as {size} bytes; expected {len(data)} below {self.limit}.')
        record = OrderedDict([('file', name)])
        record.update(entry)
        record.update(bytes=size, sha256=sha256(data).hexdigest())
        self.files.append(record)
        return record

    def shrink(self, item, measure, budget, concern_prefix, table_id, row_key):
        """Move the largest string values out of an item until it fits; nothing is shortened or lost."""
        def strings(value, path=()):
            if isinstance(value, str):
                yield path, value
            elif isinstance(value, dict):
                for key, child in value.items():
                    yield from strings(child, path + (key,))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    yield from strings(child, path + (index,))
        def assign(value, path, replacement):
            for step in path[:-1]:
                value = value[step]
            value[path[-1]] = replacement
        moved = []
        while measure(item) > budget:
            candidates = sorted(((path, value) for path, value in strings(item)
                                 if not value.startswith('[oversized value moved losslessly')),
                                key=lambda pair: len(pair[1].encode('utf-8')), reverse=True)
            if not candidates or len(candidates[0][1].encode('utf-8')) <= 400:
                raise ValueError(f'{table_id} {row_key}: one record cannot fit below {self.limit} bytes even after moving long values.')
            path, value = candidates[0]
            field = '.'.join(str(step) for step in path)
            chunks = _chunks(value, self.target // 2)
            series = self.oversized.setdefault(concern_prefix, [])
            for index, chunk in enumerate(chunks, 1):
                series.append([row_key, table_id, field, index, len(chunks), chunk])
            marker = (f'[oversized value moved losslessly to {concern_prefix}-oversized-values files: '
                      f'detail_record_id={row_key}; table_id={table_id}; field={field}; chunks={len(chunks)}; '
                      f'utf8_bytes={len(value.encode("utf-8"))}]')
            assign(item, path, marker)
            moved.append({'detail_record_id': row_key, 'table_id': table_id, 'field': field, 'chunks': len(chunks)})
        return moved

    def csv_series(self, stem, columns, rows, key_column, concern_prefix, series_id, **entry):
        header = _csv_line(columns)
        lines, moved = [], []
        for row in rows:
            values = OrderedDict((column, row.get(column)) for column in columns)
            line = _csv_line(values.values())
            if len(header) + len(line) >= self.limit:
                moved.extend(self.shrink(values, lambda item: len(header) + len(_csv_line(item.values())),
                                         self.limit - 1, concern_prefix, series_id, str(row.get(key_column) or '')))
                line = _csv_line(values.values())
            lines.append((line, values))
        total = sum(len(line) for line, _ in lines)
        target = self.target
        if total and -(-total // max(1, target - len(header))) > self.max_parts:
            target = min(self.expanded, len(header) + -(-total // self.max_parts) + 1)
        parts, current, size = [], [], len(header)
        for line, values in lines:
            if current and size + len(line) > target:
                parts.append(current)
                current, size = [], len(header)
            current.append((line, values))
            size += len(line)
        if current or not parts:
            parts.append(current)
        records = []
        for number, part in enumerate(parts, 1):
            name = f'{stem}-p{number:02d}.csv'
            data = header + b''.join(line for line, _ in part)
            formula = sum(1 for _, values in part for value in values.values()
                          if isinstance(value, str) and value.startswith(FORMULA_PREFIXES))
            records.append(self.write(name, data, format='csv', part=number, parts=len(parts), rows=len(part),
                                      columns=list(columns), formula_like_cells=formula,
                                      first_row_key=str(part[0][1].get(key_column) or '') if part else None,
                                      last_row_key=str(part[-1][1].get(key_column) or '') if part else None, **entry))
        return records, moved

    def json_series(self, stem, envelope, items, items_key, concern_prefix, **entry):
        """Write JSON split by whole items; each part repeats the envelope so it stands alone."""
        def build(chunk, number, count):
            document = OrderedDict(envelope)
            document.update(part=number, parts=count)
            document[items_key] = chunk
            return document
        moved = []
        overhead = len(_json_bytes(build([], 99, 99)))
        # Nested indentation adds a little to each item; the exact size is checked when written.
        budget = (self.limit - 1 - overhead) * 3 // 4
        sizes = []
        for item in items:
            if len(_json_bytes(item)) > budget:
                moved.extend(self.shrink(item, lambda value: len(_json_bytes(value)), budget, concern_prefix,
                                         stem, str(item.get('finding_id') or '')))
            sizes.append(len(_json_bytes(item)) * 5 // 4)
        parts, current, size = [], [], overhead
        for item, item_size in zip(items, sizes):
            if current and size + item_size > self.target:
                parts.append(current)
                current, size = [], overhead
            current.append(item)
            size += item_size
        parts.append(current)
        records = []
        for number, chunk in enumerate(parts, 1):
            name = f'{stem}.json' if len(parts) == 1 else f'{stem}-p{number:02d}.json'
            records.append(self.write(name, _json_bytes(build(chunk, number, len(parts))), format='json', part=number,
                                      parts=len(parts), rows=len(chunk), **entry))
        return records, moved

    def write_oversized(self, written, concern_files):
        """Write chunk files for every prefix that gained oversized values since the last call."""
        for prefix, rows in self.oversized.items():
            if prefix in written:
                continue
            written.add(prefix)
            records, _ = self.csv_series(f'{prefix}-oversized-values', list(OVERSIZED_COLUMNS),
                                         [dict(zip(OVERSIZED_COLUMNS, row)) for row in rows], 'detail_record_id', prefix,
                                         f'{prefix}-oversized-values', purpose='Lossless chunks of values too large for one file',
                                         concern_id=prefix.split('-', 1)[1], role='required', row_unit='value chunks')
            concern_files.setdefault(prefix.split('-', 1)[1], []).extend(record['file'] for record in records)


def _finding_document(finding, files):
    evidence = finding['evidence']
    detail = finding.get('recommendation_detail') or {}
    return OrderedDict([
        ('finding_id', finding['finding_id']), ('finding_uid', finding.get('finding_uid')),
        ('finding_key', finding.get('finding_key')), ('finding_fingerprint', finding.get('finding_fingerprint')),
        ('title', finding.get('title')), ('concern_id', finding['concern_id']), ('related_concerns', finding.get('related_concerns')),
        ('priority', finding.get('priority')), ('disposition', finding.get('disposition')),
        ('readiness_effect', finding.get('readiness_effect')), ('action_type', finding.get('action_type')),
        ('control_id', finding.get('control_id')), ('owner_role', finding.get('owner_role')),
        ('observed_at', finding.get('observed_at')), ('issue', finding.get('issue')),
        ('impact', ' '.join(part for part in (finding.get('readiness_effect') and f"Readiness effect: {finding['readiness_effect']}.",
                                              finding.get('priority') and f"Priority: {finding['priority']}.") if part)),
        ('evidence', OrderedDict([
            ('evidence_kind', evidence['kind']), ('evidence_kind_meaning', KIND_TEXT.get(evidence['kind'])),
            ('evidence_availability', evidence['availability']),
            ('availability_meaning', AVAILABILITY_TEXT.get(evidence['availability'])),
            ('record_count', evidence['record_count']), ('record_unit', evidence['record_unit']),
            ('affected_entity_count', evidence['affected_entity_count']), ('entity_unit', evidence['entity_unit']),
            ('entity_basis', evidence['entity_basis']), ('outcome_counts', evidence['outcome_counts']),
            ('worklist_count', evidence['worklist_count']), ('worklist_unit', evidence['worklist_unit']),
            ('count_relation', evidence['count_relation']),
            ('context_record_count', evidence['context_record_count']),
            ('observation_window', evidence['observation_window']), ('collected_at', evidence['collected_at']),
            ('datasets', evidence['datasets']), ('source_ids', evidence['source_ids']),
            ('selection', evidence['selection']), ('limitations', evidence['limitations']),
            ('reconciliation', evidence['reconciliation']),
            ('missing_evidence_action', evidence['missing_evidence_action']),
        ])),
        ('technical_fix', OrderedDict([
            ('guidance_status', detail.get('guidance_status')), ('what_to_change', detail.get('change')),
            ('prerequisites', detail.get('prerequisites')), ('where_to_configure', detail.get('where')),
            ('how_to_verify', detail.get('verify')), ('documentation', detail.get('links')),
            ('verified_on', detail.get('verified_on')), ('original_recommendation', detail.get('existing_recommendation')),
        ])),
        ('required_evidence_files', files['required']), ('supporting_context_files', files['context']),
        ('note', 'Rows for this finding are in required_evidence_files (match finding_id). Upload every listed part; '
                 'a findings file alone gives the app no records.' if files['required'] or files['context'] else
                 'No evidence rows were exported for this finding; see evidence_availability and missing_evidence_action.'),
    ])


def _catalog_entry(finding, findings_files, files):
    evidence = finding['evidence']
    return OrderedDict([
        ('finding_id', finding['finding_id']), ('finding_key', finding.get('finding_key')), ('title', finding.get('title')),
        ('concern_id', finding['concern_id']), ('priority', finding.get('priority')), ('disposition', finding.get('disposition')),
        ('readiness_effect', finding.get('readiness_effect')), ('evidence_kind', evidence['kind']),
        ('evidence_availability', evidence['availability']), ('record_count', evidence['record_count']),
        ('record_unit', evidence['record_unit']), ('affected_entity_count', evidence['affected_entity_count']),
        ('entity_unit', evidence['entity_unit']), ('context_record_count', evidence['context_record_count']),
        ('guidance_status', (finding.get('recommendation_detail') or {}).get('guidance_status')),
        ('findings_files', findings_files), ('evidence_files', files['required']), ('context_files', files['context']),
    ])


def _prompt(model, concern=None, files=None):
    scope = f" for the concern '{concern['title']}'" if concern else ''
    lines = [
        f"Build an app to review Microsoft 365 Copilot readiness findings{scope} for {model.get('tenant_name') or 'this tenant'} "
        f"(evaluation date {model.get('evaluation_date') or 'not recorded'}). The uploaded files are confidential assessment evidence.",
        '- 01-overview.json gives the decision, counts and concerns. 02-finding-catalog gives one row per finding; finding_id is the key.',
        '- Each NN-<concern>-findings.json file explains the issue, evidence summary, limitations and technical fix for its findings.',
        '- Each CSV row is one evidence record. Join rows to findings on finding_id (context files list finding_ids). '
        'detail_record_id or evidence_record_id identifies the row; evidence_record_ids point to the original source records.',
        '- Files ending -p01, -p02 and so on are parts of one table. Load every part; any single part is incomplete.',
        '- record_count counts rows in record_unit; affected_entity_count counts unique entity_unit. Do not add counts across findings.',
        '- Show evidence_availability, limitations and missing_evidence_action wherever evidence is partial, unavailable or aggregate-only. '
        'Never invent rows from counts.',
        '- Show outcome exactly as given (Succeeded, Blocked, Failed, Unknown). clientAppUsed is the reported client type, not proof of the exact protocol.',
        '- Views: a findings list filtered by priority and concern; a finding detail page with its evidence table (filters on outcome, account '
        'and application) and its technical fix with documentation links.',
    ]
    if files:
        lines.append('- Files for this app: ' + ', '.join(files) + '.')
    return '\n'.join(lines) + '\n'


def write_app_builder_export(model, folder, *, target_bytes=TARGET_BYTES, hard_limit=HARD_LIMIT_BYTES,
                             expanded_target=EXPANDED_TARGET_BYTES, max_parts=PREFERRED_MAX_PARTS, deliverables=None):
    """Write the App Builder folder from the shared finding-evidence model and return its file map."""
    writer = _Writer(folder, target_bytes=target_bytes, hard_limit=hard_limit, expanded_target=expanded_target,
                     max_parts=max_parts)
    findings = {item['finding_id']: item for item in model['findings']}
    table_files, moved = {}, []
    for concern in model['concerns']:
        prefix = f"{concern['number']:02d}-{concern['concern_id']}"
        for table_id in concern['tables']:
            table = model['tables'][table_id]
            key = 'detail_record_id' if table['role'] == 'selected' else 'evidence_record_id'
            records, shifted = writer.csv_series(
                table_id, table['columns'], table['rows'], key, prefix, table_id,
                purpose=table['title'], concern_id=concern['concern_id'], table_id=table_id,
                role='required' if table['role'] == 'selected' else 'supporting-context',
                record_type=table['record_type'], row_unit=table['row_unit'], finding_ids=list(table['finding_ids']))
            table_files[table_id] = [record['file'] for record in records]
            moved.extend(shifted)
    finding_files = {}
    for finding_id, finding in findings.items():
        evidence = finding['evidence']
        finding_files[finding_id] = {'required': [name for table_id in evidence['tables'] for name in table_files.get(table_id, [])],
                                     'context': [name for table_id in evidence['context_tables'] for name in table_files.get(table_id, [])]}
    concern_files = {}
    for concern in model['concerns']:
        prefix = f"{concern['number']:02d}-{concern['concern_id']}"
        documents = [_finding_document(findings[finding_id], finding_files[finding_id]) for finding_id in concern['finding_ids']]
        envelope = OrderedDict([
            ('format', 'm365-readiness-app-builder-findings'), ('app_builder_schema_version', APP_BUILDER_SCHEMA_VERSION),
            ('tenant_name', model.get('tenant_name')), ('evaluation_date', model.get('evaluation_date')),
            ('concern_id', concern['concern_id']), ('concern_title', concern['title']),
            ('related_concerns', concern['related_concerns']),
            ('evidence_files', [name for table_id in concern['tables'] if model['tables'][table_id]['role'] == 'selected'
                                for name in table_files[table_id]]),
            ('supporting_context_files', [name for table_id in concern['tables'] if model['tables'][table_id]['role'] == 'context'
                                          for name in table_files[table_id]]),
            ('note', 'Evidence rows are in the CSV files listed here, joined on finding_id. Upload all listed parts with this file.'),
        ])
        records, shifted = writer.json_series(f'{prefix}-findings', envelope, documents, 'findings', prefix,
                                              purpose=f"{concern['title']}: findings, evidence summaries and technical fixes",
                                              concern_id=concern['concern_id'], role='required', row_unit='findings',
                                              finding_ids=list(concern['finding_ids']))
        concern_files[concern['concern_id']] = [record['file'] for record in records]
        moved.extend(shifted)
    written = set()
    writer.write_oversized(written, concern_files)
    concern_by_finding = {finding_id: concern['concern_id'] for concern in model['concerns'] for finding_id in concern['finding_ids']}
    catalog = [_catalog_entry(finding, concern_files[concern_by_finding[finding_id]], finding_files[finding_id])
               for finding_id, finding in findings.items()]
    catalog_records, _ = writer.json_series('02-finding-catalog', OrderedDict([
        ('format', 'm365-readiness-app-builder-catalog'), ('app_builder_schema_version', APP_BUILDER_SCHEMA_VERSION),
        ('tenant_name', model.get('tenant_name')), ('evaluation_date', model.get('evaluation_date')),
        ('units', UNITS)]), catalog, 'findings', '02-finding-catalog', purpose='One entry per finding with counts, units and file names',
        role='catalog', row_unit='findings', finding_ids=list(findings))
    catalog_files = [record['file'] for record in catalog_records]
    writer.write_oversized(written, concern_files)
    catalog_files += concern_files.get('finding-catalog', [])
    portal_files = []
    if model.get('portal_report_highlights'):
        from .portal_insights import QUALIFICATION
        records, shifted = writer.json_series('04-pdf-highlights', OrderedDict([
            ('format', 'm365-portal-report-context'), ('qualification', QUALIFICATION)]),
            model['portal_report_highlights'], 'highlights', 'pdf-context',
            purpose='Dated source excerpts from supplied PDF reports, with page references and follow-up',
            role='portal_context', row_unit='source excerpts', finding_ids=[])
        moved.extend(shifted)
        writer.write_oversized(written, concern_files)
        portal_files = [record['file'] for record in records] + concern_files.get('pdf-context', [])
    upload_sets = OrderedDict()
    for concern in model['concerns']:
        required = ['01-overview.json', *catalog_files, *concern_files[concern['concern_id']],
                    *[name for table_id in concern['tables'] if model['tables'][table_id]['role'] == 'selected'
                      for name in table_files[table_id]]]
        context = [name for table_id in concern['tables'] if model['tables'][table_id]['role'] == 'context'
                   for name in table_files[table_id]] + portal_files
        upload_sets[concern['concern_id']] = {'title': concern['title'], 'required': list(dict.fromkeys(required)),
                                              'supporting_context': context}
    evidence_counts = Counter(item['evidence']['kind'] for item in findings.values())
    availability_counts = Counter(item['evidence']['availability'] for item in findings.values())
    priority_order = {'Critical': 0, 'High': 1, 'Medium': 2, 'Low': 3}
    priority = sorted((item for item in findings.values() if item.get('disposition') == 'Action'),
                      key=lambda item: (priority_order.get(item.get('priority'), 4), item['concern_id'], item['finding_id']))
    overview = OrderedDict([
        ('format', 'm365-readiness-app-builder-overview'), ('app_builder_schema_version', APP_BUILDER_SCHEMA_VERSION),
        ('tenant_name', model.get('tenant_name')), ('tenant_id', model.get('tenant_id')),
        ('evaluation_date', model.get('evaluation_date')), ('generated_at', model.get('generated_at')),
        ('methodology_version', model.get('methodology_version')), ('decision', model.get('decision')),
        ('rationale', model.get('rationale')), ('assessment_counts', model.get('counts')),
        ('finding_counts', OrderedDict([('findings', len(findings)),
                                        ('by_priority', dict(Counter(item.get('priority') or 'None' for item in findings.values()))),
                                        ('by_disposition', dict(Counter(item.get('disposition') or 'None' for item in findings.values()))),
                                        ('by_evidence_kind', dict(evidence_counts)),
                                        ('by_evidence_availability', dict(availability_counts))])),
        ('priority_findings', [OrderedDict([('finding_id', item['finding_id']), ('title', item.get('title')),
                                            ('priority', item.get('priority')), ('concern_id', item['concern_id']),
                                            ('record_count', item['evidence']['record_count']),
                                            ('record_unit', item['evidence']['record_unit']),
                                            ('affected_entity_count', item['evidence']['affected_entity_count']),
                                            ('entity_unit', item['evidence']['entity_unit'])]) for item in priority[:25]]),
        ('concerns', [OrderedDict([('concern_id', concern['concern_id']), ('title', concern['title']),
                                   ('findings', len(concern['finding_ids'])),
                                   ('evidence_rows', sum(len(model['tables'][table_id]['rows']) for table_id in concern['tables']
                                                         if model['tables'][table_id]['role'] == 'selected')),
                                   ('upload_set', upload_sets[concern['concern_id']])]) for concern in model['concerns']]),
        ('evidence_kinds', KIND_TEXT), ('evidence_availability', AVAILABILITY_TEXT), ('units', UNITS),
        ('important', ['Uploading this overview or a findings file alone does not give the app any detailed records. '
                       'Upload the CSV files listed for each concern, including every numbered part.',
                       'Counts describe retained evidence only. Missing, partial and aggregate-only evidence is stated per finding.',
                       'These files contain named users, applications, devices and IP addresses. Handle them like the evidence workbook.']),
        ('portal_report_context', {'highlight_count': len(model.get('portal_report_highlights') or []), 'files': portal_files,
                                  'meaning': 'Source excerpts for review; preserve dates and scope. They do not establish scored readiness results.'}),
        ('deliverables', deliverables or {}),
    ])
    writer.write('01-overview.json', _json_bytes(overview), format='json', purpose='Assessment overview, counts, concerns and upload sets',
                 role='overview', rows=len(findings), row_unit='findings', part=1, parts=1, finding_ids=[])
    _write_guides(writer, model, upload_sets, findings, moved)
    # Columns and finding IDs are listed once per table; file entries keep only file-specific facts.
    tables = OrderedDict()
    for record in writer.files:
        if record.get('table_id'):
            table = tables.setdefault(record['table_id'], OrderedDict([
                ('purpose', record.get('purpose')), ('role', record.get('role')), ('concern_id', record.get('concern_id')),
                ('record_type', record.get('record_type')), ('row_unit', record.get('row_unit')),
                ('columns', record.get('columns')), ('finding_ids', record.get('finding_ids')), ('files', []), ('rows', 0)]))
            table['files'].append(record['file'])
            table['rows'] += record.get('rows') or 0
    entries = [OrderedDict((key, value) for key, value in record.items()
                           if key not in {'columns', 'finding_ids', 'purpose', 'record_type'} or not record.get('table_id'))
               for record in writer.files]
    manifest = OrderedDict([
        ('format', 'm365-readiness-app-builder-manifest'), ('app_builder_schema_version', APP_BUILDER_SCHEMA_VERSION),
        ('tenant_name', model.get('tenant_name')), ('evaluation_date', model.get('evaluation_date')),
        ('hard_limit_bytes', f'every file is smaller than {hard_limit} bytes'), ('target_part_bytes', target_bytes),
        ('upload_sets', upload_sets), ('tables', tables), ('oversized_records', moved), ('files', entries),
    ])
    if len(_json_bytes(manifest)) >= min(hard_limit, target_bytes * 2):
        # Very large exports list their files in numbered manifest parts.
        file_records, _ = writer.json_series('03-manifest-files', OrderedDict([
            ('format', 'm365-readiness-app-builder-manifest-files'), ('app_builder_schema_version', APP_BUILDER_SCHEMA_VERSION)]),
            entries, 'files', '03-manifest', purpose='File list continued from 03-manifest.json', role='manifest',
            row_unit='files', finding_ids=[])
        manifest['files'] = None
        manifest['file_list_parts'] = [record['file'] for record in file_records]
    writer.write('03-manifest.json', _json_bytes(manifest), format='json', purpose='Every file with purpose, rows, bytes and checksum',
                 role='manifest', rows=len(entries), row_unit='files', part=1, parts=1, finding_ids=[])
    return {'folder': str(writer.folder), 'files': [record['file'] for record in writer.files],
            'manifest': str(writer.folder / '03-manifest.json'), 'guide': str(writer.folder / '00-upload-guide.md'),
            'finding_files': finding_files, 'concern_files': concern_files, 'upload_sets': upload_sets,
            'oversized_records': moved}


def _write_guides(writer, model, upload_sets, findings, moved):
    sizes = {record['file']: record['bytes'] for record in writer.files}
    lines = [f"# App Builder upload guide: {model.get('tenant_name') or 'assessment'}", '',
             f"Evaluation date: {model.get('evaluation_date') or 'not recorded'}. Decision: {model.get('decision') or 'not recorded'}.", '',
             'Every file here is smaller than 1,000,000 bytes and can be uploaded on its own. No folder upload, ZIP extraction or',
             'linked JSON traversal is needed. These files contain named users, applications, devices and IP addresses;',
             'handle them like the evidence workbook.', '',
             '## How the files connect', '',
             '- `01-overview.json`: decision, counts and the upload set for each concern.',
             '- `02-finding-catalog*.json`: one entry per finding. `finding_id` is the key used everywhere.',
             '- `NN-<concern>-findings*.json`: issue, evidence summary, limitations and the technical fix for each finding.',
             '- `NN-<concern>-<table>-pNN.csv`: evidence rows. Join on `finding_id`; context files list `finding_ids`.',
             '- `03-manifest.json`: every file with its purpose, finding IDs, row count, bytes and SHA-256 checksum.', '',
             '**Uploading a summary alone does not make its records available to the app.** Upload every numbered part',
             '(`-p01`, `-p02`, …) of a table; each part holds different rows and together they hold all of them.', '',
             'App Builder cannot add data to the lists it creates after the app is built, so upload the full set for a concern',
             'when you create the app.', '', '## Upload sets', '']
    for concern_id, upload in upload_sets.items():
        required = upload['required']
        total = sum(sizes.get(name, 0) for name in required)
        lines.append(f"### {upload['title']} (`{concern_id}`)")
        lines.append('')
        lines.append(f"Required ({len(required)} files, {total:,} bytes in total):")
        lines.extend(f'- `{name}`' for name in required)
        if upload['supporting_context']:
            lines.append('')
            lines.append('Optional supporting context (inventory and supplied PDF excerpts; these do not establish an affected population):')
            lines.extend(f'- `{name}`' for name in upload['supporting_context'])
        lines.append('')
    lines += ['## Units', '']
    lines += [f'- `{name}`: {text}' for name, text in UNITS.items()]
    lines += ['', '## Evidence kinds and availability', '']
    lines += [f'- `{name}`: {text}' for name, text in KIND_TEXT.items()]
    lines += [f'- `{name}`: {text}' for name, text in AVAILABILITY_TEXT.items()]
    lines += ['', '## Limits and handling', '',
              '- Rows are never sampled or shortened. A table larger than about 250 KB is split into numbered parts; very large',
              '  tables use larger parts (still under 1,000,000 bytes) so the upload set stays small.',
              '- A single value too large for any one file is moved, unchanged, into `NN-<concern>-oversized-values-pNN.csv`.',
              '  The original cell names the file series, record, field and chunk count. Join chunks for that',
              '  `detail_record_id` and `field` in `chunk_index` order to rebuild the exact value.',
              '- CSV values are exact. Some may begin with =, +, - or @; open CSV files in Excel through Data > From Text/CSV,',
              '  or use the evidence workbook, so that no value is treated as a formula.',
              '- Legacy-authentication outcomes come from status.errorCode and conditionalAccessStatus. clientAppUsed is the',
              '  reported client type; the exact protocol is not established by Microsoft Graph v1.0 sign-in logs.']
    if moved:
        lines += ['', f'Oversized values moved in this export: {len(moved)}. See `oversized_records` in `03-manifest.json`.']
    lines += ['', '## Starter prompt', '', 'Paste this into App Builder with the files for one concern:', '', '```text',
              _prompt(model).rstrip('\n'), '```', '']
    writer.write('00-upload-guide.md', ('\n'.join(lines) + '\n').encode('utf-8'), format='markdown',
                 purpose='Which files to upload together, units, limits and the starter prompt', role='guide',
                 rows=None, part=1, parts=1, finding_ids=[])
    prompts = [_prompt(model)]
    for concern in model['concerns']:
        upload = upload_sets[concern['concern_id']]
        prompts.append(f"---- {concern['title']} ----\n" + _prompt(model, concern, upload['required']))
    writer.write('00-starter-prompt.txt', '\n'.join(prompts).encode('utf-8'), format='text',
                 purpose='Copy-paste prompts, generic and per concern', role='guide', rows=None, part=1, parts=1, finding_ids=[])


def read_csv_parts(folder, names):
    """Read every part of one table back as dictionaries (used by validation and tests)."""
    rows = []
    for name in names:
        with (Path(folder) / name).open(encoding='utf-8', newline='') as handle:
            rows.extend(csv.DictReader(handle))
    return rows


def rebuild_oversized(folder, row):
    """Restore oversized values into one CSV row read from an export."""
    pattern = re.compile(r'^\[oversized value moved losslessly to (?P<prefix>.+?)-oversized-values files: '
                         r'detail_record_id=(?P<key>.*?); table_id=(?P<table>.*?); field=(?P<field>.*?); chunks=(?P<chunks>\d+);')
    restored = dict(row)
    for column, value in row.items():
        match = pattern.match(value or '')
        if not match:
            continue
        names = []
        for path in Path(folder).glob('*.csv'):
            part = re.search(r'-p(\d+)\.csv$', path.name)
            if not part:
                continue
            original = f"{match['prefix']}-oversized-values-p{int(part[1]):02d}.csv"
            if path.name in {original, _bounded_filename(original)}:
                names.append(path.name)
        chunks = [item for item in read_csv_parts(folder, names)
                  if item['detail_record_id'] == match['key'] and item['field'] == match['field'] and item['table_id'] == match['table']]
        restored[column] = ''.join(item['chunk_text'] for item in sorted(chunks, key=lambda item: int(item['chunk_index'])))
    return restored

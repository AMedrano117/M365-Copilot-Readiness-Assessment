"""Bounded JSON documents for agents, with lossless shared evidence references."""

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .dashboard_export import _plain


PACKAGE_FORMAT = 'm365-readiness-assessment-package'
PACKAGE_SCHEMA_VERSION = '1.0.0'
NODE_KEY = '$json_package_node'
DEFAULT_MAX_BYTES = 65_536
DEFAULT_MAX_RECORDS = 50


def _compact(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _text(value):
    """Keep each row on one physical line, and metadata readable at the top."""
    if not isinstance(value, dict):
        return _compact(value) + '\n'
    lines = ['{']
    entries = list(value.items())
    for position, (key, item) in enumerate(entries):
        suffix = ',' if position < len(entries)-1 else ''
        prefix = '  ' + _compact(key) + ': '
        if isinstance(item, list) and item:
            lines.append(prefix + '[')
            lines.extend('    ' + _compact(row) + (',' if number < len(item)-1 else '')
                         for number, row in enumerate(item))
            lines.append('  ]' + suffix)
        else:
            lines.append(prefix + _compact(item) + suffix)
    return '\n'.join([*lines, '}']) + '\n'


def _fingerprint(value):
    return sha256(_compact(value).encode('utf-8')).hexdigest()


def _safe_name(value):
    original = str(value or '')
    name = re.sub(r'[^A-Za-z0-9.-]+', '-', original).strip('.-')
    if name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{n}' for n in range(1, 10)), *(f'LPT{n}' for n in range(1, 10))}:
        name = 'item-' + name
    if len(name) > 40:
        name = name[:31].rstrip('.-') + '-' + _fingerprint(original)[:8]
    return name or 'unnamed'


def _node(kind, parts):
    return {NODE_KEY: {'kind': kind, 'parts': parts}}


class _Writer:
    def __init__(self, folder, max_bytes, max_records):
        if type(max_bytes) is not int or max_bytes < 1024:
            raise ValueError('JSON file size must be an integer of at least 1024 bytes.')
        if type(max_records) is not int or max_records < 1:
            raise ValueError('JSON page record count must be a positive integer.')
        self.folder = Path(folder)
        if self.folder.exists() and any(self.folder.iterdir()):
            raise ValueError('Use a new, empty JSON package folder to preserve existing reports.')
        self.folder.mkdir(parents=True, exist_ok=True)
        self.max_bytes, self.max_records = max_bytes, max_records
        self.budget = max_bytes // 4
        self.cache, self.files = {}, {}

    def write(self, path, value):
        body = _text(value).encode('utf-8')
        if len(body) > self.max_bytes:
            raise ValueError(f'JSON document exceeds the file size limit: {path}')
        target = self.folder / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != body:
            raise ValueError(f'JSON filename collision: {path}')
        target.write_bytes(body)
        digest = sha256(body).hexdigest()
        self.files[path] = {'bytes': len(body), 'sha256': digest}
        return {'path': path, 'sha256': digest}

    def encode(self, value):
        fingerprint = _fingerprint(value)
        if fingerprint in self.cache:
            return self.cache[fingerprint]
        if isinstance(value, dict):
            children = {key: self.encode(item) for key, item in value.items()}
            if NODE_KEY not in value and len(_compact(children).encode('utf-8')) <= self.budget:
                return children
            entries = [{'key': self.encode(key), 'value': item} for key, item in children.items()]
            parts = self.pages(entries, 'nodes/' + fingerprint[:32], kind='object', key='entries')
            result = _node('object', self.encode(parts))
        elif isinstance(value, list):
            children = [self.encode(item) for item in value]
            if len(children) <= self.max_records and len(_compact(children).encode('utf-8')) <= self.budget:
                return children
            parts = self.pages(children, 'nodes/' + fingerprint[:32], kind='array', key='items')
            result = _node('array', self.encode(parts))
        elif isinstance(value, str):
            if len(_compact(value).encode('utf-8')) <= self.budget:
                return value
            parts, start = [], 0
            while start < len(value):
                low, high = start+1, len(value)
                while low < high:
                    middle = (low+high+1)//2
                    if len(_compact(value[start:middle]).encode('utf-8')) <= self.budget:
                        low = middle
                    else:
                        high = middle-1
                chunk = value[start:low]
                path = f'nodes/{fingerprint[:32]}-{len(parts)+1:04d}.json'
                parts.append(self.write(path, {'format': 'm365-json-node-page', 'kind': 'string', 'value': chunk}))
                start = low
            result = _node('string', self.encode(parts))
        else:
            # JSON numbers cannot be split. Values exceeding even the file budget
            # must fail explicitly rather than being shortened or reinterpreted.
            if len(_compact(value).encode('utf-8')) > self.budget:
                raise ValueError('A scalar JSON value exceeds the supported file size limit.')
            return value
        self.cache[fingerprint] = result
        return result

    def array(self, values, prefix, *, records=False):
        if not values:
            return []
        entries = [self.encode(item) for item in values]
        parts = self.pages(entries, prefix, kind='array', key='records' if records else 'items')
        result = _node('array', self.encode(parts))
        self.cache[_fingerprint(values)] = result
        return result

    def pages(self, entries, prefix, *, kind='array', key='records', extra=None):
        parts, pending = [], []
        def document(rows):
            return {'format': 'm365-json-record-page' if key == 'records' else 'm365-json-node-page',
                    'kind': kind, **(extra or {}), key: rows}
        def flush():
            if pending:
                path = f'{prefix}-{len(parts)+1:04d}.json'
                ref = self.write(path, document(pending))
                parts.append({**ref, 'record_count': len(pending)})
                pending.clear()
        for item in entries:
            if pending and (len(pending) >= self.max_records or len(_text(document([*pending, item])).encode('utf-8')) > self.max_bytes):
                flush()
            pending.append(item)
            if len(_text(document(pending)).encode('utf-8')) > self.max_bytes:
                raise ValueError('An encoded record exceeds the JSON file size limit.')
        flush()
        return parts

    def document(self, path, value, *, encoded_keys=()):
        encoded = {key: item if key in encoded_keys else self.encode(item) for key, item in value.items()}
        if len(_text(encoded).encode('utf-8')) > self.max_bytes:
            entries = [{'key': self.encode(key), 'value': item} for key, item in encoded.items()]
            parts = self.pages(entries, 'nodes/' + _fingerprint(encoded)[:32], kind='object', key='entries')
            encoded = {'document': _node('object', self.encode(parts))}
        return self.write(path, encoded)

    def catalog(self, path, records, prefix, *, id_key=None):
        encoded = [self.encode(row) for row in records]
        parts = self.pages(encoded, prefix, key='records')
        offset = 0
        for part in parts:
            length = part['record_count']
            if id_key and length:
                part.update({'first_id': records[offset][id_key], 'last_id': records[offset+length-1][id_key]})
            offset += length
        return self.document(path, {'format': 'm365-json-catalog', 'record_count': len(records),
                                    'pages': parts, 'reference_path_base': 'package_root'})


def write_dashboard_package(folder, payload, *, max_bytes=DEFAULT_MAX_BYTES, max_records=DEFAULT_MAX_RECORDS):
    """Write one new bounded package and return its small index.json entry point.

    All paths within the JSON reference the package root. The original dashboard
    payload remains recoverable, while raw evidence and detail arrays are shared.
    """
    data = _plain(payload)
    writer = _Writer(folder, max_bytes, max_records)
    evidence_locations, evidence_parts = [], []
    datasets = defaultdict(list)
    for row in data.get('evidence_records') or []:
        datasets[row.get('dataset') or 'unknown'].append(row)
    occurrence_refs = {}
    for dataset, rows in sorted(datasets.items()):
        prefix = 'evidence/records/' + _safe_name(dataset) + '-' + _fingerprint(dataset)[:8]
        encoded = [writer.encode(row) for row in rows]
        parts = writer.pages(encoded, prefix, key='records', extra={'dataset': dataset})
        offset = 0
        for part in parts:
            count = part['record_count']
            evidence_parts.append(part)
            for index, row in enumerate(rows[offset:offset+count]):
                locator = {'record_id': row['record_id'], 'dataset_id': row['dataset_id'], 'dataset': row['dataset'],
                           'path': part['path'], 'sha256': part['sha256'], 'record_index': index}
                evidence_locations.append(locator)
                occurrence_refs[row['record_id']] = locator
                raw_ref = {**part, 'record_index': index, 'value_key': 'raw'}
                writer.cache.setdefault(_fingerprint(row['raw']), _node('value', [raw_ref]))
            offset += count
    # Preserve the original evidence ordering, even if sources were interleaved.
    ordered_refs = [{**occurrence_refs[row['record_id']], 'value_key': None}
                    for row in data.get('evidence_records') or []]
    writer.cache[_fingerprint(data.get('evidence_records') or [])] = _node('array', writer.encode(ordered_refs)) if ordered_refs else []
    evidence_locations.sort(key=lambda row: row['record_id'])
    writer.catalog('evidence/index.json', evidence_locations, 'evidence/locators/part', id_key='record_id')

    source_rows = data.get('sources') or []
    source_parts = writer.pages([writer.encode(row) for row in source_rows], 'sources/records/part', key='records')
    writer.cache[_fingerprint(source_rows)] = _node('array', writer.encode(source_parts)) if source_rows else []
    source_locators, offset = [], 0
    for part in source_parts:
        for index, source in enumerate(source_rows[offset:offset+part['record_count']]):
            source_locators.append({'dataset_id': source['dataset_id'], 'dataset': source['dataset'],
                                    'dataset_index': source.get('dataset_index'), 'record_count': source.get('record_count'),
                                    'path': part['path'], 'record_index': index})
        offset += part['record_count']
    source_locators.sort(key=lambda row: row['dataset_id'])
    writer.catalog('sources/index.json', source_locators, 'sources/locators/part', id_key='dataset_id')

    finding_summaries, finding_refs, used = [], [], set()
    for position, finding in enumerate(data.get('findings') or []):
        proposed = _safe_name(finding.get('finding_id') or f'finding-{position+1}')
        name = proposed
        if name.casefold() in used:
            name = _safe_name(name + '-' + _fingerprint([finding.get('finding_uid'), position])[:8])
        used.add(name.casefold())
        path = 'findings/' + name + '.json'
        records = finding.get('records') or []
        writer.array(records, 'findings/records/' + name + '/part', records=True)
        detail = writer.encode(finding)
        ref = writer.document(path, {'format': 'm365-assessment-finding', 'finding_id': finding.get('finding_id'),
                                     'title': finding.get('title'), 'record_count': finding.get('record_count'), 'detail': detail}, encoded_keys=('detail',))
        finding_refs.append({**ref, 'value_key': 'detail', 'record_count': 1})
        writer.cache[_fingerprint(finding)] = _node('value', [{**ref, 'value_key': 'detail'}])
        finding_summaries.append({key: finding.get(key) for key in
                                  ('finding_id', 'finding_uid', 'title', 'domain_id', 'disposition', 'priority',
                                   'record_count', 'record_status')} | {'path': path})
    writer.cache[_fingerprint(data.get('findings') or [])] = _node('array', writer.encode(finding_refs)) if finding_refs else []
    writer.catalog('findings/index.json', finding_summaries, 'findings/catalog/part')

    highlights = data.get('portal_report_highlights') or []
    if highlights:
        writer.catalog('portal/index.json', highlights, 'portal/records/part')
    assessment = data.get('assessment_result') or {}
    summary = {key: data.get(key) for key in ('tenant_id', 'tenant_name', 'evaluation_date', 'generated_at',
                                            'methodology_version', 'evidence_schema_version', 'decision', 'rationale', 'counts')}
    summary['export_counts'] = data.get('export_counts') or {}
    summary['control_results'] = [{key: row.get(key) for key in ('Control ID', 'Control', 'Status', 'Result',
                                                               'Configuration result', 'Operational result', 'Domain',
                                                               'ControlId', 'Title', 'State', 'Reason') if key in row}
                                 for row in data.get('control_results') or []]
    summary['domains'] = [{key: row.get(key) for key in ('domain_id', 'id', 'title', 'action_count', 'status', 'state') if key in row}
                          for row in assessment.get('assessment_domains') or []]
    summary['executive_summary'] = assessment.get('executive_summary') or {}
    if highlights:
        from .portal_insights import QUALIFICATION, featured_highlights
        summary['portal_reports'] = {'highlight_count': len(highlights), 'catalog': 'portal/index.json',
                                     'qualification': QUALIFICATION, 'highlights': featured_highlights(highlights)}
    writer.document('summary.json', summary)
    writer.document('assessment/index.json', {'format': 'm365-json-assessment-tree', 'payload': writer.encode(data),
                                             'reference_path_base': 'package_root'}, encoded_keys=('payload',))
    index = {'format': PACKAGE_FORMAT, 'package_schema_version': PACKAGE_SCHEMA_VERSION,
             'dashboard_schema_version': data.get('dashboard_schema_version'),
             'tenant_id': data.get('tenant_id'), 'tenant_name': data.get('tenant_name'),
             'evaluation_date': data.get('evaluation_date'), 'generated_at': data.get('generated_at'),
             'decision': data.get('decision'), 'counts': data.get('counts') or {},
             'entry_points': {'summary': 'summary.json', 'findings': 'findings/index.json',
                              'sources': 'sources/index.json', 'evidence': 'evidence/index.json', 'assessment': 'assessment/index.json'},
             'limits': {'max_file_bytes': max_bytes, 'max_records_per_page': max_records, 'row_layout': 'one record per line'},
             'totals': {'findings': len(finding_summaries), 'sources': len(source_rows),
                        'evidence_records': len(evidence_locations), 'files': len(writer.files)+1},
             'reference_path_base': 'package_root',
             'reading_order': ['Read summary.json first.', 'Read findings/index.json and the catalog page for the requested finding.',
                               'Read that finding file and follow detail record page references.',
                               'Resolve evidence_record_ids through evidence/index.json locator ranges, then read only the matching evidence page.',
                               'Follow $json_package_node parts for large values; all paths start at this folder.',
                               'assessment/index.json is the lossless shared assessment tree; load only relevant components.']}
    if highlights:
        index['entry_points']['portal_reports'] = 'portal/index.json'
        index['reading_order'].insert(1, 'Read portal/index.json for dated PDF excerpts and source page references; these are review context, not scored findings.')
    writer.document('index.json', index)
    return str(writer.folder / 'index.json')


class _Reader:
    def __init__(self, index):
        self.root = Path(index).resolve().parent
        self.cache, self.checksums, self.active = {}, {}, set()

    def load(self, path, digest=None):
        if not isinstance(path, str) or not path or '\\' in path or Path(path).is_absolute():
            raise ValueError('Invalid JSON package reference path.')
        target = (self.root / path).resolve()
        try:
            target.relative_to(self.root)
        except ValueError:
            raise ValueError('JSON package reference leaves the package folder.') from None
        if path not in self.cache:
            body = target.read_bytes()
            self.checksums[path] = sha256(body).hexdigest()
            self.cache[path] = json.loads(body)
        if digest and self.checksums[path] != digest:
            raise ValueError(f'JSON package reference checksum does not match: {path}')
        return self.cache[path]

    def decode(self, value):
        if isinstance(value, list):
            return [self.decode(item) for item in value]
        if not isinstance(value, dict):
            return value
        if set(value) != {NODE_KEY}:
            return {key: self.decode(item) for key, item in value.items()}
        descriptor = value[NODE_KEY]
        parts, kind = self.decode(descriptor['parts']), descriptor['kind']
        values = []
        for part in parts:
            marker = (part['path'], part.get('record_index'), part.get('value_key'))
            if marker in self.active:
                raise ValueError('Cyclic JSON package reference.')
            self.active.add(marker)
            try:
                document = self.load(part['path'], part.get('sha256'))
                if 'document' in document:
                    document = self.decode(document['document'])
                if 'record_index' in part:
                    selected = document.get('records', document.get('items'))[part['record_index']]
                    selected = self.decode(selected)
                    key = part.get('value_key')
                    values.append(selected[key] if key else selected)
                elif part.get('value_key'):
                    values.append(self.decode(document[part['value_key']]))
                elif kind == 'string':
                    values.append(document['value'])
                elif kind == 'object':
                    values.extend((self.decode(entry['key']), self.decode(entry['value'])) for entry in document['entries'])
                else:
                    values.extend(self.decode(document.get('records', document.get('items'))))
            finally:
                self.active.remove(marker)
        if kind == 'string':
            return ''.join(values)
        if kind == 'object':
            return dict(values)
        if kind == 'value':
            if len(values) != 1:
                raise ValueError('A JSON value reference must resolve exactly one value.')
            return values[0]
        if kind == 'array':
            return values
        raise ValueError(f'Unsupported JSON package node kind: {kind}')


def read_dashboard_package(index):
    """Restore the exact safe dashboard payload; no network or collection calls."""
    reader = _Reader(index)
    manifest = reader.load(Path(index).name)
    if 'document' in manifest:
        manifest = reader.decode(manifest['document'])
    if manifest.get('format') != PACKAGE_FORMAT or manifest.get('package_schema_version') != PACKAGE_SCHEMA_VERSION:
        raise ValueError('Unsupported dashboard JSON package format or version.')
    tree = reader.load(manifest['entry_points']['assessment'])
    if 'document' in tree:
        tree = reader.decode(tree['document'])
    return reader.decode(tree['payload'])


ARCHIVE_READING_GUIDE = '''AI ASSESSMENT UPLOAD — READ FIRST

This ZIP contains the split assessment JSON. Extract it if your builder supports
ZIP uploads. Keep the internal paths intact. Do not load every JSON file at once.

1. Start with index.json, then summary.json.
2. Read findings/index.json to locate the finding you need.
3. Open its finding file (for example findings/ENT-005.json).
4. Follow detail.records references to the relevant record pages only.
5. Resolve evidence_record_ids through evidence/index.json for exact raw rows.
6. Read sources/index.json for original collection dates, windows and limitations.

All JSON reference paths start at the archive root. $json_package_node references
preserve large values through linked parts. Each file is bounded; each record page
contains at most 50 records by default. Missing values and unknowns remain explicit.

The complete assessment can be reconstructed from assessment/index.json without
new collection. All retained findings, native records and timestamps are preserved.
Named tenant details are confidential. Credentials and prompt/response bodies are
excluded from the generated export.

If your builder accepts only individual JSON files, a ZIP upload will not work.
Use its supported file format rather than uploading the full assessment as text.
'''


def write_dashboard_archive(index_path, archive_path=None):
    """Package the reachable split export as a single uploadable ZIP file.

    Validate the component tree and checksums first. Follow only managed node and
    catalog references, so native fields named ``path`` remain ordinary evidence.
    Existing archives and unrelated files in the folder are preserved.
    """
    index = Path(index_path).resolve()
    if archive_path is None:
        if index.parent.name.casefold() == 'json':
            archive_path = index.parent.parent / 'Dashboard JSON.zip'
        else:
            parent = index.parent.parent
            if parent.name.casefold() == 'json':
                parent = parent.parent
            archive_path = parent / (index.parent.name + '_json.zip')
    target = Path(archive_path)
    if target.exists():
        raise FileExistsError(f'The upload archive already exists: {target}')
    if target.suffix.lower() != '.zip':
        raise ValueError('The JSON upload archive path must end in .zip.')
    reader = _Reader(index)
    manifest = reader.decode(reader.load(index.name))
    if 'document' in manifest:
        manifest = manifest['document']
    if manifest.get('format') != PACKAGE_FORMAT or manifest.get('package_schema_version') != PACKAGE_SCHEMA_VERSION:
        raise ValueError('Unsupported dashboard JSON package format or version.')
    tree = reader.load(manifest['entry_points']['assessment'])
    if 'document' in tree:
        tree = reader.decode(tree['document'])
    # This also visits all native rows and shared detail components, verifying
    # their hashes. The result itself does not need to be kept in memory.
    reader.decode(tree['payload'])
    for key, path in manifest['entry_points'].items():
        document = reader.decode(reader.load(path))
        if 'document' in document:
            document = document['document']
        if key not in {'findings', 'sources', 'evidence'}:
            continue
        for part in document.get('pages') or []:
            page = reader.decode(reader.load(part['path'], part.get('sha256')))
            for row in page.get('records') or []:
                # These are managed catalog locators, not native raw attributes.
                if row.get('path'):
                    already_loaded = row['path'] in reader.cache
                    linked = reader.load(row['path'], row.get('sha256'))
                    if not already_loaded:
                        reader.decode(linked)
    members = {}
    for path in sorted(reader.cache):
        if any(segment in {'', '.', '..'} for segment in path.split('/')):
            raise ValueError('JSON archive references must use canonical package paths.')
        resolved = (reader.root / path).resolve()
        if not resolved.is_relative_to(reader.root):
            raise ValueError('JSON package reference leaves the package folder.')
        body = resolved.read_bytes()
        if sha256(body).hexdigest() != reader.checksums[path]:
            raise ValueError(f'JSON package reference checksum does not match: {path}')
        members[path] = body
    members['Read First.txt'] = ARCHIVE_READING_GUIDE.encode('utf-8')
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Exclusive creation prevents overwriting another report, even if it
        # appeared while the package was being validated.
        with target.open('xb') as handle:
            try:
                with ZipFile(handle, 'w', compression=ZIP_DEFLATED, compresslevel=6) as archive:
                    for name, body in members.items():
                        info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                        info.compress_type = ZIP_DEFLATED
                        archive.writestr(info, body, compress_type=ZIP_DEFLATED, compresslevel=6)
            except BaseException:
                # Only remove our newly created, unfinished archive.
                handle.close()
                target.unlink(missing_ok=True)
                raise
    except FileExistsError:
        raise
    return str(target)

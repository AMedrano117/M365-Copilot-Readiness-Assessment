"""Shared selection and lineage of retained native evidence for Excel and HTML.

Source, native-row and detail identifiers preserve the existing compatibility
algorithm. This is an internal evidence model, not an assessment output contract.
"""

from collections.abc import Mapping
from datetime import datetime, timezone
from hashlib import sha256
import json
import re

from .evidence_records import expand_finding_records
from .customer_report import _heading
from .raw_evidence import KEY_SOURCES, safe_record
from .portal_insights import report_highlights


from .assessment_serialization import plain_data as _plain


def _fingerprint(value):
    return json.dumps(_plain(value), sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _id(prefix, *values):
    return prefix + sha256(_fingerprint(values).encode('utf-8')).hexdigest()[:24]


def _dataset_id(tenant_id, name, index, source):
    return _id('SRC-', tenant_id, name, index, source)


def _evidence_id(tenant_id, dataset_id, position, row):
    return _id('EVD-', tenant_id, dataset_id, position, row)


def evidence_record_ids(sources, tenant_id):
    """Map (dataset, dataset index, record index) to the deterministic EVD- identifier.

    The registry below and the workbook's raw source sheets both use this, so an
    evidence ID shown in any deliverable resolves to the same retained native row.
    """
    identifiers = {}
    for name, datasets in _plain(sources or {}).items():
        for index, dataset in enumerate(datasets or []):
            dataset = dataset if isinstance(dataset, Mapping) else {}
            dataset_id = _dataset_id(tenant_id, name, index, _plain(dataset.get('source') or {}))
            for position, row in enumerate(_plain(dataset.get('records') or [])):
                identifiers[(name, index, position)] = _evidence_id(tenant_id, dataset_id, position, row)
    return identifiers


def _native_id(row):
    if not isinstance(row, dict):
        return None
    names = {re.sub('[^a-z0-9]', '', key.lower()): value for key, value in row.items()}
    # Event and object identifiers come before related device identifiers, so a
    # flattened sign-in row is never keyed by the device that made the request.
    return next((names[key] for key in ('id', 'signinid', 'eventid', 'objectid', 'recordid', 'incidentid',
                                        'machineid', 'deviceid', 'itemid')
                 if names.get(key) not in (None, '', 'Not returned')), None)


class _Registry:
    def __init__(self, tenant_id, sources):
        self.tenant_id = tenant_id
        self.datasets = sources
        self.sources, self.evidence = [], []
        self.refs, self.fingerprints = {}, {}
        for name, datasets in sources.items():
            for index, dataset in enumerate(datasets):
                self.add(name, index, dataset)

    def add(self, name, index, dataset):
        source = _plain(dataset.get('source') or {})
        records = _plain(dataset.get('records') or [])
        dataset_id = _dataset_id(self.tenant_id, name, index, source)
        self.sources.append({'dataset_id': dataset_id, 'dataset': name,
                             'dataset_index': index, 'source': source, 'record_count': len(records)})
        for position, row in enumerate(records):
            record_id = _evidence_id(self.tenant_id, dataset_id, position, row)
            self.evidence.append({'record_id': record_id, 'dataset_id': dataset_id,
                                  'dataset': name, 'source_record_index': position,
                                  'source_record_id': _native_id(row), 'raw': row})
            ref = {'dataset': name, 'dataset_index': index, 'record_index': position}
            self.refs[(name, index, position)] = record_id
            self.fingerprints.setdefault(_fingerprint(row), []).append(ref)

    def declared(self, name, records, source):
        # Store selected/derived declarations independently when they do not
        # reproduce a native row. Their origin and granularity remain explicit.
        datasets = self.datasets.setdefault(name, [])
        index = len(datasets)
        dataset = {'source': _plain(source), 'records': _plain(records)}
        datasets.append(dataset)
        self.add(name, index, dataset)
        return [{'dataset': name, 'dataset_index': index, 'record_index': position}
                for position in range(len(records))]

    def link(self, records):
        for record in records:
            refs = record.get('source_refs') or []
            identifiers = [self.refs[(ref['dataset'], ref['dataset_index'], ref['record_index'])]
                           for ref in refs]
            record['evidence_record_ids'] = list(dict.fromkeys(identifiers))


def _record(row, refs, *, qualification=None):
    fields = _plain(row) if isinstance(row, Mapping) else {'value': _plain(row)}
    statuses = {key: ({'status': 'unavailable', 'reason': 'The source did not retain a value.'}
                     if value is None else {'status': 'available'})
                for key, value in fields.items()}
    result = {'record_id': str(_native_id(fields) or _id('ROW-', fields, refs)),
              'fields': fields, 'field_status': statuses, 'source_refs': refs}
    if qualification:
        result['qualifications'] = [qualification]
    return result


def evidence_keys(finding):
    """EvidenceKey values are joined with '; ' when findings merge; compare trimmed keys."""
    return [key.strip() for key in str(finding.get('EvidenceKey') or '').split(';') if key.strip()]


def _source_names(finding):
    names = [name for key in evidence_keys(finding) for name in KEY_SOURCES.get(key, [])]
    extra = {'standing_access_detail': ['role_assignment_schedules', 'role_assignments', 'role_definitions', 'users'],
             'risky_user_detail': ['risky_users', 'risk_detections'],
             'sharepoint_governance_detail': ['sharepoint_site_settings', 'sharepoint_tenant_settings', 'sharepoint_graph_settings'],
             'license_assignment_detail': ['license_users', 'licenses', 'users']}
    for key in evidence_keys(finding):
        names.extend(extra.get(key, []))
    text = ' '.join(str(finding.get(key) or '') for key in ('ControlId', 'FindingKey', 'Feature', 'Observation')).lower()
    if 'purview_policy_detail' in str(finding.get('EvidenceKey') or ''):
        selected = (['audit_config', 'copilot_audit'] if 'audit' in text else
                    ['retention_policies', 'retention_labels'] if 'retention' in text else
                    ['dlp_policies', 'dlp_rules', 'dlp_alerts'] if 'dlp' in text or 'data loss prevention' in text else
                    ['sensitivity_labels', 'label_policies', 'label_coverage'] if 'label' in text or 'classification' in text else
                    ['irm_config'] if 'rights management' in text or 'azure-rms' in text else names)
        names = selected
    return list(dict.fromkeys(names))


def _from_declared(finding, registry):
    declaration = finding.get('InvestigationEvidence')
    if not isinstance(declaration, dict):
        return None
    kind = declaration.get('kind', 'records')
    reason = declaration.get('reason') or finding.get('InvestigationQualification') or ''
    if kind in {'absence', 'unavailable', 'planning'}:
        return {'records': [], 'record_type': kind, 'selection': declaration.get('selection') or reason,
                'limitations': [reason or 'The evidence declaration contains no individual records.'], 'status': kind}
    rows = declaration.get('records') or []
    if not rows:
        return None
    source = dict(declaration.get('source') or {}, evidence_origin='finding_declaration',
                  observed_at=finding.get('ObservationDate') or finding.get('ObservedAt'), selection=declaration.get('selection') or reason)
    unresolved = [row for row in rows if _fingerprint(row) not in registry.fingerprints]
    declared_refs = registry.declared('declared.' + str(finding.get('RecommendationId')), unresolved, source) if unresolved else []
    remaining = iter(declared_refs)
    records = [_record(row, registry.fingerprints.get(_fingerprint(row)) or [next(remaining)])
               for row in rows]
    return {'records': records, 'record_type': 'configuration' if kind == 'configuration' else 'selected_records',
            'selection': reason or 'Individual records retained by the finding producer.',
            'limitations': [], 'reconciliation': _plain(declaration.get('reconciliation') or {})}


_RANGE = re.compile(r"^'((?:[^']|'')+)'![A-Z]+(\d+):[A-Z]+(\d+)$")


def _workbook_rows(finding, bundle):
    sheets = {sheet.get('title'): sheet for sheet in (bundle.get('sheets') or {}).values()}
    ranges = finding.get('InvestigationRanges') or [finding.get('InvestigationRange')]
    rows, seen = [], set()
    def read(location, depth=0):
        match = _RANGE.fullmatch(str(location or ''))
        if not match or depth > 2:
            return
        title = match[1].replace("''", "'")
        sheet = sheets.get(title) or {}
        for position in range(max(0, int(match[2])-2), min(len(sheet.get('rows') or []), int(match[3])-1)):
            marker = (title, position)
            if marker in seen:
                continue
            seen.add(marker)
            row = sheet['rows'][position]
            # Shared worklist rows list several findings as "A; B".
            owners = {part.strip() for part in str(row.get('RecommendationId') or '').split(';') if part.strip()}
            if owners and finding.get('RecommendationId') not in owners:
                continue
            if title == 'Investigation Items' and row.get('Source Detail'):
                read(row['Source Detail'], depth+1)
            else:
                raw = row.get('Raw record')
                if isinstance(raw, str):
                    pieces = [raw]
                    number = 2
                    while f'Raw record (continued {number})' in row:
                        pieces.append(row[f'Raw record (continued {number})'])
                        number += 1
                    try:
                        raw = json.loads(''.join(pieces))
                    except (ValueError, TypeError):
                        raw = None
                rows.append((raw if isinstance(raw, dict) else row, location, bool(raw)))
    for location in ranges:
        read(location)
    return rows


def _generic(finding, bundle, registry):
    declared = _from_declared(finding, registry)
    if declared is not None:
        return declared
    rows = _workbook_rows(finding, bundle)
    if rows:
        records, derived, positions = [], [], []
        for row, location, native in rows:
            refs = registry.fingerprints.get(_fingerprint(row))
            if refs:
                records.append(_record(row, refs))
            else:
                positions.append(len(records))
                derived.append(row)
                records.append(_record(row, [], qualification='Retained investigation detail; see source metadata for its workbook origin.'))
        if derived:
            refs = registry.declared('investigation.' + str(finding.get('RecommendationId')), derived,
                                    {'evidence_origin': 'materialized_investigation', 'granularity': 'investigation_detail',
                                     'workbook_ranges': list(dict.fromkeys(location for _, location, _ in rows)),
                                     'observed_at': finding.get('ObservationDate') or finding.get('ObservedAt'),
                                     'qualification': 'Retained investigation detail is distinct from a native API response.'})
            for position, ref in zip(positions, refs):
                records[position]['source_refs'] = [ref]
        return {'records': records, 'record_type': 'supporting_detail',
                'selection': finding.get('InvestigationQualification') or 'Retained rows selected by the finding investigation contract.',
                'limitations': ['Supporting inventory is not an affected population.' ] if finding.get('InvestigationSummary') == 'Shared source inventory' else []}
    records, names = [], _source_names(finding)
    for name in names:
        for dataset_index, dataset in enumerate(registry.datasets.get(name, [])):
            for record_index, row in enumerate(dataset.get('records') or []):
                records.append(_record(row, [{'dataset': name, 'dataset_index': dataset_index, 'record_index': record_index}],
                                       qualification='Supporting inventory; this record is not asserted to be an affected entity.'))
    return {'records': records, 'record_type': 'supporting_inventory', 'source_names': names,
            'selection': 'Retained source inventory supporting this finding; no affected subset is asserted.',
            'limitations': ['Inventory rows do not establish an affected population or effective operation.'] if records else []}


def _availability(names, sources):
    datasets = [dataset for name in names for dataset in sources.get(name, [])]
    if not datasets:
        return 'unavailable'
    states = [dataset.get('source') or {} for dataset in datasets]
    if all((state.get('complete') is True and state.get('available') is not False
            and state.get('availability_status') in {None, 'available', 'empty'} and not state.get('truncated')) for state in states):
        return 'absence'
    return 'unavailable'


def build_evidence_selection(result, bundle, *, tenant_name=None, generated_at=None):
    """Select retained evidence without evaluating controls or changing the shared result."""
    sources = _plain(bundle.get('assessment_sources') or {})
    tenant_id = result.get('tenant_id') or bundle.get('expected_tenant_id')
    registry = _Registry(tenant_id, sources)
    enriched, findings = [], []
    for original in result.get('recommendations') or []:
        row = _plain(original)
        identifier = row.get('RecommendationId')
        investigation = str(row.get('InvestigationStatus') or '').lower()
        historical = str(row.get('Historical') or '').lower() in {'yes', 'true'} or 'historical' in investigation
        planning = 'planning' in investigation or row.get('FindingKey') == 'planning'
        specialized = False
        if historical or planning:
            status = 'historical' if historical else 'planning'
            detail = {'records': [], 'record_type': status, 'status': status,
                      'selection': 'Historical evidence only.' if historical else 'Assessment planning input.',
                      'limitations': [row.get('InvestigationQualification') or
                                     ('Historical summaries cannot recreate named records.' if historical else 'A planning decision does not assert an affected record population.')]}
        else:
            detail = expand_finding_records(row, sources, evaluation_date=result.get('evaluation_date'), tenant_id=tenant_id)
            specialized = detail.get('record_type') != 'unsupported'
            if not specialized:
                detail = _generic(row, bundle, registry)
        records = _plain(detail.get('records') or [])
        registry.link(records)
        for occurrence, record in enumerate(records):
            original_id = record['record_id']
            record['compatibility_record_id'] = original_id
            record['source_record_id'] = (None if re.fullmatch(r'(?:ROW-|record-)[a-f0-9]{24}', str(original_id))
                                          else original_id)
            record['record_id'] = _id('DET-', tenant_id, identifier, occurrence,
                                      record['evidence_record_ids'], record['fields'])
        names = list(dict.fromkeys([ref['dataset'] for record in records for ref in record.get('source_refs') or []]
                                  + detail.get('source_names', []) + _source_names(row)))
        limitations = list(detail.get('limitations') or [])
        for name in names:
            for dataset in sources.get(name, []):
                source = dataset.get('source') or {}
                if source.get('complete') is False or source.get('truncated') or source.get('availability_status') not in {None, 'available', 'empty'}:
                    limitations.append(f"{name}: incomplete source ({source.get('availability_status') or 'unknown'}); {source.get('reason') or 'coverage is not established'}.")
                for field in ('partial_errors', 'errors', 'limitations'):
                    values = source.get(field) or []
                    if not isinstance(values, list):
                        values = [values]
                    limitations.extend(f'{name}: {value}' for value in values)
        status = detail.get('status') or ('records_available' if records else _availability(names, sources))
        if records and ('summary evidence' in investigation or
                        all(record['fields'].get('recordGranularity') == 'site_summary' for record in records)):
            status = 'summary_only'
            limitations.append('The retained rows are summaries. Their counts cannot recreate individual users, devices, or files.')
        elif records and not specialized and (detail.get('record_type') == 'supporting_inventory' or
                          row.get('InvestigationSummary') == 'Shared source inventory' or
                          ('unavailable' in investigation and not row.get('InvestigationEvidence'))):
            status = 'supporting_context'
            limitations.append('These are supporting inventory records; the individual affected population or missing operational proof remains unestablished.')
        if not records:
            limitations.append(row.get('InvestigationQualification') or
                               ('A complete retained source returned no matching records; aggregate conclusions cannot create individual rows.' if status == 'absence' else
                                'Individual supporting rows were not retained or collection coverage is incomplete.'))
        reconciliation = dict(detail.get('reconciliation') or {})
        reconciliation.update({'exported_record_count': len(records),
                               'count_unit': 'exported detail rows',
                               'qualification': 'Detail rows may overlap or describe grants, settings, and summary metrics; they are not a unique affected-entity count.'})
        evidence_ids = list(dict.fromkeys(evidence_id for record in records for evidence_id in record['evidence_record_ids']))
        row.update({'records': records, 'record_count': len(records), 'record_status': status,
                    'record_type': detail.get('record_type'), 'record_selection': detail.get('selection') or '',
                    'record_limitations': list(dict.fromkeys(str(value) for value in limitations if value)),
                    'record_reconciliation': reconciliation, 'evidence_record_ids': evidence_ids})
        enriched.append(row)
        finding = {'finding_id': identifier,
                   'title': _heading(row), 'domain_id': row.get('AssessmentDomainId') or row.get('DomainId'), 'control_id': row.get('ControlId'),
                   'service': row.get('Service'), 'disposition': row.get('Disposition'), 'priority': row.get('Priority'),
                   'observation': row.get('Observation'), 'recommendation': row.get('Recommendation'),
                   'readiness_effect': row.get('PilotImpact') or row.get('ReadinessEffect'),
                   'owner_role': row.get('OwnerRole'), 'observed_at': row.get('ObservationDate') or row.get('ObservedAt'),
                   'evidence_level': row.get('EvidenceLevel'), 'operational_result': row.get('OperationalResult'),
                   'historical': historical}
        finding.update({key: row[key] for key in ('records', 'record_count', 'record_status', 'record_type', 'record_selection',
                                                 'record_limitations', 'record_reconciliation', 'evidence_record_ids')})
        findings.append(finding)
    return _plain({
        'tenant_id': tenant_id, 'tenant_name': tenant_name,
        'evaluation_date': result.get('evaluation_date'),
        'generated_at': generated_at or datetime.now(timezone.utc).isoformat(),
        'methodology_version': result.get('methodology_version'),
        'decision': result.get('decision'), 'rationale': result.get('rationale'),
        'counts': result.get('counts') or {}, 'findings': findings,
        'recommendations': enriched, 'sources': registry.sources,
        'evidence_records': registry.evidence,
        'portal_report_highlights': report_highlights(bundle.get('portal_review')),
    })

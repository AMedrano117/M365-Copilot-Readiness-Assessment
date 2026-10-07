"""Workbook evidence sheets and technical recommendations from the shared finding-evidence model.

Evidence sheets contain exactly the rows exported to App Builder and the HTML evidence
pages. Each finding's rows form one contiguous block per table; the ranges are stored on the
finding, so the existing range-validated hyperlinks and sheet splitting apply to it.
"""

from collections import OrderedDict

from .finding_evidence import _CONCERN_INFO

RESERVED_TITLES = {'Start Here', 'Action Plan', 'Findings Register', 'Assessment Coverage', 'Investigation Items',
                   'Technical Recommendations', 'Recommendations', 'Evidence Index', 'Collection Coverage',
                   'Findings', 'Coverage', 'Configuration', 'Devices', 'Users & Identity', 'Apps & Consent',
                   'Sites & Sharing', 'Other Evidence', 'Findings Lineage', 'Raw Derived Records', 'PDF Highlights',
                   'Control Results', 'Run Manifest', 'Integrity Checks'}


def _title(base, used):
    title = base[:31].rstrip()
    original, suffix = title, 2
    while title.casefold() in used:
        ending = f' {suffix}'
        title = original[:31 - len(ending)].rstrip() + ending
        suffix += 1
    used.add(title.casefold())
    return title


def add_evidence_sheets(bundle, result, model, *, html_folder=None, app_builder_files=None):
    """Add selected-record evidence sheets to the workbook model and annotate result rows."""
    from .investigation_details import _ref
    from .html_evidence_pages import page_name
    sheets = bundle.setdefault('sheets', {})
    for key in [key for key in sheets if key.startswith('finding_evidence.')]:
        del sheets[key]
    used = {str(sheet.get('title', '')).casefold() for sheet in sheets.values()} | {title.casefold() for title in RESERVED_TITLES}
    rows_by_id = {row.get('RecommendationId'): row for row in result.get('recommendations') or []}
    for row in rows_by_id.values():
        row.pop('EvidenceRecordsRange', None)
        row.pop('EvidenceRecordsRanges', None)
    for table_id, table in model['tables'].items():
        if table['role'] != 'selected' or not table['rows']:
            continue
        short = _CONCERN_INFO[table['concern_id']][2]
        title = _title(f"Evidence {short} {table['short_title']}", used)
        table['sheet_title'] = title
        rows = [OrderedDict((column, row.get(column)) for column in table['columns']) for row in table['rows']]
        sheets['finding_evidence.' + table_id] = {
            'title': title, 'rows': rows, 'restricted': True, 'preview_columns': [],
            'summary': f"{table['title']}. One row per detail record (detail_record_id); evidence_record_ids link to the Raw "
                       'sheets. The same record IDs are retained when optional evidence exports are requested.'}
        width = len(table['columns'])
        blocks = OrderedDict()
        for number, row in enumerate(rows, 2):
            blocks.setdefault(row['finding_id'], []).append(number)
        for finding_id, numbers in blocks.items():
            location = _ref(title, numbers[0], numbers[-1], width)
            target = rows_by_id.get(finding_id)
            if target is not None:
                target.setdefault('EvidenceRecordsRanges', []).append(location)
                # The primary link opens the finding's largest block; every block stays listed.
                if len(numbers) > target.get('_largest_evidence_block', 0):
                    target['_largest_evidence_block'] = len(numbers)
                    target['EvidenceRecordsRange'] = location
    for row in rows_by_id.values():
        row.pop('_largest_evidence_block', None)
    for finding in model['findings']:
        row = rows_by_id.get(finding['finding_id'])
        if row is None:
            continue
        evidence = finding['evidence']
        row.update({'EvidenceKind': evidence['kind'], 'EvidenceAvailability': evidence['availability'],
                    'DetailRecords': evidence['record_count'], 'RecordUnit': evidence['record_unit'],
                    'AffectedEntities': evidence['affected_entity_count'], 'EntityUnit': evidence['entity_unit'],
                    'ContextRecords': evidence['context_record_count'],
                    'TechnicalGuidance': finding.get('recommendation_detail') or {},
                    'AppBuilderFiles': '; '.join((app_builder_files or {}).get(finding['finding_id'], []))})
    # Page paths include the build's file stem, so they stay out of the shared assessment result.
    bundle['technical_evidence_pages'] = {finding['finding_id']: f"{html_folder}/{page_name(finding['finding_id'])}"
                                          for finding in model['findings']} if html_folder else {}
    return model


def sync_workbook_locations(result, model):
    """Copy final (possibly split-remapped) evidence ranges back to the model for the HTML pages."""
    rows_by_id = {row.get('RecommendationId'): row for row in result.get('recommendations') or []}
    for finding in model['findings']:
        row = rows_by_id.get(finding['finding_id']) or {}
        finding.setdefault('workbook', {})
        finding['workbook']['evidence_range'] = row.get('EvidenceRecordsRange') or None
        finding['workbook']['evidence_ranges'] = list(row.get('EvidenceRecordsRanges') or [])
        finding['workbook']['investigation_range'] = row.get('InvestigationRange') or None
        finding['workbook']['assessment_evidence_range'] = row.get('AssessmentEvidenceRange') or None
        finding['workbook']['assessment_evidence_ranges'] = list(row.get('AssessmentEvidenceRanges') or [])
        finding['workbook']['technical_evidence_ranges'] = list(row.get('TechnicalEvidenceRanges') or [])
    return model


def technical_recommendation_rows(result, heading=None):
    """One row per actionable finding with its structured technical fix."""
    rows = []
    order = {'Action': 0, 'Coverage': 1}
    priority = {'Critical': 0, 'High': 1, 'Medium': 2, 'Low': 3}
    candidates = [row for row in result.get('recommendations') or [] if row.get('Disposition') in order]
    for row in sorted(candidates, key=lambda item: (order[item['Disposition']], priority.get(item.get('Priority'), 4),
                                                    str(item.get('RecommendationId')))):
        detail = row.get('TechnicalGuidance') or {}
        prerequisites = detail.get('prerequisites') or {}
        links = [link for link in detail.get('links') or [] if link.get('url')]
        entry = OrderedDict([
            ('RecommendationId', row.get('RecommendationId')), ('Finding', heading(row) if heading else row.get('Feature')),
            ('Priority', row.get('Priority')), ('Disposition', row.get('Disposition')),
            ('Guidance status', detail.get('guidance_status') or 'admin-center location not verified'),
            ('What to change', detail.get('change') or row.get('Recommendation')),
            ('Where to configure', detail.get('where')), ('Roles', '; '.join(prerequisites.get('roles') or [])),
            ('Licensing', '; '.join(prerequisites.get('licensing') or [])),
            ('Other prerequisites', '; '.join(prerequisites.get('other') or [])),
            ('How to verify', detail.get('verify')), ('Evidence needed', detail.get('evidence_needed')),
        ])
        for index in range(3):
            link = links[index] if index < len(links) else {}
            entry[f'Documentation {index + 1}'] = link.get('url')
            entry[f'Documentation {index + 1} title'] = link.get('title')
        entry['Verified on'] = detail.get('verified_on')
        entry['Original recommendation'] = row.get('Recommendation')
        rows.append(entry)
    return rows

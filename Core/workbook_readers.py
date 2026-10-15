"""Reader projections of retained workbook records; no assessment decisions."""
from collections import defaultdict
from copy import copy
from math import ceil
from pathlib import Path
import json
import re

CONFIGURATION_COLUMNS = ('Setting', 'Applies to / named locations', 'Current configuration',
    'Recommended configuration', 'Assessment', 'Group size / objects assessed', 'Observed', 'Source detail')
ACTION_COLUMNS = ('Priority', 'Pilot impact', 'Issue / decision', 'Next step',
    'Responsible role', 'Due date', 'Status', 'Source record')
TOPIC_COLUMNS = ('Assessment topic', 'Evidence', 'Configuration', 'Operation',
    'Next evidence / interpretation', 'Source record', 'Owner')
CONTROL_COLUMNS = ('Readiness control', 'Evidence', 'Configuration', 'Operation',
    'Overall result', 'Source record', 'Owner')

STATES = {'available': 'Available', 'collected': 'Available', 'reviewed': 'Available',
    'missing': 'Missing', 'unavailable': 'Missing', 'incomplete': 'Incomplete',
    'partial': 'Incomplete', 'truncated': 'Incomplete', 'not_established': 'Not established',
    'supported': 'Supported', 'not_applicable': 'Not applicable', 'not_assessed': 'Not assessed',
    'unknown': 'Unknown', 'fail': 'Fail', 'pass': 'Pass', 'conflict': 'Conflicting evidence', 'stale': 'Stale'}
ASSESSMENTS = {'Meets': 'Meets recommendation', 'Change': 'Change recommended', 'Review': 'Needs validation'}
SOURCE_LABELS = {'licenses': 'License inventory', 'sensitivity_labels': 'Sensitivity label definitions',
    'label_policies': 'Label publication policies', 'audit_config': 'Audit configuration',
    'copilot_audit': 'Copilot interaction audit records', 'context': 'Assessment scope and owner review',
    'sharepoint_site_settings': 'Site configuration', 'sharepoint_tenant_settings': 'Tenant SharePoint configuration',
    'sharepoint_graph_settings': 'Tenant SharePoint configuration', 'ca_policies': 'Conditional Access policies'}


def state_label(value):
    if value is None or value == '':
        return None
    return STATES.get(str(value).strip().lower().replace(' ', '_'), str(value))


def _text(value):
    return '' if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) if isinstance(value, (dict, list, tuple)) else str(value)


def configuration_rows(rows, details):
    """Join exactly CFG -> retained membership, never setting/finding names."""
    members = defaultdict(list)
    for member in details:
        if member.get('Group ID'):
            members[member['Group ID']].append(member)
    output = []
    for row in rows:
        retained = members.get(row.get('Group ID'), [])
        scope = row.get('Scope')
        count = None
        if scope == 'tenant':
            key = next((m.get('Configuration key') for m in retained if m.get('Configuration key')), None)
            applies = {'SharingCapability': 'Tenant-level SharePoint sharing setting',
                'OneDriveSharingCapability': 'Tenant-level OneDrive sharing setting',
                'DefaultSharingLinkType': 'Tenant-level default sharing link'}.get(key,
                'Tenant-level ' + str(row.get('Setting') or 'configuration setting'))
            objects = {str(member['Object ID']) for member in retained if member.get('Object ID')}
            if objects:
                count = f'{len(objects)} tenant objects in this configuration-value group'
                if row.get('Total objects assessed') is not None:
                    count += f" / {row['Total objects assessed']} distinct objects assessed"
        elif scope == 'site':
            objects = defaultdict(list)
            for member in retained:
                if member.get('Object ID') not in (None, ''):
                    objects[str(member['Object ID'])].append(member)
            identified = []
            for identifier, occurrences in objects.items():
                ordered = sorted(occurrences, key=lambda m: (not bool(m.get('Object name')), not bool(m.get('Locator')),
                    _text(m.get('Object name')).casefold(), _text(m.get('Locator')), identifier))
                member = ordered[0]
                name = member.get('Object name') or member.get('Locator') or identifier
                identified.append((str(name), str(member.get('Locator') or identifier), identifier))
            names = defaultdict(set)
            for name, locator, identifier in identified:
                names[name.casefold()].add(identifier)
            labels = [name + (' [' + locator + ']' if len(names[name.casefold()]) > 1 else '')
                      for name, locator, identifier in sorted(identified, key=lambda item: (item[0].casefold(), item[1], item[2]))]
            applies = '\n'.join(labels) if len(labels) <= 5 else f'{len(labels)} sites; examples only: ' + '; '.join(labels[:3])
            if not applies:
                applies = 'Site identities not available'
            elif any(not m.get('Object ID') for m in retained):
                applies += '\nAdditional site identities not available'
            if objects:
                denominator = row.get('Total objects assessed')
                count = f'{len(objects)} objects in this configuration-value group'
                if denominator is not None:
                    count += f' / {denominator} distinct objects assessed'
        else:
            applies = row.get('Object name') or row.get('Locator') or row.get('Object ID') or 'Entity identity not available'
            if row.get('Object ID'):
                count = '1 identified object observed'
        primary = dict(zip(CONFIGURATION_COLUMNS, (row.get('Setting'), applies, row.get('Current'),
            row.get('Recommended'), ASSESSMENTS.get(row.get('Status'), row.get('Status')), count,
            row.get('Observed'), 'Full membership and source detail' if retained else 'Configuration and source detail')))
        # Compatibility fields remain filterable but hidden on the reading surface.
        primary.update({k: v for k, v in row.items() if k not in primary})
        output.append(primary)
    return output


def evidence_discrepancies(result):
    """Explicit control/topic mappings flag apparent conflicts without closing gaps."""
    mappings = {'LICENSE.ASSIGNMENT': ('SCOPE.LICENSES', 'licenses'),
        'DATA.AUDIT': ('GOVERNANCE.AUDIT', 'audit_config'),
        'DATA.LABELS': ('CLASSIFICATION.LABELS', 'sensitivity_labels')}
    topics = {row.get('Check ID'): row for row in result.get('domain_coverage') or []}
    output = {}
    for gap in result.get('recommendations') or []:
        pair = mappings.get(gap.get('ControlId'))
        if not pair or not (gap.get('Disposition') == 'Coverage' or str(gap.get('FindingKey', '')).startswith('coverage.')):
            continue
        topic = topics.get(pair[0])
        if not topic or pair[1] not in (topic.get('Required sources') or []):
            continue
        sources = [source for source in topic.get('Source metadata') or [] if source.get('dataset') == pair[1]
            and source.get('availability_status') in {'available', 'partial', 'empty'}
            and (not source.get('tenant_id') or str(source['tenant_id']).lower() == str(result.get('tenant_id')).lower())]
        if not sources:
            continue
        missing_claim = gap.get('EvidenceAvailable') == 'No' or bool(re.search(r'not collected|missing|unavailable', _text(gap.get('Observation')), re.I))
        if not missing_claim:
            continue
        note = ('Evidence reconciliation required. The recorded gap and collected ' + SOURCE_LABELS[pair[1]].lower() +
            ' need a scope, observation-date, tenant/source and control-requirement comparison. '
            'Collected evidence alone does not resolve the recorded readiness result.')
        output[gap['RecommendationId']] = {'note': note, 'topic_id': pair[0], 'control_id': gap.get('ControlId'),
            'gap_reference': gap['RecommendationId'], 'collected_reference': pair[0], 'sources': sources,
            'gap_scope': gap.get('EvidenceScope') or gap.get('Scope'), 'gap_observed': gap.get('ObservationDate'),
            'gap_tenant': gap.get('TenantId'), 'collected_dates': topic.get('Evidence dates')}
    return output


def next_step(action, finding):
    """Prefer authored concise text; retain conditions and count-based warnings."""
    full = _text(action.get('Recommendation') or finding.get('Recommendation'))
    concise = _text(action.get('CustomerAction') or finding.get('CustomerAction'))
    if not concise or len(concise) >= len(full) or not full:
        return full or concise or None
    # Conditions are retained alongside an authored action. Procedures remain in detail.
    guard = re.compile(r'\b(if|unless|only|before|after|except|scope|pilot|restrict|never|do not|don.t|avoid|warning|stricter|counts?|revoke)\b', re.I)
    conditions = [part.strip() for part in re.split(r'(?<=[.!?])\s+|\n+', full) if guard.search(part)]
    return ' '.join(dict.fromkeys([concise, *conditions]))


def action_rows(actions, findings, legacy, discrepancies):
    by_id = defaultdict(list)
    for finding in findings:
        by_id[finding.get('RecommendationId')].append(finding)
    presentation, details = [], []
    for index, action in enumerate(actions):
        matches = by_id.get(action.get('RecommendationId'), [])
        finding = matches[0] if len(matches) == 1 else {}
        previous = dict(legacy[index])
        effect = finding.get('PilotImpact') if finding else None
        detail = dict(previous)
        detail.update({'Pilot impact': effect, 'Original action record': action,
            'Original finding record': finding or None, 'Full recommendation': action.get('Recommendation') or finding.get('Recommendation'),
            'Finding relationship': 'Exact RecommendationId' if finding else 'Unresolved: missing or ambiguous finding',
            'Source status': action.get('Status'), 'Reconciliation': discrepancies.get(action.get('RecommendationId'))})
        detail.update({k: v for k, v in action.items() if k not in detail})
        details.append(detail)
        step = next_step(action, finding)
        discrepancy = discrepancies.get(action.get('RecommendationId'))
        if discrepancy:
            step = 'Evidence reconciliation required. ' + (step or 'Compare the gap with the retained evidence and confirm scope and dates.')
        primary = dict(zip(ACTION_COLUMNS, (action.get('Priority', finding.get('Priority')), effect,
            previous.get('Finding'), step, action.get('OwnerRole') or finding.get('OwnerRole'),
            action.get('DueDate') or action.get('TargetDate') or action.get('Target Date') or None,
            action.get('ActionStatus') or action.get('WorkflowStatus') or action.get('Status') or None,
            f'Action {index + 1}: full recommendation and evidence')))
        primary.update({k: v for k, v in previous.items() if k not in primary})
        primary['InvestigationNote'] = action.get('InvestigationNote') or finding.get('InvestigationNote')
        primary['_detail_row'] = index + 2
        presentation.append(primary)
    impacts = {'blocks pilot': 0, 'pilot blocker': 0, 'confirm before pilot': 1, 'required validation': 1,
        'validation required': 1, 'planning': 2, 'pilot planning': 2, 'planning item': 2, 'planning input': 2}
    priorities = {'Critical': 0, 'High': 1, 'Medium': 2, 'Low': 3}
    presentation.sort(key=lambda row: (impacts.get(str(row.get('Pilot impact') or '').lower(), 3),
        priorities.get(row.get('Priority'), 4), str(row.get('RecommendationId') or ''), row['_detail_row']))
    return presentation, details


def _missing_text(row):
    text = _text(row.get('Missing evidence'))
    for source in sorted(row.get('Required sources') or [], key=len, reverse=True):
        label = SOURCE_LABELS.get(source, str(source).replace('_', ' ').capitalize())
        text = re.sub(r'(?<![\w])' + re.escape(source) + r'(?![\w])', lambda _: label, text)
    return text


def coverage_rows(result, bundle, discrepancies):
    topics, controls, details, topic_positions = [], [], [], {}
    for row in result.get('domain_coverage') or []:
        identifier = row.get('Check ID')
        interpretation = _missing_text(row) or row.get('Conclusion effect') or 'Review the retained evidence and its qualifications.'
        if not row.get('Owner'):
            interpretation += ' Owner assignment required.'
        metadata = row.get('Source metadata') or []
        identities = sorted({_text(source.get('scope')) for source in metadata if source.get('scope')})
        if identities:
            interpretation += ' Retained scope: ' + '; '.join(identities) + '.'
        named = sorted({_text(source.get('object_name') or source.get('locator') or source.get('object_id'))
                        for source in metadata if source.get('object_name') or source.get('locator') or source.get('object_id')})
        if named:
            interpretation += ' Retained identities: ' + '; '.join(named) + '.'
        details.append({'Row type': 'Assessment topic', 'Source ID': identifier, 'Original record': row,
            'Required sources': row.get('Required sources'), 'Source metadata': metadata, 'Interpretation': interpretation})
        topic_positions[identifier] = len(details) + 1
        topics.append(dict(zip(TOPIC_COLUMNS, (row.get('Check') or row.get('Description'), state_label(row.get('State')),
            state_label(row.get('Configuration')) or 'Not a control result',
            state_label(row.get('Operational result')) or 'Not a control result', interpretation,
            f'Topic {len(topics) + 1}: evidence and scope', row.get('Owner')))))
    recs = defaultdict(list)
    for row in result.get('recommendations') or []:
        recs[row.get('RecommendationId')].append(row)
    for row in bundle.get('control_results') or result.get('control_results') or []:
        identifier = row.get('Control ID')
        linked = [rec for key in str(row.get('Recommendation IDs') or '').split(';')
                  for rec in recs.get(key.strip(), [])]
        candidates = [value for value in discrepancies.values() if value['control_id'] == identifier]
        evidence = state_label(row.get('Evidence state'))
        if evidence is None:
            evidence = 'Available' if row.get('Evidence IDs') else 'Missing' if linked and all(rec.get('EvidenceAvailable') == 'No' for rec in linked) else 'Unknown'
        details.append({'Row type': 'Readiness control', 'Source ID': identifier, 'Original record': row,
            'Linked finding IDs': [rec.get('RecommendationId') for rec in linked],
            'Linked scopes and identities': [{'Scope': rec.get('EvidenceScope') or rec.get('Scope'),
                'Object IDs': rec.get('AffectedObjectIds')} for rec in linked],
            'Reconciliation': candidates or None, 'Owner assignment': row.get('Owner') or None})
        source = f'Control {len(controls) + 1}: recorded conclusion'
        if candidates:
            source = 'Evidence reconciliation required; ' + source
        controls.append(dict(zip(CONTROL_COLUMNS, (row.get('Control'), evidence,
            state_label(row.get('Configuration result')), state_label(row.get('Operational result')),
            state_label(row.get('Result') or row.get('Status')), source, row.get('Owner')))))
    return topics, controls, details, topic_positions


def write_coverage(workbook, topics, controls, technical_name):
    """Two filterable sections on the existing Coverage sheet, using the writer."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.table import Table
    from openpyxl.utils import get_column_letter
    from .export_recommendations import _append_dict_rows_to_sheet
    sheet = workbook.create_sheet('Coverage')
    next_row = 1
    detail_row = 2
    for title, rows, columns, table_name in (
            ('A. Assessment topics — what evidence was collected?', topics, TOPIC_COLUMNS, 'AssessmentTopics'),
            ('B. Readiness controls — what conclusion is recorded?', controls, CONTROL_COLUMNS, 'ReadinessControls')):
        sheet.cell(next_row, 1, title)
        next_row += 1
        start = next_row
        staging = Workbook()
        source = staging.active
        data = [{key: row.get(key) for key in columns} for row in rows]
        _append_dict_rows_to_sheet(source, data, PatternFill('solid', fgColor='102B40'),
            Font(name='Calibri', color='FFFFFF', bold=True), Alignment(vertical='top', wrap_text=True), table_name)
        if not data:
            source.append(columns)
        for cells in source:
            for cell in cells:
                dest = sheet.cell(start + cell.row - 1, cell.column, cell.value)
                dest.data_type = cell.data_type
                if cell.has_style:
                    dest.font, dest.fill, dest.alignment = copy(cell.font), copy(cell.fill), copy(cell.alignment)
        if rows:
            table = Table(displayName=table_name, ref=f'A{start}:G{start + len(rows)}')
            table.tableStyleInfo = copy(next(iter(source.tables.values())).tableStyleInfo)
            sheet.add_table(table)
        for offset, row in enumerate(rows, 1):
            sheet.cell(start + offset, 6).hyperlink = technical_name + f"#'Coverage Detail'!A{detail_row}"
            sheet.cell(start + offset, 6).style = 'Hyperlink'
            detail_row += 1
        next_row = start + len(rows) + 3
        staging.close()
    sheet.cell(next_row, 1, 'Coverage legend')
    for number, text in enumerate((
        'Available means evidence collected, not a passing control.',
        'Missing or incomplete means an evidence gap, not automatically failure.',
        'Not assessed means no conclusion yet.',
        'Configuration evidence and operational evidence are different.',
        'Blank owners require assignment; owners are populated only from retained assignments.'), next_row + 1):
        sheet.cell(number, 1, text)
    return sheet


def format_reader_sheets(workbook):
    """Finish readable widths, row heights, primary columns and live exceptions."""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.comments import Comment
    from openpyxl.formatting.rule import FormulaRule, CellIsRule
    from openpyxl.utils import get_column_letter
    layouts = {'Action Plan': (ACTION_COLUMNS, (13, 22, 38, 72, 27, 15, 24, 27)),
        'Configuration': (CONFIGURATION_COLUMNS, (30, 62, 38, 45, 26, 42, 23, 30)),
        'Coverage': (TOPIC_COLUMNS, (46, 19, 25, 25, 76, 30, 28))}
    for title, (columns, widths) in layouts.items():
        if title not in workbook:
            continue
        sheet = workbook[title]
        for number, width in enumerate(widths, 1):
            sheet.column_dimensions[get_column_letter(number)].width = width
        for number in range(len(columns) + 1, sheet.max_column + 1):
            sheet.column_dimensions[get_column_letter(number)].hidden = True
        for cells in sheet.iter_rows(max_col=len(columns)):
            lines = []
            for cell, width in zip(cells, widths):
                cell.alignment = Alignment(vertical='top', wrap_text=True)
                cell.font = Font(name='Calibri', size=11, color='0563C1' if cell.hyperlink else '203040',
                    underline='single' if cell.hyperlink else None)
                lines.append(sum(max(1, ceil(len(line) / max(1, width - 4))) for line in _text(cell.value).split('\n')))
            sheet.row_dimensions[cells[0].row].height = min(409, max(32, max(lines, default=1) * 16 + 8))
        if title == 'Coverage':
            for row in sheet:
                if row[0].value in {'Assessment topic', 'Readiness control'} or str(row[0].value or '').startswith(('A. Assessment topics', 'B. Readiness controls')):
                    for cell in row[:7]:
                        cell.fill = PatternFill('solid', fgColor='102B40')
                        cell.font = Font(name='Calibri', size=11, bold=True, color='FFFFFF')
            for table in sheet.tables.values():
                first = int(re.search(r'\d+', table.ref.split(':')[0])[0]) + 1
                last = int(re.search(r'\d+', table.ref.split(':')[-1])[0])
                for column in ('B', 'C', 'D', 'E'):
                    for state, color in {'Missing': 'FFF1D2', 'Incomplete': 'FFF1D2', 'Not assessed': 'FFF1D2', 'Fail': 'F7DEDA'}.items():
                        sheet.conditional_formatting.add(f'{column}{first}:{column}{last}', CellIsRule(operator='equal', formula=[f'"{state}"'], fill=PatternFill('solid', fgColor=color)))
        else:
            for cell in sheet[1][:len(columns)]:
                cell.fill = PatternFill('solid', fgColor='102B40')
                cell.font = Font(name='Calibri', size=11, bold=True, color='FFFFFF')
        if title == 'Action Plan':
            if sheet.max_row > 1:
                sheet.conditional_formatting.add(f'A2:H{sheet.max_row}', FormulaRule(
                    formula=['OR($B2="Blocks pilot",$B2="Pilot blocker")'], fill=PatternFill('solid', fgColor='F7DEDA')))
            sheet['F1'].comment = Comment('Unassigned due dates remain blank and must be agreed with the responsible role.', 'Assessment')
            sheet['H1'].comment = Comment('Full recommendations, scope, qualifications, IDs and completion evidence are retained in Action Detail in the technical workbook.', 'Assessment')
        elif title == 'Configuration':
            sheet['E1'].comment = Comment('Meets recommendation means observed configuration matches the recommendation; operational effectiveness is not proven. Original assessment codes are retained in Configuration Detail.', 'Assessment')
            sheet['F1'].comment = Comment('Numerator: distinct objects in this configuration-value group, including groups meeting recommendations. Denominator: distinct returned objects assessed in the same population. Evidence records are not object counts. Do not add groups or findings as if populations were disjoint. Missing counts remain blank.', 'Assessment')
            sheet['H1'].comment = Comment('Opens complete, filterable Configuration Detail membership and exact source references in the technical workbook.', 'Assessment')
            for state, color in {'Change recommended': 'F7DEDA', 'Needs validation': 'FFF1D2', 'Meets recommendation': 'E4F1EA'}.items():
                if sheet.max_row > 1:
                    sheet.conditional_formatting.add(f'E2:E{sheet.max_row}', CellIsRule(operator='equal', formula=[f'"{state}"'], fill=PatternFill('solid', fgColor=color)))

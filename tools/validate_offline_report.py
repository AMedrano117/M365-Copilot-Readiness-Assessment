"""Build and audit invented evidence in a temporary directory, without tenant access."""

from pathlib import Path
import json
import os
import re
import subprocess
import sys
from urllib.parse import unquote
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openpyxl import load_workbook
from openpyxl.utils.cell import range_boundaries
from tests.synthetic_package_fixture import create_synthetic_package
from tools.audit_report_signal import parse_report, summarize
from tools.audit_workbook import _compatibility_issues
from Core.export_paths import APP_BUILDER_FOLDER, EVIDENCE_FOLDER, SUMMARY_STEM
from Core.offline_collection import load_collection


def _companion_folders(reports, name, legacy_pattern):
    """Find short companion names and previously generated report companions."""
    reports = Path(reports)
    current = [folder for folder in reports.iterdir() if folder.is_dir()
               and (folder.name == name or (folder.name.startswith(name + ' (') and folder.name.endswith(')')))]
    legacy = [folder for folder in reports.glob(legacy_pattern) if folder.is_dir()]
    return sorted(set(current + legacy))


def _report_paths(package, receipt):
    """Use the latest receipt so copied earlier builds do not become extra reports."""
    files = [Path(package) / name for name in receipt['deliverables']
             if (Path(package) / name).parent.name == receipt['run_id']]
    summaries = [path for path in files if path.suffix.lower() == '.html'
                 and (path.stem == SUMMARY_STEM or path.stem.startswith(SUMMARY_STEM + ' - ') or path.stem.endswith('_summary')
                      or (path.stem.startswith(SUMMARY_STEM + ' (') and path.stem.endswith(')')))]
    reports = [path for path in files if path.suffix.lower() == '.html' and path not in summaries]
    workbooks = [path for path in files if path.suffix.lower() == '.xlsx' and 'Technical Evidence' not in path.stem]
    return reports, summaries, workbooks


def _investigation_issues(workbook, technical_workbook=None):
    """Every exported next step has actual supporting cells or a visible gap."""
    if technical_workbook is not None and 'Findings' in workbook:
        return _split_investigation_issues(workbook, technical_workbook)
    if 'Recommendations' not in workbook:
        return ['The Recommendations register is missing.']
    issues = []
    exact = re.compile(r"'((?:[^']|'')+)'!([A-Z]+[1-9][0-9]*:[A-Z]+[1-9][0-9]*)")
    def rows_for(title):
        if title not in workbook:
            return []
        sheet = workbook[title]
        headers = {cell.value: cell.column for cell in sheet[1]}
        return [{header: sheet.cell(number, column) for header, column in headers.items()}
                for number in range(2, sheet.max_row + 1)]
    def value(row, name):
        return row[name].value if name in row else None
    registers = {title: {value(row, 'RecommendationId'): row for row in rows_for(title)}
                 for title in ('Evidence Index', 'Action Plan')}
    for row in rows_for('Recommendations'):
        if not str(value(row, 'Recommendation') or '').strip():
            continue
        identifier = value(row, 'RecommendationId') or 'Unidentified recommendation'
        if not str(value(row, 'Investigation Status') or '').strip():
            issues.append(f'{identifier}: investigation status is missing.')
        location = str(value(row, 'Evidence Sheet') or '').strip()
        qualification = str(value(row, 'Investigation Qualification') or '').strip()
        if not location:
            if value(row, 'Supporting Records') not in (None, '', 0, '0'):
                issues.append(f'{identifier}: supporting records are counted without a location.')
            if not qualification:
                issues.append(f'{identifier}: no supporting range and no visible explanation.')
            continue
        match = exact.fullmatch(location)
        if not match or match.group(1).replace("''", "'") not in workbook:
            issues.append(f'{identifier}: supporting location is not an exact exported worksheet range.')
            continue
        target = workbook[match.group(1).replace("''", "'")]
        first_column, first_row, last_column, last_row = range_boundaries(match.group(2))
        if not (2 <= first_row <= last_row <= target.max_row and 1 <= first_column <= last_column <= target.max_column):
            issues.append(f'{identifier}: supporting range extends outside the exported record cells.')
            continue
        for title, field, cells in [('Recommendations', 'Evidence Sheet', row),
                                    ('Evidence Index', 'Workbook Tab', registers['Evidence Index'].get(identifier)),
                                    ('Action Plan', 'Evidence', registers['Action Plan'].get(identifier))]:
            if cells is None:
                if title == 'Evidence Index':
                    issues.append(f'{identifier}: missing Evidence Index row.')
                continue
            cell = cells.get(field)
            if cell is None or cell.value != location or not cell.hyperlink or cell.hyperlink.target != '#' + location:
                issues.append(f'{identifier}: {title} does not link to the exact supporting record range.')
    return issues


def _split_investigation_issues(workbook, technical):
    """The compact action links and legacy register links have different destinations."""
    from Core.workbook_layout import RANGE, validate_workbook_layout
    def records(title):
        values = workbook[title].iter_rows(values_only=True)
        headers = next(values, [])
        return [dict(zip(headers, row)) for row in values]
    manifest = {row.get('Item'): row.get('Value') for row in records('Run Manifest')}
    assessment_name, technical_name = manifest['Assessment workbook'], manifest['Technical evidence workbook']
    issues = validate_workbook_layout(workbook, technical, {'recommendations': []}, assessment_name, technical_name)
    by_id = {}
    findings = workbook['Findings']
    headers = {cell.value: cell.column for cell in findings[1]}
    for number in range(2, findings.max_row + 1):
        identifier = findings.cell(number, headers['ID']).value
        cell = findings.cell(number, headers['Evidence'])
        by_id[identifier] = cell.hyperlink.target if cell.hyperlink else None
        if cell.hyperlink and cell.hyperlink.target.startswith('#'):
            match = RANGE.fullmatch(cell.hyperlink.target[1:])
            if match:
                sheet = workbook[match[1].replace("''", "'")]
                if any(sheet.cell(row, 1).value != identifier for row in range(int(match[3]), int(match[5] or match[3]) + 1)):
                    issues.append(f'{identifier}: evidence block contains another finding.')
    actions = workbook['Action Plan']
    headers = {cell.value: cell.column for cell in actions[1]}
    for number in range(2, actions.max_row + 1):
        identifier = actions.cell(number, headers.get('RecommendationId', 1)).value
        cell = actions.cell(number, headers.get('Evidence', 1))
        actual = cell.hyperlink.target if cell.hyperlink else None
        if identifier in by_id and actual != by_id[identifier]:
            issues.append(f'{identifier}: Action Plan does not link to the finding evidence block.')
    for row in records('Recommendations'):
        if not row.get('Recommendation'):
            continue
        identifier = row.get('RecommendationId')
        if not row.get('Investigation Status'):
            issues.append(f'{identifier}: investigation status is missing.')
        if not row.get('Evidence Sheet') and not row.get('Investigation Qualification'):
            issues.append(f'{identifier}: no supporting range and no visible explanation.')
    return issues


def _app_builder_issues(reports, workbook, drilldown):
    """Byte limits, checksums, row preservation and cross-output reconciliation for the App Builder files."""
    import csv
    import hashlib
    import json
    from Core.app_builder_export import rebuild_oversized
    from tests.synthetic_package_fixture import GIANT_USER_AGENT, LEGACY_EVENTS
    issues = []
    folders = _companion_folders(reports, APP_BUILDER_FOLDER, '*_app_builder')
    if len(folders) != 1:
        return [f'Expected one App Builder folder; found {len(folders)}.']
    folder = folders[0]
    manifest = json.loads((folder / '03-manifest.json').read_text(encoding='utf-8'))
    entries = manifest['files'] or [entry for name in manifest.get('file_list_parts', [])
                                    for entry in json.loads((folder / name).read_text(encoding='utf-8'))['files']]
    listed = {entry['file'] for entry in entries} | {'03-manifest.json'}
    for path in folder.iterdir():
        size = path.stat().st_size
        if size >= 1_000_000:
            issues.append(f'App Builder file {path.name} is {size} bytes; files must be below 1,000,000 bytes.')
        if path.name not in listed:
            issues.append(f'App Builder file {path.name} is missing from the manifest.')
    for entry in entries:
        data = (folder / entry['file']).read_bytes()
        if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
            issues.append(f'Manifest size or checksum does not match {entry["file"]}.')
    catalog = [item for path in sorted(folder.glob('02-finding-catalog*.json'))
               for item in json.loads(path.read_text(encoding='utf-8'))['findings']]
    def rows(names):
        output = []
        for name in names:
            with (folder / name).open(encoding='utf-8', newline='') as handle:
                output.extend(csv.DictReader(handle))
        return output
    register = (workbook['Findings Lineage'] if 'Findings Lineage' in workbook else
                workbook['Findings Register'] if 'Findings Register' in workbook else None)
    columns = {cell.value: cell.column for cell in register[1]} if register is not None else {}
    links, listed_ranges = {}, {}
    if register is not None and 'Evidence records' in columns:
        for number in range(2, register.max_row + 1):
            finding_id = register.cell(number, columns['RecommendationId']).value
            cell = register.cell(number, columns['Evidence records'])
            if cell.hyperlink:
                links[finding_id] = cell.hyperlink.target.lstrip('#')
            if 'Evidence record ranges' in columns:
                listed_ranges[finding_id] = str(register.cell(number, columns['Evidence record ranges']).value or '')
    exact = re.compile(r"'((?:[^']|'')+)'!([A-Z]+[1-9][0-9]*:[A-Z]+[1-9][0-9]*)")

    def block_rows(location):
        match = exact.fullmatch(location)
        sheet = workbook[match.group(1).replace("''", "'")]
        first_column, first_row, last_column, last_row = range_boundaries(match.group(2))
        headers = [cell.value for cell in sheet[1]]
        return [dict(zip(headers, (cell.value for cell in row)))
                for row in sheet.iter_rows(min_row=first_row, max_row=last_row)]
    for item in catalog:
        exported = [row for row in rows(item['evidence_files']) if row['finding_id'] == item['finding_id']]
        if len(exported) != item['record_count'] and not item['context_files']:
            issues.append(f"{item['finding_id']}: {len(exported)} App Builder rows for {item['record_count']} {item['record_unit']}.")
        if not item['evidence_files']:
            continue
        location = links.get(item['finding_id'], '')
        if not exact.fullmatch(location):
            issues.append(f"{item['finding_id']}: the Findings Register has no exact evidence link.")
            continue
        # A finding has one block per evidence table; together the listed blocks hold every exported row.
        locations = [match.group(0) for match in exact.finditer(listed_ranges.get(item['finding_id'], ''))] or [location]
        if location not in locations:
            issues.append(f"{item['finding_id']}: the evidence link is not one of the finding's listed ranges.")
        block = [row for each in locations for row in block_rows(each)]
        if sorted(row['detail_record_id'] for row in block) != sorted(row['detail_record_id'] for row in exported):
            issues.append(f"{item['finding_id']}: workbook evidence rows do not match the App Builder rows.")
        if any(row['finding_id'] != item['finding_id'] for row in block):
            issues.append(f"{item['finding_id']}: the workbook evidence link includes another finding's rows.")
    text = '\n'.join(path.read_text(encoding='utf-8', errors='replace') for path in folder.iterdir())
    if 'fictional-token-must-not-be-exported' in text:
        issues.append('A token value was exported to App Builder files.')
    if drilldown:
        legacy = next((item for item in catalog if item['finding_key'] == 'entra.signins.legacy_auth'), None)
        if legacy is None:
            return issues + ['The legacy-authentication finding is missing from the App Builder catalog.']
        events = [row for row in rows(legacy['evidence_files']) if row['finding_id'] == legacy['finding_id']]
        if len(events) != LEGACY_EVENTS or legacy['record_unit'] != 'sign-in events' or legacy['evidence_kind'] != 'observed_event':
            issues.append(f'Legacy authentication exported {len(events)} events as {legacy["evidence_kind"]}; expected {LEGACY_EVENTS} observed events.')
        required = {'eventId', 'createdDateTime', 'userPrincipalName', 'appDisplayName', 'clientAppUsed', 'authenticationProtocol',
                    'outcome', 'outcomeDetail', 'errorCode', 'failureReason', 'conditionalAccessStatus', 'ipAddress', 'evidence_record_ids'}
        if events and required - set(events[0]):
            issues.append('Legacy events are missing columns: ' + ', '.join(sorted(required - set(events[0]))))
        if {row['outcome'] for row in events} != {'Succeeded', 'Blocked', 'Failed', 'Unknown'}:
            issues.append('Legacy events do not distinguish succeeded, blocked, failed and unknown outcomes.')
        if any(row['clientAppUsed'] in {'MAPI Over HTTP', 'Browser'} for row in events):
            issues.append('Unmatched or modern clients were added to the legacy finding.')
        giant = next((row for row in events if row['eventId'] == 'fictional-signin-00004'), {})
        if rebuild_oversized(folder, giant).get('userAgent') != GIANT_USER_AGENT:
            issues.append('The oversized value was not preserved losslessly.')
        if legacy['affected_entity_count'] != 40:
            issues.append(f'Legacy authentication counted {legacy["affected_entity_count"]} accounts; expected 40.')
    return issues


def _evidence_page_issues(report, html_source):
    """Every finding page exists, its rows equal the record count across pages, and links resolve."""
    import json
    issues, reports = [], report.parent
    folders = _companion_folders(reports, EVIDENCE_FOLDER, '*_evidence')
    if len(folders) != 1:
        return [f'Expected one technical evidence folder; found {len(folders)}.']
    folder = folders[0]
    decoded_html = unquote(html_source)
    if folder.name + '/index.html' not in decoded_html and folder.name + '/evidence-' not in decoded_html:
        issues.append('The main report does not link to the technical evidence pages.')
    app_builder_folders = _companion_folders(reports, APP_BUILDER_FOLDER, '*_app_builder')
    if len(app_builder_folders) != 1:
        return issues + [f'Expected one App Builder folder; found {len(app_builder_folders)}.']
    catalog = [item for path in sorted(app_builder_folders[0].glob('02-finding-catalog*.json'))
               for item in json.loads(path.read_text(encoding='utf-8'))['findings']]
    from Core.html_evidence_pages import page_name
    for item in catalog:
        pages = [folder / page_name(item['finding_id'], number)
                 for number in range(1, len(list(folder.glob('*.html'))) + 1)
                 if (folder / page_name(item['finding_id'], number)).is_file()]
        if not pages:
            issues.append(f"{item['finding_id']}: no technical evidence page.")
            continue
        rows = sum(len(re.findall(r'<tr id="det-', page.read_text(encoding='utf-8'))) for page in pages)
        if rows != item['record_count'] and not item['context_files']:
            issues.append(f"{item['finding_id']}: evidence pages show {rows} rows for {item['record_count']} records.")
    anchors = set(re.findall(r'\bid="([^"]+)"', html_source))
    for page in folder.glob('*.html'):
        source = page.read_text(encoding='utf-8')
        if 'fictional-token-must-not-be-exported' in source:
            issues.append(f'{page.name} contains a token value.')
        for target, anchor in re.findall(r'href="([^"#:]+\.html)(?:#([^"]*))?"', source):
            path = (page.parent / unquote(target)).resolve()
            if not path.exists():
                issues.append(f'{page.name} links to a missing page {target}.')
            elif anchor and path == report.resolve() and anchor not in anchors:
                issues.append(f'{page.name} links to a missing report anchor #{anchor}.')
    return issues


def validate_case(destination, *, active_incident=False, evidence_drilldown=False):
    destination.mkdir(parents=True, exist_ok=True)
    collection = create_synthetic_package(destination / 'evidence', active_incident=active_incident,
                                          evidence_drilldown=evidence_drilldown)
    result = subprocess.run(
        [sys.executable, str(ROOT / 'main.py'), '--mode', 'offline',
         '--collection-input', str(collection), '--evaluation-date', '2026-09-15',
         '--extra-exports', 'evidence-pages', 'app-builder', 'dashboard-json',
         '--color', 'never'], cwd=destination,
        env={**os.environ, 'PYTHONUTF8': '1', 'NO_COLOR': '1'},
        capture_output=True, text=True, encoding='utf-8', timeout=600 if evidence_drilldown else 120,
    )
    if result.returncode:
        print(result.stdout)
        print(result.stderr)
        return result.returncode
    package = Path(load_collection(collection)['package_directory'])
    receipt = json.loads((package / 'operator-log.jsonl').read_text(encoding='utf-8').splitlines()[-1])
    html_paths, summaries, workbooks = _report_paths(package, receipt)
    if len(html_paths) != 1 or len(workbooks) != 1 or len(summaries) != 1:
        print(f'Expected one HTML report, summary and workbook; found {len(html_paths)}, {len(summaries)} and {len(workbooks)}.')
        return 1
    report, workbook_path = html_paths[0], workbooks[0]
    summary_source = summaries[0].read_text(encoding='utf-8')
    audit = summarize(parse_report(report), html_source=report.read_text(encoding='utf-8'),
                      workbook=workbook_path)
    workbook = load_workbook(workbook_path, data_only=True)
    from Core.workbook_layout import technical_workbook_path
    technical_path = technical_workbook_path(workbook_path)
    technical = load_workbook(technical_path, data_only=True) if technical_path.is_file() else None
    try:
        compatibility = _compatibility_issues(workbook_path, workbook)
        if technical is not None:
            compatibility += _compatibility_issues(technical_path, technical)
        investigation = _investigation_issues(workbook, technical)
        investigation += _app_builder_issues(report.parent, technical or workbook, evidence_drilldown)
        sheet_names = list(workbook.sheetnames) + (list(technical.sheetnames) if technical is not None else [])
        coverage_headers = [cell.value for cell in workbook['Collection Coverage'][1]] if 'Collection Coverage' in workbook else []
    finally:
        workbook.close()
        if technical is not None:
            technical.close()
    issues = audit['audit_issues'] + compatibility + investigation
    html_source = report.read_text(encoding='utf-8')
    issues += _evidence_page_issues(report, html_source)
    if 'id="getting-started"' not in html_source or 'href="#getting-started"' not in html_source:
        issues.append('The getting-started roadmap section or its navigation link is missing.')
    if summaries[0].name not in unquote(html_source):
        issues.append('The full report does not link to the pilot readiness summary.')
    if '<h1>' not in summary_source or 'id="pilot-group"' not in summary_source:
        issues.append('The pilot readiness summary is missing its verdict or pilot-group guidance.')
    anchors = set(re.findall(r'\bid="([^"]+)"', html_source))
    for anchor in re.findall(re.escape(report.name) + r'#([^"]+)"', unquote(summary_source)):
        if anchor not in anchors:
            issues.append(f'The summary links to a missing full-report anchor: {anchor}.')
    if 'Adoption Guidance' not in sheet_names:
        issues.append('The Adoption Guidance workbook tab is missing.')
    if 'Collected With' not in coverage_headers:
        issues.append('Collection Coverage does not record which identity collected each source.')
    if bool(audit['total_cards']) != (active_incident or evidence_drilldown):
        issues.append('The invented incident state did not produce the expected action plan.')
    for issue in issues:
        print(f'FAIL ({destination.name}): {issue}')
    if issues:
        return 1
    print(f'Synthetic {destination.name} HTML/workbook passed integrity, cross-output and Excel compatibility checks.')
    return 0


def main():
    with tempfile.TemporaryDirectory(prefix='assessment-offline-validation-') as directory:
        for name, active, drilldown in [('complete-pilot', False, False), ('active-incident', True, False),
                                        ('evidence-drilldown', False, True)]:
            result = validate_case(Path(directory) / name, active_incident=active, evidence_drilldown=drilldown)
            if result:
                return result
    return 0


if __name__ == '__main__':
    sys.exit(main())

"""Build and audit invented evidence in a temporary directory, without tenant access."""

from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openpyxl import load_workbook
from openpyxl.utils.cell import range_boundaries
from tests.synthetic_package_fixture import create_synthetic_package
from tools.audit_report_signal import parse_report, summarize
from tools.audit_workbook import _compatibility_issues


def _investigation_issues(workbook):
    """Every exported next step has actual supporting cells or a visible gap."""
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


def validate_case(destination, *, active_incident=False):
    destination.mkdir(parents=True, exist_ok=True)
    collection = create_synthetic_package(destination / 'evidence', active_incident=active_incident)
    result = subprocess.run(
        [sys.executable, str(ROOT / 'main.py'), '--mode', 'offline',
         '--collection-input', str(collection), '--evaluation-date', '2026-09-15',
         '--color', 'never'], cwd=destination,
        env={**os.environ, 'PYTHONUTF8': '1', 'NO_COLOR': '1'},
        capture_output=True, text=True, encoding='utf-8', timeout=120,
    )
    if result.returncode:
        print(result.stdout)
        print(result.stderr)
        return result.returncode
    all_html = [path for path in destination.rglob('*.html') if 'deliverables' not in path.parts]
    summaries = [path for path in all_html if path.stem.endswith('_summary')]
    html_paths = [path for path in all_html if path not in summaries]
    workbooks = [path for path in destination.rglob('*.xlsx') if 'deliverables' not in path.parts]
    if len(html_paths) != 1 or len(workbooks) != 1 or len(summaries) != 1:
        print(f'Expected one HTML report, summary and workbook; found {len(html_paths)}, {len(summaries)} and {len(workbooks)}.')
        return 1
    report, workbook_path = html_paths[0], workbooks[0]
    summary_source = summaries[0].read_text(encoding='utf-8')
    audit = summarize(parse_report(report), html_source=report.read_text(encoding='utf-8'),
                      workbook=workbook_path)
    workbook = load_workbook(workbook_path, data_only=True)
    try:
        compatibility = _compatibility_issues(workbook_path, workbook)
        investigation = _investigation_issues(workbook)
        sheet_names = list(workbook.sheetnames)
        coverage_headers = [cell.value for cell in workbook['Collection Coverage'][1]] if 'Collection Coverage' in workbook else []
    finally:
        workbook.close()
    issues = audit['audit_issues'] + compatibility + investigation
    html_source = report.read_text(encoding='utf-8')
    if 'id="getting-started"' not in html_source or 'href="#getting-started"' not in html_source:
        issues.append('The getting-started roadmap section or its navigation link is missing.')
    if summaries[0].name not in html_source:
        issues.append('The full report does not link to the pilot readiness summary.')
    if '<h1>' not in summary_source or 'id="pilot-group"' not in summary_source:
        issues.append('The pilot readiness summary is missing its verdict or pilot-group guidance.')
    anchors = set(re.findall(r'\bid="([^"]+)"', html_source))
    for anchor in re.findall(re.escape(report.name) + r'#([^"]+)"', summary_source):
        if anchor not in anchors:
            issues.append(f'The summary links to a missing full-report anchor: {anchor}.')
    if 'Adoption Guidance' not in sheet_names:
        issues.append('The Adoption Guidance workbook tab is missing.')
    if 'Collected With' not in coverage_headers:
        issues.append('Collection Coverage does not record which identity collected each source.')
    if bool(audit['total_cards']) != active_incident:
        issues.append('The invented incident state did not produce the expected action plan.')
    for issue in issues:
        print(f'FAIL ({destination.name}): {issue}')
    if issues:
        return 1
    print(f'Synthetic {destination.name} HTML/workbook passed integrity, cross-output and Excel compatibility checks.')
    return 0


def main():
    with tempfile.TemporaryDirectory(prefix='assessment-offline-validation-') as directory:
        for name, active in [('complete-pilot', False), ('active-incident', True)]:
            result = validate_case(Path(directory) / name, active_incident=active)
            if result:
                return result
    return 0


if __name__ == '__main__':
    sys.exit(main())

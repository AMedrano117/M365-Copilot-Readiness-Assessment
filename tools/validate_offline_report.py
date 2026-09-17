"""Build and audit invented evidence in a temporary directory, without tenant access."""

from pathlib import Path
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openpyxl import load_workbook
from tests.synthetic_package_fixture import create_synthetic_package
from tools.audit_report_signal import parse_report, summarize
from tools.audit_workbook import _compatibility_issues


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
    html_paths = [path for path in destination.rglob('*.html') if 'deliverables' not in path.parts]
    workbooks = [path for path in destination.rglob('*.xlsx') if 'deliverables' not in path.parts]
    if len(html_paths) != 1 or len(workbooks) != 1:
        print(f'Expected one HTML report and workbook; found {len(html_paths)} and {len(workbooks)}.')
        return 1
    report, workbook_path = html_paths[0], workbooks[0]
    audit = summarize(parse_report(report), html_source=report.read_text(encoding='utf-8'),
                      workbook=workbook_path)
    workbook = load_workbook(workbook_path, data_only=True)
    try:
        compatibility = _compatibility_issues(workbook_path, workbook)
    finally:
        workbook.close()
    issues = audit['audit_issues'] + compatibility
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

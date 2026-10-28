"""Bind downloaded DAG bytes to retained report inventory, never file times."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re


def _digest(path):
    digest = sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def capture_download_hashes(payload):
    """Run before packaging; keep originals untouched and hashes in saved JSON."""
    result = deepcopy(payload)
    for item in result.get('exported_files') or []:
        if not isinstance(item, dict):
            continue
        try:
            item['sha256'] = _digest(item['path'])
        except (OSError, KeyError, TypeError):
            continue
    return result


def downloaded_report_metadata(payload, paths, package_directory=None):
    """Require exact report IDs, completed states and matching content hashes.

    Legacy packages can recover the binding from the exact packaged input path.
    ReportEndTime is a data-period end, not a generation or download timestamp.
    """
    from .data_exposure_assessment import _parse_date
    selected = {Path(path).resolve() for path in paths or []}
    candidates = {}
    inventory = ((payload or {}).get('dag_reports') or {}).get('reports') or []
    for item in (payload or {}).get('exported_files') or []:
        if not isinstance(item, dict):
            continue
        identifier = str(item.get('report_id') or '').casefold()
        if not identifier:
            continue
        digest = str(item.get('sha256') or '').lower()
        path = Path(item.get('path') or '')
        if not path.is_absolute():
            if package_directory:
                root = Path(package_directory).resolve()
                path = (root / path).resolve()
                if not path.is_relative_to(root):
                    continue
            else:
                path = None
        if not re.fullmatch('[a-f0-9]{64}', digest):
            if path is None:
                continue
            if path.resolve() not in selected or not path.is_file():
                continue
            digest = _digest(path)
        for row in inventory:
            if str(row.get('ReportId') or row.get('ReportID') or '').casefold() != identifier:
                continue
            if str(row.get('Status') or '').lower() not in {'complete', 'completed', 'succeeded'}:
                continue
            date, basis = None, ''
            for field, label in (('ReportEndTime', 'Collector report period end'), ('EndTime', 'Collector report period end'),
                                 ('CompletedDateTime', 'Collector report completion')):
                date = _parse_date(row.get(field))
                if date:
                    basis = label
                    break
            if not date:
                continue
            metadata = {'report_id': identifier, 'report_date': date.isoformat(),
                'date_basis': basis, 'entity': row.get('RequestedEntity') or row.get('ReportEntity') or '',
                'workload': row.get('RequestedWorkload') or row.get('Workload') or '',
                'acquisition': 'Retained collector download'}
            candidates.setdefault(digest, []).append(metadata)
            if path is not None and path.resolve() in selected:
                candidates.setdefault((str(path.resolve()), digest), []).append(metadata)
    return {digest: rows[0] for digest, rows in candidates.items() if all(row == rows[0] for row in rows)}

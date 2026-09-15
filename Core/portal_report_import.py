"""Identify portal exports by schema, without relying on customer filenames."""

from pathlib import Path
from uuid import UUID
import hashlib

from .data_exposure_assessment import (
    _matches_authoritative_schema, _read_records, detect_sharepoint_report,
)


def route_portal_reports(directories):
    routed = {"sam": [], "dspm": [], "readiness": [], "warnings": [], "reference_warnings": []}
    seen = set()
    for directory in directories or []:
        folder = Path(directory)
        if not folder.is_dir():
            raise ValueError(f"Portal report directory does not exist: {folder}")
        for path in sorted(folder.iterdir()):
            if path.is_file() and path.suffix.lower() == '.pdf':
                warning = f'{path.name}: PDF context was not imported; use --reports-dir for automatic import or --portal-review for a reviewed manifest. Check any PDF skipped messages.'
                if warning not in routed['warnings']:
                    routed['warnings'].append(warning)
                    routed['reference_warnings'].append(warning)
                continue
            if not path.is_file() or path.suffix.lower() not in {".csv", ".tsv", ".xlsx", ".zip"}:
                continue
            identity = str(path.resolve()).lower()
            if identity in seen:
                continue
            seen.add(identity)
            kinds = set()
            try:
                for row in _read_records(path):
                    keys = {str(key).strip().lower() for key in row}
                    if {"has copilot license assigned", "uses eligible update channel", "report refresh date"} <= keys:
                        kinds.add("readiness")
                    elif detect_sharepoint_report(row):
                        kinds.add("sam")
                    elif _matches_authoritative_schema(row, "dspm"):
                        kinds.add("dspm")
                    # CSV and TSV have one header; no need to read customer rows for routing.
                    if path.suffix.lower() in {".csv", ".tsv"}:
                        break
                if len(kinds) == 1:
                    routed[next(iter(kinds))].append(str(path))
                else:
                    routed["warnings"].append(
                        f"{path.name}: {'mixed report types' if kinds else 'unrecognized report schema'}; not imported."
                    )
            except (ValueError, OSError) as exc:
                routed["warnings"].append(f"{path.name}: unable to identify report ({type(exc).__name__}).")
    return routed


def select_readiness_report(paths, preferred=None):
    """Reusing a packaged copy of the same snapshot must not create a conflict."""
    paths = list(dict.fromkeys(([preferred] if preferred else []) + list(paths or [])))
    if not paths:
        return [], []
    hashes = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in paths}
    if not preferred and len(set(hashes.values())) > 1:
        from .console_reporting import display_path
        candidates = '\n'.join(f'  - {display_path(path)}' for path in paths)
        raise ValueError(
            'More than one Copilot readiness export was supplied, with different contents.\n'
            'Saved reports are restored automatically; --reports-dir adds files to them. '
            'Remove an unintended reports folder, or select one snapshot for the same tenant '
            'with --copilot-readiness-export PATH.\n'
            'Readiness files found:\n' + candidates)
    selected = preferred or paths[0]
    receipt = []
    for path in paths:
        if path == selected:
            continue
        duplicate = hashes[path] == hashes[selected]
        receipt.append({'Source': Path(path).name, 'Status': 'duplicate' if duplicate else 'not_selected',
                        'Original Date': '', 'Scope': 'Exported user rows',
                        'Reason': 'Identical file contents; assessed once.' if duplicate else
                            'A different readiness snapshot was explicitly selected.'})
    return [selected], receipt


def validate_report_tenants(paths, expected_tenant_id=None, receipt=None):
    """Prevent accidental combinations of different customers' tenant-tagged reports."""
    found = set()
    sources = {}
    def normalized(value):
        value = str(value or '').strip().lower()
        try:
            return str(UUID(value))
        except (ValueError, TypeError, AttributeError):
            return value
    for raw_path in paths or []:
        path = Path(raw_path)
        candidates = sorted(path.iterdir()) if path.is_dir() else [path]
        for candidate in candidates:
            if not candidate.is_file() or candidate.suffix.lower() not in {".csv", ".tsv", ".xlsx", ".json", ".zip"}:
                continue
            source_tenants = sources.setdefault(str(candidate.resolve()), set())
            for row in _read_records(candidate):
                if row.get("_headers_only"):
                    continue
                for key, value in row.items():
                    if str(key).replace(" ", "").replace("_", "").lower() == "tenantid" and value:
                        identifier = normalized(value)
                        found.add(identifier)
                        source_tenants.add(identifier)
    expected = normalized(expected_tenant_id)
    if receipt is not None:
        for source, identifiers in sources.items():
            verified = bool(identifiers and expected and identifiers == {expected})
            receipt.append({
                'Source': Path(source).name,
                'Status': 'identity_verified' if verified else 'identity_unverified',
                'Original Date': '', 'Scope': '',
                'Reason': 'The report tenant identifier matches the assessed tenant.' if verified else
                    'No tenant identifier is present; confirm this export belongs to the assessed customer.' if not identifiers else
                    'The export identifies a tenant; an independently identified assessed tenant is needed for comparison.',
            })
    if len(found) > 1:
        raise ValueError("The supplied portal reports identify multiple tenants. Use one tenant per report build.")
    if found and expected and expected not in found:
        # A domain cannot authorize an unrelated GUID: resolve the assessed tenant
        # to its canonical organization ID in live mode, or require that ID offline.
        raise ValueError("The portal report tenant ID does not match the assessed tenant. Use the tenant GUID when a domain or display name cannot be compared.")
    return next(iter(found), "")

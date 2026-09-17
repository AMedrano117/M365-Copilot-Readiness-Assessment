"""Local, portable assessment inputs and an append-only operator receipt.

Only declared data inputs are copied. Credentials, collectors and executable
objects are never part of the package. A collection is immutable during replay.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import re
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

PACKAGE_VERSION = 1
REBUILD_FORMAT = 'm365-readiness-rebuild'
INPUT_KEYS = {
    'sam_report', 'dspm_report', 'reports_dir', 'copilot_readiness_export',
    'copilot_dashboard_export', 'power_platform_inventory', 'assessment_profile',
    'provider_evidence', 'baseline', 'prior_report', 'purview_cache', 'portal_review',
}
LIST_INPUTS = {'sam_report', 'dspm_report', 'reports_dir'}
DATA_SUFFIXES = {'.csv', '.tsv', '.xlsx', '.zip', '.json'}
SETTING_KEYS = {'report_format', 'data_exposure_enabled', 'preview_collectors', 'permission_profile',
                'include_user_usage_detail', 'provider_evidence_max_age_days',
                'sam_report_max_age_days', 'dspm_report_max_age_days',
                'lifecycle_report_max_age_days', 'lifecycle_report_dates'}

_METHODOLOGY_MIGRATIONS = {
    ('2.0.0', '2.1.0'): (
        'The raw collection schema is unchanged. Existing collected facts and base findings '
        'are evaluated with the 2.1.0 control matching and reviewed pilot criteria; '
        'readiness conclusions can change. Original evidence dates and files are preserved.'
    ),
}


def methodology_metadata(payload, artifact):
    """Apply only a reviewed compatibility transition, without rewriting its source."""
    from .cross_provider_assessment import METHODOLOGY_VERSION
    saved = payload.get('methodology_version')
    original = payload.get('original_methodology_version', saved)
    for version in (saved, original):
        if version != METHODOLOGY_VERSION and (version, METHODOLOGY_VERSION) not in _METHODOLOGY_MIGRATIONS:
            raise ValueError(
                f'{artifact} methodology {version or "not recorded"} differs from the running methodology {METHODOLOGY_VERSION}. '
                'Use the matching tool version or an explicitly supported migration; saved conclusions cannot be silently rescored.')
    payload['original_methodology_version'] = original
    payload['effective_methodology_version'] = METHODOLOGY_VERSION
    source = saved if saved != METHODOLOGY_VERSION else original
    if source != METHODOLOGY_VERSION:
        payload['methodology_migration'] = {
            'from': source, 'to': METHODOLOGY_VERSION,
            'reason': _METHODOLOGY_MIGRATIONS[(source, METHODOLOGY_VERSION)],
        }
    else:
        payload.pop('methodology_migration', None)
    return payload


def _apply_recipe(payload, folder, recipe):
    if recipe.get('format') != REBUILD_FORMAT or recipe.get('version') != PACKAGE_VERSION:
        raise ValueError('Unsupported offline rebuild recipe version.')
    methodology_metadata(recipe, 'Rebuild recipe')
    _validate_inputs(folder, recipe)
    # A recovered collection can legitimately have no original package manifest.
    # Its recipe is now the portable package; keep later rebuilds in that folder.
    payload['package_directory'] = str(Path(folder).resolve())
    if not payload.get('package'):
        payload['package'] = {'version': PACKAGE_VERSION, 'files': recipe.get('files', []),
                              'diagnostics': recipe.get('diagnostics', [])}
    payload['resolved_inputs'] = {
        role: [str(confined_path(folder, reference)) for reference in references]
        for role, references in recipe.get('inputs', {}).items() if role in INPUT_KEYS
    }
    payload['evaluation_date'] = evaluation_day(recipe.get('evaluation_date'))
    payload.setdefault('assessment_settings', {}).update({
        key: value for key, value in recipe.get('assessment_settings', {}).items() if key in SETTING_KEYS})
    payload['rebuild_recipe'] = recipe
    if recipe.get('methodology_migration') and not payload.get('methodology_migration'):
        payload['methodology_migration'] = dict(recipe['methodology_migration'])
        payload['original_methodology_version'] = recipe['original_methodology_version']
    return payload


def evaluation_day(value=None):
    """Return an explicit ISO date; never let a render timestamp refresh evidence."""
    if value is None or value == '':
        return datetime.now(timezone.utc).date().isoformat()
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise ValueError('Evaluation date must use YYYY-MM-DD.') from None


def _digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _check_json_credentials(path):
    if path.suffix.lower() != '.json':
        return
    try:
        payload = json.loads(path.read_text(encoding='utf-8-sig'))
    except (ValueError, OSError):
        return  # Invalid data still receives the normal importer diagnosis.
    sensitive = {'clientsecret', 'clientassertion', 'secrettext', 'password', 'certificatepassword',
                 'privatekey', 'accesstoken', 'refreshtoken', 'idtoken', 'authorization'}
    def contains_secret(value):
        if isinstance(value, dict):
            return any((re.sub(r'[^a-z0-9]', '', str(key).lower()) in sensitive and isinstance(item, str) and bool(item.strip()))
                       or contains_secret(item) for key, item in value.items())
        if isinstance(value, list):
            return any(contains_secret(item) for item in value)
        return False
    if contains_secret(payload):
        raise ValueError(f'Cannot package {path.name}: credential fields must be removed from supplemental data inputs.')


def confined_path(root, reference):
    """Reject absolute, traversal and symlink references outside a copied package."""
    root = Path(root).resolve()
    path = Path(str(reference))
    if path.is_absolute():
        raise ValueError('Package source references must be relative paths.')
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError('Package source reference escapes the assessment folder.')
    return resolved


def package_inputs(folder, supplemental_inputs):
    """Copy originals, preserve basenames, and record hashes and skipped inputs."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {'version': PACKAGE_VERSION, 'inputs': {}, 'files': [], 'diagnostics': []}
    references = {}
    copied = {}
    for role, supplied in (supplemental_inputs or {}).items():
        if role not in INPUT_KEYS or not supplied:
            continue
        paths = supplied if isinstance(supplied, (list, tuple)) else [supplied]
        manifest['inputs'][role] = []
        for raw_path in paths:
            source = Path(raw_path).resolve()
            identity = (role, str(source))
            if identity in copied:
                reference = copied[identity]
            else:
                reference = (Path('inputs') / f'{len(copied) + 1:03d}_{role}' / source.name).as_posix()
                target = folder / reference
                if not source.exists():
                    raise ValueError(f'Cannot package missing {role} input: {source.name}')
                visual_assets = []
                if role == 'portal_review':
                    from .portal_review import load_portal_review
                    visual_assets = [Path(path) for path in load_portal_review(source, embed_assets=False)['asset_paths']]
                candidates = sorted(source.iterdir()) if source.is_dir() else [source]
                if source.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                for candidate in candidates:
                    if role == 'reports_dir' and candidate.is_dir():
                        continue  # Nested PDFs are retained by the generated portal-review manifest.
                    suffixes = DATA_SUFFIXES - {'.json'} if role == 'reports_dir' else DATA_SUFFIXES
                    if not candidate.is_file() or candidate.suffix.lower() not in suffixes:
                        if role == 'reports_dir' and candidate.is_file() and candidate.suffix.lower() == '.pdf':
                            manifest['diagnostics'].append({'role': role, 'source_file': candidate.name,
                                'status': 'reference_only',
                                'reason': 'PDF originals are retained through automatic PDF import or --portal-review, separately from structured data.'})
                            continue
                        manifest['diagnostics'].append({'role': role, 'source_file': candidate.name,
                                                        'status': 'unsupported', 'reason': 'Not a supported data file; not copied.'})
                        continue
                    _check_json_credentials(candidate)
                    destination = target / candidate.name if source.is_dir() else target
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(candidate, destination)
                    relative = destination.relative_to(folder).as_posix()
                    manifest['files'].append({'role': role, 'source_file': candidate.name,
                                              'path': relative, 'sha256': _digest(destination),
                                              'size_bytes': destination.stat().st_size})
                    references[str(candidate)] = relative
                    references[str(candidate.resolve())] = relative
                for asset in visual_assets:
                    destination = target.parent / asset.relative_to(source.parent)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(asset, destination)
                    relative = destination.relative_to(folder).as_posix()
                    manifest['files'].append({'role': role, 'source_file': asset.name,
                                              'path': relative, 'sha256': _digest(destination),
                                              'size_bytes': destination.stat().st_size})
                    references[str(asset)] = relative
                if source.is_file() and not target.is_file():
                    raise ValueError(f'Cannot package unsupported {role} file: {source.name}')
                copied[identity] = reference
                references[str(raw_path)] = reference
                references[str(source)] = reference
            if reference not in manifest['inputs'][role]:
                manifest['inputs'][role].append(reference)
    return manifest, references


def replace_source_paths(value, references):
    if isinstance(value, dict):
        return {key: replace_source_paths(item, references) for key, item in value.items()}
    if isinstance(value, list):
        return [replace_source_paths(item, references) for item in value]
    if isinstance(value, str):
        return references.get(value, value)
    return value


def _validate_inputs(folder, manifest):
    declared = set()
    for record in manifest.get('files', []):
        source = confined_path(folder, record['path'])
        if not source.is_file():
            raise ValueError(f'Packaged input is missing: {record["path"]}')
        if _digest(source) != record.get('sha256'):
            raise ValueError(f'Packaged input has changed: {record["path"]}. Add revised exports with --reports-dir or a report flag.')
        declared.add(source)
    for references in manifest.get('inputs', {}).values():
        for reference in references:
            source = confined_path(folder, reference)
            if not source.exists():
                raise ValueError(f'Packaged input is missing: {reference}')
            for candidate in sorted(source.iterdir()) if source.is_dir() else [source]:
                if candidate.is_file() and candidate.suffix.lower() in DATA_SUFFIXES:
                    confined_path(folder, candidate.relative_to(folder))
                    if candidate.resolve() not in declared:
                        raise ValueError(f'Unrecorded file in packaged inputs: {candidate.name}. Add new exports with --reports-dir or a report flag.')


def resolve_package(payload, collection_path):
    """Validate the immutable manifest and expose local resolved input paths."""
    manifest = payload.get('package')
    if not manifest:
        return payload
    if manifest.get('version') != PACKAGE_VERSION:
        raise ValueError('Unsupported assessment package version.')
    folder = confined_path(Path(collection_path).resolve().parent, manifest.get('directory', '.'))
    _validate_inputs(folder, manifest)
    payload['package_directory'] = str(folder)
    payload['resolved_inputs'] = {
        role: [str(confined_path(folder, reference)) for reference in references]
        for role, references in manifest.get('inputs', {}).items() if role in INPUT_KEYS
    }
    recipe_path = folder / 'rebuild.json'
    if recipe_path.is_file():
        recipe = json.loads(recipe_path.read_text(encoding='utf-8'))
        _apply_recipe(payload, folder, recipe)
    return payload


def load_rebuild_recipe(path, recipe):
    """Replay an explicit recipe; legacy recovery contains no invented raw collection."""
    if recipe.get('format') != REBUILD_FORMAT or recipe.get('version') != PACKAGE_VERSION:
        raise ValueError('Unsupported offline rebuild recipe version.')
    methodology_metadata(recipe, 'Rebuild recipe')
    folder = Path(path).resolve().parent
    _validate_inputs(folder, recipe)
    if recipe.get('collection'):
        from .offline_collection import load_collection
        payload = load_collection(confined_path(folder, recipe['collection']))
        return _apply_recipe(payload, folder, recipe)
    from .offline_collection import empty_service_results
    payload = {
        'format': REBUILD_FORMAT, 'version': PACKAGE_VERSION, 'service_results': empty_service_results(),
        'methodology_version': recipe['methodology_version'],
        'original_methodology_version': recipe['original_methodology_version'],
        'tenant_id': recipe.get('tenant_id'), 'tenant_name': recipe.get('tenant_name'),
        'collected_at': '', 'has_tenant_collection': False,
        'evaluation_date': evaluation_day(recipe.get('evaluation_date')),
        'assessment_settings': {key: value for key, value in recipe.get('assessment_settings', {}).items() if key in SETTING_KEYS},
        'package_directory': str(folder), 'rebuild_recipe': recipe,
        'resolved_inputs': {role: [str(confined_path(folder, reference)) for reference in paths]
                            for role, paths in recipe.get('inputs', {}).items() if role in INPUT_KEYS},
        'package': {'version': PACKAGE_VERSION, 'files': recipe.get('files', []),
                    'diagnostics': recipe.get('diagnostics', [])},
    }
    return methodology_metadata(payload, 'Rebuild recipe')


def new_offline_package(tenant_name):
    slug = re.sub(r'[^A-Za-z0-9._-]+', '_', str(tenant_name or 'portal_review')).strip('._-').lower()[:40] or 'portal_review'
    name = slug + '_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid4().hex[:8]
    folder = Path('output') / 'assessments' / name
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder.resolve())


def restore_arguments(args, payload):
    """Restore assessment settings; command-line additions remain explicit."""
    from copy import copy
    restored = copy(args)
    for role, paths in (payload or {}).get('resolved_inputs', {}).items():
        current = getattr(restored, role, None)
        if role in LIST_INPUTS:
            setattr(restored, role, list(dict.fromkeys(paths + list(current or []))))
        elif not current:
            setattr(restored, role, paths[0] if paths else None)
    settings = (payload or {}).get('assessment_settings', {})
    if '--report-format' not in getattr(args, '_provided_options', set()) and settings.get('report_format'):
        restored.report_format = settings['report_format']
    restored.evaluation_date = evaluation_day(
        getattr(args, 'evaluation_date', None) or (payload or {}).get('evaluation_date'))
    # User-level workbook detail always requires consent for this rendering.
    return restored


def save_rebuild_recipe(folder, args, settings=None, *, tenant_id=None, tenant_name=None,
                        methodology_migration=None):
    """Retain a successful offline build's additions without saving a collection."""
    if not folder:
        return None
    folder = Path(folder).resolve()
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid4().hex[:8]
    run_folder = folder / 'rebuilds' / run_id
    external = {}
    effective = {}
    for role in sorted(INPUT_KEYS):
        value = getattr(args, role, None)
        if not value:
            continue
        paths = value if isinstance(value, (list, tuple)) else [value]
        effective[role] = [Path(path).resolve() for path in paths]
        outside = [str(path) for path in effective[role] if not path.is_relative_to(folder)]
        if outside:
            external[role] = outside
    manifest, copied = package_inputs(run_folder, external)
    from .cross_provider_assessment import METHODOLOGY_VERSION
    recipe = {'format': REBUILD_FORMAT, 'version': PACKAGE_VERSION, 'evaluation_date': args.evaluation_date,
              'methodology_version': METHODOLOGY_VERSION,
              'tenant_id': tenant_id, 'tenant_name': tenant_name,
              'collection': 'collection.json' if (folder / 'collection.json').is_file() else None,
              'assessment_settings': {key: value for key, value in (settings or {}).items() if key in SETTING_KEYS},
              'inputs': {}, 'files': [], 'diagnostics': manifest['diagnostics']}
    if methodology_migration:
        recipe['original_methodology_version'] = methodology_migration['from']
        methodology_metadata(recipe, 'Rebuild recipe')
    recipe['assessment_settings']['report_format'] = args.report_format
    seen_files = set()
    for role, paths in effective.items():
        recipe['inputs'][role] = []
        for source in paths:
            packaged = source if source.is_relative_to(folder) else run_folder / copied[str(source)]
            recipe['inputs'][role].append(packaged.relative_to(folder).as_posix())
            included = sorted(packaged.iterdir()) if packaged.is_dir() else [packaged]
            if role == 'portal_review':
                from .portal_review import load_portal_review
                included += [Path(path) for path in load_portal_review(packaged, embed_assets=False)['asset_paths']]
            for item in included:
                if not item.is_file() or (role != 'portal_review' and item.suffix.lower() not in DATA_SUFFIXES):
                    continue
                reference = item.relative_to(folder).as_posix()
                if reference in seen_files:
                    continue
                seen_files.add(reference)
                recipe['files'].append({'role': role, 'source_file': item.name, 'path': reference,
                                         'sha256': _digest(item), 'size_bytes': item.stat().st_size})
    encoded = json.dumps(recipe, ensure_ascii=False, indent=2, allow_nan=False)
    (run_folder / 'rebuild.json').write_text(encoded, encoding='utf-8')
    temporary = folder / ('rebuild.' + uuid4().hex[:8] + '.tmp')
    temporary.write_text(encoded, encoding='utf-8')
    temporary.replace(folder / 'rebuild.json')
    return recipe


def render_with_failure_receipt(processor, *, receipt, **arguments):
    """Record rejected imports or rendering errors while retaining collected facts."""
    try:
        return processor(**arguments)
    except Exception as exc:
        record_package_run(**receipt, status='failed', error=str(exc))
        raise


def record_package_run(folder, *, mode, tenant_id, collected_at, evaluation_date,
                       outputs=None, inputs=None, status='complete', error=None, diagnostics=None,
                       collection_input=None, methodology_migration=None):
    """Keep generated artifacts and a receipt without rewriting saved evidence."""
    from .console_reporting import detail, detail_path, is_verbose, print_collection_handoff, print_paragraph
    if not folder:
        if collection_input and Path(collection_input).is_file():
            print_collection_handoff(collection_input)
        return []
    folder = Path(folder)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid4().hex[:8]
    delivered = []
    if isinstance(outputs, dict):
        for key in ('html_path', 'excel_path', 'csv_path', 'snapshot_path'):
            value = outputs.get(key)
            if not isinstance(value, (str, Path)) or not Path(value).is_file():
                continue
            source = Path(value)
            destination = folder / 'deliverables' / run_id / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            delivered.append(destination.relative_to(folder).as_posix())
    receipt = {'run_id': run_id, 'mode': mode, 'tenant_id': tenant_id,
               'collected_at': collected_at, 'evaluation_date': evaluation_date,
               'status': status, 'deliverables': delivered,
               'inputs': inputs or []}
    if methodology_migration:
        receipt['methodology_migration'] = methodology_migration
    receipt['package_diagnostics'] = diagnostics or []
    bundle = outputs.get('evidence_bundle', {}) if isinstance(outputs, dict) else {}
    if isinstance(bundle, dict):
        receipt['import_receipt'] = bundle.get('import_receipt', [])
        assessment = bundle.get('assessment_result', {}) or {}
        if isinstance(assessment, dict) and assessment:
            receipt['remaining_requirements'] = assessment.get('coverage', [])
            receipt['decision'] = assessment.get('decision')
            receipt['counts'] = assessment.get('counts', {})
    if error:
        receipt['error'] = str(error)
    with (folder / 'operator-log.jsonl').open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(receipt, ensure_ascii=False, allow_nan=False) + '\n')
    detail(f'Assessment receipt: tenant {tenant_id or "not identified"}; collected {collected_at or "not supplied"}; evaluation date {evaluation_date}.')
    detail_path('Portable assessment folder', folder)
    # One readable receipt per file; the full individual records stay in JSON/workbook.
    imported = {}
    for item in receipt.get('import_receipt', []):
        entry = imported.setdefault(item.get('Source', 'unnamed'), {'statuses': [], 'reasons': []})
        for field, value in (('statuses', item.get('Status', 'unknown')), ('reasons', item.get('Reason', ''))):
            if value and str(value) not in entry[field]:
                entry[field].append(str(value))
    for source, entry in imported.items():
        needs_attention = any(value.lower() in {
            'rejected', 'unsupported', 'error', 'failed', 'missing', 'unreadable', 'tenant_mismatch',
            'empty', 'unavailable', 'partial', 'identity_unverified',
        } for value in entry['statuses'])
        if is_verbose() or needs_attention:
            print_paragraph(f"Input {source}: {', '.join(entry['statuses'])}", tone='warning' if needs_attention else 'muted')
            for reason in entry['reasons']:
                print_paragraph(reason, indent='  ', tone='warning' if needs_attention else 'muted')
    pdf_references = []
    included_pdfs = {item.get('Source') for item in receipt.get('import_receipt', [])
                     if item.get('Status') in {'automated_reference', 'reviewed_reference'}}
    for item in receipt['package_diagnostics']:
        # Older immutable packages called ordinary PDF captures "unsupported".
        # Explain their intended workflow without rewriting the original receipt.
        if (item.get('role') == 'reports_dir' and Path(item.get('source_file', '')).suffix.lower() == '.pdf'
                and item.get('status') in {'unsupported', 'reference_only'}):
            if item.get('source_file') in included_pdfs:
                continue
            pdf_references.append(item)
            continue
        print_paragraph(f"Input {item.get('source_file', 'unnamed')}: {item.get('status', 'unknown')}; {item.get('reason', '')}", tone='warning')
    if pdf_references:
        print_paragraph(f'PDF captures: {len(pdf_references)} were not included. '
                        'Supply their original folder with --reports-dir for automatic import; check any PDF skipped messages (see docs/PORTAL_REVIEW.md). '
                        'These notices do not stop the report build.', tone='muted')
        for item in pdf_references:
            detail(f"  PDF capture: {item['source_file']}")
    if included_pdfs:
        print_paragraph(f'PDF captures included: {len(included_pdfs)}. See their page previews and extraction qualifications in the report.', tone='success')
    remaining = receipt.get('remaining_requirements')
    if isinstance(remaining, (list, dict)):
        detail(f'Remaining evidence checks: {len(remaining)}. See the report for responsible roles and completion evidence.')
    if delivered:
        detail_path('Packaged deliverables', folder / 'deliverables' / run_id)
    candidates = ([Path(collection_input)] if collection_input else []) + [folder / 'collection.json', folder / 'rebuild.json']
    print_collection_handoff(next((path for path in candidates if path.is_file()), None))
    return delivered

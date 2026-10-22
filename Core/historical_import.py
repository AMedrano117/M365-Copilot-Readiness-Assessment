"""Explicit, offline registration of report-only historical Initial assessments.

Source bytes are retained verbatim. Exported conclusions never become a raw
collection or a technically comparable evidence baseline.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
from io import BytesIO
from pathlib import Path
from uuid import UUID

VERSION = '1.0.0'
NOTICE = ('Historical report import: original conclusions are unverified historical context. '
          'No raw collection was imported. Technical improvement, regression and resolution '
          'cannot be established against this baseline. Use the archived original reports; '
          'the import snapshot is a qualified history record, not a newly scored deliverable.')


def _read_source(path, suffixes):
    from .publication_io import linked_path
    source = Path(path).absolute()
    if any(linked_path(part) for part in (source, *source.parents)):
        raise ValueError('Historical sources must not use symbolic links or junctions.')
    if source.suffix.lower() not in suffixes or not source.is_file():
        raise ValueError('Select an existing XLSX workbook and optional HTML files.')
    source = source.resolve()
    try:
        data = source.read_bytes()
    except OSError:
        raise ValueError('Historical source is unreadable.') from None
    return {'path': source, 'bytes': data, 'sha256': hashlib.sha256(data).hexdigest()}


def _day(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace('Z', '+00:00')).date().isoformat()
        except ValueError:
            raise ValueError('Historical evaluation date must be an ISO date or timestamp.') from None


def inspect_historical(args):
    """Read only the selected workbook/folder and companions; allocate no identity."""
    selected = Path(args.import_historical).absolute()
    from .publication_io import linked_path
    if any(linked_path(part) for part in (selected, *selected.parents)):
        raise ValueError('Historical sources must not use symbolic links or junctions.')
    html = list(getattr(args, 'historical_html', None) or [])
    if selected.is_dir():
        candidates = sorted(p for p in selected.iterdir() if p.suffix.lower() == '.xlsx' and not p.name.startswith('~$'))
        if len(candidates) != 1:
            raise ValueError('Historical folder must contain exactly one XLSX workbook; select the workbook explicitly when there are multiple builds.')
        if not html:
            html = sorted(p for p in selected.iterdir() if p.suffix.lower() in {'.html', '.htm'})
        selected = candidates[0]
    sources = [_read_source(selected, {'.xlsx'})]
    for path in html:
        source = _read_source(path, {'.html', '.htm'})
        if source['path'] not in {item['path'] for item in sources}:
            sources.append(source)
    names = [source['path'].name.casefold() for source in sources]
    if len(set(names)) != len(names):
        raise ValueError('Selected historical reports have duplicate filenames; select matching files from one assessment build.')
    from .prior_report_import import _read_workbook
    try:
        prior = _read_workbook(BytesIO(sources[0]['bytes']), False)
    except Exception:
        raise ValueError('Historical workbook must have a supported Recommendations worksheet with Service, Feature and Recommendation columns.') from None
    prior.update(available=True, source_file=sources[0]['path'].name)
    rows = [row for title in ('Run Manifest', 'Run Context')
            for row in prior.get('sheets', {}).get(title, {}).get('rows', [])]
    dates = {_day(row['Value']) for row in rows if row.get('Item') in {'Evaluation Date', 'EvaluatedAt'} and row.get('Value')}
    if len(dates) > 1:
        raise ValueError('Historical workbook has conflicting evaluation dates.')
    explicit_day = getattr(args, 'evaluation_date', None)
    if dates and explicit_day and _day(explicit_day) not in dates:
        raise ValueError('Explicit evaluation date conflicts with the historical workbook.')
    evaluated = next(iter(dates), None) or (_day(explicit_day) if explicit_day else None)
    if not evaluated:
        raise ValueError('Original evaluation date is unrecorded; supply --evaluation-date YYYY-MM-DD. Generation time is not the evaluation date.')
    if date.fromisoformat(evaluated) > datetime.now(timezone.utc).date():
        raise ValueError('Historical evaluation date cannot be in the future.')
    tenants = [row['Value'] for row in rows if row.get('Item') == 'Tenant ID' and row.get('Value')]
    declared = getattr(args, 'tenant_id', None)
    try:
        recorded = {str(UUID(str(value).strip())) for value in tenants}
        tenant = str(UUID(str(declared).strip())) if declared else next(iter(recorded), None)
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Historical tenant ID must be a valid GUID; correct the source metadata or supply --tenant-id if unrecorded.') from None
    if len(recorded) > 1 or recorded and tenant not in recorded:
        raise ValueError('Historical workbook and selected tenant identities conflict.')
    if not tenant:
        raise ValueError('Historical tenant GUID is unrecorded; supply --tenant-id. Customer folder names do not establish identity.')
    original_revision = {row['Item'][len('Producing '):]: row['Value'] for row in rows
        if row.get('Item') in {'Producing commit_sha', 'Producing commit_tree_sha', 'Producing branch',
                              'Producing source_state', 'Producing qualification'} and row.get('Value')}
    prior['tenant_id'] = tenant
    return {'prior': prior, 'sources': sources, 'tenant_id': tenant, 'evaluation_date': evaluated,
            'tenant_identity_basis': 'workbook-recorded; not authenticated' if recorded else 'operator-declared; not source-verified',
            'date_basis': 'workbook-recorded' if dates else 'operator-declared',
            'original_producing_revision': original_revision or None}


def normalize_historical(args):
    """Normalize before ordinary defaults can assign today's evaluation date."""
    info = inspect_historical(args)
    previous = getattr(args, '_historical_import', None)
    if previous and [s['sha256'] for s in previous['sources']] != [s['sha256'] for s in info['sources']]:
        raise ValueError('Historical source changed after preflight; restart the import.')
    args._historical_import = info
    args.tenant_id, args.evaluation_date = info['tenant_id'], info['evaluation_date']
    return args


def run_historical_import(args):
    from .repository_provenance import require_publication
    require_publication()
    from .run_progress import phase
    phase('inputs')
    normalize_historical(args)
    info = args._historical_import
    from .publication_io import validate_destination, exclusive_output, write_text
    requested = getattr(args, 'historical_output_dir', None)
    if requested:
        package = validate_destination(requested)
        # Reject destinations inside source folders: this operation preserves inputs.
        if any(package.is_relative_to(s['path'].parent) for s in info['sources']):
            raise ValueError('Choose a new historical output directory outside the source report folders.')
        package.mkdir(parents=True, exist_ok=False)
    else:
        from .export_paths import new_assessment_directory
        package = new_assessment_directory(customer_name=getattr(args, 'customer_name', None),
            tenant_name=info['prior'].get('tenant_name'), tenant_id=info['tenant_id'])
    phase('reports')
    archived = []
    for index, source in enumerate(info['sources']):
        # Preserve relative summary/detail/workbook links without rewriting HTML.
        name = source['path'].name
        locator = 'inputs/historical/' + name
        with exclusive_output(package / locator, binary=True) as handle:
            handle.write(source['bytes'])
        archived.append({'role': 'workbook' if index == 0 else 'html', 'locator': locator,
                         'sha256': source['sha256'], 'bytes': len(source['bytes'])})
    from .assessment_runs import prepare_run, complete_run
    execution = prepare_run('Initial', info['tenant_id'], evaluated_at=info['evaluation_date'],
        history_path=package / 'assessment-history.json', purpose=getattr(args, 'assessment_purpose', None))
    receipt = {'schema_version': VERSION, 'assessment_id': execution.identity['AssessmentId'],
        'run_id': execution.identity['RunId'], 'origin': 'historical_report_import', 'notice': NOTICE,
        'original_evaluation_date': info['evaluation_date'], 'imported_at': execution.identity['CreatedAt'],
        'tenant_identity_basis': info['tenant_identity_basis'], 'evaluation_date_basis': info['date_basis'],
        'original_methodology_version': info['prior'].get('methodology_version') or 'Unrecorded',
        'original_assessment_version': info['prior'].get('assessment_version') or 'Unrecorded',
        'original_generation_time': info['prior'].get('generated_at') or 'Unrecorded',
        'original_producing_revision': info['original_producing_revision'] or 'Unrecorded',
        'original_provenance_basis': 'Workbook metadata, unverified; current producing revision identifies the importer only',
        'raw_collection_available': False, 'technical_delta_eligible': False,
        'recommendation_count': len(info['prior']['recommendations']), 'sources': archived}
    execution.identity['RunContext']['HistoricalImport'] = deepcopy(receipt)
    # Deliberately exclude worksheet evidence and old coverage from current evaluation.
    prior = {key: info['prior'][key] for key in ('source_file', 'tenant_id', 'generated_at', 'methodology_version', 'recommendations')}
    from .assessment_result import build_assessment_result
    result = build_assessment_result([], {'prior_report': prior,
        'collection_context': {'identity': execution.identity, 'mode': 'historical import',
            'evaluation_date': info['evaluation_date'], 'has_tenant_collection': False}},
        evaluation_date=info['evaluation_date'], expected_tenant_id=info['tenant_id'])
    result['historical_import'] = receipt
    result['collection_coverage'] = {'historical_reports': {'state': 'unverified', 'complete': False}}
    write_text(package / 'import-receipt.json', json.dumps(receipt, indent=2, ensure_ascii=True))
    from .reassessment_cli import reassessment_command
    follow_up = reassessment_command(package / 'assessment-history.json', execution.identity['RunId'])
    follow_up += " --env-file '<customer.env>'"
    write_text(package / 'HISTORICAL-IMPORT.txt', NOTICE + '\n\n'
        'Original reports: inputs/historical/ (HTML is archived, never executed by the importer).\n'
        'Use assessment-history.json with --reassess; do not populate history manually.\n'
        'Select a later evidence-backed run as baseline when technical delta is required.\n\n'
        'Future reassessment (replace the environment-file placeholder):\n' + follow_up + '\n')
    snapshot = complete_run(result, execution, package)
    from .console_reporting import status
    status(NOTICE, 'warning')
    status(f'Historical Initial registered: {package}')
    return {'package': package, 'snapshot': Path(snapshot), 'result': result}


def validate_archives(result, package):
    """Verify archived report bytes before an imported Initial is selected."""
    from .publication_io import linked_path
    receipt = result['historical_import']
    if receipt.get('schema_version') != VERSION or not receipt.get('sources'):
        raise ValueError('Historical import archive metadata is invalid.')
    for source in receipt['sources']:
        locator = source.get('locator', '')
        relative = Path(locator)
        target = Path(package) / relative
        if (not locator or relative.is_absolute() or '..' in relative.parts
                or relative.parts[:2] != ('inputs', 'historical')
                or any(linked_path(part) for part in (target, *target.parents))):
            raise ValueError('Historical archive locator is invalid.')
        try:
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            raise ValueError('Historical archive is missing or unreadable.') from None
        if digest != source.get('sha256'):
            raise ValueError('Historical archive integrity mismatch.')

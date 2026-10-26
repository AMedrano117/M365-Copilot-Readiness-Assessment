"""Render a persisted semantic snapshot without evaluating evidence or history."""
from pathlib import Path
import hashlib
from .assessment_serialization import read_assessment_result
from .assessment_runs import run_context_rows


def render_snapshot(snapshot_path, output_directory, *, tenant_name=None, report_format='both',
                    snapshot_output=None, extra_exports=None, retained_package=None, customer_name=None):
    from .repository_provenance import require_publication
    require_publication()
    source = Path(snapshot_path).resolve()
    result = read_assessment_result(source)
    if result.get('historical_import') or (result.get('run_context') or {}).get('HistoricalImport'):
        raise ValueError('Historical import snapshots are qualified history records. Use the archived original XLSX/HTML reports; they cannot be rendered as newly scored assessments.')
    from .repository_provenance import current_revision
    result['replay_tool_revision'] = current_revision()
    from .retained_workbook_sources import package_native_sources
    retained_sheets = {}
    retained_selection = {}
    source_package = source.parent.parent if source.parent.name == 'assessment-results' else source.parent
    sources = package_native_sources(retained_package or source_package, result, retained_sheets, retained_selection)
    if snapshot_output:
        target = Path(snapshot_output).resolve()
        if target.exists():
            raise ValueError('Snapshot render cannot overwrite an existing snapshot or input; select a new output path.')
        from .assessment_serialization import write_assessment_result
        write_assessment_result(target,result)
    from .export_paths import new_numbered_directory, new_deliverables_directory, legacy_report_layout, reserve_report_names
    from .export_recommendations import export_to_excel, export_to_html, export_to_csv
    provenance = {'original_producing_revision':result['producing_revision'],
        'replay_tool_revision':result['replay_tool_revision'],'tenant_recollected':False,
        'source_snapshot_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
    legacy = legacy_report_layout(output_directory)
    if legacy:
        folder = new_numbered_directory(Path(output_directory) / 'Renders')
        names = {'report_stem':'Assessment', 'summary_name':'Readiness Summary.html',
                 'evidence_folder_name':'Evidence', 'number':int(folder.name)}
    else:
        folder = new_deliverables_directory(output_directory)
        names = reserve_report_names(folder, tenant_name=tenant_name, customer_name=customer_name,
                                     tenant_id=result.get('tenant_id'), provenance=provenance)
    from .assessment_history import atomic_write
    provenance_path = folder / 'render-provenance.json' if legacy else names['reservation']
    if legacy:
        atomic_write(provenance_path, {'schema_version':'1.0.0', **provenance}, immutable=True)
    stem = names['report_stem']
    bundle = {'assessment_result':result,'control_results':result.get('control_results',[]),
        'assessment_sources': sources,
        'sheets': retained_sheets,
        'retained_investigations': 'investigation_items' in retained_sheets,
        'retained_record_selection': retained_selection,
        'run_manifest':{'rows':run_context_rows(result)},
        'collection_context':{'mode':'snapshot render','identity':result.get('identity'),
                              'evaluation_date':result.get('evaluation_date')}}
    rows = result.get('recommendations',[])
    for key in ('ai_baseline_review', 'copilot_admin_review', 'evidence_requests', 'purview_billing_guidance'):
        if key in (result.get('supplemental_reviews') or {}):
            bundle[key] = result['supplemental_reviews'][key]
    excel = export_to_excel(rows,filename=stem+'.xlsx',tenant_name=tenant_name,
                           evidence_bundle=bundle,output_dir=folder) if report_format in {'both','excel'} else None
    csv = export_to_csv(rows,filename=stem+'.csv',tenant_name=tenant_name,output_dir=folder,assessment_result=result
                       ) if report_format in {'both','csv'} else None
    if 'evidence-pages' in set(extra_exports or ()):
        from .evidence_selection import build_evidence_selection
        from .finding_evidence import build_finding_evidence
        from .html_evidence_pages import write_html_evidence_pages
        model = bundle.get('finding_evidence') or build_finding_evidence(build_evidence_selection(result,bundle))
        bundle['finding_evidence'] = model
        bundle['html_evidence_folder'] = names['evidence_folder_name']
        pages = write_html_evidence_pages(model,folder / names['evidence_folder_name'],report_name=stem+'.html',
            workbook_name=Path(excel).name if excel else None,
            technical_workbook_name=Path(bundle['technical_excel_path']).name if bundle.get('technical_excel_path') else None,assessment_result=result)
        bundle['html_evidence_folder_path'] = pages['folder']
    html = export_to_html(rows,filename=stem+'.html',tenant_name=tenant_name,
        evidence_bundle=bundle,excel_path=excel,output_dir=folder,summary_filename=names['summary_name'])
    return {'report_directory':str(folder),'html_path':html,'excel_path':excel,'csv_path':csv,
        'build_number':names['number'], 'render_provenance_path':str(provenance_path),
        'snapshot_path':str(snapshot_output) if snapshot_output else None,
        'technical_excel_path':bundle.get('technical_excel_path'),
        'html_evidence_folder_path':bundle.get('html_evidence_folder_path'),
        'summary_html_path':bundle.get('summary_html_path'),'evidence_bundle':bundle}


def load_completed_package(payload):
    """Read a retained run manifest only; preserve collection/input bytes."""
    folder = Path(payload['package_directory']).resolve()
    manifest_path = folder / 'assessment-run.json'
    if not manifest_path.is_file():
        return payload
    import json
    from .assessment_package import confined_path
    from .assessment_history import file_hash
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    if manifest.get('SchemaVersion')!='1.0.0':
        raise ValueError('Unsupported completed-run package manifest.')
    snapshot = confined_path(folder,manifest.get('SnapshotLocator'))
    if not snapshot.is_file() or file_hash(snapshot)!=manifest.get('SnapshotHash'):
        raise ValueError('Completed-run package snapshot integrity mismatch.')
    result = read_assessment_result(snapshot)
    stored_provenance=json.loads(snapshot.read_text(encoding='utf-8-sig')).get('producing_revision')
    if 'ProducingRevision' in manifest and manifest['ProducingRevision'] != stored_provenance:
        raise ValueError('Completed-run manifest and snapshot producing revisions differ.')
    meta, seed = result['identity'], manifest.get('Identity') or {}
    from .assessment_identity import execution_metadata
    if execution_metadata(meta)!=seed:
        raise ValueError('Completed-run manifest and snapshot identities differ.')
    original = payload.get('identity') or {}
    for key in ('AssessmentId','RunId','PrimaryEnvironmentId'):
        if original.get(key) and original[key]!=meta.get(key):
            raise ValueError('Collection and completed-run ownership differ.')
    payload['identity'] = execution_metadata(meta)
    payload['completed_run_snapshot'] = str(snapshot)
    return payload

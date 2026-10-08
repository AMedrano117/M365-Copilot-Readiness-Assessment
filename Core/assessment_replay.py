"""Render a persisted semantic snapshot without evaluating evidence or history."""
from pathlib import Path
from .assessment_serialization import read_assessment_result
from .assessment_runs import run_context_rows


def render_snapshot(snapshot_path, output_directory, *, tenant_name=None, report_format='both',
                    snapshot_output=None, extra_exports=None):
    source = Path(snapshot_path).resolve()
    result = read_assessment_result(source)
    if snapshot_output:
        target = Path(snapshot_output).resolve()
        if target.exists():
            raise ValueError('Snapshot render cannot overwrite an existing snapshot or input; select a new output path.')
        from .assessment_serialization import write_assessment_result
        write_assessment_result(target,result)
    from .export_paths import new_numbered_directory
    from .export_recommendations import export_to_excel, export_to_html, export_to_csv
    folder = new_numbered_directory(Path(output_directory) / 'Renders')
    bundle = {'assessment_result':result,'control_results':result.get('control_results',[]),
        'run_manifest':{'rows':run_context_rows(result)},
        'collection_context':{'mode':'snapshot render','identity':result.get('identity'),
                              'evaluation_date':result.get('evaluation_date')}}
    rows = result.get('recommendations',[])
    excel = export_to_excel(rows,filename='Assessment.xlsx',tenant_name=tenant_name,
                           evidence_bundle=bundle,output_dir=folder) if report_format in {'both','excel'} else None
    csv = export_to_csv(rows,filename='Assessment.csv',tenant_name=tenant_name,output_dir=folder
                       ) if report_format in {'both','csv'} else None
    if 'evidence-pages' in set(extra_exports or ()):
        from .evidence_selection import build_evidence_selection
        from .finding_evidence import build_finding_evidence
        from .html_evidence_pages import write_html_evidence_pages
        model = bundle.get('finding_evidence') or build_finding_evidence(build_evidence_selection(result,bundle))
        bundle['finding_evidence'] = model
        bundle['html_evidence_folder'] = 'Evidence'
        pages = write_html_evidence_pages(model,folder / 'Evidence',report_name='Assessment.html',
            workbook_name=Path(excel).name if excel else None,
            technical_workbook_name=Path(bundle['technical_excel_path']).name if bundle.get('technical_excel_path') else None)
        bundle['html_evidence_folder_path'] = pages['folder']
    html = export_to_html(rows,filename='Assessment.html',tenant_name=tenant_name,
        evidence_bundle=bundle,excel_path=excel,output_dir=folder,summary_filename='Readiness Summary.html')
    return {'report_directory':str(folder),'html_path':html,'excel_path':excel,'csv_path':csv,
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

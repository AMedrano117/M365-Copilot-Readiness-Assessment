"""Explicit execution boundaries; renderers never mint IDs or append history."""
from copy import deepcopy
from dataclasses import dataclass
import os
from pathlib import Path
from .assessment_identity import new_identity, new_run, environment_id as verified_environment, execution_metadata
from .assessment_history import create_history, read_history, select_baseline, append_run, atomic_write, file_hash
from .run_comparability import evaluate_comparability, not_evaluated, recorded_boundaries, recorded_coverage

VERSION = '1.0.0'
RUN_TYPES = {'Initial','Reassessment','Standalone'}


@dataclass
class RunExecution:
    identity: dict
    history_path: object = None
    baseline: object = None
    expected_hash: object = None
    is_new: bool = True


def prepare_run(run_type, tenant_id, *, evaluated_at, history_path=None, assessment_id=None,
                baseline_run_id=None, purpose=None, catalog_version=None, environment_id=None,
                existing_identity=None, methodology_version=None):
    if run_type=='Replay':
        if not existing_identity:
            raise ValueError('Replay requires an existing persisted run identity.')
        if assessment_id or baseline_run_id or history_path:
            raise ValueError('Replay cannot change assessment ownership, baseline or history.')
        if tenant_id and existing_identity.get('PrimaryEnvironmentId') and (
                verified_environment(tenant_id)!=existing_identity['PrimaryEnvironmentId']):
            raise ValueError('Replay environment ownership mismatch.')
        return RunExecution(deepcopy(existing_identity),is_new=False)
    if run_type not in RUN_TYPES:
        raise ValueError('Invalid run type; choose Initial, Reassessment or Standalone.')
    if run_type!='Reassessment' and baseline_run_id:
        raise ValueError('Initial and Standalone cannot select a baseline.')
    if run_type=='Initial' and assessment_id:
        raise ValueError('Initial creates its AssessmentId; continuation must be explicit.')
    if run_type=='Reassessment' and not (assessment_id and baseline_run_id and history_path):
        raise ValueError('Reassessment requires assessment ID, explicit baseline run ID and selected history.')
    if assessment_id and not history_path:
        raise ValueError('Explicit assessment continuation requires its history for ownership validation.')
    env = verified_environment(tenant_id)
    if environment_id and environment_id!=env:
        raise ValueError('Verified primary environment does not match the selected tenant.')
    from .cross_provider_assessment import METHODOLOGY_VERSION
    from .assessment_identity import digest
    from .assessment_catalog import DOMAIN_SPECS
    from .observation_reconciliation import VERSION as RECONCILIATION_VERSION
    methodology_version = methodology_version or METHODOLOGY_VERSION
    catalog_version = catalog_version or 'catalog-' + digest(DOMAIN_SPECS)
    path = Path(history_path).resolve() if history_path else None
    baseline, history = None, None
    if assessment_id:
        history = read_history(path)
        if history.get('State')=='legacy':
            raise ValueError('Legacy history cannot establish assessment continuity.')
        if history['AssessmentId']!=assessment_id or history['PrimaryEnvironmentId']!=env:
            raise ValueError('Assessment history ownership or environment mismatch.')
        owner = dict(history,State='complete')
        seed = new_run(owner,tenant_id,evaluated_at=evaluated_at)
        seed.update(MethodologyVersion=methodology_version,CatalogVersion=catalog_version)
        if run_type=='Reassessment':
            baseline = select_baseline(path,assessment_id=assessment_id,environment_id=env,
                baseline_run_id=baseline_run_id,current_run_id=seed['RunId'],
                current_sequence=len(history['Runs'])+1,current_created_at=seed['CreatedAt'])
        purpose = purpose or history.get('Purpose')
    else:
        if path and path.exists():
            raise ValueError('Existing history cannot be claimed without explicit AssessmentId.')
        seed = new_identity(tenant_id,methodology_version=methodology_version,
                            evaluated_at=evaluated_at,catalog_version=catalog_version)
    seed.update(RunType=run_type,BaselineRunId=baseline_run_id)
    if run_type=='Initial' and path is None:
        path = (Path('AssessmentHistories') / seed['AssessmentId'] / 'assessment-history.json').resolve()
    purpose = purpose or 'm365-copilot-readiness'
    context = {key:seed.get(key) for key in ('AssessmentId','RunId','PrimaryEnvironmentId',
        'RunType','BaselineRunId','EvaluatedAt','CreatedAt','MethodologyVersion','CatalogVersion')}
    context.update(SchemaVersion=VERSION,IdentitySchemaVersion=seed['SchemaVersion'],
        ReconciliationVersion=RECONCILIATION_VERSION,Purpose=purpose,
        HistoryReference=path.name if path else None,Comparability=not_evaluated())
    if baseline is not None:
        entry = next(row for row in history['Runs'] if row['RunId']==baseline_run_id)
        context['BaselineValidation'] = {key:entry[key] for key in
            ('AssessmentId','RunId','PrimaryEnvironmentId','SnapshotHash','Sequence')}
        context['BaselineValidation'].update(Status='validated',HistoryIntegrity=history['Integrity'])
    seed['RunContext'] = context
    if path:
        if history is None:
            history = create_history(path,seed,purpose)
        atomic_write(path.parent / 'run-seeds' / (seed['RunId'] + '.json'),execution_metadata(seed))
    return RunExecution(seed,path,baseline,history['Integrity'] if history else None)


def complete_run(result, execution, package_path):
    """Persist a completed semantic snapshot before any renderer publishes."""
    if not execution.is_new:
        return None
    package = Path(package_path).resolve()
    meta = result['identity']
    for field in ('AssessmentId','RunId','PrimaryEnvironmentId','RunType','BaselineRunId','EvaluatedAt'):
        if meta.get(field)!=execution.identity.get(field):
            raise ValueError('Completed result differs from prepared execution identity.')
    context = deepcopy(execution.identity['RunContext'])
    result['run_boundaries'] = recorded_boundaries(result,context['Purpose'])
    result['collection_coverage'] = recorded_coverage(result)
    context['Comparability'] = evaluate_comparability(result,execution.baseline)
    context['HistoryReference'] = Path(os.path.relpath(execution.history_path,package)).as_posix() if execution.history_path else None
    result['run_context'] = context
    meta['RunContext'] = deepcopy(context)
    from .assessment_serialization import write_assessment_result
    from .assessment_references import require_valid_assessment
    result['identity_validation'] = require_valid_assessment(result)
    package.mkdir(parents=True,exist_ok=True)
    atomic_write(package / 'run-seed.json',execution_metadata(meta))
    snapshot = package / 'assessment-results' / (meta['RunId'] + '.json')
    if snapshot.exists():
        raise ValueError('A completed semantic snapshot cannot be overwritten.')
    write_assessment_result(snapshot,result)
    if execution.history_path:
        append_run(execution.history_path,result,snapshot_path=snapshot,package_path=package,
                   expected_hash=execution.expected_hash)
    atomic_write(package / 'assessment-run.json', {
        'SchemaVersion':VERSION,'Identity':execution_metadata(meta),
        'SnapshotLocator':snapshot.relative_to(package).as_posix(),'SnapshotHash':file_hash(snapshot)})
    return str(snapshot)


def validate_run_context(result):
    """Pure semantic diagnostics; filesystem validation happens at execution."""
    diagnostics = []
    def report(code, message, severity='error'):
        diagnostics.append({'severity':severity,'code':code,'subject':'run_context','message':message})
    meta, context = result.get('identity') or {}, result.get('run_context')
    if context is None:
        if meta.get('RunType') or meta.get('BaselineRunId'):
            report('run_context_missing','Explicit run identity requires its recorded workflow context.')
        else:
            report('legacy_run_intent','Run intent is unrecorded; no Initial classification or baseline inferred.',
                   'compatibility_warning')
        return diagnostics
    if not isinstance(context,dict) or context.get('SchemaVersion')!=VERSION:
        report('run_context_schema','Unsupported or malformed run workflow context.')
        return diagnostics
    for field in ('AssessmentId','RunId','PrimaryEnvironmentId','RunType','BaselineRunId','EvaluatedAt','CreatedAt',
                  'MethodologyVersion','CatalogVersion'):
        if context.get(field)!=meta.get(field):
            report('run_context_identity_mismatch','Workflow context and identity differ for ' + field + '.')
    if context.get('IdentitySchemaVersion')!=meta.get('SchemaVersion'):
        report('run_context_identity_mismatch','Workflow identity schema differs from persisted identity.')
    if meta.get('State')!='complete' or not context.get('AssessmentId') or not context.get('RunId') or not context.get('PrimaryEnvironmentId'):
        report('run_identity_missing','Explicit workflow requires verified assessment, run and environment identity.')
    mode, baseline = context.get('RunType'), context.get('BaselineRunId')
    if mode not in RUN_TYPES:
        report('run_type_invalid','Initial, Reassessment and Standalone are the only semantic run types.')
    if mode=='Reassessment' and not baseline:
        report('baseline_required','Reassessment requires an explicitly selected baseline.')
    if mode!='Reassessment' and baseline:
        report('baseline_unintended','Initial and Standalone cannot select a baseline.')
    if baseline and baseline==context.get('RunId'):
        report('baseline_self','Baseline cannot be the current run.')
    if mode in {'Initial','Reassessment'} and not context.get('HistoryReference'):
        report('history_reference_missing','Initial and Reassessment require recorded history references.')
    if mode=='Reassessment':
        verification = context.get('BaselineValidation') or {}
        if (not isinstance(verification,dict) or verification.get('Status')!='validated' or verification.get('RunId')!=baseline
                or any(verification.get(key)!=context.get(key) for key in ('AssessmentId','PrimaryEnvironmentId'))
                or not verification.get('SnapshotHash') or not verification.get('HistoryIntegrity')):
            report('baseline_validation_missing','Reassessment lacks verified baseline ownership and integrity metadata.')
    comparison = context.get('Comparability')
    from .run_comparability import OUTCOMES, ELIGIBILITY
    if not isinstance(comparison,dict) or comparison.get('Outcome') not in OUTCOMES:
        report('comparability_invalid','Comparability outcome is missing or invalid.')
        return diagnostics
    outcome = comparison['Outcome']
    if mode=='Reassessment' and outcome=='NotEvaluated':
        report('comparison_not_evaluated','Prepared reassessment must evaluate comparability before publication.')
    if outcome!='Comparable' and not comparison.get('Reasons'):
        report('comparability_reasons_missing','A qualified, blocked or unevaluated comparison requires reasons.')
    reasons = comparison.get('Reasons')
    if not isinstance(reasons,list) or any(not isinstance(row,dict) or
            any(not row.get(key) for key in ('Code','Area','Detail')) for row in reasons):
        report('comparability_reasons_invalid','Comparability reasons must be structured codes, affected areas and details.')
    if mode!='Reassessment' and outcome!='NotEvaluated':
        report('comparison_unintended','No-baseline runs cannot report evaluated comparability.')
    items = comparison.get('Items')
    if not isinstance(items,list):
        report('item_eligibility_invalid','Item comparison eligibility must be a list.')
        return diagnostics
    for item in items:
        if not isinstance(item,dict) or item.get('Eligibility') not in ELIGIBILITY:
            report('item_eligibility_invalid','Item comparison eligibility is invalid.')
        elif item['Eligibility']!='eligible' and not item.get('Reasons'):
            report('item_eligibility_reasons_missing','Qualified or ineligible items require recorded reasons.')
    if meta.get('RunContext') is not None and meta['RunContext']!=context:
        report('run_context_copy_mismatch','Persisted execution context and shared result differ.')
    return diagnostics


def run_context_rows(result):
    context = result.get('run_context') or {}
    if not context:
        return [{'Item':'Run intent','Value':'Unrecorded legacy workflow; no baseline inferred'}]
    rows = [{'Item':key,'Value':context.get(key)} for key in
        ('AssessmentId','RunId','RunType','BaselineRunId','EvaluatedAt','HistoryReference') if context.get(key)]
    comparison = context.get('Comparability') or {}
    rows.append({'Item':'Comparability','Value':comparison.get('Outcome')})
    if comparison.get('Reasons'):
        rows.append({'Item':'Comparability reasons','Value':'; '.join(
            row['Code'] + ': ' + row['Detail'] for row in comparison['Reasons'])})
    return rows


def add_run_manifest_context(bundle, result):
    rows = bundle.setdefault('run_manifest', {}).setdefault('rows', [])
    present = {row.get('Item') for row in rows}
    rows.extend(row for row in run_context_rows(result) if row['Item'] not in present)

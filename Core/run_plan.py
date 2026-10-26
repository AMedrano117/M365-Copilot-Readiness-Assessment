"""Read-only operator plan. No collector imports, UUID allocation or history writes."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from .repository_provenance import capture_revision, publication_policy, runtime_versions

VERSION='1.0.0'
INPUTS=('collection_input','sam_report','dspm_report','reports_dir','copilot_readiness_export',
        'copilot_dashboard_export','power_platform_inventory','assessment_profile','provider_evidence',
        'prior_report','purview_cache','portal_review','ai_baseline_export','baseline','import_historical','historical_html')


def build_run_plan(args, *, revision=None, generated_at=None):
    from .effective_configuration import resolve_effective
    args, saved, resolution_errors = resolve_effective(args)
    get=lambda name,default=None:getattr(args,name,default)
    timestamp=generated_at or datetime.now(timezone.utc).isoformat()
    revision=deepcopy(revision or capture_revision(config=args,generated_at=timestamp))
    from .repository_provenance import configuration_hash, collector_configuration_hash
    revision['run_configuration_hash']=configuration_hash(args)
    revision['collector_configuration_hash']=collector_configuration_hash(vars(args))
    policy=publication_policy(revision,policy=get('source_policy','production'),purpose=get('assessment_purpose'),
        reason=get('source_reason'),acknowledged=get('acknowledge_dirty_source',False))
    revision['publication']=policy
    from .repository_provenance import normalize_revision
    normalized=normalize_revision(revision)
    if normalized!=revision:
        revision=normalized
        revision['run_configuration_hash']=configuration_hash(args)
        revision['collector_configuration_hash']=collector_configuration_hash(vars(args))
        policy=publication_policy(revision,policy=get('source_policy','production'),purpose=get('assessment_purpose'),
            reason=get('source_reason'),acknowledged=get('acknowledge_dirty_source',False))
        revision['publication']=policy
    intent=get('run_type'); replay=bool(get('replay_snapshot'))
    stored_result=None; metadata_errors=list(resolution_errors)
    if get('collection_input') and not intent and not metadata_errors:
        try:
            from .assessment_replay import load_completed_package
            saved=load_completed_package(saved)
            if saved.get('completed_run_snapshot'):
                replay=True
                from .assessment_serialization import read_assessment_result
                stored_result=read_assessment_result(saved['completed_run_snapshot'])
        except (ValueError,OSError,KeyError,TypeError):metadata_errors.append('saved_collection_metadata_invalid')
    if get('replay_snapshot'):
        try:
            from .assessment_serialization import read_assessment_result
            stored_result=read_assessment_result(get('replay_snapshot'))
        except (ValueError,OSError,KeyError,TypeError):metadata_errors.append('replay_snapshot_invalid')
    historical = get('_historical_import')
    if stored_result and (stored_result.get('historical_import') or (stored_result.get('run_context') or {}).get('HistoricalImport')):
        metadata_errors.append('historical_import_use_archived_original_reports')
    baseline_id=get('baseline_run_id'); assessment_id=get('assessment_id'); errors=metadata_errors; warnings=[]
    baseline={'run_id':baseline_id,'completion':'not_selected','integrity':'not_evaluated','ownership':'not_evaluated'}
    if replay:
        intent='Replay'
        if get('confirm_export_tenant'):errors.append('replay_cannot_confirm_export_identity')
        if baseline_id or assessment_id or get('assessment_history'):errors.append('replay_cannot_change_ownership_or_baseline')
        if stored_result:
            identity=stored_result.get('identity') or {}
            assessment_id=identity.get('AssessmentId')
            baseline_id=identity.get('BaselineRunId')
            baseline['run_id']=baseline_id
    elif intent=='Initial':
        if baseline_id:errors.append('initial_cannot_select_baseline')
        if assessment_id:errors.append('initial_creates_assessment_id')
        if get('assessment_history') and Path(get('assessment_history')).exists():errors.append('initial_history_already_exists')
    elif intent=='Reassessment':
        if not assessment_id:errors.append('reassessment_assessment_id_required')
        if not baseline_id:errors.append('run_plan_baseline_missing')
        if not get('assessment_history'):errors.append('reassessment_history_required')
    elif intent=='Standalone':
        if baseline_id:errors.append('standalone_cannot_select_baseline')
        if assessment_id and not get('assessment_history'):errors.append('continuation_history_required')
    elif intent:
        errors.append('invalid_run_type')
    else:
        warnings.append('Legacy run intent is unclassified; Initial and a baseline are not inferred.')
    if get('import_historical'):
        from .historical_import import NOTICE
        warnings.append(NOTICE)
        if get('_historical_error'):warnings.append(get('_historical_error'))
    env=None
    if not (get('offline') or replay) and not get('tenant_id'):
        errors.append('live_tenant_selection_required')
    if intent in {'Initial','Reassessment','Standalone'}:
        from .assessment_identity import environment_id
        try:env=environment_id(get('tenant_id') or saved.get('tenant_id'))
        except ValueError:errors.append('verified_tenant_required_for_explicit_run')
        if get('primary_environment_id') and get('primary_environment_id')!=env:errors.append('environment_ownership_mismatch')
    if intent=='Reassessment' and assessment_id and baseline_id and get('assessment_history') and env:
        from .assessment_history import read_history,select_baseline
        try:
            history=read_history(get('assessment_history'))
            selected=select_baseline(get('assessment_history'),assessment_id=assessment_id,environment_id=env,
                baseline_run_id=baseline_id,current_run_id=None,current_sequence=len(history['Runs'])+1,
                current_created_at=timestamp)
            baseline.update(completion='completed',integrity='validated',ownership='validated',
                producing_revision=selected.get('producing_revision',{}).get('commit_sha'),
                methodology_version=selected.get('methodology_version'))
            versions=revision
            old_context=selected.get('run_context') or {}
            changes=[]
            for old_key,new_key in (('MethodologyVersion','methodology_version'),('CatalogVersion','catalog_version')):
                if old_context.get(old_key)!=versions.get(new_key):changes.append(old_key)
            old_revision=selected.get('producing_revision') or {}
            for key in ('taxonomy_version','import_adapters'):
                if old_revision.get(key) and old_revision[key]!=revision.get(key):changes.append(key)
            baseline['version_changes']=changes
            if selected.get('historical_import') or old_context.get('HistoricalImport'):
                baseline['technical_delta_eligible'] = False
                warnings.append('Historical report baseline: technical delta will be NotComparable; no improvement or resolution claims.')
            if changes:warnings.append('Baseline versions differ: comparison eligibility will be evaluated at completion.')
        except (ValueError,OSError,KeyError):
            baseline.update(completion='not_validated',integrity='failed',ownership='not_validated')
            errors.append('baseline_validation_failed')
    elif assessment_id and get('assessment_history') and intent=='Standalone' and env:
        from .assessment_history import read_history
        try:
            history=read_history(get('assessment_history'))
            if history.get('State')=='legacy' or history.get('AssessmentId')!=assessment_id or history.get('PrimaryEnvironmentId')!=env:
                errors.append('assessment_history_ownership_mismatch')
        except (ValueError,OSError):errors.append('assessment_history_unreadable')
    delta_enabled=intent=='Reassessment' and get('delta_mode')!='disabled'
    delta_expected=delta_enabled and baseline['completion']=='completed' and not errors
    reason=('Explicit Reassessment with validated baseline; completion evaluates comparability and delta.' if delta_expected else
            'Delta explicitly disabled.' if intent=='Reassessment' and not delta_enabled else
            'Reassessment baseline or run validation is incomplete.' if intent=='Reassessment' else
            'Replay displays retained lifecycle without recalculating delta.' if replay else
            'No explicit reassessment baseline; technical delta is not evaluated.')
    if baseline.get('technical_delta_eligible') is False and delta_enabled:
        delta_expected=False
        reason='Historical report baseline: technical delta is NotComparable; no improvement, regression or resolution claims.'
    if get('delta_mode')=='enabled' and intent!='Reassessment':errors.append('delta_requested_without_reassessment')
    if get('delta_mode')=='disabled' and intent not in {None,'Reassessment'}:errors.append('delta_setting_requires_reassessment')
    if get('baseline') and intent in {'Initial','Reassessment','Standalone'}:errors.append('legacy_baseline_cannot_mix_with_explicit_run')
    from .publication_io import validate_destination
    for role in ('snapshot_json','save_collection','historical_output_dir'):
        if get(role):
            try:validate_destination(get(role))
            except (ValueError,OSError):errors.append('protected_output_destination')
    if get('historical_output_dir') and historical:
        target = Path(get('historical_output_dir')).resolve()
        if any(target.is_relative_to(source['path'].parent) for source in historical['sources']):
            errors.append('historical_output_inside_source_folder')
    output=get('historical_output_dir') or get('render_output_dir') or get('save_collection') or get('snapshot_json') or 'Reports/<customer>/<date>/Deliverables'
    if get('import_historical') and not get('historical_output_dir'):
        output='Reports/<customer>/<import-date-new-package>'
    if intent=='Reassessment' and baseline['completion']=='completed' and not any((get('render_output_dir'),get('save_collection'),get('snapshot_json'))):
        anchor_id=history.get('InitialRunId') or history['Runs'][0]['RunId']
        anchor=next(row for row in history['Runs'] if row['RunId']==anchor_id)
        package=(Path(get('assessment_history')).resolve().parent/anchor['PackageLocator']).resolve()
        output=package/'Runs'/'<new-number>'/'Deliverables'
    if replay:
        from .export_paths import legacy_report_layout
        package = get('render_output_dir') or saved.get('package_directory')
        if not package and get('replay_snapshot'):
            source = Path(get('replay_snapshot')).resolve()
            package = source.parent.parent if source.parent.name == 'assessment-results' else source.parent
        if package:
            output = Path(package) / ('Renders/<new-number>' if legacy_report_layout(package) else 'Deliverables')
    from .repository_provenance import input_selection_count
    supplied={key:input_selection_count(key,get(key)) for key in INPUTS if get(key)}
    for key,values in (saved.get('inputs') or {}).items():
        if key in INPUTS and key not in supplied and values:supplied[key]=len(values) if isinstance(values,list) else 1
    warnings.extend(revision.get('warnings') or [])
    errors.extend(policy['errors'])
    if replay:
        warnings.append('Original producing revision is retained; current source identifies the replay tool only.')
    if policy['policy']=='engineering-validation':warnings.append(policy['output_qualification'])
    if errors:
        delta_expected=False
        if delta_enabled:reason='Delta blocked by run or publication validation.'
    identity_behavior=('preserve stored identities' if replay else 'reuse supplied AssessmentId with verified history' if assessment_id else
                       'new AssessmentId after preflight' if intent in {'Initial','Standalone'} else 'legacy compatibility identity behavior')
    return dict(schema_version=VERSION,generated_at=timestamp,requested_run_type=intent or 'UnclassifiedLegacy',
        assessment_id=assessment_id,assessment_id_behavior=identity_behavior,
        run_id_behavior='preserve stored RunId' if replay else 'new RunId after preflight; no ID allocated by plan',
        baseline=baseline,delta={'enabled':delta_enabled,'expected':delta_expected,'reason':reason},
        methodology_version=revision.get('methodology_version'),taxonomy_version=revision.get('taxonomy_version'),
        catalog_version=revision.get('catalog_version'),benchmark_catalog_version=revision.get('benchmark_catalog_version'),
        sources={'mode':'offline' if get('offline') or replay else 'live','services':[] if get('import_historical') else sorted(get('services',[]) or ['default selected collectors']),
                 'preview_collectors':get('preview_collectors','auto'),'permission_profile':get('permission_profile') or saved.get('assessment_settings',{}).get('permission_profile') or 'standard',
                 'imports_supplied':supplied,'optional_imports_not_supplied':[key for key in INPUTS if key not in supplied],
                 'tenant_collection':not bool(get('offline') or replay)},
        output_destination=str(output),overwrite_behavior='Completed snapshots and original inputs cannot be overwritten; numbered derivative outputs.',
        producing_revision=revision,publication_eligible=policy['customer_publication_eligible'] and not errors and not replay and not bool(get('import_historical')),
        original_producing_revision=(stored_result or {}).get('producing_revision'),
        source_revision_role='historical_importer' if get('import_historical') else 'replay_tool' if replay else 'producing_assessment',
        historical_import=({'recommendation_count':len(historical['prior']['recommendations']),
            'source_count':len(historical['sources']), 'tenant_identity_basis':historical['tenant_identity_basis'],
            'evaluation_date_basis':historical['date_basis'], 'technical_delta_eligible':False}
            if get('import_historical') and historical else None),
        retained_run_id=((stored_result or {}).get('identity') or {}).get('RunId'),
        effective_configuration={'evaluation_date':args.evaluation_date,'report_format':get('report_format'),
            'settings':args._effective_settings,'interactive_auth':args.interactive_auth,
            'authentication':get('_effective_authentication',{})},
        execution_eligible=not errors,replay=replay,history_mutated=False,completed_run_created=False,
        warnings=sorted(set(warnings)),validation_errors=sorted(set(errors)))


def plan_json(plan): return json.dumps(plan,sort_keys=True,ensure_ascii=True,indent=2,allow_nan=False)


def _output_qualification(plan):
    if not plan['execution_eligible']:
        return 'Blocked'
    policy = plan['producing_revision']['publication']
    if policy['policy'] == 'engineering-validation':
        return policy['output_qualification']
    if plan.get('source_revision_role') == 'historical_importer':
        return 'Historical context only; not a newly scored customer deliverable'
    if plan['replay']:
        original = (plan.get('original_producing_revision') or {}).get('publication') or {}
        if not original.get('customer_publication_eligible'):
            return 'Snapshot replay; ' + original.get('output_qualification', 'original publication eligibility unverified')
        return 'Snapshot replay; original output qualification retained'
    if plan['publication_eligible']:
        return 'Eligible, subject to assessment validation'
    return 'Not eligible for customer publication'


def plan_lines(plan, *, verbose=False):
    if verbose:
        return _detailed_plan_lines(plan)
    sources = plan['sources']
    historical = plan.get('source_revision_role') == 'historical_importer'
    mode = ('Historical import; no tenant collection' if historical else
            'Snapshot replay; no tenant collection or re-evaluation' if plan['replay'] else
            'Live tenant collection' if sources['tenant_collection'] else
            'Offline saved evidence; no tenant collection')
    rows = [('Run type', plan['requested_run_type']), ('Mode', mode)]
    if sources['tenant_collection']:
        services = ', '.join(sources['services']) or 'Default selected collectors'
        rows.append(('Scope', services + '; ' + sources['permission_profile'] + ' permissions'))
    baseline = plan['baseline']
    if plan['requested_run_type'] == 'Reassessment' or baseline['run_id']:
        validated = all(baseline[key] == value for key, value in
                        (('completion', 'completed'), ('integrity', 'validated'), ('ownership', 'validated')))
        rows.append(('Baseline', (baseline['run_id'] or 'Not selected') +
                     (' (validated)' if validated else ' (not validated)')))
    if plan['requested_run_type'] == 'Reassessment':
        delta = plan['delta']
        comparison = ('Disabled' if not delta['enabled'] else
                      'Not comparable; historical report baseline' if baseline.get('technical_delta_eligible') is False else
                      'Eligibility checked at completion' if delta['expected'] else
                      'Blocked by plan validation')
        rows.append(('Comparison', comparison))
    supplied = sources['imports_supplied']
    if supplied:
        rows.append(('Imports', str(sum(supplied.values())) + ' selected input(s)'))
    historical_info = plan.get('historical_import') or {}
    if historical_info:
        rows.append(('Historical tenant identity', historical_info['tenant_identity_basis']))
    evaluation_date = plan['effective_configuration'].get('evaluation_date')
    if evaluation_date:
        if historical_info:
            evaluation_date += ' (' + historical_info['evaluation_date_basis'] + ')'
        rows.append(('Evaluation date', evaluation_date))
    revision = plan['producing_revision']
    state = revision['source_state']
    source = 'Clean committed checkout' if state == 'CleanCommitted' else state
    commit = revision.get('commit_sha')
    if commit:
        source += ' (' + commit[:7] + ')'
    rows += [('Output', plan['output_destination']), ('Source', source),
             ('Publication', _output_qualification(plan)),
             ('Plan check', 'Passed' if plan['execution_eligible'] else 'Blocked')]
    lines = [f'{label}: {value}' for label, value in rows]
    from .historical_import import NOTICE
    for warning in plan['warnings']:
        if warning == 'Original producing revision is retained; current source identifies the replay tool only.':
            continue  # Already stated by the replay mode and publication qualification.
        if warning == revision['publication']['output_qualification']:
            continue  # Keep the engineering-only qualification once, above.
        if warning == NOTICE:
            warning = ('Historical conclusions are unverified. No raw collection was imported; '
                       'technical changes cannot be established. Use the archived original reports.')
        lines.append('Warning: ' + warning)
    lines += ['Error: ' + value for value in plan['validation_errors']]
    lines.append('Details: --verbose or --plan --plan-format json')
    return lines


def _detailed_plan_lines(plan):
    rev=plan['producing_revision']; baseline=plan['baseline']; delta=plan['delta']
    rows=[('Run type',plan['requested_run_type']),('Assessment',plan['assessment_id'] or plan['assessment_id_behavior']),
        ('Run identity',plan.get('retained_run_id') or plan['run_id_behavior']),('Baseline',baseline['run_id'] or 'None'),
        ('Baseline completion',baseline['completion']),('Baseline integrity',baseline['integrity']),('Baseline ownership',baseline['ownership']),
        ('Methodology',plan['methodology_version']),('Taxonomy',plan['taxonomy_version']),('Internal catalog',plan['catalog_version']),
        ('Benchmark catalog',plan['benchmark_catalog_version'] or 'Not implemented'),('Delta enabled',delta['enabled']),('Delta expected',delta['expected']),('Delta reason',delta['reason']),
        ('Source mode',plan['sources']['mode']),('Services',', '.join(plan['sources']['services'])),
        ('Permission profile',plan['sources']['permission_profile']),('Preview collectors',plan['sources']['preview_collectors']),
        ('Run configuration hash',rev.get('run_configuration_hash')),('Collector configuration hash',rev.get('collector_configuration_hash')),
        ('Imports supplied',json.dumps(plan['sources']['imports_supplied'],sort_keys=True)),
        ('Effective configuration',json.dumps(plan['effective_configuration'],sort_keys=True)),
        ('Source policy',rev['publication']['policy']),('Output qualification',_output_qualification(plan)),
        ('Optional imports absent',', '.join(plan['sources']['optional_imports_not_supplied'])),('Output',plan['output_destination']),
        ('Overwrite behavior',plan['overwrite_behavior']),('Importer commit' if plan.get('historical_import') else 'Replay tool commit' if plan['replay'] else 'Producing commit',rev.get('commit_sha') or 'Unavailable'),
        ('Source state',rev['source_state']),('Dirty fingerprint',rev.get('dirty_tree_fingerprint') or 'None'),
        ('Customer publication eligible',plan['publication_eligible']),('Execution eligible',plan['execution_eligible'])]
    if plan.get('historical_import'):
        info=plan['historical_import']
        rows += [('Historical source count',info['source_count']),
                 ('Historical recommendations',info['recommendation_count']),
                 ('Historical tenant identity basis',info['tenant_identity_basis']),
                 ('Historical evaluation date basis',info['evaluation_date_basis']),
                 ('Historical technical delta eligible',False)]
    if plan['replay']:
        original=plan.get('original_producing_revision') or {}
        rows.append(('Original producing commit',original.get('commit_sha') or 'Unrecorded'))
        rows.append(('Original source state',original.get('source_state') or 'Unrecorded'))
    return [f'{label}: {value}' for label,value in rows]+['Warning: '+value for value in plan['warnings']]+['Error: '+value for value in plan['validation_errors']]


def print_run_plan(plan):
    from .console_reporting import section,print_paragraph,is_verbose
    section('PRE-EXECUTION RUN PLAN')
    for line in plan_lines(plan, verbose=is_verbose()):
        tone = 'error' if line.startswith('Error:') else 'warning' if line.startswith('Warning:') else 'info'
        print_paragraph(line, tone=tone)

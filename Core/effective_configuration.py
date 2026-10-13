"""Read-only effective options shared by preflight, hashing and execution."""
from copy import copy
from datetime import date
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

INPUTS = ('sam_report','dspm_report','reports_dir','copilot_readiness_export','copilot_dashboard_export',
          'power_platform_inventory','assessment_profile','provider_evidence','prior_report','purview_cache','portal_review','baseline')
AGES = {'provider_evidence_max_age_days':('PROVIDER_EVIDENCE_MAX_AGE_DAYS',90),
        'sam_report_max_age_days':('SAM_REPORT_MAX_AGE_DAYS',35), 'dspm_report_max_age_days':('DSPM_REPORT_MAX_AGE_DAYS',8)}


def metadata(path):
    from .assessment_package import confined_path, _validate_inputs
    supplied = Path(path).resolve()
    value = json.loads(supplied.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Saved collection metadata must be an object.')
    folder = supplied.parent
    recipe = value if value.get('format') == 'm365-readiness-rebuild' else None
    package = value.get('package') or {}
    if package.get('directory') not in {None, '.'}:
        folder = confined_path(folder, package['directory'])
    if recipe is None and (folder/'rebuild.json').is_file():
        recipe = json.loads((folder/'rebuild.json').read_text(encoding='utf-8-sig'))
    selected = recipe or package
    if selected:
        _validate_inputs(folder, selected)
    collection_metadata={}
    if recipe and recipe.get('collection'):
        collection_metadata=json.loads(confined_path(folder,recipe['collection']).read_text(encoding='utf-8-sig'))
        if not isinstance(collection_metadata,dict):raise ValueError('Collection metadata must be an object.')
    result = dict(collection_metadata, **value)
    result['package_directory']=str(folder)
    if recipe:
        result.update({key:recipe[key] for key in ('tenant_id','evaluation_date','assessment_settings') if key in recipe})
    result['resolved_inputs'] = {role:[str(confined_path(folder,p)) for p in paths]
        for role,paths in selected.get('inputs',{}).items() if role in INPUTS}
    return result


def resolve_effective(args, saved=None):
    result = copy(args)
    for key in ('assessment_purpose','source_reason'):
        value=getattr(result,key,None)
        if hasattr(result,key):setattr(result,key,value.strip() if isinstance(value,str) else None)
    errors = []
    environment = dict(os.environ)
    if not getattr(args,'offline',False):
        from .credentials_check import read_env_file
        try:
            _, selected, _ = read_env_file(getattr(args,'env_file',None))
            environment.update(selected)
        except ValueError:
            errors.append('environment_configuration_invalid')
    if saved is None:
        try: saved = metadata(args.collection_input) if getattr(args,'collection_input',None) else {}
        except (ValueError,OSError,TypeError,KeyError,AttributeError):
            saved = {}; errors.append('saved_collection_metadata_invalid')
    from .assessment_package import restore_arguments
    try: result = restore_arguments(result, saved)
    except (ValueError,OSError,TypeError,AttributeError):
        saved={};errors.append('saved_collection_metadata_invalid')
    candidates = [getattr(result,'tenant_id',None),saved.get('tenant_id')]
    tenants = set()
    for value in candidates:
        try: tenants.add(str(UUID(str(value))))
        except (ValueError,TypeError,AttributeError): pass
    if len(tenants)>1: errors.append('saved_tenant_conflict')
    result.tenant_id = getattr(result,'tenant_id',None) or saved.get('tenant_id') or (environment.get('TENANT_ID') if not getattr(result,'offline',False) else None)
    result.evaluation_date = getattr(result,'evaluation_date',None) or date.today().isoformat()
    result.services = sorted(set(getattr(result,'services',[]) or []))
    if not getattr(result,'offline',False):
        from .collector_registry import normalize_permission_profile
        try:
            result.permission_profile = normalize_permission_profile(getattr(result,'permission_profile',None) or environment.get('PERMISSION_PROFILE') or 'standard')
            if result.permission_profile=='restricted' and (getattr(result,'preview_collectors','auto') not in {'auto','none'} or getattr(result,'legacy_power_platform_collector',False)):
                errors.append('restricted_collectors_invalid')
            if result.permission_profile=='restricted' and getattr(result,'delegated','auto')=='required':
                errors.append('restricted_delegated_invalid')
        except ValueError:
            errors.append('permission_profile_invalid')
        result.sharepoint_admin_url = getattr(result,'sharepoint_admin_url',None) or environment.get('SHAREPOINT_ADMIN_URL') or None
        generic_pfx = environment.get('CERTIFICATE_PATH','').lower().endswith(('.pfx','.p12'))
        result._effective_authentication = {
            'graph_method':'certificate' if environment.get('CERTIFICATE_PATH') else 'client_secret' if environment.get('CLIENT_SECRET') else 'unconfigured',
            'client_id':environment.get('CLIENT_ID') or None,
            'workload_certificates':{prefix:bool(environment.get(prefix+'_CERTIFICATE_PATH') or environment.get(prefix+'_CERTIFICATE_THUMBPRINT') or generic_pfx) for prefix in ('SHAREPOINT','PURVIEW')},
            'purview_organization':environment.get('PURVIEW_ORGANIZATION') or None,
            'expected_tenant_domain':environment.get('EXPECTED_TENANT_DOMAIN') or None,
            'purview_include_specialized':environment.get('PURVIEW_INCLUDE_SPECIALIZED','').strip().lower() in {'1','true','yes'},
            'copilot_audit_max_wait_minutes':environment.get('COPILOT_AUDIT_MAX_WAIT_MINUTES','8'),
            'compatibility_source_selection':{prefix:'file' if environment.get(prefix+'_DATA_SOURCE') else 'json' if environment.get(prefix+'_DATA_JSON') else 'api' for prefix in ('PURVIEW','POWER_PLATFORM')},
        }
    result.interactive_auth = getattr(result,'interactive_auth','auto')
    if getattr(result,'interactive_collection_policy',None)=='skip': result.interactive_auth='skip'
    elif getattr(result,'purview_auth_mode',None)=='force': result.interactive_auth='fresh'
    settings = dict(saved.get('assessment_settings') or {})
    environment_values = environment
    for key,(variable,default) in AGES.items():
        value = getattr(result,key,None)
        try: settings[key] = int(value if value is not None else settings.get(key,environment_values.get(variable,default)))
        except (ValueError,TypeError):
            settings[key] = default; errors.append('invalid_freshness_setting')
        if settings[key]<=0: errors.append('invalid_freshness_setting')
    from .lifecycle_report_settings import lifecycle_settings
    try:
        settings = lifecycle_settings(settings,max_age_days=getattr(result,'lifecycle_report_max_age_days',None),
            report_dates=getattr(result,'lifecycle_report_date',None),evaluation_date=result.evaluation_date,environment=environment_values)
    except (ValueError,OSError,TypeError):
        errors.append('invalid_lifecycle_settings')
    for key in ('sam_report','dspm_report'):
        if not getattr(result,key,None) and not getattr(result,'offline',False):
            variable='SAM_DAG_REPORT_PATHS' if key=='sam_report' else 'DSPM_REPORT_PATHS'
            setattr(result,key,[p.strip() for p in environment_values.get(variable,'').split(';') if p.strip()])
    settings.setdefault('data_exposure_enabled', True if getattr(result,'offline',False) else not result.services or bool({'M365','Purview'} & set(result.services)))
    if getattr(result,'offline',False):
        result.permission_profile=getattr(result,'permission_profile',None) or settings.get('permission_profile')
        result._recorded_collectors=sorted(set(saved.get('enabled_collectors') or []))
    result._effective_settings = settings
    if getattr(result,'offline',False) and not getattr(result,'replay_snapshot',None) and not getattr(result,'collection_input',None) and not any(getattr(result,k,None) for k in INPUTS if k not in {'baseline','assessment_profile','provider_evidence'}):
        errors.append('offline_evidence_required')
    return result,saved,errors


def portable_configuration(config):
    source = vars(config) if hasattr(config,'__dict__') else dict(config or {})
    excluded = {'plan','plan_format','verbose','color','no_progress','open_html_report','customer_name','tenant_name',
        'save_collection','snapshot_json','render_output_dir','env_file','replay_snapshot','collection_input',
        'assessment_history','lifecycle_report_date','client_secret','password','access_token','refresh_token',
        'certificate_path','certificate_password','_provided_options','snapshot','log','input','output','history','policy'}
    values = {k:v for k,v in source.items() if k not in excluded and k not in INPUTS and not k.startswith('_')
        and not any(word in k.lower() for word in ('secret','password','token','credential'))}
    for key,value in list(values.items()):
        if isinstance(value,(list,tuple,set)): values[key]=sorted(set(value))
    values['effective_settings'] = {key:value for key,value in source.get('_effective_settings',{}).items()
        if key in {*AGES,'lifecycle_report_max_age_days','lifecycle_report_dates','data_exposure_enabled','source_windows'}}
    values['recorded_collectors'] = source.get('_recorded_collectors',[])
    values['effective_authentication'] = source.get('_effective_authentication',{})
    if source.get('lifecycle_report_date') and not source.get('_effective_settings'):
        from .lifecycle_report_settings import lifecycle_settings
        values['effective_settings'] = lifecycle_settings(report_dates=source['lifecycle_report_date'],evaluation_date=source.get('evaluation_date'))
    from .repository_provenance import input_selection_count
    values['input_selections'] = {k:input_selection_count(k,source[k]) for k in (*INPUTS,'collection_input','assessment_history','replay_snapshot') if source.get(k)}
    values['policy_versions']={'source':'2.0.0','configuration':'2.0.0'}
    return values

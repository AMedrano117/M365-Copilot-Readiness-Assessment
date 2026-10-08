"""Additive identities, never readiness interpretation or presentation locators.

Execution IDs are generated once at an execution boundary and persisted. Semantic
IDs hash explicit, typed boundaries. Missing boundaries are not filled with
customer names, file paths, positions, recommendation text or favorable defaults.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from uuid import UUID, uuid4

IDENTITY_SCHEMA_VERSION = '1.0.0'
PREFIXES = {'control':'PCT-', 'observation':'POB-', 'finding':'PFI-',
    'recommendation_catalog':'PRC-', 'action':'PAC-', 'dataset':'PDS-',
    'capture':'PCP-', 'source_file':'PFL-', 'native_record':'PNR-',
    'evidence_record':'PEV-', 'support':'PSR-'}
REQUIRED = {
    'control': ('namespace','control_id'),
    'observation': ('assessment_id','run_id','environment_id','provider','control_id','metric_id',
                    'population','resource_scope','window','capture_id','evidence_level'),
    'finding': ('assessment_id','environment_id','provider','control_id','condition_key','population','resource_scope'),
    'recommendation_catalog': ('namespace','catalog_key'),
    'action': ('assessment_id','environment_id','action_key'),
    'dataset': ('environment_id','provider','workload','dataset_key'),
    'capture': ('dataset_id','captured_at','capture_key'),
    'source_file': ('environment_id','sha256'),
    'native_record': ('dataset_id','native_id'),
    'evidence_record': ('capture_id','record_key','content_digest'),
    'support': ('finding_id','evidence_id','selection_role','selector_version'),
}
OPTIONAL = {
    'observation': ('native_record_id','metric_definition','product','tier','reporting_basis','unit','semantic_variant'),
    'finding': ('product','tier','affected_resource_ids','population_definition','control_definition_version',
                'applicability','rollout_stage','customer_decision','closure_evidence','closure_requirements'),
    'dataset': ('population','resource_scope','product','tier'),
}


class MissingIdentityInputs(ValueError):
    """A legacy producer did not declare enough semantic context."""


class IdentityCollisionError(ValueError):
    """Two distinct boundaries produced the same ID; never publish either silently."""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def environment_id(tenant_id):
    """Only verified tenant GUIDs can establish a Microsoft 365 environment."""
    try:
        tenant = str(UUID(str(tenant_id)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Identity requires a verified primary tenant GUID.') from None
    if int(UUID(tenant)) == 0:
        raise ValueError('Identity requires a nonzero primary tenant GUID.')
    return 'ENV-' + digest({'namespace':'microsoft-365-tenant', 'tenant_id':tenant})


def incomplete_identity(state='incomplete'):
    return {'SchemaVersion':IDENTITY_SCHEMA_VERSION, 'State':state,
        'AssessmentId':None, 'RunId':None, 'PrimaryEnvironmentId':None,
        'MethodologyVersion':None, 'CatalogVersion':None, 'EvaluatedAt':None,
        'CreatedAt':None, 'RunType':None, 'BaselineRunId':None,
        'Entities':[], 'Aliases':[], 'References':[]}


def new_identity(tenant_id, *, methodology_version, evaluated_at, catalog_version=None):
    env = environment_id(tenant_id)
    if not methodology_version or not evaluated_at:
        raise ValueError('New execution identity requires methodology and evaluation time.')
    meta = incomplete_identity('complete')
    meta.update(AssessmentId='AST-'+uuid4().hex, RunId='RUN-'+uuid4().hex,
        PrimaryEnvironmentId=env, MethodologyVersion=str(methodology_version),
        CatalogVersion=catalog_version, EvaluatedAt=str(evaluated_at),
        CreatedAt=datetime.now(timezone.utc).isoformat())
    return meta


def new_run(assessment, tenant_id, *, evaluated_at):
    """Explicit primitive only: no reassessment classification or baseline choice."""
    if assessment.get('State') != 'complete' or not assessment.get('AssessmentId'):
        raise ValueError('A new run requires a complete persisted assessment identity.')
    if environment_id(tenant_id) != assessment.get('PrimaryEnvironmentId'):
        raise ValueError('A continuing assessment cannot change its primary environment.')
    meta = new_identity(tenant_id, methodology_version=assessment['MethodologyVersion'],
                        evaluated_at=evaluated_at, catalog_version=assessment.get('CatalogVersion'))
    meta['AssessmentId'] = assessment['AssessmentId']
    return meta


def canonical_boundary(kind, values):
    if kind not in PREFIXES:
        raise ValueError('Unknown identity type.')
    boundary = {key:deepcopy(values.get(key)) for key in REQUIRED[kind]}
    for key,value in boundary.items():
        if value is None or value == '' or value == [] or value == {} or isinstance(value, bool):
            raise MissingIdentityInputs(f'{kind} identity lacks required {key}.')
        if isinstance(value,str) and value.strip().lower() in {'unknown','ambiguous','not recorded','not supplied'}:
            raise MissingIdentityInputs(f'{kind} identity has unresolved {key}.')
        if key not in {'population','resource_scope','window'} and not isinstance(value,str):
            raise MissingIdentityInputs(f'{kind} identity requires an unambiguous string {key}.')
    boundary.update({key:deepcopy(values[key]) for key in OPTIONAL.get(kind, ()) if values.get(key) not in (None,'')})
    if 'affected_resource_ids' in boundary:
        boundary['affected_resource_ids'] = sorted(set(boundary['affected_resource_ids']))
    return boundary


def semantic_id(kind, **values):
    boundary = canonical_boundary(kind, values)
    return PREFIXES[kind] + digest({'schema':IDENTITY_SCHEMA_VERSION,
        'type':kind, 'boundary':boundary})


def entity(kind, metadata, boundary, **attributes):
    key = canonical_boundary(kind, boundary)
    # Boundary values stay internal. Hashes/IDs expose no readable sensitive data.
    identifier = semantic_id(kind, **key)
    return dict(attributes, Id=identifier, Type=kind, IdentityKey=identifier[len(PREFIXES[kind]):],
        Boundary=key, AssessmentId=metadata['AssessmentId'], RunId=metadata['RunId'],
        PrimaryEnvironmentId=metadata['PrimaryEnvironmentId'])


class IdentityRegistry:
    """Intern shared semantic nodes; reject conflicting hash meanings explicitly."""
    def __init__(self):
        self.entities = []
        self._keys = {}

    def add(self, row):
        old = self._keys.get(row['Id'])
        if old is not None:
            if old != (row['Type'], row['IdentityKey'], row.get('Boundary')):
                raise IdentityCollisionError('Persistent identity collision; publication is unsafe.')
            return row['Id']
        self._keys[row['Id']] = (row['Type'],row['IdentityKey'],deepcopy(row.get('Boundary')))
        self.entities.append(deepcopy(row))
        return row['Id']


def typed_alias(value, namespace, target_type, metadata, target_id, *, origin_artifact):
    return {'Value':str(value), 'Namespace':namespace, 'TargetType':target_type,
        'AssessmentId':metadata['AssessmentId'], 'OriginRunId':metadata['RunId'],
        'OriginArtifact':origin_artifact, 'TargetId':target_id}


def reference(owner_id, relation, target_id, target_type, metadata):
    return {'OwnerId':owner_id, 'Relation':relation, 'TargetId':target_id,
        'TargetType':target_type, 'AssessmentId':metadata['AssessmentId'],
        'RunId':metadata['RunId'], 'PrimaryEnvironmentId':metadata['PrimaryEnvironmentId']}


def execution_metadata(identity):
    """Persist the seed, not a previous build's derived identity registers."""
    meta = deepcopy(identity)
    meta.update(Entities=[], Aliases=[], References=[])
    return meta


def attach_identity(result, bundle):
    """Attach once after the shared builder; retain every existing report field.

    Legacy producers may not declare a catalog key, customer work-item key,
    provider, population or capture. Leave those aliases unresolved rather than
    manufacturing durable identities from their mixed-purpose display fields.
    """
    from .assessment_references import validate_assessment_references
    supplied = (bundle.get('collection_context') or {}).get('identity')
    meta = execution_metadata(supplied) if supplied else incomplete_identity()
    meta.setdefault('Entities',[]); meta.setdefault('Aliases',[]); meta.setdefault('References',[])
    registry = IdentityRegistry()
    aliases, refs = [], []
    complete = meta.get('State') == 'complete'

    def add(kind, boundary, **attributes):
        return registry.add(entity(kind,meta,boundary,**attributes)) if complete else None

    def alias(value, namespace, kind, target, artifact='shared-result'):
        if value not in (None,''):
            aliases.append(typed_alias(value,namespace,kind,meta,target,origin_artifact=artifact))

    def source_environment(source):
        if source.get('tenant_id'):
            try:
                return environment_id(source['tenant_id'])
            except ValueError:
                raise MissingIdentityInputs('Declared source tenant cannot verify an environment.') from None
        return meta.get('PrimaryEnvironmentId')

    def retained_file(source, artifact):
        target=None
        if complete and re.fullmatch('[0-9a-fA-F]{64}',str(source.get('source_hash') or '')):
            try:
                target=add('source_file',dict(environment_id=source_environment(source),sha256=source['source_hash'].lower()))
            except MissingIdentityInputs:
                pass
        alias(source.get('source_file'),'source_file','source_file',target,artifact)
        return target

    control_ids = {}
    for row in result.get('controls',[]):
        code = row.get('control_id')
        if code:
            target = add('control',dict(namespace='m365-readiness',control_id=code),
                         ControlId=code, DefinitionVersion=result.get('methodology_version'))
            control_ids[code] = target
            alias(code,'control_id','control',target)

    evidence_ids = {}
    observation_targets = {}
    reconciled_targets = {}
    observation_states = {}
    generated_observations = {}
    observation_variants = {}
    # Reproduced legacy POB boundaries omit conclusions. Extend only boundaries
    # that actually collide; unique legacy IDs keep their original algorithm.
    from .observation_reconciliation import semantic_payload, key as comparison_digest
    for row in result.get('evidence',[]):
        base = {field: row.get(field) for field in ('provider','control_id','metric_id','population','scope',
            'window','evidence_level','metric_definition','product','tier','reporting_basis','unit',
            'native_id','source_schema','source_hash','source_type','source_observed_at')}
        observation_variants.setdefault(digest(base),set()).add(comparison_digest(semantic_payload(row)))
    for row in result.get('evidence',[]):
        target = None
        file_id = retained_file(row,row.get('evidence_id') or 'shared-result')
        if complete:
            try:
                source = add('dataset',dict(environment_id=source_environment(row),
                    provider=row.get('provider'),workload=row.get('source_type'),dataset_key=row.get('source_schema')))
                captured = row.get('source_observed_at') or row.get('observed_at')
                capture = add('capture',dict(dataset_id=source,captured_at=captured,capture_key=row.get('source_hash')))
                if file_id:
                    refs.append(reference(capture,'source_file',file_id,'source_file',meta))
                native = (add('native_record',dict(dataset_id=source,native_id=row['native_id'])) if row.get('native_id') else None)
                key = {field:row.get(field) for field in ('provider','control_id','metric_id','population','window','evidence_level','metric_definition','product','tier','reporting_basis','unit')}
                key.update(assessment_id=meta['AssessmentId'],run_id=meta['RunId'],environment_id=meta['PrimaryEnvironmentId'],
                           resource_scope=row.get('scope'),capture_id=capture,native_record_id=native)
                base = {field: row.get(field) for field in ('provider','control_id','metric_id','population','scope',
                    'window','evidence_level','metric_definition','product','tier','reporting_basis','unit',
                    'native_id','source_schema','source_hash','source_type','source_observed_at')}
                if len(observation_variants[digest(base)]) > 1:
                    key['semantic_variant'] = comparison_digest(semantic_payload(row))
                observation = entity('observation',meta,key)
                if observation['Id'] not in generated_observations:
                    generated_observations[observation['Id']]=observation
                    observation['SourceOccurrences']=[]
                    meta['Entities'].append(observation)
                generated_observations[observation['Id']]['SourceOccurrences'].append({
                    'EvidenceId':row.get('evidence_id'),'OccurrenceId':row.get('source_occurrence_id'),
                    'CaptureId':capture,'NativeRecordId':native,'SourceFile':row.get('source_file')})
                observation_targets.setdefault(row.get('evidence_id'),set()).add(observation['Id'])
                if row.get('reconciled_observation_id'):
                    reconciled_targets.setdefault(row['reconciled_observation_id'],set()).add(observation['Id'])
                observation_states.setdefault(row.get('evidence_id'),set()).add(row.get('selection'))
                if control_ids.get(row.get('control_id')):
                    refs.append(reference(observation['Id'],'control',control_ids[row['control_id']],'control',meta))
                target = add('evidence_record',dict(capture_id=capture,
                    record_key=digest(key),content_digest=digest({k:row.get(k) for k in ('value','unit','availability','complete','control_result')})))
                refs.append(reference(observation['Id'],'evidence',target,'evidence_record',meta))
            except MissingIdentityInputs:
                # Explicitly unresolved legacy alias below; never skip the original fact.
                pass
        alias(row.get('evidence_id'),'EV','evidence_record',target)
        if row.get('evidence_id'):
            evidence_ids.setdefault(row['evidence_id'],set()).add(target)

    # Positional SRC/EVD algorithms are used only for compatibility aliases.
    from .evidence_selection import _dataset_id, _evidence_id, _native_id
    from .assessment_serialization import plain_data
    sources = plain_data(bundle.get('assessment_sources') or {})
    for name,datasets in sources.items():
        for index,dataset in enumerate(datasets or []):
            state = dataset.get('source') or {}; records = dataset.get('records') or []
            legacy = _dataset_id(result.get('tenant_id') or '',name,index,state)
            file_id=retained_file(state,legacy)
            source, capture = None, None
            if complete:
                try:
                    # Provider/workload must be declared at the source boundary.
                    source = add('dataset',dict(environment_id=source_environment(state),provider=state.get('provider'),
                        workload=state.get('workload'),dataset_key=name,population=state.get('population'),resource_scope=state.get('scope')))
                    capture = add('capture',dict(dataset_id=source,captured_at=state.get('collected_at') or state.get('observed_at'),
                        capture_key=state.get('capture_id') or digest(sorted(digest(row) for row in records))))
                    if file_id:
                        refs.append(reference(capture,'source_file',file_id,'source_file',meta))
                    refs.append(reference(capture,'dataset',source,'dataset',meta))
                except MissingIdentityInputs:
                    pass
            alias(legacy,'SRC','dataset',source,legacy)
            for position,row in enumerate(records):
                native_value = _native_id(row); native, target = None,None
                if capture:
                    native = add('native_record',dict(dataset_id=source,native_id=str(native_value))) if native_value is not None else None
                    target = add('evidence_record',dict(capture_id=capture,record_key=native or digest(row),content_digest=digest(row)))
                    refs.append(reference(target,'capture',capture,'capture',meta))
                    if native:
                        refs.append(reference(target,'native_record',native,'native_record',meta))
                alias(_evidence_id(result.get('tenant_id') or '',legacy,position,row),'EVD','evidence_record',target,legacy)
                alias(native_value,'native:'+name,'native_record',native,
                      _evidence_id(result.get('tenant_id') or '',legacy,position,row))

    finding_targets = {}
    for row in result.get('recommendations',[]):
        finding, catalog, action = None,None,None
        if complete:
            try:
                finding = add('finding',dict(assessment_id=meta['AssessmentId'],environment_id=meta['PrimaryEnvironmentId'],
                    provider=row.get('Provider'),control_id=row.get('ControlId'),condition_key=row.get('FindingKey'),
                    population=row.get('Population'),resource_scope=row.get('EvidenceScope'),product=row.get('Product'),tier=row.get('Tier'),
                    population_definition=row.get('PopulationDefinition'),control_definition_version=row.get('ControlDefinitionVersion'),
                    applicability=row.get('Applicability'),rollout_stage=row.get('RolloutStage'),
                    customer_decision=row.get('CustomerDecision'),closure_evidence=row.get('ClosureEvidence'),closure_requirements=row.get('ClosureRequirements')))
            except MissingIdentityInputs:
                pass
            if row.get('RecommendationCatalogKey') and row.get('RecommendationCatalogNamespace'):
                catalog = add('recommendation_catalog',dict(namespace=row['RecommendationCatalogNamespace'],catalog_key=row['RecommendationCatalogKey']))
            if row.get('CustomerActionKey'):
                action = add('action',dict(assessment_id=meta['AssessmentId'],environment_id=meta['PrimaryEnvironmentId'],action_key=row['CustomerActionKey']))
        for namespace,kind,target in (('RecommendationId:issue','finding',finding),('RecommendationId:catalog','recommendation_catalog',catalog),('RecommendationId:action','action',action)):
            alias(row.get('RecommendationId'),namespace,kind,target)
        finding_targets.setdefault(row.get('RecommendationId'),set()).add(finding)
        if finding:
            canonical_issue=None
            for issue in (result.get('reconciliation') or {}).get('findings',[]):
                if issue.get('recommendation_id')==row.get('RecommendationId'):
                    issue['persistent_finding_id']=finding
                    canonical_issue=issue
                    for identifier in issue.get('observation_ids') or []:
                        for observation_id in reconciled_targets.get(identifier,[]):
                            refs.append(reference(finding,'observation',observation_id,'observation',meta))
            for legacy_id in row.get('CompatibilityRecommendationIds') or []:
                if legacy_id != row.get('RecommendationId'):
                    alias(legacy_id,'RecommendationId:issue','finding',finding,artifact=row['RecommendationId'])
        if finding:
            if control_ids.get(row.get('ControlId')):
                refs.append(reference(finding,'control',control_ids[row['ControlId']],'control',meta))
            declared_evidence = row.get('EvidenceIds') or [row.get('EvidenceId'),*(row.get('RelatedEvidenceIds') or [])]
            if isinstance(declared_evidence,str):
                declared_evidence=[declared_evidence]
            for legacy in declared_evidence:
                if not canonical_issue:
                    for observation_id in observation_targets.get(legacy,[]):
                        refs.append(reference(finding,'observation',observation_id,'observation',meta))
                matches = evidence_ids.get(legacy,set()) - {None}
                if len(matches)==1 and None not in evidence_ids.get(legacy,set()):
                    evidence = next(iter(matches))
                    refs.append(reference(finding,'evidence',evidence,'evidence_record',meta))
                    states=observation_states.get(legacy,set())
                    role='conflict-support' if 'conflict' in states else 'retained-support' if legacy!=row.get('EvidenceId') else 'legacy-selected'
                    support = add('support',dict(finding_id=finding,evidence_id=evidence,selection_role=role,selector_version='1'),
                                  SelectionStates=sorted(state for state in states if state))
                    refs.extend([reference(support,'finding',finding,'finding',meta),reference(support,'evidence',evidence,'evidence_record',meta)])
            for owner in (catalog,action):
                if owner:
                    refs.append(reference(owner,'finding',finding,'finding',meta))

    # Project the existing neutral selection model into aliases only. No new
    # locators, conclusions, populations or evidence selection rules are minted.
    from .evidence_selection import build_evidence_selection
    selected = build_evidence_selection(result,bundle,generated_at=meta.get('CreatedAt') or result.get('evaluation_date'))
    ev_targets = {}
    for row in aliases:
        if row['Namespace']=='EVD':
            ev_targets.setdefault(row['Value'],set()).add(row['TargetId'])
    existing = {(row['Value'],row['Namespace'],row['OriginArtifact']) for row in aliases}
    for source_row in selected['sources']:
        legacy = source_row['dataset_id']
        if (legacy,'SRC',legacy) not in existing:
            alias(legacy,'SRC','dataset',None,legacy)
    for evidence_row in selected['evidence_records']:
        legacy=evidence_row['record_id']; artifact=evidence_row['dataset_id']
        if (legacy,'EVD',artifact) not in existing:
            alias(legacy,'EVD','evidence_record',None,artifact)
    for issue in selected['findings']:
        candidates = finding_targets.get(issue['finding_id'],set())
        finding = next(iter(candidates)) if len(candidates)==1 else None
        for record in issue['records']:
            evidence = set()
            for legacy in record['evidence_record_ids']:
                evidence.update(ev_targets.get(legacy,{None}))
            # A multi-source detail is not automatically one durable observation.
            target = next(iter(evidence)) if len(evidence)==1 and None not in evidence else None
            support = None
            if finding and target:
                support = add('support',dict(finding_id=finding,evidence_id=target,selection_role='selected-record',selector_version='1'))
                refs.extend([reference(finding,'evidence',target,'evidence_record',meta),
                             reference(support,'finding',finding,'finding',meta),
                             reference(support,'evidence',target,'evidence_record',meta)])
            alias(record['record_id'],'DET','support',support,issue['finding_id'])
            old_id=record.get('compatibility_record_id')
            if old_id and old_id.startswith(('ROW-','record-')):
                alias(old_id,'ROW' if old_id.startswith('ROW-') else 'record','evidence_record',target,record['record_id'])

    # Explicit producer declarations are validated, never deduplicated or repaired.
    meta['Entities'].extend(registry.entities)
    meta['Entities'].extend(deepcopy(bundle.get('identity_entities') or []))
    # Intern generated aliases only. An old EV locator can address more than one
    # semantic record after enrichment: retain its candidates without first-match
    # resolution. Explicit producer aliases remain untouched for validation.
    alias_groups={}
    for row in aliases:
        context=tuple(encoded_value for encoded_value in (row.get(field) for field in
            ('Value','Namespace','TargetType','AssessmentId','OriginRunId','OriginArtifact')))
        alias_groups.setdefault(context,[]).append(row)
    for rows in alias_groups.values():
        targets={row.get('TargetId') for row in rows}
        declared=deepcopy(rows[0])
        if len(targets)>1:
            declared['TargetId']=None
            declared['CandidateTargetIds']=sorted(target for target in targets if target)
            declared['CompatibilityReason']='Legacy locator has multiple semantic targets; resolve using source occurrence context.'
        if declared.get('CandidateTargetIds') and result.get('reconciliation'):
            result['reconciliation']['diagnostics'].append({'code':'ambiguous_compatibility_locator',
                'severity':'compatibility_warning','legacy_id':declared['Value'],
                'namespace':declared['Namespace'],'candidate_ids':declared['CandidateTargetIds'],
                'reason':declared['CompatibilityReason']})
        meta['Aliases'].append(declared)
    meta['Aliases'].extend(deepcopy(bundle.get('identity_aliases') or []))
    # Intern generated relationships only. Explicit declarations, including
    # duplicate invalid records, must remain available to validation/review.
    meta['References'].extend(json.loads(key) for key in sorted({json.dumps(row,sort_keys=True) for row in refs}))
    meta['References'].extend(deepcopy(bundle.get('identity_references') or []))
    result['identity'] = meta
    if meta.get('RunContext') is not None:
        result['run_context'] = deepcopy(meta['RunContext'])
    if result.get('reconciliation'):
        for observation in result['reconciliation'].get('observations',[]):
            observation['persistent_observation_ids']=sorted(reconciled_targets.get(observation['id'],[]))
        result['reconciliation']['diagnostics'].sort(key=lambda row:json.dumps(row,sort_keys=True))
    result['identity_validation'] = validate_assessment_references(result)
    return result

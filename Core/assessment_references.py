"""Deterministic semantic diagnostics; no data repair or assessment decisions."""
from collections import defaultdict
import json
import re
from .assessment_identity import PREFIXES, IDENTITY_SCHEMA_VERSION, environment_id, semantic_id

RELATIONS = {
    'evidence': ({'finding','observation','support'}, {'evidence_record'}),
    'finding': ({'recommendation_catalog','action','support'}, {'finding'}),
    'control': ({'finding','observation'}, {'control'}),
    'observation': ({'finding'}, {'observation'}),
    'dataset': ({'capture','native_record'}, {'dataset'}),
    'capture': ({'evidence_record','observation'}, {'capture'}),
    'source_file': ({'capture'}, {'source_file'}),
    'native_record': ({'evidence_record','observation'}, {'native_record'}),
}
ALIAS_CONTEXT = ('Value','Namespace','TargetType','AssessmentId','OriginRunId','OriginArtifact')


def alias_context(alias):
    return tuple(json.dumps(alias.get(key),sort_keys=True) for key in ALIAS_CONTEXT)


def resolve_alias(result, query):
    """Return one fully scoped target only; missing/ambiguous never means first."""
    meta = result.get('identity') or {}
    aliases = [row for row in meta.get('Aliases',[]) if alias_context(row)==alias_context(query)]
    if len(aliases)!=1 or not aliases[0].get('TargetId'):
        return None
    targets = [row for row in meta.get('Entities',[]) if row.get('Id')==aliases[0]['TargetId']
        and row.get('Type')==query.get('TargetType') and row.get('AssessmentId')==query.get('AssessmentId')
        and row.get('RunId')==query.get('OriginRunId')]
    return targets[0] if len(targets)==1 else None


def validate_assessment_references(result):
    from .reconciliation_validation import validate_reconciliation
    diagnostics = validate_reconciliation(result)
    def report(code, subject, message, severity='error'):
        diagnostics.append(dict(severity=severity,code=code,subject=str(subject or 'identity'),message=message))

    def retired(value):
        if isinstance(value,dict):
            for key,item in value.items():
                normalized=re.sub('[^a-z0-9]','',str(key).lower())
                if normalized in {'findinguid','dashboardpackage','dashboardidentity','dashboardschemaversion',
                                  'dashboardjsonpath','dashboardjsonfolder','appbuilderfolder','dashboardwrapper'}:
                    report('retired_identity',key,'Retired output identities are prohibited.')
                if normalized in {'namespace','type','targettype'} and isinstance(item,str):
                    concept=re.sub('[^a-z0-9]','',item.lower())
                    if concept=='findinguid' or concept.startswith(('dashboardpackage','dashboardidentity','dashboardwrapper','appbuilder')):
                        report('retired_identity',item,'Retired identity namespaces and types are prohibited.')
                retired(item)
        elif isinstance(value,list):
            for item in value:
                retired(item)
    retired(result)
    meta = result.get('identity')
    if not isinstance(meta,dict):
        report('legacy_identity','identity','Snapshot has no persistent identity; continuity is unresolved.','compatibility_warning')
        return sorted(diagnostics,key=lambda row:json.dumps(row,sort_keys=True))
    if meta.get('SchemaVersion')!=IDENTITY_SCHEMA_VERSION:
        report('schema_version','identity','Unsupported identity schema version.')
    state = meta.get('State')
    if not isinstance(state,str) or state not in {'complete','legacy','incomplete'}:
        report('invalid_state','identity','Invalid identity state.')
    complete = state == 'complete'
    if not complete:
        report('incomplete_identity','identity','Legacy/unbound execution: no assessment continuity is claimed.','compatibility_warning')
    if complete and result.get('evaluation_date') and str(meta.get('EvaluatedAt',''))[:10]!=result['evaluation_date']:
        report('evaluation_context_changed','identity','Replay evaluation date differs from the persisted execution date; run classification is deferred.','warning')
    if complete and result.get('methodology_version') and str(meta.get('MethodologyVersion'))!=str(result['methodology_version']):
        report('methodology_context_changed','identity','Execution methodology differs; no version compatibility is inferred.','compatibility_warning')
    for field,prefix,length in (('AssessmentId','AST-',32),('RunId','RUN-',32),('PrimaryEnvironmentId','ENV-',64)):
        value=meta.get(field)
        if complete and not value:
            report('missing_scope',field,'Complete identity requires execution and environment scope.')
        elif value and not re.fullmatch(re.escape(prefix)+'[0-9a-f]{'+str(length)+'}',str(value)):
            report('invalid_id',field,'Invalid metadata identity type or encoding.')
    for field in ('MethodologyVersion','EvaluatedAt','CreatedAt'):
        if complete and not meta.get(field):
            report('missing_scope',field,'Complete execution metadata requires this field.')
    if complete and result.get('tenant_id'):
        try:
            if environment_id(result['tenant_id'])!=meta.get('PrimaryEnvironmentId'):
                report('cross_environment','identity','Verified tenant differs from primary environment.')
        except ValueError:
            report('cross_environment','identity','Result tenant cannot verify the primary environment.')

    def ownership(row, subject, run_field='RunId'):
        for field,code in (('AssessmentId','cross_assessment'),('PrimaryEnvironmentId','cross_environment'),(run_field,'run_ownership')):
            expected=meta.get('RunId' if field==run_field else field)
            if not row.get(field):
                report('missing_scope',subject,'Persistent record lacks required ownership scope.')
            elif row.get(field)!=expected:
                report(code,subject,'Persistent record belongs to a different execution scope.')

    entities = meta.get('Entities',[])
    aliases = meta.get('Aliases',[])
    refs = meta.get('References',[])
    for name,value in (('Entities',entities),('Aliases',aliases),('References',refs)):
        if not isinstance(value,list) or any(not isinstance(row,dict) for row in value):
            report('invalid_shape',name,'Identity registers must contain object arrays.')
    if any(not isinstance(value,list) or any(not isinstance(row,dict) for row in value) for value in (entities,aliases,refs)):
        return sorted(diagnostics,key=lambda row:json.dumps(row,sort_keys=True))
    indexed=defaultdict(list)
    for row in entities:
        identifier=row.get('Id'); kind=row.get('Type')
        indexed[str(identifier)].append(row)
        ownership(row,identifier)
        if not isinstance(kind,str) or kind not in PREFIXES:
            report('invalid_type',identifier,'Unregistered persistent entity type.')
        elif not re.fullmatch(re.escape(PREFIXES[kind])+'[0-9a-f]{64}',str(identifier)):
            report('display_id',identifier,'Persistent entity uses an invalid or display-only identifier.')
        else:
            try:
                if semantic_id(kind,**(row.get('Boundary') or {}))!=identifier or row.get('IdentityKey')!=identifier[len(PREFIXES[kind]):]:
                    report('identity_mismatch',identifier,'Persistent ID conflicts with its semantic boundary.')
                boundary=row.get('Boundary') or {}
                for field,expected,code in (('assessment_id',meta.get('AssessmentId'),'cross_assessment'),
                    ('environment_id',meta.get('PrimaryEnvironmentId'),'cross_environment'),('run_id',meta.get('RunId'),'run_ownership')):
                    if field in boundary and boundary[field]!=expected:
                        report(code,identifier,'Semantic boundary conflicts with record ownership.')
            except (ValueError,TypeError,KeyError):
                report('missing_scope',identifier,'Semantic identity boundary is incomplete or invalid.')
    for identifier,rows in indexed.items():
        if len(rows)>1:
            report('cross_type_id' if len({row.get('Type') for row in rows})>1 else 'duplicate_id',identifier,
                   'Persistent ID is reused; records are retained for diagnosis.')

    def target(identifier,kind,subject,code='dangling_reference'):
        matches=indexed.get(str(identifier),[])
        if not matches:
            report(code,subject,'Reference has no persistent target.')
        elif len(matches)!=1:
            report('ambiguous_reference',subject,'Reference has multiple persistent targets.')
        elif matches[0].get('Type')!=kind:
            report('invalid_type',subject,'Reference target type differs from declared type.')
        return matches[0] if len(matches)==1 else None

    # Semantic boundary dependencies are references even if a producer omitted
    # their explicit relationship row. They must not evade validation.
    obligations=defaultdict(list)
    for row in entities:
        if row.get('Type')=='control':
            boundary=row.get('Boundary') or {}
            if isinstance(boundary,dict) and isinstance(boundary.get('control_id'),str):
                obligations[boundary['control_id']].append(row)
    for row in entities:
        boundary=row.get('Boundary') or {}
        if not isinstance(boundary,dict):
            continue  # Already diagnosed as an invalid semantic boundary.
        for field,kind in (('dataset_id','dataset'),('capture_id','capture'),('native_record_id','native_record'),
                            ('finding_id','finding'),('evidence_id','evidence_record')):
            if boundary.get(field):
                target(boundary[field],kind,row.get('Id'))
        if row.get('Type') in {'observation','finding'}:
            matches=obligations.get(str(boundary.get('control_id')),[])
            if not matches:
                report('dangling_reference',row.get('Id'),'Semantic boundary has no registered control obligation.')
            elif len(matches)!=1:
                report('ambiguous_reference',row.get('Id'),'Semantic control obligation is ambiguous.')

    for row in refs:
        subject='|'.join(str(row.get(field) or '') for field in ('OwnerId','Relation','TargetId'))
        ownership(row,subject)
        owner_rows=indexed.get(str(row.get('OwnerId')),[])
        if len(owner_rows)!=1:
            report('dangling_owner' if not owner_rows else 'ambiguous_reference',subject,'Reference owner must resolve exactly once.')
        kind=row.get('TargetType')
        if not isinstance(kind,str) or kind not in PREFIXES:
            report('invalid_type',subject,'Reference has an unregistered target type.')
        relation=RELATIONS.get(str(row.get('Relation')))
        if not relation or str(kind) not in relation[1] or (len(owner_rows)==1 and str(owner_rows[0].get('Type')) not in relation[0]):
            report('invalid_relation',subject,'Reference relation is invalid for its owner and target types.')
        target(row.get('TargetId'),kind,subject)
    groups=defaultdict(list)
    for row in aliases:
        subject='|'.join(str(value or '') for value in alias_context(row))
        groups[alias_context(row)].append(row)
        if not isinstance(row.get('TargetType'),str) or row.get('TargetType') not in PREFIXES:
            report('invalid_type',subject,'Alias declares an unregistered target type.')
        for field in ('Value','Namespace','OriginArtifact'):
            if not row.get(field):
                report('missing_scope',subject,'Alias lacks namespace, value or artifact context.')
        if row.get('AssessmentId')!=meta.get('AssessmentId'):
            report('cross_assessment',subject,'Alias belongs to a different assessment.')
        if row.get('OriginRunId')!=meta.get('RunId'):
            report('run_ownership',subject,'Alias belongs to a different originating run.')
        if not row.get('TargetId'):
            report('unresolved_alias',subject,'Legacy alias lacks a safely declared persistent target.','unresolved_legacy_reference')
        else:
            if complete and (not row.get('AssessmentId') or not row.get('OriginRunId')):
                report('missing_scope',subject,'Bound alias lacks required execution scope.')
            target(row['TargetId'],row.get('TargetType'),subject,'dangling_alias')
    for context,rows in groups.items():
        if len(rows)>1:
            code='ambiguous_alias' if len({str(row.get('TargetId')) for row in rows})>1 else 'duplicate_alias'
            # Unbound legacy occurrences remain publishable; declared semantic ambiguity is fatal.
            severity='compatibility_warning' if all(not row.get('TargetId') for row in rows) else 'error'
            report(code,'|'.join(str(value or '') for value in context),'Alias context occurs more than once; no first-match resolution.',severity)
    return sorted(diagnostics,key=lambda row:json.dumps(row,sort_keys=True))


def require_valid_assessment(result):
    diagnostics=validate_assessment_references(result)
    errors=[row for row in diagnostics if row['severity']=='error']
    if errors:
        raise ValueError('Assessment identity validation blocked publication: '+ ', '.join(sorted({row['code'] for row in errors})))
    return diagnostics

"""Explicit decision contract and retained semantic context; no inferred approval."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
import re
from .assessment_identity import PREFIXES, semantic_id

VERSION = '1.0.0'
TARGETS = {'ClosedByRemediation':{'finding','action'}, 'AcceptedRisk':{'finding','control'},
    'ApprovedException':{'finding','control','action'}, 'NoLongerApplicable':{'finding','control','action'}}
EVENT_TYPES = {'revoke':'DecisionRevoked','expire':'DecisionExpired','reopen':'ClosureReopened'}
STATES = {'Open','PendingReview','Approved','Active','Expired','Revoked','Superseded','Reopened','Rejected'}
INPUT_FIELDS = frozenset(('TargetEntityId TargetEntityType DecisionType AccountableOwner Rationale Population '
    'ResourceScope ParentPopulation ParentResourceScope ResourceIds EffectiveAt ReviewAt ExpirationAt '
    'ExpirationPolicy EvidenceReferences SupportingArtifacts Conditions ResidualRisk CompensatingControls '
    'CompensatingControlsRationale ValidationResult ClosureCriteriaEvaluated ClosureOverride ApplicabilityRule '
    'DecisionAuthority AuthorityRole').split())
IMMUTABLE = {'TargetEntityId','TargetEntityType','DecisionType'}
NOTICE = ('Risk acceptance is not remediation. Exceptions apply only to their recorded scope. '
    'Evidence-derived resolution is separate from formal closure. Governance counts are not a readiness score.')


class GovernanceError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def fail(code, message):
    raise GovernanceError(code, message)


def required_statement(value, field, code):
    """Require actual text; never turn a truthy object into a reviewed narrative."""
    if not isinstance(value,str) or not value.strip():
        fail(code,field+' requires a meaningful textual statement.')
    return value.strip()


def time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        fail('governance_date', 'Governance timestamps require an explicit timezone.')


def locator(value):
    if (not isinstance(value,str) or not value.strip() or '\\' in value or ':' in value
            or PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()
            or any(part in {'..','.'} for part in value.split('/'))):
        fail('governance_locator','Use a confined portable relative source locator.')
    return value


def safe_input(value):
    """Reject secret-shaped fields and embedded raw payloads at the API boundary."""
    if isinstance(value,dict):
        for key,item in value.items():
            normalized = re.sub('[^a-z0-9]','',str(key).lower())
            if any(token in normalized for token in ('password','secret','credential','privatekey','accesstoken','refreshtoken','rawevidence','rawpayload')):
                fail('governance_forbidden_data','Decision inputs must not contain credentials or raw evidence.')
            safe_input(item)
    elif isinstance(value,list):
        for item in value:safe_input(item)


def input_fields(data):
    if not isinstance(data,dict) or set(data)-INPUT_FIELDS:
        fail('governance_input','Decision input contains unsupported or derived fields.')
    safe_input(data)


def _entities(result):
    rows = (result.get('identity') or {}).get('Entities',[])
    return {row['Id']:row for row in rows}


def _node(row):
    # Identity extensions may contain detail attributes. Only identity and
    # semantic boundaries belong in a governance attestation.
    return {key:deepcopy(row.get(key)) for key in ('Id','Type','IdentityKey','Boundary',
        'AssessmentId','RunId','PrimaryEnvironmentId')}


def context(result, record):
    """Retain only the target, linked findings, and referenced persistent evidence.

    The publication gate validates the source snapshot before extracting this
    compact attested context. No raw records or complete run graph are copied.
    """
    from .assessment_references import require_valid_assessment
    source = {k:v for k,v in result.items() if not k.startswith('governance')}
    require_valid_assessment(source)
    meta = source.get('identity') or {}
    if meta.get('State')!='complete':fail('governance_identity','Governance requires verified assessment identity.')
    rows=_entities(source);target=rows.get(record.get('TargetEntityId'))
    if not target or target.get('Type')!=record.get('TargetEntityType'):
        fail('governance_target','Decision target must resolve uniquely to its persistent semantic type.')
    linked={target['Id']}
    refs=meta.get('References',[])
    if target['Type']=='action':
        linked.update(ref['TargetId'] for ref in refs if ref.get('OwnerId')==target['Id'] and ref.get('Relation')=='finding')
    if target['Type']=='control':
        control=target.get('Boundary',{}).get('control_id')
        linked.update(row['Id'] for row in rows.values() if row.get('Type')=='finding' and row.get('Boundary',{}).get('control_id')==control
            and row.get('Boundary',{}).get('population')==(record.get('ParentPopulation') or record.get('Population'))
            and row.get('Boundary',{}).get('resource_scope')==(record.get('ParentResourceScope') or record.get('ResourceScope')))
    supported={ref['TargetId'] for ref in refs if ref.get('OwnerId') in linked and ref.get('Relation')=='evidence'}
    supported.update(row['Boundary']['evidence_id'] for row in rows.values() if row.get('Type')=='support'
        and row.get('Boundary',{}).get('finding_id') in linked)
    # Control evidence may be attached to a scoped observation instead of a finding.
    if target['Type']=='control':
        observations={row['Id'] for row in rows.values() if row.get('Type')=='observation'
            and row.get('Boundary',{}).get('control_id')==target['Boundary']['control_id']
            and row.get('Boundary',{}).get('population')==record.get('Population')
            and row.get('Boundary',{}).get('resource_scope')==record.get('ResourceScope')}
        supported.update(ref['TargetId'] for ref in refs if ref.get('OwnerId') in observations and ref.get('Relation')=='evidence')
    requested={ref.get('EvidenceId') for ref in record.get('EvidenceReferences',[]) if isinstance(ref,dict)}
    files={ref.get('FileId') for ref in record.get('SupportingArtifacts',[]) if isinstance(ref,dict)}
    lifecycle={row.get('CurrentEntityId'):row for row in (source.get('lifecycle') or {}).get('Records',[])
        if row.get('EntityType') in {'finding','action','control'}}
    return {'Identity':{key:deepcopy(meta.get(key)) for key in ('AssessmentId','RunId','PrimaryEnvironmentId','BaselineRunId','MethodologyVersion')},
        'Target':_node(target), 'LinkedFindings':[_node(rows[key]) for key in sorted(linked) if key!=target['Id']],
        'SupportedEvidenceIds':sorted(supported),
        'Evidence':[_node(rows[key]) for key in sorted(requested) if key in rows],
        'Files':[_node(rows[key]) for key in sorted(files) if key in rows],
        'Lifecycle':{key:{field:deepcopy(lifecycle[key].get(field)) for field in ('State','EntityType','CurrentEntityId',
            'BaselineEntityId','ComparisonEligibility','BaselineRunId','CurrentEvidenceReferences','ResolutionEvidenceReferences',
            'PriorResolutionEvidenceReferences','RemainsOpen','RemainingClosureRequirement')} for key in sorted(linked) if key in lifecycle}}


def validate_context(ctx, log):
    if not isinstance(ctx,dict):fail('governance_context','Missing retained decision context.')
    meta=ctx.get('Identity') or {}
    for field in ('AssessmentId','PrimaryEnvironmentId'):
        if meta.get(field)!=log.get(field):fail('governance_ownership','Decision context belongs to another assessment or environment.')
    if not str(meta.get('RunId','')).startswith('RUN-') or not meta.get('MethodologyVersion'):
        fail('governance_context','Decision context lacks run and methodology provenance.')
    for row in [ctx.get('Target',{})]+ctx.get('LinkedFindings',[])+ctx.get('Evidence',[])+ctx.get('Files',[]):
        if not isinstance(row,dict):fail('governance_context','Malformed context entity.')
        for field in ('AssessmentId','RunId','PrimaryEnvironmentId'):
            if row.get(field)!=meta.get(field):fail('governance_ownership','Retained entity has foreign ownership.')
        kind=row.get('Type');boundary=row.get('Boundary') or {}
        if kind not in PREFIXES or semantic_id(kind,**boundary)!=row.get('Id'):
            fail('governance_context','Retained semantic entity does not reproduce its persistent identity.')
        for field,owner in (('assessment_id','AssessmentId'),('environment_id','PrimaryEnvironmentId'),('run_id','RunId')):
            if field in boundary and boundary[field]!=meta[owner]:fail('governance_ownership','Retained boundary has foreign ownership.')


def validate_record(record,ctx,*,complete=False,require_closure_support=True):
    kind=record.get('DecisionType');target=ctx['Target'];meta=ctx['Identity']
    if kind not in TARGETS or record.get('TargetEntityType') not in TARGETS[kind]:
        fail('governance_type','Unsupported decision type or target type.')
    if (record.get('TargetEntityId')!=target['Id'] or record.get('TargetEntityType')!=target['Type']):
        fail('governance_target','Decision is not bound to its retained persistent target.')
    for field in ('AssessmentId','PrimaryEnvironmentId','RunId','BaselineRunId','MethodologyVersion'):
        if record.get(field)!=meta.get(field):fail('governance_ownership','Decision provenance differs from its retained context.')
    if record.get('DecisionSchemaVersion')!=VERSION or not re.fullmatch('GOV-[0-9a-f]{32}',str(record.get('DecisionId',''))):
        fail('governance_identity','Decision requires its stable GOV identity and schema version.')
    boundary=target.get('Boundary',{})
    boundaries=[boundary]+[r.get('Boundary',{}) for r in ctx.get('LinkedFindings',[]) if target['Type']=='action']
    if target['Type']=='action' and not ctx.get('LinkedFindings'):
        fail('governance_target','Customer action requires a persistent finding relationship.')
    for scoped in boundaries:
        for field,key,parent in (('Population','population','ParentPopulation'),('ResourceScope','resource_scope','ParentResourceScope')):
            if record.get(field) and scoped.get(key) and record[field]!=scoped[key]:
                if kind!='ApprovedException' or record.get(parent)!=scoped[key]:
                    fail('governance_scope','Decision scope differs from the target; exceptions require explicit parent boundaries.')
    if record.get('ResourceIds'):
        if not isinstance(record['ResourceIds'],list) or not all(isinstance(i,str) and i for i in record['ResourceIds']):
            fail('governance_scope','Explicit excluded resource IDs must be a list of identifiers.')
        available=boundary.get('affected_resource_ids')
        if available and not set(record['ResourceIds']).issubset(available):fail('governance_scope','Exception includes resources outside the target.')
    for field in ('EffectiveAt','ReviewAt','ExpirationAt'):
        if record.get(field):time(record[field])
    if record.get('EffectiveAt'):
        for field in ('ReviewAt','ExpirationAt'):
            if record.get(field) and time(record[field])<time(record['EffectiveAt']):
                fail('governance_date','Review and expiration cannot precede effectiveness.')
    if record.get('ExpirationPolicy') not in (None,'review_only','expire_on_review','expire_on_expiration'):
        fail('governance_expiration_policy','Unknown explicit expiration policy.')
    if complete:
        policy=record.get('ExpirationPolicy')
        if policy=='expire_on_review' and not record.get('ReviewAt') or policy=='expire_on_expiration' and not record.get('ExpirationAt'):
            fail('governance_expiration_policy','Recorded expiration policy requires its corresponding date.')
    evidence=record.get('EvidenceReferences',[])
    if not isinstance(evidence,list):fail('governance_evidence','Evidence references must be a list.')
    indexed={row['Id']:row for row in ctx.get('Evidence',[])}
    for ref in evidence:
        if not isinstance(ref,dict):fail('governance_evidence','Malformed evidence reference.')
        if set(ref)-{'EvidenceId','AssessmentId','RunId','SourceLocator','ObservedAt','Population','ResourceScope','Period',
                'ObservedOutcome','Exceptions','AccountableOwner','Qualification','Interpretation','SourceType','MethodologyRule'}:
            fail('governance_evidence','Evidence references accept reviewed metadata, not embedded source payloads.')
        if ref.get('EvidenceId') not in indexed or indexed[ref['EvidenceId']]['Type']!='evidence_record':
            fail('governance_evidence','Supporting evidence must resolve to a retained PEV record.')
        if ref['EvidenceId'] not in ctx.get('SupportedEvidenceIds',[]):fail('governance_evidence','Evidence is unrelated to the target.')
        for field in ('AssessmentId','RunId'):
            if ref.get(field)!=meta.get(field):fail('governance_evidence','Evidence reference has foreign ownership.')
        locator(ref.get('SourceLocator'))
        if ref.get('ObservedAt'):time(ref['ObservedAt'])
        if complete:
            required=('ObservedAt','Population','ResourceScope','Period','ObservedOutcome','AccountableOwner','Qualification','Interpretation')
            text_fields=('ObservedAt','Population','ResourceScope','ObservedOutcome','AccountableOwner','Qualification','Interpretation')
            if (any(not ref.get(f) for f in required) or not isinstance(ref.get('Exceptions'),list)
                    or not all(isinstance(ref.get(f),str) and ref[f].strip() for f in text_fields)
                    or not all(isinstance(item,str) for item in ref['Exceptions'])):
                fail('governance_evidence','Reviewed evidence lacks date, scope, period, outcome, exceptions or qualification.')
            if ref['Population']!=record.get('Population') or ref['ResourceScope']!=record.get('ResourceScope'):
                fail('governance_evidence_scope','Reviewed evidence must cover the exact decision scope.')
            period=ref['Period']
            if not isinstance(period,dict) or set(period)!={'Start','End'} or time(period.get('Start'))>time(period.get('End')):
                fail('governance_evidence_period','Evidence period must be a bounded chronological interval.')
            if time(ref['ObservedAt'])>time(record['LastRecordedAt']) or time(period['End'])>time(record['LastRecordedAt']):
                fail('governance_evidence_date','A review cannot rely on evidence dated after its recorded transition.')
    artifacts=record.get('SupportingArtifacts',[])
    if not isinstance(artifacts,list):fail('governance_artifact','Artifact references must be a list.')
    files={row['Id']:row for row in ctx.get('Files',[])}
    for ref in artifacts:
        if not isinstance(ref,dict) or ref.get('FileId') not in files or files[ref['FileId']]['Type']!='source_file':
            fail('governance_artifact','Artifact reference must resolve to a retained PFL record.')
        if set(ref)-{'FileId','Locator','Purpose'}:fail('governance_artifact','Supporting artifact references cannot embed source content.')
        locator(ref.get('Locator'))
    if not complete:return
    required=('AccountableOwner','Rationale','Population','ResourceScope','EffectiveAt','DecisionAuthority','AuthorityRole')
    if any(not isinstance(record.get(f),str) or not record[f].strip() for f in required) or not evidence:
        fail('governance_required','Review requires owner, rationale, scope, authority, dates and supporting evidence.')
    for field in ('Conditions','CompensatingControls'):
        if field in record and (not isinstance(record[field],list) or not all(isinstance(item,str) and item.strip() for item in record[field])):
            fail('governance_conditions','Conditions and compensating controls must be explicit text statements.')
    if kind in {'AcceptedRisk','ApprovedException'}:
        if not record.get('ReviewAt') and not record.get('ExpirationAt'):fail('governance_review_date','Risk and exception decisions require review or expiration.')
        if not isinstance(record.get('Conditions'),list) or not record['Conditions']:fail('governance_conditions','Risk and exception conditions must be explicit.')
    if kind=='AcceptedRisk':required_statement(record.get('ResidualRisk'),'ResidualRisk','governance_risk')
    if kind=='NoLongerApplicable' and not record.get('ApplicabilityRule'):fail('governance_applicability','Applicability requires an explicit methodology or business rule.')
    if kind=='ClosedByRemediation':
        required_statement(record.get('ValidationResult'),'ValidationResult','governance_closure')
        if record.get('ClosureCriteriaEvaluated') is not True:
            fail('governance_closure','Closure requires evaluated criteria and an explicit validation result.')
        override=record.get('ClosureOverride',False)
        if not isinstance(override,bool):
            fail('governance_closure_support','ClosureOverride must be an explicit Boolean.')
        if override:
            required_statement(record.get('ResidualRisk'),'ResidualRisk','governance_closure_support')
            if not record.get('Conditions') or not record.get('ReviewAt'):
                fail('governance_closure_support','Every conditional closure override requires conditions and a review date.')
        states=[ctx['Lifecycle'].get(key,{}) for key in [target['Id']]+[r['Id'] for r in ctx.get('LinkedFindings',[])]]
        resolved=bool(states) and all(s.get('State')=='ResolvedByCurrentEvidence' for s in states)
        if not resolved and require_closure_support and not override:
            fail('governance_closure_support','Closure requires evidence-derived resolution or an explicit conditional override.')


def scope_key(record):
    from .assessment_identity import digest
    values={field:record.get(field) for field in ('TargetEntityId','TargetEntityType','Population','ResourceScope')}
    values['ResourceIds']=sorted(set(record.get('ResourceIds') or []))
    return digest(values)


def expiration_due(record,at):
    # An explicitly recorded hard expiration always removes active effect. A
    # review date expires only under a recorded expire_on_review policy.
    if record.get('ExpirationAt') and time(at)>=time(record['ExpirationAt']):return True
    policy=record.get('ExpirationPolicy');field={'expire_on_expiration':'ExpirationAt','expire_on_review':'ReviewAt'}.get(policy)
    return bool(field and record.get(field) and time(at)>=time(record[field]))


def qualify_current(result, view):
    """Bind audited treatments to this run; preserve historical workflow state.

    A changed action relationship, absent target or contradictory later closure
    evidence becomes a visible review qualification, never an inferred event.
    """
    meta=result.get('identity') or {};entities=_entities(result)
    states={r.get('CurrentEntityId'):r for r in (result.get('lifecycle') or {}).get('Records',[])
        if r.get('EntityType') in {'finding','action','control'}}
    references=meta.get('References',[])
    events=view.get('DecisionLog',{}).get('Events',[])
    for row in view['Records']:
        target=entities.get(row['TargetEntityId']);present=bool(target and target['Type']==row['TargetEntityType'])
        compatible=present
        if present and target['Type']=='finding':
            boundary=target['Boundary']
            compatible=all((row.get(parent) or row.get(field))==boundary.get(key) for field,key,parent in (
                ('Population','population','ParentPopulation'),('ResourceScope','resource_scope','ParentResourceScope')))
        if present and target['Type']=='action':
            current_links={ref['TargetId'] for ref in references if ref.get('OwnerId')==target['Id'] and ref.get('Relation')=='finding'}
            original=next((e['Context'] for e in events if e['DecisionId']==row['DecisionId'] and e['Operation']=='draft'),{})
            original_links={r['Id'] for r in original.get('LinkedFindings',[])}
            compatible=bool(current_links) and current_links==original_links
        lifecycle=states.get(row['TargetEntityId'],{})
        changed_closure=(row['DecisionType']=='ClosedByRemediation' and row['RunId']!=meta.get('RunId')
            and lifecycle.get('State') in {'Reopened','Regressed','NotReassessed','Indeterminate','NotComparable'})
        row['CurrentTargetPresent']=present;row['CurrentScopeCompatible']=compatible
        row['EffectiveForCurrentRun']=row['EffectiveActive'] and compatible and not changed_closure
        row['CurrentRequiresReview']=row['RequiresReview'] or row['EffectiveActive'] and (not compatible or changed_closure)
        row['CurrentGovernanceState']=row['GovernanceState'] if compatible and not changed_closure else 'PendingReview'
        row['CurrentAssessmentStatus']=lifecycle.get('CurrentAssessmentStatus')
        row['CurrentLifecycleState']=lifecycle.get('State')
        row['CustomerActionStatus']=None
        if present and target['Type']=='action':
            aliases={a['Value'] for a in meta.get('Aliases',[]) if a.get('TargetId')==target['Id']
                and a.get('Namespace') in {'RecommendationId:action','RecommendationId'}}
            matching=[r for r in result.get('actions',[]) if r.get('RecommendationId') in aliases]
            if len(matching)==1:row['CustomerActionStatus']=matching[0].get('ActionStatus')
        if present and not lifecycle:
            if target['Type']=='control':
                matching=[r for r in result.get('control_results',[]) if (r.get('ControlId') or r.get('Control ID'))==target['Boundary']['control_id']]
                if len(matching)==1:row['CurrentAssessmentStatus']=matching[0].get('Status')
            else:
                section='actions' if target['Type']=='action' else 'recommendations'
                namespace='RecommendationId:action' if target['Type']=='action' else 'RecommendationId:issue'
                aliases={a['Value'] for a in meta.get('Aliases',[]) if a.get('TargetId')==target['Id'] and a.get('Namespace') in {namespace,'RecommendationId'}}
                matching=[r for r in result.get(section,[]) if r.get('RecommendationId') in aliases]
                if len(matching)==1:
                    if target['Type']=='action':row['CustomerActionStatus']=matching[0].get('ActionStatus')
                    else:row['CurrentAssessmentStatus']=matching[0].get('AssessmentStatus',matching[0].get('Status'))
        row['CurrentRunQualification']=('Target or scope is absent or changed; historical decision has no current effect.' if not compatible
            else 'Later evidence requires explicit closure review; no reopening event has been inferred.' if changed_closure else None)
    summary=view['Summary'];records=view['Records']
    summary['CurrentActiveTreatments']={kind:sum(r['DecisionType']==kind and r['EffectiveForCurrentRun'] for r in records) for kind in TARGETS}
    summary['CurrentFindingsAwaitingReview']=len({r['TargetEntityId'] for r in records if r['TargetEntityType']=='finding' and r['CurrentRequiresReview']})
    summary['CurrentFindingsClosedByRemediation']=len({r['TargetEntityId'] for r in records if r['TargetEntityType']=='finding'
        and r['DecisionType']=='ClosedByRemediation' and r['EffectiveForCurrentRun']})
    summary['CurrentFindingRecords']=sum(r.get('Type')=='finding' for r in entities.values())
    summary['CustomerActionRecords']=len(result.get('actions',[]))
    return view

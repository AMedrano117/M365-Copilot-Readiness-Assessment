"""Pure append-only decision workflow and deterministic, separate treatment views."""
from copy import deepcopy
from uuid import uuid4
from .assessment_history import seal
from .assessment_identity import digest
from .governance_contract import (VERSION, TARGETS, EVENT_TYPES, STATES, IMMUTABLE,
    GovernanceError, fail, time, locator as validate_locator, input_fields, context, validate_context, validate_record,
    scope_key, expiration_due, safe_input, qualify_current)
from .governance_authority import authorize

TRANSITIONS = {'amend':({'Open'},'Open'), 'submit':({'Open'},'PendingReview'),
    'approve':({'PendingReview'},'Approved'), 'reject':({'PendingReview'},'Rejected'),
    'activate':({'Approved'},'Active'), 'revoke':({'Approved','Active'},'Revoked'),
    'expire':({'Approved','Active'},'Expired'), 'reopen':({'Approved','Active','Revoked','Expired'},'Reopened'),
    'supersede':({'Approved'},'Active'), 'review':(STATES,None)}
PRIVILEGED = {'approve','reject','activate','revoke','expire','reopen','supersede'}


def new_log(result):
    from .assessment_references import require_valid_assessment
    require_valid_assessment(result)
    meta=result.get('identity') or {}
    if meta.get('State')!='complete':fail('governance_identity','Decision log requires a complete assessment identity.')
    return seal({'SchemaVersion':VERSION,'AssessmentId':meta['AssessmentId'],
        'PrimaryEnvironmentId':meta['PrimaryEnvironmentId'],'Events':[]})


def _overlap(a,b):
    """Exact target/scope conflicts, including overlapping effectiveness windows."""
    if scope_key(a)!=scope_key(b):return False
    def end(r):
        values=[time(r['ExpirationAt'])] if r.get('ExpirationAt') else []
        if r.get('ExpirationPolicy')=='expire_on_review' and r.get('ReviewAt'):values.append(time(r['ReviewAt']))
        return min(values) if values else None
    ae,be=end(a),end(b)
    return (ae is None or time(b['EffectiveAt'])<ae) and (be is None or time(a['EffectiveAt'])<be)


def _evolve(records,event,log):
    op=event.get('Operation');key=event.get('DecisionId');actor=event.get('Actor');at=event.get('RecordedAt')
    if not isinstance(actor,str) or not actor.strip():fail('governance_actor','Every workflow event requires an explicit actor.')
    time(at);ctx=event.get('Context');validate_context(ctx,log)
    data=event.get('Data');safe_input(data)
    if not isinstance(data,dict):fail('governance_input','Workflow event data must be an object.')
    previous=records.get(key)
    if op=='draft':
        if previous:fail('governance_duplicate','DecisionId is already recorded.')
        input_fields(data)
        row=deepcopy(data)
        row.update(ctx['Identity']);row.update(DecisionId=key,DecisionSchemaVersion=VERSION,
            WorkflowState='Open',RecordedBy=actor,RecordedAt=at,LastActor=actor,LastRecordedAt=at)
        validate_record(row,ctx)
    else:
        if previous is None:fail('governance_predecessor','Workflow transition references an unknown DecisionId.')
        row=deepcopy(previous)
        if op not in TRANSITIONS or row['WorkflowState'] not in TRANSITIONS[op][0]:
            fail('governance_transition','Transition is not permitted from the recorded workflow state.')
        if op=='amend':
            input_fields(data)
            if set(data)&IMMUTABLE:fail('governance_target','Decision target and type are immutable; create a separate decision.')
            if actor!=row['RecordedBy']:fail('governance_actor','Only the recorded draft author may amend it.')
            row.update(deepcopy(data))
        elif op=='submit':
            if data:fail('governance_input','Submit cannot modify decision metadata.')
            if actor!=row['RecordedBy']:fail('governance_actor','Only the recorded draft author may submit it.')
        elif op=='review':
            if set(data)!={'Reason'} or not data.get('Reason'):fail('governance_review','Review requires a reason and cannot renew or approve a decision.')
            row['LastReview']={'Actor':actor,'RecordedAt':at,'Reason':data['Reason']}
        elif op in PRIVILEGED:
            allowed={'Reason','ResultingState'} if op=='reopen' else {'SupersededDecisionId','Reason'} if op=='supersede' else {'Reason'} if op in {'reject','revoke','expire'} else set()
            if set(data)-allowed:fail('governance_input','Transition contains unsupported decision edits.')
            if op in {'reject','revoke','expire','supersede','reopen'} and not data.get('Reason'):
                fail('governance_reason','A reason is required for this explicit transition.')
            authorize(event.get('Policy'),row,op,actor)
            if op=='approve':
                row.update(ApprovedBy=actor,ApprovedAt=at,ApprovalRole=row['AuthorityRole'],
                    ApprovalPolicyId=event['Policy']['PolicyId'],ApprovalPolicyVersion=event['Policy']['Version'])
            if op in {'activate','supersede'} and expiration_due(row,at):fail('governance_expired','A due decision cannot be activated or renewed.')
            if op=='expire' and not expiration_due(row,at):fail('governance_expiration_policy','Expiration requires a reached date under recorded policy.')
            if op in {'revoke','expire','reopen'}:
                row['PredecessorDecisionId']=key
                row['TransitionReason']=data['Reason']
                row['ResultingGovernanceState']='Open' if op=='reopen' else 'PendingReview' if op=='expire' else 'Open'
                if op=='revoke':row['RevocationReason']=data['Reason']
            if op=='reopen':
                if row['DecisionType']!='ClosedByRemediation':fail('governance_reopening','Only a prior explicit remediation closure can be reopened.')
                if data.get('ResultingState') not in {'Open','PendingReview'}:fail('governance_reopening','Reopening requires an explicit resulting open or review state.')
                row['ResultingGovernanceState']=data['ResultingState']
                row['ReopeningRunId']=ctx['Identity']['RunId']
                # Persistent finding continuity is required. Display IDs are never consulted.
                if ctx['Target']['Id']!=row['TargetEntityId']:fail('governance_reopening','Ambiguous finding continuity cannot reopen a closure.')
                if not ctx.get('Evidence'):fail('governance_reopening','Reopening requires current retained evidence.')
                row['ReopeningEvidenceReferences']=deepcopy(row.get('EvidenceReferences',[]))
            if op=='supersede':
                old_key=data.get('SupersededDecisionId');old=records.get(old_key)
                if not old or old_key==key or old['WorkflowState'] not in {'Active','Approved'} or scope_key(old)!=scope_key(row):
                    fail('governance_supersession','Supersession requires an approved predecessor for the exact same target and scope.')
                authorize(event.get('Policy'),old,'supersede',actor)
                records[old_key]=dict(deepcopy(old),WorkflowState='Superseded',SupersededByDecisionId=key,
                    LastActor=actor,LastRecordedAt=at,TransitionReason=data['Reason'])
                row['SupersededDecisionId']=old_key
        if TRANSITIONS[op][1]:row['WorkflowState']=TRANSITIONS[op][1]
        row.update(LastActor=actor,LastRecordedAt=at)
        # Ordinary transitions remain bound to the originating run. Explicit
        # reopening is permitted from a later run with the same persistent target.
        if op!='reopen' and ctx['Identity']['RunId']!=row['RunId']:
            fail('governance_run','Transition must use the original decision run snapshot.')
        if op=='reopen':
            retained=deepcopy(row)
            retained.update(ctx['Identity'])
            retained['EvidenceReferences']=deepcopy(event.get('CurrentEvidenceReferences',row['EvidenceReferences']))
            # Reopening evidence must be reviewed metadata, not just a known ID.
            validate_record(retained,ctx,complete=True,require_closure_support=False)
            row['ReopeningEvidenceReferences']=retained['EvidenceReferences']
        else:validate_record(row,ctx,complete=op in {'submit','approve','activate','supersede'})
    records[key]=row
    active=[r for r in records.values() if r['WorkflowState']=='Active']
    for i,a in enumerate(active):
        for b in active[i+1:]:
            if _overlap(a,b):fail('governance_conflict','Duplicate or conflicting active decisions for the same target and scope.')
    return row


def _replay(log):
    if not isinstance(log,dict) or log.get('SchemaVersion')!=VERSION:
        fail('governance_schema','Unsupported governance log schema.')
    if (not str(log.get('AssessmentId','')).startswith('AST-')
            or not str(log.get('PrimaryEnvironmentId','')).startswith('ENV-')):
        fail('governance_ownership','Decision log requires assessment and environment ownership.')
    if log.get('Integrity')!=seal(log)['Integrity']:fail('governance_integrity','Decision log integrity mismatch.')
    events=log.get('Events')
    if not isinstance(events,list):fail('governance_events','Decision log Events must be a list.')
    records={};previous_hash=None;seen=set();last_time=None
    for sequence,event in enumerate(events,1):
        if not isinstance(event,dict) or event.get('Sequence')!=sequence:
            fail('governance_sequence','Governance events must retain append-only sequence.')
        identifier=event.get('EventId')
        if not isinstance(identifier,str) or not identifier.startswith('GVE-') or identifier in seen:
            fail('governance_event_identity','Governance events require unique persistent event identities.')
        seen.add(identifier)
        if event.get('PreviousHash')!=previous_hash or event.get('Hash')!=digest({k:v for k,v in event.items() if k!='Hash'}):
            fail('governance_event_integrity','Governance event hash chain mismatch.')
        if last_time and time(event.get('RecordedAt'))<last_time:fail('governance_sequence','Recorded event time cannot precede the prior event.')
        last_time=time(event.get('RecordedAt'))
        row=_evolve(records,event,log)
        if event.get('Record')!=row:fail('governance_unaudited_edit','Decision record does not reproduce its audited transition.')
        expected=EVENT_TYPES.get(event['Operation'],row['DecisionType'])
        if event.get('DecisionType')!=expected:fail('governance_type','Event decision type differs from its audited operation.')
        previous_hash=event['Hash']
    return records


def validate_log(log):
    try:_replay(log)
    except (GovernanceError,KeyError,TypeError,ValueError,AttributeError) as exc:
        return [{'severity':'error','code':getattr(exc,'code','governance_shape'),'subject':'governance',
            'message':str(exc) if isinstance(exc,GovernanceError) else 'Malformed governance record or context.'}]
    return []


def require_log(log):
    diagnostics=validate_log(log)
    if diagnostics:fail(diagnostics[0]['code'],diagnostics[0]['message'])
    return log


def _append(log,result,key,op,actor,at,data,policy=None,current_evidence=None):
    records=_replay(log);prior=records.get(key,{})
    candidate=deepcopy(data) if op=='draft' else dict(prior,**data) if op=='amend' else prior
    if op=='reopen' and current_evidence is not None:candidate=dict(candidate,EvidenceReferences=current_evidence)
    ctx=context(result,candidate)
    event={'Sequence':len(log['Events'])+1,'EventId':'GVE-'+uuid4().hex,'DecisionId':key,
        'Operation':op,'Actor':actor,'RecordedAt':at,'Data':deepcopy(data),'Context':ctx,
        'Policy':deepcopy(policy) if op in PRIVILEGED else None,
        'PreviousHash':log['Events'][-1]['Hash'] if log['Events'] else None}
    if current_evidence is not None:event['CurrentEvidenceReferences']=deepcopy(current_evidence)
    if current_evidence is not None and op!='reopen':fail('governance_input','Current evidence can only accompany an explicit reopening event.')
    row=_evolve(records,event,log)
    event['Record']=deepcopy(row);event['DecisionType']=EVENT_TYPES.get(op,row['DecisionType'])
    event['Hash']=digest(event)
    updated=deepcopy(log);updated['Events'].append(event);updated=seal(updated);require_log(updated)
    return updated


def create_draft(log,result,data,*,actor,at):
    input_fields(data)
    return _append(log,result,'GOV-'+uuid4().hex,'draft',actor,at,data)


def transition(log,result,decision_id,operation,*,actor,at,policy=None,data=None,current_evidence=None):
    return _append(log,result,decision_id,operation,actor,at,data or {},policy,current_evidence)


def project(log,*,as_of):
    """Read-only projection at a recorded time. Never generates workflow events."""
    instant=time(as_of);records=_replay(log)
    if log['Events'] and instant<time(log['Events'][-1]['RecordedAt']):fail('governance_as_of','Projection time precedes recorded governance events.')
    rows=[]
    for record in records.values():
        row=deepcopy(record);state=row['WorkflowState']
        due=expiration_due(row,as_of)
        review=bool(row.get('ReviewAt') and instant>=time(row['ReviewAt']))
        row['EffectiveActive']=state=='Active' and not due and instant>=time(row['EffectiveAt'])
        row['ExpirationDue']=due and state in {'Approved','Active'}
        row['RequiresReview']=state in {'PendingReview','Expired','Reopened'} or (state in {'Approved','Active'} and (due or review))
        row['GovernanceState']=row['DecisionType'] if row['EffectiveActive'] else (
            row.get('ResultingGovernanceState') or ('PendingReview' if row['RequiresReview'] else 'Open'))
        row['HistoryReference']={'DecisionId':row['DecisionId'],'EventIds':[e['EventId'] for e in log['Events'] if e['DecisionId']==row['DecisionId']]}
        rows.append(row)
    rows.sort(key=lambda r:r['DecisionId'])
    summary={'WorkflowStates':{s:sum(r['WorkflowState']==s for r in rows) for s in sorted(STATES)},
        'DecisionTypes':{s:sum(r['DecisionType']==s for r in rows) for s in TARGETS},
        'ActiveTreatments':{s:sum(r['DecisionType']==s and r['EffectiveActive'] for r in rows) for s in TARGETS},
        'PendingDecisions':sum(r['WorkflowState']=='PendingReview' for r in rows),
        'ExpiredDecisions':sum(r['WorkflowState']=='Expired' for r in rows),
        'ExpirationDue':sum(r['ExpirationDue'] for r in rows),'ReopenedClosures':sum(r['WorkflowState']=='Reopened' for r in rows),
        'FindingsAwaitingReview':len({r['TargetEntityId'] for r in rows if r['TargetEntityType']=='finding' and r['RequiresReview']}),
        'UnresolvedLegacyDecisions':0}
    return {'SchemaVersion':VERSION,'AssessmentId':log['AssessmentId'],'PrimaryEnvironmentId':log['PrimaryEnvironmentId'],
        'AsOf':as_of,'Records':rows,'Summary':summary,'ValidationStatus':'validated'}


def effective_for(view,target_id,population,resource_scope,*,resource_ids=None):
    requested=sorted(set(resource_ids or []))
    return [deepcopy(r) for r in view.get('Records',[]) if r.get('TargetEntityId')==target_id and r.get('Population')==population
        and r.get('ResourceScope')==resource_scope and sorted(set(r.get('ResourceIds') or []))==requested
        and r.get('EffectiveForCurrentRun',r.get('EffectiveActive'))]


def attach(result,log,*,as_of,locator):
    """Return a governance overlay; never write or mutate a completed run."""
    from .governance_validation import validate_governance,legacy_diagnostics
    view=project(log,as_of=as_of)
    validate_locator(locator)
    meta=result.get('identity') or {}
    if any(view.get(f)!=meta.get(f) for f in ('AssessmentId','PrimaryEnvironmentId')):
        fail('governance_ownership','Cannot attach another assessment or environment governance.')
    view.update(DecisionLog=deepcopy(log),LogReference={'Locator':locator,'Integrity':log['Integrity'],'SchemaVersion':VERSION})
    view['LegacyReferences']=legacy_diagnostics(result)
    view['Summary']['UnresolvedLegacyDecisions']=len(view['LegacyReferences'])
    qualify_current(result,view)
    output=deepcopy(result);output['governance']=view
    errors=[d for d in validate_governance(output) if d['severity']=='error']
    if errors:fail(errors[0]['code'],errors[0]['message'])
    return output

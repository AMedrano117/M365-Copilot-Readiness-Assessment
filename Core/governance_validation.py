"""Semantic governance diagnostics, including qualified historical display values."""
import re
from .governance_contract import GovernanceError, VERSION, locator, qualify_current


def legacy_diagnostics(result):
    diagnostics=[]
    for section in ('recommendations','actions','control_results'):
        for index,row in enumerate(result.get(section,[])):
            if not isinstance(row,dict):continue
            for field,value in row.items():
                name=re.sub('[^a-z0-9]','',str(field).lower())
                text=re.sub('[^a-z0-9]','',str(value).lower())
                legacy=(name in {'acceptedrisk','riskaccepted','exception','approvedexception','governancestatus','closurestatus'}
                    or name in {'status','completionstatus','disposition'} and text in {'closed','acceptedrisk','approvedexception','notapplicable','nolongerapplicable'})
                if legacy and value not in (None,'',False):
                    diagnostics.append({'severity':'unresolved_legacy_governance_reference','code':'legacy_governance_unresolved',
                        'subject':f'{section}:{index}:{field}',
                        'message':'Historical display value has no verified governance approval provenance; retained as unresolved context.'})
    return diagnostics


def validate_governance(result):
    from .governance import project,validate_log
    diagnostics=legacy_diagnostics(result)
    if 'governance' not in result:return diagnostics
    def error(code,message):
        diagnostics.append({'severity':'error','code':code,'subject':'governance','message':message})
    view=result['governance']
    if not isinstance(view,dict):error('governance_shape','Governance view must be an object.');return diagnostics
    log=view.get('DecisionLog');bad=validate_log(log);diagnostics.extend(bad)
    if bad:return diagnostics
    try:
        expected=project(log,as_of=view.get('AsOf'))
        expected['LegacyReferences']=legacy_diagnostics(result)
        expected['Summary']['UnresolvedLegacyDecisions']=len(expected['LegacyReferences'])
        expected['DecisionLog']=log
        qualify_current(result,expected)
        for key,value in expected.items():
            if view.get(key)!=value:error('governance_projection','Governance summary, workflow or treatment does not reconcile with audited events.')
        reference=view.get('LogReference') or {};locator(reference.get('Locator'))
        if reference.get('Integrity')!=log.get('Integrity') or reference.get('SchemaVersion')!=VERSION:
            error('governance_log_reference','Decision log reference differs from retained integrity or schema.')
        meta=result.get('identity') or {}
        if any(view.get(f)!=meta.get(f) for f in ('AssessmentId','PrimaryEnvironmentId')):
            error('governance_ownership','Governance overlay belongs to another assessment or environment.')
        # Context from this run must match the source graph, without copying its
        # raw payload. Prior-run contexts remain retained, immutable attestations.
        from .governance_contract import context
        for event in log['Events']:
            ctx=event['Context']
            if ctx['Identity']['RunId']==meta.get('RunId'):
                candidate=dict(event['Record'])
                if event['Operation']=='reopen':candidate['EvidenceReferences']=event['Record']['ReopeningEvidenceReferences']
                if context(result,candidate)!=ctx:error('governance_context_mismatch','Decision context differs from the related run evidence and identities.')
        states={r.get('CurrentEntityId'):r.get('State') for r in (result.get('lifecycle') or {}).get('Records',[]) if r.get('EntityType') in {'finding','action'}}
        for row in view.get('Records',[]):
            if row['EffectiveActive'] and row['DecisionType']=='ClosedByRemediation' and states.get(row['TargetEntityId'])=='Reopened':
                diagnostics.append({'severity':'warning','code':'governance_reopening_review','subject':row['DecisionId'],
                    'message':'Current comparable evidence is Reopened; an explicit closure reopening review is required. Historical closure is retained.'})
    except (GovernanceError,TypeError,KeyError,ValueError,AttributeError) as exc:
        error(getattr(exc,'code','governance_shape'),str(exc) if isinstance(exc,GovernanceError) else 'Malformed governance view.')
    return diagnostics

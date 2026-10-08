"""Shared renderer projections of recorded governance; no workflow mutations."""
from html import escape
import json
from .governance_contract import NOTICE


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True) if isinstance(value,(dict,list)) else str(value or '')


def summary_rows(result):
    view=result.get('governance') or {};summary=view.get('Summary') or {}
    from .governance_validation import legacy_diagnostics
    legacy=len(legacy_diagnostics(result))
    if not view and not legacy:return []
    rows=[{'Item':'Interpretation','Value':NOTICE}, {'Item':'Governance as of','Value':view.get('AsOf')},
        {'Item':'Decision log','Value':(view.get('LogReference') or {}).get('Locator')},
        {'Item':'Unresolved legacy governance fields','Value':legacy}]
    rows.extend({'Item':'Current active '+kind+' decisions','Value':count} for kind,count in summary.get('CurrentActiveTreatments',{}).items())
    rows.extend({'Item':key,'Value':summary[key]} for key in ('CurrentFindingsAwaitingReview','CurrentFindingsClosedByRemediation',
        'PendingDecisions','ExpiredDecisions','ExpirationDue','ReopenedClosures','CurrentFindingRecords','CustomerActionRecords') if key in summary)
    return rows


def detail_rows(result):
    return [{key:_json(value) if isinstance(value,(dict,list)) else value for key,value in row.items()}
        for row in (result.get('governance') or {}).get('Records',[])]


def audit_rows(result):
    return [{key:_json(value) if isinstance(value,(dict,list)) else value for key,value in row.items()}
        for row in ((result.get('governance') or {}).get('DecisionLog') or {}).get('Events',[])]


def summary_html(result):
    rows=summary_rows(result)
    if not rows:return ''
    return '<section id="governance"><h2>Governance</h2><p>'+escape(NOTICE)+'</p><dl>'+''.join(
        '<dt>'+escape(str(r['Item']))+'</dt><dd>'+escape(_json(r['Value']))+'</dd>' for r in rows if r['Item']!='Interpretation')+'</dl></section>'


def technical_html(result):
    view=result.get('governance')
    if not view:return ''
    return '<details><summary>Governance audit history</summary><pre>'+escape(json.dumps(view,ensure_ascii=False,indent=2))+'</pre></details>'


def related_records(result,row):
    """Aliases navigate an already validated treatment; they never authorize it."""
    meta=result.get('identity') or {};targets=set()
    for kind,namespace,field in (('finding','RecommendationId:issue','PersistentFindingId'),('action','RecommendationId:action','PersistentActionId')):
        identifier=row.get(field)
        if identifier:targets.add(identifier)
        aliases=[a for a in meta.get('Aliases',[]) if a.get('Namespace') in {namespace,'RecommendationId'} and a.get('TargetType')==kind
            and a.get('Value')==row.get('RecommendationId') and a.get('TargetId')]
        resolved={a['TargetId'] for a in aliases}
        if len(resolved)==1:targets.update(resolved)
    valid={e['Id'] for e in meta.get('Entities',[]) if e.get('Type') in {'finding','action'}}
    return [r for r in (result.get('governance') or {}).get('Records',[]) if r['TargetEntityId'] in targets&valid]


def treatment_fields(result,row):
    records=related_records(result,row)
    if not records:return {}
    # Scope-qualified multiple records stay separate; no finding-wide exception.
    return {'Governance treatment':[_json({key:r.get(key) for key in ('DecisionId','DecisionType','WorkflowState',
        'GovernanceState','CurrentGovernanceState','CurrentLifecycleState','CurrentAssessmentStatus','EffectiveForCurrentRun',
        'CustomerActionStatus','CurrentRunQualification','Population','ResourceScope','ResourceIds')}) for r in records],
        'Governance next review':[r.get('ReviewAt') or r.get('ExpirationAt') for r in records],
        'Governance conditions':[r.get('Conditions') for r in records],
        'Governance residual risk':[r.get('ResidualRisk') for r in records],
        'Governance closure evidence':[r.get('EvidenceReferences') for r in records if r['DecisionType']=='ClosedByRemediation'],
        'Governance authority role':[r.get('ApprovalRole') or r.get('AuthorityRole') for r in records],
        'Governance accountable owner':[r.get('AccountableOwner') for r in records],
        'Governance history':(result['governance'].get('LogReference') or {}).get('Locator'),
        'Governance remaining work':'Retain technical work, monitoring, conditions and review requirements until explicitly addressed.'}


def finding_html(result,row):
    fields=treatment_fields(result,row)
    if not fields:return ''
    return '<details><summary>Governance treatment and review</summary><dl>'+''.join(
        '<dt>'+escape(key)+'</dt><dd>'+escape(_json(value))+'</dd>' for key,value in fields.items())+'</dl></details>'

"""Presentation only: consume recorded lifecycle states, never calculate them."""
from html import escape
import json
from .assessment_delta import NOTICE


def summary_rows(result):
    delta=result.get('lifecycle') or {}
    if not delta:return []
    rows=[{'Item':'Delta outcome','Value':delta.get('Outcome')},
        {'Item':'Explicit baseline','Value':delta.get('BaselineRunId') or 'No baseline'},
        {'Item':'Delta calculation','Value':'Enabled' if delta.get('Enabled') else 'Not calculated'},
        {'Item':'Interpretation','Value':NOTICE}]
    if delta.get('Records'):
        rows.extend({'Item':'Findings: '+state,'Value':count} for state,count in delta.get('Summary',{}).get('finding',{}).items())
        rows.extend({'Item':'Finding population: '+key,'Value':value} for key,value in delta.get('Summary',{}).get('FindingPopulation',{}).items())
    return rows


def detail_rows(result):
    # All technical fields retained. JSON cells keep full periods and scoped refs.
    return [{key:json.dumps(value,ensure_ascii=False,sort_keys=True) if isinstance(value,(dict,list)) else value
             for key,value in record.items()} for record in (result.get('lifecycle') or {}).get('Records',[])]


def summary_html(result):
    if (result.get('run_context') or {}).get('RunType')!='Reassessment' or not result.get('lifecycle'):
        return ''
    delta=result['lifecycle'];counts=delta.get('Summary',{}).get('finding',{})
    items=''.join('<li>'+escape(state)+': '+str(count)+'</li>' for state,count in counts.items())
    return ('<section class="reassessment" id="reassessment"><h2>Reassessment</h2><p>Explicit baseline: '
        +escape(str(delta.get('BaselineRunId') or 'None'))+'. Comparison: '+escape(str(delta.get('Outcome')))
        +'.</p>'+('<ul>'+items+'</ul>' if items else '<p>No finding lifecycle classifications were calculated.</p>')
        +'<p>'+escape(NOTICE)+'</p></section>')


def technical_html(result):
    if not result.get('lifecycle'):return ''
    rows=[]
    for record in result['lifecycle'].get('Records',[]):
        rows.append('<details><summary>'+escape(str(record.get('EntityType')))+' '
            +escape(str(record.get('EntityId')))+' — '+escape(str(record.get('State')))
            +'</summary><pre>'+escape(json.dumps(record,ensure_ascii=False,indent=2))+'</pre></details>')
    return '<details><summary>Lifecycle records</summary>'+''.join(rows)+'</details>'


def finding_html(result,row):
    # Display aliases select an already classified record for navigation only.
    aliases=[a for a in (result.get('identity') or {}).get('Aliases',[]) if a.get('Namespace')=='RecommendationId:issue'
        and a.get('Value')==row.get('RecommendationId') and a.get('TargetId')]
    targets={a['TargetId'] for a in aliases}
    if len(targets)!=1:return ''
    matches=[r for r in (result.get('lifecycle') or {}).get('Records',[]) if r.get('EntityType')=='finding' and r.get('CurrentEntityId') in targets]
    if len(matches)!=1:return ''
    record=matches[0]
    text={'Baseline status':record.get('BaselineAssessmentStatus'),'Current status':record.get('CurrentAssessmentStatus'),
        'Delta state':record['State'],'What changed':record.get('StateTransitionReason'),
        'Metric changes':record.get('MetricChanges'),'Qualifications':record.get('Qualifications'),
        'Baseline evidence':record.get('BaselineEvidenceReferences'),'Current evidence':record.get('CurrentEvidenceReferences'),
        'Remaining closure requirement':record.get('RemainingClosureRequirement'),'Remains open':record.get('RemainsOpen')}
    return '<details><summary>Reassessment finding detail</summary><dl>'+''.join('<dt>'+escape(key)+'</dt><dd>'
        +escape(json.dumps(value,ensure_ascii=False) if isinstance(value,(dict,list)) else str(value))+'</dd>' for key,value in text.items())+'</dl></details>'

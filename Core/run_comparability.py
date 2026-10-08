"""Deterministic comparison eligibility. Never change states or readiness."""
from collections import defaultdict
from copy import deepcopy
import json
from .assessment_identity import digest

OUTCOMES = {'Comparable','ComparableWithQualifications','NotComparable','NotEvaluated'}
ELIGIBILITY = {'eligible','eligible with qualifications','not eligible','unresolved legacy metadata'}
ITEM_TYPES = {'control','finding','observation','action'}
FACT_BOUNDARIES = ('provider','control_id','metric_id','metric_definition','unit','population',
    'population_definition','scope','affected_objects','window','window_start','window_end',
    'reporting_basis','control_definition_version')
BAD_STATES = {'failed','unavailable','not_requested','not_selected','unlicensed','inaccessible',
    'insufficient_permission','missing','unsupported','unknown','partial'}


def _encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',', ':'),default=str)


def reason(code, area, detail):
    return {'Code':code,'Area':str(area),'Detail':detail}


def not_evaluated():
    return {'Outcome':'NotEvaluated','Reasons':[reason('no_baseline','run','No baseline comparison requested.')],
            'Items':[],'DeltaInterpretation':'Not implemented'}


def recorded_boundaries(result, purpose=None):
    if 'run_boundaries' in result:
        return deepcopy(result['run_boundaries'])
    facts = [row for row in result.get('evidence',[]) if isinstance(row,dict)]
    scopes = sorted({_encoded(row.get('scope')) for row in facts if row.get('scope') not in (None,'')})
    return {'purpose':purpose,'scope':scopes or None,
        'providers':sorted({str(row['provider']) for row in facts if row.get('provider')}) or None,
        'population_definitions':sorted({_encoded(row.get('population_definition')) for row in facts
            if row.get('population_definition') not in (None,'')}) or None,
        'resource_scopes':scopes or None}


def recorded_coverage(result):
    if 'collection_coverage' in result:
        return deepcopy(result['collection_coverage'])
    groups = defaultdict(list)
    for row in result.get('evidence',[]):
        if isinstance(row,dict):
            boundary = {field:row.get(field) for field in FACT_BOUNDARIES}
            groups[digest(boundary)].append(row)
    coverage = {}
    for key, rows in sorted(groups.items()):
        states = {str(row.get('availability') or 'unknown') for row in rows}
        complete = all(row.get('complete') is True and not row.get('truncated') for row in rows)
        state = next(iter(states)) if len(states)==1 else 'partial'
        coverage[key] = {'state':state,'complete':complete,'control_id':rows[0].get('control_id'),
            'population':rows[0].get('population'),
            'collection_semantics':rows[0].get('metric_definition') or rows[0].get('collection_semantics')}
    for name, source in (result.get('collection_source_states') or {}).items():
        coverage['source:' + name] = deepcopy(source)
    return coverage


def collection_state_summary(bundle):
    """Retain collection states that produced no observations, without raw data."""
    states = {}
    for name, source in (bundle.get('source_statuses') or {}).items():
        if not isinstance(source,dict):
            continue
        state = source.get('availability_status') or source.get('availability') or source.get('status') or (
            'available' if source.get('available') is True else 'unavailable' if source.get('available') is False else 'unknown')
        count = source.get('records_collected')
        if state=='available' and not isinstance(count,bool) and count in (0,'0'):
            state = 'empty'
        states[str(name)] = {'state':state,'complete':source.get('complete') is True or (
            state in {'available','empty'} and not source.get('truncated') and source.get('partial') is not True),
            'population':source.get('population') or source.get('scope'),
            'collection_semantics':source.get('collection_semantics') or source.get('metric_definition'),
            'records_collected':count,'reason':source.get('reason') or ''}
    progress = (bundle.get('collection_context') or {}).get('collection_progress') or {}
    for name, source in (progress.get('services') or {}).items():
        if source.get('status')!='completed':
            states['pipeline:' + name] = {'state':source.get('availability_status') or source.get('status') or 'unknown',
                'complete':False,'reason':source.get('reason') or ''}
    return states


def _good(source):
    state = str(source.get('state') or 'unknown').lower().replace(' ','_')
    if source.get('complete') is not True or state in BAD_STATES:
        return False
    if state == 'empty':
        return bool(source.get('population') and source.get('collection_semantics'))
    return state in {'available','complete','completed'}


def _item_key(entity):
    if entity.get('Type')=='observation':
        # POB identifies a capture in a run. Cross-run eligibility instead uses
        # its fully recorded semantic boundary, retaining both original POB IDs.
        boundary = {key:value for key,value in (entity.get('Boundary') or {}).items()
                    if key not in {'run_id','capture_id'}}
        return ('observation',digest(boundary))
    return (entity.get('Type'),entity.get('Id'))


def _facts(result, entity):
    control = (entity.get('Boundary') or {}).get('control_id')
    rows = [row for row in result.get('evidence',[]) if isinstance(row,dict)]
    if control:
        rows = [row for row in rows if row.get('control_id')==control]
    boundary = entity.get('Boundary') or {}
    for key, source_key in (('provider','provider'),('population','population'),('resource_scope','scope'),
                            ('metric_id','metric_id')):
        if boundary.get(key) not in (None,''):
            rows = [row for row in rows if row.get(source_key)==boundary[key]]
    return [row for row in rows if row.get('selection') not in {'superseded','duplicate'}]


def _action_findings(snapshot, entity):
    return sorted(row.get('TargetId') for row in (snapshot.get('identity') or {}).get('References',[])
        if row.get('OwnerId')==entity.get('Id') and row.get('Relation')=='finding' and row.get('TargetId'))


def evaluate_comparability(current, baseline, *, control_mapping=None):
    if baseline is None:
        return not_evaluated()
    reasons, blocked, qualified = [], False, False
    def add(code, area, detail, *, blocking=False):
        nonlocal blocked, qualified
        reasons.append(reason(code,area,detail))
        blocked |= blocking
        qualified |= not blocking
    a, b = current.get('identity') or {}, baseline.get('identity') or {}
    for field in ('AssessmentId','PrimaryEnvironmentId'):
        if not a.get(field) or not b.get(field) or a[field]!=b[field]:
            add('ownership_mismatch',field,'Assessment and verified environment must match.',blocking=True)
    for field in ('MethodologyVersion','SchemaVersion','CatalogVersion'):
        if not a.get(field) or not b.get(field):
            add('legacy_version_gap',field,'Compatibility is unresolved because version metadata is missing.')
        elif a[field]!=b[field]:
            mapping_ok = field=='CatalogVersion' and isinstance(control_mapping,dict) and (
                control_mapping.get('CurrentCatalogVersion')==a[field] and
                control_mapping.get('BaselineCatalogVersion')==b[field] and control_mapping.get('Controls'))
            add('catalog_mapping' if mapping_ok else 'version_changed',field,
                'Explicit catalog mapping qualifies comparison.' if mapping_ok else
                'No declared compatibility for changed semantic versions.',blocking=not bool(mapping_ok))
    ac, bc = current.get('run_context') or {}, baseline.get('run_context') or {}
    if not ac.get('ReconciliationVersion') or not bc.get('ReconciliationVersion'):
        add('legacy_version_gap','reconciliation','Reconciliation version is unrecorded.')
    elif ac['ReconciliationVersion']!=bc['ReconciliationVersion']:
        add('version_changed','reconciliation','Reconciliation semantics differ.',blocking=True)
    ab, bb = recorded_boundaries(current,ac.get('Purpose')), recorded_boundaries(baseline,bc.get('Purpose'))
    for field in ('purpose','scope','providers','population_definitions','resource_scopes'):
        if ab.get(field) in (None,'',[]) or bb.get(field) in (None,'',[]):
            add('legacy_boundary_gap',field,'Recorded run boundaries are insufficient for unqualified comparison.')
        elif ab[field]!=bb[field]:
            add('boundary_changed',field,'Recorded run boundary changed; affected items need separate eligibility.')
    av, bv = recorded_coverage(current), recorded_coverage(baseline)
    current_weak, baseline_weak = False, False
    if not av:
        add('missing_current_evidence','coverage','Current collection coverage is unrecorded.')
        current_weak = True
    if not bv:
        add('missing_baseline_evidence','coverage','Baseline collection coverage is unrecorded.')
        baseline_weak = True
    for key in sorted(set(av)|set(bv)):
        if key not in av or not _good(av[key]):
            current_weak = True
            add('current_collection_incomplete',key,'Missing, failed, partial or unverified current collection cannot establish absence.')
        if key not in bv or not _good(bv[key]):
            baseline_weak = True
            add('baseline_collection_incomplete',key,'Missing, partial or unverified baseline evidence qualifies comparison.')
    def entities(snapshot):
        groups = defaultdict(list)
        for row in (snapshot.get('identity') or {}).get('Entities',[]):
            if row.get('Type') in ITEM_TYPES:
                groups[_item_key(row)].append(row)
        return groups
    ca, cb = entities(current), entities(baseline)
    items = []
    for key in sorted(set(ca)|set(cb),key=_encoded):
        left, right = ca.get(key,[]), cb.get(key,[])
        item_reasons = []
        state = 'eligible'
        if len(left)>1 or len(right)>1:
            state = 'unresolved legacy metadata'
            item_reasons.append(reason('ambiguous_identity',key[0],'No first-match identity resolution is allowed.'))
        elif not left or not right:
            state = 'not eligible'
            item_reasons.append(reason('missing_current_item' if not left else 'missing_baseline_item',key[0],
                'Absent output establishes no lifecycle transition or resolution.'))
        elif (a.get('CatalogVersion')!=b.get('CatalogVersion') and
              (not control_mapping or not isinstance(control_mapping.get('Controls'),dict) or
               control_mapping['Controls'].get((right[0].get('Boundary') or {}).get('control_id'))
                  != (left[0].get('Boundary') or {}).get('control_id'))):
            state = 'not eligible'
            item_reasons.append(reason('control_mapping_missing',key[0],
                'This item has no explicit mapping between the changed control catalogs.'))
        elif blocked or current_weak:
            state = 'not eligible'
            item_reasons.append(reason('run_boundary_or_collection_ineligible',key[0],
                'Incompatible run semantics or incomplete current collection blocks direct item comparison.'))
        else:
            current_facts, baseline_facts = _facts(current,left[0]), _facts(baseline,right[0])
            if key[0]=='action' and (not _action_findings(current,left[0]) or
                    _action_findings(current,left[0])!=_action_findings(baseline,right[0])):
                state = 'not eligible'
                item_reasons.append(reason('action_support_changed',key[0],
                    'Action support is missing or its linked finding population changed.'))
            elif not current_facts or not baseline_facts:
                state = 'not eligible'
                item_reasons.append(reason('missing_item_evidence',key[0],'Persistent identity alone is not evidence support.'))
            elif any(row.get('selection')=='conflict' or row.get('availability') in BAD_STATES
                     or row.get('complete') is not True or row.get('value') is None for row in current_facts):
                state = 'not eligible'
                item_reasons.append(reason('current_item_evidence_incomplete',key[0],
                    'Conflicted or incomplete evidence cannot support a favorable interpretation.'))
            elif key[0]=='control' and any(str(row.get('Status') or row.get('Observed') or '').lower()=='not established'
                    for row in current.get('control_results',[])
                    if row.get('ControlId',row.get('Control'))==(left[0].get('Boundary') or {}).get('control_id')):
                state = 'not eligible'
                item_reasons.append(reason('not_established',key[0],'Current control support is not established.'))
            else:
                cf = sorted({_encoded({field:row.get(field) for field in FACT_BOUNDARIES}) for row in current_facts})
                bf = sorted({_encoded({field:row.get(field) for field in FACT_BOUNDARIES}) for row in baseline_facts})
                if cf!=bf:
                    state = 'not eligible'
                    item_reasons.append(reason('item_evidence_boundary_changed',key[0],
                        'Provider, population, resource scope, definition, unit or observation period differs.'))
                elif baseline_weak or qualified:
                    state = 'eligible with qualifications'
                    item_reasons.append(reason('qualified_run',key[0],'Run qualifications also apply to this item.'))
                elif {row.get('evidence_level') for row in current_facts}!={row.get('evidence_level') for row in baseline_facts}:
                    state = 'eligible with qualifications'
                    item_reasons.append(reason('evidence_level_changed',key[0],
                        'Configuration and observed effectiveness establish different questions.'))
        items.append({'Type':key[0],'CurrentId':left[0]['Id'] if len(left)==1 else None,
            'BaselineId':right[0]['Id'] if len(right)==1 else None,'Eligibility':state,'Reasons':item_reasons})
    for origin, snapshot in [('current',current),('baseline',baseline)]:
        for alias in (snapshot.get('identity') or {}).get('Aliases',[]):
            if alias.get('TargetType') in ITEM_TYPES and not alias.get('TargetId'):
                items.append({'Type':alias['TargetType'],'CurrentId':None,'BaselineId':None,
                    'Eligibility':'unresolved legacy metadata','Reasons':[reason('alias_unresolved',origin,
                        'Legacy or ambiguous alias cannot establish cross-run continuity.')]})
    for item in items:
        if item['Eligibility'] not in {'eligible'}:
            qualified = True
    if qualified and not reasons:
        reasons.append(reason('item_eligibility_qualified','items','Some item identities or evidence boundaries are ineligible.'))
    return {'Outcome':'NotComparable' if blocked else 'ComparableWithQualifications' if qualified else 'Comparable',
        'Reasons':sorted(reasons,key=_encoded),'Items':sorted(items,key=_encoded),'DeltaInterpretation':'Not implemented'}

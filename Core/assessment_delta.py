"""Evidence-derived lifecycle records, calculated once at explicit completion."""
from collections import Counter,defaultdict
from copy import deepcopy
from .assessment_identity import digest,entity as identity_entity
from .run_comparability import _facts,_good,recorded_coverage,evaluate_comparability,reason
from .delta_metrics import compare_metric,quality,CONTROL_STATUS_RULE,CONDITION_RULES,VERSION as METRIC_VERSION

VERSION='1.0.0'
STATES=frozenset({'New','Continuing','Unchanged','Changed','Improved','Regressed','NotReassessed',
    'Indeterminate','ResolvedByCurrentEvidence','Reopened','NotComparable','NotEvaluated'})
GOVERNANCE=frozenset({'ClosedByRemediation','AcceptedRisk','ApprovedException','NoLongerApplicable'})
ENTITY_TYPES=frozenset({'control','finding','observation','action','metric'})
BOUNDARY_FIELDS=('provider','control_id','population','population_definition','resource_scope','product','tier',
    'affected_resource_ids','control_definition_version','applicability','rollout_stage','customer_decision','closure_evidence','closure_requirements')
NOTICE=('Comparisons use the explicitly selected baseline. Changed populations or scopes may qualify results. '
    'Missing current evidence does not indicate improvement. ResolvedByCurrentEvidence is evidence of resolution, '
    'not formal customer acceptance. Formal closure, accepted risk and exceptions require separate decisions. '
    'Lifecycle counts are not a readiness score.')


def _entities(snapshot):
    return {row['Id']:row for row in (snapshot.get('identity') or {}).get('Entities',[])
        if row.get('Type') in ENTITY_TYPES and row.get('Id')}


def _retained_baseline(baseline):
    """Recover a previously resolved issue's identity, not its old conclusion."""
    projected=dict(baseline,identity=deepcopy(baseline['identity']))
    known=_entities(projected)
    for record in (baseline.get('lifecycle') or {}).get('Records',[]):
        if (record.get('EntityType')!='finding' or record.get('State')!='ResolvedByCurrentEvidence'
                or record.get('EntityId') in known or not record.get('ResolutionEvidenceReferences')):
            continue
        boundary=record.get('EntityBoundary')
        if not isinstance(boundary,dict):continue
        if (boundary.get('assessment_id')!=baseline['identity'].get('AssessmentId') or
                boundary.get('environment_id')!=baseline['identity'].get('PrimaryEnvironmentId')):continue
        try:
            retained=identity_entity('finding',baseline['identity'],boundary,RetainedLifecycle=True)
        except ValueError:
            continue
        if retained['Id']==record.get('EntityId'):
            projected['identity']['Entities'].append(retained);known[retained['Id']]=retained
    return projected


def evidence_refs(snapshot, facts):
    meta=snapshot.get('identity') or {}
    refs=[]
    for row in facts:
        if not row.get('evidence_id'):continue
        targets=sorted({alias['TargetId'] for alias in meta.get('Aliases',[])
            if alias.get('Namespace')=='EV' and alias.get('Value')==row['evidence_id']
            and alias.get('TargetType')=='evidence_record' and alias.get('TargetId')})
        refs.append(dict(AssessmentId=meta.get('AssessmentId'),RunId=meta.get('RunId'),
            EvidenceId=row['evidence_id'],PersistentEvidenceIds=targets,
            OccurrenceId=row.get('source_occurrence_id'),SourceFile=row.get('source_file'),SourceHash=row.get('source_hash')))
    unique={digest(row):row for row in refs}
    return [unique[key] for key in sorted(unique)]


def _complete(snapshot):
    coverage=recorded_coverage(snapshot)
    return bool(coverage) and all(_good(row) for row in coverage.values())


def _status(snapshot, entity):
    if not entity:return None
    if entity['Type']=='control':
        code=entity['Boundary'].get('control_id')
        rows=[row for row in snapshot.get('control_results',[]) if row.get('ControlId',row.get('Control ID'))==code]
        return rows[0].get('Status') if len(rows)==1 else None
    if entity['Type']=='finding':
        rows=[row for row in snapshot.get('recommendations',[]) if row.get('PersistentFindingId')==entity['Id']]
        if not rows:
            ids={a.get('Value') for a in snapshot.get('identity',{}).get('Aliases',[]) if
                a.get('Namespace')=='RecommendationId:issue' and a.get('TargetId')==entity['Id']}
            rows=[row for row in snapshot.get('recommendations',[]) if row.get('RecommendationId') in ids]
        return rows[0].get('Status') if len(rows)==1 else entity.get('AssessmentStatus')
    return entity.get('AssessmentStatus')


def _control_state(value):
    return {'pass':'Met','met':'Met','fail':'NotMet','notmet':'NotMet',
        'not assessed':'NotVerified','notverified':'NotVerified','not established':'NotVerified'}.get(str(value or '').lower(),'NotVerified')


def resolution_requirement_supported(boundary,changes):
    """Free-text or additional closure criteria require their own later proof."""
    requirements=boundary.get('closure_requirements') or boundary.get('closure_evidence')
    if not requirements:return True
    if not isinstance(requirements,dict):return False
    return any(requirements=={'MetricId':r['MetricId'],'Target':r['Rule']['Target']} for r in changes if r.get('Rule'))


def _entity_facts(snapshot,entity):
    rows=_facts(snapshot,entity)
    if entity['Type']!='observation':return rows
    occurrences=entity.get('SourceOccurrences')
    if occurrences:
        return [row for row in rows if any(row.get('evidence_id')==occurrence.get('EvidenceId') and
            (not occurrence.get('OccurrenceId') or row.get('source_occurrence_id')==occurrence['OccurrenceId'])
            for occurrence in occurrences)]
    # Legacy native observations without occurrence links cannot borrow another
    # native record's measurements just because the metric and population agree.
    if entity['Boundary'].get('native_record_id') or len({row.get('native_id') for row in rows})>1:
        return []
    return rows


def _metric_changes(baseline,current,old,new):
    bf,cf=_entity_facts(baseline,old or new),_entity_facts(current,new or old)
    # Retain changed-scope evidence as context, never as proof for the old scope.
    control=(new or old)['Boundary'].get('control_id')
    if not cf and control and (new or old)['Type']!='observation':
        cf=[row for row in current.get('evidence',[]) if row.get('control_id')==control
            and row.get('selection') not in {'superseded','duplicate'}]
    if not bf and control and (old or new)['Type']!='observation':
        bf=[row for row in baseline.get('evidence',[]) if row.get('control_id')==control
            and row.get('selection') not in {'superseded','duplicate'}]
    def index(rows):
        groups=defaultdict(list)
        for row in rows:
            key=(row.get('metric_id'),row.get('native_id'))
            groups[key].append(row)
        return groups
    bi,ci=index(bf),index(cf)
    changes=[]
    for key in sorted(set(bi)&set(ci),key=str):
        if len(bi[key])==len(ci[key])==1:
            changes.append(compare_metric(bi[key][0],ci[key][0]))
    return bf,cf,changes


def _aliases(snapshot):
    groups=defaultdict(list)
    meta=snapshot.get('identity') or {}
    for row in meta.get('Aliases',[]):
        # Display RecommendationId aliases are explicitly not lifecycle authority.
        if row.get('TargetType') not in ENTITY_TYPES or str(row.get('Namespace','')).startswith('RecommendationId'):
            continue
        key=(row.get('Namespace'),row.get('Value'),row.get('TargetType'),row.get('OriginArtifact'))
        groups[key].append(row)
    return groups


def _matched_items(current,baseline,comparison):
    """Preserve Stage A eligibility; add only uniquely scoped typed compatibility."""
    items=deepcopy(comparison.get('Items') or [])
    ca,ba=_entities(current),_entities(baseline)
    for old in ba.values():
        if not old.get('RetainedLifecycle'):continue
        matches=[row for row in items if row.get('CurrentId')==old['Id'] and not row.get('BaselineId')]
        if len(matches)==1:
            gate=next((row for row in evaluate_comparability(current,baseline)['Items']
                if row.get('CurrentId')==old['Id'] and row.get('BaselineId')==old['Id']),None)
            if gate:
                items.remove(matches[0]);gate['MatchAuthority']='retained_baseline_lifecycle';items.append(gate)
    cg,bg=_aliases(current),_aliases(baseline)
    for key in sorted(set(cg)&set(bg),key=str):
        left,right=cg[key],bg[key]
        if len(left)!=1 or len(right)!=1 or not left[0].get('TargetId') or not right[0].get('TargetId'):continue
        a,b=ca.get(left[0]['TargetId']),ba.get(right[0]['TargetId'])
        if not a or not b or a['Id']==b['Id']:continue
        if any(row.get('AssessmentId')!=snap['identity'].get('AssessmentId') or row.get('OriginRunId')!=snap['identity'].get('RunId')
            for row,snap in ((left[0],current),(right[0],baseline))):continue
        if any(a['Boundary'].get(field)!=b['Boundary'].get(field) for field in BOUNDARY_FIELDS):continue
        ai=[row for row in items if row.get('CurrentId')==a['Id']]
        bi=[row for row in items if row.get('BaselineId')==b['Id']]
        if len(ai)!=1 or len(bi)!=1 or ai[0].get('BaselineId') or bi[0].get('CurrentId'):continue
        # Ask the existing eligibility evaluator about this declared continuity.
        projected=dict(current,identity=deepcopy(current['identity']))
        for row in projected['identity']['Entities']:
            if row.get('Id')==a['Id']:row['Id']=b['Id']
        gate=next((row for row in evaluate_comparability(projected,baseline)['Items']
            if row.get('CurrentId')==b['Id'] and row.get('BaselineId')==b['Id']),None)
        if not gate:continue
        items.remove(ai[0]);items.remove(bi[0])
        gate.update(CurrentId=a['Id'],MatchAuthority='unique_typed_alias',AliasKey=list(key))
        items.append(gate)
    return items


def summary_counts(records):
    counts={kind:dict(sorted(Counter(row['State'] for row in records if row['EntityType']==kind).items()))
        for kind in sorted(ENTITY_TYPES)}
    findings=[row for row in records if row['EntityType']=='finding' and row.get('EntityId')]
    counts['FindingPopulation']=dict(Current=len({r['CurrentEntityId'] for r in findings if r['CurrentEntityId']}),
        Baseline=len({r['BaselineEntityId'] for r in findings if r['BaselineEntityId']}),
        Matched=sum(bool(r['CurrentEntityId'] and r['BaselineEntityId']) for r in findings),
        UnmatchedCurrent=sum(bool(r['CurrentEntityId'] and not r['BaselineEntityId']) for r in findings),
        UnmatchedBaseline=sum(bool(r['BaselineEntityId'] and not r['CurrentEntityId']) for r in findings))
    return counts


def evaluate_delta(current,baseline,*,enabled=True):
    context=current.get('run_context') or {}
    meta=current.get('identity') or {}
    comparison=context.get('Comparability') or {}
    delta=dict(SchemaVersion=VERSION,EngineVersion=VERSION,MetricRuleVersion=METRIC_VERSION,
        AssessmentId=meta.get('AssessmentId'),RunId=meta.get('RunId'),PrimaryEnvironmentId=meta.get('PrimaryEnvironmentId'),
        BaselineRunId=context.get('BaselineRunId'),Enabled=bool(enabled),GeneratedAt=meta.get('CreatedAt'),
        Outcome='NotEvaluated',Reasons=[],Records=[],Summary={},Notice=NOTICE)
    if not enabled or context.get('RunType')!='Reassessment' or baseline is None:
        delta['Reasons']=[reason('delta_disabled' if not enabled else 'no_baseline','lifecycle','No lifecycle classifications calculated.')]
        return delta
    baseline=_retained_baseline(baseline)
    ba,ca=_entities(baseline),_entities(current)
    ownership=(meta.get('State')=='complete' and baseline.get('identity',{}).get('State')=='complete'
        and all(meta.get(key) and meta.get(key)==baseline.get('identity',{}).get(key) for key in ('AssessmentId','PrimaryEnvironmentId')))
    outcome=comparison.get('Outcome','NotComparable') if ownership else 'NotComparable'
    delta.update(Outcome=outcome,Reasons=deepcopy(comparison.get('Reasons') or [reason('comparison_evaluated','run','Explicit baseline comparison evaluated.')]))
    delta['BaselineCoverage']=deepcopy(recorded_coverage(baseline))
    previous={(r.get('EntityType'),r.get('EntityId')):r for r in (baseline.get('lifecycle') or {}).get('Records',[])}
    items=_matched_items(current,baseline,comparison)
    for item in items:
        old,new=ba.get(item.get('BaselineId')),ca.get(item.get('CurrentId'))
        entity=new or old
        if not entity:
            # Ambiguous aliases retain a diagnostic, not a fabricated entity.
            continue
        bf,cf,changes=_metric_changes(baseline,current,old,new)
        eligible=item.get('Eligibility') in {'eligible','eligible with qualifications'}
        baseline_good=_complete(baseline) and bool(bf) and all(quality(f) and f.get('evidence_id') for f in bf)
        current_good=_complete(current) and bool(cf) and all(quality(f) and f.get('evidence_id') for f in cf)
        compatible=bool(changes) and all(r['State']!='Indeterminate' for r in changes)
        full_metrics=len(changes)==len(bf)==len(cf)
        proof=compatible and full_metrics and baseline_good and current_good
        condition_bound=(entity['Type']!='finding' or all(entity['Boundary'].get('condition_key') in
            CONDITION_RULES.get(change['MetricId'],()) for change in changes))
        closure_supported=resolution_requirement_supported(entity['Boundary'],changes)
        related_changed=entity['Type']=='finding' and any(other['Type']=='finding' and other['Id']!=entity['Id'] and
            all(other['Boundary'].get(field)==entity['Boundary'].get(field) for field in ('control_id','condition_key','provider'))
            for other in (ba if not old else ca if not new else {}).values())
        prior=previous.get((entity['Type'],(old or entity)['Id']),{})
        record=dict(EntityId=entity['Id'],EntityType=entity['Type'],AssessmentId=meta.get('AssessmentId'),
            PrimaryEnvironmentId=meta.get('PrimaryEnvironmentId'),RunId=meta.get('RunId'),BaselineRunId=context.get('BaselineRunId'),
            BaselineAssessmentId=baseline.get('identity',{}).get('AssessmentId'),BaselineEnvironmentId=baseline.get('identity',{}).get('PrimaryEnvironmentId'),
            BaselineEntityId=old['Id'] if old else None,CurrentEntityId=new['Id'] if new else None,
            BaselineAssessmentStatus=_status(baseline,old),CurrentAssessmentStatus=_status(current,new),
            Eligibility=item.get('Eligibility'),ComparabilityOutcome=outcome,Reasons=deepcopy(item.get('Reasons') or []),
            Qualifications=deepcopy(comparison.get('Reasons') or []),MetricChanges=changes,
            BaselineEvidenceReferences=evidence_refs(baseline,bf),CurrentEvidenceReferences=evidence_refs(current,cf),
            ResolutionEvidenceReferences=[],PriorResolutionEvidenceReferences=deepcopy(prior.get('ResolutionEvidenceReferences') or []),
            PreviousLifecycleState=prior.get('State'),RuleVersion=VERSION,GeneratedAt=delta['GeneratedAt'],
            MatchAuthority=item.get('MatchAuthority','persistent_identity' if old and new else 'unmatched'),
            EntityBoundary=deepcopy(entity['Boundary']),
            BaselineEntityBoundary=deepcopy(old['Boundary']) if old else None,
            CurrentEntityBoundary=deepcopy(new['Boundary']) if new else None,
            AliasKey=item.get('AliasKey'),BaselineCoverageSufficient=baseline_good,CurrentCoverageSufficient=current_good,
            BaselineConditionRetained=bool(old),RemainsOpen=True,ControlStatusRule=None,
            ConditionMetricBinding=condition_bound,
            ClosureRequirementSupported=closure_supported,
            BoundaryChanges={field:dict(Baseline=(old or {}).get('Boundary',{}).get(field),Current=(new or {}).get('Boundary',{}).get(field))
                for field in BOUNDARY_FIELDS if old and new and old['Boundary'].get(field)!=new['Boundary'].get(field)})
        state='Indeterminate';code='comparison_indeterminate'
        if old and new and old.get('DefinitionVersion')!=new.get('DefinitionVersion'):
            eligible=False
            record['Eligibility']='not eligible'
            record['Reasons'].append(reason('control_definition_version_changed',entity['Type'],'No explicit control-definition compatibility is declared.'))
        if outcome=='NotComparable' or not ownership:
            state,code='NotComparable','run_not_comparable'
        elif any(f.get('selection')=='conflict' for f in cf):
            code='current_evidence_conflict'
        elif any(f.get('availability')=='partial' for f in cf):
            code='partial_current_evidence'
        elif old and (not cf or any(f.get('availability') not in {'available','partial'} for f in cf) or not _complete(current)):
            state,code='NotReassessed','current_evidence_not_obtained'
        elif not old:
            # New needs positive baseline absence evidence, not just an empty register.
            unresolved=any(not alias.get('TargetId') and alias.get('TargetType')==entity['Type']
                for snap in (baseline,current) for alias in snap.get('identity',{}).get('Aliases',[]))
            absent=proof and condition_bound and not related_changed and not unresolved and all(change.get('Rule') and change['Rule']['ResolutionAllowed'] and
                change['Baseline'].get('value')==change['Rule']['Target'] for change in changes)
            if new and absent and any(change['Current'].get('value')!=change['Rule']['Target'] for change in changes):
                state,code='New','baseline_positive_absence_evidence'
            else:code='baseline_absence_not_established'
        elif not new:
            if proof and condition_bound and closure_supported and not related_changed and all(change['ResolutionSupported'] for change in changes):
                state,code='ResolvedByCurrentEvidence','current_positive_resolution_evidence'
            else:code='disappearance_is_not_resolution'
        elif record['BoundaryChanges']:
            code='material_entity_boundary_changed'
        elif entity['Type']=='control' and _control_state(record['CurrentAssessmentStatus'])=='NotVerified':
            state,code='NotReassessed','control_not_verified'
        elif entity['Type']=='control' and eligible and proof:
            before,after=_control_state(record['BaselineAssessmentStatus']),_control_state(record['CurrentAssessmentStatus'])
            supported=all(f.get('evidence_level') in CONTROL_STATUS_RULE['MinimumEvidenceLevels'] for f in bf+cf)
            if before!=after and supported:
                if after=='NotVerified':state,code='NotReassessed','control_not_verified'
                elif before=='NotVerified':state,code='Changed','control_newly_established'
                else:
                    state='ResolvedByCurrentEvidence' if after=='Met' else ('Reopened' if prior.get('State') in
                        {'ResolvedByCurrentEvidence','ClosedByRemediation'} and prior.get('ResolutionEvidenceReferences') else 'Regressed')
                    code='explicit_control_status_transition';record['ControlStatusRule']=deepcopy(CONTROL_STATUS_RULE)
            else:
                state,code=_measured_state(changes,prior,allow_resolution=False)
        elif old and new and eligible and proof and condition_bound:
            state,code=_measured_state(changes,prior,allow_resolution=closure_supported)
        elif old and new and eligible and proof and not condition_bound:
            state,code=(('Unchanged','equivalent_material_condition') if all(change['State']=='Unchanged' for change in changes)
                else ('Changed','non_directional_material_change') if any(change['State']=='Changed' for change in changes)
                else ('Continuing','issue_metric_binding_unregistered'))
        elif old and new and baseline_good and current_good and full_metrics and changes and all(
                set(change['Reasons'])<= {'evidence_level_changed','metric_rule_requirements_not_met'} for change in changes) and any(
                'evidence_level_changed' in change['Reasons'] for change in changes):
            state,code='Changed','evidence_level_changed'
        record.update(State=state,StateTransitionReason=code)
        record['Reasons'].append(reason(code,entity['Type'],_explanation(state)))
        record['Qualifications'].extend(q for change in changes for q in change['Qualifications'])
        if state=='ResolvedByCurrentEvidence':
            record.update(RemainsOpen=False,ResolutionEvidenceReferences=deepcopy(record['CurrentEvidenceReferences']))
        record['RemainingClosureRequirement']=(new or old)['Boundary'].get('closure_requirements') or (new or old)['Boundary'].get('closure_evidence')
        delta['Records'].append(record)
        if entity['Type']=='observation':
            for change in changes:
                metric=deepcopy(record);metric.update(EntityType='metric',MetricId=change['MetricId'],MetricChanges=[change])
                delta['Records'].append(metric)
    # Actions inherit supported finding changes; no inference from owner or dates.
    findings={r['EntityId']:r for r in delta['Records'] if r['EntityType']=='finding'}
    for record in delta['Records']:
        if record['EntityType']!='action' or record['State']=='NotComparable':continue
        refs=current.get('identity',{}).get('References',[])
        linked=[findings.get(r.get('TargetId')) for r in refs if r.get('OwnerId')==record['CurrentEntityId'] and r.get('Relation')=='finding']
        if linked and all(r and r['State']==linked[0]['State'] for r in linked) and record['Eligibility'] in {'eligible','eligible with qualifications'}:
            record['State']=linked[0]['State'];record['StateTransitionReason']='linked_finding_transition'
            record['Reasons'].append(reason('linked_finding_transition','action','State follows uniquely linked evidence-qualified findings.'))
            record['LinkedFindingIds']=[r['EntityId'] for r in linked]
            record['RemainsOpen']=any(r['RemainsOpen'] for r in linked)
            if record['State']=='ResolvedByCurrentEvidence':record['ResolutionEvidenceReferences']=deepcopy(record['CurrentEvidenceReferences'])
    delta['Records'].sort(key=lambda r:(r['EntityType'],r['EntityId'],r.get('MetricId') or ''))
    delta['Summary']=summary_counts(delta['Records'])
    return delta


def _measured_state(changes,prior,*,allow_resolution=True):
    states={r['State'] for r in changes}
    resolved=allow_resolution and all(r['ResolutionSupported'] for r in changes) and (
        any(r.get('Rule') and r['Baseline']['value']!=r['Rule']['Target'] for r in changes)
        or prior.get('State')=='ResolvedByCurrentEvidence')
    if resolved:return 'ResolvedByCurrentEvidence','current_positive_resolution_evidence'
    if prior.get('State') in {'ResolvedByCurrentEvidence','ClosedByRemediation'} and prior.get('ResolutionEvidenceReferences') and 'Regressed' in states and states<= {'Regressed','Unchanged'}:
        return 'Reopened','previous_resolution_and_current_return'
    if states=={'Unchanged'}:return 'Unchanged','equivalent_material_condition'
    if states<= {'Improved','Unchanged'}:return 'Improved','explicit_metric_direction'
    if states<= {'Regressed','Unchanged'}:return 'Regressed','explicit_metric_direction'
    if 'Changed' in states:return 'Changed','non_directional_material_change'
    return 'Continuing','mixed_changes_same_open_condition'


def _explanation(state):
    return {'NotReassessed':'Baseline condition remains unresolved because current reassessment evidence is insufficient.',
        'Indeterminate':'Evidence or comparison boundaries cannot support a defensible transition.',
        'ResolvedByCurrentEvidence':'Comparable current evidence proves the condition absent; formal closure remains separate.',
        'New':'Complete comparable baseline evidence establishes absence; sufficient current evidence establishes the issue.',
        'Reopened':'Persistent issue returned after a retained evidence-supported resolution.'}.get(state,
        'State follows retained evidence and explicit comparison rules; current assessment status remains separate.')

"""Validate the additive reconciliation graph without repairing declarations."""
from collections import Counter


def validate_reconciliation(result):
    model=result.get('reconciliation')
    if model is None:
        return []  # Direct legacy identity declarations remain a supported API.
    diagnostics=[]
    def report(code,subject,message,severity='error'):
        diagnostics.append({'code':code,'subject':str(subject),'message':message,'severity':severity})
    if not isinstance(model,dict):
        report('invalid_reconciliation_shape','reconciliation','Reconciliation must be an object.')
        return diagnostics
    if model.get('state')=='legacy':
        report('legacy_reconciliation','reconciliation','No recorded semantic comparison or finding membership; equivalence remains unresolved.','compatibility_warning')
        return diagnostics
    if model.get('version')!='2.0.0':
        report('reconciliation_version','reconciliation','Unsupported reconciliation model version.')
    observations=model.get('observations') or []
    occurrences=model.get('occurrences') or []
    findings=model.get('findings') or []
    for name in ('observations','occurrences','findings','conflicts','exact_duplicate_groups','supporting_observations','diagnostics','aggregates'):
        if name in model and (not isinstance(model[name],list) or any(not isinstance(row,dict) for row in model[name])):
            report('invalid_reconciliation_shape',name,'Reconciliation registers must contain objects in lists.')
    if diagnostics:
        return diagnostics
    ids={row.get('id') for row in observations}
    occurrence_ids={row.get('id') for row in occurrences}
    finding_ids={row.get('id') for row in findings}
    observation_index={row.get('id'):row for row in observations}
    occurrence_index={row.get('id'):row for row in occurrences}
    declared_occurrences=Counter(row.get('source_occurrence_id') for row in result.get('evidence') or [] if row.get('source_occurrence_id'))
    if declared_occurrences and declared_occurrences!=Counter(row.get('id') for row in occurrences):
        report('lost_source_occurrences','reconciliation','The retained evidence rows and source-occurrence register do not reconcile.')
    for name,rows in (('observation',observations),('occurrence',occurrences),('finding',findings)):
        for identifier,count in Counter(row.get('id') for row in rows).items():
            if not identifier or count>1:
                report('duplicate_reconciliation_identity',identifier,f'{name} identity is missing or overwritten by several declarations.')
    ownership=Counter(identifier for row in observations for identifier in row.get('occurrence_ids') or [])
    for identifier,count in ownership.items():
        if count!=1:
            report('duplicate_exact_membership',identifier,'A source occurrence belongs to more than one retained observation.')
    for row in observations:
        if not row.get('reason'):
            report('missing_reconciliation_reason',row.get('id'),'Observation selection requires a recorded reason.')
        for identifier in row.get('occurrence_ids') or []:
            if identifier not in occurrence_ids:
                report('dangling_occurrence',identifier,'Observation references an absent source occurrence.')
    for row in occurrences:
        if row.get('observation_id') not in ids or ownership[row.get('id')]!=1:
            report('dangling_observation',row.get('id'),'Source occurrence must resolve to exactly one retained observation.')
    duplicates=set()
    for group in model.get('exact_duplicate_groups') or []:
        if group.get('observation_id') not in ids:
            report('dangling_duplicate_group',group.get('observation_id'),'Exact-duplicate group has no retained observation.')
        if group.get('count')!=len(group.get('occurrence_ids') or []):
            report('duplicate_count_mismatch',group.get('observation_id'),'Duplicate count must equal retained occurrence membership.')
        from .observation_reconciliation import semantic_payload
        sources=[occurrence_index[identifier]['source'] for identifier in group.get('occurrence_ids',[]) if identifier in occurrence_index]
        if sources and any(semantic_payload(source)!=semantic_payload(sources[0]) for source in sources[1:]):
            report('incompatible_exact_group',group.get('observation_id'),'Exact duplicate membership contains materially different observations.')
        for identifier in group.get('occurrence_ids') or []:
            if identifier in duplicates:
                report('duplicate_exact_membership',identifier,'Occurrence appears in more than one exact-duplicate group.')
            duplicates.add(identifier)
            if identifier not in occurrence_ids:
                report('dangling_occurrence',identifier,'Duplicate group refers to an absent occurrence.')
    for conflict in model.get('conflicts') or []:
        if conflict.get('result')!='Not established':
            report('favorable_conflict',conflict.get('boundary'),'An unresolved comparable conflict must remain Not established.')
        if not conflict.get('reason'):
            report('missing_reconciliation_reason',conflict.get('boundary'),'Conflict requires a recorded reason.')
        for identifier in conflict.get('observation_ids') or []:
            if identifier not in ids:
                report('dangling_observation',identifier,'Conflict references an absent observation.')
        conflict_controls={row['dimensions'].get('control_id') for row in observations if row.get('id') in conflict.get('observation_ids',[])}
        for control in result.get('controls') or []:
            if control.get('control_id') in conflict_controls and control.get('status')=='Observed':
                report('favorable_conflict',control['control_id'],'A control with unresolved comparable evidence cannot be favorable.')
    meta=result.get('identity') or {}
    for observation in observations:
        boundary=observation.get('dimensions') or {}
        for field,code in (('AssessmentId','cross_assessment'),('PrimaryEnvironmentId','cross_environment')):
            if boundary.get(field) and meta.get(field) and boundary[field]!=meta[field]:
                report(code,observation.get('id'),'Observation declares a different assessment or primary environment.')
    for finding in findings:
        identifier=finding.get('id')
        if not finding.get('reason'):
            report('missing_reconciliation_reason',identifier,'Finding membership requires a recorded grouping rule.')
        if finding.get('authority')=='legacy_unvalidated':
            report('legacy_finding_grouping',identifier,'No declared persistent or scoped issue authority; legacy occurrence is retained independently.','compatibility_warning')
        for observation_id in finding.get('observation_ids') or []:
            if observation_id not in ids:
                report('dangling_observation',observation_id,'Finding support references an absent observation.')
            else:
                observed=observation_index[observation_id].get('dimensions') or {}
                for field,destination in (('AssessmentId','AssessmentId'),('PrimaryEnvironmentId','PrimaryEnvironmentId'),
                    ('ControlId','control_id'),('Provider','provider'),('Population','population'),('EvidenceScope','scope')):
                    declared=(finding.get('boundary') or {}).get(field)
                    if declared and observed.get(destination) and declared!=observed[destination]:
                        report('incompatible_finding_support',identifier,f'Finding support crosses a declared {field} boundary.')
        boundary=finding.get('boundary') or {}
        if boundary.get('PersistentFindingId') and finding.get('persistent_finding_id') and boundary['PersistentFindingId']!=finding['persistent_finding_id']:
            report('persistent_finding_mismatch',identifier,'Declared persistent finding does not match its registered semantic boundary.')
        for member in finding.get('members') or []:
            for field,code in (('AssessmentId','cross_assessment'),('PrimaryEnvironmentId','cross_environment')):
                declared=member.get(field)
                expected=boundary.get(field) or meta.get(field)
                if declared and expected and declared!=expected:
                    report(code,identifier,'Finding membership crosses the declared execution boundary.')
            for field in ('ControlId','Provider','Population','PopulationDefinition','EvidenceScope','Applicability',
                          'RolloutStage','CustomerDecision','ClosureEvidence','ClosureRequirements'):
                if member.get(field) and boundary.get(field) and member[field]!=boundary[field]:
                    report('incompatible_finding_membership',identifier,f'Finding membership combines different {field} declarations.')
    for relation in model.get('supporting_observations') or []:
        if relation.get('finding_id') not in finding_ids or relation.get('observation_id') not in ids:
            report('dangling_support',relation,'Supporting-observation relationship must resolve both ends.')
        if not relation.get('reason'):
            report('missing_reconciliation_reason',relation,'Selected support requires a recorded reason.')
    for aggregate in model.get('aggregates') or []:
        members=[observation_index[value] for value in aggregate.get('observation_ids') or [] if value in observation_index]
        if len(members)!=len(aggregate.get('observation_ids') or []):
            report('dangling_aggregate',aggregate,'Aggregate references absent observations.')
        for dimension in ('unit','population','population_definition','scope','window','window_start','window_end'):
            values={str((row.get('dimensions') or {}).get(dimension)) for row in members}
            if len(values)>1:
                report('incompatible_aggregate',aggregate,f'Aggregate combines different {dimension} boundaries.')
        if not aggregate.get('rule') or not aggregate.get('reason'):
            report('unvalidated_aggregate',aggregate,'An aggregate requires an explicit measurement rule and reason; disjointness is never assumed.')
    recommendation_ids=[row.get('RecommendationId') for row in result.get('recommendations') or []]
    for identifier,count in Counter(recommendation_ids).items():
        if count>1:
            report('overwritten_finding',identifier,'A legacy navigation ID addresses several records without collision allocation.')
    for diagnostic in model.get('diagnostics') or []:
        if not diagnostic.get('reason'):
            report('missing_reconciliation_reason',diagnostic.get('code'),'Reconciliation diagnostic requires a recorded reason.')
    return diagnostics

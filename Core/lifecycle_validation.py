"""Pure publication gate for lifecycle transitions; never repairs a conclusion."""
from collections import Counter
from .assessment_delta import STATES,GOVERNANCE,ENTITY_TYPES,VERSION,summary_counts,_control_state,_status,resolution_requirement_supported
from .delta_metrics import compare_metric,measurement,quality,CONTROL_STATUS_RULE,CONDITION_RULES
from .run_comparability import recorded_coverage,_good
from .assessment_identity import semantic_id


def validate_lifecycle(result):
    diagnostics=[]
    def report(code,message,subject='lifecycle',severity='error'):
        diagnostics.append(dict(severity=severity,code=code,subject=str(subject),message=message))
    if 'lifecycle' not in result:
        if result.get('run_context'):
            report('legacy_lifecycle_unrecorded','Snapshot has no recorded lifecycle calculation; no states inferred.',severity='compatibility_warning')
        return diagnostics
    delta=result['lifecycle'];meta=result.get('identity') or {};context=result.get('run_context') or {}
    if not isinstance(delta,dict) or delta.get('SchemaVersion')!=VERSION or not isinstance(delta.get('Records'),list):
        report('lifecycle_schema','Unsupported or malformed lifecycle contract.')
        return diagnostics
    for field in ('AssessmentId','RunId','PrimaryEnvironmentId'):
        if delta.get(field)!=meta.get(field):report('lifecycle_ownership','Lifecycle and assessment identity differ for '+field+'.')
    if delta.get('BaselineRunId')!=context.get('BaselineRunId'):
        report('lifecycle_baseline','Lifecycle baseline differs from explicitly selected run.')
    records=delta['Records']
    if delta.get('EngineVersion')!=VERSION or not delta.get('GeneratedAt'):
        report('lifecycle_engine_version','Lifecycle calculation requires a supported version and recorded timestamp.')
    if 'DeltaEnabled' in context and delta.get('Enabled')!=context['DeltaEnabled']:
        report('lifecycle_enabled_mismatch','Lifecycle enabled state differs from prepared run intent.')
    if (context.get('RunType')!='Reassessment' or not context.get('BaselineRunId') or not delta.get('Enabled')):
        if records or delta.get('Outcome')!='NotEvaluated':
            report('lifecycle_unintended','Only enabled explicit reassessments may classify lifecycle states.')
    elif delta.get('Outcome')!=(context.get('Comparability') or {}).get('Outcome'):
        report('lifecycle_comparability','Lifecycle outcome differs from the shared comparison gate.')
    if not delta.get('Reasons'):report('lifecycle_reasons_missing','Lifecycle context requires recorded reasons.')
    seen=Counter()
    indexed={row.get('Id'):row for row in meta.get('Entities',[]) if isinstance(row,dict)}
    current_coverage=recorded_coverage(result)
    for record in records:
        if not isinstance(record,dict):report('lifecycle_record_invalid','Lifecycle record must be an object.');continue
        kind=record.get('EntityType');id_=record.get('EntityId');state=record.get('State')
        seen[(kind,id_,record.get('MetricId'))]+=1
        if not id_ or kind not in ENTITY_TYPES or state not in STATES:
            report('lifecycle_record_invalid','Entity identity, type and state must be supported.',id_)
        if state in GOVERNANCE:report('lifecycle_governance_state','Delta engine cannot create a governance approval.',id_)
        for field in ('AssessmentId','RunId','PrimaryEnvironmentId','BaselineRunId'):
            expected=context.get(field) if field=='BaselineRunId' else meta.get(field)
            if record.get(field)!=expected:report('lifecycle_ownership','Lifecycle record has foreign execution ownership.',id_)
        if record.get('BaselineAssessmentId')!=meta.get('AssessmentId') or record.get('BaselineEnvironmentId')!=meta.get('PrimaryEnvironmentId'):
            report('lifecycle_ownership','Baseline entity belongs to a different assessment or environment.',id_)
        if not record.get('Reasons') or not record.get('StateTransitionReason'):
            report('lifecycle_reasons_missing','Every lifecycle record requires structured transition reasons.',id_)
        elif any(not isinstance(r,dict) or not all(r.get(key) for key in ('Code','Area','Detail')) for r in record['Reasons']):
            report('lifecycle_reasons_invalid','Lifecycle reasons must contain code, area and detail.',id_)
        old,new=record.get('BaselineEntityId'),record.get('CurrentEntityId')
        identity_kind=kind if kind!='metric' else 'observation'
        for field,target in [('BaselineEntityBoundary',old),('CurrentEntityBoundary',new)]:
            if target:
                try:
                    valid=semantic_id(identity_kind,**(record.get(field) or {}))==target
                except (ValueError,TypeError):
                    valid=False
                if not valid:report('lifecycle_entity_boundary','Lifecycle boundary does not reproduce its persistent identity.',id_)
        if record.get('MatchAuthority') in {'persistent_identity','unique_typed_alias'} and (not old or not new):
            report('lifecycle_matched_entity_missing','Matched lifecycle record requires both entity IDs.',id_)
        if new and (new not in indexed or indexed[new].get('Type')!=(kind if kind!='metric' else 'observation')):
            report('lifecycle_current_entity_missing','Current persistent entity does not resolve with its declared type.',id_)
        if new in indexed and record.get('EntityBoundary')!=indexed[new].get('Boundary'):
            report('lifecycle_entity_boundary','Lifecycle entity boundary differs from current assessment identity.',id_)
        if new in indexed and kind in {'control','finding'} and record.get('CurrentAssessmentStatus')!=_status(result,indexed[new]):
            report('lifecycle_current_status_mismatch','Lifecycle current status differs from the unchanged assessment status.',id_)
        if state=='NotReassessed' and not old:report('lifecycle_not_reassessed_without_baseline','NotReassessed retains a baseline condition.',id_)
        if delta.get('Outcome')=='NotComparable' and state!='NotComparable':
            report('lifecycle_not_comparable_transition','Blocked run cannot claim favorable or directional states.',id_)
        favorable=state in {'New','Improved','Regressed','ResolvedByCurrentEvidence','Reopened'}
        changes=record.get('MetricChanges')
        if not isinstance(changes,list):report('lifecycle_metric_calculation','Metric changes must be a list.',id_);changes=[]
        calculated=[]
        for change in changes:
            if not isinstance(change,dict) or not isinstance(change.get('Baseline'),dict) or not isinstance(change.get('Current'),dict):
                report('lifecycle_metric_calculation','Malformed metric comparison.',id_);continue
            expected=compare_metric(change['Baseline'],change['Current']);calculated.append(expected)
            if expected!=change:report('lifecycle_metric_calculation','Metric rule or calculation differs from the versioned registry.',id_)
            current=change['Current']
            facts=[r for r in result.get('evidence',[]) if isinstance(r,dict) and r.get('evidence_id')==current.get('evidence_id')]
            if not any(measurement(fact)==current for fact in facts):
                report('lifecycle_current_evidence_mismatch','Stored current measurement does not match retained assessment evidence.',id_)
        for field,run in [('BaselineEvidenceReferences',context.get('BaselineRunId')),('CurrentEvidenceReferences',meta.get('RunId'))]:
            refs=record.get(field)
            if not isinstance(refs,list):report('lifecycle_evidence_reference','Evidence references must be a list.',id_);continue
            if favorable and not refs:report('lifecycle_evidence_reference','Evidence-derived transition lacks supporting references.',id_)
            for ref in refs:
                if (not isinstance(ref,dict) or not ref.get('EvidenceId') or ref.get('RunId')!=run
                        or ref.get('AssessmentId')!=meta.get('AssessmentId')):
                    report('lifecycle_evidence_reference','Evidence reference has missing or foreign execution ownership.',id_)
                elif favorable and ref['EvidenceId'] not in {row['Baseline' if field=='BaselineEvidenceReferences' else 'Current'].get('evidence_id') for row in calculated}:
                    report('lifecycle_evidence_reference','Transition evidence reference does not support its retained measurements.',id_)
        if favorable:
            if kind=='finding' and (not record.get('ConditionMetricBinding') or not all(
                (record.get('EntityBoundary') or {}).get('condition_key') in CONDITION_RULES.get(row['MetricId'],()) for row in calculated)):
                report('lifecycle_condition_metric_unbound','The comparison metric is not declared evidence for this finding condition.',id_)
            if (not record.get('BaselineCoverageSufficient') or not record.get('CurrentCoverageSufficient')
                    or not current_coverage or not all(_good(row) for row in current_coverage.values())
                    or not delta.get('BaselineCoverage') or not all(_good(row) for row in delta.get('BaselineCoverage',{}).values())
                    or not calculated or any(row['State']=='Indeterminate' for row in calculated)):
                report('lifecycle_coverage_insufficient','Transition requires complete, compatible baseline and current support.',id_)
            if record.get('BoundaryChanges'):report('lifecycle_boundary_changed','Changed material entity boundaries cannot establish a favorable transition.',id_)
            if old and new and record.get('Eligibility') not in {'eligible','eligible with qualifications'}:
                report('lifecycle_direction_ineligible','Matched transition requires item comparison eligibility.',id_)
            if old and new and record.get('MatchAuthority')=='persistent_identity':
                gate=[row for row in (context.get('Comparability') or {}).get('Items',[]) if
                    row.get('Type')==(kind if kind!='metric' else 'observation') and row.get('CurrentId')==new and row.get('BaselineId')==old]
                if len(gate)!=1 or gate[0].get('Eligibility') not in {'eligible','eligible with qualifications'}:
                    report('lifecycle_shared_gate_ineligible','Lifecycle transition conflicts with Stage A item eligibility.',id_)
        if state in {'Improved','Regressed','Reopened'}:
            if record.get('Eligibility') not in {'eligible','eligible with qualifications'}:
                report('lifecycle_direction_ineligible','Directional transition is blocked by item eligibility.',id_)
            rule=record.get('ControlStatusRule')
            required='Regressed' if state=='Reopened' else state
            if rule:
                if (rule!=CONTROL_STATUS_RULE or kind!='control' or _control_state(record.get('BaselineAssessmentStatus'))!='Met'
                        or _control_state(record.get('CurrentAssessmentStatus'))!='NotMet'):
                    report('lifecycle_control_transition','Control direction requires the explicit categorical rule.',id_)
            elif (not any(row['State']==required and row.get('Rule') for row in calculated)
                    or any(row['State'] not in {required,'Unchanged'} for row in calculated)):
                report('lifecycle_direction_rule_missing','Improved and Regressed require an explicit applicable metric-direction rule.',id_)
        if state=='ResolvedByCurrentEvidence':
            if not resolution_requirement_supported(record.get('EntityBoundary') or {},calculated):
                report('lifecycle_closure_requirement_unproven','Additional closure requirements need explicit supporting proof.',id_)
            refs=record.get('ResolutionEvidenceReferences')
            if not old or not refs or refs!=record.get('CurrentEvidenceReferences'):
                report('lifecycle_resolution_evidence_missing','Resolution requires a baseline condition and retained current proof.',id_)
            rule=record.get('ControlStatusRule')
            supported=(bool(calculated) and all(row['ResolutionSupported'] for row in calculated))
            if rule:
                supported=(kind=='control' and rule==CONTROL_STATUS_RULE
                    and _control_state(record.get('BaselineAssessmentStatus'))=='NotMet'
                    and _control_state(record.get('CurrentAssessmentStatus'))=='Met'
                    and all(row['Current']['evidence_level'] in rule['MinimumEvidenceLevels'] for row in calculated))
            if not supported:report('lifecycle_resolution_without_proof','Disappearance or unsupported metrics cannot prove resolution.',id_)
            if record.get('RemainsOpen') is not False:report('lifecycle_open_state','Evidence-resolved condition must be separate from open work and formal closure.',id_)
        if state=='New':
            if (old or not new or not calculated or not all(row.get('Rule') and row['Rule']['ResolutionAllowed']
                    and row['Baseline']['value']==row['Rule']['Target'] and row['Current']['value']!=row['Rule']['Target'] for row in calculated)):
                report('lifecycle_new_without_absence_proof','New requires positive baseline absence evidence for the same issue boundary.',id_)
        if state=='Reopened' and (not old or not new or record.get('PreviousLifecycleState') not in {'ResolvedByCurrentEvidence','ClosedByRemediation'}
                or not record.get('PriorResolutionEvidenceReferences')):
            report('lifecycle_reopened_without_resolution','Reopened requires persistent continuity and retained prior resolution.',id_)
        if record.get('MatchAuthority')=='unique_typed_alias':
            key=record.get('AliasKey')
            aliases=[a for a in meta.get('Aliases',[]) if [a.get('Namespace'),a.get('Value'),a.get('TargetType'),a.get('OriginArtifact')]==key]
            if len(aliases)!=1 or aliases[0].get('TargetId')!=new or str(aliases[0].get('Namespace','')).startswith('RecommendationId'):
                report('lifecycle_alias_unresolved','Ambiguous or display aliases cannot establish lifecycle continuity.',id_)
    if any(count>1 for count in seen.values()):report('lifecycle_duplicate_entity','Lifecycle records must be unique by typed entity and metric.')
    valid_records=[r for r in records if isinstance(r,dict) and r.get('EntityType') in ENTITY_TYPES and r.get('State') in STATES
        and all(key in r for key in ('CurrentEntityId','BaselineEntityId'))]
    if records and delta.get('Summary')!=summary_counts(valid_records):
        report('lifecycle_summary_mismatch','Lifecycle summary does not reconcile with unique detailed records.')
    return diagnostics

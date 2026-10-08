"""Scoped customer-issue grouping and collision-safe compatibility allocation."""
from collections import defaultdict
from copy import deepcopy

from .observation_reconciliation import encoded, key, _finalists

MATERIAL_FIELDS = ('AssessmentId','PrimaryEnvironmentId','TenantId','ControlId','ControlDefinitionVersion',
    'Provider','Population','PopulationDefinition','EvidenceScope','Product','Tier','ObservationWindow',
    'WindowStart','WindowEnd','Applicability','RolloutStage','CustomerDecision','ClosureEvidence',
    'ClosureRequirements','AffectedObjectIds','PersistentFindingId')
EARLY_FIELDS = ('Observation','Disposition','control_result','EvidenceSource','ObservationDate',
    'SourceObservedAt','MeasuredValue','NativeRecordId','SourceCaptureId','SourceHash',
    'EvidenceComplete','EvidenceTruncated','SourceType','SourceFile','Qualification','SourceAvailability','EvidenceDateBasis','EvidenceLevel')


def finding_boundary(row):
    return {field:row.get(field) for field in MATERIAL_FIELDS}


def finding_key(row):
    authority = row.get('PersistentFindingId') or row.get('FindingKey')
    if not authority:
        return key(['unvalidated_legacy_occurrence', row])
    # Evidence gap and a confirmed customer condition are different questions.
    return key([authority,finding_boundary(row),row.get('Disposition')=='Coverage'])


def source_occurrences(row):
    return deepcopy(row.get('SourceOccurrences') or [
        {field:value for field,value in row.items() if field not in {'SourceOccurrences','FindingMembership'}}])


def _rank(row):
    statuses={'critical':0,'action required':1,'warning':3,'success':9}
    return ({'High':0,'Medium':1,'Low':2}.get(row.get('Priority'),3),
        statuses.get(str(row.get('Status') or '').lower(),5),str(row.get('Feature') or ''),encoded(row))


def group_findings(records, *, early=False):
    groups=defaultdict(list)
    for row in records:
        boundary=finding_key(row)
        if early:
            declaration=row.get('InvestigationEvidence') or {}
            boundary=key([boundary,{field:row.get(field) for field in EARLY_FIELDS},
                          declaration.get('source') if isinstance(declaration,dict) else None])
        groups[boundary].append(row)
    selected, archived, findings, diagnostics=[],[],[],[]
    for boundary, group in groups.items():
        supported=[row for row in group if row.get('EvidenceStatus')=='supported']
        components=defaultdict(list)
        for row in supported:
            components[key([row.get('EvidenceLevel'),row.get('ObservationWindow'),row.get('SourceType')])].append(row)
        candidates=[]
        for component in components.values():
            complete=[row for row in component if row.get('EvidenceComplete') is True]
            pool=complete or component
            adapted=[dict(row,source_observed_at=row.get('SourceObservedAt') or row.get('ObservationDate')) for row in pool]
            candidate_ids={key({field:value for field,value in row.items() if field!='source_observed_at'}) for row in _finalists(adapted)}
            candidates.extend(row for row in pool if key(row) in candidate_ids)
        candidates=candidates or [row for row in group if row.get('Historical')!='Yes'] or group
        # A later configuration capture cannot replace a different operational
        # component of the same customer issue. Severity only selects wording.
        failures=[row for row in candidates if row.get('Disposition')=='Action' or row.get('control_result')=='fail']
        original=min(failures or candidates,key=_rank)
        winner=deepcopy(original)
        members=sorted([member for row in group for member in source_occurrences(row)],key=encoded)
        member_ids=[key(member)+':'+str(index) for index,member in enumerate(members)]
        winner['SourceOccurrences']=members
        compatibility_ids={value for row in group if row.get('Historical')!='Yes' and row.get('SourceType')!='prior_assessment'
            for value in [row.get('RecommendationId'),*(row.get('CompatibilityRecommendationIds') or [])] if value}
        if compatibility_ids:
            winner['CompatibilityRecommendationIds']=sorted(compatibility_ids)
        winner['FindingMembership']={'id':boundary,'authority':'persistent_finding' if winner.get('PersistentFindingId') else 'explicit_scoped_issue' if winner.get('FindingKey') else 'legacy_unvalidated',
            'boundary':finding_boundary(winner),'member_ids':member_ids,
            'reason':'All explicit customer-issue dimensions match; supporting declarations remain separate.' if winner.get('FindingKey') or winner.get('PersistentFindingId') else 'No grouping authority; this legacy occurrence remains independent.'}
        comparable=defaultdict(set)
        for row in candidates:
            comparable[key([row.get('EvidenceLevel'),row.get('ObservationWindow'),row.get('SourceType')])].add(
                (row.get('Disposition'),row.get('control_result')))
        if not early and supported and any(len(conclusions)>1 for conclusions in comparable.values()):
            winner.update(EvidenceStatus='conflict',Disposition='Action',
                Qualification='Comparable observations have incompatible conclusions. Confirm the condition before relying on it.')
        history=[row for row in group if row.get('Historical')=='Yes']
        winner['RelatedEvidenceIds']=sorted({evidence_id for row in group for evidence_id in
            [row.get('EvidenceId'),*(row.get('RelatedEvidenceIds') or [])] if evidence_id})
        winner['HistoricalReferences']=sorted([{'RecommendationId':row.get('OriginalRecommendationId',''),
            'ReportDate':row.get('PriorReportDate',''),'SourceFile':row.get('SourceFile','')} for row in history],key=encoded)
        if history and supported:
            winner['Qualification']=(str(winner.get('Qualification') or '')+' The same condition appeared in an earlier assessment; the comparable current evidence is used here.').strip()
        features=sorted({str(row.get('Feature') or '') for row in group if row.get('Feature') and row.get('Feature')!=winner.get('Feature')})
        if features:
            winner['AlsoLicensedVia']='; '.join(features)
        buckets=sorted({part.strip() for row in group for part in str(row.get('EvidenceKey') or '').split(';') if part.strip()})
        if buckets:
            winner['EvidenceKey']='; '.join(buckets)
        if len(group)>1 or not early:
            selected.append(winner)
        else:
            selected.append(deepcopy(group[0]))
        archived.extend(dict(deepcopy(row),Selection='superseded') for row in sorted(group,key=encoded) if row is not original)
        findings.append(dict(winner['FindingMembership'],members=members,related_evidence_ids=winner['RelatedEvidenceIds'],
            selected_display_member=key(original),conflict=winner.get('EvidenceStatus')=='conflict'))
        missing=[field for field in ('ControlId','Provider','Population','EvidenceScope') if not winner.get(field)]
        if missing or not winner.get('FindingKey') and not winner.get('PersistentFindingId'):
            diagnostics.append({'code':'legacy_finding_grouping','severity':'compatibility_warning','finding':boundary,
                'missing_dimensions':missing,'reason':'Legacy issue declarations are qualified; wording and display IDs do not establish persistent equivalence.'})
    # Preserve established customer ordering; semantic membership itself is sorted.
    return selected,archived,{'findings':sorted(findings,key=lambda row:row['id']), 'diagnostics':sorted(diagnostics,key=encoded)}


def allocate_display_ids(rows, fallback):
    """Keep a legacy anchor once, retain aliases and allocate stable collision links."""
    groups=defaultdict(list)
    for row in rows:
        groups[str(row.get('RecommendationId') or fallback(row))].append(row)
    diagnostics=[]
    used=set(groups)
    for identifier,group in sorted(groups.items()):
        ordered=sorted(group,key=lambda row:(finding_key(row),encoded(row)))
        for index,row in enumerate(ordered):
            aliases=set(row.get('CompatibilityRecommendationIds') or [])
            aliases.add(identifier)
            row['CompatibilityRecommendationIds']=sorted(aliases)
            candidate=identifier
            if index:
                candidate=identifier+'-'+key([finding_key(row),row.get('EvidenceId'),row.get('SourceType')])[:12].upper()
                suffix=1
                while candidate in used:
                    suffix+=1
                    candidate=identifier+'-'+key(row)[:12].upper()+'-'+str(suffix)
            row['RecommendationId']=candidate
            used.add(candidate)
        if len(group)>1:
            diagnostics.append({'code':'recommendation_id_collision','severity':'compatibility_warning',
                'legacy_id':identifier,'allocated_ids':sorted(row['RecommendationId'] for row in group),
                'finding_ids':sorted(finding_key(row) for row in group),
                'reason':'One legacy display ID addresses several retained findings; separate navigation anchors preserve every finding.'})
    return diagnostics


def attach_reconciliation(result, bundle):
    """Attach the shared semantic/occurrence contract before identity validation."""
    from .observation_reconciliation import reconcile_evidence
    seed=(bundle.get('collection_context') or {}).get('identity') or {}
    inputs=deepcopy(result.get('evidence') or [])
    if seed.get('State')=='complete':
        for row in inputs:
            for field in ('AssessmentId','RunId','PrimaryEnvironmentId'):
                row.setdefault(field,seed.get(field))
    model=reconcile_evidence(inputs,evaluation_date=result.get('evaluation_date'),
        expected_tenant_id=result.get('tenant_id'))
    result['evidence']=model.pop('rows')
    findings=[]
    by_evidence=defaultdict(set)
    for occurrence in model['occurrences']:
        by_evidence[occurrence['source']['evidence_id']].add(occurrence['observation_id'])
    observation_dimensions={row['id']:row['dimensions'] for row in model['observations']}
    for row in result.get('recommendations') or []:
        membership=deepcopy(row.get('FindingMembership') or {'id':finding_key(row),
            'authority':'explicit_scoped_issue' if row.get('FindingKey') else 'legacy_unvalidated',
            'boundary':finding_boundary(row),'reason':'Retained customer-issue declaration; no inferred cross-scope equivalence.',
            'members':source_occurrences(row)})
        membership.setdefault('members',source_occurrences(row))
        membership['recommendation_id']=row['RecommendationId']
        candidates={value for evidence_id in [row.get('EvidenceId'),*(row.get('RelatedEvidenceIds') or [])]
                    for value in by_evidence.get(evidence_id,[])}
        scope_fields={'ControlId':'control_id','Provider':'provider','Population':'population',
            'PopulationDefinition':'population_definition','EvidenceScope':'scope',
            'Applicability':'applicability','RolloutStage':'rollout_stage'}
        membership['observation_ids']=sorted(value for value in candidates if not any(
            row.get(field) and observation_dimensions[value].get(destination)
            and row[field]!=observation_dimensions[value][destination] for field,destination in scope_fields.items()))
        membership['conflict']=row.get('EvidenceStatus')=='conflict'
        findings.append(membership)
        if membership.get('authority')=='legacy_unvalidated' or any(not row.get(field) for field in ('ControlId','Provider','Population','EvidenceScope')):
            model['diagnostics'].append({'code':'legacy_finding_grouping','severity':'compatibility_warning',
                'finding':membership['id'],'reason':'Scoped legacy declarations retain support but do not establish missing persistent comparison dimensions.'})
    model['findings']=sorted(findings,key=lambda row:(row['id'],row['recommendation_id']))
    model['supporting_observations']=[{'finding_id':row['id'],'observation_id':identifier,
        'role':'retained-support','reason':'Explicit evidence reference on the scoped customer issue.'}
        for row in model['findings'] for identifier in row['observation_ids']]
    model['diagnostics'].extend(result.pop('_finding_diagnostics',[]))
    model['diagnostics'].sort(key=encoded)
    result['reconciliation']=model
    return result

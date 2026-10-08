"""Cross-stage tests use valid persisted identity and lifecycle graphs."""
import copy
import unittest
from test_governance_decisions import fixture,draft,advance,NOW,LATER


def lifecycle_pair(bound_condition=False):
    from Core.assessment_identity import entity,typed_alias
    from Core.run_comparability import evaluate_comparability
    from Core.assessment_delta import evaluate_delta
    r,d,p=fixture('ClosedByRemediation')
    if bound_condition:
        remap={};meta=r['identity']
        for node in meta['Entities']:
            boundary=node['Boundary']
            if node['Type']=='finding':boundary['condition_key']='policy.exceptions'
            for field,value in list(boundary.items()):
                if isinstance(value,str) and value in remap:boundary[field]=remap[value]
            replacement=entity(node['Type'],meta,boundary);remap[node['Id']]=replacement['Id'];node.update(replacement)
        for ref in meta['References']:
            ref['OwnerId']=remap.get(ref['OwnerId'],ref['OwnerId']);ref['TargetId']=remap.get(ref['TargetId'],ref['TargetId'])
        for alias in meta['Aliases']:alias['TargetId']=remap.get(alias['TargetId'],alias['TargetId'])
        d['TargetEntityId']=remap.get(d['TargetEntityId'],d['TargetEntityId'])
    current=copy.deepcopy(r);baseline=copy.deepcopy(r)
    def populate(snapshot,run_id,value,status):
        meta=snapshot['identity'];meta['RunId']=run_id;meta['BaselineRunId']=None
        remap={}
        for node in meta['Entities']:
            node['RunId']=run_id;boundary=node['Boundary']
            if 'run_id' in boundary:boundary['run_id']=run_id
            replacement=entity(node['Type'],meta,boundary);remap[node['Id']]=replacement['Id'];node.update(replacement)
        for node in meta['Entities']:
            for key,value_ in list(node['Boundary'].items()):
                if isinstance(value_,str) and value_ in remap:node['Boundary'][key]=remap[value_]
        for row in meta['References']:
            row['RunId']=run_id;row['OwnerId']=remap.get(row['OwnerId'],row['OwnerId']);row['TargetId']=remap.get(row['TargetId'],row['TargetId'])
        for alias in meta['Aliases']:alias['OriginRunId']=run_id;alias['TargetId']=remap.get(alias['TargetId'],alias['TargetId'])
        pev=next(e['Id'] for e in meta['Entities'] if e['Type']=='evidence_record')
        meta['Aliases'].append(typed_alias('EV-'+run_id,'EV','evidence_record',meta,pev,origin_artifact='shared-result'))
        snapshot['control_results']=[{'ControlId':'IDENTITY.MFA','Status':status}]
        from test_delta_metrics import fact
        f=fact(value,metric='identity.policy_exceptions',unit='count')
        f.update(control_id='IDENTITY.MFA',population='pilot users',scope='tenant',evidence_level='policy_enforcement',evidence_id='EV-'+run_id,
            numerator=None,denominator=None,condition_key='identity.mfa')
        snapshot['evidence']=[f]
        snapshot['collection_coverage']={'auth':{'state':'available','complete':True,'population':'pilot users','collection_semantics':'full enumeration'}}
        meta['MethodologyVersion']='4';meta['CatalogVersion']='catalog-1'
    populate(baseline,'RUN-'+('a'*32),4,'Fail');populate(current,'RUN-'+('b'*32),0,'Pass')
    for snapshot,kind,base in ((baseline,'Initial',None),(current,'Reassessment',baseline['identity']['RunId'])):
        meta=snapshot['identity'];meta['RunType']=kind;meta['BaselineRunId']=base
        ctx={'SchemaVersion':'1.0.0','AssessmentId':meta['AssessmentId'],'RunId':meta['RunId'],
            'PrimaryEnvironmentId':meta['PrimaryEnvironmentId'],'RunType':kind,'BaselineRunId':base,
            'CreatedAt':meta['CreatedAt'],'EvaluatedAt':meta['EvaluatedAt'],'MethodologyVersion':'4',
            'CatalogVersion':'catalog-1','IdentitySchemaVersion':'1.0.0','ReconciliationVersion':'2.0.0',
            'HistoryReference':'assessment-history.json',
            'DeltaEnabled':kind=='Reassessment','Comparability':evaluate_comparability(snapshot,baseline if base else None)}
        if base:ctx['BaselineValidation']={'Status':'validated','AssessmentId':meta['AssessmentId'],'RunId':base,
            'PrimaryEnvironmentId':meta['PrimaryEnvironmentId'],'SnapshotHash':'a'*64,'Sequence':1,'HistoryIntegrity':'b'*64}
        snapshot['run_context']=ctx;meta['RunContext']=copy.deepcopy(ctx)
        snapshot['lifecycle']=evaluate_delta(snapshot,baseline if base else None)
    d.update(ClosureOverride=False,EvidenceReferences=copy.deepcopy(d['EvidenceReferences']))
    d['EvidenceReferences'][0]['RunId']=current['identity']['RunId']
    return current,baseline,d,p


class SeparationTests(unittest.TestCase):
    def test_customer_action_status_remains_visible_with_recorded_lifecycle(self):
        from Core.governance import new_log,create_draft,attach
        from Core.assessment_identity import typed_alias
        current,_,d,p=lifecycle_pair(True);meta=current['identity']
        action=next(r['Id'] for r in meta['Entities'] if r['Type']=='action')
        meta['Aliases'].append(typed_alias('ENT-001','RecommendationId:action','action',meta,action,origin_artifact='shared-result'))
        current['actions']=[{'RecommendationId':'ENT-001','ActionStatus':'Monitoring'}]
        d.update(TargetEntityId=action,TargetEntityType='action',DecisionType='ApprovedException')
        log=create_draft(new_log(current),current,d,actor='operator',at=NOW)
        out=attach(current,log,as_of=NOW,locator='governance/decisions.json')
        row=out['governance']['Records'][0]
        self.assertIsNotNone(row['CurrentLifecycleState']);self.assertEqual(row['CustomerActionStatus'],'Monitoring')
        self.assertEqual(current['actions'][0]['ActionStatus'],'Monitoring');self.assertEqual(row['WorkflowState'],'Open')
    def test_unreviewed_future_evidence_and_embedded_payloads_are_blocked(self):
        from Core.governance import new_log,create_draft,transition
        variants=[('ObservedAt','2027-01-01T00:00:00Z'),('ObservedOutcome',{'raw':'fictional embedded payload'}),('Exceptions',{'count':0})]
        for field,value in variants:
            with self.subTest(field=field):
                r,d,p=fixture();d['EvidenceReferences'][0][field]=value
                log=create_draft(new_log(r),r,d,actor='operator',at=NOW);key=log['Events'][0]['DecisionId']
                with self.assertRaises(ValueError):transition(log,r,key,'submit',actor='operator',at=NOW)

    def test_context_does_not_copy_raw_identity_attributes(self):
        from Core.governance import new_log,create_draft
        r,d,p=fixture()
        for node in r['identity']['Entities']:node['UnrelatedRawDetail']={'body':'fictional raw record'}
        log=create_draft(new_log(r),r,d,actor='operator',at=NOW)
        self.assertNotIn('UnrelatedRawDetail',str(log['Events'][0]['Context']))

    def test_string_role_grants_are_not_accepted_as_permission_lists(self):
        from Core.governance import transition
        r,log,key,p=draft();log=advance(r,log,key,p,('submit',));p['Roles']['risk-authority']['Operations']='approve'
        with self.assertRaises(ValueError):transition(log,r,key,'approve',actor='approver',at=NOW,policy=p)

    def test_absent_target_has_no_current_effect_and_requires_review(self):
        from Core.governance import attach
        r,log,key,p=draft();log=advance(r,log,key,p)
        # A later empty valid graph does not inherit governance by display ID.
        from Core.assessment_identity import new_run
        later=copy.deepcopy(r);later['identity']=new_run(r['identity'],r['tenant_id'],evaluated_at=NOW)
        later['recommendations']=[]
        out=attach(later,log,as_of=NOW,locator='governance/decisions.json');row=out['governance']['Records'][0]
        self.assertTrue(row['EffectiveActive']);self.assertFalse(row['EffectiveForCurrentRun'])
        self.assertTrue(row['CurrentRequiresReview']);self.assertFalse(row['CurrentTargetPresent'])
        self.assertEqual(out['governance']['Summary']['CurrentActiveTreatments']['AcceptedRisk'],0)
        from Core.governance import effective_for
        self.assertEqual(effective_for(out['governance'],row['TargetEntityId'],row['Population'],row['ResourceScope']),[])

    def test_resource_subset_query_cannot_apply_exception_to_whole_scope(self):
        from Core.governance import new_log,create_draft,project,effective_for
        r,d,p=fixture('ApprovedException');d['ResourceIds']=['site-a']
        log=create_draft(new_log(r),r,d,actor='operator',at=NOW);key=log['Events'][0]['DecisionId']
        view=project(advance(r,log,key,p),as_of=NOW)
        args=(view,d['TargetEntityId'],d['Population'],d['ResourceScope'])
        self.assertEqual(effective_for(*args),[])
        self.assertEqual(effective_for(*args,resource_ids=['site-b']),[])
        self.assertEqual(len(effective_for(*args,resource_ids=['site-a'])),1)

    def test_reopening_later_run_requires_current_evidence_and_preserves_old_closure(self):
        from Core.governance import new_log,create_draft,transition,attach
        from Core.assessment_identity import entity
        from Core.run_comparability import evaluate_comparability
        from Core.assessment_delta import evaluate_delta
        from Core.assessment_references import require_valid_assessment
        current,_,d,p=lifecycle_pair(True)
        log=create_draft(new_log(current),current,d,actor='operator',at=NOW);key=log['Events'][0]['DecisionId'];log=advance(current,log,key,p)
        old=copy.deepcopy(log['Events']);third=copy.deepcopy(current);meta=third['identity'];run='RUN-'+('c'*32)
        remap={};meta.update(RunId=run,BaselineRunId=current['identity']['RunId'])
        for node in meta['Entities']:
            node['RunId']=run
            if 'run_id' in node['Boundary']:node['Boundary']['run_id']=run
            new=entity(node['Type'],meta,node['Boundary']);remap[node['Id']]=new['Id'];node.update(new)
        for ref in meta['References']:
            ref['RunId']=run;ref['OwnerId']=remap.get(ref['OwnerId'],ref['OwnerId']);ref['TargetId']=remap.get(ref['TargetId'],ref['TargetId'])
        for a in meta['Aliases']:
            a['OriginRunId']=run;a['TargetId']=remap.get(a['TargetId'],a['TargetId'])
            if a['Namespace']=='RecommendationId':a['Value']='RENAMED-99'
        third['recommendations'][0]['RecommendationId']='RENAMED-99'
        third['evidence'][0]['value']=4;third['control_results'][0]['Status']='Fail'
        ctx=third['run_context'];ctx.update(RunId=run,BaselineRunId=current['identity']['RunId'])
        ctx['BaselineValidation']['RunId']=current['identity']['RunId'];ctx['Comparability']=evaluate_comparability(third,current)
        meta['RunContext']=copy.deepcopy(ctx);third['lifecycle']=evaluate_delta(third,current)
        require_valid_assessment(third)
        self.assertEqual(next(r for r in third['lifecycle']['Records'] if r['EntityType']=='finding')['State'],'Reopened')
        evidence=copy.deepcopy(d['EvidenceReferences']);evidence[0]['RunId']=run;evidence[0]['ObservedOutcome']='Condition reappeared'
        with self.assertRaises(ValueError):transition(log,third,key,'reopen',actor='approver',at=NOW,policy=p,data={'Reason':'Comparable reappearance','ResultingState':'Open'})
        updated=transition(log,third,key,'reopen',actor='approver',at=NOW,policy=p,current_evidence=evidence,
            data={'Reason':'Comparable reappearance','ResultingState':'Open'})
        out=attach(third,updated,as_of=NOW,locator='governance/decisions.json')
        self.assertEqual(updated['Events'][:-1],old)
        row=out['governance']['Records'][0];self.assertEqual(row['ReopeningRunId'],run);self.assertFalse(row['EffectiveActive'])
        self.assertEqual(row['ReopeningEvidenceReferences'][0]['RunId'],run)

    def test_screenshot_requires_policy_and_methodology_rule(self):
        from Core.governance import new_log,create_draft,transition
        r,d,p=fixture();d['EvidenceReferences'][0]['SourceType']='screenshot'
        log=create_draft(new_log(r),r,d,actor='operator',at=NOW);key=log['Events'][0]['DecisionId'];log=advance(r,log,key,p,('submit',))
        with self.assertRaises(ValueError):transition(log,r,key,'approve',actor='approver',at=NOW,policy=p)

    def test_control_resolution_cannot_close_an_unbound_finding(self):
        from Core.governance import new_log,create_draft,attach
        from Core.assessment_references import require_valid_assessment
        current,baseline,d,p=lifecycle_pair();require_valid_assessment(current)
        control=next(r for r in current['lifecycle']['Records'] if r['EntityType']=='control')
        self.assertEqual(control['State'],'ResolvedByCurrentEvidence')
        # Bind closure to the action and its finding only when the engine has
        # proven their specific condition, rather than borrowing control pass.
        finding=next(r for r in current['lifecycle']['Records'] if r['EntityType']=='finding')
        self.assertNotEqual(finding['State'],'ResolvedByCurrentEvidence')
        log=create_draft(new_log(current),current,d,actor='operator',at=NOW)
        from Core.governance import transition
        with self.assertRaises(ValueError):transition(log,current,log['Events'][0]['DecisionId'],'submit',actor='operator',at=NOW)

    def test_positive_finding_resolution_supports_explicit_closure_without_override(self):
        from Core.governance import new_log,create_draft,attach
        from Core.assessment_references import require_valid_assessment
        current,baseline,d,p=lifecycle_pair(True);require_valid_assessment(current)
        finding=next(r for r in current['lifecycle']['Records'] if r['EntityType']=='finding')
        self.assertEqual(finding['State'],'ResolvedByCurrentEvidence')
        self.assertEqual(new_log(current)['Events'],[])
        before=copy.deepcopy((current,baseline))
        log=create_draft(new_log(current),current,d,actor='operator',at=NOW);key=log['Events'][0]['DecisionId']
        out=attach(current,advance(current,log,key,p),as_of=NOW,locator='governance/decisions.json')
        self.assertEqual(out['governance']['Records'][0]['GovernanceState'],'ClosedByRemediation')
        self.assertEqual(out['lifecycle'],before[0]['lifecycle']);self.assertEqual((current,baseline),before)

    def test_missing_lifecycle_cannot_be_upgraded_by_governance_import(self):
        from Core.governance import attach
        r,log,key,p=draft('ClosedByRemediation');before=copy.deepcopy(r)
        output=attach(r,advance(r,log,key,p),as_of=NOW,locator='governance/decisions.json')
        self.assertNotIn('lifecycle',output);self.assertEqual(r,before)

    def test_conflicting_active_treatments_and_identity_scope_diagnostics(self):
        from Core.governance import create_draft,transition,validate_log
        r,log,key,p=draft('AcceptedRisk');log=advance(r,log,key,p)
        original=log['Events'][0]['Data'];d=dict(copy.deepcopy(original),DecisionType='NoLongerApplicable')
        log=create_draft(log,r,d,actor='operator',at=NOW);other=log['Events'][-1]['DecisionId'];log=advance(r,log,other,p,('submit','approve'))
        with self.assertRaisesRegex(ValueError,'conflicting'):transition(log,r,other,'activate',actor='approver',at=NOW,policy=p)
        self.assertEqual(validate_log(log),[])

    def test_hard_expiration_always_deactivates_regardless_of_review_policy(self):
        from Core.governance import create_draft,new_log,project
        r,d,p=fixture('ApprovedException');d['ExpirationPolicy']='review_only'
        log=create_draft(new_log(r),r,d,actor='operator',at=NOW);key=log['Events'][0]['DecisionId'];log=advance(r,log,key,p)
        self.assertFalse(project(log,as_of=LATER)['Records'][0]['EffectiveActive'])

    def test_future_effective_time_is_not_active(self):
        from Core.governance import create_draft,new_log,project
        r,d,p=fixture();d['EffectiveAt']='2026-10-09T12:00:00+00:00'
        log=create_draft(new_log(r),r,d,actor='operator',at=NOW);key=log['Events'][0]['DecisionId'];log=advance(r,log,key,p)
        self.assertFalse(project(log,as_of=NOW)['Records'][0]['EffectiveActive'])

    def test_projection_before_event_time_blocked(self):
        from Core.governance import project
        r,log,key,p=draft()
        with self.assertRaises(ValueError):project(log,as_of='2026-09-01T00:00:00+00:00')

    def test_a_resealed_direct_status_edit_still_fails(self):
        from Core.governance import validate_log
        from Core.assessment_history import seal
        from Core.assessment_identity import digest
        r,log,key,p=draft();event=log['Events'][0];event['Record']['WorkflowState']='Active'
        event['Hash']=digest({k:v for k,v in event.items() if k!='Hash'})
        self.assertEqual(validate_log(seal(log))[0]['code'],'governance_unaudited_edit')

    def test_summary_counts_reconcile_all_record_states(self):
        from Core.governance import project
        r,log,key,p=draft();view=project(advance(r,log,key,p),as_of=NOW)
        self.assertEqual(sum(view['Summary']['WorkflowStates'].values()),len(view['Records']))
        self.assertEqual(sum(view['Summary']['DecisionTypes'].values()),len(view['Records']))
        self.assertEqual(sum(view['Summary']['ActiveTreatments'].values()),sum(row['EffectiveActive'] for row in view['Records']))

    def test_legacy_counts_reconcile_with_retained_context(self):
        from Core.governance import attach
        from Core.governance_validation import validate_governance
        r,log,key,p=draft();r['recommendations'][0]['AcceptedRisk']='yes'
        out=attach(r,log,as_of=NOW,locator='governance/decisions.json')
        self.assertEqual(out['governance']['Summary']['UnresolvedLegacyDecisions'],len(out['governance']['LegacyReferences']))
        self.assertFalse(any(d['severity']=='error' for d in validate_governance(out)))

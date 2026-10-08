"""Explicit governance acceptance tests; all actors and evidence are fictional."""
import copy
import unittest
from test_persistent_identity import graph, STAMP

NOW = '2026-10-08T12:00:00+00:00'
LATER = '2026-11-09T12:00:00+00:00'


def fixture(kind='AcceptedRisk', target='finding'):
    result = graph()
    result['reconciliation'] = {'state':'legacy','reason':'Synthetic graph.'}
    ids = {r['Type']:r['Id'] for r in result['identity']['Entities']}
    meta = result['identity']
    evidence = {'EvidenceId':ids['evidence_record'], 'AssessmentId':meta['AssessmentId'],
        'RunId':meta['RunId'], 'SourceLocator':'inputs/fiction.json', 'ObservedAt':STAMP,
        'Population':'pilot users', 'ResourceScope':'tenant', 'Period':{'Start':STAMP,'End':STAMP},
        'ObservedOutcome':'Condition observed', 'Exceptions':[], 'AccountableOwner':'owner-role',
        'Qualification':'Explicit reviewed evidence', 'Interpretation':'Supports the recorded decision'}
    record = {'TargetEntityId':ids[target], 'TargetEntityType':target, 'DecisionType':kind,
        'AccountableOwner':'owner-role', 'Rationale':'Explicit synthetic approval rationale',
        'Population':'pilot users', 'ResourceScope':'tenant', 'EffectiveAt':NOW,
        'ReviewAt':'2026-11-08T12:00:00+00:00', 'ExpirationAt':'2026-11-08T12:00:00+00:00',
        'ExpirationPolicy':'expire_on_expiration', 'EvidenceReferences':[evidence],
        'SupportingArtifacts':[], 'Conditions':['Monitor and review'], 'ResidualRisk':'Residual condition',
        'CompensatingControls':[], 'CompensatingControlsRationale':'Not required by this explicit policy',
        'ValidationResult':'Accepted after explicit review', 'ClosureCriteriaEvaluated':True,
        'ClosureOverride':True, 'ApplicabilityRule':'Explicit business rule',
        'DecisionAuthority':'approver', 'AuthorityRole':'risk-authority'}
    policy = {'SchemaVersion':'1.0.0', 'PolicyId':'synthetic-policy', 'Version':'1',
        'AssessmentId':meta['AssessmentId'], 'PrimaryEnvironmentId':meta['PrimaryEnvironmentId'],
        'Actors':{'approver':['risk-authority']}, 'Roles':{'risk-authority':{
            'DecisionTypes':['AcceptedRisk','ApprovedException','ClosedByRemediation','NoLongerApplicable'],
            'Operations':['approve','activate','revoke','supersede','expire','reopen'],
            'AllowClosureOverride':True, 'AllowNoCompensatingControls':True}}}
    return result,record,policy


def draft(kind='AcceptedRisk', target='finding'):
    from Core.governance import new_log,create_draft
    result,record,policy=fixture(kind,target)
    log=create_draft(new_log(result),result,record,actor='operator',at=NOW)
    return result,log,log['Events'][0]['DecisionId'],policy


def advance(result,log,identifier,policy,operations=('submit','approve','activate'),at=NOW):
    from Core.governance import transition
    for op in operations:
        log=transition(log,result,identifier,op,actor='operator' if op=='submit' else 'approver',at=at,policy=policy)
    return log


class DecisionTests(unittest.TestCase):
    def test_identity_is_stable_and_log_input_immutable(self):
        from Core.governance import project
        r,log,key,p=draft();original=copy.deepcopy((r,log))
        updated=advance(r,log,key,p)
        self.assertTrue(key.startswith('GOV-'))
        self.assertEqual({e['DecisionId'] for e in updated['Events']},{key})
        self.assertEqual((r,log),original)
        self.assertEqual(project(updated,as_of=NOW)['Records'][0]['DecisionId'],key)

    def test_invalid_identity_matrix(self):
        from Core.governance import new_log,create_draft
        for field,value in [('TargetEntityId','ENT-001'),('TargetEntityId','PFI-missing'),
                            ('TargetEntityType','observation')]:
            with self.subTest(field=field,value=value):
                r,d,_=fixture();d[field]=value
                with self.assertRaises(ValueError):create_draft(new_log(r),r,d,actor='operator',at=NOW)

    def test_foreign_assessment_and_environment_are_blocked(self):
        from Core.governance import new_log,create_draft
        for field in ('AssessmentId','PrimaryEnvironmentId'):
            with self.subTest(field=field):
                r,d,_=fixture();log=new_log(r);r['identity'][field]+='foreign'
                with self.assertRaises(ValueError):create_draft(log,r,d,actor='operator',at=NOW)

    def test_only_permitted_target_types(self):
        from Core.governance import new_log,create_draft,transition
        allowed={'ClosedByRemediation':('finding','action'),'AcceptedRisk':('finding','control'),
            'ApprovedException':('control','finding','action'),'NoLongerApplicable':('control','finding','action')}
        for kind in allowed:
            for target in ('finding','action','control'):
                with self.subTest(kind=kind,target=target):
                    r,d,p=fixture(kind,target)
                    if target not in allowed[kind]:
                        with self.assertRaises(ValueError):create_draft(new_log(r),r,d,actor='operator',at=NOW)
                    else:
                        log=create_draft(new_log(r),r,d,actor='operator',at=NOW)
                        transition(log,r,log['Events'][0]['DecisionId'],'submit',actor='operator',at=NOW)

    def test_required_fields_matrix(self):
        from Core.governance import new_log,create_draft,transition
        common=('AccountableOwner','Rationale','Population','ResourceScope','EffectiveAt','EvidenceReferences','DecisionAuthority','AuthorityRole')
        additions={'ClosedByRemediation':('ValidationResult','ClosureCriteriaEvaluated'),
            'AcceptedRisk':('ResidualRisk','Conditions'),'ApprovedException':('Conditions',),
            'NoLongerApplicable':('ApplicabilityRule',)}
        for kind in additions:
            for field in common+additions[kind]:
                with self.subTest(kind=kind,field=field):
                    r,d,_=fixture(kind);d.pop(field)
                    try:
                        log=create_draft(new_log(r),r,d,actor='operator',at=NOW)
                    except ValueError:
                        continue
                    with self.assertRaises(ValueError):transition(log,r,log['Events'][0]['DecisionId'],'submit',actor='operator',at=NOW)

    def test_risk_exception_require_review_or_expiration(self):
        from Core.governance import new_log,create_draft,transition
        for kind in ('AcceptedRisk','ApprovedException'):
            with self.subTest(kind=kind):
                r,d,_=fixture(kind);d.pop('ReviewAt');d.pop('ExpirationAt')
                log=create_draft(new_log(r),r,d,actor='operator',at=NOW)
                with self.assertRaises(ValueError):transition(log,r,log['Events'][0]['DecisionId'],'submit',actor='operator',at=NOW)

    def test_evidence_metadata_and_reference_matrix(self):
        from Core.governance import new_log,create_draft,transition
        for field in ('EvidenceId','SourceLocator','ObservedAt','Population','ResourceScope','Period',
                      'ObservedOutcome','Exceptions','AccountableOwner','Qualification','Interpretation'):
            with self.subTest(field=field):
                r,d,_=fixture('ClosedByRemediation');d['EvidenceReferences'][0].pop(field)
                try:log=create_draft(new_log(r),r,d,actor='operator',at=NOW)
                except ValueError:continue
                with self.assertRaises(ValueError):transition(log,r,log['Events'][0]['DecisionId'],'submit',actor='operator',at=NOW)

    def test_unrelated_and_foreign_evidence_rejected(self):
        from Core.governance import new_log,create_draft
        for field,value in [('EvidenceId','PEV-missing'),('RunId','RUN-foreign'),('AssessmentId','AST-foreign'),('SourceLocator','../../escape.json')]:
            with self.subTest(field=field):
                r,d,_=fixture();d['EvidenceReferences'][0][field]=value
                with self.assertRaises(ValueError):create_draft(new_log(r),r,d,actor='operator',at=NOW)

    def test_date_matrix(self):
        from Core.governance import new_log,create_draft,transition
        for field,value in [('EffectiveAt','no date'),('EffectiveAt','2026-10-08'),('ExpirationAt',STAMP),('ReviewAt',STAMP)]:
            with self.subTest(field=field):
                r,d,_=fixture();d[field]=value
                try:log=create_draft(new_log(r),r,d,actor='operator',at=NOW)
                except ValueError:continue
                with self.assertRaises(ValueError):transition(log,r,log['Events'][0]['DecisionId'],'submit',actor='operator',at=NOW)

    def test_secret_and_raw_payload_fields_rejected(self):
        from Core.governance import new_log,create_draft
        for key in ('password','client_secret','raw_evidence','WorkflowState','DecisionId'):
            with self.subTest(key=key):
                r,d,_=fixture();d[key]='not-a-real-secret'
                with self.assertRaises(ValueError):create_draft(new_log(r),r,d,actor='operator',at=NOW)

    def test_scope_matches_target_and_exception_is_explicit_subset(self):
        from Core.governance import new_log,create_draft,effective_for
        r,d,p=fixture('ApprovedException');d['Population']='subset users';d['ResourceScope']='one site'
        with self.assertRaises(ValueError):create_draft(new_log(r),r,d,actor='operator',at=NOW)
        d['ParentPopulation']='pilot users';d['ParentResourceScope']='tenant'
        log=create_draft(new_log(r),r,d,actor='operator',at=NOW);key=log['Events'][0]['DecisionId']
        view=__import__('Core.governance',fromlist=['project']).project(advance(r,log,key,p),as_of=NOW)
        self.assertEqual(len(effective_for(view,d['TargetEntityId'],'subset users','one site')),1)
        self.assertEqual(effective_for(view,d['TargetEntityId'],'pilot users','tenant'),[])

    def test_closure_requires_lifecycle_or_explicit_override(self):
        from Core.governance import new_log,create_draft,transition
        r,d,_=fixture('ClosedByRemediation');d['ClosureOverride']=False
        log=create_draft(new_log(r),r,d,actor='operator',at=NOW)
        with self.assertRaises(ValueError):transition(log,r,log['Events'][0]['DecisionId'],'submit',actor='operator',at=NOW)

    def test_resolution_alone_and_missing_collection_create_no_decisions(self):
        from Core.governance import new_log,project
        for state in ('ResolvedByCurrentEvidence','NotReassessed','Indeterminate','Reopened'):
            for source in ('missing','unlicensed','not_requested','unavailable'):
                with self.subTest(state=state,source=source):
                    r,_,_=fixture();r['collection_coverage']={'source':{'state':source}}
                    # No lifecycle mutation is needed to create an empty governance log.
                    self.assertEqual(project(new_log(r),as_of=NOW)['Records'],[])

    def test_technical_and_action_states_never_modified(self):
        from Core.governance import attach
        for kind in ('AcceptedRisk','ApprovedException','ClosedByRemediation','NoLongerApplicable'):
            with self.subTest(kind=kind):
                r,log,key,p=draft(kind);before=copy.deepcopy(r)
                output=attach(r,advance(r,log,key,p),as_of=NOW,locator='governance/decisions.json')
                self.assertEqual(r,before)
                self.assertEqual({k:v for k,v in output.items() if not k.startswith('governance')},before)


class WorkflowTests(unittest.TestCase):
    def test_draft_pending_approved_and_active_are_distinct(self):
        from Core.governance import transition,project
        r,log,key,p=draft()
        for op,state,active in [(None,'Open',False),('submit','PendingReview',False),('approve','Approved',False),('activate','Active',True)]:
            if op:log=transition(log,r,key,op,actor='operator' if op=='submit' else 'approver',at=NOW,policy=p)
            row=project(log,as_of=NOW)['Records'][0]
            self.assertEqual(row['WorkflowState'],state);self.assertIs(row['EffectiveActive'],active)

    def test_illegal_transition_matrix(self):
        from Core.governance import transition
        for op in ('approve','activate','revoke','expire','reopen','supersede'):
            with self.subTest(op=op):
                r,log,key,p=draft()
                with self.assertRaises(ValueError):transition(log,r,key,op,actor='approver',at=NOW,policy=p)

    def test_rejection_retains_history(self):
        from Core.governance import transition,project
        r,log,key,p=draft();log=advance(r,log,key,p,('submit',))
        log=transition(log,r,key,'reject',actor='approver',at=NOW,policy=p,data={'Reason':'Not accepted'})
        self.assertEqual(len(log['Events']),3);self.assertEqual(project(log,as_of=NOW)['Records'][0]['WorkflowState'],'Rejected')

    def test_revocation_preserves_original_approval_and_deactivates(self):
        from Core.governance import transition,project
        r,log,key,p=draft();log=advance(r,log,key,p);before=copy.deepcopy(log['Events'])
        updated=transition(log,r,key,'revoke',actor='approver',at=NOW,policy=p,data={'Reason':'Explicit revocation'})
        self.assertEqual(updated['Events'][:-1],before)
        self.assertEqual(updated['Events'][-1]['DecisionType'],'DecisionRevoked')
        self.assertFalse(project(updated,as_of=NOW)['Records'][0]['EffectiveActive'])

    def test_expiration_requires_explicit_policy_and_event(self):
        from Core.governance import transition,project
        r,log,key,p=draft();log=advance(r,log,key,p)
        due=project(log,as_of=LATER)['Records'][0]
        self.assertFalse(due['EffectiveActive']);self.assertTrue(due['RequiresReview'])
        self.assertEqual(due['WorkflowState'],'Active');self.assertEqual(len(log['Events']),4)
        with self.assertRaises(ValueError):transition(log,r,key,'expire',actor='approver',at=NOW,policy=p,data={'Reason':'Too early'})
        expired=transition(log,r,key,'expire',actor='approver',at=LATER,policy=p,data={'Reason':'Recorded policy expiration'})
        self.assertEqual(expired['Events'][-1]['DecisionType'],'DecisionExpired')
        self.assertEqual(project(expired,as_of=LATER)['Records'][0]['WorkflowState'],'Expired')

    def test_review_date_does_not_automatically_expire_review_only_policy(self):
        from Core.governance import new_log,create_draft,project,transition
        r,d,p=fixture();d['ExpirationPolicy']='review_only';d.pop('ExpirationAt')
        log=create_draft(new_log(r),r,d,actor='operator',at=NOW);key=log['Events'][0]['DecisionId'];log=advance(r,log,key,p)
        self.assertTrue(project(log,as_of=LATER)['Records'][0]['RequiresReview'])
        with self.assertRaises(ValueError):transition(log,r,key,'expire',actor='approver',at=LATER,policy=p,data={'Reason':'Unsupported policy'})

    def test_reopen_preserves_closure_and_requires_reason_current_evidence(self):
        from Core.governance import transition,project
        r,log,key,p=draft('ClosedByRemediation');log=advance(r,log,key,p);before=copy.deepcopy(log['Events'])
        with self.assertRaises(ValueError):transition(log,r,key,'reopen',actor='approver',at=NOW,policy=p)
        updated=transition(log,r,key,'reopen',actor='approver',at=NOW,policy=p,data={'Reason':'Closure evidence invalid for current scope','ResultingState':'Open'})
        row=project(updated,as_of=NOW)['Records'][0]
        self.assertEqual(updated['Events'][:-1],before);self.assertEqual(row['WorkflowState'],'Reopened')
        self.assertFalse(row['EffectiveActive']);self.assertEqual(row['GovernanceState'],'Open')
        self.assertEqual(updated['Events'][-1]['DecisionType'],'ClosureReopened')

    def test_duplicate_active_decisions_block_and_supersession_is_atomic(self):
        from Core.governance import create_draft,transition,project
        r,log,key,p=draft();log=advance(r,log,key,p);_,d,_=fixture()
        d['TargetEntityId']=next(e['Id'] for e in r['identity']['Entities'] if e['Type']=='finding')
        d['EvidenceReferences']=copy.deepcopy(log['Events'][0]['Record']['EvidenceReferences'])
        log=create_draft(log,r,d,actor='operator',at=NOW);second=log['Events'][-1]['DecisionId']
        log=advance(r,log,second,p,('submit','approve'))
        with self.assertRaises(ValueError):transition(log,r,second,'activate',actor='approver',at=NOW,policy=p)
        log=transition(log,r,second,'supersede',actor='approver',at=NOW,policy=p,data={'SupersededDecisionId':key,'Reason':'Explicit replacement'})
        rows=project(log,as_of=NOW)['Records'];self.assertEqual(sum(e['EffectiveActive'] for e in rows),1)
        self.assertEqual(next(e for e in rows if e['DecisionId']==key)['WorkflowState'],'Superseded')

    def test_amendment_and_review_are_audited(self):
        from Core.governance import transition,project
        r,log,key,p=draft();log=transition(log,r,key,'amend',actor='operator',at=NOW,data={'Rationale':'Reviewed changed rationale'})
        self.assertEqual(log['Events'][0]['Record']['Rationale'],'Explicit synthetic approval rationale')
        log=advance(r,log,key,p)
        updated=transition(log,r,key,'review',actor='reviewer',at=NOW,data={'Reason':'Review without renewal'})
        self.assertEqual(project(updated,as_of=NOW)['Records'][0]['WorkflowState'],'Active')
        self.assertEqual(updated['Events'][-1]['Actor'],'reviewer')

    def test_direct_status_edit_and_chain_tampering_fail(self):
        from Core.governance import validate_log
        from Core.assessment_history import seal
        r,log,key,p=draft();log=advance(r,log,key,p)
        for mutate in ('status','chain','actor','evidence','policy'):
            with self.subTest(mutate=mutate):
                bad=copy.deepcopy(log)
                if mutate=='status':bad['Events'][0]['Record']['WorkflowState']='Active'
                elif mutate=='chain':bad['Events'][1]['PreviousHash']='broken'
                elif mutate=='actor':bad['Events'][2]['Actor']='operator'
                elif mutate=='evidence':bad['Events'][0]['Record']['EvidenceReferences'][0]['EvidenceId']='PEV-missing'
                else:bad['Events'][2]['Policy']['Actors']={}
                self.assertTrue(any(d['severity']=='error' for d in validate_log(seal(bad))))


class AuthorityTests(unittest.TestCase):
    def test_default_deny_and_actor_role_binding_matrix(self):
        from Core.governance import transition
        r,log,key,p=draft();log=advance(r,log,key,p,('submit',))
        variants=[None,{},dict(p,Actors={}),dict(p,Actors={'operator':['risk-authority']}),dict(p,Roles={}),dict(p,AssessmentId='AST-foreign')]
        for policy in variants:
            with self.subTest(policy=policy),self.assertRaises(ValueError):transition(log,r,key,'approve',actor='approver',at=NOW,policy=policy)

    def test_roles_have_distinct_permissions(self):
        from Core.governance import transition
        for field,value in [('DecisionTypes',['ApprovedException']),('Operations',['activate'])]:
            with self.subTest(field=field):
                r,log,key,p=draft();log=advance(r,log,key,p,('submit',));p['Roles']['risk-authority'][field]=value
                with self.assertRaises(ValueError):transition(log,r,key,'approve',actor='approver',at=NOW,policy=p)

    def test_closure_override_and_compensation_require_policy(self):
        from Core.governance import transition
        for kind,flag in [('ClosedByRemediation','AllowClosureOverride'),('AcceptedRisk','AllowNoCompensatingControls')]:
            with self.subTest(kind=kind):
                r,log,key,p=draft(kind);log=advance(r,log,key,p,('submit',));p['Roles']['risk-authority'][flag]=False
                with self.assertRaises(ValueError):transition(log,r,key,'approve',actor='approver',at=NOW,policy=p)

    def test_operator_cannot_invent_authority(self):
        from Core.governance import transition
        r,log,key,p=draft();log=advance(r,log,key,p,('submit',))
        with self.assertRaises(ValueError):transition(log,r,key,'approve',actor='operator',at=NOW,policy=p)

    def test_transition_records_actor_time_and_policy(self):
        r,log,key,p=draft();log=advance(r,log,key,p)
        event=log['Events'][2]
        self.assertEqual(event['Actor'],'approver');self.assertEqual(event['RecordedAt'],NOW)
        self.assertEqual(event['Policy'],p);self.assertTrue(event['Hash'])

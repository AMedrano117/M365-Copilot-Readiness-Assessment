"""Conservative transitions and typed matching, without collectors or files."""
import copy
import unittest
from test_delta_metrics import fact
from test_assessment_run_workflow import TENANT


def pair():
    from Core.assessment_identity import new_identity,new_run,entity
    from Core.run_comparability import evaluate_comparability
    meta = new_identity(TENANT,methodology_version='4.0.0',catalog_version='catalog-1',evaluated_at='2026-09-15')
    current_meta=new_run(meta,TENANT,evaluated_at='2026-09-16')
    def snapshot(seed,mode,baseline):
        seed=copy.deepcopy(seed); seed.update(RunType=mode,BaselineRunId=baseline)
        boundary=dict(assessment_id=seed['AssessmentId'],environment_id=seed['PrimaryEnvironmentId'],
            provider='microsoft',control_id='IDENTITY.MFA',condition_key='policy.exceptions',population='all users',resource_scope='tenant')
        finding=entity('finding',seed,boundary)
        control=entity('control',seed,dict(namespace='m365-readiness',control_id='IDENTITY.MFA'))
        seed['Entities']=[finding,control]
        row=fact(4,'identity.policy_exceptions','count'); row['evidence_level']='policy_enforcement'
        row['evidence_id']='EV-'+seed['RunId']
        context={key:seed.get(key) for key in ('AssessmentId','RunId','PrimaryEnvironmentId','RunType','BaselineRunId')}
        context.update(ReconciliationVersion='2.0.0',Purpose='readiness')
        return dict(identity=seed,run_context=context,evidence=[row],
            control_results=[dict(ControlId='IDENTITY.MFA',Status='Fail')],
            recommendations=[dict(PersistentFindingId=finding['Id'],RecommendationId='R-1',Status='Action Required',ClosureEvidence='Policy exceptions at zero')],
            collection_coverage={'policies':dict(state='available',complete=True,population='all users',collection_semantics='full enumeration')},
            run_boundaries=dict(purpose='readiness',scope='tenant',providers=['microsoft'],population_definitions=['all users'],resource_scopes=['tenant']))
    baseline=snapshot(meta,'Initial',None); current=snapshot(current_meta,'Reassessment',meta['RunId'])
    current['run_context']['Comparability']=evaluate_comparability(current,baseline)
    return baseline,current


def compare(b,c,enabled=True):
    from Core.run_comparability import evaluate_comparability
    from Core.assessment_delta import evaluate_delta
    c['run_context']['Comparability']=evaluate_comparability(c,b)
    return evaluate_delta(c,b,enabled=enabled)


def finding_record(delta):
    return next(row for row in delta['Records'] if row['EntityType']=='finding')


class LifecycleStateTests(unittest.TestCase):
    def test_no_comparison_and_explicit_disabled_matrix(self):
        from Core.assessment_delta import evaluate_delta
        for mode in ('Initial','Standalone'):
            b,c=pair(); c['run_context'].update(RunType=mode,BaselineRunId=None)
            self.assertEqual(evaluate_delta(c,None)['Outcome'],'NotEvaluated')
            self.assertEqual(evaluate_delta(c,None)['Records'],[])
        b,c=pair(); delta=compare(b,c,False)
        self.assertEqual(delta['Outcome'],'NotEvaluated'); self.assertEqual(delta['Records'],[])
        self.assertFalse(delta['Enabled'])

    def test_unchanged_ignores_wording_severity_order_and_locator(self):
        b,c=pair(); c['recommendations'][0].update(RecommendationId='R-99',Title='Renamed',Priority='Critical',WorkbookRow=900)
        c['identity']['Entities'].reverse()
        delta=compare(b,c)
        self.assertEqual(finding_record(delta)['State'],'Unchanged')
        self.assertTrue(finding_record(delta)['BaselineEvidenceReferences'])
        self.assertTrue(finding_record(delta)['CurrentEvidenceReferences'])

    def test_metric_transition_matrix_and_open_improvement(self):
        for value,state in [(2,'Improved'),(6,'Regressed'),(0,'ResolvedByCurrentEvidence')]:
            with self.subTest(value=value):
                b,c=pair(); c['evidence'][0]['value']=value
                row=finding_record(compare(b,c))
                self.assertEqual(row['State'],state)
                self.assertEqual(row['RemainsOpen'],state!='ResolvedByCurrentEvidence')
                if value==0:self.assertTrue(row['ResolutionEvidenceReferences'])
                self.assertEqual(c['control_results'][0]['Status'],'Fail')

    def test_missing_failed_and_partial_matrix(self):
        for availability in ['failed','unavailable','not_requested','insufficient_permission','unlicensed','unsupported','inaccessible','partial']:
            with self.subTest(state=availability):
                b,c=pair(); c['evidence'][0].update(availability=availability,complete=False,value=None)
                c['collection_coverage']['policies'].update(state=availability,complete=False)
                row=finding_record(compare(b,c))
                self.assertEqual(row['State'],'Indeterminate' if availability=='partial' else 'NotReassessed')
                self.assertTrue(row['RemainsOpen'])

    def test_disappearance_alone_never_resolves(self):
        b,c=pair(); c['identity']['Entities']=[e for e in c['identity']['Entities'] if e['Type']!='finding']
        c['recommendations']=[]
        self.assertEqual(finding_record(compare(b,c))['State'],'Indeterminate')
        c['evidence']=[]; c['collection_coverage']={}
        self.assertEqual(finding_record(compare(b,c))['State'],'NotReassessed')

    def test_disappeared_finding_requires_positive_resolution_proof(self):
        b,c=pair(); c['identity']['Entities']=[e for e in c['identity']['Entities'] if e['Type']!='finding']
        c['recommendations']=[]; c['evidence'][0]['value']=0
        row=finding_record(compare(b,c))
        self.assertEqual(row['State'],'ResolvedByCurrentEvidence')
        self.assertIsNone(row['CurrentEntityId']); self.assertTrue(row['ResolutionEvidenceReferences'])

    def test_conflict_scope_population_levels_and_unknown_direction_matrix(self):
        for field,value,state in [('selection','conflict','Indeterminate'),('scope','pilot','Indeterminate'),
            ('population','administrators','Indeterminate'),('evidence_level','inventory','Changed')]:
            with self.subTest(field=field):
                b,c=pair(); c['evidence'][0].update(value=0); c['evidence'][0][field]=value
                self.assertEqual(finding_record(compare(b,c))['State'],state)
        b,c=pair()
        for r in (b,c):r['evidence'][0]['metric_id']='unknown.metric'
        c['evidence'][0]['value']=2
        self.assertEqual(finding_record(compare(b,c))['State'],'Changed')

    def test_reopened_requires_prior_resolution_same_identity_and_support(self):
        b,c=pair(); b['evidence'][0]['value']=0
        id_=next(e['Id'] for e in b['identity']['Entities'] if e['Type']=='finding')
        b['lifecycle']={'Records':[dict(EntityType='finding',EntityId=id_,State='ResolvedByCurrentEvidence',ResolutionEvidenceReferences=[{'RunId':'RUN-prior','EvidenceId':'EV-proof'}])]}
        row=finding_record(compare(b,c)); self.assertEqual(row['State'],'Reopened')
        self.assertTrue(row['PriorResolutionEvidenceReferences'])
        b.pop('lifecycle'); self.assertEqual(finding_record(compare(b,c))['State'],'Regressed')

    def test_new_requires_positive_sufficient_baseline_coverage(self):
        b,c=pair(); b['identity']['Entities']=[e for e in b['identity']['Entities'] if e['Type']!='finding']; b['recommendations']=[]
        self.assertEqual(finding_record(compare(b,c))['State'],'Indeterminate')
        b['evidence'][0]['value']=0
        self.assertEqual(finding_record(compare(b,c))['State'],'New')
        for field,value in [('complete',False),('availability','unavailable'),('scope','pilot'),('population','administrators')]:
            with self.subTest(field=field):
                saved=copy.deepcopy(b); saved['evidence'][0][field]=value
                self.assertNotEqual(finding_record(compare(saved,c))['State'],'New')

    def test_not_comparable_and_baseline_immutability(self):
        b,c=pair(); saved=copy.deepcopy(b); c['identity']['PrimaryEnvironmentId']='ENV-other'
        delta=compare(b,c)
        self.assertTrue(all(r['State']=='NotComparable' for r in delta['Records']))
        self.assertEqual(b,saved)

    def test_control_state_transitions_require_supported_same_scope(self):
        for before,after,state in [('Fail','Pass','ResolvedByCurrentEvidence'),('Pass','Fail','Regressed'),
                ('Not assessed','Pass','Changed'),('Fail','Not assessed','NotReassessed')]:
            with self.subTest(before=before,after=after):
                b,c=pair(); b['control_results'][0]['Status']=before; c['control_results'][0]['Status']=after
                row=next(r for r in compare(b,c)['Records'] if r['EntityType']=='control')
                self.assertEqual(row['State'],state)


class LifecycleMatchingTests(unittest.TestCase):
    def test_different_pfi_does_not_match_display_id(self):
        from Core.assessment_identity import entity
        b,c=pair(); old=next(e for e in c['identity']['Entities'] if e['Type']=='finding')
        new=entity('finding',c['identity'],dict(old['Boundary'],condition_key='different'))
        c['identity']['Entities']=[new if e['Type']=='finding' else e for e in c['identity']['Entities']]
        rows=[r for r in compare(b,c)['Records'] if r['EntityType']=='finding']
        self.assertEqual(len(rows),2); self.assertTrue(all(not(r['CurrentEntityId'] and r['BaselineEntityId']) for r in rows))

    def test_unique_typed_alias_and_ambiguous_matrix(self):
        from Core.assessment_identity import entity
        b,c=pair(); old=next(e for e in c['identity']['Entities'] if e['Type']=='finding')
        new=entity('finding',c['identity'],dict(old['Boundary'],condition_key='explicit-migrated'))
        c['identity']['Entities']=[new if e['Type']=='finding' else e for e in c['identity']['Entities']]
        for snap,id_ in [(b,old['Id']),(c,new['Id'])]:
            snap['identity']['Aliases']=[dict(Value='stable-issue-key',Namespace='external:issue',TargetType='finding',
                AssessmentId=snap['identity']['AssessmentId'],OriginRunId=snap['identity']['RunId'],OriginArtifact='issue-register',TargetId=id_)]
        row=finding_record(compare(b,c)); self.assertEqual(row['MatchAuthority'],'unique_typed_alias')
        self.assertEqual(row['State'],'Unchanged')
        c['identity']['Aliases'].append(dict(c['identity']['Aliases'][0],TargetId=None))
        self.assertTrue(all(r['State']=='Indeterminate' for r in compare(b,c)['Records'] if r['EntityType']=='finding'))

    def test_alias_cannot_bridge_ownership_scope_or_population(self):
        for field,value in [('AssessmentId','AST-other'),('PrimaryEnvironmentId','ENV-other')]:
            with self.subTest(field=field):
                b,c=pair(); c['identity'][field]=value
                self.assertEqual(finding_record(compare(b,c))['State'],'NotComparable')

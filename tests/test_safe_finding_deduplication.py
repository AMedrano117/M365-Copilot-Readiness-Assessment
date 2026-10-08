"""Finding authority, preservation and display-collision regression fixtures."""
import copy
import unittest
from test_stage1_semantics import DAY, TENANT, observation, evidence


def rec(**changes):
    return observation(RecommendationId='ENT-001', Provider='microsoft', Population='tenant users',
                       EvidenceScope='tenant', FindingKey='identity.mfa', **changes)


class SafeFindingDeduplicationTests(unittest.TestCase):
    def test_new_configuration_pass_cannot_hide_separate_operational_failure(self):
        from Core.finding_reconciliation import group_findings
        base=rec(EvidenceStatus='supported',Historical='No',EvidenceComplete=True)
        rows=[dict(base,EvidenceLevel='configuration',ObservationDate=DAY,Disposition='Assurance',control_result='pass',EvidenceId='EV-config'),
              dict(base,EvidenceLevel='observed_operation',ObservationDate='2026-09-09',Disposition='Action',control_result='fail',EvidenceId='EV-operation')]
        selected,_,_=group_findings(rows)
        self.assertEqual(selected[0]['Disposition'],'Action')
        self.assertEqual(selected[0]['EvidenceId'],'EV-operation')
        self.assertEqual(set(selected[0]['RelatedEvidenceIds']),{'EV-config','EV-operation'})

    def test_explicit_measurements_native_ids_and_quality_cannot_be_discarded_early(self):
        from Core.evidence_layer import deduplicate_findings
        base=rec()
        for field in ('MeasuredValue','NativeRecordId','SourceCaptureId','EvidenceTruncated','SourceHash'):
            with self.subTest(field=field):
                rows=[dict(base,**{field:'first'}),dict(base,**{field:'second'})]
                self.assertEqual(len(deduplicate_findings(rows)),2)

    def test_material_customer_boundaries_remain_separate(self):
        from Core.evidence_layer import deduplicate_findings
        base=rec()
        dimensions=dict(ControlId='OTHER',Population='pilot',EvidenceScope='selected apps',Provider='okta',
            Applicability='pilot',RolloutStage='expansion',CustomerDecision='accept exception',
            ClosureEvidence='reviewed sign-ins',ControlDefinitionVersion='5',PrimaryEnvironmentId='other',
            AssessmentId='other',PopulationDefinition='licensed only',PersistentFindingId='PFI-other')
        for field,value in dimensions.items():
            with self.subTest(field=field):
                self.assertEqual(len(deduplicate_findings([base,dict(base,**{field:value})])),2)

    def test_early_dedup_preserves_all_declarations_and_native_records(self):
        from Core.evidence_layer import deduplicate_findings
        base=rec()
        rows=[dict(base,InvestigationEvidence={'kind':'records','source':{'capture_id':name},'records':[{'id':name}]}) for name in ('a','b')]
        before=copy.deepcopy(rows)
        merged=deduplicate_findings(rows)
        self.assertEqual(rows,before)
        declarations=[member['InvestigationEvidence'] for row in merged for member in row.get('SourceOccurrences') or [row]]
        self.assertEqual({r['records'][0]['id'] for r in declarations},{'a','b'})

    def test_legacy_wording_without_authority_cannot_merge(self):
        from Core.evidence_layer import deduplicate_findings
        base=rec(); base.pop('FindingKey')
        merged=deduplicate_findings([base,dict(base,Feature='different module',SourceFile='other')])
        self.assertEqual(len(merged),2)

    def test_same_scoped_issue_wording_does_not_create_new_finding(self):
        from Core.finding_reconciliation import group_findings
        base=rec(EvidenceStatus='supported',Historical='No',ObservationDate=DAY,EvidenceId='EV-a',Disposition='Action')
        other=dict(base,Observation='Different explanation',Recommendation='Different phrasing',Priority='Low',EvidenceId='EV-b')
        merged,archived,model=group_findings([base,other])
        self.assertEqual(len(merged),1)
        self.assertEqual(set(merged[0]['RelatedEvidenceIds']),{'EV-a','EV-b'})
        self.assertNotEqual(merged[0]['EvidenceStatus'],'conflict')
        self.assertEqual(len(model['findings'][0]['members']),2)

    def test_recommendation_collision_cannot_overwrite_three_populations(self):
        from Core.assessment_result import build_assessment_result
        base=rec()
        rows=[dict(base,Population=name) for name in ('all','pilot','admins')]
        result=build_assessment_result(rows,evidence(),evaluation_date=DAY,expected_tenant_id=TENANT)
        retained=[r for r in result['recommendations'] if r.get('FindingKey')=='identity.mfa']
        self.assertEqual({r['Population'] for r in retained},{'all','pilot','admins'})
        self.assertEqual(len({r['RecommendationId'] for r in retained}),3)
        self.assertTrue(any(r['code']=='recommendation_id_collision' for r in result['reconciliation']['diagnostics']))
        self.assertTrue(all('ENT-001' in r['CompatibilityRecommendationIds'] for r in retained))

    def test_pfi_collision_for_closure_requirements_is_reproduced_and_corrected(self):
        from Core.assessment_identity import attach_identity,new_identity
        rows=[rec(ClosureEvidence=closure) for closure in ('configuration','observed operation')]
        rows[1]['RecommendationId']='ENT-002'
        result={'tenant_id':TENANT,'controls':[{'control_id':'IDENTITY.MFA'}],'recommendations':rows}
        attach_identity(result,{'collection_context':{'identity':new_identity(TENANT,methodology_version='4',evaluated_at=DAY)}})
        nodes=[r for r in result['identity']['Entities'] if r['Type']=='finding']
        self.assertEqual(len({r['Id'] for r in nodes}),2)

    def test_grouping_permutation_and_multiple_support_sources(self):
        from Core.finding_reconciliation import group_findings
        base=rec(EvidenceStatus='supported',Historical='No',ObservationDate=DAY,EvidenceId='EV-a')
        rows=[base,dict(base,EvidenceId='EV-b',SourceFile='b')]
        self.assertEqual(group_findings(rows),group_findings(rows[::-1]))

    def test_one_record_supporting_two_findings_is_not_two_unique_entities(self):
        from Core.evidence_selection import build_evidence_selection
        raw={'id':'one-user','registered':False}
        rows=[rec(InvestigationEvidence={'kind':'records','records':[raw]}),
              dict(rec(),RecommendationId='ENT-002',FindingKey='another-condition',InvestigationEvidence={'kind':'records','records':[raw]})]
        model=build_evidence_selection({'tenant_id':TENANT,'recommendations':rows},
            {'assessment_sources':{'auth_methods':[{'source':{'provider':'microsoft','workload':'entra','scope':'tenant','population':'users','complete':True},'records':[raw]}]}})
        self.assertEqual(len(model['evidence_records']),1)
        self.assertEqual(model['evidence_counts']['support_relationships'],2)
        self.assertEqual(model['evidence_counts']['unique_native_records'],1)
        self.assertEqual(model['evidence_counts']['unique_evidence_records'],1)

    def test_merged_declarations_are_selected_by_both_outputs(self):
        from Core.evidence_layer import deduplicate_findings
        from Core.evidence_selection import build_evidence_selection
        base=rec()
        rows=[dict(base,InvestigationEvidence={'kind':'records','records':[{'id':name}]}) for name in ('a','b')]
        merged=deduplicate_findings(rows)
        model=build_evidence_selection({'tenant_id':TENANT,'recommendations':merged},{})
        self.assertEqual({r['source_record_id'] for r in model['findings'][0]['records']},{'a','b'})

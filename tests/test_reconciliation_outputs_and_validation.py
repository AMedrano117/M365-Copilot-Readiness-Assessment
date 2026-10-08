"""Publication, replay and renderer contracts for the reconciled assessment."""
import copy
import os
from pathlib import Path
import tempfile
import unittest

from test_stage1_semantics import DAY,TENANT,fact,observation,evidence


def result(rows=None, observations=None, seed=None):
    from Core.assessment_result import build_assessment_result
    bundle=evidence(observations=observations or [])
    if seed:
        bundle['collection_context']['identity']=seed
    built=build_assessment_result(rows or [],bundle,evaluation_date=DAY,expected_tenant_id=TENANT)
    bundle['assessment_result']=built
    return built,bundle


class ReconciledPublicationTests(unittest.TestCase):
    def test_renderer_publication_is_blocked_before_any_output(self):
        from Core.export_recommendations import export_to_excel,export_to_html
        built,bundle=result(observations=[fact(control_result='pass'),fact(control_result='fail')])
        built['reconciliation']['conflicts'][0]['result']='Observed'
        with tempfile.TemporaryDirectory() as directory:
            for export,extension in ((export_to_excel,'xlsx'),(export_to_html,'html')):
                with self.subTest(extension=extension):
                    path=Path(directory)/('blocked.'+extension)
                    with self.assertRaises(ValueError):export(built['recommendations'],filename=str(path),evidence_bundle=bundle,output_dir=directory)
                    self.assertFalse(path.exists())

    def test_cross_assessment_environment_and_dangling_support_are_blocked(self):
        from Core.assessment_identity import new_identity
        from Core.assessment_references import require_valid_assessment
        seed=new_identity(TENANT,methodology_version='4',evaluated_at=DAY)
        row=observation(Population='business sites',EvidenceScope='all business sites',Provider='microsoft')
        built,_=result([row],seed=seed)
        for field,value in (('AssessmentId','AST-other'),('PrimaryEnvironmentId','ENV-other')):
            with self.subTest(field=field):
                bad=copy.deepcopy(built)
                bad['reconciliation']['findings'][0]['members'][0][field]=value
                with self.assertRaises(ValueError):require_valid_assessment(bad)
        bad=copy.deepcopy(built)
        bad['reconciliation']['supporting_observations'].append({'finding_id':'absent','observation_id':'absent','reason':'fixture'})
        with self.assertRaises(ValueError):require_valid_assessment(bad)

    def test_incompatible_populations_units_and_periods_cannot_form_aggregate(self):
        from Core.assessment_references import require_valid_assessment
        for change in ({'population':'pilot'},{'unit':'files'},{'window':'30 days'},{'scope':'selected sites'}):
            with self.subTest(change=change):
                built,_=result(observations=[fact(),fact(**change)])
                identifiers=[row['id'] for row in built['reconciliation']['observations'] if row['dimensions']['metric_id']=='permission_review']
                built['reconciliation']['aggregates']=[{'observation_ids':identifiers,'rule':'sum','reason':'fictional request'}]
                with self.assertRaisesRegex(ValueError,'incompatible_aggregate'):require_valid_assessment(built)

    def test_invalid_snapshot_is_blocked_before_artifact_creation(self):
        from Core.assessment_serialization import write_assessment_result
        built,_=result(observations=[fact(control_result='pass'),fact(control_result='fail')])
        built['reconciliation']['conflicts'][0]['result']='Observed'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'new-folder'/'invalid.json'
            with self.assertRaises(ValueError):write_assessment_result(path,built)
            self.assertFalse(path.parent.exists())

    def test_snapshot_old_path_is_qualified_and_byte_stable_after_load(self):
        from Core.assessment_serialization import write_assessment_result,read_assessment_result
        with tempfile.TemporaryDirectory() as directory:
            first=Path(directory)/'legacy.json';second=Path(directory)/'again.json'
            write_assessment_result(first,{'recommendations':[]})
            loaded=read_assessment_result(first)
            self.assertEqual(loaded['reconciliation']['state'],'legacy')
            # The identity legacy adapter predates this stage; preserve it too.
            write_assessment_result(second,loaded)
            third=Path(directory)/'third.json'
            write_assessment_result(third,read_assessment_result(second))
            self.assertEqual(second.read_bytes(),third.read_bytes())

    def test_invalid_duplicate_membership_cannot_claim_exact_equivalence(self):
        from Core.assessment_references import require_valid_assessment
        built,_=result(observations=[fact(),fact(source_file='other')])
        bad=copy.deepcopy(built)
        duplicate=bad['reconciliation']['exact_duplicate_groups'][0]
        occurrence=next(row for row in bad['reconciliation']['occurrences'] if row['id']==duplicate['occurrence_ids'][0])
        occurrence['source']['population']='pilot'
        with self.assertRaisesRegex(ValueError,'incompatible_exact_group'):require_valid_assessment(bad)

    def test_legacy_locator_cannot_mix_populations_in_finding_support(self):
        rows=[observation(RecommendationId='ENT-001',Provider='microsoft',Population=population)
              for population in ('pilot','admins','all users')]
        built,_=result(rows)
        observations={row['id']:row for row in built['reconciliation']['observations']}
        for finding in built['reconciliation']['findings']:
            if finding['recommendation_id'].startswith('ENT-001'):
                self.assertTrue(finding['observation_ids'])
                for identifier in finding['observation_ids']:
                    self.assertEqual(observations[identifier]['dimensions']['population'],finding['boundary']['Population'])

    def test_explanation_changes_are_not_measurement_conflicts(self):
        rows=[observation(Provider='microsoft',Population='users',control_result='fail'),
              observation(Provider='microsoft',Population='users',control_result='fail',Observation='Another explanation of the measured failure.')]
        built,_=result(rows)
        self.assertFalse(built['reconciliation']['conflicts'])
        retained=[row for row in built['recommendations'] if row.get('FindingKey')=='identity.mfa']
        self.assertEqual(len(retained),1)
        self.assertEqual(len(retained[0]['SourceOccurrences']),2)


class ReconciledReplayTests(unittest.TestCase):
    def test_complete_absence_declarations_preserve_their_state(self):
        from Core.evidence_layer import deduplicate_findings
        from Core.evidence_selection import build_evidence_selection
        row=observation(InvestigationEvidence={'kind':'absence','reason':'Complete known population returned zero records.'})
        merged=deduplicate_findings([row,dict(row,Feature='Other license')])
        selected=build_evidence_selection({'recommendations':merged},{})
        self.assertEqual(selected['findings'][0]['record_status'],'absence')
        self.assertEqual(selected['findings'][0]['record_reconciliation']['source_declaration_states'],['absence'])

    def test_native_record_counts_do_not_merge_provider_boundaries(self):
        from Core.evidence_selection import build_evidence_selection
        raw={'id':'same-spelling','flag':False}
        row=observation(InvestigationEvidence={'kind':'records','records':[raw]})
        sources={'auth_methods':[{'source':{'provider':provider,'workload':'registration','complete':True},'records':[raw]} for provider in ('entra','okta')]}
        selected=build_evidence_selection({'tenant_id':TENANT,'recommendations':[row]}, {'assessment_sources':sources})
        self.assertEqual(selected['evidence_counts']['unique_native_records'],2)
        self.assertIsNone(selected['evidence_counts']['unique_entity_count'])

    def test_different_evidence_levels_support_one_issue_without_collapsing(self):
        rows=[observation(Provider='microsoft',Population='users',EvidenceLevel=level,
                          ObservationDate=DAY,control_result='fail')
              for level in ('configuration','observed_operation')]
        built,_=result(rows)
        findings=[row for row in built['reconciliation']['findings'] if row['boundary'].get('ControlId')=='IDENTITY.MFA'
                  and row['boundary'].get('Population')=='users']
        self.assertEqual(len(findings),1)
        self.assertEqual(len(findings[0]['observation_ids']),2)

    def test_live_offline_equivalence_retains_conflicts_and_occurrences(self):
        from Core.offline_collection import _encode,_decode
        from Core.assessment_result import build_assessment_result
        rows=[fact(control_result='pass'),fact(control_result='fail',source_file='other')]
        built,bundle=result(observations=rows)
        replay=_decode(_encode({key:value for key,value in bundle.items() if key!='assessment_result'}))
        replay['collection_context']['mode']='offline'
        offline=build_assessment_result([],replay,evaluation_date=DAY,expected_tenant_id=TENANT)
        self.assertEqual(built['reconciliation'],offline['reconciliation'])

    def test_repeated_selection_preserves_shared_model_and_truncation(self):
        from Core.evidence_selection import build_evidence_selection
        raw={'id':'fictional-user','flag':False}
        rows=[observation(InvestigationEvidence={'kind':'records','records':[raw]})]
        built,bundle=result(rows)
        bundle['assessment_sources']={'auth_methods':[{'source':{'complete':False,'truncated':True,
            'availability_status':'partial','provider':'microsoft','workload':'entra'},'records':[raw]}]}
        before=copy.deepcopy(built['reconciliation'])
        first=build_evidence_selection(built,bundle,generated_at=DAY)
        second=build_evidence_selection(built,bundle,generated_at=DAY)
        self.assertEqual(first,second)
        self.assertEqual(before,built['reconciliation'])
        self.assertTrue(first['findings'][0]['record_limitations'])
        self.assertEqual(first['findings'][0]['record_count'],1)


class ReconciledRendererTests(unittest.TestCase):
    def fixture(self):
        rows=[observation(RecommendationId='ENT-001',Population=population,Provider='microsoft',
            InvestigationEvidence={'kind':'records','records':[{'id':'fictional-shared-user','flag':False}]})
            for population in ('pilot','admins','all users')]
        built,bundle=result(rows,observations=[fact(control_result='pass'),fact(control_result='fail',source_file='other')])
        bundle['assessment_sources']={'auth_methods':[{'source':{'provider':'microsoft','workload':'entra',
            'complete':True,'availability_status':'available','scope':'tenant','population':'users'},
            'records':[{'id':'fictional-shared-user','flag':False}]}]}
        return built,bundle

    def test_html_summary_and_evidence_pages_preserve_conflict_and_collisions(self):
        from Core.export_recommendations import export_to_html
        from Core.evidence_selection import build_evidence_selection
        from Core.finding_evidence import build_finding_evidence
        from Core.html_evidence_pages import write_html_evidence_pages,page_name
        built,bundle=self.fixture()
        selection=build_evidence_selection(built,bundle,generated_at=DAY)
        model=build_finding_evidence(selection)
        bundle['finding_evidence']=model
        before=copy.deepcopy(built['reconciliation'])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'assessment.html'
            export_to_html(built['recommendations'],filename=str(path),evidence_bundle=bundle)
            pages=write_html_evidence_pages(model,Path(directory)/'evidence',report_name=path.name)
            self.assertTrue(Path(bundle['summary_html_path']).is_file())
            for row in built['recommendations']:
                if row.get('FindingKey')=='identity.mfa':
                    self.assertIn(row['RecommendationId'],path.read_text(encoding='utf-8'))
                    self.assertTrue((Path(directory)/'evidence'/page_name(row['RecommendationId'])).is_file())
        self.assertEqual(before,built['reconciliation'])
        self.assertEqual(next(row['status'] for row in built['controls'] if row['control_id']=='CONTENT.PERMISSIONS'),'Not established')

    def test_assessment_and_technical_workbooks_keep_every_collision_link(self):
        from Core.export_recommendations import export_to_excel
        from tests.workbook_test_helpers import load_workbook_pair
        built,bundle=self.fixture()
        before=copy.deepcopy(built['reconciliation'])
        with tempfile.TemporaryDirectory() as directory:
            original=os.getcwd();os.chdir(directory)
            try:
                path=Path(export_to_excel(built['recommendations'],filename='assessment.xlsx',evidence_bundle=bundle)).resolve()
                book=load_workbook_pair(path)
                try:
                    register=book.technical['Findings Lineage']
                    headers={cell.value:cell.column for cell in register[1]}
                    ids={register.cell(number,headers['RecommendationId']).value for number in range(2,register.max_row+1)}
                    for row in built['recommendations']:
                        if row.get('FindingKey')=='identity.mfa':self.assertIn(row['RecommendationId'],ids)
                finally:book.close()
            finally:os.chdir(original)
        self.assertEqual(before,built['reconciliation'])

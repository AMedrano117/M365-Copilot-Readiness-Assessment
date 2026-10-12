"""Original source attribution survives fictional packages and derivative renders."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch
from Core.repository_provenance import producing_context,provenance_html
from provenance_fixture import fictional_revision
from test_assessment_run_workflow import RunFixture,TENANT,result


class ProvenancePackageTests(RunFixture):
    def completed(self):
        from Core.assessment_runs import prepare_run,complete_run
        from Core.assessment_result import build_assessment_result
        with producing_context(fictional_revision()):
            execution=prepare_run('Initial',TENANT,evaluated_at='2026-10-12',history_path=self.history)
            r=build_assessment_result([],{'collection_context':{'identity':execution.identity}},evaluation_date='2026-10-12',expected_tenant_id=TENANT)
            path=complete_run(r,execution,self.root/'package')
        return r,Path(path)
    def test_package_records_revision(self):
        r,path=self.completed();manifest=json.loads((path.parents[1]/'assessment-run.json').read_text());self.assertEqual(manifest['ProducingRevision'],r['producing_revision'])
    def test_snapshot_preserves_revision(self):
        r,path=self.completed();self.assertEqual(json.loads(path.read_text())['producing_revision']['commit_sha'],'a'*40)
    def test_snapshot_integrity_covers_provenance(self):
        from Core.assessment_history import select_baseline
        r,path=self.completed();stored=json.loads(path.read_text());stored['producing_revision']['commit_sha']='c'*40;path.write_text(json.dumps(stored))
        with self.assertRaisesRegex(ValueError,'integrity'):select_baseline(self.history,assessment_id=r['identity']['AssessmentId'],environment_id=r['identity']['PrimaryEnvironmentId'],baseline_run_id=r['identity']['RunId'])
    def test_old_snapshot_qualified(self):
        from Core.assessment_serialization import read_assessment_result
        r,path=self.completed();stored=json.loads(path.read_text());stored.pop('producing_revision');legacy=self.root/'legacy.json';legacy.write_text(json.dumps(stored))
        loaded=read_assessment_result(legacy);self.assertEqual(loaded['producing_revision']['source_state'],'SourceStateUnavailable')
    def test_replay_original_and_tool_separate(self):
        from Core.assessment_replay import render_snapshot
        r,path=self.completed();before=path.read_bytes();history=self.history.read_bytes();tool=dict(fictional_revision(),commit_sha='c'*40)
        with producing_context(tool),patch('Core.assessment_result.build_assessment_result',side_effect=AssertionError('reevaluation')):
            outputs=render_snapshot(path,self.root/'renders',report_format='html')
        metadata=json.loads((Path(outputs['report_directory'])/'render-provenance.json').read_text())
        self.assertEqual(metadata['original_producing_revision']['commit_sha'],'a'*40);self.assertEqual(metadata['replay_tool_revision']['commit_sha'],'c'*40)
        self.assertFalse(metadata['tenant_recollected']);self.assertEqual(path.read_bytes(),before);self.assertEqual(self.history.read_bytes(),history)
    def test_qualified_output_banner(self):
        r={'producing_revision':dict(fictional_revision(),publication={'output_qualification':'ENGINEERING VALIDATION ONLY — NOT CUSTOMER-READY'})}
        self.assertIn('NOT CUSTOMER-READY',provenance_html(r))
    def test_raw_collection_retains_revision(self):
        from Core.offline_collection import save_collection,empty_service_results
        path=self.root/'raw.json'
        with producing_context(fictional_revision()):save_collection(path,tenant_id=TENANT,tenant_name='Fictional',service_results=empty_service_results())
        self.assertEqual(json.loads(path.read_text())['producing_revision']['commit_sha'],'a'*40)
    def test_commit_only_does_not_change_technical_delta(self):
        from test_delta_lifecycle import pair
        from Core.assessment_delta import evaluate_delta
        before,after=pair();before['producing_revision']=fictional_revision();after['producing_revision']=dict(fictional_revision(),commit_sha='c'*40)
        first=evaluate_delta(after,before);after['producing_revision']['commit_sha']='a'*40;second=evaluate_delta(after,before)
        self.assertEqual([r['State'] for r in first['Records']],[r['State'] for r in second['Records']])
    def test_adapter_version_qualifies_comparison(self):
        from test_delta_lifecycle import pair
        from Core.run_comparability import evaluate_comparability
        before,after=pair();before['producing_revision']=fictional_revision();after['producing_revision']=deepcopy(before['producing_revision']);after['producing_revision']['import_adapters']['portal_review']['version']='99.0'
        comparison=evaluate_comparability(after,before);self.assertIn('source_semantics_changed',[r['Code'] for r in comparison['Reasons']])

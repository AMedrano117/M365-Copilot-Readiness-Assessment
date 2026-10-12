"""Preflight performs metadata reads only, with explicit baseline selection."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from datetime import datetime,timezone
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from Core.run_plan import build_run_plan,plan_json,plan_lines
from provenance_fixture import fictional_revision
from test_assessment_run_workflow import RunFixture,TENANT


def args(**overrides):
    values=dict(run_type='Initial',tenant_id=TENANT,source_policy='production',assessment_id=None,
        baseline_run_id=None,assessment_history=None,assessment_purpose=None,primary_environment_id=None,
        source_reason=None,acknowledge_dirty_source=False,delta_mode=None,replay_snapshot=None,
        offline=True,services=[],permission_profile=None,preview_collectors='auto')
    values.update(overrides);return SimpleNamespace(**values)


def plan(timestamp='2026-10-12T17:00:00Z',**overrides):
    return build_run_plan(args(**overrides),revision=fictional_revision(),generated_at=timestamp)


class PlanTests(unittest.TestCase):
    def test_initial_no_baseline(self):self.assertEqual(plan()['baseline']['completion'],'not_selected')
    def test_initial_no_delta(self):self.assertFalse(plan()['delta']['expected'])
    def test_initial_baseline_rejected(self):self.assertIn('initial_cannot_select_baseline',plan(baseline_run_id='RUN-fixture')['validation_errors'])
    def test_reassessment_assessment_required(self):self.assertIn('reassessment_assessment_id_required',plan(run_type='Reassessment')['validation_errors'])
    def test_reassessment_baseline_required(self):self.assertIn('run_plan_baseline_missing',plan(run_type='Reassessment',assessment_id='AST-fixture')['validation_errors'])
    def test_reassessment_history_required(self):self.assertIn('reassessment_history_required',plan(run_type='Reassessment',assessment_id='AST-fixture',baseline_run_id='RUN-fixture')['validation_errors'])
    def test_standalone_no_implicit_baseline(self):self.assertIsNone(plan(run_type='Standalone')['baseline']['run_id'])
    def test_standalone_owned_continuation(self):self.assertIn('continuation_history_required',plan(run_type='Standalone',assessment_id='AST-fixture')['validation_errors'])
    def test_replay_no_collection(self):self.assertFalse(plan(run_type=None,replay_snapshot='fictional.json')['sources']['tenant_collection'])
    def test_replay_preserves_ids(self):self.assertEqual(plan(run_type=None,replay_snapshot='fictional.json')['run_id_behavior'],'preserve stored RunId')
    def test_plan_does_not_write_history(self):self.assertFalse(plan()['history_mutated'])
    def test_plan_does_not_complete_run(self):self.assertFalse(plan()['completed_run_created'])
    def test_no_uuid_allocation(self):
        with patch('Core.assessment_identity.new_identity',side_effect=AssertionError('no minting')):self.assertTrue(plan()['execution_eligible'])
    def test_no_tenant_collectors(self):
        with patch('Core.orchestrator.orchestrate',side_effect=AssertionError('no collection')):self.assertTrue(plan(offline=False)['execution_eligible'])
    def test_json_deterministic(self):self.assertEqual(plan_json(plan()),plan_json(plan()))
    def test_human_json_agree(self):
        p=plan();parsed=json.loads(plan_json(p));self.assertIn('Run type: '+parsed['requested_run_type'],plan_lines(p))
    def test_output_visible(self):self.assertEqual(plan(save_collection='output/fictional.json')['output_destination'],'output/fictional.json')
    def test_source_visible(self):self.assertIn('Source state: CleanCommitted',plan_lines(plan()))
    def test_skip_reason_visible(self):self.assertIn('No explicit reassessment',plan()['delta']['reason'])
    def test_disabled_reason_visible(self):self.assertEqual(plan(run_type='Reassessment',delta_mode='disabled')['delta']['reason'],'Delta explicitly disabled.')
    def test_delta_enabled_without_mode_rejected(self):self.assertIn('delta_requested_without_reassessment',plan(delta_mode='enabled')['validation_errors'])
    def test_no_auto_initial(self):self.assertEqual(plan(run_type=None)['requested_run_type'],'UnclassifiedLegacy')
    def test_unknown_source_blocks_publication(self):
        p=build_run_plan(args(),revision=dict(fictional_revision(),source_state='DirtyUnclassified'));self.assertFalse(p['publication_eligible'])
    def test_engineering_marked(self):
        p=plan(source_policy='engineering-validation',assessment_purpose='engineering',source_reason='fictional fixture',acknowledge_dirty_source=True)
        self.assertTrue(p['execution_eligible']);self.assertFalse(p['publication_eligible']);self.assertIn('NOT CUSTOMER-READY',p['producing_revision']['publication']['output_qualification'])
    def test_legacy_baseline_not_explicit_delta(self):self.assertIn('legacy_baseline_cannot_mix_with_explicit_run',plan(baseline='old.xlsx')['validation_errors'])
    def test_optional_imports_visible(self):self.assertIn('dspm_report',plan()['sources']['optional_imports_not_supplied'])
    def test_import_counts_not_customer_paths(self):self.assertEqual(plan(sam_report=['private/source.csv'])['sources']['imports_supplied'],{'sam_report':1})
    def test_main_plan_json_no_dependency_or_import_execution(self):
        root=Path(__file__).resolve().parents[1];output=io.StringIO()
        with patch('sys.argv',['main.py','--plan','--plan-format','json','--run-type','Initial','--tenant-id',TENANT]),\
             patch('Core.run_plan.capture_revision',return_value=fictional_revision()),\
             patch('Core.console_setup.setup_console_encoding'),\
             patch('Core.check_dependencies.check_dependencies',side_effect=AssertionError('dependencies invoked')),\
             patch('Core.offline_report.run_offline_report',side_effect=AssertionError('import execution invoked')),\
             redirect_stdout(output),self.assertRaises(SystemExit) as exit:
            runpy.run_path(str(root/'main.py'),run_name='__main__')
        self.assertEqual(exit.exception.code,0);self.assertEqual(json.loads(output.getvalue())['requested_run_type'],'Initial')
    def test_missing_replay_metadata_blocks_execution(self):
        self.assertIn('replay_snapshot_invalid',plan(run_type=None,replay_snapshot='nonexistent-fixture.json')['validation_errors'])
    def test_engineering_warning_survives_clean_source(self):
        p=plan(source_policy='engineering-validation',assessment_purpose='Fictional validation',source_reason='Validate fixture',acknowledge_dirty_source=True)
        self.assertTrue(any('NOT CUSTOMER-READY' in w for w in p['warnings']))


class BaselinePlanTests(RunFixture):
    def setup_plan(self,**overrides):
        old,_=self.initial()
        options=dict(run_type='Reassessment',assessment_id=old.identity['AssessmentId'],baseline_run_id=old.identity['RunId'],assessment_history=str(self.history))
        options.update(overrides)
        before={str(f):f.read_bytes() for f in self.root.rglob('*') if f.is_file()}
        p=plan(timestamp=datetime.now(timezone.utc).isoformat(),**options)
        after={str(f):f.read_bytes() for f in self.root.rglob('*') if f.is_file()}
        self.assertEqual(before,after,'planning changed the baseline package/history')
        return p
    def test_baseline_integrity_and_completion(self):
        p=self.setup_plan();self.assertEqual(p['baseline']['integrity'],'validated');self.assertEqual(p['baseline']['completion'],'completed')
    def test_baseline_ownership(self):self.assertEqual(self.setup_plan()['baseline']['ownership'],'validated')
    def test_delta_eligibility(self):self.assertTrue(self.setup_plan()['delta']['expected'])
    def test_exact_run_required(self):self.assertIn('baseline_validation_failed',self.setup_plan(baseline_run_id='RUN-missing')['validation_errors'])
    def test_wrong_assessment(self):self.assertIn('baseline_validation_failed',self.setup_plan(assessment_id='AST-wrong')['validation_errors'])
    def test_disabled_delta(self):self.assertFalse(self.setup_plan(delta_mode='disabled')['delta']['expected'])
    def test_missing_baseline_not_latest(self):self.assertIn('run_plan_baseline_missing',self.setup_plan(baseline_run_id=None)['validation_errors'])
    def test_baseline_hash_failure(self):
        old,_=self.initial();snapshot=next((self.root/'initial'/'assessment-results').glob('*.json'));snapshot.write_text('{}')
        p=plan(run_type='Reassessment',assessment_id=old.identity['AssessmentId'],baseline_run_id=old.identity['RunId'],assessment_history=str(self.history))
        self.assertIn('baseline_validation_failed',p['validation_errors'])
    def test_initial_rejects_existing_history_before_write(self):
        self.initial()
        self.assertIn('initial_history_already_exists',plan(assessment_history=str(self.history))['validation_errors'])
    def test_saved_package_replay_plan_retains_identity_and_source(self):
        execution,_=self.initial()
        collection=self.root/'initial'/'collection.json'
        collection.write_text(json.dumps({'tenant_id':TENANT,'identity':execution.identity}))
        before={str(f):f.read_bytes() for f in self.root.rglob('*') if f.is_file()}
        p=plan(run_type=None,collection_input=str(collection))
        self.assertEqual(p['requested_run_type'],'Replay')
        self.assertEqual(p['retained_run_id'],execution.identity['RunId'])
        self.assertEqual(p['assessment_id'],execution.identity['AssessmentId'])
        self.assertFalse(p['sources']['tenant_collection'])
        self.assertIsNotNone(p['original_producing_revision'])
        self.assertEqual(before,{str(f):f.read_bytes() for f in self.root.rglob('*') if f.is_file()})
    def test_failed_source_policy_prevents_expected_delta(self):
        p=self.setup_plan(source_policy='engineering-validation')
        self.assertFalse(p['delta']['expected'])


if __name__=='__main__':unittest.main()

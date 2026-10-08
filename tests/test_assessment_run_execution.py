"""Noninteractive workflows with temporary, fictional portable packages."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from test_assessment_run_workflow import RunFixture, TENANT
from test_assessment_run_outputs import parse


class RunExecutionTests(RunFixture):
    def source(self):
        from tests.synthetic_package_fixture import create_synthetic_package
        return create_synthetic_package(self.root / 'source')

    def run_offline(self, *options):
        from Core.offline_report import run_offline_report
        original = Path.cwd()
        try:
            os.chdir(self.root)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(run_offline_report(parse('--mode','offline',*options)),0)
        finally:
            os.chdir(original)

    def test_initial_reassessment_and_package_replay_append_once(self):
        from Core.assessment_history import read_history
        source = self.source()
        self.run_offline('--collection-input',source,'--run-type','Initial','--assessment-history',self.history)
        history = read_history(self.history)
        initial = history['Runs'][0]
        self.run_offline('--collection-input',source,'--run-type','Reassessment',
            '--assessment-id',history['AssessmentId'],'--assessment-history',self.history,
            '--baseline-run-id',initial['RunId'])
        history = read_history(self.history)
        self.assertEqual(len(history['Runs']),2)
        self.assertEqual(history['Runs'][0],initial)
        current = history['Runs'][1]
        self.assertNotEqual(current['RunId'],initial['RunId'])
        package = (self.history.parent / current['PackageLocator']).resolve()
        before = self.history.read_bytes()
        original_collection = (package / 'collection.json').read_bytes()
        with patch('Core.processor.process_and_print_all_information', side_effect=AssertionError('Replay evaluated')):
            self.run_offline('--collection-input',package / 'collection.json')
        self.assertEqual(self.history.read_bytes(),before)
        self.assertEqual((package / 'collection.json').read_bytes(),original_collection)
        self.assertTrue(list((package / 'Renders').rglob('Assessment.html')))

    def test_default_initial_history_moves_with_package_and_preserves_replay(self):
        from Core.offline_collection import load_collection
        source = self.source()
        self.run_offline('--collection-input',source,'--run-type','Initial')
        histories = list(self.root.glob('Reports/*/*/assessment-history.json'))
        self.assertEqual(len(histories),1)
        history = json.loads(histories[0].read_text(encoding='utf-8'))
        package = histories[0].parent
        snapshot = package / history['Runs'][0]['SnapshotLocator']
        identity = json.loads(snapshot.read_text(encoding='utf-8'))['identity']
        moved = self.root / 'moved'
        shutil.move(str(package),moved)
        payload = load_collection(moved / 'collection.json')
        self.assertEqual(payload['identity']['RunId'],identity['RunId'])
        self.assertEqual(payload['identity']['RunContext'],identity['RunContext'])
        original = (moved / 'assessment-history.json').read_bytes()
        self.run_offline('--collection-input',moved / 'collection.json')
        self.assertEqual((moved / 'assessment-history.json').read_bytes(),original)

    def test_standalone_publishes_without_history_and_new_evidence_requires_new_intent(self):
        from Core.offline_report import run_offline_report
        source = self.source()
        self.run_offline('--collection-input',source,'--run-type','Standalone')
        manifests = list(self.root.glob('Reports/*/*/assessment-run.json'))
        self.assertEqual(len(manifests),1)
        package = manifests[0].parent
        self.assertFalse((package / 'assessment-history.json').exists())
        args = parse('--collection-input',package / 'collection.json','--evaluation-date','2026-09-16')
        with self.assertRaisesRegex(ValueError,'explicit new'):
            run_offline_report(args)

    def test_collection_checkpoint_seed_and_completed_manifest_are_consistent(self):
        from Core.assessment_runs import prepare_run, complete_run
        from Core.offline_collection import save_collection, load_collection, empty_service_results
        from test_assessment_run_workflow import result
        package = self.root / 'package'
        execution = prepare_run('Initial',TENANT,evaluated_at='2026-09-15',history_path=package / 'assessment-history.json')
        collection = save_collection(package / 'collection.json',tenant_id=TENANT,tenant_name='Fictional',
            service_results=empty_service_results(), identity=execution.identity, package_directory=package)
        original = Path(collection).read_bytes()
        complete_run(result(execution.identity),execution,package)
        loaded = load_collection(collection)
        self.assertEqual(loaded['identity']['RunId'],execution.identity['RunId'])
        self.assertEqual(Path(collection).read_bytes(),original)


class LiveRunExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from test_collection_autosave import CollectionAutosaveTests
        self.fixture = CollectionAutosaveTests('test_default_saves_distinct_replayable_collections_and_prints_paths')
        self.fixture.setUp()
        self.addCleanup(self.fixture.directory.cleanup)
        self.addCleanup(os.chdir, self.fixture.original_cwd)
        self.addCleanup(self.fixture.stack.close)
        from Core.processor import process_and_print_all_information
        self.fixture.processor.side_effect = process_and_print_all_information

    async def test_live_initial_and_reassessment_share_history_and_persist_seeds(self):
        from Core.assessment_history import read_history
        await self.fixture.run_assessment(run_type='Initial',evaluation_date='2026-09-15')
        history_path = next(Path('Reports').glob('*/*/assessment-history.json'))
        history = read_history(history_path)
        self.assertEqual(len(history['Runs']),1,self.fixture.output.getvalue())
        self.assertTrue((history_path.parent / 'collection.json').is_file())
        self.assertTrue((history_path.parent / 'run-seed.json').is_file())
        await self.fixture.run_assessment(run_type='Reassessment',assessment_id=history['AssessmentId'],
            baseline_run_id=history['InitialRunId'],assessment_history=history_path,evaluation_date='2026-09-16')
        self.assertEqual(len(read_history(history_path)['Runs']),2,self.fixture.output.getvalue())

    async def test_explicit_live_save_destination_retains_one_owned_package(self):
        from Core.offline_collection import load_collection
        target = Path('selected.json')
        await self.fixture.run_assessment(run_type='Standalone',save_collection_path=target)
        payload = load_collection(target)
        self.assertEqual(payload['identity']['RunType'],'Standalone',self.fixture.output.getvalue())
        package = Path(payload['package_directory'])
        self.assertTrue((package / 'assessment-run.json').is_file())
        self.assertFalse((package / 'assessment-history.json').exists())

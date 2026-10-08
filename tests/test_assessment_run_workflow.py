"""Stage A acceptance tests; fictional identity and evidence only."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TENANT = '11111111-1111-1111-1111-111111111111'
OTHER = '22222222-2222-2222-2222-222222222222'
DAY = '2026-09-15'


def result(identity):
    from Core.assessment_identity import entity
    meta = copy.deepcopy(identity)
    meta['Entities'] = [entity('control', meta, {'namespace':'m365-readiness', 'control_id':'IDENTITY.MFA'})]
    return {'tenant_id':TENANT, 'identity':meta, 'recommendations':[], 'actions':[],
        'control_results':[{'ControlId':'IDENTITY.MFA','Status':'Pass'}],
        'evidence':[{'control_id':'IDENTITY.MFA','metric_id':'enforcement','provider':'microsoft',
            'population':'all users','scope':'tenant','unit':'count','value':0,'availability':'available',
            'complete':True,'evidence_level':'policy_enforcement','window':'snapshot','selection':'selected'}],
        'run_context':copy.deepcopy(meta['RunContext']),
        'reconciliation':{'state':'legacy','reason':'Synthetic graph without source occurrences.'},
        'run_boundaries':{'purpose':'readiness', 'scope':'tenant-wide', 'providers':['microsoft'],
            'population_definitions':['all users'], 'resource_scopes':['tenant']},
        'collection_coverage':{'auth_methods':{'state':'available','complete':True,
            'population':'all users','collection_semantics':'full enumeration'}}}


class RunFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.history = self.root / 'assessment-history.json'

    def initial(self):
        from Core.assessment_runs import prepare_run, complete_run
        execution = prepare_run('Initial', TENANT, evaluated_at=DAY, history_path=self.history,
                                purpose='readiness', catalog_version='catalog-1')
        snapshot = result(execution.identity)
        complete_run(snapshot, execution, self.root / 'initial')
        return execution, snapshot


class WorkflowTests(RunFixture):
    def test_initial_persists_seed_and_completed_history_once(self):
        from Core.assessment_history import read_history
        execution, snapshot = self.initial()
        history = read_history(self.history)
        self.assertEqual(history['InitialRunId'], execution.identity['RunId'])
        self.assertEqual(len(history['Runs']), 1)
        self.assertIsNone(snapshot['run_context']['BaselineRunId'])
        self.assertEqual(snapshot['run_context']['Comparability']['Outcome'], 'NotEvaluated')
        self.assertTrue(list(self.root.glob('run-seeds/*.json')))

    def test_reassessment_reuses_assessment_and_requires_explicit_baseline(self):
        from Core.assessment_runs import prepare_run, complete_run
        from Core.assessment_history import read_history
        old, baseline = self.initial()
        common = dict(evaluated_at='2026-09-16', history_path=self.history,
                      assessment_id=old.identity['AssessmentId'], catalog_version='catalog-1', purpose='readiness')
        with self.assertRaisesRegex(ValueError, 'baseline'):
            prepare_run('Reassessment', TENANT, **common)
        current = prepare_run('Reassessment', TENANT, baseline_run_id=old.identity['RunId'], **common)
        self.assertEqual(current.identity['AssessmentId'], old.identity['AssessmentId'])
        self.assertNotEqual(current.identity['RunId'], old.identity['RunId'])
        original = copy.deepcopy(read_history(self.history)['Runs'])
        complete_run(result(current.identity), current, self.root / 'current')
        self.assertEqual(read_history(self.history)['Runs'][:1], original)
        self.assertEqual(len(read_history(self.history)['Runs']), 2)

    def test_invalid_intent_matrix(self):
        from Core.assessment_runs import prepare_run
        for mode, kwargs in [('Automatic', {}), ('Initial', {'baseline_run_id':'RUN-x'}),
                ('Standalone', {'baseline_run_id':'RUN-x'}), ('Reassessment', {}),
                ('Initial', {'tenant_id':'customer title'}), ('Initial', {'tenant_id':OTHER,'environment_id':'ENV-wrong'})]:
            with self.subTest(mode=mode, kwargs=kwargs), self.assertRaises(ValueError):
                supplied = {'tenant_id':TENANT, **kwargs}
                prepare_run(mode, evaluated_at=DAY, history_path=self.history, **supplied)

    def test_second_initial_cannot_overwrite_history(self):
        from Core.assessment_runs import prepare_run
        self.initial()
        before = self.history.read_bytes()
        with self.assertRaises(ValueError):
            prepare_run('Initial', TENANT, evaluated_at=DAY, history_path=self.history)
        self.assertEqual(self.history.read_bytes(), before)

    def test_standalone_without_history_and_replay_do_not_append(self):
        from Core.assessment_runs import prepare_run, complete_run
        execution = prepare_run('Standalone', TENANT, evaluated_at=DAY)
        self.assertEqual(execution.identity['RunType'], 'Standalone')
        self.assertIsNone(execution.identity['BaselineRunId'])
        self.assertFalse(self.history.exists())
        replay = prepare_run('Replay', TENANT, evaluated_at='2099-01-01', existing_identity=execution.identity)
        self.assertEqual(replay.identity, execution.identity)
        with patch('Core.assessment_history.append_run', side_effect=AssertionError('Replay append')):
            complete_run(result(replay.identity), replay, self.root / 'render')

    def test_standalone_continuation_requires_explicit_ownership(self):
        from Core.assessment_runs import prepare_run
        old, _ = self.initial()
        with self.assertRaises(ValueError):
            prepare_run('Standalone', TENANT, evaluated_at=DAY, assessment_id=old.identity['AssessmentId'])
        current = prepare_run('Standalone', TENANT, evaluated_at=DAY, history_path=self.history,
                              assessment_id=old.identity['AssessmentId'])
        self.assertEqual(current.identity['AssessmentId'], old.identity['AssessmentId'])


class HistoryTests(RunFixture):
    def test_tampering_and_unknown_schema_are_rejected(self):
        from Core.assessment_history import read_history
        self.initial()
        original = self.history.read_text(encoding='utf-8')
        for field, value in [('AssessmentId','AST-foreign'), ('SchemaVersion','999'), ('Runs',[])]:
            with self.subTest(field=field):
                history = json.loads(original)
                history[field] = value
                self.history.write_text(json.dumps(history), encoding='utf-8')
                with self.assertRaises(ValueError):
                    read_history(self.history)
        self.history.write_text(original, encoding='utf-8')

    def test_duplicate_and_foreign_append_preserve_history(self):
        from Core.assessment_history import append_run, read_history
        old, snapshot = self.initial()
        entry = read_history(self.history)['Runs'][0]
        before = self.history.read_bytes()
        with self.assertRaises(ValueError):
            append_run(self.history, snapshot, snapshot_path=self.root / entry['SnapshotLocator'],
                       package_path=self.root / entry['PackageLocator'])
        self.assertEqual(self.history.read_bytes(), before)

    def test_failed_atomic_replace_preserves_prior_history(self):
        from Core.assessment_history import atomic_write
        self.initial()
        before = self.history.read_bytes()
        with patch('pathlib.Path.replace', side_effect=OSError('Synthetic disk failure')):
            with self.assertRaises(OSError):
                atomic_write(self.history, {'replacement':'must not truncate history'})
        self.assertEqual(self.history.read_bytes(), before)

    def test_moved_history_and_packages_preserve_baseline_identity(self):
        import shutil
        from Core.assessment_history import select_baseline
        old, snapshot = self.initial()
        moved = self.root / 'moved'
        moved.mkdir()
        shutil.move(str(self.history), moved / self.history.name)
        shutil.move(str(self.root / 'initial'), moved / 'initial')
        loaded = select_baseline(moved / self.history.name,
            assessment_id=old.identity['AssessmentId'], environment_id=old.identity['PrimaryEnvironmentId'],
            baseline_run_id=old.identity['RunId'])
        self.assertEqual(loaded['identity']['RunId'], snapshot['identity']['RunId'])


class BaselineTests(RunFixture):
    def test_missing_foreign_self_future_and_unreadable_baselines(self):
        from Core.assessment_history import select_baseline, read_history
        old, _ = self.initial()
        meta = old.identity
        options = dict(assessment_id=meta['AssessmentId'], environment_id=meta['PrimaryEnvironmentId'],
                       baseline_run_id=meta['RunId'])
        cases = [dict(baseline_run_id='RUN-missing'), dict(assessment_id='AST-foreign'),
                 dict(environment_id='ENV-foreign'), dict(current_run_id=meta['RunId']), dict(current_sequence=1),
                 dict(current_created_at='2000-01-01T00:00:00+00:00')]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(ValueError):
                select_baseline(self.history, **{**options, **change})
        entry = read_history(self.history)['Runs'][0]
        (self.root / entry['SnapshotLocator']).unlink()
        with self.assertRaises(ValueError):
            select_baseline(self.history, **options)


class ComparabilityTests(unittest.TestCase):
    def pair(self):
        from Core.assessment_runs import prepare_run
        seed = prepare_run('Standalone', TENANT, evaluated_at=DAY, purpose='readiness', catalog_version='catalog-1').identity
        return result(seed), result(seed)

    def test_matching_boundaries_are_comparable_and_deterministic(self):
        from Core.run_comparability import evaluate_comparability
        current, baseline = self.pair()
        first = evaluate_comparability(current, baseline)
        self.assertEqual(first['Outcome'], 'Comparable')
        self.assertEqual(first, evaluate_comparability(current, baseline))
        self.assertEqual(first['Items'][0]['Eligibility'], 'eligible')

    def test_version_and_boundary_matrix_retains_reasons(self):
        from Core.run_comparability import evaluate_comparability
        for field in ('MethodologyVersion','CatalogVersion','SchemaVersion'):
            current, baseline = self.pair()
            current['identity'][field] = 'different'
            outcome = evaluate_comparability(current, baseline)
            self.assertEqual(outcome['Outcome'], 'NotComparable', field)
            self.assertTrue(outcome['Reasons'])
        for field in ('purpose','scope','providers','population_definitions','resource_scopes'):
            current, baseline = self.pair()
            current['run_boundaries'][field] = 'different'
            outcome = evaluate_comparability(current, baseline)
            self.assertEqual(outcome['Outcome'], 'ComparableWithQualifications', field)
            self.assertTrue(outcome['Reasons'])

    def test_current_collection_state_matrix_never_establishes_eligibility(self):
        from Core.run_comparability import evaluate_comparability
        for state in ('failed','unavailable','not_requested','unlicensed','inaccessible','missing','partial','unknown'):
            current, baseline = self.pair()
            current['collection_coverage']['auth_methods'].update(state=state, complete=False)
            outcome = evaluate_comparability(current, baseline)
            self.assertNotEqual(outcome['Outcome'], 'Comparable', state)
            self.assertNotEqual(outcome['Items'][0]['Eligibility'], 'eligible', state)
            self.assertTrue(outcome['Reasons'])

    def test_partial_baseline_and_known_empty_are_qualified_safely(self):
        from Core.run_comparability import evaluate_comparability
        current, baseline = self.pair()
        baseline['collection_coverage']['auth_methods'].update(state='partial', complete=False)
        self.assertEqual(evaluate_comparability(current, baseline)['Outcome'], 'ComparableWithQualifications')
        current, baseline = self.pair()
        current['collection_coverage']['auth_methods']['state'] = 'empty'
        self.assertEqual(evaluate_comparability(current, baseline)['Outcome'], 'Comparable')
        current['collection_coverage']['auth_methods'].pop('population')
        self.assertNotEqual(evaluate_comparability(current, baseline)['Outcome'], 'Comparable')

    def test_missing_current_item_and_unresolved_aliases_never_mean_resolution(self):
        from Core.run_comparability import evaluate_comparability
        current, baseline = self.pair()
        current['identity']['Entities'] = []
        comparison = evaluate_comparability(current, baseline)
        self.assertEqual(comparison['Items'][0]['Eligibility'], 'not eligible')
        self.assertIn('missing_current_item', str(comparison['Items'][0]['Reasons']))
        self.assertNotIn('Resolved', json.dumps(comparison))
        current, baseline = self.pair()
        current['identity']['Aliases'] = [{'Value':'old','TargetType':'finding','TargetId':None}]
        self.assertTrue(any(item['Eligibility']=='unresolved legacy metadata'
                            for item in evaluate_comparability(current, baseline)['Items']))

    def test_observation_identity_changes_across_runs_but_semantic_boundaries_match(self):
        from Core.assessment_identity import entity
        from Core.run_comparability import evaluate_comparability
        current, baseline = self.pair()
        boundary = dict(assessment_id=current['identity']['AssessmentId'],
            environment_id=current['identity']['PrimaryEnvironmentId'],provider='microsoft',
            control_id='IDENTITY.MFA',metric_id='enforcement',population='all users',
            resource_scope='tenant',window='snapshot',evidence_level='policy_enforcement',unit='count')
        for snapshot, capture in [(current,'PCP-current'),(baseline,'PCP-baseline')]:
            snapshot['identity']['Entities'].append(entity('observation',snapshot['identity'],
                {**boundary,'run_id':snapshot['identity']['RunId'],'capture_id':capture}))
        comparison = evaluate_comparability(current, baseline)
        observation = next(item for item in comparison['Items'] if item['Type']=='observation')
        self.assertEqual(observation['Eligibility'], 'eligible')
        self.assertNotEqual(observation['CurrentId'], observation['BaselineId'])

    def test_missing_evidence_conflict_and_not_established_are_not_eligible(self):
        from Core.run_comparability import evaluate_comparability
        for change in ('missing','conflict','not_established'):
            current, baseline = self.pair()
            if change=='missing':
                current['evidence'] = []
            elif change=='conflict':
                current['evidence'][0]['selection'] = 'conflict'
            else:
                current['control_results'][0]['Status'] = 'Not established'
            comparison = evaluate_comparability(current, baseline)
            self.assertNotEqual(comparison['Items'][0]['Eligibility'], 'eligible', change)

    def test_metric_definition_and_window_matrix_prevents_direct_comparison(self):
        from Core.run_comparability import evaluate_comparability
        for field in ('unit','population','scope','provider','window','metric_definition','population_definition'):
            current, baseline = self.pair()
            current['evidence'][0][field] = 'changed'
            comparison = evaluate_comparability(current, baseline)
            self.assertNotEqual(comparison['Items'][0]['Eligibility'], 'eligible', field)
            self.assertTrue(comparison['Items'][0]['Reasons'])

    def test_unresolved_versions_and_evidence_level_changes_are_explicit(self):
        from Core.run_comparability import evaluate_comparability
        current, baseline = self.pair()
        current['evidence'][0]['evidence_level'] = 'configuration'
        self.assertEqual(evaluate_comparability(current, baseline)['Items'][0]['Eligibility'],
                         'eligible with qualifications')
        current, baseline = self.pair()
        current['identity']['MethodologyVersion'] = None
        self.assertNotEqual(evaluate_comparability(current, baseline)['Outcome'], 'Comparable')

    def test_different_environment_is_not_comparable(self):
        from Core.run_comparability import evaluate_comparability
        current, baseline = self.pair()
        current['identity']['PrimaryEnvironmentId'] = 'ENV-foreign'
        self.assertEqual(evaluate_comparability(current, baseline)['Outcome'], 'NotComparable')


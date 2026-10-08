"""Conservative comparison acceptance tests; all fixtures are fictional."""
import copy
import itertools
import json
import tempfile
from pathlib import Path
import unittest

from test_stage1_semantics import DAY, TENANT, fact, evidence, observation


def reconcile(rows):
    from Core.observation_reconciliation import reconcile_evidence
    return reconcile_evidence(rows, evaluation_date=DAY, expected_tenant_id=TENANT)


class ScopeReconciliationTests(unittest.TestCase):
    def test_exact_duplicates_preserve_occurrences_and_locators(self):
        rows = [fact(source_file='a.csv', capture_id='capture-a', native_id='native-a'),
                fact(source_file='b.csv', capture_id='capture-b', native_id='native-a')]
        model = reconcile(rows)
        self.assertEqual(len(model['observations']), 1)
        self.assertEqual(len(model['occurrences']), 2)
        self.assertEqual(len(model['exact_duplicate_groups']), 1)
        self.assertEqual({r['source']['native_id'] for r in model['occurrences']}, {'native-a'})
        self.assertEqual(len(model['rows']), 2)  # compatibility adapter retains source rows
        self.assertEqual(model, reconcile(list(reversed(rows))))

    def test_material_dimensions_remain_separate(self):
        variants = dict(AssessmentId='AST-other', RunId='RUN-other', PrimaryEnvironmentId='ENV-other',
            provider='okta', source_workload='sign_ins', control_id='OTHER', control_definition_version='5',
            metric_id='other', metric_definition='other definition', unit='events', population='pilot',
            population_definition='licensed only', scope='selected sites', applicability='pilot only',
            rollout_stage='expansion', evidence_level='observed_operation', window='30 days',
            window_start='2026-08-01T00:00:00Z', window_end='2026-08-31T00:00:00Z',
            product='other', tier='other', reporting_basis='period')
        for field, value in variants.items():
            with self.subTest(field=field):
                model = reconcile([fact(), fact(**{field: value})])
                self.assertEqual(len(model['observations']), 2)
                self.assertTrue(model['non_comparable'])
                self.assertFalse(model['conflicts'])
                self.assertEqual(sum(r['selection'] == 'selected' for r in model['rows']), 2)

    def test_unknown_population_is_not_a_wildcard_or_equivalence(self):
        for missing in ('population', 'scope', 'metric_id', 'tenant_id'):
            with self.subTest(missing=missing):
                model = reconcile([fact(**{missing: '', 'source_file': 'a'}),
                                   fact(**{missing: '', 'source_file': 'b'})])
                self.assertEqual(len(model['observations']), 2)
                self.assertTrue(model['diagnostics'])
                self.assertTrue(model['non_comparable'])

    def test_full_timestamps_order_same_day_captures(self):
        model = reconcile([fact(value=1, observed_at=DAY+'T08:00:00Z'),
                           fact(value=0, observed_at=DAY+'T14:00:00Z')])
        self.assertFalse(model['conflicts'])
        self.assertEqual([r['value'] for r in model['rows'] if r['selection']=='selected'], [0])
        self.assertEqual(len(model['observations']), 2)

    def test_date_only_cannot_order_intra_day_capture(self):
        model = reconcile([fact(value=1), fact(value=0, observed_at=DAY+'T14:00:00Z')])
        self.assertTrue(model['conflicts'])

    def test_equivalent_offsets_and_dictionary_order_are_deterministic(self):
        rows=[fact(observed_at=DAY+'T12:00:00+00:00'),fact(observed_at=DAY+'T07:00:00-05:00',source_file='other')]
        model=reconcile(rows)
        self.assertEqual(len(model['observations']),1)
        reordered=[dict(reversed(list(row.items()))) for row in rows[::-1]]
        self.assertEqual(model,reconcile(reordered))

    def test_native_records_and_ratio_denominators_are_material(self):
        self.assertEqual(len(reconcile([fact(native_id='object-a'),fact(native_id='object-b')])['observations']),2)
        ratio=dict(unit='%',numerator=2,denominator=10,value=20)
        model=reconcile([fact(**ratio),fact(**dict(ratio,numerator=4,denominator=20))])
        self.assertEqual(len(model['observations']),2)
        self.assertFalse(model['conflicts'])
        self.assertEqual(sum(row['selection']=='selected' for row in model['rows']),2)

    def test_complete_outranks_partial_without_collapsing_it(self):
        model = reconcile([fact(value=0, observed_at='2026-09-09T12:00:00Z'),
                           fact(value=8, observed_at=DAY+'T14:00:00Z', complete=False, availability='partial')])
        self.assertEqual(len(model['observations']), 2)
        self.assertEqual([r['value'] for r in model['rows'] if r['selection']=='selected'], [0])

    def test_unavailable_states_never_become_zero(self):
        for state in ('missing','failed','unavailable','unsupported','unlicensed','inaccessible','not_requested','empty'):
            with self.subTest(state=state):
                model = reconcile([fact(), fact(availability=state, value=0)])
                self.assertEqual(len(model['observations']), 2)
                unavailable = next(r for r in model['rows'] if r['availability']==state)
                self.assertIsNone(unavailable['value'])
                self.assertNotEqual(unavailable['selection'], 'selected')

    def test_equal_numbers_opposite_conclusions_conflict_deterministically(self):
        rows = [fact(control_result='pass', value=0, source_file='a'),
                fact(control_result='fail', value=0.0, source_file='b')]
        model = reconcile(rows)
        self.assertTrue(model['conflicts'])
        self.assertEqual({r['selection'] for r in model['rows']}, {'conflict'})
        self.assertEqual(model, reconcile(rows[::-1]))
        self.assertEqual(model['conflicts'][0]['result'], 'Not established')

    def test_quality_and_ratio_differences_are_not_exact_duplicates(self):
        for changes in ({'complete':False}, {'truncated':True}, {'qualifications':['limited review']},
                        {'numerator':1}, {'denominator':10}, {'availability':'partial'}):
            with self.subTest(changes=changes):
                self.assertEqual(len(reconcile([fact(), fact(**changes)])['observations']), 2)

    def test_no_mutation_no_aggregation_and_insertion_stability(self):
        rows = [fact(value=20), fact(value=20.0, source_file='other')]
        before = copy.deepcopy(rows)
        model = reconcile(rows)
        self.assertEqual(rows, before)
        self.assertEqual(len(model['observations']), 1)
        extra = reconcile(rows+[fact(metric_id='unrelated', value=40)])
        self.assertEqual(model['observations'][0]['id'], next(r['id'] for r in extra['observations'] if r['dimensions']['metric_id']=='permission_review'))

    def test_builder_attaches_shared_model_and_snapshot_roundtrip(self):
        from Core.assessment_result import build_assessment_result
        from Core.assessment_serialization import write_assessment_result, read_assessment_result
        result = build_assessment_result([], {'observations':[fact(),fact(source_file='other')]}, evaluation_date=DAY, expected_tenant_id=TENANT)
        self.assertEqual(len([row for row in result['reconciliation']['observations']
                             if row['dimensions']['metric_id']=='permission_review']), 1)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'fictional.json'
            write_assessment_result(path,result)
            loaded=read_assessment_result(path)
        self.assertEqual(result['reconciliation'], loaded['reconciliation'])

    def test_generated_pob_value_collision_is_reproduced_and_corrected(self):
        from Core.assessment_identity import attach_identity, new_identity
        from Core.assessment_references import validate_assessment_references
        from Core.evidence_contract import reconcile_observations
        seed=new_identity(TENANT, methodology_version='4', evaluated_at=DAY)
        rows=[fact(provider='microsoft',source_schema='report',source_hash='a'*64,
                   evidence_level='configuration',control_result=conclusion) for conclusion in ('pass','fail')]
        result={'tenant_id':TENANT,'controls':[{'control_id':'CONTENT.PERMISSIONS'}], 'evidence':reconcile_observations(rows,evaluation_date=DAY)}
        attach_identity(result,{'collection_context':{'identity':seed}})
        nodes=[r for r in result['identity']['Entities'] if r['Type']=='observation']
        self.assertEqual(len({r['Id'] for r in nodes}), 2)
        self.assertFalse([r for r in validate_assessment_references(result) if r['severity']=='error'])


class ReconciliationValidationTests(unittest.TestCase):
    def test_invalid_memberships_and_favorable_conflict_block_publication(self):
        from Core.assessment_result import build_assessment_result
        from Core.assessment_references import require_valid_assessment
        result=build_assessment_result([], {'observations':[fact(control_result='pass'),fact(control_result='fail')]},evaluation_date=DAY,expected_tenant_id=TENANT)
        for mutation in ('dangling','favorable','missing_reason','duplicate_membership'):
            with self.subTest(mutation=mutation):
                bad=copy.deepcopy(result)
                model=bad['reconciliation']
                if mutation=='dangling': model['conflicts'][0]['observation_ids'].append('unknown')
                if mutation=='favorable': model['conflicts'][0]['result']='Observed'
                if mutation=='missing_reason': model['conflicts'][0]['reason']=''
                if mutation=='duplicate_membership': model['observations'].append(copy.deepcopy(model['observations'][0]))
                with self.assertRaises(ValueError): require_valid_assessment(bad)

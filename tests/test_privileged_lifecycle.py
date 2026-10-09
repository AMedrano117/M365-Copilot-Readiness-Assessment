"""Pass B inputs retain conservative lifecycle and explicit governance boundaries."""
import copy
import unittest
from unittest.mock import patch
from test_privileged_identity import NOW, OLD, BOUNDARY, sources, dataset, policy, build


def assessment(data, seed, days=90, purposes=None):
    from Core.assessment_result import build_assessment_result
    from Core.evidence_layer import build_evidence_bundle
    from test_privileged_outputs import TENANT
    bundle = build_evidence_bundle([], ({}, []), {}, {}, {}, {}, {})
    bundle.update(assessment_sources=data, expected_tenant_id=TENANT,
        collection_context={'tenant_id': TENANT, 'source_file': 'fictional-privilege.json', 'collected_at': NOW, 'identity': seed},
        assessment_profile={'assessment_context': {'privileged_evaluation_timestamp': NOW,
            'privileged_inactivity_policy': policy(days), 'account_purposes': purposes or []}})
    return build_assessment_result([], bundle, evaluation_date='2026-10-09', expected_tenant_id=TENANT)


def snapshots(first=None, second=None, first_days=90, second_days=90):
    from Core.assessment_identity import new_identity, new_run
    from test_privileged_outputs import TENANT
    meta = new_identity(TENANT, evaluated_at=NOW, methodology_version='4.0.0', catalog_version='catalog-1')
    current_meta = new_run(meta, TENANT, evaluated_at=NOW)
    baseline = assessment(first or sources(), meta, first_days)
    current = assessment(second or sources(), current_meta, second_days)
    for snapshot, kind, base in ((baseline, 'Initial', None), (current, 'Reassessment', meta['RunId'])):
        snapshot['identity'].update(RunType=kind, BaselineRunId=base)
        snapshot['run_context'] = {k: snapshot['identity'][k] for k in ('AssessmentId', 'PrimaryEnvironmentId', 'RunId', 'RunType', 'BaselineRunId')}
        snapshot['run_context'].update(ReconciliationVersion='2.0.0', Purpose='readiness')
    return baseline, current


class PrivilegedLifecycleTests(unittest.TestCase):
    def test_threshold_change_reaches_actual_comparison_boundary(self):
        from Core.run_comparability import evaluate_comparability
        from Core.assessment_delta import evaluate_delta
        baseline, current = snapshots(first_days=30, second_days=200)
        a = next(f for f in baseline['evidence'] if 'Inactivity' in f['metric_id'])
        b = next(f for f in current['evidence'] if 'Inactivity' in f['metric_id'])
        self.assertNotEqual(a['control_definition_version'], b['control_definition_version'])
        compared = evaluate_comparability(current, baseline)
        current['run_context']['Comparability'] = compared
        delta = evaluate_delta(current, baseline)
        relevant = [r for r in delta['Records'] if 'Inactivity' in str(r.get('EntityBoundary'))]
        self.assertTrue(relevant)
        self.assertTrue(all(r['State'] not in {'Improved', 'Regressed', 'ResolvedByCurrentEvidence'} for r in relevant))

    def test_incomplete_population_and_missing_activity_cannot_resolve_or_appear_improved(self):
        from Core.run_comparability import evaluate_comparability
        from Core.assessment_delta import evaluate_delta
        for data in (sources(users=[{'id': 'user-1', 'accountEnabled': True}]), sources()):
            data['role_assignment_schedules'][0]['source'].update(complete=False, availability_status='partial')
            baseline, current = snapshots(second=data)
            original = copy.deepcopy(baseline)
            current['run_context']['Comparability'] = evaluate_comparability(current, baseline)
            delta = evaluate_delta(current, baseline)
            relevant = [r for r in delta['Records'] if r['EntityType'] == 'finding' and 'privileged' in str(r.get('EntityBoundary'))]
            self.assertTrue(relevant)
            self.assertTrue(all(r['State'] not in {'Improved', 'ResolvedByCurrentEvidence', 'ClosedByRemediation'} for r in relevant))
            self.assertEqual(baseline, original)

    def test_privileged_counts_have_no_inferred_direction_or_resolution_rule(self):
        from Core.delta_metrics import RULES
        baseline, current = snapshots()
        metrics = {f['metric_id'] for f in current['evidence'] if f['metric_id'].startswith('entra.privileged.')}
        self.assertTrue(metrics)
        self.assertTrue(metrics.isdisjoint(RULES))

    def test_purpose_validation_does_not_rewrite_prior_observations(self):
        data = sources(purpose=[dataset([{'purpose': 'emergency'}])])
        old = build(data, inactivity_policy=policy()); original = copy.deepcopy(old)
        declaration = {'principal_id': 'user-1', 'purpose': 'emergency', 'owner': 'fictional-owner', 'source_type': 'configuration',
            'evidence_refs': [{'dataset': 'purpose', 'dataset_index': 0, 'record_index': 0}]}
        current = build(data, inactivity_policy=policy(), account_purposes=[declaration])
        self.assertEqual(old, original)
        self.assertNotEqual(current['identities'][0]['purpose'], old['identities'][0]['purpose'])
        self.assertNotIn('governance', current)

    def test_accepted_risk_and_exception_overlays_leave_privileged_technical_records_unchanged(self):
        from test_governance_decisions import draft, advance, NOW as GOVERNANCE_NOW
        from Core.governance import attach
        for kind in ('AcceptedRisk', 'ApprovedException'):
            result, log, key, authority = draft(kind)
            # Bind the additive technical model to the graph's real ownership.
            from Core.privileged_identity import build_privileged_assessment
            result['privileged_assessment'] = build_privileged_assessment(sources(), evaluation_timestamp=NOW,
                boundary=dict(BOUNDARY, assessment_id=result['identity']['AssessmentId'], environment_id=result['identity']['PrimaryEnvironmentId']))
            technical = copy.deepcopy(result['privileged_assessment'])
            log = advance(result, log, key, authority)
            overlaid = attach(result, log, as_of=GOVERNANCE_NOW, locator='governance/decisions.json')
            self.assertEqual(overlaid['privileged_assessment'], technical)
            self.assertEqual(result['privileged_assessment'], technical)

    def test_rendering_cannot_create_approve_revoke_or_reopen_governance(self):
        from test_privileged_outputs import PrivilegedOutputTests
        from Core.customer_report import render_customer_report
        from Core.pilot_summary import render_pilot_summary
        result, bundle = PrivilegedOutputTests().fixture()
        before = copy.deepcopy(result)
        with (patch('Core.governance.create_draft', side_effect=AssertionError('Implicit decision')),
              patch('Core.governance.transition', side_effect=AssertionError('Implicit transition'))):
            render_customer_report(result, bundle, 'Fictional')
            render_pilot_summary(result, bundle, 'Fictional')
        self.assertEqual(result, before)


if __name__ == '__main__':
    unittest.main()

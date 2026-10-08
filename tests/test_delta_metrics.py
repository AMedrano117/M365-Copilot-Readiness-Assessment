"""Explicit direction and measurement boundary tests; fictional evidence only."""
import copy
import unittest


def fact(value=40, metric='identity.mfa_registration_percent', unit='%'):
    return dict(metric_id=metric, metric_definition=metric, control_id='ENDPOINT.POSTURE' if metric.startswith('endpoint.') else 'IDENTITY.MFA',
        provider='microsoft', value=value, unit=unit, numerator=value, denominator=100,
        population='all users', population_definition='all user accounts', scope='tenant',
        window='snapshot', window_start='2026-09-15T00:00:00Z', window_end='2026-09-15T00:00:00Z',
        reporting_basis='snapshot', evidence_level='configuration', availability='available',
        complete=True, truncated=False, selection='selected', freshness='current', evidence_id='EV-fiction')


class MetricComparisonTests(unittest.TestCase):
    def test_explicit_direction_matrix(self):
        from Core.delta_metrics import compare_metric
        for metric, unit, before, after, state in [
            ('identity.mfa_registration_percent','%',40,60,'Improved'),
            ('identity.mfa_registration_percent','%',60,40,'Regressed'),
            ('identity.mfa_registration_percent','%',40,40,'Unchanged'),
            ('identity.policy_exceptions','count',5,2,'Improved'),
            ('identity.policy_exceptions','count',2,5,'Regressed'),
            ('endpoint.unsupported_devices','devices',3,1,'Improved'),
            ('endpoint.unsupported_devices','devices',1,3,'Regressed'),
            ('endpoint.validated_coverage_percent','%',80,90,'Improved'),
            ('unknown.metric','count',3,1,'Changed')]:
            with self.subTest(metric=metric,before=before,after=after):
                a,b=fact(before,metric,unit),fact(after,metric,unit)
                if metric=='identity.policy_exceptions':a['evidence_level']=b['evidence_level']='policy_enforcement'
                self.assertEqual(compare_metric(a,b)['State'],state)

    def test_boundary_and_quality_matrix_never_directional(self):
        from Core.delta_metrics import compare_metric
        baseline = fact()
        for field, value in [('unit','users'),('population','administrators'),('population_definition',None),
                ('scope','pilot'),('provider','other'),('complete',False),('availability','unavailable'),
                ('selection','conflict'),('value',None),('denominator',None),('denominator',0),
                ('evidence_level','observed_operation'),('freshness','stale'),('numerator',101)]:
            with self.subTest(field=field):
                current = fact(60); current[field] = value
                compared = compare_metric(baseline,current)
                self.assertNotIn(compared['State'],{'Improved','Regressed','ResolvedByCurrentEvidence'})
                self.assertTrue(compared['Reasons'])

    def test_percentage_and_missing_values(self):
        from Core.delta_metrics import compare_metric
        compared = compare_metric(fact(40),fact(60))
        self.assertEqual(compared['PercentagePointChange'],20)
        self.assertEqual(compared['AbsoluteChange'],20)
        self.assertEqual(compared['PercentageChange'],50)
        self.assertIsNone(compare_metric(fact(None),fact(0))['AbsoluteChange'])
        self.assertIsNone(compare_metric(fact(0),fact(60))['PercentageChange'])

    def test_changed_denominator_and_period_are_preserved_and_qualified(self):
        from Core.delta_metrics import compare_metric
        current = fact(60); current.update(denominator=200,numerator=120,
            window_start='2026-09-16T00:00:00Z',window_end='2026-09-16T00:00:00Z')
        compared = compare_metric(fact(),current)
        self.assertEqual(compared['State'],'Indeterminate')
        self.assertEqual(compared['Current']['window_start'],current['window_start'])
        self.assertTrue(compared['Qualifications'])

    def test_rule_does_not_infer_effectiveness_or_resolution_from_registration(self):
        from Core.delta_metrics import compare_metric
        compared = compare_metric(fact(40),fact(100))
        self.assertEqual(compared['State'],'Improved')
        self.assertFalse(compared['ResolutionSupported'])
        before, after = fact(4,'identity.policy_exceptions','count'), fact(0,'identity.policy_exceptions','count')
        before['evidence_level']=after['evidence_level']='policy_enforcement'
        self.assertTrue(compare_metric(before,after)['ResolutionSupported'])

    def test_comparison_is_pure(self):
        from Core.delta_metrics import compare_metric
        a,b=fact(),fact(60); saved=copy.deepcopy((a,b))
        compare_metric(a,b)
        self.assertEqual((a,b),saved)

"""Regression checks for raw evidence and the coordinated report contract."""
import csv
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from Core.data_exposure_assessment import build_data_exposure_assessment
from Core.evidence_contract import reconcile_observations
from Core.saved_control_checks import assess_purview_configuration


class RawEvidenceRegressionTests(unittest.TestCase):
    def report(self, folder, name, count, date='2026-09-10'):
        path = folder / name
        with path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=['Tenant ID', 'Site URL', 'Report Date', 'Anyone link count'])
            writer.writeheader()
            writer.writerow({'Tenant ID': '11111111-1111-1111-1111-111111111111',
                'Site URL': 'https://example.sharepoint.com/sites/pilot',
                'Report Date': date, 'Anyone link count': count})
        return str(path)

    def test_portal_conflicts_keep_both_original_values_and_hashes(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = [self.report(Path(folder), 'one.csv', '2'), self.report(Path(folder), 'two.csv', '7')]
            scan = build_data_exposure_assessment(paths, [], evaluation_date='2026-09-15')
            facts = reconcile_observations(scan['observations'], evaluation_date='2026-09-15')
            self.assertEqual({r['value'] for r in facts}, {2, 7})
            self.assertEqual({r['selection'] for r in facts}, {'conflict'})
            self.assertEqual(len({r['source_hash'] for r in facts}), 2)

    def test_empty_measurement_differs_from_reported_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = self.report(Path(folder), 'missing.csv', '')
            zero = self.report(Path(folder), 'zero.csv', '0')
            values = []
            for path in (missing, zero):
                scan = build_data_exposure_assessment([path], [], evaluation_date='2026-09-15')
                values.append(scan['observations'][0])
            self.assertIsNone(values[0]['value'])
            self.assertEqual(values[0]['availability'], 'unknown')
            self.assertEqual(values[1]['value'], 0)
            self.assertEqual(values[1]['availability'], 'available')

    def test_pinned_evaluation_and_thresholds_control_portal_freshness(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.report(Path(folder), 'report.csv', '0')
            fresh = build_data_exposure_assessment([path], [], evaluation_date='2026-09-15', sam_max_age_days=6)
            stale = build_data_exposure_assessment([path], [], evaluation_date='2026-09-17', sam_max_age_days=6)
            future = build_data_exposure_assessment([path], [], evaluation_date='2026-09-09', sam_max_age_days=6)
            self.assertEqual(fresh['sources']['sam']['freshness'], 'fresh')
            self.assertEqual(stale['sources']['sam']['freshness'], 'stale')
            self.assertEqual(future['sources']['sam']['freshness'], 'unknown')

    def test_raw_purview_missing_zero_and_simulation_remain_distinct(self):
        self.assertEqual(assess_purview_configuration(SimpleNamespace(dlp_policies={'available': False})), [])
        self.assertEqual(assess_purview_configuration(SimpleNamespace(dlp_policies={'available': True})), [])
        zero = assess_purview_configuration(SimpleNamespace(dlp_policies={'available': True, 'policies': []}), '2026-09-10')
        self.assertEqual(len(zero), 1)
        self.assertIn('zero dlp policies', zero[0]['Observation'])
        self.assertEqual(zero[0]['ObservationDate'], '2026-09-10')
        simulation = assess_purview_configuration(SimpleNamespace(dlp_policies={'available': True, 'policies': [{'Mode': 'TestWithoutNotifications'}]}))
        self.assertIn('simulation mode', simulation[0]['Observation'])


if __name__ == '__main__':
    unittest.main()

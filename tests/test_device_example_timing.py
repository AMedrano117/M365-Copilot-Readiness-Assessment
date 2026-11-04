"""Presentation must not confuse report age, device activity or missing dates."""
from copy import deepcopy
import unittest

from Core.device_examples import example_timing, reported_time
from test_device_example_details import render
from test_device_summary_presentation import scenario
from test_evidence_reconciliation import DAY


class DeviceExampleTimingTests(unittest.TestCase):
    def test_current_device_can_have_stale_evidence_without_mutating_source(self):
        record = {'lastReportedDateTime': '2025-01-01T10:23:45Z'}
        observations = [{'record_relevance': 'CurrentActiveDevice',
                         'timestamp': {'state': 'valid', 'age_days': 60}}]
        before = deepcopy((record, observations))
        result = example_timing(record, {}, observations, [], 7)
        self.assertEqual((result['activity'], result['recency']), ('Current', 'Stale'))
        self.assertEqual(result['reported'], '2025-01-01 10:23:45 UTC')
        self.assertEqual((record, observations), before)

    def test_sync_fallback_does_not_invent_setting_report_time(self):
        settings = [{'record': {}, 'evidence': {'timestamp': {'state': 'valid', 'age_days': 1},
                     'date_basis': 'managed device last sync; setting report date not retained'}}]
        result = example_timing({}, {'compliance_activity': 'inactive'}, [], settings, 7)
        self.assertEqual(result['reported'], 'Not returned')
        self.assertEqual(result['recency'], 'Current')
        self.assertEqual(result['activity'], 'Inactive (Intune contact check)')
        self.assertIn('setting report time was not returned', result['date_basis'])

    def test_mixed_setting_ages_and_unresolved_identity_remain_explicit(self):
        settings = [{'record': {'lastReportedDateTime': date},
                     'evidence': {'timestamp': {'state': 'valid', 'age_days': age}}}
                    for date, age in [(DAY, 0), ('2025-01-01', 60)]]
        result = example_timing({}, {}, [{'identity_unresolved': True}], settings, 7)
        self.assertEqual(result['activity'], 'Unknown')
        self.assertEqual(result['recency'], 'Mixed: Current / Stale')
        self.assertIn('2025-01-01 (date only)', result['reported'])
        self.assertEqual(example_timing({}, {}, [], [], 7)['recency'], 'Undated')

    def test_timestamp_format_preserves_timezone_uncertainty(self):
        valid = {'state': 'valid'}
        self.assertEqual(reported_time('2026-10-01T10:00:00-05:00', valid), '2026-10-01 15:00:00 UTC')
        self.assertIn('timezone not retained', reported_time('2026-10-01T10:00:00', valid))
        self.assertEqual(reported_time('bad date', {'state': 'malformed'}), 'Invalid date; see workbook')

    def test_activity_and_unconfirmed_failure_examples_have_separate_time_columns(self):
        result, html = render(scenario())
        for kind in ('ActivityOrEnrollmentFailure', 'UnconfirmedSettingState'):
            row = next(row for row in result['recommendations'] if row.get('DeviceFindingType') == kind)
            fragment = html.split('data-finding-id="'+row['RecommendationId']+'"')[1].split('</details>')[0]
            for column in ('Device activity', 'Evidence recency', 'Reported time'):
                self.assertIn('<th scope="col">'+column+'</th>', fragment)


if __name__ == '__main__':
    unittest.main()

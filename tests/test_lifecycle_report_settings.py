"""Lifecycle age and date confirmations survive portable, offline replay."""

import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from Core.lifecycle_report_settings import lifecycle_settings
from Core.offline_collection import empty_service_results, load_collection, save_collection


class LifecycleSettingsTests(unittest.TestCase):
    def test_policy_precedence_keeps_saved_and_explicit_settings(self):
        with patch.dict(os.environ, {'LIFECYCLE_REPORT_MAX_AGE_DAYS': '120'}):
            self.assertEqual(lifecycle_settings()['lifecycle_report_max_age_days'], 120)
            self.assertEqual(lifecycle_settings({'lifecycle_report_max_age_days': 180})['lifecycle_report_max_age_days'], 180)
            self.assertEqual(lifecycle_settings({'lifecycle_report_max_age_days': 180}, max_age_days=90)['lifecycle_report_max_age_days'], 90)

    def test_invalid_age_limits_are_rejected(self):
        for value in (0, -1, 'never', ''):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'positive'):
                lifecycle_settings(max_age_days=value)

    def test_confirmation_is_bound_to_file_contents_and_keeps_existing_dates(self):
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / 'lifecycle = export.csv'
            report.write_text('Report data', encoding='utf-8')
            options = lifecycle_settings({'lifecycle_report_dates': {'prior-hash': '2026-08-01'}},
                report_dates=[f'{report}=2026-07-16'], evaluation_date='2026-09-15')
            digest = hashlib.sha256(report.read_bytes()).hexdigest()
            self.assertEqual(options['lifecycle_report_dates'][digest], '2026-07-16')
            self.assertEqual(options['lifecycle_report_dates']['prior-hash'], '2026-08-01')
            report.write_text('Replacement report data', encoding='utf-8')
            self.assertNotIn(hashlib.sha256(report.read_bytes()).hexdigest(), options['lifecycle_report_dates'])

    def test_invalid_date_confirmations_are_rejected(self):
        for value, message in (('missing.csv', 'PATH=YYYY-MM-DD'),
                               ('missing.csv=2026-14-01', 'YYYY-MM-DD'),
                               ('missing.csv=2026-09-16', 'later than'),
                               ('missing.csv=2026-07-16', 'existing file')):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, message):
                lifecycle_settings(report_dates=[value], evaluation_date='2026-09-15')

    def test_package_preserves_confirmations_without_changing_original_report(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            report = folder / 'lifecycle.csv'
            original = b'Original report bytes\n'
            report.write_bytes(original)
            options = lifecycle_settings(max_age_days=90, report_dates=[f'{report}=2026-07-16'])
            collection = save_collection(folder / 'collection.json', tenant_id='example', tenant_name='Example',
                service_results=empty_service_results(), assessment_settings=options,
                supplemental_inputs={'sam_report': [str(report)]})
            replay = load_collection(collection)
            self.assertEqual(replay['assessment_settings']['lifecycle_report_max_age_days'], 90)
            self.assertEqual(replay['assessment_settings']['lifecycle_report_dates'], options['lifecycle_report_dates'])
            copied = Path(replay['resolved_inputs']['sam_report'][0])
            self.assertEqual(copied.read_bytes(), original)
            self.assertEqual(report.read_bytes(), original)

    def test_shared_result_uses_lifecycle_age_policy_and_keeps_date_qualification(self):
        from Core.assessment_result import build_assessment_result
        finding = {
            'RecommendationId': 'LIFE-001', 'Service': 'Data Exposure', 'Feature': 'Review inactive sites',
            'FindingKey': 'data_exposure.lifecycle.inactive', 'Observation': 'Two exported sites were reported inactive.',
            'Recommendation': 'Confirm the content owners and review continued business need.',
            'Priority': 'Medium', 'Status': 'Action Required', 'Disposition': 'Action',
            'EvidenceKey': 'sharepoint_lifecycle_detail', 'EvidenceAvailable': 'Yes', 'EvidenceBasis': 'Tenant evidence',
            'SourceType': 'portal_export', 'SourceFile': 'lifecycle.csv', 'ObservationDate': '2026-07-16',
            'EvidenceScope': 'Exported sites only', 'EvidenceComplete': False, 'EvidenceMaxAgeDays': 90,
            'EvidenceDateBasis': 'Operator-confirmed report date',
        }
        result = build_assessment_result([finding], {}, evaluation_date='2026-09-15', expected_tenant_id='example')
        row = next(item for item in result['recommendations'] if item.get('FindingKey') == finding['FindingKey'])
        self.assertEqual(row['Freshness'], 'current')
        self.assertEqual(row['ObservationDate'], '2026-07-16')
        self.assertFalse(row['EvidenceComplete'])
        self.assertIn('Report date confirmed by the operator', row['Qualification'])
        self.assertIn('Completeness is not established', row['Qualification'])

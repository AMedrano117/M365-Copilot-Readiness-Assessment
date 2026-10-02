"""Organization settings retain explicit raw values across live hydration/replay."""

import asyncio
import unittest
from unittest.mock import patch

from Core.get_purview_client import hydrate_purview_client
from Core.offline_collection import _decode, _encode
from Core.saved_control_checks import qualify_saved_recommendations
from Recommendations.purview import CustomerLockboxA_Enterprise, LOCKBOX_ENTERPRISE


def hydrate(properties):
    with patch('builtins.print'):
        return hydrate_purview_client({'collected_at': '2026-09-30T12:00:00Z',
            'org_config': {'available': True, 'availability_status': 'available', 'data': properties}})


class PurviewOrgConfigProvenanceTests(unittest.TestCase):
    def test_false_is_retained_as_explicit_original_field_and_generically_exportable(self):
        for field, value in [('CustomerLockBoxEnabled', False), ('CustomerLockboxEnabled', 'False'), ('CUSTOMERLOCKBOXENABLED', False)]:
            with self.subTest(field=field):
                client = hydrate({field: value, 'AuditDisabled': False, 'UnrelatedProperty': 'not-needed'})
                self.assertIs(client.org_config['customer_lockbox_enabled'], False)
                self.assertEqual(client.org_config['raw_data'], {field: value, 'AuditDisabled': False})
                self.assertNotIn('UnrelatedProperty', client.org_config['raw_data'])
                replayed = _decode(_encode(client))
                row = qualify_saved_recommendations([{'Feature': 'Customer Lockbox', 'FindingKey': 'purview.customer_lockbox.state',
                    'Observation': 'Customer Lockbox is disabled.', 'Recommendation': 'Evaluate the intended requirement.'}], 'Purview', replayed)[0]
                declaration = row['InvestigationEvidence']
                self.assertEqual(declaration['kind'], 'configuration')
                self.assertEqual(declaration['records'][0]['Original Value'], value)
                self.assertEqual(declaration['records'][0]['Source Field'], 'org_config.raw_data.' + field)

    def test_missing_invalid_or_conflicting_settings_are_unknown_not_false(self):
        for properties in ({}, {'CustomerLockBoxEnabled': None}, {'CustomerLockBoxEnabled': 0},
                           {'CustomerLockBoxEnabled': 'unknown'}, {'CustomerLockBoxEnabled': True, 'CustomerLockboxEnabled': False}):
            with self.subTest(properties=properties):
                client = hydrate(properties)
                self.assertIsNone(client.org_config['customer_lockbox_enabled'])
                self.assertIsNone(client.org_config['audit_disabled'])
                for module in (CustomerLockboxA_Enterprise, LOCKBOX_ENTERPRISE):
                    records = asyncio.run(module.get_recommendation('SPE_E5', purview_client=client))
                    configuration = records[-1]
                    self.assertEqual(configuration['Status'], 'Not Assessed')
                    self.assertEqual(configuration['Disposition'], 'Coverage')
                    self.assertNotIn('licensed but disabled', configuration['Observation'])

    def test_both_lockbox_modules_read_normalized_true_without_wrong_case_default(self):
        client = hydrate({'CustomerLockboxEnabled': 'true', 'AuditDisabled': 'FALSE'})
        self.assertIs(client.org_config['customer_lockbox_enabled'], True)
        self.assertIs(client.org_config['audit_disabled'], False)
        for module in (CustomerLockboxA_Enterprise, LOCKBOX_ENTERPRISE):
            records = asyncio.run(module.get_recommendation('SPE_E5', purview_client=client))
            self.assertIn('ENABLED', records[-1]['Observation'])
            self.assertNotEqual(records[-1].get('Disposition'), 'Coverage')


if __name__ == '__main__':
    unittest.main()

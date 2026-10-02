"""Optional next steps use the universal evidence contract without guessed objects."""

from types import SimpleNamespace
import unittest

from Core.investigation_contract import materialize_declared_evidence
from Core.saved_control_checks import qualify_saved_recommendations


def lockbox_client(payload):
    return SimpleNamespace(org_config=payload, collected_at='2026-09-30T12:00:00Z',
        collection_status={'org_config': {'available': True, 'availability_status': 'available', 'records_collected': 1}})


def lockbox_row():
    return {'RecommendationId': 'TEST-LOCKBOX', 'Service': 'Purview', 'Feature': 'Customer Lockbox',
        'FindingKey': 'purview.customer_lockbox.state', 'EvidenceSource': 'purview_org_config',
        'Observation': 'Customer Lockbox is licensed but disabled; explicit customer approval is not required for eligible Microsoft support access requests',
        'Recommendation': 'Evaluate Customer Lockbox against the intended requirements.', 'Disposition': 'Opportunity'}


class OpportunityInvestigationEvidenceTests(unittest.TestCase):
    def test_passwordless_rollout_does_not_invent_complement_as_affected_users(self):
        source = SimpleNamespace(auth_methods_registration=[
            {'id': 'passwordless', 'methodsRegistered': ['fido2'], 'userType': 'member'},
            {'id': 'other', 'methodsRegistered': ['mobilePhone'], 'userType': 'guest'},
            {'id': 'unknown', 'userType': 'member'}],
            collection_status={'auth_methods': {'availability_status': 'available', 'pages_collected': 1, 'truncated': False}})
        original = {'RecommendationId': 'TEST-PASSWORDLESS', 'Feature': 'Passwordless adoption',
            'Observation': '1 of 3 returned users have a recognized passwordless method registered.',
            'Recommendation': 'Plan passwordless rollout.', 'Disposition': 'Opportunity', 'Status': 'Insight'}
        row = qualify_saved_recommendations([original], 'Entra', source)[0]
        declaration = row['InvestigationEvidence']
        self.assertEqual(declaration['kind'], 'planning')
        self.assertNotIn('records', declaration)
        self.assertIn('remaining 2 users are not established as affected', declaration['reason'])
        self.assertIn('Actual passwordless sign-in use was not collected', declaration['reason'])
        self.assertEqual(declaration['source']['population'], 3)
        self.assertEqual(declaration['source']['method_inventory_known'], 2)
        self.assertIn('members and guests', declaration['source']['scope'])
        result = materialize_declared_evidence({'sheets': {}}, [row])['TEST-PASSWORDLESS']
        self.assertEqual(result['InvestigationStatus'], 'Planning decision')
        self.assertEqual(result['InvestigationCount'], 0)

    def test_unknown_registration_method_inventory_has_precise_gap(self):
        source = SimpleNamespace(auth_methods_registration=[{'id': 'unknown'}], collection_status={'auth_methods': {'availability_status': 'partial'}})
        row = qualify_saved_recommendations([{'Observation': '10 users use passwordless authentication',
            'Recommendation': 'Plan rollout.', 'Disposition': 'Opportunity'}], 'Entra', source)[0]
        self.assertEqual(row['InvestigationEvidence']['kind'], 'unavailable')
        self.assertIn('method inventories are missing or unknown', row['InvestigationEvidence']['reason'])

    def test_lockbox_preserves_explicit_boolean_case_variants_and_original_value(self):
        for name, value, expected in [('CustomerLockBoxEnabled', False, False),
                                      ('CustomerLockboxEnabled', 'False', False),
                                      ('CUSTOMERLOCKBOXENABLED', True, True)]:
            with self.subTest(name=name):
                row = qualify_saved_recommendations([lockbox_row()], 'Purview', lockbox_client({name: value}))[0]
                declaration = row['InvestigationEvidence']
                self.assertEqual(declaration['kind'], 'configuration')
                record = declaration['records'][0]
                self.assertEqual(record['Source Field'], 'org_config.' + name)
                self.assertEqual(record['Original Value'], value)
                self.assertIs(record['Enabled'], expected)
                self.assertEqual(record['Cmdlet'], 'Get-OrganizationConfig')
                self.assertEqual(declaration['source']['collected_at'], '2026-09-30T12:00:00Z')
                bundle = {'sheets': {}}
                result = materialize_declared_evidence(bundle, [row])['TEST-LOCKBOX']
                self.assertEqual(result['InvestigationStatus'], 'Configuration evidence')
                self.assertEqual(result['InvestigationCount'], 1)
                self.assertTrue(result['InvestigationRange'].startswith("'Customer Lockbox Setting'!A2:"))

    def test_missing_or_ambiguous_legacy_false_cannot_be_a_measured_disabled_setting(self):
        for payload in ({'available': True}, {'available': True, 'customer_lockbox_enabled': False}, {'CustomerLockBoxEnabled': None}, {'CustomerLockBoxEnabled': 0}):
            with self.subTest(payload=payload):
                row = qualify_saved_recommendations([lockbox_row()], 'Purview', lockbox_client(payload))[0]
                self.assertEqual(row['InvestigationEvidence']['kind'], 'unavailable')
                self.assertFalse(row['EvidenceComplete'])
                self.assertNotIn('records', row['InvestigationEvidence'])
                self.assertNotIn('licensed but disabled', row['Observation'])
        legacy = qualify_saved_recommendations([lockbox_row()], 'Purview', lockbox_client({'customer_lockbox_enabled': False}))[0]
        self.assertIn('legacy collector also wrote this value', legacy['InvestigationEvidence']['reason'])

    def test_original_nested_false_is_retained_despite_legacy_normalized_false(self):
        payload = {'available': True, 'customer_lockbox_enabled': False, 'data': {'CustomerLockboxEnabled': False}}
        row = qualify_saved_recommendations([lockbox_row()], 'Purview', lockbox_client(payload))[0]
        self.assertEqual(row['InvestigationEvidence']['kind'], 'configuration')
        self.assertEqual(row['InvestigationEvidence']['records'][0]['Source Field'], 'org_config.data.CustomerLockboxEnabled')

    def test_conflicting_original_booleans_cannot_assert_enabled_or_disabled(self):
        source = lockbox_client({'CustomerLockBoxEnabled': True, 'data': {'CustomerLockboxEnabled': False}})
        row = qualify_saved_recommendations([lockbox_row()], 'Purview', source)[0]
        self.assertEqual(row['InvestigationEvidence']['kind'], 'unavailable')
        self.assertIn('fields disagree', row['Observation'])


if __name__ == '__main__':
    unittest.main()

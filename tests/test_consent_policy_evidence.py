"""Consent assignment findings retain only their actual policy conditions."""

from types import SimpleNamespace
import unittest

from Core.evidence_layer import _build_app_consent_policy_sheet
from Core.saved_control_checks import qualify_saved_recommendations


def client(assignments, definitions):
    return SimpleNamespace(
        authorization_policy={'defaultUserRolePermissions': {'permissionGrantPoliciesAssigned': assignments}},
        permission_grant_policies=definitions,
        collection_status={name: {'availability_status': 'available'} for name in ('authorization_policy', 'consent_policies')})


class ConsentPolicyEvidenceTests(unittest.TestCase):
    def test_each_assigned_condition_preserves_id_and_all_original_field_values(self):
        include = {'id': 'include-condition', 'clientApplicationsFromVerifiedPublisherOnly': True,
                   'clientApplicationIds': ['app-id'], 'clientApplicationPublisherIds': ['publisher-id'],
                   'clientApplicationTenantIds': ['tenant-id'], 'permissions': ['permission-id'],
                   'permissionClassification': 'low', 'permissionType': 'delegated', 'resourceApplication': 'resource-id',
                   'futureProviderField': {'allowed': False, 'values': ['exact', 'order']}}
        exclude = {'id': 'exclude-condition', 'permissions': ['excluded-permission'], 'resourceApplication': 'another-resource'}
        assignment = 'managePermissionGrantsForSelf.assigned-policy'
        source = client([assignment, 'managePermissionGrantsForOwnedResource.other-policy'], [
            {'id': 'assigned-policy', 'displayName': 'Assigned policy', 'description': 'Known conditions', 'includes': [include], 'excludes': [exclude]},
            {'id': 'not-assigned-policy', 'includes': [{'id': 'must-not-export'}], 'excludes': []},
            {'id': 'other-policy', 'includes': [], 'excludes': []}])
        sheet = _build_app_consent_policy_sheet(source)
        self.assertEqual(len(sheet['rows']), 5)
        self.assertEqual(list(sheet['rows'][3])[:7], ['RecommendationId', 'Flagged By', 'Setting', 'Value', 'Policy ID', 'Source State', 'Evidence Confidence'])
        conditions = sheet['rows'][3:]
        self.assertEqual([row['Condition ID'] for row in conditions], ['include-condition', 'exclude-condition'])
        self.assertEqual([row['Value'] for row in conditions], ['Include', 'Exclude'])
        self.assertTrue(all(row['Policy ID'] == 'assigned-policy' for row in conditions))
        self.assertEqual(conditions[0]['Assigned Policy IDs'], assignment)
        self.assertEqual({key.removeprefix('Condition.'): value for key, value in conditions[0].items() if key.startswith('Condition.')}, include)
        self.assertEqual({key.removeprefix('Condition.'): value for key, value in conditions[1].items() if key.startswith('Condition.')}, exclude)
        self.assertIn('2 retained include/exclude condition record(s)', sheet['details'][-1])
        self.assertTrue(any('does not imply unrestricted approval' in note for note in sheet['details']))

    def test_missing_definitions_or_condition_collections_are_explicit_without_fake_rows(self):
        source = client(['managePermissionGrantsForSelf.missing', 'managePermissionGrantsForSelf.returned'], [{'id': 'returned', 'includes': []}])
        sheet = _build_app_consent_policy_sheet(source)
        self.assertEqual(len(sheet['rows']), 3)
        self.assertIn('Definitions for 1 assigned policy ID(s) were not retained', sheet['details'][-1])
        self.assertIn('1 includes/excludes collection(s) were not retained', sheet['details'][-1])
        self.assertNotIn('Condition ID', sheet['rows'][-1])

    def test_unassigned_definitions_never_enable_default_user_consent(self):
        sheet = _build_app_consent_policy_sheet(client([], [{'id': 'unassigned', 'includes': [{'id': 'condition'}], 'excludes': []}]))
        self.assertEqual(len(sheet['rows']), 1)
        self.assertEqual(sheet['rows'][0]['Value'], 'No')

    def test_saved_overstatement_is_qualified_to_assignment_conditions(self):
        source = client(['managePermissionGrantsForSelf.assigned', 'managePermissionGrantsForOwnedResource.other'], [])
        original = {'Service': 'Entra', 'Feature': 'Consent', 'Observation': 'User consent enabled for applications, allowing users to grant apps access to Copilot-generated content and M365 data without admin review',
                    'EvidenceKey': 'app_consent_policy_detail; app_access_detail', 'Disposition': 'Action'}
        row = qualify_saved_recommendations([original], 'Entra', source)[0]
        self.assertEqual(row['EvidenceKey'], 'app_consent_policy_detail')
        self.assertIn('assigns 1 self-consent', row['Observation'])
        self.assertIn('include and exclude conditions', row['Observation'])
        self.assertNotIn('without admin review', row['Observation'])
        self.assertEqual(row['OriginalObservation'], original['Observation'])


if __name__ == '__main__':
    unittest.main()

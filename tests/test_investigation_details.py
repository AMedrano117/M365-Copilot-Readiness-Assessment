"""Only the items implicated by a callout belong in the investigation worklist."""

import unittest
from types import SimpleNamespace

from Core.evidence_layer import (
    _build_admin_role_sheet, _build_conditional_access_sheet,
    _build_external_connection_sheet, _build_identity_risk_sheet,
)
from Core.investigation_details import prepare_investigation_details


class InvestigationDetailsTests(unittest.TestCase):
    def build(self, key, rows, finding_key='', observation='Review the flagged items.', **fields):
        rec = {'RecommendationId': 'TEST-001', 'Feature': 'Review finding', 'EvidenceKey': key,
               'FindingKey': finding_key, 'Observation': observation, 'Disposition': 'Action', **fields}
        bundle = {'sheets': {key: {'title': 'Original Detail', 'rows': rows}},
                  'recommendations': [dict(rec)], 'evidence_index': [{'RecommendationId': 'TEST-001'}]}
        result = {'actions': [rec], 'recommendations': [dict(rec)]}
        prepare_investigation_details(bundle, result)
        return bundle, result

    def test_high_risk_device_callout_excludes_medium_and_healthy_inventory(self):
        rows = [{'Device ID': 'high', 'Device Name': 'Investigate', 'Risk Score': 'High', 'Last Seen': '2026-09-29'},
                {'Device ID': 'medium', 'Device Name': 'Other', 'Risk Score': 'Medium'},
                {'Device ID': 'healthy', 'Device Name': 'Healthy', 'Risk Score': 'None'}]
        bundle, result = self.build('defender_device_detail', rows, 'defender.devices.high_risk')
        items = bundle['sheets']['investigation_items']['rows']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['Identifier / URL'], 'high')
        self.assertEqual(items[0]['Why Review'], 'High device risk')
        self.assertEqual(result['actions'][0]['InvestigationRange'], "'Investigation Items'!A2:L2")
        self.assertEqual(result['recommendations'][0]['InvestigationCount'], 1)
        self.assertEqual(bundle['evidence_index'][0]['Workbook Tab'], "'Investigation Items'!A2:L2")

    def test_incidents_exclude_resolved_and_preserve_owner_and_incident_reference(self):
        rows = [{'Incident ID': 'active', 'Title': 'Investigate incident', 'Status': 'inProgress', 'Severity': 'high', 'Assigned To': 'SOC'},
                {'Incident ID': 'done', 'Title': 'Resolved', 'Status': 'resolved', 'Severity': 'high'}]
        bundle, _ = self.build('defender_incident_detail', rows, 'defender.incidents.current')
        items = bundle['sheets']['investigation_items']['rows']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['Identifier / URL'], 'active')
        self.assertIn('Assigned To: SOC', items[0]['Relevant Details'])

    def test_app_callout_selects_only_matching_flag_and_distinguishes_grant_counts(self):
        rows = [{'App Display Name': 'Privileged', 'Enterprise Application Object ID': 'p',
                 'Flagged Because': 'High-privilege delegated permissions', 'Granted Scopes': 'Mail.ReadWrite'},
                {'App Display Name': 'Unverified', 'Enterprise Application Object ID': 'u', 'Flagged Because': 'Unverified publisher'},
                {'App Display Name': 'Not assessed', 'Source State': 'unavailable'}]
        bundle, result = self.build('app_access_detail', rows, observation='Review high-privilege permissions.')
        items = bundle['sheets']['investigation_items']['rows']
        self.assertEqual([row['Item'] for row in items], ['Privileged'])
        self.assertIn('Mail.ReadWrite', items[0]['Relevant Details'])
        self.assertIn('grant instances', result['actions'][0]['InvestigationNote'])
        self.assertNotIn('Privileged', result['actions'][0]['InvestigationSummary'])

    def test_application_grants_exclude_apps_outside_recommended_population(self):
        rec = {'RecommendationId': 'APP-1', 'EvidenceKey': 'app_access_detail',
               'Observation': 'One high-privilege application needs review.', 'Recommendation': 'Review permissions.'}
        bundle = {'sheets': {
            'app_access_detail': {'title': 'App Access', 'rows': [
                {'App Display Name': 'Selected', 'Enterprise Application Object ID': 'app-one', 'Flagged Because': 'High-privilege'},
                {'App Display Name': 'Other', 'Enterprise Application Object ID': 'app-two', 'Flagged Because': 'Over-privileged'},
            ]},
            'application_grant_detail': {'title': 'Application Grant Detail', 'rows': [
                {'RecommendationId': '', 'Grant ID': 'grant-one', 'Client Service Principal ID': 'app-one'},
                {'RecommendationId': '', 'Grant ID': 'grant-other', 'Client Service Principal ID': 'app-two'},
                {'RecommendationId': '', 'Grant ID': 'grant-two', 'Client Service Principal ID': 'app-one'},
            ], 'details': []},
        }}
        result = {'actions': [rec], 'recommendations': [rec]}
        for _ in range(2):
            prepare_investigation_details(bundle, result)
            grants = bundle['sheets']['application_grant_detail']
            self.assertEqual([row['Grant ID'] for row in grants['rows']], ['grant-one', 'grant-two'])
            self.assertEqual({row['RecommendationId'] for row in grants['rows']}, {'APP-1'})
            item = bundle['sheets']['investigation_items']['rows'][0]
            self.assertEqual(item['Source Detail'], "'Application Grant Detail'!A2:C3")
            self.assertIn('2 retained delegated-grant records support 1 of 1', rec['InvestigationQualification'])

    def test_permanent_global_admins_exclude_eligible_and_other_roles(self):
        rows = [{'Principal Display Name': 'Standing admin', 'Principal ID': 'one', 'Assignment ID': 'a',
                 'Role Name': 'Global Administrator', 'Assignment Type': 'Permanent Active', 'Reason Flagged': 'Standing privileged assignment'},
                {'Principal Display Name': 'Eligible admin', 'Principal ID': 'two', 'Role Name': 'Global Administrator', 'Assignment Type': 'Eligible'},
                {'Principal Display Name': 'Other admin', 'Principal ID': 'three', 'Role Name': 'Reports Reader', 'Assignment Type': 'Permanent Active'}]
        bundle, _ = self.build('admin_role_detail', rows, observation='1 permanent global administrator requires review.')
        self.assertEqual([row['Identifier / URL'] for row in bundle['sheets']['investigation_items']['rows']], ['a'])

    def test_no_expiration_headline_includes_other_roles_and_excludes_unverified_duration(self):
        def assignment(identifier, role, principal):
            return {'id': identifier, 'principalId': principal, 'roleDefinitionId': role,
                    'principal': {'displayName': principal}, 'roleDefinition': {'displayName': role}}
        global_standing = assignment('standing-global', 'Global Administrator', 'global-user')
        sharepoint_standing = assignment('standing-sharepoint', 'SharePoint Administrator', 'sharepoint-user')
        for row in (global_standing, sharepoint_standing):
            row['status'] = 'Provisioned'
            row['scheduleInfo'] = {'startDateTime': '2026-08-01T00:00:00Z', 'expiration': {'type': 'noExpiration'}}
        client = SimpleNamespace(
            role_assignment_schedules=[global_standing, sharepoint_standing],
            role_assignments=[assignment('unverified-global', 'Global Administrator', 'other-user')],
            role_eligibility_schedules=[assignment('eligible-global', 'Global Administrator', 'eligible-user')])
        rows = _build_admin_role_sheet(client, '2026-09-15T12:00:00Z')['rows']
        # Exact population wording emitted by AAD_PREMIUM_P2.
        observation = ('2 active directory role assignment schedules have no expiration, '
                       'including 1 Global Administrator assignment')
        bundle, _ = self.build('admin_role_detail', rows, observation=observation)
        items = bundle['sheets']['investigation_items']['rows']
        self.assertEqual({row['Identifier / URL'] for row in items}, {'standing-global', 'standing-sharepoint'})
        self.assertTrue(all(row['Current State'] == 'Permanent Active' for row in items))

    def test_confirmed_compromised_headline_selects_only_confirmed_user_records(self):
        client = SimpleNamespace(
            collection_status={key: {'availability_status': 'available'}
                               for key in ('risky_users', 'risk_detections')},
            risky_users=[{'id': 'compromised', 'userPrincipalName': 'compromised@example.test',
                          'riskLevel': 'high', 'riskState': 'confirmedCompromised'},
                         {'id': 'medium', 'riskLevel': 'medium', 'riskState': 'atRisk'},
                         {'id': 'resolved', 'riskLevel': 'high', 'riskState': 'remediated'}],
            risk_detections=[{'id': 'detection', 'riskLevel': 'high', 'riskState': 'confirmedCompromised'}])
        rows = _build_identity_risk_sheet(client)['rows']
        # Exact population wording emitted by AAD_PREMIUM_IDENTITY_PROTECTION.
        observation = ('1 confirmed compromised user(s) detected - immediate security incident '
                       'requiring Copilot access revocation')
        bundle, _ = self.build('identity_risk_detail', rows, observation=observation)
        items = bundle['sheets']['investigation_items']['rows']
        self.assertEqual([row['Identifier / URL'] for row in items], ['compromised'])
        self.assertEqual(items[0]['Item Type'], 'Risky User')

    def test_plural_compromised_users_excludes_other_risk_states(self):
        rows = [{'Record Type': 'Risky User', 'Object ID': identifier,
                 'Risk State': 'confirmedCompromised', 'Risk Level': 'high'} for identifier in ('one', 'two')]
        rows.append({'Record Type': 'Risky User', 'Object ID': 'at-risk',
                     'Risk State': 'atRisk', 'Risk Level': 'high'})
        bundle, _ = self.build('identity_risk_detail', rows,
                               observation='2 confirmed compromised users detected.')
        self.assertEqual([row['Identifier / URL'] for row in bundle['sheets']['investigation_items']['rows']], ['one', 'two'])

    def test_missing_mfa_control_does_not_label_every_policy_an_offender(self):
        rows = [{'Policy Name': 'Compliant device', 'Policy ID': 'c', 'State': 'enabled', 'Requires MFA': 'No',
                 'Targets All Apps': 'Yes', 'Reason Flagged': 'No MFA grant control'},
                {'Policy Name': 'MFA test', 'Policy ID': 'm', 'State': 'enabledForReportingButNotEnforced',
                 'Requires MFA': 'Yes', 'Targets All Apps': 'Yes'}]
        bundle, _ = self.build('conditional_access_detail', rows, 'baseline.identity.sign_in', observation='No enforced Conditional Access policy requires MFA for the target users.')
        self.assertEqual([row['Identifier / URL'] for row in bundle['sheets']['investigation_items']['rows']], ['m'])

    def test_group_scoped_mfa_and_m365_targets_use_normalized_policy_records(self):
        def policy(identifier, state='enabled', apps=None, users=None):
            return {'id': identifier, 'displayName': identifier, 'state': state,
                    'conditions': {'users': users or {'includeGroups': ['pilot-group']},
                                   'applications': {'includeApplications': apps or ['All']}},
                    'grantControls': {'builtInControls': ['mfa']}}
        policies = [policy('group-all-apps'),
                    policy('group-m365', apps=['00000003-0000-0ff1-ce00-000000000000']),
                    policy('m365-report-only', state='enabledForReportingButNotEnforced',
                           apps=['Office365'], users={'includeUsers': ['All']}),
                    policy('healthy-all-users', users={'includeUsers': ['All']}),
                    policy('unrelated-app', apps=['unrelated-app'])]
        rows = _build_conditional_access_sheet(SimpleNamespace(ca_policies=policies))['rows']
        bundle, _ = self.build('conditional_access_detail', rows, 'baseline.identity.sign_in',
                               observation='No enforced Conditional Access policy requires MFA for all users and cloud apps. '
                                           '2 enforced MFA policies target specific users or groups only.')
        items = bundle['sheets']['investigation_items']['rows']
        self.assertEqual({row['Identifier / URL'] for row in items},
                         {'group-all-apps', 'group-m365', 'm365-report-only'})
        self.assertIn('Include Groups: pilot-group', next(row for row in items
                      if row['Identifier / URL'] == 'group-m365')['Relevant Details'])

    def test_empty_connector_inventory_never_becomes_an_investigation_item(self):
        rows = _build_external_connection_sheet(SimpleNamespace(
            external_connections=[], collection_status={'external_connections': {'availability_status': 'available'}}))['rows']
        bundle, result = self.build('external_connection_detail', rows, 'baseline.connectors.inventory')
        self.assertEqual(result['actions'][0]['InvestigationCount'], 0)
        self.assertNotIn('investigation_items', bundle['sheets'])

    def test_missing_configuration_and_aggregate_rows_do_not_create_fake_items(self):
        bundle, result = self.build('authentication_detail', [{'Metric': 'MFA users', 'Value': 40}])
        self.assertNotIn('investigation_items', bundle['sheets'])
        self.assertEqual(result['actions'][0]['InvestigationCount'], 0)
        self.assertIn('No specific affected items', result['actions'][0]['InvestigationNote'])

    def test_exposure_callout_selects_matching_signals_and_skips_summary(self):
        rows = [{'Detail Type': 'Source Summary', 'Risk Signals': 'Anyone links', 'Signal Count': 30},
                {'Detail Type': 'Risk Evidence', 'Site URL': 'https://example.test/anyone', 'Risk Signals': 'Anyone links', 'Signal Count': 3, 'Anyone links Count': 3},
                {'Detail Type': 'Risk Evidence', 'Site URL': 'https://example.test/external', 'Risk Signals': 'External exposure', 'Signal Count': 20}]
        bundle, _ = self.build('data_exposure_detail', rows, 'data_exposure.anonymous_links')
        items = bundle['sheets']['investigation_items']['rows']
        self.assertEqual([row['Identifier / URL'] for row in items], ['https://example.test/anyone'])
        self.assertIn('Anyone links: 3', items[0]['Relevant Details'])

    def test_simulation_dlp_callout_excludes_enforced_policies_and_labels(self):
        rows = [{'Object Type': 'DLP Policy', 'Name': 'Simulation', 'Object ID': 's', 'Enabled': 'Yes', 'Mode': 'TestWithoutNotifications'},
                {'Object Type': 'DLP Policy', 'Name': 'Enforced', 'Object ID': 'e', 'Enabled': 'Yes', 'Mode': 'Enable'},
                {'Object Type': 'Sensitivity Label', 'Name': 'Disabled label', 'Enabled': 'No'}]
        bundle, _ = self.build('purview_policy_detail', rows, 'purview.dlp.simulation_only', observation='DLP remains in simulation.')
        self.assertEqual([row['Identifier / URL'] for row in bundle['sheets']['investigation_items']['rows']], ['s'])

    def test_repeated_preparation_does_not_duplicate_items_or_change_range(self):
        bundle, result = self.build('defender_incident_detail', [{'Incident ID': 'one', 'Status': 'active'}])
        expected = result['actions'][0]['InvestigationRange']
        prepare_investigation_details(bundle, result)
        self.assertEqual(result['actions'][0]['InvestigationRange'], expected)
        self.assertEqual(len(bundle['sheets']['investigation_items']['rows']), 1)

    def test_historical_claim_never_attaches_current_objects(self):
        bundle, result = self.build('defender_incident_detail', [{'Incident ID': 'today', 'Status': 'active'}], Historical='Yes')
        self.assertNotIn('investigation_items', bundle['sheets'])
        self.assertIn('Historical callout', result['actions'][0]['InvestigationNote'])


if __name__ == '__main__':
    unittest.main()

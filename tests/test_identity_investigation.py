"""Retained identity records must match the specific investigation population."""

import unittest
from types import SimpleNamespace

from Core.evidence_layer import (
    _apply_explicit_evidence_fallbacks, _build_access_review_sheet,
    _build_conditional_access_sheet, _build_guest_access_sheet,
)
from Core.identity_investigation import (
    build_intune_device_investigation, qualify_identity_recommendation,
    select_identity_item,
)
from Core.tenant_baseline import assess_identity_baseline


def client(**fields):
    return SimpleNamespace(collection_status={key: {'availability_status': 'available', 'pages_collected': 1, 'truncated': False}
        for key in ('managed_devices', 'risky_users', 'ca_policies', 'access_reviews', 'guest_users')}, **fields)


class IdentityInvestigationTests(unittest.TestCase):
    def test_device_rows_exclude_unknown_error_and_grace_period_and_preserve_ids(self):
        devices = [dict(id=state, deviceName=state, complianceState=state,
                        azureADDeviceId='entra-' + state, userId='user-' + state,
                        lastSyncDateTime='2026-09-01T12:00:00Z')
                   for state in ('compliant', 'noncompliant', 'unknown', 'error', 'inGracePeriod')]
        source = client(managed_devices=devices, device_summary={'non_compliant': 1})
        report = build_intune_device_investigation(source)
        self.assertEqual(len(report['rows']), 1)
        self.assertEqual(report['rows'][0]['Entra Device ID'], 'entra-noncompliant')
        self.assertEqual(report['rows'][0]['User ID'], 'user-noncompliant')
        self.assertIn('1 explicitly noncompliant + 1 compliant + 3 other or unknown = 5', report['reconciliation_note'])
        rec = {'Observation': '1 of 5 managed devices (80.0%) are non-compliant - may access Copilot despite policy violations'}
        qualified = qualify_identity_recommendation(rec, source)
        self.assertIn('(20.0%)', qualified['Observation'])
        self.assertIn('does not establish Copilot use', qualified['Observation'])
        self.assertEqual(select_identity_item('entra_device_detail', report['rows'][0], qualified)['Identifier / URL'], 'noncompliant')
        routed = _apply_explicit_evidence_fallbacks([rec], {'entra_device_detail': report})
        self.assertEqual(routed[0]['EvidenceKey'], 'entra_device_detail')
        self.assertIsNone(select_identity_item('entra_device_detail', report['rows'][0], {'Observation': 'No Conditional Access policy requires compliant devices.'}))

    def test_device_sdk_attributes_and_missing_source_are_qualified(self):
        report = build_intune_device_investigation(client(managed_devices=[SimpleNamespace(
            id='device', device_name='Device', compliance_state='noncompliant', azure_ad_device_id='directory')]))
        self.assertEqual(report['rows'][0]['Entra Device ID'], 'directory')
        missing = build_intune_device_investigation(SimpleNamespace(device_summary={'non_compliant': 4}))
        self.assertEqual(missing['rows'], [])
        self.assertIn('did not retain managed_devices', missing['unavailability_reason'])
        self.assertIn('Saved summary mismatch', missing['reconciliation_note'])

    def test_guest_licensed_only_preserves_account_and_sku_without_selecting_tenant_policy(self):
        source = client(guest_users=[{'id': 'licensed', 'userPrincipalName': 'guest@example.test',
            'assignedLicenses': [{'skuId': 'sku', 'disabledPlans': ['disabled-plan']}],
            'accountEnabled': True, 'externalUserState': 'Accepted'}, {'id': 'unlicensed', 'assignedLicenses': []}],
            b2b_summary={'guest_invite_restrictions': 'everyone'})
        rows = _build_guest_access_sheet(source)['rows']
        rec = {'Observation': '1 of 2 guest users have an M365 license; this does not by itself prove Copilot entitlement or inappropriate data access'}
        selected = [item for row in rows if (item := select_identity_item('guest_access_detail', row, rec))]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['Identifier / URL'], 'licensed')
        self.assertIn('Assigned License SKU IDs: sku', selected[0]['Relevant Details'])
        self.assertIn('disabled-plan', selected[0]['Relevant Details'])
        self.assertTrue(all(select_identity_item('guest_access_detail', row, {'Observation': 'Guest invitations are broadly allowed'}) is None for row in rows))

    def test_access_review_recurrence_uses_nested_pattern_and_retains_definition_id(self):
        source = client(access_reviews=[
            {'id': 'recurring', 'displayName': 'Recurring review', 'scope': {'query': '/groups/id/members'},
             'settings': {'recurrence': {'pattern': {'type': 'absoluteMonthly', 'interval': 3}, 'range': {'type': 'noEnd', 'startDate': '2026-01-01'}}}},
            {'id': 'once', 'displayName': 'One-time review', 'settings': {'recurrence': None}},
            {'id': 'unknown', 'displayName': 'Unknown schedule'}])
        rows = _build_access_review_sheet(source)['rows']
        by_id = {row['Review ID']: row for row in rows}
        self.assertEqual(by_id['recurring']['Is Recurring'], 'Yes')
        self.assertEqual(str(by_id['recurring']['Recurrence Interval']), '3')
        self.assertEqual(by_id['unknown']['Is Recurring'], 'Unknown')
        self.assertEqual(by_id['unknown']['Review Type'], 'Scope not classified')
        rec = {'Observation': '3 access review(s) configured, but none are recurring - Copilot access not continuously governed'}
        selected = [item for row in rows if (item := select_identity_item('access_review_detail', row, rec))]
        self.assertEqual([item['Identifier / URL'] for item in selected], ['once'])
        self.assertTrue(all(select_identity_item('access_review_detail', row, {'Observation': 'No group membership reviews are configured'}) is None for row in rows))

    def test_ca_auth_strength_and_report_only_legacy_policy_selected_for_actual_baseline(self):
        policies = [
            {'id': 'strength', 'displayName': 'Scoped strength', 'state': 'enabled',
             'conditions': {'applications': {'includeApplications': ['Office365'], 'excludeApplications': ['excluded-app']},
                            'users': {'includeGroups': ['group'], 'includeRoles': ['role'], 'excludeRoles': ['excluded-role']}},
             'grantControls': {'operator': 'AND', 'authenticationStrength': {'id': 'strength-id', 'displayName': 'Phishing resistant'}}},
            {'id': 'legacy', 'displayName': 'Report legacy', 'state': 'enabledForReportingButNotEnforced',
             'conditions': {'applications': {'includeApplications': ['All']}, 'users': {'includeUsers': ['All']}, 'clientAppTypes': ['other']},
             'grantControls': {'builtInControls': ['block']}},
            {'id': 'healthy', 'displayName': 'Unrelated enforced block', 'state': 'enabled',
             'conditions': {'applications': {'includeApplications': ['unrelated']}, 'users': {'includeUsers': ['All']}, 'clientAppTypes': ['browser']},
             'grantControls': {'builtInControls': ['block']}}]
        source = client(ca_policies=policies, security_defaults={'available': True, 'is_enabled': False})
        rec = assess_identity_baseline(source)[0]
        sheet = _build_conditional_access_sheet(source)
        selected = [item for row in sheet['rows'] if (item := select_identity_item('conditional_access_detail', row, rec))]
        self.assertEqual({item['Identifier / URL'] for item in selected}, {'strength', 'legacy'})
        strength = next(item for item in selected if item['Identifier / URL'] == 'strength')
        self.assertIn('Authentication Strength ID: strength-id', strength['Relevant Details'])
        self.assertIn('Exclude Roles: excluded-role', strength['Relevant Details'])
        self.assertIn('Exclude Applications: excluded-app', strength['Relevant Details'])
        defaults = next(row for row in sheet['rows'] if row['Object Type'] == 'Security Defaults')
        self.assertEqual(defaults['Security Defaults Enabled'], 'No')
        self.assertEqual(len(sheet['rows']), 4)
        self.assertEqual(next(row for row in sheet['rows'] if row['Policy ID'] == 'healthy')['Blocks Legacy Auth'], 'No')

    def test_legacy_only_gap_does_not_select_healthy_mfa_or_unrelated_disabled_policy(self):
        rec = {'FindingKey': 'baseline.identity.sign_in', 'Observation': 'No enforced policy blocks legacy authentication for all users.'}
        row = {'Policy ID': 'healthy-mfa', 'Requires MFA': 'Yes', 'Targets All Apps': 'Yes', 'State': 'enabled', 'Include Users': 'All'}
        self.assertIsNone(select_identity_item('conditional_access_detail', row, rec))
        self.assertIsNone(select_identity_item('conditional_access_detail', dict(row, State='disabled', **{'Requires MFA': 'No'}), rec))

    def test_false_zero_risk_claim_reports_raw_populations_and_retains_low_risk_context(self):
        source = client(risky_users=[{'id': 'low', 'riskLevel': 'low', 'riskState': 'atRisk'},
                                    {'id': 'resolved', 'riskLevel': 'none', 'riskState': 'remediated'}])
        rec = qualify_identity_recommendation({'Observation': 'No risky users detected, Identity Protection is actively monitoring and protecting accounts'}, source)
        self.assertIn('0 high/medium-risk user(s)', rec['Observation'])
        self.assertIn('1 low-risk user(s) with state atRisk', rec['Observation'])
        self.assertEqual(rec['ReportedRiskyUserCount'], 1)
        self.assertEqual(rec['Disposition'], 'Action')
        self.assertEqual(rec['EvidenceKey'], 'identity_risk_detail')


if __name__ == '__main__':
    unittest.main()

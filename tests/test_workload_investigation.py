import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from Core.data_exposure_assessment import build_data_exposure_assessment
from Core.evidence_layer import (
    _build_data_exposure_sheet, _build_defender_incident_sheet,
    _build_power_platform_sheet, _build_purview_policy_sheet,
)
from Core.power_platform_inventory import build_inventory_client
from Core.workload_investigation import select_workload_item


class WorkloadInvestigationTests(unittest.TestCase):
    def test_disabled_dlp_rule_retains_id_full_conditions_actions_scope_and_is_selected(self):
        client = SimpleNamespace(
            dlp_policies={'available': True, 'policies': [{
                'Identity': 'policy-id', 'Name': 'Restricted scope', 'Enabled': True, 'Mode': 'Enable',
                'SharePointLocation': ['https://example.invalid/site'],
                'SharePointLocationException': ['https://example.invalid/except'],
                'Locations': '[{"Location":"CopilotExperiences","Included":["group-id"]}]',
                'EnforcementPlanes': ['CopilotExperiences'],
            }]},
            dlp_rules={'available': True, 'rules': [{
                'Identity': 'rule-id', 'Name': 'Disabled rule', 'Disabled': True,
                'ParentPolicyName': 'Restricted scope', 'ParentPolicyId': 'policy-id',
                'ContentContainsSensitiveInformation': [{'name': 'Credit Card', 'minCount': 2, 'minConfidence': 85}],
                'AccessScope': 'NotInOrganization', 'BlockAccess': True,
                'RestrictAccess': [{'setting': 'ExcludeContentProcessing', 'value': 'Block'}],
                'WhenChangedUTC': '2026-09-29T08:30:00Z',
            }, {'Identity': 'enabled-id', 'Name': 'Enabled rule', 'Disabled': False}]},
            collection_status={'dlp_rules': {'availability_status': 'available', 'source_file': 'cache.json',
                                           'collected_at': '2026-09-30T08:00:00Z'}},
        )
        sheet = _build_purview_policy_sheet(client)
        rule = next(row for row in sheet['rows'] if row.get('Object ID') == 'rule-id')
        self.assertEqual(rule['Parent Policy ID'], 'policy-id')
        self.assertIn('minCount: 2', rule['Conditions'])
        self.assertIn('minConfidence: 85', rule['Conditions'])
        self.assertIn('NotInOrganization', rule['Conditions'])
        self.assertIn('ExcludeContentProcessing', rule['Actions'])
        self.assertEqual(rule['Modified UTC'], '2026-09-29T08:30:00Z')
        self.assertEqual(rule['Source File'], 'cache.json')
        policy = next(row for row in sheet['rows'] if row.get('Object ID') == 'policy-id')
        self.assertIn('https://example.invalid/except', policy['Excluded Locations'])
        self.assertIn('group-id', policy['Included Locations'])
        self.assertNotIn('{', policy['Included Locations'])
        rec = {'FindingKey': 'purview.dlp.no_enabled_protection', 'Feature': 'DLP enforcement'}
        selected = [select_workload_item('purview_policy_detail', row, rec) for row in sheet['rows']]
        self.assertEqual([row['Identifier / URL'] for row in selected if row], ['rule-id'])

    def test_high_severity_incident_action_excludes_low_and_resolved_records(self):
        rows = [{'id': 'high-active', 'severity': 'high', 'status': 'active', 'title': 'High',
                 'createdDateTime': '2026-09-29T08:00:00Z', 'lastUpdateDateTime': '2026-09-30T08:00:00Z',
                 'alerts': [{'id': 'alert-id'}]},
                {'id': 'low-active', 'severity': 'low', 'status': 'active'},
                {'id': 'high-resolved', 'severity': 'high', 'status': 'resolved'}]
        client = SimpleNamespace(security_incidents=rows, incident_summary={'total': 3, 'active': 2, 'high_severity': 1},
                                 collection_status={'incidents': {'availability_status': 'available', 'truncated': False}})
        detail = _build_defender_incident_sheet(client)['rows']
        rec = {'Observation': '1 high-severity incident', 'Recommendation': 'Investigate 1 high-severity incident'}
        selected = [select_workload_item('defender_incident_detail', row, rec) for row in detail]
        self.assertEqual([row['Identifier / URL'] for row in selected if row], ['high-active'])
        self.assertEqual(next(row for row in detail if row['Incident ID'] == 'high-active')['Alert IDs'], 'alert-id')
        all_active = {'FindingKey': 'defender.incidents.current', 'Observation': '2 active incidents including 1 high-severity'}
        self.assertEqual(sum(bool(select_workload_item('defender_incident_detail', row, all_active)) for row in detail), 2)

    def test_power_platform_resource_identifiers_survive_and_only_failed_flow_is_selected(self):
        client = build_inventory_client([
            {'id': 'stopped-flow', 'name': 'Flow', 'type': 'microsoft.powerautomate/flows',
             'environmentId': 'environment-id', 'ownerId': 'owner-id', 'state': 'Stopped',
             'createdTime': '2026-09-28T08:00:00Z'},
            {'id': 'healthy-flow', 'name': 'Flow', 'type': 'microsoft.powerautomate/flows', 'state': 'Started'},
            {'id': 'agent-id', 'name': 'Agent', 'type': 'microsoft.copilotstudio/agents',
             'environmentId': 'environment-id', 'ownerId': 'agent-owner', 'state': 'Stopped'},
        ], 'Synthetic inventory', '2026-09-30')
        client.power_platform_inventory['source_file'] = 'inventory.csv'
        rows = _build_power_platform_sheet(client)['rows']
        rec = {'Feature': 'Flow operations', 'Observation': '1 stopped flow requires attention'}
        selected = [select_workload_item('power_platform_detail', row, rec) for row in rows]
        self.assertEqual([row['Identifier / URL'] for row in selected if row], ['stopped-flow'])
        flow = next(row for row in rows if row.get('Resource ID') == 'stopped-flow')
        self.assertEqual(flow['Environment ID'], 'environment-id')
        self.assertEqual(flow['Owner ID'], 'owner-id')
        self.assertEqual(flow['Created UTC'], '2026-09-28T08:00:00Z')
        self.assertEqual(flow['Source File'], 'inventory.csv')
        agent = next(row for row in rows if row.get('Resource ID') == 'agent-id')
        self.assertEqual(agent['Owner ID'], 'agent-owner')

    def test_exposure_preserves_per_signal_counts_and_permission_fields_for_exact_callout(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'exposure.csv'
            source = {'Report Date': '2026-09-30', 'Site URL': 'https://example.invalid/site',
                      'Item URL': 'https://example.invalid/item', 'Item ID': 'item-id',
                      'Anyone link count': 2, 'Everyone permission count': 5,
                      'Permission Recipient': 'Everyone', 'Role definition': 'Edit',
                      'Permission ID': 'permission-id', 'Link ID': 'link-id', 'Link Type': 'Anyone'}
            with path.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.DictWriter(handle, fieldnames=source)
                writer.writeheader()
                writer.writerow(source)
            assessment = build_data_exposure_assessment([str(path)], [], evaluation_date='2026-09-30')
        rows = _build_data_exposure_sheet(assessment)['rows']
        detail = next(row for row in rows if row['Detail Type'] == 'Risk Evidence')
        self.assertEqual(detail['Anyone links Count'], 2)
        self.assertEqual(detail['Everyone permissions Count'], 5)
        self.assertEqual(detail['Signal Count'], 7)
        self.assertEqual(detail['Permission ID'], 'permission-id')
        self.assertEqual(detail['Permission Role'], 'Edit')
        self.assertEqual(detail['Item ID'], 'item-id')
        self.assertEqual(detail['Source Record'], 1)
        item = select_workload_item('data_exposure_detail', detail, {'FindingKey': 'data_exposure.anonymous_links'})
        self.assertIn('Anyone links: 2', item['Relevant Details'])
        self.assertNotIn('Signal Count', item['Relevant Details'])
        self.assertNotIn('Everyone permissions: 5', item['Relevant Details'])

    def test_distinct_permissions_on_same_item_are_retained_without_collapsing_roles(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'permissions.csv'
            base = {'Report Date': '2026-09-30', 'Site URL': 'https://example.invalid/site',
                    'Item URL': 'https://example.invalid/item', 'Recipient': 'Everyone',
                    'Role definition': 'Read', 'Permission ID': 'permission-1'}
            with path.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.DictWriter(handle, fieldnames=base)
                writer.writeheader()
                writer.writerows([base, {**base, 'Permission ID': 'permission-2', 'Role definition': 'Edit'}])
            assessment = build_data_exposure_assessment([str(path)], [], evaluation_date='2026-09-30')
        self.assertEqual({row['Permission ID'] for row in assessment['evidence_rows']}, {'permission-1', 'permission-2'})


if __name__ == '__main__':
    unittest.main()

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile

from Core.ai_usage import collect_copilot_usage, collect_license_coverage
from Core.copilot_activity_summary import summarize_activity
from Core.copilot_admin_review import COPILOT_LOCATION, build_admin_review, subscription_capacity, summarize_copilot_dlp
from Core.customer_report import render_customer_report
from tests.test_ai_usage import FakeClient, FakeResponse, summary_payload
from tests.test_customer_progress_render import result


def activity(**values):
    return {'userPrincipalName': 'private@example.invalid', 'reportRefreshDate': '2026-09-14', 'reportPeriod': 28,
            'promptsSubmittedForAllApps': '10', 'promptsSubmittedForCopilotChatWork': '4',
            'promptsSubmittedForCopilotChatWeb': '0', 'activeUsageDaysForAllApps': '2', **values}


def policy_client(mode='Enable', **overrides):
    policy = {'Name': 'Sensitive content', 'Mode': mode, 'Locations': json.dumps([
        {'Workload': 'Applications', 'Location': COPILOT_LOCATION,
         'Inclusions': [{'Type': 'Tenant', 'Identity': 'All'}], 'Exclusions': []}]),
        'EnforcementPlanes': ['CopilotExperiences'], **overrides}
    rule = {'Name': 'Block rule', 'ParentPolicyName': policy['Name'], 'Disabled': False,
            'RestrictAccess': [{'setting': 'ExcludeContentProcessing', 'value': 'Block'}]}
    return SimpleNamespace(dlp_policies={'available': True, 'policies': [policy]},
                           dlp_rules={'available': True, 'rules': [rule]}, collected_at='2026-09-14T12:00:00Z')


class ActivitySummaryTests(unittest.TestCase):
    def test_zero_counts_are_kept_without_retaining_identities(self):
        value = summarize_activity([activity(), activity(userPrincipalName='second@example.invalid')])
        self.assertEqual(value['total_prompts'], 20)
        self.assertEqual(value['web_chat_prompts'], 0)
        self.assertEqual(value['active_user_days'], 4)
        self.assertNotIn('@', json.dumps(value))

    def test_partial_invalid_or_missing_counts_do_not_become_complete_totals(self):
        for missing in (None, '', True, -1, '1.5', 'NaN'):
            value = summarize_activity([activity(), activity(userPrincipalName='b', promptsSubmittedForAllApps=missing)])
            self.assertIsNone(value['total_prompts'])
            self.assertEqual(value['work_chat_prompts'], 8)
            self.assertEqual(value['availability_status'], 'partial')

    def test_duplicate_users_mixed_dates_periods_and_paging_failures_are_not_summed(self):
        cases = [[activity(), activity()], [activity(userPrincipalName='')],
                 [activity(), activity(userPrincipalName='b', reportRefreshDate='2026-09-13')],
                 [activity(reportPeriod=7)], [activity(reportRefreshDate=None)]]
        for rows in cases:
            self.assertFalse(summarize_activity(rows)['available'])
        self.assertIsNone(summarize_activity([activity()], error='Next page failed')['total_prompts'])

    def test_nested_period_is_used_without_adding_other_periods(self):
        row = activity()
        row['copilotActivityUserDetailsByPeriod'] = [{'reportPeriod': 7, 'promptsSubmittedForAllApps': 999},
                                                   {'reportPeriod': 28, 'promptsSubmittedForAllApps': 12}]
        self.assertEqual(summarize_activity([row])['total_prompts'], 12)

    def test_default_collector_reads_and_discards_user_detail_after_aggregation(self):
        responses = [FakeResponse(payload=summary_payload(period, 2, 1)) for period in ('D7', 'D28', 'D90', 'D180')]
        responses.extend([FakeResponse(payload={'value': []}), FakeResponse(payload={'value': [activity()]})])
        client = FakeClient(responses)
        value = asyncio.run(collect_copilot_usage(client))
        self.assertEqual(value['engagement_summary']['total_prompts'], 10)
        self.assertEqual(value['user_detail'], [])
        self.assertNotIn('private@example.invalid', json.dumps(value))
        self.assertIn('UsageUserDetail', client.paths[-1][0])

    def test_paged_usage_aggregates_all_pages_and_rejects_other_hosts(self):
        for link in ("https://graph.microsoft.com/v1.0/copilot/reports/next", "https://invalid.example/next"):
            responses = [FakeResponse(payload=summary_payload(period, 2, 1)) for period in ('D7', 'D28', 'D90', 'D180')]
            responses.extend([FakeResponse(payload={'value': []}), FakeResponse(payload={'value': [activity()], '@odata.nextLink': link}),
                              FakeResponse(payload={'value': [activity(userPrincipalName='b')]})])
            client = FakeClient(responses)
            value = asyncio.run(collect_copilot_usage(client))
            expected = 20 if 'graph.microsoft.com' in link else None
            self.assertEqual(value['engagement_summary']['total_prompts'], expected)
            self.assertFalse(any('invalid.example' in path for path, _ in client.paths))

    def test_documented_csv_headings_and_pretty_json_have_equal_aggregates(self):
        csv_text = ('Report Refresh Date,User Principal Name,Report Period,Prompts submitted for all apps,'
                    'Prompts submitted for Copilot Chat (work),Prompts submitted for Copilot Chat (web),'
                    'Active Usage Days for all apps\n2026-09-14,private@example.invalid,28,10,4,0,2\n')
        json_payload = {'value': [activity()]}
        results = []
        for response in (FakeResponse(text=csv_text), FakeResponse(payload=json_payload, text=json.dumps(json_payload, indent=2))):
            responses = [FakeResponse(payload=summary_payload(period, 2, 1)) for period in ('D7', 'D28', 'D90', 'D180')]
            responses.extend([FakeResponse(payload={'value': []}), response])
            results.append(asyncio.run(collect_copilot_usage(FakeClient(responses)))['engagement_summary'])
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0]['total_prompts'], 10)

    def test_failed_or_malformed_next_page_does_not_publish_partial_totals(self):
        for response in (FakeResponse(status_code=403), FakeResponse(payload={'unexpected': []})):
            responses = [FakeResponse(payload=summary_payload(period, 2, 1)) for period in ('D7', 'D28', 'D90', 'D180')]
            responses.extend([FakeResponse(payload={'value': []}),
                              FakeResponse(payload={'value': [activity()], '@odata.nextLink': 'https://graph.microsoft.com/v1.0/copilot/reports/next'}), response])
            value = asyncio.run(collect_copilot_usage(FakeClient(responses)))
            self.assertIsNone(value['engagement_summary']['total_prompts'])
            self.assertEqual(value['user_detail'], [])


class CopilotPolicyTests(unittest.TestCase):
    def test_monitoring_and_enforced_rule_configuration_are_distinct(self):
        self.assertEqual(summarize_copilot_dlp(policy_client('TestWithNotifications'))['rows'][0]['Mode'], 'Monitoring only')
        self.assertEqual(summarize_copilot_dlp(policy_client())['rows'][0]['Mode'], 'Blocking action configured')
        self.assertEqual(summarize_copilot_dlp(policy_client(Enabled=False))['rows'][0]['Mode'], 'Disabled')

    def test_name_match_cannot_prove_target_or_enforcement(self):
        client = policy_client(Name='Copilot old policy', Locations=None, EnforcementPlanes=None)
        value = summarize_copilot_dlp(client)['rows'][0]
        self.assertEqual(value['Target evidence'], 'Policy name only; target unverified')
        self.assertNotEqual(value['Mode'], 'Blocking action configured')

    def test_wrong_parent_unknown_disabled_and_incomplete_reads_do_not_prove_blocking(self):
        for change in ({'ParentPolicyName': 'Different'}, {'Disabled': None}, {'Disabled': True}, {'RestrictAccess': None}):
            client = policy_client()
            client.dlp_rules['rules'][0].update(change)
            self.assertNotEqual(summarize_copilot_dlp(client)['rows'][0]['Mode'], 'Blocking action configured')
        client = policy_client()
        client.collection_status = {'dlp_rules': {'availability_status': 'partial'}}
        self.assertNotEqual(summarize_copilot_dlp(client)['rows'][0]['Mode'], 'Blocking action configured')

    def test_subscription_capacity_is_separate_from_people_and_missing_is_unknown(self):
        catalog = [{'skuId': 'paid', 'prepaidUnits': {'enabled': 6}, 'consumedUnits': 5}]
        self.assertEqual(subscription_capacity(catalog, {'paid'}, complete=True)['enabled_seats'], 6)
        self.assertFalse(subscription_capacity(catalog, {'paid'}, complete=False)['available'])
        self.assertFalse(subscription_capacity(catalog * 2, {'paid'}, complete=True)['available'])
        self.assertFalse(subscription_capacity([{'skuId': 'paid', 'prepaidUnits': None}], {'paid'}, complete=True)['available'])
        self.assertEqual(subscription_capacity([], {'paid'}, complete=True)['enabled_seats'], 0)

    def test_default_license_collector_preserves_capacity_in_saved_payload(self):
        catalog = {'value': [{'skuId': 'paid', 'prepaidUnits': {'enabled': 6}, 'consumedUnits': 5,
                             'servicePlans': [{'servicePlanName': 'M365_COPILOT_APPS'}]}]}
        value = asyncio.run(collect_license_coverage(FakeClient([FakeResponse(payload={'value': []}), FakeResponse(payload=catalog)])))
        self.assertEqual(value['copilot_subscription_capacity']['assigned_seats'], 5)

    def test_context_uses_original_dates_and_does_not_mutate_or_close_controls(self):
        client = policy_client('TestWithoutNotifications', DisplayName='<script>no()</script>')
        before = copy.deepcopy(vars(client))
        data = build_admin_review(SimpleNamespace(), client, {'collected_at': '2026-09-15T12:00:00Z'})
        self.assertEqual(data['dlp']['rows'][0]['Evidence date'], '2026-09-14T12:00:00Z')
        self.assertEqual(vars(client), before)
        assessment = result()
        assessment['domains'].append({'id': 'data_protection', 'title': 'Data protection', 'actions': []})
        assessment['domains'].append({'id': 'adoption', 'title': 'Adoption', 'actions': []})
        original = copy.deepcopy(assessment)
        html = render_customer_report(assessment, {'copilot_admin_review': data}, 'Example')
        self.assertIn('Monitoring only', html)
        self.assertIn('&lt;script&gt;no()&lt;/script&gt;', html)
        self.assertNotIn('<script>no()</script>', html)
        self.assertIn('Portal details that still need a review', html)
        self.assertIn('>Not established</td>', html)
        self.assertIn('This saved collection has no prompt-activity aggregates.', html)
        self.assertEqual(assessment, original)

    def test_legacy_user_detail_without_v2_metrics_does_not_imply_zero_activity(self):
        legacy = {'userPrincipalName': 'legacy@example.invalid', 'reportRefreshDate': '2026-09-14',
                  'reportPeriod': 28, 'lastActivityDate': '2026-09-13'}
        summary = summarize_activity([legacy])
        self.assertFalse(summary['available'])
        self.assertEqual(summary['availability_status'], 'unavailable')
        self.assertIsNone(summary['total_prompts'])
        self.assertIsNone(summary['active_user_days'])

    def test_collection_replay_preserves_new_context_and_source_dates(self):
        from Core.offline_collection import empty_service_results, load_collection, save_collection
        services = empty_service_results()
        client = SimpleNamespace(copilot_usage={'engagement_summary': summarize_activity([activity()])})
        services['m365_result'] = ({'_client': client}, [])
        services['purview_info'] = {'_client': policy_client()}
        expected = build_admin_review(client, services['purview_info']['_client'])
        with tempfile.TemporaryDirectory() as folder:
            path = save_collection(Path(folder) / 'collection.json', tenant_id='11111111-1111-1111-1111-111111111111',
                                   tenant_name='Fictional', service_results=services)
            loaded = load_collection(path)
            actual = build_admin_review(loaded['service_results']['m365_result'][0]['_client'],
                                        loaded['service_results']['purview_info']['_client'])
            self.assertEqual(actual, expected)


if __name__ == '__main__':
    unittest.main()

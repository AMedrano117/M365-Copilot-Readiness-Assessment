import copy
import unittest

from Core.evidence_requests import build_evidence_requests, sharepoint_next_step


class EvidenceRequestTests(unittest.TestCase):
    def test_incomplete_rules_remain_a_collection_request_when_policies_were_read(self):
        review = {'rows': [], 'dlp': {'available': True, 'complete': False, 'rules_complete': False}}
        requests = build_evidence_requests(review, context={'enabled_collectors': ['Purview']})
        self.assertEqual(len(requests), 1)
        self.assertIn('DLP rules', requests[0]['Missing evidence'])

    def test_only_missing_fields_are_requested_without_treating_zero_as_missing(self):
        review = {'rows': [
            {'Topic': 'Paid Copilot prompts', 'Value': 0, 'Source': 'Microsoft Graph Copilot usage v2'},
            {'Topic': 'Paid Copilot work-chat prompts', 'Value': None, 'Source': 'Microsoft Graph Copilot usage v2'},
        ]}
        before = copy.deepcopy(review)
        requests = build_evidence_requests(review)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['Missing evidence'], 'Paid Copilot work-chat prompts')
        self.assertIn('retry', requests[0]['Next step'])
        self.assertEqual(review, before)

    def test_completed_configuration_read_without_copilot_policy_is_not_a_download_gap(self):
        review = {'rows': [{'Topic': 'Copilot-specific DLP configuration', 'Value': None,
                            'Source': 'Purview DLP policies and rules'}], 'dlp': {'available': True}}
        self.assertEqual(build_evidence_requests(review), [])

    def test_unselected_collectors_do_not_create_live_retry_requests(self):
        review = {'rows': [{'Topic': 'Paid Copilot prompts', 'Value': None,
                            'Source': 'Microsoft Graph Copilot usage v2'}]}
        self.assertEqual(build_evidence_requests(review, context={'enabled_collectors': ['Entra']}), [])

    def test_fresh_complete_results_are_not_requested_again(self):
        scan = {'files_loaded': 1, 'freshness': 'fresh', 'coverage': {}, 'errors': []}
        self.assertEqual(build_evidence_requests({'rows': []}, {'sources': {'sam': scan, 'dspm': scan}}), [])
        partial = {**scan, 'coverage': {'domains_missing': ['Unlabeled sensitive items']}}
        request = build_evidence_requests({'rows': []}, {'sources': {'dspm': partial}})[0]
        self.assertEqual(request['Missing evidence'], 'Unlabeled sensitive items')

    def test_sharepoint_completed_report_routes_to_download_not_new_scan(self):
        step = sharepoint_next_step({}, {'dag_reports': {'available': True, 'reports': [{'Status': 'Completed'}]}})
        self.assertIn('automatically', step)
        self.assertIn('Completed status alone', step)
        self.assertNotIn('generate', step)

    def test_sharepoint_running_or_restricted_results_have_different_next_steps(self):
        running = sharepoint_next_step({}, {'dag_reports': {'available': True, 'reports': [{'Status': 'Running'}]}})
        self.assertIn('Do not start duplicate scans', running)
        restricted = sharepoint_next_step({}, context={'permission_profile': 'restricted'})
        self.assertIn('--reports-dir', restricted)
        self.assertNotIn('sign-in', restricted)

    def test_missing_workload_remains_an_explicit_export_request(self):
        scan = {'files_loaded': 1, 'freshness': 'fresh', 'coverage': {'workloads_missing': ['OneDrive']}}
        row = build_evidence_requests({'rows': []}, {'sources': {'sam': scan}})[0]
        self.assertIn('OneDrive workload coverage', row['Missing evidence'])


if __name__ == '__main__':
    unittest.main()

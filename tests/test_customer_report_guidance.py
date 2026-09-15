"""Report guidance must target actual gaps and preserve the assessment evidence."""

import copy
import re
import unittest

from Core.customer_report import _observation, render_customer_report


def assessment(keys):
    rows = [dict(RecommendationId=f'GAP-{index}', FindingKey=key, Feature=key,
                 ActionType='Evidence', Priority='Medium', Observation='Scope is unverified.',
                 OwnerRole='Assessment owner', CompletionEvidence='A dated scoped review.')
            for index, key in enumerate(keys, 1)]
    return dict(actions=rows, coverage=rows, recommendations=rows, domains=[],
                counts={'evidence_gaps': len(rows)}, decision='Readiness unconfirmed')


class CustomerReportGuidanceTests(unittest.TestCase):
    def test_related_gaps_share_guides_and_all_navigation_targets_exist(self):
        result = assessment(['sharepoint.dag.not_assessed', 'data_exposure.freshness.sam',
                             'data_exposure.snapshot_scope',
                             'data_exposure.coverage.sensitive-data_exposure_evidence'])
        before = copy.deepcopy(result)
        html = render_customer_report(result, {}, 'Example tenant', 'evidence.xlsx')
        self.assertEqual(html.count('id="report-guide-sharepoint"'), 1)
        self.assertEqual(html.count('id="report-guide-dspm"'), 1)
        self.assertEqual(html.count('href="#report-guide-sharepoint"'), 3)
        self.assertEqual(html.count('href="#report-guide-dspm"'), 1)
        ids = set(re.findall(r'\bid="([^"]+)"', html))
        self.assertTrue(all(anchor in ids for anchor in re.findall(r'href="#([^"]+)"', html)))
        for index in range(1, 5):
            self.assertIn(f'Open action {index}</a>', html)
        self.assertIn('Purview data-risk export compatibility still needs validation', html)
        self.assertEqual(result, before)

    def test_owner_reviews_do_not_request_unrelated_portal_reports(self):
        result = assessment(['coverage.adoption.baseline', 'coverage.license.assignment',
                             'coverage.data.retention'])
        result['adoption_metrics'] = [{'metric_kind': 'summary', 'label': 'Active users', 'value': 3}]
        html = render_customer_report(result, {}, 'Example tenant')
        self.assertNotIn('id="report-guide-', html)
        self.assertNotIn('id="report-handoff"', html)
        self.assertIn('Reuse the available usage evidence', html)
        self.assertIn('Match the agreed pilot roster', html)
        self.assertIn('A usage export alone does not establish this agreement', html)
        self.assertIn('Agreed retention requirements', html)
        self.assertNotIn('No measured Copilot usage was supplied', html)

    def test_lifecycle_confirmation_does_not_request_an_unrelated_scan(self):
        result = assessment(['data_exposure.ownerless_sites'])
        result['actions'][0].update(ActionTitle='Confirm remediation of Ownerless SharePoint sites',
                                    ActionType='Confirmation')
        html = render_customer_report(result, {}, 'Example tenant')
        self.assertIn('Confirm and assign site owners', html)
        self.assertNotIn('Confirm remediation of Ownerless', html)
        self.assertNotIn('id="report-guide-', html)

    def test_sharepoint_import_clarification_requires_real_permission_rows(self):
        row = {'FindingKey': 'sharepoint.dag.not_assessed', 'Observation': 'Missing report coverage.'}
        for report in ({'report_type': 'content_management_assessment', 'records_read': 3},
                       {'report_type': 'permission_snapshot', 'records_read': 0}):
            evidence = {'data_exposure': {'sources': {'sam': {'reports': [report]}}}}
            self.assertEqual(_observation(row, evidence), row['Observation'])
        evidence = {'data_exposure': {'sources': {'sam': {'reports': [
            {'report_type': 'permission_snapshot', 'records_read': 3}]}}}}
        self.assertIn('Permission exports were also supplied', _observation(row, evidence))

    def test_guidance_preserves_html_escaping_and_unknown_findings(self):
        result = assessment(['new.unknown.check'])
        result['actions'][0].update(Feature='<script>bad()</script>',
                                    CompletionEvidence='<img src=x onerror=bad()>')
        html = render_customer_report(result, {}, 'Example tenant')
        self.assertIn('&lt;script&gt;bad()&lt;/script&gt;', html)
        self.assertIn('&lt;img src=x onerror=bad()&gt;', html)
        self.assertNotIn('<img src=x', html)
        self.assertNotIn('id="report-guide-', html)


if __name__ == '__main__':
    unittest.main()

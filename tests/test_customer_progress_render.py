"""Keep rollout navigation and visual evidence separate from assessment findings."""

import copy
import unittest

from Core.customer_report import render_customer_report


def result():
    action = {'RecommendationId': 'CHECK-1', 'Feature': 'Review scoped evidence',
              'ActionType': 'Evidence', 'Priority': 'Medium', 'Observation': 'Scope is not established.'}
    return {'decision': 'Readiness unconfirmed', 'rationale': 'Review the required pilot evidence.',
            'actions': [action], 'recommendations': [action], 'coverage': [action],
            'counts': {'actions': 1, 'evidence_gaps': 1},
            'domains': [{'id': 'adoption', 'title': 'Pilot suitability and adoption',
                         'summary': 'Review the pilot population.', 'actions': [action]}],
            'rollout_progress': {'current_stage': 'Preparing for pilot',
                'current_stage_id': 'preparing', 'qualification': 'Stages describe verified readiness.',
                'stages': [{'id': key, 'label': label, 'status': status, 'requirements': [
                    {'title': 'A scoped review', 'status': 'open', 'reason': 'Owner review needed.',
                     'action_ids': ['CHECK-1']}]} for key, label, status in [
                    ('started', 'Just started', 'complete'), ('preparing', 'Preparing for pilot', 'current'),
                    ('pilot', 'Ready for pilot', 'pending'), ('broader', 'Ready for broader adoption', 'pending')]]}}


class CustomerProgressRenderTests(unittest.TestCase):
    def test_stage_is_prominent_with_assessment_status_and_action_links_retained(self):
        assessment = result()
        original = copy.deepcopy(assessment)
        html = render_customer_report(assessment, {}, 'Example')
        self.assertIn('<h1>Preparing for pilot</h1>', html)
        self.assertIn('Assessment status: Readiness unconfirmed', html)
        self.assertEqual(html.count('aria-current="step"'), 1)
        self.assertIn('href="#readiness-requirements"', html)
        self.assertIn('id="readiness-requirements"', html)
        self.assertIn('href="#action-1">Action 1</a>', html)
        self.assertEqual(html.count('<article class="action"'), 1)
        self.assertEqual(assessment, original)

    def test_portal_context_embeds_local_previews_without_adding_scored_actions(self):
        assessment = result()
        bundle = {'portal_review': {'captures': [{'id': 'usage', 'domain_id': 'adoption',
            'title': '<Usage capture>', 'summary': 'Portal shows 1 active user.',
            'source_name': 'usage.pdf', 'source_data_uri': 'data:application/pdf;base64,JVBERi0=',
            'previews': [{'data_uri': 'data:image/png;base64,iVBORw0KGgo='}],
            'limitations': 'A screenshot is limited to its visible scope.',
            'review_notes': ['Compare the reporting periods.'],
            'coverage': [{'area': '<script>bad()</script>', 'assessment_coverage': 'Usage collected',
                          'next_step': 'Confirm the pilot scope'}]}]}}
        before = copy.deepcopy((assessment, bundle))
        html = render_customer_report(assessment, bundle, 'Example')
        self.assertEqual(html.count('id="portal-usage"'), 1)
        self.assertIn('src="data:image/png;base64,iVBORw0KGgo="', html)
        self.assertIn('download="usage.pdf"', html)
        self.assertIn('&lt;script&gt;bad()&lt;/script&gt;', html)
        self.assertNotIn('<script>bad()', html)
        self.assertEqual(html.count('<article class="action"'), 1)
        self.assertEqual((assessment, bundle), before)

    def test_unvalidated_remote_or_executable_media_is_never_rendered(self):
        bundle = {'portal_review': {'captures': [{'id': 'bad', 'domain_id': 'adoption',
            'source_data_uri': 'javascript:alert(1)',
            'previews': [{'data_uri': 'https://example.com/customer.png'},
                         {'data_uri': 'data:image/svg+xml;base64,PHN2Zz4='}]}]}}
        html = render_customer_report(result(), bundle, 'Example')
        self.assertNotIn('javascript:alert', html)
        self.assertNotIn('https://example.com/customer.png', html)
        self.assertNotIn('data:image/svg+xml', html)

    def test_compatible_reassessment_retains_source_methodology_in_appendix(self):
        bundle = {'collection_context': {'methodology_migration': {
            'from': '2.0.0', 'to': '2.1.0', 'reason': 'Updated reviewed rollout criteria.'}}}
        html = render_customer_report(result(), bundle, 'Example')
        self.assertIn('Saved collection methodology: 2.0.0', html)
        self.assertIn('Reassessed using methodology 2.1.0', html)
        self.assertIn('Original collection evidence and dates are preserved.', html)


if __name__ == '__main__':
    unittest.main()

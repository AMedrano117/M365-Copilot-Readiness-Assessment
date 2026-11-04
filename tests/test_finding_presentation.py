"""Consistent, lossless presentation across findings without changing decisions."""
from copy import deepcopy
import re
import unittest

from Core.finding_presentation import statements, render_details
from Core.customer_report import prose, table, render_customer_report


class FindingTextTests(unittest.TestCase):
    def test_parallel_counts_become_rows_without_splitting_thousands(self):
        text = '1,234 registered users, 20 unregistered users, 5 users with unknown status. Registration does not establish enforcement.'
        html = render_details(text, prose, table)
        self.assertIn('>1,234</td>', html)
        self.assertIn('>registered users</td>', html)
        self.assertIn('>users with unknown status</td>', html)
        self.assertIn('Registration does not establish enforcement.', html)
        self.assertEqual(html.count('<tr>'), 4)

    def test_labeled_facts_keep_company_name_comma(self):
        html = render_details('Provider: Contoso, Inc., Status: unknown, Scope: tenant.', prose, table)
        self.assertIn('>Contoso, Inc.</td>', html)
        self.assertIn('>unknown</td>', html)
        self.assertIn('>Scope</td>', html)

    def test_normal_prose_and_negative_qualifiers_keep_their_context(self):
        text = 'No failure was confirmed, but reporting is incomplete.'
        self.assertEqual(statements(text), [text])
        self.assertIn(text, render_details(text, prose, table))

    def test_nested_outcome_labels_do_not_lose_their_population_context(self):
        text = 'MFA outcomes: Satisfied: 5, Unknown: 3.'
        self.assertEqual(statements(text), [text])
        html = render_details(text, prose, table)
        self.assertIn('<caption>MFA outcomes</caption>', html)
        self.assertIn('>Satisfied</td>', html)
        self.assertIn('>Unknown</td>', html)

    def test_parallel_assignment_counts_keep_their_total_and_population(self):
        html = render_details('10 semantic assignments: 6 active, 3 eligible, 1 unknown. Populations may overlap.', prose, table)
        self.assertIn('<caption>10 semantic assignments</caption>', html)
        for state in ['active', 'eligible', 'unknown']:
            self.assertIn('>'+state+'</td>', html)
        self.assertIn('Populations may overlap.', html)

    def test_quoted_parenthetical_and_url_delimiters_are_not_split(self):
        text = 'Policy "Review; verify" (owner: unknown; scope: tenant). Review https://example.invalid/policy;a=1,b=2 first.'
        items = statements(text)
        self.assertEqual(len(items), 2)
        self.assertIn('"Review; verify" (owner: unknown; scope: tenant)', items[0])
        self.assertIn('https://example.invalid/policy;a=1,b=2', items[1])

    def test_versions_abbreviations_and_sequential_counts_survive(self):
        text = 'Dr. Smith reviewed version 1.2.3. 1,234 records are retained. Evidence remains unverified.'
        items = statements(text)
        self.assertEqual(items[0], 'Dr. Smith reviewed version 1.2.3.')
        self.assertEqual(items[1], '1,234 records are retained.')
        self.assertEqual(items[2], 'Evidence remains unverified.')

    def test_steps_are_ordered_and_single_steps_remain_concise(self):
        html = render_details('Review scope; Verify the policy. Record the result.', prose, table, steps=True)
        self.assertIn('<ol class="finding-steps">', html)
        self.assertEqual(html.count('<li>'), 3)
        self.assertEqual(render_details('Review the policy.', prose, table, steps=True), '<p>Review the policy.</p>')

    def test_empty_details_remain_unknown_instead_of_becoming_zero_or_pass(self):
        self.assertIn('Not established', render_details(None, prose, table))

    def test_html_is_escaped_in_lists_and_tables(self):
        html = render_details('State: <script>alert(1)</script>; Scope: <img src=x onerror=bad()>', prose, table)
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img ', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('&lt;img ', html)


class FindingLayoutTests(unittest.TestCase):
    def fixture(self):
        domains = ['identity', 'content', 'classification', 'governance', 'applications', 'licensing', 'external_ai']
        actions, groups, recommendations = [], [], []
        for index, domain in enumerate(domains):
            row = {'RecommendationId': 'ACTION-'+str(index), 'FindingKey': 'fictional.'+domain,
                   'Feature': 'Review '+domain, 'ActionType': 'Remediation', 'Priority': 'High',
                   'Domain': domain, 'OwnerRole': 'Responsible administrator', 'ReadinessStage': 'Tenant readiness review',
                   'Observation': '12 confirmed records, 3 records requiring confirmation. Scope remains unverified.',
                   'Recommendation': 'Review the named policy. Confirm its scope; Record the verification result.',
                   'CompletionEvidence': 'A dated review. Confirmation of scope.', 'Qualification': 'Evidence is incomplete. No absence claim is established.'}
            supporting = dict(row, RecommendationId='SUPPORT-'+str(index), Feature='Supporting '+domain,
                              ActionType='Confirmation', Disposition='Opportunity')
            actions.append(row); recommendations.extend([row, supporting])
            groups.append({'id': domain, 'title': domain, 'status': 'Review needed', 'actions': [row], 'customer_findings': [row, supporting]})
        return {'actions': actions, 'recommendations': recommendations, 'domains': groups,
                'counts': {'remediation': 7}, 'decision': 'Tenant remediation required', 'assessment_scope': 'tenant_readiness'}

    def test_all_domains_and_supporting_findings_use_the_shared_layout(self):
        result = self.fixture(); before = deepcopy(result)
        html = render_customer_report(result, {}, 'Fictional tenant')
        self.assertEqual(html.count('<strong>What we found.</strong>'), 14)
        self.assertEqual(html.count('<strong>What to do.</strong>'), 14)
        self.assertGreaterEqual(html.count('<ol class="finding-steps">'), 14)
        self.assertIn('>confirmed records</td>', html)
        self.assertIn('>records requiring confirmation</td>', html)
        self.assertIn('<li>Scope remains unverified.</li>', html)
        self.assertIn('<li>No absence claim is established.</li>', html)
        self.assertEqual(result, before)

    def test_action_details_keep_completion_qualifications_priority_and_navigation(self):
        html = render_customer_report(self.fixture(), {}, 'Fictional tenant')
        action = html.split('id="action-1"')[1].split('</article>')[0]
        self.assertIn('<li>A dated review.</li>', action)
        self.assertIn('<li>Confirmation of scope.</li>', action)
        self.assertIn('data-priority="high"', action)
        self.assertIn('Evidence qualifications.', action)
        self.assertIn('Responsible administrator', action)
        ids = set(re.findall(r'\bid="([^"]+)"', html))
        action_links = [anchor for anchor in re.findall(r'href="#([^"]+)"', html) if re.fullmatch(r'action-\d+', anchor)]
        self.assertEqual(len(set(action_links)), 7)
        self.assertTrue(all(anchor in ids for anchor in action_links))

    def test_rendering_does_not_publish_raw_markup_from_a_finding(self):
        result = self.fixture()
        result['actions'][0]['Observation'] = 'Scope: <script>bad()</script>; Status: unknown'
        html = render_customer_report(result, {}, 'Fictional tenant')
        self.assertIn('&lt;script&gt;bad()&lt;/script&gt;', html)
        self.assertNotIn('<script>bad()', html)


if __name__ == '__main__':
    unittest.main()

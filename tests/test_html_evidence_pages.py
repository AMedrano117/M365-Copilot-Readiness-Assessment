"""Linked technical evidence pages keep every record reachable while the main report stays aggregate-only."""

import os
from pathlib import Path
import re
import tempfile
import unittest
from urllib.parse import unquote

from Core.customer_report import slug as report_slug
from Core.html_evidence_pages import context_page_name, page_name, write_html_evidence_pages
from tests.test_finding_evidence import build, event, legacy_finding, source


class EvidencePageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        logs = [event(number, code=[0, 53003, 50126][number % 3]) for number in range(1, 60)]
        logs.append(event(60, userDisplayName='<script>alert(1)</script>'))
        self.payload, self.model = build([legacy_finding()], {'signin_logs': [{'records': logs, 'source': source()}]})
        self.folder = Path(self.directory.name) / 'report_evidence'
        self.pages = write_html_evidence_pages(self.model, self.folder, report_name='report.html', page_rows=25)

    def test_pagination_keeps_every_record_and_navigation_resolves(self):
        names = self.pages['pages']['ENT-018']
        self.assertEqual(names, [page_name('ENT-018', number) for number in (1, 2, 3)])
        rows = []
        for name in names:
            source_text = (self.folder / name).read_text(encoding='utf-8')
            rows.extend(re.findall(r'<tr id="(det-[^"]+)"', source_text))
            for target in re.findall(r'href="([^"#:]+\.html)', source_text):
                if not target.startswith('../'):
                    self.assertTrue((self.folder / unquote(target)).exists(), target)
            self.assertIn('href="../report.html#evidence-ent-018"', source_text)
        evidence = self.model['findings'][0]['evidence']
        self.assertEqual(len(rows), evidence['record_count'])
        self.assertEqual(len(set(rows)), len(rows))
        index = (self.folder / 'index.html').read_text(encoding='utf-8')
        self.assertIn(names[0], index)
        self.assertIn('noindex', index)

    def test_values_are_escaped_and_the_page_explains_units_and_fix(self):
        text = ''.join((self.folder / name).read_text(encoding='utf-8') for name in self.pages['pages']['ENT-018'])
        self.assertNotIn('<script>alert(1)</script>', text)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', text)
        self.assertIn('sign-in events', text)
        self.assertIn('3 accounts', text)
        self.assertIn('Technical fix', text)
        self.assertIn('https://learn.microsoft.com/en-us/entra/identity/conditional-access/policy-block-legacy-authentication', text)
        self.assertIn('Confidential technical evidence', text)

    def test_existing_folder_is_not_overwritten(self):
        with self.assertRaises(FileExistsError):
            write_html_evidence_pages(self.model, self.folder, report_name='report.html')

    def test_long_page_identifiers_keep_links_and_anchors_with_short_distinct_names(self):
        common = 'long_finding_identifier_' * 15
        identifiers = [common + 'first', common + 'second']
        _, model = build([legacy_finding(identifier) for identifier in identifiers],
                         {'signin_logs': [{'records': [event(number) for number in range(1, 5)], 'source': source()}]})
        folder = Path(self.directory.name) / 'Evidence'
        result = write_html_evidence_pages(model, folder, report_name='AI Readiness and M365 Hardening.html', page_rows=2)
        self.assertNotEqual(result['pages'][identifiers[0]][0], result['pages'][identifiers[1]][0])
        projected_root = Path(__file__).resolve().parents[1] / 'Reports' / ('Customer' + 'x' * 32) / '2026-10-06' / 'Builds' / '1' / 'Evidence'
        for identifier in identifiers:
            for name in result['pages'][identifier]:
                self.assertLessEqual(len(name), 60)
                self.assertNotIn('_', name)
                self.assertLessEqual(len(str(projected_root / name)), 255)
                text = (folder / name).read_text(encoding='utf-8')
                self.assertIn('#evidence-' + report_slug(identifier), text)
                for target in re.findall(r'href="([^"#:]+\.html)', text):
                    if not target.startswith('../'):
                        self.assertTrue((folder / unquote(target)).exists(), target)
        for number in (1, 2, 10000):
            self.assertLessEqual(len(context_page_name(common + 'first', number)), 60)
            self.assertNotEqual(context_page_name(common + 'first', number), context_page_name(common + 'second', number))


class MainReportLinkTests(unittest.TestCase):
    def test_main_report_links_to_pages_without_named_records(self):
        from Core.assessment_result import build_assessment_result
        from Core.export_recommendations import export_to_html
        logs = [event(number) for number in range(1, 6)]
        row = legacy_finding('ENT-018', Status='Action Required', ControlId='IDENTITY.AUTH', ObservationDate='2026-09-28')
        bundle = {'assessment_sources': {'signin_logs': [{'records': logs, 'source': source()}]}, 'sheets': {}}
        result = build_assessment_result([row], bundle, evaluation_date='2026-09-30', expected_tenant_id='11111111-1111-4111-8111-111111111111')
        bundle['assessment_result'] = result
        _, model = build(result['recommendations'], bundle['assessment_sources'])
        bundle.update(finding_evidence=model, html_evidence_folder='report_evidence')
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            os.chdir(directory)
            try:
                html = Path(export_to_html(result['recommendations'], filename='report.html', evidence_bundle=bundle)).read_text(encoding='utf-8')
            finally:
                os.chdir(cwd)
        identifier = result['recommendations'][0]['RecommendationId']
        self.assertIn('report_evidence/' + page_name(identifier), html)
        self.assertIn('sign-in events', html)
        self.assertNotIn('user1@example.invalid', html)
        self.assertNotIn('192.0.2.10', html)
        self.assertIsNone(re.search(r'[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}', html, re.I))
        action_plan = html.split('id="action-plan"', 1)[1].split('</section>', 1)[0]
        self.assertNotIn(identifier, action_plan)
        self.assertIn('Technical steps', action_plan)


if __name__ == '__main__':
    unittest.main()

"""SharePoint snapshot and activity reports are described plainly in the deliverables.

Expectations are declared here, before the implementation, from Microsoft's
definitions: snapshot reports show state as of the date the report was
generated; sharing-link activity reports list sites where new links were
created in the 28 days before the report. Activity never replaces the snapshot
as the source of exposure counts. Presentation adds no finding or count.
All tenants, sites and values are fictional.
"""

import re
import unittest

try:
    from tests import content_sharing_scenarios as h
except ImportError:  # discovered with -s tests
    import content_sharing_scenarios as h

LINK_HEADER = 'Site ID,URL,SiteName,Links created,Primary admin,Primary admin email,Teams attached,Site template,Report date'
RAW_CODES = ('permission_snapshot', 'sharing_links_activity', 'no_activity', 'special_group_permissions')


def activity(*rows):
    return LINK_HEADER + '\n' + ''.join(
        f'site-{index},{url},Fictional {index},{count},Owner,owner@fictional.example,No,Team,2026-10-15\n'
        for index, (url, count) in enumerate(rows))


SNAPSHOT = ('Site_permissions_SharePoint.csv', h.snapshot(h.snapshot_row('site-alpha', h.SITE_A, anyone=3, org=5)))
ACTIVITY = [
    ('Anyone_links_2026-10-15.csv', activity((h.SITE_A, 4), (h.SITE_B, 3))),
    ('People_in_your_organization_links_2026-10-15.csv', LINK_HEADER + '\n'),
    ('Specific_people_links_shared_externally_2026-10-15.csv', activity((h.SITE_A, 2))),
]


def report(**fields):
    base = {'source_file': 'report.csv', 'report_type': 'permission_snapshot', 'workload': 'SharePoint',
            'status': 'selected', 'report_date': '2026-10-15T00:00:00+00:00', 'records_read': 1,
            'population_count': 1, 'freshness': 'fresh', 'max_age_days': 35, 'collector_report_entity': ''}
    base.update(fields)
    return base


class ReportDescriptionTests(unittest.TestCase):
    def describe(self, **fields):
        from Core.sam_report_descriptions import describe_report
        return describe_report(report(**fields))

    def test_snapshot_is_named_typed_and_dated(self):
        row = self.describe(population_count=2)
        self.assertEqual((row['name'], row['kind'], row['date'], row['result'], row['used']),
                         ('Site permissions (SharePoint)', 'Snapshot', '2026-10-15', '2 sites', 'Used'))
        self.assertIn('as of the report date', row['shows'])

    def test_activity_names_the_link_type_and_states_the_window(self):
        row = self.describe(source_file='Anyone_links_2026-10-15.csv', report_type='sharing_links_activity',
                            population_count=2, links_created=7)
        self.assertEqual((row['name'], row['kind'], row['result']),
                         ('Anyone links activity (SharePoint)', 'Activity', '7 new links across 2 sites'))
        self.assertIn('28 days', row['shows'])
        self.assertIn('already existed', row['shows'])

    def test_collector_entity_names_a_renamed_download(self):
        row = self.describe(source_file='Report.csv', report_type='sharing_links_activity',
                            collector_report_entity='SharingLinks_Guests', links_created=1)
        self.assertEqual(row['name'], 'Specific people links shared externally activity (SharePoint)')

    def test_unattributed_activity_says_so(self):
        row = self.describe(source_file='renamed.csv', report_type='sharing_links_activity', links_created=1)
        self.assertEqual(row['name'], 'Sharing links activity (link type not identified) (SharePoint)')

    def test_empty_activity_report_is_used_and_not_read_as_no_links(self):
        row = self.describe(source_file='People_in_your_organization_links.csv', report_type='sharing_links_activity',
                            status='no_activity', workload='Unknown', report_date='', records_read=0,
                            population_count=0, freshness='unknown')
        self.assertEqual((row['name'], row['date'], row['result']),
                         ('People in your organization links activity', 'Not established', 'No sites listed'))
        self.assertEqual(row['used'], 'Used: no new links of this type were created in the period')

    def test_excluded_and_aged_reports_say_why(self):
        self.assertEqual(self.describe(status='superseded')['used'], 'Not used: a newer report of the same sites replaces it')
        self.assertEqual(self.describe(status='future_date')['used'], 'Not used: the report date is after the evaluation date')
        self.assertEqual(self.describe(report_date='2026-08-20T00:00:00+00:00', freshness='stale')['date'],
                         '2026-08-20 (older than 35 days)')

    def test_one_site_and_one_link_are_singular(self):
        row = self.describe(report_type='sharing_links_activity', source_file='Anyone_links.csv', links_created=1)
        self.assertEqual(row['result'], '1 new link across 1 site')

    def test_older_results_without_a_link_total_still_describe_the_sites(self):
        row = self.describe(report_type='sharing_links_activity', source_file='Anyone_links.csv', population_count=3)
        self.assertEqual(row['result'], '3 sites listed')


class ScannerTotalsTests(unittest.TestCase):
    def test_activity_reports_record_new_link_totals_per_report(self):
        _, exposure = h.run(sam=[SNAPSHOT] + ACTIVITY)
        reports = {row['source_file']: row for row in exposure['sources']['sam']['reports']}
        self.assertEqual(reports['Anyone_links_2026-10-15.csv']['links_created'], 7)
        self.assertEqual(reports['Specific_people_links_shared_externally_2026-10-15.csv']['links_created'], 2)
        self.assertNotIn('links_created', reports['Site_permissions_SharePoint.csv'])

    def test_activity_does_not_change_exposure_counts_or_findings(self):
        _, snapshot_only = h.run(sam=[SNAPSHOT])
        _, combined = h.run(sam=[SNAPSHOT] + ACTIVITY)
        self.assertEqual(snapshot_only['sources']['sam']['signals'], combined['sources']['sam']['signals'])
        keys = lambda exposure: sorted(row['FindingKey'] for row in exposure['recommendations']
                                       if row['FindingKey'].startswith('data_exposure.') and 'freshness' not in row['FindingKey'])
        self.assertEqual(keys(snapshot_only), keys(combined))


class DeliverableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from Core.customer_report import render_customer_report
        cls.result, cls.exposure = h.run(payload=h.spo(h.COMPLIANT), sam=[SNAPSHOT] + ACTIVITY)
        cls.html = render_customer_report(cls.result, {'data_exposure': cls.exposure}, 'Fictional tenant')

    def section(self):
        match = re.search(r'<section id="sharepoint-report-sources">.*?</section>', self.html, re.S)
        self.assertIsNotNone(match, 'Content section lists the SharePoint reports used')
        return match.group(0)

    def test_content_section_explains_snapshot_versus_activity(self):
        section = self.section()
        self.assertIn('SharePoint reports used', section)
        self.assertIn('Site permissions (SharePoint)', section)
        self.assertIn('Anyone links activity (SharePoint)', section)
        self.assertIn('7 new links across 2 sites', section)
        self.assertIn('Specific people links shared externally activity (SharePoint)', section)
        text = re.sub(r'<[^>]+>', ' ', section)
        self.assertIn('Snapshot reports show', text)
        self.assertIn('Activity reports list', text)
        self.assertIn('28 days', text)

    def test_snapshot_is_listed_before_activity(self):
        section = self.section()
        self.assertLess(section.index('Site permissions (SharePoint)'), section.index('Anyone links activity'))

    def test_no_internal_codes_or_raw_timestamps_reach_the_html(self):
        for code in RAW_CODES:
            self.assertNotIn(code, self.html)
        appendix = re.search(r'<details class="appendix-panel" id="engineer-appendix">.*?</details>', self.html, re.S).group(0)
        self.assertNotIn('T00:00:00', appendix)
        self.assertIn('Site permissions (SharePoint)', appendix)

    def test_no_section_without_reports(self):
        from Core.customer_report import _sharepoint_reports
        self.assertEqual(_sharepoint_reports({}), '')

    def test_display_limit_names_the_remainder_without_dropping_workbook_rows(self):
        from Core.customer_report import _sharepoint_reports
        from Core.evidence_layer import _build_data_exposure_sheet
        reports = [report(source_file=f'Anyone_links_{index}.csv', report_type='sharing_links_activity', links_created=1)
                   for index in range(14)]
        sources = {'sam': {'files_loaded': 14, 'reports': reports}}
        html = _sharepoint_reports({'data_exposure': {'sources': sources}})
        self.assertEqual(html.count('<tr>') - 1, 12)
        self.assertIn('2 more reports are listed in the technical workbook', html)
        sheet = _build_data_exposure_sheet({'sources': sources})
        self.assertEqual(sum(row['Detail Type'] == 'Source Report' for row in sheet['rows']), 14)

    def test_workbook_source_rows_carry_plain_descriptions_and_keep_codes(self):
        from Core.evidence_layer import _build_data_exposure_sheet
        rows = {row['Source File']: row for row in _build_data_exposure_sheet(self.exposure)['rows']
                if row.get('Detail Type') == 'Source Report'}
        anyone = rows['Anyone_links_2026-10-15.csv']
        self.assertEqual(anyone['Report Name'], 'Anyone links activity (SharePoint)')
        self.assertEqual(anyone['Report Kind'], 'Activity')
        self.assertEqual(anyone['Result'], '7 new links across 2 sites')
        self.assertEqual(anyone['Used In Assessment'], 'Used')
        self.assertIn('28 days', anyone['What It Shows'])
        self.assertEqual(anyone['Report Type'], 'sharing_links_activity')
        self.assertEqual(rows['Site_permissions_SharePoint.csv']['Report Kind'], 'Snapshot')
        empty = rows['People_in_your_organization_links_2026-10-15.csv']
        self.assertEqual(empty['Used In Assessment'], 'Used: no new links of this type were created in the period')


if __name__ == '__main__':
    unittest.main()

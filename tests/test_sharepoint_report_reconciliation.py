"""Fictional collected DAG reports, date lineage and proportionate reminders."""

from copy import deepcopy
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from provenance_fixture import fictional_publication
from Core.sharepoint_report_provenance import capture_download_hashes, downloaded_report_metadata
from Core.data_exposure_assessment import build_data_exposure_assessment
from Core.sharepoint_governance import reconcile_dag_coverage, summarize_sharepoint_governance
from Core.customer_report import _heading, _observation, _action_text
from Core.assessment_result import build_assessment_result

DAY = '2026-10-27'
TENANT = '11111111-1111-4111-8111-111111111111'


def csv_file(folder, name='permissions.csv', date=None, activity=False, empty=False):
    row = {'Site URL':'https://example.invalid/sites/team', 'Workload':'SharePoint'}
    row.update({'Links created':1} if activity else {
        'Number of users having access':4, 'Anyone link count':0, 'EEEU permission count':0, 'Everyone permission count':0})
    if date:
        row['Report Date'] = date
    path = Path(folder)/name
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        if not empty:
            writer.writerow(row)
    return path


def payload(path, date=DAY, entity='PermissionedUsers'):
    return {'exported_files':[{'path':str(path),'report_id':'report-one','modified_utc':DAY}],
            'dag_reports':{'available':True,'reports':[{'ReportId':'report-one','Status':'Completed',
                'RequestedEntity':entity,'RequestedWorkload':'SharePoint','ReportEndTime':date}]}}


def scan(path, data=None):
    return build_data_exposure_assessment([str(path)], [], evaluation_date=DAY,
        sam_report_metadata=downloaded_report_metadata(data or {}, [path]))


def gap(*names):
    return {'FindingKey':'sharepoint.dag.not_assessed','Feature':'Sharing report coverage','Service':'M365',
            'Disposition':'Coverage','Priority':'High','Confidence':'Unknown','Status':'Not Assessed',
            'Observation':'Original inventory gap.', 'Recommendation':'Rerun with --reports-dir PATH.',
            'DagGaps':[{'report':name,'status':'Missing'} for name in names]}


class CollectedReportDateTests(unittest.TestCase):
    def test_exact_download_hash_and_report_id_supply_missing_csv_date(self):
        with TemporaryDirectory() as folder:
            path = csv_file(folder)
            original = path.read_bytes()
            data = payload(path)
            before = deepcopy(data)
            retained = capture_download_hashes(data)
            result = scan(path, retained)
            report = result['sources']['sam']['reports'][0]
            self.assertEqual(report['freshness'], 'fresh')
            self.assertEqual(report['date_basis'], 'Collector report period end')
            self.assertEqual(report['collector_report_id'], 'report-one')
            self.assertEqual(report['acquisition'], 'Retained collector download')
            self.assertFalse(any(row['FindingKey']=='data_exposure.freshness.sam' for row in result['recommendations']))
            self.assertEqual(data, before)
            self.assertEqual(path.read_bytes(), original)

    def test_data_period_end_takes_precedence_over_recent_completion(self):
        with TemporaryDirectory() as folder:
            path = csv_file(folder)
            data = payload(path, '2026-08-01')
            data['dag_reports']['reports'][0]['CompletedDateTime'] = DAY
            report = scan(path, capture_download_hashes(data))['sources']['sam']['reports'][0]
            self.assertTrue(report['report_date'].startswith('2026-08-01'))
            self.assertEqual(report['freshness'], 'stale')

    def test_creation_download_and_filename_dates_cannot_refresh_evidence(self):
        with TemporaryDirectory() as folder:
            path = csv_file(folder, 'permissions_2026-10-27.csv')
            data = payload(path)
            row = data['dag_reports']['reports'][0]
            row.pop('ReportEndTime')
            row.update(CreatedDateTime=DAY, TriggeredDateTime=DAY)
            result = scan(path, capture_download_hashes(data))
            self.assertEqual(result['sources']['sam']['freshness'], 'unknown')
            finding = next(row for row in result['recommendations'] if row['FindingKey']=='data_exposure.freshness.sam')
            self.assertEqual(finding['Priority'], 'Medium')
            self.assertEqual(_heading(finding), 'Confirm the date of SharePoint permissions evidence')

    def test_changed_bytes_or_wrong_report_id_cannot_borrow_a_date(self):
        with TemporaryDirectory() as folder:
            path = csv_file(folder)
            data = capture_download_hashes(payload(path))
            with path.open('a', encoding='utf-8') as handle:
                handle.write('\n')
            self.assertEqual(scan(path, data)['sources']['sam']['freshness'], 'unknown')
            data = capture_download_hashes(payload(path))
            data['exported_files'][0]['report_id'] = 'unmatched'
            self.assertEqual(scan(path, data)['sources']['sam']['freshness'], 'unknown')

    def test_csv_date_is_not_overwritten_by_collector_metadata(self):
        with TemporaryDirectory() as folder:
            path = csv_file(folder, date='2026-07-01')
            report = scan(path, capture_download_hashes(payload(path)))['sources']['sam']['reports'][0]
            self.assertTrue(report['report_date'].startswith('2026-07-01'))
            self.assertEqual(report['date_basis'], 'Report date field')

    def test_legacy_packaged_path_recovers_binding_without_file_time(self):
        with TemporaryDirectory() as folder:
            nested = Path(folder)/'inputs'/'1'
            nested.mkdir(parents=True)
            path = csv_file(nested)
            data = payload('inputs/1/permissions.csv')
            metadata = downloaded_report_metadata(data, [path], folder)
            result = build_data_exposure_assessment([str(path)], [], evaluation_date=DAY, sam_report_metadata=metadata)
            self.assertEqual(result['sources']['sam']['freshness'], 'fresh')
            data['exported_files'][0]['path'] = '../permissions.csv'
            self.assertEqual(downloaded_report_metadata(data, [path], folder), {})

    def test_conflicting_inventory_dates_and_uncompleted_reports_remain_unknown(self):
        with TemporaryDirectory() as folder:
            path = csv_file(folder)
            data = capture_download_hashes(payload(path))
            data['dag_reports']['reports'].append(dict(data['dag_reports']['reports'][0], ReportEndTime='2026-07-01'))
            self.assertEqual(downloaded_report_metadata(data, [path]), {})
            data = payload(path)
            data['dag_reports']['reports'][0]['Status'] = 'Running'
            self.assertEqual(downloaded_report_metadata(data, [path]), {})

    def test_identical_empty_downloads_keep_distinct_report_bindings(self):
        with TemporaryDirectory() as folder:
            one = csv_file(folder, 'anyone.csv', activity=True, empty=True)
            two = csv_file(folder, 'guests.csv', activity=True, empty=True)
            data = payload(one, entity='SharingLinks_Anyone')
            data['exported_files'].append({'path':str(two), 'report_id':'report-two'})
            data['dag_reports']['reports'].append(dict(data['dag_reports']['reports'][0], ReportId='report-two', RequestedEntity='SharingLinks_Guests'))
            metadata = downloaded_report_metadata(capture_download_hashes(data), [one,two])
            result = build_data_exposure_assessment([str(one),str(two)], [], evaluation_date=DAY, sam_report_metadata=metadata)
            reports = result['sources']['sam']['reports']
            self.assertEqual({row['collector_report_entity'] for row in reports}, {'SharingLinks_Anyone','SharingLinks_Guests'})
            self.assertTrue(all(row['freshness']=='fresh' and row['status']=='no_activity' for row in reports))
            self.assertEqual(result['sources']['sam']['freshness'], 'fresh')
            reconciled = reconcile_dag_coverage([gap('Anyone and guest sharing links')], reports)
            self.assertEqual(reconciled[0]['Disposition'], 'Reference')


class SharingCoverageTests(unittest.TestCase):
    def test_remaining_gap_names_survive_customer_rendering_and_use_both_acquisitions(self):
        original = gap('SharePoint permission state','Everyone except external users activity')
        reports = [{'report_type':'permission_snapshot','workload':'SharePoint','status':'selected','freshness':'fresh','records_read':2}]
        row = reconcile_dag_coverage([original], reports)[0]
        bundle = {'data_exposure':{'sources':{'sam':{'reports':reports}}}}
        self.assertIn('Everyone except external users activity', _observation(row,bundle))
        self.assertNotIn('saved collection recorded', _observation(row,bundle))
        self.assertIn('already collected or supplied', _action_text(row))
        self.assertNotIn('--reports-dir', _action_text(row))
        self.assertEqual(original['DagGaps'][0]['report'], 'SharePoint permission state')

    def test_permission_counts_do_not_close_sharing_activity_gaps(self):
        reports = [{'report_type':'permission_snapshot','workload':'SharePoint','status':'selected','freshness':'fresh'}]
        self.assertEqual(reconcile_dag_coverage([gap('Anyone and guest sharing links')],reports)[0]['Disposition'], 'Coverage')

    def test_onedrive_or_unknown_workload_does_not_answer_sharepoint_link_activity(self):
        for workload in ('OneDrive','Unknown'):
            reports = [{'report_type':'sharing_links_activity','workload':workload,'status':'selected','freshness':'fresh','source_file':name}
                       for name in ('Anyone_links.csv','Specific_people_links_shared_externally.csv')]
            self.assertEqual(reconcile_dag_coverage([gap('Anyone and guest sharing links')],reports)[0]['Disposition'],'Coverage')

    def test_bound_activity_entity_keeps_activity_separate_from_existing_permissions(self):
        with TemporaryDirectory() as folder:
            path = csv_file(folder,'arbitrary.csv',activity=True)
            result = scan(path,capture_download_hashes(payload(path,entity='SharingLinks_Anyone')))
            observations = result['sources']['sam']['observations']
            self.assertTrue(any(row['label'].startswith('Anyone links created') and row['value']==1 for row in observations))
            self.assertFalse(any(row['FindingKey']=='data_exposure.anonymous_links' for row in result['recommendations']))

    def test_completed_inventory_is_not_parsed_evidence(self):
        original = gap('SharePoint permission state')
        for reports in ([], [{'report_type':'permission_snapshot','workload':'SharePoint','status':'unparsed','freshness':'unknown'}],
                        [{'report_type':'permission_snapshot','workload':'SharePoint','status':'error','freshness':'fresh'}]):
            self.assertEqual(reconcile_dag_coverage([original],reports)[0]['Disposition'], 'Coverage')

    def test_old_or_undated_parsed_reports_do_not_keep_a_duplicate_high_gap(self):
        for freshness in ('stale','unknown'):
            with self.subTest(freshness=freshness):
                reports = [{'report_type':'permission_snapshot','workload':'SharePoint','status':'selected','freshness':freshness}]
                row = reconcile_dag_coverage([gap('SharePoint permission state')],reports)[0]
                self.assertEqual(row['Disposition'],'Reference')
                self.assertEqual(row['Priority'],'Low')
                self.assertEqual(row['DagGaps'],[])
                self.assertEqual(row['DagCoveredReports'],[])
                self.assertEqual(row['DagDateReviewReports'],['SharePoint permission state'])
                self.assertIn('separate report-date reminder',row['Observation'])

    def test_truly_missing_family_remains_separate_from_date_review(self):
        reports = [{'report_type':'permission_snapshot','workload':'SharePoint','status':'selected','freshness':'stale'}]
        row = reconcile_dag_coverage([gap('SharePoint permission state','Everyone except external users activity')],reports)[0]
        self.assertEqual(row['Priority'],'High')
        self.assertEqual(row['Disposition'],'Coverage')
        self.assertEqual([item['report'] for item in row['DagGaps']],['Everyone except external users activity'])
        self.assertEqual(row['DagDateReviewReports'],['SharePoint permission state'])

    def test_all_parsed_activity_requirements_can_clear_remaining_family(self):
        reports = [{'collector_report_entity':entity,'report_type':'exposure_counts','workload':'SharePoint','freshness':'fresh','status':'selected'}
                   for entity in ('EveryoneExceptExternalUsersAtSite','EveryoneExceptExternalUsersForItems')]
        row = reconcile_dag_coverage([gap('Everyone except external users activity')],reports)[0]
        self.assertEqual(row['Disposition'], 'Reference')
        self.assertEqual(row['DagGaps'], [])
        self.assertEqual(row['Priority'], 'Low')
        self.assertEqual(_action_text(row), '')

    def test_inventory_freshness_uses_evaluation_date_and_rejects_future_dates(self):
        data = {'dag_reports':{'available':True,'reports':[{'Status':'Completed','ReportEndTime':DAY}]}}
        self.assertEqual(summarize_sharepoint_governance(data,evaluation_date=DAY)['dag_recent_completed_count'],1)
        self.assertEqual(summarize_sharepoint_governance(data,evaluation_date='2026-07-01')['dag_recent_completed_count'],0)

    def test_real_anonymous_link_signal_keeps_its_priority(self):
        with TemporaryDirectory() as folder:
            path = csv_file(folder,date=DAY)
            content = path.read_text(encoding='utf-8').replace(',4,0,0,0,', ',4,3,0,0,')
            path.write_text(content,encoding='utf-8')
            result = scan(path)
            row = next(row for row in result['recommendations'] if row['FindingKey']=='data_exposure.anonymous_links')
            self.assertEqual(row['Priority'],'High')
            self.assertEqual(row['Disposition'],'Action')


@fictional_publication
class SharePointMaintenanceOutputTests(unittest.TestCase):
    def test_date_reminder_is_medium_and_not_a_pilot_gate_across_outputs(self):
        from Core.export_recommendations import export_to_excel
        from Core.customer_report import render_customer_report
        from Core.assessment_serialization import write_assessment_result, read_assessment_result
        from Core.workbook_layout import technical_workbook_path
        from openpyxl import load_workbook
        for date in (None, '2026-09-20'):
            with self.subTest(date=date), TemporaryDirectory() as folder:
                path = csv_file(folder, date=date)
                data = scan(path)
                bundle = {'data_exposure':data,'evaluation_date':DAY,'expected_tenant_id':TENANT,'sheets':{}}
                result = build_assessment_result(data['recommendations'],bundle,evaluation_date=DAY,expected_tenant_id=TENANT)
                row = next(row for row in result['actions'] if row['FindingKey']=='data_exposure.freshness.sam')
                self.assertEqual(row['Priority'],'Medium')
                self.assertFalse(row['SecurityGate'])
                self.assertEqual(row['PilotImpact'],'')
                self.assertEqual(row['ReadinessStage'],'Evidence review')
                bundle['assessment_result'] = result
                output = Path(export_to_excel(data['recommendations'],filename='Fictional.xlsx',evidence_bundle=bundle,output_dir=folder))
                result = bundle['assessment_result']
                before = deepcopy(result)
                html = render_customer_report(result,bundle,'Fictional',output,technical_workbook_path=technical_workbook_path(output))
                self.assertIn('Confirm the date of SharePoint permissions evidence' if date is None else 'Refresh the older SharePoint permissions evidence',html)
                workbook = load_workbook(output)
                try:
                    cells = '\n'.join(str(cell.value or '') for sheet in workbook for row in sheet for cell in row)
                    self.assertIn('SharePoint',cells)
                finally:
                    workbook.close()
                self.assertEqual(result,before)
                target = Path(folder)/'result.json'
                write_assessment_result(target,result)
                replay = read_assessment_result(target)
                self.assertEqual(html,render_customer_report(replay,bundle,'Fictional',output,technical_workbook_path=technical_workbook_path(output)))


if __name__ == '__main__':
    unittest.main()

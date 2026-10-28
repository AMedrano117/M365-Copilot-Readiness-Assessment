"""Report missing policy checks without inferring coverage from missing data."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from provenance_fixture import fictional_publication
from test_device_compliance_reasons import fixture
from test_evidence_reconciliation import DAY, TENANT, dataset
from Core.device_compliance import build_device_compliance
from Core.expanded_findings import expanded_findings
from Core.assessment_result import build_assessment_result


def assignment_bundle(sync=DAY, **fields):
    return fixture(sync, 'DefaultDeviceCompliancePolicy.RequireDeviceCompliancePolicyAssigned', **fields)


class PolicyAssignmentTests(unittest.TestCase):
    def model(self, bundle):
        return build_device_compliance(bundle, evaluation_date=DAY)

    def test_portal_label_and_graph_identifiers_select_the_same_assignment_check(self):
        for label in ('Has a compliance policy assigned', 'DefaultDeviceCompliancePolicy.RequireDeviceCompliancePolicyAssigned',
                      'RequireDeviceCompliancePolicyAssigned', 'DefaultDeviceCompliancePolicy.RequireCompliancePolicyAssigned'):
            with self.subTest(label=label):
                bundle = fixture(setting=label)
                model = self.model(bundle)
                self.assertEqual(model['settings'][0]['kind'], 'assignment')
                self.assertEqual(model['devices'][0]['policy_assignment'], 'Reported missing')
                self.assertEqual(model['policy_assignment']['counts']['Reported missing'], 1)
                row = expanded_findings(bundle, evaluation_date=DAY)[0]
                self.assertEqual(row['FindingKey'], 'expanded.compliance_setting_states.assignment_missing')
                self.assertEqual(row['Priority'], 'Medium')
                self.assertEqual(row['Service'], 'Entra')
                self.assertEqual(row['Status'], 'Attention Required')

    def test_assignment_check_is_visible_even_when_device_overall_is_compliant(self):
        bundle = assignment_bundle()
        bundle['assessment_sources']['managed_devices'][0]['records'][0]['complianceState'] = 'compliant'
        before = deepcopy(bundle)
        model = self.model(bundle)
        row = expanded_findings(bundle, evaluation_date=DAY)[0]
        self.assertEqual(model['devices'][0]['device']['complianceState'], 'compliant')
        self.assertEqual(model['devices'][0]['policy_assignment'], 'Reported missing')
        self.assertIn('1 distinct managed device', row['Observation'])
        self.assertEqual(bundle, before)

    def test_empty_missing_and_partial_sources_do_not_mean_no_policy(self):
        for mode in ('absent', 'empty', 'partial'):
            with self.subTest(mode=mode):
                bundle = assignment_bundle()
                if mode == 'absent':
                    bundle['assessment_sources'].pop('compliance_setting_states')
                else:
                    bundle['assessment_sources']['compliance_setting_states'] = [dataset([], complete=mode != 'partial')]
                bundle['assessment_sources']['compliance_policies'] = [dataset([])]
                model = self.model(bundle)
                self.assertEqual(model['devices'][0]['policy_assignment'], 'Unknown')
                self.assertEqual(model['policy_assignment']['counts']['Unknown'], 1)
                self.assertFalse(expanded_findings(bundle, evaluation_date=DAY))

    def test_error_and_unevaluated_checks_are_unknown_not_missing(self):
        for state in ('unknown', 'error', 'conflict', 'notAssigned', 'notApplicable'):
            with self.subTest(state=state):
                bundle = assignment_bundle(state=state)
                self.assertEqual(self.model(bundle)['devices'][0]['policy_assignment'], 'Unknown')
                self.assertFalse(any(row['FindingKey'].endswith('.assignment_missing') for row in expanded_findings(bundle, evaluation_date=DAY)))

    def test_one_unassigned_security_setting_is_not_proof_of_no_policy(self):
        bundle = fixture(setting='Require BitLocker', state='notAssigned')
        self.assertEqual(self.model(bundle)['devices'][0]['policy_assignment'], 'Unknown')
        self.assertFalse(expanded_findings(bundle, evaluation_date=DAY))

    def test_passing_assignment_check_does_not_invent_policy_name_or_enforcement(self):
        bundle = assignment_bundle(state='compliant')
        model = self.model(bundle)
        self.assertEqual(model['devices'][0]['policy_assignment'], 'Assignment check passed')
        self.assertEqual(model['policy_assignment']['counts']['Assignment check passed'], 1)
        self.assertIn('does not establish policy names or effective enforcement', model['policy_assignment']['qualification'])
        self.assertFalse(expanded_findings(bundle, evaluation_date=DAY))

    def test_multiple_missing_checks_count_one_linked_device_and_retain_all_rows(self):
        bundle = assignment_bundle()
        raw = bundle['assessment_sources']['compliance_setting_states'][0]['records']
        raw.append(dict(raw[0], id='another-assignment-check'))
        row = expanded_findings(bundle, evaluation_date=DAY)[0]
        self.assertIn('2 assignment-check record(s), linked to 1 distinct managed device', row['Observation'])
        self.assertEqual(len(row['InvestigationEvidence']['records']), 2)
        self.assertEqual(self.model(bundle)['policy_assignment']['counts']['Reported missing'], 1)

    def test_conflicting_assignment_results_are_reported_without_resolving_them(self):
        bundle = assignment_bundle()
        raw = bundle['assessment_sources']['compliance_setting_states'][0]['records']
        raw.append(dict(raw[0], id='passed-assignment-check', state='compliant'))
        model = self.model(bundle)
        self.assertEqual(model['devices'][0]['policy_assignment'], 'Conflicting assignment evidence')
        self.assertEqual(model['policy_assignment']['counts']['Reported missing'], 0)
        self.assertEqual(model['policy_assignment']['counts']['Conflicting assignment evidence'], 1)
        self.assertEqual(expanded_findings(bundle, evaluation_date=DAY)[0]['Priority'], 'Medium')

    def test_unmapped_check_keeps_evidence_but_does_not_identify_device_by_name(self):
        bundle = assignment_bundle(deviceId='missing-device', deviceName='Fictional laptop')
        model = self.model(bundle)
        self.assertEqual(model['policy_assignment']['unresolved_missing_records'], 1)
        self.assertEqual(model['policy_assignment']['counts']['Reported missing'], 0)
        row = expanded_findings(bundle, evaluation_date=DAY)[0]
        self.assertIn('0 distinct managed device(s); 1 record(s) have unresolved device identity', row['Observation'])

    def test_stale_assignment_and_current_security_failure_have_separate_findings(self):
        bundle = assignment_bundle('2026-07-01')
        data = bundle['assessment_sources']['compliance_setting_states'][0]['records']
        data.append(dict(data[0], id='bitlocker', settingName='Require BitLocker', lastReportedDateTime=DAY))
        bundle['assessment_sources']['machines'] = [dataset([{'id': 'defender', 'aadDeviceId': 'entra-1', 'lastSeen': DAY}])]
        rows = expanded_findings(bundle, evaluation_date=DAY)
        missing = next(row for row in rows if row['DeviceComplianceTriage'] == 'assignment')
        security = next(row for row in rows if row['DeviceComplianceTriage'] == 'current')
        self.assertEqual(missing['ObservationDate'], '2026-07-01')
        self.assertEqual(missing['Priority'], 'Medium')
        self.assertEqual(security['ObservationDate'], DAY)
        self.assertEqual(security['Priority'], 'High')
        self.assertEqual(len(missing['InvestigationEvidence']['records']), 1)
        self.assertEqual(len(security['InvestigationEvidence']['records']), 1)


@fictional_publication
class PolicyAssignmentOutputTests(unittest.TestCase):
    def test_device_worklist_html_evidence_links_and_replay_keep_assignment_detail(self):
        from openpyxl import load_workbook
        from Core.export_recommendations import export_to_excel
        from Core.customer_report import render_customer_report
        from Core.assessment_serialization import write_assessment_result, read_assessment_result
        from Core.workbook_layout import technical_workbook_path
        bundle = assignment_bundle('2026-07-01')
        bundle['assessment_sources']['managed_devices'][0]['records'][0].update(
            complianceState='compliant', userPrincipalName='fictional@example.invalid', operatingSystem='Windows')
        rows = expanded_findings(bundle, evaluation_date=DAY)
        result = build_assessment_result(rows, bundle, evaluation_date=DAY, expected_tenant_id=TENANT)
        bundle['assessment_result'] = result
        with TemporaryDirectory() as folder:
            path = Path(export_to_excel(rows, filename='Fictional.xlsx', evidence_bundle=bundle, output_dir=folder))
            result = bundle['assessment_result']
            before = deepcopy(result)
            html = render_customer_report(result, bundle, 'Fictional', path, technical_workbook_path=technical_workbook_path(path))
            self.assertIn('devices reported without a compliance policy', html)
            workbook = load_workbook(path)
            try:
                values = list(workbook['Devices'].values)
                records = [dict(zip(values[0], row)) for row in values[1:]]
                row = next(row for row in records if row.get('Issue') == 'Compliance policy assignment missing (reported)')
                self.assertEqual(row['Device'], 'Fictional laptop')
                self.assertEqual(row['Primary user'], 'fictional@example.invalid')
                self.assertEqual(row['Intune compliance'], 'Compliant')
                self.assertIn('Reported missing', row['Detail'])
                self.assertIn('Has a compliance policy assigned', row['Detail'])
                self.assertIn('2026-07-01', str(row['Last reported']))
                finding = next(row for row in result['recommendations'] if row.get('DeviceComplianceTriage') == 'assignment')
                self.assertTrue(finding['RawEvidenceRanges'])
                self.assertTrue(finding['TechnicalEvidenceRanges'])
                self.assertEqual(finding['Priority'], 'Medium')
            finally:
                workbook.close()
            self.assertEqual(result, before)
            snapshot = Path(folder) / 'result.json'
            write_assessment_result(snapshot, result)
            replay = read_assessment_result(snapshot)
            self.assertEqual(replay['device_compliance'], json.loads(json.dumps(result['device_compliance'], default=str)))
            self.assertEqual(html, render_customer_report(replay, bundle, 'Fictional', path, technical_workbook_path=technical_workbook_path(path)))


if __name__ == '__main__':
    unittest.main()

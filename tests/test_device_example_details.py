"""Invented policy/status examples must explain the selected evidence faithfully."""
from copy import deepcopy
import unittest

from test_device_summary_presentation import scenario, assessment
from test_evidence_reconciliation import DAY, dataset
from Core.device_examples import device_example, error_detail
from Core.evidence_selection import build_evidence_selection
from Core.finding_evidence import build_finding_evidence
from Core.technical_appendix import render_technical_appendix


def render(bundle):
    _, result = assessment(bundle)
    selection = build_evidence_selection(result, bundle)
    model = build_finding_evidence(selection)
    bundle = dict(bundle, finding_evidence=model)
    return result, render_technical_appendix(result, bundle, 'assessment.xlsx', 'technical.xlsx',
                    {row['finding_id']: row for row in model['findings']}, [])


class DeviceExampleDetailTests(unittest.TestCase):
    def test_selected_setting_has_separate_resolved_policy_setting_and_status(self):
        bundle = scenario()
        _, html = render(bundle)
        self.assertIn('<th scope="col">Compliance policy</th>', html)
        self.assertIn('<th scope="col">Status</th>', html)
        self.assertIn('Windows baseline', html)
        self.assertIn('BitLocker encryption requirement failed', html)
        self.assertIn('Not compliant', html)
        self.assertNotIn('Windows10CompliancePolicy', html)
        self.assertNotIn('Reason or status', html)

    def test_unknown_results_show_actual_status_without_fabricating_error_cause(self):
        bundle = scenario()
        raw = bundle['assessment_sources']['compliance_setting_states'][0]['records']
        raw[0].update(state='notEvaluated')
        result, html = render(bundle)
        row = next(row for row in result['recommendations'] if row.get('DeviceFindingType') == 'ReportingErrorOrConflict')
        self.assertEqual(row['DeviceSummary']['diagnostic_groups'][0]['checks'], 1)
        self.assertIn('no diagnostic explanation', row['DeviceSummary']['text'])
        self.assertIn('Not evaluated', html)
        self.assertIn('No diagnostic detail returned', html)
        self.assertNotIn('Firewall protection does not meet policy', row['DeviceSummary']['text'])

    def test_retained_error_details_survive_and_html_escapes_them(self):
        bundle = scenario()
        bundle['assessment_sources']['compliance_setting_states'][0]['records'][0].update(
            state='error', errorCode=65001, errorDescription='Device <report> unavailable')
        before = deepcopy(bundle['assessment_sources'])
        result, html = render(bundle)
        row = next(row for row in result['recommendations'] if row.get('DeviceFindingType') == 'ReportingErrorOrConflict')
        self.assertIn('Code: 65001', html)
        self.assertIn('Device &lt;report&gt; unavailable', html)
        self.assertEqual(row['DeviceSummary']['diagnostic_groups'][0]['checks'], 1)
        self.assertEqual(bundle['assessment_sources'], before)

    def test_inventory_examples_link_real_reasons_and_explain_overlap(self):
        result, html = render(scenario())
        row = next(row for row in result['recommendations'] if row.get('DeviceFindingType') == 'InventoryComplianceState')
        self.assertIn('not an additional setting failure', row['DeviceSummary']['text'])
        self.assertNotIn('<summary>Reconcile device compliance inventory</summary>', html)
        self.assertIn('Windows baseline', html)
        self.assertTrue(row['InvestigationEvidence']['records'])

    def test_missing_cause_is_explicit_and_names_do_not_establish_identity(self):
        bundle = scenario()
        bundle['assessment_sources'].pop('compliance_setting_states')
        _, html = render(bundle)
        self.assertNotIn('<summary>Reconcile device compliance inventory</summary>', html)
        record = {'deviceName': 'Same name', 'complianceState': 'noncompliant'}
        example = device_example(record, {}, [], 'InventoryComplianceState')
        self.assertEqual(example['policy'], 'Not retained')
        self.assertIn('not retained', example['setting'])

    def test_setting_lookup_does_not_use_an_unrelated_device_failure(self):
        bundle = scenario()
        bundle['assessment_sources']['compliance_setting_states'][0]['records'][0].update(state='unknown')
        _, html = render(bundle)
        fragment = html.split('Confirm incomplete device compliance checks')[1].split('</details>')[0]
        self.assertIn('BitLocker encryption requirement', fragment)
        self.assertNotIn('Firewall protection requirement', fragment)
        self.assertIn('Unknown', fragment)

    def test_off_observation_with_third_party_av_is_low_priority_and_not_a_blocker(self):
        bundle = scenario()
        bundle['assessment_sources']['windows_protection'] = [dataset([{
            'ParentId': 'active-0', 'realTimeProtectionEnabled': False, 'lastReportedDateTime': DAY,
            'avMode': 'passive', 'thirdPartyAntivirus': 'Example AV'}])]
        result, html = render(bundle)
        row = next(row for row in result['recommendations'] if row.get('DeviceFindingType') == 'DefenderRealTimeProtectionObservation')
        self.assertEqual(row['Priority'], 'Low')
        self.assertEqual(row['Disposition'], 'Opportunity')
        self.assertNotIn(row['RecommendationId'], [item['RecommendationId'] for item in result['blockers']])
        self.assertIn('Another antivirus may be the primary provider', row['DeviceSummary']['text'])
        self.assertIn('Mode: passive', html)
        self.assertIn('Reported provider: Example AV', html)
        self.assertIn('Off', html)
        self.assertNotIn('Review the linked evidence and finding', html)

    def test_missing_provider_is_not_reported_as_healthy_or_as_no_antivirus(self):
        example = device_example({'realTimeProtectionEnabled': 'False'}, {}, [], 'DefenderRealTimeProtectionObservation')
        self.assertEqual(example['status'], 'Off')
        self.assertIn('Other antivirus provider not verified', example['detail'])
        self.assertIn('Defender mode not retained', example['detail'])

    def test_generic_examples_preserve_error_status_and_diagnostics(self):
        result = {'recommendations': [{'RecommendationId': 'x'}], 'actions': [{'RecommendationId': 'x'}]}
        bundle = {'finding_evidence': {'tables': {'t': {'rows': [
            {'finding_id': 'x', 'displayName': 'Example policy', 'status': 'error', 'errorCode': 42,
             'failureReason': 'Evidence unavailable'}]}}}}
        html = render_technical_appendix(result, bundle, 'a.xlsx', '',
               {'x': {'evidence': {'tables': ['t'], 'record_count': 1, 'record_unit': 'records'}}}, [])
        self.assertIn('Error', html)
        self.assertIn('Code: 42; Evidence unavailable', html)

    def test_zero_is_not_presented_as_an_error_code(self):
        self.assertEqual(error_detail({'errorCode': 0}), '')
        self.assertEqual(error_detail({'errorCode': '0'}), '')

    def test_overall_compliant_inventory_does_not_hide_failed_setting(self):
        item = {'record': {'state': 'noncompliant'}, 'policy': 'Example policy',
                'setting': 'BitLockerEnabled', 'failed': True}
        example = device_example({'complianceState': 'compliant'}, {'settings': [item]}, [], 'InventoryComplianceState')
        self.assertEqual(example['status'], 'Overall: Compliant; setting: Not compliant')


if __name__ == '__main__':
    unittest.main()

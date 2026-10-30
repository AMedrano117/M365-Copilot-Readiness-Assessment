"""Separate inventory context, failed requirements, incomplete checks and missing configuration."""
from copy import deepcopy
import unittest

from test_device_summary_presentation import scenario, assessment
from test_device_example_details import render
from test_evidence_reconciliation import DAY, dataset
from Core.customer_report import render_customer_report
from Core.pilot_summary import render_pilot_summary
from Core.finding_evidence import build_finding_evidence
from Core.evidence_selection import build_evidence_selection
from Core.supporting_context import finding_context


class ComplianceContextTests(unittest.TestCase):
    def test_saved_null_settings_make_one_explained_confirmation_card(self):
        bundle = scenario()
        bundle['assessment_sources']['device_compliance_settings'] = [dataset([{'settings': None}])]
        original = deepcopy(bundle['assessment_sources'])
        rows, result = assessment(bundle)
        config = [row for row in result['recommendations'] if row.get('ConfigurationSetting')]
        self.assertEqual(len(config), 1)
        self.assertEqual(config[0]['Feature'], 'Confirm tenant compliance settings')
        self.assertEqual(len(config[0]['ConfigurationSettings']), 2)
        html = render_customer_report(result, bundle, 'Fictional tenant')
        self.assertIn('saved API response did not include tenant compliance settings', html)
        self.assertIn('Why confirmation is needed', html)
        model = build_finding_evidence(build_evidence_selection(result, bundle))
        finding = next(row for row in model['findings'] if row['finding_id'] == config[0]['RecommendationId'])
        self.assertEqual(finding['evidence']['availability'], 'partial')
        self.assertEqual(bundle['assessment_sources'], original)

    def test_inventory_is_retained_but_not_repeated_as_another_customer_finding(self):
        bundle = scenario(); _, result = assessment(bundle)
        inventory = [row for row in result['recommendations'] if row.get('DeviceFindingType') == 'InventoryComplianceState']
        self.assertEqual(len(inventory), 1)
        html = render_customer_report(result, bundle, 'Fictional tenant')
        self.assertIn('Device compliance at a glance', html)
        self.assertNotIn('<h4>Intune device inventory context</h4>', html)
        _, appendix = render(bundle)
        self.assertNotIn('<summary>Reconcile device compliance inventory</summary>', appendix)
        self.assertTrue(inventory[0]['InvestigationEvidence']['records'])

    def test_incomplete_check_groups_do_not_become_noncompliance_causes(self):
        bundle = scenario()
        raw = bundle['assessment_sources']['compliance_setting_states'][0]['records']
        raw[0].update(state='error', settingName='Has a compliance policy assigned')
        raw.append(dict(raw[0], id='duplicate-error'))
        _, result = assessment(bundle)
        row = next(row for row in result['recommendations'] if row.get('DeviceFindingType') == 'ReportingErrorOrConflict')
        summary = row['DeviceSummary']
        self.assertEqual(summary['diagnostic_groups'][0]['checks'], 1)
        self.assertEqual(summary['diagnostic_groups'][0]['devices'], 1)
        self.assertNotIn('Error', summary['text'])
        self.assertIn('separate from explicit noncompliant results', summary['text'])
        self.assertTrue(all(item['record']['state'] == 'error' for item in result['device_compliance']['settings'] if item['classification']=='ReportingErrorOrConflict'))

    def test_authentication_context_is_assigned_once_to_relevant_cards(self):
        result = {'authentication_assessment': {'registration': {'denominator': 10, 'counts': {'ExplicitlyRegistered': 7}},
                  'enforcement': {'operational_result': 'unknown'}, 'observed_authentication': {'legacy': {'counts': {'Succeeded': 2}}}}}
        rows = [{'RecommendationId':'MFA', 'FindingKey':'entra.authentication.mfa_registration'},
                {'RecommendationId':'CA', 'ControlId':'IDENTITY.AUTH'},
                {'RecommendationId':'LEGACY', 'FindingKey':'entra.signins.legacy_auth'}]
        before = deepcopy(result)
        contexts = finding_context(result, rows)
        self.assertEqual(set(contexts), {'MFA','CA','LEGACY'})
        self.assertIn('MFA registration', contexts['MFA'])
        self.assertNotIn('Observed authentication', contexts['MFA'])
        self.assertIn('MFA enforcement', contexts['CA'])
        self.assertIn('Observed authentication', contexts['LEGACY'])
        self.assertEqual(result, before)


if __name__ == '__main__':
    unittest.main()

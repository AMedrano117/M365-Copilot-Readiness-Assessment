import copy
import unittest

from Core.customer_report import _remaining_portal_review
from Core.purview_billing_guidance import build_purview_billing_guidance
from Core.data_exposure_assessment import build_data_exposure_assessment


class PurviewBillingGuidanceTests(unittest.TestCase):
    def test_customer_guidance_separates_evidence_collection_from_optional_metered_work(self):
        guidance = build_purview_billing_guidance()
        bundle = {'purview_billing_guidance': guidance}
        before = copy.deepcopy(bundle)
        html = _remaining_portal_review(bundle)
        for phrase in ('DSPM scope and billing', 'top 100 SharePoint sites', 'Potentially metered scan',
                       'Billing entitlement and active meters were not verified', 'purview-billing-usage'):
            self.assertIn(phrase, html)
        self.assertEqual(bundle, before)

    def test_missing_dspm_requests_existing_results_without_requiring_payg(self):
        result = build_data_exposure_assessment([], [], evaluation_date='2026-10-12')
        finding = next(row for row in result['recommendations'] if row['Feature'] == 'Sensitive-data exposure evidence')
        self.assertIn('existing completed', finding['Recommendation'])
        self.assertIn('do not enable PAYG solely', finding['Recommendation'])
        self.assertEqual(finding['Status'], 'Not Assessed')


if __name__ == '__main__':
    unittest.main()

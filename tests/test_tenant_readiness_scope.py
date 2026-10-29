"""Tenant readiness does not depend on pilot planning or business-case approval."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from provenance_fixture import fictional_publication
from test_readiness_progress import review_profile, source_record, DAY, TENANT
from test_device_summary_presentation import scenario
from Core.assessment_result import build_assessment_result
from Core.expanded_findings import expanded_findings


def assess(profile=None, bundle=None, rows=None):
    bundle = deepcopy(bundle or {})
    bundle.update(evaluation_date=DAY, expected_tenant_id=TENANT)
    if profile is not None:
        bundle['assessment_profile'] = deepcopy(profile)
    rows = deepcopy(rows or [])
    for row in rows:
        name = row.setdefault('EvidenceSource', 'fictional.' + row.get('FindingKey', 'review'))
        bundle.setdefault('source_statuses', {})[name] = {'availability_status': 'available', 'complete': True,
            'tenant_id': TENANT, 'scope': row.get('EvidenceScope', ''), 'collected_at': '2026-09-10',
            'source_file': 'fictional-reviewed-records.json'}
    native = expanded_findings(bundle, profile=profile, evaluation_date=DAY)
    return build_assessment_result([*(rows or []), *native], bundle,
        evaluation_date=DAY, expected_tenant_id=TENANT), bundle


class TenantReadinessScopeTests(unittest.TestCase):
    def test_tenant_reviews_can_establish_readiness_without_any_planning_inputs(self):
        profile = review_profile()
        profile.pop('use_cases')
        for key in ('pilot_scope', 'pilot_plan'):
            profile['readiness_review'].pop(key)
        result, _ = assess(profile)
        self.assertEqual(result['decision'], 'Tenant baseline ready')
        self.assertEqual(result['rollout_progress']['current_stage_id'], 'assessed')
        self.assertFalse(result['rollout_progress']['next_requirements'])
        self.assertNotIn('ADOPTION.BASELINE', {row['control_id'] for row in result['controls']})

    def test_optional_incomplete_planning_does_not_discard_valid_tenant_reviews(self):
        profile = review_profile()
        profile['readiness_review']['pilot_scope'] = ['Unfinished optional planning']
        profile['readiness_review']['pilot_plan'] = {'baseline': 'Unfinished business work'}
        result, _ = assess(profile)
        self.assertEqual(result['decision'], 'Tenant baseline ready')
        self.assertEqual(result['readiness_review']['optional_planning_context']['pilot_scope'], ['Unfinished optional planning'])

    def test_no_evidence_and_usage_do_not_establish_tenant_readiness(self):
        result, _ = assess()
        self.assertEqual(result['decision'], 'Tenant readiness unconfirmed')
        self.assertTrue(result['rollout_progress']['next_requirements'])
        self.assertFalse(any('pilot' in row['id'] for row in result['rollout_progress']['next_requirements']))

    def test_pilot_planning_cannot_close_real_failures(self):
        result, _ = assess(review_profile(), rows=[source_record()])
        self.assertEqual(result['decision'], 'Tenant remediation required')
        self.assertTrue(result['blockers'])
        self.assertEqual(result['blockers'][0]['PilotImpact'], 'Blocks readiness')

    def test_pilot_scoped_reviews_do_not_establish_tenant_wide_controls(self):
        profile = review_profile()
        for row in profile['readiness_review']['control_reviews']:
            row['scope_id'] = 'pilot'
        result, _ = assess(profile)
        self.assertEqual(result['decision'], 'Tenant readiness unconfirmed')
        self.assertTrue(result['readiness_review']['optional_planning_context']['control_reviews'])

    def test_wrong_tenant_is_still_rejected_with_incomplete_optional_plan(self):
        profile = review_profile()
        profile['readiness_review']['pilot_plan'] = {}
        with self.assertRaisesRegex(ValueError, 'tenant ID'):
            build_assessment_result([], {'assessment_profile': profile}, evaluation_date=DAY,
                                    expected_tenant_id='22222222-2222-2222-2222-222222222222')

    def test_malformed_tenant_control_scope_stays_invalid(self):
        profile = review_profile()
        profile['readiness_review']['control_reviews'][0]['scope_id'] = []
        result, _ = assess(profile)
        self.assertEqual(result['readiness_review']['status'], 'invalid')
        self.assertNotEqual(result['decision'], 'Tenant baseline ready')

    def test_missing_pilot_membership_is_not_a_device_gap(self):
        bundle = scenario()
        bundle['evaluation_date'] = '2026-10-27'
        result = build_assessment_result(expanded_findings(bundle, evaluation_date='2026-10-27'), bundle,
            evaluation_date='2026-10-27', expected_tenant_id=bundle['expected_tenant_id'])
        self.assertFalse(any(row.get('DeviceFindingType') == 'PilotDevicePostureNotEstablished'
                             for row in result['recommendations']))

    def test_current_device_failures_remain_blocking_without_or_outside_a_pilot(self):
        for with_roster in (False, True):
            bundle = scenario()
            if with_roster:
                bundle['assessment_profile'] = {'readiness_review': {'version': '1.0',
                    'tenant_id': bundle['expected_tenant_id'], 'pilot_scope': {'id': 'optional-pilot',
                    'description': 'Unrelated optional group', 'reviewed_at': '2026-10-27',
                    'reviewer_role': 'Technical owner', 'evidence_reference': 'Fictional optional roster',
                    'intune_device_ids': ['another-device'], 'membership_complete': True}}}
            rows = expanded_findings(bundle, profile=bundle.get('assessment_profile'), evaluation_date='2026-10-27')
            result = build_assessment_result(rows, bundle, evaluation_date='2026-10-27',
                                             expected_tenant_id=bundle['expected_tenant_id'])
            current = next(row for row in result['actions'] if row.get('DeviceFindingType') == 'ExplicitSecuritySettingFailure')
            self.assertIn(current, result['blockers'])
            self.assertEqual(current['PilotImpact'], 'Blocks readiness')
            self.assertFalse(any(row.get('DeviceFindingType') in {'UnconfirmedSettingState', 'ActivityOrEnrollmentFailure'}
                                 for row in result['blockers']))

    def test_saved_planning_requests_become_reference_context(self):
        row = source_record(FindingKey='adoption.pilot_plan', ControlId='ADOPTION.BASELINE',
                            Feature='Agree pilot business use cases', Disposition='Coverage', OriginalDisposition='Action')
        result, _ = assess(review_profile(), rows=[row])
        self.assertEqual(result['decision'], 'Tenant baseline ready')
        planning = next(item for item in result['recommendations'] if item['FindingKey'] == row['FindingKey'])
        self.assertEqual(planning['Disposition'], 'Reference')
        self.assertEqual(planning['OriginalDisposition'], 'Action')
        self.assertFalse(any(item['FindingKey'] == row['FindingKey'] for item in result['actions']))

    def test_business_use_case_review_is_outside_tenant_assessment(self):
        from Core.cross_provider_assessment import assess_provider_and_use_cases
        result = assess_provider_and_use_cases({'available': True, 'products': [],
            'use_cases': [{'name': 'Unfinished business case'}]}, {}, 'Tenant baseline ready')
        self.assertEqual(result['use_case_conclusion'], 'Outside assessment scope')
        self.assertEqual(result['use_cases'][0]['Readiness'], 'Outside assessment scope')

    def test_legacy_rollout_scope_cannot_establish_a_technical_delta(self):
        from Core.run_comparability import evaluate_comparability
        current, _ = assess(review_profile())
        baseline = deepcopy(current)
        baseline.pop('assessment_scope')
        baseline.pop('assessment_scope_version')
        result = evaluate_comparability(current, baseline)
        self.assertEqual(result['Outcome'], 'NotComparable')
        self.assertIn('assessment_scope_changed', {row['Code'] for row in result['Reasons']})
        self.assertNotIn('assessment_scope_changed', {row['Code'] for row in evaluate_comparability(current, deepcopy(current))['Reasons']})


@fictional_publication
class TenantScopeOutputsTests(unittest.TestCase):
    def test_legacy_frozen_presentation_keeps_recorded_pilot_scope(self):
        from Core.customer_report import render_customer_report
        from Core.readiness_progress import build_rollout_progress
        result, bundle = assess(review_profile())
        result.pop('assessment_scope')
        result['decision'] = 'Ready for a controlled pilot'
        result['rollout_progress'] = build_rollout_progress(result, result['readiness_review'])
        before = deepcopy(result)
        source = render_customer_report(result, bundle, 'Fictional historical tenant')
        self.assertIn('Choose and run the pilot', source)
        self.assertEqual(result, before)

    def test_reports_and_workbooks_use_tenant_scope_without_pilot_requirements(self):
        from Core.export_recommendations import export_to_excel, export_to_html
        from Core.workbook_layout import technical_workbook_path
        from openpyxl import load_workbook
        result, bundle = assess(review_profile(), rows=[source_record(Disposition='Reference', Status='Reference', Priority='Low')])
        bundle['assessment_result'] = result
        with TemporaryDirectory() as folder:
            workbook = Path(export_to_excel(result['recommendations'], filename='Tenant.xlsx', evidence_bundle=bundle, output_dir=folder))
            html = Path(export_to_html(result['recommendations'], filename='Tenant.html', evidence_bundle=bundle, output_dir=folder, excel_path=workbook))
            source = html.read_text(encoding='utf-8')
            self.assertIn('Tenant baseline ready', source)
            self.assertIn('handled outside this assessment', source)
            for text in ('Choose and run the pilot', 'Record the pilot group that ran', 'Confirm pilot-device posture',
                         'Review the pilot use cases, baseline and success measures', 'For broader adoption, record the pilot outcomes'):
                self.assertNotIn(text, source)
            for path in (workbook, technical_workbook_path(workbook)):
                book = load_workbook(path)
                try:
                    self.assertTrue(any('Tenant baseline ready' in str(cell.value)
                        for sheet in book for row in sheet for cell in row))
                finally:
                    book.close()

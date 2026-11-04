"""Declared SharePoint governance scenarios (fictional data only).

Expectations are declared below before production logic runs. Every scenario
asserts capability state, evidence sufficiency, technical result, readiness
treatment, affected-site and issue counts, overlap qualification, workstream
action counts, SAM-assisted and non-SAM guidance, and prohibited wording.
"""
import copy
import re
import unittest

try:
    from tests import sharepoint_governance_scenarios as h
    from tests.provenance_fixture import fictional_publication
except ImportError:  # unittest discover -s tests imports modules from the tests folder
    import sharepoint_governance_scenarios as h
    from provenance_fixture import fictional_publication

from Core.data_exposure_assessment import build_data_exposure_assessment
from Core.sharepoint_governance import build_copilot_content_control_recommendations, build_sharepoint_recommendations

ALL = ('site_owners', 'inactive_sites', 'owner_review_process', 'broad_access', 'special_groups', 'broken_inheritance',
       'external_access', 'high_risk_sites', 'exceptions', 'sensitive_content')
OWNERSHIP = ('site_owners', 'inactive_sites', 'owner_review_process')
PROHIBITED = (r'(?i)SharePoint Advanced Management (licen[cs]e |licensing )?is (required|mandatory)',
              r'(?i)(must|need to) (license|purchase|buy) SharePoint Advanced Management', r'(?i)SAM licensing is mandatory',
              r'(?i)cannot be assessed', r'(?i)isolated from Copilot')
LICENSED = {'sam_license': 'Licensed', 'entitlement_verified': True, 'basis': 'license_inventory', 'state': 'Available'}
NOT_LICENSED = {'sam_license': 'NotLicensed', 'basis': 'customer_declaration'}


def site_level_only():
    section = h.cma(lifecycle=False, oversharing=False, include_sites=False, sites=copy.deepcopy(h.SITES))
    section.pop('total_sites_assessed')
    return section


def conflicting_totals():
    section = h.cma()
    section['lifecycle']['distinct_sites'] = h.sites(50)  # more than the 40 sites assessed
    return section


def stale_dag():
    return {'sources': {'sam': {'reports': [{'status': 'selected', 'report_type': 'permission_snapshot', 'freshness': 'stale'}]}}}


def outcome(result, path, sufficiency, *, treatment=None, affected=None, issues=None, overlap=False, ids=None):
    return {'result': result, 'path': path, 'sufficiency': sufficiency, 'treatment': treatment,
            'affected': affected, 'issues': issues, 'overlap': overlap, 'ids': ids}


REQUEST = outcome('EvidenceRequest', 'none', 'SAM-assisted', treatment='EvidenceGap')
MISSING = outcome('NotEstablished', 'none', 'No evidence', treatment='EvidenceGap')
REVIEW = 'Sufficient for the affected population'
INSUFFICIENT = 'Insufficient'
# The fictional equivalent evidence declares zero affected sites and zero issues.
SUPPORTED = outcome('Supported', 'equivalent', 'Sufficient', affected=0, issues=0)

# Base CMA outcomes (site-level, current, complete).
BASE_CMA = {
    'site_owners': outcome('Failed', 'sam', 'Sufficient', treatment='Remediate', affected=3),
    'inactive_sites': outcome('NotEstablished', 'sam', REVIEW, treatment='ScopeReviewRequired', affected=4, issues=4),
    'special_groups': outcome('NotEstablished', 'sam', REVIEW, treatment='ScopeReviewRequired', affected=3, issues=12, overlap=True,
                              ids=['s09', 's10', 's11']),
    'broken_inheritance': outcome('NotEstablished', 'sam', REVIEW, treatment='ScopeReviewRequired', affected=3, issues=9,
                                  ids=['s07', 's08', 's09']),
}
BASE_ACTIONS = {'sharepoint.validate_evidence': 1, 'sharepoint.high_risk_access': 2, 'sharepoint.site_governance': 2}
GAP_ACTIONS = {'sharepoint.validate_evidence': 2, 'sharepoint.high_risk_access': 0, 'sharepoint.site_governance': 1}
# Unusable site-level evidence: counts are retained but establish nothing.
INSUFFICIENT_CMA = {name: dict(BASE_CMA[name], result='NotEstablished', sufficiency=INSUFFICIENT, treatment='EvidenceGap')
                    for name in ('site_owners', 'inactive_sites', 'special_groups', 'broken_inheritance')}
# Summary only: category counts are lower bounds that can overlap; no site IDs.
SUMMARY_ONLY = {'site_owners': outcome('NotEstablished', 'sam', INSUFFICIENT, treatment='EvidenceGap', affected=3, overlap=True),
                'inactive_sites': outcome('NotEstablished', 'sam', INSUFFICIENT, treatment='EvidenceGap', affected=4, issues=4, overlap=True),
                'special_groups': outcome('NotEstablished', 'sam', INSUFFICIENT, treatment='EvidenceGap', affected=2, issues=12, overlap=True),
                'broken_inheritance': outcome('NotEstablished', 'sam', INSUFFICIENT, treatment='EvidenceGap', affected=3, issues=9, overlap=True)}
INSUFFICIENT_ACTIONS = {'sharepoint.validate_evidence': 2, 'sharepoint.high_risk_access': 0, 'sharepoint.site_governance': 0}

SCENARIOS = {
    'S01_sam_available_complete': dict(
        documents=[h.document(capability=LICENSED, content_management_assessment=h.cma())],
        capability='Available', outcomes=BASE_CMA, actions=BASE_ACTIONS,
        controls={'CONTENT.OWNERSHIP': 'Action required', 'CONTENT.PERMISSIONS': 'Not established'}),
    'S02_sam_available_not_run': dict(
        documents=[h.document(capability=LICENSED)], capability='Available',
        outcomes={name: REQUEST for name in ALL}, actions=GAP_ACTIONS,
        controls={'CONTENT.OWNERSHIP': 'Not established', 'CONTENT.PERMISSIONS': 'Not established'}),
    'S03_sam_partially_available': dict(
        documents=[h.document(capability=dict(LICENSED, state='PartiallyAvailable'), content_management_assessment=h.cma(oversharing=False))],
        capability='PartiallyAvailable',
        outcomes={'site_owners': BASE_CMA['site_owners'], 'inactive_sites': BASE_CMA['inactive_sites'],
                  'broken_inheritance': REQUEST, 'special_groups': REQUEST},
        actions={'sharepoint.validate_evidence': 2, 'sharepoint.high_risk_access': 0, 'sharepoint.site_governance': 2},
        controls={'CONTENT.OWNERSHIP': 'Action required', 'CONTENT.PERMISSIONS': 'Not established'}),
    'S04_dag_without_full_sam': dict(
        exposure={'sources': {'sam': {'reports': [{'status': 'selected', 'report_type': 'permission_snapshot', 'freshness': 'fresh'}]}}},
        capability='DAGOnly', entitlement=False, outcomes={name: REQUEST for name in ALL}, actions=GAP_ACTIONS,
        controls={'CONTENT.PERMISSIONS': 'Not established'}),
    'S05_not_licensed_equivalent_sufficient': dict(
        documents=[h.document(capability=NOT_LICENSED, equivalent_evidence=[h.equivalent(name) for name in ALL])],
        capability='NotLicensed', outcomes={name: SUPPORTED for name in ALL},
        actions={'sharepoint.validate_evidence': 1, 'sharepoint.high_risk_access': 0, 'sharepoint.site_governance': 0},
        controls={'CONTENT.OWNERSHIP': 'Observed', 'CONTENT.PERMISSIONS': 'Observed', 'DATA.EXPOSURE': 'Observed'}),
    'S06_not_licensed_no_equivalent': dict(
        documents=[h.document(capability=NOT_LICENSED)], capability='NotLicensed',
        outcomes={name: MISSING for name in ALL}, actions=GAP_ACTIONS,
        controls={'CONTENT.OWNERSHIP': 'Not established', 'CONTENT.PERMISSIONS': 'Not established'}),
    'S07_entitlement_unknown': dict(
        capability='Unknown', capability_result='CapabilityUnknown', outcomes={name: MISSING for name in ALL},
        actions=GAP_ACTIONS, controls={'CONTENT.OWNERSHIP': 'Not established', 'CONTENT.PERMISSIONS': 'Not established'}),
    'S08_portal_summary_without_sites': dict(
        documents=[h.document(content_management_assessment=h.cma(include_sites=False))], capability='PartiallyAvailable',
        outcomes=SUMMARY_ONLY,
        actions=INSUFFICIENT_ACTIONS, controls={'CONTENT.OWNERSHIP': 'Not established', 'CONTENT.PERMISSIONS': 'Not established'}),
    'S09_sites_without_portal_summary': dict(
        documents=[h.document(content_management_assessment=site_level_only())], capability='PartiallyAvailable',
        outcomes={'site_owners': outcome('NotEstablished', 'sam', REVIEW, treatment='ScopeReviewRequired', affected=3),
                  'inactive_sites': outcome('NotEstablished', 'sam', REVIEW, treatment='ScopeReviewRequired', affected=4),
                  'special_groups': outcome('NotEstablished', 'sam', REVIEW, treatment='ScopeReviewRequired', affected=3, overlap=True),
                  'broken_inheritance': outcome('NotEstablished', 'sam', REVIEW, treatment='ScopeReviewRequired', affected=3)},
        actions={'sharepoint.validate_evidence': 1, 'sharepoint.high_risk_access': 2, 'sharepoint.site_governance': 2}),
    'S10_overlapping_lifecycle_categories': dict(
        documents=[h.document(content_management_assessment=h.cma())], capability='PartiallyAvailable',
        outcomes=BASE_CMA, actions=BASE_ACTIONS, distinct={'lifecycle': 6}),
    'S11_overlapping_oversharing_categories': dict(
        documents=[h.document(content_management_assessment=h.cma())], capability='PartiallyAvailable',
        outcomes={'special_groups': BASE_CMA['special_groups']}, actions=BASE_ACTIONS, distinct={'oversharing': 5}),
    'S12_current_summary_stale_dag': dict(
        documents=[h.document(content_management_assessment=h.cma())], exposure=stale_dag(), capability='PartiallyAvailable',
        outcomes=BASE_CMA, actions=BASE_ACTIONS, current_rows=True),
    'S13_one_site_in_several_categories': dict(
        documents=[h.document(content_management_assessment=h.cma())], capability='PartiallyAvailable',
        outcomes={'special_groups': BASE_CMA['special_groups'], 'broken_inheritance': BASE_CMA['broken_inheritance']},
        actions=BASE_ACTIONS),
    'S14_lifecycle_policy_in_simulation': dict(
        documents=[h.document(policies=[{'type': 'inactive_site', 'state': 'Simulation', 'scope': 'All sites'}])],
        capability='PartiallyAvailable', entitlement=False, outcomes={'inactive_sites': REQUEST}, actions=GAP_ACTIONS),
    'S15_active_ownership_and_attestation_policy': dict(
        documents=[h.document(policies=[{'type': 'site_ownership', 'state': 'Active'}, {'type': 'site_attestation', 'state': 'Active'}])],
        capability='PartiallyAvailable', entitlement=False,
        outcomes={'site_owners': REQUEST, 'owner_review_process': REQUEST}, actions=GAP_ACTIONS),
    'S16_restricted_discovery_configured': dict(
        governance=lambda: h._governance(sites_rows=[{'Url': 'https://fictional.example/sites/x', 'RestrictContentOrgWideSearch': True}]),
        capability='PartiallyAvailable', entitlement=False, outcomes={'special_groups': REQUEST}, actions=GAP_ACTIONS),
    'S17_restricted_capability_not_licensed': dict(
        documents=[h.document(capability=NOT_LICENSED)],
        governance=lambda: h._governance(settings={'EnableRestrictedAccessControl': False}), content_controls=True,
        capability='NotLicensed', outcomes={'special_groups': MISSING}, actions=GAP_ACTIONS,
        required_text=(r'Without it, limit those sites',)),
    'S18_confirmed_exposure_without_sam': dict(
        documents=[h.document(capability=NOT_LICENSED, equivalent_evidence=[h.equivalent('special_groups', 'not_satisfied')])],
        capability='NotLicensed',
        outcomes={'special_groups': outcome('Failed', 'equivalent', 'Sufficient', treatment='Remediate', affected=2, issues=3)},
        actions={'sharepoint.validate_evidence': 1, 'sharepoint.high_risk_access': 1, 'sharepoint.site_governance': 1},
        controls={'CONTENT.PERMISSIONS': 'Action required'}),
    'S19_missing_sam_never_fails': dict(
        documents=[h.document(capability=NOT_LICENSED)], producers=True, capability='NotLicensed',
        outcomes={name: MISSING for name in OWNERSHIP},
        actions={'sharepoint.validate_evidence': 2, 'sharepoint.high_risk_access': 0, 'sharepoint.site_governance': 1},
        controls={'CONTENT.OWNERSHIP': 'Not established', 'CONTENT.PERMISSIONS': 'Not established', 'CONTENT.SHARING': 'Not established'}),
    'S20_equivalent_evidence_supports_control': dict(
        documents=[h.document(capability=NOT_LICENSED, equivalent_evidence=[h.equivalent(name) for name in OWNERSHIP])],
        capability='NotLicensed', outcomes={name: SUPPORTED for name in OWNERSHIP}, actions=GAP_ACTIONS | {'sharepoint.site_governance': 0},
        controls={'CONTENT.OWNERSHIP': 'Observed', 'CONTENT.PERMISSIONS': 'Not established'}),
    'S21_missing_assessment_date': dict(
        documents=[h.document(content_management_assessment=h.cma(assessment_date=''))], capability='PartiallyAvailable',
        outcomes=INSUFFICIENT_CMA, actions=INSUFFICIENT_ACTIONS, diagnostics={'missing_assessment_date'}),
    'S22_conflicting_totals': dict(
        documents=[h.document(content_management_assessment=conflicting_totals())],
        capability='PartiallyAvailable', outcomes=INSUFFICIENT_CMA,
        actions=INSUFFICIENT_ACTIONS, diagnostics={'conflicting_totals'}),
    'S23_duplicate_site_records': dict(
        documents=[h.document(content_management_assessment=h.cma(sites=copy.deepcopy(h.SITES) + [copy.deepcopy(h.SITES[0])]))],
        capability='PartiallyAvailable', outcomes={'inactive_sites': BASE_CMA['inactive_sites']},
        actions={'sharepoint.validate_evidence': 2, 'sharepoint.high_risk_access': 2, 'sharepoint.site_governance': 2},
        diagnostics={'duplicate_site_records'}),
    'S24_unknown_schema': dict(
        documents=[dict(h.document(content_management_assessment=h.cma()), schema_version='9.9')], capability='Unknown',
        outcomes={name: MISSING for name in ALL},
        actions={'sharepoint.validate_evidence': 3, 'sharepoint.high_risk_access': 0, 'sharepoint.site_governance': 1},
        diagnostics={'unknown_schema'}),
    'S25_incomplete_export': dict(
        documents=[h.document(content_management_assessment=h.cma(completion_state='Running'))], capability='PartiallyAvailable',
        outcomes=INSUFFICIENT_CMA, actions=INSUFFICIENT_ACTIONS, diagnostics={'incomplete_export'}),
}


def execute(spec):
    governance = spec['governance']() if callable(spec.get('governance')) else spec.get('governance')
    rows = []
    if spec.get('content_controls'):
        rows += build_copilot_content_control_recommendations(governance)
    if spec.get('producers'):
        rows += build_data_exposure_assessment(sam_report_paths=[], dspm_report_paths=[], enabled=True,
                                               evaluation_date=h.EVALUATION)['recommendations']
        rows += build_sharepoint_recommendations({'tenant': {'available': False}}, evaluation_date=h.EVALUATION)
    return h.run(spec.get('documents', ()), governance=governance, exposure=spec.get('exposure'), extra_rows=rows)


@fictional_publication
class SharePointGovernanceScenarioTests(unittest.TestCase):
    def check(self, name):
        spec = SCENARIOS[name]
        result, assessment, _ = execute(spec)
        model = result['sharepoint_governance']
        capability = model['capability']
        self.assertEqual(capability['state'], spec['capability'], 'capability state')
        if 'capability_result' in spec:
            self.assertEqual(capability['capability_result'], spec['capability_result'])
        if 'entitlement' in spec:
            self.assertIs(capability['entitlement_verified'], spec['entitlement'])
        for key, expected in spec['outcomes'].items():
            with self.subTest(outcome=key):
                actual = h.outcome(result, key)
                self.assertEqual(actual['technical_result'], expected['result'], 'technical result')
                self.assertEqual(actual['evidence_path'], expected['path'], 'evidence path')
                self.assertTrue(actual['evidence_sufficiency'].startswith(expected['sufficiency']),
                                f"evidence sufficiency {actual['evidence_sufficiency']!r}")
                self.assertEqual(actual['readiness_treatment'], expected['treatment'], 'readiness treatment')
                self.assertEqual(actual['affected_sites'], expected['affected'], 'affected-site count')
                self.assertEqual(actual['issue_count'], expected['issues'], 'issue count')
                self.assertEqual(bool(actual['overlap_qualification']), expected['overlap'], 'overlap qualification')
                if expected['ids'] is not None:
                    self.assertEqual(actual['affected_site_ids'], expected['ids'])
                self.assertTrue(actual['sam_path'] and actual['non_sam_path'] or actual['workstream'] == 'information_protection')
        actions = h.sharepoint_actions(result)
        counts = {workstream: sum(item['governance_workstream'] == workstream for item in actions) for workstream in spec['actions']}
        self.assertEqual(counts, spec['actions'], 'customer actions per workstream: '
                         + '; '.join(f"{item['governance_workstream']}: {item['title']}" for item in actions))
        for item in actions:
            self.assertTrue(item['sharepoint']['sam_path'], 'SAM-assisted recommendation')
            self.assertTrue(item['sharepoint']['non_sam_path'], 'non-SAM recommendation')
        for control, status in (spec.get('controls') or {}).items():
            self.assertEqual(h.control(result, control), status, control)
        text = h.customer_text(result)
        for pattern in PROHIBITED:
            self.assertNotRegex(text, pattern, 'prohibited wording')
        for pattern in spec.get('required_text', ()):
            self.assertRegex(' '.join(str(row.get('Recommendation')) for row in result['recommendations']), pattern)
        for code in spec.get('diagnostics', ()):
            self.assertIn(code, {item['code'] for item in model['diagnostics']})
        for group, value in (spec.get('distinct') or {}).items():
            metric = next(item for item in model['cma']['metrics'] if item['metric'] == f'Distinct sites requiring {group} attention')
            self.assertEqual((metric['numerator'], metric['unit'], metric['basis']), (value, 'sites', 'distinct'))
        if spec.get('current_rows'):
            rows = [row for row in result['recommendations'] if str(row.get('FindingKey')).startswith('sharepoint_governance.')]
            self.assertTrue(rows and all(row['EvidenceStatus'] == 'supported' for row in rows if row['Disposition'] == 'Action'))
        # No technical failure arises without sufficient evidence that an outcome is not satisfied.
        for item in model['outcomes']:
            if item['technical_result'] == 'Failed':
                self.assertIn(item['evidence_path'], {'sam', 'equivalent', 'other_source'})
                self.assertEqual(item['evidence_sufficiency'], 'Sufficient')


def _scenario_test(name):
    def test(self):
        self.check(name)
    test.__doc__ = name
    return test


for _name in SCENARIOS:
    setattr(SharePointGovernanceScenarioTests, 'test_' + _name, _scenario_test(_name))


if __name__ == '__main__':
    unittest.main()

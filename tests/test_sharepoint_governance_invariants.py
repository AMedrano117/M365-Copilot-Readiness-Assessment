"""Invariants for SharePoint governance evidence (fictional data only)."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

try:
    from tests import sharepoint_governance_scenarios as h
    from tests.provenance_fixture import fictional_publication
except ImportError:  # unittest discover -s tests imports modules from the tests folder
    import sharepoint_governance_scenarios as h
    from provenance_fixture import fictional_publication

from Core.assessment_result import build_assessment_result
from Core.customer_actions import build_customer_actions, readiness_outcome
from Core.customer_report import _governance_outcomes, table
from Core.portal_report_import import route_portal_reports
from Core.sharepoint_governance_evidence import (OUTCOMES, is_governance_evidence, load_governance_evidence, sam_capability)
from Core.workbook_layout import _sharepoint_governance_rows

LICENSED = {'sam_license': 'Licensed', 'entitlement_verified': True, 'basis': 'license_inventory', 'state': 'Available'}
NOT_LICENSED = {'sam_license': 'NotLicensed', 'basis': 'customer_declaration'}
CONTENT_CONTROLS = ('CONTENT.OWNERSHIP', 'CONTENT.PERMISSIONS', 'CONTENT.SHARING', 'DATA.EXPOSURE')


def load(section):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder, 'governance.json')
        path.write_text(json.dumps(h.document(content_management_assessment=section)), encoding='utf-8')
        return load_governance_evidence([path], evaluation_date=h.EVALUATION, expected_tenant_id=h.TENANT)


@fictional_publication
class SharePointGovernanceInvariantTests(unittest.TestCase):
    def test_sam_absence_never_independently_creates_failure(self):
        for capability in (NOT_LICENSED, {}, {'state': 'Unsupported'}, LICENSED):
            with self.subTest(capability=capability):
                result, _, _ = h.run([h.document(capability=capability)] if capability else ())
                self.assertFalse([item for item in result['sharepoint_governance']['outcomes'] if item['technical_result'] == 'Failed'])
                for control in CONTENT_CONTROLS:
                    self.assertNotEqual(h.control(result, control), 'Action required', control)
                self.assertFalse(result['blockers'])

    def test_portal_visibility_never_proves_entitlement(self):
        imported = {'capability': {'entitlement_verified': True, 'basis': 'portal_visibility', 'state': 'Available'}}
        capability = sam_capability({}, {}, imported)
        self.assertFalse(capability['entitlement_verified'])
        self.assertNotEqual(capability['state'], 'Available')

    def test_copilot_or_one_feature_never_establishes_full_sam(self):
        copilot = [{'Feature': 'Microsoft 365 Copilot in SharePoint'}]
        capability = sam_capability({}, {}, {}, copilot)
        self.assertEqual(capability['state'], 'Unknown')
        self.assertIn('not verified', capability['license_evidence'])
        one_feature = sam_capability({'settings': {'EnableRestrictedAccessControl': True}}, {}, {})
        self.assertEqual(one_feature['state'], 'PartiallyAvailable')
        self.assertFalse(one_feature['entitlement_verified'])

    def test_licensing_never_determines_governance_result(self):
        evidence = dict(content_management_assessment=h.cma(), equivalent_evidence=[h.equivalent('external_access', 'not_satisfied')])
        views = {}
        for label, capability in (('licensed', LICENSED), ('not_licensed', NOT_LICENSED), ('unknown', {})):
            result, _, _ = h.run([h.document(capability=capability, **evidence)])
            views[label] = {item['outcome']: (item['technical_result'], item['readiness_treatment'], item['affected_sites'], item['issue_count'])
                            for item in result['sharepoint_governance']['outcomes'] if item['evidence_path'] != 'none'}
            views[label + '_controls'] = {control: h.control(result, control) for control in CONTENT_CONTROLS}
        self.assertEqual(views['licensed'], views['not_licensed'])
        self.assertEqual(views['licensed'], views['unknown'])
        self.assertEqual(views['licensed_controls'], views['not_licensed_controls'])
        # Without evidence, licensing changes only how the request is phrased, never its treatment.
        for capability in (LICENSED, NOT_LICENSED):
            result, _, _ = h.run([h.document(capability=capability)])
            self.assertEqual({item['readiness_treatment'] for item in result['sharepoint_governance']['outcomes']}, {'EvidenceGap'})

    def test_equivalent_evidence_supports_the_control(self):
        result, _, _ = h.run([h.document(capability=NOT_LICENSED, equivalent_evidence=[
            h.equivalent(name) for name in ('site_owners', 'inactive_sites', 'owner_review_process')])])
        self.assertEqual(h.control(result, 'CONTENT.OWNERSHIP'), 'Observed')
        self.assertFalse([row for row in result['recommendations'] if row.get('FindingKey') == 'coverage.content.ownership'
                          and row.get('Disposition') == 'Coverage'])

    def test_equivalent_evidence_missing_one_dimension_stays_qualified(self):
        item = h.equivalent('site_owners')
        item['dimensions'].pop('ownership')
        result, _, _ = h.run([h.document(capability=NOT_LICENSED, equivalent_evidence=[item])])
        owners = h.outcome(result, 'site_owners')
        self.assertEqual(owners['technical_result'], 'NotEstablished')
        self.assertIn('ownership', owners['evidence_sufficiency'])
        stale = h.equivalent('site_owners', observed_at='2025-01-01')
        result, _, _ = h.run([h.document(capability=NOT_LICENSED, equivalent_evidence=[stale])])
        self.assertIn('current observation date', h.outcome(result, 'site_owners')['evidence_sufficiency'])

    def test_no_sam_and_no_equivalent_is_not_established(self):
        result, _, _ = h.run([h.document(capability=NOT_LICENSED)])
        self.assertEqual({item['technical_result'] for item in result['sharepoint_governance']['outcomes']}, {'NotEstablished'})

    def test_issue_counts_never_added_into_distinct_site_counts(self):
        cma = load(h.cma())['cma']
        for metric in cma['metrics']:
            self.assertEqual((metric['unit'], metric['basis']) in {('sites', 'distinct'), ('issues', 'occurrence')}, True, metric)
        result, _, _ = h.run([h.document(content_management_assessment=h.cma())])
        special = h.outcome(result, 'special_groups')
        # EEEU (2 sites) and links (2 sites) share one site: 3 distinct, never 4.
        self.assertEqual(special['affected_sites'], 3)
        self.assertEqual(len(set(special['affected_site_ids'])), 3)
        oversharing = next(item for item in cma['metrics'] if item['metric'] == 'Distinct sites requiring oversharing attention')
        self.assertEqual(oversharing['numerator'], 5)  # not 3 + 2 + 2
        section = h.cma()
        section['oversharing']['distinct_sites'] = {'value': 21, 'unit': 'issues', 'basis': 'occurrence'}
        imported = load(section)
        self.assertIn('issue_total_as_sites', {item['code'] for item in imported['diagnostics']})
        self.assertIsNone(imported['cma']['groups']['oversharing']['distinct_sites'])

    def test_portal_summary_never_replaces_site_level_evidence(self):
        result, _, _ = h.run([h.document(content_management_assessment=h.cma(include_sites=False))])
        for item in result['sharepoint_governance']['outcomes']:
            if item['evidence_path'] == 'sam':
                self.assertEqual(item['technical_result'], 'NotEstablished')
                self.assertFalse(item['affected_site_ids'])
        section = h.cma()
        section['oversharing']['categories']['eeeu']['affected_sites'] = h.sites(0)
        self.assertIn('conflicting_totals', {item['code'] for item in load(section)['diagnostics']})

    def test_malformed_imports_remain_qualified(self):
        def variant(change):
            section = h.cma()
            change(section)
            return section
        cases = {
            'negative_count': variant(lambda s: s['lifecycle']['categories']['inactive_sites'].update(affected_sites={'value': -1, 'unit': 'sites', 'basis': 'distinct'})),
            'count_without_unit': variant(lambda s: s['lifecycle']['categories']['inactive_sites'].update(issues={'value': 4})),
            'impossible_date': variant(lambda s: s.update(assessment_date='2026-02-31')),
            'future_dated': variant(lambda s: s.update(assessment_date='2026-12-01')),
            'stale': variant(lambda s: s.update(assessment_date='2026-01-01')),
            'conflicting_site_identifiers': variant(lambda s: s['sites'].append({'site_id': 's01', 'url': 'https://fictional.example/sites/other'})),
            'unknown_category': variant(lambda s: s['lifecycle']['categories'].update(unknown={'issues': h.issues(1)})),
        }
        for code, section in cases.items():
            with self.subTest(code=code):
                imported = load(section)
                self.assertIn(code, {item['code'] for item in imported['diagnostics']})
                result, _, _ = h.run([h.document(content_management_assessment=section)])
                rows = [row for row in result['recommendations'] if row.get('FindingKey') == 'sharepoint_governance.evidence']
                self.assertEqual(len(rows), 1, 'validation finding retained')
        with tempfile.TemporaryDirectory() as folder:
            first, second = Path(folder, 'a.json'), Path(folder, 'b.json')
            first.write_text(json.dumps(h.document(content_management_assessment=h.cma())), encoding='utf-8')
            second.write_text(json.dumps(h.document(content_management_assessment=h.cma(assessment_id='CMA-FICTIONAL-2'))), encoding='utf-8')
            runs = load_governance_evidence([first, second], evaluation_date=h.EVALUATION, expected_tenant_id=h.TENANT)
            other = Path(folder, 'c.json')
            other.write_text(json.dumps(dict(h.document(), tenant_id='ffffffff-0000-4000-8000-000000000000')), encoding='utf-8')
            mismatch = load_governance_evidence([other], evaluation_date=h.EVALUATION, expected_tenant_id=h.TENANT)
        self.assertIn('conflicting_assessment_runs', {item['code'] for item in runs['diagnostics']})
        self.assertFalse(runs['cma']['usable'])
        self.assertIn('tenant_mismatch', {item['code'] for item in mismatch['diagnostics']})
        self.assertFalse(mismatch['documents'])

    def test_one_semantic_condition_creates_one_action(self):
        result, _, _ = h.run([h.document(content_management_assessment=h.cma(include_sites=False))])
        evidence = [item for item in result['customer_actions'] if item['workstream'] == 'content.governance_evidence']
        self.assertEqual(len(evidence), 1)
        self.assertEqual(len(evidence[0]['members']), 4)
        rows = [{'RecommendationId': rid, 'FindingKey': key, 'Disposition': 'Coverage', 'ActionType': 'Evidence', 'Priority': 'High'}
                for rid, key in (('A-1', 'sharepoint.dag.not_assessed'), ('A-2', 'data_exposure.coverage.sharepoint_oversharing_evidence'))]
        self.assertEqual(len(build_customer_actions(rows, [])), 1)
        # Findings that differ in decision, closure or treatment are not collapsed.
        result, _, _ = h.run([h.document(content_management_assessment=h.cma())])
        governance = [item for item in result['customer_actions'] if item['governance_workstream'] == 'sharepoint.site_governance']
        self.assertEqual(sorted(item['lane'] for item in governance), ['FixNow', 'InvestigateAffectedObjects'])

    def test_customer_and_technical_outputs_agree(self):
        result, _, _ = h.run([h.document(content_management_assessment=h.cma())])
        outcomes, metrics, _ = _sharepoint_governance_rows(result)
        self.assertEqual([(row['Outcome'], row['Technical result']) for row in outcomes],
                         [(item['title'], item['technical_result']) for item in result['sharepoint_governance']['outcomes']])
        self.assertTrue(metrics)
        html = _governance_outcomes(result, table)
        for item in result['sharepoint_governance']['outcomes']:
            self.assertIn(item['title'], html)
        rows = {row['RecommendationId']: row for row in result['recommendations']}
        for action in h.sharepoint_actions(result):
            members = [rows[member['recommendation_id']] for member in action['members']]
            if len(members) == 1 and members[0].get('AffectedSiteCount') is not None:
                self.assertEqual(action['sharepoint']['affected_sites'], members[0]['AffectedSiteCount'])

    def test_technical_detail_is_preserved(self):
        result, assessment, _ = h.run([h.document(content_management_assessment=h.cma())])
        rows = [row for row in result['recommendations'] if str(row.get('FindingKey')).startswith('sharepoint_governance.')]
        self.assertTrue(rows)
        for row in rows:
            for field in ('GovernanceOutcome', 'GovernanceResult', 'EvidenceSufficiency', 'SamPath', 'NonSamPath'):
                self.assertTrue(row.get(field), field)
        outcomes, _, _ = _sharepoint_governance_rows(result)
        special = next(row for row in outcomes if row['Outcome'] == OUTCOMES['special_groups'][0])
        self.assertEqual(special['Affected site IDs'], 's09; s10; s11')
        self.assertEqual(len(assessment['cma']['sites']), len(h.SITES))

    def test_stable_ids_are_stable(self):
        documents = [h.document(capability=NOT_LICENSED), h.document(content_management_assessment=h.cma())]
        first, _, _ = h.run(documents)
        second, _, _ = h.run(list(reversed(documents)))
        key = lambda result: (sorted((row['FindingKey'], row['RecommendationId']) for row in result['recommendations']),
                              sorted(item['customer_action_id'] for item in result['customer_actions']))
        self.assertEqual(key(first), key(second))

    def test_replay_preserves_conclusions(self):
        result, assessment, _ = h.run([h.document(capability=NOT_LICENSED, content_management_assessment=h.cma())])
        saved = json.loads(json.dumps(assessment))
        bundle = {'expected_tenant_id': h.TENANT, 'observations': copy.deepcopy(saved['observations']),
                  'data_exposure': {'sharepoint_governance': saved},
                  'collection_context': {'tenant_id': h.TENANT, 'evaluation_date': h.EVALUATION, 'mode': 'offline'}}
        replay = build_assessment_result(copy.deepcopy(saved['recommendations']), bundle,
                                         evaluation_date=h.EVALUATION, expected_tenant_id=h.TENANT)
        self.assertEqual(replay['sharepoint_governance']['outcomes'], result['sharepoint_governance']['outcomes'])
        self.assertEqual([c['status'] for c in replay['controls']], [c['status'] for c in result['controls']])
        self.assertEqual([item['customer_action_id'] for item in replay['customer_actions']],
                         [item['customer_action_id'] for item in result['customer_actions']])

    def test_reports_folder_routes_only_the_versioned_contract(self):
        result, _, routed = h.run([h.document()], raw_files={'other.json': json.dumps({'schema': 'something-else'})})
        self.assertEqual([Path(path).name for path in routed['sharepoint_governance']], ['governance-0.json'])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'renamed-anything.json')
            path.write_text(json.dumps(h.document()), encoding='utf-8')
            self.assertTrue(is_governance_evidence(path))
            self.assertEqual(route_portal_reports([folder])['sharepoint_governance'], [str(path)])

    def test_supported_observations_use_conservative_wording(self):
        source = Path(__file__).resolve().parents[1] / 'Recommendations' / 'entra' / 'AAD_PREMIUM_P2.py'
        text = source.read_text(encoding='utf-8')
        self.assertNotIn('isolated from Copilot', text)
        self.assertIn('Direct Copilot licensing was not observed for', text)
        self.assertIn('remains a separate', text)
        checks = (Path(__file__).resolve().parents[1] / 'Core' / 'saved_control_checks.py').read_text(encoding='utf-8')
        self.assertIn('does not demonstrate incident-response readiness', checks)

    def test_executive_summary_explains_fewer_actions_than_failed_controls(self):
        controls = [{'security_gate': True, 'status': 'Action required', 'recommendation_ids': ['R1']},
                    {'security_gate': True, 'status': 'Action required', 'recommendation_ids': ['R2']},
                    {'security_gate': True, 'status': 'Observed', 'recommendation_ids': []}]
        actions = [{'lane': 'FixNow', 'blocks_readiness': False, 'members': [{'recommendation_id': 'R1'}, {'recommendation_id': 'R2'}]}]
        outcome = readiness_outcome(controls, [], actions, [])
        self.assertIn('The 2 controls with confirmed issues are addressed by 1 customer action', ' '.join(outcome['sentences']))
        self.assertEqual(outcome['additional_remediation_priorities'], 1)


if __name__ == '__main__':
    unittest.main()

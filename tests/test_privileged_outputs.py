"""End-to-end privilege output, replay, privacy and publication gates."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_privileged_identity import sources, grant, policy, NOW, FUTURE, RECENT, dataset, build

TENANT = '11111111-1111-4111-8111-111111111111'


class PrivilegedOutputTests(unittest.TestCase):
    def test_population_completeness_cannot_promote_unqualified_authentication_provenance(self):
        from Core.privileged_presentation import privileged_findings
        raw = {'id': 'event', 'userId': 'user-1', 'createdDateTime': RECENT, 'status': {'errorCode': 0}, 'clientAppUsed': 'IMAP'}
        for change in ({'complete': False, 'availability_status': 'partial'}, {'source_file': ''},
                       {'collected_at': ''}, {'availability_status': 'failed'}):
            data = sources(signin_logs=[dataset([raw])]); data['signin_logs'][0]['source'].update(change)
            model = build(data)
            self.assertTrue(model['population_complete'])
            finding = next(r for r in privileged_findings(model, data) if 'SuccessfulLegacyAuthentication' in r['FindingKey'])
            self.assertFalse(finding['EvidenceComplete'])
            self.assertEqual(finding['collection_status'][finding['EvidenceSource']]['availability_status'], 'partial')

    def fixture(self):
        from Core.assessment_result import build_assessment_result
        from Core.evidence_layer import build_evidence_bundle
        from Core.privileged_presentation import prepare_privileged_sheets
        raw = grant(); raw['principal']['displayName'] = 'Fictional protected administrator'
        other = grant('future', principal='future-user'); other['scheduleInfo']['startDateTime'] = FUTURE
        data = sources([raw, other], auth_methods=[dataset([{'id': 'user-1', 'isMfaRegistered': False}])])
        bundle = build_evidence_bundle([], ({}, []), {}, {}, {}, {}, {})
        bundle.update(assessment_sources=data, expected_tenant_id=TENANT,
            collection_context={'tenant_id': TENANT, 'source_file': 'fictional-evidence.json', 'collected_at': NOW},
            assessment_profile={'assessment_context': {'privileged_evaluation_timestamp': NOW, 'privileged_inactivity_policy': policy()}})
        result = build_assessment_result([], bundle, evaluation_date='2026-10-09', expected_tenant_id=TENANT)
        bundle['assessment_result'] = result
        prepare_privileged_sheets(bundle, result)
        return result, bundle

    def test_summary_counts_match_shared_records_and_exclude_future_privilege(self):
        result, bundle = self.fixture()
        model = result['privileged_assessment']
        self.assertEqual(model['counts']['assignments'], len(bundle['sheets']['admin_role_detail']['rows']))
        self.assertEqual(model['counts']['active_user_identities'], 1)
        self.assertEqual(model['counts']['temporal_states']['FuturePending'], 1)
        self.assertEqual(result['privileged_diagnostics'], [])

    def test_html_and_readiness_are_identity_free_and_do_not_recalculate(self):
        from Core.customer_report import render_customer_report
        from Core.pilot_summary import render_pilot_summary
        result, bundle = self.fixture(); before = copy.deepcopy(result)
        with patch('Core.privileged_identity.build_privileged_assessment', side_effect=AssertionError('Renderer must not evaluate')):
            for html in (render_customer_report(result, bundle, 'Fictional'), render_pilot_summary(result, bundle, 'Fictional')):
                self.assertIn('Privileged identity review', html)
                self.assertIn('1 unique users with current active privilege', html)
                self.assertNotIn('Fictional protected administrator', html)
                self.assertNotIn('user-1', html)
        self.assertEqual(result, before)

    def test_findings_keep_candidates_qualified_and_no_automatic_pim_or_disablement(self):
        result, _ = self.fixture()
        rows = [r for r in result['recommendations'] if str(r.get('FindingKey') or '').startswith('entra.privileged.')]
        self.assertTrue(rows)
        self.assertTrue(all(r.get('PrivilegedObservationIds') for r in rows))
        for row in rows:
            self.assertNotIn('Convert standing assignments', row['Recommendation'])
            self.assertNotIn('disable the account', row['Recommendation'])
            if 'Inactivity' in row['FindingKey']:
                self.assertEqual(row['Disposition'], 'Opportunity')
                self.assertIn('Potentially inactive', row['Feature'])
        self.assertNotIn('governance', result)

    def test_evidence_selection_keeps_assignment_and_registration_components(self):
        from Core.evidence_selection import build_evidence_selection
        result, bundle = self.fixture()
        selection = build_evidence_selection(result, bundle)
        finding = next(r for r in result['recommendations'] if r.get('FindingKey') == 'entra.privileged.ActivePrivilegeWithoutMFARegistration')
        # The authoritative selection retains exact native locators in the result.
        selected = next(r for r in selection['findings'] if r['finding_id'] == finding['RecommendationId'])
        self.assertEqual(selected['record_count'], 1)
        self.assertTrue(selected['records'][0]['evidence_record_ids'])
        self.assertEqual({ref['dataset'] for ref in selected['records'][0]['source_refs']}, {'role_assignment_schedules', 'auth_methods'})

    def test_snapshot_and_offline_replay_preserve_normalized_identity(self):
        from Core.assessment_serialization import write_assessment_result, read_assessment_result
        from Core.offline_collection import _encode, _decode
        result, bundle = self.fixture()
        before = copy.deepcopy(result['privileged_assessment'])
        replayed_sources = _decode(_encode(bundle['assessment_sources']))
        from Core.privileged_identity import build_privileged_assessment, privileged_context
        replayed = build_privileged_assessment(replayed_sources, **privileged_context(bundle, '2026-10-09', TENANT))
        self.assertEqual(replayed, before)
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'snapshot.json'
            write_assessment_result(path, result)
            self.assertEqual(read_assessment_result(path)['privileged_assessment'], before)

    def test_excel_keeps_scopes_identities_and_qualified_activity(self):
        from Core.export_recommendations import export_to_excel
        from tests.workbook_test_helpers import load_workbook_pair
        result, bundle = self.fixture()
        with TemporaryDirectory() as folder:
            path = export_to_excel(result['recommendations'], filename='fictional.xlsx', evidence_bundle=bundle, output_dir=folder)
            workbook = load_workbook_pair(path)
            try:
                self.assertIn('Privileged Identity Review', workbook.technical.sheetnames)
                self.assertNotIn('Privileged Identity Review', workbook.sheetnames)
                rows = list(workbook.technical['Admin Role Detail'].values)
                self.assertIn('Application Scope ID', rows[0])
                self.assertIn('Future Pending', str(rows))
                self.assertIn('PotentiallyInactivePrivilegedAccount', str(list(workbook.technical['Privileged Identity Review'].values)))
            finally:
                workbook.close()

    def test_invalid_privilege_semantics_block_snapshot_publication(self):
        from Core.assessment_serialization import write_assessment_result
        result, _ = self.fixture()
        result['privileged_assessment']['counts']['active_user_identities'] = 999
        with TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, 'privileged_count_mismatch'):
                write_assessment_result(Path(folder) / 'snapshot.json', result)

    def test_policy_change_is_an_explicit_comparison_boundary(self):
        from Core.privileged_presentation import privileged_findings
        data = sources()
        before = privileged_findings(build(data, inactivity_policy=policy(30)), data)
        after = privileged_findings(build(data, inactivity_policy=policy(200)), data)
        a = next(r for r in before if 'Inactivity' in r['FindingKey'])
        b = next(r for r in after if 'Inactivity' in r['FindingKey'])
        self.assertNotEqual(a['ControlDefinitionVersion'], b['ControlDefinitionVersion'])


if __name__ == '__main__':
    unittest.main()

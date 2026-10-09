"""Fictional taxonomy outputs reconcile without rewriting source evidence or history."""
import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from Core.assessment_result import build_assessment_result
from Core.identity_finding_taxonomy import (
    finding_title, project_identity_result, project_identity_sheet_links,
)
from Core.evidence_layer import build_evidence_bundle
from Core.export_recommendations import export_to_excel, export_to_html
from Core.assessment_serialization import write_assessment_result, read_assessment_result
from tests.workbook_test_helpers import load_workbook_pair


TENANT = '11111111-1111-1111-1111-111111111111'
DAY = '2026-10-09'


def fixture():
    source = {'available': True, 'availability_status': 'available', 'complete': True,
              'truncated': False, 'collected_at': DAY, 'source_file': 'fictional.json',
              'scope': 'Returned registration users', 'tenant_id': TENANT}
    client = SimpleNamespace(available=True, auth_methods_registration=[
        {'id': 'fiction-user', 'userPrincipalName': 'fiction@example.invalid',
         'isMfaRegistered': False, 'isMfaCapable': False, 'methodsRegistered': ['mobilePhone']}],
        signin_logs=[], collection_status={'auth_methods': source}, assessment_datasets={})
    row = {'RecommendationId': 'ENT-fiction', 'FindingKey': 'entra.authentication.mfa_registration',
           'Service': 'Entra', 'Feature': 'Microsoft Entra ID P1',
           'Observation': 'One user is explicitly not registered for MFA.',
           'Recommendation': 'Review the unregistered user and confirm registration scope.',
           'Status': 'Action Required', 'Priority': 'High', 'Disposition': 'Action',
           'EvidenceKey': 'authentication_detail;mfa_registration_detail', 'EvidenceSource': 'auth_methods',
           'ControlId': 'IDENTITY.MFA', 'EvidenceComplete': True, 'ObservationDate': DAY}
    bundle = build_evidence_bundle([row], ({}, []), {'_client': client}, {}, {}, {}, {})
    bundle.update(expected_tenant_id=TENANT, evaluation_date=DAY,
                  source_statuses={'auth_methods': source},
                  collection_context={'tenant_id': TENANT, 'collected_at': DAY, 'source_file': 'fictional.json'})
    result = build_assessment_result(bundle['recommendations'], bundle,
                                     evaluation_date=DAY, expected_tenant_id=TENANT)
    bundle['assessment_result'] = result
    return bundle, result, client


def rows(workbook, name):
    values = iter(workbook[name].iter_rows(values_only=True))
    headers = next(values)
    return [dict(zip(headers, row)) for row in values]


class TaxonomyProjectionTests(unittest.TestCase):
    def test_shared_builder_uses_title_without_changing_feature_or_evidence(self):
        bundle, result, client = fixture()
        row = next(r for r in result['recommendations'] if r['RecommendationId'] == 'ENT-fiction')
        self.assertEqual(row['FindingTitle'], 'MFA registration coverage')
        self.assertEqual(row['Feature'], 'Microsoft Entra ID P1')
        self.assertEqual(client.auth_methods_registration[0]['isMfaRegistered'], False)
        self.assertEqual(row['EvidenceLevel'], 'configuration')
        self.assertEqual(row['ConditionEvidenceLevel'], 'registration_state')

    def test_flagged_by_is_condition_context_with_separate_licensing(self):
        from Core.identity_finding_taxonomy import resolve_identity_finding
        rec = resolve_identity_finding({'Service': 'Entra', 'Feature': 'Microsoft Entra ID P1',
            'FindingCondition': 'conditional_access.policy_coverage', 'RecommendationId': 'R',
            'EvidenceKey': 'conditional_access_detail'})
        sheets = {'conditional_access_detail': {'rows': [{'RecommendationId': 'R', 'Flagged By': 'Microsoft Entra ID P1', 'Policy ID': 'fiction-policy'}]},
                  'service_plan_inventory': {'rows': [{'Service Plan': 'Microsoft Entra ID P1'}]},
                  'raw.source': {'rows': [{'Feature': 'Microsoft Entra ID P1'}]}}
        raw = copy.deepcopy(sheets['raw.source']); plans = copy.deepcopy(sheets['service_plan_inventory'])
        project_identity_sheet_links(sheets, [rec])
        detail = sheets['conditional_access_detail']['rows'][0]
        self.assertEqual(detail['Flagged By'], 'Conditional Access policy coverage')
        self.assertEqual(detail['Associated License Feature'], 'Microsoft Entra ID P1')
        self.assertIn('Microsoft Entra ID P1', detail['Licensing Dependency'])
        self.assertEqual(detail['Policy ID'], 'fiction-policy')
        self.assertEqual(sheets['raw.source'], raw)
        self.assertEqual(sheets['service_plan_inventory'], plans)

    def test_ambiguous_alias_does_not_target_multiple_conditions(self):
        recs = [{'Service': 'Entra', 'Feature': 'Microsoft Entra ID P1', 'FindingCondition': c,
                 'RecommendationId': str(i), 'CompatibilityRecommendationIds': ['ENT-old']}
                for i, c in enumerate(('mfa.registration', 'mfa.enforcement'))]
        sheets = {'authentication_detail': {'rows': [{'RecommendationId': 'ENT-old', 'Flagged By': 'Legacy context'}]}}
        project_identity_sheet_links(sheets, recs)
        self.assertEqual(sheets['authentication_detail']['rows'][0]['Flagged By'], 'Legacy context')

    def test_excel_html_and_readiness_share_condition_title(self):
        bundle, result, _ = fixture()
        with TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            xlsx = export_to_excel(result['recommendations'], filename='fiction.xlsx', evidence_bundle=bundle, output_dir=folder)
            html = export_to_html(result['recommendations'], filename='fiction.html', evidence_bundle=bundle, output_dir=folder, excel_path=xlsx)
            workbook = load_workbook_pair(xlsx)
            try:
                for sheet in ('Recommendations', 'Evidence Index', 'Findings Lineage', 'Investigation Items', 'Technical Recommendations'):
                    with self.subTest(sheet=sheet):
                        candidates = [r for r in rows(workbook.technical, sheet) if r.get('RecommendationId') == 'ENT-fiction']
                        self.assertTrue(candidates)
                        field = 'Feature' if sheet == 'Recommendations' else 'Finding'
                        self.assertTrue(all(r[field] == 'MFA registration coverage' for r in candidates))
                rec = next(r for r in rows(workbook.technical, 'Recommendations') if r.get('RecommendationId') == 'ENT-fiction')
                self.assertEqual(rec['Compatibility Feature'], 'Microsoft Entra ID P1')
                self.assertEqual(rec['Associated License Feature'], 'Microsoft Entra ID P1')
                detail = rows(workbook.technical, 'MFA Registration Review')[0]
                self.assertEqual(detail['Record Condition'], 'User explicitly not registered for MFA')
            finally:
                workbook.close()
            self.assertIn('MFA registration coverage', Path(html).read_text(encoding='utf-8'))
            self.assertIn('MFA registration coverage', Path(bundle['summary_html_path']).read_text(encoding='utf-8'))
            self.assertNotIn('<h3>Microsoft Entra ID P1</h3>', Path(html).read_text(encoding='utf-8'))

    def test_snapshot_legacy_replay_is_additive_and_file_immutable(self):
        _, result, _ = fixture()
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'fiction.json'
            write_assessment_result(path, result)
            before = path.read_bytes()
            replay = read_assessment_result(path)
            self.assertEqual(replay['identity_taxonomy'], result['identity_taxonomy'])
            row = next(r for r in replay['recommendations'] if r['RecommendationId'] == 'ENT-fiction')
            self.assertEqual(row['FindingTitle'], 'MFA registration coverage')
            self.assertEqual(path.read_bytes(), before)
            old = json.loads(before)
            old.pop('identity_taxonomy')
            for record in old['recommendations']:
                for field in list(record):
                    if field.startswith('FindingTaxonomy') or field in {'FindingTitle', 'FindingCondition', 'TaxonomyState', 'TaxonomyDiagnostics'}:
                        record.pop(field)
            path.write_text(json.dumps(old), encoding='utf-8')
            original = path.read_bytes()
            replay = read_assessment_result(path)
            self.assertEqual(next(r for r in replay['recommendations'] if r['RecommendationId'] == 'ENT-fiction')['FindingTitle'], 'MFA registration coverage')
            self.assertEqual(path.read_bytes(), original)

    def test_each_identity_detail_family_projects_only_linked_conditions(self):
        from Core.identity_finding_taxonomy import IDENTITY_SHEETS
        for key in IDENTITY_SHEETS:
            with self.subTest(key=key):
                sheets = {key: {'rows': [{'RecommendationId': 'fiction', 'Flagged By': 'Microsoft Entra ID P1', 'Raw ID': 'native'}]}}
                rec = {'Service': 'Entra', 'Feature': 'Microsoft Entra ID P1', 'FindingCondition': 'mfa.registration',
                       'RecommendationId': 'fiction', 'EvidenceKey': key}
                project_identity_sheet_links(sheets, [rec])
                self.assertEqual(sheets[key]['rows'][0]['Flagged By'], 'MFA registration coverage')
                self.assertEqual(sheets[key]['rows'][0]['Raw ID'], 'native')

    def test_invalid_taxonomy_blocks_publication(self):
        bundle, result, _ = fixture()
        next(r for r in result['recommendations'] if r['RecommendationId'] == 'ENT-fiction')['FindingTitle'] = 'Microsoft Entra ID P1'
        with TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, 'taxonomy'):
                export_to_excel(result['recommendations'], evidence_bundle=bundle, output_dir=folder)

    def test_evidence_selection_and_portal_model_share_title(self):
        from Core.evidence_selection import build_evidence_selection
        from Core.finding_evidence import build_finding_evidence
        bundle, result, _ = fixture()
        selected = build_evidence_selection(result, bundle)
        model = build_finding_evidence(selected)
        for projection in (selected, model):
            row = next(r for r in projection['findings'] if r['finding_id'] == 'ENT-fiction')
            self.assertEqual(row['title'], 'MFA registration coverage')

    def test_packaged_snapshot_retains_taxonomy_and_original_bytes(self):
        from Core.assessment_package import record_package_run
        _, result, _ = fixture()
        with TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            root = Path(folder)
            package = root / 'portable'
            report = package / 'Builds' / '1'
            report.mkdir(parents=True)
            path = root / 'fiction.json'
            write_assessment_result(path, result)
            original = path.read_bytes()
            delivered = record_package_run(package, mode='offline', tenant_id=TENANT,
                collected_at=DAY, evaluation_date=DAY,
                outputs={'report_directory': report, 'snapshot_path': path})
            packaged = report / path.name
            self.assertIn('Builds/1/fiction.json', delivered)
            self.assertEqual(packaged.read_bytes(), original)
            replay = read_assessment_result(packaged)
            self.assertEqual(replay['identity_taxonomy'], result['identity_taxonomy'])
            self.assertEqual(replay['identity'], result['identity'])

    def test_prior_workbook_restores_semantic_feature_and_license_context(self):
        from Core.prior_report_import import load_prior_report
        from Core.assessment_result import _finding_identity
        from Core.identity_finding_taxonomy import resolve_identity_finding
        bundle, result, _ = fixture()
        source = next(row for row in result['recommendations'] if row['RecommendationId'] == 'ENT-fiction')
        with TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            path = export_to_excel(result['recommendations'], filename='fiction.xlsx', evidence_bundle=bundle, output_dir=folder)
            original = Path(path).read_bytes()
            imported = load_prior_report(path)
            row = next(row for row in imported['recommendations'] if row['RecommendationId'] == 'ENT-fiction')
            self.assertEqual(row['Feature'], source['Feature'])
            self.assertEqual(row['FindingTitle'], source['FindingTitle'])
            # Exercise the legacy fallback independently of a known FindingKey.
            self.assertEqual(_finding_identity(dict(row, FindingKey='')),
                             _finding_identity(dict(source, FindingKey='')))
            self.assertEqual(resolve_identity_finding(row)['AssociatedLicenseFeatures'], ['Microsoft Entra ID P1'])
            self.assertEqual(Path(path).read_bytes(), original)


class TaxonomyRealLifecycleTests(unittest.TestCase):
    def test_title_only_projection_leaves_actual_delta_unchanged(self):
        from test_delta_lifecycle import pair, compare, finding_record
        baseline, current = pair()
        for snapshot in (baseline, current):
            snapshot['recommendations'][0].update(Service='Entra', Feature='Microsoft Entra ID P1',
                FindingCondition='mfa.registration', ControlId='IDENTITY.MFA', FindingKey='policy.exceptions')
        original = copy.deepcopy(baseline)
        expected = compare(baseline, copy.deepcopy(current))
        projected = project_identity_result(current)
        actual = compare(baseline, projected)
        self.assertEqual(actual, expected)
        self.assertEqual(finding_record(actual)['State'], 'Unchanged')
        self.assertEqual(baseline, original)
        self.assertEqual(projected['identity'], current['identity'])

    def test_governance_overlay_and_alias_targets_are_not_rewritten(self):
        from test_governance_decisions import draft, advance, NOW
        from Core.governance import attach
        result, log, key, authority = draft('AcceptedRisk')
        log = advance(result, log, key, authority)
        attach(result, log, as_of=NOW, locator='fictional-decisions.json')
        before = copy.deepcopy(result)
        projected = project_identity_result(result)
        self.assertEqual(projected.get('governance'), before.get('governance'))
        self.assertEqual(projected['identity'], before['identity'])
        self.assertEqual(result, before)

    def test_same_license_label_does_not_merge_distinct_condition_findings(self):
        from Core.assessment_identity import semantic_id
        base = {'assessment_id': 'AST-fiction', 'environment_id': 'ENV-fiction', 'provider': 'Graph',
                'population': 'users', 'resource_scope': 'tenant'}
        registration = semantic_id('finding', **dict(base, control_id='IDENTITY.MFA', condition_key='registration'))
        enforcement = semantic_id('finding', **dict(base, control_id='IDENTITY.AUTH', condition_key='enforcement'))
        self.assertNotEqual(registration, enforcement)

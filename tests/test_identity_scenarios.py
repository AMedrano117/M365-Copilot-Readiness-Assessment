"""Declared identity evidence-state scenarios and cross-producer invariants.

Expectations live in tests/scenarios/identity/*.json and were declared before
the production correction. Every scenario runs through production functions
(tests/identity_scenarios.py adds no semantics).
"""
import json
import re
import unittest

try:
    from tests import identity_scenarios as harness
    from tests.provenance_fixture import fictional_publication
except ImportError:  # unittest discover -s tests imports modules from the tests folder
    import identity_scenarios as harness
    from provenance_fixture import fictional_publication


def _matching(result, key):
    return [row for row in result['recommendations'] if row.get('FindingKey') == key]


class DeclaredScenarioTests(unittest.TestCase):
    """One test per declared scenario file."""

    def check(self, result, check):
        row = None
        if check.get('finding_key'):
            rows = _matching(result, check['finding_key'])
            self.assertEqual(len(rows), 1, f"expected exactly one {check['finding_key']} record, found {len(rows)}")
            row = rows[0]
            for field, expected in (('disposition', 'Disposition'), ('priority', 'Priority'),
                                    ('evidence_status', 'EvidenceStatus'), ('readiness_treatment', 'ReadinessTreatment')):
                if field in check:
                    self.assertEqual(row.get(expected), check[field], f'{check["finding_key"]} {expected}')
            for condition in check.get('semantic_condition', []):
                self.assertIn(condition, str(row.get('SemanticCondition') or ''), 'semantic condition')
            body = ' '.join(str(row.get(k) or '') for k in ('Observation', 'Recommendation', 'CompletionEvidence'))
            for pattern in check.get('required', []):
                self.assertRegex(body, pattern, 'required qualification')
            if 'blocks_readiness' in check:
                blockers = {item.get('RecommendationId') for item in result['blockers']}
                self.assertEqual(row.get('RecommendationId') in blockers, check['blocks_readiness'], 'blocks readiness')
            if check.get('stable_recommendation_id'):
                self.assertEqual(row.get('RecommendationId'), check['recommendation_id'], 'stable recommendation ID')
        if check.get('control'):
            control = harness.control(result, check['control'])
            actions = harness.control_actions(result, check['control'])
            if 'configuration_result' in check:
                self.assertEqual(control['configuration_result'], check['configuration_result'], 'configuration result')
            if 'control_status' in check:
                self.assertEqual(control['status'], check['control_status'], 'control status')
            if 'control_status_not' in check:
                self.assertNotEqual(control['status'], check['control_status_not'], 'control status')
            for action_type in check.get('open_action_types', []):
                self.assertIn(action_type, [a['ActionType'] for a in harness.control_actions(result, check['control'])],
                              'an open action of this type must remain')
            if 'control_actions' in check:
                self.assertEqual(len(actions), check['control_actions'],
                                 'actions: ' + '; '.join(f"{a.get('FindingKey')}|{a.get('Feature')}" for a in actions))
            if 'action_types' in check:
                self.assertEqual(sorted(a['ActionType'] for a in actions), sorted(check['action_types']))
            if 'blocks_readiness' in check and row is None:
                blockers = {item.get('RecommendationId') for item in result['blockers']}
                self.assertEqual(any(a.get('RecommendationId') in blockers for a in actions), check['blocks_readiness'])
            related = actions + [r for r in result['recommendations'] if r.get('RecommendationId') in set(control['recommendation_ids'])]
        else:
            related = [row] if row else []
        for pattern in check.get('prohibited', []):
            for item in related:
                self.assertNotRegex(harness.text(item), pattern, f"prohibited conclusion in {item.get('FindingKey') or item.get('Feature')}")

    def run_scenario(self, name):
        spec = harness.load(name)
        expected = spec['expected']
        result, bundle, rows = harness.run(spec)
        for check in expected.get('checks', []):
            with self.subTest(check=check.get('finding_key') or check.get('control')):
                self.check(result, check)
        everything = result['recommendations'] + result['actions']
        for pattern in expected.get('prohibited_anywhere', []):
            for item in everything:
                self.assertNotRegex(harness.text(item), pattern, f"prohibited anywhere: {item.get('FindingKey') or item.get('Feature')}")
        for pattern in expected.get('no_action_titles', []):
            for action in result['actions']:
                for field in ('Feature', 'ActionTitle', 'FindingTitle'):
                    self.assertNotRegex(str(action.get(field) or ''), pattern, f'license or status used as an action title ({field})')
        if expected.get('no_actions_without_finding_key'):
            for action in result['actions']:
                if action.get('DomainId') in {'identity', 'applications'}:
                    self.assertTrue(action.get('FindingKey'), f"identity action without a finding key: {action.get('Feature')}")
        if expected.get('one_action_per_condition'):
            self.assert_one_action_per_condition(result)
        for key in expected.get('unique_finding_keys', []):
            self.assertEqual(len(_matching(result, key)), 1, key)
        if expected.get('decision_not'):
            self.assertNotEqual(result['decision'], expected['decision_not'])
        if expected.get('same_technical_conclusions_as'):
            other, _, _ = harness.run(harness.load(expected['same_technical_conclusions_as']))
            self.assertEqual(harness.technical_conclusions(result), harness.technical_conclusions(other))
        profiles = expected.get('license_profiles_with_identical_technical_conclusions') or []
        if profiles:
            baseline = harness.technical_conclusions(harness.run(spec, profiles[0])[0])
            for profile in profiles[1:]:
                with self.subTest(license_profile=profile):
                    self.assertEqual(harness.technical_conclusions(harness.run(spec, profile)[0]), baseline)

    def assert_one_action_per_condition(self, result):
        for control in result['controls']:
            actions = harness.control_actions(result, control['control_id'])
            keys = [a.get('FindingKey') or a.get('Feature') for a in actions]
            self.assertEqual(len(keys), len(set(keys)), f"{control['control_id']} has duplicate actions: {keys}")
            self.assertTrue(all(a.get('FindingKey') for a in actions if a.get('DomainId') in {'identity', 'applications'}),
                            f"{control['control_id']} has an identity action without a structured key")


def _scenario_test(name):
    def test(self):
        self.run_scenario(name)
    test.__doc__ = harness.load(name)['description']
    return test


for _name in harness.scenario_names():
    setattr(DeclaredScenarioTests, 'test_' + _name, _scenario_test(_name))


@fictional_publication
class IdentityInvariantTests(unittest.TestCase):
    SOURCES = ('ca_policies', 'security_defaults', 'authorization_policy', 'oauth_grants', 'guests', 'access_reviews')
    ABSENCE = re.compile(r'\b(?:security defaults are (?:off|disabled)|No [A-Za-z ]+ (?:was|were) returned|returned 0\b|'
                         r'are not configured|is not configured|absent)\b', re.I)

    def identity_rows(self, result):
        return [row for row in result['recommendations'] if row.get('DomainId') in {'identity', 'applications'}
                and row.get('EvidenceBasis') != 'License signal']

    # Conclusions that would claim a state or an absence the unread source never established.
    SOURCE_CLAIMS = {
        'ca_policies': r'No Conditional Access policies were returned|No enforced Conditional Access policy|verified controls include|Conditional Access requires MFA',
        'security_defaults': r'security defaults are (?:off|disabled|enabled)',
        'authorization_policy': r'Admin consent required|User consent enabled|No self-consent policy|consent policy, which lets users|Guest invitation eligibility|Guest user invitations restricted',
        'oauth_grants': r'did not flag high-impact|Application grants requiring review were detected',
        'guests': r'guest users have an M365 license|none with M365 licenses',
        'access_reviews': r'No access review definitions were returned|No active access reviews were returned|returned 0 access review',
    }
    # Every source below has a structured producer, so the check is never vacuous.
    PRODUCED = {'ca_policies', 'authorization_policy', 'oauth_grants', 'access_reviews'}

    def test_unread_failed_or_partial_source_never_creates_absence_pass_or_fail(self):
        spec = harness.load('17_license_inventory_parity')
        for source in self.SOURCES:
            for state in ('unavailable:403', 'partial', 'not_requested', 'absent'):
                patched = dict(spec, patch=harness.merge(spec['patch'], {'sources': {source: state}}))
                with self.subTest(source=source, state=state):
                    result, _, _ = harness.run(patched)
                    rows = self.identity_rows(result)
                    for row in rows:
                        self.assertNotRegex(harness.text(row), self.SOURCE_CLAIMS[source],
                                            f"{row.get('FindingKey') or row.get('Feature')} claims a state of unread {source}")
                    sourced = [row for row in rows if row.get('EvidenceSource') == source]
                    # With no recorded Conditional Access outcome, the shared gap logic
                    # answers IDENTITY.AUTH instead of a structured baseline row.
                    if source in self.PRODUCED and not (source == 'ca_policies' and state == 'absent'):
                        self.assertTrue(sourced, f'no structured row records the {source} outcome')
                    for row in sourced:
                        self.assertIn(row['Disposition'], {'Coverage', 'Reference'}, f"{row.get('FindingKey')} from unread {source}")
                        self.assertNotRegex(row.get('Observation') or '', self.ABSENCE)

    def test_complete_empty_and_failed_conditional_access_are_distinct(self):
        empty, _, _ = harness.run(harness.load('10_ca_complete_empty'))
        failed, _, _ = harness.run(harness.load('11_ca_failed'))
        empty_row = _matching(empty, 'baseline.identity.sign_in')[0]
        failed_row = _matching(failed, 'baseline.identity.source')[0]
        self.assertEqual((empty_row['Disposition'], failed_row['Disposition']), ('Action', 'Coverage'))
        self.assertNotEqual(empty_row['ReadinessTreatment'], failed_row['ReadinessTreatment'])
        self.assertFalse(_matching(empty, 'baseline.identity.source'))
        self.assertFalse(_matching(failed, 'baseline.identity.sign_in'))

    def test_partial_collection_cannot_create_tenant_wide_assurance(self):
        result, _, _ = harness.run(harness.load('12_ca_partial'))
        self.assertFalse([row for row in self.identity_rows(result)
                          if row.get('ControlId') == 'IDENTITY.AUTH' and row['Disposition'] == 'Assurance'])
        self.assertEqual(harness.control(result, 'IDENTITY.AUTH')['configuration_result'], 'not_established')

    def test_recommendation_text_is_not_a_semantic_input(self):
        spec = harness.load('02b_group_mfa_subset_measured')
        result, _, rows = harness.run(spec)
        tenant = harness.tenant_for(spec)
        client = harness.build_client(tenant)
        licenses = tenant['license_profiles'][tenant['license_profile']]
        saved = harness.build_entra_recommendations(licenses, client)
        misleading = ('No Conditional Access policies were returned by this inventory',
                      'User consent enabled for applications, allowing users to grant apps access without admin review',
                      'No access review definitions were returned', 'security defaults are off')
        for index, row in enumerate(saved):
            row['Observation'] = misleading[index % len(misleading)]
            row['Recommendation'] = 'Treat this tenant as fully compliant.'
        tampered, _, _ = harness.run(spec, saved_rows=saved, client=client)
        self.assertEqual(harness.technical_conclusions(tampered), harness.technical_conclusions(result))
        self.assertEqual(tampered['decision'], result['decision'])

    def test_one_condition_creates_one_action_in_every_scenario(self):
        checker = DeclaredScenarioTests()
        for name in harness.scenario_names():
            with self.subTest(scenario=name):
                result, _, _ = harness.run(harness.load(name))
                checker.assert_one_action_per_condition(result)

    def test_group_scope_is_never_inferred_from_names(self):
        spec = harness.load('03_group_mfa_incomplete_membership')
        renamed = dict(spec, patch=harness.merge(spec['patch'], {}))
        renamed['patch']['client']['ca_policies'][0]['displayName'] = 'Require MFA for All Users (all employees)'
        result, _, _ = harness.run(renamed)
        row = _matching(result, 'baseline.identity.scope')[0]
        self.assertIn('mfa=ScopeNotEstablished', row['SemanticCondition'])

    def test_snapshot_and_collection_replay_preserve_conclusions(self):
        from Core.assessment_serialization import read_assessment_result, write_assessment_result
        from Core.offline_collection import _decode, _encode
        import tempfile
        from pathlib import Path
        for name in ('02b_group_mfa_subset_measured', '08_security_defaults_unread', '15b_microsoft_managed_consent'):
            spec = harness.load(name)
            with self.subTest(scenario=name):
                result, _, _ = harness.run(spec)
                tenant = harness.tenant_for(spec)
                client = harness.build_client(tenant)
                replayed_client = _decode(json.loads(json.dumps(_encode(client))))
                replayed, _, _ = harness.run(spec, client=replayed_client)
                self.assertEqual(harness.technical_conclusions(replayed), harness.technical_conclusions(result))
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'result.json'
                    write_assessment_result(path, result)
                    frozen = read_assessment_result(path)
                self.assertEqual(harness.technical_conclusions(frozen), harness.technical_conclusions(result))
                self.assertEqual(frozen['decision'], result['decision'])


@fictional_publication
class CustomerAndTechnicalOutputTests(unittest.TestCase):
    """The customer HTML, readiness summary and workbook render one structured conclusion."""

    def test_outputs_agree_and_never_use_absence_wording_for_unread_sources(self):
        from Core.customer_report import render_customer_report
        from Core.pilot_summary import render_pilot_summary
        for name, key in (('08_security_defaults_unread', 'baseline.identity.source'),
                          ('02b_group_mfa_subset_measured', 'baseline.identity.sign_in'),
                          ('01_mfa_or_compliant_device', 'baseline.identity.scope')):
            spec = harness.load(name)
            with self.subTest(scenario=name):
                result, bundle, _ = harness.run(spec)
                action = next(row for row in result['actions'] if row.get('FindingKey') == key)
                customer = render_customer_report(result, bundle, 'Contoso')
                summary = render_pilot_summary(result, bundle, 'Contoso', 'report.html', 'workbook.xlsx')
                observation = re.sub(r'\s+', ' ', action['Observation']).strip()
                fragment = observation[:60]
                import html as html_module
                self.assertIn(fragment, html_module.unescape(customer))
                import io
                import tempfile
                from contextlib import redirect_stdout
                from openpyxl import load_workbook
                from Core.export_recommendations import export_to_excel
                from Core.workbook_layout import technical_workbook_path
                with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
                    path = export_to_excel(result['recommendations'], filename='fiction.xlsx', evidence_bundle=bundle,
                                           output_dir=directory)
                    cells = []
                    for workbook_path in (path, technical_workbook_path(path)):
                        workbook = load_workbook(workbook_path, read_only=True)
                        try:
                            cells.extend(str(value) for sheet in workbook for row in sheet.values for value in row
                                         if isinstance(value, str))
                        finally:
                            workbook.close()
                workbook_text = re.sub(r'\s+', ' ', ' '.join(cells))
                self.assertIn(fragment, workbook_text, 'the workbook carries the same structured conclusion')
                for document in (customer, summary, workbook_text):
                    plain = html_module.unescape(document)
                    self.assertNotRegex(plain, r'security defaults are off')
                    if name == '08_security_defaults_unread':
                        self.assertNotRegex(plain, r'security defaults are disabled')
                        self.assertNotRegex(plain, r'No Conditional Access policies were returned')


if __name__ == '__main__':
    unittest.main()

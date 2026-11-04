"""Execution and comparison outcomes, expected-change tracing and display identity (tools/validate.py).

Every fixture is fictional. Expectations are declared from the rule under test before
the comparison runs; none is read back from the comparison output.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from provenance_fixture import fictional_publication, fictional_revision
from test_validation_workflow import build_retained_package

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import validate as v  # noqa: E402

RETAINED, CURRENT, INTRODUCED = 'a' * 40, 'b' * 40, 'c' * 40


class FictionalHistory:
    """Declared ancestry: the current commit contains the introducing commit; the retained commit does not."""

    def contains(self, commit, ancestor):
        if commit == ancestor:
            return True
        return {(CURRENT, INTRODUCED): True, (RETAINED, INTRODUCED): False}.get((commit, ancestor))


def transition(*matches, kind='methodology_rule'):
    return {'id': 'T-fictional', 'kind': kind, 'reason': 'Fictional declared rule change.',
            'rule': 'Core/fictional_rule.py::rule', 'introduced_by': INTRODUCED, 'matches': list(matches)}


def finding(display, fingerprint, **fields):
    row = dict(RecommendationId=display, FindingFingerprint=fingerprint, FindingKey='fictional.' + fingerprint,
               ControlId='FICT.CONTROL', Provider='Fictional', Population='All fictional users', EvidenceScope='tenant',
               Disposition='Action', Status='Action Required', Priority='High', EvidenceId='EV-' + fingerprint)
    row.update(fields)
    return row


def result(rows, *, commit=RETAINED, aliases=()):
    return {'decision': 'Fictional decision', 'evaluation_date': '2026-09-15', 'methodology_version': '4.0.0',
            'producing_revision': {'commit_sha': commit, 'methodology_version': '4.0.0', 'taxonomy_version': '1.1.0'},
            'controls': [{'control_id': 'FICT.CONTROL', 'status': 'Observed', 'evidence_ids': ['EV-fictional'],
                          'recommendation_ids': [row['RecommendationId'] for row in rows]}],
            'recommendations': rows, 'actions': [row for row in rows if row.get('Disposition') == 'Action'],
            'blockers': [], 'counts': {}, 'identity': {'Aliases': list(aliases)}}


def compare(before_rows, after_rows, *transitions, aliases=()):
    comparison = v.compare_results(result(before_rows), result(after_rows, commit=CURRENT, aliases=aliases),
                                   transitions=list(transitions), history=FictionalHistory())
    return comparison, v.comparison_status(comparison, [])


class DisplayIdentityTests(unittest.TestCase):
    def test_same_persistent_finding_with_new_display_id_is_a_migration(self):
        alias = {'Value': 'FIC-002', 'Namespace': 'RecommendationId:issue', 'TargetType': 'finding', 'TargetId': 'PFI-fiction-1'}
        comparison, status = compare([finding('FIC-001', 'fp1', PersistentFindingId='PFI-fiction-1')],
                                     [finding('FIC-002', 'fp1')], aliases=[alias])
        migration, = comparison['display_identity']['migrations']
        self.assertEqual(migration['PreviousDisplayIds'], ['FIC-001'])
        self.assertEqual(migration['CurrentDisplayId'], 'FIC-002')
        self.assertEqual(migration['PersistentFindingId'], 'PFI-fiction-1')
        self.assertEqual(migration['MatchedBy'], 'PersistentFindingId')
        self.assertTrue(migration['SemanticKey'].startswith('SK-'))
        self.assertEqual(migration['EffectiveMethodologyVersion'], '4.0.0')
        self.assertEqual(migration['EffectiveTaxonomyVersion'], '1.1.0')
        self.assertTrue(migration['ChangeReason'])
        self.assertEqual(comparison['display_identity']['reuse_conflicts'], [])
        # A migration is a warning, not a semantic change or a conflict.
        self.assertEqual(status, 'PresentationOnlyDifferences')
        self.assertEqual(v.EXIT_CODES[status], 0)

    def test_same_display_id_for_a_different_semantic_finding_is_a_blocking_conflict(self):
        # fp2 moves to FIC-001 and fp1 is gone: FIC-001 now names a different finding.
        explained_removal = transition({'scope': 'finding', 'id': 'FIC-001', 'field': 'presence',
                                        'from': ['present'], 'to': ['absent']})
        comparison, status = compare([finding('FIC-001', 'fp1'), finding('FIC-002', 'fp2')], [finding('FIC-001', 'fp2')],
                                     explained_removal)
        conflict, = comparison['display_identity']['reuse_conflicts']
        self.assertEqual(conflict['DisplayId'], 'FIC-001')
        self.assertNotEqual(conflict['Previous']['SemanticKey'], conflict['Current']['SemanticKey'])
        self.assertEqual([item['CurrentDisplayId'] for item in comparison['display_identity']['migrations']], ['FIC-001'])
        self.assertEqual(comparison['findings_removed'], ['FIC-001'])
        # An explained removal never resolves the reuse of its display ID.
        self.assertTrue(comparison['expected_changes'])
        self.assertEqual(status, 'DisplayIdentityConflict')
        self.assertEqual(comparison['blocking_conflicts'], ['Display ID FIC-001 refers to a different semantic finding.'])
        self.assertEqual(v.EXIT_CODES[status], 3)

    def test_retired_display_id(self):
        rows = [finding('FIC-001', 'fp1'), finding('FIC-002', 'fp2')]
        retirement = transition({'scope': 'finding', 'id': 'FIC-002', 'field': 'presence', 'from': ['present'], 'to': ['absent']})
        comparison, status = compare(rows, rows[:1], retirement)
        self.assertEqual([item['DisplayId'] for item in comparison['display_identity']['retired']], ['FIC-002'])
        self.assertEqual(comparison['display_identity']['reuse_conflicts'], [])
        self.assertEqual(status, 'ExpectedSemanticDifferences')
        change, = comparison['expected_changes']
        self.assertEqual((change['scope'], change['id'], change['previous'], change['current']), ('finding', 'FIC-002', 'present', 'absent'))
        self.assertEqual(change['producing_commits'], {'retained': RETAINED, 'current': CURRENT, 'introduced_by': INTRODUCED})
        self.assertEqual(change['versions']['after'], {'methodology': '4.0.0', 'taxonomy': '1.1.0', 'commit': CURRENT})
        self.assertEqual(change['evidence']['previous']['EvidenceId'], 'EV-fp2')
        self.assertEqual((change['reason'], change['rule'], change['transition']),
                         ('Fictional declared rule change.', 'Core/fictional_rule.py::rule', 'T-fictional'))
        self.assertEqual(change['controls'], ['FICT.CONTROL'])
        # Without the declared transition the same removal is an unexpected regression.
        comparison, status = compare(rows, rows[:1])
        self.assertEqual(status, 'UnexpectedSemanticRegression')
        self.assertIn('Open finding FIC-002 is no longer reported.', comparison['unexpected_regressions'])

    def test_newly_added_display_id(self):
        addition = transition({'scope': 'finding', 'id': 'FIC-002', 'field': 'presence', 'from': ['absent'], 'to': ['present']})
        comparison, status = compare([finding('FIC-001', 'fp1')], [finding('FIC-001', 'fp1'), finding('FIC-002', 'fp2')], addition)
        self.assertEqual([item['DisplayId'] for item in comparison['display_identity']['added']], ['FIC-002'])
        self.assertEqual(comparison['findings_added'], ['FIC-002'])
        self.assertEqual(comparison['display_identity']['migrations'], [])
        self.assertEqual(status, 'ExpectedSemanticDifferences')

    def test_missing_persistent_id_with_equal_semantic_key_matches_by_semantic_key(self):
        comparison, status = compare([finding('FIC-001', 'fp1')], [finding('FIC-007', 'fp1')])
        migration, = comparison['display_identity']['migrations']
        self.assertEqual((migration['PreviousDisplayIds'], migration['CurrentDisplayId']), (['FIC-001'], 'FIC-007'))
        self.assertIsNone(migration['PersistentFindingId'])
        self.assertEqual(migration['MatchedBy'], 'SemanticKey')
        self.assertFalse(migration['PreviousIdRecordedAsAlias'])
        self.assertEqual(status, 'PresentationOnlyDifferences')

    def test_ambiguous_identity_relationship_remains_unresolved(self):
        # Two indistinguishable findings on each side, under new display IDs: no pairing can be established.
        twins = lambda *ids: [finding(display, 'fp-twin', EvidenceId='EV-twin') for display in ids]
        attempt = transition({'scope': 'finding', 'id': ['FIC-001', 'FIC-002', 'FIC-003', 'FIC-004'], 'field': 'presence',
                              'from': ['present', 'absent'], 'to': ['present', 'absent']})
        comparison, status = compare(twins('FIC-001', 'FIC-002'), twins('FIC-003', 'FIC-004'), attempt)
        identity = comparison['display_identity']
        self.assertEqual(sorted(item['DisplayId'] for item in identity['unresolved']), ['FIC-001', 'FIC-002', 'FIC-003', 'FIC-004'])
        for key in ('migrations', 'reuse_conflicts', 'retired', 'added'):
            self.assertEqual(identity[key], [], key)
        self.assertEqual(identity['matched_by'], {})
        # An unresolved relationship can never be explained, so it stays unexpected.
        self.assertEqual(comparison['expected_changes'], [])
        self.assertEqual(status, 'UnexpectedSemanticRegression')

    def test_display_id_alone_never_pairs_findings(self):
        comparison, status = compare([finding('FIC-001', 'fp1')], [finding('FIC-001', 'fp9')])
        self.assertEqual(comparison['display_identity']['matched_by'], {})
        self.assertEqual(status, 'DisplayIdentityConflict')


class TransitionRegisterTests(unittest.TestCase):
    def test_repository_register_is_valid_and_declares_evidence(self):
        for entry in v.load_transitions():
            self.assertIn(entry['kind'], v.EXPECTED_KINDS)
            self.assertRegex(entry['introduced_by'], r'^[0-9a-f]{40}$')
            self.assertTrue(entry['reason'] and entry['rule'])

    def test_register_rejects_open_ended_or_unknown_entries(self):
        folder = Path(tempfile.mkdtemp(prefix='validation-register-test-'))
        try:
            for bad in (transition({'scope': 'finding', 'field': 'Status', 'to': ['Insight']}),
                        transition({'scope': 'finding', 'field': 'Status', 'from': ['Warning']}),
                        transition({'scope': 'finding', 'field': 'Status', 'from': ['Warning'], 'to': ['Insight']}, kind='current_output'),
                        dict(transition({'scope': 'control', 'field': 'status', 'from': ['A'], 'to': ['B']}), rule='')):
                path = folder / 'register.json'
                path.write_text(json.dumps({'transitions': [bad]}), encoding='utf-8')
                with self.assertRaises(v.ValidationError):
                    v.load_transitions(path)
        finally:
            shutil.rmtree(folder)

    def test_transition_applies_only_between_the_recorded_commits(self):
        rows = [finding('FIC-001', 'fp1'), finding('FIC-002', 'fp2')]
        retirement = transition({'scope': 'finding', 'id': 'FIC-002', 'field': 'presence', 'from': ['present'], 'to': ['absent']})
        # The retained package already contains the change: the transition cannot explain the difference.
        comparison = v.compare_results(result(rows, commit=INTRODUCED), result(rows[:1], commit=CURRENT),
                                       transitions=[retirement], history=FictionalHistory())
        self.assertEqual(v.comparison_status(comparison, []), 'UnexpectedSemanticRegression')
        self.assertFalse(comparison['transitions_evaluated'][0]['applies'])

    def test_previously_absent_field_is_verified_from_the_retained_result(self):
        introduced = transition({'scope': 'finding', 'field': 'FindingClass', 'previously_absent': True},
                                kind='declared_compatibility_transition')
        comparison, status = compare([finding('FIC-001', 'fp1')], [finding('FIC-001', 'fp1', FindingClass='Customer action')],
                                     introduced)
        self.assertEqual(status, 'ExpectedSemanticDifferences')
        # A retained row that already had the field (even empty) is not a newly introduced field.
        comparison, status = compare([finding('FIC-001', 'fp1', FindingClass=None)],
                                     [finding('FIC-001', 'fp1', FindingClass='Customer action')], introduced)
        self.assertEqual(status, 'UnexpectedSemanticRegression')


@contextmanager
def pinned_run_identity(snapshot):
    """Reuse the retained run's identity, so run-scoped tie-breaks are reproducible in an exact-match rebuild."""
    identity = json.loads(Path(snapshot).read_text(encoding='utf-8'))['identity']
    values = iter([UUID(identity['AssessmentId'].split('-', 1)[1]), UUID(identity['RunId'].split('-', 1)[1])])
    with patch('Core.assessment_identity.uuid4', side_effect=lambda: next(values, None) or uuid4()):
        yield


@fictional_publication
class RebuildOutcomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = Path(tempfile.mkdtemp(prefix='validation-outcome-test-'))
        cls.package = build_retained_package(cls.temp / 'retained')
        cls.snapshot = next(cls.package.glob('assessment-results/*.json'))
        cls.hashes = v.tree_hashes(cls.package)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp, ignore_errors=True)

    def tearDown(self):
        self.assertEqual(v.tree_hashes(self.package), self.hashes, 'The retained package changed.')

    def rebuild(self, name, **kwargs):
        return v.offline_rebuild(self.package, self.temp / name, **{'revision': fictional_revision(), **kwargs})

    def test_successful_rebuild_with_exact_match(self):
        with pinned_run_identity(self.snapshot):
            report = self.rebuild('exact')
        self.assertEqual(report['execution_status'], 'Completed')
        self.assertEqual(report['comparison_status'], 'ExactMatch', report['presentation_differences'])
        self.assertEqual((report['exit_code'], report['expected_changes'], report['unexpected_regressions']), (0, [], []))
        self.assertTrue(report['passed'])

    def test_successful_rebuild_with_presentation_only_differences(self):
        from Core import customer_report
        with pinned_run_identity(self.snapshot), patch.object(customer_report, 'REPORT_CSS',
                                                              customer_report.REPORT_CSS + '\n/* presentation-only change */'):
            report = self.rebuild('presentation')
        self.assertEqual((report['execution_status'], report['comparison_status'], report['exit_code']),
                         ('Completed', 'PresentationOnlyDifferences', 0))
        self.assertIn('report.html: markup or styling changed', report['presentation_differences'])
        self.assertFalse(report['semantic_differences'])

    def lane_change(self, name, transitions):
        """A fictional methodology change: every finding's ActionLane becomes FictionalLane (no actions in this fixture)."""
        from Core import customer_actions
        with patch.object(customer_actions, 'action_lane', return_value='FictionalLane'):
            return self.rebuild(name, revision=dict(fictional_revision(), commit_sha=CURRENT), transitions=transitions,
                                history=FictionalHistory())

    def test_successful_rebuild_with_explained_methodology_differences(self):
        from Core.customer_actions import LANE_ORDER
        lanes = transition({'scope': 'finding', 'field': 'ActionLane', 'from': [None, 'PlanImprovement', *LANE_ORDER],
                            'to': ['FictionalLane']})
        report = self.lane_change('explained', [lanes])
        self.assertEqual((report['execution_status'], report['comparison_status'], report['exit_code']),
                         ('Completed', 'ExpectedSemanticDifferences', 0))
        self.assertTrue(report['passed'])
        self.assertEqual(report['unexpected_regressions'], [])
        self.assertTrue(report['expected_changes'])
        for change in report['expected_changes']:
            self.assertEqual((change['field'], change['current'], change['transition']), ('ActionLane', 'FictionalLane', 'T-fictional'))
            for key in ('previous', 'id', 'evidence', 'producing_commits', 'versions', 'reason', 'rule'):
                self.assertIn(key, change)
            self.assertEqual(change['producing_commits']['introduced_by'], INTRODUCED)
        text = (Path(report['output_directory']) / 'comparison.md').read_text(encoding='utf-8')
        self.assertLess(text.index('- Execution status: Completed'), text.index('## Expected semantic changes'))
        self.assertIn('- Comparison status: ExpectedSemanticDifferences', text)

    def test_successful_rebuild_with_unexplained_semantic_differences(self):
        report = self.lane_change('unexplained', [])
        self.assertEqual((report['execution_status'], report['comparison_status'], report['exit_code']),
                         ('Completed', 'UnexpectedSemanticRegression', 1))
        self.assertEqual(report['expected_changes'], [])
        self.assertTrue(report['unexpected_changes'])
        self.assertTrue(all(change['field'] == 'ActionLane' for change in report['unexpected_changes']))

    def test_execution_failure(self):
        from Core import offline_report
        for name, kwargs in (('failed-exit', {'return_value': 1}), ('failed-crash', {'side_effect': RuntimeError('fictional crash')})):
            with self.subTest(name), patch.object(offline_report, 'run_offline_report', **kwargs):
                with self.assertRaises(v.ValidationError) as caught:
                    self.rebuild(name)
                self.assertEqual(caught.exception.status, 'FailedToExecute')
                self.assertEqual(v.EXIT_CODES[caught.exception.status], 4)
                written = json.loads((self.temp / name / 'comparison.json').read_text(encoding='utf-8'))
                self.assertEqual((written['execution_status'], written['comparison_status']), ('FailedToExecute', None))

    def test_invalid_package(self):
        empty = self.temp / 'invalid-empty'
        empty.mkdir()
        corrupt = self.temp / 'invalid-corrupt'
        shutil.copytree(self.package, corrupt)
        snapshot = next(corrupt.glob('assessment-results/*.json'))
        snapshot.write_text(snapshot.read_text(encoding='utf-8').replace('"decision"', '"decision_altered"', 1), encoding='utf-8')
        for package in (empty, corrupt):
            with self.subTest(package.name), self.assertRaises(v.InvalidPackageError) as caught:
                v.offline_rebuild(package, self.temp / (package.name + '-out'), revision=fictional_revision())
            self.assertEqual((caught.exception.status, v.EXIT_CODES['InvalidPackage']), ('InvalidPackage', 2))
            self.assertFalse((self.temp / (package.name + '-out')).exists())

    def test_incompatible_package(self):
        with self.assertRaises(v.IncompatiblePackageError) as caught:
            self.rebuild('incompatible', revision=dict(fictional_revision(), methodology_version='9.0.0'))
        self.assertEqual((caught.exception.status, v.EXIT_CODES['IncompatiblePackage']), ('IncompatiblePackage', 5))
        self.assertIn('major version', str(caught.exception))
        self.assertFalse((self.temp / 'incompatible').exists())
        retained = json.loads(self.snapshot.read_text(encoding='utf-8'))
        declared = transition({'scope': 'decision', 'field': 'decision', 'from': ['x'], 'to': ['y']},
                              kind='declared_compatibility_transition')
        declared['compatible_from'] = {'methodology': retained['methodology_version'].split('.')[0]}
        self.assertEqual(v.package_compatibility(retained, {'methodology': '9.0.0', 'taxonomy': '1.1.0'}, [declared]), [])

    def test_isolated_temporary_directories_prevent_file_collision(self):
        first = v.render_only(self.snapshot, revision=fictional_revision())
        second = v.render_only(self.snapshot, revision=fictional_revision())
        try:
            a, b = Path(first['output_directory']), Path(second['output_directory'])
            self.assertNotEqual(a, b)
            self.assertFalse(a.is_relative_to(b) or b.is_relative_to(a))
            self.assertTrue(a.name.startswith('assessment-validation-') and b.name.startswith('assessment-validation-'))
            self.assertEqual(json.loads((a / 'comparison.json').read_text(encoding='utf-8'))['output_directory'], str(a))
            with self.assertRaises(v.ValidationError):
                v.engineering_output(a)  # an existing, non-empty folder is never reused
        finally:
            for report in (first, second):
                shutil.rmtree(report['output_directory'])

    def test_file_handles_are_released_before_cleanup(self):
        report = v.render_only(self.snapshot, self.temp / 'handles', revision=fictional_revision())
        workbook = next(Path(report['render_directory']).rglob('*.xlsx'))
        probe = self.temp / 'handle-probe.xlsx'
        shutil.copyfile(workbook, probe)
        with patch('tools.audit_workbook._compatibility_issues', side_effect=RuntimeError('fictional audit failure')):
            with self.assertRaises(RuntimeError):
                v.workbook_fingerprint(probe)
        os.remove(probe)  # Windows refuses to delete a file that is still open
        shutil.rmtree(self.temp / 'handles')  # strict: every output handle is closed
        self.assertFalse((self.temp / 'handles').exists())


if __name__ == '__main__':
    unittest.main()

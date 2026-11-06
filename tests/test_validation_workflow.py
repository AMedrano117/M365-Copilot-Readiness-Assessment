"""Layered validation workflow on a fictional package; no tenant access (tools/validate.py)."""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from provenance_fixture import fictional_publication, fictional_revision
from synthetic_package_fixture import create_synthetic_package

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import validate as v  # noqa: E402


def build_retained_package(folder):
    """A completed fictional package built by production offline code, as a retained package would be."""
    from Core.cli_parser import parse_arguments
    from Core.offline_report import run_offline_report
    folder.mkdir(parents=True)
    collection = create_synthetic_package(folder / 'evidence')
    previous_cwd, previous_argv = Path.cwd(), sys.argv
    try:
        os.chdir(folder)
        sys.argv = ['main.py', '--mode', 'offline', '--run-type', 'Standalone', '--collection-input', str(collection),
                    '--evaluation-date', '2026-09-15', '--color', 'never']
        with contextlib.redirect_stdout(io.StringIO()):
            code = run_offline_report(parse_arguments(None, []))
    finally:
        os.chdir(previous_cwd)
        sys.argv = previous_argv
    assert code == 0, code
    manifests = sorted((folder / 'Reports').rglob('assessment-run.json'))
    assert len(manifests) == 1, manifests
    return manifests[0].parent


@fictional_publication
class LayeredValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = Path(tempfile.mkdtemp(prefix='validation-workflow-test-'))
        cls.package = build_retained_package(cls.temp / 'retained')
        cls.before = v.tree_hashes(cls.package)
        cls.snapshot = next(cls.package.glob('assessment-results/*.json'))
        cls.rebuild = v.offline_rebuild(cls.package, cls.temp / 'rebuild', revision=fictional_revision())
        cls.render = v.render_only(cls.snapshot, cls.temp / 'render', revision=fictional_revision())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp, ignore_errors=True)

    def test_scenario_layer_performs_no_tenant_access(self):
        outcome = v.run_scenarios(['Supported component plus unread required component', 'Duplicate evidence supporting one action'],
                                  stream=io.StringIO())
        self.assertTrue(outcome['passed'], outcome)
        self.assertGreater(outcome['tests_run'], 0)
        self.assertEqual(outcome['tenant_access_attempts'], [])

    def test_guard_blocks_network_powershell_and_tokens(self):
        attempts = []
        with v.tenant_access_guard(attempts):
            with self.assertRaises(v.TenantAccessBlocked):
                socket.create_connection(('login.microsoftonline.example.invalid', 443))
            with self.assertRaises(v.TenantAccessBlocked):
                subprocess.Popen(['powershell.exe', '-NoProfile', '-Command', 'exit'])
            self.assertEqual(subprocess.run([sys.executable, '-c', 'pass']).returncode, 0)  # local processes run
        self.assertEqual([item['kind'] for item in attempts], ['network', 'powershell'])

    def test_offline_rebuild_performs_no_tenant_access(self):
        self.assertFalse(self.rebuild['tenant_access_performed'])
        self.assertTrue(self.rebuild['passed'], self.rebuild['failures'])

    def test_render_only_performs_no_tenant_access(self):
        self.assertFalse(self.render['tenant_access_performed'])
        self.assertTrue(self.render['passed'], self.render['failures'])

    def test_original_package_hashes_unchanged(self):
        self.assertEqual(v.tree_hashes(self.package), self.before)
        self.assertEqual(self.rebuild['source_package_sha256'], v.package_digest(self.before))

    def test_rebuild_uses_retained_observation_dates(self):
        retained = json.loads(self.snapshot.read_text(encoding='utf-8'))
        self.assertEqual(self.rebuild['rebuilt_evaluation_date'], retained['evaluation_date'])
        self.assertIsNone(self.rebuild['semantic']['evaluation_date'])
        self.assertFalse([item for item in self.rebuild['unexpected_regressions'] if 'observation date' in item])

    def test_stable_ids_unchanged_without_semantic_change(self):
        self.assertFalse(self.rebuild['semantic_differences'], self.rebuild['semantic'])
        self.assertEqual(self.rebuild['semantic']['stable_ids_changed'], {})

    def test_changed_methodology_produces_semantic_delta(self):
        from Core import assessment_result
        with patch.object(assessment_result, 'aggregate_control_status', return_value=('Not established', [])):
            report = v.offline_rebuild(self.package, self.temp / 'changed-methodology', revision=fictional_revision())
        self.assertTrue(report['semantic_differences'])
        self.assertTrue(report['semantic']['controls_changed'])
        self.assertTrue(any(item['fields'].get('status') for item in report['semantic']['controls_changed'].values()
                            if item['change'] == 'changed'))

    def test_render_only_cannot_alter_semantic_results(self):
        from Core import assessment_serialization
        original = assessment_serialization.write_assessment_result

        def altered(path, result, *args, **kwargs):
            return original(path, dict(result, decision='Altered by renderer'), *args, **kwargs)
        with patch.object(assessment_serialization, 'write_assessment_result', side_effect=altered):
            report = v.render_only(self.snapshot, self.temp / 'render-altered', revision=fictional_revision())
        self.assertFalse(report['passed'])
        self.assertEqual((report['execution_status'], report['comparison_status'], report['exit_code']),
                         ('Completed', 'UnexpectedSemanticRegression', 1))
        self.assertIn('Render-only output changed semantic results.', report['unexpected_regressions'])
        result = json.loads(self.snapshot.read_text(encoding='utf-8'))
        changed = v.compare_results(result, dict(result, controls=[]), mode='render')
        self.assertIn('Render-only output changed semantic results.', changed['unexpected_regressions'])
        # A declared transition never explains a render-only difference.
        anything = [{'id': 'T-any', 'kind': 'methodology_rule', 'reason': 'r', 'rule': 'r', 'introduced_by': 'c' * 40,
                     'matches': [{'scope': 'decision', 'field': 'decision', 'from': [result['decision']], 'to': ['Altered by renderer']}]}]
        altered = v.compare_results(result, dict(result, decision='Altered by renderer'), mode='render', transitions=anything)
        self.assertEqual((altered['expected_changes'], v.comparison_status(altered, [])), ([], 'UnexpectedSemanticRegression'))

    def test_render_only_creates_no_history_baseline_or_delta(self):
        self.assertFalse(self.render['history_created'])
        self.assertFalse(self.render['baseline_selected'])
        self.assertFalse(self.render['customer_publication_eligible'])
        output = Path(self.render['output_directory'])
        self.assertFalse(list(output.rglob('assessment-history.json')))
        self.assertTrue((output / 'ENGINEERING-VALIDATION.txt').is_file())
        self.assertIsNone(self.render['semantic']['identity'])
        self.assertIsNone(self.render['semantic']['lifecycle'])

    def test_generated_outputs_open(self):
        for report in (self.rebuild, self.render):
            roles = set(report['output_files'])
            self.assertTrue({'report.html', 'report.xlsx', 'summary.html', 'technical.xlsx'} <= roles, roles)
            self.assertFalse([item for item in report['failures'] if 'could not be opened' in item])
        self.assertTrue(self.rebuild['replay_passed'])
        self.assertTrue((Path(self.rebuild['output_directory']) / 'comparison.md').is_file())

    def test_fails_safely_on_malformed_or_incomplete_packages(self):
        with self.assertRaises(v.ValidationError):
            v.retained_package(self.temp / 'missing')
        empty = self.temp / 'no-collection'
        empty.mkdir()
        with self.assertRaises(v.ValidationError):
            v.retained_package(empty)
        corrupt = self.temp / 'corrupt'
        shutil.copytree(self.package, corrupt)
        snapshot = next(corrupt.glob('assessment-results/*.json'))
        snapshot.write_text(snapshot.read_text(encoding='utf-8').replace('"decision"', '"decision_altered"', 1), encoding='utf-8')
        with self.assertRaises(v.ValidationError):
            v.retained_package(corrupt)
        with self.assertRaises(v.ValidationError):
            v.engineering_output(self.package / 'inside', protected=[self.package])
        malformed = self.temp / 'malformed.json'
        malformed.write_text('{not json', encoding='utf-8')
        with self.assertRaises(v.ValidationError):
            v.render_only(malformed, self.temp / 'render-malformed', revision=fictional_revision())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(v.main(['rebuild', '--package', str(empty)]), 2)


class PresentationOnlyTests(unittest.TestCase):
    def test_semantic_view_ignores_presentation_fields(self):
        result = {'decision': 'X', 'recommendations': [{'RecommendationId': 'R1', 'FindingKey': 'k', 'Observation': 'old wording'}]}
        reworded = {'decision': 'X', 'recommendations': [{'RecommendationId': 'R1', 'FindingKey': 'k', 'Observation': 'new wording'}]}
        self.assertFalse(v.compare_results(result, reworded, mode='render')['semantic_differences'])


@fictional_publication
class RenderOnlyTests(unittest.TestCase):
    def test_presentation_only_change_has_no_semantic_difference(self):
        """Declared: a styling change is a presentation difference, with no semantic difference."""
        temp = Path(tempfile.mkdtemp(prefix='validation-presentation-test-'))
        try:
            package = build_retained_package(temp / 'retained')
            snapshot = next(package.glob('assessment-results/*.json'))
            first = v.render_only(snapshot, temp / 'first', revision=fictional_revision())
            from Core import customer_report
            with patch.object(customer_report, 'REPORT_CSS', customer_report.REPORT_CSS + '\n/* presentation-only change */'):
                second = v.render_only(snapshot, temp / 'second', revision=fictional_revision(),
                                       compare_to=Path(first['render_directory']))
            self.assertTrue(second['passed'], second['failures'])
            self.assertFalse(second['semantic_differences'])
            self.assertIn('report.html: markup or styling changed', second['presentation_differences'])
        finally:
            shutil.rmtree(temp, ignore_errors=True)


class NoRetainedEvidenceInFixturesTests(unittest.TestCase):
    def test_cavehouse_identifiers_never_in_committed_files(self):
        tracked = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertFalse([path for path in tracked if path.startswith('Reports/')])
        packages = sorted((ROOT / 'Reports').glob('Cave' + 'house*/*/collection.json')) if (ROOT / 'Reports').is_dir() else []
        if not packages:
            self.skipTest('The retained engineering package is not present on this machine.')
        text = packages[0].read_text(encoding='utf-8', errors='replace')
        tokens = {json.loads(text).get('tenant_id') or ''} | set(re.findall(r'[a-z0-9-]+\.onmicrosoft\.com', text.lower()))
        tokens = {token.lower() for token in tokens if token}
        self.assertTrue(tokens)
        leaked = []
        for path in tracked:
            if path.split('/')[0] in {'tests', 'tools', 'Core', 'docs', 'Recommendations'} and Path(ROOT / path).is_file():
                content = (ROOT / path).read_text(encoding='utf-8', errors='ignore').lower()
                leaked += [path for token in tokens if token in content]
        self.assertEqual(leaked, [])


if __name__ == '__main__':
    unittest.main()

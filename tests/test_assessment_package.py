"""Portable replay retains source files/settings and never needs the source machine."""

import contextlib
import io
import json
import os
import runpy
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from Core.assessment_package import restore_arguments
from Core.cli_parser import parse_arguments
from Core.offline_collection import (
    collection_context, empty_service_results, load_collection,
    refresh_saved_freshness, save_collection,
)
from Core.offline_report import run_offline_report
from Core.portal_report_import import validate_report_tenants


TENANT = '11111111-1111-1111-1111-111111111111'


class AssessmentPackageTests(unittest.TestCase):
    def parse(self, *arguments):
        with patch('sys.argv', ['main.py', *map(str, arguments)]):
            return parse_arguments(None, [])

    def saved(self, folder, **options):
        results = empty_service_results()
        results['m365_result'][0]['_client'] = SimpleNamespace(copilot_usage={
            'available': True, 'refresh_date': '2026-09-10', 'freshness': 'Fresh',
            'user_detail': [{'userPrincipalName': 'private@example.invalid'}],
        })
        path = save_collection(folder / 'customer.json', tenant_id=TENANT,
                               tenant_name='Example customer', service_results=results,
                               evaluation_date='2026-09-15', **options)
        return Path(path)

    def test_package_moves_without_original_inputs_and_restores_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'original reports'
            source.mkdir()
            report = source / 'permissions.csv'
            report.write_text('Site URL,Everyone except external users\nhttps://example.invalid/sites/a,Yes\n', encoding='utf-8')
            profile = root / 'profile.json'
            profile.write_text('{"version":"1","products":[]}', encoding='utf-8')
            collection = self.saved(root, supplemental_inputs={
                'reports_dir': [source], 'assessment_profile': profile,
            }, assessment_settings={'report_format': 'both', 'data_exposure_enabled': False})
            original = load_collection(collection)
            moved = root / 'moved customer assessment'
            shutil.copytree(original['package_directory'], moved)
            report.unlink()
            profile.unlink()
            collection.unlink()
            payload = load_collection(moved / 'collection.json')
            args = restore_arguments(self.parse('--collection-input', moved / 'collection.json'), payload)
            self.assertEqual(args.report_format, 'both')
            self.assertEqual(args.evaluation_date, '2026-09-15')
            self.assertTrue(Path(args.assessment_profile).is_relative_to(moved))
            self.assertEqual((Path(args.reports_dir[0]) / report.name).read_text(encoding='utf-8'),
                             'Site URL,Everyone except external users\nhttps://example.invalid/sites/a,Yes\n')
            self.assertNotIn(str(source), (moved / 'collection.json').read_text(encoding='utf-8'))
            self.assertFalse(args.include_user_usage_detail)

    def test_offline_additions_survive_move_without_collection_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            collection = self.saved(root)
            payload = load_collection(collection)
            folder = Path(payload['package_directory'])
            canonical = folder / 'collection.json'
            original = canonical.read_bytes()
            additions = root / 'new_exports'
            additions.mkdir()
            report = additions / 'report.csv'
            report.write_text('Tenant ID,Anyone link count\n' + TENANT + ',1\n', encoding='utf-8')
            arguments = self.parse('--collection-input', canonical, '--reports-dir', additions,
                                   '--report-format', 'both', '--evaluation-date', '2026-09-20')
            with contextlib.redirect_stdout(io.StringIO()), \
                 patch('Core.processor.process_and_print_all_information', return_value={'html_path': 'example.html'}), \
                 patch('Core.offline_collection.save_collection', side_effect=AssertionError('Offline saved collection')), \
                 patch('builtins.input', side_effect=AssertionError('Offline prompted')), \
                 patch('socket.create_connection', side_effect=AssertionError('Offline network')):
                self.assertEqual(run_offline_report(arguments), 0)
            self.assertEqual(canonical.read_bytes(), original)
            moved = root / 'final_package'
            shutil.copytree(folder, moved)
            report.unlink()
            loaded = load_collection(moved / 'collection.json')
            replay_args = restore_arguments(self.parse('--collection-input', moved / 'collection.json'), loaded)
            self.assertEqual(replay_args.report_format, 'both')
            self.assertEqual(replay_args.evaluation_date, '2026-09-20')
            retained = Path(replay_args.reports_dir[0]) / report.name
            self.assertTrue(retained.is_file())
            self.assertTrue(retained.is_relative_to(moved))
            self.assertTrue((moved / 'operator-log.jsonl').is_file())

    def test_hash_and_path_validation_reject_tampered_or_escaping_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / 'report.csv'
            report.write_text('Site URL,Anyone link count\nhttps://example.invalid,1\n', encoding='utf-8')
            collection = self.saved(root, supplemental_inputs={'sam_report': [report]})
            loaded = load_collection(collection)
            retained = Path(loaded['resolved_inputs']['sam_report'][0])
            original = retained.read_bytes()
            retained.write_text('altered evidence', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'has changed'):
                load_collection(collection)
            retained.write_bytes(original)
            payload = json.loads(collection.read_text(encoding='utf-8'))
            payload['package']['files'][0]['path'] = '../../report.csv'
            collection.write_text(json.dumps(payload), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'escapes'):
                load_collection(collection)

    def test_replay_keeps_evaluation_day_and_allows_explicit_later_review(self):
        with tempfile.TemporaryDirectory() as directory:
            collection = self.saved(Path(directory))
            payload = load_collection(collection)
            for evaluated, age, freshness in [('2026-09-15', 5, 'Fresh'), ('2026-09-25', 15, 'Stale')]:
                results = load_collection(collection)['service_results']
                refresh_saved_freshness(results, evaluated)
                evidence = results['m365_result'][0]['_client'].copilot_usage
                self.assertEqual((evidence['age_days'], evidence['freshness']), (age, freshness))
            self.assertEqual(collection_context(payload)['evaluation_date'], '2026-09-15')
            args = restore_arguments(self.parse('--collection-input', collection,
                                                 '--evaluation-date', '2026-09-25'), payload)
            self.assertEqual(args.evaluation_date, '2026-09-25')

    def test_credentials_and_unrelated_files_are_not_copied_from_export_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reports = root / 'exports'
            reports.mkdir()
            (reports / '.env').write_text('CLIENT_SECRET=private', encoding='utf-8')
            (reports / 'auth.pfx').write_bytes(b'private certificate')
            (reports / 'portal.pdf').write_bytes(b'%PDF-1.7\n')
            (reports / 'report.csv').write_text('Site URL,Anyone link count\n', encoding='utf-8')
            loaded = load_collection(self.saved(root, supplemental_inputs={'reports_dir': [reports]}))
            folder = Path(loaded['package_directory'])
            self.assertFalse(list(folder.rglob('*.pfx')))
            self.assertFalse(list(folder.rglob('.env')))
            self.assertFalse(list(folder.rglob('*.pdf')))
            self.assertEqual(len(loaded['package']['files']), 1)
            self.assertEqual(len(loaded['package']['diagnostics']), 3)
            pdf = next(row for row in loaded['package']['diagnostics'] if row['source_file'] == 'portal.pdf')
            self.assertEqual(pdf['status'], 'reference_only')
            self.assertIn('--portal-review', pdf['reason'])

    def test_credential_json_is_not_copied_and_collected_facts_are_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / 'profile.json'
            profile.write_text('{"version":1,"client_secret":"DO_NOT_COPY"}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'credential fields'):
                self.saved(root, supplemental_inputs={'assessment_profile': profile})
            self.assertEqual(load_collection(root / 'customer.json')['tenant_id'], TENANT)
            for copied in (root / 'customer_package').rglob('*'):
                if copied.is_file():
                    self.assertNotIn('DO_NOT_COPY', copied.read_text(encoding='utf-8'))

    def test_legacy_v1_json_still_loads_without_package_or_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.saved(Path(directory))
            payload = json.loads(path.read_text(encoding='utf-8'))
            payload['version'] = 1
            for key in ('package', 'evaluation_date', 'assessment_settings'):
                payload.pop(key)
            path.write_text(json.dumps(payload), encoding='utf-8')
            loaded = load_collection(path)
            self.assertEqual(loaded['version'], 1)
            self.assertNotIn('resolved_inputs', loaded)

    def test_domain_identity_cannot_bypass_tenant_tag_and_missing_tag_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'report.csv'
            report.write_text('Tenant ID,Anyone link count\n' + TENANT + ',1\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'does not match'):
                validate_report_tenants([report], 'example.onmicrosoft.com')
            receipt = []
            validate_report_tenants([report], TENANT, receipt=receipt)
            self.assertEqual(receipt[0]['Status'], 'identity_verified')
            report.write_text('Site URL,Anyone link count\nhttps://example.invalid,1\n', encoding='utf-8')
            receipt = []
            validate_report_tenants([report], TENANT, receipt=receipt)
            self.assertEqual(receipt[0]['Status'], 'identity_unverified')

    def test_snapshot_output_cannot_overwrite_saved_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            collection = self.saved(Path(directory))
            original = collection.read_bytes()
            args = self.parse('--collection-input', collection, '--snapshot-json', collection)
            with self.assertRaisesRegex(ValueError, 'cannot overwrite'):
                run_offline_report(args)
            self.assertEqual(collection.read_bytes(), original)

    def test_different_methodology_cannot_silently_replay_old_conclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            collection = self.saved(Path(directory))
            payload = json.loads(collection.read_text(encoding='utf-8'))
            payload['methodology_version'] = '0.0.0'
            collection.write_text(json.dumps(payload), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'matching tool version'):
                load_collection(collection)

    def test_collectionless_offline_recipe_preserves_originals_without_raw_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            report = root / 'portal.csv'
            report.write_text('Tenant ID,Anyone link count\n' + TENANT + ',0\n', encoding='utf-8')
            args = self.parse('--mode', 'offline', '--sam-report', report, '--tenant-name', 'Example portal review')
            previous = Path.cwd()
            try:
                os.chdir(root)
                with contextlib.redirect_stdout(io.StringIO()), \
                     patch('Core.processor.process_and_print_all_information', return_value={'html_path': 'example.html'}), \
                     patch('Core.offline_collection.save_collection', side_effect=AssertionError('Offline saved collection')):
                    self.assertEqual(run_offline_report(args), 0)
            finally:
                os.chdir(previous)
            recipe_path = next((root / 'output' / 'assessments').glob('*/rebuild.json'))
            folder = recipe_path.parent
            self.assertFalse((folder / 'collection.json').exists())
            copied = root / 'moved'
            shutil.copytree(folder, copied)
            report.unlink()
            payload = load_collection(copied / 'rebuild.json')
            self.assertFalse(payload['has_tenant_collection'])
            replay = restore_arguments(self.parse('--collection-input', copied / 'rebuild.json'), payload)
            self.assertTrue(Path(replay.sam_report[0]).is_relative_to(copied))
            self.assertTrue(Path(replay.sam_report[0]).is_file())

    def test_legacy_cache_and_prior_recipe_replay_preserves_assessment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prior = root / 'prior.json'
            prior.write_text(json.dumps({'tenant_id': TENANT, 'tenant': 'Example legacy tenant',
                                          'generated_at': '2026-09-09T12:00:00+00:00',
                                          'recommendations': [{'Service': 'Entra', 'Feature': 'Earlier MFA review',
                                                               'Observation': 'An earlier multifactor authentication review required confirmation.',
                                                               'Recommendation': 'Confirm the review result.', 'Disposition': 'Action', 'Priority': 'High'}]}), encoding='utf-8')
            cache = root / 'purview.json'
            cache.write_text(json.dumps({'schema_version': 2, 'tenant_id': TENANT,
                                          'cached_at_epoch': datetime(2026, 9, 9, tzinfo=timezone.utc).timestamp(),
                                          'purview_data_json': json.dumps({'dlp_policies': {'available': True, 'count': 1,
                                                                                        'policies': [{'Name': 'Fictional DLP', 'Mode': 'Enable'}]}})}), encoding='utf-8')
            previous = Path.cwd()
            try:
                os.chdir(root)
                with contextlib.redirect_stdout(io.StringIO()), \
                     patch('Core.processor.export_tabular_reports', return_value=(None, None)), \
                     patch('Core.processor.export_to_html', return_value='example.html') as render, \
                     patch('Core.processor.print_recommendations_summary'), \
                     patch('Core.offline_collection.save_collection', side_effect=AssertionError('Offline saved collection')):
                    self.assertEqual(run_offline_report(self.parse('--prior-report', prior, '--purview-cache', cache,
                                                                   '--evaluation-date', '2026-09-15')), 0)
                    first = render.call_args.kwargs['evidence_bundle']['assessment_result']
                    recipe = next((root / 'output' / 'assessments').glob('*/rebuild.json'))
                    copied = root / 'copied_legacy_package'
                    shutil.copytree(recipe.parent, copied)
                    prior.unlink()
                    cache.unlink()
                    self.assertEqual(run_offline_report(self.parse('--collection-input', copied / 'rebuild.json')), 0)
                    second = render.call_args.kwargs['evidence_bundle']['assessment_result']
            finally:
                os.chdir(previous)
            self.assertEqual(first, second)
            self.assertFalse((copied / 'collection.json').exists())

    def test_complete_synthetic_cli_rebuild_without_originals_or_live_operations(self):
        from tests.synthetic_package_fixture import create_synthetic_package
        repository = Path(__file__).resolve().parents[1]
        def run_cli(collection, working):
            working.mkdir(parents=True, exist_ok=True)
            snapshot = working / 'snapshot.json'
            previous = Path.cwd()
            try:
                os.chdir(working)
                with contextlib.ExitStack() as stack:
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    stack.enter_context(patch('sys.argv', ['main.py', '--mode', 'offline', '--collection-input', str(collection), '--snapshot-json', str(snapshot)]))
                    stack.enter_context(patch('Core.console_setup.setup_console_encoding'))
                    for target in ('socket.socket.connect', 'socket.create_connection', 'subprocess.Popen', 'builtins.input',
                                   'Core.credentials_check.load_env_file', 'Core.credentials_check.validate_credentials_or_exit',
                                   'Core.offline_collection.save_collection'):
                        stack.enter_context(patch(target, side_effect=AssertionError('Offline performed a live or collection-write operation')))
                    with self.assertRaises(SystemExit) as completed:
                        runpy.run_path(str(repository / 'main.py'), run_name='__main__')
                    self.assertEqual(completed.exception.code, 0)
            finally:
                os.chdir(previous)
            return json.loads(snapshot.read_text(encoding='utf-8'))['assessment_result']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            original = root / 'source_machine'
            collection = Path(create_synthetic_package(original))
            # Keep generated workbooks outside the source tree that is moved.
            # Windows can retain an Excel/zip or indexing handle briefly after
            # export; those deliverables are unrelated to replay source access.
            first = run_cli(collection, root / 'first_build')
            self.assertEqual(first['decision'], 'Ready for a controlled pilot')
            self.assertEqual(first['rollout_progress']['current_stage_id'], 'pilot')
            self.assertEqual(first['counts']['evidence_gaps'], 0)
            self.assertEqual(len(first['domains']), 7)
            usage = [metric for metric in first['adoption_metrics']
                     if metric['metric_id'] == 'copilot.active_users' and metric['value'] is not None]
            self.assertTrue(usage, 'The fictional usage finding must be supported by the actual usage adapter.')
            self.assertEqual(len(usage), 1, 'The derived workbook row must not duplicate its source usage metric.')
            self.assertTrue(all(metric['value'] == 60 and metric['window'] == 'D28'
                                and metric['observed_at'] == '2026-09-14' for metric in usage))
            portable = Path(load_collection(collection)['package_directory'])
            moved = root / 'copied_customer_package'
            shutil.copytree(portable, moved)
            previous = Path.cwd()
            try:
                # Explicitly leave every directory below source_machine before
                # renaming it; Windows forbids moving the process working tree.
                os.chdir(root)
                # Windows indexing/antimalware can briefly retain a handle to a
                # newly written package. Retry that OS lock, while still requiring
                # the original tree to be absent before the replay is exercised.
                import gc
                import time
                for attempt in range(4):
                    try:
                        original.rename(root / 'unavailable_original_paths')
                        break
                    except PermissionError:
                        if os.name != 'nt' or attempt == 3:
                            raise
                        gc.collect()
                        time.sleep(0.1 * (attempt + 1))
                self.assertFalse(original.exists())
                second = run_cli(moved / 'collection.json', moved / 'second_build')
            finally:
                os.chdir(previous)
            self.assertEqual(first, second)
            self.assertTrue(list((moved / 'second_build' / 'Reports').glob('*.xlsx')))
            self.assertTrue(list((moved / 'second_build' / 'Reports').glob('*.html')))


if __name__ == '__main__':
    unittest.main()

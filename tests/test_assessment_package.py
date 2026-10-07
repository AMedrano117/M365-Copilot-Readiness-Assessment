"""Portable replay retains source files/settings and never needs the source machine."""

import contextlib
import io
import json
import os
import runpy
import shutil
import tempfile
import unittest
import zipfile
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

    def test_default_collection_has_one_copy_and_customer_name_survives_moving_package(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(directory).resolve()
        original = Path.cwd()
        self.addCleanup(os.chdir, original)
        os.chdir(root)
        export = root / 'source.csv'
        export.write_text('Site URL,Anyone link count\nhttps://example.invalid,1\n', encoding='utf-8')
        collection = Path(save_collection(
            tenant_id=TENANT, tenant_name='Tenant display name', customer_name='Customer / North',
            service_results=empty_service_results(), supplemental_inputs={'sam_report': [export]}))
        self.assertEqual(collection.parent.parent, root / 'Reports' / 'Customer North')
        self.assertEqual(list((root / 'Reports').rglob('collection.json')), [collection])
        self.assertFalse((root / 'output').exists())
        payload = load_collection(collection)
        self.assertEqual(payload['package']['directory'], '.')
        self.assertEqual(payload['package']['files'][0]['path'], 'inputs/1/source.csv')
        self.assertEqual(payload['package']['files'][0]['source_file'], 'source.csv')
        self.assertEqual(Path(payload['package_directory']), collection.parent)
        moved = root / 'approved customer storage'
        shutil.copytree(collection.parent, moved)
        export.unlink()
        payload = load_collection(moved / 'collection.json')
        restored = restore_arguments(self.parse('--collection-input', moved / 'collection.json'), payload)
        self.assertEqual(restored.customer_name, 'Customer / North')
        self.assertEqual(collection_context(payload)['customer_name'], 'Customer / North')
        self.assertEqual(Path(restored.sam_report[0]).read_text(encoding='utf-8'),
                         'Site URL,Anyone link count\nhttps://example.invalid,1\n')
        explicit = self.parse('--collection-input', moved / 'collection.json')
        explicit.customer_name = 'Customer legal name'
        self.assertEqual(restore_arguments(explicit, payload).customer_name, 'Customer legal name')

    def test_customer_name_is_retained_by_collectionless_rebuild_recipe(self):
        from Core.assessment_package import save_rebuild_recipe
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'assessment'
            args = self.parse('--mode', 'offline', '--evaluation-date', '2026-09-15')
            args.customer_name = 'Northwind customer'
            recipe = save_rebuild_recipe(folder, args, tenant_name='Tenant display name', tenant_id=TENANT)
            self.assertEqual(recipe['customer_name'], 'Northwind customer')
            self.assertTrue((folder / 'Rebuilds' / '1' / 'rebuild.json').is_file())
            loaded = load_collection(folder / 'rebuild.json')
            self.assertEqual(loaded['customer_name'], 'Northwind customer')
            restored = restore_arguments(self.parse('--collection-input', folder / 'rebuild.json'), loaded)
            self.assertEqual(restored.customer_name, 'Northwind customer')

    def test_outputs_rendered_into_package_are_receipted_without_another_deliverable_copy(self):
        from Core.assessment_package import record_package_run
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory).resolve() / 'portable'
            report_directory = folder / 'Builds' / '1'
            json_folder = report_directory / 'JSON'
            json_folder.mkdir(parents=True)
            index = json_folder / 'index.json'
            index.write_text('{"html":"../assessment.html"}', encoding='utf-8')
            html = report_directory / 'assessment.html'
            html.write_text('<html>customer assessment</html>', encoding='utf-8')
            snapshot = Path(directory) / 'requested_snapshot.json'
            snapshot.write_text(json.dumps({'format': 'm365-readiness-assessment', 'assessment': 'complete',
                'deliverable_path_base': 'index_directory',
                'deliverables': {'html': 'portable/Builds/1/assessment.html',
                                 'summary_html': html.as_uri(), 'workbook': None}}),
                encoding='utf-8')
            original_snapshot = snapshot.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()), \
                 patch('Core.assessment_package.shutil.copy2', wraps=shutil.copy2) as copy:
                delivered = record_package_run(
                    folder, mode='offline', tenant_id=TENANT, collected_at=None,
                    evaluation_date='2026-09-15', outputs={
                        'report_directory': report_directory, 'html_path': html,
                        'json_folder_path': json_folder, 'json_path': index, 'snapshot_path': snapshot})
            self.assertEqual(copy.call_count, 1, 'Only an explicitly requested external snapshot is copied.')
            self.assertEqual(list((folder / 'Builds').iterdir()), [report_directory])
            self.assertEqual(set(delivered), {'Builds/1/assessment.html', 'Builds/1/JSON/index.json',
                                             'Builds/1/requested_snapshot.json'})
            receipt = json.loads((folder / 'operator-log.jsonl').read_text(encoding='utf-8'))
            self.assertEqual(receipt['run_id'], '1')
            self.assertEqual(receipt['deliverables'], delivered)
            retained_snapshot = json.loads((report_directory / snapshot.name).read_text(encoding='utf-8'))
            self.assertEqual(retained_snapshot['deliverables'], {
                'html': 'assessment.html', 'summary_html': 'assessment.html', 'workbook': None})
            self.assertEqual(snapshot.read_bytes(), original_snapshot)
            self.assertEqual((index.parent / json.loads(index.read_text(encoding='utf-8'))['html']).read_bytes(),
                             html.read_bytes())

    def test_generated_json_folder_is_retained_with_nested_relative_links(self):
        from Core.assessment_package import record_package_run

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            portable = root / 'portable'
            portable.mkdir()
            collection = portable / 'collection.json'
            collection.write_text('{"original":true}', encoding='utf-8')
            original = collection.read_bytes()
            reports = root / 'Reports'
            generated = reports / 'json' / 'assessment'
            finding = generated / 'findings' / 'ENT-005.json'
            evidence = generated / 'evidence' / 'users' / 'part-001.json'
            finding.parent.mkdir(parents=True)
            evidence.parent.mkdir(parents=True)
            finding.write_text('{"finding_id":"ENT-005"}', encoding='utf-8')
            evidence.write_text('{"records":[{"displayName":"Jos\u00e9"}]}', encoding='utf-8')
            index = generated / 'index.json'
            index.write_text(json.dumps({'findings': 'findings/ENT-005.json',
                                         'evidence': 'evidence/users/part-001.json',
                                         'html': '../../assessment.html'}), encoding='utf-8')
            html = reports / 'assessment.html'
            html.write_text('<html>assessment</html>', encoding='utf-8')
            snapshot = root / 'explicit.json'
            snapshot.write_text('{"format":"m365-readiness-assessment"}', encoding='utf-8')
            upload = reports / 'assessment_json.zip'
            with zipfile.ZipFile(upload, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for file in generated.rglob('*.json'):
                    archive.write(file, file.relative_to(generated).as_posix())
            original_archive = upload.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                retained = record_package_run(
                    portable, mode='offline', tenant_id=TENANT, collected_at='2026-09-14',
                    evaluation_date='2026-09-15', outputs={
                        'json_folder_path': generated, 'json_path': index,
                        'html_path': html, 'snapshot_path': snapshot,
                        'json_archive_path': upload,
                        'evidence_bundle': {'dashboard_json_archive_path': str(upload)}})
            retained_indexes = [portable / path for path in retained if Path(path).name == 'index.json']
            self.assertEqual(len(retained_indexes), 1, 'The index must not also be copied without its linked folder.')
            copied_index = retained_indexes[0]
            copied = json.loads(copied_index.read_text(encoding='utf-8'))
            self.assertEqual((copied_index.parent / copied['findings']).read_bytes(), finding.read_bytes())
            self.assertEqual((copied_index.parent / copied['evidence']).read_bytes(), evidence.read_bytes())
            self.assertEqual((copied_index.parent / copied['html']).read_bytes(), html.read_bytes())
            self.assertEqual(collection.read_bytes(), original)
            retained_archives = [portable / path for path in retained if Path(path).suffix == '.zip']
            self.assertEqual(len(retained_archives), 1)
            self.assertEqual(retained_archives[0].read_bytes(), original_archive)
            with zipfile.ZipFile(retained_archives[0]) as archive:
                self.assertEqual(archive.read('index.json'), index.read_bytes())
                self.assertEqual(archive.read('findings/ENT-005.json'), finding.read_bytes())
            self.assertTrue(any(Path(path).name == 'explicit.json' for path in retained))
            receipt = json.loads((portable / 'operator-log.jsonl').read_text(encoding='utf-8'))
            self.assertEqual(receipt['deliverables'], retained)

    def test_legacy_deliverables_directory_is_receipted_in_place(self):
        from Core.assessment_package import record_package_run
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory).resolve()
            report_directory = folder / 'deliverables' / '20260915T120000Z_original'
            report_directory.mkdir(parents=True)
            html = report_directory / 'original.html'
            html.write_text('<html>retained legacy report</html>', encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()), \
                 patch('Core.assessment_package.shutil.copy2', side_effect=AssertionError('Legacy output must stay in place')):
                delivered = record_package_run(
                    folder, mode='offline', tenant_id=TENANT, collected_at=None,
                    evaluation_date='2026-09-15', outputs={'report_directory': report_directory, 'html_path': html})
            self.assertEqual(delivered, ['deliverables/20260915T120000Z_original/original.html'])
            self.assertFalse((folder / 'Builds').exists())
            self.assertEqual(html.read_text(encoding='utf-8'), '<html>retained legacy report</html>')

    def test_generic_json_folder_copy_keeps_its_links_to_companion_reports(self):
        from Core.assessment_package import record_package_run
        for name, supply_report_directory in (('JSON', False), ('JSON (2)', False), ('Assessment Data', True)):
            with self.subTest(json_folder=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                folder = root / 'portable'
                folder.mkdir()
                source_folder = root / 'source' / name
                source_folder.mkdir(parents=True)
                index = source_folder / 'index.json'
                index.write_text('{"html":"../AI Readiness and M365 Hardening.html"}', encoding='utf-8')
                html = source_folder.parent / 'AI Readiness and M365 Hardening.html'
                html.write_text('<html>customer assessment</html>', encoding='utf-8')
                outputs = {'html_path': html, 'json_path': index, 'json_folder_path': source_folder}
                if supply_report_directory:
                    outputs['report_directory'] = source_folder.parent
                with contextlib.redirect_stdout(io.StringIO()):
                    delivered = record_package_run(
                        folder, mode='offline', tenant_id=TENANT, collected_at=None,
                        evaluation_date='2026-09-15', outputs=outputs)
                copied_index = folder / 'Builds' / '1' / name / 'index.json'
                self.assertEqual(set(delivered), {f'Builds/1/{name}/index.json',
                                                 'Builds/1/AI Readiness and M365 Hardening.html'})
                reference = json.loads(copied_index.read_text(encoding='utf-8'))['html']
                self.assertEqual((copied_index.parent / reference).read_bytes(), html.read_bytes())

    def test_failed_render_receipts_partial_build_without_copying_or_changing_collection(self):
        from Core.assessment_package import render_with_failure_receipt
        with tempfile.TemporaryDirectory() as directory:
            collection = self.saved(Path(directory))
            package = Path(load_collection(collection)['package_directory'])
            canonical = package / 'collection.json'
            original = canonical.read_bytes()
            report_directory = package / 'Builds' / '1'
            report_directory.mkdir(parents=True)
            partial = report_directory / 'assessment.html'

            def interrupted_render(*, output_dir):
                (output_dir / 'assessment.html').write_text('<html>partial report</html>', encoding='utf-8')
                raise ValueError('Synthetic failure after writing HTML')

            with contextlib.redirect_stdout(io.StringIO()), \
                 patch('Core.assessment_package.shutil.copy2', side_effect=AssertionError('Partial reports must stay in place')):
                with self.assertRaisesRegex(ValueError, 'Synthetic failure after writing HTML'):
                    render_with_failure_receipt(
                        interrupted_render, output_dir=report_directory,
                        receipt={'folder': package, 'mode': 'offline', 'tenant_id': TENANT,
                                 'collected_at': '2026-09-14', 'evaluation_date': '2026-09-15'})
            receipt = json.loads((package / 'operator-log.jsonl').read_text(encoding='utf-8'))
            self.assertEqual(receipt['run_id'], '1')
            self.assertEqual(receipt['status'], 'failed')
            self.assertEqual(receipt['error'], 'Synthetic failure after writing HTML')
            self.assertEqual(receipt['deliverables'], ['Builds/1/assessment.html'])
            self.assertEqual(list((package / 'Builds').iterdir()), [report_directory])
            self.assertEqual(list(package.rglob('assessment.html')), [partial])
            self.assertEqual(partial.read_text(encoding='utf-8'), '<html>partial report</html>')
            self.assertEqual(canonical.read_bytes(), original)

    def test_json_folder_can_be_recovered_from_the_output_bundle(self):
        from Core.assessment_package import record_package_run

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            portable = root / 'portable'
            portable.mkdir()
            generated = root / 'json' / 'assessment'
            generated.mkdir(parents=True)
            index = generated / 'index.json'
            index.write_text('{"format":"m365-readiness-assessment-package"}', encoding='utf-8')
            upload = root / 'assessment_json.zip'
            with zipfile.ZipFile(upload, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(index, 'index.json')
            outputs = {'json_path': index, 'evidence_bundle': {
                'dashboard_json_folder': str(generated),
                'dashboard_json_archive_path': str(upload)}}
            original_outputs = json.dumps(outputs, default=str, sort_keys=True)
            with contextlib.redirect_stdout(io.StringIO()):
                retained = record_package_run(
                    portable, mode='offline', tenant_id=TENANT, collected_at=None,
                    evaluation_date='2026-09-15', outputs=outputs)
            self.assertEqual(len(retained), 2)
            self.assertTrue(any('/json/assessment/index.json' in path for path in retained))
            copied_archive = next(portable / path for path in retained if path.endswith('.zip'))
            self.assertEqual(copied_archive.read_bytes(), upload.read_bytes())
            self.assertEqual(json.dumps(outputs, default=str, sort_keys=True), original_outputs)

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
            self.assertTrue(args.include_user_usage_detail)

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
            recipe_path = next((root / 'Reports').glob('*/*/rebuild.json'))
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
                     patch('Core.export_recommendations.build_report_filename', return_value='same-second.json'), \
                     patch('Core.processor.print_recommendations_summary'), \
                     patch('Core.offline_collection.save_collection', side_effect=AssertionError('Offline saved collection')):
                    self.assertEqual(run_offline_report(self.parse('--prior-report', prior, '--purview-cache', cache,
                                                                   '--evaluation-date', '2026-09-15',
                                                                   '--extra-exports', 'dashboard-json')), 0)
                    first = render.call_args.kwargs['evidence_bundle']['assessment_result']
                    recipe = next((root / 'Reports').glob('*/*/rebuild.json'))
                    copied = root / 'copied_legacy_package'
                    shutil.copytree(recipe.parent, copied)
                    prior.unlink()
                    cache.unlink()
                    self.assertEqual(run_offline_report(self.parse('--collection-input', copied / 'rebuild.json',
                                                                   '--extra-exports', 'dashboard-json')), 0)
                    second = render.call_args.kwargs['evidence_bundle']['assessment_result']
            finally:
                os.chdir(previous)
            self.assertEqual(first, second)
            self.assertEqual(len(list(copied.glob('Builds/*/JSON/index.json'))), 2,
                             'The copied package must retain both JSON builds even when their filename timestamps coincide.')
            self.assertEqual(len(list((root / 'Reports').rglob('JSON/index.json'))), 1,
                             'The replay adds its build to the copied package instead of duplicating it in Reports.')
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
            # Run from another working directory; outputs stay in the portable
            # source package and will be retained by the copied customer package.
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
            self.assertEqual(len(list(moved.glob('Builds/*'))), 2,
                             'Replay must add one build while preserving the copied original deliverables.')
            self.assertTrue(list(moved.glob('Builds/*/*.xlsx')))
            self.assertTrue(list(moved.glob('Builds/*/*.html')))


if __name__ == '__main__':
    unittest.main()

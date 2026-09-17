"""Automatic PDF intake keeps original context portable without scoring OCR guesses."""

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import pymupdf

from Core.cli_parser import parse_arguments
from Core.offline_collection import CollectionPackagingError, empty_service_results, load_collection, save_collection
from Core.offline_report import run_offline_report
from Core.pdf_report_import import _highlights, prepare_pdf_review
from Core.portal_review import load_portal_review
from tests.test_portal_review import TENANT, reviewed_fixture


def pdf_fixture(path, *, blank=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as document:
        page = document.new_page()
        if not blank:
            page.insert_text((40, 60), 'Fictional Copilot overview\nActive users: 7\nLast updated: 2026-09-14')
        document.save(path)
    return path


class PdfImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(os.chdir, previous)
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def parse(self, *arguments):
        with patch('sys.argv', ['main.py', *map(str, arguments)]):
            return parse_arguments(None, [])

    def test_recursive_discovery_deduplicates_and_retains_unchanged_originals(self):
        source = pdf_fixture(self.root / 'reports' / 'Overview.pdf')
        duplicate = source.parent / 'nested' / source.name
        duplicate.parent.mkdir()
        shutil.copy2(source, duplicate)
        before = source.read_bytes()
        with patch('Core.pdf_report_import._ocr_image', side_effect=AssertionError('Text PDF should not need OCR')):
            manifest = prepare_pdf_review([source.parent], tenant_id=TENANT)
        review = load_portal_review(manifest, TENANT)
        self.assertEqual(len(review['captures']), 1)
        item = review['captures'][0]
        self.assertEqual(item['source_sha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(item['captured_at'], '')  # Import time is not the capture/report date.
        self.assertEqual(item['review_method'], 'automated')
        self.assertIn('Active users: 7', item['extracted_pages'][0]['text'])
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(duplicate.read_bytes(), before)
        with patch('Core.pdf_report_import._extract', side_effect=AssertionError('Identical input must not be reprocessed')):
            self.assertEqual(prepare_pdf_review([source.parent], manifest, tenant_id=TENANT), manifest)

    def test_image_pdf_uses_local_ocr_and_rendered_text_is_escaped(self):
        from Core.customer_report import _portal_captures
        source = pdf_fixture(self.root / 'reports' / 'Usage.pdf', blank=True)
        text = '=1+1\n<script>alert("fixture")</script>\nActive users\n100\nLicenses assigned\n8'
        with patch('Core.pdf_report_import._ocr_image', return_value=(text, '')) as ocr:
            manifest = prepare_pdf_review([source.parent], tenant_id=TENANT)
        ocr.assert_called_once()
        review = load_portal_review(manifest, TENANT)
        capture = review['captures'][0]
        self.assertEqual(capture['extracted_pages'][0]['method'], 'Windows OCR')
        self.assertNotIn('100', ' '.join(capture['review_notes']))
        html = _portal_captures({'portal_review': review}, 'adoption')
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertIn('Capture date unavailable', html)
        self.assertIn('Download original PDF', html)

        from Core.processor import process_and_print_all_information
        from Core.export_recommendations import export_to_excel
        from openpyxl import load_workbook
        with patch('Core.processor.export_tabular_reports', return_value=(None, None)), \
                patch('Core.processor.export_to_html', return_value='example.html'), \
                patch('Core.processor.print_recommendations_summary'):
            arguments = dict(**empty_service_results(), expected_tenant_id=TENANT)
            baseline = process_and_print_all_information(**arguments)
            included = process_and_print_all_information(**arguments, portal_review=manifest)
        self.assertEqual(baseline['evidence_bundle']['assessment_result'], included['evidence_bundle']['assessment_result'])
        bundle = included['evidence_bundle']
        filename = export_to_excel(bundle['recommendations'], filename=str(self.root / 'safe.xlsx'), evidence_bundle=bundle)
        workbook = load_workbook(filename, read_only=True, data_only=False)
        try:
            rows = list(workbook['PDF Extracted Text'])
            index = [cell.value for cell in rows[0]].index('Extracted text')
            self.assertEqual(rows[1][index].value, text)
            self.assertEqual(rows[1][index].data_type, 's')
        finally:
            workbook.close()

    def test_unavailable_ocr_preserves_preview_with_explicit_limitation(self):
        source = pdf_fixture(self.root / 'reports' / 'Health.pdf', blank=True)
        with patch('Core.pdf_report_import._ocr_image', return_value=('', 'Windows OCR unavailable.')):
            manifest = prepare_pdf_review([source.parent], tenant_id=TENANT)
        capture = load_portal_review(manifest, TENANT)['captures'][0]
        self.assertIn('No readable text', capture['summary'])
        self.assertIn('Page 1: Windows OCR unavailable.', capture['limitations'])
        self.assertEqual(len(capture['previews']), 1)

    def test_bad_pdf_is_skipped_without_losing_valid_documents(self):
        source = pdf_fixture(self.root / 'reports' / 'Overview.pdf')
        (source.parent / 'broken.pdf').write_bytes(b'not a PDF')
        manifest = prepare_pdf_review([source.parent], tenant_id=TENANT)
        review = load_portal_review(manifest, TENANT)
        self.assertEqual(len(review['captures']), 1)
        log = json.loads(Path(manifest).with_name('import-log.json').read_text())
        self.assertEqual(log['skipped'][0]['source_file'], 'broken.pdf')
        self.assertIn('PDF skipped: broken.pdf', self.output.getvalue())

    def test_manual_review_can_merge_with_automatic_captures(self):
        reviewed, _ = reviewed_fixture(self.root / 'reviewed')
        source = pdf_fixture(self.root / 'reports' / 'Health.pdf')
        manifest = prepare_pdf_review([source.parent], reviewed, tenant_id=TENANT)
        captures = load_portal_review(manifest, TENANT)['captures']
        self.assertEqual(len(captures), 2)
        self.assertEqual(captures[0].get('review_method', 'manual'), 'manual')
        self.assertEqual(captures[1]['review_method'], 'automated')

    def test_folder_compatibility_and_snapshot_original_protection(self):
        source = pdf_fixture(self.root / 'reports' / 'Overview.pdf')
        manifest = prepare_pdf_review([], source.parent, tenant_id=TENANT)
        self.assertEqual(len(load_portal_review(manifest)['captures']), 1)
        before = source.read_bytes()
        for flag in ('--portal-review', '--reports-dir'):
            args = self.parse('--mode', 'offline', flag, source.parent, '--snapshot-json', source)
            with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, 'cannot overwrite'):
                run_offline_report(args)
            self.assertEqual(source.read_bytes(), before)

    def test_recovered_raw_collection_replays_from_copied_package_without_ocr(self):
        source = pdf_fixture(self.root / 'reports' / 'Overview.pdf')
        raw = self.root / 'original.json'
        with patch('Core.assessment_package.package_inputs', side_effect=ValueError('Synthetic packaging failure')):
            with self.assertRaises(CollectionPackagingError):
                save_collection(raw, tenant_id=TENANT, tenant_name='Fictional tenant', service_results=empty_service_results())
        before = raw.read_bytes()
        with patch('Core.processor.export_tabular_reports', return_value=(None, None)), \
                patch('Core.processor.export_to_html', return_value='example.html') as render, \
                patch('Core.processor.print_recommendations_summary'), \
                patch('socket.create_connection', side_effect=AssertionError('Offline network')), \
                patch('subprocess.Popen', side_effect=AssertionError('Text PDF needs no process')):
            args = self.parse('--mode', 'offline', '--collection-input', raw, '--reports-dir', source.parent)
            self.assertEqual(run_offline_report(args), 0)
            original_result = render.call_args.kwargs['evidence_bundle']['assessment_result']
            package = next((self.root / 'output' / 'assessments').glob('*/rebuild.json')).parent
            moved = self.root / 'copied'
            shutil.copytree(package, moved)
            source.parent.rename(self.root / 'unavailable-reports')
            Path('output/portal-reviews').rename('output/unavailable-reviews')
            loaded = load_collection(moved / 'rebuild.json')
            self.assertEqual(loaded['tenant_id'], TENANT)
            self.assertEqual(Path(loaded['package_directory']), moved)
            with patch('Core.pdf_report_import._extract', side_effect=AssertionError('Replay repeated extraction')):
                replay = self.parse('--mode', 'offline', '--collection-input', moved / 'rebuild.json')
                self.assertEqual(run_offline_report(replay), 0)
                self.assertEqual(run_offline_report(replay), 0)
                self.assertEqual((moved / 'collection.json').read_bytes(), before)
                self.assertEqual(Path(load_collection(moved / 'rebuild.json')['package_directory']), moved)
            replayed = render.call_args.kwargs['evidence_bundle']
            self.assertEqual(original_result, replayed['assessment_result'])
            self.assertEqual(len(replayed['portal_review']['captures']), 1)
            self.assertTrue(replayed['portal_review']['captures'][0]['source_data_uri'])
        self.assertEqual(raw.read_bytes(), before)


class PdfLivePreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_manifest_is_rejected_before_authentication(self):
        from Core.orchestrator import orchestrate
        with contextlib.redirect_stdout(io.StringIO()) as output, \
                patch('Core.orchestrator.load_modules_and_analyze', new_callable=AsyncMock) as modules, \
                patch('Core.orchestrator.setup_graph_and_licenses', new_callable=AsyncMock) as auth:
            result = await orchestrate(TENANT, portal_review='missing-manifest.json')
        self.assertEqual(result, 1)
        modules.assert_not_awaited()
        auth.assert_not_awaited()
        self.assertIn('ASSESSMENT INPUT ERROR', output.getvalue())
        self.assertIn('--reports-dir', output.getvalue())


if __name__ == '__main__':
    unittest.main()

"""Only the reviewed 2.0.0 -> 2.1.0 transition can reuse saved evidence."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

from Core.assessment_package import save_rebuild_recipe
from Core.cli_parser import parse_arguments
from Core.export_recommendations import export_to_excel
from Core.offline_collection import collection_context, empty_service_results, load_collection, save_collection
from Core.offline_report import run_offline_report


TENANT = '11111111-1111-4111-8111-111111111111'
COLLECTED = '2026-09-10T12:00:00+00:00'
EVALUATED = '2026-09-15'


class MethodologyMigrationTests(unittest.TestCase):
    def parse(self, *arguments):
        with patch('sys.argv', ['main.py', *map(str, arguments)]):
            return parse_arguments(None, [])

    def saved(self, root):
        report = root / 'original.csv'
        report.write_text('Site URL,Anyone link count\nhttps://example.invalid,0\n', encoding='utf-8')
        results = empty_service_results()
        results['entra_info']['policies'] = [{'id': 'fictional-policy', 'state': 'enabled'}]
        results['entra_info']['recommendations'] = [{
            'Service': 'Entra', 'Feature': 'Fictional collected policy',
            'Observation': 'One fictional policy was enabled at collection time.',
            'Recommendation': 'Confirm its coverage of the approved pilot scope.',
            'Disposition': 'Confirmation', 'Priority': 'Medium',
            'ObservationDate': '2026-09-10',
        }]
        with patch('Core.cross_provider_assessment.METHODOLOGY_VERSION', '2.0.0'):
            source = Path(save_collection(root / 'collection-source.json', tenant_id=TENANT,
                                          tenant_name='Fictional tenant', service_results=results,
                                          collected_at=COLLECTED, evaluation_date=EVALUATED,
                                          supplemental_inputs={'sam_report': [report]}))
        folder = root / 'collection-source_package'
        return source, folder

    def test_collection_migration_preserves_bytes_dates_and_base_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            source, folder = self.saved(Path(directory))
            before = {path: path.read_bytes() for path in (source, folder / 'collection.json')}
            payload = load_collection(source)
            self.assertEqual(payload['methodology_version'], '2.0.0')
            self.assertEqual(payload['original_methodology_version'], '2.0.0')
            self.assertEqual(payload['effective_methodology_version'], '2.1.0')
            self.assertEqual(payload['collected_at'], COLLECTED)
            self.assertEqual(payload['evaluation_date'], EVALUATED)
            self.assertEqual(payload['service_results'], json.loads(before[source])['service_results'])
            context = collection_context(payload)
            self.assertEqual(context['collected_methodology_version'], '2.0.0')
            self.assertEqual(context['methodology_migration']['from'], '2.0.0')
            self.assertEqual(context['methodology_migration']['to'], '2.1.0')
            for path, contents in before.items():
                self.assertEqual(path.read_bytes(), contents)
            retained = Path(payload['resolved_inputs']['sam_report'][0])
            retained.write_text('altered evidence', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'has changed'):
                load_collection(source)

    def test_collection_and_collectionless_recipes_migrate_and_unknown_versions_fail(self):
        for has_collection in (True, False):
            with self.subTest(has_collection=has_collection), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                if has_collection:
                    _, folder = self.saved(root)
                else:
                    folder = root / 'recipe-only'
                    folder.mkdir()
                report = root / 'new.csv'
                report.write_text('Site URL,Anyone link count\nhttps://example.invalid,0\n', encoding='utf-8')
                args = self.parse('--mode', 'offline', '--sam-report', report, '--evaluation-date', EVALUATED)
                with patch('Core.cross_provider_assessment.METHODOLOGY_VERSION', '2.0.0'):
                    save_rebuild_recipe(folder, args, tenant_id=TENANT)
                recipe_path = folder / 'rebuild.json'
                before = recipe_path.read_bytes()
                payload = load_collection(recipe_path)
                self.assertEqual(payload['methodology_migration']['to'], '2.1.0')
                self.assertEqual(payload['evaluation_date'], EVALUATED)
                self.assertEqual(recipe_path.read_bytes(), before)
                retained = Path(payload['resolved_inputs']['sam_report'][0])
                retained_before = retained.read_bytes()
                retained.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'has changed'):
                    load_collection(recipe_path)
                retained.write_bytes(retained_before)
                for unknown in ('2.0.1', '2.2.0', '9.0.0'):
                    recipe = json.loads(before)
                    recipe['methodology_version'] = unknown
                    recipe_path.write_text(json.dumps(recipe), encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, 'matching tool version'):
                        load_collection(recipe_path)
                    if has_collection:
                        with self.assertRaisesRegex(ValueError, 'matching tool version'):
                            load_collection(folder / 'collection.json')

    def test_offline_migration_reports_current_methodology_and_records_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, folder = self.saved(root)
            before = (folder / 'collection.json').read_bytes()
            console = io.StringIO()
            with contextlib.redirect_stdout(console), \
                 patch('Core.processor.export_tabular_reports', return_value=(None, None)), \
                 patch('Core.processor.export_to_html', return_value='example.html') as render, \
                 patch('Core.processor.print_recommendations_summary'), \
                 patch('socket.create_connection', side_effect=AssertionError('Unexpected network')), \
                 patch('subprocess.Popen', side_effect=AssertionError('Unexpected process')), \
                 patch('builtins.input', side_effect=AssertionError('Unexpected prompt')), \
                 patch('Core.offline_collection.save_collection', side_effect=AssertionError('Unexpected collection save')):
                self.assertEqual(run_offline_report(self.parse('--collection-input', source)), 0)
            bundle = render.call_args.kwargs['evidence_bundle']
            self.assertEqual(bundle['assessment_result']['methodology_version'], '2.1.0')
            self.assertEqual(bundle['assessment_result']['evaluation_date'], EVALUATED)
            self.assertIn('Methodology migration 2.0.0 -> 2.1.0', console.getvalue())
            self.assertEqual((folder / 'collection.json').read_bytes(), before)
            receipt = json.loads((folder / 'operator-log.jsonl').read_text(encoding='utf-8').splitlines()[-1])
            self.assertEqual(receipt['methodology_migration']['from'], '2.0.0')
            recipe = json.loads((folder / 'rebuild.json').read_text(encoding='utf-8'))
            self.assertEqual(recipe['methodology_version'], '2.1.0')
            self.assertEqual(recipe['original_methodology_version'], '2.0.0')
            self.assertEqual(load_collection(folder / 'rebuild.json')['methodology_migration']['from'], '2.0.0')
            workbook_path = export_to_excel(bundle['recommendations'], filename=str(root / 'migration.xlsx'), evidence_bundle=bundle)
            workbook = load_workbook(workbook_path, read_only=True)
            try:
                self.assertIn('2.0.0 -> 2.1.0', str(list(workbook['Collection Coverage'].values)))
            finally:
                workbook.close()


if __name__ == '__main__':
    unittest.main()

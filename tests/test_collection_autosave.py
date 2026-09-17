"""Live orchestration persists replayable evidence without an operator save flag."""

import contextlib
import asyncio
import io
import os
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from Core.offline_collection import empty_service_results, load_collection
from Core.orchestrator import orchestrate
from Core.console_reporting import display_path


class CollectionAutosaveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_cwd = Path.cwd()
        os.chdir(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(os.chdir, self.original_cwd)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.output = io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.output))
        self.results = empty_service_results()
        self.results['m365_result'] = [{'licenses': [], 'client_secret': 'DO_NOT_SAVE'}, [{
            'Service': 'M365', 'Feature': 'Collected evidence', 'Observation': 'Example tenant evidence.',
            'Recommendation': '', 'Status': 'Reference',
        }]]
        service_keys = {'m365': 'm365_result', 'entra': 'entra_info', 'purview': 'purview_info',
                        'defender': 'defender_info', 'power_platform': 'power_platform_info',
                        'copilot_studio': 'copilot_studio_info'}
        pipelines = {name: AsyncMock(return_value=self.results[key]) for name, key in service_keys.items()}
        values = {
            'Core.orchestrator.load_modules_and_analyze': AsyncMock(),
            'Core.orchestrator.setup_graph_and_licenses': AsyncMock(return_value=(object(), {}, True)),
            'Core.orchestrator.resolve_tenant_name': AsyncMock(return_value='Example customer'),
            'Core.orchestrator.resolve_sharepoint_admin_url': AsyncMock(return_value='https://example-admin.sharepoint.com'),
            'Core.connection_validation.run_connection_checks': AsyncMock(return_value=[]),
        }
        for target, replacement in values.items():
            self.stack.enter_context(patch(target, new=replacement))
        self.stack.enter_context(patch('Core.orchestrator.prepare_interactive_collection_plan', return_value={
            'power_platform': {'will_attempt': False}, 'sharepoint': {'will_attempt': False},
        }))
        self.stack.enter_context(patch('Core.orchestrator.print_interactive_collection_summary'))
        self.stack.enter_context(patch('Core.connection_validation.print_connection_results'))
        self.stack.enter_context(patch('Core.connection_validation.connection_exit_code', return_value=0))
        self.stack.enter_context(patch('Core.orchestrator.create_pipelines', return_value=pipelines))
        self.processor = self.stack.enter_context(patch('Core.orchestrator.process_and_print_all_information'))
        # The event loop is already running when the test calls orchestration.
        # Collector boundaries are mocked; any unexpected network/process/prompt is a failure.
        for target in ('socket.socket.connect', 'socket.create_connection', 'subprocess.Popen', 'builtins.input'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('Unexpected live operation')))

    async def run_assessment(self, **options):
        return await orchestrate('11111111-1111-1111-1111-111111111111', ['M365'], **options)

    async def test_default_saves_distinct_replayable_collections_and_prints_paths(self):
        await self.run_assessment()
        first = next(Path('output/collections').glob('*.json'))
        original = first.read_text(encoding='utf-8')
        await self.run_assessment()
        files = list(Path('output/collections').glob('*.json'))
        self.assertEqual(len(files), 2)
        self.assertEqual(first.read_text(encoding='utf-8'), original)
        self.assertNotIn('DO_NOT_SAVE', original)
        for path in files:
            self.assertTrue(path.name.startswith('tenant-collection_example_customer_'))
            loaded = load_collection(path)
            self.assertEqual(loaded['tenant_name'], 'Example customer')
            self.assertEqual(loaded['service_results']['m365_result'][1][0]['Feature'], 'Collected evidence')
            self.assertIn(display_path(path), self.output.getvalue())
        self.assertEqual(self.output.getvalue().count('COLLECTION INPUT (--collection-input)'), 2)
        self.assertIn('REBUILD OFFLINE', self.output.getvalue())
        self.assertIn('--mode offline `', self.output.getvalue())

    async def test_explicit_destination_overrides_automatic_location(self):
        path = Path('custom evidence') / "customer's collection.json"
        await self.run_assessment(save_collection_path=str(path))
        self.assertTrue(path.is_file())
        self.assertFalse(Path('output/collections').exists())
        self.assertEqual(load_collection(path)['tenant_name'], 'Example customer')
        self.assertIn("customer''s collection.json'", self.output.getvalue())

    async def test_connection_check_does_not_create_or_overwrite_collection(self):
        path = Path('existing.json')
        path.write_text('retain existing evidence', encoding='utf-8')
        result = await self.run_assessment(check_connections=True, save_collection_path=str(path))
        self.assertEqual(result, 0)
        self.assertEqual(path.read_text(encoding='utf-8'), 'retain existing evidence')
        self.assertFalse(Path('output/collections').exists())
        self.processor.assert_not_called()
        self.assertNotIn('COLLECTION INPUT', self.output.getvalue())

    async def test_checkpoint_exists_before_sharepoint_and_survives_interruption(self):
        async def interrupted_sharepoint(*args, **kwargs):
            path = next(Path('output/collections').glob('*.json'))
            saved = load_collection(path)
            self.assertEqual(saved['collection_progress']['status'], 'in_progress')
            self.assertEqual(saved['collection_progress']['services']['m365']['status'], 'pending')
            self.assertTrue(Path(saved['package_directory']).is_dir())
            raise asyncio.CancelledError()
        with patch('Core.orchestrator.prepare_interactive_collection_plan', return_value={
            'power_platform': {'will_attempt': False}, 'sharepoint': {'selected': True, 'will_attempt': True},
        }), patch('Core.orchestrator.collect_sharepoint_with_retry', side_effect=interrupted_sharepoint):
            with self.assertRaises(asyncio.CancelledError):
                await self.run_assessment()
        saved = load_collection(next(Path('output/collections').glob('*.json')))
        self.assertEqual(saved['collection_progress']['status'], 'interrupted')
        self.assertEqual(saved['service_results']['m365_result'][1][0]['Status'], 'Not Assessed')
        self.processor.assert_not_called()

    async def test_collection_survives_report_generation_failure(self):
        self.processor.side_effect = ValueError('Synthetic report rendering failure')
        result = await self.run_assessment()
        self.assertEqual(result, 1)
        path = next(Path('output/collections').glob('*.json'))
        self.assertEqual(load_collection(path)['tenant_name'], 'Example customer')
        self.assertIn(display_path(path), self.output.getvalue())

    async def test_failed_save_is_reported_without_claiming_success(self):
        with patch('Core.offline_collection.save_collection', side_effect=OSError('Synthetic disk error')):
            self.assertEqual(await self.run_assessment(), 1)
        self.assertNotIn('Saved tenant collection:', self.output.getvalue())
        self.assertNotIn('COLLECTION INPUT', self.output.getvalue())
        self.processor.assert_not_called()

    async def test_packaging_failure_prints_saved_collection_for_recovery(self):
        with patch('Core.assessment_package.package_inputs', side_effect=ValueError('Synthetic missing attachment')):
            self.assertEqual(await self.run_assessment(), 1)
        path = next(Path('output/collections').glob('*.json'))
        self.assertEqual(load_collection(path)['tenant_name'], 'Example customer')
        self.assertIn('PACKAGING ERROR', self.output.getvalue())
        self.assertIn(display_path(path), self.output.getvalue())
        self.assertIn('--mode offline', self.output.getvalue())
        self.processor.assert_not_called()

    async def test_live_and_replay_reject_one_foreign_tenant_export(self):
        from Core.processor import process_and_print_all_information
        from Core.cli_parser import parse_arguments
        from Core.offline_report import run_offline_report
        report = Path('foreign.csv')
        report.write_text('Tenant ID,Anyone link count\n22222222-2222-2222-2222-222222222222,1\n', encoding='utf-8')
        self.processor.side_effect = process_and_print_all_information
        self.assertEqual(await self.run_assessment(sam_report_paths=[str(report)]), 1)
        self.assertIn('does not match', self.output.getvalue())
        collection = next(Path('output/collections').glob('*.json'))
        with patch('sys.argv', ['main.py', '--collection-input', str(collection)]):
            args = parse_arguments(None, [])
        with self.assertRaisesRegex(ValueError, 'does not match'):
            run_offline_report(args)

    async def test_live_and_moved_package_replay_have_identical_assessment(self):
        from Core.processor import process_and_print_all_information
        from Core.cli_parser import parse_arguments
        from Core.offline_report import run_offline_report
        self.processor.side_effect = process_and_print_all_information
        with patch('Core.processor.export_tabular_reports', return_value=(None, None)), \
             patch('Core.processor.export_to_html', return_value='example.html') as render, \
             patch('Core.processor.print_recommendations_summary'):
            await self.run_assessment(evaluation_date='2026-09-15')
            live = render.call_args.kwargs['evidence_bundle']['assessment_result']
            collection = next(Path('output/collections').glob('*.json'))
            package = Path(load_collection(collection)['package_directory'])
            moved = Path('copied assessment').resolve()
            shutil.copytree(package, moved)
            with patch('sys.argv', ['main.py', '--collection-input', str(moved / 'collection.json')]):
                args = parse_arguments(None, [])
            self.assertEqual(run_offline_report(args), 0)
            offline = render.call_args.kwargs['evidence_bundle']['assessment_result']
        self.assertEqual(live, offline)


if __name__ == '__main__':
    unittest.main()

"""Interrupted runs retain portable evidence without inventing successful reads."""

import asyncio
import contextlib
import io
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from Core.collection_checkpoint import CollectionCheckpoint, SERVICE_PIPELINES, run_checkpointed_pipelines
from Core.offline_collection import collection_context, empty_service_results, load_collection


TENANT = '11111111-1111-1111-1111-111111111111'


class CollectionCheckpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        original = Path.cwd()
        os.chdir(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(os.chdir, original)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.output = io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.output))
        for target in ('socket.socket.connect', 'socket.create_connection', 'subprocess.Popen', 'builtins.input'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('Unexpected live operation')))

    def checkpoint(self, selected=('m365', 'entra', 'purview', 'defender'), **options):
        return CollectionCheckpoint(
            service_config={'run_' + name: name in selected for name in SERVICE_PIPELINES},
            tenant_id=TENANT, tenant_name='Synthetic tenant',
            assessment_settings={'permission_profile': options.pop('profile', 'standard')}, **options)

    def pipelines(self):
        results = empty_service_results()
        results['m365_result'] = [{'licenses': [], 'client_secret': 'DO_NOT_SAVE'}, [{
            'Service': 'M365', 'Feature': 'Synthetic finished evidence', 'Observation': 'A completed source.',
            'Status': 'Reference', 'Recommendation': '',
        }]]
        return {name: AsyncMock(return_value=results[key]) for name, (key, _) in SERVICE_PIPELINES.items()}

    async def test_completed_services_are_saved_before_slow_pipeline_then_cancellation_drains_tasks(self):
        checkpoint = self.checkpoint()
        pipelines = self.pipelines()
        waiting, cancelled = asyncio.Event(), asyncio.Event()

        async def slow_purview():
            waiting.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def defender(purview_task):
            return await purview_task

        pipelines.update(purview=slow_purview, defender=defender)
        run = asyncio.create_task(run_checkpointed_pipelines(pipelines, checkpoint))
        await waiting.wait()
        await asyncio.sleep(0)
        partial = load_collection(checkpoint.path)
        self.assertEqual(partial['collection_progress']['status'], 'in_progress')
        self.assertEqual(partial['collection_progress']['services']['m365']['status'], 'completed')
        self.assertEqual(partial['collection_progress']['services']['purview']['status'], 'pending')
        self.assertEqual(partial['service_results']['purview_info']['recommendations'][0]['Status'], 'Not Assessed')
        self.assertNotIn('DO_NOT_SAVE', Path(checkpoint.path).read_text(encoding='utf-8'))
        run.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await run
        self.assertTrue(cancelled.is_set())
        interrupted = load_collection(checkpoint.path)
        self.assertEqual(interrupted['collection_progress']['status'], 'interrupted')
        self.assertEqual(interrupted['collection_progress']['services']['defender']['status'], 'interrupted')
        self.assertEqual(interrupted['collected_at'], partial['collected_at'])
        self.assertEqual(interrupted['service_results']['m365_result'], partial['service_results']['m365_result'])
        self.assertIn('--collection-input', self.output.getvalue())

    async def test_success_reuses_one_package_and_defender_receives_purview_result(self):
        from Core.assessment_package import package_inputs
        report = Path('supplemental.csv')
        report.write_text('Site URL,Anyone link count\nhttps://example.invalid/sites/a,2\n', encoding='utf-8')
        checkpoint = self.checkpoint(supplemental_inputs={'sam_report': [report]})
        pipelines = self.pipelines()
        purview = {'available': True, 'recommendations': [], 'marker': 'purview evidence'}
        pipelines['purview'] = AsyncMock(return_value=purview)

        async def defender(purview_task):
            self.assertIs(await purview_task, purview)
            saved = load_collection(checkpoint.path)
            self.assertEqual(saved['collection_progress']['services']['purview']['status'], 'completed')
            return {'available': True, 'recommendations': []}

        pipelines['defender'] = defender
        with patch('Core.assessment_package.package_inputs', wraps=package_inputs) as package:
            path = await run_checkpointed_pipelines(pipelines, checkpoint)
        self.assertEqual(package.call_count, 1)
        self.assertEqual(len(list(Path('output/collections').glob('*.json'))), 1)
        self.assertEqual(len(list(Path('output/collections').glob('*_package*'))), 1)
        saved = load_collection(path)
        self.assertEqual(saved['collection_progress']['status'], 'completed')
        self.assertTrue(all(state['status'] in {'completed', 'not_selected'}
                            for state in saved['collection_progress']['services'].values()))
        self.assertEqual(saved['service_results']['m365_result'][1][0]['Feature'], 'Synthetic finished evidence')
        self.assertEqual(saved['tenant_id'], TENANT)
        self.assertEqual(collection_context(saved)['permission_profile'], 'standard')

    async def test_unexpected_pipeline_error_preserves_completed_evidence_and_cancels_dependants(self):
        checkpoint = self.checkpoint()
        pipelines = self.pipelines()

        async def failure():
            await asyncio.sleep(0)
            raise ValueError('Synthetic collector failure')

        pipelines['purview'] = failure
        with self.assertRaisesRegex(ValueError, 'Synthetic collector failure'):
            await run_checkpointed_pipelines(pipelines, checkpoint)
        saved = load_collection(checkpoint.path)
        self.assertEqual(saved['collection_progress']['status'], 'failed')
        self.assertEqual(saved['collection_progress']['services']['m365']['status'], 'completed')
        self.assertEqual(saved['collection_progress']['services']['purview']['status'], 'failed')

    async def test_failed_atomic_replace_retains_previous_valid_checkpoint(self):
        checkpoint = self.checkpoint()
        checkpoint.save()
        original = Path(checkpoint.path).read_bytes()
        with patch.object(Path, 'replace', side_effect=OSError('Synthetic full disk')):
            with self.assertRaisesRegex(OSError, 'Synthetic full disk'):
                checkpoint.record('m365', [{'licenses': []}, []])
        self.assertEqual(Path(checkpoint.path).read_bytes(), original)
        self.assertEqual(load_collection(checkpoint.path)['collection_progress']['services']['m365']['status'], 'pending')
        self.assertFalse(list(Path('output/collections').rglob('*.tmp')))

    async def test_restricted_and_unselected_services_are_exclusions_not_interruption_gaps(self):
        checkpoint = self.checkpoint(selected=('m365', 'purview'), profile='restricted')
        checkpoint.save()
        checkpoint.finish('interrupted')
        saved = load_collection(checkpoint.path)
        purview = saved['service_results']['purview_info']
        self.assertEqual(purview['availability_status'], 'not_requested')
        self.assertEqual(purview['recommendations'], [])
        self.assertIn('Restricted permission profile', purview['reason'])
        self.assertEqual(saved['collection_progress']['services']['entra']['status'], 'not_selected')

    async def test_interrupted_package_moves_and_replays_html_workbook_with_explicit_gaps(self):
        from Core.cli_parser import parse_arguments
        from Core.offline_report import run_offline_report
        import openpyxl
        checkpoint = self.checkpoint()
        checkpoint.save()
        checkpoint.record_sharepoint({'available': False, 'reason': 'Synthetic unavailable SharePoint source.'})
        checkpoint.finish('interrupted')
        package = Path(load_collection(checkpoint.path)['package_directory'])
        moved = Path('copied package')
        shutil.copytree(package, moved)
        canonical = moved / 'collection.json'
        original = canonical.read_bytes()
        with patch('sys.argv', ['main.py', '--collection-input', str(canonical)]):
            args = parse_arguments(None, [])
        self.assertEqual(run_offline_report(args), 0)
        self.assertEqual(canonical.read_bytes(), original)
        html = next(Path('Reports').glob('*.html')).read_text(encoding='utf-8')
        self.assertIn('Collection did not finish', html)
        self.assertIn('not assessed', html.lower())
        self.assertNotIn('No active incidents were returned by the completed', html)
        workbook = openpyxl.load_workbook(next(Path('Reports').glob('*.xlsx')), read_only=True)
        try:
            cells = '\n'.join(str(cell) for sheet in workbook for row in sheet.values for cell in row if cell is not None)
            self.assertIn('Unfinished service pipelines', cells)
            self.assertIn('interrupted', cells)
            self.assertIn('Collection did not finish', cells)
            self.assertIn('Not Assessed', cells)
        finally:
            workbook.close()

    async def test_generated_sharepoint_exports_are_added_without_recopying_original_inputs(self):
        checkpoint = self.checkpoint()
        checkpoint.save()
        export = Path('collected-sam.csv')
        export.write_text('Site URL,Anyone link count\nhttps://example.invalid/sites/a,1\n', encoding='utf-8')
        checkpoint.add_supplemental_inputs({'sam_report': [str(export)]})
        checkpoint.record_sharepoint({'available': False, 'reason': 'Only a report was obtained.',
                                     'exported_files': [{'path': str(export)}]})
        saved = load_collection(checkpoint.path)
        packaged = Path(saved['resolved_inputs']['sam_report'][0])
        self.assertEqual(packaged.read_bytes(), export.read_bytes())
        self.assertIn('collected_', saved['service_results']['m365_result'][0]['_client'].sharepoint_governance['exported_files'][0]['path'])
        self.assertEqual(len(list(Path('output/collections').glob('*_package*'))), 1)


if __name__ == '__main__':
    unittest.main()

"""Live snapshot validation must preserve original reviewed portal evidence."""

import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from Core.orchestrator import orchestrate
from tests.test_portal_review import TENANT, reviewed_fixture


class LiveSnapshotGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_manifest_pdf_and_preview_collisions_stop_before_authentication_or_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest, _ = reviewed_fixture(directory)
            inputs = [manifest, manifest.parent / 'originals' / 'invented.pdf',
                      manifest.parent / 'images' / 'invented.png']
            originals = {path: path.read_bytes() for path in inputs}
            for target in inputs:
                with self.subTest(target=target.name), contextlib.redirect_stdout(io.StringIO()) as output, \
                        patch('Core.orchestrator.load_modules_and_analyze', new_callable=AsyncMock) as modules, \
                        patch('Core.orchestrator.setup_graph_and_licenses', new_callable=AsyncMock) as graph, \
                        patch('Core.offline_collection.save_collection') as save:
                    result = await orchestrate(TENANT, portal_review=str(manifest), snapshot_json=str(target))
                    self.assertEqual(result, 1)
                    self.assertIn('Snapshot output cannot overwrite', output.getvalue())
                    modules.assert_not_awaited()
                    graph.assert_not_awaited()
                    save.assert_not_called()
                    self.assertEqual({path: path.read_bytes() for path in inputs}, originals)

    async def test_separate_snapshot_path_continues_to_service_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest, _ = reviewed_fixture(directory)
            with patch('Core.orchestrator.validate_and_prepare_services', return_value=None) as validate, \
                    patch('Core.orchestrator.console.status') as status:
                result = await orchestrate(TENANT, portal_review=str(manifest),
                                           snapshot_json=str(Path(directory) / 'assessment.json'))
            self.assertEqual(result, 1)  # Mocked service validation deliberately stops the run.
            validate.assert_called_once_with(None)
            status.assert_not_called()


if __name__ == '__main__':
    unittest.main()

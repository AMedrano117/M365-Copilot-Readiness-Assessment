from provenance_fixture import fictional_publication
"""Packaged inputs are grouped in one descriptive folder per evidence area.

Expected layout (declared before the implementation):
  inputs/SharePoint Advanced Management/<original name>   initial package inputs
  Collected/SharePoint Advanced Management/<original name> reports downloaded during a live run
  Rebuilds/<n>/inputs/<area>/<original name>               inputs added by an offline rebuild
Original names are kept; a second file with the same name receives " (2)".
Older packages keep their recorded numbered paths. All values are fictional.
"""

import contextlib
import io
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Core import assessment_package
from Core.assessment_package import INPUT_KEYS, package_inputs
from Core.collection_checkpoint import CollectionCheckpoint, SERVICE_PIPELINES
from Core.offline_collection import empty_service_results, load_collection, save_collection

TENANT = '11111111-1111-1111-1111-111111111111'
SAM = 'SharePoint Advanced Management'
PERMISSIONS = 'Site URL,Anyone link count\nhttps://fictional.example/sites/a,1\n'
ACTIVITY = 'Site URL,Links created\nhttps://fictional.example/sites/a,2\n'


def write(folder, name, text):
    path = Path(folder) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


@fictional_publication
class PackageInputFolderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_every_input_role_has_a_safe_descriptive_folder(self):
        folders = assessment_package.INPUT_FOLDERS
        self.assertEqual(set(folders), INPUT_KEYS)
        self.assertEqual(folders['sam_report'], SAM)
        self.assertEqual(len(set(folders.values())), len(folders))
        for name in folders.values():
            self.assertFalse(re.search(r'[<>:"/\\|?*]|^\d+$', name), name)

    def test_all_sam_reports_share_one_named_folder_with_original_names(self):
        snapshot = write(self.root / 'downloads' / 'one', 'Site_permissions_SharePoint.csv', PERMISSIONS)
        activity = write(self.root / 'downloads' / 'two', 'Anyone_links.csv', ACTIVITY)
        manifest, references = package_inputs(self.root / 'package', {'sam_report': [snapshot, activity]})
        self.assertEqual(manifest['inputs']['sam_report'],
                         [f'inputs/{SAM}/Site_permissions_SharePoint.csv', f'inputs/{SAM}/Anyone_links.csv'])
        self.assertEqual(sorted(path.name for path in (self.root / 'package' / 'inputs' / SAM).iterdir()),
                         ['Anyone_links.csv', 'Site_permissions_SharePoint.csv'])
        self.assertEqual([path.name for path in (self.root / 'package' / 'inputs').iterdir()], [SAM])
        self.assertEqual(references[str(activity)], f'inputs/{SAM}/Anyone_links.csv')

    def test_each_area_gets_its_own_folder(self):
        sam = write(self.root, 'permissions.csv', PERMISSIONS)
        dspm = write(self.root, 'risk.csv', 'Site URL,Potentially overshared items\nhttps://fictional.example/sites/a,1\n')
        manifest, _ = package_inputs(self.root / 'package', {'sam_report': [sam], 'dspm_report': [dspm]})
        self.assertEqual(manifest['inputs']['sam_report'], [f'inputs/{SAM}/permissions.csv'])
        self.assertEqual(manifest['inputs']['dspm_report'],
                         [f"inputs/{assessment_package.INPUT_FOLDERS['dspm_report']}/risk.csv"])

    def test_same_name_from_different_reports_keeps_both_files(self):
        first = write(self.root / 'report-a', 'Report.csv', PERMISSIONS)
        second = write(self.root / 'report-b', 'Report.csv', ACTIVITY)
        manifest, _ = package_inputs(self.root / 'package', {'sam_report': [first, second]})
        self.assertEqual(manifest['inputs']['sam_report'], [f'inputs/{SAM}/Report.csv', f'inputs/{SAM}/Report (2).csv'])
        hashes = {record['path']: record['sha256'] for record in manifest['files']}
        self.assertEqual(len(set(hashes.values())), 2)
        self.assertEqual((self.root / 'package' / 'inputs' / SAM / 'Report (2).csv').read_text(encoding='utf-8'), ACTIVITY)
        self.assertEqual({record['source_file'] for record in manifest['files']}, {'Report.csv'})

    def test_report_folder_keeps_its_name_inside_the_area_folder(self):
        folder = self.root / 'October exports'
        write(folder, 'permissions.csv', PERMISSIONS)
        manifest, _ = package_inputs(self.root / 'package', {'reports_dir': [folder]})
        area = assessment_package.INPUT_FOLDERS['reports_dir']
        self.assertEqual(manifest['inputs']['reports_dir'], [f'inputs/{area}/October exports'])
        self.assertEqual(manifest['files'][0]['path'], f'inputs/{area}/October exports/permissions.csv')

    def test_saved_package_with_named_folders_reloads_after_moving(self):
        original = Path.cwd()
        self.addCleanup(os.chdir, original)
        os.chdir(self.root)
        export = write(self.root, 'permissions.csv', PERMISSIONS)
        collection = Path(save_collection(tenant_id=TENANT, tenant_name='Fictional tenant',
                                          service_results=empty_service_results(),
                                          supplemental_inputs={'sam_report': [export]}))
        moved = self.root / 'moved'
        import shutil
        shutil.copytree(collection.parent, moved)
        export.unlink()
        payload = load_collection(moved / 'collection.json')
        self.assertEqual(Path(payload['resolved_inputs']['sam_report'][0]),
                         (moved / 'inputs' / SAM / 'permissions.csv').resolve())

    def test_older_numbered_layout_still_validates(self):
        package = self.root / 'legacy'
        write(package / 'inputs' / '1', 'permissions.csv', PERMISSIONS)
        digest = assessment_package._digest(package / 'inputs' / '1' / 'permissions.csv')
        manifest = {'version': assessment_package.PACKAGE_VERSION, 'inputs': {'sam_report': ['inputs/1/permissions.csv']},
                    'files': [{'role': 'sam_report', 'source_file': 'permissions.csv', 'path': 'inputs/1/permissions.csv',
                               'sha256': digest, 'size_bytes': 0}], 'diagnostics': []}
        assessment_package._validate_inputs(package, manifest)


@fictional_publication
class CollectedFolderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        original = Path.cwd()
        os.chdir(temporary.name)
        self.addCleanup(temporary.cleanup)
        self.addCleanup(os.chdir, original)
        self.root = Path(temporary.name).resolve()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        for target in ('socket.socket.connect', 'socket.create_connection', 'subprocess.Popen', 'builtins.input'):
            stack.enter_context(patch(target, side_effect=AssertionError('Unexpected live operation')))

    def checkpoint(self):
        checkpoint = CollectionCheckpoint(service_config={'run_' + name: name == 'm365' for name in SERVICE_PIPELINES},
                                          tenant_id=TENANT, tenant_name='Fictional tenant',
                                          assessment_settings={'permission_profile': 'standard'})
        checkpoint.save()
        return checkpoint

    def test_downloaded_sam_reports_are_consolidated_in_one_named_folder(self):
        checkpoint = self.checkpoint()
        downloads = [write(self.root / 'cache' / f'report-{index}', name, text) for index, (name, text) in enumerate((
            ('Site_permissions_SharePoint.csv', PERMISSIONS), ('Anyone_links.csv', ACTIVITY), ('Anyone_links.csv', ACTIVITY + 'x\n')))]
        checkpoint.add_supplemental_inputs({'sam_report': [str(path) for path in downloads]})
        checkpoint.record_sharepoint({'available': False, 'reason': 'Reports only.',
                                      'exported_files': [{'path': str(path)} for path in downloads]})
        package = Path(checkpoint.path).parent
        collected = package / 'Collected'
        self.assertEqual([path.name for path in collected.iterdir()], [SAM])
        self.assertEqual(sorted(path.name for path in (collected / SAM).iterdir()),
                         ['Anyone_links (2).csv', 'Anyone_links.csv', 'Site_permissions_SharePoint.csv'])
        saved = load_collection(checkpoint.path)
        packaged = [Path(path) for path in saved['resolved_inputs']['sam_report']]
        self.assertEqual([path.read_bytes() for path in packaged], [path.read_bytes() for path in downloads])
        exported = saved['service_results']['m365_result'][0]['_client'].sharepoint_governance['exported_files']
        self.assertTrue(all(item['path'].startswith(f'Collected/{SAM}/') for item in exported), exported)
        self.assertFalse(any(part.isdigit() for path in collected.rglob('*') for part in path.relative_to(collected).parts))


if __name__ == '__main__':
    unittest.main()

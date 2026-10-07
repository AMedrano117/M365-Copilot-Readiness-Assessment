"""App Builder files: byte limits, lossless splitting, linked findings and visible gaps."""

import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from Core.app_builder_export import read_csv_parts, rebuild_oversized, write_app_builder_export
from Core.finding_evidence import rows_for_finding
from tests.test_finding_evidence import LOGS, build, event, legacy_finding, source


def configuration_finding():
    return {'RecommendationId': 'M365-009', 'Service': 'M365', 'Feature': 'SharePoint legacy authentication',
            'Disposition': 'Action', 'Priority': 'Medium', 'FindingKey': 'sharepoint.authentication.legacy_permitted',
            'EvidenceKey': 'sharepoint_governance_detail', 'Observation': 'SharePoint permits legacy authentication protocols.',
            'InvestigationEvidence': {'kind': 'configuration', 'reason': 'Tenant setting',
                                      'records': [{'Setting': 'LegacyAuthProtocolsEnabled', 'Value': True}]}}


def gap_finding():
    return {'RecommendationId': 'GAP-IDENTITY-MFA', 'Service': 'Entra', 'Disposition': 'Coverage', 'Priority': 'High',
            'ControlId': 'IDENTITY.MFA', 'Observation': 'Registration details were not collected.'}


class AppBuilderExportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def export(self, rows, sources, **options):
        payload, model = build(rows, sources)
        folder = Path(self.directory.name) / f'export-{len(list(Path(self.directory.name).iterdir()))}'
        return payload, model, folder, write_app_builder_export(model, folder, **options)

    def manifest(self, folder):
        manifest = json.loads((folder / '03-manifest.json').read_text(encoding='utf-8'))
        entries = manifest['files'] or [entry for name in manifest['file_list_parts']
                                        for entry in json.loads((folder / name).read_text(encoding='utf-8'))['files']]
        return manifest, entries

    def test_every_file_is_below_the_limit_and_the_manifest_matches_disk(self):
        logs = [event(number, code=[0, 53003, 50126][number % 3]) for number in range(1, 300)]
        _, _, folder, result = self.export([legacy_finding(), configuration_finding(), gap_finding()],
                                           {'signin_logs': [{'records': logs, 'source': source()}]})
        manifest, entries = self.manifest(folder)
        self.assertEqual({entry['file'] for entry in entries} | {'03-manifest.json'}, {path.name for path in folder.iterdir()})
        for entry in entries:
            data = (folder / entry['file']).read_bytes()
            self.assertLess(len(data), 1_000_000)
            self.assertEqual((len(data), hashlib.sha256(data).hexdigest()), (entry['bytes'], entry['sha256']))
        self.assertIn('legacy-authentication', manifest['upload_sets'])
        required = manifest['upload_sets']['legacy-authentication']['required']
        self.assertIn('01-overview.json', required)
        self.assertTrue(all(name in required for name in result['finding_files']['ENT-018']['required']))
        guide = (folder / '00-upload-guide.md').read_text(encoding='utf-8')
        self.assertIn('Uploading a summary alone does not make its records available', guide)
        self.assertIn('-p01', guide)
        overview = json.loads((folder / '01-overview.json').read_text(encoding='utf-8'))
        self.assertTrue(any('does not give the app any detailed records' in note for note in overview['important']))

    def test_forced_splitting_preserves_every_row_once_in_order(self):
        logs = [event(number, code=[0, 53003, 50126, None][number % 4]) for number in range(1, 120)]
        _, model, folder, result = self.export([legacy_finding()], {'signin_logs': [{'records': logs, 'source': source()}]},
                                               target_bytes=3_000, expanded_target=6_000, hard_limit=60_000, max_parts=200)
        parts = result['finding_files']['ENT-018']['required']
        self.assertGreater(len(parts), 5)
        exported = read_csv_parts(folder, parts)
        expected = rows_for_finding(model, 'ENT-018')
        self.assertEqual([row['detail_record_id'] for row in exported], [row['detail_record_id'] for row in expected])
        self.assertEqual(len({row['detail_record_id'] for row in exported}), len(exported))
        self.assertEqual({row['outcome'] for row in exported}, {'Succeeded', 'Blocked', 'Failed', 'Unknown'})

    def test_large_series_use_larger_parts_instead_of_many_files(self):
        logs = [event(number) for number in range(1, 120)]
        _, _, folder, result = self.export([legacy_finding()], {'signin_logs': [{'records': logs, 'source': source()}]},
                                           target_bytes=3_000, expanded_target=40_000, hard_limit=60_000, max_parts=4)
        self.assertLessEqual(len(result['finding_files']['ENT-018']['required']), 5)

    def test_oversized_values_move_losslessly_and_are_reported(self):
        giant = 'ä' * 50_000 + 'end'
        logs = [event(1, userAgent=giant), event(2)]
        _, _, folder, result = self.export([legacy_finding()], {'signin_logs': [{'records': logs, 'source': source()}]},
                                           target_bytes=4_000, expanded_target=8_000, hard_limit=40_000)
        for path in folder.iterdir():
            self.assertLess(path.stat().st_size, 40_000, path.name)
        row = next(row for row in read_csv_parts(folder, result['finding_files']['ENT-018']['required']) if row['eventId'] == 'event-1')
        self.assertTrue(row['userAgent'].startswith('[oversized value moved losslessly'))
        self.assertEqual(rebuild_oversized(folder, row)['userAgent'], giant)
        manifest, _ = self.manifest(folder)
        self.assertEqual(manifest['oversized_records'][0]['field'], 'userAgent')
        self.assertTrue(any(name.startswith('10-legacy-authentication-oversized-values') for name in manifest['upload_sets']['legacy-authentication']['required']))

    def test_long_table_names_are_bounded_without_changing_ids_rows_or_reconstruction(self):
        giant = 'retained value ' * 10000
        _, model = build([legacy_finding(), configuration_finding()],
                         {'signin_logs': [{'records': [event(1, userAgent=giant), event(2)], 'source': source()}]})
        old_concern = model['concerns'][0]['concern_id']
        concern_id = 'legacy_authentication_' * 7
        mapping = {table_id: 'long_table_identifier_' * 8 + str(number)
                   for number, table_id in enumerate(model['tables'], 1)}
        model['tables'] = {mapping[table_id]: dict(table, table_id=mapping[table_id], concern_id=concern_id)
                           for table_id, table in model['tables'].items()}
        for finding in model['findings']:
            finding['concern_id'] = concern_id
            for key in ('tables', 'context_tables'):
                finding['evidence'][key] = [mapping[table_id] for table_id in finding['evidence'][key]]
        for concern in model['concerns']:
            if concern['concern_id'] == old_concern:
                concern['concern_id'] = concern_id
            concern['tables'] = [mapping[table_id] for table_id in concern['tables']]
        folder = Path(self.directory.name) / 'App Builder'
        result = write_app_builder_export(model, folder, target_bytes=4000, expanded_target=8000, hard_limit=60000)
        manifest, entries = self.manifest(folder)
        self.assertEqual(set(mapping.values()), set(manifest['tables']))
        projected_root = Path(__file__).resolve().parents[1] / 'Reports' / ('Customer' + 'x' * 32) / '2026-10-06' / 'Builds' / '1' / 'App Builder'
        for path in folder.iterdir():
            self.assertLessEqual(len(path.name), 72)
            self.assertNotIn('_', path.name)
            self.assertLessEqual(len(str(projected_root / path.name)), 255)
        rows = read_csv_parts(folder, result['finding_files']['ENT-018']['required'])
        row = next(item for item in rows if item['eventId'] == 'event-1')
        self.assertEqual(rebuild_oversized(folder, row)['userAgent'], giant)
        self.assertEqual([item['eventId'] for item in rows], ['event-1', 'event-2'])
        self.assertTrue(all((folder / item['file']).is_file() for item in entries))

    def test_long_part_filenames_keep_unique_visible_part_numbers(self):
        from Core.app_builder_export import _Writer
        folder = Path(self.directory.name) / 'numbered'
        writer = _Writer(folder, target_bytes=1000, hard_limit=10000, expanded_target=2000, max_parts=100)
        names = []
        for stem in ('same_long_identifier_' * 8 + 'first', 'same_long_identifier_' * 8 + 'second'):
            for number in (1, 2, 10000):
                record = writer.write(f'{stem}-p{number:02d}.csv', b'row\n', part=number)
                names.append(record['file'])
                self.assertTrue(record['file'].endswith(f'-p{number:02d}.csv'))
                self.assertLessEqual(len(record['file']), 72)
                self.assertNotIn('_', record['file'])
        self.assertEqual(len(set(names)), len(names))

    def test_missing_partial_and_configuration_evidence_stay_visible(self):
        _, _, folder, result = self.export([legacy_finding(), configuration_finding(), gap_finding()],
                                           {'signin_logs': [{'records': LOGS, 'source': source(truncated=True)}]})
        catalog = json.loads((folder / '02-finding-catalog.json').read_text(encoding='utf-8'))['findings']
        by_id = {item['finding_id']: item for item in catalog}
        self.assertEqual(by_id['ENT-018']['evidence_availability'], 'partial')
        self.assertEqual(by_id['GAP-IDENTITY-MFA']['evidence_availability'], 'not_retained')
        self.assertEqual(by_id['GAP-IDENTITY-MFA']['evidence_files'], [])
        self.assertEqual(by_id['M365-009']['evidence_kind'], 'configuration')
        self.assertEqual(by_id['M365-009']['concern_id'], 'legacy-authentication')
        self.assertTrue(set(by_id['M365-009']['evidence_files']).isdisjoint(by_id['ENT-018']['evidence_files']))
        findings = json.loads((folder / '11-mfa-registration-findings.json').read_text(encoding='utf-8'))['findings']
        gap = findings[0]['evidence']
        self.assertTrue(gap['missing_evidence_action'])
        self.assertEqual(gap['evidence_availability'], 'not_retained')
        legacy = json.loads((folder / '10-legacy-authentication-findings.json').read_text(encoding='utf-8'))['findings']
        entry = next(item for item in legacy if item['finding_id'] == 'ENT-018')
        self.assertEqual(entry['technical_fix']['guidance_status'], 'verified')
        self.assertTrue(entry['technical_fix']['documentation'][0]['url'].startswith('https://learn.microsoft.com/'))

    def test_supporting_context_is_written_once_with_every_finding_id(self):
        rows = [dict(legacy_finding('REF-1'), Disposition='Reference', FindingKey='synthetic.inventory', EvidenceKey='legacy_signin_detail',
                     InvestigationSummary='Shared source inventory', Observation='Inventory one.'),
                dict(legacy_finding('REF-2'), Disposition='Reference', FindingKey='synthetic.inventory2', EvidenceKey='legacy_signin_detail',
                     InvestigationSummary='Shared source inventory', Observation='Inventory two.')]
        payload, model = build(rows, {'signin_logs': [{'records': LOGS[:3], 'source': source()}]})
        context = [table for table in model['tables'].values() if table['role'] == 'context']
        self.assertEqual(len(context), 1)
        self.assertEqual(len(context[0]['rows']), 3)
        self.assertEqual({row['finding_ids'] for row in context[0]['rows']}, {'REF-1; REF-2'})
        self.assertEqual([item['evidence']['context_record_count'] for item in model['findings']], [3, 3])

    def test_credentials_are_never_exported_and_output_is_deterministic(self):
        logs = [event(1, accessToken='never-export-this', refreshToken='never-export-this')]
        _, model = build([legacy_finding()], {'signin_logs': [{'records': logs, 'source': source()}]})
        folders = [Path(self.directory.name) / name for name in ('a', 'b')]
        for folder in folders:
            write_app_builder_export(model, folder)
        for path in folders[0].iterdir():
            data = path.read_bytes()
            self.assertNotIn(b'never-export-this', data)
            self.assertEqual(data, (folders[1] / path.name).read_bytes(), path.name)

    def test_existing_files_are_never_overwritten(self):
        _, model = build([legacy_finding()], {'signin_logs': [{'records': LOGS, 'source': source()}]})
        folder = Path(self.directory.name) / 'occupied'
        folder.mkdir()
        (folder / 'keep.txt').write_text('keep', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            write_app_builder_export(model, folder)

    def test_csv_values_are_exact(self):
        logs = [event(1, userAgent='=HYPERLINK("x")', userDisplayName='Ünïcode, "quoted"\nline')]
        _, _, folder, result = self.export([legacy_finding()], {'signin_logs': [{'records': logs, 'source': source()}]})
        name = result['finding_files']['ENT-018']['required'][0]
        with (folder / name).open(encoding='utf-8', newline='') as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row['userAgent'], '=HYPERLINK("x")')
        self.assertEqual(row['userDisplayName'], 'Ünïcode, "quoted"\nline')
        manifest, entries = self.manifest(folder)
        self.assertEqual(next(entry for entry in entries if entry['file'] == name)['formula_like_cells'], 1)


if __name__ == '__main__':
    unittest.main()

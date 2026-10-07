import contextlib
import csv
import io
import json
import os
from pathlib import Path
import re
import runpy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from html import unescape
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from Core.offline_collection import (
    empty_service_results, load_collection, save_collection, refresh_saved_freshness,
)
from Core.portal_report_import import route_portal_reports, validate_report_tenants
from Core.export_paths import APP_BUILDER_FOLDER, BUILDS_FOLDER, EVIDENCE_FOLDER, JSON_ARCHIVE_STEM, JSON_FOLDER, SUMMARY_STEM, customer_reports_directory

ROOT = Path(__file__).resolve().parents[1]


class OfflineReportTests(unittest.TestCase):
    def csv(self, path, rows):
        with path.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0])
            writer.writeheader()
            writer.writerows(rows)
        return path

    def saved(self, path, collected_at=None):
        results = empty_service_results()
        results['m365_result'] = [{'_client': SimpleNamespace(
            available=True, users_summary={'total': 3},
            external_connections=[], collection_status={},
            client_secret='DO_NOT_SAVE_SECRET', access_token='DO_NOT_SAVE_TOKEN',
        ), 'licenses': []}, [{
            'Service': 'M365', 'Feature': 'Saved control finding',
            'Observation': 'Evidence from the original collection.', 'Recommendation': 'Review this control.',
            'Status': 'Attention Required', 'Priority': 'Medium', 'Disposition': 'Action',
        }]]
        save_collection(path, tenant_id='11111111-1111-1111-1111-111111111111',
                        tenant_name='Example tenant', service_results=results, collected_at=collected_at)
        return path

    def test_collection_round_trip_excludes_secrets_and_preserves_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.saved(Path(directory) / 'collection.json')
            raw = path.read_text(encoding='utf-8')
            self.assertNotIn('DO_NOT_SAVE', raw)
            loaded = load_collection(path)
            self.assertEqual(loaded['service_results']['m365_result'][0]['_client'].users_summary['total'], 3)
            self.assertEqual(loaded['service_results']['m365_result'][1][0]['Feature'], 'Saved control finding')

    def test_assessment_snapshot_is_not_mistaken_for_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot.json'
            path.write_text('{"recommendations": []}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'save-collection'):
                load_collection(path)

    def test_power_platform_inventory_and_legacy_transport_save_evidence_only(self):
        import asyncio
        import httpx
        from Core.power_platform_inventory import PowerPlatformInventoryData
        with tempfile.TemporaryDirectory() as directory:
            results = empty_service_results()
            inventory = PowerPlatformInventoryData()
            inventory.agents = [{'id': 'invented-agent', 'name': 'Example agent'}]
            results['power_platform_info']['_client'] = inventory
            transport = httpx.AsyncClient(headers={'Authorization': 'Bearer DO_NOT_SAVE'})
            try:
                transport.environments = [{'name': 'Example environment'}]
                transport.environment_summary = {'total': 1}
                results['copilot_studio_info']['_client'] = transport
                path = Path(directory) / 'collection.json'
                save_collection(path, tenant_id='example', tenant_name='Example', service_results=results)
                loaded = load_collection(path)['service_results']
                self.assertEqual(loaded['power_platform_info']['_client'].agents, inventory.agents)
                self.assertEqual(loaded['copilot_studio_info']['_client'].environment_summary, {'total': 1})
                self.assertNotIn('DO_NOT_SAVE', path.read_text(encoding='utf-8'))
                self.assertFalse(hasattr(loaded['copilot_studio_info']['_client'], 'headers'))
            finally:
                asyncio.run(transport.aclose())

    def test_no_executable_object_serialization(self):
        with tempfile.TemporaryDirectory() as directory:
            results = empty_service_results()
            results['m365_result'][0]['_client'] = object()
            with self.assertRaisesRegex(ValueError, 'non-data object'):
                save_collection(Path(directory) / 'bad.json', tenant_id='example',
                                tenant_name='Example', service_results=results)

    def test_old_usage_freshness_is_recomputed(self):
        results = empty_service_results()
        usage = {'available': True, 'freshness': 'Fresh',
                 'refresh_date': (datetime.now(timezone.utc) - timedelta(days=20)).date().isoformat()}
        results['m365_result'][0]['_client'] = SimpleNamespace(copilot_usage=usage)
        refresh_saved_freshness(results)
        self.assertEqual(usage['freshness'], 'Stale')

    def test_portal_routing_includes_header_only_label_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'unhelpful-filename.csv'
            path.write_text('Site ID,URL,Primary admin,Labeled files,SiteSensitivity,External sharing\n', encoding='utf-8')
            routed = route_portal_reports([directory])
            self.assertEqual(routed['sam'], [str(path)])
            self.assertFalse(routed['dspm'])

    def test_cross_tenant_export_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.csv(Path(directory) / 'report.csv', [{'Tenant ID': '22222222-2222-2222-2222-222222222222', 'Anyone link count': '1'}])
            with self.assertRaisesRegex(ValueError, 'does not match'):
                validate_report_tenants([str(path)], '11111111-1111-1111-1111-111111111111')

    def run_cli(self, args, directory):
        old_cwd = Path.cwd()
        try:
            os.chdir(directory)
            with patch('sys.argv', ['main.py', *args]), \
                 patch('Core.console_setup.setup_console_encoding'), \
                 patch('Core.credentials_check.load_env_file', side_effect=AssertionError('loaded credentials')), \
                 patch('Core.credentials_check.validate_credentials_or_exit', side_effect=AssertionError('validated credentials')), \
                 patch('socket.socket.connect', side_effect=AssertionError('network attempted')), \
                 patch('socket.create_connection', side_effect=AssertionError('network attempted')), \
                 patch('subprocess.Popen', side_effect=AssertionError('external process attempted')), \
                 patch('builtins.input', side_effect=AssertionError('interactive prompt attempted')), \
                 patch.dict(os.environ, {'SAM_DAG_REPORT_PATHS': str(Path(directory) / 'must-not-open.csv')}), \
                 contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exit_context:
                runpy.run_path(str(ROOT / 'main.py'), run_name='__main__')
            self.assertEqual(exit_context.exception.code, 0)
            if '--collection-input' in args:
                collection = args[args.index('--collection-input') + 1]
                package = Path(load_collection(collection)['package_directory'])
            else:
                receipt_path = max((Path(directory) / 'Reports').rglob('operator-log.jsonl'),
                                   key=lambda path: path.stat().st_mtime_ns)
                package = receipt_path.parent
            receipt = json.loads((package / 'operator-log.jsonl').read_text(encoding='utf-8').splitlines()[-1])
            report_directory = package / BUILDS_FOLDER / receipt['run_id']
            summary_paths = [package / name for name in receipt['deliverables']
                             if (package / name).parent == report_directory and name.endswith('.html')
                             and (Path(name).stem == SUMMARY_STEM or Path(name).stem.startswith(SUMMARY_STEM + ' - '))]
            self.assertEqual(len(summary_paths), 1, 'The receipt must identify one pilot summary.')
            self.assertTrue(summary_paths[0].is_file(), 'The pilot summary must be written.')
            html_paths = [package / name for name in receipt['deliverables']
                          if (package / name).parent == report_directory and name.endswith('.html')
                          and package / name not in summary_paths]
            self.assertEqual(len(html_paths), 1, 'The receipt must identify one primary HTML report.')
            self.assertTrue(html_paths[0].is_file())
            self.assertFalse(list((Path(directory) / 'Reports').glob('*.html')))
            return html_paths[0]
        finally:
            os.chdir(old_cwd)

    def assertLocalHtmlLinksExist(self, folder):
        for path in folder.rglob('*.html'):
            for href in re.findall(r'href="([^"]+)"', path.read_text(encoding='utf-8')):
                uri = urlsplit(unescape(href))
                if uri.scheme or uri.netloc or not uri.path:
                    continue
                self.assertTrue((path.parent / unquote(uri.path)).resolve().is_file(), f'{path.name}: {href}')

    def test_default_build_retains_workbook_evidence_without_extra_packages(self):
        from openpyxl import load_workbook
        from Core.workbook_layout import technical_workbook_path
        from tests.synthetic_package_fixture import create_synthetic_package
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # The separate rebuild test covers 1,500 events and oversized fields;
            # a small source population keeps this default-output check focused.
            with patch('tests.synthetic_package_fixture.LEGACY_EVENTS', 12), \
                 patch('tests.synthetic_package_fixture.GIANT_USER_AGENT', 'Fictional default export client'):
                collection = create_synthetic_package(root / 'source', evidence_drilldown=True)
            expected = {row['id'] for row in load_collection(collection)['service_results']['entra_info']['_client'].signin_logs
                        if row['id'].startswith('fictional-signin-')}
            html = self.run_cli(['--collection-input', str(collection)], root)
            files = list(html.parent.iterdir())
            self.assertEqual(len(files), 4, 'Default deliverables are two HTML files and two workbooks.')
            self.assertTrue(all(path.is_file() and path.suffix in {'.html', '.xlsx'} for path in files))
            self.assertLocalHtmlLinksExist(html.parent)
            body = html.read_text(encoding='utf-8')
            self.assertIn('Technical evidence workbook', body)
            self.assertNotIn('Evidence/', body)
            assessment = load_workbook(html.with_suffix('.xlsx'), read_only=True)
            technical = load_workbook(technical_workbook_path(html.with_suffix('.xlsx')), read_only=True)
            try:
                self.assertEqual(assessment['Users & Identity'].sheet_state, 'visible')
                self.assertGreater(assessment['Users & Identity'].max_row, 1)
                retained = {value for sheet in technical for row in sheet.values for value in row
                            if isinstance(value, str) and value.startswith('fictional-signin-')}
                self.assertTrue(expected.issubset(retained), 'Every original sign-in ID must remain in the technical workbook.')
            finally:
                assessment.close()
                technical.close()

    def test_extra_exports_are_independent_and_have_no_missing_html_targets(self):
        from Core.dashboard_package import read_dashboard_package
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collection = self.saved(root / 'collection.json')
            for selected, folder in (('evidence-pages', EVIDENCE_FOLDER), ('app-builder', APP_BUILDER_FOLDER),
                                     ('dashboard-json', JSON_FOLDER)):
                with self.subTest(selected=selected):
                    html = self.run_cli(['--collection-input', str(collection), '--extra-exports', selected], root)
                    self.assertEqual({path.name for path in html.parent.iterdir() if path.is_dir()}, {folder})
                    self.assertLocalHtmlLinksExist(html.parent)
                    archives = list(html.parent.glob('Dashboard JSON*.zip'))
                    self.assertEqual(len(archives), int(selected == 'dashboard-json'))
                    if selected == 'app-builder':
                        overview = json.loads((html.parent / folder / '01-overview.json').read_text(encoding='utf-8'))
                        self.assertIsNone(overview['deliverables']['evidence_pages'])
                        self.assertIsNone(overview['deliverables']['dashboard_json'])
                        for target in overview['deliverables'].values():
                            if target:
                                self.assertTrue((html.parent / target).is_file(), target)
                    if selected == 'dashboard-json':
                        index = html.parent / JSON_FOLDER / 'index.json'
                        dashboard = read_dashboard_package(index)
                        self.assertNotIn('evidence_pages', dashboard['deliverables'])
                        self.assertNotIn('app_builder', dashboard['deliverables'])
                        for target in dashboard['deliverables'].values():
                            if target:
                                self.assertTrue((index.parent / target).is_file(), target)

    def test_single_snapshot_does_not_enable_extra_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            collection = self.saved(root / 'collection.json')
            snapshot = root / 'snapshot.json'
            html = self.run_cli(['--collection-input', str(collection), '--snapshot-json', str(snapshot)], root)
            self.assertTrue(snapshot.is_file())
            self.assertFalse(any(path.is_dir() for path in html.parent.iterdir()))
            dashboard = json.loads(snapshot.read_text(encoding='utf-8'))
            self.assertNotIn('evidence_pages', dashboard['deliverables'])
            self.assertNotIn('app_builder', dashboard['deliverables'])
            for target in dashboard['deliverables'].values():
                if target:
                    self.assertTrue((snapshot.parent / target).is_file(), target)

    def assertNotEstablished(self, text, message):
        # The headline states the verdict; replayed or partial evidence must not reach a pilot verdict.
        headline = re.search(r'<h1>(.*?)</h1>', text).group(1)
        self.assertIn(headline, {'Not enough evidence to decide yet', 'Not ready for a pilot yet'}, message)

    def test_cli_replay_builds_reports_without_auth_network_or_powershell(self):
        with tempfile.TemporaryDirectory() as directory:
            timestamp = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
            path = self.saved(Path(directory) / 'collection.json', collected_at=timestamp)
            html = self.run_cli(['--collection-input', str(path)], directory)
            text = html.read_text(encoding='utf-8')
            self.assertIn('saved control finding', text.lower())
            self.assertIn('40 days old', text)
            self.assertNotEstablished(text, 'Stale replay must not establish readiness.')
            self.assertTrue(html.with_suffix('.xlsx').is_file())
            self.assertNotIn('must-not-open', text)

    def test_customer_package_replays_from_another_directory_without_duplicate_exports(self):
        from Core.dashboard_package import read_dashboard_package

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                collection = Path(save_collection(
                    tenant_id='11111111-1111-1111-1111-111111111111', tenant_name='Example tenant',
                    customer_name='Fictional Customer', service_results=empty_service_results(),
                    collected_at='2026-09-15T12:00:00Z'))
            finally:
                os.chdir(original_cwd)
            package = collection.parent
            self.assertEqual(package.parent, root / 'Reports' / 'Fictional Customer')
            original_collection = collection.read_bytes()
            runner = root / 'separate working directory'
            runner.mkdir()
            arguments = ['--mode', 'offline', '--collection-input', str(collection),
                         '--evaluation-date', '2026-09-15', '--extra-exports', 'dashboard-json']
            first = self.run_cli(arguments, runner)
            second = self.run_cli(arguments, runner)
            self.assertNotEqual(first.parent, second.parent)
            self.assertEqual({first.parent.parent, second.parent.parent}, {package / BUILDS_FOLDER})
            self.assertTrue(first.is_file(), 'Rebuilding must retain the previous report.')
            self.assertEqual(collection.read_bytes(), original_collection)
            self.assertFalse((runner / 'Reports').exists())
            self.assertFalse((root / 'output').exists())
            self.assertEqual(len(list((root / 'Reports').rglob('collection.json'))), 1)
            for report in (first, second):
                receipt_paths = [json.loads(line)['deliverables']
                                 for line in (package / 'operator-log.jsonl').read_text(encoding='utf-8').splitlines()]
                own_receipt = next(paths for paths in receipt_paths if report.relative_to(package).as_posix() in paths)
                self.assertEqual(len(own_receipt), len(set(own_receipt)))
                self.assertEqual(len(list(report.parent.glob(JSON_ARCHIVE_STEM + '*.zip'))), 1)
                index_path = report.parent / JSON_FOLDER / 'index.json'
                dashboard = read_dashboard_package(index_path)
                self.assertEqual(dashboard['deliverable_path_base'], 'index_directory')
                for target in dashboard['deliverables'].values():
                    if target:
                        self.assertTrue((index_path.parent / target).is_file(), target)
                body = report.read_text(encoding='utf-8')
                self.assertIn('href="' + report.with_suffix('.xlsx').name.replace(' ', '%20') + '"', body)
                self.assertNotIn(str(root), body)

    def test_portal_only_customer_name_organizes_one_report_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self.csv(root / 'CMA.csv', [{
                'Site name': 'Old site', 'URL': 'https://example.sharepoint.com/sites/old',
                'Is inactive': 'True', 'Is ownerless': 'True',
                'Email address of site owners': '', 'Site creation date (UTC)': '2020-01-01',
            }])
            html = self.run_cli(['--mode', 'offline', '--sam-report', str(report),
                                 '--customer-name', 'Fictional Customer'], directory)
            customer = root / 'Reports' / 'Fictional Customer'
            self.assertTrue(html.is_relative_to(customer))
            self.assertEqual(len(list(customer.glob('*/rebuild.json'))), 1)
            package = next(customer.glob('*/rebuild.json')).parent
            self.assertTrue(html.is_relative_to(package / BUILDS_FOLDER))
            self.assertFalse((root / 'output').exists())

    def test_complete_synthetic_build_fits_repository_paths_and_preserves_links_on_rebuild(self):
        import zipfile
        from Core.dashboard_package import read_dashboard_package
        from tests.synthetic_package_fixture import create_synthetic_package

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = load_collection(create_synthetic_package(root / 'source', evidence_drilldown=True))
            results = fixture['service_results']
            long_id = 'Fictional_Recommendation_' + 'Identifier With Spaces_' * 12
            finding = next(row for row in results['entra_info']['recommendations']
                           if row.get('FindingKey') == 'entra.signins.legacy_auth')
            finding['RecommendationId'] = long_id
            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                collection = Path(save_collection(
                    tenant_id=fixture['tenant_id'], tenant_name=fixture['tenant_name'],
                    customer_name='Fictional_Customer With A Very Long Display Name For Path Validation',
                    service_results=results, supplemental_inputs=fixture['resolved_inputs'],
                    collected_at=fixture['collected_at'], evaluation_date='2026-09-15',
                    enabled_collectors=fixture['enabled_collectors'], assessment_settings=fixture['assessment_settings']))
            finally:
                os.chdir(old_cwd)
            self.assertLessEqual(len(collection.parent.parent.name.encode('utf-16-le')) // 2, 32)
            original = collection.read_bytes()
            args = ['--mode', 'offline', '--collection-input', str(collection), '--evaluation-date', '2026-09-15',
                    '--extra-exports', 'evidence-pages', 'app-builder', 'dashboard-json']
            first = self.run_cli(args, root)
            preserved = {path: path.read_bytes() for path in first.parent.rglob('*') if path.is_file()}
            second = self.run_cli(args, root)
            self.assertEqual(first.parent.name, '1')
            self.assertEqual(second.parent.name, '2')
            self.assertEqual(collection.read_bytes(), original)
            for path, data in preserved.items():
                self.assertEqual(path.read_bytes(), data, path)
            for report in (first, second):
                self.assertTrue((report.parent / APP_BUILDER_FOLDER / '03-manifest.json').is_file())
                self.assertTrue((report.parent / EVIDENCE_FOLDER / 'index.html').is_file())
                index = report.parent / JSON_FOLDER / 'index.json'
                dashboard = read_dashboard_package(index)
                self.assertIn(long_id, [row['RecommendationId'] for row in dashboard['recommendations']])
                for path in report.parent.rglob('*'):
                    if not path.is_file():
                        continue
                    relative = path.relative_to(root)
                    actual_repository_path = ROOT / relative
                    self.assertLessEqual(len(str(actual_repository_path).encode('utf-16-le')) // 2, 255, actual_repository_path)
                    self.assertTrue(all('_' not in part for part in relative.parts), relative)
                    if path.suffix == '.html':
                        source = path.read_text(encoding='utf-8')
                        for href in re.findall(r'href="([^"]+)"', source):
                            uri = urlsplit(unescape(href))
                            if uri.scheme or uri.netloc or not uri.path:
                                continue
                            target = (path.parent / unquote(uri.path)).resolve()
                            self.assertTrue(target.is_file(), f'{path.name}: {href}')
                with zipfile.ZipFile(next(report.parent.glob(JSON_ARCHIVE_STEM + '*.zip'))) as archive:
                    self.assertIsNone(archive.testzip())
                    for path in index.parent.rglob('*.json'):
                        self.assertEqual(archive.read(path.relative_to(index.parent).as_posix()), path.read_bytes())

    def test_portal_only_without_a_name_uses_each_tenant_id_for_organization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self.csv(root / 'lifecycle.csv', [{
                'Site name': 'Old site', 'URL': 'https://example.sharepoint.com/sites/old',
                'Is inactive': 'True', 'Is ownerless': 'True',
                'Email address of site owners': '', 'Site creation date (UTC)': '2020-01-01',
            }])
            for tenant_id in ('11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222'):
                html = self.run_cli(['--mode', 'offline', '--sam-report', str(report),
                                     '--tenant-id', tenant_id], directory)
                self.assertTrue(html.is_relative_to(root / customer_reports_directory(tenant_id=tenant_id)))
            self.assertFalse((root / customer_reports_directory(tenant_name='Portal export review')).exists())

    def test_repeating_reports_directory_reuses_packaged_readiness_snapshot_once(self):
        from Core.copilot_readiness_import import FLAG_COLUMNS
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            reports = folder / 'admin exports'
            reports.mkdir()
            row = {name: 'False' for name in FLAG_COLUMNS.values()}
            row.update({'Report Refresh Date': '2026-09-12', 'User Principal Name': 'invented@example.test', 'Report Period': '30'})
            self.csv(reports / 'readiness.csv', [row])
            collection = self.saved(folder / 'collection.json', collected_at='2026-09-15T12:00:00Z')
            original = collection.read_bytes()
            snapshots = []
            for index in range(2):
                snapshot = folder / f'snapshot-{index}.json'
                self.run_cli(['--mode', 'offline', '--collection-input', str(collection),
                              '--reports-dir', str(reports), '--evaluation-date', '2026-09-15',
                              '--snapshot-json', str(snapshot)], directory)
                snapshots.append(json.loads(snapshot.read_text(encoding='utf-8'))['assessment_result'])
            self.assertEqual(snapshots[0], snapshots[1])
            self.assertEqual(collection.read_bytes(), original)
            package = Path(load_collection(collection)['package_directory'])
            receipt = json.loads((package / 'operator-log.jsonl').read_text(encoding='utf-8').splitlines()[-1])
            self.assertTrue(any(row['Status'] == 'duplicate' for row in receipt['import_receipt']))

    def test_distinct_readiness_snapshots_require_an_explicit_selection(self):
        from Core.portal_report_import import select_readiness_report
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / 'first.csv', Path(directory) / 'second.csv'
            first.write_text('first snapshot', encoding='utf-8')
            second.write_text('different snapshot', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'More than one') as error:
                select_readiness_report([str(first), str(second)])
            self.assertIn('first.csv', str(error.exception))
            self.assertIn('second.csv', str(error.exception))
            self.assertIn('--reports-dir adds files', str(error.exception))
            self.assertIn('same tenant', str(error.exception))
            selected, receipt = select_readiness_report([str(first), str(second)], str(second))
            self.assertEqual(selected, [str(second)])
            self.assertEqual(receipt[0]['Status'], 'not_selected')

    def test_replay_user_usage_details_are_default_and_alias_is_compatible(self):
        from Core.cli_parser import parse_arguments
        from Core.offline_report import run_offline_report
        with tempfile.TemporaryDirectory() as directory:
            results = empty_service_results()
            results['m365_result'][0]['_client'] = SimpleNamespace(copilot_usage={
                'available': False, 'user_detail': [{'userPrincipalName': 'private@example.invalid'}],
            })
            path = Path(directory) / 'collection.json'
            save_collection(path, tenant_id='example', tenant_name='Example', service_results=results)
            for opted_in in (False, True):
                arguments = ['main.py', '--collection-input', str(path)]
                if opted_in:
                    arguments.append('--include-user-usage-detail')
                with patch('sys.argv', arguments):
                    args = parse_arguments(None, [])
                with patch('Core.processor.process_and_print_all_information', return_value={'html_path': 'test.html'}) as process:
                    self.assertEqual(run_offline_report(args), 0)
                usage = process.call_args.kwargs['m365_result'][0]['_client'].copilot_usage
                self.assertTrue(usage.get('user_detail'))

    def test_portal_only_cli_does_not_claim_complete_tenant_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.csv(Path(directory) / 'CMA.csv', [{
                'Site name': 'Old site', 'URL': 'https://example.sharepoint.com/sites/old',
                'Is inactive': 'True', 'Is ownerless': 'True',
                'Email address of site owners': '', 'Site creation date (UTC)': '2020-01-01',
            }])
            html = self.run_cli(['--mode', 'offline', '--sam-report', str(report)], directory)
            text = html.read_text(encoding='utf-8')
            self.assertIn('Require MFA and block legacy sign-in for all users', text)
            self.assertNotEstablished(text, 'Portal exports alone must not establish readiness.')
            self.assertNotIn('must-not-open', text)

    def test_readiness_cli_import_is_separate_from_copilot_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            from Core.copilot_readiness_import import FLAG_COLUMNS
            row = {'Report Refresh Date': '2026-09-12', 'Report Period': '30',
                   'User Principal Name': 'private-user@example.invalid',
                   **{header: 'Yes' for header in FLAG_COLUMNS.values()}}
            report = self.csv(Path(directory) / 'readiness.csv', [row])
            html = self.run_cli(['--offline', '--copilot-readiness-export', str(report)], directory)
            text = html.read_text(encoding='utf-8')
            self.assertIn('Copilot readiness portal export', text)
            self.assertNotIn('private-user@example.invalid', text)

    def test_prior_workbook_and_portal_export_build_together_offline(self):
        from openpyxl import Workbook
        from tests.workbook_test_helpers import load_workbook_pair as load_workbook
        with tempfile.TemporaryDirectory() as directory:
            prior = Path(directory) / 'prior.xlsx'
            wb = Workbook()
            ws = wb.active
            ws.title = 'Recommendations'
            ws.append(['Service', 'Feature', 'Observation', 'Recommendation', 'Disposition', 'Priority'])
            ws.append(['Entra', 'Historical MFA finding', 'Prior MFA evidence.', 'Review sign-in protection.', 'Action', 'High'])
            manifest = wb.create_sheet('Run Manifest')
            manifest.append(['Item', 'Value'])
            manifest.append(['Tenant', 'Example tenant'])
            manifest.append(['Generation Time (UTC)', '2026-09-09T18:30:00+00:00'])
            detail = wb.create_sheet('Authentication Coverage')
            detail.append(['Metric', 'Value'])
            detail.append(['Prior MFA registration count', 12])
            wb.save(prior)
            wb.close()
            report = self.csv(Path(directory) / 'CMA.csv', [{
                'Site name': 'Old site', 'URL': 'https://example.sharepoint.com/sites/old',
                'Is inactive': 'True', 'Is ownerless': 'True',
                'Email address of site owners': '', 'Site creation date (UTC)': '2020-01-01',
            }])
            cache = Path(directory) / 'purview-cache.json'
            cache.write_text(json.dumps({
                'schema_version': 2, 'tenant_id': '11111111-1111-1111-1111-111111111111',
                'cached_at_epoch': datetime(2026, 9, 9, tzinfo=timezone.utc).timestamp(),
                'purview_data_json': json.dumps({'dlp_policies': {
                    'available': True, 'count': 1,
                    'policies': [{'Name': 'Example cached DLP policy', 'Mode': 'Enable'}],
                }}),
            }), encoding='utf-8')
            html = self.run_cli(['--mode', 'offline', '--prior-report', str(prior),
                                 '--purview-cache', str(cache), '--sam-report', str(report)], directory)
            body = html.read_text(encoding='utf-8')
            self.assertTrue('historical mfa finding' in body.lower(), 'Prior findings should appear with imported reports.')
            self.assertTrue('2026-09-09T18:30:00+00:00' in body, 'Prior generation date must be retained.')
            self.assertTrue('Content ownership and lifecycle' in body)
            self.assertNotEstablished(body, 'Prior findings and exports alone must not establish readiness.')
            self.assertTrue('purview-cache.json' in body, 'The Purview source must be identified.')
            combined = load_workbook(html.with_suffix('.xlsx'), read_only=True)
            try:
                history = [s for s in combined.technical if s.title.startswith('Prior')]
                self.assertTrue(history, 'Historical evidence must remain in the technical workbook.')
                values = [str(value) for sheet in history for row in sheet.values for value in row]
                self.assertIn('Prior MFA registration count', values)
                self.assertIn('Purview Policy Detail', combined.technical.sheetnames)
            finally:
                combined.close()


if __name__ == '__main__':
    unittest.main()

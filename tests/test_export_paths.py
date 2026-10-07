import builtins
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Core.export_paths import REPORT_STEM, customer_reports_directory, new_assessment_directory
from Core.export_recommendations import build_report_filename, export_to_csv, export_to_excel, export_to_json


class CustomerExportPathTests(unittest.TestCase):
    def setUp(self):
        self.original_cwd = Path.cwd()
        self.directory = tempfile.TemporaryDirectory()
        os.chdir(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(os.chdir, self.original_cwd)
        self.rows = [{'Service': 'M365', 'Feature': 'Review', 'Status': 'Attention Required',
                      'Observation': 'Review the saved evidence.', 'Recommendation': 'Confirm the configuration.'}]

    def test_customer_label_takes_precedence_with_tenant_id_fallback(self):
        self.assertEqual(customer_reports_directory('Customer A', 'Tenant name', 'tenant-id'), Path('Reports/Customer A'))
        self.assertEqual(customer_reports_directory(tenant_name='Tenant name'), Path('Reports/Tenant name'))
        self.assertEqual(customer_reports_directory(tenant_id='tenant-id'), Path('Reports/tenant-id'))

    def test_untrusted_customer_labels_stay_under_reports_and_work_on_windows(self):
        for label in ('../../Other customer', r'C:\outside\folder', 'CON', 'nul.txt', 'LPT9', '... ', '客户'):
            with self.subTest(label=label):
                folder = new_assessment_directory(customer_name=label)
                self.assertTrue(folder.is_dir())
                self.assertTrue(folder.is_relative_to(Path('Reports').resolve()))
                self.assertEqual(len(folder.relative_to(Path('Reports').resolve()).parts), 2)

    def test_repeat_assessments_preserve_previous_customer_outputs(self):
        first = new_assessment_directory(customer_name='Customer A')
        (first / 'retained.json').write_text('original', encoding='utf-8')
        second = new_assessment_directory(customer_name='Customer A')
        other = new_assessment_directory(customer_name='Customer B')
        self.assertNotEqual(first, second)
        self.assertNotEqual(first.parent, other.parent)
        self.assertEqual((first / 'retained.json').read_text(encoding='utf-8'), 'original')

    def test_unicode_customer_labels_respect_windows_path_budget(self):
        for label in ('Customer ' + '\U0001f680' * 35, '\u5ba2\u6237' * 30):
            with self.subTest(label=label):
                folder = customer_reports_directory(label)
                self.assertLessEqual(len(folder.name.encode('utf-16-le')) // 2, 32)
                self.assertNotIn('_', folder.name)

    def test_direct_csv_and_json_exports_share_customer_directory(self):
        csv = Path(export_to_csv(self.rows, filename='findings.csv', tenant_name='Customer A'))
        json = Path(export_to_json(self.rows, filename='findings.json', tenant_name='Customer A'))
        self.assertEqual(csv.parent, Path('Reports/Customer A'))
        self.assertEqual(csv.parent, json.parent)
        self.assertTrue(csv.is_file())
        self.assertTrue(json.is_file())

    def test_excel_csv_fallback_keeps_selected_directory_and_filename(self):
        original_import = builtins.__import__
        def without_excel(name, *args, **kwargs):
            if name == 'openpyxl':
                raise ImportError('Excel unavailable for this test')
            return original_import(name, *args, **kwargs)
        folder = Path('Reports/Customer A/Builds/1')
        with patch('builtins.__import__', side_effect=without_excel), contextlib.redirect_stdout(io.StringIO()):
            path = Path(export_to_excel(self.rows, filename='findings.xlsx', tenant_name='Customer A', output_dir=folder))
        self.assertEqual(path, folder / 'findings.csv')
        self.assertTrue(path.is_file())
        self.assertFalse(list(Path('Reports').glob('*.csv')))

    def test_default_report_name_includes_customer_without_timestamp(self):
        self.assertEqual(build_report_filename('xlsx', tenant_name='Customer A'), REPORT_STEM + ' - Customer A.xlsx')
        self.assertEqual(build_report_filename('xlsx', tenant_name='Tenant name', customer_name='Customer A'),
                         REPORT_STEM + ' - Customer A.xlsx')
        self.assertEqual(build_report_filename('xlsx', tenant_id='tenant-id'), REPORT_STEM + ' - tenant-id.xlsx')
        self.assertEqual(build_report_filename('xlsx'), REPORT_STEM + '.xlsx')
        path = Path(export_to_csv(self.rows, tenant_name='Customer A'))
        self.assertEqual(path, Path('Reports/Customer A') / (REPORT_STEM + ' - Customer A.csv'))
        self.assertNotIn('_', path.name)


if __name__ == '__main__':
    unittest.main()

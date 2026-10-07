"""Offline companion tools locate compact output trees and retain legacy inputs."""

import tempfile
import unittest
from pathlib import Path

from Core.export_paths import EVIDENCE_FOLDER
from tools.validate_offline_report import _companion_folders, _report_paths


class ExportToolPathTests(unittest.TestCase):

    def test_validation_companions_support_both_layouts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (EVIDENCE_FOLDER, 'old_evidence'):
                (root / name).mkdir()
            self.assertEqual(_companion_folders(root, EVIDENCE_FOLDER, '*_evidence'),
                             sorted([root / EVIDENCE_FOLDER, root / 'old_evidence']))

    def test_validation_reads_only_report_files_in_latest_receipt(self):
        package = Path('customer assessment')
        report = 'Builds/2/AI Readiness and M365 Hardening'
        receipt = {'run_id': '2', 'deliverables': [report + '.html', report + '.xlsx',
                   'Builds/2/Readiness Summary.html', 'Builds/2/Evidence/index.html']}
        self.assertEqual(_report_paths(package, receipt),
                         ([package / (report + '.html')], [package / 'Builds/2/Readiness Summary.html'],
                          [package / (report + '.xlsx')]))
        named_report = report + ' - Customer A'
        named_summary = 'Builds/2/Readiness Summary - Customer A.html'
        named = {'run_id': '2', 'deliverables': [named_report + '.html', named_report + '.xlsx', named_summary,
                                               'Builds/2/Technical Evidence - Customer A.xlsx']}
        self.assertEqual(_report_paths(package, named),
                         ([package / (named_report + '.html')], [package / named_summary],
                          [package / (named_report + '.xlsx')]))
        legacy = {'run_id': 'old', 'deliverables': ['deliverables/old/report.html',
                  'deliverables/old/report_summary.html', 'deliverables/old/report.xlsx']}
        self.assertEqual(_report_paths(package, legacy),
                         ([package / 'deliverables/old/report.html'], [package / 'deliverables/old/report_summary.html'],
                          [package / 'deliverables/old/report.xlsx']))


if __name__ == '__main__':
    unittest.main()

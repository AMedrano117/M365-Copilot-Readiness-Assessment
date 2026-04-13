import os
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from Core.export_recommendations import export_to_excel, export_to_html


class ExportRecommendationsTests(unittest.TestCase):
    def setUp(self):
        self.original_cwd = os.getcwd()
        self.tempdir = tempfile.TemporaryDirectory()
        os.chdir(self.tempdir.name)

    def tearDown(self):
        os.chdir(self.original_cwd)
        self.tempdir.cleanup()

    def _sample_recommendations(self):
        return [
            {
                "RecommendationId": "DEF-001",
                "Service": "Defender",
                "Feature": "Copilot Security Posture",
                "Status": "Warning",
                "Priority": "High",
                "Observation": "14 apps with high-level access were identified.",
                "Recommendation": "Review the flagged apps and security signals before rollout.",
                "LinkText": "Copilot Security",
                "LinkUrl": "https://example.com",
                "EvidenceAvailable": "Yes",
                "EvidenceSheet": "App Access Detail",
                "EvidenceSummary": "See App Access Detail for the flagged applications, reasons, and available activity.",
            }
        ]

    def _sample_evidence_bundle(self):
        return {
            "evidence_index": [
                {
                    "RecommendationId": "DEF-001",
                    "Service": "Defender",
                    "Feature": "Copilot Security Posture",
                    "Evidence Available": "Yes",
                    "Workbook Tab": "App Access Detail",
                    "Engineer Follow-Up": "See App Access Detail for the flagged applications, reasons, and available activity.",
                }
            ],
            "sheets": {
                "app_access_detail": {
                    "title": "App Access Detail",
                    "rows": [
                        {
                            "RecommendationId": "DEF-001",
                            "Flagged By": "Copilot Security Posture",
                            "App Display Name": "Risky App",
                            "Flagged Because": "High-privilege delegated permissions",
                            "Activity Band": "Unavailable",
                            "Last Activity": "",
                        }
                    ],
                }
            },
            "appendix_sections": [
                {
                    "key": "app_access_detail",
                    "title": "Appendix: Application Access Detail",
                    "workbook_tab": "App Access Detail",
                    "summary": "1 unique application was flagged.",
                    "details": [
                        "The workbook identifies which applications were reviewed.",
                        "Activity context was unavailable from the reviewed source data.",
                    ],
                    "preview_columns": ["App Display Name", "Flagged Because", "Activity Band"],
                    "preview_rows": [
                        {
                            "RecommendationId": "DEF-001",
                            "Flagged By": "Copilot Security Posture",
                            "App Display Name": "Risky App",
                            "Flagged Because": "High-privilege delegated permissions",
                            "Activity Band": "Unavailable",
                        }
                    ],
                }
            ],
        }

    def test_export_to_excel_creates_multi_sheet_evidence_workbook(self):
        recommendations = self._sample_recommendations()
        evidence_bundle = self._sample_evidence_bundle()

        excel_path = export_to_excel(
            recommendations,
            filename="tenant_report.xlsx",
            tenant_name="Contoso",
            evidence_bundle=evidence_bundle,
        )

        workbook = load_workbook(excel_path)
        self.assertEqual(workbook.sheetnames[:3], ["Recommendations", "Evidence Index", "App Access Detail"])
        self.assertEqual(len(workbook["Recommendations"].tables), 1)
        self.assertEqual(len(workbook["Evidence Index"].tables), 1)
        self.assertEqual(len(workbook["App Access Detail"].tables), 1)

        headers = [cell.value for cell in workbook["App Access Detail"][1]]
        self.assertIn("RecommendationId", headers)

    def test_export_to_html_renders_engineer_appendix_and_follow_up_reference(self):
        recommendations = self._sample_recommendations()
        evidence_bundle = self._sample_evidence_bundle()

        html_path = export_to_html(
            recommendations,
            filename="tenant_report.html",
            tenant_name="Contoso",
            evidence_bundle=evidence_bundle,
            excel_path=str(Path("Reports") / "tenant_report.xlsx"),
        )

        html = Path(html_path).read_text(encoding="utf-8")
        self.assertIn("Engineer Follow-Up Appendix", html)
        self.assertIn("App Access Detail", html)
        self.assertIn("DEF-001", html)
        self.assertIn("Review the flagged apps and security signals before rollout.", html)
        self.assertIn("Workbook tab: App Access Detail in tenant_report.xlsx", html)


if __name__ == "__main__":
    unittest.main()

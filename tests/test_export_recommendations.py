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
            },
            {
                "RecommendationId": "ENT-001",
                "Service": "Entra",
                "Feature": "Conditional Access",
                "Status": "Success",
                "Priority": "",
                "Observation": "Conditional Access requires MFA for administrative access.",
                "Recommendation": "",
                "EvidenceAvailable": "Yes",
                "EvidenceBasis": "Tenant evidence",
                "Disposition": "Assurance",
                "ImpactArea": "Identity & access",
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
        self.assertEqual(workbook.sheetnames[:5], [
            "Action Plan", "Evidence Index", "Collection Coverage", "App Access Detail", "Recommendations",
        ])
        self.assertEqual(len(workbook["Recommendations"].tables), 1)
        self.assertEqual(len(workbook["Evidence Index"].tables), 1)
        self.assertEqual(len(workbook["App Access Detail"].tables), 1)
        for worksheet in workbook.worksheets:
            if worksheet.tables:
                self.assertIsNone(worksheet.auto_filter.ref)

        action_headers = [cell.value for cell in workbook["Action Plan"][1]]
        self.assertEqual(action_headers, [
            "Priority", "What We Found", "Recommended Action",
            "Owner", "Target Date", "Completion Evidence",
        ])
        self.assertNotIn("Recommendation ID", action_headers)
        self.assertNotIn("Control ID", action_headers)

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
        action_plan = html.split('id="action-plan"', 1)[1].split("</section>", 1)[0]
        self.assertIn("What we found", action_plan)
        self.assertIn("What to do", action_plan)
        self.assertNotIn("Control ID", action_plan)
        self.assertNotIn("DEF-001", action_plan)
        self.assertIn("What the tenant is doing well", html)
        self.assertIn("What is working", html)
        self.assertIn("Why it helps AI readiness", html)
        self.assertNotIn("Run manifest and collection outcomes", html)
        self.assertIn("Engineer Follow-Up Appendix", html)
        self.assertIn('<details class="appendix-panel" id="engineer-appendix">', html)
        self.assertIn('<summary class="appendix-intro">', html)
        self.assertIn("App Access Detail", html)
        self.assertIn("DEF-001", html)
        self.assertIn("Review the flagged apps and security signals before rollout.", html)
        self.assertIn("Workbook tab: App Access Detail in tenant_report.xlsx", html)

    def test_export_to_html_explains_opportunity_and_external_ai_decision_effects(self):
        recommendations = self._sample_recommendations() + [
            {
                "RecommendationId": "M365-001",
                "Service": "M365",
                "Feature": "Teams pilot opportunity",
                "Status": "Insight",
                "Priority": "Low",
                "Observation": "Teams activity suggests a focused pilot population.",
                "Recommendation": "Run a measured pilot with a named business owner.",
            }
        ]

        html_path = export_to_html(
            recommendations,
            filename="decision_explanation.html",
            tenant_name="Contoso",
        )

        html = Path(html_path).read_text(encoding="utf-8")
        self.assertIn("Separate from security readiness", html)
        self.assertIn("They are\n          separate from security readiness", html)
        self.assertIn("Measured tenant signal", html)
        self.assertIn("Measurement and decision", html)
        self.assertIn("Three readiness conclusions", html)
        self.assertIn("Microsoft 365 foundation", html)
        self.assertIn("Provider and tier approval", html)
        self.assertIn("current provider register is required", html)
        self.assertIn("complete once per product and subscription tier", html)

    def test_html_surfaces_entra_license_data_scans_and_dlp_reference(self):
        evidence_bundle = self._sample_evidence_bundle()
        evidence_bundle.update({
            "entra_license_context": {
                "detected_tier": "Microsoft Entra ID P1",
                "rows": [{
                    "Capability": "Full Identity Protection risk data and risk-based Conditional Access",
                    "P1": "Not included", "P2": "Included", "Tenant": "Not included with detected P1",
                }],
            },
            "data_exposure": {"sources": {
                "sam": {"files_loaded": 0},
                "dspm": {"files_loaded": 1, "freshness": "fresh"},
            }},
            "purview_policy_summary": {
                "available": True, "total": 1, "enabled": 1,
                "rows": [{
                    "Policy": "Protect financial data", "Enabled": "Yes", "Mode": "Enable",
                    "Locations": "Exchange, SharePoint",
                }],
            },
        })

        html_path = export_to_html(
            self._sample_recommendations(), filename="license_and_data.html",
            tenant_name="Contoso", evidence_bundle=evidence_bundle,
        )
        html = Path(html_path).read_text(encoding="utf-8")

        self.assertIn("Detected tenant tier: Microsoft Entra ID P1", html)
        self.assertIn("Full Identity Protection risk data", html)
        self.assertIn("SharePoint Advanced Management Data Access Governance", html)
        self.assertIn("Check for a recent report", html)
        self.assertIn("Purview DSPM data-risk assessment", html)
        self.assertIn("Current report assessed", html)
        self.assertIn("DLP policy reference (1)", html)
        self.assertIn("Protect financial data", html)


if __name__ == "__main__":
    unittest.main()

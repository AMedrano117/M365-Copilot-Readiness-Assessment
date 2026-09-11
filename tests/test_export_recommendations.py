import os
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from openpyxl import load_workbook

from Core.export_recommendations import (
    export_to_excel,
    export_to_html,
    print_recommendations_summary,
)


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
        self.assertIn("What we verified", html)
        self.assertIn("Why it matters for AI", html)
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
        self.assertIn("They are\n          separate from security readiness", html)
        self.assertIn("Measured tenant signal", html)
        self.assertIn("Measurement and decision", html)
        self.assertNotIn("Three readiness conclusions", html)
        self.assertNotIn("Provider and tier approval", html)
        self.assertNotIn("complete once per product and subscription tier", html)

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
                "rules_available": True, "total_rules": 1, "enabled_rules": 1,
                "rows": [{
                    "Policy": "Protect financial data", "Enabled": "Yes", "Mode": "Enable",
                    "Locations": "Exchange, SharePoint", "Rules": 1,
                    "Protection behavior": "Block access, Notify users",
                }],
                "rule_rows": [{
                    "Policy": "Protect financial data", "Rule": "Block financial records",
                    "Enabled": "Yes", "Conditions": "Sensitive information",
                    "Actions": "Block access, Notify users", "Severity": "High",
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
        self.assertIn("DLP rule detail (1)", html)
        self.assertIn("Block financial records", html)

    def test_html_uses_curated_configured_strengths_and_excludes_license_entitlements(self):
        recommendations = self._sample_recommendations() + [{
            "Service": "Purview", "Feature": "Power Automate Free", "Status": "Success",
            "Priority": "", "Observation": "Power Automate Free is active in Power Automate Free.",
            "Recommendation": "", "Disposition": "Assurance", "EvidenceAvailable": "Yes",
            "EvidenceBasis": "Tenant evidence",
        }]
        evidence_bundle = self._sample_evidence_bundle()
        evidence_bundle["verified_strengths"] = [{
            "Area": "Data loss prevention",
            "Strength": "DLP policies and their protection rules are enabled.",
            "Evidence": "2 enabled policies and 4 enabled rules were returned.",
            "Benefit": "Configured restrictions can be applied when sensitive content is shared.",
        }]

        html_path = export_to_html(
            recommendations, filename="curated_strengths.html", tenant_name="Contoso",
            evidence_bundle=evidence_bundle,
        )
        html = Path(html_path).read_text(encoding="utf-8")

        self.assertIn("DLP policies and their protection rules are enabled", html)
        self.assertIn("2 enabled policies and 4 enabled rules", html)
        self.assertNotIn("Power Automate Free is active", html)

    def test_html_leads_with_tenant_specific_readiness_and_hides_empty_scope(self):
        evidence_bundle = self._sample_evidence_bundle()
        evidence_bundle.update({
            "verified_strengths": [{
                "Area": "Data loss prevention",
                "Strength": "DLP rules are enabled.",
                "Evidence": "Four enabled rules were returned.",
                "Benefit": "Configured restrictions can be applied.",
            }],
            "data_exposure": {"sources": {
                "sam": {"files_loaded": 0},
                "dspm": {"files_loaded": 0},
            }},
            "ai_usage": {"copilot_usage": {
                "selected_period": "D28",
                "periods": {"D28": {
                    "enabled_users": 6,
                    "active_users": 1,
                    "total_prompts": 85,
                }},
            }},
            "conclusions": {
                "provider_conclusion": "Not assessed",
                "use_case_conclusion": "Not assessed",
            },
        })

        html_path = export_to_html(
            self._sample_recommendations(), filename="customer_readout.html",
            tenant_name="Contoso", evidence_bundle=evidence_bundle,
        )
        html = Path(html_path).read_text(encoding="utf-8")

        self.assertNotIn("How to read this assessment", html)
        self.assertIn("Address priority safeguards before broad AI rollout", html)
        self.assertIn("1 verified safeguard", html)
        self.assertIn("1 priority improvement", html)
        self.assertIn("2 open evidence checks", html)
        self.assertIn("Immediate focus:", html)
        self.assertIn("14 apps with high-level access were identified", html)
        self.assertIn("Evidence still needed:", html)
        self.assertIn("SharePoint Data Access Governance", html)
        self.assertIn("Purview DSPM", html)
        self.assertIn("Active users — last 28 days", html)
        self.assertIn(">85</div>", html)
        self.assertNotIn("Provider and subscription-tier approval", html)
        self.assertNotIn("Three readiness conclusions", html)

    def test_html_shows_provider_and_use_case_review_only_when_records_are_supplied(self):
        evidence_bundle = self._sample_evidence_bundle()
        evidence_bundle["conclusions"] = {
            "provider_conclusion": "Approved",
            "use_case_conclusion": "Ready",
            "providers": [{
                "Provider": "Contoso AI", "Product": "Enterprise Chat", "Tier": "Business",
                "Approval Status": "Approved", "Reason": "Current review supplied.",
            }],
            "use_cases": [{
                "Use Case": "Proposal review", "Business Owner": "Sales Operations",
                "Provider": "Contoso AI", "Readiness": "Ready for controlled pilot",
                "Reason": "Required evidence is complete.",
            }],
        }

        html_path = export_to_html(
            self._sample_recommendations(), filename="provider_scope.html",
            tenant_name="Contoso", evidence_bundle=evidence_bundle,
        )
        html = Path(html_path).read_text(encoding="utf-8")

        self.assertIn("AI products and use cases supplied for this assessment", html)
        self.assertIn("Contoso AI", html)
        self.assertIn("Proposal review", html)

    def test_html_translates_sharepoint_settings_into_customer_language(self):
        evidence_bundle = self._sample_evidence_bundle()
        evidence_bundle["sharepoint_governance"] = {
            "available": True,
            "site_count": 12,
            "anyone_site_count": 3,
            "settings": {
                "SharingCapability": "ExternalUserAndGuestSharing",
                "OneDriveSharingCapability": "ExternalUserSharingOnly",
                "DefaultSharingLinkType": "AnonymousAccess",
                "RequireAnonymousLinksExpireInDays": 0,
                "ExternalUserExpirationRequired": False,
                "LegacyAuthProtocolsEnabled": True,
            },
        }

        html_path = export_to_html(
            self._sample_recommendations(), filename="friendly_sharepoint.html",
            tenant_name="Contoso", evidence_bundle=evidence_bundle,
        )
        html = Path(html_path).read_text(encoding="utf-8")

        self.assertIn("Anyone links, new guests, and existing guests", html)
        self.assertIn("New and existing guests; no Anyone links", html)
        self.assertIn("<td>Anyone link</td>", html)
        self.assertIn("<td>Not enforced</td>", html)
        self.assertNotIn("ExternalUserAndGuestSharing", html)
        self.assertNotIn("AnonymousAccess", html)

    def test_console_summary_uses_same_curated_strength_count_as_report(self):
        output = io.StringIO()
        with redirect_stdout(output):
            print_recommendations_summary(
                self._sample_recommendations(),
                tenant_name="Contoso",
                evidence_bundle={"verified_strengths": [{}, {}, {}]},
            )

        self.assertIn("Verified strengths: 3", output.getvalue())


if __name__ == "__main__":
    unittest.main()

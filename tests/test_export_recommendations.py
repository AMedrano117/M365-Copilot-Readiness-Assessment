import os
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from html import escape
from datetime import date
import re
from urllib.parse import unquote


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
                "TenantId": "11111111-1111-1111-1111-111111111111",
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
                "TenantId": "11111111-1111-1111-1111-111111111111",
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
            "evaluation_date": "2026-09-15",
            "collection_context": {"collected_at": "2026-09-14T12:00:00Z", "tenant_id": "example-tenant", "mode": "offline"},
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
        bundle = self._sample_evidence_bundle()
        excel_path = export_to_excel(recommendations, filename="tenant_report.xlsx", tenant_name="Contoso", evidence_bundle=bundle)
        workbook = load_workbook(excel_path)
        self.addCleanup(workbook.close)
        self.assertEqual(workbook.sheetnames[:3], ["Action Plan", "Evidence Index", "Collection Coverage"])
        for title in ("Recommendations", "Evidence Index", "App Access Detail", "Assessment Summary", "Domain Results", "Evidence Observations"):
            self.assertIn(title, workbook.sheetnames)
            self.assertEqual(len(workbook[title].tables), 1)
        for worksheet in workbook.worksheets:
            if worksheet.tables:
                self.assertIsNone(worksheet.auto_filter.ref)
        rows = list(workbook["Action Plan"].values)
        actions = [dict(zip(rows[0], row)) for row in rows[1:]]
        self.assertEqual([row["RecommendationId"] for row in actions], [row["RecommendationId"] for row in bundle["assessment_result"]["actions"]])
        self.assertTrue(all(row["Responsible Role"] and row["Rollout Stage"] and row["Completion Evidence"] for row in actions))
        self.assertTrue(all(row["Target Date"] is None for row in actions))
        evidence_column = rows[0].index("Evidence") + 1
        self.assertTrue(all(workbook["Action Plan"].cell(i, evidence_column).value for i in range(2, len(rows)+1)))
        self.assertFalse(any(workbook["Action Plan"].cell(i, evidence_column).value == 'Evidence Index' for i in range(2, len(rows)+1)))
        headers = [cell.value for cell in workbook["App Access Detail"][1]]
        self.assertIn("RecommendationId", headers)

    def test_investigation_links_select_only_flagged_objects_and_keep_registers_readable(self):
        recommendations = self._sample_recommendations()
        recommendations[0].update(
            EvidenceKey="app_access_detail", EvidenceBasis="Tenant evidence",
            ObservationDate="2026-09-14", EvidenceComplete=True,
            EvidenceScope="Returned enterprise applications and delegated grants",
            Observation="One application has high-privilege delegated permissions.",
        )
        bundle = self._sample_evidence_bundle()
        records = bundle['sheets']['app_access_detail']['rows']
        records[0]['Enterprise Application Object ID'] = 'flagged-app-object'
        records.append({**records[0], 'App Display Name': 'Healthy application',
                        'Enterprise Application Object ID': 'healthy-app-object', 'Flagged Because': ''})
        path = export_to_excel(recommendations, filename='focused.xlsx', evidence_bundle=bundle)
        workbook = load_workbook(path)
        self.addCleanup(workbook.close)
        rows = list(workbook['Investigation Items'].values)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[0]), 12)
        self.assertIn('flagged-app-object', rows[1])
        self.assertNotIn('healthy-app-object', rows[1])
        for title, column_name in (('Action Plan', 'Investigation Details'),
                                   ('Evidence Index', 'Workbook Tab'), ('Recommendations', 'Evidence Sheet')):
            sheet = workbook[title]
            headers = {cell.value: cell.column for cell in sheet[1]}
            number = next(i for i in range(2, sheet.max_row + 1)
                          if sheet.cell(i, headers['RecommendationId']).value == 'DEF-001')
            cell = sheet.cell(number, headers[column_name])
            self.assertEqual(cell.hyperlink.target, "#'Investigation Items'!A2:L2")
            if title == 'Action Plan':
                self.assertEqual(cell.value, '1 item to review')
                self.assertIsNotNone(cell.comment)
                self.assertIn('distinct applications', cell.comment.text)
                self.assertIn('grant instances', cell.comment.text)
                self.assertLessEqual(len(headers), 15)
                self.assertEqual(sheet.cell(number, headers['Evidence']).hyperlink.target, "#'Investigation Items'!A2:L2")
            self.assertNotIn('Evidence IDs', headers)
            self.assertNotIn('Evidence Reconciliation', headers)
        self.assertNotIn('Evidence Reconciliation', workbook.sheetnames)
        html_path = export_to_html(recommendations, filename='focused.html', evidence_bundle=bundle, excel_path=path)
        html = Path(html_path).read_text(encoding='utf-8')
        self.assertIn('Investigation Items', html)
        action_plan = html.split('id="action-plan"', 1)[1].split('</section>', 1)[0]
        investigation_link = re.search(r'href="([^"]+)"[^>]*>1 item to review in workbook</a>', action_plan)
        self.assertIsNotNone(investigation_link)
        self.assertEqual(unquote(investigation_link.group(1)), "focused.xlsx#'Investigation Items'!A2:L2")
        self.assertNotIn('flagged-app-object', html)
        self.assertNotIn('healthy-app-object', html)
        self.assertNotIn('Risky App', action_plan)
        without_workbook = export_to_html(recommendations, filename='focused_without_workbook.html', evidence_bundle=bundle)
        self.assertNotIn('item to review in workbook</a>', Path(without_workbook).read_text(encoding='utf-8'))
        self.assertFalse(list(Path('Reports').glob('*_evidence')))

    def test_missing_items_have_visible_status_and_explanation_without_an_invented_worklist(self):
        bundle = self._sample_evidence_bundle()
        bundle['sheets']['app_access_detail']['rows'][0]['Flagged Because'] = ''
        recommendations = self._sample_recommendations()
        recommendations[0]['EvidenceKey'] = 'app_access_detail'
        path = export_to_excel(recommendations, filename='no_specific_items.xlsx', evidence_bundle=bundle)
        workbook = load_workbook(path)
        self.addCleanup(workbook.close)
        self.assertNotIn('Investigation Items', workbook.sheetnames)
        headers = {cell.value: cell.column for cell in workbook['Action Plan'][1]}
        for row in range(2, workbook['Action Plan'].max_row + 1):
            self.assertTrue(workbook['Action Plan'].cell(row, headers['Investigation Details']).value)
            self.assertTrue(workbook['Action Plan'].cell(row, headers['Qualification']).value)
            self.assertTrue(workbook['Action Plan'].cell(row, headers['Evidence']).value)
            self.assertNotEqual(workbook['Action Plan'].cell(row, headers['Evidence']).value, 'Evidence Index')
            if workbook['Action Plan'].cell(row, headers['RecommendationId']).value == 'DEF-001':
                comment = workbook['Action Plan'].cell(row, headers['Investigation Details']).comment
                self.assertIsNotNone(comment)
                self.assertIn('No specific affected items', comment.text)

    def test_oversized_optional_text_keeps_excel_and_detail_identifiers_are_preserved(self):
        recommendations = self._sample_recommendations()
        recommendations[0]['Observation'] = 'Long optional finding text. ' * 1800
        recommendations[0]['EvidenceKey'] = 'app_access_detail'
        bundle = self._sample_evidence_bundle()
        identifier = 'identifier-' + 'x' * 40000
        bundle['sheets']['app_access_detail']['rows'][0]['Enterprise Application Object ID'] = identifier
        path = export_to_excel(recommendations, filename='long_fields.xlsx', evidence_bundle=bundle)
        self.assertTrue(Path(path).is_file())
        workbook = load_workbook(path)
        self.addCleanup(workbook.close)
        headers = {cell.value: cell.column for cell in workbook['Recommendations'][1]}
        observation = workbook['Recommendations'].cell(2, headers['Observation']).value
        self.assertIn('[Truncated for Excel:', observation)
        self.assertLessEqual(len(observation.encode('utf-16-le')) // 2, 32767)
        for title, field in (('App Access Detail', 'Enterprise Application Object ID'),
                             ('Investigation Items', 'Identifier / URL')):
            rows = list(workbook[title].values)
            detail = dict(zip(rows[0], rows[1]))
            self.assertEqual(detail[field] + detail[field + ' (continued 2)'], identifier)
            self.assertIn('Full values continue', detail['Excel Export Note'])

    def test_export_to_html_renders_engineer_appendix_and_follow_up_reference(self):
        recommendations = self._sample_recommendations()
        evidence_bundle = self._sample_evidence_bundle()
        html_path = export_to_html(recommendations, filename="tenant_report.html", tenant_name="Contoso",
            evidence_bundle=evidence_bundle, excel_path=str(Path("Reports") / "tenant_report.xlsx"))
        html = Path(html_path).read_text(encoding="utf-8")
        action_plan = html.split('id="action-plan"', 1)[1].split("</section>", 1)[0]
        self.assertIn("What we found", action_plan)
        self.assertIn("What to do", action_plan)
        self.assertNotIn("Control ID", action_plan)
        self.assertNotIn("DEF-001", action_plan)
        self.assertIn("Observed strengths", html)
        self.assertIn("Why it matters", html)
        self.assertIn("Technical appendix and evidence workbook", html)
        self.assertIn('<details class="appendix-panel" id="engineer-appendix">', html)
        self.assertIn('href="tenant_report.xlsx"', html)
        appendix = html.split('id="engineer-appendix"', 1)[1]
        self.assertIn("Detail mapping missing", appendix)
        self.assertIn("DEF-001", appendix)
        self.assertIn("Review the flagged apps and security signals before rollout.", html)
        for anchor in re.findall(r'href="#(evidence-[^"]+)"', html):
            self.assertIn('id="' + anchor + '"', appendix)
        self.assertNotIn("Run manifest and collection outcomes", html)

    def test_export_to_html_explains_opportunity_and_external_ai_decision_effects(self):
        recommendations = self._sample_recommendations() + [
            {
                "RecommendationId": "M365-001",
                "Service": "M365",
                "Feature": "Teams pilot opportunity",
                "FindingKey": "adoption.reviewed_teams_pilot_opportunity",
                "Disposition": "Opportunity",
                "EvidenceBasis": "Reviewed pilot opportunity",
                "EvidenceKey": "m365_activity_detail",
                "ObservationDate": date.today().isoformat(),
                "EvidenceScope": "Reviewed 20-user Teams pilot cohort",
                "TenantId": "11111111-1111-1111-1111-111111111111",
                "EvidenceComplete": True,
                "SourceType": "reviewed_workshop",
                "SourceFile": "pilot-review.json",
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
        self.assertIn("Adoption opportunities guide the value of a pilot", html)
        self.assertIn("They do not establish that security controls are effective", html)
        self.assertIn("Teams activity suggests a focused pilot population", html)
        self.assertIn("How to decide whether to expand", html)
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
                "dspm": {"files_loaded": 1, "freshness": "fresh", "reports": [{
                    "source_file": "dspm-assessment.csv", "report_type": "dspm_assessment",
                    "report_date": "2026-09-14", "status": "selected", "records_read": 1,
                    "freshness": "fresh",
                }]},
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

        evidence_bundle["sheets"]["purview_policy_detail"] = {
            "title": "Purview Policy Detail", "rows": [
                {"Detail Type": "DLP policy", **evidence_bundle["purview_policy_summary"]["rows"][0]},
                {"Detail Type": "DLP rule", **evidence_bundle["purview_policy_summary"]["rule_rows"][0]},
            ],
        }
        html_path = export_to_html(
            self._sample_recommendations(), filename="license_and_data.html",
            tenant_name="Contoso", evidence_bundle=evidence_bundle,
        )
        html = Path(html_path).read_text(encoding="utf-8")

        self.assertIn("Microsoft Entra ID P1", html)
        self.assertIn("Full Identity Protection risk data", html)
        self.assertIn("Confirm tenant sharing defaults", html)
        self.assertIn("dspm-assessment.csv", html)
        self.assertIn("2026-09-14", html)
        self.assertIn("selected", html)
        self.assertNotIn("Current report assessed", html)
        self.assertIn("Confirm data loss prevention coverage and enforcement", html)
        path = export_to_excel(self._sample_recommendations(), filename="license_and_data.xlsx", evidence_bundle=evidence_bundle)
        workbook = load_workbook(path)
        self.addCleanup(workbook.close)
        cells = [str(cell.value or "") for row in workbook["Purview Policy Detail"] for cell in row]
        self.assertIn("Protect financial data", cells)
        self.assertIn("Block financial records", cells)
        self.assertIn("Block access, Notify users", cells)

    def test_html_uses_curated_configured_strengths_and_excludes_license_entitlements(self):
        recommendations = self._sample_recommendations() + [{
            "Service": "Purview", "Feature": "Power Automate Free", "Status": "Success",
            "Priority": "", "Observation": "Power Automate Free is active in Power Automate Free.",
            "Recommendation": "", "Disposition": "Assurance", "EvidenceAvailable": "Yes",
            "EvidenceBasis": "Tenant evidence",
        }]
        evidence_bundle = self._sample_evidence_bundle()
        evidence_bundle["verified_strengths"] = [{
            "Area": "Data loss prevention", "TenantId": "11111111-1111-1111-1111-111111111111",
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
        path = export_to_excel(recommendations, filename="curated_strengths.xlsx", evidence_bundle=evidence_bundle)
        workbook = load_workbook(path)
        self.addCleanup(workbook.close)
        cells = [str(cell.value or "") for sheet in workbook for row in sheet for cell in row]
        self.assertTrue(any("2 enabled policies and 4 enabled rules" in value for value in cells))
        self.assertNotIn("Power Automate Free is active", html)

    def test_html_leads_with_tenant_specific_readiness_and_hides_empty_scope(self):
        evidence_bundle = self._sample_evidence_bundle()
        evidence_bundle.update({
            "verified_strengths": [{
                "Area": "Data loss prevention", "TenantId": "11111111-1111-1111-1111-111111111111",
                "Strength": "DLP rules are enabled.",
                "Evidence": "Four enabled rules were returned.",
                "Benefit": "Configured restrictions can be applied.",
            }],
            "data_exposure": {"sources": {
                "sam": {"files_loaded": 0},
                "dspm": {"files_loaded": 0},
            }},
            "ai_usage": {"copilot_usage": {
                "available": True, "refresh_date": "2026-09-14", "availability_status": "available",
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

        result = evidence_bundle["assessment_result"]
        executive = html.split('id="executive"', 1)[1].split("</section>", 1)[0]
        from Core.pilot_summary import VERDICTS
        self.assertIn(escape(VERDICTS[result["decision"]][0]), executive)
        self.assertIn("First actions", executive)
        self.assertIn("14 apps with high-level access were identified", html)
        self.assertNotIn("Built offline", executive)
        self.assertNotIn("source_file", executive)
        self.assertNotIn("Not established to Not established", html)
        for key, label in (("remediation", "Remediation actions"), ("confirmation", "Findings to confirm"), ("evidence_gaps", "Evidence checks"), ("strengths", "Verified strengths")):
            self.assertIn(f'<div class="value">{result["counts"][key]}</div><div class="stat-label">{label}</div>', executive)
        self.assertIn(f'>{len(result["actions"])} actions</span>', html)
        self.assertEqual(len(re.findall(r'<article class="action" ', html)), len(result["actions"]))
        self.assertIn("Active users", html)
        self.assertIn("85", html)
        self.assertNotIn('id="domain-external_ai"', html)
        self.assertNotIn("Three readiness conclusions", html)

    def test_html_shows_provider_and_use_case_review_only_when_records_are_supplied(self):
        evidence_bundle = self._sample_evidence_bundle()
        evidence_bundle["assessment_profile"] = {"scope": {"external_ai": True}}
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

        self.assertIn('id="domain-external_ai"', html)
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
        self.assertIn("<td>Disabled</td>", html)
        self.assertNotIn("ExternalUserAndGuestSharing", html)
        self.assertNotIn("AnonymousAccess", html)

    def test_console_summary_uses_same_curated_strength_count_as_report(self):
        output = io.StringIO()
        bundle = self._sample_evidence_bundle()
        bundle["verified_strengths"] = [{"Area": "Data loss prevention", "TenantId": "11111111-1111-1111-1111-111111111111", "Strength": "DLP rules are enabled.", "Evidence": "Four rules were returned."}]
        recommendations = self._sample_recommendations()
        html = Path(export_to_html(recommendations, evidence_bundle=bundle)).read_text(encoding="utf-8")
        expected = bundle["assessment_result"]["counts"]["strengths"]
        self.assertGreater(expected, 0)
        with redirect_stdout(output):
            print_recommendations_summary(recommendations, tenant_name="Contoso", evidence_bundle=bundle)
        self.assertIn(f"Verified strengths: {expected}", output.getvalue())
        self.assertIn(f'<div class="value">{expected}</div><div class="stat-label">Verified strengths</div>', html)

    def test_zero_count_configuration_context_has_an_exact_html_link(self):
        recommendations = self._sample_recommendations()[:1]
        recommendations[0].update(Service='Entra', Feature='Conditional Access scope review',
                                  EvidenceKey='conditional_access_detail', EvidenceBasis='Tenant evidence',
                                  Observation='Confirm whether the retained configuration covers the intended scope.',
                                  Recommendation='Confirm the policy scope with the identity owner.')
        bundle = self._sample_evidence_bundle()
        bundle['sheets'] = {'conditional_access_detail': {'title': 'Conditional Access Detail', 'rows': [
            {'Policy ID': 'private-policy-id', 'Policy Name': 'Private policy', 'State': 'enabled'},
        ]}}
        workbook_path = export_to_excel(recommendations, filename='context.xlsx', evidence_bundle=bundle)
        result_row = next(row for row in bundle['assessment_result']['recommendations'] if row['RecommendationId'] == 'DEF-001')
        self.assertEqual(result_row['InvestigationCount'], 0)
        self.assertTrue(result_row['InvestigationRange'])
        html = Path(export_to_html(recommendations, filename='context.html', evidence_bundle=bundle,
                                  excel_path=workbook_path)).read_text(encoding='utf-8')
        self.assertIn('context.xlsx#' + result_row['InvestigationRange'], unquote(html))
        self.assertIn(result_row['InvestigationSummary'] + ' in workbook</a>', html)
        self.assertNotIn('private-policy-id', html)
        self.assertNotIn('Private policy', html)

    def test_opportunity_next_step_links_declared_records_without_html_identity_or_source_filter(self):
        rec = {**self._sample_recommendations()[0], 'Service': 'Fictional Observatory', 'DomainId': 'applications', 'Feature': 'Optional resource pilot',
               'Disposition': 'Opportunity', 'Status': 'Success', 'EvidenceBasis': 'Tenant evidence',
               'Observation': 'Two retained resources are available to assess for an optional pilot.',
               'Recommendation': 'Review optional pilot resources with their accountable owner.',
               'InvestigationEvidence': {'kind': 'records', 'sheet_name': 'Optional Resources',
                   'records': [{'id': 'private-resource-id', 'name': 'Private resource', 'state': 'Stopped'}],
                   'source': {'file': 'private-source-file.json', 'filter': 'ownerId=private-filter-identity',
                              'scope': 'Supplied resource records', 'complete': True, 'retention': '30 days'},
                   'reconciliation': {'operation': 'count', 'expected': 1}}}
        bundle = self._sample_evidence_bundle()
        bundle['sheets'] = {}
        workbook_path = export_to_excel([rec], filename='opportunity.xlsx', evidence_bundle=bundle)
        workbook = load_workbook(workbook_path)
        self.addCleanup(workbook.close)
        self.assertIn('Optional Resources', workbook.sheetnames)
        self.assertNotIn('DEF-001', {row['RecommendationId'] for row in bundle['assessment_result']['actions']})
        for title in ('Evidence Index', 'Recommendations'):
            sheet = workbook[title]
            headers = {cell.value: cell.column for cell in sheet[1]}
            row = next(i for i in range(2, sheet.max_row + 1) if sheet.cell(i, headers['RecommendationId']).value == 'DEF-001')
            self.assertEqual(sheet.cell(row, headers['Investigation Status']).value, 'Records available')
            self.assertEqual(sheet.cell(row, headers['Supporting Records']).value, 1)
            self.assertTrue(sheet.cell(row, headers['Supporting Records']).hyperlink)
        coverage_values = str(list(workbook['Collection Coverage'].values))
        self.assertIn('Optional Resources', coverage_values)
        self.assertIn('Retention: 30 days', coverage_values)
        html = Path(export_to_html([rec], filename='opportunity.html', evidence_bundle=bundle,
                                  excel_path=workbook_path)).read_text(encoding='utf-8')
        self.assertIn('Review optional pilot resources with their accountable owner.', html)
        self.assertIn('1 item to review in workbook</a>', html)
        for private_value in ('private-resource-id', 'Private resource', 'private-source-file.json', 'private-filter-identity'):
            self.assertNotIn(private_value, html)

    def test_investigation_item_source_link_opens_the_application_grant_records(self):
        recommendations = self._sample_recommendations()
        recommendations[0]['EvidenceKey'] = 'app_access_detail'
        bundle = self._sample_evidence_bundle()
        bundle['sheets']['app_access_detail']['rows'][0]['Enterprise Application Object ID'] = 'app-object'
        bundle['sheets']['application_grant_detail'] = {
            'title': 'Application Grant Detail', 'restricted': True,
            'rows': [{'RecommendationId': '', 'Client Service Principal ID': 'app-object', 'Grant ID': 'grant-one', 'Scope': 'Mail.Read'},
                     {'RecommendationId': '', 'Client Service Principal ID': 'app-object', 'Grant ID': 'grant-two', 'Scope': 'Files.Read.All'}],
            'app_ranges': {'app-object': "'Application Grant Detail'!A2:D3"},
            'details': ['Each row is a retained grant; multiple grants can identify one application.'],
        }
        workbook_path = export_to_excel(recommendations, filename='grant-links.xlsx', evidence_bundle=bundle)
        workbook = load_workbook(workbook_path)
        self.addCleanup(workbook.close)
        sheet = workbook['Investigation Items']
        headers = {cell.value: cell.column for cell in sheet[1]}
        source = sheet.cell(2, headers['Source Detail'])
        self.assertEqual(source.value, "'Application Grant Detail'!A2:D3")
        self.assertEqual(source.hyperlink.target, "#'Application Grant Detail'!A2:D3")


if __name__ == "__main__":
    unittest.main()

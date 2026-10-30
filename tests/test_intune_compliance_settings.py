"""Fictional singleton collection and tenant configuration projections."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx

from provenance_fixture import fictional_publication
from test_retained_sources import empty_client
from test_device_compliance_assignment import assignment_bundle
from test_evidence_reconciliation import dataset, DAY, TENANT
from Core.expanded_collection import DEVICE_COMPLIANCE_SETTINGS_PATH, collect_entra_details
from Core.get_entra_client import _fetch_graph_detail_via_http
from Core.assessment_catalog import collect_assessment_sources
from Core.collector_registry import collector_permissions
from Core.intune_compliance_settings import tenant_compliance_settings
from Core.device_compliance import build_device_compliance
from Core.expanded_findings import expanded_findings
from Core.assessment_result import build_assessment_result


def config_bundle(settings=None, **metadata):
    bundle = assignment_bundle()
    raw = {'settings': settings if settings is not None else {'secureByDefault': False, 'deviceComplianceCheckinThresholdDays': 45}}
    bundle['assessment_sources']['device_compliance_settings'] = [dataset([raw], **metadata)]
    return bundle


class ComplianceSettingsCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_zero_is_retained_as_partial_without_retry_or_default(self):
        calls = []
        async def fetch(path):
            calls.append(path)
            return {'available': True, 'availability_status': 'available',
                    'object': {'secureByDefault': False, 'deviceComplianceCheckinThresholdDays': 0}}
        with patch('Core.get_entra_client._fetch_graph_object_via_http', side_effect=fetch):
            source = await _fetch_graph_detail_via_http(DEVICE_COMPLIANCE_SETTINGS_PATH)
        self.assertEqual(calls, ['/v1.0/deviceManagement/settings'])
        self.assertEqual(source['availability_status'], 'partial')
        self.assertIs(source['value'][0]['secureByDefault'], False)
        self.assertEqual(source['value'][0]['deviceComplianceCheckinThresholdDays'], 0)
        bundle = {'assessment_sources': {'device_compliance_settings': [
            {'source': source, 'records': source['value']}]}}
        fields = tenant_compliance_settings(bundle)['fields']
        self.assertIs(fields['secureByDefault']['value'], False)
        self.assertEqual(fields['deviceComplianceCheckinThresholdDays']['raw_values'], [0])
        self.assertIsNone(fields['deviceComplianceCheckinThresholdDays']['value'])

    async def test_direct_settings_returns_flat_values_without_parent_projection(self):
        calls = []
        def respond(request):
            if request.url.path == '/v1.0/deviceManagement/settings':
                calls.append(str(request.url))
                return httpx.Response(200, json={'secureByDefault': False, 'deviceComplianceCheckinThresholdDays': 45})
            return httpx.Response(200, json={'value': []})
        async def factory():
            return httpx.AsyncClient(base_url='https://graph.microsoft.com', transport=httpx.MockTransport(respond))
        client = empty_client()
        with patch('Core.get_entra_client._get_graph_http_client', side_effect=factory):
            await collect_entra_details(client, _fetch_graph_detail_via_http, preview_collectors='none')
        self.assertEqual(len(calls), 1)
        saved = client.assessment_datasets['device_compliance_settings'][0]
        self.assertIs(saved['records'][0]['secureByDefault'], False)
        self.assertTrue(saved['source']['complete'])
        self.assertEqual(saved['source']['source_api'], 'https://graph.microsoft.com/v1.0/deviceManagement/settings')
        model = tenant_compliance_settings({'assessment_sources': collect_assessment_sources([client])})
        self.assertIs(model['fields']['secureByDefault']['value'], False)
        self.assertEqual(model['fields']['deviceComplianceCheckinThresholdDays']['value'], 45)

    async def test_null_success_is_partial_not_complete_and_does_not_invent_defaults(self):
        calls = []
        def respond(request):
            if request.url.path == '/v1.0/deviceManagement/settings':
                calls.append(request)
                return httpx.Response(200, json={'settings': None})
            return httpx.Response(200, json={'value': []})
        async def factory():
            return httpx.AsyncClient(base_url='https://graph.microsoft.com', transport=httpx.MockTransport(respond))
        client = empty_client()
        with patch('Core.get_entra_client._get_graph_http_client', side_effect=factory):
            await collect_entra_details(client, _fetch_graph_detail_via_http, preview_collectors='none')
        saved = client.assessment_datasets['device_compliance_settings'][0]
        self.assertEqual(len(calls), 1)
        self.assertFalse(saved['source']['complete'])
        self.assertEqual(saved['source']['availability_status'], 'partial')
        self.assertIsNone(saved['records'][0]['settings'])
        model = tenant_compliance_settings({'assessment_sources': collect_assessment_sources([client])})
        self.assertTrue(all(field['value'] is None for field in model['fields'].values()))

    async def test_singleton_get_preserves_nested_legacy_values_and_source(self):
        calls = []
        def respond(request):
            calls.append(request)
            if request.url.path == '/v1.0/deviceManagement/settings':
                return httpx.Response(200, json={'settings': {'secureByDefault': False, 'deviceComplianceCheckinThresholdDays': 45}})
            return httpx.Response(200, json={'value': []})
        async def client_factory():
            return httpx.AsyncClient(base_url='https://graph.microsoft.com', transport=httpx.MockTransport(respond))
        client = empty_client()
        with patch('Core.get_entra_client._get_graph_http_client', side_effect=client_factory):
            await collect_entra_details(client, _fetch_graph_detail_via_http, preview_collectors='none')
        requests = [request for request in calls if request.url.path == '/v1.0/deviceManagement/settings']
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].method, 'GET')
        self.assertNotIn('$select', requests[0].url.params)
        source = client.assessment_datasets['device_compliance_settings'][0]
        self.assertIs(source['records'][0]['settings']['secureByDefault'], False)
        self.assertEqual(source['records'][0]['settings']['deviceComplianceCheckinThresholdDays'], 45)
        self.assertTrue(source['source']['complete'])
        self.assertEqual(source['source']['scope'], 'Tenant-wide Intune compliance settings')
        self.assertEqual(source['source']['source_api'], 'https://graph.microsoft.com'+DEVICE_COMPLIANCE_SETTINGS_PATH)
        retained = collect_assessment_sources([client])
        self.assertEqual(retained['device_compliance_settings'][0], source)

    async def test_denied_settings_do_not_interrupt_other_sources_or_assume_defaults(self):
        def respond(request):
            if request.url.path == '/v1.0/deviceManagement/settings':
                return httpx.Response(403, json={'error': {'code': 'Forbidden', 'message': 'Fictional denied read'}})
            return httpx.Response(200, json={'value': []})
        async def client_factory():
            return httpx.AsyncClient(base_url='https://graph.microsoft.com', transport=httpx.MockTransport(respond))
        client = empty_client()
        with patch('Core.get_entra_client._get_graph_http_client', side_effect=client_factory):
            await collect_entra_details(client, _fetch_graph_detail_via_http, preview_collectors='none')
        source = client.assessment_datasets['device_compliance_settings'][0]
        self.assertEqual(source['records'], [])
        self.assertEqual(source['source']['status_code'], 403)
        self.assertFalse(source['source']['complete'])
        self.assertTrue(client.assessment_datasets['compliance_setting_summaries'][0]['source']['complete'])
        model = tenant_compliance_settings({'assessment_sources': collect_assessment_sources([client])})
        self.assertTrue(all(field['display'] == 'Unknown' for field in model['fields'].values()))


class ComplianceSettingsTests(unittest.TestCase):
    def test_zero_does_not_establish_inactivity_or_a_default_period(self):
        bundle = config_bundle({'secureByDefault': False, 'deviceComplianceCheckinThresholdDays': 0})
        before = deepcopy(bundle)
        model = build_device_compliance(bundle, evaluation_date=DAY)
        field = model['tenant_settings']['fields']['deviceComplianceCheckinThresholdDays']
        self.assertEqual(field['display'], 'API returned 0; effective period unverified')
        self.assertIsNone(field['value'])
        self.assertEqual(field['raw_values'], [0])
        self.assertIsNone(model['summary']['validity_days'])
        self.assertFalse(any(device.get('compliance_activity') == 'overdue' for device in model['devices']))
        self.assertEqual(bundle, before)

    def test_existing_setup_permission_covers_both_permission_profiles(self):
        for profile in ('standard', 'restricted'):
            self.assertIn('DeviceManagementConfiguration.Read.All', collector_permissions('entra_controls', profile))

    def test_boolean_semantics_and_validity_period_are_independent(self):
        for value, display in ((False, 'Compliant'), (True, 'Noncompliant')):
            with self.subTest(value=value):
                bundle = config_bundle({'secureByDefault': value, 'deviceComplianceCheckinThresholdDays': 45})
                before = deepcopy(bundle)
                model = build_device_compliance(bundle, evaluation_date=DAY)
                self.assertEqual(model['tenant_settings']['fields']['secureByDefault']['display'], display)
                self.assertEqual(model['tenant_settings']['fields']['deviceComplianceCheckinThresholdDays']['value'], 45)
                self.assertEqual(model['reporting_days'], 7)
                self.assertEqual(model['activity_days'], 30)
                self.assertEqual(bundle, before)

    def test_missing_fields_and_old_collections_do_not_assume_false_or_thirty_days(self):
        for bundle in ({}, config_bundle({}), config_bundle({'secureByDefault': None})):
            fields = tenant_compliance_settings(bundle)['fields']
            self.assertEqual(fields['secureByDefault']['display'], 'Unknown')
            self.assertIsNone(fields['deviceComplianceCheckinThresholdDays']['value'])

    def test_conflicting_values_are_preserved_as_conflicts(self):
        bundle = config_bundle()
        bundle['assessment_sources']['device_compliance_settings'].append(dataset([{
            'settings': {'secureByDefault': True, 'deviceComplianceCheckinThresholdDays': 30}}]))
        fields = tenant_compliance_settings(bundle)['fields']
        self.assertTrue(all(field['state'] == 'conflict' for field in fields.values()))
        rows = [row for row in expanded_findings(bundle, evaluation_date=DAY) if row.get('ConfigurationSetting')]
        self.assertTrue(all(row['Disposition'] == 'Coverage' and row['ConfigurationValue'] == 'Conflicting retained values' for row in rows))

    def test_invalid_periods_remain_unknown_and_valid_boundaries_survive(self):
        for period in (0, 121, 1024, False, '45', None):
            self.assertIsNone(tenant_compliance_settings(config_bundle({'deviceComplianceCheckinThresholdDays': period}))['fields']['deviceComplianceCheckinThresholdDays']['value'])
        for period in (1, 120):
            self.assertEqual(tenant_compliance_settings(config_bundle({'deviceComplianceCheckinThresholdDays': period}))['fields']['deviceComplianceCheckinThresholdDays']['value'], period)

    def test_unavailable_envelope_does_not_establish_effective_values(self):
        fields = tenant_compliance_settings(config_bundle(available=False, availability_status='unavailable'))['fields']
        self.assertTrue(all(field['state'] == 'unknown' for field in fields.values()))

    def test_configuration_does_not_prove_device_assignment_or_security_health(self):
        bundle = config_bundle()
        bundle['assessment_sources'].pop('compliance_setting_states')
        model = build_device_compliance(bundle, evaluation_date=DAY)
        self.assertEqual(model['devices'][0]['policy_assignment'], 'Unknown')
        rows = expanded_findings(bundle, evaluation_date=DAY)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['Disposition'] == 'Reference' and row['EvidenceLevel'] == 'configuration' for row in rows))
        self.assertTrue(all(row['Priority'] == 'Low' for row in rows))


@fictional_publication
class ComplianceSettingsOutputTests(unittest.TestCase):
    def test_zero_period_is_visible_as_returned_but_unverified_in_html_and_workbook(self):
        from Core.customer_report import render_customer_report
        from Core.export_recommendations import export_to_excel
        from openpyxl import load_workbook
        bundle = config_bundle({'secureByDefault': False, 'deviceComplianceCheckinThresholdDays': 0})
        result = build_assessment_result(expanded_findings(bundle, evaluation_date=DAY), bundle,
                                        evaluation_date=DAY, expected_tenant_id=TENANT)
        bundle['assessment_result'] = result
        html = render_customer_report(result, bundle, 'Fictional')
        self.assertIn('API returned 0; effective period unverified', html)
        self.assertNotIn('Compliance status validity period: 0 days', html)
        with TemporaryDirectory() as folder:
            path = export_to_excel(result['recommendations'], filename='Fictional.xlsx', evidence_bundle=bundle, output_dir=folder)
            book = load_workbook(path)
            try:
                self.assertTrue(any('API returned 0; effective period unverified' in str(cell.value)
                                    for row in book['Configuration'] for cell in row))
            finally:
                book.close()

    def test_configuration_sheet_html_lineage_and_snapshot_use_same_values(self):
        from openpyxl import load_workbook
        from Core.export_recommendations import export_to_excel
        from Core.customer_report import render_customer_report
        from Core.assessment_serialization import write_assessment_result, read_assessment_result
        from Core.workbook_layout import technical_workbook_path
        bundle = config_bundle()
        rows = expanded_findings(bundle, evaluation_date=DAY)
        result = build_assessment_result(rows, bundle, evaluation_date=DAY, expected_tenant_id=TENANT)
        bundle['assessment_result'] = result
        with TemporaryDirectory() as folder:
            path = Path(export_to_excel(rows, filename='Fictional.xlsx', evidence_bundle=bundle, output_dir=folder))
            result = bundle['assessment_result']
            before = deepcopy(result)
            html = render_customer_report(result, bundle, 'Fictional', path, technical_workbook_path=technical_workbook_path(path))
            self.assertIn('Compliance status validity period', html)
            self.assertIn('Devices without an assigned compliance policy are marked compliant', html)
            workbook = load_workbook(path)
            try:
                values = list(workbook['Configuration'].values)
                records = [dict(zip(values[0], row)) for row in values[1:]]
                setting = next(row for row in records if row.get('Setting') == 'Mark devices with no compliance policy assigned as')
                self.assertEqual(setting['Current'], 'Compliant')
                period = next(row for row in records if row.get('Setting') == 'Compliance status validity period (days)')
                self.assertEqual(str(period['Current']), '45')
                for finding in result['recommendations']:
                    if finding.get('ConfigurationSetting'):
                        self.assertTrue(finding['RawEvidenceRanges'])
                        self.assertTrue(finding['TechnicalEvidenceRanges'])
            finally:
                workbook.close()
            self.assertEqual(result, before)
            snapshot = Path(folder) / 'result.json'
            write_assessment_result(snapshot, result)
            replay = read_assessment_result(snapshot)
            self.assertEqual(replay['device_compliance']['tenant_settings'], json.loads(json.dumps(result['device_compliance']['tenant_settings'], default=str)))
            self.assertEqual(html, render_customer_report(replay, bundle, 'Fictional', path, technical_workbook_path=technical_workbook_path(path)))


if __name__ == '__main__':
    unittest.main()

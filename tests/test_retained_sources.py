"""Retained named records and independent user-activity enrichment."""

import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from Core.assessment_catalog import collect_assessment_sources
from Core.expanded_collection import USER_SIGNIN_ACTIVITY_PATH, collect_entra_details
from Core.get_entra_client import _fetch_graph_collection_via_http, get_entra_client


def empty_client(**attributes):
    return SimpleNamespace(collection_status={}, service_principals=[], ca_policies=[],
                           compliance_policies=[], managed_devices=[], **attributes)


class RetainedSourceTests(unittest.TestCase):
    def test_role_and_risk_sources_retain_nested_records_original_dates_and_partial_status(self):
        original = '2026-09-28T12:00:00Z'
        client = SimpleNamespace(
            role_definitions=[{'id':'role', 'displayName':'Named administrator'}],
            role_assignments=[{'id':'assignment', 'principal':{'id':'user'}, 'roleDefinitionId':'role'}],
            role_assignment_schedules=[{'id':'schedule', 'principalId':'user',
                'principal':{'id':'user', 'userPrincipalName':'fictional@example.test'},
                'assignmentType':'Activated', 'scheduleInfo':{'startDateTime':original,
                'expiration':{'type':'noExpiration'}}}],
            role_eligibility_schedules=[{'id':'eligible', 'scheduleInfo':{'startDateTime':original}}],
            risky_users=[{'id':'user', 'riskLevel':'medium', 'riskState':'atRisk'}],
            risk_detections=[{'id':'detection', 'userId':'user', 'activityDateTime':original,
                             'location':{'city':'Fictional City'}}],
            collection_status={'role_assignment_schedules':{'available':True, 'availability_status':'partial',
                'pages_collected':2, 'truncated':True, 'collected_at':original,
                'request_params':{'$expand':'principal,roleDefinition'}},
                'risk_detections':{'available':True, 'availability_status':'available', 'collected_at':original}}
        )
        sources = collect_assessment_sources([client], collected_at='2026-10-02T12:00:00Z')
        schedules = sources['role_assignment_schedules'][0]
        self.assertEqual(schedules['records'], client.role_assignment_schedules)
        self.assertEqual(schedules['source']['collected_at'], original)
        self.assertEqual(schedules['source']['pages_collected'], 2)
        self.assertFalse(schedules['source']['complete'])
        self.assertEqual(sources['risk_detections'][0]['records'][0]['location'], {'city':'Fictional City'})
        self.assertEqual(sources['risk_detections'][0]['source']['evidence_level'], 'observed_operation')
        schedules['records'][0]['scheduleInfo']['expiration']['type'] = 'modified'
        self.assertEqual(client.role_assignment_schedules[0]['scheduleInfo']['expiration']['type'], 'noExpiration')

    def test_app_activity_and_guest_request_aliases_preserve_outcomes(self):
        state = {'available':True, 'availability_status':'available', 'pages_collected':3,
                 'collected_at':'2026-09-30T12:00:00Z'}
        client = SimpleNamespace(
            service_principal_signin_activities=[{'appId':'app',
                'delegatedClientSignInActivity':{'lastSignInDateTime':'2026-09-29T12:00:00Z'}}],
            application_signin_summary=[{'id':'app', 'successfulSignInCount':5}],
            guest_users=[{'id':'guest', 'userType':'Guest'}],
            cross_tenant_access_policy={'inboundTrust':{'isMfaAccepted':True}},
            security_defaults={'isEnabled':False},
            collection_status={name:state for name in (
                'service_principal_signin_activities','app_signin_summary','guests','cross_tenant_policy','security_defaults')}
        )
        sources = collect_assessment_sources([client], collected_at='2026-10-02T12:00:00Z')
        self.assertEqual(sources['application_signin_summary'][0]['source']['pages_collected'], 3)
        self.assertEqual(sources['guest_users'][0]['source']['collected_at'], state['collected_at'])
        self.assertTrue(sources['cross_tenant_access_policy'][0]['source']['complete'])
        self.assertEqual(sources['service_principal_signin_activities'][0]['source']['evidence_quality'], 'preview')
        self.assertFalse(sources['security_defaults'][0]['records'][0]['isEnabled'])

    def test_sharepoint_tenant_sites_and_graph_remain_distinct_with_conflicting_settings(self):
        state = {'available':True, 'availability_status':'available', 'collected_at':'2026-09-30T12:00:00Z'}
        governance = {'source':'Retained SPO report',
            'tenant':{'available':True, 'settings':{'SharingCapability':'ExternalUserAndGuestSharing',
                       'RequireAnonymousLinksExpireInDays':5}},
            'sites':{'available':True, 'items':[{'Url':'https://fictional.test/site',
                'SharingCapability':'Disabled', 'DefaultSharingLinkType':'Internal'}]},
            'graph_settings':{'SharingCapability':'ExternalUserSharingOnly'},
            'conflicts':[{'setting':'SharingCapability', 'sources':['SPO','Graph']}],
            'collection_status':{name:state for name in (
                'sharepoint_tenant_settings','sharepoint_site_settings','sharepoint_tenant_settings_graph')}}
        sources = collect_assessment_sources([SimpleNamespace(sharepoint_governance=governance)])
        self.assertEqual(len(sources['sharepoint_tenant_settings'][0]['records']), 1)
        self.assertEqual(sources['sharepoint_tenant_settings'][0]['records'][0]['RequireAnonymousLinksExpireInDays'], 5)
        self.assertEqual(sources['sharepoint_site_settings'][0]['records'][0]['SharingCapability'], 'Disabled')
        self.assertEqual(sources['sharepoint_graph_settings'][0]['records'][0]['SharingCapability'], 'ExternalUserSharingOnly')
        self.assertEqual(sources['sharepoint_site_settings'][0]['source']['collected_at'], state['collected_at'])
        self.assertEqual(sources['sharepoint_graph_settings'][0]['source']['conflicts'], governance['conflicts'])
        self.assertIn('default values', sources['sharepoint_site_settings'][0]['source']['limitations'])

    def test_older_collection_does_not_invent_native_activity_or_detailed_sharing(self):
        client = SimpleNamespace(users=[{'id':'user', 'assignedLicenses':[]}], collection_status={})
        sources = collect_assessment_sources([client], collected_at='2026-09-01T12:00:00Z')
        self.assertNotIn('user_signin_activity', sources)
        self.assertNotIn('sharepoint_site_settings', sources)
        self.assertNotIn('signInActivity', sources['users'][0]['records'][0])


class UserActivityEnrichmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_activity_pages_keep_success_and_attempt_timestamps_separate(self):
        calls = []
        def respond(request):
            calls.append(request.url)
            if request.url.path == '/v1.0/users':
                second = request.url.params.get('$skiptoken') == 'second'
                record = {'id':'second' if second else 'first', 'signInActivity':{
                    'lastSignInDateTime':'2026-10-01T15:00:00Z',
                    'lastSuccessfulSignInDateTime':'2026-09-30T12:00:00Z'}}
                body = {'value':[record]}
                if not second:
                    body['@odata.nextLink'] = 'https://graph.microsoft.com/v1.0/users?$skiptoken=second'
                return httpx.Response(200, json=body)
            return httpx.Response(200, json={'value':[]})
        async def http_client():
            return httpx.AsyncClient(base_url='https://graph.microsoft.com', transport=httpx.MockTransport(respond))
        client = empty_client(users=[{'id':'existing', 'assignedLicenses':[]}])
        with patch('Core.get_entra_client._get_graph_http_client', side_effect=http_client):
            await collect_entra_details(client, _fetch_graph_collection_via_http, preview_collectors='none')
        dataset = client.assessment_datasets['user_signin_activity'][0]
        self.assertEqual([row['id'] for row in dataset['records']], ['first','second'])
        self.assertEqual(dataset['source']['pages_collected'], 2)
        self.assertEqual(dataset['source']['request_params']['$top'], '500')
        self.assertEqual(httpx.URL(dataset['source']['request_url']).params['$top'], '500')
        self.assertTrue(dataset['source']['collection_started_at'])
        self.assertTrue(dataset['source']['complete'])
        self.assertEqual(dataset['records'][0]['signInActivity']['lastSuccessfulSignInDateTime'], '2026-09-30T12:00:00Z')
        self.assertEqual(client.users, [{'id':'existing', 'assignedLicenses':[]}])
        self.assertEqual(next(url for url in calls if url.path == '/v1.0/users').params['$top'], '500')

    async def test_permission_or_licensing_failure_does_not_replace_users_or_other_sources(self):
        client = empty_client(users=[{'id':'existing', 'assignedLicenses':['license']}])
        async def fetch(path):
            if path == USER_SIGNIN_ACTIVITY_PATH:
                return {'available':False, 'availability_status':'unavailable', 'value':[],
                        'status_code':403, 'error':'Read permission or licensing unavailable'}
            return {'available':True, 'availability_status':'available', 'value':[{'id':'other'}]}
        await collect_entra_details(client, fetch, preview_collectors='none')
        source = client.assessment_datasets['user_signin_activity'][0]
        self.assertEqual(source['records'], [])
        self.assertFalse(source['source']['complete'])
        self.assertEqual(source['source']['status_code'], 403)
        self.assertIn('licensing', source['source']['reason'])
        self.assertTrue(client.collection_status['authentication_strengths']['complete'])
        self.assertEqual(client.users[0]['id'], 'existing')

    async def test_partial_activity_is_usable_and_restricted_never_requests_it(self):
        client = empty_client()
        fetch = AsyncMock(return_value={'available':False, 'availability_status':'partial',
            'value':[{'id':'retained', 'signInActivity':{'lastSignInDateTime':None}}],
            'pages_collected':1, 'truncated':True, 'status_code':403})
        await collect_entra_details(client, fetch, preview_collectors='none')
        source = client.assessment_datasets['user_signin_activity'][0]
        self.assertEqual(source['records'][0]['id'], 'retained')
        self.assertIsNone(source['records'][0]['signInActivity']['lastSignInDateTime'])
        self.assertFalse(source['source']['complete'])
        restricted = empty_client()
        fetch.reset_mock()
        await collect_entra_details(restricted, fetch, permission_profile='restricted')
        self.assertFalse(any(call.args[0] == USER_SIGNIN_ACTIVITY_PATH for call in fetch.await_args_list))
        self.assertEqual(restricted.collection_status['user_signin_activity']['availability_status'], 'not_requested')
        self.assertEqual(restricted.assessment_datasets['user_signin_activity'][0]['records'], [])

    async def test_role_request_metadata_survives_phase_processing(self):
        def respond(request):
            if request.url.path.endswith('/roleAssignments'):
                return httpx.Response(200, json={'value':[{'id':'assignment', 'principalId':'user',
                    'roleDefinitionId':'role', 'directoryScopeId':'/'}]})
            return httpx.Response(200, json={'value':[]})
        async def http_client():
            return httpx.AsyncClient(base_url='https://graph.microsoft.com', transport=httpx.MockTransport(respond))
        with patch('Core.get_entra_client._get_graph_http_client', side_effect=http_client), \
             redirect_stdout(io.StringIO()):
            client = await get_entra_client(SimpleNamespace(), 'fictional', preview_collectors='none')
        state = client.collection_status['role_assignments']
        self.assertEqual(state['request_params'], {'$expand':'principal'})
        self.assertTrue(state['source_api'].endswith('/roleAssignments'))
        self.assertEqual(state['collected_at'], state['collection_completed_at'])
        retained = collect_assessment_sources([client], collected_at='1999-01-01T00:00:00Z')
        self.assertEqual(retained['role_assignments'][0]['source']['collected_at'], state['collected_at'])


if __name__ == '__main__':
    unittest.main()

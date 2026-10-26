"""Synthetic-only checks for operator guidance, ownership and bounded recovery."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from provenance_fixture import fictional_publication
from Core.console_reporting import configure_console, print_source_gaps, cli_command
from Core.evidence_records import _matches_user, _user_positions
from Core.evidence_selection import build_evidence_selection
from Core.portal_report_import import export_confirmations, validate_report_tenants

TENANT = '11111111-1111-1111-1111-111111111111'
OTHER = '22222222-2222-2222-2222-222222222222'


@fictional_publication
class ConsoleTests(unittest.TestCase):
    def test_license_retry_and_generic_forbidden_remain_distinct(self):
        states = {
            'risky_users': {'availability_status':'unavailable', 'reason':'HTTP 403: Your tenant is not licensed for this feature.'},
            'roles': {'availability_status':'unavailable', 'reason':'HTTP 400: AadPremiumLicenseRequired - full original service explanation'},
            'teams': {'availability_status':'unavailable', 'reason':'HTTP read stopped: remaining retry wait budget (0s).'},
            'other': {'availability_status':'unavailable', 'reason':'HTTP 403: Forbidden'},
        }
        quiet, verbose = io.StringIO(), io.StringIO()
        try:
            for enabled, output in ((False,quiet),(True,verbose)):
                configure_console(verbose=enabled,color='never')
                with redirect_stdout(output):
                    print_source_gaps(states)
            self.assertIn('Source collection gaps: 4', quiet.getvalue())
            self.assertIn('risky_users: license required', quiet.getvalue())
            self.assertIn('teams: retry allowance exhausted', quiet.getvalue())
            self.assertIn('other: unavailable', quiet.getvalue())
            self.assertNotIn('full original service explanation', quiet.getvalue())
            self.assertIn('full original service explanation', verbose.getvalue())
            self.assertEqual(states['other']['reason'], 'HTTP 403: Forbidden')
        finally:
            configure_console(verbose=False,color='auto')

    def test_command_preserves_checkout_interpreter_and_quotes(self):
        from Core.reassessment_cli import reassessment_command
        from Core import console_reporting
        with tempfile.TemporaryDirectory(prefix='quoted space ') as folder:
            history = Path(folder) / "customer's history.json"
            command = reassessment_command(history, 'RUN-fixture')
            self.assertTrue(command.startswith(cli_command() + ' --mode live'))
            self.assertIn("customer''s history.json", command)
            self.assertIn(console_reporting.display_path(Path(console_reporting.__file__).resolve().parents[1] / 'main.py'), command)
            self.assertIn(console_reporting.display_path(sys.executable), command)
            self.assertNotIn('\n', command)

    def test_operator_log_retains_service_and_retry_details(self):
        from Core.assessment_package import record_package_run
        states = {'teams': {'availability_status':'unavailable','status_code':429,
            'reason':'Original service response', 'retry_details':{'attempts':5,'waited_seconds':240},
            'authorization':'must not be logged'}}
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            record_package_run(folder,mode='live',tenant_id=TENANT,collected_at='2026-10-26',
                evaluation_date='2026-10-26',outputs={'evidence_bundle':{'source_statuses':states}})
            receipt=json.loads((Path(folder)/'operator-log.jsonl').read_text())
        self.assertEqual(receipt['source_statuses']['teams']['retry_details']['waited_seconds'],240)
        self.assertEqual(receipt['source_statuses']['teams']['reason'],'Original service response')
        self.assertNotIn('authorization',receipt['source_statuses']['teams'])


@fictional_publication
class ExportConfirmationTests(unittest.TestCase):
    def test_confirmation_is_byte_bound_and_never_overrides_a_different_guid(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'export.csv'
            path.write_text('Site URL,Links created\nhttps://example.sharepoint.com/sites/a,4\n')
            confirmations=export_confirmations([path],TENANT)
            receipt=[]
            validate_report_tenants([path],TENANT,receipt,confirmations=confirmations)
            self.assertEqual(receipt[0]['Status'],'identity_operator_confirmed')
            self.assertEqual(receipt[0]['IdentityBasis'],'operator-confirmed')
            self.assertIn(receipt[0]['SourceHash'],confirmations)
            path.write_text(path.read_text().replace(',4',',5'))
            receipt=[]
            validate_report_tenants([path],TENANT,receipt,confirmations=confirmations)
            self.assertEqual(receipt[0]['Status'],'identity_unverified')
            path.write_text('Tenant ID,Value\n'+OTHER+',4\n')
            confirmations=export_confirmations([path],TENANT)
            with self.assertRaisesRegex(ValueError,'tenant ID does not match'):
                validate_report_tenants([path],TENANT,[],confirmations=confirmations)

    def test_confirmation_does_not_cross_customers_or_create_evidence_for_headers(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'export.csv'
            path.write_text('Value\n4\n')
            confirmations=export_confirmations([path],TENANT)
            receipt=[]
            validate_report_tenants([path],OTHER,receipt,confirmations=confirmations)
            self.assertEqual(receipt[0]['Status'],'identity_unverified')
            path.write_text('Value\n')
            receipt=[]
            validate_report_tenants([path],TENANT,receipt,confirmations=export_confirmations([path],TENANT))
            self.assertEqual(receipt[0]['Status'],'no_rows')

    def test_cli_preflight_restores_confirmations_without_confirming_new_files(self):
        from Core.cli_parser import parse_arguments
        from Core.effective_configuration import resolve_effective
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'export.csv'
            path.write_text('Value\n4\n')
            with patch.object(sys,'argv',['main.py','--mode','offline','--tenant-id',TENANT,
                    '--sam-report',str(path),'--confirm-export-tenant',TENANT]):
                args=parse_arguments('',[])
            resolved,_,errors=resolve_effective(args,saved={})
            self.assertEqual(errors,[])
            confirmations=resolved._effective_settings['export_tenant_confirmations']
            self.assertEqual(confirmations,export_confirmations([path],TENANT))
            args.confirm_export_tenant=OTHER
            self.assertIn('export_tenant_confirmation_invalid',resolve_effective(args,saved={})[2])
            args.confirm_export_tenant=None
            path.write_text('Value\n9\n')
            replay,_,errors=resolve_effective(args,saved={'assessment_settings':{'export_tenant_confirmations':confirmations}})
            self.assertEqual(errors,[])
            receipt=[]
            validate_report_tenants([path],TENANT,receipt,confirmations=replay._effective_settings['export_tenant_confirmations'])
            self.assertEqual(receipt[0]['Status'],'identity_unverified')

    def test_packaged_confirmation_survives_recipe_restore(self):
        from Core.assessment_package import save_rebuild_recipe, _apply_recipe
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            args=SimpleNamespace(evaluation_date='2026-10-26',report_format='excel')
            confirmations={'a'*64:TENANT}
            save_rebuild_recipe(folder,args,{'export_tenant_confirmations':confirmations},tenant_id=TENANT)
            recipe=json.loads((Path(folder)/'rebuild.json').read_text())
            payload={}
            _apply_recipe(payload,Path(folder),recipe)
            self.assertEqual(payload['assessment_settings']['export_tenant_confirmations'],confirmations)


class ReportRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_lightweight_report_adapters_do_not_need_httpx_extensions(self):
        from Core.http_retry import request_with_retry
        response=SimpleNamespace(status_code=200,headers={})
        self.assertIs(await request_with_retry(AsyncMock(return_value=response)),response)

    async def test_report_recovers_after_three_60_second_delays(self):
        from Core.get_graph_client import GraphRestClient
        client=GraphRestClient.__new__(GraphRestClient)
        client.max_retries=4
        client.credential=SimpleNamespace(get_token=lambda *args:SimpleNamespace(token='fictional'))
        responses=[httpx.Response(429,headers={'Retry-After':'60'}) for _ in range(3)]
        responses.append(httpx.Response(200,text='User,Count\nexample,4\n'))
        client._http=SimpleNamespace(request=AsyncMock(side_effect=responses))
        with patch('Core.http_retry.asyncio.sleep',new=AsyncMock()) as sleep:
            rows=await client.get_csv('/v1.0/reports/fixture')
        self.assertEqual(rows,[{'User':'example','Count':'4'}])
        self.assertEqual([call.args[0] for call in sleep.await_args_list],[60,60,60])
        self.assertEqual(client._http.request.await_count,4)

    async def test_retry_still_stops_and_preserves_failure_history(self):
        from Core.get_graph_client import GraphRestClient, GraphRequestError
        client=GraphRestClient.__new__(GraphRestClient)
        client.max_retries=4
        client.credential=SimpleNamespace(get_token=lambda *args:SimpleNamespace(token='fictional'))
        client._http=SimpleNamespace(request=AsyncMock(return_value=httpx.Response(429,headers={'Retry-After':'60'})))
        with patch('Core.http_retry.asyncio.sleep',new=AsyncMock()) as sleep:
            with self.assertRaises(GraphRequestError) as caught:
                await client.get_csv('/v1.0/reports/fixture')
        self.assertEqual(client._http.request.await_count,5)
        self.assertEqual(sleep.await_count,4)
        self.assertEqual(caught.exception.retry_details,{'attempts':5,'waited_seconds':240,
            'status_code':429,'retry_after_seconds':60})

    async def test_json_budget_and_long_server_delay_remain_bounded(self):
        from Core.get_graph_client import GraphRestClient, GraphRequestError
        client=GraphRestClient.__new__(GraphRestClient)
        client.max_retries=4
        client.credential=SimpleNamespace(get_token=lambda *args:SimpleNamespace(token='fictional'))
        client._http=SimpleNamespace(request=AsyncMock(return_value=httpx.Response(429,headers={'Retry-After':'60'})))
        with patch('Core.http_retry.asyncio.sleep',new=AsyncMock()) as sleep:
            with self.assertRaises(GraphRequestError) as caught:
                await client.get_json('/v1.0/users')
        self.assertEqual(sleep.await_count,2)
        self.assertEqual(caught.exception.retry_details['waited_seconds'],120)
        client._http.request=AsyncMock(return_value=httpx.Response(429,headers={'Retry-After':'1000'}))
        with patch('Core.http_retry.asyncio.sleep',new=AsyncMock()) as sleep:
            with self.assertRaises(GraphRequestError):
                await client.get_csv('/v1.0/reports/fixture')
        sleep.assert_not_awaited()
        self.assertEqual(client._http.request.await_count,1)


class IndexedEvidenceTests(unittest.TestCase):
    def test_index_preserves_id_precedence_fallback_duplicates_and_order(self):
        rows=[({'id':'OTHER','userPrincipalName':'same@example.test'},{}),
              ({'userPrincipalName':'same@example.test'},{}),
              ({'id':'target','userPrincipalName':'changed@example.test'},{}),
              ({'ID':'TARGET','UPN':'same@example.test'},{}),
              ({'userId':'target','User Principal Name':'same@example.test'},{}),
              ({}, {})]
        index=_user_positions(rows)
        for identifier,upn in (('target','SAME@example.test'),(None,'same@example.test'),('missing','same@example.test'),('target',None),(None,None)):
            expected=[i for i,(row,_) in enumerate(rows) if _matches_user(row,identifier,upn)]
            self.assertEqual(index(identifier,upn),expected)

    def test_mfa_population_is_reused_only_within_one_selection(self):
        rows=[{'RecommendationId':'one','FindingKey':'entra.mfa_registration.one'},
              {'RecommendationId':'two','FindingKey':'entra.mfa_registration.two'}]
        detail={'records':[],'record_type':'user_mfa_registration','selection':'Synthetic','limitations':[]}
        with patch('Core.evidence_selection.expand_finding_records',return_value=detail) as expand:
            for _ in range(2):
                build_evidence_selection({'recommendations':rows},{})
            self.assertEqual(expand.call_count,2)

    def test_privileged_findings_keep_their_separate_selected_populations(self):
        rows=[{'RecommendationId':'one','FindingKey':'entra.privileged.mfa_registration.one',
               'PrivilegedObservationIds':['principal-a']},
              {'RecommendationId':'two','FindingKey':'entra.privileged.mfa_registration.two',
               'PrivilegedObservationIds':['principal-b']}]
        def selected(row,*args,**options):
            from Core.evidence_records import _record
            return {'records':[_record({'principal':row['PrivilegedObservationIds'][0]},[])],
                    'record_type':'privileged_identity','selection':'Selected observations','limitations':[]}
        with patch('Core.evidence_selection.expand_finding_records',side_effect=selected):
            result=build_evidence_selection({'recommendations':rows},{})
        self.assertEqual([finding['records'][0]['fields']['principal'] for finding in result['findings']],
                         ['principal-a','principal-b'])


from test_assessment_run_workflow import RunFixture


@fictional_publication
class ConfirmationExecutionTests(RunFixture):
    def test_initial_confirmation_is_packaged_and_frozen_replay_rejects_new_confirmation(self):
        from test_reassessment_cli import NestedReassessmentExecutionTests
        from synthetic_package_fixture import create_synthetic_package, SYNTHETIC_TENANT
        from test_sharepoint_sharing_rules import LINK_HEADER
        from test_assessment_run_outputs import parse
        from Core.assessment_history import read_history
        from Core.offline_collection import load_collection
        from Core.run_plan import build_run_plan
        from provenance_fixture import fictional_revision
        source=create_synthetic_package(self.root/'source')
        export=self.root/'Anyone_links_2026-09-15.csv'
        export.write_text(LINK_HEADER+'\n'+
            'site-1,https://example.sharepoint.com/sites/finance,Finance,4,Owner,owner@example.test,No,Team,,,,Anyone,Private\n')
        execute=lambda *options:NestedReassessmentExecutionTests.execute(self,*options)
        output=execute('--collection-input',source,'--run-type','Initial','--sam-report',export,
                       '--confirm-export-tenant',SYNTHETIC_TENANT)
        history=next(self.root.glob('Reports/*/*/assessment-history.json'))
        package=history.parent
        receipt=json.loads((package/'operator-log.jsonl').read_text().splitlines()[-1])
        identities=[row for row in receipt['import_receipt'] if row.get('Status')=='identity_operator_confirmed']
        self.assertEqual(len(identities),1)
        self.assertIn('operator-confirmed',output)
        restored=load_collection(package/'collection.json')
        expected=export_confirmations([export],SYNTHETIC_TENANT)
        retained=restored['assessment_settings']['export_tenant_confirmations']
        self.assertTrue(expected.items() <= retained.items())
        self.assertIn('assessment_model',receipt['build_timings_seconds'])
        self.assertIn('total',receipt['build_timings_seconds'])
        before=history.read_bytes()
        args=parse('--collection-input',package/'collection.json','--confirm-export-tenant',SYNTHETIC_TENANT)
        plan=build_run_plan(args,revision=fictional_revision())
        self.assertIn('replay_cannot_confirm_export_identity',plan['validation_errors'])
        with self.assertRaisesRegex(ValueError,'explicit new --run-type'):
            execute('--collection-input',package/'collection.json','--confirm-export-tenant',SYNTHETIC_TENANT)
        self.assertEqual(history.read_bytes(),before)
        self.assertEqual(len(read_history(history)['Runs']),1)

"""Upfront authentication with fictional local processes, never tenant access."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from Core.authentication_preparation import AuthenticationPreparation, active_preparation
from Core.orchestrator_powershell import _launch_powershell, _communicate_with_progress
from provenance_fixture import fictional_publication


READY_SCRIPT = '''import sys,json
print('AUTH_PROMPT:Fictional service',file=sys.stderr,flush=True)
print('AUTH_READY:Fictional service',file=sys.stderr,flush=True)
command=sys.stdin.readline().strip()
if command!='COLLECT':sys.exit(2)
print(json.dumps({'available':True,'session':'same-process'}),flush=True)
'''


class PreparationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.preparation = AuthenticationPreparation(timeout=2).activate()

    async def asyncTearDown(self):
        await self.preparation.close()

    def process(self, code=READY_SCRIPT):
        process = subprocess.Popen([sys.executable,'-u','-c',code],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',bufsize=1)
        return self.preparation.attach(process)

    async def collect(self, label, events):
        process = self.process()
        events.append('authenticate-'+label)
        stdout,stderr = await asyncio.to_thread(_communicate_with_progress,process,label)
        events.append('collect-'+label)
        return json.loads(stdout),stderr

    async def test_every_sign_in_finishes_before_any_collection_and_session_survives(self):
        events = []
        first = await self.preparation.prepare('first',self.collect('first',events))
        second = await self.preparation.prepare('second',self.collect('second',events))
        self.assertEqual(events,['authenticate-first','authenticate-second'])
        self.assertFalse(first.done())
        self.assertFalse(second.done())
        self.assertTrue(all(process.ready for process in self.preparation.processes.values()))
        pids = [process.process.pid for process in self.preparation.processes.values()]
        self.preparation.release()
        results = await asyncio.wait_for(asyncio.gather(first,second),timeout=2)
        self.assertTrue(all(result[0]['session']=='same-process' for result in results))
        self.assertEqual(pids,[process.process.pid for process in self.preparation.processes.values()])
        self.assertEqual(events[:2],['authenticate-first','authenticate-second'])
        self.assertTrue(all('AUTH_READY' not in stderr for _,stderr in results))

    async def test_cached_skipped_or_failed_auth_result_is_not_retried(self):
        cached = AsyncMock(return_value=True)
        skipped = AsyncMock(return_value=False)
        first = await self.preparation.prepare('cached',cached())
        second = await self.preparation.prepare('skipped',skipped())
        self.assertEqual(self.preparation.processes,{})
        self.preparation.release()
        self.assertTrue(await first)
        self.assertFalse(await second)
        cached.assert_awaited_once();skipped.assert_awaited_once()

    async def test_failed_process_is_consumed_as_a_gap_without_second_sign_in(self):
        async def fail():
            process = self.process('import sys;print(\'AUTH_ERROR:Fictional:declined\',file=sys.stderr);sys.exit(1)')
            stdout,stderr = await asyncio.to_thread(process.communicate)
            return process.returncode,stderr
        task = await self.preparation.prepare('failed',fail())
        self.assertTrue(task.done())
        code,stderr = await task
        self.assertEqual(code,1)
        self.assertIn('declined',stderr)
        self.assertEqual(len(self.preparation.processes),1)

    async def test_cancel_closes_every_prepared_process_and_restores_context(self):
        first = await self.preparation.prepare('first',self.collect('first',[]))
        process = self.preparation.processes['first']
        await self.preparation.close()
        self.assertIsNotNone(process.process.poll())
        self.assertTrue(first.done())
        self.assertTrue(all(not reader.is_alive() for reader in process.readers))
        self.assertIsNone(active_preparation())

    async def test_timeout_cancels_sign_in_and_reaps_process(self):
        self.preparation.timeout = 0.1
        async def stuck():
            process = self.process('import time;time.sleep(30)')
            return await asyncio.to_thread(process.communicate)
        with self.assertRaises(TimeoutError):
            await self.preparation.prepare('stuck',stuck())
        self.assertIsNotNone(self.preparation.processes['stuck'].process.poll())
        self.assertIsNone(active_preparation())

    async def test_late_launcher_is_rejected_before_starting_a_process(self):
        self.preparation.release()
        with patch('Core.orchestrator_powershell.subprocess.Popen') as launch:
            with self.assertRaisesRegex(RuntimeError,'Authentication preparation is complete'):
                _launch_powershell('collector.ps1',[])
        launch.assert_not_called()

    async def test_secrets_use_first_stdin_line_and_gate_uses_second_line(self):
        actual_popen = subprocess.Popen
        launches=[]
        code='''import sys,json
secret=json.loads(sys.stdin.readline())
print('AUTH_READY:Fixture',file=sys.stderr,flush=True)
if sys.stdin.readline().strip()!='COLLECT':sys.exit(2)
print(json.dumps({'secret_received':secret['certificate_password']=='fictional-password'}))
'''
        def launch(args,**kwargs):
            launches.append(args)
            return actual_popen([sys.executable,'-u','-c',code],**kwargs)
        async def collect():
            process = _launch_powershell('collector.ps1',[],secrets={'certificate_password':'fictional-password'})
            return await asyncio.to_thread(process.communicate)
        with patch('Core.orchestrator_powershell.shutil.which',return_value='fictional-powershell'), \
             patch('Core.orchestrator_powershell.subprocess.Popen',side_effect=launch):
            task = await self.preparation.prepare('fixture',collect())
        self.assertFalse(task.done())
        self.assertIn('-WaitForCollectionStart',launches[0])
        self.assertIn('-SecretsFromStdin',launches[0])
        self.assertNotIn('fictional-password',' '.join(launches[0]))
        self.preparation.release()
        stdout,stderr = await asyncio.wait_for(task,timeout=2)
        self.assertTrue(json.loads(stdout)['secret_received'])
        self.assertNotIn('fictional-password',stderr)


class DelegatedSilentRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_upfront_credential_can_open_a_browser(self):
        from Core import delegated_auth
        import base64
        body = base64.urlsafe_b64encode(json.dumps({'tid':'fictional-tenant'}).encode()).decode().rstrip('=')
        record = SimpleNamespace(serialize=lambda:'fictional record')
        initial = SimpleNamespace(authenticate=lambda **kwargs:record,
            get_token=lambda *scopes:SimpleNamespace(token='header.'+body+'.signature'))
        silent = SimpleNamespace()
        with patch.object(delegated_auth,'delegated_state',return_value='can_prompt'), \
             patch.object(delegated_auth,'_load_record',return_value=None), \
             patch.object(delegated_auth,'_save_record'), \
             patch.object(delegated_auth,'can_prompt',return_value=True), \
             patch.object(delegated_auth,'_build_credential',side_effect=[initial,silent]) as build:
            session,reason = await delegated_auth.acquire_delegated_session('fictional-tenant','fictional-app')
        self.assertEqual(reason,'')
        self.assertIs(session.credential,silent)
        self.assertTrue(build.call_args_list[0].kwargs['allow_prompt'])
        self.assertFalse(build.call_args_list[1].kwargs['allow_prompt'])
        self.assertIs(build.call_args_list[1].kwargs['record'],record)


class CollectorGateTests(unittest.TestCase):
    def test_script_gates_follow_authentication_and_precede_bulk_reads(self):
        root=Path(__file__).resolve().parents[1]
        for filename,connect,first_read in (
            ('collect_purview_data.ps1','Connect-ExchangeOnline','Get-DlpCompliancePolicy -'),
            ('collect_sharepoint_governance.ps1','Connect-SPOService','$tenantProperties ='),
            ('collect_power_platform_and_copilot_studio_data.ps1','Connect-AzAccount',"$powerPlatformData =")):
            with self.subTest(filename=filename):
                source=(root/filename).read_text(encoding='utf-8-sig')
                gate=source.index("[Console]::Error.WriteLine('AUTH_READY:")
                self.assertGreater(gate,source.rindex(connect))
                start=source.index('# Step 2:') if filename=='collect_purview_data.ps1' else 0
                self.assertLess(gate,source.index(first_read,start))
                self.assertIn("[Console]::In.ReadLine() -cne 'COLLECT'",source)
        power=(root/'collect_power_platform_and_copilot_studio_data.ps1').read_text(encoding='utf-8-sig')
        self.assertLess(power.rindex('Get-AzAccessToken'),power.index("[Console]::Error.WriteLine('AUTH_READY:"))


@fictional_publication
class UpfrontOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Reuse the existing isolated orchestration fixture and its network,
        # subprocess and prompt guards. It writes fictional temporary packages.
        from test_collection_autosave import CollectionAutosaveTests
        CollectionAutosaveTests.setUp(self)
        self.events=[]
        self.release=asyncio.Event()
        self.preparation=None
        outer=self
        class Preparation:
            def __init__(self):
                self.tasks=[]
                outer.preparation=self
            def activate(self):
                return self
            async def prepare(self,label,operation):
                task=asyncio.create_task(operation)
                self.tasks.append(task)
                await asyncio.sleep(0)
                outer.events.append('ready:'+label)
                return task
            def release(self):
                outer.release.set()
            async def close(self):
                for task in self.tasks:
                    if not task.done():task.cancel()
                await asyncio.gather(*self.tasks,return_exceptions=True)
        self.stack.enter_context(patch('Core.authentication_preparation.AuthenticationPreparation',Preparation))
        self.plan={'sharepoint':{'selected':True,'will_attempt':True},
                   'purview':{'selected':True,'will_attempt':True},
                   'power_platform':{'selected':True,'will_attempt':True}}
        self.stack.enter_context(patch('Core.orchestrator.prepare_interactive_collection_plan',return_value=self.plan))
        self.stack.enter_context(patch.dict(os.environ,{'CLIENT_ID':'fictional-app','TENANT_ID':'fictional.onmicrosoft.com'},clear=True))
        async def check(*args,**kwargs):
            kwargs['auth_plan_out']['datasets']={'copilot_limited_mode':{'selected_path':'graph_delegated'}}
            return []
        self.stack.enter_context(patch('Core.connection_validation.run_connection_checks',side_effect=check))
        async def sign_in(*args,**kwargs):
            self.events.append('authenticate:Graph')
            return SimpleNamespace(identity={'upn':'fictional@example.invalid'}),''
        self.delegated=self.stack.enter_context(patch('Core.delegated_auth.acquire_delegated_session',side_effect=sign_in))
        async def collector(label,result):
            self.events.append('authenticate:'+label)
            await self.release.wait()
            self.events.append('collect:'+label)
            return result
        def operation(label,result):
            async def run(*args,**kwargs):
                return await collector(label,result)
            return run
        self.sharepoint=self.stack.enter_context(patch('Core.orchestrator.collect_sharepoint_with_retry',side_effect=operation('SharePoint',{'available':False,'reason':'Fictional no export'})))
        self.purview=self.stack.enter_context(patch('Core.orchestrator.collect_purview_data_via_powershell',side_effect=operation('Purview',False)))
        self.power=self.stack.enter_context(patch('Core.orchestrator.collect_power_platform_data',side_effect=operation('Power Platform',None)))
        async def licenses(*args):
            self.events.append('collect:licenses')
            return False
        self.stack.enter_context(patch('Core.orchestrator.load_license_context',side_effect=licenses))
        async def settings(*args):
            self.events.append('collect:settings')
            return {'available':False,'reason':'Fictional unavailable'}
        self.stack.enter_context(patch('Core.sharepoint_graph_settings.collect_sharepoint_tenant_settings_graph',side_effect=settings))
        async def limited(*args):
            self.events.append('collect:limited-mode')
            return {'available':False,'reason':'Fictional not available'}
        self.stack.enter_context(patch('Core.copilot_settings.collect_copilot_limited_mode',side_effect=limited))

    async def run_assessment(self,**options):
        from Core.orchestrator import orchestrate
        return await orchestrate('11111111-1111-1111-1111-111111111111',
            ['M365','Purview','Power Platform'],permission_profile='standard',
            preview_collectors='none',legacy_power_platform_collector=True,**options)

    async def test_all_selected_sign_ins_precede_licenses_and_collectors(self):
        result=await self.run_assessment()
        self.assertIsNone(result)
        expected=['authenticate:Graph','authenticate:SharePoint','authenticate:Purview','authenticate:Power Platform']
        self.assertEqual([event for event in self.events if event.startswith('authenticate:')],expected)
        first_read=min(index for index,event in enumerate(self.events) if event.startswith('collect:'))
        self.assertTrue(all(self.events.index(event)<first_read for event in expected))
        self.sharepoint.assert_awaited_once();self.purview.assert_awaited_once();self.power.assert_awaited_once()
        self.assertEqual(self.delegated.call_args.args[0],'11111111-1111-1111-1111-111111111111')
        self.assertTrue(self.preparation.tasks[1].done())

    async def test_required_delegated_failure_stops_before_workload_auth_or_evidence(self):
        self.delegated.side_effect=None
        self.delegated.return_value=(None,'Fictional sign-in declined')
        result=await self.run_assessment(delegated='required')
        self.assertEqual(result,2)
        self.sharepoint.assert_not_called();self.purview.assert_not_called();self.power.assert_not_called()
        self.assertFalse(any(event.startswith('collect:') for event in self.events))
        self.assertFalse(Path('Reports').exists())
        self.processor.assert_not_called()

    async def test_failed_optional_sign_in_does_not_schedule_a_late_retry(self):
        self.delegated.side_effect=None
        self.delegated.return_value=(None,'Fictional optional sign-in declined')
        result=await self.run_assessment()
        self.assertIsNone(result)
        self.delegated.assert_awaited_once()
        self.assertNotIn('collect:limited-mode',self.events)
        self.sharepoint.assert_awaited_once()


if __name__=='__main__':
    unittest.main()

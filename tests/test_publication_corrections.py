"""Adversarial production-boundary regressions with fictional evidence only."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from Core import repository_provenance as p
from Core.assessment_result import build_assessment_result
from Core.run_plan import build_run_plan, plan_json, plan_lines
from Core.publication_io import exclusive_output, validate_destination
from provenance_fixture import fictional_revision, fictional_publication
from test_repository_provenance import FakeGit


@fictional_publication
class PublicationCorrections(unittest.TestCase):
    def test_all_writer_boundaries_block_before_io(self):
        from Core import export_recommendations as e, assessment_package as a, assessment_history as h
        from Core import offline_collection as c, assessment_runs as r, export_paths as x
        from Core.assessment_serialization import write_assessment_result
        from Core.assessment_replay import render_snapshot
        from Core.html_evidence_pages import write_html_evidence_pages
        from Core.publication_io import copy_file,append_receipt,_create_exclusive
        calls=[(e.export_to_html,([],)),(e.export_to_excel,([],)),(e.export_to_csv,([],)),(e.export_to_json,([],)),
               (write_assessment_result,('never.json',{})),(h.atomic_write,('never.json',{})),
               (r.complete_run,({},None,'never')),(a.package_inputs,('never',{})),
               (a.save_rebuild_recipe,('never',None)),(a.new_offline_package,('Fictional',)),
               (c._write_collection_json,(Path('never.json'),{})),(render_snapshot,('never.json','never')),
               (write_html_evidence_pages,({},'never')),(x.new_numbered_directory,('never',)),
               (x.new_assessment_directory,()),(x.report_directory,('never',)),
               (copy_file,('never.json','never-new.json')),(append_receipt,('never.json',{})),(_create_exclusive,(Path('never.json'),))]
        for revision in (None,p.unrecorded_revision(),dict(fictional_revision(),source_state='DirtyUnclassified',publication=p.publication_policy({'source_state':'DirtyUnclassified'}))):
            with p.producing_context(revision),patch.object(Path,'mkdir',side_effect=AssertionError('mkdir reached')),patch.object(Path,'open',side_effect=AssertionError('open reached')):
                for function,args in calls:
                    with self.subTest(writer=function.__name__,state=(revision or {}).get('source_state')):
                        with self.assertRaises(ValueError):function(*args)
                with self.assertRaises(ValueError):a.record_package_run('never',mode='offline',tenant_id=None,collected_at=None,evaluation_date=None)
                with self.assertRaises(ValueError):c.save_collection('never.json',tenant_id=None,tenant_name=None,service_results={})

    def test_whitespace_and_nonstring_engineering_annotations(self):
        for value in (None,'','  ','\t',False,7,[],{}):
            for field in ('purpose','reason'):
                args=dict(policy='engineering-validation',purpose='Fixture',reason='Review',acknowledged=True);args[field]=value
                with self.subTest(field=field,value=value):self.assertFalse(p.publication_policy(fictional_revision(),**args)['execution_eligible'])
        policy=p.publication_policy(fictional_revision(),policy='engineering-validation',purpose=' Fixture ',reason=' Review ',acknowledged=True)
        self.assertEqual((policy['purpose'],policy['reason']),('Fixture','Review'))
        for acknowledgement in (None,False,'true',1):
            self.assertFalse(p.publication_policy(fictional_revision(),policy='engineering-validation',purpose='Fixture',reason='Review',acknowledged=acknowledgement)['execution_eligible'])
        from Core.effective_configuration import resolve_effective
        first=SimpleNamespace(offline=True,sam_report=['fictional.csv'],assessment_purpose=' Fixture ',source_reason=' Review ')
        second=SimpleNamespace(offline=True,sam_report=['fictional.csv'],assessment_purpose='Fixture',source_reason='Review')
        one,_,_=resolve_effective(first);two,_,_=resolve_effective(second)
        self.assertEqual(p.configuration_hash(one),p.configuration_hash(two))

    def test_malformed_and_future_provenance_is_unverified(self):
        result=build_assessment_result([],evaluation_date='2026-10-13')
        for value in (None,False,[], 'invalid',{}, {'schema_version':'999','source_state':'CleanCommitted'},
                      dict(fictional_revision(),commit_tree_sha=None),dict(fictional_revision(),publication='invalid')):
            with self.subTest(value=value),patch.object(Path,'open',return_value=io.StringIO(json.dumps(dict(result,producing_revision=value)))):
                from Core.assessment_serialization import read_assessment_result
                loaded=read_assessment_result('fictional.json')
                self.assertEqual(loaded['producing_revision']['source_state'],'SourceStateUnavailable')
                self.assertNotIn('Customer publication eligible',p.provenance_html(loaded))
                self.assertTrue(p.provenance_rows(loaded))

    def test_existing_alias_and_completed_package_destinations(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);existing=root/'baseline.json';existing.write_text('immutable')
            for target in (existing,root/'child'/'..'/'baseline.json'):
                with self.subTest(target=target),self.assertRaises(FileExistsError):validate_destination(target)
            (root/'assessment-run.json').write_text('{}')
            with self.assertRaises(ValueError):validate_destination(root/'new-snapshot.json')
            with self.assertRaises(FileExistsError):
                with exclusive_output(existing):self.fail('Existing file opened')
            self.assertEqual(existing.read_text(),'immutable')

    def test_symlink_destination_is_rejected(self):
        with patch.object(Path,'is_symlink',return_value=True):
            with self.assertRaises(ValueError):validate_destination('fictional/new.json')

    def test_final_destination_race_does_not_truncate(self):
        with tempfile.TemporaryDirectory() as folder:
            from Core.publication_io import _create_exclusive
            target=Path(folder)/'race.json'
            def race(path,*args,**kwargs):
                target.write_text('racing immutable input')
                return _create_exclusive(path,*args,**kwargs)
            with patch('Core.publication_io._create_exclusive',side_effect=race),self.assertRaises(FileExistsError):
                with exclusive_output(target):self.fail('Raced destination opened')
            self.assertEqual(target.read_text(),'racing immutable input')

    def test_snapshot_writer_refuses_existing_target(self):
        from Core.assessment_serialization import write_assessment_result
        result=build_assessment_result([],evaluation_date='2026-10-13')
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'baseline.json';target.write_text('immutable')
            with self.assertRaises(FileExistsError):write_assessment_result(target,result)
            self.assertEqual(target.read_text(),'immutable')

    def test_atomic_and_copy_writers_refuse_baseline(self):
        from Core.assessment_history import atomic_write
        from Core.publication_io import copy_file, append_receipt
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);target=root/'baseline.json';target.write_text('{"identity":{}}')
            source=root/'input.json';source.write_text('{}')
            before=target.read_bytes()
            with self.assertRaises(ValueError):atomic_write(target,{'replacement':True})
            with self.assertRaises(FileExistsError):copy_file(source,target)
            with self.assertRaises(ValueError):append_receipt(target,{'run_id':'fictional','mode':'replay','deliverables':[]})
            self.assertEqual(target.read_bytes(),before)

    def test_parent_replacement_cannot_redirect_creation(self):
        from Core.publication_io import _create_exclusive
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);parent=root/'output';parent.mkdir();target=parent/'result.json'
            def race(path,*args,**kwargs):
                try:parent.rename(root/'moved')
                except PermissionError:
                    self.assertEqual(os.name,'nt')
                    raise ValueError('Pinned directory prevented replacement')
                parent.mkdir();(parent/'result.json').write_text('racing baseline')
                return _create_exclusive(path,*args,**kwargs)
            with patch('Core.publication_io._create_exclusive',side_effect=race),self.assertRaises((ValueError,FileExistsError)):
                with exclusive_output(target):self.fail('Redirected destination opened')
            if target.exists():self.assertEqual(target.read_text(),'racing baseline')

    def test_windows_junction_metadata_is_rejected(self):
        from Core.publication_io import linked_path
        with patch.object(Path,'is_symlink',return_value=False),patch.object(Path,'lstat',return_value=SimpleNamespace(st_file_attributes=0x400)):
            self.assertTrue(linked_path('fictional/junction'))
            with self.assertRaises(ValueError):validate_destination('fictional/junction/new.json')

    def test_corrupt_provenance_fields_are_unverified(self):
        replacements={'warnings':'unsafe','publication':{'policy':'production'},'import_adapters':[],
            'execution_environment':[],'fingerprint_version':'future','generated_at':'not a date',
            'run_configuration_hash':'short','collector_configuration_hash':None,'branch':[],
            'remote_identifier':None,'repository_identifier':'unknown','staged_change_count':False,
            'tracked_modification_count':-1,'untracked_entry_count':{},'diagnostics':[float('nan')]}
        for field,value in replacements.items():
            with self.subTest(field=field):
                self.assertEqual(p.normalize_revision(dict(fictional_revision(),**{field:value}))['source_state'],'SourceStateUnavailable')
        for adapter in (None,[],{'version':{},'implementation_sha256':'f'*64},{'version':None,'implementation_sha256':7}):
            self.assertEqual(p.normalize_revision(dict(fictional_revision(),import_adapters={'fictional':adapter}))['source_state'],'SourceStateUnavailable')

    def test_future_package_provenance_is_readable_without_trusted_attribution(self):
        from Core.assessment_runs import prepare_run, complete_run
        from Core.assessment_replay import load_completed_package
        from Core.assessment_serialization import read_assessment_result
        from Core.assessment_history import file_hash
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);package=root/'package'
            execution=prepare_run('Initial','11111111-1111-1111-1111-111111111111',evaluated_at='2026-10-13',history_path=root/'history.json')
            result=build_assessment_result([],{'collection_context':{'identity':execution.identity}},evaluation_date='2026-10-13')
            snapshot=Path(complete_run(result,execution,package))
            value=json.loads(snapshot.read_text(encoding='utf-8'));value['producing_revision']['schema_version']='999.0.0'
            snapshot.write_text(json.dumps(value),encoding='utf-8')
            manifest_path=package/'assessment-run.json';manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
            manifest.update(ProducingRevision=value['producing_revision'],SnapshotHash=file_hash(snapshot))
            manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
            loaded=load_completed_package({'package_directory':str(package)})
            restored=read_assessment_result(loaded['completed_run_snapshot'])
            self.assertEqual(restored['identity']['RunId'],result['identity']['RunId'])
            self.assertEqual(restored['producing_revision']['source_state'],'SourceStateUnavailable')

    def test_replay_unknown_source_is_blocked(self):
        from Core.assessment_replay import render_snapshot
        args=SimpleNamespace(run_type=None,offline=True,replay_snapshot='fictional.json',services=[],source_policy='production')
        stored=build_assessment_result([],evaluation_date='2026-10-13')
        with patch('Core.assessment_serialization.read_assessment_result',return_value=stored):
            for state in ('SourceStateUnavailable','DetachedOrUnowned','DirtyUnclassified'):
                revision=dict(fictional_revision(),source_state=state)
                plan=build_run_plan(args,revision=revision)
                self.assertFalse(plan['execution_eligible'])
                revision['publication']=plan['producing_revision']['publication']
                with p.producing_context(revision),patch('Core.assessment_replay.read_assessment_result',side_effect=AssertionError('replay read')):
                    with self.assertRaises(ValueError):render_snapshot('fictional.json','never')

    def test_replay_workbook_and_html_retain_tool_warning(self):
        from Core.workbook_navigation import apply_workbook_navigation
        from openpyxl import Workbook
        original=fictional_revision();original['publication']=p.publication_policy(original)
        tool=fictional_revision();tool['publication']=p.publication_policy(tool,policy='engineering-validation',purpose='Fixture',reason='Review',acknowledged=True)
        result=build_assessment_result([],evaluation_date='2026-10-13');result.update(producing_revision=original,replay_tool_revision=tool)
        for role in ('assessment','technical'):
            workbook=Workbook();apply_workbook_navigation(workbook,result,{},role=role)
            values=' '.join(str(c.value) for row in workbook['Start Here'] for c in row)
            self.assertIn('NOT CUSTOMER-READY',values)
            self.assertIn('NOT CUSTOMER-READY',json.dumps(p.provenance_rows(result)))
        self.assertIn('NOT CUSTOMER-READY',p.provenance_html(result))
        self.assertEqual(result['producing_revision'],original)

    def test_material_options_change_configuration_hash(self):
        pairs={'lifecycle_report_max_age_days':(30,90),'provider_evidence_max_age_days':(30,90),
               'sam_report_max_age_days':(20,35),'dspm_report_max_age_days':(5,8),
               'interactive_collection_policy':('skip','prompt'),'purview_auth_mode':('auto','force'),
               'evaluation_date':('2026-10-01','2026-10-13'),'sharepoint_admin_url':('https://a.invalid','https://b.invalid'),
               'source_windows':({'start':'2026-01-01'},{'start':'2026-02-01'}),
               'preview_collectors':('none','auto'),'services':(['M365'],['Entra'])}
        for key,(first,second) in pairs.items():
            with self.subTest(option=key):self.assertNotEqual(p.configuration_hash({key:first}),p.configuration_hash({key:second}))
        self.assertEqual(p.configuration_hash({'services':['M365','M365','Entra']}),p.configuration_hash({'services':['Entra','M365']}))
        self.assertEqual(p.configuration_hash({'snapshot_json':'a/new.json','client_secret':'fictional'}),p.configuration_hash({'snapshot_json':'b/new.json'}))

    def test_plan_execution_configuration_parity(self):
        from Core.effective_configuration import resolve_effective
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'report.csv').write_text('fictional,row\n')
            payload={'tenant_id':'11111111-1111-1111-1111-111111111111','evaluation_date':'2026-10-01',
                'assessment_settings':{'report_format':'excel','lifecycle_report_max_age_days':45},
                'package':{'inputs':{'sam_report':['report.csv']},'files':[{'path':'report.csv','sha256':hashlib.sha256((root/'report.csv').read_bytes()).hexdigest()}]}}
            source=root/'collection.json';source.write_text(json.dumps(payload))
            args=SimpleNamespace(run_type='Initial',offline=True,collection_input=str(source),tenant_id=None,services=[],source_policy='production',report_format='html')
            effective,_,errors=resolve_effective(args);plan=build_run_plan(args,revision=fictional_revision())
            self.assertFalse(errors);self.assertTrue(plan['execution_eligible'])
            self.assertEqual(plan['sources']['imports_supplied']['sam_report'],1)
            self.assertEqual(plan['effective_configuration']['evaluation_date'],effective.evaluation_date)
            self.assertEqual(plan['effective_configuration']['report_format'],effective.report_format)
            self.assertEqual(json.loads(plan_json(plan)),plan)
            self.assertIn('Effective configuration: '+json.dumps(plan['effective_configuration'],sort_keys=True),plan_lines(plan,verbose=True))
            args.tenant_id='22222222-2222-2222-2222-222222222222'
            self.assertIn('saved_tenant_conflict',build_run_plan(args,revision=fictional_revision())['validation_errors'])
            args.collection_input=None
            self.assertIn('offline_evidence_required',build_run_plan(args,revision=fictional_revision())['validation_errors'])

    def test_plan_protects_existing_snapshot_before_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'baseline.json';target.write_text('immutable')
            args=SimpleNamespace(run_type='Initial',offline=False,services=[],tenant_id='11111111-1111-1111-1111-111111111111',source_policy='production',snapshot_json=str(target))
            self.assertIn('protected_output_destination',build_run_plan(args,revision=fictional_revision())['validation_errors'])
            self.assertEqual(target.read_text(),'immutable')

    def test_live_environment_resolution_is_read_only_and_shared(self):
        from Core.effective_configuration import resolve_effective, portable_configuration
        from Core.credentials_check import load_env_file
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{},clear=True):
            selected=Path(folder)/'fictional.env'
            selected.write_text('TENANT_ID=11111111-1111-1111-1111-111111111111\nPERMISSION_PROFILE=restricted\nDSPM_REPORT_MAX_AGE_DAYS=9\nCLIENT_SECRET=fictional\n')
            args=SimpleNamespace(offline=False,env_file=str(selected),services=[],source_policy='production')
            before=dict(os.environ);effective,_,errors=resolve_effective(args);plan=build_run_plan(args,revision=fictional_revision())
            self.assertFalse(errors);self.assertEqual(dict(os.environ),before)
            self.assertEqual(plan['sources']['permission_profile'],'restricted')
            self.assertEqual(plan['effective_configuration']['settings']['dspm_report_max_age_days'],9)
            load_env_file(str(selected));executed,_,errors=resolve_effective(args)
            self.assertFalse(errors);self.assertEqual(p.configuration_hash(effective),p.configuration_hash(executed))
            self.assertNotIn('fictional.env',json.dumps(portable_configuration(effective)))
            self.assertNotIn('fictional',json.dumps(portable_configuration(effective)))

    def test_invalid_effective_freshness_returns_blocked_plan(self):
        args=SimpleNamespace(offline=False,services=[],source_policy='production',tenant_id='11111111-1111-1111-1111-111111111111')
        with patch.dict(os.environ,{'DSPM_REPORT_MAX_AGE_DAYS':'bad','LIFECYCLE_REPORT_MAX_AGE_DAYS':'0'}):
            plan=build_run_plan(args,revision=fictional_revision())
        self.assertFalse(plan['execution_eligible']);self.assertIn('invalid_freshness_setting',plan['validation_errors'])
        self.assertIn('invalid_lifecycle_settings',plan['validation_errors'])

    def test_effective_environment_authentication_changes_configuration_hash(self):
        from Core.effective_configuration import resolve_effective
        args=SimpleNamespace(offline=False,services=[],source_policy='production')
        with patch.dict(os.environ,{},clear=True),patch('Core.credentials_check.read_env_file',return_value=('fictional',{},False)):
            initial,_,_=resolve_effective(args);first=p.configuration_hash(initial)
            for key,value in {'CLIENT_ID':'11111111-1111-1111-1111-111111111111','CERTIFICATE_PATH':'fictional.pem',
                'PURVIEW_CERTIFICATE_THUMBPRINT':'fictional','PURVIEW_ORGANIZATION':'fictional.onmicrosoft.com',
                'PURVIEW_INCLUDE_SPECIALIZED':'true','COPILOT_AUDIT_MAX_WAIT_MINUTES':'9'}.items():
                with self.subTest(option=key),patch.dict(os.environ,{key:value}):
                    effective,_,_=resolve_effective(args);self.assertNotEqual(first,p.configuration_hash(effective))
            with patch.dict(os.environ,{'CERTIFICATE_PATH':'first.pem'}):
                one,_,_=resolve_effective(args)
            with patch.dict(os.environ,{'CERTIFICATE_PATH':'second.pem'}):
                two,_,_=resolve_effective(args)
            self.assertEqual(p.configuration_hash(one),p.configuration_hash(two))

    def test_every_extracted_output_retains_engineering_qualification(self):
        from Core.export_recommendations import export_to_csv,export_to_json
        from Core.html_evidence_pages import write_html_evidence_pages
        from Core.assessment_package import record_package_run
        result=build_assessment_result([],evaluation_date='2026-10-13')
        rows=[{'Service':'M365','Recommendation':'Fictional action'}]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with __import__('contextlib').redirect_stdout(io.StringIO()):
                csv=export_to_csv(rows,filename='fixture.csv',output_dir=root,assessment_result=result)
                exported=export_to_json(rows,filename='fixture.json',output_dir=root,assessment_result=result)
                pages=write_html_evidence_pages({'tenant':'Fictional','findings':[],'tables':{}},root/'Evidence',assessment_result=result)
                record_package_run(root,mode='replay',tenant_id=None,collected_at=None,evaluation_date='2026-10-13',outputs={'evidence_bundle':{'assessment_result':result}})
            for path in (csv,exported,pages['index'],root/'operator-log.jsonl'):
                self.assertIn('NOT CUSTOMER-READY',Path(path).read_text(encoding='utf-8'))


class UnmockedSourceBoundaryTests(unittest.TestCase):
    def test_direct_writers_recheck_actual_source(self):
        from Core.export_recommendations import export_to_html,export_to_excel,export_to_csv,export_to_json
        from Core.assessment_serialization import write_assessment_result
        original=fictional_revision();changed=dict(original,source_state='DirtyUnclassified')
        with p.producing_context(original),patch.object(p,'capture_revision',return_value=changed),patch.object(Path,'mkdir',side_effect=AssertionError('directory mutation')):
            for writer in (export_to_html,export_to_excel,export_to_csv,export_to_json):
                with self.subTest(writer=writer.__name__),self.assertRaisesRegex(ValueError,'changed after preflight'):writer([])
            with self.assertRaisesRegex(ValueError,'changed after preflight'):write_assessment_result('never.json',{})
        for field in ('branch','catalog_version','remote_identifier','fingerprint_version'):
            changed=dict(original,**{field:'changed'})
            with self.subTest(field=field),p.producing_context(original),patch.object(p,'capture_revision',return_value=changed):
                with self.assertRaisesRegex(ValueError,'changed after preflight'):p.require_publication()

    def test_fixture_policy_cannot_bypass_production_gate(self):
        revision=fictional_revision();revision['publication']={'policy':'fictional-test-fixture','execution_eligible':True,'customer_publication_eligible':False,'output_qualification':'NOT CUSTOMER-READY'}
        with p.producing_context(revision),patch.object(p,'capture_revision',side_effect=AssertionError('Git must not run for invalid policy')):
            with self.assertRaises(ValueError):p.require_publication()

    def test_cli_replay_cannot_skip_source_policy(self):
        import main
        revision=dict(fictional_revision(),source_state='DirtyUnclassified')
        stored=build_assessment_result([],evaluation_date='2026-10-13')
        with patch('sys.argv',['main.py','--mode','offline','--replay-snapshot','fictional.json']),patch('Core.check_dependencies.check_dependencies'),\
             patch('Core.run_plan.capture_revision',return_value=revision),patch('Core.assessment_serialization.read_assessment_result',return_value=stored),\
             patch('Core.assessment_replay.render_snapshot',side_effect=AssertionError('replay publication')),__import__('contextlib').redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as exit:main.main()
        self.assertEqual(exit.exception.code,2)


@fictional_publication
class ExecutedSourceCorrections(unittest.TestCase):
    def test_stale_loaded_code_is_unverified(self):
        from types import ModuleType
        from Core.runtime_source import binding_errors
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'Core').mkdir();file=root/'Core'/'sample.py'
            source='def value():\n    return 1\n';file.write_text(source)
            module=ModuleType('Core.sample');module.__file__=str(file)
            exec(compile(source,str(file),'exec'),module.__dict__)
            self.assertFalse(binding_errors(root,{'Core.sample':module}))
            file.write_text('def value():\n    return 2\n')
            self.assertEqual(binding_errors(root,{'Core.sample':module})['Core/sample.py'],'RuntimeCodeDiffersFromSource')

    def test_foreign_product_module_is_not_read_or_fingerprinted(self):
        from types import ModuleType
        from Core.runtime_source import binding_errors
        module=ModuleType('Core.sample');module.__file__='/outside-preserved-archive/sample.py'
        with patch.object(Path,'read_bytes',side_effect=AssertionError('archive read')):
            errors=binding_errors(Path.cwd(),{'Core.sample':module})
        self.assertEqual(errors,{'Core/sample.py':'RuntimeSourceOutsideRepository'})

    def test_loaded_constant_differs_from_committed_source(self):
        from types import ModuleType
        from Core.runtime_source import binding_errors
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'Core').mkdir();file=root/'Core'/'sample.py';file.write_text('VERSION="1"\n')
            module=ModuleType('Core.sample');module.__file__=str(file);module.VERSION='2'
            self.assertEqual(binding_errors(root,{'Core.sample':module}),{'Core/sample.py':'RuntimeConstantDiffersFromSource'})

    def test_product_module_from_evidence_archive_is_not_read(self):
        from types import ModuleType
        from Core.runtime_source import binding_errors
        module=ModuleType('Core.sample');module.__file__=str(Path.cwd()/'Reports'/'archive'/'sample.py')
        with patch.object(Path,'read_bytes',side_effect=AssertionError('archive read')):
            self.assertEqual(binding_errors(Path.cwd(),{'Core.sample':module}),{'Core/sample.py':'RuntimeSourcePathMismatch'})

    def test_dynamic_module_in_evidence_archive_is_not_read(self):
        from types import ModuleType
        from Core.runtime_source import binding_errors
        module=ModuleType('dynamic_plugin');module.__file__=str(Path.cwd()/'Reports'/'archive'/'sample.py')
        with patch.object(Path,'read_bytes',side_effect=AssertionError('archive read')):
            self.assertEqual(binding_errors(Path.cwd(),{'dynamic_plugin':module}),{'dynamic_plugin.py':'RuntimeSourcePathMismatch'})

    def test_virtual_environment_dependencies_are_excluded_from_product_source(self):
        from types import ModuleType
        from Core.runtime_source import binding_errors
        module=ModuleType('azure.helper');module.__file__=str(Path.cwd()/'.venv'/'Lib'/'azure'/'helper.py')
        with patch.object(Path,'read_bytes',side_effect=AssertionError('dependency cache read')):
            self.assertFalse(binding_errors(Path.cwd(),{'azure.helper':module}))

    def test_unverified_dynamic_binary_product_module_is_blocked(self):
        from types import ModuleType
        from Core.runtime_source import binding_errors
        module=ModuleType('dynamic_plugin');module.__file__=str(Path.cwd()/'Core'/'plugin.pyd')
        with patch.object(Path,'read_bytes',side_effect=AssertionError('binary read')):
            self.assertEqual(binding_errors(Path.cwd(),{'dynamic_plugin':module}),{'dynamic_plugin.py':'UnsupportedLoadedSource'})

    def test_foreign_function_cannot_replace_product_binding(self):
        from types import ModuleType
        from Core.runtime_source import binding_errors
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'Core').mkdir();file=root/'Core'/'sample.py';file.write_text('def value():\n    return 1\n')
            module=ModuleType('Core.sample');module.__file__=str(file);module.value=lambda:2
            self.assertEqual(binding_errors(root,{'Core.sample':module}),{'Core/sample.py':'RuntimeFunctionBindingChanged'})

    def test_source_constant_composed_at_import_is_supported(self):
        from types import ModuleType
        from Core.runtime_source import binding_errors
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'Core').mkdir();file=root/'Core'/'sample.py'
            source='REPORT_CSS="base"\nREPORT_CSS+="extra"\n';file.write_text(source)
            module=ModuleType('Core.sample');module.__file__=str(file);exec(compile(source,str(file),'exec'),module.__dict__)
            self.assertFalse(binding_errors(root,{'Core.sample':module}))
            module.REPORT_CSS='older content'
            self.assertEqual(binding_errors(root,{'Core.sample':module}),{'Core/sample.py':'RuntimeConstantDiffersFromSource'})

    def test_declared_mutable_client_state_is_not_a_source_change(self):
        from types import ModuleType
        from Core.runtime_source import binding_errors
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'Core').mkdir();file=root/'Core'/'sample.py'
            source='ATTEMPTED=False\ndef collect():\n    global ATTEMPTED\n    ATTEMPTED=True\n';file.write_text(source)
            module=ModuleType('Core.sample');module.__file__=str(file);exec(compile(source,str(file),'exec'),module.__dict__)
            module.collect()
            self.assertFalse(binding_errors(root,{'Core.sample':module}))

    def test_cached_git_metadata_invalidates_on_index_bytes_not_timestamp(self):
        from Core.runtime_source import MetadataReader
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);git=root/'.git';git.mkdir();index=git/'index';index.write_bytes(b'original');before=index.stat()
            calls=[]
            def reader(*args):
                calls.append(args)
                if args==('rev-parse','--git-dir','--git-common-dir'):return b'.git\n.git\n'
                return index.read_bytes()
            cached=MetadataReader(root,reader);cached.refresh()
            self.assertEqual(cached('ls-files','-v','-z'),b'original');cached('ls-files','-v','-z')
            self.assertEqual(calls.count(('ls-files','-v','-z')),1)
            index.write_bytes(b'modified');os.utime(index,ns=(before.st_atime_ns,before.st_mtime_ns));cached.refresh()
            self.assertEqual(cached('ls-files','-v','-z'),b'modified');self.assertEqual(calls.count(('ls-files','-v','-z')),2)
            for command in (('status','--porcelain=v2'),('rev-parse','HEAD'),('rev-parse','HEAD^{tree}')):
                cached(*command);cached(*command);self.assertEqual(calls.count(command),2)

    def capture(self,root,content=None,flag='H'):
        file=root/'Core'/'sample.py';blob=hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest() if content is not None else None
        fake=FakeGit()
        def read(*args):
            if args==('ls-files','--stage','-z'):return f'100644 {blob} 0\tCore/sample.py\0'.encode() if blob else b''
            if args==('ls-files','-v','-z'):return f'{flag} Core/sample.py\0'.encode() if blob else b''
            return fake(*args)
        return p.capture_revision(root,git_reader=read)

    def test_status_hidden_tracked_modification_is_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'Core').mkdir();file=root/'Core'/'sample.py';file.write_bytes(b'x=1\n')
            self.assertEqual(self.capture(root,b'x=1\n')['source_state'],'CleanCommitted')
            file.write_bytes(b'x=2\n')
            self.assertEqual(self.capture(root,b'x=1\n')['source_state'],'DirtyReproducible')

    def test_ignored_python_is_detected_and_fingerprinted(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'Core').mkdir();file=root/'Core'/'sample.py';file.write_bytes(b'x=1\n')
            first=self.capture(root);self.assertEqual(first['source_state'],'DirtyReproducible')
            self.assertTrue(first['dirty_tree_fingerprint']);file.write_bytes(b'x=2\n')
            self.assertNotEqual(first['dirty_tree_fingerprint'],self.capture(root)['dirty_tree_fingerprint'])

    def test_hidden_index_flags_block_engineering(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'Core').mkdir();(root/'Core'/'sample.py').write_bytes(b'x=1\n')
            for flag in ('h','S','s'):
                revision=self.capture(root,b'x=1\n',flag)
                self.assertEqual(revision['source_state'],'DirtyUnclassified')
                self.assertFalse(p.publication_policy(revision,policy='engineering-validation',purpose='Fixture',reason='Review',acknowledged=True)['execution_eligible'])

    def test_nested_repository_is_not_reproducible(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);nested=root/'Core'/'nested';nested.mkdir(parents=True);(nested/'.git').write_text('gitdir: fictional')
            self.assertEqual(self.capture(root)['source_state'],'DirtyUnclassified')

    def test_inaccessible_executable_blocks_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'Core').mkdir();(root/'Core'/'sample.py').write_bytes(b'x=1\n')
            with patch.object(p,'_read',return_value=({'read_error':'PermissionError','content_sha256':None},None)):
                revision=self.capture(root,b'x=1\n')
            self.assertEqual(revision['source_state'],'DirtyUnclassified')
            self.assertFalse(p.publication_policy(revision)['execution_eligible'])

    def test_symlinked_executable_blocks_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'Core').mkdir();(root/'Core'/'sample.py').write_bytes(b'x=1\n')
            with patch.object(p,'_read',return_value=({'symlink':True},None)):
                self.assertEqual(self.capture(root,b'x=1\n')['source_state'],'DirtyUnclassified')

    def test_customer_and_generated_bytes_are_excluded_even_when_tracked(self):
        from test_repository_provenance import entry
        source=entry('Core/sample.py')
        for path in ('Reports/customer.json','.cache/customer.json','Core/Reports/customer.json','Core/__pycache__/sample.pyc'):
            with self.subTest(path=path):
                self.assertEqual(p.dirty_fingerprint([source]),p.dirty_fingerprint([source,entry(path,tracked=True)]))

    def test_untracked_executable_mode_changes_fingerprint(self):
        from test_repository_provenance import entry
        row=entry(tracked=False,status='??',executable_mode='100644')
        self.assertNotEqual(p.dirty_fingerprint([row]),p.dirty_fingerprint([dict(row,executable_mode='100755')]))

    def test_adapter_bytes_do_not_prove_semantic_change(self):
        from test_delta_lifecycle import pair
        from Core.run_comparability import evaluate_comparability
        before,after=pair();before['producing_revision']=fictional_revision();after['producing_revision']=deepcopy(before['producing_revision'])
        name=next(iter(after['producing_revision']['import_adapters']));after['producing_revision']['import_adapters'][name]['implementation_sha256']='f'*64
        reasons=[r['Code'] for r in evaluate_comparability(after,before)['Reasons']]
        self.assertIn('source_implementation_changed',reasons);self.assertNotIn('source_semantics_changed',reasons)

"""Fictional read-only Git protocols and source-policy regression cases."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from Core import repository_provenance as p
from provenance_fixture import fictional_revision


def entry(path='Core/sample.py',status='.M',tracked=True,content=b'x = 1\n',**extra):
    row=dict(path=path,status=status,tracked=tracked,ignored=False,
        head_blob='a'*40,index_blob='b'*40,content_sha256=hashlib.sha256(content).hexdigest())
    row.update(extra)
    return p.classify_entry(row,content)


class FakeGit:
    def __init__(self,status=b'',detached=False,remote='https://github.com/AMedrano117/M365-Copilot-Readiness-Assessment.git',unavailable=False):
        self.status=status; self.detached=detached; self.remote=remote; self.unavailable=unavailable;self.calls=[]
    def __call__(self,*args):
        self.calls.append(args)
        if self.unavailable:raise ValueError('metadata unavailable')
        if args[0]=='status':return self.status
        if args[0]=='symbolic-ref':
            if self.detached:raise ValueError('detached')
            return b'fictional-branch'
        if args[0]=='remote':return self.remote.encode()
        if args[0]=='rev-list':return b'2\t3'
        if args==('rev-parse','HEAD^{tree}'):return b'b'*40
        if args==('rev-parse','--abbrev-ref','--symbolic-full-name','@{upstream}'):return b'github/main'
        return b'a'*40


class ClassificationTests(unittest.TestCase):
    def test_collector_configuration_order_is_stable(self):
        self.assertEqual(p.collector_configuration_hash({'services':['b','a']}),p.collector_configuration_hash({'services':['a','b']}))
    def test_history_ignore_is_scoped_to_root_output(self):
        rules=(Path(__file__).resolve().parents[1]/'.gitignore').read_text()
        self.assertIn('/AssessmentHistories/',rules.splitlines())
        self.assertNotIn('*.json',rules.splitlines())
    def test_clean_status(self):self.assertEqual(p.parse_status(b''),[])
    def test_staged_tracked(self):self.assertEqual(entry(status='M.')['classification'],'IntendedProductSource')
    def test_unstaged_tracked(self):self.assertTrue(entry()['relevant_source'])
    def test_untracked_source(self):self.assertEqual(entry(status='??',tracked=False)['classification'],'IntendedProductSource')
    def test_ignored_cache(self):self.assertFalse(entry('.cache/cache.py',tracked=False,ignored=True)['relevant_source'])
    def test_generated_workbook(self):self.assertIn('UnsafeToCommit',entry('Reports/result.xlsx')['secondary_labels'])
    def test_customer_evidence(self):self.assertEqual(entry('Reports/evidence.csv')['classification'],'CustomerEvidence')
    def test_environment(self):self.assertEqual(entry('.env',tracked=False)['classification'],'LocalEnvironment')
    def test_unknown(self):self.assertEqual(entry('unknown.bin',tracked=False)['classification'],'UnknownOrReviewRequired')
    def test_mixed(self):self.assertEqual({entry()['classification'],entry('Reports/evidence.csv')['classification']},{'IntendedProductSource','CustomerEvidence'})
    def test_parser_order(self):
        raw=b'? Core/z.py\0? Core/a.py\0'
        self.assertEqual([r['path'] for r in p.parse_status(raw)],['Core/a.py','Core/z.py'])
    def test_inventory_never_mutates(self):
        fake=FakeGit()
        with tempfile.TemporaryDirectory() as folder:
            before=list(Path(folder).iterdir());p.inventory_repository(folder,git_reader=fake)
            self.assertEqual(list(Path(folder).iterdir()),before)
        self.assertEqual(fake.calls,[('status','--porcelain=v2','-z','--untracked-files=all')])
    def test_rename_unicode_and_spaces(self):
        raw=('2 R. N... 100644 100644 100644 '+'a'*40+' '+'b'*40+' R100 Core/é sample.py\0Core/old.py\0').encode()
        row=p.parse_status(raw)[0];self.assertEqual(row['path'],'Core/é sample.py');self.assertEqual(row['original_path'],'Core/old.py')
    def test_unmerged_is_unknown(self):self.assertEqual(entry(unmerged=True)['classification'],'UnknownOrReviewRequired')
    def test_invalid_source_is_unknown(self):self.assertEqual(entry(content=b'def broken(')['classification'],'UnknownOrReviewRequired')
    def test_symlink_is_unknown(self):self.assertEqual(entry(symlink=True)['classification'],'UnknownOrReviewRequired')
    def test_read_failure_is_unknown(self):self.assertEqual(entry(read_error='PermissionError')['classification'],'UnknownOrReviewRequired')
    def test_private_key_never_exposed(self):
        marker=b'-----BEGIN '+b'PRIVATE KEY-----\n' + b'fictional'
        row=entry(content=marker);self.assertEqual(row['secret_risk_types'],['private_key']);self.assertNotIn('fictional',json.dumps(row))
    def test_preserve_customer_workbook(self):self.assertFalse(entry('Reports/sample.xlsx')['may_delete'])
    def test_preserve_product_source(self):self.assertFalse(entry()['may_delete'])
    def test_preserve_purview(self):self.assertEqual(entry('Core/ai_baseline_import.py')['classification'],'PurviewImportDevelopment')
    def test_obsolete_not_guessed(self):self.assertNotEqual(entry('old.py')['classification'],'ObsoleteOrSuperseded')
    def test_generated_never_stageable(self):self.assertFalse(entry('Reports/result.xlsx')['may_stage'])
    def test_evidence_never_stageable(self):self.assertFalse(entry('Reports/source.csv')['may_commit'])
    def test_unknown_preserved(self):self.assertEqual(entry('scratch.bin')['disposition'],'PreserveInPlacePendingReview')
    def test_env_example_source(self):self.assertEqual(entry('.env.example',content=b'CLIENT_ID=example')['classification'],'IntendedProductSource')
    def test_tracked_cache_is_relevant(self):self.assertTrue(entry('.cache/settings.json')['relevant_source'])


class FingerprintTests(unittest.TestCase):
    def test_clean(self):self.assertIsNone(p.dirty_fingerprint([]))
    def test_repeatable(self):self.assertEqual(p.dirty_fingerprint([entry()]),p.dirty_fingerprint([entry()]))
    def test_order(self):
        rows=[entry('Core/a.py'),entry('Core/b.py')];self.assertEqual(p.dirty_fingerprint(rows),p.dirty_fingerprint(list(reversed(rows))))
    def test_tracked_bytes(self):self.assertNotEqual(p.dirty_fingerprint([entry()]),p.dirty_fingerprint([entry(content=b'x=2')]))
    def test_untracked_bytes(self):self.assertNotEqual(p.dirty_fingerprint([entry(tracked=False,content=b'x=1')]),p.dirty_fingerprint([entry(tracked=False,content=b'x=2')]))
    def test_timestamp_excluded(self):
        row=entry(); other=dict(row,modified_at='2099-01-01');self.assertEqual(p.dirty_fingerprint([row]),p.dirty_fingerprint([other]))
    def test_ignored_cache_excluded(self):self.assertEqual(p.dirty_fingerprint([entry()]),p.dirty_fingerprint([entry(),entry('.cache/a.py',tracked=False,ignored=True)]))
    def test_customer_artifact_excluded(self):self.assertEqual(p.dirty_fingerprint([entry()]),p.dirty_fingerprint([entry(),entry('Reports/test.xlsx',tracked=False)]))
    def test_stage_state(self):self.assertNotEqual(p.dirty_fingerprint([entry(status='M.')]),p.dirty_fingerprint([entry(status='.M')]))
    def test_index_blob(self):
        row=entry();changed=dict(row,index_blob='c'*40);self.assertNotEqual(p.dirty_fingerprint([row]),p.dirty_fingerprint([changed]))
    def test_no_content_in_output(self):
        value=p.dirty_fingerprint([entry(content=b'private_variable=123')]);self.assertEqual(len(value),64);self.assertNotIn('private_variable',value)
    def test_rename_old_path(self):
        row=entry();self.assertNotEqual(p.dirty_fingerprint([row]),p.dirty_fingerprint([dict(row,original_path='Core/earlier.py')]))
    def test_unknown_fingerprint_is_not_classification(self):self.assertIsNone(p.dirty_fingerprint([entry('unknown.bin',tracked=False)]))


class RevisionTests(unittest.TestCase):
    def capture(self,fake=None,entries=None):
        with patch.object(p,'inventory_repository',return_value=entries or []):
            return p.capture_revision(git_reader=fake or FakeGit(),config={'services':['Entra','M365']},generated_at='2026-10-12T17:00:00Z')
    def test_commit_tree(self):
        r=self.capture();self.assertEqual(r['commit_sha'],'a'*40);self.assertEqual(r['commit_tree_sha'],'b'*40)
    def test_clean(self):self.assertEqual(self.capture()['source_state'],'CleanCommitted')
    def test_dirty_classified(self):
        r=self.capture(entries=[entry()]);self.assertEqual(r['source_state'],'DirtyReproducible');self.assertTrue(r['dirty_tree_fingerprint'])
    def test_dirty_unknown(self):self.assertEqual(self.capture(entries=[entry('scratch.bin',tracked=False)])['source_state'],'DirtyUnclassified')
    def test_unavailable(self):self.assertEqual(self.capture(FakeGit(unavailable=True))['source_state'],'SourceStateUnavailable')
    def test_detached(self):self.assertEqual(self.capture(FakeGit(detached=True))['source_state'],'DetachedOrUnowned')
    def test_remote_credentials_removed(self):self.assertEqual(p.normalize_remote('https://user:credential@example.invalid/project.git?token=hidden#secret'),'example.invalid/project')
    def test_ssh_credentials_removed(self):self.assertEqual(p.normalize_remote('git@example.invalid:org/project.git'),'example.invalid/org/project')
    def test_local_remote_not_exposed(self):self.assertIsNone(p.normalize_remote('C:\\Users\\fictional\\private'))
    def test_methodology(self):self.assertEqual(self.capture()['methodology_version'],'4.0.0')
    def test_taxonomy(self):self.assertEqual(self.capture()['taxonomy_version'],'1.1.0')
    def test_adapter_versions(self):
        r=self.capture();self.assertIn('data_exposure_assessment',r['import_adapters']);self.assertTrue(r['import_adapters']['data_exposure_assessment']['implementation_sha256'])
    def test_config_deterministic(self):self.assertEqual(p.configuration_hash({'services':['M365','Entra']}),p.configuration_hash({'services':['Entra','M365']}))
    def test_config_ignores_secrets(self):self.assertEqual(p.configuration_hash({'client_secret':'fictional'}),p.configuration_hash({}))
    def test_config_survives_package_relocation(self):
        self.assertEqual(p.configuration_hash({'collection_input':'original/collection.json'}),p.configuration_hash({'collection_input':'copied/collection.json'}))
    def test_config_distinguishes_source_selections(self):
        self.assertNotEqual(p.configuration_hash({'sam_report':['first.csv']}),p.configuration_hash({'sam_report':['first.csv','second.csv']}))
    def test_repeated_packaged_copy_preserves_effective_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            first=Path(folder)/'original';second=Path(folder)/'packaged'
            first.mkdir();second.mkdir()
            (first/'readiness.csv').write_bytes(b'fictional,record\n')
            (second/'readiness.csv').write_bytes(b'fictional,record\n')
            self.assertEqual(p.configuration_hash({'reports_dir':[str(first)]}),p.configuration_hash({'reports_dir':[str(second),str(first)]}))
    def test_private_annotation_is_redacted_and_blocked(self):
        policy=p.publication_policy(fictional_revision(),policy='engineering-validation',purpose='Fixture validation',reason='C:/fictional/private-evidence',acknowledged=True)
        self.assertFalse(policy['execution_eligible'])
        self.assertNotIn('private-evidence',json.dumps(policy))
    def test_config_inputs_redacted(self):
        r=self.capture();self.assertNotIn(str(Path.home()),json.dumps(r))
    def test_counts(self):
        r=self.capture(entries=[entry(status='M.'),entry(tracked=False,status='??')]);self.assertEqual((r['staged_change_count'],r['tracked_modification_count'],r['untracked_entry_count']),(1,1,1))
    def test_ahead_behind(self):
        r=self.capture();self.assertEqual((r['ahead_count'],r['behind_count']),(2,3))
    def test_remote_mismatch(self):self.assertEqual(self.capture(FakeGit(remote='https://example.invalid/other'))['source_state'],'DetachedOrUnowned')
    def test_staged_secret_diagnostic(self):
        r=self.capture(entries=[entry('.env',status='A.',content=b'CLIENT_'+b'SECRET=fictional-not-a-real-credential')]);self.assertIn('secret_risk_file_staged',[d['code'] for d in r['diagnostics']])
    def test_only_read_commands(self):
        f=FakeGit();self.capture(f);self.assertTrue(all(c[0] in {'status','symbolic-ref','remote','rev-list','rev-parse'} for c in f.calls))
    def test_actual_inventory_reads_without_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'Core').mkdir();file=root/'Core'/'test.py';file.write_text('x=1')
            before=file.read_bytes();rows=p.inventory_repository(root,git_reader=FakeGit(b'? Core/test.py\0'))
            self.assertEqual(file.read_bytes(),before);self.assertEqual(rows[0]['classification'],'IntendedProductSource')


class PolicyTests(unittest.TestCase):
    def policy(self,state='CleanCommitted',**kwargs):return p.publication_policy(dict(fictional_revision(),source_state=state,dirty_tree_fingerprint='f'*64),**kwargs)
    def test_clean_production(self):self.assertTrue(self.policy()['customer_publication_eligible'])
    def test_dirty_unknown_blocked(self):self.assertFalse(self.policy('DirtyUnclassified')['execution_eligible'])
    def test_dirty_classified_production_blocked(self):self.assertFalse(self.policy('DirtyReproducible')['customer_publication_eligible'])
    def test_engineering_explicit(self):self.assertTrue(self.policy('DirtyReproducible',policy='engineering-validation',purpose='engineering-validation',reason='Fictional qualification',acknowledged=True)['execution_eligible'])
    def test_engineering_warning(self):self.assertIn('NOT CUSTOMER-READY',self.policy(policy='engineering-validation',purpose='validation',reason='fixture',acknowledged=True)['output_qualification'])
    def test_engineering_missing_ack(self):self.assertFalse(self.policy(policy='engineering-validation',purpose='validation',reason='fixture')['execution_eligible'])
    def test_unknown_not_overridden(self):self.assertFalse(self.policy('DirtyUnclassified',policy='engineering-validation',purpose='validation',reason='fixture',acknowledged=True)['execution_eligible'])
    def test_detached_not_overridden(self):self.assertFalse(self.policy('DetachedOrUnowned',policy='engineering-validation',purpose='validation',reason='fixture',acknowledged=True)['execution_eligible'])
    def test_programmatic_unit_result_allowed(self):
        from Core.assessment_result import build_assessment_result
        with patch.object(p,'capture_revision',side_effect=AssertionError('unit tests must not query Git')):
            r=build_assessment_result([],evaluation_date='2026-10-12');self.assertIn('producing_revision',r)
    def test_context_restored(self):
        before=p.current_revision()
        with p.producing_context(fictional_revision()):self.assertEqual(p.current_revision()['commit_sha'],'a'*40)
        self.assertEqual(p.current_revision(),before)
    def test_changed_source_blocks(self):
        r=fictional_revision();r['publication']=self.policy()
        with p.producing_context(r),patch.object(p,'capture_revision',return_value=dict(r,commit_sha='c'*40)):
            with self.assertRaisesRegex(ValueError,'changed after preflight'):p.verify_execution_source()
    def test_unchanged_source_allowed(self):
        r=fictional_revision();r['publication']=self.policy()
        with p.producing_context(r),patch.object(p,'capture_revision',return_value=r):p.verify_execution_source()
    def test_no_implicit_git_for_library(self):
        with p.producing_context(p.unrecorded_revision()),patch.object(p,'capture_revision',side_effect=AssertionError('unexpected')):p.verify_execution_source()


if __name__=='__main__':unittest.main()

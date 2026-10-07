"""Acceptance tests for the additive identity boundary; fictional evidence only."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TENANT = '11111111-1111-1111-1111-111111111111'
OTHER = '22222222-2222-2222-2222-222222222222'
STAMP = '2026-09-10T12:34:56.123456+00:00'


def api():
    from Core import assessment_identity as identity
    from Core import assessment_references as references
    return identity, references


def metadata():
    identity, _ = api()
    return identity.new_identity(TENANT, methodology_version='4', evaluated_at=STAMP)


def boundaries(meta):
    common = dict(assessment_id=meta['AssessmentId'], environment_id=meta['PrimaryEnvironmentId'])
    return {
        'control': dict(namespace='m365-readiness', control_id='IDENTITY.MFA'),
        'finding': dict(**common, provider='microsoft', control_id='IDENTITY.MFA',
                        condition_key='identity.mfa', population='pilot users', resource_scope='tenant'),
        'action': dict(**common, action_key='customer-approved-work-item-1'),
        'recommendation_catalog': dict(namespace='approved-catalog', catalog_key='mfa-enforcement'),
        'dataset': dict(environment_id=meta['PrimaryEnvironmentId'], provider='microsoft',
                        workload='entra', dataset_key='auth_methods'),
        'source_file': dict(environment_id=meta['PrimaryEnvironmentId'], sha256='a'*64),
    }


def graph():
    identity, _ = api()
    meta = metadata()
    keys = boundaries(meta)
    keys['capture'] = dict(dataset_id=identity.semantic_id('dataset', **keys['dataset']),
                           captured_at=STAMP, capture_key='capture-1')
    keys['native_record'] = dict(dataset_id=identity.semantic_id('dataset', **keys['dataset']), native_id='object-1')
    keys['evidence_record'] = dict(capture_id=identity.semantic_id('capture', **keys['capture']),
                                   record_key='object-1', content_digest='b'*64)
    keys['observation'] = dict(assessment_id=meta['AssessmentId'], run_id=meta['RunId'],
        environment_id=meta['PrimaryEnvironmentId'], provider='microsoft', control_id='IDENTITY.MFA',
        metric_id='enforcement', population='pilot users', resource_scope='tenant', window='snapshot',
        capture_id=identity.semantic_id('capture', **keys['capture']), evidence_level='configuration')
    keys['support'] = dict(finding_id=identity.semantic_id('finding', **keys['finding']),
        evidence_id=identity.semantic_id('evidence_record', **keys['evidence_record']),
        selection_role='observed', selector_version='1')
    meta['Entities'] = [identity.entity(kind, meta, key) for kind, key in keys.items()]
    by_type = {row['Type']: row['Id'] for row in meta['Entities']}
    meta['Aliases'] = [identity.typed_alias('ENT-001', 'RecommendationId', 'finding', meta,
                                           by_type['finding'], origin_artifact='result')]
    meta['References'] = [identity.reference(by_type[a], relation, by_type[b], b, meta)
        for a, relation, b in [('finding','evidence','evidence_record'),
            ('recommendation_catalog','finding','finding'), ('action','finding','finding'),
            ('observation','control','control'), ('finding','observation','observation')]]
    return {'tenant_id': TENANT, 'identity': meta, 'recommendations': [{'RecommendationId':'ENT-001'}]}


class PersistentIdentityTests(unittest.TestCase):
    def test_bound_compatibility_locators_and_selected_support(self):
        identity, references = api()
        meta=metadata()
        raw={'id':'fictional-native','flag':False,'value':0}
        source={'provider':'microsoft','workload':'entra','collected_at':STAMP,
                'availability_status':'available','complete':True,'population':'pilot users','scope':'tenant'}
        rec={'RecommendationId':'ENT-001','Provider':'microsoft','ControlId':'IDENTITY.MFA',
             'FindingKey':'identity.mfa','Population':'pilot users','EvidenceScope':'tenant',
             'RecommendationCatalogNamespace':'approved-catalog','RecommendationCatalogKey':'enforcement',
             'CustomerActionKey':'work-item-1','InvestigationEvidence':{'kind':'records','records':[raw]},
             'Observation':'Fictional statement','Recommendation':'Review fictional configuration'}
        result={'tenant_id':TENANT,'methodology_version':'4','evaluation_date':'2026-09-10',
                'recommendations':[rec], 'controls':[{'control_id':'IDENTITY.MFA'}], 'evidence':[]}
        bundle={'collection_context':{'identity':meta},'assessment_sources':{'auth_methods':[{'source':source,'records':[raw]}]}}
        before=copy.deepcopy((result,bundle))
        identity.attach_identity(result,bundle)
        self.assertEqual(bundle,before[1])
        self.assertEqual(result['recommendations'],before[0]['recommendations'])
        namespaces={row['Namespace'] for row in result['identity']['Aliases']}
        self.assertTrue({'SRC','EVD','DET','native:auth_methods'} <= namespaces)
        types={row['Type'] for row in result['identity']['Entities']}
        self.assertTrue({'finding','action','recommendation_catalog','support','native_record','capture','dataset','evidence_record'} <= types)
        self.assertEqual(references.validate_assessment_references(result),[])
        for row in result['identity']['Aliases']:
            self.assertIsNotNone(references.resolve_alias(result,row))

    def test_row_compatibility_alias_is_explicitly_unresolved_when_unscoped(self):
        identity, _=api()
        result={'tenant_id':TENANT,'recommendations':[{'RecommendationId':'ENT-001','InvestigationEvidence':
            {'kind':'records','records':[{'flag':False}]}}]}
        identity.attach_identity(result,{'collection_context':{'identity':metadata()}})
        rows=[row for row in result['identity']['Aliases'] if row['Namespace']=='ROW']
        self.assertTrue(rows)
        self.assertTrue(all(row['Value'].startswith('ROW-') and row['TargetId'] is None for row in rows))

    def test_new_shared_result_contains_execution_ids(self):
        from Core.assessment_result import build_assessment_result
        meta = metadata()
        result = build_assessment_result([], {'collection_context': {'identity': meta}},
                                          expected_tenant_id=TENANT, evaluation_date='2026-09-10')
        self.assertEqual(result['identity']['AssessmentId'], meta['AssessmentId'])
        self.assertEqual(result['identity']['RunId'], meta['RunId'])

    def test_separate_execution_gets_new_run(self):
        identity, _ = api()
        first = metadata()
        second = identity.new_run(first, TENANT, evaluated_at=STAMP)
        self.assertEqual(first['AssessmentId'], second['AssessmentId'])
        self.assertNotEqual(first['RunId'], second['RunId'])
        self.assertIsNone(second['RunType'])
        self.assertIsNone(second['BaselineRunId'])

    def test_other_environment_cannot_reuse_assessment(self):
        identity, _ = api()
        with self.assertRaises(ValueError):
            identity.new_run(metadata(), OTHER, evaluated_at=STAMP)

    def test_customer_names_cannot_seed_identity(self):
        identity, _ = api()
        with self.assertRaises(ValueError):
            identity.new_identity('Example Organization', methodology_version='4', evaluated_at=STAMP)

    def test_environment_is_namespaced_and_private(self):
        identity, _ = api()
        value = identity.environment_id(TENANT)
        self.assertNotIn(TENANT, value)
        self.assertNotEqual(value, identity.environment_id(OTHER))

    def test_input_permutation_and_unrelated_insertion(self):
        identity, _ = api()
        meta = metadata()
        keys = boundaries(meta)
        first = {kind:identity.semantic_id(kind, **key) for kind,key in keys.items()}
        keys['control2'] = dict(namespace='m365-readiness', control_id='DATA.DLP')
        second = {kind:identity.semantic_id(kind if kind != 'control2' else 'control', **dict(reversed(list(key.items()))))
                  for kind,key in reversed(list(keys.items()))}
        self.assertEqual(first, {kind:second[kind] for kind in first})

    def test_display_wording_severity_and_membership_are_not_keys(self):
        identity, _ = api()
        for kind,key in boundaries(metadata()).items():
            with self.subTest(kind=kind):
                self.assertEqual(identity.semantic_id(kind, **key), identity.semantic_id(kind, **key,
                    RecommendationId='CHANGED-99', severity='Critical', wording='changed',
                    owner='other', status='closed', target_date='2030-01-01', linked_findings=['new']))

    def test_observation_window_scope_population_and_provider_distinct(self):
        identity, _ = api()
        result = graph()
        observation = next(row for row in result['identity']['Entities'] if row['Type']=='observation')
        key = observation['Boundary']
        for field in ('window','resource_scope','population','provider','run_id','capture_id','evidence_level'):
            with self.subTest(field=field):
                self.assertNotEqual(identity.semantic_id('observation', **key),
                    identity.semantic_id('observation', **dict(key, **{field:'different'})))
        self.assertEqual(identity.semantic_id('observation', **key),
                         identity.semantic_id('observation', **key, wording='New explanation', severity='Low'))

    def test_native_ids_are_dataset_and_tenant_scoped(self):
        identity, _ = api()
        ids = []
        for tenant,dataset in ((TENANT,'users'),(OTHER,'users'),(TENANT,'signins')):
            source = identity.semantic_id('dataset', environment_id=identity.environment_id(tenant),
                                         provider='microsoft', workload='entra', dataset_key=dataset)
            ids.append(identity.semantic_id('native_record', dataset_id=source, native_id='same-native-id'))
        self.assertEqual(len(set(ids)),3)

    def test_control_definition_version_is_separate(self):
        identity, _ = api()
        key = boundaries(metadata())['control']
        self.assertEqual(identity.semantic_id('control', **key, definition_version='1'),
                         identity.semantic_id('control', **key, definition_version='2'))

    def test_missing_boundary_fails_safely(self):
        identity, _ = api()
        for kind,key in boundaries(metadata()).items():
            for field in key:
                with self.subTest(kind=kind, field=field):
                    incomplete = dict(key)
                    incomplete[field] = None
                    with self.assertRaises(ValueError):
                        identity.semantic_id(kind, **incomplete)

    def test_collision_is_not_silently_overwritten(self):
        identity, _ = api()
        registry = identity.IdentityRegistry()
        first = {'Id':'PCT-'+ 'a'*64,'Type':'control','IdentityKey':'one'}
        registry.add(first)
        with self.assertRaises(ValueError):
            registry.add(dict(first, IdentityKey='two'))
        self.assertEqual(registry.entities, [first])

    def test_hash_collision_compares_boundaries_and_is_fatal(self):
        identity, _ = api()
        registry=identity.IdentityRegistry(); meta=metadata()
        with patch.object(identity,'digest',return_value='a'*64):
            registry.add(identity.entity('control',meta,{'namespace':'m365-readiness','control_id':'IDENTITY.MFA'}))
            with self.assertRaises(identity.IdentityCollisionError):
                registry.add(identity.entity('control',meta,{'namespace':'m365-readiness','control_id':'DATA.DLP'}))

    def test_dangling_boundary_capture_is_detected(self):
        identity, references=api(); result=graph()
        old=next(row for row in result['identity']['Entities'] if row['Type']=='capture')
        result['identity']['Entities'].remove(old)
        self.assertIn('dangling_reference',{row['code'] for row in references.validate_assessment_references(result)})

    def test_undeclared_control_obligation_is_detected(self):
        _, references=api(); result=graph()
        result['identity']['Entities']=[row for row in result['identity']['Entities'] if row['Type']!='control']
        self.assertIn('dangling_reference',{row['code'] for row in references.validate_assessment_references(result)})

    def test_missing_shape_returns_structured_diagnostics(self):
        _, references=api(); result=graph(); result['identity']['Entities']={}
        self.assertIn('invalid_shape',{row['code'] for row in references.validate_assessment_references(result)})

    def test_malformed_boundary_returns_diagnostics_without_deleting_record(self):
        _, references=api(); result=graph(); result['identity']['Entities'][0]['Boundary']=['invalid']
        before=copy.deepcopy(result)
        self.assertIn('missing_scope',{row['code'] for row in references.validate_assessment_references(result)})
        self.assertEqual(result,before)

    def test_two_engagements_in_same_environment_are_not_grouped(self):
        first=metadata(); second=metadata()
        self.assertEqual(first['PrimaryEnvironmentId'],second['PrimaryEnvironmentId'])
        self.assertNotEqual(first['AssessmentId'],second['AssessmentId'])

    def test_repeated_shared_build_and_live_offline_preserve_ids(self):
        from Core.assessment_result import build_assessment_result
        seed=metadata(); bundle={'collection_context':{'identity':seed,'mode':'live'}}
        first=build_assessment_result([],bundle,evaluation_date='2026-09-10',expected_tenant_id=TENANT)
        bundle['collection_context']['mode']='offline'
        second=build_assessment_result([],bundle,evaluation_date='2026-09-10',expected_tenant_id=TENANT)
        self.assertEqual(first['identity'],second['identity'])
        self.assertEqual(first['identity']['RunId'],seed['RunId'])

    def test_provider_and_scoped_finding_boundaries_are_material(self):
        identity,_=api(); key=boundaries(metadata())['finding']
        for field in ('provider','population','resource_scope','environment_id','condition_key'):
            with self.subTest(field=field):
                self.assertNotEqual(identity.semantic_id('finding',**key),
                    identity.semantic_id('finding',**dict(key,**{field:'different'})))

    def test_declared_foreign_source_tenant_blocks_publication(self):
        identity,references=api(); result={'tenant_id':TENANT,'recommendations':[]}
        bundle={'collection_context':{'identity':metadata()},'assessment_sources':{'users':[{'records':[{'id':'fictional'}],
            'source':{'provider':'microsoft','workload':'entra','tenant_id':OTHER,'collected_at':STAMP}}]}}
        identity.attach_identity(result,bundle)
        self.assertIn('cross_environment',{row['code'] for row in result['identity_validation']})
        with self.assertRaises(ValueError):
            references.require_valid_assessment(result)

    def test_retained_file_identity_does_not_require_dataset_provider(self):
        identity,_=api(); result={'tenant_id':TENANT,'recommendations':[]}
        bundle={'collection_context':{'identity':metadata()},'assessment_sources':{'legacy':[{'records':[],
            'source':{'source_file':'fictional.json','source_hash':'a'*64,'tenant_id':TENANT}}]}}
        identity.attach_identity(result,bundle)
        aliases=[row for row in result['identity']['Aliases'] if row['Namespace']=='source_file']
        self.assertEqual(len(aliases),1)
        self.assertTrue(aliases[0]['TargetId'].startswith('PFL-'))

    def test_builder_retains_duplicate_invalid_reference_declarations(self):
        identity,_=api(); result=graph(); meta=result['identity']
        invalid=dict(meta['References'][0],TargetId='PEV-missing')
        identity.attach_identity(result,{'collection_context':{'identity':meta},
            'identity_entities':meta['Entities'],'identity_references':[invalid,copy.deepcopy(invalid)]})
        self.assertEqual(sum(row['TargetId']=='PEV-missing' for row in result['identity']['References']),2)
        self.assertIn('dangling_reference',{row['code'] for row in result['identity_validation']})

    def test_duplicate_observations_are_not_merged(self):
        from Core.assessment_result import build_assessment_result
        meta = metadata()
        declared = next(row for row in graph()['identity']['Entities'] if row['Type']=='observation')
        # Duplicate declarations are invalid, not an instruction to merge facts.
        declared.update(AssessmentId=meta['AssessmentId'], RunId=meta['RunId'], PrimaryEnvironmentId=meta['PrimaryEnvironmentId'])
        result = build_assessment_result([], {'collection_context':{'identity':meta},
            'identity_entities':[declared, copy.deepcopy(declared)]}, expected_tenant_id=TENANT, evaluation_date='2026-09-10')
        self.assertEqual(sum(row['Type']=='observation' for row in result['identity']['Entities']),2)
        self.assertTrue(any(row['code']=='duplicate_id' for row in result['identity_validation']))

    def test_alias_single_target_resolves(self):
        _, references = api()
        result = graph()
        alias = result['identity']['Aliases'][0]
        self.assertEqual(references.resolve_alias(result, alias)['Id'], alias['TargetId'])

    def test_alias_ambiguity_is_not_first_match(self):
        _, references = api()
        result = graph()
        alias = result['identity']['Aliases'][0]
        result['identity']['Aliases'].append(dict(alias, TargetId=result['identity']['Entities'][0]['Id']))
        self.assertIsNone(references.resolve_alias(result, alias))
        self.assertIn('ambiguous_alias', {row['code'] for row in references.validate_assessment_references(result)})

    def test_alias_scope_is_part_of_lookup(self):
        _, references = api()
        result = graph()
        alias = result['identity']['Aliases'][0]
        self.assertIsNone(references.resolve_alias(result, dict(alias, AssessmentId='AST-other')))

    def test_valid_graph_passes(self):
        _, references = api()
        self.assertEqual(references.validate_assessment_references(graph()), [])

    def test_validation_is_deterministic_and_does_not_mutate(self):
        _, references = api()
        result = graph()
        result['identity']['References'][0]['TargetId']='PEV-missing'
        before = copy.deepcopy(result)
        first = references.validate_assessment_references(result)
        self.assertEqual(result,before)
        for field in ('Entities','Aliases','References'):
            result['identity'][field].reverse()
        self.assertEqual(first,references.validate_assessment_references(result))


def invalid_case(code, mutate):
    def test(self):
        _, references = api()
        result = graph()
        mutate(result)
        diagnostics = references.validate_assessment_references(result)
        self.assertIn(code, {row['code'] for row in diagnostics})
        self.assertTrue(all(set(('severity','code','subject','message')) <= set(row) for row in diagnostics))
    return test


for name,code,mutation in [
    ('duplicate_id','duplicate_id',lambda r:r['identity']['Entities'].append(copy.deepcopy(r['identity']['Entities'][0]))),
    ('cross_type','cross_type_id',lambda r:r['identity']['Entities'].append(dict(r['identity']['Entities'][0],Type='action'))),
    ('dangling_evidence','dangling_reference',lambda r:r['identity']['References'][0].update(TargetId='PEV-missing')),
    ('dangling_recommendation_finding','dangling_reference',lambda r:r['identity']['References'][1].update(TargetId='PFI-missing')),
    ('dangling_action_finding','dangling_reference',lambda r:r['identity']['References'][2].update(TargetId='PFI-missing')),
    ('dangling_control','dangling_reference',lambda r:r['identity']['References'][3].update(TargetId='PCT-missing')),
    ('dangling_observation','dangling_reference',lambda r:r['identity']['References'][4].update(TargetId='POB-missing')),
    ('invalid_type','invalid_type',lambda r:r['identity']['References'][0].update(TargetType='unknown')),
    ('wrong_relation_type','invalid_relation',lambda r:r['identity']['References'][0].update(TargetType='action')),
    ('cross_assessment','cross_assessment',lambda r:r['identity']['Entities'][0].update(AssessmentId='AST-other')),
    ('cross_environment','cross_environment',lambda r:r['identity']['Entities'][0].update(PrimaryEnvironmentId='ENV-other')),
    ('cross_tenant','cross_environment',lambda r:r.update(tenant_id=OTHER)),
    ('run_ownership','run_ownership',lambda r:r['identity']['Entities'][0].update(RunId='RUN-other')),
    ('missing_scope','missing_scope',lambda r:r['identity']['Entities'][0].pop('AssessmentId')),
    ('display_id','display_id',lambda r:r['identity']['Entities'][0].update(Id='ENT-001')),
    ('duplicate_alias','duplicate_alias',lambda r:r['identity']['Aliases'].append(copy.deepcopy(r['identity']['Aliases'][0]))),
    ('missing_alias_target','unresolved_alias',lambda r:r['identity']['Aliases'][0].update(TargetId=None)),
    ('dangling_alias','dangling_alias',lambda r:r['identity']['Aliases'][0].update(TargetId='PFI-missing')),
    ('retired_uid','retired_identity',lambda r:r['recommendations'][0].update(finding_uid='FND-old')),
    ('retired_package','retired_identity',lambda r:r.update(dashboard_package={'id':'old'})),
    ('retired_alias_namespace','retired_identity',lambda r:r['identity']['Aliases'][0].update(Namespace='finding_uid')),
    ('boundary_tamper','identity_mismatch',lambda r:r['identity']['Entities'][0]['Boundary'].update(control_id='DATA.DLP')),
    ('invalid_metadata','missing_scope',lambda r:r['identity'].update(RunId=None)),
]:
    setattr(PersistentIdentityTests, 'test_validation_'+name, invalid_case(code,mutation))


class IdentitySerializationTests(unittest.TestCase):
    def test_snapshot_render_replay_preserves_identity_and_compatibility(self):
        import test_offline_report as offline_tests
        from synthetic_package_fixture import create_synthetic_package
        from Core.offline_collection import load_collection
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            collection=Path(create_synthetic_package(root/'inputs',active_incident=True))
            original=collection.read_bytes()
            first_path=root/'first.json'; second_path=root/'second.json'
            runner=offline_tests.OfflineReportTests()
            first_html=runner.run_cli(['--collection-input',str(collection),'--evaluation-date','2026-09-15',
                '--snapshot-json',str(first_path),'--extra-exports','evidence-pages'],root)
            package=load_collection(collection)['package_directory']
            second_html=runner.run_cli(['--collection-input',str(Path(package)/'rebuild.json'),
                '--snapshot-json',str(second_path),'--extra-exports','evidence-pages'],root)
            first=json.loads(first_path.read_text(encoding='utf-8'))
            second=json.loads(second_path.read_text(encoding='utf-8'))
            self.assertEqual(first['identity'],second['identity'])
            self.assertEqual(first['counts'],second['counts'])
            self.assertEqual([row['RecommendationId'] for row in first['recommendations']],
                             [row['RecommendationId'] for row in second['recommendations']])
            self.assertTrue(first['identity']['AssessmentId'])
            self.assertTrue(first['identity']['RunId'])
            self.assertEqual(collection.read_bytes(),original)
            runner.assertLocalHtmlLinksExist(first_html.parent)
            runner.assertLocalHtmlLinksExist(second_html.parent)

    def test_round_trip_repeated_serialization(self):
        from Core.assessment_serialization import write_assessment_result, read_assessment_result
        result = graph()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'snapshot.json'
            write_assessment_result(path,result)
            first = path.read_bytes()
            loaded = read_assessment_result(path)
            self.assertEqual(loaded['identity'],result['identity'])
            write_assessment_result(path,loaded)
            self.assertEqual(first,path.read_bytes())
            self.assertEqual(loaded['recommendations'][0]['RecommendationId'],'ENT-001')
            self.assertNotIn('dashboard', loaded)

    def test_legacy_snapshot_no_guessed_continuity(self):
        from Core.assessment_serialization import read_assessment_result
        legacy = {'tenant_id':TENANT,'recommendations':[{'RecommendationId':'ENT-001','EvidenceId':'EV-old'}], 'custom':{'kept':True}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'old.json'
            path.write_text(json.dumps(legacy),encoding='utf-8')
            before=path.read_bytes()
            result=read_assessment_result(path)
            self.assertEqual(path.read_bytes(),before)
            for field in ('AssessmentId','RunId','RunType','BaselineRunId','PrimaryEnvironmentId'):
                self.assertIsNone(result['identity'][field])
            self.assertEqual(result['identity']['State'],'legacy')
            self.assertEqual(result['custom'],legacy['custom'])
            self.assertEqual(result['recommendations'],legacy['recommendations'])
            self.assertTrue(result['identity_validation'])

    def test_validation_blocks_before_creating_file(self):
        from Core.assessment_serialization import write_assessment_result
        result = graph()
        result['identity']['References'][0]['TargetId']='PEV-missing'
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'new'/'invalid.json'
            with self.assertRaises(ValueError):
                write_assessment_result(path,result)
            self.assertFalse(path.parent.exists())

    def test_diagnostics_persist_and_unresolved_legacy_does_not_block(self):
        from Core.assessment_serialization import write_assessment_result, read_assessment_result
        result=graph()
        result['identity']['Aliases'][0]['TargetId']=None
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'snapshot.json'
            write_assessment_result(path,result)
            loaded=read_assessment_result(path)
            self.assertIn('unresolved_alias',{row['code'] for row in loaded['identity_validation']})

    def test_full_timestamp_preserved_without_changing_legacy_ev_id(self):
        from Core.evidence_contract import normalize_observation
        from Core.assessment_serialization import write_assessment_result, read_assessment_result
        fact=dict(tenant_id=TENANT,observed_at=STAMP,availability='available',value=0,
                  complete=True,population='pilot users',scope='tenant',window='snapshot')
        first=normalize_observation(fact,evaluation_date='2026-09-10')
        again=normalize_observation(first,evaluation_date='2026-09-10')
        self.assertEqual(first['evidence_id'],again['evidence_id'])
        self.assertEqual(first['observed_at'],'2026-09-10')
        self.assertEqual(first['source_observed_at'],STAMP)
        result=graph(); result['evidence']=[first]
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'snapshot.json'
            write_assessment_result(path,result)
            self.assertEqual(read_assessment_result(path)['evidence'][0]['source_observed_at'],STAMP)

    def test_collection_checkpoint_and_offline_replay_preserve_identity(self):
        from Core.offline_collection import save_collection, load_collection, collection_context, empty_service_results
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'collection.json'; checkpoint={}
            kwargs=dict(tenant_id=TENANT,tenant_name='Fictional',service_results=empty_service_results(),
                        collected_at=STAMP,evaluation_date='2026-09-10',_checkpoint_package=checkpoint)
            save_collection(path,**kwargs)
            first=load_collection(path)
            save_collection(path,**kwargs)
            second=load_collection(path)
            self.assertEqual(first['identity'],second['identity'])
            live=collection_context(first,path,mode='live')['identity']
            offline=collection_context(second,path,mode='offline')['identity']
            self.assertEqual(live,offline)

    def test_rebuild_recipe_carries_identity(self):
        from Core.assessment_package import save_rebuild_recipe, load_rebuild_recipe
        from types import SimpleNamespace
        meta=metadata()
        with tempfile.TemporaryDirectory() as folder:
            args=SimpleNamespace(evaluation_date='2026-09-10', report_format='both')
            recipe=save_rebuild_recipe(folder,args,tenant_id=TENANT,tenant_name='Fictional',identity=meta)
            loaded=load_rebuild_recipe(Path(folder)/'rebuild.json',recipe)
            self.assertEqual(loaded['identity'],meta)


if __name__ == '__main__':
    unittest.main()

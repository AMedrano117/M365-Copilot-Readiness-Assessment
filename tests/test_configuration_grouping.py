"""Fictional presentation membership, semantic boundaries and lineage regressions."""
import copy
import json
import unittest

from Core.configuration_groups import group_observations, build_configuration_groups, validate_configuration_groups


def observation(object_id='site-a', **values):
    row = dict(finding_id='M365-fiction', configuration_key='SharingCapability', configuration='SharePoint external sharing',
               observed='ExternalUserSharingOnly', recommended='Guests or stricter', assessment='Meets',
               object_type='SharePoint site', object_id=object_id, scope='site', provider='SharePoint', environment='Commercial',
               period={'capture':'2026-10-09T12:00:00.0000007Z'}, collection_state='complete', qualification=[],
               conflict_state=False, configuration_source='unknown', evidence_ids=['EVD-'+str(object_id)], source_refs=[],
               raw_observed='ExternalUserSharingOnly')
    row.update(values)
    return row


class ConfigurationGroupingTests(unittest.TestCase):
    def test_equivalent_objects_have_one_group_and_all_members(self):
        rows=[observation(),observation('site-b')]; before=copy.deepcopy(rows)
        model=group_observations(rows)
        self.assertEqual(len(model['groups']),1)
        self.assertEqual(model['groups'][0]['affected_object_count'],2)
        self.assertEqual(model['groups'][0]['total_objects_assessed'],2)
        self.assertEqual(len(model['members']),2)
        self.assertEqual(rows,before)
        self.assertEqual(validate_configuration_groups(model),[])

    def test_every_material_boundary_splits_groups(self):
        changes=dict(finding_id='other',configuration_key='DefaultSharingLinkType',configuration='Other setting',
                     observed='Anyone',recommended='Specific people',assessment='Change',object_type='OneDrive account',
                     scope='tenant',provider='Graph',environment='Government',period={'capture':'2026-09-01'},
                     collection_state='partial',qualification=['subset'],conflict_state=True,configuration_source='inherited')
        for key,value in changes.items():
            with self.subTest(key=key):
                self.assertEqual(len(group_observations([observation(),observation('site-b',**{key:value})])['groups']),2)

    def test_identity_is_order_independent_and_not_first_evidence(self):
        rows=[observation(),observation('site-b')]
        self.assertEqual(group_observations(rows),group_observations(rows[::-1]))
        other=group_observations([observation(evidence_ids=['EVD-new'])])
        self.assertEqual(other['groups'][0]['group_id'],group_observations(rows)['groups'][0]['group_id'])

    def test_duplicate_capture_does_not_inflate_distinct_objects(self):
        rows=[observation(),observation(evidence_ids=['EVD-another'])]
        result=group_observations(rows)
        self.assertEqual(result['groups'][0]['affected_object_count'],1)
        self.assertEqual(len(result['members']),2)
        self.assertEqual(result['groups'][0]['evidence_count'],2)

    def test_partial_population_is_never_a_tenant_denominator(self):
        group=group_observations([observation(collection_state='partial')])['groups'][0]
        self.assertEqual(group['total_objects_assessed'],1)
        self.assertIsNone(group['tenant_population_total'])
        self.assertIn('returned',group['population_qualification'].lower())

    def test_conflicting_observations_of_same_object_remain_separate(self):
        result=group_observations([observation(),observation(observed='Anyone',assessment='Conflict',conflict_state=True)])
        self.assertEqual(len(result['groups']),2)
        self.assertTrue(all(g['conflicting_object_count']==1 for g in result['groups']))

    def test_unknown_object_is_not_a_fabricated_count(self):
        group=group_observations([observation(object_id=None)])['groups'][0]
        self.assertEqual(group['affected_object_count'],0)
        self.assertEqual(group['unidentified_record_count'],1)

    def test_validation_rejects_membership_and_count_tampering(self):
        result=group_observations([observation()]); result['groups'][0]['affected_object_count']=9
        self.assertTrue(validate_configuration_groups(result))
        result=group_observations([observation()]); result['members'][0]['group_id']='CFG-missing'
        self.assertTrue(validate_configuration_groups(result))


def selection():
    state=dict(available=True,complete=True,availability_status='available',settings_verified=True,collected_at='2026-10-09T12:00:00.1234567Z')
    sources=[dict(dataset_id='SRC-sites',dataset='sharepoint_site_settings',source=state),
             dict(dataset_id='SRC-tenant',dataset='sharepoint_tenant_settings',source=state)]
    raw=[dict(Id='site-'+name,Url='https://example.invalid/'+name,Title='Fictional '+name,
              SharingCapability=mode,DefaultSharingLinkType=link) for name,mode,link in [('a',1,0),('b',1,0),('c',2,2)]]
    evidence=[dict(record_id='EVD-'+str(n),dataset_id='SRC-sites',raw=r,source_record_index=n) for n,r in enumerate(raw)]
    records=[dict(record_id='DET-'+str(n),fields=dict(siteId=r['Id'],siteUrl=r['Url'],scope='site'),
                  evidence_record_ids=[evidence[n]['record_id']],source_refs=[dict(dataset='sharepoint_site_settings',dataset_index=0,record_index=n)]) for n,r in enumerate(raw)]
    return dict(tenant_id='fictional-tenant',sources=sources,evidence_records=evidence,
                findings=[dict(finding_id='M365-fiction',finding_key='sharepoint.sharing.partial_settings',records=records)])


class SharePointGroupingTests(unittest.TestCase):
    def test_three_sites_four_groups(self):
        result=build_configuration_groups(selection())
        self.assertEqual(len(result['groups']),4)
        self.assertEqual(sorted(g['affected_object_count'] for g in result['groups']),[1,1,2,2])
        self.assertTrue(all(g['total_objects_assessed']==3 for g in result['groups']))
        self.assertTrue(all(g['configuration_source']=='unknown' for g in result['groups']))

    def test_native_lineage_and_raw_precision_survive_serialization(self):
        payload=selection(); before=copy.deepcopy(payload); result=build_configuration_groups(payload)
        self.assertEqual(payload,before)
        self.assertEqual({i for m in result['members'] for i in m['evidence_ids']},{'EVD-0','EVD-1','EVD-2'})
        self.assertTrue(all(m['source_refs'] and m['source_capture']['collected_at'].endswith('1234567Z') for m in result['members']))
        self.assertEqual(json.loads(json.dumps(result)),result)

    def test_unverified_list_defaults_are_qualified_review(self):
        payload=selection(); payload['sources'][0]['source'].pop('settings_verified')
        result=build_configuration_groups(payload)
        self.assertTrue(all(g['assessment']=='Review' for g in result['groups']))
        self.assertTrue(all(g['qualification'] for g in result['groups']))

    def test_origin_object_type_and_tenant_remain_distinct(self):
        payload=selection(); payload['evidence_records'][1]['raw']['ConfigurationSource']='inherited'
        payload['evidence_records'][2]['raw']['ObjectType']='OneDrive account'
        result=build_configuration_groups(payload)
        self.assertEqual(len(result['groups']),6)

    def test_source_conflict_and_qualification_are_not_lost(self):
        payload=selection(); payload['sources'][0]['source'].update(conflicting=True,limitations=['retained disagreement'])
        result=build_configuration_groups(payload)
        self.assertTrue(all(g['assessment']=='Conflict' and g['conflict_state'] for g in result['groups']))
        self.assertTrue(all('retained disagreement' in g['qualification'] for g in result['groups']))


if __name__=='__main__': unittest.main()

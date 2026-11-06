"""Content & Sharing rule and invariant tests (fictional data only).

Expected results are declared from documented Microsoft behavior and the
repository methodology before production logic runs (see
docs/CONTENT_SHARING_VALIDATION.md). Every case calls production functions.
"""
import copy
import unittest
from types import SimpleNamespace

from Core.evidence_layer import _build_sharepoint_governance_sheet, _build_verified_strengths
from Core.evidence_records import expand_finding_records
from Core.sharepoint_governance import (build_sharepoint_recommendations, merge_sharepoint_payloads,
                                        summarize_sharepoint_governance)

EVALUATION = '2026-10-20'
SPO_STATE = {'available': True, 'availability_status': 'available', 'records_collected': 1, 'pages_collected': 1,
             'truncated': False, 'reason': '', 'coverage': 'full'}
GRAPH_STATE = dict(SPO_STATE, coverage='partial', source='Microsoft Graph SharePoint tenant settings')
COMPLIANT = {'SharingCapability': 'ExternalUserSharingOnly', 'OneDriveSharingCapability': 'ExternalUserSharingOnly',
             'DefaultSharingLinkType': 'Direct', 'RequireAnonymousLinksExpireInDays': 0,
             'FileAnonymousLinkType': 'View', 'FolderAnonymousLinkType': 'View',
             'ExternalUserExpirationRequired': True, 'ExternalUserExpireInDays': 60,
             'PreventExternalUsersFromResharing': True, 'LegacyAuthProtocolsEnabled': False}


def spo(settings, sites=None, dag=None):
    """A SharePoint admin payload in the collector's shape."""
    return {'source': 'SharePoint Online Management Shell', 'available': True, 'availability_status': 'available',
            'tenant': {'available': True, 'settings': copy.deepcopy(settings), 'coverage': 'full'},
            'sites': {'available': sites is not None, 'items': copy.deepcopy(sites or [])},
            'dag_reports': {'available': dag is not None, 'reports': copy.deepcopy(dag or [])},
            'dag_activity_data': {'available': False, 'items': []},
            'collection_status': {'sharepoint_tenant_settings': dict(SPO_STATE)}}


def graph(settings):
    return {'source': 'Microsoft Graph SharePoint tenant settings', 'available': True, 'availability_status': 'partial',
            'tenant': {'available': True, 'settings': copy.deepcopy(settings), 'coverage': 'partial', 'unavailable_fields': []},
            'sites': {'available': False, 'items': []}, 'dag_reports': {'available': False, 'reports': []},
            'dag_activity_data': {'available': False, 'items': []},
            'collection_status': {'sharepoint_tenant_settings_graph': dict(GRAPH_STATE)}}


def findings(payload):
    return {row['FindingKey']: row for row in build_sharepoint_recommendations(payload, evaluation_date=EVALUATION)
            if str(row.get('FindingKey') or '').startswith('sharepoint.sharing.')}


def strength(payload):
    rows = [row for row in _build_verified_strengths(None, None, SimpleNamespace(sharepoint_governance=payload))
            if row['Key'] == 'sharepoint-sharing-restricted']
    return rows[0] if rows else None


def setting_result(rows, name):
    return next(item for item in rows if item['setting'] == name)


class NumericEnumTests(unittest.TestCase):
    """SharePoint PowerShell serializes enums as numbers; member 0 is a value, not an absent one."""

    def test_numeric_zero_values_are_named(self):
        from Core.sharepoint_governance import setting_label, setting_name
        self.assertEqual(setting_name('SharingCapability', 0), 'disabled')
        self.assertEqual(setting_name('OneDriveSharingCapability', '0'), 'disabled')
        self.assertEqual(setting_name('DefaultSharingLinkType', 0), 'none')
        self.assertEqual(setting_label('SharingCapability', 0), 'off')
        self.assertEqual(setting_name('SharingCapability', None), '')

    def test_numeric_disabled_boundary_is_evaluated(self):
        found = findings(spo({'SharingCapability': 0, 'OneDriveSharingCapability': 0, 'DefaultSharingLinkType': 1}))
        self.assertFalse(found)
        self.assertIsNotNone(strength(spo({'SharingCapability': 0, 'OneDriveSharingCapability': 0, 'DefaultSharingLinkType': 1})))


class DefaultLinkSemanticsTests(unittest.TestCase):
    """Set-SPOTenant: DefaultSharingLinkType None uses the widest scope the other settings permit."""

    def test_none_with_anyone_permitted_is_an_effective_anyone_default(self):
        found = findings(spo({'SharingCapability': 'ExternalUserAndGuestSharing', 'DefaultSharingLinkType': 'None',
                              'RequireAnonymousLinksExpireInDays': 0}))
        self.assertIn('sharepoint.sharing.permissive_anonymous_defaults', found)
        self.assertEqual(found['sharepoint.sharing.permissive_anonymous_defaults']['Priority'], 'High')
        self.assertNotIn('sharepoint.sharing.anyone_enabled', found)

    def test_numeric_none_with_anyone_permitted_matches_the_named_value(self):
        found = findings(spo({'SharingCapability': 2, 'DefaultSharingLinkType': 0, 'RequireAnonymousLinksExpireInDays': 0}))
        self.assertIn('sharepoint.sharing.permissive_anonymous_defaults', found)

    def test_none_with_anyone_prohibited_is_an_effective_organization_default(self):
        found = findings(spo({'SharingCapability': 'ExternalUserSharingOnly', 'DefaultSharingLinkType': 'None'}))
        self.assertIn('sharepoint.sharing.organization_default', found)
        self.assertIsNone(strength(spo({'SharingCapability': 'ExternalUserSharingOnly', 'DefaultSharingLinkType': 'None'})))

    def test_effective_default_helper(self):
        from Core.sharepoint_governance import effective_default_link
        self.assertEqual(effective_default_link({'SharingCapability': 2, 'DefaultSharingLinkType': 0}), 'anonymousaccess')
        self.assertEqual(effective_default_link({'SharingCapability': 1, 'DefaultSharingLinkType': 'None'}), 'internal')
        self.assertEqual(effective_default_link({'SharingCapability': 0, 'DefaultSharingLinkType': 'Direct'}), 'direct')
        # Without the boundary, the widest permitted scope is not established.
        self.assertEqual(effective_default_link({'DefaultSharingLinkType': 'None'}), '')
        self.assertEqual(effective_default_link({}), '')


class AnyoneLinkRuleTests(unittest.TestCase):
    def test_missing_expiry_and_edit_permission_are_stated_when_anyone_is_not_the_default(self):
        found = findings(spo({'SharingCapability': 'ExternalUserAndGuestSharing', 'DefaultSharingLinkType': 'Direct',
                              'RequireAnonymousLinksExpireInDays': 0, 'FileAnonymousLinkType': 'Edit',
                              'FolderAnonymousLinkType': 'View'}))
        row = found['sharepoint.sharing.anyone_enabled']
        self.assertIn('no expiration', row['Observation'])
        self.assertIn('edit', row['Observation'])
        results = row['SharingSettingResults']
        self.assertEqual(setting_result(results, 'RequireAnonymousLinksExpireInDays')['result'], 'does_not_meet')
        self.assertEqual(setting_result(results, 'FileAnonymousLinkType')['result'], 'does_not_meet')
        self.assertEqual(setting_result(results, 'FolderAnonymousLinkType')['result'], 'meets')

    def test_expiry_within_the_methodology_limit_meets(self):
        from Core.sharepoint_governance import evaluate_tenant_sharing
        results = evaluate_tenant_sharing({'SharingCapability': 'ExternalUserAndGuestSharing', 'DefaultSharingLinkType': 'Direct',
                                           'RequireAnonymousLinksExpireInDays': 14, 'FileAnonymousLinkType': 'View'})
        self.assertEqual(setting_result(results, 'RequireAnonymousLinksExpireInDays')['result'], 'meets')
        # The external boundary still does not meet the methodology baseline.
        self.assertEqual(setting_result(results, 'SharingCapability')['result'], 'does_not_meet')

    def test_anyone_settings_are_not_applicable_when_anyone_is_prohibited(self):
        from Core.sharepoint_governance import evaluate_tenant_sharing
        results = evaluate_tenant_sharing(dict(COMPLIANT, FileAnonymousLinkType='Edit'))
        for name in ('RequireAnonymousLinksExpireInDays', 'FileAnonymousLinkType'):
            self.assertEqual(setting_result(results, name)['result'], 'not_applicable', name)


class NeverMeetsWhenContradictedTests(unittest.TestCase):
    """A setting is never classified as meeting a rule its normalized value contradicts."""

    CASES = {
        'SharingCapability': ('ExternalUserAndGuestSharing', 'does_not_meet'),
        'OneDriveSharingCapability': ('ExternalUserAndGuestSharing', 'does_not_meet'),
        'DefaultSharingLinkType': ('AnonymousAccess', 'does_not_meet'),
        'PreventExternalUsersFromResharing': (False, 'does_not_meet'),
        'ExternalUserExpirationRequired': (False, 'does_not_meet'),
    }

    def test_contradicting_values(self):
        from Core.sharepoint_governance import evaluate_tenant_sharing
        for name, (value, expected) in self.CASES.items():
            with self.subTest(setting=name):
                results = evaluate_tenant_sharing(dict(COMPLIANT, **{name: value}))
                self.assertEqual(setting_result(results, name)['result'], expected)

    def test_unreported_and_unmapped_values_are_not_established(self):
        from Core.sharepoint_governance import evaluate_tenant_sharing
        settings = {key: value for key, value in COMPLIANT.items() if key != 'DefaultSharingLinkType'}
        self.assertEqual(setting_result(evaluate_tenant_sharing(settings), 'DefaultSharingLinkType')['result'], 'not_established')
        self.assertEqual(setting_result(evaluate_tenant_sharing(dict(COMPLIANT, SharingCapability='Sideways')), 'SharingCapability')['result'],
                         'not_established')

    def test_guest_settings_are_not_applicable_when_external_sharing_is_disabled(self):
        from Core.sharepoint_governance import evaluate_tenant_sharing
        results = evaluate_tenant_sharing(dict(COMPLIANT, SharingCapability='Disabled', OneDriveSharingCapability='Disabled',
                                               PreventExternalUsersFromResharing=False, ExternalUserExpirationRequired=False))
        for name in ('PreventExternalUsersFromResharing', 'ExternalUserExpirationRequired'):
            self.assertEqual(setting_result(results, name)['result'], 'not_applicable', name)


class GuestConfigurationTests(unittest.TestCase):
    """Configuration evidence establishes configuration, never operational governance."""

    def test_guest_expiry_meets_as_configuration_only(self):
        from Core.sharepoint_governance import evaluate_tenant_sharing
        item = setting_result(evaluate_tenant_sharing(COMPLIANT), 'ExternalUserExpirationRequired')
        self.assertEqual((item['result'], item['establishes'], item['requirement']), ('meets', 'configuration', 'hardening'))

    def test_guest_resharing_does_not_meet_and_is_hardening(self):
        from Core.sharepoint_governance import evaluate_tenant_sharing
        item = setting_result(evaluate_tenant_sharing(dict(COMPLIANT, PreventExternalUsersFromResharing=False)),
                              'PreventExternalUsersFromResharing')
        self.assertEqual((item['result'], item['requirement']), ('does_not_meet', 'hardening'))

    def test_strength_wording_does_not_claim_operational_effectiveness(self):
        row = strength(spo(COMPLIANT))
        self.assertIsNotNone(row)
        text = (row['Strength'] + ' ' + row.get('Qualification', '')).lower()
        self.assertNotRegex(text, r'effectively governed|is governed|operating effectively|no exposure')
        self.assertIn('existing', text)  # states the limit: existing content access is not established


class PositiveStrengthTests(unittest.TestCase):
    def test_compliant_configuration_produces_an_auditable_strength(self):
        row = strength(spo(COMPLIANT))
        self.assertEqual(row['ControlId'], 'CONTENT.SHARING')
        self.assertEqual(row['EvidenceSource'], 'sharepoint_tenant_settings')
        self.assertTrue(row['EvidenceScope'])
        self.assertEqual(row['ObservedValues']['SharingCapability'], 'externalusersharingonly')
        self.assertEqual(row['ObservedValues']['DefaultSharingLinkType (effective)'], 'direct')
        self.assertTrue(row['ExpectedRule'])
        self.assertTrue(row['MethodologyRule'])
        self.assertTrue(row['SharingSettingResults'])

    def test_missing_boundary_never_produces_a_strength(self):
        settings = {key: value for key, value in COMPLIANT.items() if key != 'SharingCapability'}
        self.assertIsNone(strength(spo(settings)))

    def test_onedrive_anyone_never_produces_a_strength(self):
        self.assertIsNone(strength(spo(dict(COMPLIANT, OneDriveSharingCapability='ExternalUserAndGuestSharing'))))

    def test_collection_success_alone_never_produces_a_strength(self):
        for settings in (dict(COMPLIANT, DefaultSharingLinkType='Internal'),
                         dict(COMPLIANT, SharingCapability='ExternalUserAndGuestSharing'), {}):
            with self.subTest(settings=settings):
                self.assertIsNone(strength(spo(settings)))

    def test_graph_partial_settings_never_produce_a_strength(self):
        self.assertIsNone(strength(graph({'SharingCapability': 'ExternalUserSharingOnly'})))


class SourceConflictTests(unittest.TestCase):
    def merged(self):
        return merge_sharepoint_payloads(graph({'SharingCapability': 'Disabled'}), spo(COMPLIANT))

    def test_conflict_is_retained_with_both_values(self):
        conflicts = summarize_sharepoint_governance(self.merged(), evaluation_date=EVALUATION)['conflicts']
        self.assertEqual(conflicts, [{'setting': 'SharingCapability', 'sharepoint_admin': 'ExternalUserSharingOnly', 'graph': 'Disabled'}])

    def test_conflict_prevents_a_strength_and_opens_a_question(self):
        self.assertIsNone(strength(self.merged()))
        row = findings(self.merged())['sharepoint.sharing.conflicting_sources']
        self.assertEqual((row['Disposition'], row['ControlId']), ('Coverage', 'CONTENT.SHARING'))
        self.assertIn('ExternalUserSharingOnly', row['Observation'])
        self.assertIn('Disabled', row['Observation'])
        self.assertEqual(row['SourceConflicts'][0]['setting'], 'SharingCapability')


class EffectiveSiteConfigurationTests(unittest.TestCase):
    """A site's stored setting can be broader than the tenant; the tenant setting is the ceiling."""

    def test_effective_capability(self):
        from Core.sharepoint_governance import effective_sharing_capability as effective
        cases = [
            ('Disabled', 'ExternalUserSharingOnly', 'disabled'),                          # 1 site disables sharing
            ('ExternalUserSharingOnly', 'ExternalUserAndGuestSharing', 'externalusersharingonly'),  # 2 site stricter
            ('ExternalUserAndGuestSharing', 'ExternalUserSharingOnly', 'externalusersharingonly'),  # 3 tenant ceiling
            (None, 'ExternalUserSharingOnly', None),                                       # 5 site evidence missing
            ('ExternalUserSharingOnly', None, None),                                       # 6 tenant evidence missing
            ('Disabled', None, 'disabled'),                                                # a disabled site needs no tenant value
            (None, 'Disabled', 'disabled'),                                                # a disabled tenant disables every site
            ('Sideways', 'ExternalUserSharingOnly', None),
        ]
        for site, tenant, expected in cases:
            with self.subTest(site=site, tenant=tenant):
                self.assertEqual(effective(site, tenant), expected)

    def test_configured_and_effective_site_counts_are_separate(self):
        sites = [{'Url': 'https://fictional.example/sites/a', 'SharingCapability': 'ExternalUserAndGuestSharing'},
                 {'Url': 'https://fictional.example/sites/b', 'SharingCapability': 'Disabled'}]
        summary = summarize_sharepoint_governance(spo(COMPLIANT, sites=sites), evaluation_date=EVALUATION)
        self.assertEqual((summary['configured_anyone_site_count'], summary['effective_anyone_site_count']), (1, 0))
        permissive = summarize_sharepoint_governance(spo(dict(COMPLIANT, SharingCapability='ExternalUserAndGuestSharing'), sites=sites),
                                                     evaluation_date=EVALUATION)
        self.assertEqual((permissive['configured_anyone_site_count'], permissive['effective_anyone_site_count']), (1, 1))
        unknown = summarize_sharepoint_governance(spo({}, sites=sites), evaluation_date=EVALUATION)
        self.assertIsNone(unknown['effective_anyone_site_count'])

    def test_workbook_sheet_does_not_claim_sites_permit_anyone_under_a_stricter_tenant(self):
        sites = [{'Url': 'https://fictional.example/sites/a', 'SharingCapability': 'ExternalUserAndGuestSharing'}]
        sheet = _build_sharepoint_governance_sheet(SimpleNamespace(sharepoint_governance=spo(COMPLIANT, sites=sites)))
        row = next(item for item in sheet['rows'] if item['Setting or report'] == 'Sites assessed')
        self.assertIn('0 site(s) can create Anyone links', row['Why it matters'])
        self.assertIn('1 site(s) are configured', row['Why it matters'])

    def test_sharepoint_and_onedrive_boundaries_stay_separate(self):
        from Core.sharepoint_governance import evaluate_tenant_sharing
        results = evaluate_tenant_sharing(dict(COMPLIANT, SharingCapability='ExternalUserAndGuestSharing',
                                               OneDriveSharingCapability='Disabled'))
        self.assertEqual(setting_result(results, 'SharingCapability')['result'], 'does_not_meet')
        self.assertEqual(setting_result(results, 'OneDriveSharingCapability')['result'], 'meets')


class SiteRecordInheritanceTests(unittest.TestCase):
    """Missing site evidence is not filled from the tenant unless the override state establishes inheritance."""

    def records(self, site):
        sources = {
            'sharepoint_tenant_settings': [{'records': [dict(COMPLIANT, SharingCapability='ExternalUserAndGuestSharing',
                                                             RequireAnonymousLinksExpireInDays=7)],
                                            'source': dict(SPO_STATE)}],
            'sharepoint_site_settings': [{'records': [site], 'source': dict(SPO_STATE, settings_verified=True)}],
        }
        out = expand_finding_records({'FindingKey': 'sharepoint.sharing.anyone_enabled'}, sources)
        return next(row['fields'] for row in out['records'] if row['fields']['scope'] == 'site')

    def test_site_expiry_not_retained_and_override_unknown_stays_unknown(self):
        fields = self.records({'Url': 'https://fictional.example/sites/a', 'SharingCapability': 'ExternalUserAndGuestSharing'})
        self.assertIsNone(fields['effectiveAnyoneLinkExpiryDays'])
        self.assertEqual(fields['anyoneLinkExpiryDays'], 7)  # the tenant fallback stays labelled as a fallback

    def test_explicit_inheritance_uses_the_tenant_expiry(self):
        fields = self.records({'Url': 'https://fictional.example/sites/a', 'SharingCapability': 'ExternalUserAndGuestSharing',
                               'OverrideTenantAnonymousLinkExpirationPolicy': False})
        self.assertEqual(fields['effectiveAnyoneLinkExpiryDays'], 7)

    def test_effective_site_capability_is_recorded_beside_the_configured_value(self):
        sources = {
            'sharepoint_tenant_settings': [{'records': [COMPLIANT], 'source': dict(SPO_STATE)}],
            'sharepoint_site_settings': [{'records': [{'Url': 'https://fictional.example/sites/a',
                                                       'SharingCapability': 'ExternalUserAndGuestSharing'}],
                                          'source': dict(SPO_STATE, settings_verified=True)}],
        }
        out = expand_finding_records({'FindingKey': 'sharepoint.sharing.anyone_enabled'}, sources)
        site = next(row['fields'] for row in out['records'] if row['fields']['scope'] == 'site')
        self.assertEqual(site['sharingSetting'], 'ExternalUserAndGuestSharing')
        self.assertEqual(site['effectiveSharingSetting'], 'externalusersharingonly')


class DagInventoryFreshnessTests(unittest.TestCase):
    def coverage(self, reports):
        summary = summarize_sharepoint_governance(spo(COMPLIANT, dag=reports), evaluation_date=EVALUATION)
        return {row['report']: row['status'] for row in summary['dag_coverage']}

    def test_report_within_the_35_day_sam_threshold_is_recent(self):
        reports = [{'RequestedEntity': 'PermissionedUsers', 'RequestedWorkload': 'SharePoint', 'Status': 'Completed',
                    'CompletedDateTime': '2026-09-17T00:00:00Z'}]  # 33 days before evaluation
        self.assertEqual(self.coverage(reports)['SharePoint permission state'], 'Recent')

    def test_report_beyond_the_threshold_is_not_recent(self):
        reports = [{'RequestedEntity': 'PermissionedUsers', 'RequestedWorkload': 'SharePoint', 'Status': 'Completed',
                    'CompletedDateTime': '2026-09-10T00:00:00Z'}]  # 40 days
        self.assertEqual(self.coverage(reports)['SharePoint permission state'], 'Stale or incomplete')

    def test_undated_completed_report_is_never_recent(self):
        reports = [{'RequestedEntity': 'PermissionedUsers', 'RequestedWorkload': 'SharePoint', 'Status': 'Completed'}]
        self.assertEqual(self.coverage(reports)['SharePoint permission state'], 'Stale or incomplete')


if __name__ == '__main__':
    unittest.main()

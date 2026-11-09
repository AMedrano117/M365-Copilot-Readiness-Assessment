"""Conditional Anyone-link semantics and Content sub-control strengths (fictional data only).

Expected results are declared before production logic runs. Their basis:

* Set-SPOTenant documents RequireAnonymousLinksExpireInDays, FileAnonymousLinkType
  and FolderAnonymousLinkType as Anyone-link settings. They govern links that can
  be created only where the effective sharing level permits Anyone links. Under a
  narrower boundary the stored value is inactive: it neither meets nor fails the
  methodology, and it is evaluated normally if Anyone links become permitted.
* A site cannot be more permissive than the tenant, so the effective site level
  (Core.sharepoint_governance.effective_sharing_capability) decides applicability
  at site scope.
* A sub-control strength describes one returned, unconflicted, current
  configuration value. It never establishes the aggregate CONTENT.SHARING control,
  operational effectiveness, or the absence of existing links.

All tenants, sites and values are invented.
"""
import json
import re
import unittest

try:
    from tests import content_sharing_scenarios as h
except ImportError:  # unittest discover -s tests
    import content_sharing_scenarios as h

from Core import sharepoint_governance as sg
from Core.sharepoint_governance import evaluate_tenant_sharing, merge_sharepoint_payloads, summarize_sharepoint_governance

DEPENDENT = ('RequireAnonymousLinksExpireInDays', 'FileAnonymousLinkType', 'FolderAnonymousLinkType')

ANYONE_OFF = {'SharingCapability': 'ExternalUserSharingOnly', 'OneDriveSharingCapability': 'ExternalUserSharingOnly'}
ANYONE_ON = {'SharingCapability': 'ExternalUserAndGuestSharing', 'OneDriveSharingCapability': 'ExternalUserAndGuestSharing'}
DORMANT = {'RequireAnonymousLinksExpireInDays': 0, 'FileAnonymousLinkType': 'Edit', 'FolderAnonymousLinkType': 'Edit'}
CLAIMS = (r'(?i)no (anyone|anonymous) links', r'(?i)effectively governed', r'(?i)securely shared', r'(?i)(were|was|have been) removed',
          r'(?i)\bsecure because\b', r'(?i)permission meets')


def results(settings, conflicts=()):
    return {item['setting']: item for item in evaluate_tenant_sharing(settings, conflicts)}


def strengths(payload):
    return {row['Key']: row for row in sg.sharing_sub_strengths(summarize_sharepoint_governance(payload, evaluation_date=h.EVALUATION))}


def working(result):
    from Core.content_scorecard import build_content_scorecard
    return build_content_scorecard(result)['working']


class AnyoneDependentApplicabilityTests(unittest.TestCase):
    """Cases 1-9: the effective boundary decides whether Anyone-link settings apply."""

    def test_dependent_settings_are_declared(self):
        self.assertEqual(set(sg.ANYONE_DEPENDENT_SETTINGS),
                         {'RequireAnonymousLinksExpireInDays', 'FileAnonymousLinkType', 'FolderAnonymousLinkType'})

    def test_boundary_and_applicability_are_tri_state(self):
        self.assertIs(sg.anyone_links_permitted(ANYONE_ON), True)
        self.assertIs(sg.anyone_links_permitted(ANYONE_OFF), False)
        self.assertIs(sg.anyone_links_permitted({'SharingCapability': 0}), False)       # numeric Disabled
        self.assertIsNone(sg.anyone_links_permitted({'FileAnonymousLinkType': 'Edit'}))  # boundary not returned
        self.assertEqual([sg.anyone_applicability(value) for value in (True, False, None)],
                         ['applicable', 'not_applicable', 'not_established'])

    def test_01_02_09_anyone_disabled_permissions_are_inactive_and_preserved(self):
        evaluated = results(dict(ANYONE_OFF, **DORMANT))
        for name, observed in (('FileAnonymousLinkType', 'edit'), ('FolderAnonymousLinkType', 'edit')):
            with self.subTest(name):
                item = evaluated[name]
                self.assertEqual(item['result'], 'not_applicable')
                self.assertNotEqual(item['result'], 'meets')
                self.assertEqual(item['applicability'], 'not_applicable')
                self.assertEqual(item['observed'], observed)          # the configured value is kept, not replaced
                self.assertRegex(item['basis'], r'(?i)inactive')

    def test_03_anyone_disabled_missing_expiry_is_inactive(self):
        item = results(dict(ANYONE_OFF, RequireAnonymousLinksExpireInDays=0))['RequireAnonymousLinksExpireInDays']
        self.assertEqual((item['result'], item['applicability'], item['observed']), ('not_applicable', 'not_applicable', '0'))

    def test_04_05_anyone_enabled_edit_does_not_meet(self):
        evaluated = results(dict(ANYONE_ON, RequireAnonymousLinksExpireInDays=14, FileAnonymousLinkType='Edit', FolderAnonymousLinkType=2))
        for name in ('FileAnonymousLinkType', 'FolderAnonymousLinkType'):
            with self.subTest(name):
                self.assertEqual((evaluated[name]['result'], evaluated[name]['applicability']), ('does_not_meet', 'applicable'))

    def test_06_anyone_enabled_expiry_missing_or_invalid(self):
        cases = {None: 'not_established', 0: 'does_not_meet', -1: 'does_not_meet', 365: 'does_not_meet', 9999: 'not_established',
                 'soon': 'not_established'}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                settings = dict(ANYONE_ON)
                if raw is not None:
                    settings['RequireAnonymousLinksExpireInDays'] = raw
                item = results(settings)['RequireAnonymousLinksExpireInDays']
                self.assertEqual((item['result'], item['applicability']), (expected, 'applicable'))

    def test_07_anyone_enabled_compliant_values_meet(self):
        evaluated = results(dict(ANYONE_ON, RequireAnonymousLinksExpireInDays=14, FileAnonymousLinkType='View', FolderAnonymousLinkType=1))
        for name in DEPENDENT:
            self.assertEqual((evaluated[name]['result'], evaluated[name]['applicability']), ('meets', 'applicable'), name)

    def test_08_unknown_boundary_stays_unresolved(self):
        evaluated = results(dict(DORMANT, FileAnonymousLinkType='View'))
        for name in DEPENDENT:
            with self.subTest(name):
                self.assertEqual((evaluated[name]['result'], evaluated[name]['applicability']), ('not_established', 'not_established'))

    def test_conflicted_boundary_is_not_used_to_decide_applicability(self):
        conflicts = [{'setting': 'SharingCapability', 'sharepoint_admin': 'ExternalUserSharingOnly', 'graph': 'ExternalUserAndGuestSharing'}]
        item = results(dict(ANYONE_OFF, **DORMANT), conflicts)['FileAnonymousLinkType']
        self.assertEqual((item['result'], item['applicability']), ('not_established', 'not_established'))


class AnyoneDependentFindingTests(unittest.TestCase):
    """Cases 1-3 and 9 through the production pipeline: a dormant value creates no remediation."""

    def test_dormant_values_create_no_anyone_finding(self):
        result, _ = h.run(payload=h.spo(dict(h.COMPLIANT, **DORMANT)))
        keys = {row.get('FindingKey') for row in result['recommendations']}
        self.assertFalse(keys & {'sharepoint.sharing.anyone_enabled', 'sharepoint.sharing.permissive_anonymous_defaults'})
        self.assertEqual(h.control(result, 'CONTENT.SHARING')['status'], 'Observed')

    def test_dormant_values_never_reach_needs_improvement(self):
        result, _ = h.run(payload=h.spo(dict(h.COMPLIANT, DefaultSharingLinkType='Internal', **DORMANT)))
        from Core.content_scorecard import build_content_scorecard
        card = build_content_scorecard(result)
        text = json.dumps(card['needs_improvement'])
        for label in ('Anyone link expiry', 'Anyone link permission'):
            self.assertNotIn(label, text)
        row = h.rows_with_key(result, 'sharepoint.sharing.organization_default')[0]
        dependent = {item['setting']: item for item in row['SharingSettingResults'] if item['setting'] in DEPENDENT}
        self.assertEqual({item['result'] for item in dependent.values()}, {'not_applicable'})
        self.assertEqual(dependent['FileAnonymousLinkType']['observed'], 'edit')


class SharingTablePresentationTests(unittest.TestCase):
    """The customer settings table, grouped configuration rows and workbook use the same applicability."""

    def check(self, key, value, anyone):
        from Core.customer_report import _sharing_check
        return _sharing_check(key, value, anyone)

    def test_dormant_value_is_not_labelled_meets(self):
        for key, value in (('FileAnonymousLinkType', 'View and edit'), ('FolderAnonymousLinkType', 'View and edit'),
                           ('RequireAnonymousLinksExpireInDays', 'No expiry requirement (0 days)')):
            with self.subTest(key):
                recommended, state = self.check(key, value, False)
                self.assertEqual(state, 'not_applicable')
                self.assertRegex(recommended, r'(?i)when Anyone links are permitted')

    def test_unknown_boundary_needs_review(self):
        for key, value in (('FileAnonymousLinkType', 'View only'), ('RequireAnonymousLinksExpireInDays', '14 days')):
            self.assertEqual(self.check(key, value, None)[1], 'review', key)

    def test_applicable_values_are_evaluated(self):
        expected = {('FileAnonymousLinkType', 'View and edit'): 'change', ('FolderAnonymousLinkType', 'View only'): 'ok',
                    ('FileAnonymousLinkType', 'Not set'): 'review', ('RequireAnonymousLinksExpireInDays', '14 days'): 'ok',
                    ('RequireAnonymousLinksExpireInDays', 'No expiry requirement (0 days)'): 'change',
                    ('RequireAnonymousLinksExpireInDays', 'Not reported'): 'review'}
        for (key, value), state in expected.items():
            self.assertEqual(self.check(key, value, True)[1], state, (key, value))

    def test_report_table_uses_effective_boundary(self):
        from Core.customer_report import SETTING_STATES, _settings, _sharing_changes, _sharing_review
        bundle = {'sharepoint_governance': {'settings': dict(ANYONE_OFF, **DORMANT)}}
        rows = {row['label']: row for row in _sharing_review(bundle)}
        self.assertEqual(rows['Anyone link permission (files)']['state'], 'not_applicable')
        self.assertEqual(rows['Anyone link permission (files)']['value'], 'View and edit')
        self.assertNotIn('Anyone link', _sharing_changes(bundle))    # nothing to change for an inactive value
        html = _settings(bundle)
        self.assertIn(SETTING_STATES['not_applicable'], html)
        unknown = {row['label']: row['state'] for row in _sharing_review({'sharepoint_governance': {'settings': dict(DORMANT)}})}
        self.assertEqual(unknown['Anyone link permission (files)'], 'review')

    def test_grouped_configuration_rows(self):
        from Core.configuration_groups import build_configuration_groups

        def payload(settings):
            return {'tenant_id': h.TENANT,
                    'sources': [{'dataset_id': 'DS-1', 'dataset': 'sharepoint_tenant_settings',
                                 'source': {'complete': True, 'truncated': False, 'availability_status': 'available'}}],
                    'evidence_records': [{'record_id': 'EV-1', 'dataset_id': 'DS-1', 'raw': settings}],
                    'findings': [{'finding_id': 'F-1', 'finding_key': 'sharepoint.dag.not_assessed',
                                  'records': [{'evidence_record_ids': ['EV-1']}]}]}

        def assessments(settings):
            return {group['configuration_key']: group['assessment'] for group in build_configuration_groups(payload(settings))['groups']}

        off = assessments(dict(ANYONE_OFF, **DORMANT))
        self.assertEqual({off[name] for name in DEPENDENT}, {'Not applicable'})
        self.assertEqual(off['SharingCapability'], 'Meets')
        on = assessments(dict(ANYONE_ON, **DORMANT))
        self.assertEqual((on['FileAnonymousLinkType'], on['RequireAnonymousLinksExpireInDays']), ('Change', 'Change'))
        unknown = assessments(dict(DORMANT))
        self.assertEqual({unknown[name] for name in DEPENDENT}, {'Review'})

    def test_workbook_projection(self):
        from Core.workbook_layout import _projection
        row = {'RecommendationId': 'M365-001', 'FindingKey': 'sharepoint.dag.not_assessed'}

        def status(anyone=...):
            record = {'evidence_id': 'EV-1', 'dataset': 'sharepoint_tenant_settings', 'evidence_kind': 'configuration',
                      'fields': {'setting': 'FileAnonymousLinkType', 'value': 2}}
            if anyone is not ...:
                record['anyone'] = anyone
            return _projection(row, record, {})[1]['Status']

        self.assertEqual(status(False), 'Not applicable')
        self.assertEqual(status(True), 'Change')
        self.assertEqual(status(None), 'Review')
        self.assertNotEqual(status(), 'Meets')        # a record without a resolved boundary is never Meets


class SiteBoundaryApplicabilityTests(unittest.TestCase):
    """Case 10: applicability at site scope follows the effective site boundary."""

    def site(self, tenant, site, **state):
        from Core.evidence_records import expand_finding_records
        sources = {'sharepoint_tenant_settings': [{'records': [tenant], 'source': dict(h.SPO_STATE)}],
                   'sharepoint_site_settings': [{'records': [site], 'source': dict(h.SPO_STATE, **state)}]}
        out = expand_finding_records({'FindingKey': 'sharepoint.sharing.anyone_enabled'}, sources)
        return next(row['fields'] for row in out['records'] if row['fields']['scope'] == 'site')

    def test_narrower_site_boundary_makes_site_anyone_settings_inactive(self):
        fields = self.site(dict(ANYONE_ON, RequireAnonymousLinksExpireInDays=0),
                           {'Url': h.SITE_A, 'SharingCapability': 'ExternalUserSharingOnly', 'AnonymousLinkExpirationInDays': 0,
                            'OverrideTenantAnonymousLinkExpirationPolicy': True}, settings_verified=True)
        self.assertEqual(fields['effectiveSharingSetting'], 'externalusersharingonly')
        self.assertEqual(fields['anyoneLinkSettingsApplicability'], 'not_applicable')
        self.assertEqual(fields['anyoneLinkExpiryDays'], 0)       # configured value preserved

    def test_tenant_ceiling_makes_a_configured_anyone_site_inactive(self):
        fields = self.site(dict(ANYONE_OFF), {'Url': h.SITE_A, 'SharingCapability': 'ExternalUserAndGuestSharing'}, settings_verified=True)
        self.assertEqual(fields['anyoneLinkSettingsApplicability'], 'not_applicable')

    def test_effective_anyone_site_is_applicable(self):
        fields = self.site(dict(ANYONE_ON), {'Url': h.SITE_A, 'SharingCapability': 'ExternalUserAndGuestSharing'}, settings_verified=True)
        self.assertEqual(fields['anyoneLinkSettingsApplicability'], 'applicable')

    def test_unverified_site_read_is_not_established(self):
        fields = self.site(dict(ANYONE_OFF, SharingCapability='ExternalUserAndGuestSharing'),
                           {'Url': h.SITE_A, 'SharingCapability': 'ExternalUserSharingOnly'})
        self.assertEqual(fields['anyoneLinkSettingsApplicability'], 'not_established')


class SubControlStrengthTests(unittest.TestCase):
    """Cases 11-17: evidence-backed sub-control strengths with provenance."""

    def test_11_restriction_is_a_strength_while_existing_exposure_stays_separate(self):
        payload = h.spo(dict(h.COMPLIANT, DefaultSharingLinkType='Internal', **DORMANT))
        result, _ = h.run(payload=payload, sam=[('permissions.csv', h.snapshot(h.snapshot_row('site-alpha', h.SITE_A, anyone=3)))])
        from Core.content_scorecard import build_content_scorecard
        card = build_content_scorecard(result)
        keys = {item.get('strength_key') for item in card['working']}
        self.assertIn('content.sharing.anyone_creation_restricted', keys)
        self.assertIn('data_exposure.anonymous_links', {item.get('finding_key') for item in card['exposure_to_review']})
        self.assertTrue(h.rows_with_key(result, 'data_exposure.anonymous_links'))   # the exposure finding is not suppressed
        # Customer statements only; the expected-rule text names a setting level, not a claim.
        text = ' '.join(str(item.get('title') or '') for item in card['working'])
        for pattern in CLAIMS:
            self.assertNotRegex(text, pattern)
        # A dormant dependent value never produces a strength of its own.
        self.assertFalse(keys & {'content.sharing.anyone_link_expiry_required', 'content.sharing.anyone_links_view_only'})

    def test_12_sharepoint_authenticated_guest_boundary(self):
        found = strengths(h.spo({'SharingCapability': 'ExternalUserSharingOnly'}))
        row = found['content.sharing.anyone_creation_restricted']
        self.assertRegex(row['Strength'], r'(?i)SharePoint')
        self.assertRegex(row['Strength'], r'(?i)authenticated guests or stricter')
        self.assertRegex(row['Strength'], r'(?i)assessed tenant configuration')
        self.assertEqual(row['ObservedValues']['SharingCapability'], 'externalusersharingonly')

    def test_13_onedrive_authenticated_guest_boundary(self):
        found = strengths(h.spo({'SharingCapability': 'ExternalUserAndGuestSharing', 'OneDriveSharingCapability': 'ExistingExternalUserSharingOnly'}))
        self.assertNotIn('content.sharing.anyone_creation_restricted', found)  # SharePoint still permits Anyone links
        self.assertNotIn('content.sharing.sharepoint_guest_boundary', found)
        row = found['content.sharing.onedrive_guest_boundary']
        self.assertRegex(row['Strength'], r'(?i)OneDrive external sharing is limited to authenticated guests or stricter')
        self.assertEqual(row['EvidenceScope'], 'Tenant-wide OneDrive sharing configuration')

    def test_14_guest_expiration_is_configuration_only(self):
        found = strengths(h.spo({'SharingCapability': 'ExternalUserSharingOnly', 'ExternalUserExpirationRequired': True,
                                 'ExternalUserExpireInDays': 60}))
        row = found['content.sharing.guest_expiration_required']
        self.assertRegex(row['Strength'], r'(?i)guest access expiration is configured')
        self.assertEqual((row['Requirement'], row['Establishes']), ('hardening', 'configuration'))
        for pattern in CLAIMS:
            self.assertNotRegex(row['Strength'], pattern)
        self.assertNotIn('content.sharing.guest_expiration_required',
                         strengths(h.spo({'SharingCapability': 'ExternalUserSharingOnly', 'ExternalUserExpirationRequired': False})))

    def test_15_missing_or_conflicting_evidence_yields_no_strength(self):
        self.assertEqual(strengths({}), {})
        self.assertEqual(strengths(h.graph({})), {})
        conflicted = strengths(h.conflicting())       # SharingCapability disagrees between sources
        self.assertFalse({'content.sharing.anyone_creation_restricted', 'content.sharing.sharepoint_guest_boundary'} & set(conflicted))
        for row in conflicted.values():
            self.assertNotIn('SharingCapability', row['ObservedValues'])
        # A returned value that is unmapped is not evidence either.
        self.assertNotIn('content.sharing.guest_expiration_required',
                         strengths(h.spo({'SharingCapability': 'ExternalUserSharingOnly', 'ExternalUserExpirationRequired': 'maybe'})))

    def test_15_stale_evidence_is_not_shown_as_working(self):
        payload = h.graph(dict(ANYONE_OFF, ExternalUserExpirationRequired=True))
        result, _ = h.run(payload=payload, collected='2026-01-05T09:00:00+00:00')
        self.assertFalse([item for item in working(result) if item.get('strength_key')])
        self.assertTrue(result['content_strengths'])                         # retained, qualified, not shown
        self.assertNotIn('supported', {row['EvidenceStatus'] for row in result['content_strengths']})

    def test_16_corroborating_sources_give_one_strength(self):
        merged = merge_sharepoint_payloads(h.graph(dict(ANYONE_OFF)), h.spo(dict(h.COMPLIANT, DefaultSharingLinkType='Internal')))
        found = strengths(merged)
        rows = [row for row in found.values() if 'SharingCapability' in row['ObservedValues']]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['EvidenceSource'], 'sharepoint_tenant_settings')
        self.assertEqual(rows[0]['CorroboratingSources'], ['sharepoint_tenant_settings', 'sharepoint_tenant_settings_graph'])
        result, _ = h.run(payload=merged)
        shown = [item['strength_key'] for item in working(result) if item.get('strength_key')]
        self.assertEqual(len(shown), len(set(shown)))

    def test_17_aggregate_not_established_while_strengths_show(self):
        payload = h.graph(dict(ANYONE_OFF, ExternalUserExpirationRequired=True, PreventExternalUsersFromResharing=False))
        result, _ = h.run(payload=payload)
        self.assertNotEqual(h.control(result, 'CONTENT.SHARING')['status'], 'Observed')
        items = working(result)
        self.assertNotIn('CONTENT.SHARING', {item.get('control_id') for item in items})
        keys = {item.get('strength_key') for item in items}
        self.assertTrue({'content.sharing.anyone_creation_restricted', 'content.sharing.guest_expiration_required'} <= keys)
        self.assertNotIn('content.sharing.guest_resharing_prevented', keys)
        for item in items:
            if item.get('strength_key'):
                provenance = item['provenance']
                for field in ('observed', 'expected', 'scope', 'source', 'observed_at', 'methodology_rule'):
                    self.assertTrue(provenance.get(field), (item['strength_key'], field))
        from Core.content_scorecard import build_content_scorecard
        card = build_content_scorecard(result)
        self.assertEqual(card['control_counts']['Established'], 0)       # strengths are not counted as controls
        self.assertIn('CONTENT.SHARING', {item['control_id'] for item in card['not_established']})
        sharing = h.control(result, 'CONTENT.SHARING')
        self.assertFalse({item['finding_key'] for item in sharing['component_results']} & set(keys))

    def test_aggregate_established_suppresses_overlapping_sub_strengths(self):
        result, _ = h.run(payload=h.spo(h.COMPLIANT))
        items = working(result)
        self.assertIn('CONTENT.SHARING', {item.get('control_id') for item in items})
        keys = [item.get('strength_key') for item in items if item.get('strength_key')]
        self.assertNotIn('content.sharing.anyone_creation_restricted', keys)    # already stated by the control
        self.assertIn('content.sharing.guest_expiration_required', keys)        # hardening is not part of the control

    def test_applicable_compliant_anyone_settings_are_strengths(self):
        found = strengths(h.spo(dict(ANYONE_ON, RequireAnonymousLinksExpireInDays=14, FileAnonymousLinkType='View',
                                     FolderAnonymousLinkType='View')))
        self.assertRegex(found['content.sharing.anyone_link_expiry_required']['Strength'], r'14 days')
        self.assertRegex(found['content.sharing.anyone_links_view_only']['Strength'], r'(?i)files and folders')
        self.assertNotIn('content.sharing.anyone_creation_restricted', found)

    def test_render_lists_strengths_with_provenance(self):
        from Core.content_scorecard import build_content_scorecard, render_content_scorecard
        from Core.customer_report import prose
        result, _ = h.run(payload=h.graph(dict(ANYONE_OFF, ExternalUserExpirationRequired=True)))
        html = render_content_scorecard(build_content_scorecard(result), prose)
        section = html.split("What&#x27;s working", 1)[-1].split('Needs improvement', 1)[0] if "What&#x27;s working" in html else \
            html.split("What's working", 1)[1].split('Needs improvement', 1)[0]
        self.assertRegex(section, r'(?i)Anyone-link creation is restricted')
        self.assertRegex(section, r'(?i)Guest access expiration is configured')
        self.assertIn('2026-10-18', section)
        self.assertNotRegex(section, r'(?i)\bscore\b|\bgrade\b|\d+\s?%')


if __name__ == '__main__':
    unittest.main()

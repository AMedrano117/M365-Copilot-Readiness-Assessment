"""Declared Content & Sharing scenarios (fictional data only).

Each scenario declares, before production logic runs: control statuses,
findings and readiness treatments, whether the sharing strength is
established, the customer scorecard sections, and prohibited conclusions.
The basis for each expectation is in docs/CONTENT_SHARING_VALIDATION.md.
"""
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

try:
    from tests import content_sharing_scenarios as h
    from tests.provenance_fixture import fictional_publication, fictional_revision
except ImportError:  # unittest discover -s tests
    import content_sharing_scenarios as h
    from provenance_fixture import fictional_publication, fictional_revision

NOT_OBSERVED = ('not', 'Observed')
STRENGTH = 'sharepoint-sharing-restricted'
# Conclusions the evidence never supports.
PROHIBITED = (r'(?i)links? (are|remain|is) (still )?active', r'(?i)(were|was|have been) removed', r'(?i)no (anyone|anonymous) links exist',
              r'(?i)effectively governed', r'(?i)\d+ exposed items', r'(?i)SharePoint Advanced Management (licen[cs]e )?is (required|mandatory)')


def compliant(**override):
    return h.spo(dict(h.COMPLIANT, **override))


SCENARIOS = {
    '01_exact_compliant_configuration': dict(
        payload=compliant(), strength=True,
        controls={'CONTENT.SHARING': 'Observed', 'CONTENT.PERMISSIONS': 'Not established', 'DATA.EXPOSURE': 'Not established'},
        absent_prefix='sharepoint.sharing.',
        scorecard={'working': {'CONTENT.SHARING'}, 'not_established': {'CONTENT.PERMISSIONS', 'DATA.EXPOSURE', 'CONTENT.OWNERSHIP'},
                   'needs_improvement': set(), 'exposure_to_review': set()}),
    '02_anyone_prohibited_no_conflicting_exposure': dict(
        payload=compliant(), sam=[('permissions.csv', h.zero_snapshot())], strength=True,
        controls={'CONTENT.SHARING': 'Observed'}, absent={'data_exposure.anonymous_links'}),
    '03_policy_prohibits_anyone_but_exposure_reports_links': dict(
        payload=compliant(), sam=[('permissions.csv', h.snapshot(h.snapshot_row('site-alpha', h.SITE_A, anyone=3)))],
        strength=True, controls={'CONTENT.SHARING': 'Observed', 'CONTENT.PERMISSIONS': NOT_OBSERVED},
        present={'data_exposure.anonymous_links': {'ReadinessTreatment': 'ScopeReviewRequired', 'SeverityBasis': 'presence_only'}},
        not_failed={'CONTENT.PERMISSIONS'},
        scorecard={'working': {'CONTENT.SHARING'}, 'exposure_to_review': {'data_exposure.anonymous_links'}},
        coexistence=True),
    '04_anyone_expiry_not_configured': dict(
        payload=compliant(SharingCapability='ExternalUserAndGuestSharing', OneDriveSharingCapability='ExternalUserAndGuestSharing'),
        strength=False, controls={'CONTENT.SHARING': NOT_OBSERVED},
        present={'sharepoint.sharing.anyone_enabled': {}},
        settings={'sharepoint.sharing.anyone_enabled': {'RequireAnonymousLinksExpireInDays': 'does_not_meet', 'SharingCapability': 'does_not_meet'}}),
    '05_anyone_file_link_allows_edit': dict(
        payload=compliant(SharingCapability='ExternalUserAndGuestSharing', RequireAnonymousLinksExpireInDays=14, FileAnonymousLinkType='Edit'),
        strength=False, controls={'CONTENT.SHARING': NOT_OBSERVED},
        settings={'sharepoint.sharing.anyone_enabled': {'FileAnonymousLinkType': 'does_not_meet',
                                                        'RequireAnonymousLinksExpireInDays': 'meets', 'FolderAnonymousLinkType': 'meets'}}),
    '06_broader_default_link': dict(
        payload=compliant(DefaultSharingLinkType='Internal'), strength=False, controls={'CONTENT.SHARING': NOT_OBSERVED},
        present={'sharepoint.sharing.organization_default': {'Priority': 'Medium'}}),
    '06b_none_default_with_anyone_and_no_expiry': dict(
        payload=compliant(SharingCapability='ExternalUserAndGuestSharing', DefaultSharingLinkType='None'), strength=False,
        present={'sharepoint.sharing.permissive_anonymous_defaults': {'Priority': 'High'}}, absent={'sharepoint.sharing.anyone_enabled'}),
    '07_guest_expiry_satisfies_methodology': dict(
        payload=compliant(), strength=True, controls={'CONTENT.SHARING': 'Observed'},
        strength_settings={'ExternalUserExpirationRequired': 'meets'}),
    '08_guest_resharing_does_not_satisfy_methodology': dict(
        payload=compliant(PreventExternalUsersFromResharing=False), strength=True, controls={'CONTENT.SHARING': 'Observed'},
        strength_settings={'PreventExternalUsersFromResharing': 'does_not_meet'},
        scorecard={'working': {'CONTENT.SHARING'}, 'needs_improvement': {'PreventExternalUsersFromResharing'}}),
    '09_external_sharing_disabled': dict(
        payload=compliant(SharingCapability='Disabled', OneDriveSharingCapability='Disabled', ExternalUserExpirationRequired=False),
        strength=True, controls={'CONTENT.SHARING': 'Observed'},
        strength_settings={'ExternalUserExpirationRequired': 'not_applicable', 'PreventExternalUsersFromResharing': 'not_applicable'},
        scorecard={'needs_improvement': set()}),
    '10_guests_allowed_anyone_prohibited': dict(
        payload=compliant(), strength=True, controls={'CONTENT.SHARING': 'Observed'},
        strength_settings={'RequireAnonymousLinksExpireInDays': 'not_applicable', 'SharingCapability': 'meets'}),
    '11_organization_wide_links_present': dict(
        payload=compliant(), sam=[('permissions.csv', h.snapshot(h.snapshot_row('site-alpha', h.SITE_A, org=4)))], strength=True,
        controls={'CONTENT.SHARING': 'Observed'}, not_failed={'CONTENT.PERMISSIONS'},
        present={'data_exposure.broad_internal_access': {'ReadinessTreatment': 'ScopeReviewRequired'}},
        units={'sam.peopleinyourorglinkcount': 'links', 'sam.organizationlinks': 'links'},
        scorecard={'exposure_to_review': {'data_exposure.broad_internal_access'}}),
    '12_everyone_permissions_present': dict(
        sam=[('permissions.csv', h.snapshot(h.snapshot_row('site-alpha', h.SITE_A, everyone=2)))], not_failed={'CONTENT.PERMISSIONS'},
        present={'data_exposure.broad_internal_access': {'ReadinessTreatment': 'ScopeReviewRequired'}}),
    '13_eeeu_permissions_present': dict(
        sam=[('permissions.csv', h.snapshot(h.snapshot_row('site-alpha', h.SITE_A, eeeu=5)))], not_failed={'CONTENT.PERMISSIONS'},
        present={'data_exposure.broad_internal_access': {'ReadinessTreatment': 'ScopeReviewRequired'}}),
    '14_external_recipients_present': dict(
        sam=[('permissions.csv', h.snapshot(h.snapshot_row('site-alpha', h.SITE_A, guests=3, external=1)))], not_failed={'CONTENT.PERMISSIONS'},
        present={'data_exposure.external_exposure': {'ReadinessTreatment': 'ScopeReviewRequired'}}),
    '15_missing_dspm_evidence': dict(
        payload=compliant(), sam=[('permissions.csv', h.zero_snapshot())],
        controls={'DATA.EXPOSURE': 'Not established'},
        present={'data_exposure.coverage.sensitive-data_exposure_evidence': {'Disposition': 'Coverage'}},
        scorecard={'not_established': {'DATA.EXPOSURE'}}),
    '16_incomplete_dag_evidence': dict(
        payload=h.spo(h.COMPLIANT, dag=[{'RequestedEntity': 'PermissionedUsers', 'RequestedWorkload': 'SharePoint', 'Status': 'Completed',
                                         'CompletedDateTime': '2026-10-14T00:00:00Z'}]),
        controls={'CONTENT.SHARING': 'Observed', 'CONTENT.PERMISSIONS': 'Not established'},
        present={'sharepoint.dag.not_assessed': {'Disposition': 'Coverage'}}, dag_answers='CONTENT.PERMISSIONS'),
    '17_undated_dag_evidence': dict(
        sam=[('permissions.csv', h.snapshot(h.snapshot_row('site-alpha', h.SITE_A, anyone=2, date='')))],
        present={'data_exposure.freshness.sam': {'Disposition': 'Coverage'}, 'data_exposure.anonymous_links': {'EvidenceStatus': ('not', 'supported')}},
        not_failed={'CONTENT.PERMISSIONS'}),
    '18_zero_exposure_with_insufficient_completeness': dict(
        sam=[('permissions.csv', h.zero_snapshot())],
        dspm_files=[('dspm.csv', h.dspm(h.dspm_row(h.SITE_A, 'SharePoint'), h.dspm_row(h.PERSONAL, 'OneDrive')))],
        controls={'CONTENT.PERMISSIONS': NOT_OBSERVED, 'DATA.EXPOSURE': NOT_OBSERVED},
        present={'data_exposure.assessed_no_signals': {'EvidenceStatus': ('not', 'supported')}}),
    '21_conflicting_evidence_from_different_sources': dict(
        payload=h.conflicting(), strength=False, controls={'CONTENT.SHARING': 'Not established'},
        present={'sharepoint.sharing.conflicting_sources': {'Disposition': 'Coverage'}}),
    '22_current_configuration_older_exposure': dict(
        payload=compliant(), sam=[('permissions.csv', h.snapshot(h.snapshot_row('site-alpha', h.SITE_A, anyone=3, date=h.OLD_REPORT)))],
        strength=True, controls={'CONTENT.SHARING': 'Observed'},
        present={'data_exposure.freshness.sam': {'Disposition': 'Coverage'},
                 'data_exposure.anonymous_links': {'EvidenceStatus': 'stale', 'ObservationDate': '2026-08-20'}}),
    '23_older_configuration_current_exposure': dict(
        payload=compliant(), collected='2026-08-01T09:00:00+00:00',
        sam=[('permissions.csv', h.snapshot(h.snapshot_row('site-alpha', h.SITE_A, anyone=3)))],
        strength=False, controls={'CONTENT.SHARING': 'Not established'},
        present={'data_exposure.anonymous_links': {'EvidenceStatus': 'supported', 'ObservationDate': '2026-10-15'}}),
    '24_evidence_with_unknown_timestamp': dict(
        payload=compliant(), sam=[('permissions.csv', h.snapshot(h.snapshot_row('site-alpha', h.SITE_A, eeeu=2, date='')))],
        present={'data_exposure.broad_internal_access': {'ObservationDate': '', 'EvidenceStatus': ('not', 'supported')}}),
    '27_apparently_positive_configuration_with_insufficient_evidence': dict(
        payload=h.spo({key: value for key, value in h.COMPLIANT.items() if key != 'SharingCapability'}), strength=False,
        controls={'CONTENT.SHARING': 'Not established'}),
}


def check(test, value, expected, label):
    if isinstance(expected, tuple) and expected[0] == 'not':
        test.assertNotEqual(value, expected[1], label)
    else:
        test.assertEqual(value, expected, label)


def customer_text(result, scorecard):
    parts = [json.dumps(scorecard, default=str)]
    for row in result['recommendations']:
        if row.get('DomainId') == 'content' or str(row.get('FindingKey') or '').startswith(('sharepoint', 'data_exposure')):
            parts += [str(row.get('Observation') or ''), str(row.get('Recommendation') or '')]
    return ' '.join(parts)


@fictional_publication
class ContentSharingScenarioTests(unittest.TestCase):
    def check_scenario(self, name):
        spec = SCENARIOS[name]
        result, exposure = h.run(payload=spec.get('payload'), sam=spec.get('sam', ()), dspm_files=spec.get('dspm_files', ()),
                                 collected=spec.get('collected', h.COLLECTED))
        for control_id, expected in (spec.get('controls') or {}).items():
            check(self, h.control(result, control_id)['status'], expected, control_id)
        for control_id in spec.get('not_failed', ()):
            self.assertNotEqual(h.control(result, control_id)['status'], 'Action required', control_id + ' must not fail on presence-only exposure')
        for key, fields in (spec.get('present') or {}).items():
            rows = h.rows_with_key(result, key)
            self.assertTrue(rows, key + ' expected')
            for field, expected in fields.items():
                value = rows[0].get(field)
                if field == 'ObservationDate' and value:
                    value = value[:10]
                check(self, value, expected, f'{key}.{field}')
        for key in spec.get('absent', ()):
            self.assertFalse(h.rows_with_key(result, key), key + ' not expected')
        if spec.get('absent_prefix'):
            self.assertFalse([row['FindingKey'] for row in result['recommendations']
                              if str(row.get('FindingKey') or '').startswith(spec['absent_prefix']) and row.get('Disposition') in {'Action', 'Coverage'}])
        if 'strength' in spec:
            supported = [row for row in result['strengths'] if row.get('FindingKey') == STRENGTH]
            self.assertEqual(bool(supported), spec['strength'], 'sharing strength established')
        for key, expected in (spec.get('settings') or {}).items():
            results = {item['setting']: item['result'] for item in h.rows_with_key(result, key)[0]['SharingSettingResults']}
            for setting, value in expected.items():
                self.assertEqual(results[setting], value, f'{key}: {setting}')
        if spec.get('strength_settings'):
            row = next(row for row in result['strengths'] if row.get('FindingKey') == STRENGTH)
            results = {item['setting']: item['result'] for item in row['SharingSettingResults']}
            for setting, value in spec['strength_settings'].items():
                self.assertEqual(results[setting], value, setting)
        for metric, unit in (spec.get('units') or {}).items():
            units = {row['unit'] for row in result['evidence'] if row.get('metric_id') == metric}
            if units:
                self.assertEqual(units, {unit}, metric)
        if spec.get('dag_answers'):
            for control_id in ('CONTENT.SHARING', 'CONTENT.PERMISSIONS'):
                keys = {item['finding_key'] for item in h.control(result, control_id)['component_results']}
                self.assertEqual('sharepoint.dag.not_assessed' in keys, control_id == spec['dag_answers'], control_id)
        from Core.content_scorecard import build_content_scorecard
        scorecard = build_content_scorecard(result)
        names = {section: {item.get('control_id') if section in {'working', 'not_established'} else item.get('finding_key') or item.get('setting')
                           for item in scorecard[section]} for section in ('working', 'needs_improvement', 'exposure_to_review', 'not_established')}
        for section, expected in (spec.get('scorecard') or {}).items():
            if expected:
                self.assertTrue(expected <= names[section], f'{section}: {names[section]}')
            else:
                self.assertEqual(names[section], set(), section)
        if spec.get('coexistence'):
            self.assertRegex(scorecard['summary'], r'(?i)current sharing policy .* existing')
        self.assertFalse(re.search(r'(?i)score|grade|percent|%', json.dumps(scorecard['control_counts'])))
        text = customer_text(result, scorecard)
        for pattern in PROHIBITED:
            self.assertNotRegex(text, pattern, 'prohibited conclusion')


def _scenario_test(name):
    def test(self):
        self.check_scenario(name)
    test.__doc__ = name
    return test


for _name in SCENARIOS:
    setattr(ContentSharingScenarioTests, 'test_S' + _name, _scenario_test(_name))


@fictional_publication
class ExposureCountingTests(unittest.TestCase):
    """Units and identity are preserved; overlapping measures and repeated ingestion never inflate counts."""

    ROW = h.snapshot_row('site-alpha', h.SITE_A, anyone=2, eeeu=3, org=4)

    def test_S19_overlapping_measurements_are_not_summed(self):
        result, exposure = h.run(sam=[('permissions.csv', h.snapshot(self.ROW))])
        signals = exposure['sources']['sam']['signals']
        self.assertEqual((signals['Anyone links'], signals['EEEU permissions'], signals['Organization links']), (2, 3, 4))
        broad = h.rows_with_key(result, 'data_exposure.broad_internal_access')[0]
        self.assertIn('can overlap', broad['Observation'])
        self.assertNotIn('7 ', broad['Observation'])  # EEEU permissions and organization links are never added
        units = {row['label']: row['unit'] for row in exposure['observations'] if row.get('source_schema') == 'permission_snapshot'}
        self.assertEqual((units['Anyone links'], units['EEEU permissions'], units['Organization links']), ('links', 'permissions', 'links'))

    def test_S20_repeated_ingestion_does_not_increase_counts(self):
        _, once = h.run(sam=[('permissions.csv', h.snapshot(self.ROW))])
        _, twice = h.run(sam=[('permissions.csv', h.snapshot(self.ROW)), ('permissions-copy.csv', h.snapshot(self.ROW))])
        self.assertEqual(once['sources']['sam']['signals'], twice['sources']['sam']['signals'])
        self.assertEqual(len(once['evidence_rows']), len(twice['evidence_rows']))
        self.assertEqual(twice['evidence_rows'][0]['Source File'], 'permissions-copy.csv; permissions.csv')

    def test_S25_observations_from_different_periods(self):
        older = h.snapshot(h.snapshot_row('site-alpha', h.SITE_A, anyone=9, date='2026-10-01T00:00:00Z'))
        newer = h.snapshot(h.snapshot_row('site-alpha', h.SITE_A, anyone=2, date=h.CURRENT_REPORT))
        _, exposure = h.run(sam=[('older.csv', older), ('newer.csv', newer)])
        reports = {report['source_file']: report for report in exposure['sources']['sam']['reports']}
        self.assertEqual((reports['older.csv']['status'], reports['newer.csv']['status']), ('superseded', 'selected'))
        self.assertEqual(exposure['sources']['sam']['signals']['Anyone links'], 2)
        dates = {row['observed_at'][:10] for row in exposure['observations'] if row.get('label') == 'Anyone links'}
        self.assertEqual(dates, {'2026-10-01', '2026-10-15'})  # both periods retained with their own dates


@fictional_publication
class PositiveProvenanceTests(unittest.TestCase):
    """S26: an Established control is as auditable as a finding."""

    def test_S26_established_control_retains_provenance(self):
        result, _ = h.run(payload=h.spo(h.COMPLIANT))
        sharing = h.control(result, 'CONTENT.SHARING')
        self.assertEqual(sharing['status'], 'Observed')
        self.assertTrue(sharing['evidence_ids'])
        supporting = [row for row in result['recommendations'] if row.get('EvidenceId') in sharing['evidence_ids']]
        self.assertEqual(len(supporting), 1)
        row = supporting[0]
        self.assertEqual((row['ControlId'], row['Disposition'], row['EvidenceStatus']), ('CONTENT.SHARING', 'Assurance', 'supported'))
        self.assertTrue(row['ObservedValues'])                        # observed value
        self.assertTrue(row['ExpectedRule'])                          # expected value or rule
        self.assertTrue(row['MethodologyRule'])                       # methodology requirement
        self.assertTrue(row['EvidenceScope'])                         # assessed scope
        self.assertEqual(row['EvidenceSource'], 'sharepoint_tenant_settings')  # evidence source
        self.assertTrue(row['SourceFile'])
        self.assertEqual(row['ObservationDate'][:10], '2026-10-18')    # evidence timestamp
        self.assertEqual(row['Freshness'], 'current')
        self.assertTrue(all(item['result'] in {'meets', 'not_applicable'} for item in row['SharingSettingResults']
                            if item['requirement'] == 'CONTENT.SHARING'))
        from Core.content_scorecard import build_content_scorecard
        working = next(item for item in build_content_scorecard(result)['working'] if item['control_id'] == 'CONTENT.SHARING')
        for field in ('observed', 'expected', 'scope', 'source', 'observed_at', 'methodology_rule', 'result'):
            self.assertTrue(working['provenance'].get(field), field)

    def test_established_requires_sufficient_evidence_not_collection_success(self):
        for payload in (h.spo({'SharingCapability': 'ExternalUserSharingOnly', 'DefaultSharingLinkType': 'Internal'}),
                        h.graph({'SharingCapability': 'ExternalUserSharingOnly'})):
            with self.subTest(source=payload['source']):
                result, _ = h.run(payload=payload)
                self.assertNotEqual(h.control(result, 'CONTENT.SHARING')['status'], 'Observed')

    def test_configuration_does_not_establish_operation(self):
        result, _ = h.run(payload=h.spo(h.COMPLIANT))
        sharing = h.control(result, 'CONTENT.SHARING')
        self.assertEqual(sharing['configuration_result'], 'supported')
        self.assertEqual(sharing['operational_result'], 'not_applicable')
        row = next(row for row in result['strengths'] if row.get('FindingKey') == STRENGTH)
        self.assertEqual({item['establishes'] for item in row['SharingSettingResults']}, {'configuration'})


@fictional_publication
class ScorecardRenderOnlyTests(unittest.TestCase):
    """S28: rendering the scorecard changes presentation only."""

    def test_S28_render_only_has_zero_semantic_change(self):
        import sys
        here = Path(__file__).resolve().parent
        for folder in (here.parent, here):
            if str(folder) not in sys.path:
                sys.path.insert(0, str(folder))
        from tools import validate as v
        from test_validation_workflow import build_retained_package
        temp = Path(tempfile.mkdtemp(prefix='content-scorecard-render-'))
        try:
            package = build_retained_package(temp / 'retained')
            before = v.tree_hashes(package)
            snapshot = next(package.glob('assessment-results/*.json'))
            report = v.render_only(snapshot, temp / 'render', revision=fictional_revision())
            self.assertTrue(report['passed'], report['failures'])
            self.assertFalse(report['semantic_differences'])
            self.assertFalse(report['tenant_access_performed'])
            html = next(Path(report['render_directory']).rglob('*.html')).parent
            text = ' '.join(path.read_text(encoding='utf-8') for path in html.glob('*.html'))
            self.assertIn('content-scorecard', text)
            self.assertNotRegex(text.split('content-scorecard', 1)[1][:4000], r'(?i)\bscore:|\bgrade\b|\d+\s?%')
            self.assertEqual(v.tree_hashes(package), before)
        finally:
            shutil.rmtree(temp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()

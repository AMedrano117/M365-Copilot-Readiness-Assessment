"""Flagged applications link to their original, unmerged delegated grants."""

from datetime import datetime, timezone
import re
from types import SimpleNamespace
import unittest

from Core.application_investigation import build_application_grant_investigation


def app(identifier='client-sp', **values):
    return {'Enterprise Application Object ID': identifier, 'Application (Client) ID': 'client-app',
            'App Display Name': 'Flagged fixture app', 'Flagged Because': 'Unverified publisher',
            'Publisher Verification State': 'No', **values}


def grant(identifier, client_id='client-sp', **values):
    return {'id': identifier, 'clientId': client_id, 'resourceId': 'resource-sp',
            'consentType': 'AllPrincipals', 'principalId': None, 'scope': 'User.Read', **values}


def client(grants):
    return SimpleNamespace(oauth_permission_grants=grants,
        service_principals=[{'id': 'client-sp', 'appId': 'client-app', 'displayName': 'Flagged fixture app'},
                            {'id': 'resource-sp', 'appId': 'resource-app', 'displayName': 'Resource fixture'}],
        collection_status={'oauth_grants': {'available': True, 'availability_status': 'available',
                                            'pages_collected': 3, 'truncated': False}})


class ApplicationInvestigationTests(unittest.TestCase):
    def test_selects_flagged_client_grants_without_merging_resources_principals_or_scopes(self):
        source = client([grant('one', scope='Mail.ReadWrite User.Read'),
                         grant('unflagged', client_id='other-sp'),
                         grant('two', resourceId='different-resource', consentType='Principal',
                               principalId='user-object', scope='openid profile')])
        result = build_application_grant_investigation(source, [app()])
        self.assertEqual([row['Grant ID'] for row in result['rows']], ['one', 'two'])
        self.assertEqual(result['rows'][0]['Granted Scopes'], 'Mail.ReadWrite User.Read')
        self.assertEqual(result['rows'][1]['Granted Scopes'], 'openid profile')
        self.assertEqual(result['rows'][1]['Principal ID'], 'user-object')
        self.assertEqual(result['rows'][1]['Resource Service Principal ID'], 'different-resource')
        self.assertEqual(result['rows'][1]['Resource Display Name'], 'Not retained')
        self.assertIs(result['rows'][0]['High Privilege Match'], True)
        self.assertIs(result['rows'][1]['High Privilege Match'], False)
        self.assertEqual(result['high_privilege_grant_count'], 1)
        self.assertEqual(result['matched_count'], 2)
        self.assertEqual(result['matched_app_count'], 1)
        self.assertEqual(result['source_record_count'], 3)
        self.assertEqual([row['Source Row'] for row in result['rows']], [1, 3])

    def test_ranges_group_actual_grants_per_app_and_escape_sheet_names(self):
        source = client([grant('b1', client_id='b'), grant('a1'), grant('b2', client_id='b')])
        result = build_application_grant_investigation(source, [app(), app('b')], sheet_title="App's Grant Detail")
        self.assertEqual([row['Grant ID'] for row in result['rows']], ['a1', 'b1', 'b2'])
        self.assertEqual(result['app_ranges'], {'client-sp': "'App''s Grant Detail'!A2:AC2", 'b': "'App''s Grant Detail'!A3:AC4"})
        self.assertEqual(result['matched_app_count'], 2)

    def test_app_ids_are_resolved_never_used_as_grant_client_ids(self):
        source = client([grant('actual'), grant('wrong', client_id='client-app')])
        result = build_application_grant_investigation(source, [app('', **{'App Display Name': 'Same name'})])
        self.assertEqual([row['Grant ID'] for row in result['rows']], ['actual'])
        source.service_principals.append({'id': 'second-sp', 'appId': 'client-app', 'displayName': 'Same name'})
        ambiguous = build_application_grant_investigation(source, [app('')])
        self.assertEqual(ambiguous['rows'], [])
        self.assertEqual(ambiguous['unmatched_app_count'], 1)
        self.assertIn('could not be resolved', ambiguous['reconciliation_note'])

    def test_same_names_do_not_join_apps_and_duplicate_flag_rows_do_not_duplicate_grants(self):
        result = build_application_grant_investigation(client([grant('actual')]),
                   [app(), app('CLIENT-SP'), app('absent'), app('other', **{'Flagged Because': ''})])
        self.assertEqual(result['flagged_app_count'], 2)
        self.assertEqual(result['matched_count'], 1)
        self.assertEqual(result['unmatched_app_count'], 1)
        self.assertNotIn('absent', result['app_ranges'])

    def test_sdk_rows_and_collected_provenance_are_retained(self):
        raw = grant('sdk', createdDateTime=datetime(2026, 9, 30, tzinfo=timezone.utc))
        sdk = SimpleNamespace(**{re.sub(r'(?<!^)(?=[A-Z])', '_', key).lower(): value for key, value in raw.items()})
        result = build_application_grant_investigation(client([sdk]), [app()],
                    collection_context={'source_file': 'fixture.json', 'collected_at': '2026-09-30T12:00:00Z'})
        row = result['rows'][0]
        self.assertEqual(row['Grant Created UTC'], '2026-09-30T00:00:00+00:00')
        self.assertEqual(row['Collected At'], '2026-09-30T12:00:00Z')
        self.assertEqual(row['Source File'], 'fixture.json')
        self.assertEqual(row['Resource Application ID'], 'resource-app')
        self.assertEqual(row['Resource Display Name'], 'Resource fixture')
        self.assertEqual(row['Pages Collected'], 3)
        self.assertIs(row['Truncated'], False)

    def test_partial_or_missing_source_cannot_create_rows_from_merged_scopes(self):
        partial = client([grant('retained')])
        partial.collection_status['oauth_grants'].update(available=False, availability_status='partial', truncated=True)
        result = build_application_grant_investigation(partial, [app()])
        self.assertEqual(result['rows'][0]['Source State'], 'partial')
        self.assertIn('retained records only', result['reconciliation_note'])
        missing = build_application_grant_investigation(SimpleNamespace(), [app(**{'Granted Scopes': 'Mail.ReadWrite'})])
        self.assertEqual(missing['rows'], [])
        self.assertIn('did not retain oauth_permission_grants', missing['unavailability_reason'])
        self.assertEqual(missing['app_ranges'], {})

    def test_original_repeated_grant_records_and_missing_ids_remain_explicit(self):
        result = build_application_grant_investigation(client([grant('repeat'), grant('repeat'), grant(None)]), [app()])
        self.assertEqual(result['matched_count'], 3)
        self.assertEqual(result['unique_grant_count'], 1)
        self.assertIn('1 records missing an ID', result['reconciliation_note'])
        self.assertIn('1 repeated-ID records', result['reconciliation_note'])

    def test_empty_selection_never_exports_unflagged_inventory(self):
        result = build_application_grant_investigation(client([grant('hidden')]), [])
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['flagged_app_count'], 0)
        self.assertEqual(result['unavailability_reason'], '')


if __name__ == '__main__':
    unittest.main()

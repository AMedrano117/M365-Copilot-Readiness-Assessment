"""MFA worklists identify only known unregistered users in the reported population."""

from datetime import datetime, timezone
from enum import Enum
import json
import re
from types import SimpleNamespace
import unittest

from Core.authentication_investigation import build_mfa_registration_investigation
from Core.authentication_methods import summarize_registrations


def user(identifier, flag=False, **values):
    return {'id': identifier, 'userPrincipalName': identifier + '@example.test',
            'userDisplayName': 'Same display name', 'userType': 'member',
            'isAdmin': False, 'isMfaRegistered': flag, 'isMfaCapable': False,
            'methodsRegistered': ['email'], 'lastUpdatedDateTime': '2026-09-29T10:00:00Z', **values}


def client(records, **values):
    return SimpleNamespace(auth_methods_registration=records,
                           collection_status={'auth_methods': {'available': True, 'availability_status': 'available',
                                                               'pages_collected': 2, 'truncated': False}}, **values)


class MfaRegistrationInvestigationTests(unittest.TestCase):
    def test_only_explicit_false_flags_identify_users_and_counts_reconcile(self):
        records = [user('absent'), user('enrolled', True), user('unknown', None),
                   user('text-false', 'false'), user('number-zero', 0)]
        result = build_mfa_registration_investigation(client(records))
        self.assertEqual([row['User ID'] for row in result['rows']], ['absent'])
        self.assertEqual((result['matched_count'], result['registered_count'], result['unknown_count'], result['total_count']), (1, 1, 3, 5))
        self.assertEqual(result['unavailability_reason'], '')
        self.assertIn('1 investigation rows', result['reconciliation_note'])

    def test_duplicate_population_and_conflicts_match_existing_aggregate(self):
        records = [user('same-id'), user('same-id'), user('conflict'), user('conflict', True),
                   user('different-id'), user('method-conflict'), user('method-conflict', methodsRegistered=[])]
        report = summarize_registrations(records)
        result = build_mfa_registration_investigation(client(records))
        self.assertEqual([row['User ID'] for row in result['rows']], ['same-id', 'different-id'])
        self.assertEqual(result['rows'][0]['Source Row'], '1; 2')
        self.assertEqual(result['total_count'], report['total_users'])
        self.assertEqual(result['unknown_count'], 2)
        self.assertEqual(result['registered_count'], report['metrics']['mfa_registered'])
        self.assertEqual(result['matched_count'] + result['registered_count'] + result['unknown_count'], result['total_count'])
        self.assertIn('2 users with conflicting snapshots', result['reconciliation_note'])

    def test_upn_fallback_and_anonymous_records_are_not_deduplicated_by_name(self):
        first = user('upn-only')
        del first['id']
        anonymous = {'userDisplayName': 'Same display name', 'isMfaRegistered': False}
        result = build_mfa_registration_investigation(client([first, dict(first), anonymous, dict(anonymous)]))
        self.assertEqual(result['matched_count'], 3)
        self.assertEqual(result['rows'][1]['User ID'], 'Not returned')
        self.assertEqual(result['rows'][1]['Source Row'], '3')
        self.assertEqual(result['rows'][2]['Source Row'], '4')

    def test_sdk_attributes_and_enums_preserve_engineering_fields(self):
        class Kind(Enum):
            GUEST = 'guest'
        record = user('sdk-user', userType=Kind.GUEST, isAdmin=True,
                      lastUpdatedDateTime=datetime(2026, 9, 29, 10, tzinfo=timezone.utc))
        sdk = SimpleNamespace(**{re.sub(r'(?<!^)(?=[A-Z])', '_', key).lower(): value for key, value in record.items()})
        result = build_mfa_registration_investigation(client([sdk]))
        row = result['rows'][0]
        self.assertEqual(row['User Principal Name'], 'sdk-user@example.test')
        self.assertEqual(row['User Display Name'], 'Same display name')
        self.assertEqual(row['User Type'], 'guest')
        self.assertIs(row['Is Admin'], True)
        self.assertIs(row['MFA Registered'], False)
        self.assertIs(row['MFA Capable'], False)
        self.assertEqual(row['Methods Registered'], 'email')
        self.assertEqual(row['Last Updated UTC'], '2026-09-29T10:00:00+00:00')
        self.assertEqual(row['Pages Collected'], 2)
        self.assertIs(row['Truncated'], False)
        self.assertTrue(row['Source API'].endswith('/reports/authenticationMethods/userRegistrationDetails'))

    def test_partial_source_retains_known_users_and_qualifies_counts(self):
        source = client([user('partial')])
        source.collection_status['auth_methods'].update(available=False, availability_status='partial', truncated=True,
                                                        collection_started_at='2026-09-29T10:00:00Z')
        result = build_mfa_registration_investigation(source)
        self.assertEqual(result['matched_count'], 1)
        self.assertEqual(result['rows'][0]['Source State'], 'partial')
        self.assertIs(result['rows'][0]['Truncated'], True)
        self.assertEqual(result['rows'][0]['Collection Started UTC'], '2026-09-29T10:00:00Z')
        self.assertIn('retained records only', result['reconciliation_note'])

    def test_unavailable_and_legacy_aggregate_do_not_fabricate_users(self):
        legacy = SimpleNamespace(auth_summary={'total_users': 40, 'mfa_registered': 3})
        result = build_mfa_registration_investigation(legacy)
        self.assertEqual(result['rows'], [])
        self.assertIn('did not retain auth_methods_registration', result['unavailability_reason'])
        self.assertIn('Saved summary mismatch: total users 40', result['reconciliation_note'])
        failed = client([])
        failed.collection_status['auth_methods'].update(available=False, availability_status='unavailable', status_code=403)
        self.assertIn('HTTP 403', build_mfa_registration_investigation(failed)['unavailability_reason'])

    def test_complete_empty_and_registered_only_sources_have_distinct_explanations(self):
        empty = build_mfa_registration_investigation(client([]))
        self.assertIn('completed registration report returned zero', empty['unavailability_reason'])
        enrolled = build_mfa_registration_investigation(client([user('registered', True)]))
        self.assertEqual(enrolled['rows'], [])
        self.assertEqual(enrolled['unavailability_reason'], '')
        unknown = build_mfa_registration_investigation(client([user('unknown', None)]))
        self.assertIn('1 users have unknown', unknown['unavailability_reason'])

    def test_no_identities_escape_rows_and_input_is_not_mutated(self):
        records = [user('sensitive-identity')]
        before = json.dumps(records, sort_keys=True)
        result = build_mfa_registration_investigation(client(records, auth_summary={'total_users': 1, 'mfa_registered': 0}))
        metadata = json.dumps({key: value for key, value in result.items() if key != 'rows'})
        self.assertNotIn('sensitive-identity', metadata)
        self.assertNotIn('Same display name', metadata)
        self.assertEqual(json.dumps(records, sort_keys=True), before)


if __name__ == '__main__':
    unittest.main()

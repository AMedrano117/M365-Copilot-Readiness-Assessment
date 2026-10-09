"""Fictional evidence regressions for shared privileged identity interpretation."""
import copy
import unittest
from unittest.mock import patch

NOW = '2026-10-09T12:00:00Z'
OLD = '2026-01-01T00:00:00Z'
RECENT = '2026-10-08T12:00:00.0000007Z'
FUTURE = '2027-01-01T00:00:00Z'
BOUNDARY = {'assessment_id': 'fictional-assessment', 'environment_id': 'fictional-environment',
            'provider': 'Microsoft Graph directory'}


def grant(identifier='grant-1', principal='user-1', **extra):
    return dict(id=identifier, principalId=principal, roleDefinitionId='role-1', directoryScopeId='/',
                principal={'id': principal, '@odata.type': '#microsoft.graph.user'},
                roleDefinition={'id': 'role-1', 'displayName': 'Global Administrator'},
                status='Provisioned', scheduleInfo={'startDateTime': OLD, 'expiration': {'type': 'noExpiration'}}, **extra)


def dataset(records, **state):
    return {'records': records, 'source': {'available': True, 'availability_status': 'available', 'complete': True,
            'truncated': False, 'collected_at': NOW, 'source_file': 'fictional-retained-evidence.json', **state}}


def sources(assignments=None, users=None, **extra):
    data = {'role_assignments': [dataset([])], 'role_assignment_schedules': [dataset(assignments if assignments is not None else [grant()])],
            'role_eligibility_schedules': [dataset([])], 'users': [dataset(users if users is not None else
            [{'id': 'user-1', 'accountEnabled': True, 'signInActivity': {'lastSuccessfulSignInDateTime': OLD}}])],
            'auth_methods': [dataset([])], 'signin_logs': [dataset([], window_start=OLD, window_end=NOW)]}
    data.update(extra)
    return data


def policy(days=90, **extra):
    return dict(policy_id='fictional-policy', rule_version='1.0.0', threshold=days, threshold_unit='days',
                methodology_reference='docs/METHODOLOGY.md', **extra)


def build(data=None, **kwargs):
    from Core.privileged_identity import build_privileged_assessment
    return build_privileged_assessment(data or sources(), evaluation_timestamp=NOW, boundary=BOUNDARY, **kwargs)


class AssignmentTemporalTests(unittest.TestCase):
    def test_temporal_matrix(self):
        from Core.privileged_assignments import classify_assignment
        cases = [('active', OLD, None, 'noExpiration', 'ActivePermanent'),
                 ('active', OLD, FUTURE, 'afterDateTime', 'ActiveTimeBound'),
                 ('eligible', OLD, None, 'noExpiration', 'EligiblePermanent'),
                 ('eligible', OLD, FUTURE, 'afterDateTime', 'EligibleTimeBound'),
                 ('active', FUTURE, None, 'noExpiration', 'FuturePending'),
                 ('eligible', FUTURE, None, 'noExpiration', 'FuturePending'),
                 ('active', OLD, RECENT, 'afterDateTime', 'Expired'),
                 ('eligible', OLD, RECENT, 'afterDateTime', 'Expired'),
                 ('active', 'invalid', None, 'noExpiration', 'UnknownTemporalState'),
                 ('active', OLD, 'invalid', 'afterDateTime', 'UnknownTemporalState'),
                 ('active', None, None, 'noExpiration', 'UnknownTemporalState'),
                 ('active', OLD, FUTURE, 'noExpiration', 'UnknownTemporalState')]
        for kind, start, end, expiration, expected in cases:
            with self.subTest(kind=kind, expected=expected, start=start):
                row = grant()
                row['scheduleInfo'] = {'startDateTime': start, 'expiration': {'type': expiration, 'endDateTime': end}}
                self.assertEqual(classify_assignment(row, kind, NOW), expected)

    def test_pending_revoked_and_unknown_status_never_active(self):
        from Core.privileged_assignments import classify_assignment
        for status in ('PendingApproval', 'PendingProvisioning', 'Revoked', 'Denied', 'Failed', 'invented'):
            with self.subTest(status=status):
                row = grant(); row['status'] = status
                self.assertEqual(classify_assignment(row, 'active', NOW), 'UnknownTemporalState')

    def test_explicit_evaluation_and_fractional_boundary(self):
        from Core.privileged_assignments import classify_assignment
        row = grant(); row['scheduleInfo']['startDateTime'] = '2026-10-09T12:00:00.0000007Z'
        self.assertEqual(classify_assignment(row, 'active', NOW), 'FuturePending')
        self.assertEqual(classify_assignment(row, 'active', FUTURE), 'ActivePermanent')
        self.assertEqual(classify_assignment(row, 'active', None), 'UnknownTemporalState')

    def test_unscheduled_active_is_duration_unverified(self):
        model = build(sources(assignments=[], role_assignments=[dataset([grant()])]))
        self.assertEqual(model['assignments'][0]['temporal_state'], 'DirectActiveDurationUnverified')

    def test_duration_and_instance_semantics(self):
        from Core.privileged_assignments import classify_assignment
        row = grant(); row['scheduleInfo'] = {'startDateTime': '2026-10-09T11:00:00Z',
                                            'expiration': {'type': 'afterDuration', 'duration': 'PT2H'}}
        self.assertEqual(classify_assignment(row, 'active', NOW), 'ActiveTimeBound')
        instance = {'startDateTime': OLD, 'endDateTime': FUTURE}
        self.assertEqual(classify_assignment(instance, 'eligible', NOW, source_kind='instance'), 'EligibleTimeBound')


class AssignmentIdentityTests(unittest.TestCase):
    def test_scope_and_kind_boundaries_remain_distinct(self):
        rows = [grant('a', appScopeId='/app-one'), grant('b', appScopeId='/app-two'),
                grant('c'), grant('d', administrativeUnitId='au-one')]
        rows[2]['directoryScopeId'] = '/administrativeUnits/au-two'
        model = build(sources(rows, role_eligibility_schedules=[dataset([grant('e', appScopeId='/app-one')])]))
        self.assertEqual(len(model['assignments']), 5)
        self.assertEqual(len({row['assignment_key'] for row in model['assignments']}), 5)

    def test_permutation_insertion_and_duplicate_stability(self):
        rows = [grant('a'), grant('b', appScopeId='/app-two')]
        first = build(sources(rows))
        other = build(sources([grant('new', principal='other')] + list(reversed(rows)) + [copy.deepcopy(rows[0])]))
        expected = {r['source_id']: r['assignment_key'] for r in first['assignments']}
        actual = {r['source_id']: r['assignment_key'] for r in other['assignments']}
        self.assertTrue(all(actual[key] == value for key, value in expected.items()))
        self.assertEqual(len(other['assignments']), 3)

    def test_schedule_and_direct_occurrences_reconcile_without_loss(self):
        model = build(sources(role_assignments=[dataset([grant('direct')])]))
        self.assertEqual(len(model['assignments']), 1)
        self.assertEqual(len(model['assignments'][0]['source_occurrences']), 2)

    def test_ambiguous_reconciliation_retains_both_and_diagnostics(self):
        model = build(sources([grant('one'), grant('two')], role_assignments=[dataset([grant('direct')])]))
        self.assertEqual(len(model['assignments']), 3)
        self.assertIn('ambiguous_assignment_match', {d['code'] for d in model['diagnostics']})

    def test_raw_input_is_immutable(self):
        raw = sources(); before = copy.deepcopy(raw)
        build(raw)
        self.assertEqual(raw, before)


class PrivilegedPopulationTests(unittest.TestCase):
    def test_many_roles_count_one_identity(self):
        row = grant('second'); row['roleDefinitionId'] = 'role-two'
        model = build(sources([grant(), row]))
        self.assertEqual(model['counts']['assignments'], 2)
        self.assertEqual(model['counts']['active_user_identities'], 1)

    def test_eligible_and_both_populations(self):
        for rows, expected in (([], 'EligiblePrivilegedIdentity'), ([grant()], 'ActiveAndEligiblePrivilegedIdentity')):
            model = build(sources(rows, role_eligibility_schedules=[dataset([grant('elig')])]))
            self.assertIn(expected, model['identities'][0]['states'])

    def test_future_expired_do_not_count_current_users(self):
        for start, end, expected in ((FUTURE, None, 'FuturePrivilegedIdentity'), (OLD, RECENT, 'HistoricalExpiredPrivilege')):
            row = grant(); row['scheduleInfo'] = {'startDateTime': start, 'expiration': {'type': 'afterDateTime' if end else 'noExpiration', 'endDateTime': end}}
            model = build(sources([row]))
            self.assertEqual(model['counts']['active_user_identities'], 0)
            self.assertIn(expected, model['identities'][0]['states'])

    def test_disabled_current_active_and_eligible(self):
        for active in (True, False):
            data = sources([grant()] if active else [], users=[{'id': 'user-1', 'accountEnabled': False}],
                           role_eligibility_schedules=[dataset([] if active else [grant('eligible')])])
            identity = build(data)['identities'][0]
            self.assertIn('DisabledIdentityWithCurrentPrivilege', identity['states'])
            self.assertEqual(identity['activity']['state'], 'DisabledAccountRetainingPrivilege')

    def test_workload_and_unresolved_are_not_users(self):
        for principal_type, expected in (('#microsoft.graph.servicePrincipal', 'WorkloadOrServicePrincipalPrivilege'), (None, 'UnresolvedPrivilegedIdentity')):
            row = grant(); row['principal'] = {'@odata.type': principal_type}
            model = build(sources([row], users=[]))
            self.assertEqual(model['counts']['active_user_identities'], 0)
            self.assertIn(expected, model['identities'][0]['states'])

    def test_incomplete_role_collection_and_user_join_qualify_population(self):
        for name in ('role_assignments', 'role_assignment_schedules', 'users'):
            data = sources(); data[name][0]['source'].update(complete=False, availability_status='partial')
            self.assertFalse(build(data)['population_complete'])

    def test_cross_assessment_provider_and_environment_sources_do_not_join(self):
        for field in ('assessment_id', 'environment_id', 'provider'):
            data = sources(); data['role_assignment_schedules'][0]['source'][field] = 'other'
            self.assertEqual(build(data)['counts']['active_user_identities'], 0)


class GroupPrivilegeTests(unittest.TestCase):
    def data(self, members=True):
        group = grant('group-grant', principal='group-1'); group['principal']['@odata.type'] = '#microsoft.graph.group'
        group['memberType'] = 'Group'
        return sources([grant(), group], group_members=[dataset([{'id': 'user-1', '@odata.type': '#microsoft.graph.user'}] if members else [], group_id='group-1')])

    def test_direct_and_group_access_counts_once_preserves_lineage(self):
        model = build(self.data())
        self.assertEqual(model['counts']['active_user_identities'], 1)
        user = next(i for i in model['identities'] if i['principal_id'] == 'user-1')
        self.assertIn('GroupMediatedPrivilegedIdentity', user['states'])
        self.assertTrue(user['membership_refs'])

    def test_unavailable_membership_preserves_group_and_incomplete_population(self):
        data = self.data(); del data['group_members']
        model = build(data)
        self.assertFalse(model['population_complete'])
        self.assertIn('group-1', {i['principal_id'] for i in model['identities']})

    def test_removed_member_not_resurrected_from_old_capture(self):
        data = self.data(); data['role_assignment_schedules'][0]['records'] = data['role_assignment_schedules'][0]['records'][1:]
        old = copy.deepcopy(data['group_members'][0]); old['source']['collected_at'] = OLD
        data['group_members'] = [old, dataset([], group_id='group-1')]
        self.assertNotIn('user-1', {i['principal_id'] for i in build(data)['identities']})

    def test_partial_or_stale_membership_cannot_establish_complete_users(self):
        for change in ({'complete': False}, {'collected_at': OLD}):
            data = self.data(); data['group_members'][0]['source'].update(change)
            self.assertFalse(build(data)['population_complete'])


class PrivilegedActivityTests(unittest.TestCase):
    def test_recent_old_and_missing_success_activity(self):
        for stamp, expected in ((RECENT, 'ActivePrivilegedAccount'), (OLD, 'PotentiallyInactivePrivilegedAccount'), (None, 'PrivilegedActivityUnknown')):
            data = sources(users=[{'id': 'user-1', 'accountEnabled': True, 'signInActivity': {'lastSuccessfulSignInDateTime': stamp}}])
            self.assertEqual(build(data, inactivity_policy=policy())['identities'][0]['activity']['state'], expected)

    def test_attempt_fields_failed_events_and_missing_policy_do_not_imply_inactivity(self):
        data = sources(users=[{'id': 'user-1', 'accountEnabled': True, 'signInActivity': {'lastSignInDateTime': RECENT, 'lastNonInteractiveSignInDateTime': RECENT}}])
        self.assertEqual(build(data, inactivity_policy=policy())['identities'][0]['activity']['state'], 'PrivilegedActivityUnknown')
        self.assertEqual(build()['identities'][0]['activity']['state'], 'PrivilegedActivityUnknown')

    def test_permission_license_partial_and_future_activity_remain_unknown(self):
        for change in ({'complete': False}, {'availability_status': 'unavailable', 'reason': 'License or permission'}, {'collected_at': FUTURE}):
            data = sources(); data['users'][0]['source'].update(change)
            self.assertEqual(build(data, inactivity_policy=policy())['identities'][0]['activity']['state'], 'PrivilegedActivityUnknown')

    def test_explicit_threshold_changes_policy_identity(self):
        one, two = build(inactivity_policy=policy(30)), build(inactivity_policy=policy(365))
        self.assertNotEqual(one['policy_signature'], two['policy_signature'])
        self.assertNotEqual(one['identities'][0]['activity']['state'], two['identities'][0]['activity']['state'])

    def test_successful_noninteractive_event_prevents_inactivity(self):
        from Core.signin_evidence import normalize_signin_event
        raw = {'id': 'noninteractive', 'userId': 'user-1', 'status': {'errorCode': 0}, 'isInteractive': False, 'createdDateTime': RECENT}
        data = sources(signin_logs=[dataset([raw], window_start=OLD, window_end=NOW)])
        identity = build(data, inactivity_policy=policy())['identities'][0]
        self.assertEqual(identity['activity']['state'], 'ActivePrivilegedAccount')
        self.assertEqual(identity['authentication']['events'][0]['NormalizedSignInOutcome'], normalize_signin_event(raw)['NormalizedSignInOutcome'])


class PrivilegedAuthenticationTests(unittest.TestCase):
    def test_mfa_registration_states_and_conflicts(self):
        for flags, expected in (([True], 'ExplicitlyRegistered'), ([False], 'ExplicitlyNotRegistered'), ([None], 'Unknown'), ([True, False], 'Conflicting')):
            data = sources(auth_methods=[dataset([{'id': 'user-1', 'isMfaRegistered': flag} for flag in flags])])
            self.assertEqual(build(data)['identities'][0]['authentication']['registration_state'], expected)

    def test_event_outcomes_delegate_to_pass_a(self):
        from Core.signin_evidence import normalize_signin_event
        for error, ca, expected in ((0, 'notApplied', 'PrivilegedSuccessfulLegacyAuthentication'), (53003, 'failure', 'PrivilegedLegacyAttemptBlockedByCA')):
            raw = {'id': 'event', 'userId': 'user-1', 'createdDateTime': RECENT, 'clientAppUsed': 'IMAP', 'status': {'errorCode': error}, 'conditionalAccessStatus': ca}
            data = sources(signin_logs=[dataset([raw])])
            with patch('Core.signin_evidence.normalize_signin_event', wraps=normalize_signin_event) as classifier:
                identity = build(data)['identities'][0]
            self.assertTrue(classifier.called)
            self.assertIn(expected, identity['authentication']['observations'])

    def test_other_identity_and_assessment_events_do_not_correlate(self):
        raw = {'id': 'other', 'userId': 'another-user', 'status': {'errorCode': 0}, 'createdDateTime': RECENT}
        data = sources(signin_logs=[dataset([raw])])
        self.assertEqual(build(data)['identities'][0]['authentication']['events'], [])
        raw['userId'] = 'user-1'; data['signin_logs'][0]['source']['assessment_id'] = 'other'
        self.assertEqual(build(data)['identities'][0]['authentication']['events'], [])

    def test_ca_not_applied_requires_explicit_expected_policy_evidence(self):
        raw = {'id': 'event', 'userId': 'user-1', 'createdDateTime': RECENT, 'status': {'errorCode': 0},
               'authenticationRequirement': 'singleFactorAuthentication', 'conditionalAccessStatus': 'notApplied',
               'appliedConditionalAccessPolicies': []}
        data = sources(signin_logs=[dataset([raw])])
        self.assertNotIn('PrivilegedSuccessfulSignInOutsideExpectedCACoverage', build(data)['identities'][0]['authentication']['observations'])
        data['ca_policies'] = [dataset([{'id': 'expected'}])]
        model = build(data, expected_ca=[{'principal_id': 'user-1', 'policy_id': 'expected', 'evidence_refs': [{'dataset': 'ca_policies', 'dataset_index': 0, 'record_index': 0}]}])
        self.assertIn('PrivilegedSuccessfulSignInOutsideExpectedCACoverage', model['identities'][0]['authentication']['observations'])


class SpecialPurposeTests(unittest.TestCase):
    def test_name_patterns_are_candidates_only(self):
        for name, expected in (('break-glass', 'EmergencyAccessCandidate'), ('svc-automation', 'ServiceOrAutomationAccountCandidate')):
            data = sources(); data['users'][0]['records'][0]['displayName'] = name
            purpose = build(data, inactivity_policy=policy())['identities'][0]['purpose']
            self.assertEqual(purpose['classification'], expected)
            self.assertFalse(purpose['validated'])

    def test_explicit_purpose_validation_requires_support_and_owner(self):
        for kind in ('emergency', 'service', 'ordinary'):
            declaration = {'principal_id': 'user-1', 'purpose': kind, 'owner': 'fictional-owner',
                           'evidence_refs': [{'dataset': 'purpose', 'dataset_index': 0, 'record_index': 0}], 'source_type': 'customer_declaration'}
            data = sources(purpose=[dataset([{'id': 'fictional-purpose', 'purpose': kind}])])
            identity = build(data, account_purposes=[declaration], inactivity_policy=policy())['identities'][0]
            self.assertTrue(identity['purpose']['validated'])
            if kind == 'emergency': self.assertNotEqual(identity['activity']['state'], 'PotentiallyInactivePrivilegedAccount')
            del declaration['evidence_refs']
            self.assertFalse(build(account_purposes=[declaration])['identities'][0]['purpose']['validated'])

    def test_sync_guest_workload_disabled_separate_treatment(self):
        for extra, expected in (({'onPremisesSyncEnabled': True}, 'SynchronizationAccountCandidate'), ({'userType': 'Guest'}, 'GuestAdministrator'), ({'accountEnabled': False}, 'DisabledPrivilegedAccount')):
            data = sources(); data['users'][0]['records'][0].update(extra)
            self.assertEqual(build(data)['identities'][0]['purpose']['classification'], expected)

    def test_emergency_use_and_service_interactive_use_are_review_items(self):
        raw = {'id': 'event', 'userId': 'user-1', 'createdDateTime': RECENT, 'status': {'errorCode': 0}, 'isInteractive': True}
        for purpose, expected in (('emergency', 'EmergencyAccessRecentlyUsed'), ('service', 'InteractiveUseObservedForServiceAccount')):
            declaration = {'principal_id': 'user-1', 'purpose': purpose, 'owner': 'fictional-owner', 'source_type': 'configuration', 'evidence_refs': [{'dataset': 'purpose', 'dataset_index': 0, 'record_index': 0}]}
            identity = build(sources(signin_logs=[dataset([raw])], purpose=[dataset([{'id': 'fictional-purpose', 'purpose': purpose}])]), account_purposes=[declaration], inactivity_policy=policy())['identities'][0]
            self.assertIn(expected, identity['review_states'])


class CombinedRiskTests(unittest.TestCase):
    def test_combined_risk_retains_components_and_native_refs(self):
        data = sources(auth_methods=[dataset([{'id': 'user-1', 'isMfaRegistered': False}])])
        model = build(data)
        combined = next(o for o in model['observations'] if o['condition'] == 'ActivePrivilegeWithoutMFARegistration')
        self.assertEqual(combined['classification'], 'confirmed_observation')
        self.assertTrue(combined['component_ids']); self.assertTrue(combined['evidence_refs'])
        self.assertTrue(set(combined['component_ids']) <= {o['observation_id'] for o in model['observations']})
        self.assertNotIn('governance', combined)

    def test_missing_component_and_eligible_only_prevent_active_combination(self):
        for data in (sources(), sources([], role_eligibility_schedules=[dataset([grant('eligible')])], auth_methods=[dataset([{'id': 'user-1', 'isMfaRegistered': False}])])):
            self.assertNotIn('ActivePrivilegeWithoutMFARegistration', {o['condition'] for o in build(data)['observations']})

    def test_no_directional_delta_rule_or_automatic_closure(self):
        from Core.delta_metrics import RULES
        self.assertFalse(any(key.startswith('identity.privileged') for key in RULES))
        model = build(inactivity_policy=policy())
        self.assertFalse(any('ClosedByRemediation' in str(row) for row in model['observations']))


class PrivilegedValidationTests(unittest.TestCase):
    def test_invalid_active_future_and_combined_refs_are_diagnosed_without_repair(self):
        from Core.privileged_validation import validate_privileged_assessment
        model = build(); model['assignments'][0]['temporal_state'] = 'FuturePending'
        model['assignments'][0]['active'] = True
        model['observations'].append({'observation_id': 'broken', 'condition': 'combined', 'classification': 'confirmed_observation', 'component_ids': ['missing'], 'evidence_refs': []})
        before = copy.deepcopy(model)
        codes = {row['code'] for row in validate_privileged_assessment(model)}
        self.assertIn('noncurrent_assignment_active', codes)
        self.assertIn('missing_combined_component', codes)
        self.assertEqual(model, before)


if __name__ == '__main__':
    unittest.main()

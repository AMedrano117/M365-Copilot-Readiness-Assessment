"""Adversarial fictional boundaries for Pass B; no collector or customer input."""
import copy
import unittest
from test_privileged_identity import NOW, OLD, RECENT, FUTURE, BOUNDARY, grant, dataset, sources, policy, build


def event(identifier='event', **extra):
    return dict(id=identifier, userId='user-1', createdDateTime=RECENT, status={'errorCode': 0}, **extra)


def purpose(kind, source='customer_declaration', **extra):
    return dict(principal_id='user-1', purpose=kind, owner='fictional-owner', source_type=source,
                evidence_refs=[{'dataset': 'purpose', 'dataset_index': 0, 'record_index': 0}], **extra)


class AssignmentBoundaryTests(unittest.TestCase):
    def test_timezone_equivalence_end_exclusive_and_invalid_expiration(self):
        from Core.privileged_assignments import classify_assignment, instant
        self.assertEqual(instant(NOW), instant('2026-10-09T07:00:00-05:00'))
        for expiration, end, expected in [('afterDateTime', NOW, 'Expired'), ('notSpecified', FUTURE, 'UnknownTemporalState'),
                                          ('noExpiration', None, 'ActivePermanent')]:
            row = grant(); row['scheduleInfo']['expiration'] = {'type': expiration, 'endDateTime': end}
            self.assertEqual(classify_assignment(row, 'active', NOW), expected)
        self.assertIsNone(instant('2026-10-09T12:00:00'))

    def test_native_grant_scope_or_time_conflict_is_not_two_active_grants(self):
        for change in ('scope', 'time'):
            row = grant()
            if change == 'scope': row['appScopeId'] = '/other'
            else: row['scheduleInfo']['startDateTime'] = RECENT
            model = build(sources([grant(), row]))
            self.assertEqual(len(model['assignments']), 2)
            self.assertEqual(model['counts']['active_assignments'], 0)
            self.assertIn('conflicting_native_assignment', {d['code'] for d in model['diagnostics']})

    def test_schedule_instance_parent_reconciliation_preserves_native_ids(self):
        instance = {'id': 'instance-1', 'roleAssignmentScheduleId': 'grant-1', 'principalId': 'user-1',
            'principal': {'@odata.type': '#microsoft.graph.user'}, 'roleDefinitionId': 'role-1',
            'directoryScopeId': '/', 'memberType': 'Direct', 'startDateTime': OLD, 'endDateTime': None}
        model = build(sources(role_assignment_schedule_instances=[dataset([instance])]))
        self.assertEqual(model['counts']['assignments'], 1)
        self.assertEqual(model['assignments'][0]['instance_ids'], ['instance-1'])
        self.assertEqual(len(model['assignments'][0]['evidence_refs']), 2)

    def test_unknown_pending_schedule_does_not_suppress_direct_active_evidence(self):
        pending = grant(); pending['scheduleInfo']['startDateTime'] = FUTURE
        model = build(sources([pending], role_assignments=[dataset([grant('direct')])]))
        self.assertEqual(model['counts']['active_assignments'], 1)
        self.assertEqual(model['counts']['temporal_states']['FuturePending'], 1)
        self.assertEqual(model['counts']['temporal_states']['DirectActiveDurationUnverified'], 1)

    def test_conflicting_same_boundary_raw_variants_keep_stable_keys(self):
        row = grant(); row['status'] = 'PendingApproval'
        a = build(sources([grant(), row])); b = build(sources([row, grant()]))
        self.assertEqual({r['assignment_key'] for r in a['assignments']}, {r['assignment_key'] for r in b['assignments']})
        self.assertEqual(a['counts']['active_assignments'], 0)


class PopulationBoundaryTests(unittest.TestCase):
    def test_latest_empty_user_capture_does_not_resurrect_removed_user_metadata(self):
        data = sources()
        data['users'][0]['source']['collected_at'] = RECENT
        data['users'].append(dataset([]))
        model = build(data, inactivity_policy=policy())
        self.assertFalse(model['population_complete'])
        self.assertEqual(model['identities'][0]['account'], {})
        self.assertEqual(model['identities'][0]['activity']['state'], 'PrivilegedActivityUnknown')

    def test_conflicting_account_state_and_principal_type_do_not_get_selected(self):
        data = sources(users=[{'id': 'user-1', 'accountEnabled': True}, {'id': 'user-1', 'accountEnabled': False}])
        identity = build(data)['identities'][0]
        self.assertIn('accountEnabled', identity['account_conflicts'])
        self.assertNotIn('accountEnabled', identity['account'])
        self.assertFalse(build(data)['population_complete'])
        data['service_principals'] = [dataset([{'id': 'user-1'}])]
        self.assertEqual(build(data)['identities'][0]['principal_type'], 'unresolved')

    def test_complete_empty_and_unavailable_collections_remain_distinct(self):
        data = sources(assignments=[], users=[])
        self.assertTrue(build(data)['population_complete'])
        for status in ('failed', 'partial', 'not_requested', 'insufficient_permission', 'unlicensed', 'unavailable'):
            raw = copy.deepcopy(data); raw['role_assignment_schedules'][0]['source']['availability_status'] = status
            self.assertFalse(build(raw)['population_complete'])
            self.assertEqual(build(raw)['counts']['active_assignments'], 0)

    def test_old_capture_and_cross_tenant_cannot_establish_complete_population(self):
        data = sources(); data['role_assignment_schedules'][0]['source']['collected_at'] = OLD
        self.assertFalse(build(data)['population_complete'])
        data['role_assignment_schedules'][0]['source']['tenant_id'] = 'foreign-tenant'
        self.assertEqual(build(data)['counts']['assignments'], 0)

    def test_historical_only_grants_do_not_create_current_activity_findings(self):
        row = grant(); row['scheduleInfo']['startDateTime'] = FUTURE
        self.assertEqual(build(sources([row]), inactivity_policy=policy())['observations'], [])


class GroupBoundaryTests(unittest.TestCase):
    def test_group_principal_direct_schedule_matches_direct_assignment_without_losing_lineage(self):
        row = grant(principal='group-1'); row['principal']['@odata.type'] = '#microsoft.graph.group'
        row['memberType'] = 'Direct'
        direct = copy.deepcopy(row); direct['id'] = 'direct-group'; direct.pop('memberType')
        model = build(sources([row], role_assignments=[dataset([direct])], group_members=[dataset([], group_id='group-1')]))
        self.assertEqual(model['counts']['assignments'], 1)
        self.assertEqual(model['assignments'][0]['principal_type'], 'group')
        self.assertEqual(len(model['assignments'][0]['source_occurrences']), 2)

    def test_partial_group_retains_members_but_qualifies_population_and_lineage(self):
        row = grant(principal='group-1'); row['principal']['@odata.type'] = '#microsoft.graph.group'
        data = sources([row], group_members=[dataset([{'id': 'user-1', '@odata.type': '#microsoft.graph.user'}], group_id='group-1')])
        data['group_members'][0]['source'].update(complete=False, availability_status='partial')
        model = build(data)
        self.assertEqual(model['counts']['active_user_identities'], 1)
        self.assertFalse(model['population_complete'])
        component = next(o for o in model['observations'] if o['condition'] == 'CurrentActivePrivilege'
                         and o['identity_id'] == next(i['identity_id'] for i in model['identities'] if i['principal_id'] == 'user-1'))
        self.assertIn('group_members', {r['dataset'] for r in component['evidence_refs']})
        user = next(i for i in model['identities'] if i['principal_id'] == 'user-1')
        self.assertEqual(model['evidence_bindings'][next(n for n, b in enumerate(model['evidence_bindings']) if b['source_ref'] == user['membership_refs'][0])]['source']['collected_at'], NOW)

    def test_conflicting_latest_group_membership_is_not_complete(self):
        row = grant(principal='group-1'); row['principal']['@odata.type'] = '#microsoft.graph.group'
        data = sources([row], group_members=[dataset([{'id': 'user-1', '@odata.type': '#microsoft.graph.user'}], group_id='group-1'),
                                            dataset([], group_id='group-1')])
        self.assertFalse(build(data)['population_complete'])
        self.assertEqual(build(data)['counts']['active_user_identities'], 0)


class ActivityBoundaryTests(unittest.TestCase):
    def test_separate_signin_activity_source_joins_without_inventing_users_fields(self):
        data = sources(users=[{'id': 'user-1', 'accountEnabled': True}],
            user_signin_activity=[dataset([{'id': 'user-1', 'signInActivity': {'lastSuccessfulSignInDateTime': RECENT}}])])
        identity = build(data, inactivity_policy=policy())['identities'][0]
        self.assertEqual(identity['activity']['state'], 'ActivePrivilegedAccount')
        self.assertEqual(identity['activity']['evidence_refs'][0]['dataset'], 'user_signin_activity')
        self.assertNotIn('signInActivity', identity['account'])

    def test_attempt_only_and_pre_history_account_never_establish_inactivity(self):
        for activity in ({}, {'lastSignInDateTime': RECENT}, {'lastNonInteractiveSignInDateTime': RECENT},
                         {'lastSuccessfulSignInDateTime': None}):
            data = sources(users=[{'id': 'user-1', 'accountEnabled': True, 'createdDateTime': '2020-01-01T00:00:00Z', 'signInActivity': activity}],
                signin_logs=[dataset([dict(event(), status={'errorCode': 50126})])])
            self.assertEqual(build(data, inactivity_policy=policy())['identities'][0]['activity']['state'], 'PrivilegedActivityUnknown')

    def test_stale_partial_or_unavailable_activity_does_not_make_inactivity_candidate(self):
        for state in ({'collected_at': OLD}, {'complete': False}, {'availability_status': 'unlicensed'}, {'availability_status': 'insufficient_permission'}):
            data = sources(); data['users'][0]['source'].update(state)
            self.assertNotEqual(build(data, inactivity_policy=policy())['identities'][0]['activity']['state'], 'PotentiallyInactivePrivilegedAccount')

    def test_recent_noninteractive_success_prevents_ordinary_staleness_without_interactive_events(self):
        data = sources(signin_logs=[dataset([event(isInteractive=False)])])
        identity = build(data, inactivity_policy=policy())['identities'][0]
        self.assertEqual(identity['activity']['state'], 'ActivePrivilegedAccount')
        self.assertEqual(identity['activity']['last_qualified_success'], RECENT)

    def test_success_after_capture_or_before_account_creation_is_not_qualified(self):
        data = sources(users=[{'id': 'user-1', 'accountEnabled': True, 'createdDateTime': RECENT,
                              'signInActivity': {'lastSuccessfulSignInDateTime': OLD}}])
        self.assertEqual(build(data, inactivity_policy=policy())['identities'][0]['activity']['state'], 'PrivilegedActivityUnknown')
        data['users'][0]['records'][0]['createdDateTime'] = OLD
        data['users'][0]['records'][0]['signInActivity']['lastSuccessfulSignInDateTime'] = FUTURE
        self.assertEqual(build(data, inactivity_policy=policy())['identities'][0]['activity']['state'], 'PrivilegedActivityUnknown')

    def test_invalid_policies_exclusions_and_eligible_only_never_get_default_inactivity(self):
        from Core.privileged_identity import normalize_inactivity_policy
        for update in ({'threshold': 0}, {'threshold': True}, {'threshold': float('nan')}, {'threshold': float('inf')},
                       {'threshold_unit': 'months'}, {'activity_fields': None}, {'minimum_source_coverage': 'partial'},
                       {'emergency_treatment': 'ordinary'}, {'successful_activity_required': False}):
            raw = policy(); raw.update(update)
            self.assertFalse(normalize_inactivity_policy(raw, NOW)['valid'])
            self.assertNotEqual(build(inactivity_policy=raw)['identities'][0]['activity']['state'], 'PotentiallyInactivePrivilegedAccount')
        self.assertEqual(build(inactivity_policy=policy(exclusions=['user-1']))['identities'][0]['activity']['state'], 'PrivilegedActivityUnknown')
        data = sources([], role_eligibility_schedules=[dataset([grant('eligible')])])
        self.assertNotEqual(build(data, inactivity_policy=policy())['identities'][0]['activity']['state'], 'PotentiallyInactivePrivilegedAccount')


class AuthenticationBoundaryTests(unittest.TestCase):
    def test_workload_principal_does_not_acquire_human_registration_state(self):
        row = grant(); row['principal']['@odata.type'] = '#microsoft.graph.servicePrincipal'
        model = build(sources([row], users=[], auth_methods=[dataset([{'id': 'user-1', 'isMfaRegistered': True}])]))
        self.assertEqual(model['identities'][0]['authentication']['registration_state'], 'Unknown')
        self.assertEqual(model['identities'][0]['authentication']['registration_refs'], [])

    def test_three_conflicting_native_event_occurrences_never_select_success_by_order(self):
        success = event(clientAppUsed='IMAP'); failed = dict(success, status={'errorCode': 50126})
        for events in ([success, failed, success], [failed, success, failed]):
            identity = build(sources(users=[{'id': 'user-1', 'accountEnabled': True}], signin_logs=[dataset(events)]), inactivity_policy=policy())['identities'][0]
            self.assertFalse(any(e['correlation_qualified'] for e in identity['authentication']['events']))
            self.assertEqual(identity['activity']['state'], 'PrivilegedActivityUnknown')
            self.assertNotIn('PrivilegedSuccessfulLegacyAuthentication', identity['authentication']['observations'])

    def test_event_scoped_combination_has_exact_successful_event_evidence(self):
        data = sources(signin_logs=[dataset([event('success', clientAppUsed='IMAP'), dict(event('failed', clientAppUsed='IMAP'), status={'errorCode': 50126})])])
        model = build(data)
        observation = next(o for o in model['observations'] if o['condition'] == 'ActivePrivilege.PrivilegedSuccessfulLegacyAuthentication')
        self.assertEqual([r['record_index'] for r in observation['evidence_refs'] if r['dataset'] == 'signin_logs'], [0])

    def test_event_outside_capture_window_or_future_never_correlates_as_activity(self):
        for stamp, state in ((FUTURE, {}), (RECENT, {'window_end': OLD}), (RECENT, {'collected_at': OLD})):
            raw = dict(event(), createdDateTime=stamp)
            data = sources(users=[{'id': 'user-1', 'accountEnabled': True}], signin_logs=[dataset([raw], **state)])
            self.assertEqual(build(data, inactivity_policy=policy())['identities'][0]['activity']['state'], 'PrivilegedActivityUnknown')

    def test_single_factor_success_and_ca_combination_keep_independent_components(self):
        data = sources(signin_logs=[dataset([event(authenticationRequirement='singleFactorAuthentication', conditionalAccessStatus='notApplied',
                                                       appliedConditionalAccessPolicies=[])])], ca_policies=[dataset([{'id': 'expected'}])])
        expected = [{'principal_id': 'user-1', 'policy_id': 'expected', 'evidence_refs': [{'dataset': 'ca_policies', 'dataset_index': 0, 'record_index': 0}]}]
        model = build(data, expected_ca=expected)
        for condition in ('PrivilegedObservedSingleFactorSuccess', 'PrivilegedSuccessfulSignInOutsideExpectedCACoverage'):
            observation = next(o for o in model['observations'] if o['condition'] == 'ActivePrivilege.' + condition)
            self.assertEqual(len(observation['component_ids']), 2)
        data['ca_policies'][0]['source']['complete'] = False
        self.assertNotIn('PrivilegedSuccessfulSignInOutsideExpectedCACoverage', build(data, expected_ca=expected)['identities'][0]['authentication']['observations'])


class PurposeBoundaryTests(unittest.TestCase):
    def test_invalid_purpose_reference_is_an_explicit_gap_diagnostic(self):
        model = build(account_purposes=[dict(purpose('emergency'), evidence_refs=[{'dataset': 'missing', 'dataset_index': 0, 'record_index': 999}])])
        self.assertFalse(model['identities'][0]['purpose']['validated'])
        self.assertIn('privileged_input_reference_invalid', {d['code'] for d in model['diagnostics']})

    def test_old_service_and_sync_activity_requires_separate_dependency_review(self):
        for kind in ('service', 'synchronization'):
            data = sources(purpose=[dataset([{'id': 'explicit-purpose', 'purpose': kind}])])
            identity = build(data, account_purposes=[purpose(kind)], inactivity_policy=policy())['identities'][0]
            self.assertTrue(identity['purpose']['validated'])
            self.assertNotEqual(identity['activity']['state'], 'PotentiallyInactivePrivilegedAccount')
            data['users'][0]['records'][0]['signInActivity'] = {}
            self.assertEqual(build(data, account_purposes=[purpose(kind)], inactivity_policy=policy())['identities'][0]['activity']['state'], 'PrivilegedActivityUnknown')

    def test_unverified_governance_label_or_name_is_not_an_approval(self):
        data = sources(purpose=[dataset([{'DecisionType': 'ApprovedException', 'WorkflowState': 'Active'}])])
        declaration = purpose('emergency', source='governance', governance_verified=True)
        self.assertFalse(build(data, account_purposes=[declaration])['identities'][0]['purpose']['validated'])

    def test_explicit_governance_purpose_requires_audited_authority_and_exact_resource(self):
        from test_governance_decisions import fixture, advance, NOW as GOVERNANCE_NOW
        from Core.governance import new_log, create_draft
        from Core.privileged_identity import build_privileged_assessment
        result, record, authority = fixture('ApprovedException')
        record.update(ResourceIds=['user-1'], AccountableOwner='fictional-owner', Conditions=['Account purpose: emergency'])
        log = create_draft(new_log(result), result, record, actor='operator', at=GOVERNANCE_NOW)
        identifier = log['Events'][0]['DecisionId']
        log = advance(result, log, identifier, authority)
        boundary = dict(BOUNDARY, assessment_id=log['AssessmentId'], environment_id=log['PrimaryEnvironmentId'])
        data = sources(purpose=[dataset([{'DecisionLog': log}])])
        declaration = purpose('emergency', source='governance', decision_id=identifier)
        before = copy.deepcopy(log)
        model = build_privileged_assessment(data, boundary=boundary, evaluation_timestamp=NOW, inactivity_policy=policy(), account_purposes=[declaration])
        self.assertTrue(model['identities'][0]['purpose']['validated'])
        self.assertEqual(log, before)
        self.assertEqual(model['identities'][0]['activity']['state'], 'EmergencyAccessValidated')
        for field, value in (('principal_id', 'other-user'), ('owner', 'other-owner'), ('decision_id', 'GOV-missing')):
            bad = dict(declaration); bad[field] = value
            checked = build_privileged_assessment(data, boundary=boundary, evaluation_timestamp=NOW, account_purposes=[bad])
            self.assertFalse(checked['identities'][0]['purpose']['validated'])
        corrupt = copy.deepcopy(data); corrupt['purpose'][0]['records'][0]['DecisionLog']['Events'][-1]['Actor'] = 'unauthorized'
        self.assertFalse(build_privileged_assessment(corrupt, boundary=boundary, evaluation_timestamp=NOW, account_purposes=[declaration])['identities'][0]['purpose']['validated'])

    def test_emergency_use_change_and_permanent_privilege_remain_review_candidates(self):
        data = sources(purpose=[dataset([{'purpose': 'emergency'}])], signin_logs=[dataset([event()])],
                       privileged_account_changes=[dataset([{'principalId': 'user-1', 'activityDateTime': RECENT}])])
        model = build(data, account_purposes=[purpose('emergency')], inactivity_policy=policy())
        identity = model['identities'][0]
        self.assertTrue(identity['active_assignments'])
        self.assertIn('EmergencyAccessRecentlyUsed', identity['review_states'])
        self.assertIn('EmergencyAccessConfigurationNeedsReview', identity['review_states'])
        self.assertEqual(next(o for o in model['observations'] if o['condition'] == 'PermanentPrivilegeNeedsReview')['classification'], 'review_candidate')
        self.assertNotIn('ActivePrivilege.PotentialInactivity', {o['condition'] for o in model['observations']})

    def test_conflicting_explicit_purposes_do_not_support_ordinary_inactivity(self):
        data = sources(purpose=[dataset([{'purpose': 'emergency'}])])
        identity = build(data, account_purposes=[purpose('ordinary'), purpose('emergency')], inactivity_policy=policy())['identities'][0]
        self.assertFalse(identity['purpose']['validated'])
        self.assertTrue(identity['purpose']['conflicting'])
        self.assertNotEqual(identity['activity']['state'], 'PotentiallyInactivePrivilegedAccount')

    def test_eligible_no_activation_is_qualified_by_retained_period(self):
        data = sources([], role_eligibility_schedules=[dataset([grant('eligible')])],
                       role_assignment_schedule_instances=[dataset([], window_start=OLD, window_end=NOW)])
        identity = build(data)['identities'][0]
        self.assertIn('EligiblePrivilegeWithoutRecentActivationEvidence', identity['review_states'])
        self.assertEqual(identity['activation_coverage'][0]['window_end'], NOW)
        del data['role_assignment_schedule_instances'][0]['source']['window_start']
        self.assertNotIn('EligiblePrivilegeWithoutRecentActivationEvidence', build(data)['identities'][0]['review_states'])


class PublicationBoundaryTests(unittest.TestCase):
    def test_tampered_semantics_return_structured_errors_without_repair(self):
        from Core.privileged_validation import validate_privileged_assessment
        original = build(inactivity_policy=policy())
        def corrupt_temporal(m): m['assignments'][0].update(temporal_state='FuturePending', active=False)
        def corrupt_scope(m): m['assignments'][0]['identity_boundary'].pop('app_scope_id')
        def corrupt_type(m): m['identities'][0]['principal_type'] = 'serviceprincipal'
        def corrupt_activity(m): m['identities'][0]['activity']['last_qualified_success'] = None
        def corrupt_policy(m): m['inactivity_policy']['threshold'] = 999
        def corrupt_ref(m): m['assignments'][0]['evidence_refs'][0]['record_index'] = 999
        for mutate in (corrupt_temporal, corrupt_scope, corrupt_type, corrupt_activity, corrupt_policy, corrupt_ref):
            model = copy.deepcopy(original); mutate(model); before = copy.deepcopy(model)
            self.assertTrue(validate_privileged_assessment(model))
            self.assertEqual(model, before)

    def test_missing_authentication_component_event_evidence_and_population_claim_block_publication(self):
        from Core.privileged_validation import validate_privileged_assessment
        model = build(sources(signin_logs=[dataset([event(clientAppUsed='IMAP')])]))
        combination = next(o for o in model['observations'] if o['condition'].startswith('ActivePrivilege.'))
        combination['component_ids'] = []
        self.assertIn('missing_combined_component', {d['code'] for d in validate_privileged_assessment(model)})
        model = build(sources(signin_logs=[dataset([event(clientAppUsed='IMAP')])]))
        model['identities'][0]['authentication']['condition_refs']['PrivilegedSuccessfulLegacyAuthentication'] = []
        self.assertIn('privileged_authentication_event_missing', {d['code'] for d in validate_privileged_assessment(model)})
        model = build(); model['population_complete'] = True
        model['diagnostics'].append({'code': 'group_membership_incomplete', 'subject': 'group'})
        self.assertIn('incomplete_group_population_claimed_complete', {d['code'] for d in validate_privileged_assessment(model)})

    def test_malformed_models_are_diagnostics_not_silent_repairs(self):
        from Core.privileged_validation import validate_privileged_assessment
        for model in ([], 'invalid', {'assignments': [None]}, {'identities': ['invalid']}):
            self.assertTrue(validate_privileged_assessment(model))


if __name__ == '__main__':
    unittest.main()

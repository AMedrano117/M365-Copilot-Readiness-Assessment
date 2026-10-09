"""Condition titles are projections, never evidence, identity or governance inputs."""
import copy
import unittest

from Core.identity_finding_taxonomy import (
    VERSION, resolve_identity_finding, finding_title, project_identity_result,
    validate_identity_taxonomy,
)


def legacy(observation='', **values):
    row = dict(Service='Entra', Feature='Microsoft Entra ID P1', Observation=observation,
                Recommendation='Review the retained population and dated evidence.',
                RecommendationId='ENT-FICTIONAL')
    row.update(values)
    return row


class TaxonomyAuthorityTests(unittest.TestCase):
    def test_scoped_finding_type_precedes_broad_control(self):
        row = legacy(FindingType='mfa.not_registered', ControlId='IDENTITY.MFA')
        self.assertEqual(finding_title(row), 'User explicitly not registered for MFA')

    def test_explicit_condition_is_authoritative_over_license_and_wording(self):
        row = legacy('A license and risk summary', FindingCondition='mfa.registration', ControlId='IDENTITY.MFA')
        result = resolve_identity_finding(row)
        self.assertEqual(result['FindingTitle'], 'MFA registration coverage')
        self.assertEqual(result['FindingTaxonomyVersion'], VERSION)
        self.assertEqual(result['TaxonomyAuthority'], 'explicit_condition')

    def test_control_key_mapping_precedes_legacy_words(self):
        row = legacy('Review role context', FindingKey='entra.authentication.mfa_registration')
        self.assertEqual(finding_title(row), 'MFA registration coverage')

    def test_unknown_and_ambiguous_mapping_retains_label_with_warning(self):
        for text in ('', 'MFA registration and Conditional Access policy coverage need review'):
            row = resolve_identity_finding(legacy(text))
            self.assertEqual(row['FindingTitle'], 'Microsoft Entra ID P1')
            self.assertEqual(row['TaxonomyState'], 'unresolved')
            self.assertTrue(row['TaxonomyDiagnostics'])
            self.assertTrue(all(d['severity'] == 'compatibility_warning' for d in row['TaxonomyDiagnostics']))

    def test_recommendation_id_and_severity_do_not_select_condition(self):
        for identifier in ('ENT-005', 'ENT-011', 'another'):
            row = resolve_identity_finding(legacy('', RecommendationId=identifier, Priority='High'))
            self.assertEqual(row['TaxonomyState'], 'unresolved')

    def test_nonidentity_title_is_not_rewritten(self):
        row = {'Service': 'SharePoint', 'Feature': 'Site owners', 'Observation': 'Review ownership'}
        self.assertEqual(resolve_identity_finding(row), row)

    def test_resolution_is_pure_and_idempotent(self):
        row = legacy('Passwordless methods are registered', FindingCondition='methods.passwordless')
        original = copy.deepcopy(row)
        result = resolve_identity_finding(row)
        self.assertEqual(row, original)
        self.assertEqual(resolve_identity_finding(result), result)


class LicensingConditionTests(unittest.TestCase):
    def test_license_inventory_keeps_distinct_license_subjects(self):
        for name in ('Microsoft Entra ID P1', 'Microsoft Entra ID P2', 'Microsoft Entra MFA'):
            row = legacy(name + ' is active in a fictional SKU')
            row['Feature'] = name
            result = resolve_identity_finding(row)
            self.assertEqual(result['FindingTitle'], name + ' license availability')
            self.assertEqual(result['FindingType'], 'licensing_context')
            self.assertNotIn('EnforcementState', result)

    def test_technical_condition_keeps_original_license_context_without_entitlement_claim(self):
        row = resolve_identity_finding(legacy('MFA registration coverage is incomplete', FindingCondition='mfa.registration'))
        self.assertEqual(row['OriginalCompatibilityLabel'], 'Microsoft Entra ID P1')
        self.assertEqual(row['AssociatedLicenseFeatures'], ['Microsoft Entra ID P1'])
        self.assertEqual(row['LicenseEvidenceState'], 'unknown')
        self.assertNotIn('LicenseObserved', row)
        self.assertNotEqual(row['Capability'], row['OriginalCompatibilityLabel'])

    def test_only_retained_service_plan_inventory_establishes_observed_license(self):
        bundle = {'sheets': {'service_plan_inventory': {'rows': [
            {'Service Plan Name': 'AAD_PREMIUM', 'Service Plan': 'Microsoft Entra ID P1', 'Provisioning Status': 'Success'}]}}}
        row = resolve_identity_finding(legacy(FindingCondition='conditional_access.policy_coverage'), bundle)
        self.assertEqual(row['LicenseEvidenceState'], 'present')
        self.assertTrue(row['LicenseObserved'])
        self.assertIn('LicenseSource', row)
        self.assertNotIn('EnforcementState', row)

    def test_conflicting_plan_states_remain_conflicting_not_absent(self):
        bundle = {'sheets': {'service_plan_inventory': {'rows': [
            {'Service Plan Name': 'AAD_PREMIUM', 'Provisioning Status': status}
            for status in ('Success', 'Unknown')]}}}
        row = resolve_identity_finding(legacy(FindingCondition='conditional_access.policy_coverage'), bundle)
        self.assertEqual(row['LicenseEvidenceState'], 'unknown')


class MFATaxonomyTests(unittest.TestCase):
    def test_registration_states_are_separate(self):
        cases = {'mfa.registration': 'MFA registration coverage',
                 'mfa.not_registered': 'User explicitly not registered for MFA',
                 'mfa.registration_unknown': 'MFA registration state unknown',
                 'mfa.registration_conflicting': 'MFA registration state conflicting'}
        for condition, title in cases.items():
            with self.subTest(condition=condition):
                result = resolve_identity_finding(legacy(FindingCondition=condition))
                self.assertEqual(result['FindingTitle'], title)
                self.assertEqual(result['ConditionEvidenceLevel'], 'registration_state')
                self.assertNotIn('EnforcementState', result)

    def test_method_and_sspr_conditions_do_not_become_observed_authentication(self):
        conditions = ['methods.passwordless', 'methods.phishing_resistant', 'methods.phone',
                      'methods.phone_only', 'methods.system_preferred',
                      'sspr.registration', 'sspr.enablement', 'sspr.capability']
        titles = set()
        for condition in conditions:
            row = resolve_identity_finding(legacy(FindingCondition=condition))
            titles.add(row['FindingTitle'])
            self.assertNotIn('ObservedOperationState', row)
            self.assertNotIn('EnforcementState', row)
        self.assertEqual(len(titles), len(conditions))


class ConditionalAccessTaxonomyTests(unittest.TestCase):
    def test_policy_enforcement_and_event_conditions_remain_distinct(self):
        conditions = ['conditional_access.policy_coverage', 'conditional_access.report_only',
                      'conditional_access.exclusions', 'conditional_access.observed_outside_coverage',
                      'mfa.enforcement_not_established', 'mfa.enforcement_report_only',
                      'mfa.admin_enforcement_not_established', 'legacy.blocking_coverage']
        titles = {finding_title(legacy(FindingCondition=c)) for c in conditions}
        self.assertEqual(len(titles), len(conditions))
        self.assertTrue(all('Microsoft Entra ID' not in title for title in titles))

    def test_policy_inventory_does_not_supply_operational_conclusion(self):
        row = resolve_identity_finding(legacy('Two Conditional Access policies found',
                                             FindingCondition='conditional_access.policy_coverage'))
        self.assertNotIn('ObservedOperationState', row)
        self.assertNotIn('EnforcementState', row)

    def test_legacy_outcome_conditions_stay_distinct(self):
        conditions = ['legacy.success', 'legacy.ca_blocked', 'legacy.failed_other', 'legacy.unknown']
        titles = [finding_title(legacy(FindingCondition=c)) for c in conditions]
        self.assertEqual(len(set(titles)), 4)


class PrivilegedTaxonomyTests(unittest.TestCase):
    def test_pass_b_condition_keys_keep_distinct_evidenced_names(self):
        cases = {'ActivePrivilegeWithoutMFARegistration': 'Privileged identities explicitly not registered for MFA',
                 'DisabledIdentityWithCurrentPrivilege': 'Disabled privileged identities retaining access',
                 'PrivilegedActivityUnknown': 'Privileged activity not established',
                 'EmergencyAccessCandidateUnverified': 'Emergency-access purpose requiring validation',
                 'ServicePurposeUnverified': 'Service-account purpose requiring validation',
                 'PermanentPrivilegeNeedsReview': 'Permanent privileged access requiring business-purpose review',
                 'EligiblePrivilegeWithoutRecentActivationEvidence': 'Eligible privilege requiring review'}
        for condition, expected in cases.items():
            with self.subTest(condition=condition):
                row = legacy(FindingKey='entra.privileged.' + condition, ControlId='IDENTITY.ADMIN')
                row['Feature'] = 'Microsoft Entra ID P2'
                self.assertEqual(finding_title(row), expected)

    def test_assignment_and_identity_counts_do_not_choose_title(self):
        for value in (0, 1, 99):
            self.assertEqual(finding_title(legacy(FindingCondition='privileged.scope_purpose',
                                                  MeasuredValue=value)), 'Privileged access scope and purpose review')


class RiskGuestConsentTaxonomyTests(unittest.TestCase):
    def test_control_conditions_remain_separate(self):
        conditions = ['risk.users', 'risk.detections', 'risk.disposition', 'access_review.coverage',
                      'access_review.definitions', 'guest.lifecycle', 'guest.stale', 'guest.administrator',
                      'consent.policy', 'consent.high_privilege', 'consent.unverified_publisher', 'consent.owner_unknown']
        titles = set()
        for condition in conditions:
            row = resolve_identity_finding(legacy(FindingCondition=condition))
            titles.add(row['FindingTitle'])
            self.assertEqual(row['TaxonomyState'], 'resolved')
        self.assertEqual(len(titles), len(conditions))


class LegacyTaxonomyTests(unittest.TestCase):
    def test_unresolved_historical_label_survives_confirmation_action_wording(self):
        row = legacy('Prior MFA evidence.', Feature='Confirm multifactor authentication coverage',
                     OriginalFeature='Historical MFA finding', Historical='Yes')
        resolved = resolve_identity_finding(row)
        self.assertEqual(resolved['FindingTitle'], 'Historical MFA finding')
        self.assertEqual(resolved['TaxonomyState'], 'unresolved')
        self.assertTrue(resolved['TaxonomyDiagnostics'])

    def test_workbook_condition_signatures_resolve_without_row_numbers(self):
        cases = [('2 Conditional Access policies found; verified controls include 1 requiring MFA', 'Conditional Access policy coverage'),
                 ('34 of 132 users with known registration flags are MFA registered.', 'MFA registration coverage'),
                 ('5% of users have a passwordless method registered', 'Passwordless authentication registration'),
                 ('3 current permanent or duration-unverified active assignments require scope and purpose review.', 'Privileged access scope and purpose review'),
                 ('No active access reviews were returned for user access governance', 'Access review coverage'),
                 ('No access review definitions were returned', 'Access review definitions'),
                 ('2 risky users detected in the tenant', 'Risky users requiring investigation'),
                 ('Guest invitation eligibility is configured', 'Guest invitation policy coverage'),
                 ('User consent enabled for applications', 'User consent policy coverage')]
        for observation, title in cases:
            with self.subTest(observation=observation):
                self.assertEqual(finding_title(legacy(observation)), title)

    def test_no_mapping_from_first_record_or_sheet_or_display_id(self):
        row = legacy('', EvidenceSheet='MFA Registration Review',
                     InvestigationEvidence={'records': [{'isMfaRegistered': False}]})
        self.assertEqual(resolve_identity_finding(row)['TaxonomyState'], 'unresolved')

    def test_legacy_diagnostics_survive_projection_without_mutating_snapshot(self):
        result = {'recommendations': [legacy('')], 'identity': {'Aliases': []}, 'lifecycle': {'Records': []}}
        original = copy.deepcopy(result)
        projected = project_identity_result(result)
        self.assertEqual(result, original)
        self.assertTrue(projected['identity_taxonomy']['diagnostics'])
        self.assertEqual(project_identity_result(projected), projected)


class TaxonomyValidationTests(unittest.TestCase):
    def test_malformed_snapshot_records_are_rejected_before_projection(self):
        for value in ({}, ['not a row'], 'not a list'):
            with self.subTest(value=value):
                result = {'recommendations': value}
                self.assertEqual(validate_identity_taxonomy(result)[0]['code'], 'identity_taxonomy_records_invalid')
                with self.assertRaises(ValueError):
                    project_identity_result(result)

    def test_validation_checks_action_projection_and_required_licensing(self):
        row = resolve_identity_finding(legacy(FindingCondition='conditional_access.policy_coverage'))
        row['LicensingDependencies'] = []
        errors = validate_identity_taxonomy({'actions': [row]})
        self.assertTrue(any(e['code'] == 'identity_taxonomy_licensing_dependency_missing' for e in errors))
        row = resolve_identity_finding(legacy(FindingCondition='mfa.registration'))
        row['TaxonomyState'] = 'unresolved'
        self.assertTrue(any(e['severity'] == 'error' for e in validate_identity_taxonomy({'actions': [row]})))

    def test_invalid_structured_title_is_diagnosed_not_silently_repaired(self):
        row = resolve_identity_finding(legacy(FindingCondition='mfa.registration'))
        row['FindingTitle'] = 'Microsoft Entra ID P1'
        result = {'recommendations': [row]}
        errors = validate_identity_taxonomy(result)
        self.assertTrue(any(e['severity'] == 'error' for e in errors))
        self.assertEqual(project_identity_result(result)['recommendations'][0]['FindingTitle'], 'Microsoft Entra ID P1')

    def test_unknown_taxonomy_version_remains_qualified(self):
        row = resolve_identity_finding(legacy(FindingCondition='mfa.registration'))
        row['FindingTaxonomyVersion'] = 'unsupported'
        self.assertTrue(validate_identity_taxonomy({'recommendations': [row]}))


class TaxonomyLifecycleTests(unittest.TestCase):
    def test_projection_preserves_all_semantic_and_governance_inputs(self):
        row = legacy(FindingCondition='mfa.registration', FindingKey='registration', ControlId='IDENTITY.MFA',
                     PersistentFindingId='PFI-fiction', EvidenceScope='fictional scope', Population='users',
                     EvidenceLevel='configuration', Status='Action Required', Disposition='Action',
                     CustomerActionStatus='Open', LifecycleState='Continuing', GovernanceState='AcceptedRisk',
                     CompatibilityRecommendationIds=['ENT-old'])
        baseline = {'recommendations': [row], 'actions': [copy.deepcopy(row)],
                    'identity': {'Aliases': [{'TargetId': 'PFI-fiction'}]},
                    'lifecycle': {'Records': [{'State': 'Continuing'}]}, 'governance': {'decisions': []}}
        original = copy.deepcopy(baseline)
        current = project_identity_result(baseline)
        self.assertEqual(baseline, original)
        self.assertEqual(current['identity'], baseline['identity'])
        self.assertEqual(current['lifecycle'], baseline['lifecycle'])
        self.assertEqual(current['governance'], baseline['governance'])
        for field, value in row.items():
            self.assertEqual(current['recommendations'][0][field], value, field)

    def test_reordering_and_repeated_projection_preserve_titles(self):
        rows = [legacy(FindingCondition='mfa.registration'), legacy(FindingCondition='risk.users')]
        rows[1]['RecommendationId'] = 'ENT-risk'
        original = project_identity_result({'recommendations': rows})
        reordered = project_identity_result({'recommendations': list(reversed(rows))})
        self.assertEqual({r['RecommendationId']: r['FindingTitle'] for r in original['recommendations']},
                         {r['RecommendationId']: r['FindingTitle'] for r in reordered['recommendations']})

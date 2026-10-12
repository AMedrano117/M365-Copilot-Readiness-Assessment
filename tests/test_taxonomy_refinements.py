import copy
import unittest
from Core.identity_finding_taxonomy import resolve_identity_finding, validate_identity_taxonomy, project_identity_sheet_links
from Core.authentication_metric_taxonomy import resolve_metric
from tests.test_identity_taxonomy import legacy


class AssignmentTaxonomyTests(unittest.TestCase):
    def test_group_assignment_beats_generic_license_signal(self):
        for identifier in ('ENT-008','unrelated-id'):
            row=resolve_identity_finding(legacy('No groups use license assignment',EvidenceBasis='License signal',RecommendationId=identifier))
            self.assertEqual(row['FindingTitle'],'Group-based license assignment coverage')
    def test_copilot_assignment_is_specific_but_not_a_required_control(self):
        row=resolve_identity_finding(legacy('No groups are configured for Copilot licensing',EvidenceBasis='License signal',Disposition='Opportunity'))
        self.assertEqual(row['FindingTitle'],'Copilot group-based license assignment coverage')
        self.assertEqual(row['Disposition'],'Opportunity')
    def test_guest_license_is_not_guest_access_or_entitlement(self):
        row=resolve_identity_finding(legacy('2 of 4 guest users have an M365 license; this does not prove Copilot entitlement or inappropriate access',Feature='Microsoft Entra ID P2',EvidenceBasis='License signal'))
        self.assertEqual(row['FindingTitle'],'Guest license assignment review')
        self.assertNotIn('EnforcementState',row)
        self.assertNotEqual(row['EntitlementVerificationState'],'entitlement_confirmed')
    def test_guest_lifecycle_stays_distinct(self):
        row=resolve_identity_finding(legacy('Guest users with stale sign-in activity',FindingCondition='guest.lifecycle'))
        self.assertEqual(row['FindingCondition'],'guest.lifecycle')
    def test_all_technical_signatures_beat_license_basis(self):
        for text in ('MFA registration coverage is incomplete','SSPR registration coverage is incomplete','2 Conditional Access policies found','2 risky users detected','User consent enabled for applications'):
            with self.subTest(text=text):
                self.assertNotIn('license availability',resolve_identity_finding(legacy(text,EvidenceBasis='License signal'))['FindingTitle'])
    def test_explicit_license_with_technical_subject_is_blocked(self):
        row=resolve_identity_finding(legacy('MFA registration coverage is incomplete',FindingCondition='license.Microsoft Entra ID P1'))
        self.assertTrue(any(d['severity']=='error' for d in validate_identity_taxonomy({'recommendations':[row]})))


class MetricTaxonomyTests(unittest.TestCase):
    def test_every_produced_summary_metric_has_a_stable_known_condition(self):
        from Core.authentication_methods import summarize_registrations
        report=summarize_registrations([{'id':'fiction','isMfaRegistered':True,'methodsRegistered':['fido2']}],{'availability_status':'available'})
        for row in report['summary_rows']:
            with self.subTest(metric=row['Metric']):
                self.assertIn('Metric ID',row)
                self.assertNotEqual(resolve_metric(row)['Metric Condition'],'Authentication metric classification review')
    def test_stable_metric_id_overrides_display(self):
        row=resolve_metric({'Metric ID':'mfa_capable','Metric':'MFA registered users'})
        self.assertEqual(row['Metric Condition'],'MFA capability coverage')
        self.assertEqual(row['Metric Evidence Level'],'capability_state')
    def test_legacy_display_fallback_and_unknown_are_conservative(self):
        self.assertEqual(resolve_metric({'Metric':'SSPR enabled'})['Metric Condition'],'SSPR enablement coverage')
        self.assertEqual(resolve_metric({'Metric ID':'unrecognized','Metric':'SSPR enabled'})['Metric Condition'],'Authentication metric classification review')
    def test_coverage_flagged_by_is_metric_parent_is_separate(self):
        sheets={'authentication_detail':{'rows':[{'Metric':'MFA capable users','Value':3}]}}
        parents=[legacy(FindingCondition='mfa.registration',EvidenceKey='authentication_detail')]
        project_identity_sheet_links(sheets,parents)
        detail=sheets['authentication_detail']['rows'][0]
        self.assertEqual(detail['Flagged By'],'MFA capability coverage')
        self.assertEqual(detail['Parent Finding Context'],'MFA registration coverage')
        self.assertEqual(detail['Value'],3)


class LicensingNormalizationTests(unittest.TestCase):
    def test_html_uses_normalized_partial_state_and_retained_plan(self):
        from Core.identity_finding_taxonomy import licensing_context
        bundle={'source_statuses':{'license_details':dict(availability_status='partial',complete=False)},
                'sheets':{'service_plan_inventory':{'rows':[{'Service Plan Name':'AAD_PREMIUM','Provisioning Status':'Success'}]}}}
        row=resolve_identity_finding(legacy(FindingCondition='conditional_access.policy_coverage'),bundle)
        html=licensing_context(row)
        self.assertIn('License inventory evidence: Incomplete',html)
        self.assertIn('Observed service plans: AAD_PREMIUM',html)
        self.assertIn('entitlement requires separate confirmation',html)
    def test_technical_columns_preserve_legacy_and_normalized_state_separately(self):
        row=resolve_identity_finding(legacy(FindingCondition='mfa.registration'))
        sheets={'mfa_registration_detail':{'rows':[{'RecommendationId':row['RecommendationId']}]}}
        project_identity_sheet_links(sheets,[row])
        detail=sheets['mfa_registration_detail']['rows'][0]
        self.assertEqual(detail['License Evidence State'],'unknown')
        self.assertEqual(detail['License Inventory Evidence State'],'NotCollected')
    def test_disabled_plan_is_retained_inventory_not_entitlement(self):
        bundle={'sheets':{'service_plan_inventory':{'rows':[{'Service Plan Name':'AAD_PREMIUM','Provisioning Status':'Disabled'}]}}}
        row=resolve_identity_finding(legacy(FindingCondition='conditional_access.policy_coverage'),bundle)
        self.assertEqual(row['AssociatedServicePlans'],['AAD_PREMIUM'])
        self.assertEqual(row['LicenseInventoryEvidenceState'],'Observed')
        self.assertEqual(row['LicenseEvidenceState'],'unknown')
        self.assertNotIn('LicenseObserved',row)
        self.assertNotEqual(row['EntitlementVerificationState'],'entitlement_confirmed')
    def test_complete_empty_inventory_is_explicit_absence(self):
        bundle={'source_statuses':{'license_details':dict(availability_status='empty',complete=True)}}
        row=resolve_identity_finding(legacy(FindingCondition='conditional_access.policy_coverage'),bundle)
        self.assertEqual(row['LicenseInventoryEvidenceState'],'NotObserved')
    def test_complete_absence_requires_retained_envelope(self):
        bundle={'source_statuses':{'license_details':dict(availability_status='available',complete=True)},'sheets':{'service_plan_inventory':{'rows':[]}}}
        row=resolve_identity_finding(legacy(FindingCondition='conditional_access.policy_coverage'),bundle)
        self.assertEqual(row['LicenseInventoryEvidenceState'],'NotObserved')
    def test_valid_previous_projection_upgrades_without_identity_changes(self):
        row=resolve_identity_finding(legacy('No groups use license assignment',PersistentFindingId='PFI-fiction'))
        row.update(FindingTaxonomyVersion='1.0.0',FindingCondition='license.Microsoft Entra ID P1',FindingTitle='Microsoft Entra ID P1 license availability',
                   Capability='Service-plan inventory',ConditionEvidenceLevel='license_inventory',FindingType='licensing_context')
        before=copy.deepcopy(row); after=resolve_identity_finding(row)
        self.assertEqual(after['FindingTitle'],'Group-based license assignment coverage')
        self.assertEqual(after['PersistentFindingId'],row['PersistentFindingId'])
        self.assertEqual(row,before)
    def test_missing_inventory_is_not_absence(self):
        row=resolve_identity_finding(legacy(FindingCondition='conditional_access.policy_coverage'))
        self.assertEqual(row['LicenseInventoryEvidenceState'],'NotCollected')
        self.assertEqual(row['AssociatedServicePlans'],[])
        self.assertIn('Microsoft Entra ID P1 (Conditional Access route)',row['LicensingDependencies'])
    def test_partial_and_failed_inventory_never_absent(self):
        for state in ('partial','failed','unavailable'):
            bundle={'source_statuses':{'license_details':dict(availability_status=state,complete=False)},'sheets':{'service_plan_inventory':{'rows':[]}}}
            row=resolve_identity_finding(legacy(FindingCondition='mfa.registration'),bundle)
            self.assertNotEqual(row['LicenseInventoryEvidenceState'],'NotObserved')
    def test_observed_plan_is_actual_name_not_feature(self):
        bundle={'sheets':{'service_plan_inventory':{'rows':[{'Service Plan Name':'AAD_PREMIUM','Provisioning Status':'Success'}]}}}
        row=resolve_identity_finding(legacy(FindingCondition='conditional_access.policy_coverage'),bundle)
        self.assertEqual(row['AssociatedServicePlans'],['AAD_PREMIUM'])
        self.assertEqual(row['LicenseInventoryEvidenceState'],'Observed')
        self.assertEqual(row['EntitlementVerificationState'],'license_plan_observed')
    def test_unknown_plan_status_is_conflicting(self):
        bundle={'sheets':{'service_plan_inventory':{'rows':[{'Service Plan Name':'AAD_PREMIUM','Provisioning Status':v} for v in ('Success','Unknown')]}}}
        row=resolve_identity_finding(legacy(FindingCondition='mfa.registration'),bundle)
        self.assertEqual(row['LicenseInventoryEvidenceState'],'Conflicting')
    def test_projection_preserves_identity_lifecycle_and_governance(self):
        row=legacy('No groups use license assignment',PersistentFindingId='PFI-fiction',ObservationId='OBS-fiction',FindingKey='stable-key',Disposition='Opportunity',TreatmentState='Open',EvidenceIds=['EVD-fiction'])
        before=copy.deepcopy(row); after=resolve_identity_finding(row)
        self.assertEqual(row,before)
        for key in ('PersistentFindingId','ObservationId','FindingKey','Disposition','TreatmentState','EvidenceIds'): self.assertEqual(after[key],row[key])

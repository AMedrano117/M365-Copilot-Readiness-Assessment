"""Reject fabricated favorable transitions instead of silently repairing them."""
import copy
import unittest
from test_delta_lifecycle import pair,compare,finding_record


class LifecycleValidationTests(unittest.TestCase):
    def errors(self,current):
        from Core.lifecycle_validation import validate_lifecycle
        return {row['code'] for row in validate_lifecycle(current) if row['severity']=='error'}

    def test_valid_engine_output_and_conservative_states(self):
        for value in (0,2,4,6):
            with self.subTest(value=value):
                b,c=pair(); c['evidence'][0]['value']=value;c['lifecycle']=compare(b,c)
                self.assertEqual(self.errors(c),set())

    def test_no_baseline_initial_and_standalone_are_blocked(self):
        for field,value in [('RunType','Initial'),('RunType','Standalone'),('BaselineRunId',None)]:
            b,c=pair();c['lifecycle']=compare(b,c);c['run_context'][field]=value
            self.assertTrue(self.errors(c))

    def test_governance_states_are_never_created(self):
        for state in ['AcceptedRisk','ApprovedException','ClosedByRemediation','NoLongerApplicable']:
            with self.subTest(state=state):
                b,c=pair(); c['lifecycle']=compare(b,c);finding_record(c['lifecycle'])['State']=state
                self.assertIn('lifecycle_governance_state',self.errors(c))

    def test_required_identity_reason_reference_and_rule_matrix(self):
        for field,value in [('BaselineEntityId',None),('AssessmentId','AST-foreign'),('PrimaryEnvironmentId','ENV-foreign'),
                ('BaselineAssessmentId','AST-foreign'),('BaselineEnvironmentId','ENV-foreign'),('Reasons',[]),
                ('CurrentEvidenceReferences',[]),('MetricChanges',[]),('EntityBoundary',{}),('BaselineEntityBoundary',{})]:
            with self.subTest(field=field):
                b,c=pair();c['evidence'][0]['value']=2;c['lifecycle']=compare(b,c)
                finding_record(c['lifecycle'])[field]=value
                self.assertTrue(self.errors(c))

    def test_direction_without_registered_rule_or_eligible_support_is_blocked(self):
        b,c=pair();c['evidence'][0]['value']=2;c['lifecycle']=compare(b,c)
        row=finding_record(c['lifecycle']);row['MetricChanges'][0]['Rule']=None
        self.assertIn('lifecycle_metric_calculation',self.errors(c))
        row['Eligibility']='not eligible'
        self.assertIn('lifecycle_direction_ineligible',self.errors(c))

    def test_resolution_from_disappearance_scope_partial_conflict_or_missing_is_blocked(self):
        for field,value in [('CurrentCoverageSufficient',False),('ResolutionEvidenceReferences',[]),('MetricChanges',[])]:
            with self.subTest(field=field):
                b,c=pair();c['evidence'][0]['value']=0;c['lifecycle']=compare(b,c)
                finding_record(c['lifecycle'])[field]=value
                self.assertTrue(self.errors(c))
        for field,value in [('scope','pilot'),('population','admins'),('complete',False),('selection','conflict'),('value',None)]:
            with self.subTest(field=field):
                b,c=pair();c['evidence'][0]['value']=0;c['lifecycle']=compare(b,c);c['evidence'][0][field]=value
                self.assertIn('lifecycle_current_evidence_mismatch',self.errors(c))

    def test_new_and_reopened_prerequisites(self):
        b,c=pair();c['lifecycle']=compare(b,c);row=finding_record(c['lifecycle']);row['State']='New'
        self.assertIn('lifecycle_new_without_absence_proof',self.errors(c))
        row['State']='Reopened'
        self.assertIn('lifecycle_reopened_without_resolution',self.errors(c))

    def test_counts_must_reconcile_and_records_must_be_unique(self):
        b,c=pair();c['lifecycle']=compare(b,c);c['lifecycle']['Summary']['finding']['Unchanged']=100
        self.assertIn('lifecycle_summary_mismatch',self.errors(c))
        c['lifecycle']['Records'].append(copy.deepcopy(finding_record(c['lifecycle'])))
        self.assertIn('lifecycle_duplicate_entity',self.errors(c))

    def test_legacy_readability_is_warning_without_invented_state(self):
        from Core.lifecycle_validation import validate_lifecycle
        b,c=pair(); before=copy.deepcopy(c)
        self.assertTrue(any(r['severity']=='compatibility_warning' for r in validate_lifecycle(c)))
        self.assertEqual(c,before)

    def test_malformed_records_produce_structured_errors(self):
        from Core.lifecycle_validation import validate_lifecycle
        b,c=pair()
        for invalid in (None,[],{'Records':'bad'}, {'SchemaVersion':'1.0.0','Records':[None]}):
            with self.subTest(invalid=invalid):
                c['lifecycle']=invalid
                self.assertTrue(all(isinstance(r,dict) for r in validate_lifecycle(c)))
                self.assertTrue(self.errors(c))

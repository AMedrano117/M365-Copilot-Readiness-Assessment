"""Explicit configured grants; no authority is inferred from assessment operation."""
from .governance_contract import VERSION, fail, safe_input


def authorize(policy,record,operation,actor):
    if not isinstance(policy,dict):fail('governance_unauthorized','Approval requires an explicit configured authority policy.')
    safe_input(policy)
    if policy.get('SchemaVersion')!=VERSION or not policy.get('PolicyId') or not policy.get('Version'):
        fail('governance_policy','Authority policy requires schema, persistent policy name and version.')
    for field in ('AssessmentId','PrimaryEnvironmentId'):
        if policy.get(field)!=record.get(field):fail('governance_policy','Authority policy has foreign ownership.')
    role=record.get('AuthorityRole');roles=policy.get('Roles');actors=policy.get('Actors')
    if not isinstance(roles,dict) or not isinstance(actors,dict):fail('governance_policy','Policy requires explicit Roles and Actors registries.')
    assigned=actors.get(actor,[]);grant=roles.get(role,{})
    if not isinstance(grant,dict) or any(not isinstance(grant.get(field),list) or not all(isinstance(v,str) for v in grant[field])
            for field in ('DecisionTypes','Operations')):
        fail('governance_policy','Role grants require explicit decision-type and operation lists.')
    if (not isinstance(assigned,list) or role not in assigned or not isinstance(grant,dict)
            or record.get('DecisionType') not in grant.get('DecisionTypes',[])
            or operation not in grant.get('Operations',[])):
        fail('governance_unauthorized','Actor lacks the configured role, decision type or operation grant.')
    if operation=='approve' and record.get('DecisionAuthority')!=actor:
        fail('governance_unauthorized','Approval actor differs from the explicitly named decision authority.')
    if operation in {'approve','activate','supersede'}:
        for evidence in record.get('EvidenceReferences',[]):
            if str(evidence.get('SourceType','')).lower()=='screenshot':
                if grant.get('AllowScreenshots') is not True or not evidence.get('MethodologyRule'):
                    fail('governance_screenshot','Screenshot approval requires an explicit methodology rule and policy permission.')
        if record.get('ClosureOverride') and record['DecisionType']=='ClosedByRemediation' and grant.get('AllowClosureOverride') is not True:
            fail('governance_override_authority','Policy does not authorize conditional remediation closure.')
        if record['DecisionType'] in {'AcceptedRisk','ApprovedException'} and not record.get('CompensatingControls'):
            if grant.get('AllowNoCompensatingControls') is not True or not record.get('CompensatingControlsRationale'):
                fail('governance_compensation','Omitting compensating controls requires explicit policy permission and rationale.')
    return grant

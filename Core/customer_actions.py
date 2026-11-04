"""Customer action plan: lanes, consolidation and finding classification.

One semantic condition + one scope + one population + one required decision
is one customer action. Several retained findings can support one action; each
finding stays in the technical register with its own stable ID. Lanes are a
projection of existing dispositions and readiness treatments (see
control_correlation.LANES); nothing here changes a control result, blocker or
finding identity.
"""
from collections import Counter
from hashlib import sha256
import json

from .control_correlation import LANES, LEGACY_KEY, correlate_device_protection

LANE_ORDER = ('FixNow', 'ValidateExistingControl', 'InvestigateAffectedObjects', 'CollectMissingEvidence')
EVIDENCE_SUFFICIENCY = {'FixNow': 'Remediate now', 'ValidateExistingControl': 'Validate an existing control',
                        'InvestigateAffectedObjects': 'Investigate affected objects', 'CollectMissingEvidence': 'Request missing evidence'}
FINDING_CLASSES = ('Customer action', 'Supporting observation', 'Assurance', 'Coverage gap', 'Technical investigation detail',
                   'Planning opportunity', 'Adoption context', 'Reference inventory', 'Deprecated or superseded producer')
# A producer whose condition is reported by a more specific finding with the same meaning.
SUPERSEDED_BY = {'purview.sensitivity_labels.deployed': 'purview.raw.sensitivity_labels'}
ACTION_STATUSES = {'Action Required', 'Attention Required', 'Critical', 'Warning'}
# Workstreams: related findings that one owner closes with one decision.
WORKSTREAM_TITLES = {
    'identity.legacy_blocking': 'Block legacy authentication for all users',
    'information_protection.labels': 'Define and publish sensitivity labels for business content',
    'content.broad_access': 'Review anonymous, organization-wide and external access to business content',
    'content.exposure_evidence': 'Refresh and validate the SharePoint exposure evidence',
    'endpoint.evidence_quality': 'Validate device compliance evidence and compliance design',
    ('endpoint.protection_failures', 'FixNow'): 'Remediate confirmed endpoint protection failures',
    ('endpoint.protection_failures', 'InvestigateAffectedObjects'): 'Confirm and remediate failed device compliance settings',
}
WORKSTREAM_DECISIONS = {
    'information_protection.labels': 'Agree the classification model and the users who receive each label.',
    'content.broad_access': 'Decide, per site, which anonymous, organization-wide and external access remains justified.',
    'content.exposure_evidence': None,
}
TITLES = {
    'entra.authentication.mfa_registration': 'Register the remaining users for MFA',
    'entra.app_consent.high_impact_grants': 'Review high-privilege and unverified application grants',
    'entra.apps.user_consent_assignment': 'Confirm the user-consent boundary for applications',
    'sharepoint.sharing.organization_default': 'Decide the default sharing-link type for new links',
    'baseline.connectors.inventory': 'Confirm Copilot connector ownership and permission mapping',
    'coverage.content.ownership': 'Establish site ownership and recurring access review',
    'coverage.data.audit': 'Confirm audit retention, workload coverage and response ownership',
    'coverage.license.assignment': 'Reconcile Copilot license assignment populations',
    'coverage.license.apps': 'Collect Microsoft 365 Apps version and update-channel evidence',
    'entra.devices.compliant_access': 'Validate Conditional Access use of compliant-device signals',
    'entra.identity_risk.users': 'Collect the current risky-user report',
    'baseline.identity.scope': 'Validate Conditional Access MFA coverage and exclusions',
    'purview.raw.dlp_mode': 'Review the DLP simulation results and approve enforcement',
    'purview.raw.sensitivity_labels': 'Define sensitivity labels for business content',
    'purview.raw.label_policies': 'Publish sensitivity labels to the intended users',
    'data_exposure.coverage.sensitive-data_exposure_evidence': 'Collect sensitive-content exposure evidence',
    'entra.privileged.activeprivilegewithoutmfaregistration': 'Register MFA for privileged accounts or remove their privilege',
    'entra.privileged.privilegedactivityunknown': 'Validate use and ownership of privileged accounts',
    'expanded.device_compliance_settings.devicecompliancecheckinthresholddays': 'Confirm when inactive devices become noncompliant',
    'expanded.device_compliance_settings.securebydefault': 'Confirm treatment of devices without a compliance policy',
    'expanded.compliance_setting_states.errors': 'Investigate device compliance reporting errors',
    'expanded.compliance_setting_states.noncompliant': 'Remediate failed device compliance settings',
    'expanded.windows_protection.overdue.current': 'Update overdue endpoint protection signatures',
}
LEGACY_TITLES = {
    'ControlScopeUnresolved': 'Validate legacy-authentication exclusions and their successful sign-ins',
    'ControlOperationUnresolved': 'Correlate successful legacy sign-ins with the existing blocking policy',
    'ControlBypassObserved': 'Correct the legacy-authentication policy conditions',
    'ControlConflict': 'Resolve conflicting legacy sign-in and policy results',
}
LANE_DESCRIPTIONS = {
    'FixNow': 'Confirmed conditions that need remediation.',
    'ValidateExistingControl': 'A relevant control exists, but its effective scope or operation is unresolved.',
    'InvestigateAffectedObjects': 'Affected objects are retained; their ownership, purpose or current need requires review.',
    'CollectMissingEvidence': 'A required question cannot be answered from the retained evidence.',
}


def _key(row):
    return str(row.get('FindingKey') or '')


def _legacy_only_failure(row):
    semantic = str(row.get('SemanticCondition') or '')
    return ('legacy=NoEnforcedPolicy' in semantic and row.get('ReadinessTreatment') == 'Remediate'
            and any(f'mfa={state}' in semantic for state in ('EnforcedForAllUsers', 'EnforcedWithValidatedEmergencyExclusions', 'SecurityDefaults')))


def action_lane(row):
    """Lane of one finding from its correlation, disposition and treatment."""
    disposition = row.get('Disposition')
    if disposition == 'Opportunity':
        return 'PlanImprovement'
    if disposition not in {'Action', 'Coverage'}:
        return None
    correlation = row.get('ControlCorrelation') or {}
    if correlation.get('lane'):
        return correlation['lane']
    treatment = row.get('ReadinessTreatment')
    kind = row.get('ActionType')
    if kind == 'Remediation':
        return 'FixNow'
    if kind == 'Evidence':
        return 'ValidateExistingControl' if treatment in {'ScopeReviewRequired', 'ConfirmBeforePilot'} else 'CollectMissingEvidence'
    if treatment == 'ConfirmBeforePilot' or _key(row) == 'sharepoint.sharing.organization_default':
        return 'ValidateExistingControl'
    return 'InvestigateAffectedObjects'


def _workstream(row):
    key = _key(row)
    state = (row.get('ControlCorrelation') or {}).get('state')
    if key == LEGACY_KEY and state in {'ControlAbsent', 'ControlIneffective'} or key == 'baseline.identity.sign_in' and _legacy_only_failure(row):
        return 'identity.legacy_blocking'
    if key in {'purview.raw.sensitivity_labels', 'purview.raw.label_policies'}:
        return 'information_protection.labels'
    if key in {'data_exposure.anonymous_links', 'data_exposure.broad_internal_access', 'data_exposure.external_exposure'}:
        return 'content.broad_access'
    if key == 'sharepoint.dag.not_assessed' or key.startswith('data_exposure.freshness'):
        return 'content.exposure_evidence'
    if key.startswith('expanded.device_compliance_settings.') or key == 'expanded.compliance_setting_states.errors':
        return 'endpoint.evidence_quality'
    if key == 'expanded.compliance_setting_states.noncompliant' or key.startswith('expanded.windows_protection.overdue'):
        return 'endpoint.protection_failures'
    return None


def member_title(row):
    key = _key(row)
    if key == LEGACY_KEY:
        state = (row.get('ControlCorrelation') or {}).get('state')
        if state in LEGACY_TITLES:
            return LEGACY_TITLES[state]
    if key.lower() in TITLES:
        return TITLES[key.lower()]
    # Otherwise the shared finding-title authority used by every output.
    from .customer_report import _heading
    return _heading(row)


def _identifier(parts):
    return 'CA-' + sha256(json.dumps(parts, sort_keys=True).encode('utf-8')).hexdigest()[:10].upper()


def _unique(values):
    return list(dict.fromkeys(str(value) for value in values if value))


def build_customer_actions(actions, blockers, overlaps=()):
    """Consolidate open technical actions into customer actions; every action belongs to exactly one."""
    blocking = {row.get('RecommendationId') for row in blockers}
    groups, order = {}, []
    for index, row in enumerate(actions):
        # Saved or hand-built action rows may predate dispositions; their action type decides the lane.
        lane = (row.get('ActionLane') or action_lane(row) or
                {'Remediation': 'FixNow', 'Evidence': 'CollectMissingEvidence'}.get(row.get('ActionType'), 'InvestigateAffectedObjects'))
        workstream = _workstream(row)
        group = (workstream, lane) if workstream else (_key(row) or row.get('RecommendationId'), lane, 'single')
        if group not in groups:
            groups[group] = []
            order.append(group)
        groups[group].append((index, row))
    priority = {'Critical': 0, 'High': 1, 'Medium': 2, 'Low': 3}
    result = []
    for group in order:
        members = [row for _, row in groups[group]]
        lane = group[1]
        workstream = group[0] if len(group) == 2 else None
        correlated = [row['ControlCorrelation'] for row in members if (row.get('ControlCorrelation') or {}).get('customer_narrative')]
        single = correlated[0] if len(members) == 1 and correlated else None
        title = (WORKSTREAM_TITLES.get((workstream, lane)) or WORKSTREAM_TITLES.get(workstream)) if workstream else member_title(members[0])
        narrative = single['customer_narrative'] if single else None
        if workstream == 'endpoint.evidence_quality':
            for overlap in overlaps:
                if 'expanded.compliance_setting_states.errors' in overlap['finding_keys'] and overlap['overlapping_devices']:
                    narrative = (f"{overlap['overlapping_devices']} of the devices with reporting errors also have confirmed failed settings "
                                 '(matched by stable device identity); reporting errors alone are not confirmed failures.')
        steps = (single.get('next_steps') if single else None) or _unique(row.get('Recommendation') for row in members)
        decision = (single.get('required_decision') if single else WORKSTREAM_DECISIONS.get(workstream))
        result.append({
            'customer_action_id': _identifier([workstream or sorted({_key(row) or row.get('RecommendationId') for row in members}), lane]),
            'title': title, 'lane': lane, 'lane_label': LANES[lane][0], 'readiness_treatment': LANES[lane][1],
            'evidence_sufficiency': EVIDENCE_SUFFICIENCY.get(lane), 'workstream': workstream,
            'priority': min((row.get('Priority') for row in members), key=lambda value: priority.get(value, 4)),
            'pilot_impact': 'Blocks readiness' if any(row.get('RecommendationId') in blocking for row in members)
                            else next((row.get('PilotImpact') for row in members if row.get('PilotImpact')), ''),
            'blocks_readiness': any(row.get('RecommendationId') in blocking for row in members),
            'domain_id': members[0].get('DomainId'), 'domain': members[0].get('Domain'),
            'owner_role': '; '.join(_unique(row.get('OwnerRole') for row in members)),
            'readiness_stage': members[0].get('ReadinessStage'),
            'narrative': narrative, 'next_steps': list(steps or []), 'required_decision': decision,
            'closure_evidence': ' '.join(_unique((single or {}).get('closure_evidence') and [single['closure_evidence']]
                                                 or [row.get('CompletionEvidence') for row in members])),
            'control_narrative': single.get('narrative_fields') if single else None,
            'members': [{'recommendation_id': row.get('RecommendationId'), 'finding_key': _key(row), 'title': member_title(row),
                         'action_type': row.get('ActionType'), 'lane': lane, 'priority': row.get('Priority'),
                         'summary': row.get('Observation'), 'next_step': row.get('Recommendation'),
                         'closure_evidence': row.get('CompletionEvidence'), 'pilot_impact': row.get('PilotImpact')} for row in members],
            '_first': groups[group][0][0],
        })
    result.sort(key=lambda item: (LANE_ORDER.index(item['lane']) if item['lane'] in LANE_ORDER else len(LANE_ORDER),
                                  not item['blocks_readiness'], priority.get(item['priority'], 4), item['_first']))
    for number, item in enumerate(result, 1):
        item['number'] = number
        item.pop('_first')
    return result


def retire_superseded(records):
    """A producer whose condition is reported by a more specific finding becomes reference context."""
    present = {_key(row) for row in records if row.get('Disposition') in {'Action', 'Coverage'}}
    for row in records:
        target = SUPERSEDED_BY.get(_key(row))
        if target and target in present and row.get('Disposition') in {'Opportunity', 'Action', 'Coverage'}:
            row.update(OriginalDisposition=row.get('Disposition'), Disposition='Reference', SupersededBy=target,
                       FindingClass='Deprecated or superseded producer')


def _license_only(row):
    return (row.get('EvidenceBasis') in {'License signal', 'Workload inventory context'} or row.get('LicenseContext')
            or 'service_plan_inventory' in str(row.get('EvidenceKey') or ''))


def finding_class(row, open_ids):
    if row.get('FindingClass') == 'Deprecated or superseded producer' or _key(row).startswith('legacy_service.') or row.get('SupersededBy'):
        return 'Deprecated or superseded producer'
    disposition = row.get('Disposition')
    if disposition == 'Action' and row.get('RecommendationId') in open_ids:
        return 'Customer action'
    if disposition == 'Coverage':
        return 'Coverage gap'
    if disposition == 'Assurance':
        return 'Assurance'
    if disposition == 'Opportunity':
        if row.get('DeviceEvidenceVersion') or _key(row).startswith(('entra.privileged.', 'entra.pim.', 'expanded.')):
            return 'Technical investigation detail'
        return 'Planning opportunity'
    if row.get('EvidenceBasis') == 'Workload inventory context' or row.get('ControlId') == 'ADOPTION-001' or row.get('DomainId') == 'adoption':
        return 'Adoption context'
    if _license_only(row):
        return 'Reference inventory'
    return 'Supporting observation'


def classify_findings(records, actions):
    """Classify every retained finding; license-only rows never keep an action status."""
    open_ids = {row.get('RecommendationId') for row in actions}
    for row in records:
        row['FindingClass'] = finding_class(row, open_ids)
        if row['FindingClass'] in {'Reference inventory', 'Adoption context'} and row.get('Status') in ACTION_STATUSES:
            row.update(OriginalStatus=row['Status'], Status='Insight')
    return dict(Counter(row['FindingClass'] for row in records))


def qualify_device_protection(records):
    for row in records:
        if row.get('DeviceFindingType') == 'DefenderRealTimeProtectionObservation' and not row.get('ControlCorrelation'):
            row['ControlCorrelation'] = correlate_device_protection(row)


def population_overlaps(records):
    """Device overlap between findings, only where stable device identity was resolved."""
    sets = {}
    for row in records:
        if not row.get('DeviceEvidenceVersion') or row.get('Disposition') not in {'Action', 'Coverage', 'Opportunity'}:
            continue
        devices = {item.get('intune_id') or item.get('entra_id') for item in row.get('DeviceEvidenceInterpretations') or []
                   if not item.get('identity_unresolved') and (item.get('intune_id') or item.get('entra_id'))}
        if devices:
            sets[_key(row)] = devices
    overlaps = []
    keys = sorted(sets)
    for index, first in enumerate(keys):
        for second in keys[index + 1:]:
            shared = sets[first] & sets[second]
            if shared:
                overlaps.append({'finding_keys': [first, second], 'overlapping_devices': len(shared),
                                 'devices': {first: len(sets[first]), second: len(sets[second])},
                                 'identity_basis': 'Resolved Intune or Entra device IDs; unresolved records are excluded.'})
    return overlaps


def readiness_outcome(controls, blockers, customer_actions, planning):
    """Customer-ready counts and sentences; derived from the shared decision, never recalculated."""
    gates = [row for row in controls if row.get('security_gate')]
    supported = sum(row['status'] == 'Observed' for row in gates)
    confirmed = [row for row in gates if row['status'] == 'Action required']
    unresolved = sum(row['status'] == 'Not established' for row in gates)
    blocker_ids = {row.get('RecommendationId') for row in blockers}
    blocking_controls = [row for row in confirmed if blocker_ids & set(row.get('recommendation_ids') or [])]
    lanes = Counter(item['lane'] for item in customer_actions)
    verb = lambda count, one, many: one if count == 1 else many
    sentences = [f"Of {len(gates)} required controls, {supported} {verb(supported, 'is', 'are')} supported by the evidence, "
                 f"{len(confirmed)} {verb(len(confirmed), 'has', 'have')} confirmed issues and {unresolved} "
                 f"{verb(unresolved, 'is', 'are')} not yet established."]
    sentences.append(f"{len(blockers)} confirmed {verb(len(blockers), 'condition blocks', 'conditions block')} the next stage."
                     if blockers else 'No confirmed condition blocks the next stage.')
    remaining = len(confirmed) - len(blocking_controls)
    if remaining > 0:
        sentences.append('A confirmed issue blocks the next stage only when its material impact on readiness is established. '
                         f"The other {remaining} {verb(remaining, 'control', 'controls')} with confirmed issues "
                         f"{verb(remaining, 'is', 'are')} tenant remediation work.")
    parts = [f"{lanes[lane]} to {label}" for lane, label in (('FixNow', 'fix now'), ('ValidateExistingControl', 'validate existing controls'),
             ('InvestigateAffectedObjects', 'investigate affected objects'), ('CollectMissingEvidence', 'collect missing evidence')) if lanes[lane]]
    if customer_actions:
        sentences.append(f"The action plan has {len(customer_actions)} customer {verb(len(customer_actions), 'action', 'actions')}: "
                         + (', '.join(parts[:-1]) + ' and ' + parts[-1] if len(parts) > 1 else parts[0]) + '.')
    if planning:
        sentences.append(f"{len(planning)} planning {verb(len(planning), 'item is', 'items are')} listed separately and "
                         f"{verb(len(planning), 'is', 'are')} not counted as open actions.")
    return {'required_controls': len(gates), 'supported': supported, 'confirmed_issues': len(confirmed),
            'unresolved': unresolved, 'blocking_conditions': len(blockers), 'controls_with_blockers': len(blocking_controls),
            'customer_actions': len(customer_actions), 'lanes': {lane: lanes[lane] for lane in LANE_ORDER},
            'planning': len(planning), 'sentences': sentences}

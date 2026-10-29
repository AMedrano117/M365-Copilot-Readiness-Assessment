"""Tenant technical readiness; deployment planning does not establish control results."""

SCOPE = 'tenant_readiness'
OUT_OF_SCOPE_CHECKS = frozenset({'SCOPE.PILOT', 'EXTERNAL_AI.OUTCOMES'})
QUALIFICATION = ('This assessment evaluates the tenant technical baseline. Pilot membership, business use cases, '
                 'success measures and rollout authorization are handled outside this assessment.')


def prepare_tenant_scope(result):
    """Attach scope and presentation fields during assessment construction."""
    impact_labels = {'Blocks pilot': 'Blocks readiness', 'Confirm before pilot': 'Evidence review',
                     'Fix before broad rollout': 'Tenant remediation'}
    for row in result.get('actions', []):
        row['PilotImpact'] = impact_labels.get(row.get('PilotImpact'), row.get('PilotImpact', ''))
        row['ReadinessImpact'] = row['PilotImpact']
        row['ReadinessStage'] = 'Tenant remediation' if row.get('ActionType') == 'Remediation' else 'Evidence review'
    by_id = {row['RecommendationId']: row for row in result.get('actions', [])}
    for row in result.get('recommendations', []):
        action = by_id.get(row.get('RecommendationId'))
        if action:
            row.update({key: action[key] for key in ('PilotImpact', 'ReadinessImpact', 'ReadinessStage')})
    result['assessment_scope_qualification'] = QUALIFICATION
    result['assessment_scope_version'] = '1.0'
    result['readiness_conditions'] = [row for row in result.get('actions', [])
                                      if row.get('SecurityGate') and row not in result.get('blockers', [])]
    result['pilot_conditions'] = []


def build_tenant_progress(result):
    """Project technical requirements without changing the recorded decision."""
    from .readiness_progress import _requirement, _counts
    controls = [row for row in result.get('controls', []) if row.get('security_gate')]
    actions = [row for row in result.get('actions', []) if row.get('SecurityGate')]
    requirements = []
    for control in controls:
        linked = [row for row in actions if row.get('ControlId') == control['control_id'] or
                  row.get('RecommendationId') in control.get('recommendation_ids', [])]
        state = ('issue' if control['status'] == 'Action required' else
                 'open' if control['status'] == 'Not established' else 'met')
        if linked and state == 'met':
            state = 'issue' if any(row.get('ActionType') == 'Remediation' for row in linked) else 'open'
        reason = {'issue': 'Resolve the retained tenant finding and verify current remediation evidence.',
                  'open': 'Obtain dated, scoped evidence for this tenant control; missing evidence is not a pass.',
                  'met': 'Current evidence supports this tenant control within the assessed scope.'}[state]
        requirements.append(_requirement('tenant.' + control['control_id'], control['title'], state, reason,
            control_id=control['control_id'], action_ids=[row['RecommendationId'] for row in linked]))
    covered = {key for row in requirements for key in row['action_ids']}
    for row in actions:
        if row['RecommendationId'] not in covered:
            requirements.append(_requirement('tenant.action.' + row['RecommendationId'],
                row.get('ActionTitle') or row.get('Feature'),
                'issue' if row.get('ActionType') == 'Remediation' else 'open',
                'Review the tenant finding and its evidence qualifications.', action_ids=[row['RecommendationId']]))
    answered = sum(row['status'] != 'Not established' for row in controls)
    has_evidence = bool(answered or any(row.get('availability') == 'available' and row.get('value') is not None
                                      for row in result.get('evidence', [])))
    current = 'assessed' if result['decision'] == 'Tenant baseline ready' else 'reviewing' if has_evidence else 'started'
    definitions = [('started', 'Evidence collection'), ('reviewing', 'Tenant readiness review'), ('assessed', 'Tenant baseline assessed')]
    stages = [{'id': key, 'label': label,
               'status': 'complete' if index < [key for key, _ in definitions].index(current) else
                         'current' if key == current else 'pending',
               'requirements': requirements if key == 'reviewing' else [],
               'counts': _counts(requirements if key == 'reviewing' else [])}
              for index, (key, label) in enumerate(definitions)]
    return {'scope': SCOPE, 'current_stage_id': current, 'current_stage': dict(definitions)[current],
            'label': dict(definitions)[current], 'stages': stages,
            'counts': {'required_controls': len(controls), 'established_controls': sum(row['status'] == 'Observed' for row in controls),
                       'unconfirmed_controls': len(controls) - answered,
                       'observed_issues': sum(row['status'] == 'Action required' for row in controls),
                       'open_actions': len(result.get('actions', [])), 'readiness_blockers': len(result.get('blockers', []))},
            'next_requirements': [row for row in requirements if row['status'] != 'met'],
            'pilot_conditions': [], 'qualification': QUALIFICATION}


def scope_handoff_html(result):
    return ('<section id="rollout"><h2>After the tenant assessment</h2><p>' + QUALIFICATION
            + '</p><p>Use the technical findings and their evidence qualifications to inform the separate deployment engagement.</p></section>')


def getting_started_html():
    return ('<section id="getting-started"><h2>Next technical steps</h2><ol>'
            '<li>Review the tenant findings with the accountable technical owners.</li>'
            '<li>Resolve confirmed security failures and obtain missing control evidence.</li>'
            '<li>Verify changes against fresh evidence and retain the assessment package.</li>'
            '</ol><p>' + QUALIFICATION + '</p></section>')

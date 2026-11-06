"""Customer Content & Sharing scorecard: a projection of the shared assessment result.

It reads control results, findings and their provenance; it never changes a
conclusion and adds no score, percentage, grade or weighting. Counts are
counts of controls by their existing status.
"""
from collections import Counter

CONTROLS = ('CONTENT.SHARING', 'CONTENT.PERMISSIONS', 'CONTENT.OWNERSHIP', 'DATA.EXPOSURE')
EXPOSURE_KEYS = frozenset({'data_exposure.anonymous_links', 'data_exposure.broad_internal_access', 'data_exposure.external_exposure',
                           'data_exposure.potentially_overshared', 'data_exposure.unlabeled_sensitive'})
SECTIONS = (
    ('working', "What's working", 'Controls that current, scoped evidence shows meet the methodology rule.'),
    ('needs_improvement', 'Needs improvement', 'Configuration or controls that the evidence shows do not meet the methodology baseline.'),
    ('exposure_to_review', 'Exposure to review', 'Existing broad, internal or external access that owners should validate; '
                                                 'it is not automatically inappropriate.'),
    ('not_established', 'Not established', 'Questions the available evidence cannot answer yet.'),
)
SETTING_LABELS = {
    'SharingCapability': 'SharePoint external sharing', 'OneDriveSharingCapability': 'OneDrive external sharing',
    'DefaultSharingLinkType': 'Default sharing link', 'RequireAnonymousLinksExpireInDays': 'Anyone link expiry',
    'FileAnonymousLinkType': 'Anyone link permission (files)', 'FolderAnonymousLinkType': 'Anyone link permission (folders)',
    'ExternalUserExpirationRequired': 'Guest access expiry', 'PreventExternalUsersFromResharing': 'Prevent guests from resharing',
}


def _in_scope(row):
    return (row.get('DomainId') == 'content' or row.get('ControlId') in CONTROLS
            or str(row.get('FindingKey') or '').startswith(('data_exposure.', 'sharepoint')))


def _display(value):
    if isinstance(value, dict):
        return '; '.join(f'{key}: {_display(item)}' for key, item in value.items() if item not in (None, '', [], {}))
    if isinstance(value, (list, tuple)):
        return '; '.join(_display(item) for item in value)
    return '' if value is None else str(value)


def _provenance(control, rows, facts):
    """Observed value, rule, scope, source, date and result of an established control."""
    row = next((item for item in rows if item.get('Disposition') == 'Assurance'), rows[0] if rows else {})
    fact = facts[0] if facts else {}
    fields = {
        'observed': row.get('ObservedValues') or row.get('Observation') or fact.get('value'),
        'expected': row.get('ExpectedRule') or fact.get('label') or control.get('title'),
        'scope': row.get('EvidenceScope') or fact.get('scope'),
        'source': row.get('EvidenceSource') or row.get('SourceFile') or fact.get('source_file') or fact.get('source_type'),
        'observed_at': row.get('ObservationDate') or fact.get('observed_at'),
        'freshness': row.get('Freshness') or fact.get('freshness'),
        'methodology_rule': row.get('MethodologyRule') or (f"{control['control_id']}: {control.get('title')}" if control.get('title') else ''),
        'result': control.get('status'),
    }
    return {**{key: _display(value) for key, value in fields.items()}, 'evidence_ids': list(control.get('evidence_ids') or [])}


def build_content_scorecard(result):
    result = result or {}
    controls = [item for item in result.get('controls') or [] if item.get('control_id') in CONTROLS]
    records = [row for row in result.get('recommendations') or [] if isinstance(row, dict)]
    by_evidence = {}
    for row in records:
        if row.get('EvidenceId'):
            by_evidence.setdefault(row['EvidenceId'], []).append(row)
    facts = {row.get('evidence_id'): row for row in result.get('evidence') or [] if isinstance(row, dict)}
    by_id = {row.get('RecommendationId'): row for row in records}
    actions = [row for row in result.get('actions') or [] if _in_scope(row)]
    card = {key: [] for key, _, _ in SECTIONS}

    for control in controls:
        identifier = control['control_id']
        if control.get('status') == 'Observed':
            supporting = [row for evidence in control.get('evidence_ids') or [] for row in by_evidence.get(evidence, [])]
            known = [facts[evidence] for evidence in control.get('evidence_ids') or [] if evidence in facts]
            card['working'].append({'control_id': identifier, 'title': control.get('title'),
                                    'statement': next((row.get('Observation') for row in supporting if row.get('Observation')), control.get('title')),
                                    'provenance': _provenance(control, supporting, known)})
        elif control.get('status') == 'Not established':
            questions = [by_id[rid].get('ActionTitle') or by_id[rid].get('Feature') for rid in control.get('recommendation_ids') or []
                         if rid in by_id and by_id[rid].get('Disposition') in {'Action', 'Coverage'}
                         and by_id[rid].get('FindingKey') not in EXPOSURE_KEYS]
            card['not_established'].append({'control_id': identifier, 'title': control.get('title'),
                                            'questions': list(dict.fromkeys(item for item in questions if item))})

    for row in actions:
        key = row.get('FindingKey') or ''
        if row.get('ActionType') == 'Remediation':
            card['needs_improvement'].append({'finding_key': key, 'title': row.get('ActionTitle') or row.get('Feature'),
                                              'statement': row.get('Observation'), 'control_id': row.get('ControlId') or ''})
        elif key in EXPOSURE_KEYS or (key.startswith('sharepoint_governance.') and row.get('ReadinessTreatment') == 'ScopeReviewRequired'):
            card['exposure_to_review'].append({'finding_key': key, 'title': row.get('ActionTitle') or row.get('Feature'),
                                               'statement': row.get('Observation'), 'observed_at': row.get('ObservationDate') or '',
                                               'source': row.get('SourceFile') or ''})

    # Hardening results from supported configuration evidence; they never change a control.
    seen = set()
    for row in records:
        if row.get('EvidenceStatus') != 'supported' or not isinstance(row.get('SharingSettingResults'), list):
            continue
        for item in row['SharingSettingResults']:
            if item.get('requirement') == 'hardening' and item.get('result') == 'does_not_meet' and item['setting'] not in seen:
                seen.add(item['setting'])
                card['needs_improvement'].append({'setting': item['setting'], 'title': SETTING_LABELS.get(item['setting'], item['setting']),
                                                  'statement': f"{SETTING_LABELS.get(item['setting'], item['setting'])}: {item.get('observed') or 'not returned'}; "
                                                               f"recommended: {item.get('expected')}. Recommended hardening; it does not change a control result."})

    counts = Counter(control.get('status') for control in controls)
    control_counts = {'Established': counts.get('Observed', 0), 'Action required': counts.get('Action required', 0),
                      'Not established': counts.get('Not established', 0)}
    sharing_established = any(item['control_id'] == 'CONTENT.SHARING' for item in card['working'])
    if sharing_established and card['exposure_to_review']:
        summary = ('Current sharing policy restricts how new links are created, while retained exposure evidence identifies existing '
                   'access that owners should validate. Both are reported; neither changes the other.')
    else:
        summary = (f"{control_counts['Established']} of {len(controls)} content controls are established by current evidence; "
                   f"{control_counts['Action required']} require action and {control_counts['Not established']} are not yet established.")
    card.update(control_counts=control_counts, summary=summary)
    return card


def render_content_scorecard(card, prose):
    """Aggregate-only HTML; object-level detail stays in the technical evidence."""
    if not card or not sum(card['control_counts'].values()):
        return ''
    counts = card['control_counts']
    parts = ['<section class="content-scorecard" aria-label="Content and sharing scorecard"><h3>Content and sharing at a glance</h3>',
             '<p class="section-description">' + prose(f"Established: {counts['Established']} · Action required: {counts['Action required']} · "
                                                       f"Not established: {counts['Not established']} (counts of controls)") + '</p>',
             '<p>' + prose(card['summary']) + '</p>']
    for key, heading, description in SECTIONS:
        items = card.get(key) or []
        body = ''
        for item in items:
            line = prose(item.get('title') or item.get('control_id') or '')
            if key == 'working':
                provenance = item['provenance']
                line += (' <span class="muted">(' + prose('; '.join(part for part in (
                    'observed ' + str(provenance['observed_at'])[:10] if provenance.get('observed_at') else '',
                    'scope: ' + provenance['scope'] if provenance.get('scope') else '',
                    'source: ' + provenance['source'] if provenance.get('source') else '') if part)) + ')</span>')
            elif key == 'not_established' and item.get('questions'):
                line += ': ' + prose('; '.join(item['questions'][:3]))
            elif item.get('statement') and key != 'working':
                line += ' <span class="muted">' + prose(str(item['statement'])) + '</span>'
            body += '<li>' + line + '</li>'
        parts.append(f'<h4>{prose(heading)} ({len(items)})</h4><p class="section-description">{prose(description)}</p>'
                     + (f'<ul>{body}</ul>' if body else '<p class="muted">None identified from the retained evidence.</p>'))
    parts.append('<p class="qualification">Configuration describes how new links are created; it does not establish who can reach '
                 'existing content. Exposure counts keep their own units and can overlap.</p></section>')
    return ''.join(parts)

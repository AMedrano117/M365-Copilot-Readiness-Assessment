"""Readable retained workload evidence and precise investigation selectors."""

import json
import re


def readable_value(value):
    """Preserve nested field values as labelled text, without a raw JSON dump."""
    if value is None:
        return ''
    if isinstance(value, str) and value.strip().startswith(('{', '[')):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            pass
        else:
            return readable_value(parsed)
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, dict):
        return '; '.join(f'{key}: {readable_value(item)}' for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return '\n'.join(readable_value(item) for item in value)
    return str(getattr(value, 'value', value)).strip()


def source_fields(client, source, endpoint=''):
    state = (getattr(client, 'collection_status', {}) or {}).get(source, {}) or {}
    provenance = getattr(client, 'cache_provenance', {}) or getattr(client, 'power_platform_inventory', {}) or {}
    return {
        'Source API / Command': state.get('source_api') or state.get('command') or endpoint or 'Not retained',
        'Source File': state.get('source_file') or provenance.get('source_file') or 'Not retained',
        'Collected At': state.get('collected_at') or state.get('collection_completed_at') or provenance.get('collected_at') or provenance.get('refresh_date') or getattr(client, 'collected_at', '') or 'Not retained',
        'Collection Window': state.get('collection_window') or 'Original query window not retained; configuration inventory is a point-in-time observation',
        'Source State': state.get('availability_status') or ('available' if provenance.get('available') else 'Not retained'),
        'Pages Collected': state.get('pages_collected', 'Not retained'),
        'Truncated': state.get('truncated', 'Not retained'),
    }


def select_workload_item(key, row, rec):
    # Runtime import avoids a cycle when the main selector dispatches here.
    from .investigation_details import _item, _text, _details, _first

    text = ' '.join(_text(rec, field) for field in ('OriginalFeature', 'Feature', 'OriginalObservation', 'Observation', 'FindingKey')).lower()
    finding_key = _text(rec, 'FindingKey')
    if key == 'defender_incident_detail':
        if _text(row, 'Status').lower().replace('_', '') not in {'active', 'new', 'inprogress'}:
            return None
        high_only = finding_key in {'defender.incidents.high_severity', 'defender.incidents.high_severity_active'}
        if not finding_key and re.search(r'high[ -]severity', text):
            high_only = bool(re.search(r'high[ -]severity', _text(rec, 'Recommendation'), re.I)
                             or re.match(r'^\d+\s+high[ -]severity', _text(rec, 'Observation'), re.I))
        if high_only and _text(row, 'Severity').lower() != 'high':
            return None
        return _item('Incident', _text(row, 'Title'), _first(row, 'Incident URL', 'Incident ID'),
            'Unresolved high-severity security incident' if high_only else 'Unresolved security incident',
            _text(row, 'Status'), _details(row, 'Severity', 'Assigned To', 'Incident ID', 'Classification', 'Determination'),
            _first(row, 'Last Updated', 'Created Date'), 'Microsoft Defender > Incidents',
            'Open the incident, review the affected entities and record remediation or the security owner\'s treatment.')

    if key == 'purview_policy_detail':
        kind, enabled, mode = _text(row, 'Object Type'), _text(row, 'Enabled').lower(), _text(row, 'Mode').lower()
        if kind == 'DLP Rule' and any(word in text for word in ('dlp', 'data loss prevention')):
            if enabled not in {'no', 'false'} or finding_key == 'purview.dlp.simulation_only':
                return None
            reason = 'DLP rule is disabled'
        elif kind == 'DLP Policy' and any(word in text for word in ('dlp', 'data loss prevention')):
            if enabled in {'no', 'false'} or mode in {'disable', 'disabled', 'pendingdeletion', 'testwithnotifications', 'testwithoutnotifications', 'simulation', 'audit'}:
                reason = 'DLP policy is disabled or not enforcing protection'
            elif finding_key == 'baseline.dlp.scoped' and mode in {'enable', 'enabled', 'enforce', 'enforced'}:
                reason = 'Confirm this policy covers the intended users and content'
            else:
                return None
        elif kind == 'Label Policy' and any(word in text for word in ('publishing', 'published', 'publication')):
            if enabled in {'no', 'false'} or mode in {'disable', 'disabled', 'pendingdeletion'}:
                reason = 'Label publishing policy is inactive'
            elif finding_key == 'baseline.labels.published' and 'all users' not in _text(rec, 'Observation').lower():
                reason = 'Confirm label publishing scope for the intended users'
            else:
                return None
        else:
            return None
        return _item(kind, _text(row, 'Name'), _text(row, 'Object ID'), reason,
            _details(row, 'Enabled', 'Mode'), _details(row, 'Parent Policy', 'Parent Policy ID', 'Included Locations', 'Excluded Locations', 'Conditions', 'Actions', 'Published Labels'),
            _first(row, 'Modified UTC', 'Collected At'),
            'Microsoft Purview > Information protection > Publishing policies' if kind == 'Label Policy' else 'Microsoft Purview > Data loss prevention > Policies',
            'Review the named policy or rule, scope, conditions and actions with its owner before enabling or broadening protection.')

    if key == 'data_exposure_detail':
        signal_map = {'anonymous_links': ['Anyone links'], 'broad_internal_access': ['Everyone permissions', 'EEEU permissions', 'Organization links'],
                      'external_exposure': ['External exposure'], 'potentially_overshared': ['Potentially overshared items'],
                      'unlabeled_sensitive': ['Unlabeled sensitive items'], 'ownerless_sites': ['Ownerless site']}
        wanted = signal_map.get(finding_key.removeprefix('data_exposure.'))
        signals = set(_text(row, 'Risk Signals').split('; '))
        matches = [name for name in wanted or [] if name in signals]
        if _text(row, 'Detail Type') != 'Risk Evidence' or not matches or not _first(row, 'Item URL', 'Item ID', 'Site URL', 'Site ID', 'Site Name'):
            return None
        measures = '; '.join(f'{name}: {_text(row, name + " Count") or "individual signal count not retained"}' for name in matches)
        return _item('Content location', _first(row, 'Site Name', 'Item URL', 'Site URL', 'Site ID'),
            _first(row, 'Item URL', 'Item ID', 'Site URL', 'Site ID'), '; '.join(matches), _text(row, 'Freshness'),
            measures + '; ' + _details(row, 'Permission Recipient', 'Permission ID', 'Permission Role', 'Link ID', 'Link Type', 'Owner', 'Sensitivity Label', 'Source File'),
            _text(row, 'Report Date'), 'SharePoint site permissions / Microsoft Purview > DSPM',
            'Validate the named permission or link with its owner. A site-level count identifies a location to inspect; it does not supply individual link records.')

    if key == 'power_platform_detail':
        kind, state = _text(row, 'Detail Type'), _text(row, 'State').lower()
        if kind in {'Flow', 'App', 'Agent'}:
            if not re.search(r'\b(?:suspended|stopped|disabled|failed|faulted)\b', text):
                return None
            if state not in {'suspended', 'stopped', 'disabled', 'failed', 'faulted'} or state not in text:
                return None
            # A flow failure never selects an unrelated app or agent.
            if kind.lower() not in text and not (kind == 'Agent' and 'bot' in text):
                return None
            reason = f'{kind} is {state}'
        elif kind == 'DLP Policy' and ('dlp' in text or 'data polic' in text) and 'block' in text:
            blocked = _text(row, 'Blocked Connectors')
            if not blocked:
                return None
            requested = [term for term in ('http', 'custom', 'premium') if term in text]
            if requested and not any(term in blocked.lower() for term in requested):
                return None
            reason = 'Policy contains the reported connector restriction'
        else:
            return None
        return _item('Power Platform ' + kind.lower(), _text(row, 'Name'), _first(row, 'Resource ID', 'Resource Name'), reason,
            _text(row, 'State'), _details(row, 'Environment ID', 'Owner ID', 'Blocked Connectors', 'Scope', 'Additional Context'),
            _first(row, 'Modified UTC', 'Created UTC', 'Collected At'), 'Power Platform admin center > Manage',
            'Open the resource in its named environment, confirm the owner and intended use, and investigate its reported state or applicable policy.')
    return None

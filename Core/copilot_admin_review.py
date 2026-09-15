"""Dated admin-center context derived from collected data, without scoring portal cards."""

import json
import re

from .source_evidence import source_is_complete


COPILOT_LOCATION = '470f2276-e011-4e9d-a6ec-20768be3a4b0'


def _list(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    return value if isinstance(value, list) else [value] if isinstance(value, dict) else []


def _get(row, name, default=None):
    return next((value for key, value in row.items() if key.casefold() == name.casefold()), default) if isinstance(row, dict) else default


def _boolean(value):
    if value is True or str(value).lower() == 'true':
        return True
    if value is False or str(value).lower() == 'false':
        return False
    return None


def subscription_capacity(catalog, paid_ids, *, complete):
    rows = [row for row in catalog if isinstance(row, dict) and str(row.get('skuId', '')).lower() in paid_ids]
    from .copilot_activity_summary import _count
    result = {'available': False, 'enabled_seats': None, 'assigned_seats': None,
              'reason': 'Subscription capacity was not completely returned.'}
    if (not complete or any(not isinstance(row, dict) or not row.get('skuId') for row in catalog)
            or len({str(row.get('skuId')).lower() for row in rows}) != len(rows)):
        return result
    capacity = [_count(row['prepaidUnits'].get('enabled')) if isinstance(row.get('prepaidUnits'), dict) else None for row in rows]
    consumed = [_count(row.get('consumedUnits')) for row in rows]
    if any(value is None for value in capacity + consumed):
        return result
    result.update(available=True, enabled_seats=sum(capacity), assigned_seats=sum(consumed), reason='',
                  qualification='Subscription seats across recognized paid Copilot products; not unique people or active users.')
    return result


def summarize_copilot_dlp(client):
    policies = getattr(client, 'dlp_policies', {}) or {}
    rules = getattr(client, 'dlp_rules', {}) or {}
    policies_complete = source_is_complete(client, 'dlp_policies', policies)
    rules_complete = source_is_complete(client, 'dlp_rules', rules)
    rows, detail = [], []
    for policy in policies.get('policies', []) or []:
        if not isinstance(policy, dict):
            continue
        locations = [row for row in _list(policy.get('Locations'))
                     if str(_get(row, 'Location', '')).lower() == COPILOT_LOCATION]
        planes = policy.get('EnforcementPlanes') or []
        if isinstance(planes, str):
            planes = [planes]
        plane_match = 'copilotexperiences' in {str(value).lower() for value in planes}
        name_match = bool(re.search(r'\bcopilot\b', str(policy.get('Name', '')), re.I))
        if not (locations or plane_match or name_match):
            continue
        identifiers = {str(policy[key]).casefold() for key in ('Name', 'Identity', 'Guid') if policy.get(key)}
        matched = [row for row in rules.get('rules', []) or [] if isinstance(row, dict)
                   and any(str(row.get(key, '')).casefold() in identifiers for key in ('ParentPolicyName', 'ParentPolicyId'))]
        blocking = []
        for rule in matched:
            # Read the documented explicit action. Policy names and generic BlockAccess
            # flags cannot establish Copilot content-processing restrictions.
            actions = _list(rule.get('RestrictAccess'))
            if _boolean(rule.get('Disabled')) is False and any(
                str(_get(action, 'setting', '')).lower() == 'excludecontentprocessing'
                and str(_get(action, 'value', '')).lower() == 'block' for action in actions):
                blocking.append(rule)
        mode = str(policy.get('Mode') or '')
        if _boolean(policy.get('Enabled')) is False or mode.lower() in {'disable', 'disabled', 'pendingdeletion'}:
            status = 'Disabled'
        elif mode.lower() in {'testwithnotifications', 'testwithoutnotifications'}:
            status = 'Monitoring only'
        elif mode.lower() in {'enable', 'enforce'}:
            status = 'Enabled; rule effect needs review'
            if blocking and locations and rules_complete and policies_complete:
                status = 'Blocking action configured'
        else:
            status = 'Mode not established'
        scope = 'Targeting not returned; collect current policy details'
        if locations:
            inclusions = [item for loc in locations for item in _list(_get(loc, 'Inclusions'))]
            exclusions = [item for loc in locations for item in _list(_get(loc, 'Exclusions'))]
            all_tenant = any(str(_get(item, 'Type', '')).lower() == 'tenant'
                             and str(_get(item, 'Identity', '')).lower() == 'all' for item in inclusions)
            scope = ('Tenant inclusion' if all_tenant else f'{len(inclusions)} explicit inclusions')
            scope += f'; {len(exclusions)} explicit exclusions'
            scope += '. Validate group membership and the intended users.'
            if any(_get(loc, 'Inclusions') is None or _get(loc, 'Exclusions') is None for loc in locations):
                scope += ' Inclusion or exclusion details were not fully returned.'
        basis = 'Copilot application location' if locations else 'Copilot enforcement plane' if plane_match else 'Policy name only; target unverified'
        row = {'Policy': policy.get('DisplayName') or policy.get('Name') or 'Unnamed policy',
               'Mode': status, 'Target evidence': basis, 'Scope': scope,
               'Rules': len(matched) if rules_complete else None,
               'Action': f'{len(blocking)} enabled content-processing block rules returned' if blocking else 'Copilot blocking action not established',
               'Qualification': 'Configuration evidence only. Validate rule conditions, exclusions and effective behavior; this does not close the DLP readiness check.'}
        if not policies_complete or not rules_complete:
            row['Qualification'] += ' Policy or rule collection is incomplete.'
        rows.append(row)
        detail.append({**row, 'Raw policy': json.dumps(policy, ensure_ascii=False, default=str),
                       'Raw matched rules': json.dumps(matched, ensure_ascii=False, default=str)})
    return {'rows': rows, 'detail': detail, 'available': policies_complete,
            'reason': '' if policies_complete else 'DLP policy collection was unavailable or incomplete.'}


def build_admin_review(m365_client, purview_client, context=None):
    context = context or {}
    collected = context.get('collected_at') or ''
    policy_date = getattr(purview_client, 'collected_at', '') or ''
    rows = []

    def add(area, topic, value, source, observed='', note='', status=None):
        rows.append({'Area': area, 'Topic': topic, 'Value': value,
                     'Status': status or ('Collected' if value is not None else 'Not established'),
                     'Evidence date': observed or 'Source date not recorded', 'Source': source,
                     'Follow-up': note})

    usage = getattr(m365_client, 'copilot_usage', {}) or {}
    period = usage.get('selected_period') or 'D28'
    summary = (usage.get('periods') or {}).get(period, {}) if usage.get('available') else {}
    add('adoption', 'Paid Copilot enabled users', summary.get('enabled_users'), 'Microsoft Graph Copilot usage',
        usage.get('refresh_date'), f'{period} reporting window; this count is not purchased license capacity.')
    add('adoption', 'Paid Copilot active users', summary.get('active_users'), 'Microsoft Graph Copilot usage',
        usage.get('refresh_date'), f'{period} reporting window; unlicensed Chat is excluded.')
    engagement = usage.get('engagement_summary') or {}
    for label, key in [('Paid Copilot prompts', 'total_prompts'), ('Paid Copilot work-chat prompts', 'work_chat_prompts'),
                       ('Paid Copilot web-chat prompts', 'web_chat_prompts'), ('Paid Copilot active user-days', 'active_user_days')]:
        note = ('D28; user-days sum each reported user\'s active days, not unique calendar days.'
                if key == 'active_user_days' else 'D28; aggregate counts for licensed users. No prompt content is collected.')
        if not engagement:
            note = 'This saved collection has no prompt-activity aggregates. A new live collection can supply them.'
        add('adoption', label, engagement.get(key), 'Microsoft Graph Copilot usage v2', engagement.get('refresh_date'),
            engagement.get('reason') or note)
    license_data = getattr(m365_client, 'license_coverage', {}) or {}
    capacity = license_data.get('copilot_subscription_capacity') or {}
    for label, key in [('Paid Copilot subscription seats enabled', 'enabled_seats'), ('Paid Copilot subscription seats assigned', 'assigned_seats')]:
        add('licensing', label, capacity.get(key) if capacity.get('available') else None, 'Microsoft Graph subscribed products',
            license_data.get('refresh_date'), capacity.get('qualification') or capacity.get('reason') or 'A new live collection supplies subscription seat counts.')
    dlp = summarize_copilot_dlp(purview_client)
    for row in dlp['rows'] + dlp['detail']:
        row['Evidence date'] = policy_date or 'Source date not recorded'
        row['Included in collection'] = collected
    for row in dlp['rows']:
        add('data_protection', row['Policy'], row['Mode'], 'Purview DLP policies and rules', policy_date,
            row['Qualification'] + ' ' + row['Target evidence'] + '. ' + row['Scope'], status='Configuration observed')
    if not dlp['rows']:
        add('data_protection', 'Copilot-specific DLP configuration', None, 'Purview DLP policies and rules', policy_date,
            dlp['reason'] or 'No Copilot-specific policy was identified in the supplied fields. This does not establish the absence of protection; review policy targeting.')
    audit = getattr(purview_client, 'audit_config', {}) or {}
    audit_enabled = _boolean(audit.get('unified_audit_enabled')) if source_is_complete(purview_client, 'audit_config', audit) else None
    add('data_protection', 'Unified audit logging', 'Enabled' if audit_enabled is True else 'Disabled' if audit_enabled is False else None,
        'Exchange organization audit configuration', policy_date, 'Logging configuration does not prove all Copilot events are captured.')
    connections = getattr(m365_client, 'external_connections', None)
    connections_ok = source_is_complete(m365_client, 'external_connections')
    add('applications', 'External connections returned', len(connections) if connections_ok and isinstance(connections, list) else None,
        'Microsoft Graph external connections', collected, 'Connections visible to the assessment application; review access boundaries and business use.')
    manual = [
        {'Topic': 'Unlicensed Copilot Chat active users', 'Reason': 'Microsoft Graph Copilot reports cover licensed users. Audit events have different scope and are not a replacement for this portal count.', 'When needed': 'When included Chat adoption is in scope.'},
        {'Topic': 'Agent activity, Copilot Search, credits and assisted hours', 'Reason': 'These portal metrics are not returned by the supported reporting endpoints used here.', 'When needed': 'When these experiences or value/cost measurements are in scope.'},
        {'Topic': 'Security dashboard referenced files/sites and recommendation completion', 'Reason': 'DLP configuration and access exports do not reproduce these dashboard totals.', 'When needed': 'When the dashboard totals are needed in the customer deliverable.'},
        {'Topic': 'Optimize checklist and remaining Copilot experience settings', 'Reason': 'The full checklist is not exposed by the interfaces used here. Some individual settings need a separate delegated API permission or are preview-only.', 'When needed': 'Review the remaining applicable settings; screenshots are optional evidence of that review.'},
    ]
    return {'rows': rows, 'dlp': dlp, 'manual_checks': manual,
            'collection_date': collected, 'qualification': 'Original evidence dates are retained. These configuration and usage observations do not reproduce a portal score or establish pilot approval.'}

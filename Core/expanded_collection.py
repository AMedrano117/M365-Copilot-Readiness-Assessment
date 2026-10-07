"""Additional read-only datasets, with independent failures and bounded fan-out."""

import asyncio
from datetime import datetime, timezone

from .collector_registry import source_allowed, supplemental_enabled


USER_SIGNIN_ACTIVITY_PATH = '/v1.0/users?$select=id,displayName,userPrincipalName,signInActivity&$top=500'


def plain(value):
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if hasattr(value, '__dict__'):
        return {key: plain(item) for key, item in vars(value).items() if not key.startswith('_')}
    return value


def get(record, key, default=None):
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)


async def collect_entra_details(client, fetch, *, permission_profile='standard', preview_collectors='auto'):
    """Reuse the existing paginated Graph reader and never discard completed children."""
    semaphore = asyncio.Semaphore(4)
    client.assessment_datasets = getattr(client, 'assessment_datasets', {})

    async def read(name, path, parent=None, quality='standard'):
        if not source_allowed(name, permission_profile) or (name == 'user_signin_activity' and permission_profile == 'restricted'):
            result = {'available': False, 'availability_status': 'not_requested', 'value': [],
                      'reason': 'Excluded by the Restricted permission profile.'}
        else:
            try:
                async with semaphore:
                    result = await fetch(path)
                result = dict(result or {})
            except Exception as exc:
                result = {'available': False, 'availability_status': 'unavailable', 'value': [],
                          'reason': str(exc), 'status_code': getattr(exc, 'status_code', None)}
        rows = plain(result.get('value') or [])
        if parent is not None:
            rows = [dict(row, ParentId=parent) for row in rows]
        state = {key: plain(value) for key, value in result.items() if key != 'value'}
        state.update(source_api=result.get('source_api') or 'https://graph.microsoft.com' + path,
                     collected_at=result.get('collection_completed_at') or datetime.now(timezone.utc).isoformat(),
                     evidence_quality=quality, scope='Returned tenant records' if parent is None else f'Parent object {parent}',
                     complete=result.get('available') is True and not result.get('truncated') and result.get('availability_status', 'available') == 'available')
        state['evidence_level'] = 'observed_operation' if name in {'windows_protection','compliance_setting_states','user_signin_activity'} else 'policy_enforcement' if name=='authentication_strengths' else 'configuration'
        if name == 'user_signin_activity':
            state.setdefault('request_url', 'https://graph.microsoft.com' + path)
            state['request_params'] = {'$select':'id,displayName,userPrincipalName,signInActivity', '$top':'500'}
            state['permissions'] = 'Existing User.Read.All or Directory.Read.All plus AuditLog.Read.All application permissions.'
            state['licensing'] = 'Microsoft documents Entra ID P1 or P2 for signInActivity; an API failure does not independently identify its cause.'
            state['limitations'] = ('User-object activity is independent of the seven-day sign-in log query. '
                                    'lastSignInDateTime records interactive attempts, including failures; '
                                    'lastSuccessfulSignInDateTime records successful interactive or non-interactive access and is not backfilled. '
                                    'Null or omitted values remain unknown. This independent read never replaces the basic user inventory.')
            state['documentation_verified_at'] = '2026-10-02'
            state['documentation_urls'] = [
                'https://learn.microsoft.com/en-us/graph/api/user-list?view=graph-rest-1.0',
                'https://learn.microsoft.com/en-us/graph/api/resources/signinactivity?view=graph-rest-1.0',
            ]
            state.setdefault('reason', state.get('error', ''))
        client.collection_status[name if parent is None else f'{name}:{parent}'] = state
        client.assessment_datasets.setdefault(name, []).append({'records': rows, 'source': state})
        return rows

    await asyncio.gather(
        read('directory_devices', '/v1.0/devices'),
        read('user_signin_activity', USER_SIGNIN_ACTIVITY_PATH),
        read('authentication_strengths', '/v1.0/identity/conditionalAccess/authenticationStrength/policies'),
        read('compliance_setting_summaries', '/v1.0/deviceManagement/deviceCompliancePolicySettingStateSummaries'))
    jobs = []
    for policy in getattr(client, 'compliance_policies', []) or []:
        identifier = get(policy, 'id')
        if identifier:
            jobs.append(read('compliance_assignments', f'/v1.0/deviceManagement/deviceCompliancePolicies/{identifier}/assignments', identifier))
    for device in getattr(client, 'managed_devices', []) or []:
        identifier = get(device, 'id')
        if identifier and str(get(device, 'operatingSystem', '')).lower() == 'windows':
            jobs.append(read_protection(read, identifier))
    for dataset in client.assessment_datasets.get('compliance_setting_summaries', []):
        for summary in dataset['records']:
            identifier = summary.get('id')
            if identifier:
                jobs.append(read('compliance_setting_states', f'/v1.0/deviceManagement/deviceCompliancePolicySettingStateSummaries/{identifier}/deviceComplianceSettingStates', identifier))
    for app in getattr(client, 'service_principals', []) or []:
        identifier = get(app, 'id')
        if identifier:
            jobs.extend([read('application_owners', f'/v1.0/servicePrincipals/{identifier}/owners', identifier),
                         read('application_permissions', f'/v1.0/servicePrincipals/{identifier}/appRoleAssignments', identifier)])
    groups = set()
    for policy in getattr(client, 'ca_policies', []) or []:
        users = get(get(policy, 'conditions', {}), 'users', {}) or {}
        groups.update(get(users, 'includeGroups', []) or [])
        groups.update(get(users, 'excludeGroups', []) or [])
    for identifier in sorted(groups):
        if identifier != 'All':
            jobs.append(read('ca_group_members', f'/v1.0/groups/{identifier}/transitiveMembers', identifier))
    await asyncio.gather(*jobs)
    if supplemental_enabled(preview_collectors, 'entra-recommendations', permission_profile):
        rows = await read('entra_recommendations', '/beta/directory/recommendations', quality='preview')
        await asyncio.gather(*[read('entra_impacted_resources', f"/beta/directory/recommendations/{row['id']}/impactedResources", row['id'], 'preview') for row in rows if row.get('id')])


async def read_protection(read, identifier):
    # This navigation property returns an object, not a collection. The shared
    # reader accepts object_reader=True through the adapter at the call site.
    return await read('windows_protection', f'/v1.0/deviceManagement/managedDevices/{identifier}/windowsProtectionState', identifier)


def dataset_rows(client, name):
    return [row for dataset in getattr(client, 'assessment_datasets', {}).get(name, []) for row in dataset.get('records', [])]

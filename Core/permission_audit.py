"""Read-only verification of requested and consented application API access."""

from uuid import UUID

from .collector_registry import RESOURCE_APP_IDS, profile_permission_resources


async def audit_restricted_access(client, client_id, graph_roles=()):
    """Fail closed on excess grants or incomplete inventory; never revoke access."""
    allowed = profile_permission_resources('restricted')
    excess = set()

    async def complete_collection(path, params=None):
        result = await client.get_collection(path, params=params)
        if (not isinstance(result, dict) or result.get('available') is not True
                or result.get('availability_status') != 'available'
                or result.get('truncated') or result.get('complete') is False
                or not isinstance(result.get('value'), list)):
            raise ValueError(f'Incomplete permission inventory from {path}.')
        return result['value']

    async def application_resource(app_id):
        rows = await complete_collection('/v1.0/servicePrincipals', {
            '$filter': f"appId eq '{str(UUID(app_id))}'",
            '$select': 'id,appId,displayName,appRoles',
        })
        if len(rows) != 1:
            raise ValueError(f'Cannot uniquely identify API resource {app_id}.')
        return rows[0]

    def inspect_permission(resource, role_id, origin, permission_type='Role'):
        resource_app_id = str(resource.get('appId', '')).lower()
        role = next((role for role in resource.get('appRoles', [])
                     if str(role.get('id', '')).lower() == str(role_id).lower()), {})
        role_name = role.get('value') or str(role_id)
        if permission_type != 'Role' or role_name not in allowed.get(resource_app_id, set()):
            label = resource.get('displayName') or resource_app_id
            excess.add(f'{origin}: {label} / {role_name} ({permission_type})')

    try:
        app_id = str(UUID(str(client_id)))
        for role in set(graph_roles) - allowed.get(RESOURCE_APP_IDS['graph'], set()):
            excess.add(f'Current Graph token: {role}')
        applications = await complete_collection('/v1.0/applications', {
            '$filter': f"appId eq '{app_id}'", '$select': 'id,appId,requiredResourceAccess',
        })
        if len(applications) != 1 or 'requiredResourceAccess' not in applications[0]:
            raise ValueError('Cannot uniquely inspect the assessment application manifest.')
        principal = await application_resource(app_id)
        principal_id = str(UUID(principal['id']))
        assignments = await complete_collection(f'/v1.0/servicePrincipals/{principal_id}/appRoleAssignments')
        by_app_id, by_object_id = {}, {}
        for entry in applications[0]['requiredResourceAccess'] or []:
            resource_id = str(entry['resourceAppId']).lower()
            if resource_id not in by_app_id:
                resource = await application_resource(resource_id)
                by_app_id[resource_id] = resource
                by_object_id[str(resource['id']).lower()] = resource
            for permission in entry.get('resourceAccess', []):
                inspect_permission(by_app_id[resource_id], permission['id'], 'Requested', permission.get('type'))
        for assignment in assignments:
            resource_id = str(UUID(assignment['resourceId']))
            if resource_id not in by_object_id:
                resource = await client.get_json(f'/v1.0/servicePrincipals/{resource_id}',
                                                params={'$select': 'id,appId,displayName,appRoles'})
                if not isinstance(resource, dict) or not resource.get('appId') or 'appRoles' not in resource:
                    raise ValueError(f'Cannot inspect consented API resource {resource_id}.')
                by_object_id[resource_id] = resource
            inspect_permission(by_object_id[resource_id], assignment['appRoleId'], 'Consented')
        if excess:
            return {
                'verified': False, 'excess_permissions': sorted(excess),
                'reason': 'Restricted profile has excess application access: ' + '; '.join(sorted(excess))
                    + '. Review and revoke excess consent manually, or use a dedicated restricted application. '
                      'Editing the requested-permission manifest alone does not revoke consent.',
            }
        return {'verified': True, 'excess_permissions': [],
                'reason': 'Requested and consented application API permissions match the restricted profile.'}
    except Exception as exc:
        return {'verified': False, 'excess_permissions': [],
                'reason': f'Restricted application access could not be verified: {exc} '
                          'Verify Application.Read.All consent and rerun preflight; no access was changed.'}

"""
Microsoft Entra Client
Provides authenticated access to Microsoft Entra ID (formerly Azure AD) APIs.
Fetches and caches: Conditional Access policies, MFA registration, Identity Protection,
PIM, Access Reviews, Device Compliance, B2B settings, and Application Consent data.
Used for enhanced Entra observations focused on Copilot adoption.
"""
from . import console_reporting as console
import asyncio
import base64
import httpx
import json
from azure.core.exceptions import HttpResponseError
from .spinner import get_timestamp, _stdout_lock
from datetime import datetime, timedelta

MICROSOFT_OWNER_TENANT_IDS = {
    'f8cdef31-a31e-4b4a-93e4-5f571e91255a',
    '72f988bf-86f1-41af-91ab-2d7cd011db47',
}
MICROSOFT_FIRST_PARTY_APP_IDS = {
    '00000002-0000-0000-c000-000000000000',
    '00000003-0000-0000-c000-000000000000',
    '00000002-0000-0ff1-ce00-000000000000',
    '00000003-0000-0ff1-ce00-000000000000',
    '08e18876-6177-487e-b8b5-cf950c1e598c',
    '14d82eec-204b-4c2f-b7e8-296a70dab67e',
    '1950a258-227b-4e31-a9cf-717495945fc2',
}

def _get_attr(obj, attr_name, default=''):
    """Safely read a field from either a Graph SDK model object or a plain dict.

    Graph collections reach us in both shapes: the SDK path yields model objects with
    snake_case attributes, while the raw-HTTP path yields camelCase dicts. Callers must not
    assume one or the other - assuming dicts previously raised AttributeError mid-loop, which
    was swallowed by the surrounding except and silently zeroed the resulting counters.
    """
    if obj is None:
        return default

    import re
    snake_case = re.sub(r'(?<!^)(?=[A-Z])', '_', attr_name).lower()

    # Dict shape (raw HTTP / JSON responses): try camelCase then snake_case
    if isinstance(obj, dict):
        for key in (attr_name, snake_case):
            if key in obj:
                value = obj.get(key)
                if value is not None:
                    return value
        return default

    # SDK model object: try direct attribute access, then snake_case
    value = getattr(obj, attr_name, None)
    if value is not None:
        return value
    value = getattr(obj, snake_case, None)
    return value if value is not None else default


def _apply_authorization_policy(client_obj, policy):
    """Apply tenant authorization-policy fields without inferring assignments from definitions."""
    client_obj.authorization_policy = policy
    guest_setting = _get_attr(policy, 'allowInvitesFrom', 'Unknown')
    client_obj.auth_policy_summary['guest_invite_setting'] = guest_setting
    client_obj.b2b_summary['guest_invite_restrictions'] = guest_setting

    default_perms = _get_attr(policy, 'defaultUserRolePermissions')
    client_obj.auth_policy_summary['default_user_role_permissions'] = default_perms
    if not default_perms:
        return
    client_obj.auth_policy_summary['allow_users_to_register_apps'] = _get_attr(
        default_perms, 'allowedToCreateApps', False
    )
    assigned_policies = _get_attr(default_perms, 'permissionGrantPoliciesAssigned', None)
    if assigned_policies is None:
        assigned_policies = _get_attr(default_perms, 'permission_grant_policies_assigned', None)
    if assigned_policies is None:
        return

    assigned_policies = list(assigned_policies or [])
    user_consent_policies = [
        str(value) for value in assigned_policies
        if str(value).lower().startswith('managepermissiongrantsforself.')
    ]
    client_obj.consent_summary['consent_configuration_available'] = True
    client_obj.consent_summary['assigned_user_consent_policies'] = user_consent_policies
    client_obj.consent_summary['user_consent_allowed'] = bool(user_consent_policies)
    client_obj.consent_summary['admin_consent_required'] = not bool(user_consent_policies)

async def _get_graph_http_client():
    """Get HTTP client for Microsoft Graph API with bearer token"""
    from .get_graph_client import get_shared_credential
    
    credential = get_shared_credential()
    token = credential.get_token('https://graph.microsoft.com/.default')
    
    return httpx.AsyncClient(
        base_url='https://graph.microsoft.com',
        headers={
            "Authorization": f"Bearer {token.token}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        },
        timeout=30.0
    )


def _get_graph_token_roles():
    """Return application roles in the current Graph token without logging token material."""
    try:
        from .get_graph_client import get_shared_credential

        token = get_shared_credential().get_token('https://graph.microsoft.com/.default').token
        payload = token.split('.')[1]
        payload += '=' * (-len(payload) % 4)
        return set(json.loads(base64.urlsafe_b64decode(payload)).get('roles', []))
    except Exception:
        return set()


def _extract_response_items(response):
    """Normalize SDK or HTTP responses into a list of items."""
    if response is None:
        return []
    if isinstance(response, dict):
        return response.get('value', []) if isinstance(response.get('value', []), list) else []
    if hasattr(response, 'value'):
        value = getattr(response, 'value', None)
        return value if isinstance(value, list) else []
    if hasattr(response, 'json'):
        try:
            data = response.json()
            if isinstance(data, dict):
                value = data.get('value', [])
                return value if isinstance(value, list) else []
        except Exception:
            return []
    return []


async def _fetch_graph_collection_via_http(path, params=None, max_pages=100, headers=None):
    """Fetch a Graph collection using raw HTTP to support endpoints missing in the SDK."""
    http_client = await _get_graph_http_client()
    results = []
    next_url = path
    next_params = params
    pages = 0

    try:
        while next_url and pages < max_pages:
            response = await http_client.get(next_url, params=next_params, headers=headers)
            if response.status_code >= 400:
                return {
                    'available': False,
                    'availability_status': 'partial' if pages else 'unavailable',
                    'status_code': response.status_code,
                    'error': _graph_error_detail(response),
                    'value': results,
                    'records_collected': len(results),
                    'pages_collected': pages,
                    'truncated': bool(pages),
                }

            response.raise_for_status()
            data = response.json()
            values = data.get('value', []) if isinstance(data, dict) else []
            if isinstance(values, list):
                results.extend(values)

            next_url = data.get('@odata.nextLink') if isinstance(data, dict) else None
            next_params = None
            pages += 1

        return {
            'available': True,
            'availability_status': 'partial' if next_url else 'available',
            'truncated': bool(next_url),
            'value': results,
            'records_collected': len(results),
            'pages_collected': pages,
        }
    except Exception as exc:
        return {
            'available': False,
            'availability_status': 'partial' if results else 'unavailable',
            'error': str(exc),
            'value': results,
            'records_collected': len(results),
            'pages_collected': pages,
            'truncated': bool(results),
        }
    finally:
        await http_client.aclose()


def _graph_error_detail(response):
    """Keep the Graph explanation, without response headers, credentials or tracebacks."""
    from .orchestrator_powershell import _sanitize_collector_detail
    detail = f'Microsoft Graph HTTP {response.status_code}'
    try:
        body = response.json()
        error = body.get('error') if isinstance(body, dict) else None
        if isinstance(error, dict):
            parts = [str(error[key]) for key in ('code', 'message') if isinstance(error.get(key), str)]
            if parts:
                detail += ': ' + ' - '.join(parts)
    except (ValueError, TypeError):
        pass
    return ' '.join(_sanitize_collector_detail(detail).split())[:1000]


async def _fetch_graph_object_via_http(path):
    """Fetch a Graph singleton while preserving the same collection-status contract."""
    http_client = await _get_graph_http_client()
    try:
        response = await http_client.get(path)
        if response.status_code >= 400:
            return {
                'available': False, 'availability_status': 'unavailable',
                'status_code': response.status_code, 'object': None, 'error': _graph_error_detail(response),
                'records_collected': 0, 'pages_collected': 0, 'truncated': False,
            }
        response.raise_for_status()
        return {
            'available': True, 'availability_status': 'available',
            'object': response.json(), 'records_collected': 1,
            'pages_collected': 1, 'truncated': False,
        }
    except Exception as exc:
        return {
            'available': False, 'availability_status': 'unavailable',
            'error': str(exc), 'object': None, 'records_collected': 0,
            'pages_collected': 0, 'truncated': False,
        }
    finally:
        await http_client.aclose()

async def get_entra_client(graph_client, tenant_id=None, preview_collectors='none'):
    """
    Get authenticated client for Microsoft Entra ID (Azure AD) APIs.
    Fetches comprehensive identity, security, and compliance data.
    
    Args:
        graph_client: Existing Microsoft Graph SDK client
        tenant_id: Azure tenant ID (optional)
    
    Returns:
        EntraClient object with cached Entra data and pre-computed summaries,
        or minimal client if permissions are insufficient (graceful degradation)
    """
    
    class EntraClient:
        def __init__(self):
            self.available = False
            self.tenant_id = tenant_id

            # Per-dataset fetch outcome, keyed by phase-1 task name (risky_users, auth_methods,
            # role_assignments, ...). True only when that specific query returned data, so that a
            # failed query is never reported downstream as a clean result.
            self.data_sources = {}
            self.collection_status = {}
            self.role_definitions = []

            
            # Conditional Access
            self.ca_policies = []
            self.ca_summary = {
                'total': 0,
                'require_mfa': 0,
                'require_compliant_device': 0,
                'require_managed_device': 0,
                'target_m365_apps': 0,
                'target_all_apps': 0,
                'block_legacy_auth': 0,
                'location_based': 0,
                'user_risk_based': 0,
                'signin_risk_based': 0,
                'enabled': 0,
                'disabled': 0,
                'report_only': 0
            }
            
            # MFA & Authentication Methods
            self.auth_methods_registration = []
            self.auth_summary = {
                'total_users': 0,
                'mfa_registered': 0,
                'mfa_capable': 0,
                'passwordless_enabled': 0,
                'mfa_registration_rate': 0,
                'passwordless_adoption_rate': 0,
                'methods': {
                    'microsoftAuthenticator': 0,
                    'fido2': 0,
                    'windowsHello': 0,
                    'phone': 0,
                    'email': 0,
                    'softwareOath': 0,
                    'temporaryAccessPass': 0
                }
            }
            
            # Identity Protection
            self.risky_users = []
            self.risk_detections = []
            self.risk_summary = {
                'risky_users_total': 0,
                'risky_users_high': 0,
                'risky_users_medium': 0,
                'risky_users_low': 0,
                'confirmed_compromised': 0,
                'at_risk': 0,
                'remediated': 0,
                'dismissed': 0,
                'risk_detections_total': 0,
                'risk_detections_high': 0,
                'user_risk_policy_exists': False,
                'signin_risk_policy_exists': False
            }
            
            # Privileged Identity Management (PIM)
            self.role_assignments = []
            self.role_eligibility_schedules = []
            self.role_assignment_schedules = []
            self.pim_summary = {
                'total_active_assignments': 0,
                'total_eligible_assignments': 0,
                'total_time_bound_assignments': 0,
                'permanent_assignments': 0,
                'permanent_global_admins': 0,
                'permanent_privileged_roles': 0,
                'unclassified_active_assignments': 0,
                'eligible_assignments': 0,
                'pim_enabled_roles': 0,
                'roles_with_only_permanent': 0
            }
            
            # Access Reviews
            self.access_reviews = []
            self.access_review_summary = {
                'total_definitions': 0,
                'active_reviews': 0,
                'group_membership_reviews': 0,
                'role_assignment_reviews': 0,
                'application_assignment_reviews': 0,
                'guest_user_reviews': 0,
                'recurring_reviews': 0,
                'one_time_reviews': 0
            }
            
            # Device Management & Compliance
            self.managed_devices = []
            self.compliance_policies = []
            self.device_summary = {
                'total_managed': 0,
                'compliant': 0,
                'non_compliant': 0,
                'in_grace_period': 0,
                'not_applicable': 0,
                'error': 0,
                'corporate_owned': 0,
                'personal_byod': 0,
                'windows': 0,
                'ios': 0,
                'android': 0,
                'macos': 0,
                'compliance_policies_total': 0,
                'ca_requires_compliance': False
            }
            
            # Group-Based Licensing
            self.groups_with_licenses = []
            self.group_licensing_summary = {
                'total_groups_with_licenses': 0,
                'groups_with_errors': 0,
                'total_license_errors': 0,
                'copilot_license_groups': 0,
                'dynamic_groups': 0,
                'security_groups': 0,
                'distribution_groups': 0
            }
            
            # External Collaboration (B2B)
            self.guest_users = []
            self.cross_tenant_access_policy = {}
            self.b2b_summary = {
                'total_guests': 0,
                'guests_with_licenses': 0,
                'guest_invite_restrictions': 'Unknown',
                'cross_tenant_access_configured': False,
                'default_settings': {},
                'partner_configurations': 0
            }
            
            # Application Consent & Permissions
            self.service_principals = []
            self.oauth_permission_grants = []
            self.permission_grant_policies = []
            self.application_signin_summary = []
            self.service_principal_signin_activities = []
            self.app_activity_summary = {
                'available': False,
                'by_app': {},
                'reason': None,
            }
            self.consent_summary = {
                'total_apps': 0,
                'apps_with_delegated_permissions': 0,
                'apps_with_application_permissions': 0,
                'consent_configuration_available': False,
                'assigned_user_consent_policies': [],
                'user_consent_allowed': False,
                'admin_consent_required': False,
                'high_privilege_apps': 0,
                'apps_with_graph_access': 0,
                'apps_with_mail_access': 0,
                'apps_with_files_access': 0,
                'unverified_publishers': 0
            }
            
            # Sign-in Logs (Sample - last 7 days)
            self.signin_logs = []
            self.signin_summary = {
                'total_signins_sampled': 0,
                'legacy_auth_attempts': 0,
                'mfa_required': 0,
                'mfa_success': 0,
                'mfa_failure': 0,
                'ca_success': 0,
                'ca_failure': 0,
                'failed_signins': 0,
                'risky_signins': 0
            }
            
            # Global Secure Access (Entra Internet Access)
            self.network_filtering_policies = []
            self.network_forwarding_profiles = []
            self.network_access_summary = {
                'status': 'Success',
                'error': None,
                'enabled': False,
                'total_filtering_policies': 0,
                'total_forwarding_profiles': 0,
                'web_filtering_enabled': False,
                'traffic_forwarding_enabled': False,
                'fqdn_rules_count': 0,
                'web_category_rules_count': 0,
                'm365_traffic_forwarding': False,
                'internet_traffic_forwarding': False
            }
            
            # Global Secure Access (Entra Private Access)
            self.private_access_connectors = []
            self.private_access_apps = []
            self.private_access_summary = {
                'status': 'Success',
                'error': None,
                'enabled': False,
                'total_connectors': 0,
                'active_connectors': 0,
                'total_apps': 0,
                'apps_with_quick_access': 0,
                'apps_with_per_app_access': 0
            }
            
            # Policy Read (Auth Policy, Consent Policies)
            self.authorization_policy = {}
            self.auth_policy_summary = {
                'guest_invite_setting': 'Unknown',
                'default_user_role_permissions': {},
                'allow_users_to_register_apps': False
            }
    
    client_obj = EntraClient()
    
    import sys
    from .spinner import _stdout_lock, get_timestamp
    
    # Independent collectors share stdout; emit complete lines and no simulated percentage.
    with _stdout_lock:
        console.detail(f'[{get_timestamp()}]   Entra data collection started.\n')
        sys.stdout.flush()
    
    try:
        # Phase 1 uses the shared pagination-aware REST client for every collection.
        # This avoids generated-SDK dependencies and ensures nextLink handling is uniform.
        phase1_tasks = {}

        try:
            phase1_tasks['app_signin_summary'] = _fetch_graph_collection_via_http(
                "/beta/reports/getAzureADApplicationSignInSummary(period='D30')"
            )
            phase1_tasks['service_principal_signin_activities'] = _fetch_graph_collection_via_http(
                "/beta/reports/servicePrincipalSignInActivities",
                params={'$top': '999'}
            )
            phase1_tasks['cross_tenant_policy'] = _fetch_graph_object_via_http(
                "/v1.0/policies/crossTenantAccessPolicy/default"
            )
            phase1_tasks['authorization_policy'] = _fetch_graph_object_via_http(
                "/v1.0/policies/authorizationPolicy"
            )
        except Exception:
            pass

        # Use one pagination-aware HTTP path for every collection that contributes to a
        # finding or workbook count so a first page can never be mistaken for the complete
        # tenant inventory.
        seven_days_ago = (datetime.utcnow() - timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%SZ')
        collection_requests = {
            'ca_policies': ("/v1.0/identity/conditionalAccess/policies", {'$top': '999'}, None),
            'auth_methods': ("/v1.0/reports/authenticationMethods/userRegistrationDetails", {'$top': '999'}, None),
            # Several Entra APIs reject oversized $top values instead of silently capping
            # them. Let Graph choose its documented default and follow every nextLink.
            'risky_users': ("/v1.0/identityProtection/riskyUsers", {'$top': '500'}, None),
            'risk_detections': ("/v1.0/identityProtection/riskDetections", {}, None),
            'role_definitions': ("/v1.0/roleManagement/directory/roleDefinitions", {'$select': 'id,templateId,displayName,isBuiltIn'}, None),
            # v1.0 documents expansion of principal. Role names are resolved from the
            # separately paginated roleDefinitions collection.
            'role_assignments': ("/v1.0/roleManagement/directory/roleAssignments", {'$expand': 'principal'}, None),
            # Schedule list endpoints document select/filter/expand, not a client page size.
            # Use the service's paging and follow every returned nextLink.
            'role_eligibility_schedules': ("/v1.0/roleManagement/directory/roleEligibilitySchedules", {'$expand': 'principal,roleDefinition'}, None),
            'role_assignment_schedules': ("/v1.0/roleManagement/directory/roleAssignmentSchedules", {'$expand': 'principal,roleDefinition'}, None),
            'access_reviews': ("/v1.0/identityGovernance/accessReviews/definitions", {}, None),
            'managed_devices': ("/v1.0/deviceManagement/managedDevices", {'$top': '999'}, None),
            'compliance_policies': ("/v1.0/deviceManagement/deviceCompliancePolicies", {'$top': '999'}, None),
            'groups': ("/v1.0/groups", {'$filter': 'assignedLicenses/$count ne 0', '$count': 'true', '$select': 'id,displayName,groupTypes,assignedLicenses,licenseProcessingState', '$top': '999'}, {'ConsistencyLevel': 'eventual'}),
            'guests': ("/v1.0/users", {'$filter': "userType eq 'Guest'", '$select': 'id,displayName,userPrincipalName,createdDateTime,assignedLicenses', '$top': '999'}, None),
            'service_principals': ("/v1.0/servicePrincipals", {'$select': 'id,appId,displayName,publisherName,verifiedPublisher,appOwnerOrganizationId,servicePrincipalType,appRoles,oauth2PermissionScopes', '$top': '999'}, None),
            'oauth_grants': ("/v1.0/oauth2PermissionGrants", {'$top': '999'}, None),
            'consent_policies': ("/v1.0/policies/permissionGrantPolicies", {'$top': '999'}, None),
            'signin_logs': ("/v1.0/auditLogs/signIns", {'$filter': f'createdDateTime ge {seven_days_ago}', '$top': '999'}, None),
        }
        for task_name, (path, params, headers) in collection_requests.items():
            phase1_tasks[task_name] = _fetch_graph_collection_via_http(path, params=params, headers=headers)
        
        # Execute all phase 1 tasks in parallel
        phase1_results = {}
        if phase1_tasks:
            results = await asyncio.gather(*phase1_tasks.values(), return_exceptions=True)
            phase1_results = dict(zip(phase1_tasks.keys(), results))
            # A task counts as read only when it neither raised nor returned nothing.
            for _task_name, _task_result in phase1_results.items():
                if isinstance(_task_result, dict) and 'available' in _task_result:
                    _reason = _task_result.get('error', '') or (f"HTTP {_task_result.get('status_code')}" if _task_result.get('status_code') else '')
                    if _task_result.get('status_code') == 403 and _task_name in {'risky_users', 'risk_detections'}:
                        _service_reason = _reason
                        _required_permission = (
                            'IdentityRiskyUser.Read.All' if _task_name == 'risky_users'
                            else 'IdentityRiskEvent.Read.All'
                        )
                        if _required_permission in _get_graph_token_roles():
                            _reason = (
                                f"Microsoft Graph returned HTTP 403 even though {_required_permission} is present. "
                                "Full Identity Protection risk data requires Microsoft Entra ID P2 or another "
                                "qualifying Entra entitlement; Microsoft Entra ID P1 provides only limited portal risk data. "
                                "Verify licensing and the service response; HTTP 403 alone does not establish the cause."
                            )
                        else:
                            _reason = (
                                f"Microsoft Graph returned HTTP 403. Grant the application permission "
                                f"{_required_permission}, provide tenant-wide admin consent, and rerun."
                            )
                        if _service_reason and _service_reason != 'HTTP 403':
                            _reason += ' Service response: ' + _service_reason
                        with _stdout_lock:
                            console.status(f"   Entra: {_task_name.replace('_', ' ').title()} unavailable — {_reason}", tone='warning')
                    _status = {
                        'availability_status': _task_result.get('availability_status', 'available' if _task_result.get('available') else 'unavailable'),
                        'available': bool(_task_result.get('available')),
                        'records_collected': int(_task_result.get('records_collected', len(_extract_response_items(_task_result))) or 0),
                        'pages_collected': int(_task_result.get('pages_collected', 1 if _task_result.get('available') else 0) or 0),
                        'truncated': bool(_task_result.get('truncated')),
                        'reason': _reason,
                    }
                    client_obj.collection_status[_task_name] = _status
                    client_obj.data_sources[_task_name] = _status['available'] and not _status['truncated']
                else:
                    _available = bool(_task_result is not None and not isinstance(_task_result, Exception))
                    client_obj.data_sources[_task_name] = _available
                    client_obj.collection_status[_task_name] = {
                        'availability_status': 'available' if _available else 'unavailable',
                        'available': _available,
                        'records_collected': len(_extract_response_items(_task_result)) if _available else 0,
                        'pages_collected': 1 if _available else 0,
                        'truncated': False,
                        'reason': '' if _available else type(_task_result).__name__ if isinstance(_task_result, Exception) else 'No response',
                    }
        
        # Process Conditional Access Policies
        ca_response = phase1_results.get('ca_policies')
        if ca_response and not isinstance(ca_response, Exception):
            try:
                # SDK returns collection response objects with .value property
                policies = _extract_response_items(ca_response)
                client_obj.ca_policies = policies
                
                # Analyze policies
                for policy in policies:
                    client_obj.ca_summary['total'] += 1
                    
                    state = (_get_attr(policy, 'state') or '').lower()
                    if state == 'enabled':
                        client_obj.ca_summary['enabled'] += 1
                    elif state == 'disabled':
                        client_obj.ca_summary['disabled'] += 1
                    elif state == 'enabledForReportingButNotEnforced' or state == 'enabled_for_reporting_but_not_enforced':
                        client_obj.ca_summary['report_only'] += 1
                    
                    # Check grant controls
                    grant_controls = _get_attr(policy, 'grantControls')
                    if grant_controls:
                        built_in_controls = _get_attr(grant_controls, 'builtInControls', []) or []
                        
                        if 'mfa' in built_in_controls:
                            client_obj.ca_summary['require_mfa'] += 1
                        if 'compliantDevice' in built_in_controls:
                            client_obj.ca_summary['require_compliant_device'] += 1
                        if 'domainJoinedDevice' in built_in_controls or 'approvedApplication' in built_in_controls:
                            client_obj.ca_summary['require_managed_device'] += 1
                    
                    # Check conditions
                    conditions = _get_attr(policy, 'conditions')
                    if conditions:
                        # Applications targeted
                        apps = _get_attr(conditions, 'applications')
                        if apps:
                            include_apps = _get_attr(apps, 'includeApplications', []) or []
                            if 'All' in include_apps:
                                client_obj.ca_summary['target_all_apps'] += 1
                            if '00000003-0000-0ff1-ce00-000000000000' in include_apps:  # Office 365
                                client_obj.ca_summary['target_m365_apps'] += 1
                        
                        # Client app types (legacy auth blocking)
                        client_app_types = _get_attr(conditions, 'clientAppTypes', []) or []
                        if client_app_types and 'exchangeActiveSync' not in client_app_types and 'other' not in client_app_types:
                            client_obj.ca_summary['block_legacy_auth'] += 1
                        
                        # Risk-based policies
                        user_risk_levels = _get_attr(conditions, 'userRiskLevels', []) or []
                        signin_risk_levels = _get_attr(conditions, 'signInRiskLevels', []) or []
                        if user_risk_levels:
                            client_obj.ca_summary['user_risk_based'] += 1
                            client_obj.risk_summary['user_risk_policy_exists'] = True
                        if signin_risk_levels:
                            client_obj.ca_summary['signin_risk_based'] += 1
                            client_obj.risk_summary['signin_risk_policy_exists'] = True
                    
                        # Location-based
                        locations = _get_attr(conditions, 'locations')
                        if locations:
                            client_obj.ca_summary['location_based'] += 1
                
                # Check if CA requires compliance
                if client_obj.ca_summary['require_compliant_device'] > 0:
                    client_obj.device_summary['ca_requires_compliance'] = True
                
            except Exception as e:
                with _stdout_lock:
                    console.status(f"Entra: CA policies parse error: {e}", tone='error')
        
        # Process Authentication Methods Registration
        auth_response = phase1_results.get('auth_methods')
        if auth_response and not isinstance(auth_response, Exception):
            try:
                # SDK returns collection response objects with .value property
                registrations = _extract_response_items(auth_response)
                client_obj.auth_methods_registration = registrations
                
                client_obj.auth_summary['total_users'] = len(registrations)
                
                for user_reg in registrations:
                    # The pagination-aware HTTP path yields camelCase dictionaries while
                    # older SDK fallbacks yield snake_case model objects.
                    is_mfa_registered = bool(_get_attr(user_reg, 'isMfaRegistered', False))
                    is_mfa_capable = bool(_get_attr(user_reg, 'isMfaCapable', False))
                    
                    if is_mfa_registered:
                        client_obj.auth_summary['mfa_registered'] += 1
                    if is_mfa_capable:
                        client_obj.auth_summary['mfa_capable'] += 1
                    
                    methods = _get_attr(user_reg, 'methodsRegistered', []) or []
                    
                    # Check passwordless methods
                    passwordless_methods = ['microsoftAuthenticator', 'fido2', 'windowsHello']
                    if any(m in methods for m in passwordless_methods):
                        client_obj.auth_summary['passwordless_enabled'] += 1
                    
                    # Count by method type
                    for method in methods:
                        if method in client_obj.auth_summary['methods']:
                            client_obj.auth_summary['methods'][method] += 1
                
                # Calculate rates
                total = client_obj.auth_summary['total_users']
                if total > 0:
                    client_obj.auth_summary['mfa_registration_rate'] = int((client_obj.auth_summary['mfa_registered'] / total) * 100)
                    client_obj.auth_summary['passwordless_adoption_rate'] = int((client_obj.auth_summary['passwordless_enabled'] / total) * 100)
                
            except Exception as e:
                with _stdout_lock:
                    console.status(f"Entra: Auth methods parse error: {e}", tone='error')
        
        # Process Risky Users
        risky_response = phase1_results.get('risky_users')
        if risky_response and not isinstance(risky_response, Exception):
            try:
                risky_users = _extract_response_items(risky_response)
                client_obj.risky_users = risky_users
                
                client_obj.risk_summary['risky_users_total'] = len(risky_users)
                
                for user in risky_users:
                    risk_level = str(_get_attr(user, 'riskLevel', '') or '').lower()
                    risk_state = str(_get_attr(user, 'riskState', '') or '').lower()
                    
                    if risk_level == 'high':
                        client_obj.risk_summary['risky_users_high'] += 1
                    elif risk_level == 'medium':
                        client_obj.risk_summary['risky_users_medium'] += 1
                    elif risk_level == 'low':
                        client_obj.risk_summary['risky_users_low'] += 1
                    
                    if risk_state == 'confirmedcompromised':
                        client_obj.risk_summary['confirmed_compromised'] += 1
                    elif risk_state == 'atrisk':
                        client_obj.risk_summary['at_risk'] += 1
                    elif risk_state == 'remediated':
                        client_obj.risk_summary['remediated'] += 1
                    elif risk_state == 'dismissed':
                        client_obj.risk_summary['dismissed'] += 1
                
            except Exception as e:
                with _stdout_lock:
                    console.status(f"Entra: Risky users parse error: {e}", tone='error')
        
        # Process Risk Detections
        risk_det_response = phase1_results.get('risk_detections')
        if risk_det_response and not isinstance(risk_det_response, Exception):
            try:
                # SDK returns collection response objects with .value property
                detections = _extract_response_items(risk_det_response)
                client_obj.risk_detections = detections
                
                client_obj.risk_summary['risk_detections_total'] = len(detections)
                
                for detection in detections:
                    risk_level = (_get_attr(detection, 'riskLevel') or '').lower()
                    if risk_level == 'high':
                        client_obj.risk_summary['risk_detections_high'] += 1
                
            except Exception as e:
                with _stdout_lock:
                    console.status(f"Entra: Risk detections parse error: {e}", tone='error')
        
        # Process role definitions before assignments so built-in role template IDs and
        # human-readable role names are available to both scoring and workbook evidence.
        role_definition_by_id = {}
        role_defs_response = phase1_results.get('role_definitions')
        if role_defs_response and not isinstance(role_defs_response, Exception):
            client_obj.role_definitions = _extract_response_items(role_defs_response)
            for definition in client_obj.role_definitions:
                definition_id = str(_get_attr(definition, 'id', '') or '').lower()
                if definition_id:
                    role_definition_by_id[definition_id] = definition

        # Process Role Assignments (PIM - Active/Permanent)
        role_assign_response = phase1_results.get('role_assignments')
        if role_assign_response and not isinstance(role_assign_response, Exception):
            try:
                assignments = _extract_response_items(role_assign_response)
                client_obj.role_assignments = assignments
                
                client_obj.pim_summary['total_active_assignments'] = len(assignments)
                
            except Exception as e:
                with _stdout_lock:
                    console.status(f"Entra: Role assignments parse error: {e}", tone='error')
        
        # Process Role Eligibility Schedules (PIM - Eligible)
        role_elig_response = phase1_results.get('role_eligibility_schedules')
        if role_elig_response and not isinstance(role_elig_response, Exception):
            try:
                schedules = _extract_response_items(role_elig_response)
                client_obj.role_eligibility_schedules = schedules
                
                client_obj.pim_summary['total_eligible_assignments'] = len(schedules)
                client_obj.pim_summary['eligible_assignments'] = len(schedules)
                
            except Exception as e:
                with _stdout_lock:
                    console.status(f"Entra: Role eligibility parse error: {e}", tone='error')
        
        # Process Role Assignment Schedules (PIM - Time-bound active)
        role_sched_response = phase1_results.get('role_assignment_schedules')
        if role_sched_response and not isinstance(role_sched_response, Exception):
            try:
                schedules = _extract_response_items(role_sched_response)
                client_obj.role_assignment_schedules = schedules

                global_admin_role = '62e90394-69f5-4237-9190-012177145e10'
                for schedule in schedules:
                    schedule_info = _get_attr(schedule, 'scheduleInfo', {}) or {}
                    expiration = _get_attr(schedule_info, 'expiration', {}) or {}
                    expiration_type = str(_get_attr(expiration, 'type', '') or '').lower()
                    end_date = _get_attr(expiration, 'endDateTime', None)
                    role_def_id = str(_get_attr(schedule, 'roleDefinitionId', '') or '').lower()
                    role_definition = _get_attr(schedule, 'roleDefinition', {}) or role_definition_by_id.get(role_def_id, {})
                    role_template_id = str(_get_attr(role_definition, 'templateId', '') or '').lower()
                    role_name = str(_get_attr(role_definition, 'displayName', '') or '').lower()

                    if 'noexpiration' in expiration_type.replace('_', ''):
                        client_obj.pim_summary['permanent_assignments'] += 1
                        if role_template_id == global_admin_role or role_def_id == global_admin_role or role_name == 'global administrator':
                            client_obj.pim_summary['permanent_global_admins'] += 1
                    elif end_date or 'after' in expiration_type:
                        client_obj.pim_summary['total_time_bound_assignments'] += 1
                    else:
                        client_obj.pim_summary['unclassified_active_assignments'] += 1
                
            except Exception as e:
                with _stdout_lock:
                    console.status(f"Entra: Role schedules parse error: {e}", tone='error')
        
        # Calculate PIM metrics
        if client_obj.pim_summary['total_eligible_assignments'] > 0:
            client_obj.pim_summary['pim_enabled_roles'] = client_obj.pim_summary['total_eligible_assignments']
        
        # Process Access Reviews
        reviews_response = phase1_results.get('access_reviews')
        if reviews_response and not isinstance(reviews_response, Exception):
            try:
                reviews = _extract_response_items(reviews_response)
                client_obj.access_reviews = reviews
                
                client_obj.access_review_summary['total_definitions'] = len(reviews)
                
                for review in reviews:
                    status = (_get_attr(review, 'status') or '').lower()
                    if status in ['inprogress', 'notstarted']:
                        client_obj.access_review_summary['active_reviews'] += 1
                    
                    # Check scope type
                    scope = _get_attr(review, 'scope')
                    query = (_get_attr(scope, 'query') or '').lower() if scope else ''
                    
                    if 'group' in query or 'groupmember' in query:
                        client_obj.access_review_summary['group_membership_reviews'] += 1
                    if 'roleassignment' in query or 'role' in query:
                        client_obj.access_review_summary['role_assignment_reviews'] += 1
                    if 'guest' in query or 'usertype' in query:
                        client_obj.access_review_summary['guest_user_reviews'] += 1
                    
                    # Check recurrence
                    settings = _get_attr(review, 'settings', None)
                    recurrence = _get_attr(settings, 'recurrence', None) if settings else None
                    if recurrence and _get_attr(recurrence, 'pattern', None):
                        client_obj.access_review_summary['recurring_reviews'] += 1
                    else:
                        client_obj.access_review_summary['one_time_reviews'] += 1
                
            except Exception as e:
                with _stdout_lock:
                    console.status(f"Entra: Access reviews parse error: {e}", tone='error')
        
        # Process Managed Devices
        devices_response = phase1_results.get('managed_devices')
        if devices_response and not isinstance(devices_response, Exception):
            try:
                devices = _extract_response_items(devices_response)
                client_obj.managed_devices = devices
                
                client_obj.device_summary['total_managed'] = len(devices)
                
                for device in devices:
                    compliance = str(_get_attr(device, 'complianceState', '') or '').lower()
                    if compliance == 'compliant':
                        client_obj.device_summary['compliant'] += 1
                    elif compliance == 'noncompliant':
                        client_obj.device_summary['non_compliant'] += 1
                    elif compliance == 'ingraceperiod':
                        client_obj.device_summary['in_grace_period'] += 1
                    elif compliance == 'error':
                        client_obj.device_summary['error'] += 1
                    
                    ownership = str(_get_attr(device, 'managedDeviceOwnerType', '') or '').lower()
                    if ownership == 'company':
                        client_obj.device_summary['corporate_owned'] += 1
                    elif ownership == 'personal':
                        client_obj.device_summary['personal_byod'] += 1
                    
                    os = str(_get_attr(device, 'operatingSystem', '') or '').lower()
                    if 'windows' in os:
                        client_obj.device_summary['windows'] += 1
                    elif 'ios' in os:
                        client_obj.device_summary['ios'] += 1
                    elif 'android' in os:
                        client_obj.device_summary['android'] += 1
                    elif 'mac' in os:
                        client_obj.device_summary['macos'] += 1
                
            except Exception as e:
                with _stdout_lock:
                    console.status(f"Entra: Devices parse error: {e}", tone='error')
        
        # Process Compliance Policies
        compliance_response = phase1_results.get('compliance_policies')
        if compliance_response and not isinstance(compliance_response, Exception):
            try:
                # SDK returns collection response objects with .value property
                policies = _extract_response_items(compliance_response)
                client_obj.compliance_policies = policies
                
                client_obj.device_summary['compliance_policies_total'] = len(policies)
                
            except Exception as e:
                with _stdout_lock:
                    console.status(f"Entra: Compliance policies parse error: {e}", tone='error')
        
        # Process Groups with Licenses
        groups_response = phase1_results.get('groups')
        if groups_response and not isinstance(groups_response, Exception):
            try:
                # SDK returns collection response objects with .value property
                groups = _extract_response_items(groups_response)
                client_obj.groups_with_licenses = groups
                
                client_obj.group_licensing_summary['total_groups_with_licenses'] = len(groups)
                
                # Copilot-related SKU part numbers
                copilot_sku_keywords = ['COPILOT', 'M365_COPILOT', 'MICROSOFT_365_COPILOT']
                
                for group in groups:
                    # Check for errors
                    license_state = _get_attr(group, 'licenseProcessingState', {}) or {}
                    if license_state and _get_attr(license_state, 'state', '') == 'ProcessingFailed':
                        client_obj.group_licensing_summary['groups_with_errors'] += 1
                    
                    # Check group type
                    group_types = _get_attr(group, 'groupTypes', []) or []
                    if 'DynamicMembership' in group_types:
                        client_obj.group_licensing_summary['dynamic_groups'] += 1
                    
                    # Check for Copilot licenses (approximate - would need SKU lookup)
                    display_name = str(_get_attr(group, 'displayName', '') or '').upper()
                    if any(keyword in display_name for keyword in copilot_sku_keywords):
                        client_obj.group_licensing_summary['copilot_license_groups'] += 1
                
            except Exception as e:
                with _stdout_lock:
                    console.status(f"Entra: Groups parse error: {e}", tone='error')
        
        # Process Guest Users
        guests_response = phase1_results.get('guests')
        if guests_response and not isinstance(guests_response, Exception):
            try:
                # SDK returns collection response objects with .value property
                guests = _extract_response_items(guests_response)
                client_obj.guest_users = guests
                
                client_obj.b2b_summary['total_guests'] = len(guests)
                
                # Count guests with licenses
                for guest in guests:
                    licenses = _get_attr(guest, 'assignedLicenses', []) or []
                    if licenses and len(licenses) > 0:
                        client_obj.b2b_summary['guests_with_licenses'] += 1
                
            except Exception as e:
                with _stdout_lock:
                    console.status(f"Entra: Guest users parse error: {e}", tone='error')
        
        # Process Cross-Tenant Access Policy
        cross_tenant_response = phase1_results.get('cross_tenant_policy')
        if cross_tenant_response and not isinstance(cross_tenant_response, Exception):
            try:
                cross_tenant_policy = (
                    cross_tenant_response.get('object')
                    if isinstance(cross_tenant_response, dict) and 'object' in cross_tenant_response
                    else cross_tenant_response
                )
                client_obj.cross_tenant_access_policy = cross_tenant_policy
                
                client_obj.b2b_summary['cross_tenant_access_configured'] = True
                
                # Get default settings
                default = _get_attr(cross_tenant_policy, 'default', {}) or {}
                client_obj.b2b_summary['default_settings'] = default
                
            except Exception as e:
                with _stdout_lock:
                    console.status(f"Entra: Cross-tenant policy parse error: {e}", tone='error')
        
        # Process Service Principals. OAuth grant clientId/resourceId values are service-
        # principal object IDs, not application IDs, so retain an object-ID index for joins.
        service_principal_by_id = {}
        sp_response = phase1_results.get('service_principals')
        if sp_response and not isinstance(sp_response, Exception):
            try:
                service_principals = _extract_response_items(sp_response)
                client_obj.service_principals = service_principals
                client_obj.consent_summary['total_apps'] = len(service_principals)
                for sp in service_principals:
                    sp_id = str(_get_attr(sp, 'id', '') or '').lower()
                    if sp_id:
                        service_principal_by_id[sp_id] = sp
            except Exception as e:
                with _stdout_lock:
                    console.status(f"Entra: Service principals parse error: {e}", tone='error')

        # Process OAuth Permission Grants and count unique client applications rather than
        # grant rows. Publisher verification is evaluated only for apps with an actual grant;
        # a blank publisher on an unused/local service principal is not a risk finding.
        oauth_response = phase1_results.get('oauth_grants')
        if oauth_response and not isinstance(oauth_response, Exception):
            try:
                grants = _extract_response_items(oauth_response)
                client_obj.oauth_permission_grants = grants

                graph_app_id = '00000003-0000-0000-c000-000000000000'
                delegated_clients = set()
                graph_clients = set()
                mail_clients = set()
                files_clients = set()
                high_privilege_clients = set()

                for grant in grants:
                    client_id = str(_get_attr(grant, 'clientId', '') or '').lower()
                    resource_id = str(_get_attr(grant, 'resourceId', '') or '').lower()
                    scope = str(_get_attr(grant, 'scope', '') or '').lower()
                    if not client_id:
                        continue
                    delegated_clients.add(client_id)

                    resource_sp = service_principal_by_id.get(resource_id)
                    resource_app_id = str(_get_attr(resource_sp, 'appId', '') or '').lower()
                    if resource_app_id == graph_app_id:
                        graph_clients.add(client_id)
                    if 'mail' in scope:
                        mail_clients.add(client_id)
                    if 'files' in scope or 'sharepoint' in scope:
                        files_clients.add(client_id)
                    if any(marker in scope for marker in ('mail.readwrite', 'files.readwrite', 'directory.readwrite')):
                        high_privilege_clients.add(client_id)

                unverified_external_clients = set()
                tenant_id_normalized = str(tenant_id or '').lower()
                for client_id in delegated_clients:
                    sp = service_principal_by_id.get(client_id)
                    if not sp:
                        continue
                    verified = _get_attr(sp, 'verifiedPublisher', None)
                    verified_id = str(_get_attr(verified, 'verifiedPublisherId', '') or '')
                    verified_name = str(_get_attr(verified, 'displayName', '') or '')
                    owner_tenant = str(_get_attr(sp, 'appOwnerOrganizationId', '') or '').lower()
                    publisher = str(_get_attr(sp, 'publisherName', '') or '')
                    principal_type = str(_get_attr(sp, 'servicePrincipalType', '') or '').lower()
                    app_id = str(_get_attr(sp, 'appId', '') or '').lower()
                    is_internal = bool(tenant_id_normalized and owner_tenant == tenant_id_normalized)
                    is_microsoft = (
                        owner_tenant in MICROSOFT_OWNER_TENANT_IDS
                        or app_id in MICROSOFT_FIRST_PARTY_APP_IDS
                    )
                    is_managed_identity = principal_type == 'managedidentity'
                    if not (verified_id or verified_name or is_internal or is_microsoft or is_managed_identity):
                        unverified_external_clients.add(client_id)

                client_obj.consent_summary['apps_with_delegated_permissions'] = len(delegated_clients)
                client_obj.consent_summary['apps_with_graph_access'] = len(graph_clients)
                client_obj.consent_summary['apps_with_mail_access'] = len(mail_clients)
                client_obj.consent_summary['apps_with_files_access'] = len(files_clients)
                client_obj.consent_summary['high_privilege_apps'] = len(high_privilege_clients)
                client_obj.consent_summary['unverified_publishers'] = len(unverified_external_clients)
            except Exception as e:
                with _stdout_lock:
                    console.status(f"Entra: OAuth grants parse error: {e}", tone='error')
        
        # Process Permission Grant Policies
        consent_pol_response = phase1_results.get('consent_policies')
        if consent_pol_response and not isinstance(consent_pol_response, Exception):
            try:
                # SDK returns collection response objects with .value property
                policies = _extract_response_items(consent_pol_response)
                client_obj.permission_grant_policies = policies
                
                # These are policy definitions, not proof that a policy is assigned to the
                # default user role. The effective consent boundary is read from the tenant
                # authorization policy below.
                
            except Exception as e:
                with _stdout_lock:
                    console.status(f"Entra: Consent policies parse error: {e}", tone='error')

        # Process application sign-in summary (30-day activity)
        app_signin_summary = phase1_results.get('app_signin_summary')
        if app_signin_summary and not isinstance(app_signin_summary, Exception):
            try:
                if app_signin_summary.get('available'):
                    summaries = app_signin_summary.get('value', [])
                    client_obj.application_signin_summary = summaries
                    activity_by_app = {}

                    for item in summaries:
                        app_id = (_get_attr(item, 'appId') or _get_attr(item, 'id') or '').lower()
                        if not app_id:
                            continue

                        successful = int(_get_attr(item, 'successfulSignInCount', 0) or 0)
                        failed = int(_get_attr(item, 'failedSignInCount', 0) or 0)
                        interrupted = int(_get_attr(item, 'interruptedSignInCount', 0) or 0)

                        existing = activity_by_app.setdefault(app_id, {
                            'activity_count': 0,
                            'last_activity': '',
                        })
                        existing['activity_count'] += successful + failed + interrupted

                    client_obj.app_activity_summary['available'] = True
                    client_obj.app_activity_summary['by_app'] = activity_by_app
                else:
                    client_obj.app_activity_summary['reason'] = f"Application activity unavailable (HTTP {app_signin_summary.get('status_code', 'unknown')})"
            except Exception as e:
                client_obj.app_activity_summary['reason'] = f"Application activity parse error: {e}"

        # Process service principal sign-in activity (last activity)
        sp_signin_activities = phase1_results.get('service_principal_signin_activities')
        if sp_signin_activities and not isinstance(sp_signin_activities, Exception):
            try:
                if sp_signin_activities.get('available'):
                    activities = sp_signin_activities.get('value', [])
                    client_obj.service_principal_signin_activities = activities

                    existing_index = client_obj.app_activity_summary.setdefault('by_app', {})
                    for item in activities:
                        app_id = (_get_attr(item, 'appId') or '').lower()
                        if not app_id:
                            continue

                        last_signin = _get_attr(_get_attr(item, 'lastSignInActivity', {}), 'lastSignInDateTime', '')
                        if not last_signin:
                            for field_name in [
                                'delegatedClientSignInActivity',
                                'delegatedResourceSignInActivity',
                                'applicationAuthenticationClientSignInActivity',
                                'applicationAuthenticationResourceSignInActivity',
                            ]:
                                candidate = _get_attr(_get_attr(item, field_name, {}), 'lastSignInDateTime', '')
                                if candidate:
                                    last_signin = candidate
                                    break

                        existing = existing_index.setdefault(app_id, {
                            'activity_count': 0,
                            'last_activity': '',
                        })

                        if last_signin:
                            current_last = existing.get('last_activity', '')
                            if not current_last or str(last_signin) > str(current_last):
                                existing['last_activity'] = str(last_signin)

                    client_obj.app_activity_summary['available'] = bool(existing_index)
                elif not client_obj.app_activity_summary.get('reason'):
                    client_obj.app_activity_summary['reason'] = f"Service principal activity unavailable (HTTP {sp_signin_activities.get('status_code', 'unknown')})"
            except Exception as e:
                if not client_obj.app_activity_summary.get('reason'):
                    client_obj.app_activity_summary['reason'] = f"Service principal activity parse error: {e}"

        # Process Authorization Policy
        auth_pol_response = phase1_results.get('authorization_policy')
        if auth_pol_response and not isinstance(auth_pol_response, Exception):
            try:
                if isinstance(auth_pol_response, dict) and 'object' in auth_pol_response:
                    auth_pol_response = auth_pol_response.get('object')
                # Authorization policy may return single object or collection
                if hasattr(auth_pol_response, 'value'):
                    policies = auth_pol_response.value or []
                    policy = policies[0] if policies else None
                else:
                    policy = auth_pol_response
                
                if policy:
                    _apply_authorization_policy(client_obj, policy)
                
            except Exception as e:
                with _stdout_lock:
                    console.status(f"Entra: Auth policy parse error: {e}", tone='error')
        
        # Process Sign-in Logs
        signin_response = phase1_results.get('signin_logs')
        if signin_response and not isinstance(signin_response, Exception):
            try:
                signins = _extract_response_items(signin_response)
                client_obj.signin_logs = signins
                
                client_obj.signin_summary['total_signins_sampled'] = len(signins)
                
                for signin in signins:
                    client_app = str(_get_attr(signin, 'clientAppUsed', '') or '').lower()
                    signin_status = _get_attr(signin, 'status', {}) or {}
                    error_code = _get_attr(signin_status, 'errorCode', 0) or 0
                    
                    # Legacy auth detection
                    legacy_apps = ['pop', 'imap', 'smtp', 'activesync', 'other clients', 'exchange web services']
                    if any(app in client_app for app in legacy_apps):
                        client_obj.signin_summary['legacy_auth_attempts'] += 1
                    
                    # MFA
                    auth_details = _get_attr(signin, 'authenticationDetails', []) or []
                    if auth_details:
                        for detail in auth_details:
                            if _get_attr(detail, 'authenticationMethod', '') == 'MFA':
                                client_obj.signin_summary['mfa_required'] += 1
                                if _get_attr(detail, 'succeeded', False):
                                    client_obj.signin_summary['mfa_success'] += 1
                                else:
                                    client_obj.signin_summary['mfa_failure'] += 1
                    
                    # CA status
                    ca_status = str(_get_attr(signin, 'conditionalAccessStatus', '') or '').lower()
                    if ca_status == 'success':
                        client_obj.signin_summary['ca_success'] += 1
                    elif ca_status == 'failure':
                        client_obj.signin_summary['ca_failure'] += 1
                    
                    # Failed sign-ins
                    if error_code != 0:
                        client_obj.signin_summary['failed_signins'] += 1
                    
                    # Risky sign-ins
                    risk_level = str(_get_attr(signin, 'riskLevelDuringSignIn', '') or '').lower()
                    if risk_level in ['high', 'medium']:
                        client_obj.signin_summary['risky_signins'] += 1
                
            except Exception as e:
                with _stdout_lock:
                    console.status(f"Entra: Sign-in logs parse error: {e}", tone='error')
        
        # ====================================================================
        # GLOBAL SECURE ACCESS (Entra Internet Access) - NetworkAccess API (Beta)
        # ====================================================================
        if preview_collectors not in {'network-access', 'all'}:
            not_selected = {
                'status': 'OptionalNotSelected',
                'error': 'Optional Network Access preview collector was not selected.',
            }
            client_obj.network_access_summary.update(not_selected)
            client_obj.private_access_summary.update(not_selected)
            if (client_obj.ca_summary['total'] > 0 or
                    client_obj.auth_summary['total_users'] > 0 or
                    client_obj.risk_summary['risky_users_total'] >= 0 or
                    client_obj.device_summary['total_managed'] >= 0):
                client_obj.available = True
            with _stdout_lock:
                console.detail(f'[{get_timestamp()}]   Entra data collection finished; see source coverage for completeness.\n')
                sys.stdout.flush()
            return client_obj
        try:
            # Get HTTP client for direct beta API access
            http_client = await _get_graph_http_client()
            
            try:
                # Filtering Policies (Web Content Filtering)
                filtering_response = await http_client.get('/beta/networkAccess/filteringPolicies')
                filtering_response.raise_for_status()
                filtering_data = filtering_response.json()
                
                if filtering_data and filtering_data.get('value'):
                    policies = filtering_data['value']
                    client_obj.network_filtering_policies = policies
                    client_obj.network_access_summary['total_filtering_policies'] = len(policies)
                    client_obj.network_access_summary['enabled'] = True
                    
                    # Count FQDN and web category rules
                    for policy in policies:
                        policy_rules = _get_attr(policy, 'policyRules', [])
                        if policy_rules:
                            for rule in policy_rules:
                                destinations = _get_attr(rule, 'destinations', [])
                                if destinations:
                                    for dest in destinations:
                                        dest_type = str(_get_attr(dest, '@odata.type', '')).lower()
                                        if 'fqdn' in dest_type:
                                            client_obj.network_access_summary['fqdn_rules_count'] += 1
                                        elif 'webcategory' in dest_type:
                                            client_obj.network_access_summary['web_category_rules_count'] += 1
                    
                    # Mark web filtering as enabled (policies exist)
                    client_obj.network_access_summary['web_filtering_enabled'] = True
                
                # Forwarding Profiles (Traffic Forwarding Configuration)
                forwarding_response = await http_client.get('/beta/networkAccess/forwardingProfiles')
                forwarding_response.raise_for_status()
                forwarding_data = forwarding_response.json()
                
                if forwarding_data and forwarding_data.get('value'):
                    profiles = forwarding_data['value']
                    client_obj.network_forwarding_profiles = profiles
                    client_obj.network_access_summary['total_forwarding_profiles'] = len(profiles)
                    client_obj.network_access_summary['enabled'] = True
                    
                    # Check if M365 or internet traffic forwarding is configured
                    for profile in profiles:
                        profile_name = profile.get('name', '').lower()
                        profile_state = profile.get('state', '').lower()
                        
                        if profile_state == 'enabled':
                            if 'microsoft365' in profile_name or 'm365' in profile_name:
                                client_obj.network_access_summary['m365_traffic_forwarding'] = True
                            elif 'internet' in profile_name:
                                client_obj.network_access_summary['internet_traffic_forwarding'] = True
                            
                            client_obj.network_access_summary['traffic_forwarding_enabled'] = True
            
            finally:
                # Always close the HTTP client
                await http_client.aclose()
            
            # ====================================================================
            # GLOBAL SECURE ACCESS - PRIVATE ACCESS (Entra Private Access)
            # ====================================================================
            # Get HTTP client for direct beta API access (reuse connection pattern)
            http_client = await _get_graph_http_client()
            
            try:
                # Remote Network Connectors
                connectors_response = await http_client.get('/beta/networkAccess/connectivity/remoteNetworks')
                connectors_response.raise_for_status()
                connectors_data = connectors_response.json()
                
                if connectors_data and connectors_data.get('value'):
                    connectors = connectors_data['value']
                    client_obj.private_access_connectors = connectors
                    client_obj.private_access_summary['total_connectors'] = len(connectors)
                    client_obj.private_access_summary['enabled'] = True
                    
                    # Count active connectors
                    active_count = sum(1 for c in connectors if c.get('connectivityState') == 'alive')
                    client_obj.private_access_summary['active_connectors'] = active_count
                    
                    with _stdout_lock:
                        console.detail(f"[{get_timestamp()}] ✅  Entra: {len(connectors)} Private Access connector(s) found ({active_count} active)")
                
                # Application Segments (Private Access Apps)
                try:
                    # Note: This endpoint may not be available in all tenants
                    apps_response = await http_client.get('/beta/networkAccess/connectivity/branches')
                    apps_response.raise_for_status()
                    apps_data = apps_response.json()
                    
                    if apps_data and apps_data.get('value'):
                        apps = apps_data['value']
                        client_obj.private_access_apps = apps
                        client_obj.private_access_summary['total_apps'] = len(apps)
                        client_obj.private_access_summary['enabled'] = True
                        
                        with _stdout_lock:
                            console.detail(f"[{get_timestamp()}] ✅  Entra: {len(apps)} Private Access app segment(s) configured")
                except httpx.HTTPStatusError:
                    # App segments endpoint may not be available
                    pass
            
            finally:
                # Always close the HTTP client
                await http_client.aclose()
        
        except httpx.HTTPStatusError as e:
            # HTTP error from beta API
            if e.response.status_code == 403:
                if 'NetworkAccess.Read.All' in _get_graph_token_roles():
                    detail = (
                        'Graph returned HTTP 403 even though NetworkAccess.Read.All is present; '
                        'Global Secure Access may not be onboarded or available in this tenant'
                    )
                    client_obj.network_access_summary['status'] = 'Unavailable'
                    client_obj.network_access_summary['error'] = detail
                    client_obj.private_access_summary['status'] = 'Unavailable'
                    client_obj.private_access_summary['error'] = detail
                    with _stdout_lock:
                        console.status(f"   Entra: Global Secure Access API is unavailable despite the required application permission; verify tenant onboarding and licensing", tone='warning')
                else:
                    client_obj.network_access_summary['status'] = 'PermissionDenied'
                    client_obj.network_access_summary['error'] = 'NetworkAccess.Read.All permission required'
                    client_obj.private_access_summary['status'] = 'PermissionDenied'
                    client_obj.private_access_summary['error'] = 'NetworkAccess.Read.All permission required'
                    with _stdout_lock:
                        console.status(f"   Entra: Global Secure Access API access denied (requires NetworkAccess.Read.All permission)", tone='warning')
            elif e.response.status_code == 404:
                client_obj.network_access_summary['status'] = 'NotLicensed'
                client_obj.network_access_summary['error'] = 'Entra Suite license required'
                client_obj.private_access_summary['status'] = 'NotLicensed'
                client_obj.private_access_summary['error'] = 'Entra Suite license required'
                with _stdout_lock:
                    console.status(f"   Entra: Global Secure Access not available (requires Entra Suite license)", tone='warning')
            else:
                client_obj.network_access_summary['status'] = 'Error'
                client_obj.network_access_summary['error'] = f'HTTP {e.response.status_code}'
                client_obj.private_access_summary['status'] = 'Error'
                client_obj.private_access_summary['error'] = f'HTTP {e.response.status_code}'
                with _stdout_lock:
                    console.status(f"Entra: Global Secure Access API error - HTTP {e.response.status_code}", tone='error')
        except Exception as e:
            client_obj.network_access_summary['status'] = 'Error'
            client_obj.network_access_summary['error'] = str(e)
            client_obj.private_access_summary['status'] = 'Error'
            client_obj.private_access_summary['error'] = str(e)
            with _stdout_lock:
                console.status(f"Entra: Global Secure Access data fetch failed - {str(e)}", tone='error')
        
        # Mark client as available if we got at least some data
        if (client_obj.ca_summary['total'] > 0 or 
            client_obj.auth_summary['total_users'] > 0 or 
            client_obj.risk_summary['risky_users_total'] >= 0 or
            client_obj.device_summary['total_managed'] >= 0):
            client_obj.available = True
        else:
            with _stdout_lock:
                console.status(f"Entra: Limited data available (check permissions)", tone='warning')
        
        with _stdout_lock:
            console.detail(f'[{get_timestamp()}]   Entra data collection finished; see source coverage for completeness.\n')
            sys.stdout.flush()
        
        return client_obj
        
    except HttpResponseError as e:
        with _stdout_lock:
            console.status(f'Entra: HTTP {e.status_code} - {e.message}', tone='error')
        return client_obj
    
    except Exception as e:
        with _stdout_lock:
            console.status(f"Entra: Unexpected error - {str(e)}", tone='error')
        return client_obj

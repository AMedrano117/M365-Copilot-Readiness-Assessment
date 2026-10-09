"""Named finding records for evidence drill-down, without invented identities.

The normalized fields supplement retained source rows.  Missing values are null,
and every contributing source row has a zero-based locator in assessment_sources.
No adapter refreshes a historical observation or performs network collection.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re

from .raw_evidence import safe_record


def _text(value):
    return str(value).strip() if value is not None else ''


def _norm(value):
    return re.sub(r'[^a-z0-9]', '', _text(value).lower())


def _value(record, *names):
    """Recognize source field spelling; never substitute unrelated attributes."""
    normalized = {_norm(key): value for key, value in (record or {}).items()}
    for name in names:
        value = normalized.get(_norm(name))
        if value is not None and value != '':
            return value
    return None


def _boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (str, int)):
        if str(value).lower() in {'true', 'yes', '1'}:
            return True
        if str(value).lower() in {'false', 'no', '0'}:
            return False
    return None


def _date(value):
    try:
        result = datetime.fromisoformat(_text(value).replace('Z', '+00:00'))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _rows(sources, *names):
    for name in dict.fromkeys(names):
        for dataset_index, dataset in enumerate(sources.get(name, []) or []):
            for record_index, record in enumerate(dataset.get('records', []) or []):
                if isinstance(record, dict):
                    yield record, {'dataset': name, 'dataset_index': dataset_index, 'record_index': record_index}


def _refs(*items):
    result = []
    for item in items:
        values = item if isinstance(item, list) else [item]
        for value in values:
            if value and value not in result:
                result.append(value)
    return result


def _record(fields, refs, *, native_id=None, derived=None, conflicts=None, qualifications=None):
    clean = safe_record(fields)
    derived = derived or {}
    conflicts = conflicts or {}
    status = {}
    for key, value in clean.items():
        if key in conflicts:
            status[key] = {'status': 'conflict', 'reason': conflicts[key]}
        elif value is None:
            status[key] = {'status': 'unavailable', 'reason': derived.get(key, 'This field was not retained in the contributing source records.')}
        elif key in derived:
            status[key] = {'status': 'derived', 'reason': derived[key]}
        else:
            status[key] = {'status': 'available', 'reason': 'Retained source attribute.'}
        if len({ref['dataset'] for ref in refs}) == 1:
            status[key]['source'] = refs[0]['dataset']
    identity = _text(native_id)
    if not identity:
        # This is a content identifier, not proof of entity identity.
        payload = json.dumps({'fields': clean, 'refs': refs}, sort_keys=True, default=str, ensure_ascii=False)
        identity = 'record-' + hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]
    result = {'record_id': identity, 'fields': clean, 'field_status': status, 'source_refs': refs}
    if qualifications:
        result['qualifications'] = list(dict.fromkeys(qualifications))
    return result


def _finish(records, record_type, selection, sources, names, limitations=None, reconciliation=None):
    notes = list(limitations or [])
    for name in dict.fromkeys(names):
        if name not in sources:
            continue
        for dataset in sources[name]:
            state = dataset.get('source', {})
            availability = state.get('availability_status')
            if availability not in {None, 'available', 'empty'} or state.get('truncated') or state.get('complete') is False:
                notes.append(f'{name}: retained records do not establish complete coverage ({availability or "unknown"}).')
            if state.get('reason'):
                notes.append(f'{name}: {state["reason"]}')
    if not records:
        notes.append('No matching named records were retained; aggregate counts cannot reconstruct missing records.')
    result = {'records': records, 'record_type': record_type, 'selection': selection,
              'limitations': list(dict.fromkeys(notes))}
    if reconciliation is not None:
        result['reconciliation'] = reconciliation
    return result


def _matches_user(row, identifier, upn):
    row_id = _value(row, 'id', 'userId')
    if identifier and row_id:
        return _text(identifier).casefold() == _text(row_id).casefold()
    row_upn = _value(row, 'userPrincipalName', 'upn', 'User Principal Name')
    return bool(upn and row_upn and _text(upn).casefold() == _text(row_upn).casefold())


def _latest(candidates):
    dated = [(value, ref) for value, ref in candidates if _date(value)]
    return max(dated, key=lambda pair: _date(pair[0])) if dated else (None, None)


def _mfa(sources):
    output = []
    users = list(_rows(sources, 'users', 'license_users', 'user_signin_activity'))
    signins = list(_rows(sources, 'signin_logs'))
    from .authentication_methods import reconcile_registration_population
    registrations = list(_rows(sources, 'auth_methods', 'auth_methods_registration'))
    population = reconcile_registration_population([row for row, _ in registrations], users=[row for row, _ in users])
    for entry in population['entries']:
        if entry['state'] != 'ExplicitlyNotRegistered':
            continue
        row = entry['record']
        registration_refs = [registrations[position-1][1] for position in entry['positions']]
        ref = registration_refs[0]
        identifier = _value(row, 'id', 'userId')
        upn = _value(row, 'userPrincipalName', 'upn')
        matches = [(user, user_ref) for user, user_ref in users if _matches_user(user, identifier, upn)]
        refs = registration_refs + [user_ref for _, user_ref in matches]
        display_name = _value(row, 'userDisplayName', 'displayName')
        if not display_name:
            display_name = next((_value(user, 'displayName') for user, _ in matches if _value(user, 'displayName')), None)
        licensed = _boolean(_value(row, 'isLicensed'))
        license_states = []
        for user, _ in matches:
            if isinstance(user.get('assignedLicenses'), list):
                license_states.append(bool(user['assignedLicenses']))
            elif _boolean(_value(user, 'isLicensed')) is not None:
                license_states.append(_boolean(_value(user, 'isLicensed')))
        conflicts = {}
        if licensed is None and license_states:
            licensed = license_states[0] if len(set(license_states)) == 1 else None
        if len(set(license_states + ([] if licensed is None else [licensed]))) > 1:
            conflicts['isLicensed'] = 'Retained user/license inventories disagree about license assignment.'
        history = []
        successful_history = []
        attempt_history = []
        for user, user_ref in matches:
            activity = user.get('signInActivity') or {}
            history.extend((activity.get(field), user_ref) for field in ('lastSuccessfulSignInDateTime', 'lastSignInDateTime'))
            successful_history.append((activity.get('lastSuccessfulSignInDateTime'), user_ref))
            attempt_history.append((activity.get('lastSignInDateTime'), user_ref))
        last, last_ref = _latest(history)
        basis = 'Retained user signInActivity' if last else None
        if not last:
            candidates = [(event.get('createdDateTime'), event_ref) for event, event_ref in signins
                          if _matches_user({'id': event.get('userId'), 'userPrincipalName': event.get('userPrincipalName')}, identifier, upn)]
            last, last_ref = _latest(candidates)
            if last:
                basis = 'Latest observed sign-in within the retained sign-in collection window; full-history last sign-in is unavailable.'
        refs = _refs(refs, last_ref)
        preference = _value(row, 'userPreferredMethodForSecondaryAuthentication')
        system_preferred = _boolean(_value(row, 'isSystemPreferredAuthenticationMethodEnabled'))
        default = _value(row, 'defaultMfaMethod')
        default_reason = None
        if default is None:
            if system_preferred is False and preference is not None:
                default = preference
                default_reason = 'User-preferred method is used only when the retained record explicitly disables system-preferred authentication.'
            else:
                default_reason = 'An effective default MFA method was not retained. User preference and system-preferred candidates do not establish the method selected for a sign-in.'
        last_successful, successful_ref = _latest(successful_history)
        last_attempt, attempt_ref = _latest(attempt_history)
        refs = _refs(refs, successful_ref, attempt_ref)
        fields = {'userId': identifier, 'upn': upn, 'displayName': display_name, 'isMfaRegistered': False,
                  'userType': row.get('userType'), 'accountEnabled': row.get('accountEnabled'),
                  'isMfaCapable': row.get('isMfaCapable'), 'isPasswordlessCapable': row.get('isPasswordlessCapable'),
                  'methodsRegistered': row.get('methodsRegistered'), 'lastUpdatedDateTime': row.get('lastUpdatedDateTime'),
                  'registrationState': entry['state'], 'exclusionReason': entry['exclusion_reason'],
                  'defaultMfaMethod': default,
                  'userPreferredMfaMethod': preference,
                  'systemPreferredAuthenticationEnabled': system_preferred,
                  'systemPreferredAuthenticationMethods': _value(row, 'systemPreferredAuthenticationMethods'),
                  'isAdmin': _boolean(_value(row, 'isAdmin')), 'isLicensed': licensed,
                  'lastSignIn': last, 'lastSignInBasis': basis,
                  'lastSuccessfulSignIn': last_successful, 'lastInteractiveSignInAttempt': last_attempt}
        derived = {'lastSignIn': basis or 'No user signInActivity or matching dated sign-in record was retained.',
                   'lastSignInBasis': 'Describes the retained observation window rather than full-history activity.'}
        if default_reason:
            derived['defaultMfaMethod'] = default_reason
        if license_states and _value(row, 'isLicensed') is None:
            derived['isLicensed'] = 'Derived from assignedLicenses on a user joined by object ID, or UPN where an object ID is unavailable.'
        output.append(_record(fields, refs, native_id=identifier, derived=derived, conflicts=conflicts,
                              qualifications=['MFA registration is distinct from policy enforcement and observed MFA behavior.']))
    return _finish(output, 'user_mfa_registration', 'Users explicitly reporting isMfaRegistered=false; unknown values are excluded.',
                   sources, ['auth_methods', 'users', 'license_users', 'user_signin_activity', 'signin_logs'],
                   reconciliation={'selected_user_records': len(output), 'registration_population': population['counts'],
                                   'known_denominator': population['denominator'], 'total_users': population['total']})


def _admin(sources, evaluation_timestamp=None, privileged_assessment=None):
    from .privileged_identity import build_privileged_assessment
    from .privileged_assignments import instant
    dates = [d.get('source', {}).get('collected_at') for datasets in sources.values() for d in datasets
             if instant(d.get('source', {}).get('collected_at')) is not None]
    stamp = evaluation_timestamp or (max(dates, key=instant) if dates else None)
    model = deepcopy(privileged_assessment) if privileged_assessment is not None else build_privileged_assessment(sources, evaluation_timestamp=stamp)
    selected = [a for a in model['assignments'] if a['temporal_state'] in {'ActivePermanent', 'DirectActiveDurationUnverified'}]
    for a in selected:
        matches = [(u, ref) for u, ref in _rows(sources, 'users') if u.get('id') == a['principal_id']]
        a['principal_display_reference'] = a['principal_display_reference'] or (matches[0][0].get('displayName') if len(matches) == 1 else None)
        a['upn'] = matches[0][0].get('userPrincipalName') if len(matches) == 1 else None
        a['evidence_refs'].extend(ref for _, ref in matches)
    records = [_record({'assignmentId': a['source_id'], 'principalId': a['principal_id'],
        'principalDisplayName': a['principal_display_reference'], 'upn': a.get('upn'),
        'principalType': a['principal_type'], 'roleDefinitionId': a['role_definition_id'],
        'roleName': a['role_name'], 'scope': a['directory_scope_id'], 'appScopeId': a['app_scope_id'],
        'assignmentType': 'Permanent Active' if a['temporal_state'] == 'ActivePermanent' else 'Active (duration unverified)',
        'temporalState': a['temporal_state'], 'activatedSince': a['start'], 'assignmentKey': a['assignment_key']},
        a['evidence_refs'], native_id=a['assignment_key'],
        derived={'assignmentType': 'Shared temporal classification at the recorded evaluation timestamp.'}) for a in selected]
    return _finish(records, 'standing_admin_assignment',
        'Shared current permanent or duration-unverified active assignments; future, expired and eligible grants excluded.',
        sources, ['role_assignments', 'role_assignment_schedules', 'role_eligibility_schedules'],
        model['limitations'], {'selected_assignment_records': len(records)})


def _risks(sources):
    output = []
    detections = list(_rows(sources, 'risk_detections'))
    users = list(_rows(sources, 'users'))
    for row, ref in _rows(sources, 'risky_users'):
        state = _norm(row.get('riskState'))
        level = _norm(row.get('riskLevel'))
        if state not in {'atrisk', 'confirmedcompromised'} and not (not state and level in {'high', 'medium'}):
            continue
        identifier = row.get('id') or row.get('userId')
        refs = [ref]
        matched = [(event, event_ref) for event, event_ref in detections
                   if identifier and _text(event.get('userId')).casefold() == _text(identifier).casefold()]
        dated = [(event, event_ref) for event, event_ref in matched
                 if _date(event.get('activityDateTime') or event.get('detectedDateTime'))]
        event, event_ref = max(dated, key=lambda pair: _date(pair[0].get('activityDateTime') or pair[0].get('detectedDateTime'))) if dated else ({}, None)
        refs = _refs(refs, [detection_ref for _, detection_ref in matched])
        upn = _value(row, 'userPrincipalName')
        display = _value(row, 'userDisplayName', 'displayName')
        for user, user_ref in users:
            if _matches_user(user, identifier, upn):
                upn = upn or _value(user, 'userPrincipalName')
                display = display or _value(user, 'displayName')
                refs = _refs(refs, user_ref)
        fields = {'userId': identifier, 'upn': upn, 'displayName': display,
                  'riskLevel': _value(row, 'riskLevel'), 'riskState': _value(row, 'riskState'),
                  'riskDetail': _value(row, 'riskDetail'),
                  'lastRiskyActivity': _value(row, 'lastRiskyActivity', 'lastRiskyActivityDateTime') or _value(event, 'activityDateTime'),
                  'location': _value(row, 'location') or _value(event, 'location'),
                  'riskLastUpdated': _value(row, 'riskLastUpdatedDateTime'), 'riskDetectionId': _value(event, 'id'),
                  'riskDetectionIds': [_value(detection, 'id') for detection, _ in matched if _value(detection, 'id')]}
        derived = {}
        if event_ref:
            derived.update({'lastRiskyActivity': 'Latest retained risk detection joined by stable user ID; risk-status update time is not activity time.',
                            'location': 'Location of the latest retained risk detection joined by stable user ID.'})
        output.append(_record(fields, refs, native_id=identifier, derived=derived,
                              qualifications=['Risk status is dated source evidence and should be checked before remediation.']))
    return _finish(output, 'risky_user', 'Users in atRisk or confirmedCompromised state; medium/high risk with an unavailable state remains qualified. Resolved/dismissed users are excluded.',
                   sources, ['risky_users', 'risk_detections', 'users'],
                   reconciliation={'selected_user_records': len(output)})


def _consent(sources):
    output = []
    policies = list(_rows(sources, 'consent_policies', 'permission_grant_policies'))
    assigned = []
    for authorization, auth_ref in _rows(sources, 'authorization_policy'):
        permissions = authorization.get('defaultUserRolePermissions') or {}
        for value in permissions.get('permissionGrantPoliciesAssigned', []) or []:
            if _text(value).lower().startswith('managepermissiongrantsforself.'):
                identifier = _text(value).split('.', 1)[1]
                if not any(identifier.casefold() == old[0].casefold() for old in assigned):
                    assigned.append((identifier, value, auth_ref))
    for identifier, assignment, auth_ref in assigned:
        matched = [(row, ref) for row, ref in policies if _text(row.get('id')).casefold() == identifier.casefold()]
        policy, policy_ref = matched[0] if matched else ({}, None)
        includes = policy.get('includes') if isinstance(policy.get('includes'), list) else None
        excludes = policy.get('excludes') if isinstance(policy.get('excludes'), list) else None
        verified = [_boolean(condition.get('clientApplicationsFromVerifiedPublisherOnly'))
                    for condition in includes or [] if isinstance(condition, dict)]
        requirement = False if False in verified else True if verified and all(value is True for value in verified) else None
        fields = {'policyId': identifier, 'displayName': _value(policy, 'displayName'),
                  'description': _value(policy, 'description'), 'includeConditions': includes,
                  'excludeConditions': excludes, 'publisherVerifiedRequired': requirement,
                  'scope': {'assignment': assignment, 'appliesTo': 'Default user role: consent for self'},
                  'definitionResolved': bool(matched)}
        output.append(_record(fields, _refs(auth_ref, policy_ref), native_id=identifier,
                              derived={'publisherVerifiedRequired': 'True only when every retained include condition explicitly requires a verified publisher; mixed or missing requirements remain qualified.',
                                       'definitionResolved': 'Assigned policy ID matched to a retained permissionGrantPolicy definition.'},
                              qualifications=['Assignments do not imply unrestricted user consent. Full nested include/exclude conditions define the approval boundary.']))
    return _finish(output, 'user_consent_policy', 'Permission-grant policies assigned to managePermissionGrantsForSelf in the authorization policy.',
                   sources, ['authorization_policy', 'consent_policies'],
                   reconciliation={'assigned_self_consent_policies': len(assigned), 'resolved_definitions': sum(row['fields']['definitionResolved'] for row in output)})


def _app_publisher(row, tenant_id):
    from .evidence_layer import MICROSOFT_FIRST_PARTY_APP_IDS, MICROSOFT_OWNER_TENANT_IDS
    owner = _text(row.get('appOwnerOrganizationId')).casefold()
    app_id = _text(row.get('appId')).casefold()
    verified = row.get('verifiedPublisher') or {}
    if owner and tenant_id and owner == _text(tenant_id).casefold():
        return None, 'Tenant-owned; publisher-verification requirement is not applicable.'
    if _norm(row.get('servicePrincipalType')) == 'managedidentity':
        return None, 'Managed identity; publisher-verification requirement is not applicable.'
    if owner in MICROSOFT_OWNER_TENANT_IDS or app_id in MICROSOFT_FIRST_PARTY_APP_IDS:
        return True, 'Microsoft first-party identity classified using retained owner tenant/application ID and the existing assessment allowlist.'
    if verified.get('verifiedPublisherId') or verified.get('displayName'):
        return True, 'Graph returned verifiedPublisher metadata.'
    if owner:
        return False, 'External owner tenant returned without verifiedPublisher metadata.'
    return None, 'Publisher metadata is insufficient to establish verification.'


def _apps(sources, tenant_id):
    from .evidence_layer import HIGH_PRIVILEGE_SCOPE_MARKERS
    principals = list(_rows(sources, 'service_principals'))
    users = list(_rows(sources, 'users'))
    principal_index = {_text(row.get('id')).casefold(): (row, ref) for row, ref in principals if row.get('id')}
    grants = []
    for grant, ref in _rows(sources, 'oauth_grants', 'oauth_permission_grants'):
        grants.append((grant, ref, 'delegated', _value(grant, 'clientId'), _text(grant.get('scope')).split()))
    for grant, ref in _rows(sources, 'application_permissions'):
        resource, _ = principal_index.get(_text(grant.get('resourceId')).casefold(), ({}, None))
        role = next((role for role in resource.get('appRoles', []) or []
                     if _text(role.get('id')).casefold() == _text(grant.get('appRoleId')).casefold()), {})
        scopes = [role.get('value')] if role.get('value') else []
        grants.append((grant, ref, 'application', _value(grant, 'principalId', 'ParentId'), scopes))
    by_app = {}
    for grant, ref, kind, client_id, scopes in grants:
        if client_id:
            by_app.setdefault(_text(client_id).casefold(), []).append((grant, ref, kind, scopes))
    output = []
    app_count = high_count = unverified_count = overlap = 0
    delegated_high_apps = delegated_high_instances = application_high_apps = application_instances = delegated_instances = 0
    activities = list(_rows(sources, 'service_principal_signin_activities'))
    signins = list(_rows(sources, 'signin_logs'))
    for client_id, instances in by_app.items():
        principal, principal_ref = principal_index.get(client_id, ({}, None))
        verified, verification_reason = _app_publisher(principal, tenant_id)
        high = any(any(marker in _text(scope).lower() for marker in HIGH_PRIVILEGE_SCOPE_MARKERS)
                   for _, _, _, scopes in instances for scope in scopes)
        unverified = verified is False
        if not high and not unverified:
            continue
        app_count += 1
        high_count += high
        unverified_count += unverified
        overlap += high and unverified
        def high_scopes(scopes):
            return any(any(marker in _text(scope).lower() for marker in HIGH_PRIVILEGE_SCOPE_MARKERS) for scope in scopes)
        delegated_high_apps += any(kind == 'delegated' and high_scopes(scopes) for _, _, kind, scopes in instances)
        application_high_apps += any(kind == 'application' and high_scopes(scopes) for _, _, kind, scopes in instances)
        delegated_high_instances += sum(kind == 'delegated' and high_scopes(scopes) for _, _, kind, scopes in instances)
        delegated_instances += sum(kind == 'delegated' for _, _, kind, _ in instances)
        application_instances += sum(kind == 'application' for _, _, kind, _ in instances)
        app_id = _value(principal, 'appId')
        last_candidates = []
        for activity, activity_ref in activities:
            matches = (app_id and _text(activity.get('appId')).casefold() == _text(app_id).casefold()) or _text(activity.get('id')).casefold() == client_id
            if not matches:
                continue
            # Resource-side sign-ins do not establish this application's use as a client.
            for field in ('delegatedClientSignInActivity', 'applicationAuthenticationClientSignInActivity'):
                value = activity.get(field) or {}
                if isinstance(value, dict):
                    last_candidates.append((_value(value, 'lastSignInDateTime', 'lastSuccessfulSignInDateTime'), activity_ref))
        for event, event_ref in signins:
            if (app_id and _text(event.get('appId')).casefold() == _text(app_id).casefold()) or _text(event.get('servicePrincipalId')).casefold() == client_id:
                last_candidates.append((event.get('createdDateTime'), event_ref))
        last_used, activity_ref = _latest(last_candidates)
        for grant, ref, kind, scopes in instances:
            refs = _refs(ref, principal_ref, activity_ref)
            resource_id = _value(grant, 'resourceId')
            resource, resource_ref = principal_index.get(_text(resource_id).casefold(), ({}, None))
            refs = _refs(refs, resource_ref)
            consent_type = _value(grant, 'consentType')
            consenting = None
            if kind == 'delegated' and consent_type:
                if _norm(consent_type) == 'allprincipals':
                    consenting = [{'type': 'AllPrincipals', 'principalId': None, 'displayName': None, 'upn': None}]
                elif grant.get('principalId'):
                    user_id = grant['principalId']
                    user, user_ref = next(((row, user_ref) for row, user_ref in users if _text(row.get('id')).casefold() == _text(user_id).casefold()), ({}, None))
                    refs = _refs(refs, user_ref)
                    consenting = [{'type': 'Principal', 'principalId': user_id, 'displayName': _value(user, 'displayName'), 'upn': _value(user, 'userPrincipalName')}]
            elif kind == 'application':
                consenting = [{'type': 'ApplicationPrincipal', 'principalId': _value(grant, 'principalId', 'ParentId'),
                               'displayName': _value(principal, 'displayName'), 'upn': None}]
            fields = {'grantId': _value(grant, 'id'), 'servicePrincipalId': _value(principal, 'id') or client_id,
                      'appId': app_id, 'appDisplayName': _value(principal, 'displayName'),
                      'publisher': _value(principal, 'publisherName'), 'publisherVerified': verified,
                      'publisherVerificationBasis': verification_reason, 'grantType': kind,
                      'exactScopes': scopes if kind == 'delegated' or scopes else None,
                      'appRoleId': _value(grant, 'appRoleId'), 'resourceId': resource_id,
                      'resourceDisplayName': _value(resource, 'displayName'), 'consentType': consent_type,
                      'consentingPrincipals': consenting, 'approvedBy': None, 'lastUsed': last_used,
                      'highPrivilegeApp': high, 'unverifiedPublisherApp': unverified}
            output.append(_record(fields, refs, native_id=_value(grant, 'id'),
                                  derived={'publisherVerified': verification_reason,
                                           'lastUsed': 'Latest retained client-side sign-in joined by app ID or service-principal ID; activity outside the retained window is unknown.',
                                           'consentingPrincipals': 'Principals covered by the grant; the approving administrator identity is not supplied by the grant API.',
                                           'approvedBy': 'Approver identity was not retained; grant principal/consent scope is not an approver identity.',
                                           'highPrivilegeApp': 'Existing high-privilege scope markers evaluated across this application grant inventory.',
                                           'unverifiedPublisherApp': 'Existing publisher classification evaluated from retained owner and verifiedPublisher metadata.'},
                                  qualifications=['A grant row is distinct from an application. High-privilege and unverified-publisher populations can overlap.']))
    return _finish(output, 'application_permission_grant', 'Retained delegated and application grants for applications with high-privilege scopes or an unverified external publisher; grant instances remain separate.',
                   sources, ['service_principals', 'oauth_grants', 'application_permissions', 'service_principal_signin_activities', 'signin_logs'],
                   reconciliation={'grant_records': len(output), 'distinct_applications': app_count,
                                   'high_privilege_applications': high_count, 'unverified_publisher_applications': unverified_count,
                                   'application_overlap': overlap, 'delegated_grant_records': delegated_instances,
                                   'application_grant_records': application_instances,
                                   'high_privilege_delegated_applications': delegated_high_apps,
                                   'high_privilege_application_permission_apps': application_high_apps,
                                   'high_privilege_delegated_grant_instances': delegated_high_instances})


def _incidents(sources):
    output = []
    alerts = list(_rows(sources, 'alerts', 'security_alerts'))
    for row, ref in _rows(sources, 'incidents', 'security_incidents'):
        if _norm(row.get('status')) not in {'active', 'new', 'inprogress'}:
            continue
        identifier = _value(row, 'id', 'incidentId')
        linked = [(alert, alert_ref) for alert, alert_ref in alerts
                  if identifier and _text(alert.get('incidentId')).casefold() == _text(identifier).casefold()]
        categories = []
        dates = []
        for alert, _ in linked:
            category_values = alert.get('categories') or []
            if isinstance(category_values, str):
                category_values = [category_values]
            for value in ([alert.get('category')] + list(category_values)):
                if value and value not in categories:
                    categories.append(value)
            if _date(alert.get('firstActivityDateTime')):
                dates.append(alert['firstActivityDateTime'])
        first_activity = min(dates, key=_date) if dates else None
        first_seen = _value(row, 'firstActivityDateTime', 'firstSeen') or first_activity or _value(row, 'createdDateTime')
        category = _value(row, 'category', 'categories') or (categories if categories else None)
        fields = {'incidentId': identifier, 'severity': _value(row, 'severity'), 'title': _value(row, 'displayName', 'title'),
                  'status': _value(row, 'status'), 'classification': _value(row, 'classification'),
                  'firstSeen': first_seen, 'firstSeenBasis': 'Incident first activity' if _value(row, 'firstActivityDateTime', 'firstSeen') else
                  'Earliest retained linked-alert activity' if first_activity else 'Incident creation time' if first_seen else None,
                  'assignedTo': _value(row, 'assignedTo'), 'category': category,
                  'alertIds': [_value(alert, 'id') for alert, _ in linked if _value(alert, 'id')]}
        output.append(_record(fields, _refs(ref, [alert_ref for _, alert_ref in linked]), native_id=identifier,
                              derived={'category': 'Retained incident category, or categories of alerts linked by incident ID; classification is never substituted.',
                                       'firstSeen': 'Retained first activity when available; otherwise earliest linked-alert activity or explicitly qualified incident creation time.'},
                              qualifications=['Unknown incident categories remain null when no linked category was retained.']))
    return _finish(output, 'security_incident', 'Incidents explicitly in active/new/inProgress state. Categories and alert dates are joined by incident ID only.',
                   sources, ['incidents', 'alerts'], reconciliation={'active_incident_records': len(output)})


def _exposures(sources, finding):
    output = []
    sites = list(_rows(sources, 'sites'))
    key = str(finding.get('FindingKey') or '')
    anonymous = 'anonymous_links' in key
    exposure_columns = [('Anyone links', ('Anyone link count', 'Anonymous link count'), 'links')] if anonymous else [
        ('Everyone permissions', ('Everyone permission count', 'Everyone permissions Count'), 'permissions'),
        ('Everyone except external users permissions', ('EEEU permission count', 'EEEU permissions Count'), 'permissions'),
        ('Organization links', ('Organization link count', 'PeopleInYourOrg link count', 'Organization links Count'), 'links')]
    for row, ref in _rows(sources, 'content_exposure', 'sensitive_exposure'):
        if row.get('_headers_only'):
            continue
        report_type = _value(row, '_report_type', 'Report Type')
        item_id = _value(row, 'Item ID', 'Unique Id', 'UniqueId', 'Object ID')
        item_url = _value(row, 'Item URL', 'Item url')
        item = bool(item_id or item_url)
        site_url = _value(row, 'Site URL', 'SiteUrl')
        if not site_url and not item:
            site_url = _value(row, 'URL')
        row_refs = [ref]
        site_id = _value(row, 'Site ID')
        if not site_url and site_id:
            matches = []
            for site, site_ref in sites:
                native = _text(_value(site, 'id', 'siteId'))
                # Graph composite site IDs identify the site collection in the
                # second component. Device/file names are never identity keys.
                keys = [native.casefold()]
                components = native.split(',')
                if len(components) == 3:
                    keys.append(components[1].casefold())
                if _text(site_id).casefold() in keys and _value(site, 'webUrl', 'Url', 'Site URL'):
                    matches.append((site, site_ref))
            urls = {_text(_value(site, 'webUrl', 'Url', 'Site URL')) for site, _ in matches}
            if len(urls) == 1:
                site_url = next(iter(urls))
                row_refs = _refs(row_refs, [site_ref for _, site_ref in matches])
        workload = _value(row, 'Workload')
        workload_derived = False
        if not workload:
            address = _text(site_url or item_url).lower()
            template = _norm(_value(row, 'Site template', 'Template'))
            if '/personal/' in address or 'spspers' in template:
                workload = 'OneDrive'
            elif '.sharepoint.com/' in address:
                workload = 'SharePoint'
            workload_derived = bool(workload)
        recipient = _value(row, 'Recipient', 'Permission Recipient')
        signals = _text(_value(row, 'Risk Signals')).lower()
        counts = []
        for label, aliases, unit in exposure_columns:
            value = _value(row, *aliases)
            try:
                count = int(str(value).replace(',', '')) if value is not None else None
            except (ValueError, TypeError):
                count = None
            if count is not None and count > 0:
                counts.append((label, count, unit))
        if item and not counts:
            recipient_norm = _norm(recipient)
            if anonymous and ('anyone' in recipient_norm or _norm(_value(row, 'Link scope', 'Link Type')) == 'anonymous'):
                counts = [('Anyone links', 1, 'links')]
            elif not anonymous:
                if recipient_norm == 'everyone' or 'everyone permissions' in signals:
                    counts = [('Everyone permissions', 1, 'permissions')]
                elif 'everyoneexceptexternalusers' in recipient_norm or 'eeeu' in recipient_norm or 'spo-grid-all-users' in _text(recipient).lower() or 'eeeu permissions' in signals:
                    counts = [('Everyone except external users permissions', 1, 'permissions')]
                elif _norm(_value(row, 'Link scope', 'Link Type')) in {'organization', 'peopleinyourorganization'}:
                    counts = [('Organization links', 1, 'links')]
        # Activity reports count links for a site; they do not supply link IDs.
        if not counts and not item and _value(row, 'Links created') is not None:
            source_name = _text(_value(row, '_source_file', 'Source File')).lower()
            matching = 'anyone_links' in source_name if anonymous else 'people_in_your_organization_links' in source_name
            if matching:
                try:
                    count = int(str(_value(row, 'Links created')).replace(',', ''))
                except ValueError:
                    count = 0
                if count > 0:
                    counts = [('Anyone links' if anonymous else 'Organization links', count, 'links')]
        for exposure_type, count, unit in counts:
            fields = {'siteId': site_id, 'siteUrl': site_url, 'workload': workload,
                      'exposureType': exposure_type, 'permissionedUserCount': _value(row, 'Number of users having access', 'Number of users with permissions', 'Permissioned users', 'Total user count'),
                      'lastModified': _value(row, 'Last Modified', 'Last modified date', 'LastModifiedDateTime'),
                      'itemId': item_id, 'itemUrl': item_url, 'permissionId': _value(row, 'Permission ID'),
                      'linkId': _value(row, 'Link ID'), 'recipient': recipient,
                      'recordGranularity': 'item_or_permission' if item else 'site_summary',
                      'exposureCount': count, 'countUnit': unit, 'reportDate': _value(row, '_evidence_date', 'Report date', 'Report Date'),
                      'reportType': report_type, 'sourceFile': _value(row, '_source_file', 'Source File')}
            derived = {'exposureType': 'Exposure classified from retained permission/link attributes or the corresponding report count column.',
                       'recordGranularity': 'Item detail requires an item identifier/URL. Site aggregate rows remain site summaries.',
                       'exposureCount': 'Native count for this row and sharing unit; overlapping report populations cannot be added as unique files.',
                       'lastModified': 'Only an explicit last-modified attribute is used; report date is not substituted.'}
            if workload_derived:
                derived['workload'] = 'Derived from a personal-site URL/template (OneDrive) or SharePoint URL; this classification is not proof of item identity.'
            if len(row_refs) > 1:
                derived['siteUrl'] = 'Resolved from a retained directory site joined by native site/site-collection ID.'
            output.append(_record(fields, row_refs, native_id=None,
                                  derived=derived,
                                  qualifications=['Summary counts do not identify individual exposed files, links, permissions or users. Distinct sharing units and overlapping reports remain separate.']))
    return _finish(output, 'content_exposure', 'Matching organization-wide permissions/links retained in supplied reports; site-level counts remain summaries. Historical and overlapping records are retained.',
                   sources, ['content_exposure', 'sensitive_exposure', 'sites'],
                   reconciliation={'detail_records': sum(row['fields']['recordGranularity'] == 'item_or_permission' for row in output),
                                   'summary_records': sum(row['fields']['recordGranularity'] == 'site_summary' for row in output),
                                   'unique_exposed_files': None})


def _sharing(sources):
    output = []
    tenant_rows = list(_rows(sources, 'sharepoint_tenant_settings'))
    tenant = tenant_rows[0][0] if tenant_rows else {}
    tenant_ref = tenant_rows[0][1] if tenant_rows else None
    for name in ('sharepoint_tenant_settings', 'sharepoint_site_settings', 'sharepoint_graph_settings'):
        for row, ref in _rows(sources, name):
            if isinstance(row.get('settings'), dict):
                row = row['settings']
            mode = _value(row, 'SharingCapability', 'sharingCapability', 'sharingSetting')
            normalized_mode = _norm(mode)
            guest_allowed = (False if normalized_mode in {'disabled', '0'} else True if normalized_mode in
                             {'existingexternalusersonly', 'existingexternalusersharingonly', 'externaluserandguestsharing',
                              'externalusersharingonly', 'existingguestsonly', 'newandexistingguests', 'anyone', '1', '2', '3'} else None)
            expiry = _value(row, 'AnonymousLinkExpirationInDays', 'RequireAnonymousLinksExpireInDays', 'anyoneLinkExpiryDays')
            expiry_basis = 'Site setting' if name == 'sharepoint_site_settings' and expiry is not None else 'Tenant setting' if expiry is not None else None
            refs = [ref]
            if name == 'sharepoint_site_settings' and expiry is None:
                expiry = _value(tenant, 'RequireAnonymousLinksExpireInDays')
                if expiry is not None:
                    refs = _refs(refs, tenant_ref)
                    expiry_basis = 'Tenant fallback; a site-specific expiry was not retained.'
            native_site_expiry = _value(row, 'AnonymousLinkExpirationInDays') if name == 'sharepoint_site_settings' else None
            override = _boolean(_value(row, 'OverrideTenantAnonymousLinkExpirationPolicy'))
            effective_expiry = expiry
            effective_guests = guest_allowed
            default_link = _value(row, 'DefaultSharingLinkType', 'sharingLinkDefaultType')
            effective_default = default_link
            site_quality_note = None
            if name == 'sharepoint_site_settings':
                # SPO list queries may populate defaults instead of effective
                # site settings. A zero expiry also requires the override flag
                # to distinguish inheritance from removing an expiry rule.
                state = sources[name][ref['dataset_index']].get('source', {})
                exact = (state.get('settings_verified') is True or state.get('single_site_query') is True or
                         state.get('settings_read_mode') == 'per_site_identity') and row.get('SettingsReadStatus') != 'unavailable'
                if not exact:
                    effective_expiry = None
                    effective_guests = None
                    effective_default = None
                    site_quality_note = 'Site settings from an inventory/list query may contain provider defaults; effective site configuration requires a targeted site read or dated review.'
                elif override is False:
                    effective_expiry = _value(tenant, 'RequireAnonymousLinksExpireInDays')
                    refs = _refs(refs, tenant_ref)
                elif override is None and _text(native_site_expiry) in {'0', '-1'}:
                    effective_expiry = None
                tenant_mode = _norm(_value(tenant, 'SharingCapability'))
                if tenant_mode in {'disabled', '0'}:
                    effective_guests = False
            fields = {'siteUrl': _value(row, 'Url', 'Site URL', 'siteUrl'), 'siteId': _value(row, 'Id', 'Site ID'),
                      'title': _value(row, 'Title', 'displayName'), 'scope': 'site' if name == 'sharepoint_site_settings' else 'tenant',
                      'sharingSetting': mode, 'oneDriveSharingSetting': _value(row, 'OneDriveSharingCapability'),
                      'defaultLinkType': default_link, 'effectiveDefaultLinkType': effective_default,
                      'defaultLinkScope': _value(row, 'DefaultShareLinkScope'), 'defaultLinkRole': _value(row, 'DefaultShareLinkRole'),
                      'anyoneLinkExpiryDays': expiry, 'anyoneLinkExpiryBasis': expiry_basis,
                      'externalGuestsAllowed': guest_allowed, 'externalGuestMode': mode,
                      'effectiveExternalGuestsAllowed': effective_guests,
                      'effectiveAnyoneLinkExpiryDays': effective_expiry,
                      'overrideTenantAnyoneLinkExpirationPolicy': override,
                      'sharingDomainRestrictionMode': _value(row, 'SharingDomainRestrictionMode'),
                      'sharingAllowedDomains': _value(row, 'SharingAllowedDomainList'),
                      'sharingBlockedDomains': _value(row, 'SharingBlockedDomainList')}
            output.append(_record(fields, refs, native_id=fields['siteId'] or fields['siteUrl'],
                                  derived={'externalGuestsAllowed': 'Configured sharing capability allows/disallows guest sharing; does not establish guest access or bypass domain restrictions.',
                                           'anyoneLinkExpiryDays': (expiry_basis or 'Neither applicable site nor tenant expiry was retained.') + ' This is a returned configuration value, not independently verified effective site behavior.',
                                           'effectiveDefaultLinkType': site_quality_note or 'Returned effective/default link setting for this source scope.',
                                           'effectiveExternalGuestsAllowed': site_quality_note or 'Configuration permits guest sharing subject to domain restrictions and more restrictive tenant settings.',
                                           'effectiveAnyoneLinkExpiryDays': site_quality_note or 'Effective expiry requires verified site settings and a known tenant-override policy; ambiguous zero/default values remain unknown.'},
                                  qualifications=['Tenant and site configurations are separate scopes; a permissive site cannot override a more restrictive tenant setting. Anyone links and authenticated guest access are distinct.'] + ([site_quality_note] if site_quality_note else [])))
    return _finish(output, 'sharing_configuration', 'Retained SharePoint tenant, site and Graph sharing settings, preserving source conflicts and expiry scope.',
                   sources, ['sharepoint_tenant_settings', 'sharepoint_site_settings', 'sharepoint_graph_settings'],
                   reconciliation={'site_configuration_records': sum(row['fields']['scope'] == 'site' for row in output),
                                   'tenant_configuration_records': sum(row['fields']['scope'] == 'tenant' for row in output)})


def _antivirus(sources, finding):
    output = []
    names = ['antivirus_health'] if 'antivirus_health' in str(finding.get('FindingKey') or '') or 'antivirus' in str(finding.get('Feature') or '').lower() else ['windows_protection']
    machines = list(_rows(sources, 'machines'))
    devices = list(_rows(sources, 'managed_devices', 'directory_devices'))
    declared = finding.get('InvestigationEvidence') or {}
    selected_records = declared.get('records') if isinstance(declared, dict) and declared.get('kind') == 'records' else None
    selected_fingerprints = None
    if isinstance(selected_records, list):
        selected_fingerprints = {json.dumps(safe_record(record), sort_keys=True, default=str, ensure_ascii=False)
                                 for record in selected_records if isinstance(record, dict)}
    for row, ref in _rows(sources, *names):
        if selected_fingerprints is not None and json.dumps(safe_record(row), sort_keys=True, default=str, ensure_ascii=False) not in selected_fingerprints:
            continue
        outdated = _boolean(_value(row, 'avIsSignatureUpToDate')) is False if ref['dataset'] == 'antivirus_health' else _boolean(_value(row, 'signatureUpdateOverdue')) is True
        if not outdated:
            continue
        machine_id = _value(row, 'machineId', 'deviceId')
        managed_id = _value(row, 'ParentId')
        refs = [ref]
        joined = {}
        for machine, machine_ref in machines:
            if machine_id and _text(machine.get('id')).casefold() == _text(machine_id).casefold():
                joined = {**joined, **machine}
                refs = _refs(refs, machine_ref)
        if managed_id:
            for device, device_ref in devices:
                if _text(device.get('id')).casefold() == _text(managed_id).casefold():
                    joined = {**joined, **device}
                    refs = _refs(refs, device_ref)
        observed = _value(row, 'dataRefreshTimestamp', 'lastReportedDateTime')
        updated = _value(row, 'avSignatureUpdateTime', 'signatureLastUpdated')
        age = _value(row, 'signatureAgeDays', 'avSignatureAge')
        age_reason = None
        if age is None and _date(observed) and _date(updated) and _date(observed) >= _date(updated):
            age = round((_date(observed) - _date(updated)).total_seconds() / 86400, 3)
            age_reason = 'Elapsed days from retained signature-update time to this antivirus report time; rebuild date is not used.'
        fields = {'deviceName': _value(row, 'computerDnsName', 'deviceName') or _value(joined, 'computerDnsName', 'deviceName', 'displayName'),
                  'deviceId': machine_id or managed_id, 'osPlatform': _value(row, 'osPlatform', 'osKind') or _value(joined, 'osPlatform', 'operatingSystem'),
                  'signatureVersion': _value(row, 'avSignatureVersion', 'signatureVersion'), 'signatureAgeDays': age,
                  'signatureUpdatedAt': updated, 'signatureAgeAsOf': observed, 'lastSeen': _value(row, 'lastSeenTime', 'lastSeen') or _value(joined, 'lastSeen', 'lastSyncDateTime'),
                  'signatureUpToDate': _boolean(_value(row, 'avIsSignatureUpToDate')), 'signatureUpdateOverdue': _boolean(_value(row, 'signatureUpdateOverdue')),
                  'reportDate': observed, 'directoryDeviceId': _value(joined, 'aadDeviceId', 'azureADDeviceId')}
        derived = {'signatureAgeDays': age_reason or 'Signature age requires a retained age or signature-update and observation timestamps.',
                   'deviceId': 'Defender machine ID or Intune parent device ID; device names are never used to establish identity.'}
        output.append(_record(fields, refs, native_id=_value(row, 'id') or machine_id or managed_id,
                              derived=derived, qualifications=['The health finding is dated by the endpoint report, not the later assessment export date.']))
    return _finish(output, 'antivirus_signature_health', 'Antivirus rows explicitly reporting signatures not current, or Windows protection rows explicitly reporting signature update overdue.',
                   sources, names + ['machines', 'managed_devices', 'directory_devices'],
                   reconciliation={'outdated_signature_records': len(output)})


def _legacy_signins(sources, finding):
    from .signin_evidence import (LEGACY_CLASSIFICATION_RULE, LEGACY_CLIENT_TYPES_SOURCE, LEGACY_SIGNIN_COUNT,
                                  OUTCOME_RULE, SIGNIN_OUTCOMES, classify_signin_outcome, normalize_signin_event, is_legacy_signin,
                                  unmatched_legacy_client_type)
    output, unmatched, examined = [], {}, 0
    outcomes = dict.fromkeys(SIGNIN_OUTCOMES, 0)
    accounts, applications = set(), set()
    for row, ref in _rows(sources, 'signin_logs'):
        examined += 1
        if not is_legacy_signin(row):
            other = unmatched_legacy_client_type(row)
            if other:
                unmatched[other] = unmatched.get(other, 0) + 1
            continue
        status = row.get('status') if isinstance(row.get('status'), dict) else {}
        location = row.get('location') if isinstance(row.get('location'), dict) else {}
        device = row.get('deviceDetail') if isinstance(row.get('deviceDetail'), dict) else {}
        outcome = classify_signin_outcome(row)
        normalized = normalize_signin_event(row, ref)
        outcomes[outcome['outcome']] += 1
        policies = row.get('appliedConditionalAccessPolicies')
        account = _text(_value(row, 'userId')) or _text(_value(row, 'userPrincipalName'))
        if account:
            accounts.add(account.casefold())
        if _value(row, 'appId', 'appDisplayName'):
            applications.add(_text(_value(row, 'appId', 'appDisplayName')).casefold())
        fields = {
            'eventId': _value(row, 'id'), 'createdDateTime': _value(row, 'createdDateTime'),
            'userPrincipalName': _value(row, 'userPrincipalName'), 'userId': _value(row, 'userId'),
            'userDisplayName': _value(row, 'userDisplayName'),
            'appDisplayName': _value(row, 'appDisplayName'), 'appId': _value(row, 'appId'),
            'resourceDisplayName': _value(row, 'resourceDisplayName'),
            'clientAppUsed': _value(row, 'clientAppUsed'),
            'authenticationProtocol': _value(row, 'authenticationProtocol'),
            'normalized_outcome': normalized['NormalizedSignInOutcome'],
            'legacyAuthenticationState': normalized['LegacyAuthenticationState'],
            'authenticationRuleVersion': normalized['AuthenticationRuleVersion'],
            'authenticationRequirement': row.get('authenticationRequirement'),
            'mfaRequirement': normalized['MFARequirement'], 'mfaSatisfaction': normalized['MFASatisfaction'],
            'authenticationDetails': row.get('authenticationDetails'),
            'authenticationMethodsUsed': row.get('authenticationMethodsUsed'),
            'rootAuthenticationMethods': normalized['RootAuthenticationMethods'],
            'appliedPoliciesState': normalized['AppliedPoliciesState'],
            'reportOnlyResults': normalized['ReportOnlyResults'],
            'authenticationConflicts': normalized['AuthenticationConflicts'],
            'riskLevelDuringSignIn': row.get('riskLevelDuringSignIn'),
            'riskLevelAggregated': row.get('riskLevelAggregated'), 'riskState': row.get('riskState'),
            'resourceId': row.get('resourceId'), 'userType': row.get('userType'),
            'outcome': outcome['outcome'], 'outcomeDetail': outcome['detail'], 'outcomeBasis': outcome['basis'],
            'errorCode': status.get('errorCode'), 'failureReason': _value(status, 'failureReason'),
            'additionalDetails': _value(status, 'additionalDetails'),
            'conditionalAccessStatus': _value(row, 'conditionalAccessStatus'),
            'appliedConditionalAccessPolicies': ([{'id': policy.get('id'), 'displayName': policy.get('displayName'),
                                                   'result': policy.get('result')}
                                                  for policy in policies if isinstance(policy, dict)]
                                                 if isinstance(policies, list) else None),
            'ipAddress': _value(row, 'ipAddress'),
            'location': ', '.join(_text(location.get(part)) for part in ('city', 'state', 'countryOrRegion')
                                  if _text(location.get(part))) or None,
            'isInteractive': _boolean(row.get('isInteractive')),
            'correlationId': _value(row, 'correlationId'), 'userAgent': _value(row, 'userAgent'),
            'deviceId': _value(device, 'deviceId'), 'deviceName': _value(device, 'displayName'),
            'operatingSystem': _value(device, 'operatingSystem'),
        }
        derived = {'clientAppUsed': 'Reported client type from the sign-in log. It classifies the client; it does not establish the exact protocol.',
                   'outcome': OUTCOME_RULE, 'outcomeDetail': 'Derived from status.errorCode and conditionalAccessStatus.',
                   'outcomeBasis': 'The retained field values used to classify the outcome.',
                   'location': 'Joined from location.city, state and countryOrRegion.',
                   'authenticationProtocol': 'Not returned by the Microsoft Graph v1.0 sign-in collector; the protocol or grant type is not established.',
                   'appliedConditionalAccessPolicies': 'Not returned. Applied policy detail requires Policy.Read.All or Policy.Read.ConditionalAccess when the logs were collected.'}
        if fields['authenticationProtocol'] is not None:
            derived.pop('authenticationProtocol')
        if fields['appliedConditionalAccessPolicies'] is not None:
            derived.pop('appliedConditionalAccessPolicies')
        if fields['location'] is None:
            derived['location'] = 'The sign-in record did not include location detail.'
        output.append(_record(fields, [ref], native_id=_value(row, 'id'), derived=derived, qualifications=[
            'A legacy client-type match does not prove the exact protocol, successful access or a control bypass.']))
    match = LEGACY_SIGNIN_COUNT.search(str(finding.get('Observation') or ''))
    reported = finding.get('ReportedLegacySignInCount')
    if reported is None and match:
        reported = int(match.group(1).replace(',', ''))
    notes = ['Classification: ' + LEGACY_CLASSIFICATION_RULE,
             'Collection: Microsoft Graph v1.0 auditLogs/signIns returns interactive user sign-ins; non-interactive and '
             'service-principal sign-ins and events outside the retained window are not included.']
    if unmatched:
        notes.append(f'{sum(unmatched.values())} retained sign-in events use Microsoft-listed legacy client types that the '
                     'historical rule does not match (' + '; '.join(f'{name}: {count}' for name, count in sorted(unmatched.items()))
                     + f'). They are reported here and are not part of this finding. Source: {LEGACY_CLIENT_TYPES_SOURCE}')
    if reported is not None and int(reported) != len(output):
        notes.append(f'The finding reports {reported} legacy sign-in events; {len(output)} matching events are retained.')
    reconciliation = {'legacy_signin_events': len(output), 'outcomes': outcomes,
                      'unique_accounts': len(accounts), 'unique_applications': len(applications),
                      'retained_signin_records_examined': examined,
                      'reported_legacy_signin_events': reported,
                      'unmatched_legacy_client_events': sum(unmatched.values()),
                      'unmatched_legacy_client_types': unmatched}
    return _finish(output, 'legacy_signin_event', 'Retained signin_logs events whose clientAppUsed matches the legacy classification rule; one record per event, without deduplication.',
                   sources, ['signin_logs'], notes, reconciliation)


_STANDING_ADMIN = re.compile(r'role assignment schedules? have no expiration|permanent admin role assignment', re.IGNORECASE)


def _standing_admin_finding(finding):
    keys = {key.strip() for key in str(finding.get('EvidenceKey') or '').split(';')}
    text = ' '.join(str(finding.get(name) or '') for name in ('OriginalObservation', 'Observation'))
    return 'admin_role_detail' in keys and bool(_STANDING_ADMIN.search(text))


def expand_finding_records(finding, sources, *, evaluation_date=None, tenant_id=None, privileged_assessment=None):
    """Return named records for a supported finding, with exact source locators.

    Adapters are chosen from finding content (FindingKey, evidence key and wording),
    never from the positional RecommendationId, which differs between builds.
    Unsupported findings return no records and ``record_type=unsupported`` so the
    exporter can retain declared investigation records and source inventories.
    ``evaluation_date`` is intentionally not substituted for observed dates.
    """
    from .signin_evidence import is_legacy_signin_finding
    key = str(finding.get('FindingKey') or '')
    feature = str(finding.get('Feature') or '').lower()
    if key.startswith('entra.privileged.'):
        model = privileged_assessment or {}
        selected = set(finding.get('PrivilegedObservationIds') or [])
        identities = {i['identity_id']: i for i in model.get('identities') or []}
        records = []
        for observation in model.get('observations') or []:
            if observation['observation_id'] not in selected:
                continue
            identity = identities[observation['identity_id']]
            fields = {'identityId': identity['identity_id'], 'principalId': identity['principal_id'],
                'principalType': identity['principal_type'], 'condition': observation['condition'],
                'classification': observation['classification'], 'componentObservationIds': observation['component_ids'],
                'activeAssignments': identity['active_assignments'], 'eligibleAssignments': identity['eligible_assignments'],
                'assignmentScopes': identity['scopes'], 'activity': identity['activity'],
                'purpose': identity['purpose'], 'registrationState': identity['authentication']['registration_state']}
            records.append(_record(fields, observation['evidence_refs'], native_id=observation['observation_id']))
        return _finish(records, 'privileged_identity', 'Shared privileged component observations with exact native source locators.',
                       sources, [], model.get('limitations') or [])
    if 'mfa_registration' in key:
        return _mfa(sources)
    if _standing_admin_finding(finding):
        return _admin(sources, evaluation_date, privileged_assessment)
    if key == 'entra.identity_risk.users':
        return _risks(sources)
    if key == 'entra.apps.user_consent_assignment':
        return _consent(sources)
    if key == 'entra.app_consent.high_impact_grants':
        return _apps(sources, tenant_id)
    if key in {'defender.incidents.current', 'baseline.defender.incidents'}:
        return _incidents(sources)
    if is_legacy_signin_finding(finding):
        return _legacy_signins(sources, finding)
    if any(marker in key for marker in ('broad_internal_access', 'anonymous_links')):
        return _exposures(sources, finding)
    if key.startswith('sharepoint.sharing.') or key == 'baseline.content.sharing':
        return _sharing(sources)
    if 'antivirus_health' in key or 'windows_protection' in key and 'overdue' in key or 'antivirus signatures' in feature:
        return _antivirus(sources, finding)
    return {'records': [], 'record_type': 'unsupported', 'selection': 'No specialized normalized adapter; use retained investigation records and source inventories.', 'limitations': []}

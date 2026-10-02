"""Individual delegated grants for applications already flagged for review.

This worklist never infers grants from an application's combined scope list.
Its rows are retained oauth2PermissionGrant records; app counts and grant counts
are separate, and each application's records occupy one exact workbook range.
"""

from datetime import date, datetime
import json

from .authentication_methods import _get


SOURCE_API = 'https://graph.microsoft.com/v1.0/oauth2PermissionGrants'
_MISSING = {'not returned', 'not assessed', 'unknown', 'unresolved', 'none'}


def _identifier(value):
    value = str(value or '').strip()
    return '' if value.casefold() in _MISSING else value


def _cell(value):
    value = getattr(value, 'value', value)
    if value is None:
        return 'Not retained'
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return value


def _range(title, first, last, width):
    column = ''
    while width:
        width, remainder = divmod(width - 1, 26)
        column = chr(65 + remainder) + column
    return "'" + title.replace("'", "''") + f"'!A{first}:{column}{last}"


def build_application_grant_investigation(client, flagged_app_rows, *,
                                         sheet_title='Application Grant Detail', collection_context=None):
    """Select actual grants for flagged client service principals only.

    ``app_ranges`` is keyed by the exact Enterprise Application Object ID from
    the supplied flagged row. Matching is case-insensitive. A supplied client
    application ID is resolved only when it has one matching service principal;
    application IDs are never compared directly with grant clientId values.
    """
    from .evidence_layer import HIGH_PRIVILEGE_SCOPE_MARKERS

    context = collection_context or {}
    raw = _get(client, 'oauth_permission_grants')
    grants = list(raw) if isinstance(raw, (list, tuple)) else []
    principals = _get(client, 'service_principals') or []
    sp_by_id, sp_by_app_id = {}, {}
    for principal in principals:
        identifier = _identifier(_get(principal, 'id'))
        app_id = _identifier(_get(principal, 'appId'))
        if identifier:
            sp_by_id[identifier.casefold()] = principal
            if app_id:
                sp_by_app_id.setdefault(app_id.casefold(), {})[identifier.casefold()] = principal

    selected, unresolved = {}, 0
    for app in flagged_app_rows or []:
        if not isinstance(app, dict) or not str(app.get('Flagged Because') or '').strip():
            continue
        identifier = _identifier(app.get('Enterprise Application Object ID'))
        if not identifier:
            possible = sp_by_app_id.get(_identifier(app.get('Application (Client) ID')).casefold(), {})
            if len(possible) == 1:
                identifier = _identifier(_get(next(iter(possible.values())), 'id'))
        if identifier:
            selected.setdefault(identifier.casefold(), {'identifier': identifier, 'app': app, 'grants': []})
        else:
            unresolved += 1

    for number, grant in enumerate(grants, 1):
        key = _identifier(_get(grant, 'clientId')).casefold()
        if key in selected:
            selected[key]['grants'].append((number, grant))

    states = _get(client, 'collection_status') or {}
    state = states.get('oauth_grants', {}) if isinstance(states, dict) else {}
    state = state if isinstance(state, dict) else {}
    source_state = str(state.get('availability_status') or ('available' if state.get('available') is True else 'unknown'))
    if state.get('truncated') or state.get('complete') is False:
        source_state = 'partial'
    elif state.get('available') is False and source_state == 'available':
        source_state = 'partial' if grants else 'unavailable'
    source_api = state.get('source_api') or SOURCE_API
    source_file = state.get('source_file') or _get(client, 'source_file') or context.get('source_file')
    collected_at = (state.get('collected_at') or state.get('collection_completed_at')
                    or _get(client, 'collected_at') or context.get('collected_at'))
    rows, app_ranges = [], {}
    matched_apps, high_privilege_grants, missing_ids = 0, 0, 0
    grant_ids = set()
    timestamps_known = 0
    for key, selection in selected.items():
        if not selection['grants']:
            continue
        matched_apps += 1
        first = len(rows) + 2
        app = selection['app']
        principal = sp_by_id.get(key)
        for number, grant in selection['grants']:
            grant_id = _identifier(_get(grant, 'id'))
            if grant_id:
                grant_ids.add(grant_id.casefold())
            else:
                missing_ids += 1
            scope = _get(grant, 'scope')
            high_privilege = isinstance(scope, str) and any(
                marker in permission.casefold() for permission in scope.split() for marker in HIGH_PRIVILEGE_SCOPE_MARKERS)
            high_privilege_grants += high_privilege
            resource_id = _get(grant, 'resourceId')
            resource = sp_by_id.get(_identifier(resource_id).casefold())
            created = _get(grant, 'createdDateTime')
            modified = _get(grant, 'lastModifiedDateTime')
            timestamps_known += created is not None or modified is not None
            rows.append({
                'RecommendationId': '', 'Flagged By': '',
                'Grant ID': _cell(_get(grant, 'id')),
                'App Display Name': _cell(app.get('App Display Name') or _get(principal, 'displayName')),
                'Application (Client) ID': _cell(app.get('Application (Client) ID') or _get(principal, 'appId')),
                'Client Service Principal ID': _cell(_get(grant, 'clientId')),
                'Resource Service Principal ID': _cell(resource_id),
                'Resource Application ID': _cell(_get(resource, 'appId')),
                'Resource Display Name': _cell(_get(resource, 'displayName')),
                'Principal ID': _cell(_get(grant, 'principalId')),
                'Consent Type': _cell(_get(grant, 'consentType')),
                'Permission Type': 'Delegated',
                'Granted Scopes': _cell(scope),
                'High Privilege Match': high_privilege,
                'App Flagged Because': app.get('Flagged Because', ''),
                'Publisher Verification State': _cell(app.get('Publisher Verification State')),
                'Grant Created UTC': _cell(created),
                'Grant Modified UTC': _cell(modified),
                'Grant Start UTC': _cell(_get(grant, 'startTime')),
                'Grant Expiry UTC': _cell(_get(grant, 'expiryTime')),
                'Source Row': number,
                'Source API': source_api,
                'Source File': _cell(source_file),
                'Collected At': _cell(collected_at),
                'Collection Window': state.get('collection_window') or 'Current delegated-grant snapshot at collection time',
                'Source Filter': _cell(state.get('filters') or state.get('request_params')),
                'Source State': source_state,
                'Pages Collected': _cell(state.get('pages_collected')),
                'Truncated': _cell(state.get('truncated')),
            })
        app_ranges[selection['identifier']] = _range(sheet_title, first, len(rows) + 1, len(rows[-1]))

    flagged_apps = len(selected) + unresolved
    unmatched_apps = flagged_apps - matched_apps
    duplicate_ids = len(rows) - missing_ids - len(grant_ids)
    reconciliation = (f'{len(rows)} retained delegated-grant records support {matched_apps} of {flagged_apps} flagged applications. '
                      f'{high_privilege_grants} grant records match the existing high-privilege scope rule. '
                      'Application counts are distinct client service principals; an application can have several grants, resources or consenting principals.')
    if unmatched_apps:
        reconciliation += f' {unmatched_apps} flagged applications have no matched retained grant record; no substitute grant rows were created.'
    if unresolved:
        reconciliation += f' {unresolved} flagged application rows could not be resolved to an unambiguous client service principal.'
    if duplicate_ids or missing_ids:
        reconciliation += (f' Grant identifiers: {len(grant_ids)} distinct, {missing_ids} records missing an ID, '
                           f'{duplicate_ids} repeated-ID records. Original returned records are retained without deduplication.')
    if source_state != 'available':
        reconciliation += ' Source completeness is not established; counts describe retained records only.'
    unavailable = ''
    if flagged_apps and not rows:
        if raw is None:
            unavailable = 'The collection did not retain oauth_permission_grants; combined application scopes cannot reconstruct individual grant records.'
        elif not grants:
            unavailable = 'No delegated-grant records were retained for the flagged applications; the application summary cannot identify their individual grants.'
        else:
            unavailable = 'No retained grant clientId matches a resolved flagged client service principal; application IDs and display names are not substitutes for client service principal IDs.'
    details = [
        reconciliation,
        'Selection: retained oauth_permission_grants whose clientId matches a flagged enterprise application object ID. Unflagged applications are excluded; contextual grants for a flagged app remain visible alongside high-privilege grants.',
        'High Privilege Match uses the same scope markers as App Access Detail, applied to this individual grant only. A publisher flag belongs to the application and does not itself classify the grant scopes as high privilege.',
        'Consent Type and Principal ID are preserved. AllPrincipals consent normally has no individual principal ID; Principal consent identifies the consenting principal. Resource IDs identify the target service principal, separately from its application ID.',
        f'Grant creation/update timestamps were retained for {timestamps_known} of {len(rows)} selected records. A collection timestamp is not a consent or modification timestamp; missing grant dates cannot be reconstructed.',
        'Pagination, truncation, source file and collection time are copied when retained. Missing query filters, timestamps or pagination metadata remain unknown, including for older saved collections.',
        'This is delegated OAuth consent evidence, not an inventory of application-permission app-role assignments, app owners, effective authorization or business approval. Those require separate evidence.',
        'Permissions, licensing and retention were not independently checked by this offline worklist. Inaccessible, expired or uncollected grants cannot be recovered from an aggregate.',
    ]
    if unavailable:
        details.append(unavailable)
    return {
        'rows': rows, 'matched_count': len(rows), 'flagged_app_count': flagged_apps,
        'matched_app_count': matched_apps, 'unmatched_app_count': unmatched_apps,
        'high_privilege_grant_count': high_privilege_grants, 'unique_grant_count': len(grant_ids),
        'source_record_count': len(grants), 'app_ranges': app_ranges,
        'reconciliation_note': reconciliation, 'unavailability_reason': unavailable,
        'details': details, 'restricted': True,
    }

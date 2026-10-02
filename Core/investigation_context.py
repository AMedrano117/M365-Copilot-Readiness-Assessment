"""Configuration evidence and explicit explanations when no affected list exists."""

import re


SOURCE_KEYS = {
    'authentication_detail': ('auth_methods',),
    'mfa_registration_detail': ('auth_methods',),
    'legacy_signin_detail': ('signin_logs',),
    'conditional_access_detail': ('ca_policies', 'security_defaults'),
    'admin_role_detail': ('role_assignment_schedules', 'role_assignments', 'role_eligibility_schedules'),
    'identity_risk_detail': ('risky_users', 'risk_detections'),
    'app_access_detail': ('service_principals', 'oauth_grants'),
    'app_consent_policy_detail': ('authorization_policy',),
    'guest_access_detail': ('guest_users', 'authorization_policy'),
    'access_review_detail': ('access_reviews',),
    'entra_device_detail': ('managed_devices',),
    'defender_device_detail': ('defender_machines',),
    'defender_incident_detail': ('defender_incidents',),
    'data_exposure_detail': ('data_exposure_sam', 'data_exposure_dspm'),
    'sharepoint_lifecycle_detail': ('data_exposure_sam',),
    'sharepoint_governance_detail': ('sharepoint_governance',),
}


def is_consent_configuration(rec):
    """Match the observed configuration, never generic remediation wording."""
    text = str(rec.get('OriginalObservation') or rec.get('Observation') or '').lower()
    return str(rec.get('Service') or '').lower() == 'entra' and any(term in text for term in (
        'user consent is', 'user consent enabled', 'user consent policy', 'users can consent', 'users are allowed to consent',
        'default-user consent', 'default user consent', 'user self-consent',
        'admin consent is', 'admin consent required', 'consent policies assigned',
    ))


def select_configuration_item(key, row, rec):
    from .investigation_details import _item, _text, _details
    finding = _text(rec, 'FindingKey')
    if key == 'app_consent_policy_detail':
        if not is_consent_configuration(rec) and 'consent' not in finding:
            return None
        if _text(row, 'Setting') == 'Policy assigned to the default user role' and _text(row, 'Value') != 'User self-consent':
            return None
        return _item('Consent configuration', _text(row, 'Setting'),
                     _text(row, 'Policy ID') or 'authorizationPolicy/defaultUserRolePermissions',
                     'Review the consent configuration that supports this callout', _text(row, 'Value'),
                     _details(row, 'Policy ID', 'Source State', 'Evidence Confidence'),
                     _text(rec, 'ObservationDate'), 'Entra admin center > Enterprise applications > Consent and permissions',
                     'Review the assigned permission-grant policy and its conditions. A self-consent assignment alone does not establish which permissions a user may approve.')
    if key != 'sharepoint_governance_detail':
        return None
    if _text(row, 'Detail Type') != 'Tenant setting':
        return None
    wanted = {
        'sharepoint.sharing.organization_default': {'DefaultSharingLinkType', 'SharingCapability', 'OneDriveSharingCapability'},
        'sharepoint.sharing.permissive_anonymous_defaults': {'SharingCapability', 'OneDriveSharingCapability', 'DefaultSharingLinkType', 'FileAnonymousLinkType', 'FolderAnonymousLinkType', 'RequireAnonymousLinksExpireInDays'},
        'sharepoint.sharing.anyone_enabled': {'SharingCapability', 'OneDriveSharingCapability'},
        'sharepoint.authentication.legacy_permitted': {'LegacyAuthProtocolsEnabled', 'LegacyBrowserAuthProtocolsEnabled'},
    }.get(finding)
    if wanted is None and finding != 'sharepoint.sharing.partial_settings':
        return None
    setting = _text(row, 'Setting Key')
    if wanted is not None and setting not in wanted:
        return None
    return _item('Tenant setting', _text(row, 'Setting or report'), setting,
                 'Tenant configuration supporting the sharing or access review; this does not identify actual exposed content',
                 _text(row, 'Value or status'), _text(row, 'Why it matters'), _text(rec, 'ObservationDate'),
                 'SharePoint admin center > Policies > Sharing / Access control',
                 'Validate the displayed setting and relevant site exceptions. Use completed sharing or sign-in reports to identify actual access events.')


def source_limitations(bundle, rec, detail_keys):
    states = bundle.get('source_statuses') or {}
    requested = [str(rec.get('EvidenceSource') or '')]
    for key in detail_keys:
        requested.extend(SOURCE_KEYS.get(key, ()))
    output = []
    seen = set()
    for requested_key in requested:
        if not requested_key:
            continue
        actual = next((key for key in (requested_key, requested_key.removeprefix('entra_'), 'entra_' + requested_key) if key in states), None)
        if not actual or actual in seen or not isinstance(states[actual], dict):
            continue
        seen.add(actual)
        state = states[actual]
        status = str(state.get('availability_status') or state.get('status') or 'unknown')
        if status.lower() == 'available' and not state.get('truncated') and state.get('complete') is not False:
            continue
        reason = str(state.get('reason') or state.get('error') or state.get('unlock') or '').strip()
        if state.get('truncated'):
            reason = (reason + ' The returned records were truncated.').strip()
        output.append(f'{actual}: {status}' + (f' — {reason}' if reason else '; the source did not establish complete coverage.'))
    return list(dict.fromkeys(output))


def supporting_range(sheet):
    from .investigation_details import _ref
    rows = sheet.get('rows') or []
    if not rows:
        return ''
    width = len(dict.fromkeys(field for row in rows for field in row))
    return _ref(sheet['title'], 2, len(rows) + 1, width)


def explain_missing_detail(bundle, rec, detail_keys):
    """Return an honest source explanation, never an invented affected object."""
    observation = str(rec.get('OriginalObservation') or rec.get('Observation') or '').strip()
    limitations = source_limitations(bundle, rec, detail_keys)
    basis = str(rec.get('EvidenceBasis') or '').casefold()
    if basis == 'workload inventory context':
        feature = str(rec.get('OriginalFeature') or rec.get('Feature') or 'this workload')
        return 'Details unavailable', (
            f'The recommendation for {feature} supplies workload inventory summaries without the underlying record-level report used to calculate its activity or adoption measures. '
            'A directory user or site inventory cannot reconstruct those activity records. Obtain the dated detailed workload report for the stated population and period before identifying individual users or objects for follow-up.'), ''
    if basis == 'license signal':
        return 'Planning input needed', (
            'This next step is based on service-plan entitlement, not a measured population of affected users or objects. '
            'The license entry does not establish actual use, configuration, or a migration requirement. Confirm the intended use case and collect the relevant configuration or activity report before identifying items to change.'), ''

    if rec.get('ControlId') == 'ADOPTION.BASELINE' or rec.get('FindingKey') == 'coverage.adoption.baseline':
        return 'Planning input needed', 'This recommendation requires business decisions about use cases, participants and success measures; there is no affected-object population to export.', ''
    if rec.get('ActionType') == 'Evidence' or rec.get('Disposition') == 'Coverage':
        reason = ' '.join(limitations) or observation
        return 'Evidence needed', (reason + ' No affected-object list is established by this evidence gap.').strip(), ''
    # A configuration-absence finding can be supported by a complete inventory.
    # Link that inventory explicitly without calling every row an affected item.
    sheets = bundle.get('sheets') or {}
    configuration = ('conditional_access_detail', 'access_review_detail', 'purview_policy_detail', 'app_consent_policy_detail')
    for key in detail_keys:
        sheet = sheets.get(key) or {}
        if key in configuration and sheet.get('rows') and not limitations:
            location = supporting_range(sheet)
            return 'Review supporting configuration', (
                f'The collected configuration is at {location}. No individual affected-object subset is established for this callout; '
                'use these settings and policy scopes to validate the stated control gap.'), location
    if limitations:
        return 'Details unavailable', ' '.join(limitations), ''
    titles = [str((sheets.get(key) or {}).get('title') or key) for key in detail_keys]
    source = ', '.join(titles) or str(rec.get('EvidenceSource') or 'the saved collection')
    if any(key in configuration for key in detail_keys) and re.search(r'\bno\b|\bnone\b|\bzero\b', observation, re.I):
        return 'Configuration review needed', (
            f'{source} does not contain an affected-object list for this absence or configuration finding. '
            + observation + ' Confirm the specified control and collection scope before creating or changing it.'), ''
    return 'Detail mapping missing', (
        f'No specific affected items are mapped from {source} to this recommended action. '
        'The recommendation supplied neither selected supporting records nor a source-specific absence or unavailability declaration. '
        'Its aggregate or conclusion cannot be independently reproduced from the linked export. '
        'Obtain the dated underlying source records and validate the stated scope before acting.'), ''

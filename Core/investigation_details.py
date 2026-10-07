"""A short, finding-specific worklist built from existing engineer detail tables.

Only items that meet an explicit review condition are selected. Healthy inventory,
aggregate metrics, and missing-control findings never become invented affected
objects. This module neither collects data nor changes assessment conclusions.
"""

import re


FIELDS = ('RecommendationId', 'Finding', 'Item Type', 'Item', 'Identifier / URL',
          'Why Review', 'Current State', 'Relevant Details', 'Observed / Last Activity',
          'Where to Review', 'Source Detail', 'Next Step')


def _text(row, key):
    value = row.get(key)
    return '' if value is None else str(value).strip()


def _first(row, *keys):
    return next((_text(row, key) for key in keys if _text(row, key)), '')


def _yes(value):
    return value is True or str(value).lower() in {'yes', 'true'}


def _details(row, *keys):
    return '; '.join(f'{key}: {_text(row, key)}' for key in keys if _text(row, key))


def _tokens(value):
    return {part.strip().lower() for part in str(value or '').split(';') if part.strip()}


def _column(number):
    value = ''
    while number:
        number, part = divmod(number - 1, 26)
        value = chr(65 + part) + value
    return value


def _ref(title, first, last, columns):
    return "'" + title.replace("'", "''") + f"'!A{first}:{_column(columns)}{last}"


def _sheet_rows(sheets, key):
    sheet = sheets.get(key) or {}
    rows = sheet.get('rows') or []
    width = len(dict.fromkeys(field for row in rows for field in row))
    for number, row in enumerate(rows, 2):
        yield row, _ref(sheet.get('title', key), number, number, width)


def _item(kind, name, identifier, reason, state, details, observed, portal, next_step):
    return dict(zip(FIELDS[2:10], (kind, name or 'Name not returned', identifier or 'Identifier not returned',
                    reason, state or 'Not returned', details, observed or 'Not returned', portal)), **{'Next Step': next_step})


def _select(key, row, rec):
    if key in {'entra_device_detail', 'guest_access_detail', 'access_review_detail', 'conditional_access_detail'}:
        from .identity_investigation import select_identity_item
        return select_identity_item(key, row, rec)
    if key in {'purview_policy_detail', 'defender_incident_detail', 'power_platform_detail', 'data_exposure_detail'}:
        from .workload_investigation import select_workload_item
        return select_workload_item(key, row, rec)
    if key in {'app_consent_policy_detail', 'sharepoint_governance_detail'}:
        from .investigation_context import select_configuration_item
        return select_configuration_item(key, row, rec)
    text = ' '.join(_text(rec, field) for field in ('OriginalFeature', 'Feature', 'Observation', 'FindingKey')).lower()
    finding_key = _text(rec, 'FindingKey')
    observation = (_text(rec, 'OriginalObservation') or _text(rec, 'Observation')).lower()
    if key == 'legacy_signin_detail':
        return _item('Sign-in event', _first(row, 'User Principal Name', 'User Display Name', 'User ID'),
            _text(row, 'Sign-In ID'), 'Sign-in client matches the legacy-authentication classification',
            _text(row, 'Outcome'),
            _details(row, 'Application Name', 'Application ID', 'Resource Name', 'Client App Used (reported client type)',
                     'IP Address', 'Error Code', 'Outcome Detail', 'Failure Reason', 'Conditional Access Status', 'Correlation ID'),
            _text(row, 'Created UTC'), 'Entra admin center > Monitoring & health > Sign-in logs',
            'Locate this event by its ID and timestamp. Review the user, client, application, IP address, result and Conditional Access evaluation before changing authentication.')
    if key == 'mfa_registration_detail':
        return _item('User registration', _first(row, 'User Principal Name', 'User Display Name', 'User ID'),
            _text(row, 'User ID'), 'Registration report explicitly indicates MFA is not registered',
            'MFA not registered', _details(row, 'User Type', 'Is Admin', 'Methods Registered', 'MFA Capable'),
            _text(row, 'Last Updated UTC'), 'Entra admin center > Authentication methods > Activity > Registration',
            'Confirm that this account is in scope, including guest and service-account exceptions, and arrange MFA registration. Registration alone does not establish sign-in enforcement.')
    if key == 'app_access_detail':
        flags = _text(row, 'Flagged Because')
        if not flags or _text(row, 'App Display Name').lower() == 'not assessed':
            return None
        requested = []
        if 'unverified' in text or 'did not return verified-publisher metadata' in text:
            requested.append('unverified publisher')
        if any(term in text for term in ('high-privilege', 'high privilege', 'high-impact', 'high impact')):
            requested.append('high-privilege')
        if 'over-privileged' in text:
            requested.append('over-privileged')
        if requested and not any(term in flags.lower() for term in requested):
            return None
        return _item('Application', _text(row, 'App Display Name'),
            _first(row, 'Enterprise Application Object ID', 'Application (Client) ID'), flags,
            _text(row, 'Publisher Verification State'),
            _details(row, 'Application (Client) ID', 'Granted Scopes', 'Consent Type', 'High Privilege Match Count', 'Activity Band'),
            _text(row, 'Last Activity'), 'Entra admin center > Enterprise applications > Permissions',
            'Validate the app owner, granted permissions and business use before changing consent.')
    if key == 'defender_device_detail':
        risk, exposure = _text(row, 'Risk Score').lower(), _text(row, 'Exposure Level').lower()
        onboarding = _text(row, 'Onboarding Status').lower()
        if 'onboard' in text:
            if onboarding not in {'canbeonboarded', 'notonboarded', 'offboarded'}:
                return None
            reason = 'Device is not currently onboarded'
        elif finding_key == 'defender.devices.high_risk' or any(term in text for term in ('high-risk device', 'high risk device', 'devices with high risk')):
            if risk != 'high':
                return None
            reason = 'High device risk'
        elif risk in {'high', 'medium'} or exposure in {'high', 'medium'}:
            reason = _text(row, 'Reason Flagged')
        else:
            return None
        return _item('Device', _text(row, 'Device Name'), _text(row, 'Device ID'), reason,
            _text(row, 'Health Status'), _details(row, 'Risk Score', 'Exposure Level', 'Onboarding Status', 'OS Platform'),
            _text(row, 'Last Seen'), 'Microsoft Defender > Assets > Devices',
            'Review alerts and exposure on this device; confirm whether it is used by pilot participants.')
    if key == 'identity_risk_detail':
        kind, state, level = _text(row, 'Record Type'), _text(row, 'Risk State').lower(), _text(row, 'Risk Level').lower()
        if kind not in {'Risky User', 'Risk Detection'} or state in {'remediated', 'dismissed', 'confirmedsafe', 'none'}:
            return None
        if finding_key == 'entra.identity_risk.users' and (kind != 'Risky User' or state not in {'atrisk', 'confirmedcompromised'}):
            return None
        # The legacy Identity Protection recommendations have no FindingKey.
        # Their leading population distinguishes confirmed compromise from a
        # general risk review; subgroup counts elsewhere are not selectors.
        compromised_users = bool(re.match(r'^\d+\s+confirmed[ -]compromised\s+users?\b', observation))
        if compromised_users and (kind != 'Risky User' or state != 'confirmedcompromised'):
            return None
        if kind == 'Risk Detection' and 'risky user' in text and 'risk detection' not in text:
            return None
        if level not in {'high', 'medium'} and state not in {'atrisk', 'confirmedcompromised'}:
            return None
        return _item(kind, _first(row, 'User Principal Name', 'Display Name'), _text(row, 'Object ID'),
            'Identity risk still requires review', _text(row, 'Risk State'), _details(row, 'Risk Level', 'Risk Detail'),
            _text(row, 'Last Updated'), 'Entra admin center > Protection > Identity Protection',
            'Review the risk detections and recent sign-ins, then confirm remediation or dismissal.')
    if key == 'admin_role_detail':
        assignment = _text(row, 'Assignment Type')
        if assignment not in {'Permanent Active', 'Active (Duration Unverified)'}:
            return None
        # Legacy PIM recommendations have no FindingKey. Match their leading
        # population, not an "including N Global Administrators" subgroup.
        global_only = bool(re.match(r'^\d+\s+(?:permanent\s+)?global admin', observation))
        if global_only and 'global admin' not in _text(row, 'Role Name').lower():
            return None
        standing_only = bool(re.search(r'\bpermanent\b|\bno[ -]expiration\b', observation))
        if standing_only and assignment != 'Permanent Active':
            return None
        return _item('Role assignment', _text(row, 'Principal Display Name'),
            _first(row, 'Assignment ID', 'Principal ID'), _text(row, 'Reason Flagged'), assignment,
            _details(row, 'Role Name', 'Principal ID', 'Role Definition ID', 'Directory Scope ID', 'End Date'),
            _text(row, 'Start Date'), 'Entra admin center > Privileged Identity Management > Microsoft Entra roles',
            'Validate the principal and business need; review eligibility, duration and least privilege.')
    if key == 'sharepoint_lifecycle_detail':
        ownerless = 'ownerless' in text and _yes(row.get('Is Ownerless'))
        inactive = 'inactive' in text and _yes(row.get('Is Inactive'))
        if not (ownerless or inactive):
            return None
        reason = '; '.join(label for flag, label in ((ownerless, 'Reported ownerless'), (inactive, 'Reported inactive')) if flag)
        return _item('SharePoint site', _first(row, 'Site Name', 'Site URL'), _text(row, 'Site URL'), reason,
            _text(row, 'Site Lock State'), _details(row, 'Owner', 'Sensitivity Label', 'Source File'),
            _first(row, 'Last Activity Date', 'Report Date'), 'SharePoint admin center > Active sites',
            'Confirm ownership and continued business need with the site owner before archiving or removing access.')
    if key == 'external_connection_detail' and finding_key == 'baseline.connectors.inventory':
        if (_text(row, 'State').lower() == 'available; empty'
                or (_text(row, 'Connection Name') == 'No connections returned'
                    and not _first(row, 'Connection ID', 'ID'))):
            return None
        return _item('Copilot connector', _text(row, 'Connection Name'), _first(row, 'Connection ID', 'ID'),
            'Connector ownership and indexed-content permissions require review', _text(row, 'State'),
            _text(row, 'Description'), _text(rec, 'ObservationDate'), 'Microsoft 365 admin center > Copilot > Connectors',
            'Verify the owner, indexed content and item permission mapping before including the connector in the pilot.')
    return None


def _sharing_items(bundle, rec):
    if _text(rec, 'FindingKey') not in {'sharepoint.sharing.anyone_enabled', 'sharepoint.sharing.permissive_anonymous_defaults'}:
        return
    from .sharepoint_governance import setting_name, ANYONE_VALUES
    from .export_recommendations import _excel_safe_rows
    from hashlib import sha256
    import json
    selected = [site for site in (bundle.get('sharepoint_governance') or {}).get('sites', [])
                if setting_name('SharingCapability', site.get('SharingCapability')) in ANYONE_VALUES]
    if not selected:
        return
    key = 'sharepoint_site_configuration_detail'
    sheets = bundle.setdefault('sheets', {})
    if key not in sheets:
        context = bundle.get('collection_context') or {}
        rows = [dict(site, **{'RecommendationId': '', 'Evidence ID': 'INV-' + sha256(json.dumps(site, sort_keys=True, default=str).encode()).hexdigest()[:20],
                'Source File': context.get('source_file', 'Not retained'), 'Collected At': context.get('collected_at', 'Not retained'),
                'Collection Scope': 'Retained SharePoint site configurations permitting Anyone links; actual sharing links were not collected by this query.'}) for site in selected]
        title = 'Sharing Site Configuration'
        used = {sheet.get('title', '').casefold() for sheet in sheets.values()}
        suffix = 2
        while title.casefold() in used:
            title, suffix = f'Sharing Site Config {suffix}', suffix + 1
        sheets[key] = {'title': title, 'rows': _excel_safe_rows(rows, title), 'restricted': True, 'preview_columns': [],
                       'details': ['Selection: site SharingCapability permits Anyone links. This is configuration evidence, not actual sharing-link or recipient evidence.'],
                       'summary': 'Site configurations permitting Anyone links.'}
    for site, source in _sheet_rows(sheets, key):
        identifiers = set(filter(None, str(site.get('RecommendationId') or '').split('; ')))
        identifiers.add(rec['RecommendationId'])
        site['RecommendationId'] = '; '.join(sorted(identifiers))
        yield _item('SharePoint site', _first(site, 'Title', 'Url', 'URL'), _first(site, 'Url', 'URL', 'SiteId'),
            'Site configuration permits Anyone links; actual links require a sharing report',
            _text(site, 'SharingCapability'), _details(site, 'Owner', 'Template', 'LockState'),
            _text(rec, 'ObservationDate'), 'SharePoint admin center > Active sites > Settings > More sharing settings',
            'Confirm whether Anyone sharing is required and review the site sharing links.'), source


def _scope_application_grants(sheets, actions):
    """Keep only grants used by the same selected application worklists."""
    from .investigation_context import is_consent_configuration
    sheet = sheets.get('application_grant_detail')
    if not sheet:
        return
    source_rows = sheet.setdefault('_candidate_rows', [dict(row) for row in sheet.get('rows') or []])
    source_details = sheet.setdefault('_source_details', list(sheet.get('details') or []))
    app_actions = [rec for rec in actions if 'app_access_detail' in {key.strip() for key in str(rec.get('EvidenceKey') or '').split(';')}
                   and not is_consent_configuration(rec)
                   and rec.get('Historical') != 'Yes' and rec.get('SourceType') != 'prior_assessment']
    selected = [row for row in (sheets.get('app_access_detail') or {}).get('rows') or []
                if any(_select('app_access_detail', row, rec) for rec in app_actions)]
    wanted = {_text(row, 'Enterprise Application Object ID').casefold() for row in selected}
    wanted.discard('')
    rows = [dict(row, RecommendationId='') for row in source_rows
            if _text(row, 'Client Service Principal ID').casefold() in wanted]
    order = {_text(app, 'Enterprise Application Object ID').casefold(): index for index, app in enumerate(selected)}
    rows.sort(key=lambda row: order[_text(row, 'Client Service Principal ID').casefold()])
    sheet['rows'] = rows
    sheet['app_ranges'] = {}
    width = len(dict.fromkeys(field for row in rows for field in row))
    for app in selected:
        identifier = _text(app, 'Enterprise Application Object ID')
        positions = [index for index, row in enumerate(rows, 2)
                     if _text(row, 'Client Service Principal ID').casefold() == identifier.casefold()]
        if positions:
            sheet['app_ranges'][identifier] = _ref(sheet['title'], min(positions), max(positions), width)
    matched = len(sheet['app_ranges'])
    missing = len(selected) - matched
    high = sum(_yes(row.get('High Privilege Match')) for row in rows)
    note = (f'{len(rows)} retained delegated-grant records support {matched} of {len(selected)} applications selected by recommended next steps. '
            f'{high} grant records match the high-privilege scope rule. Application counts are distinct client service principals; an application can have several grants, resources or consenting principals.')
    if any(_text(row, 'Source State').lower() != 'available' for row in rows):
        note += ' Source completeness is not established; counts describe retained records only.'
    if missing:
        note += f' {missing} selected applications have no matched retained grant record; no substitute grant rows were created.'
    sheet.update(matched_count=len(rows), flagged_app_count=len(selected), matched_app_count=matched,
                 unmatched_app_count=missing, high_privilege_grant_count=high,
                 unique_grant_count=len({_text(row, 'Grant ID') for row in rows if _text(row, 'Grant ID')}),
                 reconciliation_note=note, summary=note,
                 details=[note, *source_details[1:]])


def prepare_investigation_details(bundle, result):
    """Require evidence or a visible, specific gap for every recommended next step."""
    from .investigation_contract import materialize_declared_evidence, recommended_actions, validate_investigation_coverage
    from .investigation_context import explain_missing_detail, is_consent_configuration, source_limitations, supporting_range
    signature = (id(result), tuple(row.get('RecommendationId') for row in result.get('recommendations',[])))
    if bundle.get('_split_investigation_signature') == signature:
        return result
    sheets = bundle.setdefault('sheets', {})
    from .raw_evidence import prepare_raw_details
    prepare_raw_details(bundle, result)
    sheets.pop('investigation_items', None)
    sheets.pop('sharepoint_site_configuration_detail', None)
    actions = recommended_actions(result)
    by_id = materialize_declared_evidence(bundle, actions)
    for rec in actions:
        if rec.get('_shared_inventory_support') and rec['RecommendationId'] not in by_id:
            by_id[rec['RecommendationId']] = dict(rec['_shared_inventory_support'])
    _scope_application_grants(sheets, [rec for rec in actions if rec.get('RecommendationId') not in by_id])
    used_titles = {sheet.get('title', '').casefold() for sheet in sheets.values()}
    title, suffix = 'Investigation Items', 2
    while title.casefold() in used_titles:
        title, suffix = f'Investigation Items {suffix}', suffix + 1
    all_rows = []
    for rec in actions:
        identifier = _text(rec, 'RecommendationId')
        if identifier in by_id:
            continue
        detail_keys = [key.strip() for key in str(rec.get('EvidenceKey') or '').split(';') if key.strip()]
        if is_consent_configuration(rec):
            detail_keys = ['app_consent_policy_detail']
        direct_key = next((key for key in detail_keys if key in {'legacy_signin_detail', 'mfa_registration_detail', 'entra_device_detail'}), None)
        historical = rec.get('Historical') == 'Yes' or rec.get('SourceType') == 'prior_assessment'
        candidates = []
        if not historical:
            for key in detail_keys:
                for row, source in _sheet_rows(sheets, key):
                    item = _select(key, row, rec)
                    if item:
                        if key == 'app_access_detail':
                            source = (sheets.get('application_grant_detail') or {}).get('app_ranges', {}).get(
                                _text(row, 'Enterprise Application Object ID'), source)
                        candidates.append((item, source))
            candidates.extend(_sharing_items(bundle, rec) or [])
        first, seen = len(all_rows) + 2, set()
        for item, source in candidates:
            # Each retained source row is independently investigable, including
            # repeated events and grants involving the same user or object.
            identity = (item['Item Type'], item['Identifier / URL'], item['Item'], item['Why Review'], source)
            if identity in seen:
                continue
            seen.add(identity)
            match = re.fullmatch(r"'((?:[^']|'')+)'!([A-Z]+)([0-9]+):([A-Z]+)([0-9]+)", source)
            if match:
                source_sheet = next((sheet for sheet in sheets.values() if sheet.get('title') == match.group(1).replace("''", "'")), {})
                for source_row in (source_sheet.get('rows') or [])[int(match.group(3)) - 2:int(match.group(5)) - 1]:
                    if 'RecommendationId' in source_row:
                        identifiers = set(filter(None, str(source_row['RecommendationId'] or '').split('; ')))
                        identifiers.add(identifier)
                        source_row['RecommendationId'] = '; '.join(sorted(identifiers))
            item.update(RecommendationId=identifier, Finding=_first(rec, 'OriginalFeature', 'Feature'), **{'Source Detail': source})
            all_rows.append({field: item.get(field, '') for field in FIELDS})
        count = len(seen)
        location = _ref(title, first, first + count - 1, len(FIELDS)) if count else ''
        notes = source_limitations(bundle, rec, detail_keys)
        status = 'Records available'
        summary = f'{count} item{"s" if count != 1 else ""} to review' if count else ''
        if historical:
            status, summary = 'Historical evidence', 'Original evidence needed'
            notes.append('Historical callout: the original assessment did not retain its individual supporting records here. Obtain its dated source export before identifying affected items; current tenant records cannot establish the historical population.')
        elif direct_key:
            source_sheet = sheets.get(direct_key) or {}
            source_rows = source_sheet.get('rows') or []
            if count and len(source_rows) == count:
                location = supporting_range(source_sheet)
            label = 'sign-in records' if direct_key == 'legacy_signin_detail' else 'users to review' if direct_key == 'mfa_registration_detail' else 'devices to review'
            summary = f'{count} {label}' if count else 'Supporting records unavailable'
            notes.extend(str(part) for part in (source_sheet.get('reconciliation_note'), source_sheet.get('unavailability_reason')) if part)
            if not count:
                status = 'Evidence unavailable'
                if not notes:
                    notes.append('The saved collection did not retain the individual records supporting this finding. Recollect the source or supply its dated export; an aggregate count cannot identify affected items.')
            elif any('mismatch' in note.lower() for note in notes):
                status = 'Count mismatch'
        elif not count:
            summary, reason, location = explain_missing_detail(bundle, rec, detail_keys)
            notes.append(reason)
            status = ('Planning decision' if summary == 'Planning input needed' else
                      'Configuration evidence' if summary in {'Review supporting configuration', 'Configuration review needed'} else
                      'Evidence unavailable' if summary in {'Evidence needed', 'Details unavailable'} else 'Detail mapping missing')
            if location:
                # Older collections can retain configuration context without an
                # affected-object subset. Count the linked supporting rows, not
                # a fictional zero-row range, and qualify their role explicitly.
                from openpyxl.utils.cell import range_boundaries
                linked = re.fullmatch(r"'((?:[^']|'')+)'!([A-Z]+[0-9]+:[A-Z]+[0-9]+)",location)
                if linked:
                    _,start,_,end = range_boundaries(linked.group(2))
                    count = end-start+1
                    summary = f'{count} configuration context record(s)'
                    notes.append('These linked rows are configuration context. No selected affected-object population is established.')
        else:
            if any(item['Item Type'] in {'Consent configuration', 'Tenant setting', 'Conditional Access policy', 'DLP Policy', 'Label Policy'} for item, _ in candidates):
                status = 'Configuration evidence'
            if 'app_access_detail' in detail_keys:
                notes.append('Counts here are distinct applications; one application can contribute several grant instances to the finding. Each Source Detail link opens its retained individual grants when available.')
                grant_sheet = sheets.get('application_grant_detail') or {}
                notes.extend(str(grant_sheet[key]) for key in ('reconciliation_note', 'unavailability_reason') if grant_sheet.get(key))
            if 'conditional_access_detail' in detail_keys:
                inventory = supporting_range(sheets.get('conditional_access_detail') or {})
                if inventory:
                    notes.append(f'Validate the control conclusion against the full policy inventory and Security Defaults at {inventory}; selected policies alone do not prove an absence of tenant-wide enforcement.')
            notes.append(f'{count} selected source record(s) are listed for this callout. Each Source Detail location identifies the retained supporting row or grant group.')
        if count and rec.get('ActionType') == 'Confirmation':
            notes.append('These items reflect the supplied evidence date; confirm their current state.')
        note = ' '.join(dict.fromkeys(part.strip() for part in notes if part))
        by_id[identifier] = {'InvestigationCount': count, 'InvestigationRange': location,
            'InvestigationSummary': summary, 'InvestigationQualification': note,
            'InvestigationNote': note, 'InvestigationPublicQualification': note, 'InvestigationStatus': status}
    if all_rows:
        sheets['investigation_items'] = {'title': title, 'rows': all_rows, 'summary': 'Specific items requiring investigation, grouped by recommended next step.',
            'details': [], 'preview_columns': [], 'restricted': True}
    visited = set()
    def apply(value):
        if isinstance(value, (dict, list)):
            if id(value) in visited:
                return
            visited.add(id(value))
            if isinstance(value, dict):
                fields = by_id.get(value.get('RecommendationId'))
                if fields:
                    value.setdefault('InvestigationOriginalEvidenceSheet', value.get('EvidenceSheet', ''))
                    value.update(fields)
                    value['EvidenceSheet'] = fields['InvestigationRange'] or ''
                else:
                    for child in list(value.values()):
                        apply(child)
            else:
                for child in value:
                    apply(child)
    apply(result)
    apply(bundle.get('recommendations') or [])
    for row in bundle.get('evidence_index') or []:
        fields = by_id.get(row.get('RecommendationId'))
        if fields:
            row.update(fields)
            row['Workbook Tab'] = fields['InvestigationRange']
            row['Engineer Follow-Up'] = fields['InvestigationSummary']
    bundle['investigation_coverage'] = [dict(RecommendationId=key, **fields) for key, fields in by_id.items()]
    from .raw_evidence import split_evidence_sheets
    split_evidence_sheets(bundle, result, bundle.get('worksheet_row_capacity',1_048_575))
    # Later export calls reuse these exact sheets and ranges, so the shared
    # finding-evidence model, workbook and HTML describe identical rows.
    bundle['_split_investigation_signature'] = signature
    bundle['investigation_validation'] = validate_investigation_coverage(result, bundle)
    return result

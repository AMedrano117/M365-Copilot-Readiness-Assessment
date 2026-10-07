"""One finding-evidence model shared by the App Builder files, HTML evidence pages and workbook.

It is built from the dashboard export, the shared projection of the assessment result,
so every deliverable shows the same findings, DET-/EVD-/SRC- identifiers, record counts
and units. These are pure functions: no files, tenant access or documentation lookups.
"""

from collections import Counter, OrderedDict
import json
import re

from .dashboard_export import _source_names, evidence_keys

MODEL_SCHEMA_VERSION = '1.0.0'

# (id, number, title, short title used in worksheet names)
CONCERNS = (
    ('legacy-authentication', 10, 'Legacy authentication', 'Legacy Auth'),
    ('mfa-registration', 11, 'MFA registration and methods', 'MFA'),
    ('conditional-access', 12, 'Conditional Access and sign-in policy', 'Cond Access'),
    ('admin-access', 13, 'Privileged and administrative access', 'Admin Access'),
    ('identity-risk', 14, 'Identity risk', 'Identity Risk'),
    ('app-consent', 15, 'Application consent and permissions', 'App Consent'),
    ('connectors-agents', 16, 'Connectors and agents', 'Connectors'),
    ('devices-endpoint', 17, 'Devices and endpoint protection', 'Devices'),
    ('security-incidents', 18, 'Security incidents', 'Incidents'),
    ('content-sharing', 19, 'Content sharing and oversharing', 'Sharing'),
    ('data-protection', 20, 'Labels, DLP, audit and retention', 'Data Protection'),
    ('licensing-adoption', 21, 'Licensing, readiness and adoption', 'Licensing'),
    ('external-ai', 22, 'External AI services', 'External AI'),
    ('other', 99, 'Other findings', 'Other'),
)
_CONCERN_INFO = {identifier: (number, title, short) for identifier, number, title, short in CONCERNS}

_FINDING_KEY_CONCERNS = (
    ('entra.signins.legacy_auth', 'legacy-authentication'),
    ('sharepoint.authentication.legacy_permitted', 'legacy-authentication'),
    ('entra.authentication.mfa_registration', 'mfa-registration'),
    ('baseline.identity.', 'conditional-access'), ('operation.identity.auth', 'conditional-access'),
    ('entra.identity_risk.', 'identity-risk'),
    ('entra.apps.', 'app-consent'), ('entra.app_consent.', 'app-consent'),
    ('entra.devices.', 'devices-endpoint'), ('defender.devices.', 'devices-endpoint'),
    ('expanded.antivirus_health', 'devices-endpoint'), ('expanded.windows_protection', 'devices-endpoint'),
    ('defender.incidents.', 'security-incidents'),
    ('sharepoint.sharing.', 'content-sharing'), ('sharepoint.dag.', 'content-sharing'),
    ('sharepoint.governance.', 'content-sharing'), ('sharepoint.copilot.', 'content-sharing'),
    ('data_exposure.', 'content-sharing'),
    ('purview.', 'data-protection'), ('baseline.dlp.', 'data-protection'), ('baseline.labels.', 'data-protection'),
    ('baseline.retention.', 'data-protection'), ('copilot.audit.', 'data-protection'),
    ('baseline.connectors.', 'connectors-agents'),
    ('baseline.license.', 'licensing-adoption'), ('m365.portal_copilot_readiness', 'licensing-adoption'),
)
_EVIDENCE_KEY_CONCERNS = {
    'legacy_signin_detail': 'legacy-authentication',
    'mfa_registration_detail': 'mfa-registration', 'authentication_detail': 'mfa-registration',
    'authentication_methods_detail': 'mfa-registration', 'authentication_preferences_detail': 'mfa-registration',
    'authentication_populations_detail': 'mfa-registration',
    'conditional_access_detail': 'conditional-access',
    'admin_role_detail': 'admin-access', 'access_review_detail': 'admin-access', 'standing_access_detail': 'admin-access',
    'identity_risk_detail': 'identity-risk', 'risky_user_detail': 'identity-risk',
    'app_consent_policy_detail': 'app-consent', 'app_access_detail': 'app-consent',
    'external_connection_detail': 'connectors-agents', 'power_platform_detail': 'connectors-agents',
    'entra_device_detail': 'devices-endpoint', 'defender_device_detail': 'devices-endpoint',
    'defender_incident_detail': 'security-incidents',
    'sharepoint_governance_detail': 'content-sharing', 'data_exposure_detail': 'content-sharing',
    'sharepoint_lifecycle_detail': 'content-sharing', 'guest_access_detail': 'content-sharing',
    'purview_policy_detail': 'data-protection',
    'ai_usage_detail': 'licensing-adoption', 'copilot_readiness_detail': 'licensing-adoption',
    'copilot_user_usage_detail': 'licensing-adoption', 'copilot_readiness_user_detail': 'licensing-adoption',
    'm365_activity_detail': 'licensing-adoption', 'service_plan_inventory': 'licensing-adoption',
    'license_assignment_detail': 'licensing-adoption',
}
_CONTROL_CONCERNS = (
    ('IDENTITY.LEGACY', 'legacy-authentication'),
    ('IDENTITY.MFA', 'mfa-registration'), ('IDENTITY.REGISTRATION', 'mfa-registration'),
    ('IDENTITY.METHODS', 'mfa-registration'), ('IDENTITY.DEFAULTS', 'mfa-registration'),
    ('IDENTITY.PASSWORDLESS', 'mfa-registration'), ('IDENTITY.SSPR', 'mfa-registration'),
    ('IDENTITY.ADMIN', 'admin-access'), ('IDENTITY.', 'conditional-access'), ('IDENTITY-', 'conditional-access'),
    ('DATA.EXPOSURE', 'content-sharing'), ('CONTENT.', 'content-sharing'),
    ('DATA.', 'data-protection'), ('DATA-', 'data-protection'), ('GOV-', 'data-protection'),
    ('GOVERNANCE.', 'data-protection'), ('CLASSIFICATION.', 'data-protection'),
    ('APPS.CONNECTIONS', 'connectors-agents'), ('AGENTS.', 'connectors-agents'), ('ACTIONS-', 'connectors-agents'),
    ('APPS', 'app-consent'), ('APPLICATIONS.', 'app-consent'),
    ('ENDPOINT.', 'devices-endpoint'), ('DEVICES.', 'devices-endpoint'),
    ('THREAT', 'security-incidents'), ('DEFENDER.', 'security-incidents'),
    ('LICENSE.', 'licensing-adoption'), ('ADOPTION', 'licensing-adoption'), ('SCOPE.', 'licensing-adoption'),
    ('EXTERNAL', 'external-ai'), ('EGRESS-', 'external-ai'), ('EXTERNAL_AI.', 'external-ai'),
)
_DOMAIN_CONCERNS = {'scope': 'licensing-adoption', 'licensing': 'licensing-adoption', 'adoption': 'licensing-adoption',
                    'identity': 'conditional-access', 'devices': 'devices-endpoint', 'endpoints': 'devices-endpoint',
                    'defender': 'security-incidents', 'content': 'content-sharing', 'classification': 'data-protection',
                    'governance': 'data-protection', 'data_protection': 'data-protection',
                    'applications': 'app-consent', 'external_ai': 'external-ai'}
_RELATED = {'legacy-authentication': ['conditional-access'], 'conditional-access': ['legacy-authentication', 'mfa-registration'],
            'mfa-registration': ['conditional-access'], 'content-sharing': ['data-protection'], 'data-protection': ['content-sharing']}

EVIDENCE_KINDS = ('observed_event', 'entity_state', 'configuration', 'supporting_context', 'aggregate_only', 'none')
DATASET_KINDS = {
    'signin_logs': 'observed_event', 'copilot_audit': 'observed_event', 'alerts': 'observed_event',
    'incidents': 'observed_event', 'risk_detections': 'observed_event', 'dlp_alerts': 'observed_event',
    'ca_policies': 'configuration', 'authentication_strengths': 'configuration', 'authorization_policy': 'configuration',
    'consent_policies': 'configuration', 'security_defaults': 'configuration', 'cross_tenant_access_policy': 'configuration',
    'compliance_policies': 'configuration', 'compliance_assignments': 'configuration', 'role_definitions': 'configuration',
    'sharepoint_tenant_settings': 'configuration', 'sharepoint_site_settings': 'configuration',
    'sharepoint_graph_settings': 'configuration', 'sensitivity_labels': 'configuration', 'label_policies': 'configuration',
    'dlp_policies': 'configuration', 'dlp_rules': 'configuration', 'retention_policies': 'configuration',
    'retention_labels': 'configuration', 'audit_config': 'configuration', 'irm_config': 'configuration',
    'external_connections': 'configuration', 'access_reviews': 'configuration', 'shadow_streams': 'configuration',
    'users': 'entity_state', 'license_users': 'entity_state', 'guest_users': 'entity_state', 'auth_methods': 'entity_state',
    'user_signin_activity': 'entity_state', 'role_assignments': 'entity_state', 'role_assignment_schedules': 'entity_state',
    'role_eligibility_schedules': 'entity_state', 'risky_users': 'entity_state', 'service_principals': 'entity_state',
    'oauth_grants': 'entity_state', 'application_permissions': 'entity_state', 'application_owners': 'entity_state',
    'service_principal_signin_activities': 'entity_state', 'managed_devices': 'entity_state',
    'directory_devices': 'entity_state', 'machines': 'entity_state', 'antivirus_health': 'entity_state',
    'windows_protection': 'entity_state', 'compliance_setting_states': 'entity_state', 'ca_group_members': 'entity_state',
    'sites': 'entity_state', 'licenses': 'entity_state', 'copilot_readiness': 'entity_state', 'copilot_usage': 'entity_state',
    'office_activations': 'entity_state', 'entra_impacted_resources': 'entity_state', 'shadow_entities': 'entity_state',
    'shadow_app_catalog': 'entity_state',
    'application_signin_summary': 'aggregate_only', 'compliance_setting_summaries': 'aggregate_only',
    'shadow_ai': 'aggregate_only', 'content_exposure': 'aggregate_only', 'sensitive_exposure': 'aggregate_only',
    'label_coverage': 'aggregate_only', 'email_activity': 'aggregate_only', 'teams_activity': 'aggregate_only',
    'sharepoint_usage': 'aggregate_only', 'onedrive_usage': 'aggregate_only', 'active_users': 'aggregate_only',
    'entra_recommendations': 'supporting_context', 'context': 'supporting_context', 'check_reviews': 'supporting_context',
}
_RECORD_TYPES = {
    # record_type: (record unit, evidence kind, entity unit, entity identifier fields, table slug, table title)
    'legacy_signin_event': ('sign-in events', 'observed_event', 'accounts', ('userId', 'userPrincipalName'), 'signin-events', 'Sign-ins'),
    'user_mfa_registration': ('registration records', 'entity_state', 'users', ('userId', 'upn'), 'registration-gaps', 'Registration'),
    'standing_admin_assignment': ('role assignments', 'entity_state', 'principals', ('principalId',), 'standing-assignments', 'Assignments'),
    'risky_user': ('risky-user records', 'entity_state', 'users', ('userId', 'upn'), 'risky-users', 'Risky Users'),
    'user_consent_policy': ('consent policy assignments', 'configuration', None, (), 'consent-policies', 'Policies'),
    'application_permission_grant': ('grant records', 'entity_state', 'applications', ('servicePrincipalId', 'appId'), 'app-grants', 'Grants'),
    'security_incident': ('active incidents', 'observed_event', 'incidents', ('incidentId',), 'active-incidents', 'Incidents'),
    'content_exposure': ('exposure rows', 'aggregate_only', 'sites', ('siteUrl', 'siteId'), 'exposure', 'Exposure'),
    'sharing_configuration': ('sharing setting records', 'configuration', None, (), 'sharing-settings', 'Settings'),
    'antivirus_signature_health': ('device health records', 'entity_state', 'devices', ('deviceId',), 'antivirus-health', 'Antivirus'),
}
_GENERIC_UNITS = {'configuration': 'configuration records', 'selected_records': 'selected records',
                  'supporting_detail': 'supporting records', 'supporting_inventory': 'inventory records'}
COMMON_COLUMNS = ('finding_id', 'detail_record_id', 'evidence_record_ids', 'dataset', 'dataset_id', 'source_record_id',
                  'evidence_kind', 'collected_at', 'window_start', 'window_end', 'source_state', 'qualification')
CONTEXT_COLUMNS = ('finding_ids', 'evidence_record_id', 'evidence_record_ids', 'dataset', 'dataset_id', 'source_record_id',
                   'evidence_kind', 'collected_at', 'window_start', 'window_end', 'source_state', 'qualification')


def slug(value, limit=48):
    text = re.sub(r'[^a-z0-9]+', '-', str(value or '').lower()).strip('-')
    return text[:limit].strip('-') or 'records'


def concern_for(row):
    """Assign exactly one concern from finding content; positional IDs are never used."""
    key = str(row.get('FindingKey') or '')
    for prefix, concern in _FINDING_KEY_CONCERNS:
        if key == prefix or (prefix.endswith('.') and key.startswith(prefix)) or key.startswith(prefix + '.'):
            return concern
    for evidence_key in evidence_keys(row):
        if evidence_key in _EVIDENCE_KEY_CONCERNS:
            return _EVIDENCE_KEY_CONCERNS[evidence_key]
    control = str(row.get('ControlId') or '').upper()
    for prefix, concern in _CONTROL_CONCERNS:
        if control.startswith(prefix):
            return concern
    domain = str(row.get('AssessmentDomainId') or row.get('DomainId') or '').lower()
    return _DOMAIN_CONCERNS.get(domain, 'other')


def _cell(value):
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
    return value


def _source_complete(source):
    return (source.get('complete') is True and source.get('available') is not False and not source.get('truncated')
            and source.get('availability_status') in (None, 'available', 'empty'))


def _derived(dataset):
    return str(dataset or '').startswith(('declared.', 'investigation.'))


def _source_summary(entry):
    source = entry.get('source') or {}
    return {'dataset_id': entry['dataset_id'], 'dataset': entry['dataset'], 'dataset_index': entry.get('dataset_index'),
            'record_count': entry.get('record_count'),
            'collected_at': source.get('collected_at') or source.get('collection_completed_at') or source.get('refresh_date')
            or source.get('observed_at') or None,
            'window_start': source.get('window_start') or None, 'window_end': source.get('window_end') or None,
            'collection_window': source.get('collection_window') or source.get('period') or None,
            'availability_status': source.get('availability_status'), 'complete': source.get('complete'),
            'truncated': source.get('truncated'), 'pages_collected': source.get('pages_collected'),
            'scope': source.get('scope'), 'reason': source.get('reason'),
            'derived': _derived(entry['dataset']),
            'evidence_origin': source.get('evidence_origin')}


def _state(summary):
    if not summary:
        return 'unknown'
    if summary['derived']:
        return 'derived from retained workbook detail'
    status = summary.get('availability_status') or 'unknown'
    if summary.get('truncated'):
        return f'{status}; truncated'
    if summary.get('complete') is True:
        return f'{status}; complete'
    return f'{status}; completeness not established'


def _record_kind(record, record_type, dataset, finding_kind_hint):
    fields = record.get('fields') or {}
    if fields.get('recordGranularity') == 'site_summary':
        return 'aggregate_only'
    if record_type in _RECORD_TYPES:
        kind = _RECORD_TYPES[record_type][1]
        return 'entity_state' if record_type == 'content_exposure' and fields.get('recordGranularity') == 'item_or_permission' else kind
    if record_type == 'configuration':
        return 'configuration'
    if dataset in DATASET_KINDS:
        return DATASET_KINDS[dataset]
    return finding_kind_hint or 'entity_state'


def _worklist(row):
    count = row.get('InvestigationCount')
    summary = str(row.get('InvestigationSummary') or '').strip()
    match = re.fullmatch(r'(\d[\d,]*)\s+(.+)', summary)
    unit = match.group(2) if match else (summary or None)
    try:
        count = int(count) if count not in (None, '') else (int(match.group(1).replace(',', '')) if match else None)
    except (TypeError, ValueError):
        count = None
    return count, unit


def _entity_count(records, record_type):
    spec = _RECORD_TYPES.get(record_type)
    if not spec or not spec[2]:
        return None, None, ('Configuration records describe settings, not affected entities.' if spec else
                            'No stable entity identifier is mapped for these records; the record count is not an entity count.')
    unit, keys = spec[2], spec[3]
    identities, fallback, missing = set(), 0, 0
    for record in records:
        fields = record.get('fields') or {}
        value = next(((index, fields.get(key)) for index, key in enumerate(keys) if fields.get(key) not in (None, '')), None)
        if value is None:
            missing += 1
            continue
        fallback += value[0] > 0
        identities.add(str(value[1]).casefold())
    basis = f'Unique {keys[0]}'
    if len(keys) > 1:
        basis += f'; {keys[1]} used for {fallback} record(s) without {keys[0]}'
    if missing:
        basis += f'; {missing} record(s) without an identifier are not counted'
    return len(identities), unit, basis + '.'


def _missing_action(guidance, row, availability, record_status):
    needed = (guidance or {}).get('evidence_needed') or ''
    if availability == 'historical':
        return ('Historical summaries cannot recreate named records. Obtain the original dated export or run a new collection. '
                + needed).strip()
    if availability in {'unavailable', 'not_retained', 'partial', 'unknown'} or record_status == 'summary_only':
        reason = str(row.get('InvestigationQualification') or '').strip()
        parts = [needed, reason] if needed else [reason or 'Collect or export the source records for this check, then rebuild offline.']
        return ' '.join(dict.fromkeys(part for part in parts if part))
    return None


def build_finding_evidence(payload):
    """Return the shared model: concerns, per-finding evidence summaries and homogeneous row tables."""
    sources = {entry['dataset_id']: _source_summary(entry) for entry in payload.get('sources') or []}
    originals = {entry['dataset_id']: entry.get('source') or {} for entry in payload.get('sources') or []}
    by_name = {}
    for summary in sources.values():
        by_name.setdefault(summary['dataset'], []).append(summary)
    evidence_dataset = {entry['record_id']: entry['dataset_id'] for entry in payload.get('evidence_records') or []}
    rows = {row.get('RecommendationId'): row for row in payload.get('recommendations') or []}
    tables = OrderedDict()
    context_members = {}
    findings = []
    concern_findings = OrderedDict()
    for finding in payload.get('findings') or []:
        row = rows.get(finding['finding_id']) or {}
        concern = concern_for(row)
        records = finding.get('records') or []
        record_type = finding.get('record_type') or ''
        status = finding.get('record_status') or ''
        names = [name for name in _source_names(row) if name in by_name]
        record_sources = []
        for record in records:
            for identifier in record.get('evidence_record_ids') or []:
                dataset_id = evidence_dataset.get(identifier)
                if dataset_id and dataset_id not in record_sources:
                    record_sources.append(dataset_id)
        native = [sources[item] for item in record_sources if not sources[item]['derived']]
        if not native:
            native = [summary for name in names for summary in by_name[name]]
        if status in {'historical', 'planning'}:
            availability = status
        elif status == 'absence':
            availability = 'absent'
        elif status == 'unavailable':
            availability = 'unavailable' if native else 'not_retained'
        elif native:
            availability = 'complete' if all(_source_complete(originals[item['dataset_id']]) for item in native) else 'partial'
        else:
            availability = 'unknown'
        hint = next((DATASET_KINDS[name] for name in names if name in DATASET_KINDS), None)
        kinds = Counter()
        primary = {}
        for record in records:
            refs = record.get('source_refs') or []
            dataset = refs[0]['dataset'] if refs else ''
            primary[record['record_id']] = dataset
            kinds[_record_kind(record, record_type, dataset, hint)] += 1
        if status == 'supporting_context':
            kind = 'supporting_context'
        elif status == 'summary_only':
            kind = 'aggregate_only'
        elif kinds:
            kind = kinds.most_common(1)[0][0]
        else:
            kind = 'none'
        entity_count, entity_unit, entity_basis = _entity_count(records, record_type) if records else (
            None, None, 'No records were exported for this finding.')
        record_unit = (_RECORD_TYPES[record_type][0] if record_type in _RECORD_TYPES else
                       _GENERIC_UNITS.get(record_type, 'records'))
        worklist_count, worklist_unit = _worklist(row)
        relation = None
        if worklist_count is not None and worklist_count != len(records):
            relation = (f'The workbook worklist lists {worklist_count} {worklist_unit or "items"}; this evidence lists '
                        f'{len(records)} {record_unit}' + (f' for {entity_count} {entity_unit}' if entity_count is not None else '')
                        + '. Worklist items can group several detail records.')
        outcomes = None
        if record_type == 'legacy_signin_event':
            from .signin_evidence import SIGNIN_OUTCOMES
            counter = Counter((record.get('fields') or {}).get('outcome') for record in records)
            outcomes = {name: counter.get(name, 0) for name in SIGNIN_OUTCOMES}
        windows = [item for item in native if item.get('window_start') or item.get('window_end')]
        evidence = {
            'kind': kind, 'record_kinds': dict(kinds), 'availability': availability,
            'record_status': status, 'record_type': record_type, 'record_count': len(records), 'record_unit': record_unit,
            'affected_entity_count': entity_count, 'entity_unit': entity_unit, 'entity_basis': entity_basis,
            'outcome_counts': outcomes, 'worklist_count': worklist_count, 'worklist_unit': worklist_unit,
            'count_relation': relation, 'selection': finding.get('record_selection') or '',
            'limitations': list(finding.get('record_limitations') or []),
            'reconciliation': finding.get('record_reconciliation') or {},
            'missing_evidence_action': _missing_action(finding.get('recommendation_detail') or row.get('recommendation_detail'),
                                                       row, availability, status),
            'collected_at': sorted({item['collected_at'] for item in native if item.get('collected_at')}),
            'observation_window': {'start': min((item['window_start'] for item in windows if item.get('window_start')), default=None),
                                   'end': max((item['window_end'] for item in windows if item.get('window_end')), default=None)},
            'source_ids': [item['dataset_id'] for item in native] + [item for item in record_sources if sources[item]['derived']],
            'datasets': list(dict.fromkeys([item['dataset'] for item in native] + [sources[item]['dataset'] for item in record_sources])),
            'evidence_record_ids': list(finding.get('evidence_record_ids') or []),
            'tables': [], 'context_tables': [], 'context_record_count': 0,
        }
        model = {key: finding.get(key) for key in ('finding_id', 'finding_uid', 'finding_key', 'finding_fingerprint', 'title',
                                                   'priority', 'disposition', 'readiness_effect', 'domain_id', 'control_id',
                                                   'service', 'owner_role', 'observed_at', 'evidence_level',
                                                   'operational_result', 'historical', 'recommendation_detail')}
        number, concern_title, _ = _CONCERN_INFO[concern]
        model.update({'concern_id': concern, 'concern_title': concern_title, 'related_concerns': list(_RELATED.get(concern, [])),
                      'action_type': row.get('ActionType'), 'issue': finding.get('observation') or '',
                      'recommendation': finding.get('recommendation') or '', 'evidence': evidence,
                      'workbook': {'investigation_range': row.get('InvestigationRange') or None,
                                   'investigation_ranges': list(row.get('InvestigationRanges') or []),
                                   'raw_ranges': list(row.get('RawEvidenceRanges') or [])}})
        if model['recommendation_detail'] is None:
            from .technical_guidance import recommendation_detail
            model['recommendation_detail'] = recommendation_detail(row)
        for record in records:
            dataset = primary[record['record_id']]
            dataset_id = evidence_dataset.get(next(iter(record.get('evidence_record_ids') or []), ''), '')
            summary = sources.get(dataset_id)
            base = {'evidence_record_ids': '; '.join(record.get('evidence_record_ids') or []), 'dataset': dataset,
                    'dataset_id': dataset_id, 'source_record_id': _cell(record.get('source_record_id')),
                    'evidence_kind': _record_kind(record, record_type, dataset, hint),
                    'collected_at': (summary or {}).get('collected_at'), 'window_start': (summary or {}).get('window_start'),
                    'window_end': (summary or {}).get('window_end'), 'source_state': _state(summary),
                    'qualification': '; '.join(record.get('qualifications') or [])}
            fields = OrderedDict()
            for name, value in (record.get('fields') or {}).items():
                # A source attribute named like a common column is kept under a distinct name.
                fields[f'{name} (record field)' if name in COMMON_COLUMNS or name in CONTEXT_COLUMNS else name] = _cell(value)
            if status == 'supporting_context':
                table_id = f'{number:02d}-{concern}-context-{slug(dataset)}'
                table = tables.setdefault(table_id, {'table_id': table_id, 'concern_id': concern, 'role': 'context',
                                                     'record_type': record_type, 'dataset': dataset,
                                                     'title': f'{concern_title}: supporting context ({dataset})',
                                                     'short_title': 'Context ' + dataset.replace('_', ' ').title(),
                                                     'row_unit': 'unique retained source records',
                                                     'columns': list(CONTEXT_COLUMNS), 'rows': [], 'finding_ids': []})
                key = next(iter(record.get('evidence_record_ids') or []), record['record_id'])
                member = context_members.setdefault((table_id, key), None)
                if member is None:
                    entry = OrderedDict([('finding_ids', [finding['finding_id']]), ('evidence_record_id', key)])
                    entry.update(base)
                    entry.update((name, value) for name, value in fields.items() if name not in entry)
                    context_members[(table_id, key)] = entry
                    table['rows'].append(entry)
                elif finding['finding_id'] not in member['finding_ids']:
                    member['finding_ids'].append(finding['finding_id'])
                if finding['finding_id'] not in table['finding_ids']:
                    table['finding_ids'].append(finding['finding_id'])
                if table_id not in evidence['context_tables']:
                    evidence['context_tables'].append(table_id)
                continue
            if record_type in _RECORD_TYPES:
                table_slug, short = _RECORD_TYPES[record_type][4], _RECORD_TYPES[record_type][5]
            else:
                table_slug = slug(f'{record_type or "records"}-{dataset}')
                short = (record_type or 'records').replace('_', ' ').title()
            table_id = f'{number:02d}-{concern}-{table_slug}'
            table = tables.setdefault(table_id, {'table_id': table_id, 'concern_id': concern, 'role': 'selected',
                                                 'record_type': record_type, 'dataset': dataset,
                                                 'title': f'{concern_title}: {record_unit}', 'short_title': short,
                                                 'row_unit': record_unit, 'columns': list(COMMON_COLUMNS), 'rows': [],
                                                 'finding_ids': []})
            entry = OrderedDict([('finding_id', finding['finding_id']), ('detail_record_id', record['record_id'])])
            entry.update(base)
            entry.update((name, value) for name, value in fields.items() if name not in entry)
            for name in entry:
                if name not in table['columns']:
                    table['columns'].append(name)
            table['rows'].append(entry)
            if finding['finding_id'] not in table['finding_ids']:
                table['finding_ids'].append(finding['finding_id'])
            if table_id not in evidence['tables']:
                evidence['tables'].append(table_id)
        evidence['context_record_count'] = sum(
            1 for (table_id, _), member in context_members.items()
            if table_id in evidence['context_tables'] and finding['finding_id'] in member['finding_ids'])
        findings.append(model)
        concern_findings.setdefault(concern, []).append(finding['finding_id'])
    for table in tables.values():
        if table['role'] == 'context':
            for entry in table['rows']:
                for name in entry:
                    if name not in table['columns']:
                        table['columns'].append(name)
                entry['finding_ids'] = '; '.join(entry['finding_ids'])
    concerns = []
    for identifier, number, title, short in CONCERNS:
        if identifier not in concern_findings:
            continue
        concerns.append({'concern_id': identifier, 'number': number, 'title': title, 'short_title': short,
                         'finding_ids': concern_findings[identifier],
                         'tables': [table_id for table_id, table in tables.items() if table['concern_id'] == identifier],
                         'related_concerns': [item for item in _RELATED.get(identifier, []) if item in concern_findings]})
    return {'format': 'm365-readiness-finding-evidence', 'schema_version': MODEL_SCHEMA_VERSION,
            'tenant_id': payload.get('tenant_id'), 'tenant_name': payload.get('tenant_name'),
            'evaluation_date': payload.get('evaluation_date'), 'generated_at': payload.get('generated_at'),
            'methodology_version': payload.get('methodology_version'), 'decision': payload.get('decision'),
            'rationale': payload.get('rationale'), 'counts': payload.get('counts') or {},
            'concerns': concerns, 'findings': findings, 'tables': tables, 'sources': sources,
            'portal_report_highlights': payload.get('portal_report_highlights') or []}


def finding_tables(model, finding_id):
    finding = next((item for item in model['findings'] if item['finding_id'] == finding_id), None)
    return [] if finding is None else finding['evidence']['tables'] + finding['evidence']['context_tables']


def rows_for_finding(model, finding_id):
    """Every exported evidence row that belongs to one finding, across selected and context tables."""
    result = []
    for table_id in finding_tables(model, finding_id):
        table = model['tables'][table_id]
        if table['role'] == 'selected':
            result.extend(row for row in table['rows'] if row['finding_id'] == finding_id)
        else:
            result.extend(row for row in table['rows'] if finding_id in str(row['finding_ids']).split('; '))
    return result

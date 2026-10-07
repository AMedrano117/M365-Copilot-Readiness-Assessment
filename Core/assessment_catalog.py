"""Nine-domain coverage catalog, retaining the established readiness control IDs."""

from collections import defaultdict
from copy import deepcopy
import re

from .evidence_contract import evaluation_day, parse_date
from .expanded_collection import plain


# Entries are (check suffix, description, dataset names, evidence level). The
# descriptions intentionally cover every required check, including owner-only
# evidence. Availability never means that the associated safeguard passed.
DOMAIN_SPECS = (
 ('scope', 'Scope, ownership, licensing and application readiness', 'licensing', (
  ('SERVICES','Planned AI services and workloads','context','owner_review'),
  ('PILOT','Pilot users, devices, content locations, applications and connectors','context','owner_review'),
  ('OWNERS','Identity, devices, data protection, security operations and adoption owners','context','owner_review'),
  ('APPLICATIONS','Microsoft 365 collaboration and application readiness','users office_activations copilot_readiness','configuration'),
  ('LICENSES','Copilot licensing path and proposed Entra, Intune, Defender, Cloud Apps and Purview licensing','licenses','configuration'),
  ('REQUIREMENTS','Business, legal, privacy and regulatory requirements','context','owner_review'),
  ('REMEDIATION','Remediation dates and reassessment plans','context','owner_review'),
  ('EXCEPTIONS','Exception approvals and expiry dates','context','owner_review'))),
 ('identity', 'Identity, MFA and Conditional Access', 'identity', (
  ('ACCOUNTS','User account status','users','configuration'),
  ('REGISTRATION','MFA registration and capability','auth_methods','configuration'),
  ('DEFAULTS','Users without a default MFA method','auth_methods','configuration'),
  ('METHODS','Authentication method distribution and SMS/voice dependencies','auth_methods','configuration'),
  ('PASSWORDLESS','Passwordless capability','auth_methods','configuration'),
  ('SSPR','Self-service password reset coverage','auth_methods','configuration'),
  ('POLICIES','Conditional Access policies, assignments, exclusions and operating modes','ca_policies authentication_strengths ca_group_members','policy_enforcement'),
  ('SIGNIN_OPERATION','Sign-ins outside Conditional Access and without an MFA requirement','signin_logs','observed_operation'),
  ('LEGACY','Legacy authentication activity','signin_logs','observed_operation'),
  ('ACCOUNT_GAPS','Service-account, guest and exclusion-related coverage gaps','users ca_policies ca_group_members context','owner_review'),
  ('RECOMMENDATIONS','Available policy recommendations','entra_recommendations entra_impacted_resources','configuration'),
  ('REPORT_ONLY','Report-only results, enforcement readiness, dependencies and user-impact risks','signin_logs context','observed_operation'))),
 ('devices', 'Device estate and compliance', 'endpoints', (
  ('ESTATE','Total, active in-scope, managed and unmanaged devices','directory_devices managed_devices machines','configuration'),
  ('ACTIVITY','Stale records and last check-in dates','directory_devices managed_devices machines','observed_operation'),
  ('RETIREMENT','Devices proposed for retirement','context','owner_review'),
  ('PILOT','Pilot-device membership','context','owner_review'),
  ('POLICIES','Compliance policies and assignments','compliance_policies compliance_assignments','configuration'),
  ('RESULTS','Compliance results and failing settings','managed_devices compliance_setting_states','observed_operation'),
  ('PROTECTION','Real-Time Protection status','windows_protection','observed_operation'),
  ('INTELLIGENCE','Defender security-intelligence currency','antivirus_health windows_protection','observed_operation'),
  ('ERRORS','Synchronization, reporting and configuration issues','managed_devices windows_protection compliance_setting_states','observed_operation'),
  ('UNMANAGED','Enrollment or exclusion decisions for unmanaged devices','context','owner_review'))),
 ('defender', 'Defender readiness and security operations', 'endpoints', (
  ('SETUP','Defender for Business setup state','context','owner_review'),
  ('ACCESS','Security roles and portal access','role_assignments context','owner_review'),
  ('NOTIFICATIONS','Alert and vulnerability notifications','context','owner_review'),
  ('POLICIES','Default security policies','context','owner_review'),
  ('ONBOARDING','Onboarding of active in-scope devices','machines directory_devices managed_devices','configuration'),
  ('REPORTING','Last-seen status and active endpoint telemetry','machines','observed_operation'),
  ('OWNERS','Monitoring ownership','context','owner_review'),
  ('RESPONSE','Alert triage and incident-response procedures','incidents alerts context','owner_review'),
  ('PILOT','Pilot-device reporting coverage','context machines','observed_operation'))),
 ('content', 'Content access and oversharing', 'content', (
  ('LOCATIONS','SharePoint, OneDrive and Teams locations used by pilot participants','context','owner_review'),
  ('REFERENCED','Sites and files referenced by Copilot','copilot_audit context','observed_operation'),
  ('OWNERS','Site and business ownership','sites content_exposure context','owner_review'),
  ('ANYONE','Anyone links','content_exposure','observed_operation'),
  ('ORGANIZATION','Organization-wide links','content_exposure','observed_operation'),
  ('EVERYONE','Everyone permissions','content_exposure','observed_operation'),
  ('EEEU','Everyone except external users permissions','content_exposure','observed_operation'),
  ('JUSTIFICATION','Business justification for broad access','context','owner_review'),
  ('REMOVAL','Unnecessary access requiring removal','context','owner_review'),
  ('EXCEPTIONS','Exceptions, owners and review dates','context','owner_review'))),
 ('classification', 'Classification, sensitivity labels and Copilot DLP', 'data_protection', (
  ('SENSITIVE','Detected sensitive information types','sensitive_exposure','observed_operation'),
  ('MODEL','Classification model','context','owner_review'),
  ('LABELS','Label definitions, publication and scope','sensitivity_labels label_policies','configuration'),
  ('COVERAGE','Label coverage on pilot and Copilot-referenced content','label_coverage context','observed_operation'),
  ('REQUIREMENTS','Protection requirements for files and sites','context','owner_review'),
  ('PROTECTION','Observed protection behavior in planned AI scenarios','context','observed_operation'),
  ('DLP','Copilot interaction DLP policies, scope, sensitive-information conditions and operating mode','dlp_policies dlp_rules','policy_enforcement'),
  ('SIMULATION','Simulation results and unresolved impact','context','observed_operation'),
  ('ENFORCEMENT','DLP enforcement readiness','context','observed_operation'),
  ('MONITORING','Sensitive-data monitoring signals','sensitive_exposure context','observed_operation'))),
 ('governance', 'Audit, retention, compliance and investigation', 'data_protection', (
  ('AUDIT','Audit configuration and usable records','audit_config copilot_audit','observed_operation'),
  ('SEARCH','Ability to search AI interaction logs','copilot_audit context','observed_operation'),
  ('REVIEW','Log-review ownership and frequency','context','owner_review'),
  ('RESPONSE','User reporting and response procedures','context','owner_review'),
  ('ACTIONS','Open, failed and high-risk compliance actions, ownership and prioritization','context','owner_review'),
  ('RETENTION','Retention treatment for AI interactions, generated content and meeting content','retention_policies context','configuration'),
  ('POLICIES','Relevant retention policies and scope','retention_policies','configuration'),
  ('LEGAL','Legal and privacy decisions','context','owner_review'),
  ('ENTITLEMENTS','Role and entitlement governance','role_assignments access_reviews context','owner_review'),
  ('INSIDER_RISK','Insider Risk Management requirements','context','owner_review'),
  ('COMMUNICATION','Communication Compliance requirements','context','owner_review'),
  ('EDISCOVERY','eDiscovery requirements for AI interactions','context','owner_review'),
  ('INVESTIGATION','Investigation capacity and operating procedures','context','owner_review'),
  ('ENTERPRISE','Broader privacy and enterprise retention needs','context','owner_review'))),
 ('applications', 'Applications, connectors and consent', 'applications', (
  ('INVENTORY','Connected applications and connectors relevant to AI','service_principals external_connections','configuration'),
  ('PERMISSIONS','Granted permissions and consent','oauth_grants application_permissions','configuration'),
  ('CONSENT','User consent settings','authorization_policy consent_policies','configuration'),
  ('OWNERS','Business owners','application_owners context','owner_review'),
  ('JUSTIFICATION','Business justification for access','context','owner_review'),
  ('REVIEW','Unreviewed or excessive permissions','application_permissions oauth_grants context','owner_review'),
  ('EXCEPTIONS','Approval and exception processes','context','owner_review'))),
 ('external_ai', 'External AI and shadow AI visibility', 'external_ai', (
  ('SERVICES','Public AI chat, writing assistants, meeting, file-analysis and automation services','shadow_ai','observed_operation'),
  ('COLLECTORS','Collector availability and configuration','shadow_ai context','configuration'),
  ('ENDPOINTS','Endpoint management and security telemetry','managed_devices machines','observed_operation'),
  ('DISCOVERY','Defender for Cloud Apps discovery options','shadow_ai context','configuration'),
  ('NETWORK','Supported firewall and proxy log coverage','context','owner_review'),
  ('UNMANAGED','Visibility into unmanaged devices','context','owner_review'),
  ('DEPENDENCIES','Licensing and architecture dependencies','context','owner_review'),
  ('USAGE','Observed services and usage','shadow_ai','observed_operation'),
  ('PROVIDERS','Provider and data-handling considerations','context','owner_review'),
  ('OWNERS','Business need, risk and ownership','context','owner_review'),
  ('DECISIONS','Approval, restriction and investigation decisions','context','owner_review'),
  ('OUTCOMES','Business outcomes, support demand and risk','context','owner_review'))),
)
CHECKS = tuple({'id': domain.upper() + '.' + suffix, 'domain_id': domain, 'title': title,
               'legacy_domain': legacy, 'description': description, 'sources': sources.split(),
               'evidence_level': level, 'applicability': 'requirements-dependent' if suffix in {
                   'INSIDER_RISK','COMMUNICATION','EDISCOVERY','ENTERPRISE'} else 'assess',
               'readiness_effect': 'Context; existing required controls determine readiness'}
              for domain, title, legacy, entries in DOMAIN_SPECS for suffix, description, sources, level in entries)
CHECK_BY_ID = {row['id']: row for row in CHECKS}
CHECK_CONTROLS = {'IDENTITY.POLICIES':'IDENTITY.AUTH','IDENTITY.SIGNIN_OPERATION':'IDENTITY.AUTH',
                  'DEVICES.RESULTS':'ENDPOINT.POSTURE','DEVICES.PROTECTION':'ENDPOINT.POSTURE',
                  'DEFENDER.REPORTING':'ENDPOINT.POSTURE','CLASSIFICATION.LABELS':'DATA.PUBLISHING',
                  'CLASSIFICATION.DLP':'DATA.DLP','CLASSIFICATION.ENFORCEMENT':'DATA.DLP',
                  'GOVERNANCE.AUDIT':'DATA.AUDIT','GOVERNANCE.RETENTION':'DATA.RETENTION',
                  'APPLICATIONS.PERMISSIONS':'APPS.CONSENT','SCOPE.LICENSES':'LICENSE.ASSIGNMENT'}
for _check in CHECKS:
    if _check['id'] in CHECK_CONTROLS:
        _check['control_id'] = CHECK_CONTROLS[_check['id']]
        _check['readiness_effect'] = 'Contributes to required control ' + _check['control_id'] + '; configuration alone cannot establish operation'


def context_validation(profile):
    from .control_reviews import _profile_raw
    raw = _profile_raw(profile)
    context = raw.get('assessment_context', {})
    errors = []
    if not isinstance(context, dict):
        return ['assessment_context must be an object']
    for field in ('device_activity_days', 'endpoint_reporting_days'):
        if field in context and (type(context[field]) is not int or not 1 <= context[field] <= 365):
            errors.append(f'assessment_context.{field} must be an integer from 1 to 365')
    for index, exception in enumerate(context.get('exceptions', []) if isinstance(context.get('exceptions', []), list) else [None]):
        if not isinstance(exception, dict) or not all(exception.get(key) for key in ('id','owner','reason','approved_by')) or not parse_date(exception.get('approved_at')) or not parse_date(exception.get('expires_at')):
            errors.append(f'assessment_context.exceptions[{index}] requires id, owner, reason, approved_by, approved_at and expires_at')
        elif parse_date(exception['expires_at']) < parse_date(exception['approved_at']):
            errors.append(f'assessment_context.exceptions[{index}] expiry precedes approval')
    return errors


def collect_assessment_sources(clients, *, collected_at='', data_exposure=None, profile=None):
    """Copy source rows, never transport objects, into the shared evidence bundle."""
    sources = defaultdict(list)
    configuration = {'users','sites','external_connections','auth_methods','managed_devices','compliance_policies',
                     'role_assignments','role_definitions','role_assignment_schedules','role_eligibility_schedules',
                     'guest_users','cross_tenant_access_policy','security_defaults',
                     'access_reviews','service_principals','oauth_grants','consent_policies',
                     'authorization_policy','sensitivity_labels','label_policies','retention_policies','licenses','office_activations',
                     'audit_config','irm_config'}
    attributes = {
        'users':'users', 'sites':'sites', 'external_connections':'external_connections',
        'auth_methods':'auth_methods_registration', 'ca_policies':'ca_policies',
        'managed_devices':'managed_devices', 'compliance_policies':'compliance_policies',
        'role_assignments':'role_assignments', 'access_reviews':'access_reviews',
        'role_definitions':'role_definitions', 'role_assignment_schedules':'role_assignment_schedules',
        'role_eligibility_schedules':'role_eligibility_schedules', 'risky_users':'risky_users',
        'risk_detections':'risk_detections', 'guest_users':'guest_users',
        'cross_tenant_access_policy':'cross_tenant_access_policy', 'security_defaults':'security_defaults',
        'service_principal_signin_activities':'service_principal_signin_activities',
        'application_signin_summary':'application_signin_summary',
        'service_principals':'service_principals', 'oauth_grants':'oauth_permission_grants',
        'consent_policies':'permission_grant_policies', 'authorization_policy':'authorization_policy',
        'signin_logs':'signin_logs', 'machines':'defender_devices', 'incidents':'security_incidents',
        'alerts':'security_alerts', 'sensitivity_labels':'sensitivity_labels', 'label_policies':'label_policies',
        'dlp_policies':'dlp_policies', 'dlp_rules':'dlp_rules', 'retention_policies':'retention_policies',
        'audit_config':'audit_config', 'irm_config':'irm_config', 'licenses':'subscribed_skus', 'office_activations':'office_activations',
    }
    # The retained attribute and request task do not always share a name. Keep
    # the original outcome, dates and pagination beside those rows rather than
    # accidentally treating a completed collection as unknown.
    status_names = {'application_signin_summary':'app_signin_summary',
                    'cross_tenant_access_policy':'cross_tenant_policy',
                    'guest_users':'guests'}
    for client in clients:
        if client is None:
            continue
        statuses = getattr(client, 'collection_status', {}) or {}
        for name, datasets in (getattr(client, 'assessment_datasets', {}) or {}).items():
            sources[name].extend(deepcopy(datasets))
        for name, attr in attributes.items():
            value = getattr(client, attr, None)
            if value is None or name in (getattr(client, 'assessment_datasets', {}) or {}):
                continue
            value = plain(value)
            rows = value if isinstance(value, list) else value.get('policies', value.get('labels', [value])) if isinstance(value, dict) else []
            state = deepcopy(statuses.get(status_names.get(name, name), {}))
            if isinstance(value, dict):
                state = {**{key:item for key,item in value.items() if key not in {'policies','labels','rules','records'}}, **state}
                if name == 'dlp_rules': rows=value.get('rules',rows)
            # For older saves without request metadata, do not invent coverage.
            state.setdefault('collected_at', getattr(client,'collected_at','') or collected_at)
            state.setdefault('evidence_level', 'configuration' if name in configuration else 'policy_enforcement' if name in {'ca_policies','dlp_policies','dlp_rules'} else 'observed_operation')
            state.setdefault('availability_status', 'unknown')
            state.setdefault('scope', 'Returned tenant records')
            if name in {'service_principal_signin_activities','application_signin_summary'}:
                state.setdefault('evidence_quality', 'preview')
                state.setdefault('limitations', 'Microsoft Graph beta report; returned activity cannot establish complete application usage or a foundation-control pass.')
            state.setdefault('complete', state.get('available') is True and not state.get('truncated') and state['availability_status'] == 'available')
            sources[name].append({'records': rows, 'source': state})
        governance = plain(getattr(client, 'sharepoint_governance', {}) or {})
        if governance:
            governance_statuses = governance.get('collection_status') or {}
            # These are the original SPO/Graph settings, not an expansion of
            # the counts in sites_summary. Keep inheritance/defaults, conflicts
            # and the authentication limitations available for drill-down.
            for name, container, record_field, status_name in (
                ('sharepoint_tenant_settings', 'tenant', 'settings', 'sharepoint_tenant_settings'),
                ('sharepoint_site_settings', 'sites', 'items', 'sharepoint_site_settings'),
                ('sharepoint_graph_settings', 'graph_settings', None, 'sharepoint_tenant_settings_graph'),
            ):
                value = governance.get(container)
                if value is None:
                    continue
                value = value if isinstance(value, dict) else {}
                records = value.get(record_field, []) if record_field else value
                rows = records if isinstance(records, list) else [records] if records else []
                state = {key: deepcopy(item) for key, item in value.items()
                         if record_field and key != record_field}
                state.update(deepcopy(governance_statuses.get(status_name) or {}))
                state.setdefault('source', governance.get('source', ''))
                state.setdefault('collected_at', getattr(client, 'collected_at', '') or collected_at)
                state.setdefault('availability_status', 'unknown')
                state.setdefault('evidence_level', 'configuration')
                state.setdefault('scope', 'Returned tenant settings' if container != 'sites' else 'Returned SharePoint and OneDrive site settings')
                state.setdefault('complete', state.get('available') is True and not state.get('truncated') and state['availability_status'] == 'available')
                if name == 'sharepoint_site_settings' and state.get('settings_read_mode') != 'per_site_identity':
                    state.setdefault('settings_read_mode', 'unrecorded')
                    limitation = ('Microsoft documents that Get-SPOSite with Limit or Filter can return default values instead of populated sharing, link-expiration and sensitivity-label settings. '
                                  'Older collections did not retain independent per-site Identity reads or override flags; these returned settings are unverified, and zero expiry does not prove an effective expiry policy.')
                    state['limitations'] = ' '.join(filter(None, [state.get('limitations', ''), limitation]))
                    state['documentation_verified_at'] = '2026-10-02'
                    state['documentation_urls'] = [
                        'https://learn.microsoft.com/en-us/powershell/module/microsoft.online.sharepoint.powershell/get-sposite?view=sharepoint-ps',
                        'https://learn.microsoft.com/powershell/module/sharepoint-online/set-sposite?view=sharepoint-ps',
                    ]
                if governance.get('conflicts'):
                    state.setdefault('conflicts', deepcopy(governance['conflicts']))
                sources[name].append({'records': deepcopy(rows), 'source': state})
        for name, attr, field in (('copilot_audit','copilot_interaction_audit','records'),
                                 ('shadow_ai','shadow_ai_usage','applications'),
                                 ('shadow_streams','shadow_ai_usage','streams'),
                                 ('shadow_app_catalog','shadow_ai_usage','discovered_app_records'),
                                 ('shadow_entities','shadow_ai_usage','entity_records'),
                                 ('copilot_usage','copilot_usage','user_detail'),
                                 ('licenses','license_coverage','subscription_records'),
                                 ('license_users','license_coverage','user_records'),
                                 ('copilot_readiness','copilot_readiness_export','user_details')):
            value = plain(getattr(client, attr, {}) or {})
            if value:
                state = {key:item for key,item in value.items() if key not in {field,'user_details','user_detail','summary','applications','streams','discovered_app_records','entity_records','subscription_records','user_records'}}
                state.setdefault('collected_at', collected_at)
                state.setdefault('evidence_level','configuration' if name in {'licenses','license_users','copilot_readiness'} else 'observed_operation')
                state.setdefault('complete', state.get('availability_status') == 'available' and not state.get('truncated') and not state.get('user_detail_reason'))
                sources[name].append({'records':value.get(field, []), 'source':state})
    exposure = data_exposure or {}
    for kind, scan in exposure.get('sources', {}).items():
        name = 'content_exposure' if kind == 'sam' else 'sensitive_exposure'
        retained = scan.get('retained_records') or scan.get('risk_rows') or []
        state = {'availability_status':'partial' if scan.get('errors') else 'available' if scan.get('files_loaded') else 'unavailable',
                 'complete':False, 'evidence_level':'observed_operation', 'source_reports':scan.get('reports', []),
                 'scope':'Supplied exports; tenant-wide completeness is not independently established',
                 'reason':'Historical and conflicting rows remain dated source evidence.'}
        sources[name].append({'records':plain(retained),'source':state})
        if kind == 'sam':
            sources['label_coverage'].append({'records':plain([row for row in retained if row.get('_report_type')=='label_inventory']), 'source':deepcopy(state)})
    from .control_reviews import _profile_raw
    raw = _profile_raw(profile or {})
    if raw.get('assessment_context'):
        sources['context'].append({'records':[deepcopy(raw['assessment_context'])],
            'source':{'availability_status':'available','complete':True,'evidence_level':'owner_review',
                      'scope':'Structured assessment context; planning inputs do not establish tested operational behavior',
                      'source_file':(profile or {}).get('filename',''),'refresh_date':raw['assessment_context'].get('reviewed_at','')}})
    if raw.get('readiness_review'):
        sources['check_reviews'].append({'records':deepcopy(raw['readiness_review'].get('check_reviews',[])),
            'source':{'availability_status':'available','complete':True,'scope':'Supplied dated tenant check reviews','evidence_level':'owner_review'}})
    return dict(sources)


def current_check_reviews(profile, day, tenant_id):
    from .control_reviews import _profile_raw, validate_readiness_review
    if validate_readiness_review(profile or {}):
        return {}
    review = _profile_raw(profile).get('readiness_review') or {}
    if not isinstance(review, dict) or str(review.get('tenant_id','')).lower() != str(tenant_id or '').lower():
        return {}
    scope = review.get('tenant_scope') or {}
    scope_date = parse_date(scope.get('reviewed_at'))
    if not scope_date or not 0 <= (day - scope_date).days <= 35:
        return {}
    groups = defaultdict(list)
    operational_controls = {'IDENTITY.AUTH':'IDENTITY.SIGNIN_OPERATION','ENDPOINT.POSTURE':'DEFENDER.REPORTING',
                            'DATA.DLP':'CLASSIFICATION.ENFORCEMENT','DATA.AUDIT':'GOVERNANCE.AUDIT'}
    candidates = list(review.get('check_reviews', [])) + [dict(row,check_id=operational_controls[row['control_id']])
        for row in review.get('control_reviews', []) if row.get('control_id') in operational_controls]
    for row in candidates:
        observed = parse_date(row.get('reviewed_at'))
        if row.get('check_id') in CHECK_BY_ID and row.get('scope_id') == scope.get('id') and observed and scope_date <= observed <= day and (day-observed).days <= 35 and all(row.get(key) for key in ('reviewer_role','rationale','evidence_reference')):
            groups[row['check_id']].append(row)
    output = {}
    for key, rows in groups.items():
        latest = max(parse_date(row['reviewed_at']) for row in rows)
        selected = [row for row in rows if parse_date(row['reviewed_at']) == latest]
        output[key] = {'result':'conflict' if len({row.get('result') for row in selected}) > 1 else selected[0].get('result'),
                       'records':selected, 'scope':scope}
    return output


def attach_catalog(result, bundle):
    day = evaluation_day(result['evaluation_date'])
    reviews = current_check_reviews(bundle.get('assessment_profile'), day, result.get('tenant_id'))
    sources = bundle.get('assessment_sources', {})
    from .control_reviews import _profile_raw
    context = _profile_raw(bundle.get('assessment_profile') or {}).get('assessment_context') or {}
    if context_validation(bundle.get('assessment_profile') or {}):
        context = {}
    coverage = []
    for check in CHECKS:
        datasets = [(name, dataset) for name in check['sources'] for dataset in sources.get(name, [])]
        collected, limitations, dates = [], [], set()
        for name, dataset in datasets:
            state = dataset.get('source', {})
            available = state.get('availability_status', 'unknown')
            observed = state.get('refresh_date') or state.get('collected_at')
            if observed:
                dates.add(str(observed))
            if available in {'available','partial','empty'}:
                collected.append(f"{name}: {len(dataset.get('records', []))} retained record(s), {available}")
            if state.get('reason'):
                limitations.append(f"{name}: {state['reason']}")
            if state.get('partial_errors'):
                limitations.append(f"{name}: {state['partial_errors']}")
            if available not in {'available','empty'} or state.get('complete') is not True:
                limitations.append(f'{name}: {available}; full coverage not established')
        owner = reviews.get(check['id'])
        if owner:
            dates.update(str(row['reviewed_at']) for row in owner['records'])
        state = 'reviewed' if owner and owner['result'] in {'pass','fail'} else 'conflict' if owner and owner['result']=='conflict' else 'collected' if collected and not limitations else 'partial' if collected else 'missing'
        missing = [name for name in check['sources'] if not sources.get(name) or all(dataset.get('source',{}).get('availability_status') not in {'available','partial','empty'} for dataset in sources[name])]
        if check['evidence_level'] in {'owner_review','observed_operation'} and 'context' in check['sources'] and not owner:
            missing.append('dated check review documenting scope and behavior or decision')
        if owner and owner['result'] == 'not_applicable' and check['applicability'] == 'requirements-dependent':
            state = 'not_applicable'
        tested = owner and all(row.get('evidence_level') == 'observed_operation' and
            str(row.get('tested_behavior','')).strip() and str(row.get('tested_scope','')).strip() for row in owner['records'])
        if owner and check['evidence_level']=='observed_operation' and not tested:
            state='partial'
            missing.append('tested behavior and scope in an observed-operation review, or suitable retained operational records')
        # Source retrieval establishes collection, not semantic applicability.
        coverage.append({'Check ID':check['id'], 'Domain':check['title'], 'Check':check['description'],
            'State':state, 'Evidence level':check['evidence_level'], 'Evidence collected':'; '.join(collected),
            'Missing evidence':'; '.join(missing) if not owner or state=='partial' else '', 'Failures and limitations':'; '.join(dict.fromkeys(limitations)),
            'Evidence dates':'; '.join(sorted(dates)), 'Conclusion effect': 'Reviewed result: ' + str(owner['result']) if owner else 'Coverage is limited to returned records; the full check still requires confirmation.',
            'Readiness effect':check['readiness_effect'], 'Owner review':deepcopy(owner['records']) if owner else [],
            'Applicability':check['applicability'], 'Required sources':check['sources'],
            'Source metadata':[{'dataset':name,**deepcopy(dataset.get('source',{}))} for name,dataset in datasets]})
    domains = []
    def domain_id(row):
        legacy = row.get('DomainId')
        control = row.get('ControlId', '')
        text = str(row.get('Feature', '')).lower()
        if legacy == 'licensing' or legacy == 'adoption': return 'scope'
        if legacy == 'endpoints': return 'defender' if control.startswith('THREAT') or any(word in text for word in ('incident','alert','onboarding','monitoring')) else 'devices'
        if legacy == 'data_protection': return 'governance' if control in {'DATA.AUDIT','DATA.RETENTION'} or any(word in text for word in ('audit','retention','ediscovery','insider','compliance')) else 'classification'
        return legacy if legacy in {item[0] for item in DOMAIN_SPECS} else 'applications'
    for row in result['recommendations']:
        row['AssessmentDomainId'] = domain_id(row)
    for identifier, title, legacy, _ in DOMAIN_SPECS:
        findings = [row for row in result['recommendations'] if row['AssessmentDomainId']==identifier and row.get('Disposition')!='Reference' and row.get('EvidenceBasis')!='License signal']
        actions = [row for row in result['actions'] if row.get('AssessmentDomainId', domain_id(row))==identifier]
        checks = [row for row in coverage if row['Domain']==title]
        domains.append({'id':identifier,'title':title,'findings':findings,'actions':actions,'action_count':len(actions),
            'status':'Action required' if any(row.get('ActionType')=='Remediation' for row in actions) else 'Evidence required' if any(row['State'] in {'missing','partial','conflict'} for row in checks) else 'Reviewed' if all(row['State'] in {'reviewed','not_applicable'} for row in checks) else 'Evidence collected',
            'summary':f"{len(checks)} checks; {sum(row['State']=='reviewed' for row in checks)} have current tenant-wide owner reviews.",
            'owner_role':next((row.get('OwnerRole') for row in findings if row.get('OwnerRole')), 'Accountable assessment owner'),
            'why_it_matters':'Configuration, observed operation and owner decisions establish different aspects of this assessment.'})
    result['assessment_domains'] = domains + [row for row in result.get('domains',[]) if row['id']=='agents']
    result['domain_coverage'] = coverage
    from .guidance_verification import verification_rows
    from .technical_guidance import verification_rows as technical_guidance_rows
    result['documentation_verification'] = verification_rows() + technical_guidance_rows()
    result['source_windows'] = [{'Source':name,'Scope':dataset.get('source',{}).get('scope',''),
        'Collected at':dataset.get('source',{}).get('collected_at',''),
        'Window start':dataset.get('source',{}).get('window_start',''),
        'Window end':dataset.get('source',{}).get('window_end',''),
        'Period':dataset.get('source',{}).get('period',dataset.get('source',{}).get('selected_period','')),
        'Limitations':dataset.get('source',{}).get('reason','')}
        for name,datasets in sources.items() for dataset in datasets]
    result['executive_summary'] = {
        'Overall recommendation':result['decision'], 'Scope':'Tenant-wide Microsoft 365 foundation; pilot and external services as explicitly recorded',
        'Evidence dates':result['evidence_period'], 'Established strengths':[row.get('Feature') for row in result['strengths']],
        'Confirmed gaps':[row.get('Feature') for row in result['actions'] if row['ActionType']=='Remediation'],
        'Critical unknowns':[row['Feature'] for row in result.get('decision_coverage', [])],
        'Pilot blockers':[row['Feature'] for row in result['blockers']],
        'Decisions required':[row['Check ID'] + ': ' + row['Check'] for row in coverage if row['Missing evidence'] and ('context' in row['Missing evidence'] or 'review' in row['Missing evidence'])],
    }
    result['assessment_context'] = deepcopy(context)
    result['collection_thresholds'] = {'signin_days':7,'audit_days':7,
        'device_activity_days':context.get('device_activity_days',30),
        'endpoint_reporting_days':context.get('endpoint_reporting_days',7),'review_freshness_days':35}
    return result

"""Qualified component observations; presence never establishes a control pass."""
from copy import deepcopy
import re


CUSTOMER_REQUESTS = {
    'IDENTITY.AUTH': 'Provide dated Conditional Access and security-default settings, including policy modes, target populations and exclusions. Confirm MFA requirements and legacy sign-in blocking separately.',
    'IDENTITY.MFA': 'Provide a current authentication registration report with its population, report date and explicit registration flags. Confirm enforcement and observed authentication separately.',
    'IDENTITY.ADMIN': 'Provide dated role assignments, eligibility and assignment schedules, including principal types, scope and expiry. Record accountable owners and business purpose separately.',
    'CONTENT.SHARING': 'Provide dated SharePoint and OneDrive tenant sharing defaults and applicable site overrides, including the assessed scope.',
    'CONTENT.PERMISSIONS': 'Provide a current completed SharePoint Data Access Governance export that includes the report or scan date and the assessed locations.',
    'CONTENT.OWNERSHIP': 'Provide a dated site ownership and activity inventory. Confirm accountable business owners and decisions for inactive or ownerless locations.',
    'DATA.LABELS': 'Provide dated sensitivity-label definitions and identify their intended protection requirements. Confirm publication scope, content coverage and operational use separately.',
    'DATA.PUBLISHING': 'Provide dated label publication policies, target users and groups, exclusions and evidence of effective publication for the intended population.',
    'DATA.DLP': 'Provide dated DLP policies, rules, scope, exclusions, operating modes and recent operational test results for the intended locations and interactions.',
    'DATA.AUDIT': 'Provide dated audit configuration, workload and retention coverage, the audit query period, and documented review and response ownership.',
    'DATA.RETENTION': 'Provide dated retention policies and their applicable workloads, content locations, exclusions and retention periods.',
    'DATA.EXPOSURE': 'Provide a recent completed Microsoft Purview DSPM data-risk assessment export covering the intended SharePoint and OneDrive population.',
    'APPS.CONSENT': 'Provide dated application consent settings and granted permissions. Record application ownership, business justification and review decisions separately.',
    'APPS.CONNECTIONS': 'Provide a dated connector inventory with its source, indexed scope, access boundaries and accountable owners.',
    'THREAT.INCIDENTS': 'Provide a current incident register with its assessed scope, observation period, investigation status and response ownership.',
    'LICENSE.ASSIGNMENT': 'Provide a dated Microsoft 365 license inventory showing available, assigned and unassigned Copilot licenses for the intended pilot population.',
    'LICENSE.APPS': 'Provide a current Microsoft 365 Copilot readiness export showing Microsoft 365 Apps versions and update channels for the intended pilot population.',
}


def customer_text(value, control=None):
    """One customer projection rule; operator originals remain in technical output."""
    text = '' if value is None else str(value)
    def internal_instruction(fragment):
        command = re.search(r'(?<![\w-])--[a-z][a-z-]+|\b(?:SAM_DAG_REPORT_PATHS|DSPM_REPORT_PATHS|COPILOT_AUDIT_[A-Z_]+)\b|setup-service-principal\.ps1', fragment, re.I)
        collection = re.search(r'\b(?:collect|collector|collection|collected|rerun|permission required)\b', fragment, re.I)
        permission_or_environment = re.search(r'\b[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z0-9]+)+\.(?:All|Selected)\b|\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b', fragment)
        return bool(command or collection and permission_or_environment)
    internal = internal_instruction(text)
    if not internal:
        return text
    if control not in CUSTOMER_REQUESTS:
        lower = text.lower()
        control = ('DATA.EXPOSURE' if 'dspm' in lower else 'CONTENT.PERMISSIONS' if 'sam' in lower or 'data access governance' in lower
                   else 'LICENSE.APPS' if 'readiness' in lower or 'update channel' in lower else control)
    request = CUSTOMER_REQUESTS.get(control, 'Provide current, dated evidence covering the assessed population, with its source, scope and limitations. Review detailed collection instructions in the technical evidence package.')
    retained = [sentence.strip() for sentence in re.split(r'(?<=[.!?])\s+', text)
                if sentence.strip() and not internal_instruction(sentence)]
    return ' '.join([*retained, request])


def component_observations(bundle, control):
    """Retain observed component counts and source qualification without inference."""
    names = {'DATA.LABELS':('sensitivity_labels','label_policies'),
             'DATA.PUBLISHING':('sensitivity_labels','label_policies'),
             'DATA.AUDIT':('audit_config','copilot_audit'),
             'LICENSE.ASSIGNMENT':('licenses','license_users','copilot_usage')} .get(control, ())
    components = []
    for name in names:
        for dataset in (bundle.get('assessment_sources') or {}).get(name, []):
            source = dataset.get('source') or {}
            records = dataset.get('records') or []
            if not records and source.get('availability_status') not in {'available','empty','partial'}:
                continue
            components.append({'dataset':name, 'record_count':len(records), 'source':deepcopy(source)})
    return components


def qualify_gap(row, bundle):
    control = row['ControlId']
    row.setdefault('OriginalRecommendation',row.get('Recommendation'))
    original_observation = row.get('Observation')
    row['Recommendation'] = CUSTOMER_REQUESTS.get(control, row['Recommendation'])
    components = component_observations(bundle, control)
    usage = (bundle.get('ai_usage') or {}).get('copilot_usage') or {}
    capacity = (bundle.get('ai_usage') or {}).get('license_coverage') or (bundle.get('ai_usage') or {}).get('license_summary') or {}
    observed_usage = bool(usage.get('user_detail') or usage.get('available') is True or
                          usage.get('available') is not False and (usage.get('periods') or type(usage.get('enabled_users')) in (int,float)))
    observed_capacity = bool(capacity.get('subscription_records') or capacity.get('user_records') or
        capacity.get('available') is True or capacity.get('available') is not False and any(type(capacity.get(k)) in (int,float)
        for k in ('enabled_users','copilot_license_coverage','copilot_licensed_users')))
    if control == 'LICENSE.ASSIGNMENT' and (components or observed_usage or observed_capacity):
        observed = ('Copilot usage and limited license-capacity signals' if observed_usage and observed_capacity else
                    'Copilot usage signals' if observed_usage else 'limited licensing inventory or capacity signals')
        row['Observation'] = ('The assessment retained ' + observed + ', '
            'but authoritative license assignment and entitlement for the intended pilot population were not established. '
            'Observed usage is not proof of assigned entitlement. Microsoft 365 Apps prerequisites require separate evidence.')
        row['ObservedEvidence'] = {'components':components, 'usage_signal':deepcopy(usage), 'license_signal':deepcopy(capacity),
            'authoritative_inventory':'not_established', 'pilot_entitlement':'not_established', 'apps_prerequisites':'not_established'}
    elif control in {'DATA.LABELS','DATA.PUBLISHING'} and components:
        definitions = sum(item['record_count'] for item in components if item['dataset']=='sensitivity_labels')
        policies = sum(item['record_count'] for item in components if item['dataset']=='label_policies')
        row['Observation'] = (f'{definitions} sensitivity-label definition record(s) and {policies} publication-policy record(s) '
            'were retained. Publication target scope, content labeling coverage, completeness and operational label use '
            'were not established by those returned records.')
        row['ObservedEvidence'] = {'components':components, 'publication_scope':'not_established',
                                  'content_coverage':'not_established', 'operational_use':'not_established'}
    elif control == 'DATA.AUDIT' and components:
        configs = sum(item['record_count'] for item in components if item['dataset']=='audit_config')
        events = sum(item['record_count'] for item in components if item['dataset']=='copilot_audit')
        periods = [item['source'].get('collection_window') or item['source'].get('observation_period') or
                   {'start':item['source'].get('window_start'), 'end':item['source'].get('window_end')}
                   for item in components if item['dataset']=='copilot_audit']
        settings = []
        for dataset in (bundle.get('assessment_sources') or {}).get('audit_config', []):
            for record in dataset.get('records') or []:
                # Only explicit configuration fields establish a reported setting;
                # response availability is not an audit-enabled flag.
                settings.append({key:deepcopy(record[key]) for key in
                    ('unified_audit_enabled','admin_audit_enabled','UnifiedAuditLogIngestionEnabled',
                     'AdminAuditLogEnabled','retention_days') if key in record})
        reported = '; '.join(f'{key}={value}' for setting in settings for key,value in setting.items())
        setting_text = (' Reported configuration: ' + reported + '.' if reported else
                        ' The retained configuration record does not establish an explicit audit-enabled setting.') if configs else ''
        known_periods = [str(p.get('start') or p.get('window_start') or 'unknown') + ' through ' +
                        str(p.get('end') or p.get('window_end') or 'unknown') for p in periods
                        if isinstance(p,dict) and (p.get('start') or p.get('end') or p.get('window_start') or p.get('window_end'))]
        row['Observation'] = (f'{configs} audit-configuration record(s) and {events} Copilot audit-event record(s) were retained. '
            + setting_text + (' Query window: ' + '; '.join(sorted(set(known_periods))) + '.' if known_periods else ' Query window is unresolved.') +
            ' Configuration records describe only their reported settings; events establish observed activity only within the retained query window. '
            + ('A bounded operational audit observation was retained, but ' if events else 'Event-based operation was not established; ') +
            'complete tenant-wide configuration, retention coverage '
            'and review and response procedures were not established.')
        row['ObservedEvidence'] = {'components':components, 'configuration_settings':settings, 'query_windows':periods,
            'tenant_configuration':'not_established', 'retention_coverage':'not_established', 'review_ownership':'not_established'}
    if row.get('ObservedEvidence'):
        row.setdefault('OriginalObservation',original_observation)
        row['EvidenceAvailable'] = 'Yes'
        row['EvidenceBasis'] = 'Limited component evidence; required control remains unresolved'
        qualification = 'Returned component records do not establish effective scope, coverage, entitlement or operation.'
        if qualification not in str(row.get('Qualification') or ''):
            row['Qualification'] = (str(row.get('Qualification') or '') + ' ' + qualification).strip()
    return row


def declared_support(row):
    declaration = row.get('InvestigationEvidence') or {}
    return (isinstance(declaration, dict) and declaration.get('kind') in {'records','configuration'}
            and bool(declaration.get('records')) and isinstance(declaration.get('source'), dict)
            and bool(declaration['source']))


def require_supported_confidence(result):
    """Reject unsupported conclusions; rendering never changes confidence or decisions."""
    facts = {r.get('evidence_id'):r for r in result.get('evidence') or [] if isinstance(r,dict) and r.get('evidence_id')}
    for row in result.get('recommendations') or []:
        if not isinstance(row,dict):
            raise ValueError('Assessment recommendation must be an object with evidence references.')
        fact = facts.get(row.get('EvidenceId')) or {}
        linked = bool(fact.get('source_file') and fact.get('scope') and fact.get('tenant_id'))
        if (row.get('Confidence') == 'High' and row.get('Disposition') in {'Action','Assurance'}
                and (row.get('EvidenceAvailable') != 'Yes' or not linked) and not declared_support(row)):
            raise ValueError(str(row.get('RecommendationId') or 'Finding') +
                             ': high-confidence conclusion lacks required supporting evidence references.')

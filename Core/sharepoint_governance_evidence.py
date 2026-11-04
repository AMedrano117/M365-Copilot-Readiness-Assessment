"""SharePoint governance outcomes, independent of SharePoint Advanced Management.

SharePoint Advanced Management (SAM) is an optional evidence, governance and
remediation capability. This module assesses the governance outcomes Copilot
readiness depends on (owners, inactive sites, broad and special-group access,
broken inheritance, external access, sensitive content, high-risk sites, an
operating owner review and exceptions) from whichever evidence is retained:
SAM-assisted evidence when available, or dated equivalent evidence otherwise.

Licensing, entitlement, source availability, evidence sufficiency, technical
result and readiness treatment are separate fields. Absence of SAM never
creates a failure; only sufficient evidence that an outcome is not satisfied
does.

It also defines a bounded, versioned import contract for Content Management
Assessment results, which Microsoft does not expose through a supported API.
Distinct-site counts and issue (occurrence) counts are kept apart; overlapping
category counts are never added into a distinct-site total.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

SCHEMA = 'm365-readiness.sharepoint-governance-evidence'
SCHEMA_VERSIONS = frozenset({'1.0'})
ADAPTER_VERSION = '1.0.0'
VERSION = '1.0.0'
DEFAULT_MAX_AGE_DAYS = 35
SOURCE_TYPE = 'governance_evidence_import'

CAPABILITY_STATES = ('Available', 'PartiallyAvailable', 'DAGOnly', 'NotLicensed', 'NotRequested',
                     'Unavailable', 'Unsupported', 'Unknown')
TECHNICAL_RESULTS = ('Supported', 'Failed', 'NotEstablished', 'EvidenceRequest')
TREATMENT = {'Failed': 'Remediate', 'NotEstablished': 'EvidenceGap', 'EvidenceRequest': 'EvidenceGap', 'Supported': None}
CMA_CATEGORIES = {
    # category: (group, outcome)
    'inactive_sites': ('lifecycle', 'inactive_sites'),
    'site_ownership': ('lifecycle', 'site_owners'),
    'broken_inheritance': ('oversharing', 'broken_inheritance'),
    'eeeu': ('oversharing', 'special_groups'),
    'unrestricted_links': ('oversharing', 'special_groups'),
}
BASE_DIMENSIONS = ('tenant', 'population', 'scope', 'observed_at', 'complete')
# Outcome: title, control, customer workstream, outcome-specific required dimensions.
OUTCOMES = {
    'site_owners': ('Site owners are established', 'CONTENT.OWNERSHIP', 'sharepoint.site_governance', ('ownership',)),
    'inactive_sites': ('Inactive or abandoned sites are reviewed', 'CONTENT.OWNERSHIP', 'sharepoint.site_governance',
                       ('activity_state', 'review_result')),
    'owner_review_process': ('An owner-review or site-attestation process operates', 'CONTENT.OWNERSHIP',
                             'sharepoint.site_governance', ('operating_process', 'review_result')),
    'broad_access': ('Broad access is understood and justified', 'CONTENT.PERMISSIONS', 'sharepoint.high_risk_access',
                     ('access_condition', 'review_result')),
    'special_groups': ('Anonymous, organization-wide, Everyone and EEEU access is governed', 'CONTENT.PERMISSIONS',
                       'sharepoint.high_risk_access', ('access_condition',)),
    'broken_inheritance': ('Broken permission inheritance is reviewed', 'CONTENT.PERMISSIONS', 'sharepoint.high_risk_access',
                           ('access_condition', 'review_result')),
    'external_access': ('External access is reviewed', 'CONTENT.PERMISSIONS', 'sharepoint.high_risk_access',
                        ('access_condition', 'review_result')),
    'high_risk_sites': ('High-risk sites are prioritized', 'CONTENT.PERMISSIONS', 'sharepoint.high_risk_access', ('review_result',)),
    'exceptions': ('Exceptions are documented, approved and reviewed', 'CONTENT.PERMISSIONS', 'sharepoint.site_governance',
                   ('exceptions',)),
    'sensitive_content': ('Sensitive content is identified and protected', 'DATA.EXPOSURE', 'information_protection',
                          ('access_condition',)),
}
EVIDENCE_KEYS = {'CONTENT.OWNERSHIP': 'sharepoint_lifecycle_detail', 'CONTENT.PERMISSIONS': 'data_exposure_detail',
                 'DATA.EXPOSURE': 'data_exposure_detail'}
DOMAINS = {'CONTENT.OWNERSHIP': 'content', 'CONTENT.PERMISSIONS': 'content', 'DATA.EXPOSURE': 'data_protection'}
POLICY_TYPES = frozenset({'site_ownership', 'inactive_site', 'site_attestation', 'restricted_content_discovery',
                          'restricted_search', 'restricted_access_control', 'block_download', 'site_access_review'})
POLICY_OUTCOMES = {'site_ownership': 'site_owners', 'inactive_site': 'inactive_sites', 'site_attestation': 'owner_review_process',
                   'site_access_review': 'owner_review_process'}
# Customer workstreams. Titles are customer wording; keys are stable.
WORKSTREAMS = {
    'sharepoint.validate_evidence': 'Validate SharePoint governance evidence',
    'sharepoint.high_risk_access': 'Remediate high-risk access',
    'sharepoint.site_governance': 'Establish ongoing site governance',
    'authentication': 'Authentication',
    'information_protection': 'Information protection foundation for Copilot',
}
# Concise guidance per workstream: the SAM-assisted path and a viable non-SAM alternative.
PATHS = {
    'sharepoint.validate_evidence': (
        'Run or refresh the Content Management Assessment and Data access governance reports, then import the current results.',
        'Supply dated site, permission and sharing-link inventories from SharePoint Online PowerShell or Microsoft Graph, '
        'with owner reviews, using the governance evidence import.'),
    'sharepoint.high_risk_access': (
        'Use Data access governance reports to target sites; apply restricted access control, Restricted Content Discovery '
        'or site access reviews where they are available.',
        'Review the affected sites from site permission and sharing-link inventories; remove or justify broad access, '
        'and record each decision with its owner and date.'),
    'sharepoint.site_governance': (
        'Use site ownership, inactive-site and site attestation policies; start them in simulation, then activate.',
        'Run a recurring owner review from the site inventory and last-activity records, notify owners, and track '
        'exceptions and closure in a dated register.'),
}
SHAREPOINT_KEYS = {
    'sharepoint.validate_evidence': ('sharepoint.dag.not_assessed', 'sharepoint.governance.not_assessed', 'sharepoint.sharing.partial_settings',
                                     'data_exposure.coverage.sharepoint_oversharing_evidence', 'data_exposure.freshness',
                                     'data_exposure.lifecycle.freshness', 'data_exposure.snapshot_scope', 'data_exposure.scope',
                                     'coverage.content.permissions', 'coverage.content.sharing', 'sharepoint_governance.evidence'),
    'sharepoint.high_risk_access': ('data_exposure.anonymous_links', 'data_exposure.broad_internal_access', 'data_exposure.external_exposure',
                                    'sharepoint.sharing.', 'sharepoint.copilot.', 'sharepoint_governance.broad_access',
                                    'sharepoint_governance.special_groups', 'sharepoint_governance.broken_inheritance',
                                    'sharepoint_governance.external_access', 'sharepoint_governance.high_risk_sites'),
    'sharepoint.site_governance': ('data_exposure.ownerless_sites', 'data_exposure.inactive_sites', 'coverage.content.ownership',
                                   'sharepoint_governance.site_owners', 'sharepoint_governance.inactive_sites',
                                   'sharepoint_governance.owner_review_process', 'sharepoint_governance.exceptions'),
}


def governance_workstream(row):
    """Customer workstream of one finding; None outside SharePoint, authentication and information protection."""
    key = str(row.get('FindingKey') or '')
    if key == 'sharepoint.authentication.legacy_permitted':
        return 'authentication'
    if key.startswith('sharepoint_governance.') and row.get('Disposition') == 'Coverage' and key != 'sharepoint_governance.sensitive_content':
        # Imported evidence that does not establish an outcome is an evidence question, whatever the outcome.
        return 'sharepoint.validate_evidence'
    for workstream in ('sharepoint.validate_evidence', 'sharepoint.site_governance', 'sharepoint.high_risk_access'):
        if any(key == prefix or prefix.endswith('.') and key.startswith(prefix) or key.startswith(prefix + '.')
               for prefix in SHAREPOINT_KEYS[workstream]):
            return workstream
    if key == 'sharepoint_governance.sensitive_content' or key == 'data_exposure.coverage.sensitive-data_exposure_evidence' or (
            row.get('ControlId') in {'DATA.LABELS', 'DATA.PUBLISHING', 'DATA.DLP', 'DATA.RETENTION', 'DATA.EXPOSURE'}
            or key.startswith(('purview.raw.sensitivity_labels', 'purview.raw.label_policies', 'purview.raw.dlp', 'purview.rms',
                               'coverage.data.labels', 'coverage.data.publishing', 'coverage.data.dlp', 'coverage.data.retention',
                               'coverage.data.exposure'))):
        return 'information_protection'
    return None


# Information protection: recommended order of the related planning and actions.
INFORMATION_PROTECTION_SEQUENCE = (
    'Determine sensitive-content exposure.', 'Approve the classification model.', 'Create and publish labels.',
    'Review Copilot DLP simulation.', 'Approve appropriate enforcement.', 'Validate representative users and content.')


def _day(value):
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).strip().replace('Z', '+00:00')).date()
    except (TypeError, ValueError):
        return 'invalid'


def is_governance_evidence(path):
    """A JSON document that declares this contract's schema; filenames are never evidence of schema."""
    path = Path(path)
    if path.suffix.lower() != '.json' or not path.is_file():
        return False
    try:
        with path.open(encoding='utf-8-sig') as handle:
            document = json.load(handle)
    except (OSError, ValueError):
        return False
    return isinstance(document, dict) and document.get('schema') == SCHEMA


def _count(entry, diagnostics, name):
    """One count with its unit and basis; a count without a unit, or negative, is not used."""
    if not isinstance(entry, dict):
        diagnostics.append({'code': 'count_without_unit', 'field': name,
                            'detail': 'A count must declare its value, unit and distinct or occurrence basis.'})
        return None
    value, unit, basis = entry.get('value'), entry.get('unit'), entry.get('basis')
    if type(value) is not int:
        diagnostics.append({'code': 'invalid_count', 'field': name, 'detail': 'The count is missing or not a whole number.'})
        return None
    if value < 0:
        diagnostics.append({'code': 'negative_count', 'field': name, 'detail': 'Negative counts are rejected.'})
        return None
    if unit not in {'sites', 'issues'} or basis not in {'distinct', 'occurrence'}:
        diagnostics.append({'code': 'count_without_unit', 'field': name,
                            'detail': 'The count does not declare unit sites or issues and basis distinct or occurrence.'})
        return None
    if unit == 'issues' and basis == 'distinct' or unit == 'sites' and basis == 'occurrence':
        diagnostics.append({'code': 'inconsistent_count_basis', 'field': name,
                            'detail': 'Issue counts are occurrences and site counts are distinct; this count mixes them.'})
        return None
    return {'value': value, 'unit': unit, 'basis': basis}


def _metric(name, count, *, population, period, source, denominator=None, overlap=''):
    return {'metric': name, 'unit': count['unit'], 'basis': count['basis'], 'numerator': count['value'],
            'denominator': denominator, 'population': population, 'period': period, 'source': source,
            'overlap_qualification': overlap}


OVERLAP = ('Category counts can overlap: one site can appear in several categories. They are not added into a '
           'distinct-site total.')


def normalize_cma(section, *, source, evaluation_day, max_age_days, diagnostics):
    """Normalize one Content Management Assessment result without inventing missing fields."""
    if not isinstance(section, dict):
        return None
    local = []
    assessed_on = _day(section.get('assessment_date'))
    if assessed_on is None:
        local.append({'code': 'missing_assessment_date', 'detail': 'The assessment date is missing; freshness is unknown.'})
    elif assessed_on == 'invalid':
        local.append({'code': 'impossible_date', 'detail': 'The assessment date is not a valid date.'})
        assessed_on = None
    elif assessed_on > evaluation_day:
        local.append({'code': 'future_dated', 'detail': 'The assessment date is after the evaluation date.'})
    elif (evaluation_day - assessed_on).days > max_age_days:
        local.append({'code': 'stale', 'detail': f'The assessment is older than the {max_age_days}-day methodology window.'})
    previous = _day(section.get('previous_assessment_date'))
    if previous == 'invalid' or previous and assessed_on and previous not in (None, 'invalid') and previous > assessed_on:
        local.append({'code': 'impossible_date', 'detail': 'The previous assessment date is invalid or later than the assessment date.'})
        previous = None
    completion = str(section.get('completion_state') or '')
    completeness = str(section.get('source_completeness') or '')
    if completion.lower() != 'completed' or completeness.lower() != 'complete':
        local.append({'code': 'incomplete_export', 'detail': 'The assessment is not recorded as completed with a complete export.'})
    total = _count(section.get('total_sites_assessed'), local, 'total_sites_assessed') if 'total_sites_assessed' in section else None
    # With site records, a missing total only leaves the denominator unknown (qualified below).
    if 'total_sites_assessed' not in section and not section.get('sites'):
        local.append({'code': 'missing_totals', 'detail': 'The number of sites assessed is missing.'})
    if total and total['unit'] != 'sites':
        local.append({'code': 'issue_total_as_sites', 'field': 'total_sites_assessed',
                      'detail': 'The sites-assessed total is not a distinct-site count.'})
        total = None
    period = assessed_on.isoformat() if isinstance(assessed_on, date) else ''
    groups = {}
    for group in ('lifecycle', 'oversharing'):
        data = section.get(group) or {}
        distinct = _count(data.get('distinct_sites'), local, f'{group}.distinct_sites') if 'distinct_sites' in data else None
        if distinct and distinct['unit'] != 'sites':
            local.append({'code': 'issue_total_as_sites', 'field': f'{group}.distinct_sites',
                          'detail': 'An issue total was supplied as the number of distinct sites; it is not used as a site count.'})
            distinct = None
        categories = {}
        for name, entry in (data.get('categories') or {}).items():
            if CMA_CATEGORIES.get(name, (None,))[0] != group:
                local.append({'code': 'unknown_category', 'field': f'{group}.{name}', 'detail': 'Unrecognized category; retained but not evaluated.'})
                continue
            parsed = {}
            for measure in ('affected_sites', 'issues'):
                if measure in (entry or {}):
                    count = _count(entry[measure], local, f'{group}.{name}.{measure}')
                    if count and count['unit'] != ('sites' if measure == 'affected_sites' else 'issues'):
                        local.append({'code': 'issue_total_as_sites' if measure == 'affected_sites' else 'inconsistent_count_basis',
                                      'field': f'{group}.{name}.{measure}', 'detail': 'The count unit does not match the measure.'})
                        count = None
                    if count:
                        parsed[measure] = count
            for sub in ('no_owner', 'single_owner'):
                if sub in (entry or {}):
                    count = _count(entry[sub], local, f'{group}.{name}.{sub}')
                    if count and count['unit'] == 'sites':
                        parsed[sub] = count
            categories[name] = parsed
        if distinct and total and distinct['value'] > total['value']:
            local.append({'code': 'conflicting_totals', 'field': f'{group}.distinct_sites',
                          'detail': 'More distinct sites require attention than were assessed.'})
        for name, parsed in categories.items():
            affected = parsed.get('affected_sites')
            if affected and distinct and affected['value'] > distinct['value']:
                local.append({'code': 'conflicting_totals', 'field': f'{group}.{name}.affected_sites',
                              'detail': 'A category affects more sites than the distinct sites requiring attention.'})
        groups[group] = {'distinct_sites': distinct, 'categories': categories}
    sites, seen, urls = [], {}, {}
    for record in section.get('sites') or []:
        site_id, url = str((record or {}).get('site_id') or '').strip().lower(), str((record or {}).get('url') or '').strip().lower()
        if not site_id:
            local.append({'code': 'site_without_identifier', 'detail': 'A site record has no site ID and is not counted.'})
            continue
        if site_id in seen:
            if url and seen[site_id] and url != seen[site_id]:
                local.append({'code': 'conflicting_site_identifiers', 'site_id': site_id,
                              'detail': 'The same site ID has different URLs.'})
            else:
                local.append({'code': 'duplicate_site_records', 'site_id': site_id, 'detail': 'Duplicate site record; counted once.'})
            continue
        if url and url in urls and urls[url] != site_id:
            local.append({'code': 'conflicting_site_identifiers', 'site_id': site_id, 'detail': 'The same URL has different site IDs.'})
        seen[site_id] = url
        if url:
            urls[url] = site_id
        categories = sorted({str(item) for item in (record.get('categories') or []) if str(item) in CMA_CATEGORIES})
        sites.append({'site_id': site_id, 'url': url, 'categories': categories,
                      'owner_count': record.get('owner_count') if type(record.get('owner_count')) is int else None})
    if sites:
        for group, data in groups.items():
            for name, parsed in data['categories'].items():
                declared = (parsed.get('affected_sites') or {}).get('value')
                derived = sum(name in site['categories'] for site in sites)
                if declared is not None and declared != derived:
                    local.append({'code': 'conflicting_totals', 'field': f'{group}.{name}.affected_sites',
                                  'detail': f'The summary reports {declared} affected sites; the site records show {derived}.'})
    diagnostics.extend(dict(item, section='content_management_assessment') for item in local)
    codes = {item['code'] for item in local}
    metrics = []
    population = 'Sites assessed by Content Management Assessment'
    if total:
        metrics.append(_metric('Sites assessed', total, population=population, period=period, source=source))
    for group, data in groups.items():
        if data['distinct_sites']:
            metrics.append(_metric(f'Distinct sites requiring {group} attention', data['distinct_sites'], population=population,
                                   period=period, source=source, denominator=total['value'] if total else None))
        for name, parsed in data['categories'].items():
            for measure, count in parsed.items():
                metrics.append(_metric(f'{name.replace("_", " ")} {measure.replace("_", " ")}', count, population=population,
                                       period=period, source=source, denominator=total['value'] if total and count['unit'] == 'sites' else None,
                                       overlap=OVERLAP))
    return {
        'assessment_id': str(section.get('assessment_id') or ''), 'assessment_date': period,
        'completion_state': completion, 'source_completeness': completeness,
        'source_qualification': ' '.join(filter(None, (str(section.get('source_qualification') or ''),
            'The number of sites assessed was not supplied; site records may list only impacted sites.'
            if 'total_sites_assessed' not in section and sites else ''))),
        'previous_assessment_date': previous.isoformat() if isinstance(previous, date) else '',
        'trend': section.get('trend') if isinstance(section.get('trend'), (dict, str)) else None,
        'total_sites_assessed': total, 'groups': groups, 'sites': sites, 'site_level': bool(sites),
        'summary': any(data['distinct_sites'] or data['categories'] for data in groups.values()) or bool(total),
        'metrics': metrics, 'diagnostic_codes': sorted(codes), 'adapter_version': ADAPTER_VERSION, 'source': source,
        # Site-level records answer affected-site questions without a summary total.
        'usable': not codes & ({'missing_assessment_date', 'impossible_date', 'future_dated', 'stale', 'incomplete_export',
                                'conflicting_totals', 'conflicting_site_identifiers', 'missing_totals'}),
    }


def load_governance_evidence(paths, *, evaluation_date=None, expected_tenant_id=None, max_age_days=DEFAULT_MAX_AGE_DAYS):
    """Load and validate governance evidence documents; malformed input stays qualified, never normalized silently."""
    day = _day(evaluation_date) if evaluation_date else datetime.now(timezone.utc).date()
    day = day if isinstance(day, date) else datetime.now(timezone.utc).date()
    result = {'documents': [], 'cma': None, 'equivalent': [], 'policies': [], 'capability': {}, 'diagnostics': [],
              'adapter_version': ADAPTER_VERSION, 'tenant_id': ''}
    cmas = []
    for path in paths or []:
        path = Path(path)
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        source = path.name
        try:
            document = json.loads(raw.decode('utf-8-sig'))
        except ValueError:
            result['diagnostics'].append({'code': 'unknown_schema', 'source': source, 'detail': 'The file is not valid JSON.'})
            continue
        if not isinstance(document, dict) or document.get('schema') != SCHEMA or str(document.get('schema_version')) not in SCHEMA_VERSIONS:
            result['diagnostics'].append({'code': 'unknown_schema', 'source': source,
                                          'detail': 'Unrecognized schema or schema version; the file was not imported.'})
            continue
        tenant = str(document.get('tenant_id') or '').lower()
        if expected_tenant_id and tenant and tenant != str(expected_tenant_id).lower():
            result['diagnostics'].append({'code': 'tenant_mismatch', 'source': source,
                                          'detail': 'The document tenant does not match the assessed tenant; it was not imported.'})
            continue
        if not tenant:
            result['diagnostics'].append({'code': 'missing_tenant', 'source': source,
                                          'detail': 'The document does not record its tenant; conclusions remain qualified.'})
        result['tenant_id'] = result['tenant_id'] or tenant
        result['documents'].append({'source': source, 'sha256': digest, 'schema_version': str(document['schema_version']), 'tenant_id': tenant})
        capability = document.get('capability') or {}
        if capability:
            result['capability'] = {**capability, 'source': source}
        if 'content_management_assessment' in document:
            cma = normalize_cma(document['content_management_assessment'], source=source, evaluation_day=day,
                                max_age_days=max_age_days, diagnostics=result['diagnostics'])
            if cma:
                cma['tenant_id'] = tenant
                cmas.append(cma)
        for item in document.get('equivalent_evidence') or []:
            result['equivalent'].append(_equivalent(item, source, tenant, day, max_age_days, result['diagnostics']))
        for policy in document.get('policies') or []:
            if (policy or {}).get('type') in POLICY_TYPES:
                result['policies'].append({'type': policy['type'], 'state': str(policy.get('state') or 'Unknown'),
                                           'scope': str(policy.get('scope') or ''), 'observed_at': str(policy.get('observed_at') or ''),
                                           'source': source})
            else:
                result['diagnostics'].append({'code': 'unknown_policy_type', 'source': source, 'detail': 'Unrecognized policy type; not evaluated.'})
    if len(cmas) > 1:
        keys = {(cma['assessment_id'], cma['assessment_date']) for cma in cmas}
        if len(keys) > 1 or len({json.dumps(cma['groups'], sort_keys=True) for cma in cmas}) > 1:
            result['diagnostics'].append({'code': 'conflicting_assessment_runs', 'section': 'content_management_assessment',
                                          'detail': 'Several assessment runs were supplied with different identities or totals; '
                                                    'none is selected silently.'})
            for cma in cmas:
                cma['usable'] = False
                cma['diagnostic_codes'] = sorted(set(cma['diagnostic_codes']) | {'conflicting_assessment_runs'})
        result['cma'] = cmas[0]
        result['cma_runs'] = cmas
    elif cmas:
        result['cma'] = cmas[0]
    return result


def _equivalent(item, source, tenant, day, max_age_days, diagnostics):
    item = item or {}
    outcome = item.get('outcome')
    observed = _day(item.get('observed_at'))
    missing = []
    if not tenant:
        missing.append('tenant')
    for name in ('population', 'scope'):
        if not str(item.get(name) or '').strip():
            missing.append(name)
    if not isinstance(observed, date):
        missing.append('observed_at')
    elif observed > day or (day - observed).days > max_age_days:
        missing.append('current observation date')
    if item.get('complete') is not True:
        missing.append('complete')
    required = OUTCOMES.get(outcome, (None, None, None, ()))[3]
    dimensions = item.get('dimensions') or {}
    missing += [name for name in required if dimensions.get(name) in (None, '', [])]
    result = item.get('result') if item.get('result') in {'satisfied', 'not_satisfied'} else None
    if result is None:
        missing.append('review result')
    if outcome not in OUTCOMES:
        diagnostics.append({'code': 'unknown_outcome', 'source': source, 'detail': 'Equivalent evidence names an unrecognized outcome.'})
    affected = item.get('affected_sites') if type(item.get('affected_sites')) is int and item.get('affected_sites') >= 0 else None
    issues = item.get('issue_count') if type(item.get('issue_count')) is int and item.get('issue_count') >= 0 else None
    return {'outcome': outcome, 'source': source, 'source_name': str(item.get('source') or ''), 'tenant_id': tenant,
            'population': str(item.get('population') or ''), 'scope': str(item.get('scope') or ''),
            'observed_at': observed.isoformat() if isinstance(observed, date) else '', 'complete': item.get('complete') is True,
            'result': result, 'affected_sites': affected, 'issue_count': issues, 'missing': missing,
            'sufficient': not missing and outcome in OUTCOMES}


def sam_capability(governance=None, exposure=None, imported=None, recommendations=()):
    """SAM capability state with license, entitlement and source availability kept separate."""
    governance, exposure, imported = governance or {}, exposure or {}, imported or {}
    declared = imported.get('capability') or {}
    basis = str(declared.get('basis') or '')
    # Portal visibility never proves entitlement.
    entitlement_verified = declared.get('entitlement_verified') is True and basis not in {'', 'portal_visibility'}
    license_declared = declared.get('sam_license') if declared.get('sam_license') in {'Licensed', 'NotLicensed', 'Unknown'} else None
    copilot_observed = any('copilot' in str(row.get('Feature') or '').lower() and 'sharepoint' in str(row.get('Feature') or '').lower()
                           for row in recommendations or [])
    sam = ((exposure.get('sources') or {}).get('sam') or {})
    dag_reports = [report for report in sam.get('reports') or [] if report.get('status') in {'selected', 'no_activity'}
                   and report.get('report_type') != 'content_management_assessment']
    dag = bool(dag_reports) or bool(governance.get('dag_completed_count'))
    cma = bool(imported.get('cma')) or bool((exposure.get('lifecycle_summary') or {}).get('available'))
    settings = governance.get('settings') or {}
    sam_features = [name for name, present in (
        ('Content Management Assessment', cma),
        ('Restricted access control', str(settings.get('EnableRestrictedAccessControl')).lower() == 'true'),
        ('Restricted Content Discovery', any(str(site.get('RestrictContentOrgWideSearch')).lower() == 'true'
                                             for site in governance.get('sites') or [])),
        ('Site lifecycle policies', any(policy['type'] in POLICY_OUTCOMES and policy['state'].lower() in {'active', 'simulation'}
                                        for policy in imported.get('policies') or [])),
    ) if present]
    declared_state = declared.get('state') if declared.get('state') in CAPABILITY_STATES else None
    if license_declared == 'NotLicensed' or declared_state == 'NotLicensed':
        state = 'NotLicensed'
    elif declared_state == 'Unsupported':
        state = 'Unsupported'
    elif entitlement_verified and declared_state in {'Available', 'PartiallyAvailable'}:
        state = declared_state
    elif entitlement_verified:
        state = 'Available'
    elif dag and sam_features:
        state = 'PartiallyAvailable'
    elif dag:
        state = 'DAGOnly'
    elif sam_features:
        state = 'PartiallyAvailable'
    elif governance.get('availability_status') == 'not_requested' and not imported.get('documents'):
        state = 'NotRequested'
    elif governance and not governance.get('available', True) and governance.get('availability_status') in {'unavailable', 'failed', 'inaccessible'}:
        state = 'Unavailable'
    else:
        state = 'Unknown'
    license_evidence = ({'Licensed': 'Declared licensed', 'NotLicensed': 'Declared not licensed', 'Unknown': 'Declared unknown'}[license_declared]
                        if license_declared else 'Copilot license observed; included SAM features not verified' if copilot_observed
                        else 'Not recorded')
    return {'state': state, 'license_evidence': license_evidence, 'entitlement_verified': entitlement_verified,
            'entitlement_basis': basis or 'Not recorded',
            'source_availability': {'data_access_governance': dag, 'content_management_assessment': cma, 'sam_features_observed': sam_features},
            'capability_result': 'CapabilityUnknown' if state == 'Unknown' else state,
            'qualification': ('Portal visibility, a Copilot or E5 license, a Data access governance report or one SAM feature '
                              'does not establish full SAM availability.' if state in {'DAGOnly', 'PartiallyAvailable', 'Unknown'} else '')}


def _cma_items(cma):
    """Outcome evidence from a CMA result; summary counts alone never replace site-level records."""
    if not cma:
        return {}
    items = {}
    for name, (group, outcome) in CMA_CATEGORIES.items():
        parsed = (cma['groups'].get(group) or {}).get('categories', {}).get(name)
        site_ids = sorted(site['site_id'] for site in cma['sites'] if name in site['categories'])
        # A category absent from the summary and from every site record is not evidence either way.
        if parsed is None and not site_ids:
            continue
        affected = len(site_ids) if cma['site_level'] else (parsed or {}).get('affected_sites', {}).get('value')
        issues = (parsed or {}).get('issues', {}).get('value')
        entry = items.setdefault(outcome, {'categories': [], 'affected_site_ids': set(), 'affected_sites': None, 'issue_count': None,
                                           'no_owner': None, 'site_level': cma['site_level'], 'usable': cma['usable'],
                                           'summary_zero': True})
        entry['categories'].append(name)
        # Site records may list only impacted sites; zero needs a summary that states zero.
        entry['summary_zero'] = entry['summary_zero'] and (parsed or {}).get('affected_sites', {}).get('value') == 0
        entry['affected_site_ids'].update(site_ids)
        if issues is not None:
            entry['issue_count'] = (entry['issue_count'] or 0) + issues
        if not cma['site_level'] and affected is not None:
            # Summary-only category counts can overlap; keep the largest as a lower bound, never a sum.
            entry['affected_sites'] = max(entry['affected_sites'] or 0, affected)
        if (parsed or {}).get('no_owner'):
            entry['no_owner'] = parsed['no_owner']['value']
    for entry in items.values():
        if entry['site_level']:
            entry['affected_sites'] = len(entry['affected_site_ids'])
        entry['affected_site_ids'] = sorted(entry['affected_site_ids'])
    return items


def _existing_condition(rows, outcome):
    """Confirmed exposure from another source is evaluated independently of SAM."""
    keys = {'special_groups': ('data_exposure.anonymous_links', 'data_exposure.broad_internal_access',
                               'sharepoint.sharing.permissive_anonymous_defaults'),
            'external_access': ('data_exposure.external_exposure',),
            'site_owners': ('data_exposure.ownerless_sites',), 'inactive_sites': ('data_exposure.inactive_sites',),
            'sensitive_content': ('data_exposure.sensitive_items', 'data_exposure.unlabeled_sensitive_items')}.get(outcome, ())
    matched = [row for row in rows if str(row.get('FindingKey') or '') in keys and row.get('Disposition') == 'Action']
    return matched


def evaluate_outcomes(capability, imported=None, rows=()):
    imported = imported or {}
    cma = imported.get('cma')
    cma_items = _cma_items(cma)
    equivalent = {}
    for item in imported.get('equivalent') or []:
        equivalent.setdefault(item['outcome'], []).append(item)
    sam_path = capability['state'] in {'Available', 'PartiallyAvailable', 'DAGOnly'}
    outcomes = []
    for outcome, (title, control, workstream, _) in OUTCOMES.items():
        items = equivalent.get(outcome) or []
        sufficient = [item for item in items if item['sufficient']]
        from_cma = cma_items.get(outcome)
        existing = _existing_condition(rows, outcome)
        result, path, sufficiency, qualification = 'NotEstablished', 'none', 'No evidence', ''
        affected = issues = None
        if sufficient:
            path = 'equivalent'
            failed = [item for item in sufficient if item['result'] == 'not_satisfied']
            result = 'Failed' if failed else 'Supported'
            sufficiency = 'Sufficient'
            chosen = failed or sufficient
            affected = max((item['affected_sites'] for item in chosen if item['affected_sites'] is not None), default=None)
            issues = max((item['issue_count'] for item in chosen if item['issue_count'] is not None), default=None)
        elif from_cma:
            path = 'sam'
            affected, issues = from_cma['affected_sites'], from_cma['issue_count']
            if not from_cma['usable']:
                sufficiency = 'Insufficient: ' + ', '.join(cma['diagnostic_codes'])
            elif not from_cma['site_level']:
                sufficiency = 'Insufficient: portal summary without site-level records'
                qualification = 'Summary counts are retained; site-level records are needed to identify affected sites.'
            elif affected == 0 and not from_cma['summary_zero']:
                sufficiency = 'Insufficient: site records may list only impacted sites, and no summary states zero'
            elif affected == 0:
                result, sufficiency = 'Supported', 'Sufficient'
            elif outcome == 'site_owners' and from_cma.get('no_owner'):
                result, sufficiency = 'Failed', 'Sufficient'
                qualification = 'Sites without any owner are confirmed; sites with a single owner are retained as review context.'
            else:
                sufficiency = 'Sufficient for the affected population; review result not recorded'
                qualification = 'Affected sites are identified; whether each is justified or remediated is not recorded.'
        elif items:
            path = 'equivalent'
            sufficiency = 'Insufficient: missing ' + ', '.join(sorted({name for item in items for name in item['missing']}))
        if existing and result != 'Failed':
            confirmed = [row for row in existing if row.get('EvidenceStatus', 'supported') == 'supported'
                         and row.get('ReadinessTreatment') not in {'ScopeReviewRequired', 'ConfirmBeforePilot', 'EvidenceGap'}]
            if confirmed:
                result, sufficiency = 'Failed', 'Sufficient'
                path = path if path != 'none' else 'other_source'
                qualification = 'Confirmed by retained evidence from another source, independently of SAM.'
        if result == 'NotEstablished' and path == 'none' and sam_path:
            result = 'EvidenceRequest'
            sufficiency = 'SAM-assisted reports were not run or supplied'
        outcomes.append({
            'outcome': outcome, 'title': title, 'control_id': control, 'workstream': workstream,
            'workstream_label': WORKSTREAMS[workstream], 'technical_result': result, 'evidence_path': path,
            'evidence_sufficiency': sufficiency,
            # An identified population awaiting review is a scope review, not missing evidence.
            'readiness_treatment': 'ScopeReviewRequired' if sufficiency.startswith('Sufficient for the affected population')
                                   and result == 'NotEstablished' else TREATMENT[result],
            'affected_sites': affected, 'issue_count': issues,
            'affected_site_ids': (from_cma or {}).get('affected_site_ids', []) if path == 'sam' else [],
            'overlap_qualification': OVERLAP if path == 'sam' and len((from_cma or {}).get('categories', [])) > 1 or
                                     (from_cma and not from_cma['site_level']) else '',
            'categories': (from_cma or {}).get('categories', []), 'qualification': qualification,
            'capability_state': capability['state'], 'capability_result': capability['capability_result'],
            'sam_path': PATHS.get(workstream, ('', ''))[0], 'non_sam_path': PATHS.get(workstream, ('', ''))[1],
            'evidence_items': [{key: item[key] for key in ('source', 'source_name', 'observed_at', 'population', 'scope', 'result', 'missing')}
                               for item in items],
        })
    return outcomes


def _row(outcome, imported, day_text):
    """Customer finding for one imported outcome; the shared result builder qualifies it."""
    cma = imported.get('cma') or {}
    item = next((entry for entry in imported.get('equivalent') or [] if entry['outcome'] == outcome['outcome']), None)
    from_sam = outcome['evidence_path'] == 'sam'
    source = cma.get('source') if from_sam else (item or {}).get('source', '')
    observed = cma.get('assessment_date') if from_sam else (item or {}).get('observed_at', '')
    complete = (cma.get('usable') and cma.get('site_level')) if from_sam else bool(item and item['sufficient'])
    result = outcome['technical_result']
    affected = outcome['affected_sites']
    counts = []
    if affected is not None:
        counts.append(f"{affected:,} distinct affected site{'s' if affected != 1 else ''}")
    if outcome['issue_count'] is not None:
        counts.append(f"{outcome['issue_count']:,} issue occurrence{'s' if outcome['issue_count'] != 1 else ''}")
    basis = 'Content Management Assessment' if from_sam else 'Equivalent governance evidence'
    if result == 'Supported':
        observation = f"{basis} supports this outcome: {outcome['title'].lower()}."
        disposition, treatment = 'Assurance', None
    elif result == 'Failed':
        observation = f"{basis} shows this outcome is not satisfied: {outcome['title'].lower()}."
        disposition, treatment = 'Action', 'Remediate'
    elif outcome['evidence_sufficiency'].startswith('Sufficient for the affected population'):
        observation = f"{basis} identifies sites for review: {outcome['title'].lower()} is not yet established."
        disposition, treatment = 'Action', 'ScopeReviewRequired'
    else:
        observation = f"{basis} was supplied but does not establish this outcome ({outcome['evidence_sufficiency'].lower()})."
        disposition, treatment = 'Coverage', 'EvidenceGap'
    observation += (' ' + '; '.join(counts) + '.') if counts else ''
    if outcome['overlap_qualification']:
        observation += ' ' + outcome['overlap_qualification']
    control = outcome['control_id']
    row = {
        'Service': 'Data Exposure', 'Feature': outcome['title'], 'FindingKey': 'sharepoint_governance.' + outcome['outcome'],
        'ControlId': control, 'DomainId': DOMAINS[control], 'Observation': observation,
        'Recommendation': '' if result == 'Supported' else (outcome['non_sam_path'] if not from_sam else outcome['sam_path']),
        'Priority': 'High' if result == 'Failed' else 'Medium', 'Status': 'Success' if result == 'Supported' else
        'Action Required' if result == 'Failed' else 'Attention Required' if disposition == 'Action' else 'Not Assessed',
        'Disposition': disposition, 'ReadinessTreatment': treatment, 'Category': 'Tenant Finding' if disposition != 'Coverage' else 'Scan Coverage',
        'EvidenceKey': EVIDENCE_KEYS[control], 'EvidenceBasis': 'Tenant evidence', 'Confidence': 'High' if complete else 'Low',
        'SourceType': 'portal_export', 'SourceFile': source, 'SourceAvailability': 'available' if complete else 'partial',
        'ObservationDate': observed, 'EvidenceScope': (cma.get('total_sites_assessed') and 'Sites assessed by Content Management Assessment')
        if from_sam else (item or {}).get('scope', ''), 'EvidenceComplete': bool(complete), 'TenantId': imported.get('tenant_id', ''),
        'LinkText': 'Content Management Assessment' if from_sam else 'SharePoint governance evidence import',
        'LinkUrl': 'https://learn.microsoft.com/sharepoint/content-management-assessment',
        'CompletionEvidence': 'A dated review of the affected sites, with each decision, owner and exception recorded.',
        'GovernanceOutcome': outcome['outcome'], 'GovernanceResult': result, 'GovernanceEvidencePath': outcome['evidence_path'],
        'EvidenceSufficiency': outcome['evidence_sufficiency'], 'AffectedSiteCount': affected, 'IssueCount': outcome['issue_count'],
        'OverlapQualification': outcome['overlap_qualification'], 'SamCapabilityState': outcome['capability_state'],
        'SamPath': outcome['sam_path'], 'NonSamPath': outcome['non_sam_path'], 'SharePointGovernanceVersion': VERSION,
        'Qualification': outcome['qualification'],
    }
    return row


def _validation_row(imported):
    codes = sorted({item['code'] for item in imported.get('diagnostics') or []})
    if not codes:
        return None
    return {
        'Service': 'Data Exposure', 'Feature': 'Validate the SharePoint governance evidence import',
        'FindingKey': 'sharepoint_governance.evidence', 'DomainId': 'content',
        'Observation': 'The SharePoint governance evidence import has unresolved validation results: ' + ', '.join(
            code.replace('_', ' ') for code in codes) + '. Affected values were not used to support or fail a governance outcome.',
        'Recommendation': 'Supply a current, complete export with a valid assessment date, consistent totals and one record per site.',
        'Priority': 'Medium', 'Status': 'Not Assessed', 'Disposition': 'Coverage', 'ReadinessTreatment': 'EvidenceGap',
        # Describes the import itself, so it answers no control question.
        'Category': 'Scan Coverage', 'EvidenceKey': 'sharepoint_governance_import', 'EvidenceBasis': 'Collection status',
        'Confidence': 'Unknown', 'SourceType': 'portal_export',
        'SourceFile': '; '.join(sorted({doc['source'] for doc in imported.get('documents') or []})),
        'GovernanceImportDiagnostics': imported.get('diagnostics'), 'SharePointGovernanceVersion': VERSION,
        'OptionalEvidence': 'Yes',
    }


def assess_sharepoint_governance(governance=None, exposure=None, imported=None, recommendations=(), *, evaluation_date=None):
    """Capability state, outcome results, customer rows and control facts for the shared result builder."""
    imported = imported or {}
    capability = sam_capability(governance, exposure, imported, recommendations)
    existing = list(recommendations or []) + list((exposure or {}).get('recommendations') or [])
    # Rows describe what the imported evidence shows; other sources keep their own findings.
    outcomes = evaluate_outcomes(capability, imported, ())
    rows, observations = [], []
    imported_outcomes = [item for item in outcomes if item['evidence_path'] in {'sam', 'equivalent'}]
    for outcome in imported_outcomes:
        rows.append(_row(outcome, imported, evaluation_date))
    validation = _validation_row(imported)
    if validation:
        rows.append(validation)
    # A control passes only when every outcome it requires is supported by sufficient imported
    # evidence and no other retained source shows an open condition for it.
    for control in sorted({item['control_id'] for item in outcomes}):
        members = [item for item in outcomes if item['control_id'] == control]
        contradicted = any(_existing_condition(existing, item['outcome']) for item in members)
        if not contradicted and all(item['technical_result'] == 'Supported' and item['evidence_path'] in {'sam', 'equivalent'}
                                    for item in members):
            dates = sorted(row['ObservationDate'] for row in rows if row.get('ControlId') == control and row.get('ObservationDate'))
            sources = sorted({row['SourceFile'] for row in rows if row.get('ControlId') == control and row.get('SourceFile')})
            observations.append({
                'metric_id': 'sharepoint_governance.' + control.lower(), 'label': control, 'domain_id': DOMAINS[control],
                'control_id': control, 'control_result': 'pass', 'value': 'Supported', 'unit': 'governance outcome',
                'availability': 'available', 'tenant_id': imported.get('tenant_id', ''),
                'population': 'SharePoint sites', 'scope': 'Tenant-wide SharePoint governance outcomes',
                'observed_at': dates[0] if dates else '', 'complete': True, 'source_type': SOURCE_TYPE,
                'source_file': '; '.join(sources), 'source_schema': SCHEMA, 'evidence_key': EVIDENCE_KEYS[control],
                'evidence_level': 'configuration', 'max_age_days': DEFAULT_MAX_AGE_DAYS,
            })
    return {'version': VERSION, 'capability': capability, 'outcomes': outcomes, 'recommendations': rows,
            'observations': observations, 'cma': imported.get('cma'), 'diagnostics': imported.get('diagnostics') or [],
            'documents': imported.get('documents') or [], 'policies': imported.get('policies') or [],
            'imported': imported}


def project_outcomes(assessment, records):
    """Final outcome view over qualified records; confirmed exposure from any source is included."""
    if not assessment:
        return None
    outcomes = evaluate_outcomes(assessment['capability'], assessment.get('imported') or {}, records)
    by_outcome = {}
    for row in records:
        if row.get('GovernanceOutcome'):
            by_outcome[row['GovernanceOutcome']] = row.get('RecommendationId')
    for outcome in outcomes:
        outcome['recommendation_id'] = by_outcome.get(outcome['outcome'], '')
    return {key: value for key, value in assessment.items() if key not in {'imported', 'recommendations', 'observations'}} | {
        'outcomes': outcomes}

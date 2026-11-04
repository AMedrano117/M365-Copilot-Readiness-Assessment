"""Run fictional SharePoint governance scenarios through production functions only.

Documents use the versioned import contract with invented site IDs. The
harness writes them to a temporary folder, routes the folder the way
--reports-dir does, and passes the result to the shared assessment builder.
No semantics are implemented here.
"""
import copy
import json
import tempfile
from pathlib import Path

from Core.assessment_result import build_assessment_result
from Core.new_recommendation import new_recommendation
from Core.portal_report_import import route_portal_reports
from Core.sharepoint_governance import summarize_sharepoint_governance
from Core.sharepoint_governance_evidence import SCHEMA, assess_sharepoint_governance, load_governance_evidence

TENANT = '00000000-0000-4000-8000-0000000000aa'
EVALUATION = '2026-10-20'
DATE = '2026-10-15'

# Fictional sites. Lifecycle: inactive s01-s04, ownership s04-s06 (s04 overlaps) = 6 distinct.
# Oversharing: broken s07-s09, EEEU s09-s10, links s10-s11 (s09, s10 overlap) = 5 distinct.
SITES = [
    {'site_id': 's01', 'url': 'https://fictional.example/sites/a', 'categories': ['inactive_sites']},
    {'site_id': 's02', 'url': 'https://fictional.example/sites/b', 'categories': ['inactive_sites']},
    {'site_id': 's03', 'url': 'https://fictional.example/sites/c', 'categories': ['inactive_sites']},
    {'site_id': 's04', 'url': 'https://fictional.example/sites/d', 'categories': ['inactive_sites', 'site_ownership']},
    {'site_id': 's05', 'url': 'https://fictional.example/sites/e', 'categories': ['site_ownership'], 'owner_count': 0},
    {'site_id': 's06', 'url': 'https://fictional.example/sites/f', 'categories': ['site_ownership'], 'owner_count': 1},
    {'site_id': 's07', 'url': 'https://fictional.example/sites/g', 'categories': ['broken_inheritance']},
    {'site_id': 's08', 'url': 'https://fictional.example/sites/h', 'categories': ['broken_inheritance']},
    {'site_id': 's09', 'url': 'https://fictional.example/sites/i', 'categories': ['broken_inheritance', 'eeeu']},
    {'site_id': 's10', 'url': 'https://fictional.example/sites/j', 'categories': ['eeeu', 'unrestricted_links']},
    {'site_id': 's11', 'url': 'https://fictional.example/sites/k', 'categories': ['unrestricted_links']},
]


def sites(n):
    return {'value': n, 'unit': 'sites', 'basis': 'distinct'}


def issues(n):
    return {'value': n, 'unit': 'issues', 'basis': 'occurrence'}


def cma(*, include_sites=True, lifecycle=True, oversharing=True, **override):
    section = {
        'assessment_id': 'CMA-FICTIONAL-1', 'assessment_date': DATE, 'completion_state': 'Completed',
        'source_completeness': 'complete', 'total_sites_assessed': sites(40), 'previous_assessment_date': '2026-09-15',
        'trend': 'improving', 'source_qualification': 'Fictional export',
    }
    if lifecycle:
        section['lifecycle'] = {'distinct_sites': sites(6), 'categories': {
            'inactive_sites': {'affected_sites': sites(4), 'issues': issues(4)},
            'site_ownership': {'affected_sites': sites(3), 'no_owner': sites(1), 'single_owner': sites(1)}}}
    if oversharing:
        section['oversharing'] = {'distinct_sites': sites(5), 'categories': {
            'broken_inheritance': {'affected_sites': sites(3), 'issues': issues(9)},
            'eeeu': {'affected_sites': sites(2), 'issues': issues(5)},
            'unrestricted_links': {'affected_sites': sites(2), 'issues': issues(7)}}}
    if include_sites:
        chosen = [site for site in SITES if any(
            (lifecycle and category in {'inactive_sites', 'site_ownership'}) or (oversharing and category not in {'inactive_sites', 'site_ownership'})
            for category in site['categories'])]
        section['sites'] = copy.deepcopy(chosen)
    section.update(override)
    return section


def equivalent(outcome, result='satisfied', **override):
    item = {'outcome': outcome, 'source': 'SharePoint Online PowerShell inventory and owner review', 'observed_at': DATE,
            'population': 'All SharePoint sites', 'scope': 'Tenant-wide', 'complete': True, 'result': result,
            'dimensions': {'ownership': 'Owner inventory', 'activity_state': 'Last-activity records', 'review_result': 'Owner decisions',
                           'operating_process': 'Quarterly owner review', 'access_condition': 'Permission and link inventory',
                           'exceptions': 'Approved exception register'},
            'affected_sites': 0 if result == 'satisfied' else 2, 'issue_count': 0 if result == 'satisfied' else 3}
    item.update(override)
    return item


def document(**sections):
    return {'schema': SCHEMA, 'schema_version': '1.0', 'tenant_id': TENANT, **sections}


def _governance(settings=None, sites_rows=None):
    payload = {'available': True, 'availability_status': 'available',
               'tenant': {'available': True, 'settings': settings or {}}}
    if sites_rows is not None:
        payload['sites'] = {'available': True, 'items': sites_rows}
    return summarize_sharepoint_governance(payload, evaluation_date=EVALUATION)


def other_source_row(key, feature):
    """A retained finding produced by another source, built with the production constructor."""
    row = new_recommendation('Data Exposure', feature, f'{feature}: 2 site(s) measured in the supplied inventory.',
                             'Review the listed sites.', priority='High', status='Action Required', disposition='Action',
                             finding_key=key, evidence_key='data_exposure_detail', evidence_basis='Tenant evidence', confidence='High')
    row.update(SourceType='portal_export', SourceFile='fictional-inventory.csv', ObservationDate=DATE, TenantId=TENANT,
               EvidenceScope='Tenant-wide', EvidenceComplete=True, SourceAvailability='available')
    return row


def run(documents=(), *, governance=None, exposure=None, extra_rows=(), raw_files=None):
    """Production path: route a reports folder, load, assess, then build the shared result."""
    with tempfile.TemporaryDirectory() as folder:
        for index, item in enumerate(documents):
            Path(folder, f'governance-{index}.json').write_text(json.dumps(item), encoding='utf-8')
        for name, text in (raw_files or {}).items():
            Path(folder, name).write_text(text, encoding='utf-8')
        routed = route_portal_reports([folder])
        imported = load_governance_evidence(routed['sharepoint_governance'], evaluation_date=EVALUATION, expected_tenant_id=TENANT)
    rows = [copy.deepcopy(row) for row in extra_rows]
    assessment = assess_sharepoint_governance(governance or {}, exposure or {}, imported, rows, evaluation_date=EVALUATION)
    records = rows + copy.deepcopy(assessment['recommendations'])
    bundle = {'expected_tenant_id': TENANT, 'observations': copy.deepcopy(assessment['observations']),
              'data_exposure': {'sharepoint_governance': assessment},
              'collection_context': {'tenant_id': TENANT, 'evaluation_date': EVALUATION, 'mode': 'offline'}}
    result = build_assessment_result(records, bundle, evaluation_date=EVALUATION, expected_tenant_id=TENANT)
    return result, assessment, routed


def outcome(result, name):
    return next(item for item in result['sharepoint_governance']['outcomes'] if item['outcome'] == name)


def control(result, control_id):
    return next(item['status'] for item in result['controls'] if item['control_id'] == control_id)


def sharepoint_actions(result):
    return [item for item in result['customer_actions'] if str(item.get('governance_workstream') or '').startswith('sharepoint.')]


def customer_text(result):
    """Every customer-facing string for the SharePoint actions and governance findings."""
    parts = []
    for item in sharepoint_actions(result):
        parts += [item['title'], item.get('narrative') or '', item.get('required_decision') or '', *item.get('next_steps', []),
                  json.dumps(item.get('sharepoint') or {})]
    for row in result['recommendations']:
        if str(row.get('FindingKey') or '').startswith(('sharepoint', 'data_exposure', 'coverage.content')):
            parts += [str(row.get('Observation') or ''), str(row.get('Recommendation') or '')]
    return ' '.join(parts)

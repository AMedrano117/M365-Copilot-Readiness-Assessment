"""Load declared identity scenarios and run them through production functions only.

Each scenario JSON in tests/scenarios/identity patches one fictional base tenant.
The harness builds the same retained client a collection would save, generates
the Entra findings with the collection-time function, then evaluates them with
the processor's shared Entra path and the shared assessment result. It adds no
semantics of its own.
"""
import copy
import csv
import json
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace

from Core.assessment_catalog import collect_assessment_sources
from Core.assessment_result import QUESTIONS, build_assessment_result
from Core.get_entra_info import build_entra_recommendations
from Core.processor import entra_recommendations

SCENARIO_DIR = Path(__file__).resolve().parent / 'scenarios' / 'identity'
BASE = json.loads((SCENARIO_DIR / '_base_tenant.json').read_text(encoding='utf-8'))
TEXT_FIELDS = ('Feature', 'ActionTitle', 'FindingTitle', 'Observation', 'Recommendation')
CONTROLS = {question[0] for question in QUESTIONS}


def scenario_names():
    return sorted(path.stem for path in SCENARIO_DIR.glob('[0-9]*.json'))


def load(name):
    return json.loads((SCENARIO_DIR / f'{name}.json').read_text(encoding='utf-8'))


def merge(base, patch):
    """Objects merge, lists and scalars replace, and null removes a key."""
    result = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def source_state(code, tenant):
    common = {'collected_at': tenant['collected_at'], 'tenant_id': tenant['tenant_id'],
              'scope': 'Assessed tenant', 'source_file': 'collection.json',
              'records_collected': 1, 'pages_collected': 1}
    if code == 'available':
        return {**common, 'available': True, 'availability_status': 'available', 'complete': True, 'truncated': False}
    if code == 'partial':
        return {**common, 'available': True, 'availability_status': 'partial', 'complete': False, 'truncated': True,
                'reason': 'Pagination stopped after the first page.'}
    if code == 'not_requested':
        return {**common, 'available': False, 'availability_status': 'not_requested', 'complete': False,
                'truncated': False, 'records_collected': 0, 'pages_collected': 0,
                'reason': 'Not requested by the Restricted permission profile.'}
    if code.startswith('unavailable'):
        status = int(code.split(':')[1]) if ':' in code else None
        return {**common, 'available': False, 'availability_status': 'unavailable', 'complete': False,
                'truncated': False, 'records_collected': 0, 'pages_collected': 0, 'status_code': status,
                'reason': f'HTTP {status}: Forbidden' if status else 'Request failed', 'error': 'Forbidden'}
    raise ValueError(f'Unknown declared source state {code!r}')


def tenant_for(spec, license_profile=None):
    tenant = merge(BASE, spec.get('patch') or {})
    if license_profile:
        tenant['license_profile'] = license_profile
    return tenant


def build_client(tenant):
    attributes = copy.deepcopy(tenant['client'])
    statuses = {name: source_state(code, tenant) for name, code in tenant['sources'].items() if code != 'absent'}
    for name, extra in (tenant.get('source_overrides') or {}).items():
        statuses.setdefault(name, {}).update(extra)
    data_sources = {name: state.get('available') is True and state.get('complete') is True for name, state in statuses.items()}
    return SimpleNamespace(**attributes, collection_status=statuses, data_sources=data_sources)


def profile_for(tenant):
    purposes = tenant.get('account_purposes') or []
    return {'assessment_context': {'account_purposes': purposes}} if purposes else {}


def exposure_rows(spec, tenant):
    exposure = spec.get('exposure')
    if not exposure:
        return [], []
    from Core.data_exposure_assessment import build_data_exposure_assessment
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'sam.csv'
        rows = exposure['sam_rows']
        with path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        result = build_data_exposure_assessment([str(path)], [], evaluation_date=tenant['evaluation_date'])
    return result['recommendations'], result.get('observations', [])


def run(spec, license_profile=None, *, saved_rows=None, client=None):
    """Return (result, bundle, rows) for one declared scenario."""
    tenant = tenant_for(spec, license_profile)
    client = client or build_client(tenant)
    licenses = tenant['license_profiles'][tenant['license_profile']]
    profile = profile_for(tenant)
    # Collection time: the live collector saves these rows with the client.
    saved = build_entra_recommendations(licenses, client) if saved_rows is None else saved_rows
    entra_info = {'licenses': licenses, '_client': client, 'recommendations': saved}
    rows = entra_recommendations(entra_info, collected_at=tenant['collected_at'], profile=profile)
    extra, observations = exposure_rows(spec, tenant)
    rows = rows + extra
    bundle = {
        'source_statuses': dict(client.collection_status),
        'collection_context': {'tenant_id': tenant['tenant_id'], 'collected_at': tenant['collected_at'],
                               'source_file': 'collection.json', 'evaluation_date': tenant['evaluation_date'], 'mode': 'offline'},
        'assessment_sources': collect_assessment_sources([client], collected_at=tenant['collected_at'], profile=profile,
                                                          recommendations=rows),
        'assessment_profile': profile, 'expected_tenant_id': tenant['tenant_id'], 'observations': observations,
    }
    result = build_assessment_result(rows, bundle, evaluation_date=tenant['evaluation_date'],
                                     expected_tenant_id=tenant['tenant_id'])
    bundle['assessment_result'] = result
    return result, bundle, rows


def text(row):
    return ' '.join(str(row.get(field) or '') for field in TEXT_FIELDS)


def control(result, control_id):
    return next(row for row in result['controls'] if row['control_id'] == control_id)


def control_actions(result, control_id):
    ids = set(control(result, control_id)['recommendation_ids'])
    return [row for row in result['actions'] if row.get('RecommendationId') in ids]


def technical_conclusions(result):
    """Identity and application conclusions, excluding license-inventory context rows."""
    rows = []
    for row in result['recommendations']:
        if row.get('DomainId') not in {'identity', 'applications'}:
            continue
        if row.get('EvidenceBasis') == 'License signal' or row.get('LicenseContext'):
            continue
        rows.append((row.get('FindingKey') or '', row.get('ControlId') or '', row.get('Disposition') or '',
                     row.get('Priority') or '', row.get('ActionType') or '', row.get('SemanticCondition') or '',
                     re.sub(r'\s+', ' ', str(row.get('Observation') or '')).strip()))
    controls = sorted((row['control_id'], row['status'], row['configuration_result'])
                      for row in result['controls'] if row['domain_id'] in {'identity', 'applications'})
    return sorted(rows), controls

"""Run declared control-correlation scenarios through production functions only.

Each JSON file in tests/scenarios/correlation declares its inputs and expected
results before the production change. Identity and exposure scenarios reuse the
identity harness; other kinds build the same retained datasets a collection
saves and call the shared producers and assessment result. No semantics here.
"""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

try:
    from tests import identity_scenarios as identity_harness
except ImportError:  # unittest discover -s tests imports modules from the tests folder
    import identity_scenarios as identity_harness

from Core.assessment_result import build_assessment_result
from Core.new_recommendation import new_recommendation

DIRECTORY = Path(__file__).resolve().parent / 'scenarios' / 'correlation'
TENANT = identity_harness.BASE['tenant_id']
COLLECTED = identity_harness.BASE['collected_at']
EVALUATION = identity_harness.BASE['evaluation_date']
DAY = EVALUATION


def names():
    return sorted(path.stem for path in DIRECTORY.glob('C*.json'))


def load(name):
    return json.loads((DIRECTORY / f'{name}.json').read_text(encoding='utf-8'))


def dataset(records, **metadata):
    return {'records': copy.deepcopy(records), 'source': {
        'available': True, 'availability_status': 'available', 'complete': True, 'truncated': False,
        'collected_at': COLLECTED, 'scope': 'Fictional tenant scope', 'tenant_id': TENANT,
        'source_file': 'collection.json', **metadata}}


def _bundle(sources=None, **extra):
    # Declared rows name this retained source, as a collection records it.
    state = dataset([])['source']
    return {'assessment_sources': sources or {}, 'expected_tenant_id': TENANT, 'source_statuses': {'purview_policies': state},
            'collection_context': {'tenant_id': TENANT, 'collected_at': COLLECTED, 'source_file': 'collection.json',
                                   'evaluation_date': EVALUATION, 'mode': 'offline'}, **extra}


def _declared_row(item):
    """A retained finding built with the production constructor."""
    disposition = item.get('disposition', 'Action')
    basis = item.get('basis', 'Tenant evidence')
    status = item.get('status') or {'Action': 'Attention Required', 'Opportunity': 'Insight', 'Reference': 'Success'}[disposition]
    service = 'Purview' if str(item.get('key') or '').startswith('purview.') else 'M365'
    row = new_recommendation(service, item['feature'], item['observation'], item.get('recommendation', ''),
                             priority=item.get('priority', 'Medium'), status=status, disposition=disposition,
                             finding_key=item.get('key') or None,
                             evidence_key='service_plan_inventory' if basis == 'License signal' else 'purview_policy_detail',
                             evidence_basis=basis, confidence='High' if basis == 'Tenant evidence' else 'Low')
    row.update(EvidenceSource='purview_policies', EvidenceComplete=True, EvidenceScope='Tenant policy configuration',
               ObservationDate=COLLECTED, TenantId=TENANT, SourceType='tenant_collection', SourceFile='collection.json')
    if item.get('control'):
        row['ControlId'] = item['control']
    return row


def _purview_client():
    state = {'available': True, 'availability_status': 'available', 'complete': True, 'truncated': False,
             'collected_at': COLLECTED, 'tenant_id': TENANT, 'scope': 'Assessed tenant', 'source_file': 'collection.json'}
    return SimpleNamespace(irm_config={'available': True, 'azure_rms_enabled': True},
                           sensitivity_labels={'available': True, 'labels': []},
                           label_policies={'available': True, 'policies': []},
                           collection_status={name: dict(state) for name in ('irm_config', 'sensitivity_labels', 'label_policies')},
                           cache_provenance={'collected_at': COLLECTED, 'source_file': 'collection.json', 'source_type': 'tenant_collection'})


def _device_bundle(spec):
    from Core.expanded_findings import expanded_findings
    devices = [dict(device, deviceName='Fictional device ' + device['id'], complianceState='noncompliant',
                    lastSyncDateTime=DAY) for device in spec.get('devices') or []]
    sources = {'managed_devices': [dataset(devices)]}
    if spec.get('settings'):
        settings = [dict(row, ParentId='summary-1', lastReportedDateTime=DAY) for row in spec['settings']]
        sources['compliance_setting_states'] = [dataset(settings, source_api=(
            'https://graph.microsoft.com/v1.0/deviceManagement/deviceCompliancePolicySettingStateSummaries/'
            'summary-1/deviceComplianceSettingStates'))]
    if spec.get('policies'):
        sources['compliance_policies'] = [dataset(spec['policies'])]
    if spec.get('protection'):
        sources['windows_protection'] = [dataset([dict(row, lastReportedDateTime=DAY) for row in spec['protection']])]
    bundle = _bundle(sources, evaluation_date=DAY, sheets={})
    return expanded_findings(bundle, evaluation_date=DAY), bundle


def run(spec):
    """Return (result, bundle) for one declared scenario."""
    kind = spec['kind']
    if kind in {'identity', 'exposure'}:
        result, bundle, _ = identity_harness.run(spec)
        return result, bundle
    if kind == 'outputs':
        return run(load(spec['base']))
    if kind == 'device':
        rows, bundle = _device_bundle(spec)
    elif kind == 'purview':
        from Core.saved_control_checks import assess_purview_configuration
        from Recommendations.purview.RMS_S_PREMIUM import get_recommendation
        client = _purview_client()
        rows = asyncio.run(get_recommendation('RMS_S_PREMIUM', 'Success', purview_client=client))
        rows += assess_purview_configuration(client, COLLECTED)
        bundle = _bundle()
    else:
        sources = {name: [dataset(value.get('records') or [], **(value.get('source') or {}))]
                   for name, value in (spec.get('sources') or {}).items()}
        rows = [_declared_row(item) for item in spec.get('rows') or []]
        bundle = _bundle(sources, **({'ai_usage': copy.deepcopy(spec['ai_usage'])} if spec.get('ai_usage') else {}))
    result = build_assessment_result(rows, bundle, evaluation_date=EVALUATION, expected_tenant_id=TENANT)
    bundle['assessment_result'] = result
    return result, bundle


def find_row(result, check):
    rows = result['recommendations']
    if check.get('finding_key'):
        matches = [row for row in rows if row.get('FindingKey') == check['finding_key']]
    elif check.get('finding_key_prefix'):
        matches = [row for row in rows if str(row.get('FindingKey') or '').startswith(check['finding_key_prefix'])]
    else:
        matches = [row for row in rows if row.get('Feature') == check['feature']]
    return matches


def narrative(row):
    """Every customer-facing statement attached to one finding."""
    correlation = row.get('ControlCorrelation') or {}
    parts = [row.get('Observation'), row.get('Recommendation'), correlation.get('customer_narrative'),
             correlation.get('residual_risk'), correlation.get('required_decision'), correlation.get('closure_evidence'),
             *(correlation.get('next_steps') or [])]
    return ' '.join(str(part) for part in parts if part)


def customer_actions_with(result, keys):
    """Customer actions whose members include any of these finding keys."""
    keys = set(keys)
    return [action for action in result.get('customer_actions') or []
            if keys & {member.get('finding_key') for member in action.get('members') or []}]


def customer_action_text(action):
    return ' '.join(str(part) for part in (action.get('title'), action.get('narrative'), action.get('required_decision'),
                                           action.get('closure_evidence'), *(action.get('next_steps') or []),
                                           *(member.get('summary') for member in action.get('members') or [])) if part)

"""Bounded endpoint finding qualification before the shared decision is made."""

from .device_evidence import (CA_QUALIFICATION, annotate_finding, count_text, population,
                              interpret_records, value, timestamp)
from .new_recommendation import new_recommendation
from .evidence_contract import parse_date
from collections import Counter
import json


def _finding(key, feature, observation, action, items, kind, *, disposition='Opportunity'):
    row = new_recommendation('Entra', feature, observation, action, priority='Medium',
        status='Attention Required', disposition=disposition, finding_key=key,
        evidence_key='entra_device_detail', evidence_basis='Retained device records', confidence='Low')
    dates = [item['timestamp']['date'] for item in items if item['timestamp']['state'] == 'valid']
    states = [item['source_metadata'] for item in items]
    row.update(ControlId='ENDPOINT.POSTURE', DomainId='endpoints',
        ObservationDate=min(dates) if dates else '', EvidenceMaxAgeDays=7,
        EvidenceComplete=bool(items) and all(state.get('complete') is True for state in states),
        EvidenceScope='Selected retained device records; not fleet or pilot coverage',
        EvidenceLevel='observed_operation', SourceType='tenant_collection',
        InvestigationEvidence={'kind':'records', 'records':[item['record'] for item in items],
            'reason':observation, 'source':{
                'dataset':'managed_devices',
                'source_api': next((state.get('source_api') for state in states if state.get('source_api')), ''),
                'scope':'Retained managed-device inventory', 'complete':all(state.get('complete') is True for state in states)},
            'reconciliation':{'operation':'count','expected':len(items)}})
    return annotate_finding(row, items, kind)


def qualify_device_findings(recommendations, bundle, compliance):
    """Qualify retained legacy device conclusions too; do not rewrite raw evidence."""
    from .identity_investigation import is_noncompliant_device_finding
    inventory = compliance['inventory']
    day = parse_date(compliance['evaluation_date'])
    device_sources = {'compliance_setting_states','windows_protection','antivirus_health'}
    legacy_sources = {str(row.get('FindingKey') or '').split('.')[1] for row in recommendations
        if not row.get('DeviceEvidenceVersion') and str(row.get('FindingKey') or '').startswith('expanded.')
        and len(str(row.get('FindingKey')).split('.')) > 1
        and str(row.get('FindingKey')).split('.')[1] in device_sources}
    if legacy_sources:
        from .expanded_findings import expanded_findings
        fresh = [row for row in expanded_findings(bundle, profile=bundle.get('assessment_profile'),
            evaluation_date=day, tenant_id=bundle.get('expected_tenant_id'))
            if str(row.get('FindingKey') or '').split('.')[1] in legacy_sources]
        present = {row.get('FindingKey') for row in recommendations if row.get('DeviceEvidenceVersion')}
        # Re-evaluation is explicit at build time. Frozen snapshot rendering never
        # enters this path. Rebuild only these endpoint families from native rows.
        recommendations = [row for row in recommendations if not (
            not row.get('DeviceEvidenceVersion') and str(row.get('FindingKey') or '').startswith('expanded.') and
            str(row.get('FindingKey')).split('.')[1] in legacy_sources and
            (bundle.get('assessment_sources') or {}).get(str(row.get('FindingKey')).split('.')[1]))]
        recommendations += [row for row in fresh if row.get('FindingKey') not in present]
    output, has_inventory = [], False
    for original in recommendations:
        if original.get('DeviceFindingType') == 'PilotDevicePostureNotEstablished' or original.get('FindingKey') == 'device.pilot.posture_not_established':
            continue
        row = dict(original)
        text = ' '.join(str(row.get(key) or '') for key in ('Observation', 'Recommendation', 'OriginalRecommendation'))
        key = str(row.get('FindingKey') or '')
        if not row.get('DeviceEvidenceVersion') and key.startswith('expanded.') and key.split('.')[1] in device_sources:
            # No native rows: retain the old claim technically, never endorse its
            # device count, date, active-risk interpretation or pilot impact.
            row.update(UnverifiedSourceObservation=row.get('Observation'),
                Feature='Confirm earlier endpoint observations', Disposition='Opportunity', Priority='Medium',
                Observation='No native endpoint records are retained for validation. The earlier endpoint conclusion remains unverified.',
                Recommendation='Obtain dated device and setting records, reconcile stable identities and confirm the current protection provider and intended pilot population.',
                ObservationDate='', EvidenceComplete=False,
                InvestigationEvidence={'kind':'records','records':[],'source':{},'reconciliation':{'operation':'count','expected':0}})
            annotate_finding(row, [], 'UnverifiedLegacyDeviceObservation')
        if row.get('DeviceEvidenceVersion') and key.startswith('expanded.'):
            source = key.split('.')[1]
            interpreted = interpret_records(compliance['identity_model'], source,
                (bundle.get('assessment_sources') or {}).get(source, []))
            selected = Counter(json.dumps(raw,sort_keys=True,default=str) for raw in
                (row.get('InvestigationEvidence') or {}).get('records', []))
            items = []
            for item in interpreted:
                marker = json.dumps(item['record'],sort_keys=True,default=str)
                if selected[marker]:
                    items.append(item)
                    selected[marker] -= 1
            annotate_finding(row, items, row['DeviceFindingType'], confirmed=row['DeviceFindingType']=='ExplicitSecuritySettingFailure')
        ca = key == 'entra.devices.compliant_access' or 'bypass Intune security policies' in text or (
            'Conditional Access' in text and 'compliant devices for' in text)
        if ca:
            datasets = (bundle.get('assessment_sources') or {}).get('ca_policies', [])
            items = interpret_records(compliance['identity_model'], 'ca_policies', datasets)
            for item in items:
                state = item['source_metadata']
                item['timestamp'] = timestamp(state.get('refresh_date') or state.get('collected_at'), day)
            row.update(FindingKey='entra.devices.compliant_access',
                Feature='Confirm compliant-device Conditional Access enforcement',
                Observation=CA_QUALIFICATION+' '+count_text(population(items))+' Policy records are configuration evidence; they are not device failures or observed sign-in outcomes.',
                Recommendation='Review enabled policy grants, Microsoft 365 targeting, inclusions and exclusions. Validate the intended pilot population using report-only or sign-in results before enforcing changes.',
                OriginalRecommendation='', Priority='Medium', Disposition='Coverage', Status='Not Assessed',
                ControlId='ENDPOINT.POSTURE', DomainId='endpoints', EvidenceKey='conditional_access_detail',
                EvidenceLevel='configuration',
                InvestigationEvidence={'kind':'records','records':[item['record'] for item in items],
                    'source':dict(datasets[0].get('source') or {},dataset='ca_policies') if len(datasets)==1 else {'dataset':'ca_policies','scope':'Returned Conditional Access policies','complete':False},
                    'reason':CA_QUALIFICATION, 'reconciliation':{'operation':'count','expected':len(items)}})
            annotate_finding(row, items, 'CompliantDeviceAccessEnforcementNotEstablished')
            row['CompletionEvidence'] = 'Dated effective Conditional Access targeting and grant evaluation, plus scoped sign-in validation; preserve exclusions and report-only results.'
        elif is_noncompliant_device_finding(row):
            has_inventory = True
            selected = [item for item in inventory if str(value(item['record'], 'complianceState') or '').casefold() == 'noncompliant']
            row.update(Feature='Review Intune device noncompliance reasons', Disposition='Opportunity', Priority='Medium',
                Observation=count_text(population(selected))+' Overall Intune noncompliance requires reconciliation with setting, activity and enrollment evidence. It does not establish a security-setting cause, Copilot use or successful access.',
                InvestigationEvidence={'kind':'records','records':[item['record'] for item in selected],
                    'reason':'Retained overall noncompliant Intune inventory',
                    'source':dict(next((item['source_metadata'] for item in selected), {}),dataset='managed_devices'),
                    'reconciliation':{'operation':'count','expected':len(selected)}})
            annotate_finding(row, selected, 'InventoryComplianceState')
        if row.get('DeviceEvidenceVersion'):
            # Do not let source collection time replace an invalid/absent endpoint
            # report time in the common observation qualifier.
            counts = row['DeviceEvidenceCounts']
            if not counts['valid_timestamp_count']:
                row.update(ObservationDate='', EvidenceComplete=False)
            row.setdefault('ActionTitle', row.get('Feature'))
        output.append(row)
    if inventory and not has_inventory:
        counts = population(inventory)
        states = {}
        for item in inventory:
            state = str(value(item['record'], 'complianceState') or 'unknown').casefold()
            states[state] = states.get(state, 0)+1
        output.append(_finding('device.inventory.reconciliation', 'Reconcile device compliance inventory',
            count_text(counts)+f" Overall states: {states.get('noncompliant',0)} noncompliant; {states.get('compliant',0)} compliant; "
            f"{len(inventory)-states.get('noncompliant',0)-states.get('compliant',0)} other or unknown. "
            'Overall compliance is independent of each retained setting result and does not prove access or Copilot use.',
            'Reconcile current devices, stale or retired records, duplicate identities and setting-level failures. Validate policy assignment and device ownership; retain an explained inventory of every other state.',
            inventory, 'InventoryComplianceState'))
    from .device_summary import finding_summary
    for row in output:
        summary = finding_summary(row, compliance)
        if summary:
            row['DeviceSummary'] = summary
    return output

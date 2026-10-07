"""Findings tied directly to retained supplemental records and dated reviews."""

import re

from .assessment_catalog import CHECK_BY_ID, CHECK_CONTROLS, current_check_reviews
from .evidence_contract import evaluation_day, parse_date
from .new_recommendation import new_recommendation
from .operational_evidence import reported_boolean
from .control_reviews import _profile_raw
from .assessment_catalog import context_validation


def expanded_findings(bundle, *, profile=None, evaluation_date=None, tenant_id=None):
    day = evaluation_day(evaluation_date)
    output = []
    context=_profile_raw(profile or {}).get('assessment_context') or {}
    reporting_days=context.get('endpoint_reporting_days',7) if not context_validation(profile or {}) else 7

    def finding(name, selected, state, feature, explanation, *, domain='endpoints', control='ENDPOINT.POSTURE', disposition='Action', priority='Medium',
                recommendation='Review the exact retained records, document ownership and treatment, and repeat the applicable operational check.'):
        date = state.get('refresh_date') or state.get('collected_at') or ''
        row = new_recommendation('Defender' if domain=='endpoints' else 'Entra',feature,explanation,recommendation,
            status='Action Required' if disposition=='Action' else 'Not Assessed', priority=priority, disposition=disposition,
            finding_key='expanded.'+name, evidence_key='defender_device_detail' if domain=='endpoints' else 'app_access_detail',
            evidence_basis='Returned operational records', confidence='High' if disposition=='Action' else 'Unknown')
        row.update(ControlId=control,DomainId=domain,ObservationDate=date,EvidenceComplete=bool(selected),
            EvidenceScope=state.get('scope') or 'Returned tenant records',SourceType='live_api',EvidenceLevel='observed_operation',
            InvestigationEvidence={'kind':'records','records':selected,'source':state,
                'reason':explanation,'reconciliation':{'operation':'count','expected':len(selected)}})
        output.append(row)

    def health_finding(name,selected,state,feature,explanation,recommendation,priority='Medium'):
        # One finding per condition and freshness: devices that reported within the
        # endpoint window, then older or undated reports that need confirmation.
        # Grouping per report date repeated the same action for nearly every device.
        date_field='lastReportedDateTime' if name.startswith('windows_protection') else 'dataRefreshTimestamp'
        groups={'current':[],'older':[]}
        for record in selected:
            observed=parse_date(record.get(date_field))
            groups['current' if observed and 0<=(day-observed).days<=reporting_days else 'older'].append((observed,record))
        for group,entries in groups.items():
            if not entries: continue
            dates=sorted(observed for observed,_ in entries if observed)
            # The oldest current report keeps the whole finding inside the window;
            # older reports are dated by the newest one and still need confirmation.
            date=(dates[0] if group=='current' else dates[-1]).isoformat() if dates else ''
            span=('' if not dates else f' Last reported {dates[0].isoformat()}.' if dates[0]==dates[-1]
                  else f' Last reported between {dates[0].isoformat()} and {dates[-1].isoformat()}.')
            undated=len(entries)-len(dates)
            span+=f' {undated} record(s) have no report date.' if undated else ''
            finding(f'{name}.{group}',[record for _,record in entries],state,feature,
                explanation.format(count=len(entries))+span,priority=priority,recommendation=recommendation)
            output[-1].update(ObservationDate=date,EvidenceMaxAgeDays=reporting_days,
                EvidenceComplete=bool(date) and not undated,ObservationDateBasis=date_field,
                Qualification='State is dated by the endpoint report; recollection does not refresh stale or undated endpoint observations.')

    def merged_state(states):
        # Per-object reads (one per device or setting summary) describe one source.
        if len(states)==1:
            return states[0]
        dates=sorted(filter(None,(state.get('refresh_date') or state.get('collected_at') for state in states)))
        api=re.sub(r'/(managedDevices|deviceCompliancePolicySettingStateSummaries)/[^/]+/',r'/\1/{id}/',
                   str(states[0].get('source_api') or ''))
        return {'source_api':api,'scope':f'Returned tenant records from {len(states)} per-object reads',
                'collected_at':dates[-1] if dates else '','evidence_level':states[0].get('evidence_level'),
                'evidence_quality':states[0].get('evidence_quality'),
                'available':all(state.get('available') is True for state in states),
                'complete':all(state.get('complete') is True for state in states)}

    for name, datasets in bundle.get('assessment_sources',{}).items():
        if name in {'windows_protection','antivirus_health','compliance_setting_states'} and datasets:
            datasets=[{'source':merged_state([dataset.get('source',{}) for dataset in datasets]),
                       'records':[row for dataset in datasets for row in dataset.get('records',[])]}]
        for index,dataset in enumerate(datasets):
            state=dataset.get('source',{})
            records=dataset.get('records',[])
            if name=='windows_protection':
                disabled=[row for row in records if reported_boolean(row.get('realTimeProtectionEnabled')) is False]
                if disabled: health_finding(f'{name}.disabled',disabled,state,'Restore real-time antivirus protection',
                    '{count} Windows device(s) report Microsoft Defender Antivirus real-time protection turned off.',
                    'Turn real-time protection back on for the listed devices: assign an Intune Endpoint security > Antivirus policy '
                    'that allows real-time monitoring and turn on tamper protection so it cannot be switched off locally. '
                    'If a device intentionally runs another antivirus product, record that product as the approved exception.',priority='High')
                overdue=[row for row in records if reported_boolean(row.get('signatureUpdateOverdue')) is True]
                if overdue: health_finding(f'{name}.overdue',overdue,state,'Update overdue endpoint protection signatures',
                    '{count} Windows device(s) report overdue security intelligence (signature) updates.',
                    'Update security intelligence on the listed devices and confirm they can reach Windows Update or the configured '
                    'update source. Retire or re-enroll devices that no longer report.')
            elif name=='antivirus_health':
                outdated=[row for row in records if reported_boolean(row.get('avIsSignatureUpToDate')) is False]
                if outdated: health_finding(f'{name}.outdated',outdated,state,'Update observed antivirus signatures',
                    '{count} returned antivirus health record(s) report signatures are not current.',
                    'Update security intelligence on the listed devices and confirm they can reach the configured update source.')
            elif name=='compliance_setting_states':
                failed=[row for row in records if str(row.get('state','')).lower()=='noncompliant']
                if failed: finding(f'{name}.noncompliant',failed,state,'Resolve failed device compliance settings',
                    f'{len(failed)} returned setting record(s) report noncompliance. Settings are not a count of distinct devices.',
                    recommendation='In Intune (Devices > Compliance > the policy > Per-setting status), fix the failing setting on each '
                    'listed device, or change the policy if the setting is not required. Re-run the assessment to confirm.')
                errors=[row for row in records if str(row.get('state','')).lower() in {'error','conflict','unknown'}]
                if errors: finding(f'{name}.errors',errors,state,'Investigate device compliance reporting errors',
                    f'{len(errors)} setting record(s) report error, conflict or unknown. Effective compliance remains unresolved.',disposition='Coverage',
                    recommendation='Review the per-setting status in Intune for the listed records, resolve conflicting policy assignments '
                    'and devices that fail to report, then re-run the assessment.')
            elif name=='entra_recommendations' and records:
                finding(f'{name}.{index}',records,state,'Review Entra recommendation records',
                    'Microsoft Graph beta returned recommendation records. Their status and impacted resources are supplemental advice, not an independent foundation-control result.',
                    domain='identity',control='CATALOG.IDENTITY.RECOMMENDATIONS',disposition='Reference',priority='Low')
                output[-1].update(EvidenceLevel='configuration',EvidenceBasis='Preview API',EvidenceKey='conditional_access_detail')

    operations={'IDENTITY.SIGNIN_OPERATION','DEFENDER.REPORTING','CLASSIFICATION.ENFORCEMENT','GOVERNANCE.AUDIT'}
    for check_id,review in current_check_reviews(profile,day,tenant_id).items():
        if review['result'] not in {'fail','conflict'} or check_id in operations: continue
        check=CHECK_BY_ID[check_id]
        state={'scope':review['scope']['description'],'refresh_date':max(row['reviewed_at'] for row in review['records']),
               'complete':True,'evidence_level':check['evidence_level']}
        finding('review.'+check_id,review['records'],state,'Address reviewed check: '+check['description'],
            'Dated tenant-wide review records '+review['result']+': '+'; '.join(row['rationale'] for row in review['records']),
            domain=check['legacy_domain'],control=CHECK_CONTROLS.get(check_id,'CATALOG.'+check_id),
            disposition='Action' if review['result']=='fail' else 'Coverage')
        output[-1].update(SourceType='operator_attestation',EvidenceLevel=check['evidence_level'],AssessmentDomainId=check['domain_id'],
            OwnerRole=review['records'][0]['reviewer_role'])
    return output

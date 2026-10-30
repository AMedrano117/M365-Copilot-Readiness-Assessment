"""Findings tied directly to retained supplemental records and dated reviews."""

import re

from .assessment_catalog import CHECK_BY_ID, CHECK_CONTROLS, current_check_reviews
from .evidence_contract import evaluation_day, parse_date
from .new_recommendation import new_recommendation
from .operational_evidence import reported_boolean
from .control_reviews import _profile_raw
from .assessment_catalog import context_validation
from .device_evidence import interpret_records, annotate_finding, count_text, population, timestamp


def expanded_findings(bundle, *, profile=None, evaluation_date=None, tenant_id=None):
    if tenant_id and not bundle.get('expected_tenant_id'):
        bundle = dict(bundle, expected_tenant_id=tenant_id)
    day = evaluation_day(evaluation_date)
    output = []
    context=_profile_raw(profile or {}).get('assessment_context') or {}
    reporting_days=context.get('endpoint_reporting_days',7) if not context_validation(profile or {}) else 7
    from .device_compliance import build_device_compliance
    compliance = build_device_compliance(bundle, profile=profile, evaluation_date=day)
    health_items = {name: interpret_records(compliance['identity_model'], name,
                    (bundle.get('assessment_sources') or {}).get(name, [])) for name in ('windows_protection','antivirus_health')}

    def finding(name, selected, state, feature, explanation, *, domain='endpoints', control='ENDPOINT.POSTURE', disposition='Action', priority='Medium',
                recommendation='Review the exact retained records, document ownership and treatment, and repeat the applicable operational check.'):
        date = state.get('refresh_date') or state.get('collected_at') or ''
        row = new_recommendation('Defender' if domain=='endpoints' else 'Entra',feature,explanation,recommendation,
            status='Action Required' if disposition=='Action' else 'Not Assessed', priority=priority, disposition=disposition,
            finding_key='expanded.'+name, evidence_key='defender_device_detail' if domain=='endpoints' else 'app_access_detail',
            evidence_basis='Returned operational records', confidence='High' if disposition=='Action' else 'Unknown')
        if name.startswith(('compliance_setting_states', 'windows_protection', 'antivirus_health')):
            dataset = name.split('.')[0]
            row['EvidenceKey'] = {'compliance_setting_states':'compliance_setting_detail',
                'windows_protection':'windows_protection_detail', 'antivirus_health':'antivirus_health_detail'}[dataset]
            state = dict(state, dataset=dataset)
        row.update(ControlId=control,DomainId=domain,ObservationDate=date,EvidenceComplete=bool(selected),
            EvidenceAvailable='Yes' if selected else 'No',
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
            observed=parse_date(timestamp(record.get(date_field), day)['date'])
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
            import json
            from collections import Counter
            selected_keys = Counter(json.dumps(record,sort_keys=True,default=str) for _, record in entries)
            selected_items = []
            for item in health_items[name.split('.')[0]]:
                key = json.dumps(item['record'],sort_keys=True,default=str)
                if selected_keys[key]:
                    selected_items.append(item)
                    selected_keys[key] -= 1
            kind = 'DefenderRealTimeProtectionObservation' if '.disabled' in name else 'DefenderSignatureObservation'
            output[-1]['Observation'] = count_text(population(selected_items))+' '+output[-1]['Observation']
            if kind == 'DefenderRealTimeProtectionObservation':
                output[-1].update(Disposition='Opportunity', Status='Attention Required', Priority='Low', Confidence='Low')
                output[-1]['Observation'] += (' Real-time protection off is an observation, not proof of an active unprotected device. '
                    'Current protection effectiveness is not established without current AV mode, onboarding, approved third-party AV, platform and complete source correlation. '
                    'Returned Defender machine records do not establish fleet coverage.')
                output[-1]['CompletionEvidence'] = 'Dated protection-provider and AV-mode validation, onboarding status, approved antivirus exceptions and current device health.'
            annotate_finding(output[-1], selected_items, kind)

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
        if name in {'windows_protection','antivirus_health','compliance_setting_states','device_compliance_settings'} and datasets:
            datasets=[{'source':merged_state([dataset.get('source',{}) for dataset in datasets]),
                       'records':[row for dataset in datasets for row in dataset.get('records',[])]}]
        for index,dataset in enumerate(datasets):
            state=dataset.get('source',{})
            records=dataset.get('records',[])
            if name=='device_compliance_settings':
                config = compliance['tenant_settings']
                for key, field in config['fields'].items():
                    observed = field['state'] == 'observed'
                    finding(f'{name}.{key}',records,state,field['label'],
                        field['label']+': '+field['display']+'. '+config['qualification'],
                        disposition='Reference' if observed else 'Coverage',priority='Low' if observed else 'Medium',
                        recommendation='Review the retained tenant configuration and intended compliance design. Confirm missing or conflicting values in Intune.')
                    output[-1].update(Service='Entra',Status='Insight' if observed else 'Not Assessed',
                        EvidenceKey='device_compliance_settings_detail',EvidenceLevel='configuration',
                        EvidenceComplete=observed and state.get('complete') is True,
                        ObservationDate=field['observed_at'],EvidenceScope='Tenant-wide Intune compliance settings',
                        ConfigurationSetting=key,ConfigurationLabel=field['label'],ConfigurationValue=field['display'],
                        Qualification=config['qualification'])
            elif name=='windows_protection':
                disabled=[row for row in records if reported_boolean(row.get('realTimeProtectionEnabled')) is False]
                if disabled: health_finding(f'{name}.disabled',disabled,state,'Validate Defender real-time protection observations',
                    '{count} Windows protection record(s) report Microsoft Defender Antivirus real-time protection turned off.',
                    'Validate the current antivirus provider, approved third-party antivirus, Defender mode, onboarding, platform, '
                    'tamper protection and signatures against the retained report dates. Restore the intended protection only after '
                    'confirming current device ownership, activity and assessed tenant scope.',priority='Medium')
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
                failed=[item for item in compliance['settings'] if item['failed']]
                assignment=[item for item in compliance['settings'] if item['assignment_missing']]
                current=[item for item in failed if item['triage']=='current_security_failure']
                review=[item for item in failed if item['triage']!='current_security_failure' and not item['assignment_missing'] and item['kind'] in {'activity','identity'}]
                unconfirmed=[item for item in failed if item['triage']!='current_security_failure' and not item['assignment_missing'] and item['kind'] not in {'activity','identity'}]
                for group, items in (('current', current), ('review', review), ('unconfirmed', unconfirmed), ('assignment', assignment)):
                    if not items:
                        continue
                    reasons=list(dict.fromkeys(item['reason'] for item in items))
                    dates=sorted(filter(None,(parse_date(timestamp(item['last_reported'], day)['date']) for item in items)))
                    counts = population([item['evidence'] for item in items])
                    explanation=(f"{len(items)} "+('assignment-check' if group=='assignment' else 'failed setting')+
                                 f" record(s), linked to {counts['resolved_device_count']} distinct managed device(s); "
                                 f"{counts['unresolved_identity_count']} record(s) have unresolved device identity. "
                                 f"Reported IDs: {counts['distinct_intune_devices']} Intune, {counts['distinct_entra_devices']} Entra, {counts['distinct_defender_devices']} Defender. "
                                 'Settings are not a count of distinct devices. '+ '; '.join(reasons[:5])+'. ')
                    explanation+=('Intune reports no applicable compliance policy for these checks. '+compliance['policy_assignment']['qualification'] if group=='assignment' else
                                  'Explicit security-setting failures have recent device reports.' if group=='current' else
                        'Activity/enrollment failures require review. Noncompliance is retained; current security risk is not established by inactivity alone.' if group=='review' else
                        'Older or unresolved security states require confirmation; current security risk is not established by inactivity alone.')
                    finding(f'{name}.assignment_missing' if group=='assignment' else f'{name}.noncompliant' if group=='current' else f'{name}.reporting_review',
                        [item['record'] for item in items],state,
                        'Review devices reported without a compliance policy' if group=='assignment' else
                        'Resolve failed device compliance settings' if group=='current' else 'Review device compliance activity and unconfirmed failures',
                        explanation,priority='High' if group=='current' else 'Medium',disposition='Action' if group=='current' else 'Opportunity',
                        recommendation='Review the linked setting, policy and last check-in in Intune. Remediate explicit security failures; '
                        'confirm ownership and reporting for inactive devices before retiring or re-enrolling them. Do not treat stale devices as safe or compliant.')
                    output[-1].update(ObservationDate=dates[0].isoformat() if dates else '',
                        ObservationDateBasis='Retained setting report date, otherwise managed-device last sync; collection time does not refresh the observation',
                        EvidenceMaxAgeDays=compliance['reporting_days'],
                        Qualification=compliance['qualification'],DeviceComplianceTriage='review' if group=='unconfirmed' else group)
                    if group == 'unconfirmed':
                        output[-1]['FindingKey'] = 'expanded.compliance_setting_states.unconfirmed_security'
                        output[-1]['Feature'] = 'Reconcile older or unresolved security-setting records'
                    annotate_finding(output[-1], [item['evidence'] for item in items],
                        'ExplicitSecuritySettingFailure' if group=='current' else
                        'ActivityOrEnrollmentFailure' if group in {'review','assignment'} else 'UnconfirmedSettingState', confirmed=group=='current')
                    if group=='assignment':
                        output[-1].update(Service='Entra', Status='Attention Required', Recommendation='Review the listed devices and confirm an applicable compliance policy is assigned to their users or devices. '
                            'Check targeting, exclusions and last check-in, document intended exceptions, and verify the assignment result after the device reports. '
                            'Older or conflicting results require confirmation; absence of returned checks does not establish that all devices have a policy.')
                errors=[item['record'] for item in compliance['settings'] if item['classification']=='ReportingErrorOrConflict']
                if errors: finding(f'{name}.errors',errors,state,'Review unresolved device compliance results',
                    f'{len(errors)} setting record(s) report error, conflict or unknown. Effective compliance remains unresolved.',disposition='Coverage',
                    recommendation='Review the per-setting status in Intune for the listed records, resolve conflicting policy assignments '
                    'and devices that fail to report, then re-run the assessment.')
                if errors:
                    items=[item['evidence'] for item in compliance['settings'] if item['classification']=='ReportingErrorOrConflict']
                    output[-1]['Observation'] = count_text(population(items))+' Error, conflict, unknown and not-evaluated states do not establish confirmed noncompliance.'
                    annotate_finding(output[-1], items, 'ReportingErrorOrConflict')
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

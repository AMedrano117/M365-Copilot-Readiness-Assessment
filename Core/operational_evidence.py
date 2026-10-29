"""Conservative operational confirmation and stable-ID device reconciliation."""

from collections import defaultdict
from datetime import datetime, timezone

from .evidence_contract import evaluation_day, parse_date
from .assessment_catalog import current_check_reviews


OPERATION_CHECKS = {'IDENTITY.AUTH':'IDENTITY.SIGNIN_OPERATION', 'ENDPOINT.POSTURE':'DEFENDER.REPORTING',
                    'DATA.DLP':'CLASSIFICATION.ENFORCEMENT', 'DATA.AUDIT':'GOVERNANCE.AUDIT'}


def reported_boolean(value):
    """Read API boolean/string encodings without treating unknown values as false."""
    if type(value) is bool:
        return value
    if isinstance(value,str) and value.lower() in {'true','false'}:
        return value.lower()=='true'
    return None


def rows(bundle, source):
    return [record for dataset in bundle.get('assessment_sources', {}).get(source, []) for record in dataset.get('records', [])]


def source_current(bundle, source, day):
    datasets = bundle.get('assessment_sources', {}).get(source, [])
    return bool(datasets) and all(dataset.get('source', {}).get('complete') is True
        and (observed := parse_date(dataset['source'].get('refresh_date') or dataset['source'].get('collected_at')))
        and 0 <= (day-observed).days <= 35 for dataset in datasets)


def signin_record(record):
    from .signin_evidence import normalize_signin_event
    return normalize_signin_event(record)


def reconcile_devices(bundle, profile=None, *, evaluation_date=None):
    from .control_reviews import _profile_raw
    from .assessment_catalog import context_validation
    day = evaluation_day(evaluation_date or bundle.get('evaluation_date'))
    context = _profile_raw(profile or {}).get('assessment_context') or {}
    if context_validation(profile or {}):
        context = {}
    active_days, reporting_days = context.get('device_activity_days',30), context.get('endpoint_reporting_days',7)
    exceptions = {str(row.get('device_id','')).lower():row for row in context.get('exceptions', [])
                  if row.get('device_id') and parse_date(row.get('approved_at')) and parse_date(row.get('expires_at'))
                  and parse_date(row['approved_at']) <= day <= parse_date(row['expires_at'])}
    from .device_evidence import build_device_identities, timestamp
    model = build_device_identities(bundle, profile=profile, evaluation_date=day)
    output = []
    for identity in model['identities']:
        records = identity['sources']
        machines = [entry['record'] for entry in records if entry['source'] == 'machines']
        onboarded = bool(machines) and all(str(record.get('onboardingStatus', record.get('onboardingstatus', 'unknown'))).lower() == 'onboarded' for record in machines)
        reporting = onboarded and any(str(record.get('healthStatus', '')).lower() == 'active' and
            (date := timestamp(record.get('lastSeen'), day))['state'] == 'valid' and date['age_days'] <= reporting_days for record in machines)
        key = identity['entra_id'] or 'unmatched:' + identity['identity_key']
        output.append({'Device identity': key,
            'Activity': {'CurrentActiveDevice': 'active', 'StaleDeviceRecord': 'stale'}.get(identity['relevance'], 'unknown'),
            'Relevance': identity['relevance'], 'Pilot relevance': identity['pilot_relevance'],
            'Excluded': key in exceptions, 'Exception': exceptions.get(key),
            'Managed': bool(identity['intune_id']), 'Onboarded': onboarded, 'Actively reporting': reporting,
            'Conflict': identity['conflict'], 'Compliant': any(entry['source'] == 'managed_devices' and
                str(entry['record'].get('complianceState', '')).lower() == 'compliant' for entry in records),
            'Unmatched': not identity['entra_id'] or identity['unresolved'],
            'Duplicates': len(records) - len({entry['source'] for entry in records}),
            'Last observed': identity['last_observed'],
            'Sources': [{'source': entry['source'], 'record': entry['record']} for entry in records]})
    active = [row for row in output if row['Activity']=='active' and not row['Excluded']]
    justified = bool(active) and all(source_current(bundle,name,day) for name in ('directory_devices','managed_devices','machines')) and not any(row['Unmatched'] or row['Conflict'] or row['Activity']=='unknown' for row in output)
    denominator = len(active) if justified else None
    windows = [{'source':name, **dataset.get('source',{})} for name in ('directory_devices','managed_devices','machines')
               for dataset in bundle.get('assessment_sources',{}).get(name,[])]
    return {'records':output, 'active_days':active_days, 'reporting_days':reporting_days,
        'scope':'Known active tenant device records, reconciled by Microsoft Entra device ID; unseen devices are outside observation',
        'denominator':denominator, 'known_active':len(active),
        'observation_windows':windows, 'evaluation_date':day.isoformat(), 'population':'Known active, nonexcluded device identities',
        'onboarded':sum(row['Onboarded'] for row in active), 'reporting':sum(row['Actively reporting'] for row in active),
        'onboarding_percent':100*sum(row['Onboarded'] for row in active)/denominator if denominator else None,
        'reporting_percent':100*sum(row['Actively reporting'] for row in active)/denominator if denominator else None,
        'qualification':'Coverage percentages require complete current inventories, unambiguous identities and activity. Device enrollment, compliance, onboarding and telemetry are separate measures.'}


def operational_results(bundle, profile, *, evaluation_date=None, tenant_id=None):
    day = evaluation_day(evaluation_date)
    reviews = current_check_reviews(profile, day, tenant_id)
    result = {control:{'result':'unknown', 'reason':'Operational behavior is not established by configuration alone.', 'records':[]} for control in OPERATION_CHECKS}
    from .signin_evidence import normalize_signin_event
    datasets = bundle.get('assessment_sources', {}).get('signin_logs', [])
    signins = [normalize_signin_event(record, dataset.get('source')) for dataset in datasets for record in dataset.get('records', [])]
    current = [row for row in signins if (observed := parse_date(row.get('createdDateTime')))
               and 0 <= (day-observed).days <= 7
               and (not tenant_id or str(row['AuthenticationSource'].get('tenant_id') or
                    (bundle.get('collection_context') or {}).get('tenant_id') or '').casefold() == str(tenant_id).casefold())]
    successful_legacy = [row for row in current if row['LegacyAuthenticationState'] == 'SuccessfulLegacyAuthentication']
    conflicts = [row for row in current if row['AuthenticationConflicts']]
    if signins:
        result['IDENTITY.AUTH'] = {
            'result': 'conflict' if conflicts else 'fail' if successful_legacy else 'unknown',
            'reason': 'Conflicting authentication evidence requires resolution.' if conflicts else
            'A successful legacy-client token request contradicts the legacy-blocking requirement; favorable MFA or Conditional Access fields do not establish effective blocking.' if successful_legacy else
            'Returned sign-ins provide event-scoped authentication observations. A complete, dated tenant-wide operational review must establish the required population, MFA enforcement and legacy blocking; missing details do not prove bypass.',
            'records': signins, 'event_derived': True,
            'source': ((successful_legacy or conflicts or current or signins)[0]['AuthenticationSource'])}
    devices = reconcile_devices(bundle,profile,evaluation_date=day)
    if devices['denominator']:
        # Device reporting alone establishes no real-time or antivirus protection.
        active = [row for row in devices['records'] if row['Activity']=='active' and not row['Excluded']]
        protection,antivirus = defaultdict(list),defaultdict(list)
        for row in rows(bundle,'windows_protection'): protection[str(row.get('ParentId'))].append(row)
        for row in rows(bundle,'antivirus_health'): antivirus[str(row.get('machineId'))].append(row)
        healthy = source_current(bundle,'windows_protection',day) and source_current(bundle,'antivirus_health',day)
        def recent(value):
            from .device_evidence import timestamp
            observed = timestamp(value, day)
            return observed['state'] == 'valid' and observed['age_days'] <= devices['reporting_days']
        for row in active:
            managed = [entry['record'] for entry in row['Sources'] if entry['source']=='managed_devices']
            machines = [entry['record'] for entry in row['Sources'] if entry['source']=='machines']
            healthy = healthy and row['Compliant'] and row['Actively reporting'] and bool(managed) and bool(machines)
            healthy = healthy and all(str(record.get('operatingSystem','')).lower()=='windows' and
                protection[str(record.get('id'))] and all(reported_boolean(health.get('realTimeProtectionEnabled')) is True and
                    reported_boolean(health.get('signatureUpdateOverdue')) is False and recent(health.get('lastReportedDateTime'))
                    for health in protection[str(record.get('id'))]) for record in managed)
            healthy = healthy and all(antivirus[str(record.get('id'))] and
                all(reported_boolean(health.get('avIsSignatureUpToDate')) is True and recent(health.get('dataRefreshTimestamp'))
                    for health in antivirus[str(record.get('id'))]) for record in machines)
        result['ENDPOINT.POSTURE'] = {'result':'pass' if healthy else 'unknown',
            'reason':'Active reporting, compliance, real-time protection and antivirus currency are assessed separately. Unverified platforms or missing protection fields require an operational review.', 'records':devices['records']}
    audit = rows(bundle,'copilot_audit')
    usable_audit = [row for row in audit if row.get('id') and
        (observed := parse_date(row.get('createdDateTime'))) and 0 <= (day-observed).days <= 7 and
        (str(row.get('operation','')).lower()=='copilotinteraction' or
         str(row.get('recordType','')).lower()=='copilotinteraction')]
    if audit and source_current(bundle,'copilot_audit',day):
        usable = len(usable_audit)==len(audit)
        result['DATA.AUDIT'] = {'result':'pass' if usable else 'unknown',
            'reason':'Identified, dated Copilot interaction metadata was returned by the bounded audit query. Retention and review procedures require separate assessment.' if usable else
                     'Returned audit rows lack usable identifiers, dates or Copilot event classification, or fall outside the seven-day observation window.',
            'records':audit}
    for control, check in OPERATION_CHECKS.items():
        review = reviews.get(check)
        if review:
            # A configuration-only review never supplies tested behavior.
            operational = all(row.get('evidence_level')=='observed_operation' and
                str(row.get('tested_behavior','')).strip() and str(row.get('tested_scope','')).strip()
                for row in review['records'])
            if operational and review['result'] in {'pass','fail','conflict'}:
                prior = result[control]['result']
                if control != 'IDENTITY.AUTH':
                    resolved = 'conflict' if prior == 'pass' and review['result'] == 'fail' else review['result']
                    result[control] = {'result': resolved,
                        'reason': 'Dated tenant-wide review of observed behavior; source records and review are retained.',
                        'records': review['records']}
                    continue
                resolved = ('conflict' if prior == 'conflict' or prior in {'pass', 'fail'} and review['result'] in {'pass', 'fail'} and prior != review['result']
                            else review['result'])
                # Missing/materially incomplete sign-in windows cannot be repaired
                # by choosing a favorable review without resolving that coverage.
                if datasets and not source_current(bundle, 'signin_logs', day) and resolved == 'pass':
                    resolved = 'unknown'
                previous = result[control]
                result[control] = {**previous, 'result':resolved,
                    'reason': previous['reason'] + ' Dated tenant-wide review of observed behavior; source records and review are retained.',
                    'records': previous['records'] + review['records']}
                if resolved == 'fail' and prior not in {'fail', 'conflict'}:
                    result[control].pop('event_derived', None)
    return result, devices

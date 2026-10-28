"""Pure interpretation of retained endpoint evidence; never collect or join by name.

Counts have explicit units and describe the selected rows, not fleet coverage.
Native rows and their EVD IDs are never rewritten by this projection.
"""

from collections import defaultdict
import re

from .evidence_contract import parse_date


CA_QUALIFICATION = ('The collected Conditional Access evidence does not establish that access to '
                    'Microsoft 365 is consistently restricted to devices marked compliant by Intune.')
ZERO_ID = '00000000-0000-0000-0000-000000000000'
INVENTORIES = ('managed_devices', 'directory_devices', 'machines')


def value(record, *names):
    keys = {re.sub('[^a-z0-9]', '', str(key).lower()): item for key, item in record.items()}
    return next((keys[re.sub('[^a-z0-9]', '', name.lower())] for name in names
                 if keys.get(re.sub('[^a-z0-9]', '', name.lower())) not in (None, '')), None)


def stable(value_):
    text = str(value_ or '').strip().casefold()
    return '' if text in {'', ZERO_ID, 'unknown', 'not returned', 'none'} else text


def timestamp(raw, day):
    """ISO source dates only. Missing is distinct from present-but-invalid."""
    if raw in (None, ''):
        return {'state': 'missing', 'date': '', 'age_days': None}
    parsed = None if isinstance(raw, (bool, int, float)) else parse_date(raw)
    if not parsed or parsed.year < 1900 or day is None or parsed > day:
        return {'state': 'malformed', 'date': '', 'age_days': None}
    return {'state': 'valid', 'date': parsed.isoformat(), 'age_days': (day-parsed).days}


def _ids(source, raw):
    return {
        'intune_id': stable(value(raw, 'id') if source == 'managed_devices' else value(raw, 'managedDeviceId')),
        'entra_id': stable(value(raw, 'deviceId') if source == 'directory_devices' else
                          value(raw, 'azureADDeviceId', 'aadDeviceId', 'entraDeviceId')),
        'defender_id': stable(value(raw, 'id') if source == 'machines' else value(raw, 'machineId', 'defenderDeviceId')),
    }


def _pilot_scope(profile, day):
    from .control_reviews import _profile_raw
    # Optional correlation extends the existing dated pilot scope. A description
    # or tenant-wide device population is never a device roster.
    scope = (_profile_raw(profile or {}).get('readiness_review') or {}).get('pilot_scope') or {}
    reviewed = timestamp(scope.get('reviewed_at'), day)
    valid = (reviewed['state'] == 'valid' and reviewed['age_days'] <= 35
             and all(scope.get(key) for key in ('id', 'description', 'reviewer_role', 'evidence_reference')))
    keys = ('intune_device_ids', 'entra_device_ids', 'defender_device_ids', 'user_ids')
    valid = valid and any(scope.get(key) for key in keys) and all(
        isinstance(scope.get(key, []), list) and all(isinstance(item, str) and stable(item) for item in scope.get(key, []))
        for key in keys) and type(scope.get('membership_complete', False)) is bool
    return {'valid': bool(valid), 'complete': valid and scope.get('membership_complete') is True,
            **{key: {stable(item) for item in scope.get(key, [])} if valid else set() for key in keys}}


def pilot_relevance(identity, scope):
    if not scope['valid'] or identity.get('unresolved'):
        return 'PilotRelevanceUnknown'
    for key, field in (('intune_device_ids', 'intune_id'), ('entra_device_ids', 'entra_id'),
                       ('defender_device_ids', 'defender_id'), ('user_ids', 'primary_user_id')):
        if stable(identity.get(field)) in scope[key]:
            return 'PilotRelevant'
    # An incomplete roster cannot prove that an unmatched device is outside it.
    return 'OutsideKnownPilotScope' if scope['complete'] else 'PilotRelevanceUnknown'


def build_device_identities(bundle, *, profile=None, evaluation_date=None):
    """Stable IDs are namespaced; contradictory stable IDs prevent any merge."""
    from .evidence_selection import evidence_record_ids
    day = parse_date(evaluation_date or bundle.get('evaluation_date') or
                     (bundle.get('collection_context') or {}).get('collected_at'))
    from .control_reviews import _profile_raw
    from .assessment_catalog import context_validation
    context = _profile_raw(profile or {}).get('assessment_context') or {}
    if context_validation(profile or {}):
        context = {}
    active_days, reporting_days = context.get('device_activity_days', 30), context.get('endpoint_reporting_days', 7)
    sources = bundle.get('assessment_sources') or {}
    tenant = bundle.get('expected_tenant_id') or (bundle.get('collection_context') or {}).get('tenant_id')
    # Use the very same registry as the raw workbook, including source metadata.
    evidence_ids = evidence_record_ids({key: sources.get(key, []) for key in (*INVENTORIES,
        'compliance_setting_states', 'windows_protection', 'antivirus_health', 'ca_policies')}, tenant)
    entries, lookup = [], defaultdict(list)
    for source in INVENTORIES:
        for di, dataset in enumerate(sources.get(source, [])):
            for ri, raw in enumerate(dataset.get('records') or []):
                ids = _ids(source, raw)
                date_field = {'managed_devices': 'lastSyncDateTime', 'directory_devices': 'approximateLastSignInDateTime', 'machines': 'lastSeen'}[source]
                entry = {**ids, 'key': f'{source}:{di}:{ri}', 'source': source, 'record': raw,
                         'source_metadata': dataset.get('source') or {}, 'evidence_id': evidence_ids[(source, di, ri)],
                         'timestamp': timestamp(value(raw, date_field), day)}
                entries.append(entry)
                for field, identifier in ids.items():
                    if identifier:
                        lookup[(field, identifier)].append(entry)
    # A connected component is mergeable only if every ID namespace has at most
    # one value. Transitive contradictions cannot be resolved by first/last wins.
    remaining = {entry['key']: entry for entry in entries}
    groups = []
    while remaining:
        _, first = remaining.popitem()
        component, pending = {first['key']: first}, [first]
        while pending:
            entry = pending.pop()
            for field in ('intune_id', 'entra_id', 'defender_id'):
                for other in lookup.get((field, entry[field]), []) if entry[field] else []:
                    if other['key'] not in component:
                        component[other['key']] = other
                        remaining.pop(other['key'], None)
                        pending.append(other)
        component = list(component.values())
        conflict = any(len({entry[field] for entry in component if entry[field]}) > 1
                       for field in ('intune_id', 'entra_id', 'defender_id'))
        if conflict:
            groups.extend(([entry], True) for entry in component)
        else:
            groups.append((component, False))
    scope = _pilot_scope(profile, day)
    identities = []
    for members, conflict in groups:
        identity = {field: next((entry[field] for entry in members if entry[field]), '')
                    for field in ('intune_id', 'entra_id', 'defender_id')}
        managed = [entry['record'] for entry in members if entry['source'] == 'managed_devices']
        machines = [entry['record'] for entry in members if entry['source'] == 'machines']
        representative = (managed or machines or [members[0]['record']])[0]
        duplicate = len(members) > len({entry['source'] for entry in members}) or any(
            value(raw, 'mergedIntoMachineId') or value(raw, 'isPotentialDuplication') is True for raw in machines)
        state_conflict = any(len({str(value(entry['record'], field)) for entry in members
                                 if entry['source'] == source and value(entry['record'], field) is not None}) > 1
            for source, field in (('managed_devices', 'complianceState'), ('machines', 'onboardingStatus'), ('directory_devices', 'accountEnabled')))
        unresolved = conflict or not any(identity.values()) or state_conflict
        dates = [entry['timestamp']['date'] for entry in members if entry['timestamp']['state'] == 'valid']
        malformed = any(entry['timestamp']['state'] == 'malformed' for entry in members)
        missing = any(entry['timestamp']['state'] == 'missing' for entry in members)
        retired = any(str(value(raw, 'managementState', 'lifecycleState') or '').casefold() in
                      {'retired', 'decommissioned', 'wiped', 'deleted'} or
                      timestamp(value(raw, 'retiredDateTime', 'wipeCompletedDateTime'), day)['state'] == 'valid'
                      for raw in managed)
        relevance = ('RetiredOrDecommissioned' if retired else 'DuplicateOrSupersededRecord' if duplicate else
                     'MalformedTimestamp' if malformed else 'CurrentActiveDevice' if dates and day and
                     (day-parse_date(max(dates))).days <= active_days else
                     'ActivityUnknown' if missing or not dates else 'StaleDeviceRecord')
        identity.update(identity_key=members[0]['key'], sources=members, unresolved=bool(unresolved),
            conflict=bool(conflict or state_conflict), relevance=relevance,
            serial_number=value(representative, 'serialNumber'), protected_name=value(representative, 'deviceName', 'computerDnsName', 'displayName'),
            primary_user_id=value(representative, 'userId'), primary_user_reference=value(representative, 'userPrincipalName', 'emailAddress'),
            management_state=value(representative, 'managementState'), platform=value(representative, 'operatingSystem', 'osPlatform'),
            enrollment_type=value(representative, 'deviceEnrollmentType'), ownership=value(representative, 'managedDeviceOwnerType'),
            last_intune_sync=value(managed[0], 'lastSyncDateTime') if managed else None,
            last_defender_seen=value(machines[0], 'lastSeen') if machines else None,
            retire_wipe_state=[value(raw, 'deviceActionResults') or value(raw, 'managementState') for raw in managed],
            last_observed=max(dates) if dates and not malformed else '',
            onboarding_state=value(machines[0], 'onboardingStatus') if machines else None)
        identity['pilot_relevance'] = pilot_relevance(identity, scope)
        identities.append(identity)
    return {'identities': sorted(identities, key=lambda row: row['identity_key']),
            'setting_summary_ids': [stable(value(raw, 'id')) for dataset in sources.get('compliance_setting_summaries', []) for raw in dataset.get('records', [])],
            'evaluation_date': day.isoformat() if day else '', 'active_days': active_days,
            'reporting_days': reporting_days,
            'evidence_ids': {f'{name}:{di}:{ri}': identifier for (name, di, ri), identifier in evidence_ids.items()},
            'pilot_scope_established': scope['valid']}


def resolve_identity(model, source, raw, source_api=''):
    if source in INVENTORIES:
        matches = [identity for identity in model['identities'] if any(entry['source'] == source and entry['record'] == raw for entry in identity['sources'])]
    else:
        ids = _ids(source, raw)
        if source == 'compliance_setting_states':
            explicit = {stable(value(raw, key)) for key in ('managedDeviceId','deviceId') if stable(value(raw, key))}
            if len(explicit) > 1:
                return None
            ids['intune_id'] = stable(value(raw, 'managedDeviceId', 'deviceId'))
            if not ids['intune_id'] and stable(value(raw, 'ParentId')) not in model.get('setting_summary_ids', []) and 'devicecompliancepolicysettingstatesummaries' not in str(source_api).lower():
                ids['intune_id'] = stable(value(raw, 'ParentId'))
        elif source == 'windows_protection':
            ids['intune_id'] = stable(value(raw, 'managedDeviceId', 'ParentId'))
        supplied = [(key, identifier) for key, identifier in ids.items() if identifier]
        matches = [identity for identity in model['identities'] if supplied and
                   all(identity[field] == identifier for field, identifier in supplied)]
    return matches[0] if len(matches) == 1 and not matches[0]['unresolved'] else None


def record_diagnostics(raw, source, evidence_id, day):
    diagnostics = []
    for key, raw_value in raw.items():
        normalized = re.sub('[^a-z0-9]', '', str(key).casefold())
        if normalized.endswith(('datetime', 'timestamp')) or normalized in {'lastseen', 'firstseen'}:
            if timestamp(raw_value, day)['state'] == 'malformed':
                diagnostics.append({'code': 'MalformedTimestamp', 'field': key, 'raw_value': raw_value,
                                    'source': source, 'evidence_id': evidence_id})
        if normalized.endswith('version') and str(raw_value).strip() == '0.0.0.0':
            diagnostics.append({'code': 'InvalidMeaningfulVersion', 'field': key, 'raw_value': raw_value,
                                'source': source, 'evidence_id': evidence_id})
    return diagnostics


def interpret_records(model, source, datasets):
    """One projection per native row, preserving native row order and references."""
    day = parse_date(model['evaluation_date'])
    output = []
    for di, dataset in enumerate(datasets):
        for ri, raw in enumerate(dataset.get('records') or []):
            identity = resolve_identity(model, source, raw, (dataset.get('source') or {}).get('source_api', ''))
            ids = _ids(source, raw)
            if source == 'compliance_setting_states':
                ids['intune_id'] = stable(value(raw, 'managedDeviceId', 'deviceId'))
            if source == 'windows_protection':
                ids['intune_id'] = stable(value(raw, 'managedDeviceId', 'ParentId'))
            evidence_id = model['evidence_ids'].get(f'{source}:{di}:{ri}', '')
            observed = (value(raw, 'lastReportedDateTime') if source in {'windows_protection', 'compliance_setting_states'} else
                        value(raw, 'dataRefreshTimestamp') if source == 'antivirus_health' else
                        value(raw, 'lastSyncDateTime') if source == 'managed_devices' else value(raw, 'lastSeen'))
            date_basis = 'source report date'
            if observed in (None, '') and source == 'compliance_setting_states' and identity:
                observed, date_basis = identity['last_intune_sync'], 'managed device last sync; setting report date not retained'
            date = timestamp(observed, day)
            diagnostics = record_diagnostics(raw, source, evidence_id, day)
            if date['state'] == 'malformed' and not any(item['raw_value'] == observed for item in diagnostics):
                diagnostics.append({'code': 'MalformedTimestamp', 'field': date_basis, 'raw_value': observed,
                                    'source': source, 'evidence_id': evidence_id})
            output.append({'record': raw, 'source': source, 'dataset_index': di, 'record_index': ri,
                'evidence_id': evidence_id, 'identity': identity,
                'ids': {key: (identity or {}).get(key) or identifier for key, identifier in ids.items()},
                'unresolved': identity is None,
                'relevance': (identity or {}).get('relevance', 'ActivityUnknown'),
                'pilot_relevance': (identity or {}).get('pilot_relevance', 'PilotRelevanceUnknown'),
                'timestamp': date, 'observed_raw': observed, 'date_basis': date_basis,
                'diagnostics': diagnostics, 'source_metadata': dataset.get('source') or {}})
    return output


def population(items):
    """Unique counts are reported per namespace; unresolved counts are rows."""
    keys = {key: {item['ids'].get(key) for item in items if item['ids'].get(key)}
            for key in ('intune_id', 'entra_id', 'defender_id')}
    def count(predicate):
        return len({item['identity']['identity_key'] for item in items if item.get('identity') and predicate(item)})
    dates = sorted(item['timestamp']['date'] for item in items if item['timestamp']['state'] == 'valid')
    return {'source_record_count': len(items), 'distinct_intune_devices': len(keys['intune_id']),
            'distinct_entra_devices': len(keys['entra_id']), 'distinct_defender_devices': len(keys['defender_id']),
            'resolved_device_count': count(lambda item: True), 'unresolved_identity_count': sum(item['unresolved'] for item in items),
            'active_device_count': count(lambda item: item['relevance'] == 'CurrentActiveDevice'),
            'stale_device_count': count(lambda item: item['relevance'] == 'StaleDeviceRecord'),
            'pilot_relevant_count': count(lambda item: item['pilot_relevance'] == 'PilotRelevant'),
            'pilot_relevance_unknown_count': count(lambda item: item['pilot_relevance'] == 'PilotRelevanceUnknown'),
            'pilot_relevance_unknown_records': sum(item['pilot_relevance'] == 'PilotRelevanceUnknown' for item in items),
            'valid_timestamp_count': sum(item['timestamp']['state'] == 'valid' for item in items),
            'malformed_timestamp_count': sum(item['timestamp']['state'] == 'malformed' for item in items),
            'missing_timestamp_count': sum(item['timestamp']['state'] == 'missing' for item in items),
            'evidence_period': {'start': dates[0] if dates else '', 'end': dates[-1] if dates else ''},
            'qualification': 'Selected source rows; distinct IDs are separate namespaces. Findings may overlap; do not add their counts. Unresolved identity counts are records.'}


def count_text(counts):
    return (f"{counts['source_record_count']} source record(s); {counts['distinct_intune_devices']} distinct managed device IDs; "
            f"{counts['distinct_entra_devices']} distinct Entra and {counts['distinct_defender_devices']} distinct Defender IDs. "
            f"{counts['resolved_device_count']} resolved device identities; {counts['unresolved_identity_count']} record(s) have unresolved device identity.")


def defender_context(item):
    raw, identity = item['record'], item.get('identity') or {}
    return {'real_time_protection':value(raw, 'realTimeProtectionEnabled'),
        'tamper_protection':value(raw, 'tamperProtectionEnabled', 'isTamperProtected'),
        'signature_overdue':value(raw, 'signatureUpdateOverdue'),
        'signature_current':value(raw, 'avIsSignatureUpToDate'),
        'defender_mode':value(raw, 'avMode', 'defenderAvMode', 'antivirusMode'),
        'onboarding_state':value(raw, 'onboardingStatus') or identity.get('onboarding_state'),
        'approved_third_party_av':value(raw, 'approvedThirdPartyAV', 'thirdPartyAntivirusApproved'),
        'third_party_product':value(raw, 'thirdPartyAntivirus', 'antivirusProvider'),
        'platform':value(raw, 'operatingSystem', 'osPlatform') or identity.get('platform'),
        'source_complete':item['source_metadata'].get('complete') is True,
        'source_dataset':item['source'], 'last_reported_raw':item['observed_raw'],
        'qualification':'An off observation alone does not establish current protection effectiveness. Validate mode, onboarding, approved provider, platform, dates, source completeness and pilot relevance.'}


def annotate_finding(row, items, kind, *, confirmed=False):
    original_observation = row.get('DeviceEvidenceObservation', row.get('Observation', ''))
    counts = population(items)
    confirmed_pilot = any(confirmed and not item['unresolved'] and item['relevance'] == 'CurrentActiveDevice'
        and item['pilot_relevance'] == 'PilotRelevant' and item['timestamp']['state'] == 'valid' for item in items)
    relevant = {item['relevance'] for item in items}
    pilot = {item['pilot_relevance'] for item in items}
    impact = ('Blocks pilot' if confirmed_pilot else
              'Inventory hygiene' if relevant and relevant <= {'StaleDeviceRecord', 'RetiredOrDecommissioned', 'DuplicateOrSupersededRecord'} else
              'Evidence gap' if not items or all(item['unresolved'] for item in items) else
              'Tenant hardening' if confirmed and pilot == {'OutsideKnownPilotScope'} and relevant == {'CurrentActiveDevice'} else
              'Confirm before pilot')
    if kind == 'PilotDevicePostureNotEstablished':
        impact = 'Confirm before pilot'
    row.update(DeviceEvidenceVersion='1.0.0', DeviceFindingType=kind, DeviceEvidenceCounts=counts,
        DeviceEvidenceObservation=original_observation,
        DevicePilotImpact=impact, DevicePilotBlockerEligible=confirmed_pilot,
        DeviceEvidencePeriod=counts['evidence_period'],
        DeviceEvidenceDiagnostics=[diagnostic for item in items for diagnostic in item['diagnostics']],
        DeviceEvidenceIds=[item['evidence_id'] for item in items],
        DeviceEvidenceInterpretations=[{'evidence_id':item['evidence_id'], **item['ids'],
            'record_relevance':item['relevance'], 'pilot_relevance':item['pilot_relevance'],
            'identity_unresolved':item['unresolved'], 'timestamp':item['timestamp'], 'date_basis':item['date_basis'],
            **({'defender':defender_context(item)} if item['source'] in {'windows_protection','antivirus_health'} else {})} for item in items],
        DeviceEvidenceQualification=('Current protection and intended pilot relevance require separate evidence. '
                                     'Inventory compliance is not proof that every setting passed or that access occurred.'),
        RecordUnit='source records', CompletionEvidence=row.get('CompletionEvidence') or
        'Dated per-device/per-setting results, reconciled stable IDs and approved pilot membership; validate protection and effective access separately.')
    row['Observation'] = str(original_observation or '') + ' Evidence period: ' + (
        counts['evidence_period']['start']+' to '+counts['evidence_period']['end'] if counts['evidence_period']['start'] else 'not established') + '. '
    row['Observation'] += (f"Active devices: {counts['active_device_count']}; stale devices: {counts['stale_device_count']}; "
        f"pilot-relevant devices: {counts['pilot_relevant_count']}; pilot relevance unknown: {counts['pilot_relevance_unknown_count']} devices "
        f"and {counts['pilot_relevance_unknown_records']} records. Valid/malformed/missing timestamps: "
        f"{counts['valid_timestamp_count']}/{counts['malformed_timestamp_count']}/{counts['missing_timestamp_count']}. "
        f'Pilot treatment: {impact}.')
    return row


def pilot_blocker_eligible(row):
    if row.get('DeviceEvidenceVersion'):
        return row.get('DevicePilotBlockerEligible') is True
    return row.get('ControlId') != 'ENDPOINT.POSTURE'

"""Readable device populations from frozen Intune evidence, never tenant defaults."""
import re
from collections import defaultdict

from .device_evidence import timestamp, value
from .evidence_contract import parse_date
from .device_compliance_text import requirement_result, group_settings, group_state


def _readable(lines):
    singular = [re.sub(r'\b1 ((?:(?:other|additional|distinct|retained|noncompliant|active|inactive|overdue|retired) )*)devices\b',
                       lambda match: '1 ' + match[1] + 'device', line) for line in lines]
    return [re.sub(r'\b(1 (?:[a-z]+ )*device) (are|have|report)\b',
                   lambda match: match[1] + ' ' + {'are': 'is', 'have': 'has', 'report': 'reports'}[match[2]], line)
            for line in singular]


def setting_reason(label):
    return requirement_result(label, 'noncompliant')


def compliance_activity(device, model):
    """Compliance reporting is independent of Entra sign-in/Defender activity."""
    if device.get('conflict'):
        return 'unknown'
    if device.get('relevance') == 'RetiredOrDecommissioned':
        return 'retired'
    checks = [item for item in device.get('settings', []) if item['setting'] == 'Is active']
    if any(group_state(group) == 'conflictingResults' for group in group_settings(checks)):
        return 'unknown'
    if any(item['failed'] for item in checks):
        return 'inactive'
    day = parse_date(model.get('evaluation_date'))
    validity = model['tenant_settings']['fields']['deviceComplianceCheckinThresholdDays']['value']
    sync = timestamp(device.get('last_sync'), day)
    # An explicit recent passing Is active check can establish activity even
    # when this older collection did not retain the configured validity period.
    if any(str(value(item['record'], 'state') or '').casefold() == 'compliant'
           and timestamp(item['last_reported'], day)['state'] == 'valid'
           and timestamp(item['last_reported'], day)['age_days'] <= model['reporting_days'] for item in checks):
        return 'active'
    if validity is not None and sync['state'] == 'valid':
        return 'overdue' if sync['age_days'] > validity else 'active'
    return 'unknown'


def _devices(model):
    grouped = defaultdict(list)
    for device in model.get('devices', []):
        identifier = str(device.get('device_id') or '').strip().casefold()
        if identifier:
            grouped[identifier].append(device)
    output = {}
    for identifier, records in grouped.items():
        activities = {record.get('compliance_activity', 'unknown') for record in records}
        states = {str(value(record['device'], 'complianceState') or '').casefold() for record in records}
        output[identifier] = {'activity': next(iter(activities)) if len(activities) == 1 and len(states) == 1 else 'unknown',
            'noncompliant': 'noncompliant' in states, 'records': records}
    return output


def _policy_groups(settings, devices):
    groups = {}
    for item in settings:
        identifier = str(item.get('device_id') or '').casefold()
        device = devices.get(identifier)
        if (not device or device['activity'] != 'active' or not item['failed']
                or item['kind'] != 'security' or item['triage'] != 'current_security_failure'):
            continue
        policy = item['policy'] if item['policy'] != 'not retained' else 'Policy name not retained'
        group = groups.setdefault(policy, {'devices': set(), 'reasons': defaultdict(set)})
        group['devices'].add(identifier)
        group['reasons'][setting_reason(item['setting'])].add(identifier)
    return [{'policy': policy, 'devices': len(group['devices']),
             'reasons': [{'reason': reason, 'devices': len(ids)} for reason, ids in sorted(group['reasons'].items())]}
            for policy, group in sorted(groups.items())]


def compliance_overview(model):
    devices = _devices(model)
    reported = {identifier: device for identifier, device in devices.items() if device['noncompliant']}
    counts = {state: sum(device['activity'] == state for device in reported.values())
              for state in ('active', 'inactive', 'overdue', 'unknown', 'retired')}
    fields = model['tenant_settings']['fields']
    days = fields['deviceComplianceCheckinThresholdDays']['value']
    marking = fields['secureByDefault']
    lines = [f"{counts['active']} noncompliant devices are active; inactive and overdue devices are excluded from this count."]
    if not model.get('devices'):
        lines = ['No Intune managed-device records were retained; device compliance could not be assessed.']
    elif not reported and not any(not device.get('device_id') for device in model['devices']):
        lines = [f'No retained managed devices are reported noncompliant ({len(devices)} distinct device IDs assessed).']
    if counts['inactive']:
        lines.append(f"{counts['inactive']} noncompliant devices may be inactive/not recently checked in (failed contact requirement).")
    if counts['overdue']:
        lines.append(f"{counts['overdue']} other noncompliant devices last checked in more than {days} days ago; confirm their compliance-reporting status.")
    if counts['unknown']:
        lines.append(f"Activity could not be verified for {counts['unknown']} noncompliant devices.")
    if counts['retired']:
        lines.append(f"{counts['retired']} noncompliant devices are recorded as retired; review inventory cleanup.")
    lines.append(f'Compliance status validity period: {days} days. Devices must successfully report compliance within this period.'
                 if days is not None else 'Compliance status validity period was not collected or has conflicting values; no default day count is assumed.')
    if marking['state'] == 'observed':
        lines.append('Devices without an assigned compliance policy are marked ' + marking['display'].lower() + '.')
        if marking['value'] is False:
            lines.append('Review this setting and change it to Not compliant when enrollment and policy targeting are ready, or as required by policy.')
    else:
        lines.append('The setting for devices without an assigned compliance policy could not be verified.')
    missing = sum(any(record.get('policy_assignment') == 'Reported missing' for record in device['records']) for device in devices.values())
    if missing:
        lines.append(f'{missing} devices are reported without an applicable compliance policy; confirm their assignments.')
    unresolved = sum(not device.get('device_id') and str(value(device['device'], 'complianceState') or '').casefold() == 'noncompliant'
                     for device in model.get('devices', []))
    if unresolved:
        lines.append(f'{unresolved} noncompliant inventory records lack a stable device ID and are outside these device counts.')
        if not reported:
            lines[0] = 'The noncompliant device count could not be verified because stable device IDs are missing.'
    if model.get('inventory') and any(item['source_metadata'].get('complete') is not True for item in model['inventory']):
        lines.append('Counts cover retained Intune records; inventory completeness is not established.')
    return {'counts': counts, 'total_noncompliant': len(reported), 'validity_days': days,
            'missing_policy_devices': missing, 'unresolved_records': unresolved, 'lines': _readable(lines),
            'policy_groups': _policy_groups(model.get('settings', []), devices),
            'scope': 'Distinct retained Intune device IDs; counts do not establish tenant-wide completeness. Policy groups may overlap.'}


def finding_summary(row, model):
    kind = row.get('DeviceFindingType')
    if not kind:
        return None
    devices = _devices(model)
    selected = set(row.get('DeviceEvidenceIds') or [])
    settings = [item for item in model.get('settings', []) if item['evidence'].get('evidence_id') in selected]
    ids = {str(item.get('device_id')).casefold() for item in settings if item.get('device_id')}
    groups = _policy_groups(settings, devices)
    counts = row.get('DeviceEvidenceCounts') or {}
    count = counts.get('resolved_device_count', 0)
    lines = []
    if kind == 'InventoryComplianceState':
        lines = list((model.get('summary') or compliance_overview(model))['lines'])
        lines.append('Overall noncompliance is an inventory total, not an additional setting failure. Devices may also appear in the setting and reporting findings; do not add their counts.')
        reported = [device for device in model.get('devices', [])
                    if str(value(device['device'], 'complianceState') or '').casefold() == 'noncompliant']
        missing = {str(device['device_id']).casefold() for device in reported if device.get('device_id')
                   and not any(item['failed'] or item['assignment_missing'] for item in device.get('settings', []))}
        if missing:
            lines.append(f'{len(missing)} noncompliant devices have no retained failing setting that explains the inventory status. Confirm the reason in Intune.')
    elif kind == 'ExplicitSecuritySettingFailure':
        active = sum(devices.get(identifier, {}).get('activity') == 'active' for identifier in ids)
        lines = [f'{active} active devices have current compliance-policy failures.']
        unknown = len(ids) - active
        if not active and unknown:
            lines = [f'{unknown} devices have current reported compliance-policy failures; their activity needs confirmation.']
        if unknown:
            if active:
                lines.append(f'Activity could not be verified for {unknown} additional devices with failed settings.')
        for group in groups[:3]:
            reasons = '; '.join(item['reason'] for item in group['reasons'][:3])
            lines.append(f"{group['devices']} devices: {group['policy']}. {reasons}.")
        if not groups:
            reasons = list(dict.fromkeys(setting_reason(item['setting']) for item in settings if item['kind'] == 'security'))
            if reasons:
                lines.append('Reported failures: ' + '; '.join(reasons[:3]) + '.')
    elif kind == 'ActivityOrEnrollmentFailure' and str(row.get('FindingKey') or '').endswith('assignment_missing'):
        lines = [f'{count} devices are reported without an applicable compliance policy. Confirm assignment and check-in.']
    elif kind == 'ActivityOrEnrollmentFailure':
        inactive = {item['device_id'] for item in settings if item['setting'] == 'Is active' and item['failed'] and item['device_id']}
        lines = [f'{len(inactive)} devices may be inactive/not recently checked in (failed contact requirement).' if inactive else
                 f'{count} devices have activity or enrollment checks that need review.']
        days = (model.get('summary') or {}).get('validity_days')
        lines.append(f'Compliance status validity period: {days} days; this checks compliance reporting, not user sign-ins.'
                     if days is not None else 'The configured compliance validity period is unknown; confirm it in Intune.')
        reasons = list(dict.fromkeys(requirement_result(item['setting'], item['record'].get('state')) for item in settings if item['setting'] != 'Is active'))
        if reasons:
            lines.append('Other checks: ' + '; '.join(reasons[:3]) + '.')
    elif kind == 'PolicyAssignmentNotEstablished':
        lines = [f'{count} devices are reported without an applicable compliance policy. Confirm assignment and check-in.']
    elif kind == 'ReportingErrorOrConflict':
        from collections import Counter
        from .device_examples import status_name, error_detail
        check_groups = group_settings(settings)
        states = Counter(status_name(group_state(group)) for group in check_groups)
        lines = [f'{count} devices have unresolved compliance-setting results. These are reporting gaps, not confirmed security failures.']
        if states:
            lines.append('Distinct device/setting checks: ' + '; '.join(f'{state}: {total}' for state, total in sorted(states.items())) + '.')
        examples = []
        for group in check_groups:
            item = group[0]
            policy = item['policy'] if item['policy'] != 'not retained' else 'Policy name not retained'
            description = f"{policy}: {requirement_result(item['setting'], group_state(group))} — {status_name(group_state(group))}"
            if description not in examples:
                examples.append(description)
            if len(examples) == 3:
                break
        lines.extend(description + '.' for description in examples)
        if settings and not any(error_detail(item['record']) for item in settings):
            lines.append('No error code or diagnostic message was retained. The source status alone does not explain the cause; review the device setting in Intune.')
        elif settings:
            lines.append('Retained diagnostic codes and messages are shown in the technical examples and evidence workbook.')
    elif kind == 'UnconfirmedSettingState':
        lines = [f'{count} devices have older or unconfirmed compliance-setting failures.']
        inactive = sum(devices.get(identifier, {}).get('activity') in {'inactive', 'overdue', 'retired'} for identifier in ids)
        if inactive:
            lines.append(f'{inactive} are inactive, overdue or retired and are excluded from active-device security counts.')
        reasons = list(dict.fromkeys(setting_reason(item['setting']) for item in settings if item['kind'] == 'security'))
        if reasons:
            lines.append('Reported failures: ' + '; '.join(reasons[:3]) + '.')
    elif kind == 'DefenderRealTimeProtectionObservation':
        lines = [f'{count} devices report Defender real-time protection off.',
                 'Another antivirus may be the primary provider while Defender is passive or disabled. Confirm the active provider, Defender mode and report date before changing protection. This observation alone does not establish that the devices are unprotected.']
    elif kind == 'DefenderSignatureObservation':
        lines = [f'{count} devices have reported antivirus signature-update issues.',
                 'Check the endpoint report date and current device activity before treating the issue as current.']
    elif kind == 'PilotDevicePostureNotEstablished':
        lines = [f'Pilot-device protection needs confirmation for {count} retained device identities.',
                 'Confirm intended pilot membership and current protection. Tenant-wide inventory is not a pilot-device roster.']
    elif kind == 'CompliantDeviceAccessEnforcementNotEstablished':
        lines = [f"{counts.get('source_record_count', 0)} Conditional Access policies require review; compliant-device enforcement for the intended users is not established."]
    elif kind == 'UnverifiedLegacyDeviceObservation':
        lines = ['Earlier device findings could not be verified from native endpoint records.']
    else:
        return None
    if counts.get('unresolved_identity_count'):
        if not count and kind != 'CompliantDeviceAccessEnforcementNotEstablished':
            lines[0] = 'The affected device count could not be verified from the retained records.'
        lines.append(f"{counts['unresolved_identity_count']} source records could not be linked unambiguously to a device.")
    if row.get('EvidenceComplete') is False:
        lines.append('Counts cover retained records; collection completeness is not established.')
    lines.append('For specific devices, review Device Detail in the technical evidence workbook.')
    action = ('Confirm user/device targeting and exclusions. Assign the required policy and verify the next compliance report.'
              if str(row.get('FindingKey') or '').endswith('assignment_missing') else
              'Review the named policy failures in Device Detail. Remediate the failed protections and verify a new compliant report.'
              if kind == 'ExplicitSecuritySettingFailure' else
              'Confirm ownership and compliance reporting. Reconnect, re-enroll or retire devices as appropriate.'
              if kind == 'ActivityOrEnrollmentFailure' else
              'Confirm current activity and setting results before remediation. Review inactive devices for inventory cleanup.'
              if kind == 'UnconfirmedSettingState' else
              'Review reporting errors and conflicting assignments in Intune, then verify fresh setting results.'
              if kind == 'ReportingErrorOrConflict' else
              'Validate the active antivirus provider, operating mode and current report date before changing protection.'
              if kind == 'DefenderRealTimeProtectionObservation' else
              'Confirm current device activity, update antivirus signatures and verify the next endpoint report.'
              if kind == 'DefenderSignatureObservation' else
              'Review the device groups and policy assignments in the technical workbook.')
    lines = _readable(lines)
    display = (str(counts.get('source_record_count', 0)) + ' policies' if kind == 'CompliantDeviceAccessEnforcementNotEstablished' else
               str(count) + (' device' if count == 1 else ' devices'))
    if kind != 'CompliantDeviceAccessEnforcementNotEstablished' and counts.get('unresolved_identity_count'):
        display = ((str(count) + ' identified device(s); ' if count else 'Device count unverified; ')
                   + str(counts['unresolved_identity_count']) + ' unresolved source record(s)')
    return {'lines': lines, 'text': ' '.join(lines), 'policy_groups': groups, 'action': action, 'display_count': display}

"""Result-aware compliance wording and grouped views; native rows stay intact."""
import re


def status_key(state):
    return re.sub('[^a-z]', '', str(state or 'unknown').casefold())


def setting_key(label):
    key = re.sub('[^a-z0-9]', '', str(label or '').rsplit('.', 1)[-1].casefold())
    if key in {'isactive', 'requireremaincontact'}:
        return 'requireremaincontact'
    if key in {'enrolleduserexists', 'requireuserexistence'}:
        return 'requireuserexistence'
    if key in {'hasacompliancepolicyassigned', 'requirecompliancepolicyassigned', 'requiredevicecompliancepolicyassigned'}:
        return 'requiredevicecompliancepolicyassigned'
    return key


def requirement_name(label):
    builtin = {'requireremaincontact': 'Device activity/check-in requirement',
               'requireuserexistence': 'Enrolled-user verification requirement',
               'requiredevicecompliancepolicyassigned': 'Compliance policy assignment check'}
    key = setting_key(label)
    if key in builtin:
        return builtin[key]
    for marker, name in [('bitlocker', 'BitLocker encryption requirement'),
                         ('secureboot', 'Secure Boot requirement'),
                         ('firewall', 'Firewall protection requirement'),
                         ('antivirussignature', 'Antivirus signature currency requirement'),
                         ('antivirus', 'Antivirus protection requirement'),
                         ('antispyware', 'Antispyware protection requirement')]:
        if marker in key:
            return name
    short = str(label or 'Setting name not retained').rsplit('.', 1)[-1]
    return re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', short).replace('_', ' ').replace('Bit Locker', 'BitLocker')


def requirement_result(label, state):
    key, status = setting_key(label), status_key(state)
    if status == 'noncompliant':
        failed = {'requireremaincontact': 'Device may be inactive/not recently checked in',
                  'requireuserexistence': 'Enrolled user could not be confirmed',
                  'requiredevicecompliancepolicyassigned': 'No applicable compliance policy reported'}
        if key in failed:
            return failed[key]
        for marker, text in (
            ('bitlocker', 'BitLocker encryption requirement failed'),
            ('secureboot', 'Secure Boot requirement failed'),
            ('firewall', 'Firewall protection does not meet policy'),
            ('antivirussignature', 'Antivirus signatures are out of date'),
            ('antivirus', 'Antivirus protection does not meet policy'),
            ('realtimeprotection', 'Real-time protection does not meet policy'),
        ):
            if marker in key:
                return text
        return requirement_name(label) + ' requirement not met'
    name = requirement_name(label)
    if status in {'error', 'conflict', 'unknown', 'notevaluated', 'conflictingresults'}:
        return name + ': result unresolved'
    return name


def group_settings(items):
    """Group by stable device ID and canonical setting, retaining all variants.

    Unresolved identities remain separate occurrences. Different statuses/policies
    are retained in a group rather than selecting a convenient winning record.
    """
    groups = {}
    for index, item in enumerate(items):
        device = str(item.get('device_id') or '').strip().casefold()
        setting = setting_key(item['setting'])
        key = (device, setting) if device and setting not in {'', 'settingnamenotretained'} else ('unresolved', index)
        groups.setdefault(key, []).append(item)
    return list(groups.values())


def group_state(group):
    states = {status_key(item['record'].get('state')) for item in group}
    return next(iter(states)) if len(states) == 1 else 'conflictingResults'

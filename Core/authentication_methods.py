"""Aggregate registration and second-factor preferences without exposing identities.

Registration describes available credentials, not successful sign-ins or enforced
Conditional Access strengths. This module evaluates saved and live Graph rows alike.
"""

from collections import Counter
from datetime import datetime
import re


METHODS = {}


def _register(values, label, group, note):
    for value in values.split():
        METHODS[value.casefold()] = (label, group, note)


_register('fido2 fido fido2SecurityKey', 'FIDO2 security key', 'resistant', 'Phishing-resistant')
_register('passKeyDeviceBound', 'Device-bound passkey', 'resistant', 'Phishing-resistant')
_register('passKeyDeviceBoundAuthenticator', 'Passkey in Microsoft Authenticator', 'resistant', 'Phishing-resistant')
_register('passKeySynced', 'Synced passkey', 'resistant', 'Phishing-resistant')
_register('windowsHello windowsHelloForBusiness passKeyDeviceBoundWindowsHello', 'Windows Hello for Business', 'resistant', 'Phishing-resistant')
_register('macOsSecureEnclaveKey', 'macOS platform credential', 'resistant', 'Phishing-resistant')
_register('microsoftAuthenticatorPush appNotification', 'Microsoft Authenticator push', 'app', 'App-based MFA; susceptible to phishing')
_register('microsoftAuthenticatorPasswordless', 'Authenticator passwordless phone sign-in', 'app', 'Passwordless; susceptible to phishing')
_register('softwareOneTimePasscode softwareOath appCode', 'Software OATH code', 'app', 'App-based MFA; susceptible to phishing')
_register('hardwareOneTimePasscode hardwareOath', 'Hardware OATH code', 'app', 'Token-based MFA; susceptible to phishing')
_register('mobilePhone phone', 'Mobile phone (SMS or voice)', 'phone', 'Lower-assurance phone method; registration does not distinguish SMS from voice')
_register('alternateMobilePhone alternateMobileCall', 'Alternate mobile phone', 'phone', 'Lower-assurance voice method')
_register('officePhone', 'Office phone', 'phone', 'Lower-assurance voice method')
_register('mobileSMS sms', 'SMS', 'phone', 'Lower-assurance text-message method')
_register('mobileCall', 'Mobile voice call', 'phone', 'Lower-assurance voice method')
_register('email', 'Email', 'recovery', 'Password reset / guest sign-in; not workforce MFA')
_register('securityQuestion securityQuestions', 'Security questions', 'recovery', 'Password reset only; not MFA')
_register('temporaryAccessPass', 'Temporary Access Pass', 'bootstrap', 'Time-limited onboarding/recovery; not a long-term method')
_register('certificateBasedAuthentication', 'Certificate-based authentication', 'review', 'Verify multifactor certificate configuration before classifying MFA strength')
_register('microsoftAuthenticator', 'Authenticator (type unspecified)', 'review', 'Legacy value; push, passwordless and passkey modes cannot be distinguished')
_register('none', 'No method reported', 'none', 'No registered method reported')

PREFERENCES = {
    'push': ('Authenticator push', 'App-based; susceptible to phishing'),
    'oath': ('OATH verification code', 'Code-based; susceptible to phishing'),
    'sms': ('SMS', 'Lower-assurance phone method'),
    'voicemobile': ('Mobile voice call', 'Lower-assurance phone method'),
    'voicealternatemobile': ('Alternate mobile voice call', 'Lower-assurance phone method'),
    'voiceoffice': ('Office voice call', 'Lower-assurance phone method'),
    'voice': ('Voice call (type unspecified)', 'Lower-assurance phone method'),
    'none': ('None reported', 'No preferred second-factor method reported'),
}
PHONE_PREFERENCES = {'sms', 'voice', 'voicemobile', 'voicealternatemobile', 'voiceoffice'}
# The registration report also returns these older service spellings in saved
# v1.0 responses. Keep unrecognized future values visible instead of guessing.
PREFERENCE_ALIASES = {'phoneappnotification': 'push', 'phoneappotp': 'oath'}
FIELDS = ('isAdmin', 'userType', 'isMfaRegistered', 'isMfaCapable', 'isPasswordlessCapable',
          'methodsRegistered', 'isSystemPreferredAuthenticationMethodEnabled',
          'systemPreferredAuthenticationMethods', 'userPreferredMethodForSecondaryAuthentication',
          'lastUpdatedDateTime')


def _get(row, key):
    snake = re.sub(r'(?<!^)(?=[A-Z])', '_', key).lower()
    if isinstance(row, dict):
        value = row.get(key, row.get(snake))
    else:
        value = getattr(row, key, getattr(row, snake, None))
    return getattr(value, 'value', value)


def _list(value):
    if not isinstance(value, (list, tuple)) or any(not isinstance(v, str) or not v.strip() for v in value):
        return None
    return {v.strip().casefold() for v in value}


def _method(value):
    return METHODS.get(value, ('Unrecognized: ' + value, 'review', 'Unknown method; review required'))


def _preference(value):
    return PREFERENCES.get(value, ('Unrecognized: ' + value, 'Unknown preference; review required'))


def summarize_registrations(records, state=None):
    state = state or {}
    availability = state.get('availability_status') or ('available' if state.get('available') else 'unknown')
    if state.get('truncated'):
        availability = 'partial'
    if state.get('complete') is False:
        availability = 'partial'
    if availability == 'available' and state.get('available') is False:
        availability = 'partial' if records else 'unavailable'
    unique, conflicts, duplicates = {}, set(), 0
    for index, row in enumerate(records or []):
        key = _get(row, 'id') or _get(row, 'userPrincipalName') or ('anonymous', index)
        normalized = {field: _get(row, field) for field in FIELDS}
        if key in unique:
            if normalized != unique[key]:
                conflicts.add(key)
            else:
                duplicates += 1
        else:
            unique[key] = normalized
    # Conflicting snapshots stay in the denominator with unknown fields.
    for key in conflicts:
        unique[key] = {}
    records = list(unique.values())
    total = len(records)
    metrics = Counter()
    registered, chosen, system, current = Counter(), Counter(), Counter(), Counter()
    dates = []
    populations = {name: Counter() for name in ('Members', 'Guests', 'User type unknown', 'Administrators')}
    for row in records:
        methods = _list(row.get('methodsRegistered'))
        groups = {_method(value)[1] for value in methods or []}
        has_resistant = 'resistant' in groups
        resistant_known = methods is not None and (has_resistant or 'review' not in groups)
        phone_only_known = methods is not None and type(row.get('isMfaRegistered')) is bool and 'review' not in groups
        # Only identify phone-only MFA where the returned inventory is understood.
        phone_only = row.get('isMfaRegistered') is True and 'phone' in groups and not groups.intersection({'app', 'resistant', 'review'})
        if methods is not None:
            metrics['method_inventory_known'] += 1
            for label in {_method(value)[0] for value in methods}:
                registered[label] += 1
        metrics['phishing_resistant_registered'] += has_resistant
        metrics['resistant_inventory_known'] += resistant_known
        metrics['phone_only_inventory_known'] += phone_only_known
        metrics['phone_registered'] += 'phone' in groups
        metrics['phone_only_mfa_registered'] += phone_only
        metrics['email_registered'] += 'email' in (methods or set())
        metrics['methods_need_review'] += 'review' in groups
        metrics['passwordless_registered'] += has_resistant or 'microsoftauthenticatorpasswordless' in (methods or set())
        labels = {_method(value)[0] for value in methods or []}
        metrics['authenticator_registered'] += bool(labels & {'Microsoft Authenticator push', 'Authenticator passwordless phone sign-in'})
        metrics['authenticator_passwordless_registered'] += 'Authenticator passwordless phone sign-in' in labels
        metrics['fido_registered'] += bool(labels & {'FIDO2 security key', 'Device-bound passkey', 'Passkey in Microsoft Authenticator', 'Synced passkey'})
        metrics['windows_hello_registered'] += 'Windows Hello for Business' in labels
        metrics['software_oath_registered'] += 'Software OATH code' in labels
        metrics['tap_registered'] += 'Temporary Access Pass' in labels
        for key, field in (('mfa_registered', 'isMfaRegistered'), ('mfa_capable', 'isMfaCapable'),
                           ('passwordless_capable', 'isPasswordlessCapable'), ('admin', 'isAdmin')):
            if type(row.get(field)) is bool:
                metrics[key + '_known'] += 1
                metrics[key] += row[field]
        user_pref = row.get('userPreferredMethodForSecondaryAuthentication')
        user_pref = user_pref.strip().casefold() if isinstance(user_pref, str) and user_pref.strip() else None
        user_pref = PREFERENCE_ALIASES.get(user_pref, user_pref)
        sys_pref = _list(row.get('systemPreferredAuthenticationMethods'))
        if sys_pref is not None:
            sys_pref = {PREFERENCE_ALIASES.get(value, value) for value in sys_pref}
        mode = row.get('isSystemPreferredAuthenticationMethodEnabled')
        selected = None
        if user_pref is not None:
            chosen[user_pref] += 1
            metrics['user_preference_known'] += 1
        if mode is True:
            metrics['system_preferred_mode_known'] += 1
            metrics['system_preferred_enabled'] += 1
            if sys_pref is not None:
                selected = sys_pref or {'none'}
                system.update(selected)
                metrics['system_preference_known'] += 1
        elif mode is False:
            metrics['system_preferred_mode_known'] += 1
            metrics['system_preferred_disabled'] += 1
            selected = {user_pref} if user_pref is not None else None
        else:
            metrics['system_preferred_unknown'] += 1
        if selected is not None:
            current.update(selected)
            metrics['current_preference_known'] += 1
        phone_preferred = bool((selected or set()) & PHONE_PREFERENCES)
        phone_preference_known = selected is not None and (phone_preferred or not (selected - PREFERENCES.keys()))
        metrics['phone_preference_known'] += phone_preference_known
        metrics['phone_preferred'] += phone_preferred
        metrics['user_phone_preferred'] += user_pref in PHONE_PREFERENCES
        metrics['user_phone_preference_known'] += user_pref in PREFERENCES
        metrics['current_preference_needs_review'] += bool((selected or set()) - PREFERENCES.keys())
        kind = str(row.get('userType') or '').casefold()
        names = [{'member': 'Members', 'guest': 'Guests'}.get(kind, 'User type unknown')]
        if row.get('isAdmin') is True:
            names.append('Administrators')
        for name in names:
            populations[name].update(users=1, resistant=has_resistant, phone_only=phone_only,
                                     phone_preferred=phone_preferred, inventory_known=methods is not None,
                                     resistant_known=resistant_known, phone_only_known=phone_only_known,
                                     preference_known=phone_preference_known)
        try:
            value = row.get('lastUpdatedDateTime')
            parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                dates.append(parsed.date().isoformat())
        except (ValueError, TypeError):
            pass
    summary_rows = []
    def metric(label, key, known_key=None, note=''):
        known = metrics[known_key] if known_key else total
        value = metrics[key] if known else None
        summary_rows.append({'Metric': label, 'Value': value if value is not None else 'Unknown',
                             'Known users': known, 'Unknown users': total - known, 'Interpretation': note})
    summary_rows.append({'Metric': 'Users in returned registration report', 'Value': total,
                         'Known users': total, 'Unknown users': 0, 'Interpretation': 'Members and guests; disabled/deleted users are outside this report.'})
    metric('MFA registered users', 'mfa_registered', 'mfa_registered_known', 'Registration does not establish enforcement or phishing resistance.')
    registered_known = metrics['mfa_registered_known']
    summary_rows.append({'Metric': 'MFA registration rate (%)',
                         'Value': round(metrics['mfa_registered'] / registered_known * 100, 1) if registered_known else 'Unknown',
                         'Known users': registered_known, 'Unknown users': total - registered_known,
                         'Interpretation': 'Percentage of users with a known registration flag; not an enforcement or method-strength score.'})
    metric('MFA capable users', 'mfa_capable', 'mfa_capable_known', 'Registered for an MFA method allowed by policy; does not prove it was used.')
    metric('Passwordless capable users', 'passwordless_capable', 'passwordless_capable_known', 'Graph capability flag; passwordless is not always phishing-resistant.')
    metric('Phishing-resistant method registered', 'phishing_resistant_registered', 'resistant_inventory_known', 'FIDO2/passkeys, Windows Hello, or macOS platform credentials. CBA requires separate MFA configuration verification.')
    metric('Phone method registered', 'phone_registered', 'method_inventory_known', 'SMS/voice availability; may coexist with stronger methods and may be disabled by policy.')
    metric('Phone-only MFA registration', 'phone_only_mfa_registered', 'phone_only_inventory_known', 'MFA-registered users with a phone and no reported reusable non-phone MFA method; unknown methods exclude this classification.')
    metric('Email registered for recovery / guest sign-in', 'email_registered', 'method_inventory_known', 'Not counted as workforce MFA.')
    metric('SMS/voice preferred in report', 'phone_preferred', 'phone_preference_known', 'Uses system preference when enabled, otherwise the user preference. Does not establish actual sign-in usage.')
    metric('User-selected SMS/voice default', 'user_phone_preferred', 'user_phone_preference_known', 'May be superseded by system-preferred authentication.')
    metric('System-preferred authentication enabled', 'system_preferred_enabled', 'system_preferred_mode_known', 'Unknown mode is reported separately.')
    metric('System-preferred mode unknown', 'system_preferred_unknown')
    metric('Registered methods needing classification review', 'methods_need_review', 'method_inventory_known')
    metric('Preferred methods needing classification review', 'current_preference_needs_review', 'current_preference_known')
    by_label = {label: (group, note) for label, group, note in METHODS.values()}
    method_rows = [{'Method': label, 'Registered users': count,
                    'Percent of known inventories': round(count / metrics['method_inventory_known'] * 100, 1),
                    'Strength / purpose': by_label.get(label, ('review', 'Unknown method; review required'))[1]}
                   for label, count in sorted(registered.items(), key=lambda item: (-item[1], item[0]))]
    preference_rows = [{'Method': _preference(value)[0],
                        'User-selected users': chosen[value] if metrics['user_preference_known'] else 'Unknown',
                        'System-preferred users': system[value] if metrics['system_preference_known'] else 'Unknown',
                        'Preferred users in report': current[value] if metrics['current_preference_known'] else 'Unknown',
                        'Strength': _preference(value)[1]}
                       for value in sorted(chosen.keys() | system.keys() | current.keys())]
    population_rows = [{'Population': name, 'Users': values['users'],
                        'Phishing-resistant registered': values['resistant'] if values['resistant_known'] else 'Unknown',
                        'Phone-only MFA registration': values['phone_only'] if values['phone_only_known'] else 'Unknown',
                        'SMS/voice preferred': values['phone_preferred'] if values['preference_known'] else 'Unknown',
                        'Method inventories unknown': values['users'] - values['inventory_known'],
                        'Phishing-resistant classification unknown': values['users'] - values['resistant_known'],
                        'Phone-only classification unknown': values['users'] - values['phone_only_known'],
                        'Preferences unknown': values['users'] - values['preference_known']}
                       for name, values in populations.items() if values['users']]
    return {'available': bool(records), 'source_state': availability,
            'complete': availability == 'available' and not conflicts,
            'total_users': total, 'metrics': dict(metrics), 'summary_rows': summary_rows,
            'method_rows': method_rows, 'preference_rows': preference_rows, 'population_rows': population_rows,
            'updated_from': min(dates) if dates else '', 'updated_to': max(dates) if dates else '',
            'dates_unknown': total - len(dates), 'duplicates_removed': duplicates, 'conflicting_users': len(conflicts),
            'details': [
                'Counts cover users returned by the authentication registration report, including members and guests. Administrators overlap those populations; do not add them to the total.',
                'Users can register multiple methods. Method counts and percentages overlap; they do not add to 100%. Unknown fields are not measured zero.',
                'Default/preferred fields describe second-factor preferences, not a complete ranking of passwordless sign-ins or methods actually used. System preference can override the user-selected default.',
                f"Preference fields available: user-selected {metrics['user_preference_known']}/{total}; system-preferred for enabled users {metrics['system_preference_known']}/{metrics['system_preferred_enabled']}; selected route {metrics['current_preference_known']}/{total}. Multiple system preferences can overlap.",
                f"Source state: {availability}. Source report update dates: {min(dates) if dates else 'unknown'} to {max(dates) if dates else 'unknown'}; {total - len(dates)} dates unavailable. Dates describe report refresh, not method registration. Microsoft reports usually refresh within 36 hours.",
                'Authenticator push and verification codes improve on phone methods but remain susceptible to phishing. Passkeys and Windows Hello provide phishing resistance; certificate-based MFA needs configuration verification.',
                'Registration and preference do not prove that Conditional Access enforces an authentication strength for the intended users. This breakdown does not independently pass or fail readiness controls.',
            ]}


def authentication_method_report(client):
    return summarize_registrations(getattr(client, 'auth_methods_registration', None),
        (getattr(client, 'collection_status', {}) or {}).get('auth_methods', {}))


def legacy_registration_summary(records):
    """Preserve collector summary keys using current Graph method names."""
    report = summarize_registrations(records)
    metrics = report['metrics']
    total = report['total_users']
    return {'total_users': total, 'mfa_registered': metrics.get('mfa_registered', 0),
            'mfa_capable': metrics.get('mfa_capable', 0), 'passwordless_enabled': metrics.get('passwordless_registered', 0),
            'mfa_registration_rate': int(metrics.get('mfa_registered', 0) / total * 100) if total else 0,
            'passwordless_adoption_rate': int(metrics.get('passwordless_registered', 0) / total * 100) if total else 0,
            'methods': {'microsoftAuthenticator': metrics.get('authenticator_registered', 0),
                        'microsoftAuthenticatorPasswordless': metrics.get('authenticator_passwordless_registered', 0),
                        'fido2': metrics.get('fido_registered', 0),
                        'windowsHello': metrics.get('windows_hello_registered', 0),
                        'phone': metrics.get('phone_registered', 0), 'email': metrics.get('email_registered', 0),
                        'softwareOath': metrics.get('software_oath_registered', 0),
                        'temporaryAccessPass': metrics.get('tap_registered', 0)}}

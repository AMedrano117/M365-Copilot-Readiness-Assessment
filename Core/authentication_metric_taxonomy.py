"""Metric-level technical naming; never creates findings or changes measurements."""
METRICS = {
 'population.returned': ('Authentication registration population', 'population'),
 'population.coverage': ('Registration population coverage', 'population'),
 'population.unknown': ('Registration population unknown', 'population'),
 'mfa_registered': ('MFA registration coverage', 'registration_state'),
 'registration.ExplicitlyRegistered': ('Explicitly registered for MFA', 'registration_state'),
 'registration.ExplicitlyNotRegistered': ('Explicitly not registered for MFA', 'registration_state'),
 'registration.Unknown': ('MFA registration unknown', 'registration_state'),
 'registration.Conflicting': ('MFA registration conflicting', 'registration_state'),
 'registration.ExcludedWithReason': ('MFA registration excluded with reason', 'registration_state'),
 'mfa.rate': ('MFA registration coverage', 'registration_state'),
 'mfa_capable': ('MFA capability coverage', 'capability_state'),
 'isSsprRegistered': ('SSPR registration coverage', 'registration_state'),
 'isSsprEnabled': ('SSPR enablement coverage', 'configuration'),
 'isSsprCapable': ('SSPR capability coverage', 'capability_state'),
 'passwordless_capable': ('Passwordless authentication capability', 'capability_state'),
 'passwordless_registered': ('Passwordless authentication registration', 'registration_state'),
 'passwordless_classification': ('Passwordless method classification review', 'classification'),
 'phishing_resistant_registered': ('Phishing-resistant method registration', 'registration_state'),
 'phishing_resistant_capable': ('Phishing-resistant capability coverage', 'capability_state'),
 'phone_registered': ('Phone-based authentication method registration', 'registration_state'),
 'phone_only_mfa_registered': ('Phone-only MFA registration', 'registration_state'),
 'email_registered': ('Recovery email registration', 'registration_state'),
 'recovery.coverage': ('Recovery method coverage', 'registration_state'),
 'preferences.configuration': ('Authentication preference configuration', 'configuration'),
 'phone_preferred': ('SMS or voice preference review', 'configuration'),
 'user_phone_preferred': ('User-selected default method review', 'configuration'),
 'system_preferred_enabled': ('System-preferred authentication configuration', 'configuration'),
 'system_preferred_unknown': ('System-preferred mode unknown', 'evidence_gap'),
 'methods_need_review': ('Authentication-method classification review', 'classification'),
 'current_preference_needs_review': ('Preferred-method classification review', 'classification'),
}
LEGACY = dict(zip(('Users in returned registration report','MFA registered users','MFA registration rate (%)',
 'MFA capable users','SSPR registered','SSPR enabled','SSPR capable','Passwordless capable users',
 'Phishing-resistant method registered','Phone method registered','Phone-only MFA registration',
 'Email registered for recovery / guest sign-in','SMS/voice preferred in report','User-selected SMS/voice default',
 'System-preferred authentication enabled','System-preferred mode unknown','Registered methods needing classification review',
 'Preferred methods needing classification review'), ('population.returned','mfa_registered','mfa.rate','mfa_capable',
 'isSsprRegistered','isSsprEnabled','isSsprCapable','passwordless_capable','phishing_resistant_registered','phone_registered',
 'phone_only_mfa_registered','email_registered','phone_preferred','user_phone_preferred','system_preferred_enabled',
 'system_preferred_unknown','methods_need_review','current_preference_needs_review')))
LEGACY.update({'Registration state: '+key.removeprefix('registration.'):key for key in METRICS if key.startswith('registration.')})


def resolve_metric(row):
    result = dict(row)
    identifier = row.get('Metric ID') or LEGACY.get(row.get('Metric'))
    title, level = METRICS.get(identifier, ('Authentication metric classification review', 'evidence_gap'))
    result.update({'Metric ID':identifier or 'unresolved', 'Metric Condition':title, 'Metric Evidence Level':level,
                   'Metric Taxonomy Authority':'metric_id' if row.get('Metric ID') else 'legacy_metric_signature',
                   'Metric Measurement State':'unknown' if row.get('Value') in (None,'Unknown') else 'returned',
                   'Metric Control Area':'Authentication registration report'})
    return result

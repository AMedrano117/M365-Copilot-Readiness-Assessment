"""Render only shared authentication facts; never reevaluate raw source data."""

from html import escape


def authentication_summary_html(result):
    data = result.get('authentication_assessment') or {}
    if not data:
        return ''
    registration = data.get('registration') or {}
    counts = registration.get('counts') or {}
    registered = counts.get('ExplicitlyRegistered')
    denominator = registration.get('denominator')
    registration_text = (f'{registered} registered among {denominator} known eligible users; '
                         f"{counts.get('Unknown', 0)} unknown, {counts.get('Conflicting', 0)} conflicting and "
                         f"{counts.get('ExcludedWithReason', 0)} excluded with reason. " if denominator else
                         'A known eligible registration denominator is not established. ')
    registration_text += 'Registration does not establish enforcement or actual use.'
    enforcement = data.get('enforcement') or {}
    observed = data.get('observed_authentication') or {}
    legacy = observed.get('legacy') or {}
    outcomes = legacy.get('counts') or {}
    observed_text = ', '.join(f'{name}: {count}' for name, count in outcomes.items())
    observed_text += '. MFA satisfaction: ' + (', '.join(f'{name}: {count}' for name, count in (observed.get('mfa_satisfaction') or {}).items()) or 'unknown')
    observed_text += '. Complete known source period: ' + ('yes' if legacy.get('complete') and legacy.get('known_population_and_period') else 'not established')
    observed_text += '. ' + str(observed.get('qualification') or '')
    facts = [('MFA registration', registration_text),
             ('MFA enforcement', str(enforcement.get('operational_result') or 'unknown') + '. ' + str(enforcement.get('qualification') or '')),
             ('Observed authentication', observed_text)]
    return ('<section id="authentication-observations"><h2>Authentication evidence</h2>'
            + ''.join(f'<p><strong>{escape(label)}.</strong> {escape(text)}</p>' for label, text in facts) + '</section>')

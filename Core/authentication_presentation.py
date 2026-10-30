"""Render only shared authentication facts; never reevaluate raw source data."""

from html import escape


def authentication_summary_html(result):
    from .finding_presentation import render_details
    from .customer_report import table
    comparison = (((result.get('authentication_assessment') or {}).get('registration') or {}).get('metric_reconciliation') or {}).get('comparison_explanation','')
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
    registration_text += 'Registration does not establish enforcement or actual use. ' + comparison
    enforcement = data.get('enforcement') or {}
    observed = data.get('observed_authentication') or {}
    legacy = observed.get('legacy') or {}
    outcomes = legacy.get('counts') or {}
    outcome_rows = [{'outcome': name, 'count': count} for name, count in outcomes.items()]
    mfa_rows = [{'outcome': name, 'count': count} for name, count in (observed.get('mfa_satisfaction') or {}).items()]
    observed_text = 'Complete known source period: ' + ('yes' if legacy.get('complete') and legacy.get('known_population_and_period') else 'not established')
    observed_text += '. ' + str(observed.get('qualification') or '')
    facts = [('MFA registration', registration_text),
             ('MFA enforcement', str(enforcement.get('operational_result') or 'unknown') + '. ' + str(enforcement.get('qualification') or ''))]
    return ('<section id="authentication-observations"><h2>Authentication evidence</h2>'
            + ''.join('<h3>' + escape(label) + '</h3>' + render_details(text, escape, table) for label, text in facts)
            + '<h3>Observed authentication</h3>'
            + (table(outcome_rows, [('Legacy authentication outcome', 'outcome'), ('Recorded count', 'count')], mobile_labels=True)
               if outcome_rows else '<p>Legacy authentication outcomes not established.</p>')
            + (table(mfa_rows, [('MFA satisfaction outcome', 'outcome'), ('Recorded count', 'count')], mobile_labels=True)
               if mfa_rows else '<p>MFA satisfaction: unknown.</p>')
            + render_details(observed_text, escape, table) + '</section>')

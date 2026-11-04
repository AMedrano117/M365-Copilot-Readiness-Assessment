"""Assign recorded aggregate context to relevant visible cards, once per topic."""


def finding_context(result, rows):
    from .authentication_presentation import authentication_summary_html
    from .privileged_presentation import privileged_summary_html
    unique = {row.get('RecommendationId'): row for row in rows if row.get('RecommendationId')}
    def choose(predicate):
        return next((identifier for identifier, row in unique.items() if predicate(row)), None)
    def text(row):
        return (str(row.get('FindingKey') or '')+' '+str(row.get('Feature') or '')).casefold()
    registration = choose(lambda row: 'mfa_registration' in text(row) or 'mfa registration' in text(row))
    enforcement = choose(lambda row: row.get('ControlId') == 'IDENTITY.AUTH' or 'mfa_enforcement' in text(row))
    observed = choose(lambda row: 'legacy_auth' in text(row) or 'legacy authentication' in text(row))
    privilege = choose(lambda row: str(row.get('FindingKey') or '').startswith('entra.privileged.'))
    if privilege is None:
        privilege = choose(lambda row: row.get('ControlId') == 'IDENTITY.ADMIN')
    sections = {}
    for topic, target in [('registration', registration), ('enforcement', enforcement or registration), ('observed', observed)]:
        if target:
            sections.setdefault(target, []).append(topic)
    output = {}
    for target, topics in sections.items():
        content = authentication_summary_html(result, sections=topics, embedded=True)
        if content:
            output[target] = '<details class="supporting-detail"><summary>Authentication supporting details</summary>'+content+'</details>'
    if privilege:
        content = privileged_summary_html(result, embedded=True)
        if content:
            output[privilege] = output.get(privilege, '') + '<details class="supporting-detail"><summary>Privilege supporting details</summary>'+content+'</details>'
    return output

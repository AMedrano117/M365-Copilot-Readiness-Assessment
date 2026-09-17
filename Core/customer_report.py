"""Standalone customer narrative rendered exclusively from the assessment result.

No scoring or finding selection belongs here. Detailed customer inventories stay
in the workbook; HTML contains aggregate evidence and links to the action register.
"""

from html import escape
from pathlib import Path
import re
from urllib.parse import quote
from .report_styles import REPORT_CSS
from .customer_report_guidance import GUIDES, guide_for


REPORT_SCRIPT = r'''
(() => {
  function reveal(hash) {
    if (!hash || hash === '#') return;
    let target;
    try { target = document.getElementById(decodeURIComponent(hash.slice(1))); }
    catch (_) { return; }
    if (!target) return;
    for (let node = target; node; node = node.parentElement) {
      if (node.tagName === 'DETAILS') node.open = true;
    }
    const action = target.matches('.action') && target.querySelector('details');
    if (action) action.open = true;
    requestAnimationFrame(() => {
      target.setAttribute('tabindex', '-1');
      target.focus({preventScroll: true});
      target.scrollIntoView({block: 'start'});
    });
  }
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href^="#"]');
    if (link && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
      setTimeout(() => reveal(link.hash), 0);
    }
  });
  window.addEventListener('hashchange', () => reveal(location.hash));
  reveal(location.hash);
  const printButton = document.getElementById('print-report');
  if (printButton) {
    printButton.hidden = false;
    printButton.addEventListener('click', () => window.print());
  }
  let printState = [];
  window.addEventListener('beforeprint', () => {
    if (printState.length) return;
    printState = Array.from(document.querySelectorAll('main details'))
      .map(node => [node, node.open]);
    printState.forEach(([node]) => { node.open = true; });
  });
  window.addEventListener('afterprint', () => {
    printState.forEach(([node, wasOpen]) => { node.open = wasOpen; });
    printState = [];
  });
})();
'''


def plain(value):
    text = '' if value is None else str(value)
    def plural(match):
        phrase = re.split(r'[.;:]|\b(?:and|but|including)\b', text[:match.start()])[-1]
        counts = re.findall(r'\b\d[\d,]*\b', phrase)
        singular = counts and int(counts[-1].replace(',', '')) == 1
        stem, suffix = match.groups()
        return stem + ('y' if singular else 'ies') if suffix == 'ies' else stem + ('' if singular else 's')
    return re.sub(r'\b([A-Za-z]+)\((s|ies)\)', plural, text).strip()


def prose(value):
    safe = escape(plain(value))
    safe = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', safe, flags=re.S)
    return safe.replace('\n', '<br>') or '<span class="muted">Not established</span>'


def slug(value):
    return re.sub(r'[^a-z0-9]+', '-', str(value).lower()).strip('-') or 'evidence'


def table(rows, columns, caption='', *, mobile_labels=False):
    if not rows:
        return '<p class="muted">No observations supplied.</p>'
    heading = ''.join(f'<th scope="col">{escape(label)}</th>' for label, _ in columns)
    labels = {key: f' data-label="{escape(label, quote=True)}"' if mobile_labels else '' for label, key in columns}
    body = ''.join('<tr>' + ''.join(f'<td{labels[key]}>{prose(row.get(key))}</td>' for _, key in columns) + '</tr>' for row in rows)
    return f'<div class="table-scroll"><table><caption>{escape(caption)}</caption><thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table></div>'


def _date_text(row):
    return row.get('ObservationDate') or row.get('observed_at') or 'Date not established'


def _heading(row):
    """Use reviewed topic labels for the parser-facing report families."""
    titles = {
        'data_exposure.inactive_sites': 'Review inactive SharePoint sites',
        'data_exposure.ownerless_sites': 'Confirm and assign site owners',
        'sharepoint.dag.not_assessed': 'Complete the SharePoint permissions and sharing review',
        'data_exposure.freshness.sam': 'Refresh the older SharePoint permissions evidence',
        'purview.audit.state': 'Audit logging configuration',
    }
    if row.get('FindingKey') in titles:
        return titles[row['FindingKey']]
    feature = str(row.get('ActionTitle') or row.get('Feature') or row.get('Strength') or 'Review this condition')
    if row.get('ActionTitle'):
        return feature
    observation = str(row.get('Observation') or '').lower()
    for token, title in (
        ('user consent enabled for applications', 'Restrict application consent'),
        ('application grants requiring review', 'Review application grants and permissions'),
        ('enrolled in mfa', 'Close multifactor authentication registration gaps'),
        ('role assignment schedules have no expiration', 'Reduce standing administrative access'),
        ('risky users detected', 'Review unresolved identity risks'),
        ('intune query returned no managed devices', 'Verify device and browser protection'),
        ('sharepoint tenant sharing settings were not collected', 'Confirm tenant sharing settings'),
        ('conditional access policies found', 'Conditional Access policies'),
        ('no legacy authentication sign-ins', 'Sign-in sample'),
        ('use passwordless authentication', 'Passwordless authentication'),
        ('access reviews were returned', 'Access reviews'),
        ('access review definitions were returned', 'Access reviews'),
        ('guest invitation eligibility', 'Guest invitation settings'),
    ):
        if token in observation:
            return title
    text = feature.lower()
    for token, title in (
        ('ownerless', 'Assign accountable owners to SharePoint sites'),
        ('inactive', 'Review sites that are no longer active'),
        ('everyone except external', 'Review access granted to everyone in the organization'),
        ('eeeu', 'Review access granted to everyone in the organization'),
        ('anyone link', 'Review links that allow access without sign-in'),
        ('sensitive-data exposure evidence', 'Confirm who can access sensitive content'),
        ('sharepoint oversharing evidence', 'Complete the content access review'),
        ('offline tenant evidence coverage', 'Confirm the outstanding tenant controls'),
        ('saved tenant collection freshness', 'Refresh dated tenant control evidence'),
    ):
        if token in text:
            return title
    return feature


def _action_text(row):
    # Keep the action brief; report procedures are linked from the action card.
    key = row.get('FindingKey')
    if key == 'sharepoint.dag.not_assessed':
        return 'Review the permission exports already available, confirm their coverage of the pilot sites, and obtain any missing sharing-activity reports. The report guide below explains where to find them.'
    if key == 'coverage.adoption.baseline':
        return 'Agree the pilot users, work tasks and success measures with the business sponsor. Reuse the available usage evidence where its population and period match the pilot.'
    if key == 'coverage.license.assignment':
        return 'Match the agreed pilot roster to assigned Copilot licenses and application prerequisites. Reuse the supplied readiness export where it covers those users, and review any missing or unknown assignments.'
    if key == 'coverage.license.apps':
        return 'Check the named pilot users against the required Microsoft 365 applications, update channels and enabled services. Reuse the readiness export, and validate any users or prerequisites it does not cover.'
    if key == 'coverage.apps.connections':
        return 'Identify which connected data sources the pilot is intended to use, and review their permissions and ownership. If none are needed, document that scope decision; enabling a connector is not a prerequisite for every pilot.'
    if key == 'coverage.data.retention':
        return 'Ask the compliance and content owners to agree retention requirements for the pilot content and verify which policies meet them.'
    feature = str(row.get('Feature', '')).lower()
    if 'offline tenant evidence coverage' in feature or 'saved tenant collection freshness' in feature:
        return 'Ask the tenant administrators to confirm the outstanding controls for the intended pilot population and record the evidence date.'
    if 'sensitive-data exposure evidence' in feature:
        return 'Ask the data protection owner for a completed risk assessment or a dated access review covering sensitive pilot content and the people who can access it. Record the scope, findings and supporting evidence.'
    if 'sharepoint oversharing evidence' in feature:
        return 'Ask the SharePoint owner for the missing permissions or sharing reports for the intended pilot sites. Record their scope and completion dates.'
    if 'scan freshness' in feature:
        return 'Confirm the dated observations that still apply to the pilot content. Obtain a completed, current report for any remaining checks, and retain its selected scope and completion date.'
    text = row.get('Recommendation') or ''
    if any(marker in text for marker in ('--', '_PATHS', '_DAYS', 'main.py', 'PowerShell', 'Connect-')):
        return 'Ask the responsible administrator to complete this check for the pilot population. Record the result, original observation date, and any required remediation in the evidence workbook.'
    return row.get('Recommendation') or row.get('CompletionEvidence') or 'Confirm this condition with the responsible owner and record the outcome.'


def _observation(row, bundle):
    """Clarify what the observed configuration establishes; retain raw evidence in the workbook."""
    text = row.get('Observation') or row.get('Evidence') or row.get('Strength') or ''
    if row.get('FindingKey') == 'purview.audit.state' and 'Copilot activities are logged' in text:
        return text.replace(' - Copilot activities are logged', '. Copilot event coverage still needs verification.')
    if text.startswith('User consent enabled for applications, allowing users to grant apps access to Copilot-generated content'):
        return 'User consent is enabled for applications. Review which permissions users can approve without individual administrator review; this setting alone does not establish which content an app can access.'
    permission_reports = [report for report in (bundle.get('data_exposure') or {}).get('sources', {}).get('sam', {}).get('reports', [])
                          if report.get('report_type') in {'permission_snapshot', 'special_group_permissions'} and report.get('records_read', 0)]
    if row.get('FindingKey') == 'sharepoint.dag.not_assessed' and permission_reports:
        return 'The saved collection recorded incomplete SharePoint report coverage. Permission exports were also supplied and assessed in the content findings. Confirm that the combined evidence covers the pilot sites and the remaining sharing activity.'
    return text


def _completion(row):
    requested = {
        'sharepoint.dag.not_assessed': 'Completed permission and sharing-activity exports, with their dates, workloads, selected sites and filters recorded; document any unavailable report.',
        'data_exposure.freshness.sam': 'Current permission evidence for the sites still represented only by older reports, or a dated administrator review explaining their present status.',
        'data_exposure.coverage.sensitive-data_exposure_evidence': 'A completed data-risk assessment or dated owner review of sensitive pilot content and access, with the covered population, findings and supporting evidence recorded.',
        'data_exposure.snapshot_scope': 'A dated comparison of the site populations and filters, with an explanation for each site omitted from the newer report.',
        'coverage.adoption.baseline': 'A sponsor-approved pilot roster, work tasks, baseline period and success measures. A usage export alone does not establish this agreement.',
        'coverage.license.assignment': 'A dated check of named pilot users against assigned Copilot licenses and application prerequisites, with exceptions resolved or recorded.',
        'coverage.license.apps': 'A dated check of the pilot users’ application prerequisites and update channels, with unsupported or unknown configurations identified and addressed.',
        'coverage.apps.connections': 'The approved list of connected sources, their owners and reviewed access boundaries, or a dated confirmation that no connected sources are in scope.',
        'coverage.data.exposure': 'A completed risk assessment or dated owner review identifying sensitive pilot content, who can access it, and the treatment of any access concerns.',
        'coverage.data.retention': 'Agreed retention requirements, the policies and locations that meet them, and the compliance owner’s decision on any gaps.',
        'data_exposure.inactive_sites': 'The reviewed site list, each business owner’s decision to retain, restrict or archive it, and confirmation of any completed changes.',
        'data_exposure.ownerless_sites': 'The reviewed site list with an accountable owner for each retained site, plus confirmation that the ownership details are current.',
        'defender.incidents.current': 'Incident IDs, their impact on pilot users or devices, and closure or the security owner’s approved response.',
        'entra.app_consent.high_impact_grants': 'A reviewed list of application grants, their business owners and approved permissions, with unnecessary access removed or an approved treatment recorded.',
    }
    if row.get('FindingKey') in requested:
        return requested[row['FindingKey']]
    observation = str(row.get('Observation') or '').lower()
    for token, deliverable in (
        ('intune query returned no managed devices', 'A dated record of the actual device or browser protection method, its coverage of pilot users, and a test that the required access controls are enforced.'),
        ('enrolled in mfa', 'Named pilot users, multifactor authentication registration status, and a successful test of the required sign-in controls.'),
        ('role assignment schedules have no expiration', 'The reviewed administrator assignments, their business need and expiry or approval, with unnecessary standing access removed.'),
        ('risky users detected', 'The reviewed risky-user cases affecting the pilot, investigation outcomes, and remediation or the identity owner’s approved response.'),
        ('user consent enabled for applications', 'The reviewed application consent policy, allowed permissions, approval process and a test of the intended restrictions.'),
    ):
        if token in observation:
            return deliverable
    return row.get('CompletionEvidence')


def _report_guides(actions):
    """One expandable guide per needed report family, shared by related actions."""
    guides = []
    for key in dict.fromkeys(guide_for(row) for row in actions):
        if not key:
            continue
        guide = GUIDES[key]
        steps = ''.join('<li>' + prose(step) + '</li>' for step in guide['steps'])
        sources = ' · '.join(f'<a href="{escape(url, quote=True)}">{escape(title)}</a>' for title, url in guide['sources'])
        guides.append(f'''<details class="domain-evidence report-guide" id="report-guide-{key}"><summary>How to obtain: {prose(guide['title'])}</summary><div class="guide-body">
          <p><strong>Responsible administrator.</strong> {prose(guide['owner'])}</p><p><strong>Access needed.</strong> {prose(guide['prerequisites'])}</p>
          <ol>{steps}</ol><p><strong>Return for review.</strong> {prose(guide['completion'])}</p><p class="qualification">Microsoft instructions: {sources}</p></div></details>''')
    if guides:
        guides.append('''<details class="domain-evidence report-guide" id="report-handoff"><summary>How to add completed reports to this assessment</summary><div class="guide-body">
          <ol><li>Give the assessment operator the original exports for this tenant. Keep the original headers and record the completion date, selected sites and filters. Extract CSVs from downloaded ZIP files.</li>
          <li>The operator adds the exports to the customer’s reports folder and rebuilds this assessment using the same saved tenant collection. The operator command is in the technical appendix.</li>
          <li>Review the new report’s imported sources and remaining checks. An export closes a gap only when its content, date and scope support that check. Portal formats vary; unrecognized exports need review and remain unassessed. Purview data-risk export compatibility still needs validation against a real export.</li>
          <li>For owner decisions, return the dated review described in the action. The assessment owner records and verifies it in the evidence workbook; it is not an automatic report import.</li></ol></div></details>''')
    return ''.join(guides)


def _readiness_export(bundle):
    from .copilot_readiness_import import FLAG_COLUMNS
    source = bundle.get('copilot_readiness_export') or {}
    if not source:
        return ''
    if not source.get('available'):
        return '<section id="copilot-readiness-export"><h3>Copilot prerequisites</h3><p>The supplied readiness report could not be assessed. ' + prose(source.get('error')) + '</p></section>'
    rows = [{'label': label, **(source.get('metrics', {}).get(key) or {})} for key, label in FLAG_COLUMNS.items()]
    return f'''<section id="copilot-readiness-export"><h3>Copilot prerequisites in the exported population</h3>
      <p>{prose(source.get('total_rows'))} exported rows · {prose(source.get('report_date'))} · {prose(source.get('report_period'))} days.</p>
      <p>These flags do not establish tenant-wide license totals and do not measure actual Copilot usage. Unknown flags need confirmation.</p>
      {table(rows, [('Reported condition', 'label'), ('Yes', 'true'), ('No', 'false'), ('Unknown', 'unknown')])}</section>'''


def _lifecycle(bundle):
    data = (bundle.get('data_exposure') or {}).get('lifecycle_summary') or {}
    if not data.get('available'):
        return ''
    rows = [{'label': label, 'value': data.get(key)} for label, key in (
        ('Sites in the export', 'site_count'), ('Reported ownerless', 'ownerless_site_count'),
        ('Reported inactive', 'inactive_site_count'), ('Owner status unknown', 'unknown_owner_status_count'),
        ('Missing owner contact', 'missing_owner_contact_count'))]
    return f'''<section id="sharepoint-lifecycle-summary"><h3>Content ownership and lifecycle</h3>
      <p>Observed {prose(data.get('latest_report_date') or 'date not established')}. These measures do not establish oversharing. A missing owner contact does not establish that a site has no owner.</p>
      {('<p class="qualification">' + prose(data.get('date_basis')) + '. Accepted lifecycle report age: ' + prose(data.get('max_age_days')) + ' days.</p>') if data.get('max_age_days') else ''}
      {table(rows, [('Measure', 'label'), ('Sites', 'value')])}</section>'''


def _sharing_setting_value(key, value):
    """Translate by property type; bool keys must never consume enum values 0/1.

    Numeric enum members were verified against Microsoft's tenant client DLL
    bundled with SharePoint Online PowerShell 16.0.27612.12000. Meanings and
    zero-day expiry behavior follow the Set-SPOTenant parameter documentation:
    https://learn.microsoft.com/powershell/module/microsoft.online.sharepoint.powershell/set-spotenant
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return 'Not reported'
    text = str(value).strip()
    number = value if type(value) is int else int(text) if isinstance(value, str) and re.fullmatch(r'[+-]?\d+', text) else None
    enum_values = None
    if key in {'SharingCapability', 'OneDriveSharingCapability'}:
        enum_values = {
            'disabled': 'External sharing disabled',
            'externalusersharingonly': 'New and existing guests; no Anyone links',
            'externaluserandguestsharing': 'Anyone links, new guests, and existing guests',
            'existingexternalusersharingonly': 'Existing guests only',
        }
        names = {0: 'disabled', 1: 'externalusersharingonly',
                 2: 'externaluserandguestsharing', 3: 'existingexternalusersharingonly'}
    elif key == 'DefaultSharingLinkType':
        enum_values = {
            'none': 'Widest sharing scope allowed by other sharing settings',
            'direct': 'Specific people', 'specificpeople': 'Specific people',
            'internal': 'People in the organization', 'anonymousaccess': 'Anyone link',
        }
        names = {0: 'none', 1: 'direct', 2: 'internal', 3: 'anonymousaccess'}
    if enum_values is not None:
        return enum_values.get(names.get(number) if number is not None else text.lower(),
                               f'Unmapped value ({text})')
    if key == 'RequireAnonymousLinksExpireInDays':
        if number == 0:
            return 'No expiry requirement (0 days)'
        if number is not None and 1 <= number <= 730:
            return f'{number} day' + ('' if number == 1 else 's')
        return f'Unmapped value ({text})'
    if key in {'RequireAcceptingAccountMatchInvitedAccount', 'PreventExternalUsersFromResharing',
               'ExternalUserExpirationRequired', 'LegacyAuthProtocolsEnabled'}:
        if type(value) is bool:
            return 'Enabled' if value else 'Disabled'
        if number in {0, 1}:
            return 'Enabled' if number == 1 else 'Disabled'
        return {'true': 'Enabled', 'false': 'Disabled', 'enabled': 'Enabled',
                'disabled': 'Disabled', 'yes': 'Enabled', 'no': 'Disabled'}.get(
                    text.lower(), f'Unmapped value ({text})')
    return f'Unmapped value ({text})'


def _settings(bundle):
    settings = (bundle.get('sharepoint_governance') or {}).get('settings') or {}
    labels = {'SharingCapability': 'SharePoint external sharing', 'OneDriveSharingCapability': 'OneDrive external sharing',
              'DefaultSharingLinkType': 'Default sharing link', 'RequireAcceptingAccountMatchInvitedAccount': 'Require invited account match',
              'PreventExternalUsersFromResharing': 'Prevent guests from sharing again',
              'ExternalUserExpirationRequired': 'Require guest access expiry',
              'LegacyAuthProtocolsEnabled': 'Legacy authentication protocols',
              'RequireAnonymousLinksExpireInDays': 'Anyone link expiry'}
    rows = [{'label': label, 'value': _sharing_setting_value(key, settings[key])} for key, label in labels.items() if key in settings]
    return '<h3>Tenant sharing settings</h3><p>Configuration describes permitted behavior; it does not establish who currently has access to each file.</p>' + table(rows, [('Setting', 'label'), ('Observed value', 'value')]) if rows else ''


def _license_context(bundle):
    context = bundle.get('entra_license_context') or {}
    tier = context.get('tier') or context.get('detected_tier')
    if not tier or tier == 'Not detected from service plans':
        return ''
    return '<h3>Identity licensing context</h3><p>Detected tenant tier: ' + prose(tier) + '</p>' + table(context.get('capabilities') or context.get('rows') or [], [('Capability', 'Capability'), ('Availability', 'Tenant')])


def _scoped_products(bundle):
    conclusion = bundle.get('conclusions') or {}
    profile = bundle.get('assessment_profile') or {}
    products = profile.get('products') or []
    cases = conclusion.get('use_cases') or []
    if not products and not cases:
        return ''
    products = [p if isinstance(p, dict) else {'product': p} for p in products]
    return '<h3>Scoped products and use cases</h3>' + table(products, [(key.replace('_', ' ').title(), key) for key in dict.fromkeys(k for r in products for k in r)]) + table(cases, [(key, key) for key in dict.fromkeys(k for r in cases for k in r)])


def _adoption_report(result):
    metrics = result.get('adoption_metrics') or []
    if not metrics:
        return '<h3>Usage baseline</h3><p>No measured Copilot usage was supplied. Agree whether the pilot is a first deployment or an expansion, and record the baseline needed to judge its outcomes.</p>'
    periods = {str(m.get('window') or '') for m in metrics if m.get('metric_kind') == 'summary'}
    primary = next((p for p in ('D28', 'D30', 'D7', 'D90', 'D180') if p in periods), next(iter(sorted(periods)), ''))
    summary = [m for m in metrics if m.get('metric_kind') == 'summary' and m.get('window') == primary]
    if not summary:
        return '<h3>Available aggregate observations</h3><p>The supplied evidence includes the observations below. A measured Copilot activity report is still needed if activity is part of the pilot baseline.</p>' + table(metrics, [('Measure', 'label'), ('Value', 'value'), ('Unit', 'unit'), ('Population', 'population'), ('Reporting window', 'window_label'), ('Observed', 'observed_at')])
    dates = sorted({m['observed_at'] for m in summary if m.get('observed_at')})
    heading = (summary[0].get('window_label') or primary) if summary else 'Available period'
    qualification = 'Historical aggregate usage' if any(m.get('source_type') == 'prior_assessment' for m in summary) else 'Measured usage'
    columns = [('Measure', 'label'), ('Value', 'value'), ('Unit', 'unit'), ('Population', 'population'), ('Reporting window', 'window_label'), ('Observed', 'observed_at')]
    content = f'<h3>{prose(qualification)} · {prose(heading)}</h3><p>Reported {prose(", ".join(dates) or "date not established")}. These values describe the reported population and period. They do not establish current activity outside that window.</p>'
    content += table(summary, [('Measure', 'label'), ('Value', 'value'), ('Unit', 'unit')])
    rest = [m for m in metrics if m not in summary]
    if rest:
        content += '<details class="domain-evidence"><summary>Other reporting windows, applications and workload activity (' + str(len(rest)) + ' observations)</summary>' + table(rest, columns) + '</details>'
    return content


def _progress_timeline(result):
    progress = result.get('rollout_progress') or {}
    if not progress:
        return ''
    stages = []
    status_labels = {'complete': 'Reached', 'current': 'Current stage', 'pending': 'Next milestone'}
    for index, stage in enumerate(progress.get('stages', []), 1):
        state = stage.get('status', 'pending')
        current = ' aria-current="step"' if state == 'current' else ''
        stages.append(f'<li data-state="{slug(state)}"{current}><span class="stage-number">{index:02d}</span> <span class="stage-status">{prose(status_labels.get(state, state))}</span><strong>{prose(stage.get("label"))}</strong></li>')
    return '<div class="rollout-track"><ol aria-label="Readiness stages">' + ''.join(stages) + '</ol><p><a href="#readiness-requirements">See the requirements for the next stage</a></p></div>'


def _progress_requirements(result):
    progress = result.get('rollout_progress') or {}
    if not progress:
        return ''
    numbers = {row.get('RecommendationId'): n for n, row in enumerate(result.get('actions', []), 1)}
    stages = []
    labels = {'met': 'Met', 'open': 'Evidence needed', 'issue': 'Action required', 'condition': 'Pilot condition'}
    for stage in progress.get('stages', []):
        items = []
        for requirement in stage.get('requirements', []):
            state = requirement.get('status', 'open')
            links = ', '.join(f'<a href="#action-{numbers[key]}">Action {numbers[key]}</a>'
                              for key in requirement.get('action_ids', []) if key in numbers)
            items.append(f'<li><div class="requirement-heading"><strong>{prose(requirement.get("title"))}</strong><span class="requirement-status" data-state="{slug(state)}">{prose(labels.get(state, state))}</span></div><p>{prose(requirement.get("reason"))}</p>'
                         + (f'<p class="qualification">Responsible role: {prose(requirement["owner_role"])}</p>' if requirement.get('owner_role') else '')
                         + (f'<p>{links}</p>' if links else '') + '</li>')
        stages.append(f'<details class="domain-evidence stage-requirements"><summary>{prose(stage.get("label"))}: requirements and evidence</summary><ul class="requirement-list">{"".join(items)}</ul></details>')
    counts = progress.get('counts') or {}
    totals = (f'<p class="control-totals"><strong>{counts["required_controls"]} required controls:</strong> '
              f'{counts.get("established_controls", 0)} established · {counts.get("observed_issues", 0)} with observed issues · '
              f'{counts.get("unconfirmed_controls", 0)} unconfirmed.</p><p class="qualification">Several actions can relate to one control check.</p>') if 'required_controls' in counts else ''
    return f'''<section id="readiness-requirements"><div class="section-heading"><span class="section-index">→</span><div><div class="eyebrow">HOW TO MOVE FORWARD</div><h2>What each readiness stage requires</h2></div></div>
      <p>Current stage: <strong>{prose(progress.get('current_stage'))}</strong>. These stages describe readiness supported by the assessment. Existing Copilot use is shown separately in the adoption section.</p>
      <p class="section-description">{prose(progress.get('qualification'))}</p>{totals}{''.join(stages)}
      <details class="domain-evidence"><summary>How to record an owner review</summary><div class="guide-body"><p>Return a dated review identifying the pilot population, responsible role, result, supporting evidence and any conditions. The assessment operator records these in the assessment profile and rebuilds the report. A documented review can establish an unanswered check; an observed failure still needs remediation evidence.</p><p>For broader adoption, record the pilot outcomes, sponsor approval and control coverage for the larger population. Usage counts alone do not establish approval to expand.</p></div></details>
      <p class="qualification">These are this assessment’s criteria. Microsoft recommends a phased rollout with a defined strategy, protected data, a small initial group and a review of outcomes before expansion. <a href="https://learn.microsoft.com/en-us/microsoft-365/copilot/microsoft-365-copilot-minimum-requirements-rollout">Microsoft rollout guidance</a></p></section>'''


def _authentication_methods(bundle):
    data = bundle.get('authentication_methods') or {}
    if not data.get('available'):
        return '<div class="mfa-profile"><h3>MFA method strength and defaults</h3><p>Registered methods and preferred second-factor methods were not available in this evidence. Collect the Entra authentication registration report to assess them.</p></div>'
    metrics = data.get('metrics') or {}
    total = data['total_users']
    def count(key, known):
        return str(metrics.get(key, 0)) if metrics.get(known, 0) else 'Unknown'
    cards = ''.join(f'<div class="mfa-stat"><strong>{prose(value)}</strong><span>{prose(label)}</span></div>' for value, label in (
        (count('phishing_resistant_registered', 'resistant_inventory_known'), 'Phishing-resistant method registered'),
        (count('phone_only_mfa_registered', 'phone_only_inventory_known'), 'Phone-only MFA registration'),
        (count('phone_preferred', 'phone_preference_known'), 'SMS / voice preferred in report'),
    ))
    date_note = ('Source report updated ' + prose(data['updated_from']) + ' to ' + prose(data['updated_to'])) if data.get('updated_from') else 'Source report update dates unavailable'
    missing_methods = total - metrics.get('method_inventory_known', 0)
    missing_preference = total - metrics.get('current_preference_known', 0)
    review_note = f"{metrics.get('methods_need_review', 0)} users have methods needing classification review. "
    if metrics.get('current_preference_needs_review'):
        review_note += f"{metrics['current_preference_needs_review']} users have preferred methods needing classification review. "
    if data.get('conflicting_users'):
        review_note += f"{data['conflicting_users']} users have conflicting source rows and remain unknown. "
    population_columns = [('Population', 'Population'), ('Users', 'Users'),
        ('Phishing-resistant registered', 'Phishing-resistant registered'),
        ('Phone-only MFA', 'Phone-only MFA registration'), ('SMS / voice preferred', 'SMS/voice preferred')]
    for label, field in [('Methods unknown', 'Method inventories unknown'),
                         ('Phishing resistance unknown', 'Phishing-resistant classification unknown'),
                         ('Phone-only status unknown', 'Phone-only classification unknown'), ('Preferences unknown', 'Preferences unknown')]:
        if any(row[field] for row in data['population_rows']):
            population_columns.append((label, field))
    return f'''<div class="mfa-profile" id="mfa-method-strength"><h3>MFA method strength and defaults</h3>
      <p>{total} users in the returned registration report. Source coverage: {prose(data['source_state'])}. {date_note}; {data.get('dates_unknown', 0)} update dates unavailable.</p>
      <div class="mfa-stats">{cards}</div>
      <p class="qualification">Counts describe returned users and can overlap. {missing_methods} method inventories and {missing_preference} current preferences are unknown. {prose(review_note)} Registration does not prove enforcement or actual sign-in use.</p>
      <p><strong>Where to focus.</strong> Prioritize administrators and users whose only reported reusable MFA method is a phone. Move SMS/voice preferences to stronger methods; use passkeys or Windows Hello where supported. Authenticator push and codes remain susceptible to phishing. Confirm Conditional Access authentication strengths for the intended users before retiring phone fallback.</p>
      <details class="domain-evidence"><summary>Registered methods and strength</summary><p>Email is shown for recovery or guest sign-in and is not counted as workforce MFA. Phone registration does not identify whether SMS or voice is used. Certificate-based MFA requires separate configuration verification.</p>
      {table(data['method_rows'], [('Registered method', 'Method'), ('Users', 'Registered users'), ('% of known inventories', 'Percent of known inventories'), ('Strength / purpose', 'Strength / purpose')], mobile_labels=True)}</details>
      <details class="domain-evidence"><summary>Default and system-preferred methods</summary><p>System preference takes precedence when enabled. User-selected defaults may therefore differ from the preferred route in this report. A user can have multiple system-preferred methods; rows can overlap. These second-factor fields do not list every passwordless sign-in option or establish which method was used.</p>
      {table(data['preference_rows'], [('Method', 'Method'), ('User-selected', 'User-selected users'), ('System-preferred (enabled)', 'System-preferred users'), ('Preferred in report', 'Preferred users in report')], mobile_labels=True)}</details>
      <details class="domain-evidence"><summary>Members, guests and administrators</summary><p>Administrators overlap members and guests. Missing administrator flags: {total - metrics.get('admin_known', 0)}. Counts for partial fields cover only known values. These counts do not assess authentication in a guest’s home tenant.</p>
      {table(data['population_rows'], population_columns, mobile_labels=True)}</details>
      <p class="qualification">See Authentication Coverage, Authentication Methods, MFA Preferences and MFA Populations in the workbook for counts and qualifications. Method registration alone does not approve a rollout stage.</p></div>'''


def _portal_captures(bundle, domain_id):
    """Display reviewed portal captures alongside their topic, without scoring screenshots."""
    captures = [row for row in (bundle.get('portal_review') or {}).get('captures', []) if row.get('domain_id') == domain_id]
    panels = []
    for capture in captures:
        limitations = capture.get('limitations') or ''
        if isinstance(limitations, list):
            limitations = ' '.join(str(item) for item in limitations)
        images = []
        for index, preview in enumerate(capture.get('previews') or [], 1):
            uri = str(preview.get('data_uri') or '')
            if not re.fullmatch(r'data:image/(?:png|jpeg);base64,[A-Za-z0-9+/=\r\n]+', uri):
                continue
            alt = escape(f'{capture.get("title", "Admin center capture")}, page {index}', quote=True)
            images.append(f'<figure><img src="{uri}" alt="{alt}"><figcaption>Page {index} · {prose(capture.get("source_name") or capture.get("source_file"))}</figcaption></figure>')
        pdf = str(capture.get('source_data_uri') or '')
        download = (f'<a class="button button-secondary" href="{pdf}" download="{escape(str(capture.get("source_name") or "portal-capture.pdf"), quote=True)}">Download original PDF</a>'
                    if re.fullmatch(r'data:application/pdf;base64,[A-Za-z0-9+/=\r\n]+', pdf) else '')
        notes = ''.join('<li>' + prose(note) + '</li>' for note in capture.get('review_notes') or [])
        review_label = 'automatically extracted PDF' if capture.get('review_method') == 'automated' else 'reviewed screenshots'
        extracted = ''.join(f'<h4>Page {page["page"]} · {prose(page["method"])}</h4><pre class="pdf-extracted-text">{escape(page["text"])}</pre>' for page in capture.get('extracted_pages', []))
        if extracted:
            extracted = '<details class="domain-evidence"><summary>Extracted text — check against the page previews</summary>' + extracted + '</details>'
        captured = 'Captured ' + prose(capture['captured_at']) if capture.get('captured_at') else 'Capture date unavailable'
        notes_label = '<p><strong>Source excerpts</strong> (reading order may differ from the page layout):</p>' if notes and capture.get('review_method') == 'automated' else ''
        panels.append(f'''<details class="domain-evidence portal-capture" id="portal-{slug(capture.get('id'))}"><summary>{prose(capture.get('title'))} · {review_label}</summary><div class="guide-body">
          <p class="qualification">{captured}{(' · Report refreshed ' + prose(capture.get('report_date'))) if capture.get('report_date') else ''}. Visual evidence supplied for review.</p>
          <p><strong>Portal context.</strong> {prose(capture.get('summary'))}</p>{notes_label}<ul>{notes}</ul>
          <p class="qualification portal-limit">{prose(limitations)}</p>
          {table(capture.get('coverage') or [], [('Portal topic', 'area'), ('What this assessment covers', 'assessment_coverage'), ('Follow-up', 'next_step')], mobile_labels=True)}{extracted}
          <details class="domain-evidence portal-pages"><summary>View the captured pages ({len(images)})</summary>{download}<div class="portal-image-grid">{''.join(images)}</div></details></div></details>''')
    return ''.join(panels)


def _collected_admin_context(bundle, area):
    context = bundle.get('copilot_admin_review') or {}
    if not context:
        return ''
    rows = [row for row in context.get('rows', []) if row.get('Area') == area]
    dlp = context.get('dlp') or {}
    if area == 'adoption':
        rows = [row for row in rows if 'prompts' in row['Topic'] or 'user-days' in row['Topic']]
    if area == 'data_protection' and dlp.get('rows'):
        rows = [row for row in rows if row['Topic'] == 'Unified audit logging']
    if not rows and not (area == 'data_protection' and dlp.get('rows')):
        return ''
    display_rows = [{**row, 'Value': row.get('Value') if row.get('Value') is not None else 'Not established'} for row in rows]
    content = table(display_rows, [('Collected item', 'Topic'), ('Observed value', 'Value'), ('Evidence date', 'Evidence date'), ('Scope and follow-up', 'Follow-up')], mobile_labels=True)
    if area == 'data_protection' and dlp.get('rows'):
        content += '<h3>Copilot-specific DLP policies</h3><p>Modes and configured actions come from the policy and rule data. Names alone do not prove targeting. Review scope, conditions and exclusions before relying on a policy for the pilot.</p>'
        content += table(dlp['rows'], [('Policy', 'Policy'), ('Mode', 'Mode'), ('Evidence date', 'Evidence date'), ('Target evidence', 'Target evidence'), ('Scope', 'Scope'), ('Configured action', 'Action')], mobile_labels=True)
    return '<details class="domain-evidence admin-review"><summary>Collected Copilot configuration and activity</summary><div class="guide-body"><p>Available values come from collected data and do not require a PDF export. Missing details include the next step. ' + prose(context.get('qualification')) + '</p>' + content + '</div></details>'


def _remaining_portal_review(bundle):
    rows = (bundle.get('copilot_admin_review') or {}).get('manual_checks') or []
    if not rows:
        return ''
    return '<details class="domain-evidence admin-review"><summary>Portal details that still need a review</summary><div class="guide-body"><p>Review these items when relevant to the agreed scope. The tool does not request a full PDF for data already collected. An unavailable optional dashboard metric does not add a readiness failure.</p>' + table(rows, [('Portal detail', 'Topic'), ('Why it is separate', 'Reason'), ('When to request it', 'When needed')], mobile_labels=True) + '</div></details>'


def render_customer_report(result, bundle, tenant_name, workbook_path=None):
    actions = result['actions']
    domains = result['domains']
    counts = result['counts']
    workbook_name = Path(workbook_path).name if workbook_path else ''
    workbook_url = quote(workbook_name) if workbook_name else ''
    def evidence_link(row):
        key = row.get('RecommendationId') or row.get('FindingKey') or row.get('Feature')
        return f'<a href="#evidence-{slug(key)}">Evidence and qualifications</a>'
    def record(row, with_action=False):
        description = _observation(row, bundle)
        support = row.get('Evidence') if row.get('Evidence') != description else ''
        qualification = row.get('Qualification') or row.get('qualification') or ''
        status = row.get('ActionType') or row.get('EvidenceStatus') or row.get('Disposition')
        status = {'supported': 'Observed', 'limited': 'Limited coverage', 'gap': 'Evidence needed', 'stale': 'Older evidence'}.get(status, status)
        return f'''<article class="finding"><div class="eyebrow">{prose(status)} · {prose(_date_text(row))}</div>
          <h4>{prose(_heading(row))}</h4><p>{prose(description)}</p>{('<p>' + prose(support) + '</p>') if support else ''}
          {('<p><strong>What to do.</strong> ' + prose(_action_text(row)) + '</p>') if with_action else ''}
          {('<p class="qualification">' + prose(qualification) + '</p>') if qualification else ''}
          <p class="evidence-link">{evidence_link(row)}</p></article>'''

    action_html = []
    for index, row in enumerate(actions, 1):
        guide = guide_for(row)
        guidance_link = (f'<p class="evidence-link"><a href="#report-guide-{guide}">How to obtain the reports</a> · <a href="#report-handoff">How to return the evidence</a></p>' if guide else '')
        action_html.append(f'''<article class="action" data-priority="{slug(row.get('Priority'))}" id="action-{index}"><div class="action-number">{index:02d}</div><details class="action-detail"{' open' if index <= 3 else ''}><summary>
          <div class="action-tags"><span class="priority-badge" data-priority="{slug(row.get('Priority'))}">{prose(row.get('Priority') or 'Review')} priority</span><span class="action-kind">{prose(row.get('ActionType'))}</span><span class="action-domain">{prose(row.get('Domain'))}</span></div>
          <h3>{prose(_heading(row))}</h3></summary><div class="action-body"><p><strong>What we found.</strong> {prose(_observation(row, bundle))}</p>
          <p><strong>What to do.</strong> {prose(_action_text(row))}</p>
          <div class="action-meta"><p><strong>Responsible role</strong><br>{prose(row.get('OwnerRole'))}</p>
          <p><strong>Rollout stage</strong><br>{prose(row.get('ReadinessStage'))}</p></div>
          <p><strong>Evidence of completion.</strong> {prose(_completion(row))}</p>{guidance_link}
          <p class="qualification">{prose(_date_text(row))}{(' · ' + prose(row.get('Qualification'))) if row.get('Qualification') else ''}</p>
          {evidence_link(row)}</div></details></article>''')

    domain_html = []
    action_numbers = {r.get('RecommendationId'): i for i, r in enumerate(actions, 1)}
    for domain_index, domain in enumerate(domains, 1):
        rows = domain.get('customer_findings', domain.get('findings')) or []
        # Findings are already selected and qualified by the shared model.
        findings_html = '<details class="domain-evidence"><summary>Evidence observations and qualifications (' + str(len(rows)) + ')</summary>' + ''.join(record(row) for row in rows) + '</details>' if rows else ''
        links = [f'<a href="#action-{action_numbers[r.get("RecommendationId")]}">Action {action_numbers[r.get("RecommendationId")]}</a>'
                 for r in domain.get('actions', []) if r.get('RecommendationId') in action_numbers]
        extra = ''
        if domain['id'] == 'identity':
            extra = _authentication_methods(bundle)
        if domain['id'] == 'content':
            extra = _settings(bundle) + _lifecycle(bundle)
        if domain['id'] == 'licensing':
            extra = _readiness_export(bundle) + _license_context(bundle)
        if domain['id'] in {'agents', 'external_ai'}:
            extra = _scoped_products(bundle)
        if domain['id'] == 'adoption':
            extra = _adoption_report(result)
            extra += '<h3>What the pilot should prove</h3><p>Select a small group with a defined work task, accountable content owners, and approved access. Agree how the group will judge output quality and time saved.</p><h3>How to decide whether to expand</h3><p>Review actual Copilot activity, task quality, and user feedback before expanding. Familiarity with Teams, email, or Office can guide pilot selection; it does not measure Copilot use.</p>'
        extra += _collected_admin_context(bundle, domain['id'])
        extra += _portal_captures(bundle, domain['id'])
        summary = re.sub(r'(?<![.!?]) (?=No legacy authentication sign-ins)', '. ', str(domain.get('summary') or ''))
        domain_html.append(f'''<section class="domain" data-state="{slug(domain.get('status'))}" id="domain-{escape(domain['id'])}"><div class="section-title"><div class="domain-heading"><span class="domain-index">{domain_index:02d}</span><h2>{prose(domain['title'])}</h2></div><span class="badge" data-state="{slug(domain.get('status'))}">{prose(domain.get('status'))}</span></div>
          <p class="lead">{prose(summary)}</p><p><strong>Why it matters.</strong> {prose(domain.get('why_it_matters'))}</p>
          {findings_html}{extra}<div class="next"><strong>What to do.</strong> {', '.join(links) if links else 'Maintain the observed controls and confirm their scope before the next rollout stage.'}</div></section>''')

    strength_rows = result.get('strengths') or []
    strengths = ''.join(f'<li>{prose(r.get("Observation") or r.get("Strength"))} <span class="muted">({prose(_date_text(r))})</span></li>' for r in strength_rows[:4])
    if not strengths:
        strengths = '<li>No current safeguard has enough dated, scoped evidence to be described as verified. Dated positive observations remain in the relevant assessment areas.</li>'
    concerns = [r for r in actions if r.get('ActionType') != 'Evidence'][:3] or actions[:3]
    first_actions = ''.join(f'<li><a href="#action-{action_numbers.get(r.get("RecommendationId"), 1)}">{prose(_heading(r))}</a></li>' for r in concerns)
    cards = ''.join(f'<div class="stat" data-kind="{key}"><div class="value">{counts.get(key, 0)}</div><div class="stat-label">{label}</div><p class="stat-note">{note}</p></div>' for key, label, note in (
        ('remediation', 'Remediation actions', 'Address observed conditions'),
        ('confirmation', 'Findings to confirm', 'Check current status and scope'),
        ('evidence_gaps', 'Evidence checks', 'Close unanswered questions'),
        ('strengths', 'Verified strengths', 'Maintain supported safeguards')))
    chart_rows = []
    maximum = max([d.get('action_count', 0) for d in domains] + [1])
    for domain in domains:
        breakdown = {kind: sum(row.get('ActionType') == action_type for row in domain.get('actions', []))
                     for kind, action_type in (('remediation', 'Remediation'), ('confirmation', 'Confirmation'), ('evidence_gaps', 'Evidence'))}
        segments = ''.join(f'<span class="bar-segment" data-kind="{kind}" style="width:{100 * value / maximum:.2f}%"></span>'
                           for kind, value in breakdown.items() if value)
        description = f"{breakdown['remediation']} remediation, {breakdown['confirmation']} confirmation, {breakdown['evidence_gaps']} evidence checks"
        chart_rows.append(f'''<div class="bar-row"><div><a href="#domain-{escape(domain['id'])}">{prose(domain['title'])}</a><span class="bar-status">{prose(domain.get('status'))}</span></div>
          <span class="bar-track" aria-hidden="true">{segments}</span><b>{domain.get('action_count', 0)}<span class="sr-only"> actions: {description}</span></b></div>''')
    chart = ''.join(chart_rows)

    all_records = result.get('recommendations') or []
    evidence_rows = []
    for row in all_records:
        key = row.get('RecommendationId') or row.get('FindingKey') or row.get('Feature')
        tabs = row.get('EvidenceSheet') or 'Evidence Index'
        evidence_rows.append(f'<tr id="evidence-{slug(key)}"><td>{prose(key)}</td><td>{prose(row.get("OriginalFeature") or row.get("Feature"))}</td><td>{prose(_date_text(row))}</td><td>{prose(row.get("EvidenceBasis"))}{("<br>" + prose(row.get("Qualification"))) if row.get("Qualification") else ""}</td><td>{prose(tabs)}</td></tr>')
    # Some curated strengths arrive independently of the recommendation register.
    seen_ids = {r.get('RecommendationId') or r.get('FindingKey') or r.get('Feature') for r in all_records}
    for row in strength_rows + (result.get('historical_strengths') or []):
        key = row.get('RecommendationId') or row.get('FindingKey') or row.get('Feature')
        if key not in seen_ids:
            seen_ids.add(key)
            evidence_rows.append(f'<tr id="evidence-{slug(key)}"><td>{prose(key)}</td><td>{prose(_heading(row))}</td><td>{prose(_date_text(row))}</td><td>{prose(row.get("Qualification"))}</td><td>Evidence Index</td></tr>')
    source_rows = [r for source in (bundle.get('data_exposure') or {}).get('sources', {}).values() for r in source.get('reports', [])]
    sources = table(source_rows, [('Source file', 'source_file'), ('Report type', 'report_type'), ('Workload', 'workload'), ('Original date', 'report_date'), ('Date basis', 'date_basis'), ('Selection', 'status'), ('Rows', 'records_read'), ('Freshness', 'freshness')])
    collection_rows = []
    for name, state in (bundle.get('source_statuses') or {}).items():
        if name.startswith('connection_') or not isinstance(state, dict):
            continue
        status = state.get('availability_status') or state.get('status') or ('available' if state.get('available') else 'unavailable')
        collection_rows.append({'source': name, 'status': status,
                                'records': state.get('records_collected', state.get('record_count')),
                                'reason': state.get('reason') or state.get('error') or ('Read succeeded.' if status == 'available' else 'No additional detail recorded.')})
    collection_table = table(collection_rows, [('Source', 'source'), ('Read status', 'status'), ('Rows returned', 'records'), ('Reason or qualification', 'reason')]) if collection_rows else ''
    context = bundle.get('collection_context') or {}
    migration = context.get('methodology_migration') or {}
    migration_note = (
        '<p>Saved collection methodology: ' + prose(migration.get('from'))
        + '. Reassessed using methodology ' + prose(migration.get('to'))
        + '. ' + prose(migration.get('reason'))
        + ' Original collection evidence and dates are preserved.</p>'
    ) if migration else ''
    prior = bundle.get('prior_report') or {}
    report_guides = _report_guides(actions) + _remaining_portal_review(bundle)
    operator_handoff = '''<h3>Rebuild with additional exports</h3><p>From the assessment repository, replace both placeholder paths with the saved collection and the folder containing this tenant’s completed exports. The collection can be the portable package’s collection.json. Existing packaged evidence and settings are restored automatically.</p>
      <pre class="operator-command"><code>.\\.venv\\Scripts\\python.exe main.py --mode offline --collection-input "&lt;saved collection.json&gt;" --reports-dir "&lt;exports folder&gt;"</code></pre>
      <p>Check the imported source reports and remaining evidence in the rebuilt report. A successful rebuild does not mean every export format or evidence requirement was satisfied.</p>''' if report_guides else ''
    technical = f'''<details class="appendix-panel" id="engineer-appendix"><summary>Technical appendix and evidence workbook</summary>
      <p>{('<a href="' + workbook_url + '">Download ' + escape(workbook_name) + '</a>') if workbook_url else 'The workbook contains the complete evidence register.'}</p>
      <p>Evaluation date: {prose(result.get('evaluation_date'))}. Methodology: {prose(result.get('methodology_version'))}. Evidence schema: {prose(result.get('evidence_schema_version'))}.</p>
      <p>Original tenant collection: {prose(context.get('collected_at'))}. Report execution: {prose(context.get('mode') or 'Evidence supplied directly')}.</p>
      <p>Collection permission profile: {prose(context.get('permission_profile') or 'unrecorded')}.</p>
      {migration_note}
      {operator_handoff}
      {table(context.get('historical_sources') or [], [('Source file', 'source_file'), ('Source type', 'source_type'), ('Original date', 'reported_at')]) if context.get('historical_sources') else ''}
      {('<p>Historical workbook: ' + prose(prior.get('source_file')) + '. Original report date: <strong>' + prose(prior.get('generated_at')) + '</strong>. The complete original register and source mapping are preserved in the Prior workbook tabs.</p>') if prior else ''}
      {('<h3>Source collection results</h3><p>These are the source states retained with the tenant collection. An unsuccessful read cannot establish a zero tenant count.</p>' + collection_table) if collection_rows else ''}
      <h3>Imported source reports and dates</h3>{sources}<h3>Evidence references</h3>
      <div class="table-scroll"><table><thead><tr><th>Reference</th><th>Finding</th><th>Original date</th><th>Evidence and qualifications</th><th>Workbook tab</th></tr></thead><tbody>{''.join(evidence_rows)}</tbody></table></div></details>'''

    gaps = result.get('coverage') or []
    gap_items = []
    for row in gaps:
        number = action_numbers.get(row.get('RecommendationId'))
        link = f'<a href="#action-{number}">Open action {number}</a>' if number else evidence_link(row)
        gap_items.append(f'<li><strong>{prose(_heading(row))}.</strong> {prose(_completion(row))} <span class="muted">Responsible role: {prose(row.get("OwnerRole"))}.</span> {link}</li>')
    gap_html = ''.join(gap_items)
    gap_intro = ('Evidence is incomplete for the assessed scope. Each item below states what the responsible owner should return. Expand the report guides for portal steps; other checks require an owner’s review or decision.' if gaps
                 else 'No required evidence checks remain open for the assessed scope. The deployment decision also considers the findings and actions above.')
    scope = 'Microsoft 365 Copilot'
    if any(d['id'] == 'agents' for d in domains):
        scope += '; agents'
    if any(d['id'] == 'external_ai' for d in domains):
        scope += '; external AI'
    period = result.get('evidence_period') or {}
    if isinstance(period, dict):
        start, end = period.get('start'), period.get('end')
        period_text = f'{start} to {end}' if start and end and start != end else (start or end or 'Source dates appear beside the findings')
    else:
        period_text = str(period)
    decision = result['decision']
    progress = result.get('rollout_progress') or {}
    headline = progress.get('current_stage') or decision
    decision_badge = ('Assessment status: ' + decision) if progress else 'Assessment outcome'
    observed_use = any(row.get('metric_id') == 'copilot.active_users' and isinstance(row.get('value'), (int, float))
                       and row['value'] > 0 for row in result.get('adoption_metrics', []))
    adoption_note = ('<p class="qualification">The supplied reports show existing Copilot use. This stage identifies what is verified for the next rollout decision.</p>' if progress and observed_use else '')
    portal_count = len((bundle.get('portal_review') or {}).get('captures') or [])
    portal_links = ' · '.join(f'<a href="#portal-{slug(capture.get("id"))}">{prose(capture.get("title"))}</a>'
                            for capture in (bundle.get('portal_review') or {}).get('captures', [])
                            if capture.get('domain_id') in {domain['id'] for domain in domains})
    portal_intro = ('<p class="qualification">Admin-center captures: ' + portal_links + '</p>') if portal_count else ''
    decision_state = 'ready' if decision in {'Ready for a controlled pilot', 'Ready for broader adoption'} else 'blocked' if decision == 'Not ready for pilot' else 'unknown'
    workbook_button = f'<a class="button button-primary" href="{workbook_url}">Evidence workbook <span aria-hidden="true">↗</span></a>' if workbook_url else ''
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(str(tenant_name or 'Tenant'))} — Copilot readiness</title><style>{REPORT_CSS}</style></head><body>
      <a class="skip-link" href="#executive">Skip to assessment</a>
      <header class="report-header"><div class="brand"><span class="brand-mark" aria-hidden="true"><span></span><span></span><span></span><span></span></span><div>MICROSOFT 365 COPILOT<span class="brand-subtitle">Readiness assessment</span></div></div><div class="header-meta"><strong>{escape(str(tenant_name or 'Tenant assessment'))}</strong><span>Executive briefing · {prose(result.get('evaluation_date'))}</span></div></header>
      <nav class="report-nav" aria-label="Report sections"><div class="nav-links"><a href="#executive">Overview</a><a href="#action-plan">Action plan</a><a href="#readiness-domains">Areas</a><a href="#rollout">Rollout</a><a href="#remaining-evidence">Open checks</a><a href="#engineer-appendix">Evidence</a></div><div class="nav-tools">{workbook_button}<button class="button button-secondary" id="print-report" type="button" hidden>Print report</button></div></nav>
      <main><section class="executive" id="executive"><div class="hero-grid"><div class="hero-main"><div class="hero-kicker">YOUR ROLLOUT READINESS</div><div class="decision-label" data-state="{decision_state}">{prose(decision_badge)}</div>
      <h1>{prose(headline)}</h1><p class="hero-copy">{prose(result.get('rationale'))}</p>{adoption_note}</div><aside class="hero-aside" aria-label="Assessment scope and dates"><div class="hero-kicker">ASSESSMENT SCOPE</div><h2>{prose(scope)}</h2><div class="hero-meta"><dl><div><dt>Evaluation date</dt><dd>{prose(result.get('evaluation_date'))}</dd></div><div><dt>Evidence period</dt><dd>{prose(period_text)}</dd></div><div><dt>Open actions</dt><dd>{len(actions)} to resolve or verify</dd></div></dl></div></aside></div>
      {_progress_timeline(result)}<div class="stats">{cards}</div><div class="executive-brief two-column"><div class="brief-card"><div class="eyebrow">WHERE TO START</div><h2>First actions</h2><ol>{first_actions or '<li>Confirm the pilot scope and maintain the assessed controls.</li>'}</ol></div><div class="brief-card strengths-card"><div class="eyebrow">WHAT IS WORKING</div><h2>Observed strengths</h2><ul>{strengths}</ul></div></div></section>{_progress_requirements(result)}
      <section id="action-plan"><div class="section-title"><div class="section-heading"><span class="section-index">01</span><div><div class="eyebrow">PRIORITIES AND ACCOUNTABILITY</div><h2>Prioritized action plan</h2></div></div><span class="badge">{len(actions)} actions</span></div><p class="section-description">Start with the highest-priority conditions. Remediation actions address observed issues. Confirmation actions check whether earlier or limited findings still apply. Evidence checks need a report, validation or owner decision.</p>{''.join(action_html) or '<p>No outstanding actions were identified.</p>'}</section>
      <section id="readiness-domains"><div class="section-heading"><span class="section-index">02</span><div><div class="eyebrow">THE ASSESSMENT AT A GLANCE</div><h2>Readiness by assessment area</h2></div></div><p class="section-description">Open actions by assessment area. Counts reflect the action plan and workbook; they describe the work remaining.</p>{portal_intro}<div class="chart-legend"><span><i class="legend-dot" data-kind="remediation" aria-hidden="true"></i>Remediation</span><span><i class="legend-dot" data-kind="confirmation" aria-hidden="true"></i>Confirmation</span><span><i class="legend-dot" data-kind="evidence_gaps" aria-hidden="true"></i>Evidence checks</span></div><div class="chart" aria-label="Actions by assessment area">{chart}</div></section>{''.join(domain_html)}
      <section id="rollout"><div class="section-heading"><span class="section-index">03</span><div><div class="eyebrow">FROM ASSESSMENT TO PILOT</div><h2>Conditions for the next rollout stage</h2></div></div><div class="phase-grid"><div class="phase"><span class="phase-number">1</span><h3>Prepare the pilot</h3><p>Define the pilot users, work tasks, permitted data, and accountable owners. Validate licensing and application prerequisites for those users.</p></div><div class="phase"><span class="phase-number">2</span><h3>Before enabling access</h3><p>Resolve critical safeguards and the evidence checks that affect the pilot population. Confirm unresolved historical findings and record the security owner's decision against the action plan.</p></div><div class="phase"><span class="phase-number">3</span><h3>Before expanding</h3><p>Close the high-priority conditions, verify access to the intended content, and demonstrate that the agreed controls cover the larger group. Review actual Copilot usage and task outcomes with the business sponsor.</p></div></div><p class="qualification">Adoption opportunities guide the value of a pilot. They do not establish that security controls are effective.</p></section>
      <section id="remaining-evidence"><div class="section-heading"><span class="section-index">04</span><div><div class="eyebrow">COMPLETE THE PICTURE</div><h2>Remaining evidence and decisions</h2></div></div><p class="section-description">{prose(gap_intro)}</p><ul class="gap-list">{gap_html or '<li>No required evidence gaps remain for the assessed scope.</li>'}</ul>{report_guides}</section>{technical}</main>
      <footer class="report-footer"><strong>Microsoft 365 Copilot readiness</strong><span>{escape(str(tenant_name or 'Tenant'))} · Evaluation {prose(result.get('evaluation_date'))}</span></footer><script>{REPORT_SCRIPT}</script></body></html>'''

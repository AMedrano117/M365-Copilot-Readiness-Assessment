"""Compact presentation of the shared assessment, without new scoring."""

from collections import Counter
from html import escape
import re


SHORT_DOMAINS = {
    'scope':'Scope & licensing', 'identity':'Identity & access',
    'devices':'Devices & compliance', 'defender':'Defender & operations',
    'content':'Content & sharing', 'classification':'Labels & DLP',
    'governance':'Audit & compliance', 'applications':'Apps & consent',
    'external_ai':'External AI',
}


def short_text(value, limit=190):
    """One complete sentence where possible; the full text stays in details."""
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
    first = sentences[0] if sentences else text
    return first if len(first) <= limit else first[:limit-1].rsplit(' ',1)[0] + '…'


def baseline_panel(result):
    controls = [row for row in result.get('controls',[]) if row.get('security_gate')]
    if not controls:
        return ''
    states = {'Observed':('pass','Established'), 'Action required':('fail','Issues found'),
              'Not established':('unknown','Unresolved')}
    counts = Counter(row.get('status') for row in controls)
    marks = ''.join('<span class="control-mark" data-state="' + states.get(row.get('status'),states['Not established'])[0]
                    + '" title="' + escape(str(row.get('title','')) + ': ' + str(row.get('status','')),quote=True)
                    + '"></span>' for row in controls)
    legend = ''.join(f'<span><i class="control-mark" data-state="{key}"></i><b>{counts.get(status,0)}</b> {label}</span>'
                     for status,(key,label) in states.items())
    return f'<div class="baseline-panel"><div><div class="eyebrow">TENANT-WIDE FOUNDATION</div><h2>{len(controls)} required controls</h2><p>Configuration and required operational proof are assessed together. One control can have several findings.</p></div><div><div class="control-marks" aria-label="Required control results">{marks}</div><div class="control-legend">{legend}</div></div></div>'


def domain_tiles(result):
    output = []
    for index,domain in enumerate(result.get('assessment_domains') or result.get('domains') or [],1):
        actions = domain.get('actions',[])
        remediations = sum(row.get('ActionType')=='Remediation' for row in actions)
        checks = sum(row.get('ActionType')!='Remediation' for row in actions)
        state = 'fail' if remediations else 'unknown' if domain.get('status') in {'Evidence required','Not established','Evidence collected'} else 'pass'
        # Collected source records never establish that all safeguards passed.
        status = 'Issues found' if remediations else 'Review needed' if state=='unknown' else domain.get('status','Review needed')
        identifier = domain['id']
        title = SHORT_DOMAINS.get(identifier,domain['title'])
        coverage = [row for row in result.get('domain_coverage',[]) if row['Domain']==domain['title']]
        retained = sum(bool(row.get('Evidence collected')) for row in coverage)
        note = f'{retained} of {len(coverage)} checks have source evidence' if coverage else domain.get('summary','')
        additional = '<p class="tile-review-note">Additional catalog reviews pending</p>' if not actions and state=='unknown' else ''
        output.append(f'<a class="domain-tile" data-state="{state}" href="#domain-{escape(identifier)}"><div class="tile-top"><span class="domain-index">{index:02d}</span><span class="tile-state">{escape(status)}</span></div><h3>{escape(title)}</h3><div class="tile-counts"><span><b>{remediations}</b> fixes</span><span><b>{checks}</b> action checks</span></div><p>{escape(note)}</p>{additional}<span class="tile-link">View findings <span aria-hidden="true">→</span></span></a>')
    return '<div class="domain-tiles">' + ''.join(output) + '</div>'


def coverage_panels(result, table):
    panels = []
    rows = result.get('domain_coverage',[])
    for domain in result.get('assessment_domains',[]):
        checks = [row for row in rows if row['Domain']==domain['title']]
        if not checks:
            continue
        states = Counter(row['State'] for row in checks)
        summary = ', '.join(f'{count} {state.replace("_"," ")}' for state,count in states.items())
        body = []
        for check in checks:
            # Each catalog entry is retained with all fields, inside its own
            # disclosure instead of an eleven-column wall of repeated prose.
            detail = [{'Field':key,'Detail':value} for key,value in check.items() if key not in {'Check ID','Check','Domain','State'}]
            body.append(f'<details class="check-detail"><summary><code>{escape(check["Check ID"])}</code> {escape(check["Check"])} <span class="badge">{escape(check["State"].replace("_"," "))}</span></summary>'
                        + table(detail,[('Evidence requirement','Field'),('Coverage and limitations','Detail')],mobile_labels=True) + '</details>')
        panels.append(f'<details class="coverage-domain" id="coverage-{escape(domain["id"])}"><summary><strong>{escape(domain["title"])}</strong><span>{len(checks)} checks · {escape(summary)}</span></summary>{"".join(body)}</details>')
    return '<section id="collection-coverage"><h2>Collection coverage</h2><p class="section-description">All nine domains and every catalog check. “Collected” describes available evidence; it does not mean the control passed. Expand a domain, then a check for failures, missing evidence and conclusion limits.</p>' + ''.join(panels) + '</section>'


def decision_groups(result, *, report_name=''):
    """Keep decisions distinct from confirmed failures and collection counts."""
    candidates = [row for row in result.get('domain_coverage',[]) if row.get('Missing evidence')
                  and ('context' in row['Missing evidence'] or 'review' in row['Missing evidence'])]
    groups = []
    for domain in result.get('assessment_domains',[]):
        checks = [row for row in candidates if row['Domain']==domain['title']]
        if not checks:
            continue
        items = ''.join('<li><code>'+escape(row['Check ID'])+'</code> '+escape(row['Check'])
                        + (' <span class="muted">(conditional on stated requirements)</span>' if row.get('Applicability')=='requirements-dependent' else '') + '</li>' for row in checks)
        groups.append(f'<details class="decision-group"><summary>{escape(SHORT_DOMAINS.get(domain["id"],domain["title"]))} <span class="muted">{len(checks)} owner decisions or reviews</span></summary><ul>{items}</ul></details>')
    return '<div class="decision-groups">' + ''.join(groups) + '</div>' if groups else '<p>No additional owner decisions are listed.</p>'

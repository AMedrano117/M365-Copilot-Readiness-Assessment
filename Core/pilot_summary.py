"""Short customer summary: is the tenant ready for a Copilot pilot, and what first.

Rendered only from the shared assessment result, like the full report; it adds
no scoring. The full report and the workbook keep the detail and evidence, and
each item here links to its action in the full report.
"""

from html import escape
from pathlib import Path
import re
from urllib.parse import quote

from .customer_report import _heading, _observation, prose, slug


VERDICTS = {
    "Not ready for pilot": ("Not ready for a pilot yet", "blocked"),
    "Readiness unconfirmed": ("Not enough evidence to decide yet", "unknown"),
    "Controlled pilot with conditions": ("Ready for a pilot, with conditions", "conditions"),
    "Pilot only — remediation required": ("Ready for a pilot, with conditions", "conditions"),
    "Ready for a controlled pilot": ("Ready for a pilot", "ready"),
    "Ready for broader adoption": ("Ready for broader adoption", "ready"),
}

PILOT_GROUP_GUIDANCE = (
    "Choose 20 to 50 people across two or three roles with clear, repeatable tasks, such as drafting, summarizing meetings or finding information.",
    "Prefer people with MFA registered, verified entitlement for the planned Copilot experience, and supported Microsoft 365 apps and connected experiences.",
    "Prefer teams whose content sits in sites with named owners, sensible permissions and sensitivity labels.",
    "Agree three to five success measures (for example time saved, output quality and weekly use) and a feedback channel before assigning licenses.",
    "Assign licenses through a group so the pilot is easy to track, extend or stop.",
)

SUMMARY_CSS = r'''
:root { --navy:#102b40; --teal:#006b70; --ink:#203849; --muted:#596d7b; --line:#dbe5eb; --canvas:#f3f6f8;
  --red:#ad3345; --red-pale:#fff0f1; --amber:#855a13; --amber-pale:#fff5df; --green:#216c50; --green-pale:#eaf6ef; }
* { box-sizing:border-box; }
body { margin:0; background:var(--canvas); color:var(--ink); font:15px/1.6 "Segoe UI", Arial, sans-serif; }
.page { max-width:900px; margin:0 auto; padding:28px 20px 40px; }
header { display:flex; justify-content:space-between; align-items:flex-end; gap:16px; flex-wrap:wrap; margin-bottom:18px; }
.brand { color:var(--navy); font-size:12px; font-weight:750; letter-spacing:1.1px; }
.brand span { display:block; color:var(--muted); font-weight:500; letter-spacing:.5px; }
.meta { color:var(--muted); font-size:13px; text-align:right; }
.meta strong { display:block; color:var(--navy); font-size:15px; }
section { background:#fff; border:1px solid var(--line); border-radius:12px; padding:22px 26px; margin-bottom:16px; }
.verdict { border-top:6px solid var(--muted); }
.verdict[data-state="blocked"] { border-top-color:var(--red); }
.verdict[data-state="conditions"], .verdict[data-state="unknown"] { border-top-color:#c49942; }
.verdict[data-state="ready"] { border-top-color:var(--green); }
.kicker { color:var(--muted); font-size:12px; font-weight:700; letter-spacing:1px; text-transform:uppercase; }
h1 { margin:4px 0 8px; color:var(--navy); font-size:30px; line-height:1.2; }
.verdict[data-state="blocked"] h1 { color:var(--red); }
.verdict[data-state="ready"] h1 { color:var(--green); }
h2 { margin:0 0 4px; color:var(--navy); font-size:19px; }
.intro { margin:0 0 12px; color:var(--muted); }
.scores { display:flex; flex-wrap:wrap; gap:10px; margin-top:14px; }
.score { flex:1 1 150px; border:1px solid var(--line); border-radius:10px; padding:10px 12px; }
.score b { display:block; font-size:24px; color:var(--navy); line-height:1.2; }
.score[data-kind="fail"] b { color:var(--red); } .score[data-kind="pass"] b { color:var(--green); } .score[data-kind="confirm"] b { color:var(--amber); }
.score span { color:var(--muted); font-size:13px; }
ol, ul { margin:8px 0 0; padding-left:22px; }
li { margin:0 0 10px; }
li strong { color:var(--navy); }
.found { display:block; color:var(--ink); }
.owner, .more { color:var(--muted); font-size:13px; }
.tag { display:inline-block; margin-left:6px; padding:0 7px; border-radius:6px; font-size:12px; font-weight:650; }
.tag[data-priority="high"], .tag[data-priority="critical"] { background:var(--red-pale); color:var(--red); }
.tag[data-priority="medium"] { background:var(--amber-pale); color:var(--amber); }
.passed li::marker { color:var(--green); }
.decision-group { border-top:1px solid var(--line); padding:10px 0; }
.decision-group summary { cursor:pointer; color:var(--navy); font-weight:600; }
.decision-group summary span { display:block; font-size:12px; font-weight:400; }
.decision-group ul { font-size:13px; }
footer { color:var(--muted); font-size:13px; }
footer p { margin:6px 0; }
a { color:var(--teal); }
@media print { body { background:#fff; font-size:12px; } .page { padding:0; } section { break-inside:avoid; padding:14px 18px; } a { color:inherit; } }
'''


def _first_sentence(text, limit=240):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    sentence = match.group(1) if match else text
    return sentence if len(sentence) <= limit else sentence[:limit - 1].rstrip() + "…"


def _link(report_name, number):
    if not report_name or not number:
        return ""
    return f' <a href="{quote(report_name)}#action-{number}">Details</a>'


def _action_items(rows, numbers, report_name, bundle, *, found=True, limit=8):
    items = []
    for row in rows[:limit]:
        number = numbers.get(row.get("RecommendationId"))
        priority = str(row.get("Priority") or "").lower()
        tag = f'<span class="tag" data-priority="{escape(priority)}">{escape(row.get("Priority") or "")}</span>' if priority else ""
        detail = (f'<span class="found">{prose(_first_sentence(_observation(row, bundle)))}</span>' if found else "")
        items.append(f'<li><strong>{prose(_heading(row))}</strong>{tag}{detail}'
                     f'<span class="owner">Owner: {prose(row.get("OwnerRole") or "Tenant administrator")}.</span>'
                     f'{_link(report_name, number)}</li>')
    return "".join(items)


def _more(rows, limit=8):
    more = len(rows) - limit
    return f'<p class="more">{more} more in the full report.</p>' if more > 0 else ''


def render_pilot_summary(result, bundle, tenant_name, report_path=None, workbook_path=None, technical_workbook_path=None):
    from .report_presentation import decision_groups
    actions = result.get("actions") or []
    numbers = {row.get("RecommendationId"): index for index, row in enumerate(actions, 1)}
    report_name = Path(report_path).name if report_path else ""
    workbook_name = Path(workbook_path).name if workbook_path else ""
    technical_name = Path(technical_workbook_path).name if technical_workbook_path else ""
    headline, state = VERDICTS.get(result.get("decision"), (result.get("decision") or "Assessment outcome", "unknown"))

    gates = [row for row in result.get("controls") or [] if row.get("security_gate")]
    passed = [row for row in gates if row.get("status") == "Observed"]
    failing = [row for row in gates if row.get("status") == "Action required"]
    unanswered = [row for row in gates if row.get("status") == "Not established"]

    blockers = [row for row in actions if row.get("PilotImpact") == "Blocks pilot"]
    confirm = [row for row in actions if row.get("PilotImpact") == "Confirm before pilot"]
    later = [row for row in actions if row.get("PilotImpact") == "Fix before broad rollout"]

    scores = "".join(f'<div class="score" data-kind="{kind}"><b>{value}</b><span>{label}</span></div>' for kind, value, label in (
        ("pass", len(passed), f"of {len(gates)} required checks passed"),
        ("fail", len(failing), "required checks failing"),
        ("confirm", len(unanswered), "checks awaiting sufficient evidence"),
    ))

    sections = []
    pdf_section = ''
    from .portal_insights import featured_highlights, report_highlights
    highlights = featured_highlights(report_highlights(bundle.get('portal_review')))
    if highlights:
        items = []
        for row in highlights:
            source_link = (f'<a href="{quote(report_name)}#portal-{slug(row["capture_id"])}">'
                           f'{prose(row["report"])}{(" · page " + str(row["page"])) if row["page"] else ""}</a>'
                           if report_name else prose(row['report']))
            items.append('<li><strong>' + prose(row['topic']) + '</strong>'
                         '<span class="found">' + prose(row['excerpt']) + '</span>'
                         '<span class="owner">' + source_link + ' · Captured ' + prose(row['captured_at'] or 'date unavailable')
                         + ' · Report refreshed ' + prose(row['report_date'] or 'not verified') + '</span></li>')
        all_link = f'<p><a href="{quote(report_name)}#pdf-report-highlights">All PDF report highlights and source pages</a></p>' if report_name else ''
        pdf_section = ('<section id="pdf-summary"><h2>What the supplied PDF reports show</h2>'
                        '<p class="intro">Source excerpts for review. Check OCR and card layout against the original pages; '
                        'their dates and populations may differ from the collected metrics. They do not independently establish readiness.</p>'
                        '<ul>' + ''.join(items) + '</ul>' + all_link + '</section>')
    if blockers:
        sections.append(
            '<section id="fix-first"><h2>1. Fix before the pilot</h2>'
            '<p class="intro">These confirmed conditions require remediation before pilot approval. Review the affected population and exceptions in the evidence workbook.</p>'
            f'<ol>{_action_items(blockers, numbers, report_name, bundle)}</ol>{_more(blockers)}</section>')
    if confirm:
        sections.append(
            f'<section id="confirm"><h2>{len(sections) + 1}. Confirm before the pilot</h2>'
            '<p class="intro">These checks lack sufficient current evidence of scope, configuration or effective operation. Record the missing evidence and tested behavior with the accountable owner.</p>'
            f'<ol>{_action_items(confirm, numbers, report_name, bundle, found=False)}</ol>{_more(confirm)}</section>')
    if later:
        sections.append(
            f'<section id="fix-later"><h2>{len(sections) + 1}. Fix before broad rollout</h2>'
            '<p class="intro">These do not block a pilot. Schedule them before Copilot reaches more users.</p>'
            f'<ul>{_action_items(later, numbers, report_name, bundle, found=False)}</ul>{_more(later)}</section>')

    # One line per passed required check, preferring its tenant-wide baseline row.
    by_id = {row.get("RecommendationId"): row for row in result.get("recommendations") or []}
    strengths = []
    for control in passed:
        support = [by_id[key] for key in control.get("recommendation_ids") or [] if key in by_id
                   and by_id[key].get("Disposition") == "Assurance" and by_id[key].get("EvidenceStatus") == "supported"]
        support.sort(key=lambda row: not row.get("BaselineCheck"))
        if support:
            strengths.append(support[0])
    if strengths:
        items = "".join(f'<li><strong>{prose(_heading(row))}.</strong> {prose(_first_sentence(row.get("Observation") or row.get("Strength"), 300))}</li>'
                        for row in strengths)
        sections.append(f'<section id="in-place" class="passed"><h2>{len(sections) + 1}. Already in place</h2><ul>{items}</ul></section>')

    from .authentication_presentation import authentication_summary_html
    sections.append(authentication_summary_html(result))
    executive = result.get('executive_summary') or {}
    decisions = executive.get('Decisions required') or []
    if decisions:
        sections.append('<section id="decisions-required"><h2>Decisions required</h2>' + decision_groups(result) + '</section>')
    guidance = "".join(f"<li>{prose(item)}</li>" for item in PILOT_GROUP_GUIDANCE)
    sections.append(
        f'<section id="pilot-group"><h2>{len(sections) + 1}. Choosing the pilot group</h2>'
        '<p class="intro">The readiness baseline covers the whole tenant. Pilot users, devices and content can be recorded as additional context. '
        'You do not need to name the pilot users for the assessment.</p>'
        f'<ul>{guidance}</ul></section>')

    period = result.get("evidence_period") or {}
    start, end = (period.get("start"), period.get("end")) if isinstance(period, dict) else (None, None)
    period_text = f"{start} to {end}" if start and end and start != end else (start or end or "see the full report")
    links = " · ".join(link for link in (
        f'<a href="{quote(report_name)}">Full assessment report</a>' if report_name else "",
        f'<a href="{quote(workbook_name)}">Assessment workbook</a>' if workbook_name else "",
        f'<a href="{quote(technical_name)}">Technical evidence workbook</a>' if technical_name else "",
    ) if link)
    tenant = escape(str(tenant_name or "Tenant"))
    run = result.get('run_context') or {}
    from .lifecycle_presentation import summary_html
    from .governance_presentation import summary_html as governance_summary
    run_note = ('<p>Run type: ' + prose(run.get('RunType')) + '. Baseline comparability: '
                + prose((run.get('Comparability') or {}).get('Outcome'))
                + '. See the full report technical appendix for qualifications.</p>') if run else ''
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{tenant} — Copilot pilot readiness summary</title><style>{SUMMARY_CSS}</style></head><body><div class="page">
<header><div class="brand">MICROSOFT 365 COPILOT<span>Executive readiness summary</span></div><div class="meta"><strong>{tenant}</strong>Evaluated {prose(result.get("evaluation_date"))}</div></header>
<main><section class="verdict" data-state="{state}" id="verdict"><div class="kicker">Pilot readiness</div><h1>{escape(headline)}</h1>
<p>{prose(result.get("rationale"))}</p><p>Scope: {prose(executive.get('Scope') or 'Tenant-wide Microsoft 365 foundation')}. Evidence dates: {prose(period_text)}.</p><div class="scores">{scores}</div></section>
{pdf_section}{"".join(sections)}{summary_html(result)}{governance_summary(result)}</main>
<footer><p>{links}</p><p>Evidence period: {prose(period_text)}. Methodology {prose(result.get("methodology_version"))}: required checks distinguish configuration, policy enforcement, observed operation and dated owner reviews. Missing operational proof remains unresolved. The full report and workbook hold the evidence or precise collection gap behind every finding.</p></footer>
{run_note}</div></body></html>'''

"""Embedded styles for the portable customer assessment dashboard."""

REPORT_CSS = r'''
:root {
  --navy: #102b40;
  --teal: #007f85;
  --teal-dark: #006b70;
  --ink: #203849;
  --muted: #596d7b;
  --line: #dbe5eb;
  --canvas: #f3f6f8;
  --pale: #eaf5f5;
  --red: #ad3345;
  --red-pale: #fff0f1;
  --amber: #855a13;
  --amber-pale: #fff5df;
  --green: #216c50;
  --green-pale: #eaf6ef;
  --radius: 12px;
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
html { scroll-behavior: smooth; scroll-padding-top: 90px; }
body { margin: 0; color: var(--ink); background: var(--canvas); font: 16px/1.65 "Segoe UI", Arial, sans-serif; }
header, nav, main, footer { max-width: 1240px; margin-inline: auto; }
a { color: var(--teal-dark); text-underline-offset: 3px; }
a:hover { text-decoration-thickness: 2px; }
button, a, summary { -webkit-tap-highlight-color: transparent; }
button { font: inherit; }
a:focus-visible, button:focus-visible, summary:focus-visible, [tabindex]:focus-visible {
  outline: 3px solid #d49323;
  outline-offset: 4px;
  border-radius: 4px;
}
.skip-link { position: absolute; top: 8px; left: 16px; z-index: 50; padding: 10px 16px; background: #fff; transform: translateY(-160%); }
.skip-link:focus { transform: translateY(0); }
.report-header, header { display: flex; align-items: center; justify-content: space-between; gap: 24px; padding: 28px 30px 24px; }
.brand { display: flex; align-items: center; gap: 12px; color: var(--navy); font-size: 12px; font-weight: 750; line-height: 1.5; letter-spacing: 1.1px; }
.brand-subtitle { display: block; margin-top: 2px; color: var(--muted); font-size: 11px; font-weight: 500; letter-spacing: .6px; }
.brand-mark { display: grid; grid-template-columns: repeat(2, 11px); gap: 3px; flex: 0 0 auto; }
.brand-mark span { width: 11px; height: 11px; border-radius: 2px; background: var(--teal); }
.brand-mark span:nth-child(2) { background: #5ab8b7; }
.brand-mark span:nth-child(3) { background: #48788f; }
.brand-mark span:nth-child(4) { background: var(--navy); }
.header-meta { color: var(--muted); font-size: 13px; text-align: right; line-height: 1.55; overflow-wrap: anywhere; }
.header-meta strong { display: block; color: var(--navy); font-size: 15px; }
.report-nav, nav { position: sticky; top: 0; z-index: 10; display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 12px 30px; background: #fffffff5; border-block: 1px solid var(--line); box-shadow: 0 3px 12px #102b4005; }
.nav-links { display: flex; align-items: center; gap: 24px; min-width: 0; }
.report-nav a, nav a { text-decoration: none; font-size: 13px; font-weight: 650; }
.nav-links a { color: var(--muted); padding-block: 8px; white-space: nowrap; }
.nav-links a:hover { color: var(--teal-dark); }
.nav-tools { display: flex; align-items: center; gap: 10px; flex: 0 0 auto; }
.button { display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 40px; padding: 9px 14px; border: 1px solid var(--line); border-radius: 8px; background: #fff; color: var(--navy); text-decoration: none; font-size: 13px; font-weight: 650; line-height: 1.4; cursor: pointer; }
.button-primary { background: var(--teal-dark); color: #fff; border-color: var(--teal-dark); }
.button-primary:hover { background: #005b60; border-color: #005b60; }
.button-secondary:hover { background: var(--canvas); border-color: #b5c8d2; }
main { padding: 28px 24px 10px; }
section { margin: 0 0 24px; padding: 32px 34px; background: #fff; border: 1px solid var(--line); border-radius: var(--radius); }
section section { margin: 22px 0 0; padding: 24px 0 0; border: 0; border-top: 1px solid var(--line); border-radius: 0; }
h1, h2, h3, h4 { color: var(--navy); }
h1 { margin: 14px 0 20px; font-size: 42px; line-height: 1.15; letter-spacing: -1.3px; }
h2 { margin: 0 0 16px; font-size: 25px; line-height: 1.3; letter-spacing: -.5px; }
h3 { margin: 4px 0 12px; font-size: 19px; line-height: 1.4; letter-spacing: -.15px; }
h4 { margin: 6px 0 10px; font-size: 17px; line-height: 1.45; }
p { margin: 0 0 15px; }
ul, ol { margin: 0; padding-left: 23px; }
li + li { margin-top: 9px; }
.muted, .qualification { color: var(--muted); font-size: 13px; line-height: 1.65; }
.eyebrow, .hero-kicker { font-size: 11px; font-weight: 750; text-transform: uppercase; letter-spacing: 1.1px; color: var(--muted); line-height: 1.6; }
.executive { padding: 0; overflow: hidden; border-color: #cad9e3; box-shadow: 0 8px 28px #102b4008; }
.hero-grid { display: grid; grid-template-columns: minmax(0, 1.75fr) minmax(230px, 1fr); gap: 44px; padding: 40px; color: #fff; background: radial-gradient(ellipse at 110% 5%, #00868c70, transparent 65%), linear-gradient(125deg, #102b40, #183e53); }
.hero-main { min-width: 0; }
.hero-main h1 { max-width: 20ch; color: #fff; font-size: clamp(32px, 3.3vw, 47px); line-height: 1.13; }
.hero-kicker, .hero-grid .eyebrow { color: #bad5de; }
.hero-kicker { margin-bottom: 12px; }
.hero-copy { max-width: 780px; font-size: 18px; line-height: 1.7; }
.hero-grid .hero-copy { color: #e4eff4; }
.hero-grid .muted, .hero-grid .qualification { color: #c1d6e0; }
.hero-grid a { color: #d0f5f3; }
.hero-aside { align-self: start; padding: 22px; border: 1px solid #ffffff2b; border-radius: 12px; background: #ffffff08; }
.hero-aside h2 { color: #fff; font-size: 22px; line-height: 1.4; }
.decision-label { display: inline-flex; align-items: center; padding: 6px 11px; margin-bottom: 20px; border: 1px solid #ffffff40; border-radius: 6px; font-size: 12px; font-weight: 700; line-height: 1.5; background: #ffffff10; color: #fff; }
.decision-label[data-state="unknown"] { color: #ffe4a8; background: #9b702430; border-color: #dba64b66; }
.decision-label[data-state="blocked"] { color: #ffd6d9; background: #aa3d493d; border-color: #ef919966; }
.decision-label[data-state="ready"] { color: #c6f4de; background: #23865c30; border-color: #71c69d66; }
.hero-meta, .hero-meta dl { margin: 0; }
.hero-meta dl > div, .hero-meta > div { padding: 11px 0; border-top: 1px solid #ffffff24; }
.hero-meta dt { color: #b8cfdc; font-size: 11px; text-transform: uppercase; letter-spacing: .7px; }
.hero-meta dd { margin: 3px 0 0; color: #fff; font-size: 13px; font-weight: 600; overflow-wrap: anywhere; }
.stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0; background: #fff; border-bottom: 1px solid var(--line); }
.stat { position: relative; min-width: 0; padding: 25px 22px 24px 37px; color: var(--muted); font-size: 13px; line-height: 1.5; }
.stat + .stat { border-left: 1px solid var(--line); }
.stat::before { position: absolute; top: 29px; left: 22px; width: 4px; height: 36px; border-radius: 3px; background: var(--teal); content: ""; }
.value { margin-bottom: 4px; color: var(--navy); font-size: 35px; font-weight: 750; line-height: 1.2; font-variant-numeric: tabular-nums; letter-spacing: -.8px; }
.stat[data-kind="remediation"]::before { background: var(--red); }
.stat[data-kind="confirmation"]::before { background: #b37b21; }
.stat[data-kind="evidence_gaps"]::before { background: #56728b; }
.stat[data-kind="strengths"]::before { background: #248261; }
.stat-note { display: block; margin-top: 7px; color: var(--muted); font-size: 11px; line-height: 1.5; }
.stat-label { color: var(--ink); font-weight: 650; }
.executive-brief { display: grid; grid-template-columns: 1fr 1fr; gap: 28px; padding: 30px 34px 34px; background: #fff; }
.brief-card { min-width: 0; padding: 23px; border: 1px solid var(--line); border-radius: 10px; background: #fafcfd; }
.brief-card h2 { font-size: 18px; margin-bottom: 16px; }
.brief-card li { font-size: 14px; }
.brief-card .muted { display: block; margin-top: 4px; }
.two-column { display: grid; grid-template-columns: 1fr 1fr; gap: 34px; }
.two-column h2 { font-size: 20px; }
.section-title, .section-heading { display: flex; align-items: start; justify-content: space-between; gap: 22px; }
.section-heading { justify-content: flex-start; min-width: 0; gap: 15px; }
.section-heading > div { min-width: 0; }
.section-heading h2, .section-title h2 { margin-bottom: 11px; }
.section-index, .domain-index { display: inline-block; color: var(--teal-dark); font-size: 11px; font-weight: 750; letter-spacing: 1.2px; text-transform: uppercase; }
.section-index { display: inline-flex; align-items: center; justify-content: center; flex: 0 0 auto; width: 32px; height: 32px; margin: 4px 0 7px; border: 1px solid #d7e5ea; border-radius: 8px; background: #f1f7f8; }
.section-description { max-width: 860px; color: var(--muted); font-size: 14px; }
.badge { display: inline-flex; align-items: center; flex: 0 0 auto; padding: 6px 11px; border: 1px solid #c7dfe0; border-radius: 6px; color: var(--teal-dark); background: var(--pale); font-size: 12px; font-weight: 650; line-height: 1.5; }
.priority-badge { display: inline-block; margin-right: 9px; padding: 4px 8px; border: 1px solid #ccdbe3; border-radius: 5px; color: #3f5f75; background: #eef3f7; font-size: 11px; font-weight: 750; line-height: 1.4; letter-spacing: .35px; text-transform: uppercase; }
.priority-badge[data-priority="high"], .priority-badge[data-priority="critical"] { color: var(--red); background: var(--red-pale); border-color: #f0ccd2; }
.priority-badge[data-priority="medium"] { color: var(--amber); background: var(--amber-pale); border-color: #ecd6a8; }
.priority-badge[data-priority="low"] { color: var(--green); background: var(--green-pale); border-color: #c5e2d2; }
.action { display: grid; grid-template-columns: 38px minmax(0, 1fr); gap: 18px; padding: 25px 0; border-top: 1px solid var(--line); }
.action:last-child { padding-bottom: 0; }
.action-number { display: flex; align-items: center; justify-content: center; align-self: start; width: 36px; height: 36px; margin-top: 2px; color: #4d6b81; background: #eff4f7; border: 1px solid #dce6ed; border-radius: 9px; font-size: 13px; font-weight: 750; font-variant-numeric: tabular-nums; }
.action[data-priority="high"] .action-number, .action[data-priority="critical"] .action-number { color: var(--red); border-color: #f0d7db; background: #fff6f7; }
.action-detail { min-width: 0; }
.action-detail summary, .domain-evidence summary { cursor: pointer; }
.action-detail summary { padding: 3px 0 3px 4px; list-style-position: outside; }
.action-detail summary::marker { color: #708997; font-size: 12px; }
.action-detail summary h3 { display: inline; font-size: 18px; }
.action-detail[open] summary { margin-bottom: 17px; }
.action .eyebrow { margin-bottom: 8px; }
.action-kind { color: var(--muted); font-size: 11px; font-weight: 650; letter-spacing: .35px; text-transform: uppercase; }
.action-tags { display: flex; align-items: center; flex-wrap: wrap; gap: 7px 10px; margin-bottom: 9px; }
.action-tags .priority-badge { margin-right: 0; }
.action-domain { color: var(--muted); font-size: 11px; line-height: 1.5; }
.action-body { padding: 2px 0 0; }
.action-body > p, .action-detail > p { max-width: 940px; font-size: 14px; }
.action-meta { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; margin: 18px 0; padding: 15px 17px; border: 1px solid #e2e9ee; border-radius: 8px; background: #f7fafb; font-size: 13px; }
.action-meta p { margin: 0; }
.action-meta strong { color: #516876; font-size: 11px; text-transform: uppercase; letter-spacing: .45px; }
.domain { border-top: 3px solid #c4d4df; }
.domain[data-state="ready"], .domain[data-state="observed"] { border-top-color: #339174; }
.domain[data-state="blocked"], .domain[data-state="action-required"] { border-top-color: #bb5561; }
.domain[data-state="unknown"], .domain[data-state="confirmation-required"] { border-top-color: #c49942; }
.domain[data-state="evidence-required"] { border-top-color: #66869d; }
.domain[data-state="ready"] .badge, .domain[data-state="observed"] .badge { background: var(--green-pale); color: var(--green); border-color: #c5e2d2; }
.domain[data-state="blocked"] .badge, .domain[data-state="action-required"] .badge { background: var(--red-pale); color: var(--red); border-color: #f0ccd2; }
.domain[data-state="unknown"] .badge, .domain[data-state="confirmation-required"] .badge { background: var(--amber-pale); color: var(--amber); border-color: #ecd6a8; }
.domain[data-state="evidence-required"] .badge { background: #eef3f7; color: #3f5f75; border-color: #cddce6; }
.domain-heading { display: flex; align-items: baseline; gap: 13px; min-width: 0; }
.domain-heading .domain-index { flex: 0 0 auto; }
.domain-heading h2 { min-width: 0; }
.domain-summary, .domain .lead { max-width: 920px; font-size: 17px; }
.domain-evidence { margin-top: 20px; }
.domain-evidence summary { padding: 12px 15px; margin-bottom: 12px; border: 1px solid var(--line); border-radius: 7px; color: #416174; background: #f8fafb; font-size: 13px; font-weight: 650; }
.finding { padding: 20px 0; border-top: 1px solid var(--line); }
.finding:last-of-type { padding-bottom: 6px; }
.finding p { font-size: 14px; }
.next { margin-top: 24px; padding: 15px 18px; border-left: 3px solid var(--teal); border-radius: 0 7px 7px 0; background: var(--pale); font-size: 13px; }
.evidence-link { margin: 0; font-size: 12px; }
.chart { margin: 22px 0 12px; }
.chart-legend { display: flex; flex-wrap: wrap; gap: 10px 24px; margin: 14px 0 23px; color: var(--muted); font-size: 12px; }
.chart-legend > * { display: inline-flex; align-items: center; gap: 7px; }
.legend-dot { display: inline-block; width: 9px; height: 9px; flex: 0 0 auto; border-radius: 3px; background: var(--teal); }
.bar-row { display: grid; grid-template-columns: minmax(220px, 1fr) minmax(150px, 1.1fr) 32px; align-items: center; gap: 22px; margin: 16px 0; font-size: 13px; }
.bar-row a { text-decoration: none; font-weight: 600; }
.bar-status { display: block; margin-top: 3px; color: var(--muted); font-size: 11px; font-weight: 400; }
.bar-row b { text-align: right; font-size: 14px; font-variant-numeric: tabular-nums; }
.bar, .bar-track { display: flex; height: 12px; min-width: 0; overflow: hidden; border-radius: 4px; background: #edf2f5; }
.bar { background: linear-gradient(to right, var(--teal) var(--size), #edf2f5 var(--size)); }
.bar-segment { display: block; height: 100%; flex: 0 0 auto; background: var(--teal); }
.bar-segment[data-kind="remediation"], .legend-dot[data-kind="remediation"] { background: #bd5361; }
.bar-segment[data-kind="confirmation"], .legend-dot[data-kind="confirmation"] { background: #be923d; }
.bar-segment[data-kind="evidence_gaps"], .legend-dot[data-kind="evidence_gaps"] { background: #5d8199; }
.bar-segment[data-kind="strengths"], .legend-dot[data-kind="strengths"] { background: #25816c; }
.domain-map { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 27px; padding-top: 24px; border-top: 1px solid var(--line); }
.domain-tile { display: block; min-width: 0; padding: 16px; border: 1px solid var(--line); border-top: 3px solid #b8ccd8; border-radius: 8px; background: #fbfcfd; text-decoration: none; }
.domain-tile:hover { border-color: #90b5ba; box-shadow: 0 3px 10px #102b4008; }
.domain-tile[data-state="ready"], .domain-tile[data-state="observed"] { border-top-color: #339174; }
.domain-tile[data-state="blocked"], .domain-tile[data-state="action-required"] { border-top-color: #bb5561; }
.domain-tile[data-state="unknown"], .domain-tile[data-state="confirmation-required"] { border-top-color: #c49942; }
.domain-tile[data-state="evidence-required"] { border-top-color: #66869d; }
.domain-tile-top { display: flex; align-items: center; justify-content: space-between; gap: 10px; color: var(--muted); font-size: 11px; }
.domain-tile-title { display: block; margin-top: 9px; color: var(--navy); font-size: 13px; font-weight: 650; line-height: 1.5; }
.domain-tile-count { color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.phase-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; margin-top: 22px; }
.phase { min-width: 0; padding: 21px; border: 1px solid var(--line); border-radius: 10px; background: #fafcfd; }
.phase-number { display: inline-flex; justify-content: center; align-items: center; width: 29px; height: 29px; margin-bottom: 16px; border-radius: 50%; background: var(--navy); color: #fff; font-size: 12px; font-weight: 750; }
.phase h3 { font-size: 16px; }
.phase p { font-size: 13px; }
.phase p:last-child { margin-bottom: 0; }
.phase-grid + .qualification { margin-top: 22px; }
.gap-list { padding-left: 20px; }
.gap-list li { margin-bottom: 17px; font-size: 14px; }
.gap-list li::marker { color: #9c772e; }
.guide-body { padding: 0 15px 8px; font-size: 14px; }
.guide-body li { margin-bottom: 12px; }
.operator-command { white-space: pre-wrap; overflow-wrap: anywhere; padding: 14px; background: #f8fafb; color: var(--ink); border-radius: 7px; }
.rollout-track { padding: 24px 32px 16px; border-bottom: 1px solid var(--line); }
.rollout-track ol { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); list-style: none; padding: 0; gap: 14px; }
.rollout-track li { margin: 0; padding: 13px 12px; border-top: 3px solid var(--line); color: var(--muted); }
.rollout-track li[data-state="complete"] { border-color: var(--teal); }
.rollout-track li[data-state="current"] { border-color: var(--teal); background: var(--pale); border-radius: 0 0 8px 8px; color: var(--navy); }
.stage-number { margin-right: 8px; color: var(--teal-dark); font-size: 12px; font-weight: 750; }
.stage-status { font-size: 11px; }
.rollout-track strong { display: block; margin-top: 8px; font-size: 15px; line-height: 1.5; }
.rollout-track > p { margin: 16px 0 0; font-size: 13px; }
.requirement-list { padding: 0 15px; list-style: none; }
.requirement-list > li { padding: 15px 0; border-bottom: 1px solid var(--line); }
.requirement-list p { font-size: 14px; margin: 8px 0 0; }
.requirement-heading { display: flex; justify-content: space-between; align-items: start; gap: 15px; font-size: 14px; }
.requirement-status { flex-shrink: 0; padding: 2px 8px; font-size: 11px; border-radius: 5px; color: var(--amber); background: var(--amber-pale); }
.requirement-status[data-state="met"] { color: var(--green); background: var(--green-pale); }
.requirement-status[data-state="issue"] { color: var(--red); background: var(--red-pale); }
.portal-limit { padding: 14px 0 0; }
.portal-image-grid { display: grid; grid-template-columns: minmax(0,1fr); gap: 18px; max-width: 900px; margin: 16px auto 0; }
.portal-image-grid figure { margin: 0; min-width: 0; }
.portal-image-grid img { display: block; width: 100%; height: auto; border: 1px solid var(--line); }
.portal-image-grid figcaption { font-size: 12px; margin: 8px 0 16px; overflow-wrap: anywhere; }
.portal-pages .button { margin-top: 10px; }
.pdf-extracted-text { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 13px; line-height: 1.55; padding: 16px; background: var(--surface); max-height: 440px; overflow: auto; }
@media (max-width: 680px) {
  .rollout-track { padding: 20px; }
  .rollout-track ol { grid-template-columns: repeat(2,minmax(0,1fr)); gap: 9px; }
  .rollout-track strong { font-size: 13px; }
  .rollout-track li { padding: 10px 8px; }
  .requirement-heading { flex-wrap: wrap; gap: 8px; }
  .portal-image-grid { grid-template-columns: 1fr; }
  .portal-capture table, .portal-capture tbody, .portal-capture tr, .portal-capture td, .admin-review table, .admin-review tbody, .admin-review tr, .admin-review td { display: block; }
  .portal-capture thead, .admin-review thead { display: none; }
  .portal-capture tr, .admin-review tr { padding: 12px 0; border-bottom: 1px solid var(--line); }
  .portal-capture td, .admin-review td { width: auto; padding: 5px 0; border: 0; }
  .portal-capture td::before, .admin-review td::before { content: attr(data-label); display: block; font-weight: 700; color: var(--navy); }
}
.table-scroll { margin: 17px 0; max-width: 100%; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; text-align: left; font-size: 12px; line-height: 1.55; }
th, td { padding: 11px 12px; border-bottom: 1px solid var(--line); vertical-align: top; overflow-wrap: anywhere; }
th { color: #3d5b6e; background: #eef4f6; font-weight: 700; white-space: nowrap; }
tbody tr:nth-child(even) { background: #fafcfd; }
caption { margin-bottom: 10px; text-align: left; font-size: 13px; font-weight: 650; }
caption:empty { display: none; }
.appendix-panel { padding: 26px 34px; border: 1px solid var(--line); border-radius: var(--radius); background: #fff; }
.appendix-panel > summary { cursor: pointer; color: var(--navy); font-size: 19px; font-weight: 650; line-height: 1.5; }
.appendix-panel[open] > summary { margin-bottom: 24px; }
.appendix-panel h3 { margin-top: 25px; }
.appendix-panel p { font-size: 13px; }
.report-footer, footer { padding: 25px 30px 40px; color: var(--muted); font-size: 11px; }
.report-footer { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 8px 24px; }
:target { scroll-margin-top: 95px; }
.action:target { border-radius: 8px; background: #fff9eb; outline: 2px solid #e9d4a2; outline-offset: 5px; }
tr:target { background: #fff3cf !important; outline: 2px solid #c49942; outline-offset: -2px; }

@media (max-width: 980px) {
  .report-nav, nav { align-items: stretch; flex-wrap: wrap; gap: 8px; padding-inline: 24px; }
  .nav-links { flex: 1 1 auto; gap: 19px; }
  .nav-tools { margin-left: auto; }
  .hero-grid { gap: 28px; padding: 32px; grid-template-columns: minmax(0, 1.5fr) minmax(210px, 1fr); }
  .hero-main h1 { font-size: 36px; }
  .hero-aside { padding: 18px; }
  .stat { padding: 23px 16px 23px 29px; }
  .stat::before { left: 16px; }
  .executive-brief { gap: 18px; padding: 25px; }
  .brief-card { padding: 20px; }
  .domain-map { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .phase-grid { gap: 12px; }
  .phase { padding: 17px; }
}
@media (max-width: 720px) {
  html { scroll-padding-top: 138px; }
  body { font-size: 15px; }
  .report-header, header { align-items: start; flex-direction: column; gap: 14px; padding: 22px 20px 20px; }
  .brand { font-size: 10px; letter-spacing: .8px; }
  .header-meta { text-align: left; font-size: 12px; }
  .header-meta strong { font-size: 14px; }
  .report-nav, nav { display: block; padding: 8px 18px; }
  .nav-links { gap: 20px; margin-right: -4px; padding: 3px 4px 6px 0; overflow-x: auto; scrollbar-width: thin; }
  .nav-links a { flex: 0 0 auto; font-size: 12px; min-height: 36px; }
  .nav-tools { justify-content: flex-end; gap: 9px; margin-top: 4px; padding-bottom: 3px; }
  .button { padding: 8px 12px; min-height: 38px; font-size: 12px; }
  main { padding: 18px 12px 8px; }
  section { padding: 25px 21px; margin-bottom: 18px; }
  .executive { padding: 0; }
  .hero-grid { grid-template-columns: 1fr; gap: 22px; padding: 28px 23px; }
  .hero-main h1 { font-size: 33px; max-width: 20ch; letter-spacing: -.9px; }
  .hero-copy { font-size: 16px; line-height: 1.7; }
  .hero-aside { padding: 18px; }
  .hero-meta dt { font-size: 10px; }
  .decision-label { margin-bottom: 13px; }
  .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .stat { padding: 21px 15px 21px 29px; font-size: 12px; }
  .stat::before { top: 25px; left: 16px; height: 30px; }
  .stat:nth-child(odd) { border-left: 0; }
  .stat:nth-child(n+3) { border-top: 1px solid var(--line); }
  .value { font-size: 31px; }
  .stat-note { font-size: 10px; }
  .executive-brief { grid-template-columns: 1fr; gap: 14px; padding: 20px; }
  .brief-card { padding: 20px; }
  .brief-card h2 { font-size: 17px; }
  .two-column { grid-template-columns: 1fr; gap: 25px; }
  h2 { font-size: 22px; }
  h3 { font-size: 18px; }
  .section-title, .section-heading { align-items: start; flex-wrap: wrap; gap: 6px 15px; }
  .section-title .badge, .section-heading .badge { margin-bottom: 12px; }
  .section-description { font-size: 13px; }
  .action { grid-template-columns: 28px minmax(0, 1fr); gap: 12px; padding-block: 24px; }
  .action-number { width: 27px; height: 29px; font-size: 11px; border-radius: 7px; }
  .action-detail summary h3 { font-size: 17px; }
  .priority-badge { margin-right: 6px; }
  .action-kind { font-size: 10px; }
  .action-meta { grid-template-columns: 1fr; gap: 13px; padding: 13px; }
  .action-body > p, .action-detail > p { font-size: 13px; }
  .domain-summary, .domain .lead { font-size: 15px; }
  .domain-evidence summary { padding: 11px 12px; font-size: 12px; }
  .chart-legend { gap: 8px 15px; font-size: 11px; }
  .bar-row { grid-template-columns: minmax(0, 1fr) 27px; gap: 7px 12px; margin-block: 20px; font-size: 12px; }
  .bar-row > a, .bar-row > div:first-child { grid-column: 1; grid-row: 1; min-width: 0; }
  .bar-row > b { grid-column: 2; grid-row: 1; }
  .bar-track, .bar { grid-column: 1 / -1; grid-row: 2; height: 11px; }
  .domain-map { grid-template-columns: 1fr; gap: 10px; margin-top: 22px; padding-top: 20px; }
  .domain-tile { padding: 14px 15px; }
  .domain-tile-title { margin-top: 5px; }
  .phase-grid { grid-template-columns: 1fr; gap: 13px; }
  .phase { padding: 20px; }
  .phase-number { margin-bottom: 12px; }
  .appendix-panel { padding: 23px 21px; }
  .appendix-panel > summary { font-size: 18px; }
  th, td { padding: 10px; }
  .report-footer, footer { padding: 22px 22px 30px; }
  :target { scroll-margin-top: 138px; }
}
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
@media print {
  .pdf-extracted-text { max-height: none; overflow: visible; }
  @page { margin: 15mm 13mm; }
  html { scroll-behavior: auto; }
  body { background: #fff; font-size: 10pt; line-height: 1.5; }
  header, nav, main, footer { max-width: none; }
  .report-header, header { padding: 0 0 14px; }
  .report-nav, nav, .nav-tools, .skip-link { display: none !important; }
  main { padding: 0; }
  section { padding: 20px; margin-bottom: 16px; break-inside: auto; box-shadow: none; }
  .executive { padding: 0; box-shadow: none; }
  .hero-grid { padding: 25px; gap: 24px; grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr); background: #fff; color: var(--ink); border-bottom: 2px solid var(--navy); }
  .hero-main h1 { color: var(--navy); font-size: 29pt; }
  .hero-aside h2 { color: var(--navy); }
  .hero-grid .hero-copy, .hero-grid .muted, .hero-grid .eyebrow, .hero-grid .hero-kicker { color: var(--ink); }
  .hero-copy { font-size: 11pt; }
  .hero-aside { background: #fff; border-color: var(--line); padding: 16px; }
  .hero-meta dt { color: var(--muted); }
  .hero-meta dd { color: var(--ink); }
  .hero-meta dl > div, .hero-meta > div { border-color: var(--line); }
  .decision-label, .decision-label[data-state] { background: #fff; color: var(--ink); border-color: #7c909d; }
  .stats { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .stat { padding: 17px 14px 17px 25px; font-size: 9pt; }
  .stat::before { left: 13px; top: 21px; }
  .value { font-size: 25pt; }
  .executive-brief { gap: 16px; padding: 20px; }
  .brief-card { padding: 17px; }
  h2 { font-size: 17pt; }
  h3, .action-detail summary h3 { font-size: 12pt; }
  h2, h3, h4, summary { break-after: avoid; }
  .action, .finding, .phase, .domain-tile { break-inside: avoid; }
  .action { padding-block: 20px; }
  .action-detail::details-content, .domain-evidence::details-content, .appendix-panel::details-content {
    display: block !important;
    content-visibility: visible !important;
    height: auto !important;
    overflow: visible !important;
  }
  .action-detail > :not(summary), .domain-evidence > :not(summary), .appendix-panel > :not(summary) { content-visibility: visible !important; }
  .action-detail summary, .domain-evidence summary, .appendix-panel > summary { list-style: none; }
  .action-detail summary::marker, .domain-evidence summary::marker, .appendix-panel > summary::marker { content: ""; }
  .action-detail summary, .appendix-panel > summary { margin-bottom: 15px; }
  .action-body > p, .action-detail > p, .finding p, .gap-list li { font-size: 10pt; }
  .action-meta { padding: 12px 14px; }
  .domain-map { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .phase-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .table-scroll { overflow: visible; }
  table { font-size: 8pt; }
  th, td { padding: 7px 8px; }
  thead { display: table-header-group; }
  th { white-space: normal; }
  tr { break-inside: avoid; }
  .appendix-panel { padding: 20px; margin-top: 20px; }
  .report-footer, footer { padding: 15px 0; }
  a { color: inherit; }
  .action:target, tr:target { outline: none; background: transparent !important; }
  .bar-track, .bar-segment, .legend-dot, .priority-badge, .stat::before { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
}
'''

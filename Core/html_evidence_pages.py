"""Linked technical evidence pages that sit beside the customer HTML report.

The main report and one-page summary stay aggregate-only. These companion pages hold the
named records for technical reviewers, paginated so every record remains reachable.
Every value is HTML-escaped; the pages use no scripts or external resources.
"""

from html import escape
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote

from .customer_report import slug as report_slug

PAGE_ROWS = 250

CSS = '''
:root{--ink:#14202b;--muted:#5b6b78;--line:#d6dde3;--panel:#f5f7f9;--accent:#007f85;--warn:#8a5a00;--bg:#fff}
@media (prefers-color-scheme:dark){:root{--ink:#e6edf3;--muted:#9aa8b4;--line:#33414d;--panel:#18222b;--accent:#4fc3c9;--warn:#e0b252;--bg:#0f161c}}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 "Segoe UI",Arial,sans-serif;color:var(--ink);background:var(--bg)}
header,main,footer{max-width:1400px;margin:0 auto;padding:16px}a{color:var(--accent)}
.banner{border:1px solid var(--warn);color:var(--warn);padding:8px 12px;border-radius:6px;margin:8px 0}
.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px;margin:12px 0}
.meta div{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:8px}
.meta dt{color:var(--muted);font-size:12px}.meta dd{margin:0;font-weight:600}
.table-scroll{overflow-x:auto;border:1px solid var(--line);border-radius:6px}
table{border-collapse:collapse;width:100%;font-size:12px}th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{background:var(--panel);position:sticky;top:0}td{word-break:break-word;max-width:420px}
tr:target{outline:2px solid var(--accent)}nav.pages{margin:12px 0}nav.pages a,nav.pages strong{margin-right:8px}
ul.notes li{margin:4px 0}.muted{color:var(--muted)}code{font-size:12px}
@media print{nav,.banner{display:none}}
'''


def _text(value):
    if value is None or value == '':
        return '<span class="muted">Not returned</span>'
    return escape(str(value)).replace('\n', '<br>')


def _page_name(prefix, identifier, page):
    """Bound file paths while preserving the original identifiers and HTML anchors."""
    suffix = ('' if page == 1 else f'-p{page}') + '.html'
    name = f'{prefix}-{report_slug(identifier)}'.replace('_', '-')
    available = 60 - len(suffix)
    if len(name) > available:
        digest = sha256(str(identifier).encode('utf-8')).hexdigest()[:8]
        name = name[:available - 9].rstrip('-') + '-' + digest
    return name + suffix


def page_name(finding_id, page=1):
    return _page_name('evidence', finding_id, page)


def context_page_name(table_id, page=1):
    return _page_name('context', table_id, page)


def _document(title, body, tenant):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="robots" content="noindex,nofollow"><title>{escape(title)}</title><style>{CSS}</style></head><body>'
            f'<header><div class="muted">{escape(str(tenant or "Tenant"))} · Technical evidence</div><h1>{escape(title)}</h1>'
            '<p class="banner">Confidential technical evidence. These pages contain named users, applications, devices and IP addresses. '
            'Share them only with authorized technical reviewers; the main report remains aggregate-only.</p></header>'
            f'<main>{body}</main><footer class="muted">Generated from the shared assessment result. Identifiers match '
            'the technical evidence workbook.</footer></body></html>\n')


def _pager(names, current):
    if len(names) <= 1:
        return ''
    links = []
    if current > 1:
        links.append(f'<a href="{quote(names[current - 2])}">Previous</a>')
    for number, name in enumerate(names, 1):
        links.append(f'<strong>{number}</strong>' if number == current else f'<a href="{quote(name)}">{number}</a>')
    if current < len(names):
        links.append(f'<a href="{quote(names[current])}">Next</a>')
    return '<nav class="pages" aria-label="Pages">Page: ' + ' '.join(links) + '</nav>'


def _rows_table(columns, rows, anchor_column):
    head = ''.join(f'<th scope="col">{escape(str(column))}</th>' for column in columns)
    body = ''.join(f'<tr id="{escape(report_slug(row.get(anchor_column)))}">' +
                   ''.join(f'<td>{_text(row.get(column))}</td>' for column in columns) + '</tr>' for row in rows)
    return f'<div class="table-scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _fix(detail):
    detail = detail or {}
    prerequisites = detail.get('prerequisites') or {}
    items = [('Guidance status', detail.get('guidance_status')), ('What to change', detail.get('change')),
             ('Roles', '; '.join(prerequisites.get('roles') or []) or None),
             ('Licensing', '; '.join(prerequisites.get('licensing') or []) or None),
             ('Other prerequisites', '; '.join(prerequisites.get('other') or []) or None),
             ('Where to configure', detail.get('where')), ('How to verify', detail.get('verify')),
             ('Verified on', detail.get('verified_on'))]
    links = ''.join(f'<li><a href="{escape(link["url"], quote=True)}" rel="noopener">{escape(link.get("title") or link["url"])}</a></li>'
                    for link in detail.get('links') or [] if link.get('url'))
    return ('<section id="technical-fix"><h2>Technical fix</h2><div class="meta">' +
            ''.join(f'<div><dt>{escape(label)}</dt><dd>{_text(value)}</dd></div>' for label, value in items if value) +
            '</div>' + (f'<h3>Microsoft documentation</h3><ul>{links}</ul>' if links else '') + '</section>')


def write_html_evidence_pages(model, folder, *, report_name=None, page_rows=PAGE_ROWS, workbook_name=None, technical_workbook_name=None):
    """Write index, per-finding and shared-context pages; return the page map and row counts."""
    folder = Path(folder)
    if folder.exists() and any(folder.iterdir()):
        raise FileExistsError(f'Evidence page folder is not empty: {folder}')
    folder.mkdir(parents=True, exist_ok=True)
    tenant = model.get('tenant_name')
    pages, rows_written = {}, {}
    context_pages = {}
    for table_id, table in model['tables'].items():
        if table['role'] != 'context':
            continue
        chunks = [table['rows'][index:index + page_rows] for index in range(0, len(table['rows']), page_rows)] or [[]]
        names = [context_page_name(table_id, number) for number in range(1, len(chunks) + 1)]
        context_pages[table_id] = names
        for number, chunk in enumerate(chunks, 1):
            body = (f'<p><a href="index.html">All findings</a></p><p>{escape(table["title"])}: {len(table["rows"])} '
                    f'{escape(table["row_unit"])}, shared by findings {escape(", ".join(table["finding_ids"]))}. '
                    'Supporting context does not assert an affected population.</p>'
                    + _pager(names, number) + _rows_table(table['columns'], chunk, 'evidence_record_id') + _pager(names, number))
            (folder / names[number - 1]).write_text(_document(table['title'], body, tenant), encoding='utf-8')
    for finding in model['findings']:
        evidence = finding['evidence']
        selected = [row for table_id in evidence['tables'] for row in model['tables'][table_id]['rows']
                    if row['finding_id'] == finding['finding_id']]
        columns = list(dict.fromkeys(column for table_id in evidence['tables'] for column in model['tables'][table_id]['columns']))
        chunks = [selected[index:index + page_rows] for index in range(0, len(selected), page_rows)] or [[]]
        names = [page_name(finding['finding_id'], number) for number in range(1, len(chunks) + 1)]
        pages[finding['finding_id']] = names
        rows_written[finding['finding_id']] = len(selected)
        entity = (f"{evidence['affected_entity_count']} {evidence['entity_unit']}" if evidence['affected_entity_count'] is not None
                  else 'not established')
        facts = [('Finding ID', finding['finding_id']), ('Priority', finding.get('priority')), ('Disposition', finding.get('disposition')),
                 ('Readiness effect', finding.get('readiness_effect')), ('Concern', finding.get('concern_title')),
                 ('Evidence kind', evidence['kind']), ('Evidence availability', evidence['availability']),
                 ('Records', f"{evidence['record_count']} {evidence['record_unit']}"), ('Affected entities', entity),
                 ('Observation window', ' to '.join(filter(None, [evidence['observation_window']['start'],
                                                                   evidence['observation_window']['end']])) or None),
                 ('Collected at', ', '.join(evidence['collected_at']) or None),
                 ('Source datasets', ', '.join(evidence['datasets']) or None),
                 ('Source IDs', ', '.join(evidence['source_ids']) or None)]
        if evidence['outcome_counts']:
            facts.append(('Outcomes', ', '.join(f'{name}: {count}' for name, count in evidence['outcome_counts'].items())))
        if evidence['worklist_count'] is not None:
            facts.append(('Workbook worklist', f"{evidence['worklist_count']} {evidence['worklist_unit'] or ''}".strip()))
        workbook = finding.get('workbook') or {}
        locations = [('Evidence workbook', workbook.get('evidence_range')),
                     ('Workbook detail', workbook.get('investigation_range'))]
        notes = list(evidence['limitations'])
        if evidence['count_relation']:
            notes.insert(0, evidence['count_relation'])
        if evidence['missing_evidence_action']:
            notes.insert(0, 'What would supply the missing records: ' + evidence['missing_evidence_action'])
        context_links = ''.join(f'<li><a href="{quote(context_pages[table_id][0])}">{escape(model["tables"][table_id]["title"])}</a> '
                                f'({len(model["tables"][table_id]["rows"])} {escape(model["tables"][table_id]["row_unit"])})</li>'
                                for table_id in evidence['context_tables'])
        back = (f'<a href="../{quote(report_name)}#evidence-{report_slug(finding["finding_id"])}">Back to the report</a> · '
                if report_name else '')
        header = (f'<p>{back}<a href="index.html">All findings</a> · <a href="#technical-fix">Technical fix</a></p>'
                  f'<p>{_text(finding.get("issue"))}</p><dl class="meta">' +
                  ''.join(f'<div><dt>{escape(label)}</dt><dd>{_text(value)}</dd></div>' for label, value in facts + locations if value is not None) +
                  '</dl>' + ('<h2>Limitations and reconciliation</h2><ul class="notes">' + ''.join(f'<li>{_text(note)}</li>' for note in notes) + '</ul>'
                             if notes else '') +
                  (f'<h2>Supporting context</h2><ul>{context_links}</ul>' if context_links else ''))
        for number, chunk in enumerate(chunks, 1):
            if selected:
                records = (f'<h2>Records ({len(selected)} {escape(evidence["record_unit"])}; rows {(number - 1) * page_rows + 1}–'
                           f'{(number - 1) * page_rows + len(chunk)})</h2>' + _pager(names, number) +
                           _rows_table(columns, chunk, 'detail_record_id') + _pager(names, number))
            else:
                records = ('<h2>Records</h2><p>No named records were exported for this finding. '
                           + escape({'supporting_context': 'Its supporting context is listed above.'}.get(evidence['kind'],
                                    'See evidence availability and the limitations above.')) + '</p>')
            body = header + records + _fix(finding.get('recommendation_detail'))
            title = f"{finding['finding_id']}: {finding.get('title') or ''}"
            (folder / names[number - 1]).write_text(_document(title, body, tenant), encoding='utf-8')
    index_rows = ''.join(
        f'<tr><td><a href="{quote(pages[item["finding_id"]][0])}">{escape(item["finding_id"])}</a></td><td>{_text(item.get("title"))}</td>'
        f'<td>{_text(item.get("priority"))}</td><td>{_text(item["concern_title"])}</td><td>{_text(item["evidence"]["kind"])}</td>'
        f'<td>{_text(item["evidence"]["availability"])}</td><td>{item["evidence"]["record_count"]} {escape(item["evidence"]["record_unit"])}</td>'
        f'<td>{_text(item["evidence"]["affected_entity_count"])} {escape(item["evidence"]["entity_unit"] or "")}</td></tr>'
        for item in model['findings'])
    back = f'<p><a href="../{quote(report_name)}">Back to the report</a></p>' if report_name else ''
    workbook = f'<p>Workbook: {escape(workbook_name)}</p>' if workbook_name else ''
    index = (back + workbook + '<p>Every finding, its evidence kind and availability, and its record and entity counts. '
             'Record counts and entity counts use different units; do not add counts across findings.</p>'
             '<div class="table-scroll"><table><thead><tr><th>Finding</th><th>Title</th><th>Priority</th><th>Concern</th>'
             '<th>Evidence kind</th><th>Availability</th><th>Records</th><th>Affected entities</th></tr></thead>'
             f'<tbody>{index_rows}</tbody></table></div>')
    (folder / 'index.html').write_text(_document('Technical evidence index', index, tenant), encoding='utf-8')
    return {'folder': str(folder), 'index': str(folder / 'index.html'), 'pages': pages, 'context_pages': context_pages,
            'rows_written': rows_written}

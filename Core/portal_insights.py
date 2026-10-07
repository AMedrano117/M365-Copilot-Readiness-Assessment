"""Page-backed PDF excerpts shared by reports, workbooks and agent exports.

Numbers remain quoted source text. In particular, OCR reading order cannot turn
neighboring dashboard labels and numbers into verified assessment measurements.
"""

from hashlib import sha256
import re


QUALIFICATION = ('PDF context; check the excerpt against the original page. Capture dates are not report refresh dates. '
                 'Dashboard counts and percentages retain their source scope and do not establish a readiness control result.')

# Match complete statements or retain literal label/value blocks as quotations.
# Never assign a typed metric, calculate a percentage, or fill an absent value.
RULES = (
    ('DLP enforcement', r'Policy is monitoring only[^\n]*(?:not enforced|not enabled)',
     'Confirm policy enforcement, targeting and exclusions in Purview.'),
    ('Update channel support', r'Last month,\s+[\d.]+% of Copilot\s+users[\u2019\'] devices were not on a\s+supported update channel',
     'Review affected devices and their Microsoft 365 Apps update channels.'),
    ('Microsoft 365 app versions', r'Last month,\s+[\d.]+% of Copilot\s+users[\u2019\'] devices were multiple\s+versions behind',
     'Identify older app versions and confirm the update plan for pilot devices.'),
    ('Connected experiences', r'Last month,\s+connected\s+experiences were enabled for\s+[\d.]+% of Copilot users',
     'Confirm connected-experience settings for the planned pilot population.'),
    ('OneDrive availability', r'Last month,\s+OneDrive was\s+enabled for [\d.]+% of Copilot\s+users',
     'Confirm OneDrive availability for the planned pilot population.'),
    ('Copilot adoption score', r'Copilot adoption score\s+\d+\s*/\s*\d+',
     'Review the portal adoption definition and reporting window with the adoption owner.'),
    ('Users who have not tried Copilot', r'[\d.]+%\s+of licensed Copilot users haven[\u2019\']t tried Copilot yet',
     'Plan guided learning and measure adoption within the licensed population.'),
    ('User feedback', r'No feedback from users yet',
     'Agree a feedback channel and success measures for the pilot.'),
    ('Copilot Chat pinning', r'Copilot Chat not pinned',
     'Review the Copilot Chat pinning policy and intended user population.'),
    ('License requests', r'License requests pending',
     'Review pending requests and available entitlement before assigning licenses.'),
    ('Copilot content connections', r'Copilot responses are limited',
     'Review the portal recommendation and approved content connections.'),
    ('Label protection on referenced files', r'File referenced with labeling protection\s+\d+\s*/\s*\d+',
     'Confirm the referenced-file scope and review labeling and permissions; the ratio is not tenant-wide coverage.'),
    ('Referenced sites and files', r'Sharepoint sites referenced\s+\d+\s+(?:Improve permissions\s+)?Files referenced\s+\d+',
     'Review the referenced sites and their access; referenced counts alone do not prove oversharing.'),
    ('Sensitive information in interactions', r'Prompts with sensitive info types\s+[\d.,]+[KMB]?',
     'Verify the reporting window, count definition and sensitive-information matches in Purview.'),
    ('Organization compliance actions', r'Completed by your organization\s+\d+\s*/\s*\d+',
     'Review the remaining Microsoft recommendations with the compliance owner.'),
    ('Enabled and active users shown in PDF', r'Enabled users\s+\d+\s+Active users\s+\d+',
     'Confirm the user population and source refresh date before comparing with collected usage reports.'),
    ('User activity shown in PDF', r'Active users\s+\d+\s+Avg daily active users\s+\d+',
     'Confirm whether these users are licensed or unlicensed and the reporting window.'),
    ('Agent activity', r'Agent usage\s+Active agents\s+\d+\s+Active users\s+\d+',
     'Review the reported agents, their owners, access and activity window.'),
    ('Copilot search availability', r'Copilot search usage\s+Loading data[^\n]*',
     'Return a completed search-usage view; a loading state does not establish zero use.'),
    ('Credit usage availability', r'No credit usage available',
     'Confirm metered usage and reporting availability; unavailable data does not establish zero credits.'),
    ('Reported active users', r'Active users\s*:\s*\d+',
     'Confirm the user population and reporting window against the captured page.'),
)


def report_highlights(review):
    """Derive excerpts from retained text, including older replay manifests."""
    rows = []
    for capture in (review or {}).get('captures') or []:
        seen = set()
        for page in capture.get('extracted_pages') or []:
            text = str(page.get('text') or '')
            for topic, pattern, follow_up in RULES:
                for match in re.finditer(pattern, text, re.I):
                    excerpt = match.group().strip()
                    signature = (topic, re.sub(r'\s+', ' ', excerpt).casefold())
                    if signature in seen:
                        continue
                    seen.add(signature)
                    rows.append(_row(capture, topic, excerpt, page, follow_up))
        if not seen:
            # Reviewed notes and unfamiliar PDF layouts still get a visible
            # summary. No source page or numeric interpretation is invented.
            notes = capture.get('review_notes') or [capture.get('summary') or 'No readable summary supplied.']
            for note in notes:
                rows.append(_row(capture, 'Report context', str(note), None,
                                 'Review the original capture and confirm its reporting scope.'))
    return rows


def _row(capture, topic, excerpt, page, follow_up):
    identifier = sha256(f'{capture.get("id")}\0{(page or {}).get("page")}\0{topic}\0{excerpt}'.encode('utf-8')).hexdigest()[:20]
    return {'highlight_id': 'PDF-' + identifier, 'capture_id': capture.get('id'), 'report': capture.get('title'),
            'topic': topic, 'excerpt': excerpt, 'page': (page or {}).get('page'),
            'extraction_method': (page or {}).get('method') or capture.get('review_method') or 'manual',
            'captured_at': capture.get('captured_at') or None, 'report_date': capture.get('report_date') or None,
            'source_file': capture.get('source_name') or capture.get('source_file'),
            'source_sha256': capture.get('source_sha256'), 'domain_id': capture.get('domain_id'),
            'follow_up': follow_up, 'evidence_kind': 'portal_context',
            'qualification': ' '.join(filter(None, [QUALIFICATION, capture.get('qualification'),
                ' '.join(capture.get('limitations') or []) if isinstance(capture.get('limitations'), list) else capture.get('limitations')]))}


def workbook_highlights(review):
    return [{'Highlight ID': row['highlight_id'], 'Report': row['report'], 'Topic': row['topic'],
             'Source excerpt': row['excerpt'], 'Captured': row['captured_at'], 'Report refreshed': row['report_date'],
             'Page': row['page'], 'Source file': row['source_file'], 'Follow-up': row['follow_up'],
             'Extraction method': row['extraction_method'], 'Qualification': row['qualification'],
             'Capture ID': row['capture_id'], 'SHA-256': row['source_sha256'],
             'Source detail': f'Page {row["page"]} extracted text' if row['page'] else 'Capture metadata'}
            for row in report_highlights(review)]


def featured_highlights(rows, per_capture=2):
    groups, selected = {}, []
    for row in rows:
        groups.setdefault(row['capture_id'], []).append(row)
    order = {topic: index for index, (topic, _, _) in enumerate(RULES)}
    for group in groups.values():
        selected.extend(sorted(group, key=lambda row: order.get(row['topic'], len(order)))[:per_capture])
    return selected

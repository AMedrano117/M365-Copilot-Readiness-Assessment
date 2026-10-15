"""Ask for missing evidence without turning optional dashboard cards into controls."""


def sharepoint_next_step(scan, payload=None, context=None):
    payload, context = payload or {}, context or {}
    if context.get('permission_profile') == 'restricted' or payload.get('availability_status') == 'not_requested':
        return ('SharePoint administrative collection is excluded. Ask the owner for existing completed '
                'DAG exports covering the missing fields and scope, and import them with --reports-dir. '
                'Keep the selected permission profile.')
    inventory = payload.get('dag_reports') or {}
    states = {str(row.get('Status', '')).lower() for row in inventory.get('reports', [])}
    if states & {'completed', 'complete', 'succeeded'}:
        return ('Existing completed DAG reports were observed. Retry the live SharePoint collector to download '
                'the relevant results automatically; check download errors, report dates and covered scope. '
                'If automatic download remains unavailable, export only the missing results and import with --reports-dir. '
                'A Completed status alone does not establish usable exposure evidence.')
    if states & {'started', 'running', 'inprogress', 'queued', 'inqueue'}:
        return ('DAG reports are still running. Let Microsoft complete the relevant reports, then rerun live '
                'collection to download them. Do not start duplicate scans. Import a manual export only if '
                'automatic download is unavailable.')
    if not inventory.get('available'):
        return ('Report inventory was not verified. On the next live run, complete the configured SharePoint '
                'certificate or administrator sign-in and check collector access. Existing completed reports '
                'can then be downloaded automatically; an owner-provided structured export is the fallback.')
    return ('Check SharePoint admin center > Reports > Data access governance for existing results covering '
            'the missing fields. If none are current, ask the administrator to generate only those reports, '
            'wait for completion, then rerun live collection for automatic download. The assessment does not start scans.')


def build_evidence_requests(admin_review, data_exposure=None, context=None, sharepoint=None):
    context = context or {}
    requests = []
    selected = {str(name).lower() for name in context.get('enabled_collectors', [])}
    rows = admin_review.get('rows', [])
    groups = {}
    for row in rows:
        if row.get('Value') is not None:
            continue
        source = row.get('Source', '')
        if selected and source.startswith('Microsoft Graph') and 'm365' not in selected:
            continue
        if selected and ('Purview' in source or 'Exchange' in source) and 'purview' not in selected:
            continue
        dlp = admin_review.get('dlp') or {}
        if source == 'Purview DLP policies and rules' and dlp.get('complete', dlp.get('available')):
            # A complete read with no identified Copilot policy needs a configuration
            # review, rather than another request for the same collected dataset.
            continue
        groups.setdefault(source, []).append(row['Topic'])
    dlp = admin_review.get('dlp') or {}
    if dlp.get('rules_complete') is False and (not selected or 'purview' in selected):
        groups.setdefault('Purview DLP policies and rules', []).append('Complete DLP rules and linked Copilot blocking actions')
    for source, topics in groups.items():
        action = ('On the next live run, allow delegated administrator sign-in to read this setting; '
                  'alternatively record the specific setting and review date in the admin center.'
                  if 'delegated' in source else
                  'Review the source collection gap and retry the supported live collector. '
                  'A full Copilot PDF is not needed for these fields.')
        requests.append({'Topic': source, 'Missing evidence': '; '.join(topics),
                         'Collection route': 'Live collector', 'Next step': action,
                         'Scope': 'Selected collection; unavailable values remain unknown.'})
    for kind, scan in ((data_exposure or {}).get('sources') or {}).items():
        if kind not in {'sam', 'dspm'}:
            continue
        coverage = scan.get('coverage') or {}
        missing = coverage.get('domains_missing', []) + coverage.get('domains_unknown', [])
        missing += [f'{name} workload coverage' for name in coverage.get('workloads_missing', [])]
        freshness = scan.get('freshness')
        if scan.get('files_loaded') and not missing and freshness == 'fresh' and not scan.get('errors'):
            continue
        needs = list(dict.fromkeys(missing))
        if not scan.get('files_loaded'):
            needs.insert(0, 'Completed structured assessment results')
        if freshness != 'fresh':
            needs.append('Recognized, current report date')
        if scan.get('errors'):
            needs.append('Readable supported export')
        action = (sharepoint_next_step(scan, sharepoint, context) if kind == 'sam' else
                  'Export an existing completed DSPM data risk assessment covering the missing fields and locations; retain its scope and date. '
                  'The weekly default covers the top 100 SharePoint sites by usage. Before requesting a custom assessment or additional '
                  'classification, confirm the scope and applicable entitlement with the owner. On-demand classification and other '
                  'PAYG protections are separate capabilities; this request does not require activating them. PDF highlights do not replace structured exposure evidence.')
        requests.append({'Topic': 'SharePoint Data Access Governance' if kind == 'sam' else 'Purview DSPM sensitive-data exposure',
                         'Missing evidence': '; '.join(needs), 'Collection route': 'Structured results',
                         'Next step': action,
                         'Scope': 'Conclusions apply to the returned or exported population.'})
    return requests

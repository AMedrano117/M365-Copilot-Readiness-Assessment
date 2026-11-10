"""Plain-language descriptions of imported SharePoint and Purview reports.

Presentation only: names, report kinds and outcomes for reports already parsed
by data_exposure_assessment. Nothing here selects, counts or evaluates evidence.

Microsoft's Data access governance (DAG) reports come in two kinds. Snapshot
reports show state as of the date the report was generated. Activity reports
list the sites where new sharing links were created, or content was shared with
Everyone except external users, in the 28 days before the report.
"""

from .data_exposure_assessment import _parse_date, sharing_link_type

ACTIVITY_WINDOW_DAYS = 28

# report_type -> (name, kind, what the report shows)
_FAMILIES = {
    'permission_snapshot': ('Site permissions', 'Snapshot',
        'Who can access each listed site, with the sharing links and broad permissions on it, as of the report date.'),
    'special_group_permissions': ('Items shared with Everyone or Everyone except external users', 'Snapshot',
        'Sites, folders and files shared with these organization-wide groups, as of the report date.'),
    'sharing_links_activity': (None, 'Activity',
        f'Sites where people created new links of this type in the {ACTIVITY_WINDOW_DAYS} days before the report date. '
        'Links that already existed are not counted here; the site permissions snapshot shows them.'),
    'exposure_counts': ('Sharing and permission counts', 'Counts',
        'Sharing-link and permission counts for each listed location.'),
    'sharing_details': ('Sharing link details', 'Detail',
        'Individual sharing links or recipients listed in the export.'),
    'content_management_assessment': ('Content management assessment', 'Lifecycle',
        'Inactive and ownerless sites as of the report date.'),
    'label_inventory': ('Sensitivity labels for files', 'Snapshot',
        'Sites containing files with sensitivity labels, as of the report date.'),
    'dspm_assessment': ('Purview data risk assessment', 'Assessment',
        'Potentially overshared and unlabeled sensitive items found by the assessment.'),
}
_KIND_ORDER = {'Snapshot': 0, 'Activity': 1}

_USED = {
    'selected': 'Used',
    'no_activity': 'Used: no new links of this type were created in the period',
    'superseded': 'Not used: a newer report of the same sites replaces it',
    'empty': 'Not used: the file has no data rows',
    'future_date': 'Not used: the report date is after the evaluation date',
}

EXPLANATION = (
    'Snapshot reports show the permissions and sharing links that existed when each report was generated; '
    'exposure counts in this assessment come from them. '
    f'Activity reports list the sites where people created new sharing links in the {ACTIVITY_WINDOW_DAYS} days before the report date. '
    'They do not count links that already existed, and an activity report with no sites means only that '
    'no new links of that type were created in that period.')


def _count(number, singular, plural):
    return f'{number} {singular if number == 1 else plural}'


def _date(report):
    parsed = _parse_date(report.get('report_date'))
    if not parsed:
        return 'Not established'
    text = parsed.date().isoformat()
    if report.get('freshness') == 'stale' and report.get('max_age_days'):
        text += f" (older than {report['max_age_days']} days)"
    return text


def describe_report(report):
    """Return name, kind, meaning, date, result and use for one parsed report."""
    report_type = str(report.get('report_type') or '')
    name, kind, shows = _FAMILIES.get(report_type, ('Imported report', 'Report', 'Rows read from the supplied export.'))
    if report_type == 'sharing_links_activity':
        link_type = sharing_link_type(report.get('source_file', ''), report.get('collector_report_entity'))
        name = ('Sharing links activity (link type not identified)' if link_type == 'Sharing links'
                else link_type + ' activity')
    workload = str(report.get('workload') or '')
    if workload and workload != 'Unknown':
        name += f' ({workload})'
    sites = int(report.get('population_count') or 0)
    if report_type == 'sharing_links_activity':
        if report.get('status') == 'no_activity' or (not sites and not report.get('links_created')):
            result = 'No sites listed'
        elif report.get('links_created') is not None:
            result = (_count(int(report['links_created']), 'new link', 'new links')
                      + ' across ' + _count(sites, 'site', 'sites'))
        else:
            result = _count(sites, 'site', 'sites') + ' listed'
    elif report_type in {'permission_snapshot', 'label_inventory', 'content_management_assessment'}:
        result = _count(sites, 'site', 'sites')
    else:
        result = _count(int(report.get('records_read') or 0), 'row', 'rows')
    status = str(report.get('status') or '')
    return {'name': name, 'kind': kind, 'shows': shows, 'date': _date(report), 'result': result,
            'used': _USED.get(status, status.replace('_', ' ').capitalize() or 'Not recorded'),
            'source_file': report.get('source_file', '')}


def describe_reports(data_exposure):
    """Describe every parsed SAM and DSPM report, snapshots first."""
    sources = (data_exposure or {}).get('sources') or {}
    rows = [describe_report(report) for key in ('sam', 'dspm')
            for report in (sources.get(key) or {}).get('reports') or []]
    return sorted(rows, key=lambda row: (_KIND_ORDER.get(row['kind'], 2), row['name'], row['date'], row['source_file']))

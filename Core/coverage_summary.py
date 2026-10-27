"""Summarize retained requests by dataset without discarding request metadata."""
from collections import defaultdict
from copy import deepcopy
import re

GUID = re.compile(r'[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}')


def coverage_projection(result, bundle):
    """Use persisted metadata, with one shared projection for older saved results."""
    retained = result.get('dataset_coverage')
    if isinstance(retained,dict) and 'summary' in retained and 'requests' in retained:
        return retained
    return summarize_collection(bundle.get('assessment_sources'),bundle.get('source_statuses'))


def summarize_collection(sources, statuses=None):
    grouped = defaultdict(list)
    for name, datasets in (sources or {}).items():
        logical = GUID.sub('{object}', name).split(':{object}')[0].split('.{object}')[0]
        for dataset in datasets:
            grouped[logical].append(dataset)
    native = set(grouped)
    for name, state in (statuses or {}).items():
        if not isinstance(state, dict) or name.startswith(('connection_','pipeline_')):
            continue
        logical = GUID.sub('{object}', name).split(':{object}')[0].split('.{object}')[0]
        if logical not in native:
            grouped[logical].append({'records':[], 'source':deepcopy(state), '_status_only':True})
    summaries, requests = [], []
    for name, datasets in sorted(grouped.items()):
        parents, windows, providers = set(), set(), set()
        success = zero = failed = partial = unknown = records = not_queried = known_counts = 0
        completeness, truncation = [], []
        first = len(requests) + 2
        for number, dataset in enumerate(datasets, 1):
            source = dataset.get('source') or {}
            rows = dataset.get('records') or []
            count = source.get('records_collected', source.get('record_count')) if dataset.get('_status_only') else len(rows)
            records += count if isinstance(count, int) and not isinstance(count, bool) else 0
            known_counts += isinstance(count, int) and not isinstance(count, bool)
            state = source.get('availability_status') or source.get('status') or ('available' if source.get('available') is True else 'unknown')
            errors = source.get('partial_errors') or source.get('errors') or source.get('error')
            if state in {'not_requested','skipped','not_selected','not_applicable'}:
                outcome = 'not queried'; not_queried += 1
            elif state in {'partial','truncated'} or source.get('truncated') is True or errors and rows:
                outcome = 'partial'; partial += 1
            elif state in {'unavailable','failed','error'} or errors:
                outcome = 'failed'; failed += 1
            elif state in {'available','empty','collected'}:
                outcome = 'successful'; success += 1
                zero += count == 0
            else:
                outcome = 'unknown'; unknown += 1
            parent = source.get('parent') or {}
            parent = parent if isinstance(parent, dict) else {'id':parent}
            parent_id = source.get('parent_id') or source.get('parent_object_id') or source.get('object_id') or parent.get('id')
            if not parent_id and str(source.get('scope') or '').startswith('Parent object '):
                parent_id = source['scope'][len('Parent object '):]
            if not parent_id and rows and isinstance(rows[0], dict):
                parent_id = rows[0].get('ParentId')
            if parent_id:
                parents.add(str(parent_id))
            window = source.get('collection_window') or source.get('observation_period') or {}
            window = window if isinstance(window, dict) else {}
            start = window.get('start') or window.get('window_start') or source.get('window_start')
            end = window.get('end') or window.get('window_end') or source.get('window_end')
            if start or end:
                windows.add((str(start or 'unknown'), str(end or 'unknown')))
            api = ' '.join(str(source.get(key) or '') for key in ('source_api','provider','source','source_workload')).lower()
            providers.add('Microsoft Graph' if 'graph.microsoft.com' in api or 'microsoft graph' in api or api.startswith('/v1.0/') else
                          'Microsoft Defender' if 'securitycenter' in api or 'defender' in api else
                          'Microsoft Purview' if 'purview' in api else
                          'SharePoint' if 'sharepoint' in api else
                          'Exchange' if 'exchange' in api else 'Retained workload evidence')
            completeness.append(source.get('complete'))
            truncation.append(source.get('truncated'))
            requests.append({'Dataset':name, 'Query':number, 'Parent object':parent_id,
                'Outcome':outcome, 'Returned records':count, 'Retained records':len(rows),
                'Source metadata':deepcopy(source)})
        complete = ('Not requested' if not_queried == len(datasets) else
                    'Incomplete' if failed or partial or any(value is False for value in completeness) else
                    'Complete for recorded queries' if completeness and all(value is True for value in completeness) and not unknown else 'Unknown')
        truncated = 'Yes' if any(value is True for value in truncation) else 'No' if truncation and all(value is False for value in truncation) else 'Unknown'
        period = '; '.join(start + ' through ' + end for start, end in sorted(windows)) if len(windows) <= 2 else f'{len(windows)} recorded query windows; see technical detail'
        qualification = ('Returned rows are not a unique-entity count. Zero-result queries are a subset of successful queries. '
                         'Completeness is limited to the recorded query scope; tenant-wide assurance is not inferred.')
        if failed or partial or unknown:
            qualification += f' {failed} failed, {partial} partial and {unknown} unclassified queries require review.'
        if not_queried:
            qualification += f' {not_queried} requests were not run; no zero-result observation is inferred.'
        if known_counts != len(datasets):
            qualification += f' {len(datasets)-known_counts} request record counts are unknown; the total includes known counts only.'
        availability = ('not_requested' if not_queried == len(datasets) else 'partial' if partial or failed and success else
                        'failed' if failed else 'available' if success and not unknown else 'unknown')
        summaries.append({'Dataset':name, 'Source':'; '.join(sorted(providers)),
            'Availability state':availability,
            'Parent objects queried':len(parents) if parents else None, 'Successful queries':success,
            'Zero-result queries':zero, 'Failed queries':failed, 'Partial queries':partial,
            'Unknown queries':unknown, 'Not queried':not_queried,
            'Returned records':records if known_counts else None, 'Completeness':complete,
            'Truncation':truncated, 'Observation period':period or 'Not established',
            'Qualification':qualification, 'Technical detail row':first})
    return {'summary':summaries, 'requests':requests}


def coverage_text(row):
    fields = ('Source','Parent objects queried','Successful queries','Zero-result queries','Failed queries',
              'Partial queries','Returned records','Completeness','Truncation','Observation period')
    return row['Dataset'].replace('_',' ') + ': ' + '; '.join(
        field.lower() + '=' + ('unknown' if row.get(field) is None else str(row[field])) for field in fields) + '. ' + row['Qualification']

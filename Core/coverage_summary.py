"""Summarize retained requests by dataset without discarding request metadata."""
from collections import defaultdict
from copy import deepcopy
import re

GUID = re.compile(r'[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}')


def _logical_name(name):
    logical = GUID.sub('{object}',name).split(':{object}')[0].split('.{object}')[0]
    logical = logical.partition(':')[0]
    for prefix in ('entra_','m365_','purview_','defender_'):
        if logical.startswith(prefix):
            return logical[len(prefix):]
    return logical


def _parent(source, name='', rows=()):
    parent = source.get('parent') or {}
    parent = parent if isinstance(parent,dict) else {'id':parent}
    identifier = source.get('parent_id') or source.get('parent_object_id') or source.get('object_id') or parent.get('id')
    if not identifier and str(source.get('scope') or '').startswith('Parent object '):
        identifier = source['scope'][len('Parent object '):]
    if not identifier and ':' in name:
        identifier = name.partition(':')[2]
    if not identifier and rows and isinstance(rows[0],dict):
        identifier = rows[0].get('ParentId')
    return identifier


def coverage_projection(result, bundle):
    """Use persisted metadata, with one shared projection for older saved results."""
    retained = result.get('dataset_coverage')
    if isinstance(retained,dict) and 'summary' in retained and 'requests' in retained:
        return retained
    return summarize_collection(bundle.get('assessment_sources'),bundle.get('source_statuses'))


def summarize_collection(sources, statuses=None):
    grouped = defaultdict(list)
    for name, datasets in (sources or {}).items():
        logical = _logical_name(name)
        for dataset in datasets:
            grouped[logical].append(dict(dataset,_source_name=name))
    request_index = defaultdict(list)
    for logical,datasets in grouped.items():
        for dataset in datasets:
            source = dataset.get('source') or {}
            parent = _parent(source,dataset.get('_source_name',''),dataset.get('records') or [])
            api = source.get('source_api') or source.get('request_url')
            if parent: request_index[(logical,'parent',str(parent))].append(dataset)
            if api: request_index[(logical,'api',api)].append(dataset)
            if not parent: request_index[(logical,'root',None)].append(dataset)
    for name, state in (statuses or {}).items():
        if not isinstance(state, dict) or name.startswith(('connection_','pipeline_')):
            continue
        logical = _logical_name(name)
        parent = _parent(state,name)
        api = state.get('source_api') or state.get('request_url')
        mirrors = []
        candidates = request_index.get((logical,'parent',str(parent)) if parent else
                                       (logical,'api',api) if api else (logical,'root',None),[])
        for dataset in candidates:
            source = dataset.get('source') or {}
            native_parent = _parent(source,dataset.get('_source_name',''),dataset.get('records') or [])
            native_api = source.get('source_api') or source.get('request_url')
            same_capture = not (state.get('collected_at') and source.get('collected_at')) or state['collected_at'] == source['collected_at']
            same_status = (state.get('availability_status',state.get('status')) == source.get('availability_status',source.get('status'))
                and state.get('available') == source.get('available')
                and bool(state.get('truncated')) == bool(source.get('truncated'))
                and bool(state.get('errors') or state.get('error') or state.get('partial_errors')) ==
                    bool(source.get('errors') or source.get('error') or source.get('partial_errors')))
            same_request = (parent and str(parent) == str(native_parent) and (not api or not native_api or api == native_api)) or (api and api == native_api and parent == native_parent)
            if same_capture and same_status and (same_request or not parent and not api and not native_parent):
                mirrors.append(dataset)
        if len(mirrors) == 1:
            mirrors[0].setdefault('_status_mirrors',[]).append({'name':name,'source':deepcopy(state)})
        else:
            grouped[logical].append({'records':[], 'source':deepcopy(state), '_status_only':True,'_source_name':name})
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
            state = source.get('availability_status') or source.get('status') or ('available' if source.get('available') is True else 'unknown')
            if source.get('available') is False and state in {'available','empty','collected'}:
                state = 'unavailable'
            errors = source.get('partial_errors') or source.get('errors') or source.get('error')
            if state in {'not_requested','skipped','not_selected','not_applicable'}:
                outcome = 'not queried'; not_queried += 1; count = None
            elif state in {'partial','truncated'} or source.get('truncated') is True or errors and rows:
                outcome = 'partial'; partial += 1
            elif state in {'unavailable','failed','error'} or errors:
                outcome = 'failed'; failed += 1
            elif state in {'available','empty','collected'}:
                outcome = 'successful'; success += 1
                zero += count == 0
            else:
                outcome = 'unknown'; unknown += 1
            records += count if isinstance(count, int) and not isinstance(count, bool) else 0
            known_counts += isinstance(count, int) and not isinstance(count, bool)
            parent_id = _parent(source,dataset.get('_source_name',''),rows)
            if parent_id and outcome != 'not queried':
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
                'Source metadata':deepcopy(source), 'Source state key':dataset.get('_source_name'),
                'Mirrored status metadata':deepcopy(dataset.get('_status_mirrors') or [])})
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
            'Parent objects queried':len(parents) if parents else 0 if not_queried == len(datasets) else None, 'Successful queries':success,
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

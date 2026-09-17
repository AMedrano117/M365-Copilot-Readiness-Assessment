"""Incremental, replayable snapshots of completed service pipelines."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from . import offline_collection


SERVICE_PIPELINES = {
    'm365': ('m365_result', 'M365'),
    'entra': ('entra_info', 'Entra'),
    'purview': ('purview_info', 'Purview'),
    'defender': ('defender_info', 'Defender'),
    'power_platform': ('power_platform_info', 'Power Platform'),
    'copilot_studio': ('copilot_studio_info', 'Copilot Studio'),
}


def _timestamp():
    return datetime.now(timezone.utc).isoformat()


def _unfinished_result(name, state):
    from .new_recommendation import new_recommendation, CATEGORY_SCAN_COVERAGE
    _, label = SERVICE_PIPELINES[name]
    result = {'available': False, 'availability_status': state['availability_status'],
              'reason': state['reason'], 'recommendations': []}
    if state['status'] not in {'not_selected', 'not_requested'}:
        result['recommendations'].append(new_recommendation(
            label, 'Collection did not finish', state['reason'],
            'Run a new live assessment to collect this service. Offline replay preserves completed evidence but does not resume collection.',
            status='Not Assessed', category=CATEGORY_SCAN_COVERAGE, disposition='Coverage',
            finding_key=f'collection.unfinished.{name}', confidence='Unknown',
        ))
    if name == 'm365':
        recommendations = result.pop('recommendations')
        return [result, recommendations]
    return result


class CollectionCheckpoint:
    """Keep one collection path, package and evidence timestamp for a live run.

    Completed means the pipeline returned, not that all of its API sources were
    readable. The returned source availability records remain authoritative.
    """

    def __init__(self, path=None, *, service_config, **save_options):
        self.path = path
        self.options = save_options
        self.package = {}
        self.results = offline_collection.empty_service_results()
        now = _timestamp()
        self.options.setdefault('collected_at', now)
        self.progress = {'status': 'in_progress', 'started_at': now, 'updated_at': now, 'services': {}}
        restricted = save_options.get('assessment_settings', {}).get('permission_profile') == 'restricted'
        for name, (key, label) in SERVICE_PIPELINES.items():
            selected = service_config.get('run_' + name, False)
            status = 'pending' if selected else 'not_selected'
            reason = f'{label} collection has not finished. Its tenant controls remain not assessed.'
            if not selected:
                reason = f'{label} was not selected for this run.'
            elif restricted and name == 'purview':
                status = 'not_requested'
                reason = 'Restricted permission profile: Purview administrative collection is not requested; supply supported exports or saved evidence.'
            state = {'status': status, 'availability_status': 'unavailable' if status == 'pending' else status,
                     'available': False, 'reason': reason}
            self.progress['services'][name] = state
            self.results[key] = _unfinished_result(name, state)

    def save(self):
        self.progress['updated_at'] = _timestamp()
        self.path = offline_collection.save_collection(
            self.path, service_results=self.results, collection_progress=self.progress,
            _checkpoint_package=self.package, **self.options,
        )
        return self.path

    def record(self, name, result):
        state = self.progress['services'][name]
        # Preserve deliberate exclusions.
        if state['status'] in {'not_selected', 'not_requested'}:
            return
        key, _ = SERVICE_PIPELINES[name]
        if name == 'm365' and not isinstance(result[0], dict):
            state.update(status='failed', finished_at=_timestamp(),
                         reason='M365 returned no completed service result. Tenant controls remain not assessed.')
            self.results[key] = _unfinished_result(name, state)
            self.save()
            return
        self.results[key] = result
        state.update(status='completed', finished_at=_timestamp(),
                     availability_status='recorded',
                     reason='Pipeline finished; consult individual source coverage for collection completeness.')
        state.pop('available', None)
        self.save()

    def record_sharepoint(self, payload):
        """Keep completed SharePoint evidence even if the later M365 task stops."""
        if self.progress['services']['m365']['status'] != 'pending':
            return
        from .sharepoint_governance import build_sharepoint_recommendations
        self.results['m365_result'][0]['_client'] = SimpleNamespace(sharepoint_governance=payload)
        self.results['m365_result'][1].extend(build_sharepoint_recommendations(payload))
        self.progress['sharepoint_finished_at'] = _timestamp()
        self.save()

    def add_supplemental_inputs(self, inputs):
        """Retain exports generated by an administrative collector in this package."""
        from .assessment_package import package_inputs
        prefix = 'collected_' + uuid4().hex[:8]
        manifest, references = package_inputs(self.package['folder'] / prefix, inputs)
        for role, paths in manifest['inputs'].items():
            self.package['manifest']['inputs'].setdefault(role, []).extend(
                str(Path(prefix) / path).replace('\\', '/') for path in paths)
        for record in manifest['files']:
            record['path'] = str(Path(prefix) / record['path']).replace('\\', '/')
        self.package['manifest']['files'].extend(manifest['files'])
        self.package['manifest']['diagnostics'].extend(manifest['diagnostics'])
        self.package['references'].update({key: str(Path(prefix) / value).replace('\\', '/')
                                           for key, value in references.items()})

    def finish(self, status):
        self.progress.update(status=status, finished_at=_timestamp())
        for name, state in self.progress['services'].items():
            if state['status'] != 'pending':
                continue
            state.update(status='interrupted' if status == 'interrupted' else 'failed',
                         reason=f'{SERVICE_PIPELINES[name][1]} collection did not finish. Apart from separately saved sources, its tenant controls remain not assessed.')
            key = SERVICE_PIPELINES[name][0]
            previous = self.results[key]
            self.results[key] = _unfinished_result(name, state)
            if name == 'm365' and isinstance(previous[0], dict) and previous[0].get('_client'):
                self.results[key][0]['_client'] = previous[0]['_client']
                self.results[key][1].extend(row for row in previous[1]
                                           if row.get('FindingKey') != 'collection.unfinished.m365')
        self.save()


async def run_checkpointed_pipelines(pipelines, checkpoint):
    """Persist each result before dependants proceed, and drain tasks on failure."""
    from . import console_reporting as console
    if not checkpoint.package:
        checkpoint.save()
    console.detail(f'Collection checkpoint: {checkpoint.path}')

    async def run(name, *args):
        result = await pipelines[name](*args)
        checkpoint.record(name, result)
        return result

    purview_task = asyncio.create_task(run('purview'))
    tasks = [purview_task] + [
        asyncio.create_task(run(name, purview_task) if name == 'defender' else run(name))
        for name in SERVICE_PIPELINES if name != 'purview'
    ]
    try:
        await asyncio.gather(*tasks)
        checkpoint.finish('completed')
    except BaseException as exc:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            checkpoint.finish('interrupted' if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)) else 'failed')
        except Exception:
            console.status('Could not update collection progress. The last successfully saved checkpoint remains available.', tone='warning')
        console.status('Collection stopped. Completed service evidence can be rebuilt offline; unfinished services remain not assessed.', tone='warning')
        console.print_collection_handoff(checkpoint.path)
        raise
    return checkpoint.path

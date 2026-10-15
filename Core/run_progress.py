"""Numbered CLI stages and wall-clock durations, separate from tenant evidence."""

from contextvars import ContextVar
import time

from . import console_reporting as console
from .spinner import get_timestamp


_current_run = ContextVar('assessment_console_run', default=None)


def format_duration(seconds):
    seconds = max(0, int(seconds))
    if seconds == 0:
        return '<1s'
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f'{hours}h {minutes:02d}m {seconds:02d}s'
    if minutes:
        return f'{minutes}m {seconds:02d}s'
    return f'{seconds}s'


def phase(key):
    """Advance the CLI display; library callers do not acquire console state."""
    progress = _current_run.get()
    if progress is not None:
        progress.phase(key)


def record_connection_results(results):
    """Keep the preflight receipt for the final CLI summary, after the auth plan."""
    progress = _current_run.get()
    if progress is None or not progress._check_connections:
        return False
    progress._connection_results = [dict(result) for result in results]
    return True


class RunProgress:
    def __init__(self, *, offline=False, check_connections=False):
        self._check_connections = check_connections and not offline
        self.stages = [('startup', 'Check configuration and dependencies')]
        if offline:
            self.stages += [('inputs', 'Load saved evidence and exports'),
                            ('reports', 'Build reports')]
        else:
            self.stages += [('access', 'Prepare and verify access')]
            if not check_connections:
                self.stages += [('collection', 'Collect tenant evidence'),
                                ('reports', 'Build reports')]
        self._timings = []
        self._stage = None
        self._connection_results = None

    def __enter__(self):
        self._started = self._stage_started = time.monotonic()
        self._token = _current_run.set(self)
        console.status(f'Started: {get_timestamp()}')
        self.phase('startup')
        return self

    def phase(self, key):
        if key == self._stage:
            return
        index, label = next((index, label) for index, (name, label)
                            in enumerate(self.stages, 1) if name == key)
        now = time.monotonic()
        self._finish_stage(now, 'done')
        self._stage, self._stage_started = key, now
        console.section(f'[{index}/{len(self.stages)}] {label}')
        console.status(f'Elapsed: {format_duration(now - self._started)}', 'muted')

    def _finish_stage(self, now, state):
        if self._stage is not None:
            label = dict(self.stages)[self._stage]
            self._timings.append((label, now - self._stage_started, state))

    def __exit__(self, exc_type, exc, traceback):
        try:
            now = time.monotonic()
            interrupted = isinstance(exc, KeyboardInterrupt)
            succeeded = exc_type is None or (isinstance(exc, SystemExit) and exc.code in (None, 0))
            checked_connections = (self._check_connections and self._stage == 'access'
                                   and (succeeded or isinstance(exc, SystemExit) and exc.code in (1, 2)))
            summary = None
            if checked_connections and self._connection_results is not None:
                from .connection_validation import summarize_connections
                summary = summarize_connections(self._connection_results)
            state = 'done' if succeeded else 'interrupted' if interrupted else 'failed'
            if summary is not None:
                state = summary['stage_state']
            elif checked_connections and not succeeded:
                state = 'done with issues'
            self._finish_stage(now, state)
            tone = 'success' if succeeded else 'warning' if interrupted else 'error'
            title = 'RUN COMPLETE' if succeeded else 'RUN INTERRUPTED' if interrupted else 'RUN FAILED'
            if summary is not None:
                title, tone = summary['title'], summary['tone']
            elif checked_connections and not succeeded:
                title, tone = 'CONNECTION CHECKS COMPLETE WITH ISSUES', 'warning'
            elif succeeded and self._check_connections:
                title = 'CONNECTION CHECKS COMPLETE'
            console.section(title, tone)
            console.status(f'Total elapsed: {format_duration(now - self._started)}', tone)
            console.status(f'Finished: {get_timestamp()}', 'muted')
            for label, duration, stage_state in self._timings:
                console.print_paragraph(f'{label}: {format_duration(duration)} ({stage_state})', indent='  ')
            if summary is not None:
                from .connection_validation import print_connection_summary
                print_connection_summary(summary)
            elif succeeded and not self._check_connections:
                console.detail('Run completion describes execution; review the assessment decision and source gaps for readiness.')
        finally:
            _current_run.reset(self._token)
        return False

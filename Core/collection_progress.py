"""Live progress for tenant evidence collection.

One bar per service (SharePoint, M365, Entra, Defender, Purview and any
optional collector that runs) plus an overall bar, drawn at the bottom of an
interactive terminal. Every other console message keeps printing above the
bars: while the display is active, standard output and standard error pass
through a stream that erases the bars, writes the message and redraws them.
A heartbeat redraw keeps the elapsed time moving and names the reads still in
flight, so a slow Microsoft API or a waiting browser sign-in never looks stuck.

Nothing is drawn when output is not a terminal (tests, redirected logs) or when
--no-progress is used; services still record their counts, which is harmless.
"""

import os
import shutil
import sys
import threading
import time
from contextlib import contextmanager


# Short labels for dataset names used by the collectors.
FRIENDLY_NAMES = {
    'ca_policies': 'Conditional Access', 'auth_methods': 'MFA registration', 'risky_users': 'risky users',
    'risk_detections': 'risk detections', 'role_definitions': 'role definitions', 'role_assignments': 'role assignments',
    'role_eligibility_schedules': 'eligible roles', 'role_assignment_schedules': 'active role schedules',
    'access_reviews': 'access reviews', 'managed_devices': 'Intune devices', 'compliance_policies': 'device compliance',
    'groups': 'licensed groups', 'guests': 'guest users', 'service_principals': 'enterprise apps',
    'oauth_grants': 'app consent grants', 'consent_policies': 'consent policies', 'signin_logs': 'sign-in logs',
    'app_signin_summary': 'app sign-ins', 'service_principal_signin_activities': 'app sign-in activity',
    'cross_tenant_policy': 'cross-tenant policy', 'authorization_policy': 'authorization policy',
    'security_defaults': 'security defaults', 'users': 'users', 'external_connections': 'Copilot connectors',
    'email_activity': 'email activity', 'teams_activity': 'Teams activity', 'sharepoint_usage': 'SharePoint usage',
    'onedrive_usage': 'OneDrive usage', 'office_activations': 'Office activations', 'active_users': 'active users',
    'sites': 'sites', 'report_settings': 'report settings', 'copilot_interaction_audit': 'Copilot audit log',
    'ai_usage': 'Copilot usage reports', 'alerts': 'alerts', 'incidents': 'incidents', 'secure_scores': 'Secure Score',
    'secure_score_controls': 'Secure Score controls', 'machines': 'Defender devices',
}
FINISHED = {'done', 'partial', 'failed', 'skipped'}
SPINNER = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'


def label_for(name):
    return FRIENDLY_NAMES.get(name, str(name).replace('_', ' '))


class _Service:
    def __init__(self, key, label):
        self.key, self.label = key, label
        self.total = 0
        self.done = 0
        self.running = []      # parallel reads still in flight
        self.current = ''      # sequential step (PowerShell collectors)
        self.note = ''         # for example "waiting for browser sign-in"
        self.state = 'waiting'

    def fraction(self):
        if self.state in FINISHED:
            return 1.0
        # Reads can all be back while results are still processed; only a
        # finished service reaches 100%.
        return min(0.95, self.done / self.total) if self.total else 0.0


class _ProgressStream:
    """Stream wrapper that keeps the bars below every line written."""

    def __init__(self, owner, target):
        self._owner = owner
        self._target = target

    def write(self, text):
        return self._owner._write(self._target, text)

    def flush(self):
        return self._target.flush()

    def __getattr__(self, name):
        return getattr(self._target, name)


class CollectionProgress:
    def __init__(self):
        self._lock = threading.RLock()
        self._services = {}
        self._active = False
        self._paused = False
        self._out = None
        self._saved_streams = None
        self._drawn = 0
        self._at_line_start = True
        self._started = None
        self._frame = 0
        self._stop_event = threading.Event()
        self._thread = None
        self._ascii = False
        self._color = False

    # ----- service state (safe whether or not the display is active) -----

    def reset(self):
        with self._lock:
            self._services = {}

    def register(self, key, label):
        with self._lock:
            if key not in self._services:
                self._services[key] = _Service(key, label)
            self._refresh()

    def _service(self, key, label=None):
        service = self._services.get(key)
        if service is None:
            service = self._services[key] = _Service(key, label or key)
        if service.state == 'waiting':
            service.state = 'running'
        return service

    def begin(self, key, label=None):
        with self._lock:
            self._service(key, label)
            self._refresh()

    def set_total(self, key, total):
        with self._lock:
            service = self._service(key)
            service.total = max(int(total or 0), service.done)
            self._refresh()

    def track(self, key, tasks):
        """Wrap a dict of coroutines so each completion advances the service bar."""
        with self._lock:
            service = self._service(key)
            service.total += len(tasks)
            service.running.extend(tasks)
            self._refresh()
        return {name: self._tracked(key, name, coroutine) for name, coroutine in tasks.items()}

    async def _tracked(self, key, name, coroutine):
        try:
            return await coroutine
        finally:
            with self._lock:
                service = self._services.get(key)
                if service is not None:
                    if name in service.running:
                        service.running.remove(name)
                    service.done = min(service.done + 1, service.total or service.done + 1)
                    service.note = ''
                self._refresh()

    def step(self, key, label):
        """Sequential collectors: the previous step is complete; ``label`` has started."""
        with self._lock:
            service = self._service(key)
            if service.current:
                service.done += 1
            service.current = label
            service.note = ''
            service.total = max(service.total, service.done + 1)
            self._refresh()

    def end_steps(self, key):
        with self._lock:
            service = self._services.get(key)
            if service is not None and service.current:
                service.done += 1
                service.current = ''
                service.total = max(service.total, service.done)
            self._refresh()

    def note(self, key, text):
        with self._lock:
            self._service(key).note = text
            self._refresh()

    def finish(self, key, *, done=None, total=None, state='done', only_if_open=False):
        with self._lock:
            service = self._services.get(key)
            if service is None or (only_if_open and service.state in FINISHED):
                return
            if only_if_open and service.state == 'waiting':
                state = 'skipped'
            if total is not None:
                service.total = int(total)
            if done is not None:
                service.done = int(done)
            elif state in {'done', 'partial'} and service.total:
                service.done = max(service.done, service.total if state == 'done' else service.done)
            service.running, service.current, service.note = [], '', ''
            service.state = state
            self._refresh()

    def snapshot(self):
        with self._lock:
            return {key: {'label': s.label, 'done': s.done, 'total': s.total, 'state': s.state,
                          'running': list(s.running), 'current': s.current, 'note': s.note}
                    for key, s in self._services.items()}

    # ----- display -----

    def active(self):
        return self._active

    def start(self, *, enabled=True, stream=None):
        """Draw the bars when output is an interactive terminal. Returns True when shown."""
        stream = stream or sys.stdout
        if self._active or not enabled or os.environ.get('ASSESSMENT_PROGRESS', '').lower() == 'never':
            return False
        if os.environ.get('TERM') == 'dumb' or not getattr(stream, 'isatty', lambda: False)():
            return False
        from .console_reporting import enable_virtual_terminal, _use_color
        if not enable_virtual_terminal():
            return False
        encoding = getattr(stream, 'encoding', None) or 'utf-8'
        try:
            ('█░' + SPINNER).encode(encoding)
            self._ascii = False
        except (UnicodeEncodeError, LookupError):
            self._ascii = True
        self._color = _use_color()
        with self._lock:
            self._out = stream
            self._saved_streams = (sys.stdout, sys.stderr)
            sys.stdout = _ProgressStream(self, stream)
            if getattr(sys.stderr, 'isatty', lambda: False)():
                sys.stderr = _ProgressStream(self, sys.stderr)
            self._active = True
            self._paused = False
            self._drawn = 0
            self._at_line_start = True
            self._started = time.monotonic()
            self._stop_event.clear()
            self._out.write('\x1b[?25l')   # hide the cursor while bars redraw
            self._draw()
        self._thread = threading.Thread(target=self._heartbeat, name='collection-progress', daemon=True)
        self._thread.start()
        return True

    def stop(self):
        """Remove the bars and restore normal output. Safe to call more than once."""
        thread = None
        with self._lock:
            if not self._active:
                return
            self._erase()
            self._out.write('\x1b[?25h')
            self._out.flush()
            stdout, stderr = self._saved_streams
            if isinstance(sys.stdout, _ProgressStream):
                sys.stdout = stdout
            if isinstance(sys.stderr, _ProgressStream):
                sys.stderr = stderr
            self._active = False
            self._stop_event.set()
            thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)

    def elapsed(self):
        return time.monotonic() - self._started if self._started else 0.0

    @contextmanager
    def paused(self):
        """Hide the bars while the operator answers a console prompt."""
        with self._lock:
            was_paused = self._paused
            self._paused = True
            if self._active:
                self._erase()
        try:
            yield
        finally:
            with self._lock:
                self._paused = was_paused
                self._at_line_start = True
                self._refresh()

    def _heartbeat(self):
        while not self._stop_event.wait(0.25):
            with self._lock:
                self._frame += 1
                self._refresh()

    def _refresh(self):
        # One write per redraw keeps the heartbeat from flickering.
        if self._active and not self._paused and self._at_line_start:
            lines = self.render_lines()
            self._out.write(self._erase_sequence() + '\n'.join(lines))
            self._out.flush()
            self._drawn = len(lines)

    def _write(self, target, text):
        with self._lock:
            if not text:
                return 0
            if not self._active:
                return target.write(text)
            self._erase()
            written = target.write(text)
            self._at_line_start = str(text).endswith('\n')
            if self._at_line_start:
                target.flush()
                if not self._paused:
                    self._draw()
            return written

    def _erase_sequence(self):
        if not self._drawn:
            return ''
        up = f'\x1b[{self._drawn - 1}A' if self._drawn > 1 else ''
        return '\r' + up + '\x1b[J'

    def _erase(self):
        if self._drawn:
            self._out.write(self._erase_sequence())
            self._out.flush()
            self._drawn = 0

    def _draw(self):
        if not self._active or self._paused:
            return
        lines = self.render_lines()
        self._out.write('\n'.join(lines))
        self._out.flush()
        self._drawn = len(lines)

    # ----- rendering -----

    def _bar(self, fraction, width, finished_state=''):
        filled = int(round(max(0.0, min(1.0, fraction)) * width))
        full, empty = ('#', '-') if self._ascii else ('█', '░')
        text = full * filled + empty * (width - filled)
        if self._color:
            tone = '91' if finished_state == 'failed' else '93' if finished_state == 'partial' else '92' if finished_state == 'done' else '96'
            return f'\x1b[{tone}m{text}\x1b[0m'
        return text

    def render_lines(self, columns=None):
        columns = columns or shutil.get_terminal_size((100, 20)).columns
        width = max(40, columns - 1)
        with self._lock:
            services = list(self._services.values())
            counted = [s for s in services if s.state != 'skipped']
            overall = sum(s.fraction() for s in counted) / len(counted) if counted else 0.0
            done = sum(s.done for s in counted)
            total = sum(s.total for s in counted)
            seconds = int(self.elapsed())
            spinner = ('|/-\\' if self._ascii else SPINNER)[self._frame % (4 if self._ascii else len(SPINNER))]
            head_prefix = 'Collecting tenant evidence '
            head_tail = f' {int(overall * 100):>3}%  {done}/{total} datasets  {seconds // 60}:{seconds % 60:02d} {spinner}'
            bar_width = max(10, min(30, width - len(head_prefix) - len(head_tail) - 2))
            lines = [head_prefix + '[' + self._bar(overall, bar_width) + ']' + head_tail[:max(0, width - len(head_prefix) - bar_width - 2)]]
            label_width = max([len(s.label) for s in services] + [8])
            for service in services:
                prefix = f'  {service.label:<{label_width}} '
                counts = f' {service.done:>3}/{service.total:<3} ' if service.total else '   …    '
                if service.state == 'waiting':
                    status = 'waiting to start'
                elif service.state in FINISHED:
                    status = {'done': 'done', 'partial': 'partial - see source gaps', 'failed': 'unavailable',
                              'skipped': 'skipped'}[service.state]
                elif service.note:
                    status = service.note
                elif service.current:
                    status = service.current
                elif service.running:
                    names = [label_for(name) for name in service.running]
                    status = 'reading ' + ', '.join(names[:3]) + (f' (+{len(names) - 3} more)' if len(names) > 3 else '')
                else:
                    status = 'processing results'
                small = 16
                room = width - len(prefix) - small - 2 - len(counts)
                lines.append(prefix + '[' + self._bar(service.fraction(), small, service.state if service.state in FINISHED else '')
                             + ']' + counts + (status[:room - 1] + '…' if len(status) > room > 1 else status if room > 0 else ''))
            return lines


progress = CollectionProgress()

reset = progress.reset
register = progress.register
begin = progress.begin
set_total = progress.set_total
track = progress.track
step = progress.step
end_steps = progress.end_steps
note = progress.note
finish = progress.finish
start = progress.start
stop = progress.stop
paused = progress.paused
active = progress.active
snapshot = progress.snapshot


def handle_collector_line(key, line):
    """Apply one PROGRESS line from a PowerShell collector. Returns True when handled.

    PROGRESS:TOTAL:<n>, PROGRESS:STEP:<label>, PROGRESS:NOTE:<text> and PROGRESS:END.
    """
    if not str(line).startswith('PROGRESS:'):
        return False
    parts = str(line).strip().split(':', 2)
    kind = parts[1] if len(parts) > 1 else ''
    value = parts[2] if len(parts) > 2 else ''
    if kind == 'TOTAL':
        try:
            set_total(key, int(value))
        except ValueError:
            pass
    elif kind == 'STEP':
        step(key, value)
    elif kind == 'NOTE':
        note(key, value)
    elif kind == 'END':
        end_steps(key)
    return True

"""Authenticate collectors first, keeping their process-local sessions alive.

Collectors stop at AUTH_READY until the parent releases collection. Existing
collectors consume the same streams and results. The handshake stores no tokens.
"""

import asyncio
from contextvars import ContextVar
import queue
import threading


_group = ContextVar('assessment_authentication_group', default=None)
_collector = ContextVar('assessment_authentication_collector', default=None)
AUTHENTICATION_TIMEOUT = 900


class AuthenticationPreparationTimeout(TimeoutError):
    pass


class _Stream:
    def __init__(self):
        self.lines = queue.Queue()
        self.ended = False

    def readline(self):
        if self.ended:
            return ''
        line = self.lines.get()
        if line is None:
            self.ended = True
            return ''
        return line

    def read(self):
        return ''.join(iter(self.readline, ''))

    def __iter__(self):
        return iter(self.readline, '')


class PreparedProcess:
    """Drain both OS pipes while the collector waits at its authentication gate."""
    def __init__(self, process, loop, changed):
        self.process = process
        self.stdout, self.stderr = _Stream(), _Stream()
        self.ready = False
        self.released = False
        self.readers = []
        self.loop, self.changed = loop, changed
        for pipe, stream, is_error in ((process.stdout,self.stdout,False),(process.stderr,self.stderr,True)):
            reader = threading.Thread(target=self._pump,args=(pipe,stream,is_error),daemon=True)
            reader.start()
            self.readers.append(reader)

    def _notify(self):
        try:
            self.loop.call_soon_threadsafe(self.changed.set)
        except RuntimeError:
            pass  # Event loop has already closed during interruption cleanup.

    def _pump(self, pipe, stream, is_error):
        try:
            for line in iter(pipe.readline, ''):
                if is_error and line.strip().startswith('AUTH_READY:'):
                    self.ready = True
                    self._notify()
                stream.lines.put(line)
        except (OSError, ValueError):
            pass  # Pipe closed during interruption cleanup.
        finally:
            stream.lines.put(None)
            if is_error:
                self._notify()
            pipe.close()

    @property
    def returncode(self):
        return self.process.returncode

    def wait(self):
        return self.process.wait()

    def communicate(self):
        stdout = self.stdout.read()
        stderr = self.stderr.read()
        self.wait()
        return stdout, ''.join(line for line in stderr.splitlines(keepends=True) if not line.strip().startswith('AUTH_READY:'))

    def release(self):
        if self.released or self.process.poll() is not None:
            return
        if not self.ready:
            raise RuntimeError('Collector authentication has not completed.')
        self.released = True
        try:
            self.process.stdin.write('COLLECT\n')
            self.process.stdin.flush()
        except (OSError, ValueError):
            pass  # Collector failure is reported by its normal result parser.
        finally:
            self._close_input()

    def _close_input(self):
        if self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except (OSError, ValueError):
                pass
            self.process.stdin = None

    def stop(self):
        try:
            if self.process.poll() is None:
                self.process.kill()
        except ProcessLookupError:
            pass
        self.process.wait()
        self._close_input()
        for reader in self.readers:
            reader.join(timeout=2)


class AuthenticationPreparation:
    def __init__(self, *, timeout=AUTHENTICATION_TIMEOUT):
        self.timeout = timeout
        self.processes, self.tasks = {}, {}
        self.changed = asyncio.Event()
        self.loop = asyncio.get_running_loop()
        self.released = False
        self.context_token = None

    def activate(self):
        self.context_token = _group.set(self)
        return self

    def attach(self, process):
        label = _collector.get()
        if self.released or not label or label in self.processes:
            process.kill()
            process.wait()
            raise RuntimeError('A new sign-in cannot start after authentication preparation.')
        prepared = PreparedProcess(process,self.loop,self.changed)
        self.processes[label] = prepared
        self.changed.set()
        return prepared

    def verify_launch(self):
        label = _collector.get()
        if self.released or not label or label in self.processes:
            raise RuntimeError('Authentication preparation is complete or this collector was not selected; rerun to establish a new service sign-in.')

    async def prepare(self, label, operation):
        """Run one sign-in at a time, including any in-process fallback methods."""
        async def run():
            token = _group.set(self)
            label_token = _collector.set(label)
            try:
                return await operation
            finally:
                _collector.reset(label_token)
                _group.reset(token)
                self.changed.set()
        task = asyncio.create_task(run(),name='authenticate-'+label)
        self.tasks[label] = task
        async def wait_ready():
            while True:
                self.changed.clear()
                process = self.processes.get(label)
                if process and process.ready:
                    return
                if task.done():
                    # Cached/skipped collectors and failures before AUTH_READY
                    # have a completed normal result, with no later auth retry.
                    task.result()
                    return
                await self.changed.wait()
        try:
            await asyncio.wait_for(wait_ready(),timeout=self.timeout)
        except asyncio.TimeoutError:
            await self.close()
            raise AuthenticationPreparationTimeout(
                f'{label} sign-in preparation timed out. Complete the required sign-in and rerun the assessment.') from None
        except BaseException:
            await self.close()
            raise
        return task

    def release(self):
        self.released = True
        for process in self.processes.values():
            if process.ready:
                process.release()

    async def close(self):
        for process in self.processes.values():
            await asyncio.to_thread(process.stop)
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(),return_exceptions=True)
        if self.context_token is not None:
            _group.reset(self.context_token)
            self.context_token = None


def active_preparation():
    return _group.get()

"""Live collection progress bars: rendering, stream handling and collector lines."""

import asyncio
import io
import re
import sys
import unittest
from unittest.mock import patch

from Core.collection_progress import CollectionProgress


class FakeTerminal(io.StringIO):
    encoding = 'utf-8'

    def isatty(self):
        return True


def plain(text):
    return re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', text)


class RenderingTests(unittest.TestCase):
    def setUp(self):
        self.progress = CollectionProgress()

    def test_parallel_reads_advance_and_name_what_is_still_running(self):
        progress = self.progress
        progress.register('entra', 'Entra')

        async def read(value, delay):
            await asyncio.sleep(delay)
            return value

        async def run():
            tasks = progress.track('entra', {'ca_policies': read(1, 0), 'signin_logs': read(2, 0.05)})
            first = asyncio.ensure_future(tasks['ca_policies'])
            second = asyncio.ensure_future(tasks['signin_logs'])
            await first
            middle = progress.snapshot()['entra']
            rendered = plain('\n'.join(progress.render_lines(120)))
            await second
            return middle, rendered

        middle, rendered = asyncio.run(run())
        self.assertEqual((middle['done'], middle['total'], middle['running']), (1, 2, ['signin_logs']))
        self.assertIn('reading sign-in logs', rendered)
        final = progress.snapshot()['entra']
        self.assertEqual((final['done'], final['total']), (2, 2))
        # All reads back but not yet summarized: not shown as 100%.
        self.assertIn(' 95%', plain(progress.render_lines(120)[0]))
        self.assertIn('processing results', plain('\n'.join(progress.render_lines(120))))

    def test_overall_bar_averages_services_and_skips_unused_ones(self):
        progress = self.progress
        for key in ('m365', 'entra', 'power_platform'):
            progress.register(key, key)
        progress.set_total('m365', 4)
        progress.step('m365', 'a')
        progress.step('m365', 'b')           # a finished: 1 of 4
        progress.finish('entra', done=10, total=10)
        progress.finish('power_platform', only_if_open=True)  # never started: skipped
        header = plain(progress.render_lines(120)[0])
        self.assertIn(' 62%', header)       # (0.25 + 1.0) / 2
        self.assertIn('11/14 datasets', header)
        lines = plain('\n'.join(progress.render_lines(120)))
        self.assertIn('skipped', lines)
        self.assertIn('done', lines)

    def test_lines_never_exceed_the_terminal_width(self):
        progress = self.progress
        progress.register('m365', 'M365')
        progress.note('m365', 'waiting for a very long explanation ' * 5)
        for line in progress.render_lines(60):
            self.assertLessEqual(len(plain(line)), 59, line)

    def test_collector_lines_step_through_sequential_datasets(self):
        from Core import collection_progress as module
        with patch.object(module, 'progress', self.progress), \
                patch.multiple(module, set_total=self.progress.set_total, step=self.progress.step,
                               note=self.progress.note, end_steps=self.progress.end_steps):
            self.assertTrue(module.handle_collector_line('purview', 'PROGRESS:TOTAL:3'))
            module.handle_collector_line('purview', 'PROGRESS:STEP:DLP policies')
            module.handle_collector_line('purview', 'PROGRESS:NOTE:waiting for the browser sign-in')
            state = self.progress.snapshot()['purview']
            self.assertEqual((state['done'], state['total'], state['note']), (0, 3, 'waiting for the browser sign-in'))
            module.handle_collector_line('purview', 'PROGRESS:STEP:sensitivity labels')
            module.handle_collector_line('purview', 'PROGRESS:END')
            self.assertFalse(module.handle_collector_line('purview', 'AUTH_PROMPT:Purview'))
        state = self.progress.snapshot()['purview']
        self.assertEqual((state['done'], state['total'], state['current'], state['note']), (2, 3, '', ''))


class TerminalTests(unittest.TestCase):
    def test_not_drawn_when_output_is_not_a_terminal(self):
        progress = CollectionProgress()
        before = sys.stdout
        self.assertFalse(progress.start(stream=io.StringIO()))
        self.assertIs(sys.stdout, before)

    def test_disabled_by_option_or_environment(self):
        progress = CollectionProgress()
        self.assertFalse(progress.start(enabled=False, stream=FakeTerminal()))
        with patch.dict('os.environ', {'ASSESSMENT_PROGRESS': 'never'}):
            self.assertFalse(progress.start(stream=FakeTerminal()))

    def test_messages_print_above_the_bars_and_stop_restores_output(self):
        terminal = FakeTerminal()
        progress = CollectionProgress()
        progress.register('m365', 'M365')
        original = sys.stdout
        with patch('Core.console_reporting.enable_virtual_terminal', return_value=True), \
                patch('Core.console_reporting._use_color', return_value=False), \
                patch('sys.stderr', new=io.StringIO()):
            self.assertTrue(progress.start(stream=terminal))
            try:
                self.assertIsNot(sys.stdout, original)
                print('Entra: collecting identity evidence...')
                with progress.paused():
                    sys.stdout.write('Enter a URL: ')
                progress.finish('m365', done=3, total=3)
            finally:
                progress.stop()
                sys.stdout = original if sys.stdout is not original and not hasattr(sys.stdout, '_owner') else sys.stdout
        self.assertIs(sys.stdout, original)
        output = terminal.getvalue()
        visible = plain(output)
        self.assertIn('Entra: collecting identity evidence...\n', visible)
        self.assertIn('Collecting tenant evidence', visible)
        # The bars are erased (cursor up + clear) before each message and at stop.
        self.assertIn('\x1b[J', output)
        self.assertTrue(output.endswith('\x1b[?25h'), repr(output[-40:]))
        self.assertLess(visible.index('Collecting tenant evidence'), visible.index('Entra: collecting'))

    def test_stop_is_safe_to_repeat_and_without_start(self):
        progress = CollectionProgress()
        progress.stop()
        progress.stop()


if __name__ == '__main__':
    unittest.main()

"""An explicit mode must never silently select the other execution path."""

import contextlib
import io
import unittest
from unittest.mock import patch

from Core.cli_parser import parse_arguments


class ExecutionModeTests(unittest.TestCase):
    def parse(self, *arguments):
        with patch('sys.argv', ['main.py', *arguments]):
            return parse_arguments(None, [])

    def test_mode_selection_and_compatibility(self):
        cases = [
            ([], 'live'),
            (['--mode', 'live'], 'live'),
            (['--mode', 'offline'], 'offline'),
            (['--offline'], 'offline'),
            (['--collection-input', 'saved.json'], 'offline'),
            (['--prior-report', 'prior.xlsx'], 'offline'),
            (['--purview-cache', 'cached.json'], 'offline'),
            (['--mode', 'offline', '--collection-input', 'saved.json'], 'offline'),
            (['--mode', 'live', '--reports-dir', 'exports'], 'live'),
        ]
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                args = self.parse(*arguments)
                self.assertEqual(args.mode, expected)
                self.assertEqual(args.offline, expected == 'offline')

    def test_conflicting_modes_and_live_operations_are_rejected(self):
        cases = [
            ['--mode', 'live', '--collection-input', 'saved.json'],
            ['--mode', 'live', '--prior-report', 'prior.xlsx'],
            ['--mode', 'live', '--purview-cache', 'cached.json'],
            ['--collection-input', 'saved.json', '--purview-cache', 'cached.json'],
            ['--mode', 'live', '--offline'],
            ['--offline', '--mode', 'live'],
            ['--mode', 'offline', '--save-collection', 'saved.json'],
            ['--mode', 'offline', '--check-connections'],
            ['--mode', 'offline', '--preview-collectors', 'all'],
            ['--mode', 'offline', '--legacy-power-platform-collector'],
            ['--mode', 'offline', '--env-file', 'credentials.env'],
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    self.parse(*arguments)
                self.assertEqual(raised.exception.code, 2)


if __name__ == '__main__':
    unittest.main()

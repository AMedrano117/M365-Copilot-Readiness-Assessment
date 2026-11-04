"""Exercise operator diagnostic with mocked Graph; never authenticate or use a tenant."""
from pathlib import Path
import shutil
import subprocess
import unittest
import os


class IntuneProbeTests(unittest.TestCase):
    def test_read_only_probe_against_mocked_graph(self):
        windows_shell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        shell = str(windows_shell) if windows_shell.exists() else shutil.which('pwsh')
        if not shell:
            self.skipTest('PowerShell unavailable')
        completed = subprocess.run([shell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(Path(__file__).with_name('Test-IntuneEvidenceProbe.ps1'))],
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stdout+completed.stderr)


if __name__ == '__main__':
    unittest.main()

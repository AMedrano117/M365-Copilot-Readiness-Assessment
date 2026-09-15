"""Offline optional CSV imports must work without installed live collectors."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class OptionalOfflineImportTests(unittest.TestCase):
    def test_optional_csv_loaders_run_without_site_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dashboard.csv").write_text(
                "Report date,Returning users,Total prompts\n2026-09-15,3,10\n", encoding="utf-8"
            )
            (root / "inventory.csv").write_text(
                "id,type,display name,environment id\nexample-app,microsoft.powerapps/canvasapps,Example app,example-env\n",
                encoding="utf-8",
            )
            script = """
import sys
from pathlib import Path
from Core.ai_usage import load_copilot_dashboard_export
from Core.power_platform_inventory import load_power_platform_inventory
root = Path(sys.argv[1])
dashboard = load_copilot_dashboard_export(root / 'dashboard.csv')
inventory = load_power_platform_inventory(root / 'inventory.csv')
assert dashboard['available'] and dashboard['returning_users'] == 3, dashboard
assert inventory.power_platform_inventory['available'] and len(inventory.apps) == 1
assert not any(name == 'httpx' or name == 'azure' or name.startswith('azure.') or name == 'Core.get_graph_client' for name in sys.modules)
"""
            # -S disables site-packages: the regression fails even on a developer
            # machine that happens to have every live dependency installed.
            result = subprocess.run(
                [sys.executable, "-S", "-c", script, str(root)],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

import json
import os
import shutil
import unittest
from unittest.mock import patch

from Core.orchestrator_setup import get_local_powershell_module_availability


class PowerShellModuleSetupTests(unittest.TestCase):
    @patch("Core.orchestrator_setup.subprocess.run")
    @patch("Core.orchestrator_setup.shutil.which", return_value="powershell.exe")
    def test_default_m365_precheck_installs_sharepoint_module_when_missing(self, _which, run):
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps({
            "ExchangeOnlineManagement": True,
            "Az.Accounts": True,
            "Microsoft.Online.SharePoint.PowerShell": True,
            "sharepoint_install_attempted": True,
            "sharepoint_install_succeeded": True,
            "sharepoint_module_manifest": "C:\\Redirected Documents\\WindowsPowerShell\\Modules\\Microsoft.Online.SharePoint.PowerShell\\16.0.1\\Microsoft.Online.SharePoint.PowerShell.psd1",
        })

        status = get_local_powershell_module_availability(install_sharepoint=True)

        command = run.call_args.args[0]
        self.assertIn("Install-Module", command[3])
        self.assertIn("Microsoft.Online.SharePoint.PowerShell", command[3])
        self.assertEqual(180, run.call_args.kwargs["timeout"])
        self.assertTrue(status["Microsoft.Online.SharePoint.PowerShell"])
        self.assertTrue(status["sharepoint_install_attempted"])
        self.assertEqual(status["sharepoint_module_manifest"], os.environ["SHAREPOINT_MODULE_PATH"])
        self.assertIn("GetFolderPath('MyDocuments')", command[3])
        self.assertIn("OneDrive*", command[3])

    @patch("Core.orchestrator_setup.subprocess.run")
    @patch("Core.orchestrator_setup.shutil.which", return_value="powershell.exe")
    def test_read_only_precheck_does_not_install_modules(self, _which, run):
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps({
            "ExchangeOnlineManagement": False,
            "Az.Accounts": False,
            "Microsoft.Online.SharePoint.PowerShell": False,
        })

        get_local_powershell_module_availability(install_sharepoint=False)

        command = run.call_args.args[0]
        self.assertNotIn("Install-Module", command[3])
        self.assertEqual(15, run.call_args.kwargs["timeout"])

    @unittest.skipUnless(shutil.which("powershell"), "Windows PowerShell is not available")
    def test_real_read_only_probe_has_no_powershell_parser_error(self):
        status = get_local_powershell_module_availability(install_sharepoint=False)
        error = str(status.get("sharepoint_install_error", ""))
        self.assertNotIn("ParserError", error)
        self.assertNotIn("Missing '=' operator", error)


if __name__ == "__main__":
    unittest.main()

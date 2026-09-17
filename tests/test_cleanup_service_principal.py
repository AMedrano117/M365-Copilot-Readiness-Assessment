"""Exercise cloud teardown only through local PowerShell mocks (no tenant access)."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
TENANT = "11111111-1111-4111-8111-111111111111"
CLIENT = "22222222-2222-4222-8222-222222222222"
APP = "33333333-3333-4333-8333-333333333333"
SP = "44444444-4444-4444-8444-444444444444"

HARNESS = r"""
param([string]$Scenario, [switch]$Apply, [switch]$Simulate, [switch]$DeleteEnv, [switch]$Workload, [switch]$SavedSp, [string]$Profile = 'Restricted')
$ErrorActionPreference = 'Stop'
$global:cleanupCalls = [System.Collections.Generic.List[string]]::new()
$global:cleanupScopes = @()
$global:cleanupAppExists = $Scenario -notin @('missing','sp_only','retry_workload')
$global:cleanupSpExists = $Scenario -notin @('missing','app_only','retry_workload')
$global:cleanupWorkload = ''
$global:cleanupWorkloadRemoved = @{}
$global:cleanupMemberRemoved = @{}
$global:cleanupTenant = '11111111-1111-4111-8111-111111111111'
$global:cleanupClient = '22222222-2222-4222-8222-222222222222'
$global:cleanupAppId = '33333333-3333-4333-8333-333333333333'
$global:cleanupSpId = '44444444-4444-4444-8444-444444444444'
$global:cleanupName = 'M365 Copilot Readiness Assessment Tool'
if ($Profile -eq 'Restricted') { $global:cleanupName += ' - Restricted' }
function Import-Module { param($Name,$MinimumVersion,$ErrorAction) $global:cleanupCalls.Add("Import:$Name") }
function Get-Module { param($Name,[switch]$ListAvailable) if ($Scenario -eq 'old_module') { return [pscustomobject]@{Version=[version]'3.7.1'} }; return [pscustomobject]@{Version=[version]'3.7.2'} }
function Connect-MgGraph {
    param($TenantId,$ContextScope,$Scopes,[switch]$NoWelcome,$ErrorAction)
    if ([string]$TenantId -ne $global:cleanupTenant -or $ContextScope -ne 'Process') { throw 'Graph connection was not explicitly isolated to the confirmed tenant.' }
    $global:cleanupCalls.Add('GraphConnect'); $global:cleanupScopes = @($Scopes)
}
function Disconnect-MgGraph { param($ErrorAction) $global:cleanupCalls.Add('GraphDisconnect') }
function Get-MgContext { [pscustomobject]@{TenantId = if ($Scenario -eq 'wrong_tenant') {'99999999-9999-4999-8999-999999999999'} else {$global:cleanupTenant} } }
function Get-MgApplication {
    param($Filter,[switch]$All,$Property,$ErrorAction)
    $global:cleanupCalls.Add('ReadApplication')
    if ($Filter -ne "appId eq '$global:cleanupClient'" -or -not $All) { throw 'Application query was not exact and complete.' }
    if (-not $global:cleanupAppExists) { return }
    $app = [pscustomobject]@{
        Id=$global:cleanupAppId; AppId=if ($Scenario -eq 'wrong_app_id') {'99999999-9999-4999-8999-999999999999'} else {$global:cleanupClient}
        DisplayName=if ($Scenario -eq 'wrong_name') {'Shared customer application'} else {$global:cleanupName}
        PasswordCredentials=@([pscustomobject]@{SecretText='DO-NOT-PRINT-SECRET'}); KeyCredentials=@([pscustomobject]@{Key='DO-NOT-PRINT-CERTIFICATE'})
        RequiredResourceAccess=@([pscustomobject]@{ResourceAppId='resource';ResourceAccess=@([pscustomobject]@{Id='permission';Type='Role'})})
    }
    if ($Scenario -eq 'duplicate_app') { return @($app,$app) }
    return $app
}
function Get-MgServicePrincipal {
    param($Filter,[switch]$All,$Property,$ErrorAction)
    $global:cleanupCalls.Add('ReadPrincipal')
    if ($Filter -ne "appId eq '$global:cleanupClient'" -or -not $All) { throw 'Principal query was not exact and complete.' }
    if (-not $global:cleanupSpExists) { return }
    $principal = [pscustomobject]@{Id=$global:cleanupSpId;AppId=$global:cleanupClient;DisplayName=if ($Scenario -eq 'wrong_sp_name') {'Unrelated app'} else {$global:cleanupName}}
    if ($Scenario -eq 'duplicate_sp') { return @($principal,$principal) }
    return $principal
}
function Get-MgServicePrincipalAppRoleAssignment {
    param($ServicePrincipalId,[switch]$All,$ErrorAction)
    if ($ServicePrincipalId -ne $global:cleanupSpId -or -not $All) { throw 'Unexpected assignment query.' }
    $global:cleanupCalls.Add('ReadGrants')
    return @([pscustomobject]@{Id='grant-1'},[pscustomobject]@{Id='grant-2'})
}
function Remove-MgServicePrincipal {
    param($ServicePrincipalId,$Confirm,$ErrorAction)
    $global:cleanupCalls.Add("RemoveGraphPrincipal:$ServicePrincipalId")
    if ($ServicePrincipalId -ne $global:cleanupSpId) { throw 'Unexpected principal removal.' }
    if ($Scenario -eq 'sp_failure') { throw 'Synthetic principal removal failure.' }
    if ($Scenario -ne 'still_visible') { $global:cleanupSpExists = $false }
}
function Remove-MgApplication {
    param($ApplicationId,$Confirm,$ErrorAction)
    $global:cleanupCalls.Add("RemoveGraphApplication:$ApplicationId")
    if ($ApplicationId -ne $global:cleanupAppId) { throw 'Unexpected application removal.' }
    if ($Scenario -eq 'app_failure') { throw 'Synthetic application removal failure.' }
    if ($Scenario -ne 'still_visible') { $global:cleanupAppExists = $false; $global:cleanupSpExists = $false }
    if ($Scenario -eq 'changed_env') { Add-Content -LiteralPath (Join-Path $PSScriptRoot '.env.restricted') -Value 'CLIENT_ID=99999999-9999-4999-8999-999999999999' }
}
function Register-CleanupWorkloadMocks {
    param($Prefix,$Name)
    $global:cleanupWorkload = $Name
    $global:cleanupConnectionPrefix = $Prefix
    $global:cleanupCalls.Add("Connect:$Name")
    Set-Item -Path "Function:global:Get-${Prefix}ServicePrincipal" -Value {
        param($ErrorAction)
        $global:cleanupCalls.Add("ReadWorkload:$global:cleanupWorkload")
        if ($Scenario -eq 'workload_read_failure' -and $global:cleanupWorkload -eq 'Exchange') { throw 'Synthetic workload authorization failure.' }
        # Include unrelated references so missing/unknown identities cannot remove all.
        [pscustomobject]@{AppId='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';ObjectId='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';Identity='unrelated'}
        if ($Scenario -eq 'workload_missing' -or $global:cleanupWorkloadRemoved[$global:cleanupWorkload]) { return }
        $objectId = if ($Scenario -eq 'workload_mismatch' -and $global:cleanupWorkload -eq 'Exchange') {'99999999-9999-4999-8999-999999999999'} else {$global:cleanupSpId}
        $principal = [pscustomobject]@{AppId=$global:cleanupClient;ObjectId=$objectId;Identity=$objectId}
        if ($Scenario -eq 'workload_duplicate') { $principal }
        $principal
    }
    Set-Item -Path "Function:global:Get-${Prefix}RoleGroup" -Value {
        param($ResultSize,$ErrorAction)
        if ($Scenario -eq 'group_missing') { return }
        $name = "AI Readiness $global:cleanupWorkload Read-Only"
        [pscustomobject]@{Name=$name;Identity=$name}
        [pscustomobject]@{Name='Unrelated shared group';Identity='Unrelated shared group'}
    }
    Set-Item -Path "Function:global:Get-${Prefix}RoleGroupMember" -Value {
        param($Identity,$ResultSize,$ErrorAction)
        if ($Identity -ne "AI Readiness $global:cleanupWorkload Read-Only" -or $ResultSize -ne 'Unlimited') { throw 'Unexpected role group enumeration.' }
        [pscustomobject]@{ExternalDirectoryObjectId='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';Identity='unrelated-member'}
        if ($Scenario -notin @('workload_missing','membership_missing') -and -not $global:cleanupMemberRemoved[$global:cleanupWorkload]) {
            [pscustomobject]@{ExternalDirectoryObjectId=$global:cleanupSpId;Identity=$global:cleanupSpId}
        }
    }
    Set-Item -Path "Function:global:Remove-${Prefix}RoleGroupMember" -Value {
        param($Identity,$Member,$Confirm,$ErrorAction)
        if ($Identity -ne "AI Readiness $global:cleanupWorkload Read-Only" -or $Member -ne $global:cleanupSpId) { throw 'Unexpected membership removal.' }
        $global:cleanupCalls.Add("RemoveMember:$global:cleanupWorkload`:$Member")
        if ($Scenario -ne 'workload_still_visible') { $global:cleanupMemberRemoved[$global:cleanupWorkload] = $true }
    }
    Set-Item -Path "Function:global:Remove-${Prefix}ServicePrincipal" -Value {
        param($Identity,$Confirm,$ErrorAction)
        if ($Identity -ne $global:cleanupSpId) { throw 'Unexpected workload principal removal.' }
        $global:cleanupCalls.Add("RemoveWorkload:$global:cleanupWorkload`:$Identity")
        if ($Scenario -eq 'workload_remove_failure') { throw 'Synthetic workload removal failure.' }
        if ($Scenario -ne 'workload_still_visible') { $global:cleanupWorkloadRemoved[$global:cleanupWorkload] = $true }
    }
}
function Connect-IPPSSession { param($Prefix,[switch]$DisableWAM,$ErrorAction) Register-CleanupWorkloadMocks -Prefix $Prefix -Name 'Purview' }
function Connect-ExchangeOnline { param($Prefix,[switch]$DisableWAM,$ShowBanner,$ErrorAction) Register-CleanupWorkloadMocks -Prefix $Prefix -Name 'Exchange' }
function Get-ConnectionInformation {
    param($ModulePrefix,$ErrorAction)
    if ($ModulePrefix -ne $global:cleanupConnectionPrefix) { throw 'Connection lookup not isolated.' }
    [pscustomobject]@{
        TenantID=if ($Scenario -eq 'workload_wrong_tenant') {'99999999-9999-4999-8999-999999999999'} else {$global:cleanupTenant}
        ConnectionId="connection-$global:cleanupWorkload"; State='Connected'; IsEopSession=($global:cleanupWorkload -eq 'Purview')
    }
}
function Disconnect-ExchangeOnline { param($ConnectionId,$Confirm,$ErrorAction) $global:cleanupCalls.Add("Disconnect:$global:cleanupWorkload") }
function Read-Host { throw 'No interactive prompts are allowed in offline mocks.' }
$message = ''
try {
    $arguments = @{TenantId=$global:cleanupTenant;ClientId=$global:cleanupClient;PermissionProfile=$Profile;Apply=$Apply;WhatIf=$Simulate;RemoveEnvironmentFile=$DeleteEnv;IncludeWorkloadRbac=$Workload;Confirm=$false}
    if ($Scenario -eq 'confirmation_required') { $arguments.Remove('Confirm') }
    if ($SavedSp) { $arguments.ServicePrincipalObjectId = $global:cleanupSpId }
    if ($Scenario -eq 'wrong_saved_sp') { $arguments.ServicePrincipalObjectId = '99999999-9999-4999-8999-999999999999' }
    & (Join-Path $PSScriptRoot 'cleanup-service-principal.ps1') @arguments
} catch { $message = $_.Exception.Message + ' [line ' + $_.InvocationInfo.ScriptLineNumber + ']' }
@{ error=$message;calls=@($global:cleanupCalls);scopes=@($global:cleanupScopes) } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'result.json') -Encoding UTF8
"""


@unittest.skipUnless(POWERSHELL, "PowerShell is required for offline teardown mocks")
class CleanupServicePrincipalTests(unittest.TestCase):
    def run_cleanup(self, scenario="normal", *, apply=False, what_if=False,
                    delete_env=False, workload=False, profile="Restricted", saved_sp=False,
                    env_tenant=TENANT, env_client=CLIENT, env_profile=None, extra_env=""):
        with tempfile.TemporaryDirectory() as folder:
            temporary = Path(folder)
            shutil.copyfile(ROOT / "cleanup-service-principal.ps1", temporary / "cleanup-service-principal.ps1")
            (temporary / "tools").mkdir()
            shutil.copyfile(ROOT / "tools/cleanup-reparse-points.ps1", temporary / "tools/cleanup-reparse-points.ps1")
            (temporary / "harness.ps1").write_text(HARNESS, encoding="utf-8")
            selected = temporary / (".env.restricted" if profile == "Restricted" else ".env")
            other = temporary / (".env" if profile == "Restricted" else ".env.restricted")
            other.write_text("unrelated-profile-credentials\n", encoding="utf-8")
            selected.write_text(
                f'export TENANT_ID="{env_tenant}" # tenant\nCLIENT_ID=\'{env_client}\' # application\n'
                f"PERMISSION_PROFILE={env_profile or profile.lower()}\nCLIENT_SECRET=DO-NOT-PRINT-SECRET\n{extra_env}",
                encoding="utf-8",
            )
            arguments = [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(temporary / "harness.ps1"), "-Scenario", scenario, "-Profile", profile]
            for flag, enabled in (("-Apply", apply), ("-Simulate", what_if), ("-DeleteEnv", delete_env), ("-Workload", workload), ("-SavedSp", saved_sp)):
                if enabled:
                    arguments.append(flag)
            completed = subprocess.run(arguments, capture_output=True, text=True, timeout=45)
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            result = json.loads((temporary / "result.json").read_text(encoding="utf-8-sig"))
            result["output"] = completed.stdout + completed.stderr
            result["environment_exists"] = selected.exists()
            self.assertEqual("unrelated-profile-credentials\n", other.read_text(encoding="utf-8"))
            self.assertNotIn("DO-NOT-PRINT-SECRET", result["output"])
            self.assertNotIn("DO-NOT-PRINT-CERTIFICATE", result["output"])
            return result

    def assert_no_removals(self, result):
        self.assertFalse([call for call in result["calls"] if call.startswith("Remove")], result)
        self.assertTrue(result["environment_exists"])

    def test_preview_is_read_only_with_counts_and_explicit_identity(self):
        result = self.run_cleanup(delete_env=True)
        self.assertEqual("", result["error"])
        self.assertEqual(["Application.Read.All"], result["scopes"])
        self.assert_no_removals(result)
        self.assertIn("password credentials: 1", result["output"])
        self.assertIn("application grants: 2", result["output"])
        self.assertIn("requested permissions: 1", result["output"])
        self.assertIn(SP, result["output"])
        self.assertEqual("GraphDisconnect", result["calls"][-1])

    def test_identity_and_profile_mismatch_fail_before_import_or_auth(self):
        for arguments in ({"env_tenant": CLIENT}, {"env_client": TENANT}, {"env_profile": "standard"}, {"env_client": "invalid"}, {"extra_env": f"CLIENT_ID={CLIENT}\n"}):
            with self.subTest(arguments=arguments):
                result = self.run_cleanup(apply=True, **arguments)
                self.assertTrue(result["error"])
                self.assertEqual([], result["calls"])
                self.assert_no_removals(result)

    def test_unexpected_graph_context_and_object_identity_fail_closed(self):
        for scenario in ("wrong_tenant", "duplicate_app", "duplicate_sp", "wrong_name", "wrong_sp_name", "wrong_app_id", "wrong_saved_sp"):
            with self.subTest(scenario=scenario):
                result = self.run_cleanup(scenario, apply=True, delete_env=True)
                self.assertTrue(result["error"])
                self.assert_no_removals(result)
                self.assertEqual("GraphDisconnect", result["calls"][-1])

    def test_apply_removes_only_confirmed_objects_then_environment(self):
        result = self.run_cleanup(apply=True, delete_env=True)
        self.assertEqual("", result["error"])
        self.assertEqual(["Application.ReadWrite.All"], result["scopes"])
        removals = [call for call in result["calls"] if call.startswith("Remove")]
        self.assertEqual([f"RemoveGraphPrincipal:{SP}", f"RemoveGraphApplication:{APP}"], removals)
        self.assertFalse(result["environment_exists"])
        self.assertGreater(result["calls"].index("ReadPrincipal", result["calls"].index(removals[-1])), result["calls"].index(removals[-1]))

    def test_whatif_never_calls_removals_even_with_workload_cmdlets(self):
        for workload, profile in ((False, "Restricted"), (True, "Standard")):
            with self.subTest(workload=workload):
                result = self.run_cleanup(apply=True, what_if=True, delete_env=True, workload=workload, profile=profile)
                self.assertEqual("", result["error"])
                self.assert_no_removals(result)

    def test_apply_requires_interactive_confirmation_by_default(self):
        result = self.run_cleanup("confirmation_required", apply=True, delete_env=True)
        self.assertTrue(result["error"])
        self.assert_no_removals(result)

    def test_missing_application_principal_or_both_are_idempotent(self):
        for scenario, expected in (("missing", []), ("app_only", [f"RemoveGraphApplication:{APP}"]), ("sp_only", [f"RemoveGraphPrincipal:{SP}"])):
            with self.subTest(scenario=scenario):
                result = self.run_cleanup(scenario, apply=True, delete_env=True)
                self.assertEqual("", result["error"])
                self.assertEqual(expected, [call for call in result["calls"] if call.startswith("Remove")])
                self.assertFalse(result["environment_exists"])

    def test_partial_or_unconfirmed_deletion_preserves_environment(self):
        for scenario in ("sp_failure", "app_failure", "still_visible", "changed_env"):
            with self.subTest(scenario=scenario):
                result = self.run_cleanup(scenario, apply=True, delete_env=True)
                self.assertTrue(result["error"])
                self.assertTrue(result["environment_exists"])
                if scenario == "sp_failure":
                    self.assertNotIn(f"RemoveGraphApplication:{APP}", result["calls"])

    def test_workload_plans_both_precede_any_mutation(self):
        result = self.run_cleanup(apply=True, workload=True, profile="Standard", delete_env=True)
        self.assertEqual("", result["error"])
        removals = [call for call in result["calls"] if call.startswith("Remove")]
        self.assertEqual([
            f"RemoveMember:Purview:{SP}", f"RemoveWorkload:Purview:{SP}",
            f"RemoveMember:Exchange:{SP}", f"RemoveWorkload:Exchange:{SP}",
            f"RemoveGraphPrincipal:{SP}", f"RemoveGraphApplication:{APP}",
        ], removals)
        first_removal = result["calls"].index(removals[0])
        self.assertLess(result["calls"].index("ReadWorkload:Exchange"), first_removal)
        self.assertFalse(result["environment_exists"])

    def test_workload_preflight_errors_never_allow_graph_deletion(self):
        for scenario in ("workload_read_failure", "workload_mismatch", "workload_duplicate", "workload_wrong_tenant", "old_module"):
            with self.subTest(scenario=scenario):
                result = self.run_cleanup(scenario, apply=True, workload=True, profile="Standard", delete_env=True)
                self.assertTrue(result["error"])
                self.assert_no_removals(result)
                self.assertEqual("GraphDisconnect", result["calls"][-1])

    def test_workload_partial_failure_stops_before_graph_and_preserves_env(self):
        result = self.run_cleanup("workload_remove_failure", apply=True, workload=True, profile="Standard", delete_env=True)
        self.assertTrue(result["error"])
        self.assertTrue(result["environment_exists"])
        self.assertFalse(any(call.startswith("RemoveGraph") for call in result["calls"]))
        self.assertIn("Disconnect:Purview", result["calls"])

    def test_workload_removal_must_be_verified_before_graph_deletion(self):
        result = self.run_cleanup("workload_still_visible", apply=True, workload=True, profile="Standard", delete_env=True)
        self.assertIn("still visible", result["error"])
        self.assertTrue(result["environment_exists"])
        self.assertFalse(any(call.startswith("RemoveGraph") for call in result["calls"]))

    def test_absent_workload_principals_groups_and_memberships_are_safe(self):
        for scenario in ("workload_missing", "group_missing", "membership_missing"):
            with self.subTest(scenario=scenario):
                result = self.run_cleanup(scenario, apply=True, workload=True, profile="Standard")
                self.assertEqual("", result["error"])
                self.assertFalse(any(call.startswith("RemoveMember") for call in result["calls"]))
                if scenario == "workload_missing":
                    self.assertFalse(any(call.startswith("RemoveWorkload") for call in result["calls"]))

    def test_workload_retry_requires_recorded_object_id_when_graph_sp_absent(self):
        result = self.run_cleanup("retry_workload", apply=True, workload=True, profile="Standard")
        self.assertIn("ServicePrincipalObjectId", result["error"])
        self.assert_no_removals(result)
        result = self.run_cleanup("retry_workload", apply=True, workload=True, profile="Standard", saved_sp=True)
        self.assertEqual("", result["error"])
        self.assertEqual(4, len([call for call in result["calls"] if call.startswith("Remove")]))

    def test_restricted_rejects_workload_option_before_authentication(self):
        result = self.run_cleanup(apply=True, workload=True)
        self.assertTrue(result["error"])
        self.assertEqual([], result["calls"])


if __name__ == "__main__":
    unittest.main()

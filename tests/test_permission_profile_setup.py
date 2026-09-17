"""Execute setup with offline Graph/module mocks; no tenant calls are possible."""

import json
import base64
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from Core.collector_registry import (
    COLLECTOR_REGISTRY,
    RESOURCE_APP_IDS,
    collector_permissions,
    normalize_permission_profile,
    profile_permission_resources,
    selected_collector_ids,
    source_allowed,
)


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


HARNESS = r"""
param([string]$Scenario, [string]$Profile = 'Restricted', [string]$SetupMode = 'Standard', [string]$Preview = 'None', [string]$SharePointUrl = '')
$ErrorActionPreference = 'Stop'
$global:mockSetupCalls = [System.Collections.Generic.List[string]]::new()
$global:mockSetupPermissions = @()
$global:mockSetupConnectedScopes = @()
$global:mockSetupApplicationFilter = ''
$global:mockLockedEnvironment = $null
$global:mockEnvironmentAtConsent = ''
$global:mockPasswordIssued = $false
$registry = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'collector-registry.json') -Raw | ConvertFrom-Json
$certificateTest = if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'certificate-test.json')) { Get-Content -LiteralPath (Join-Path $PSScriptRoot 'certificate-test.json') -Raw | ConvertFrom-Json } else { $null }
$global:mockSetupResources = @{}
foreach ($entry in $registry.resource_app_ids.PSObject.Properties) {
    $resourceName = $entry.Name
    $names = @($registry.collectors | ForEach-Object { $_.permission_resources.$resourceName } | Sort-Object -Unique)
    $global:mockSetupResources[$entry.Value] = [pscustomobject]@{
        Id = "object-$resourceName"; AppId = $entry.Value; DisplayName = $resourceName
        AppRoles = @($names | ForEach-Object { [pscustomobject]@{ Id = "role-$_"; Value = $_; AllowedMemberTypes = @('Application') } })
    }
}
function Import-Module { param($Name, [switch]$Force) $global:mockSetupCalls.Add("Import:$Name") }
function Get-Content {
    [CmdletBinding()]
    param([string]$LiteralPath, [string]$Encoding, [switch]$Raw)
    if ($Scenario -eq 'env_reread_failure' -and $global:mockPasswordIssued -and $LiteralPath -like '*.env.restricted') {
        $global:mockSetupCalls.Add('EnvironmentReadAfterPassword')
        throw 'Mock environment became unreadable after the one-time secret was issued.'
    }
    Microsoft.PowerShell.Management\Get-Content @PSBoundParameters
}
function Get-ChildItem {
    param($Path, $ErrorAction)
    if ($Path -like 'Cert:*') {
        $storeThumbprint = if ($certificateTest) { $certificateTest.store_thumbprint } else { 'mock-thumb' }
        $mockCertificate = [pscustomobject]@{HasPrivateKey=$true;Thumbprint=$storeThumbprint;NotBefore=(Get-Date).AddDays(-1);NotAfter=(Get-Date).AddDays(90);RawData=[byte[]](1,2,3)}
        $mockCertificate | Add-Member -MemberType ScriptMethod -Name GetCertHash -Value { return [byte[]](1,2,3) }
        return $mockCertificate
    }
    throw 'Unexpected filesystem enumeration in offline setup test.'
}
function Connect-MgGraph { param($Scopes, $ContextScope, [switch]$NoWelcome) $global:mockSetupConnectedScopes = $Scopes; $global:mockSetupCalls.Add('Connect') }
function Disconnect-MgGraph { $global:mockSetupCalls.Add('Disconnect') }
function Get-MgContext { [pscustomobject]@{TenantId = 'tenant-current'} }
function Invoke-RestMethod {
    param($Method, $Uri, $ContentType, $TimeoutSec, $ErrorAction, $Body)
    $global:mockSetupCalls.Add('ValidateSecret')
    if ($Uri -ne 'https://login.microsoftonline.com/tenant-current/oauth2/v2.0/token' -or $Method -ne 'Post' -or $Body.client_id -ne 'app-current' -or $Body.grant_type -ne 'client_credentials' -or $Body.scope -ne 'https://graph.microsoft.com/.default' -or $Body.client_secret -ne 'saved-mock-secret') { throw 'Unexpected token request in offline setup test.' }
    if ($Scenario -in @('wrong_secret', 'token_expired', 'network_failure', 'policy_failure')) {
        $code = if ($Scenario -eq 'wrong_secret') { 7000215 } elseif ($Scenario -eq 'token_expired') { 7000222 } else { 53003 }
        $record = [System.Management.Automation.ErrorRecord]::new([System.Exception]::new('UNSAFE saved-mock-secret response'), 'MockTokenFailure', 'AuthenticationError', $null)
        if ($Scenario -ne 'network_failure') { $record.ErrorDetails = [System.Management.Automation.ErrorDetails]::new((@{error='invalid_client';error_codes=@($code);error_description='UNSAFE saved-mock-secret response'} | ConvertTo-Json)) }
        throw $record
    }
    return @{access_token='test-only-access-token'}
}
function Set-Acl {
    param($LiteralPath, $AclObject, $ErrorAction)
    if ($Scenario -eq 'staging_failure') { throw 'Mock local permissions failure.' }
    Microsoft.PowerShell.Security\Set-Acl -LiteralPath $LiteralPath -AclObject $AclObject -ErrorAction Stop
}
function Get-MgApplication {
    param($Filter, $Property, $ApplicationId)
    if ($Filter) { $global:mockSetupApplicationFilter = $Filter }
    if ($Scenario -eq 'new_app') { return }
    $requested = @()
    if ($Scenario -eq 'excess_requested') {
        $requested = @([pscustomobject]@{ResourceAppId = $registry.resource_app_ids.graph; ResourceAccess = @([pscustomobject]@{Id='role-Sites.Read.All';Type='Role'})})
    }
    $keys = @()
    if ($certificateTest -and $certificateTest.registered) {
        $keyExpiration = if ($certificateTest.registered_expired) { (Get-Date).AddDays(-1) } else { (Get-Date).AddDays(30) }
        $keys = @([pscustomobject]@{Key=$certificateTest.public_certificate;CustomKeyIdentifier=$certificateTest.thumbprint;StartDateTime=(Get-Date).AddDays(-2);EndDateTime=$keyExpiration})
    }
    $secretExpiry = if ($Scenario -eq 'expired_key') { (Get-Date).AddDays(-1) } elseif ($Scenario -eq 'expiring_key') { (Get-Date).AddDays(5) } else { (Get-Date).AddDays(30) }
    $passwords = @([pscustomobject]@{KeyId='saved-key-id';StartDateTime=(Get-Date).AddDays(-2);EndDateTime=$secretExpiry})
    if ($Scenario -in @('legacy_multiple', 'expired_key', 'revoked_key')) { $passwords += [pscustomobject]@{KeyId='other-valid-key';EndDateTime=(Get-Date).AddDays(60)} }
    if ($Scenario -eq 'revoked_key') { $passwords = @($passwords | Where-Object KeyId -ne 'saved-key-id') }
    $app = [pscustomobject]@{
        Id='app-object'; AppId='app-current'; DisplayName='Mock Assessment'
        RequiredResourceAccess=$requested; KeyCredentials=$keys
        PasswordCredentials=$passwords
    }
    if ($Scenario -eq 'duplicate') { return @($app, $app) }
    return $app
}
function New-MgApplication {
    param($DisplayName, $SignInAudience, $PublicClient)
    $global:mockSetupCalls.Add('NewApplication')
    return [pscustomobject]@{Id='app-object';AppId='app-current';DisplayName=$DisplayName;RequiredResourceAccess=@();PasswordCredentials=@();KeyCredentials=@()}
}
function Update-MgApplication {
    param($ApplicationId, $RequiredResourceAccess, $KeyCredentials)
    $global:mockSetupCalls.Add('UpdateApplication')
    if ($RequiredResourceAccess) { $global:mockSetupPermissions = @($RequiredResourceAccess) }
}
function Get-MgServicePrincipal {
    param($Filter, $Property, $ErrorAction)
    if ($Filter -match "appId eq '([^']+)'") {
        $appId = $Matches[1]
        if ($global:mockSetupResources.ContainsKey($appId)) { return $global:mockSetupResources[$appId] }
    }
    if ($Scenario -eq 'new_app') { return }
    return [pscustomobject]@{Id='sp-object';AppId='app-current';DisplayName='Mock Assessment'}
}
function New-MgServicePrincipal { param($AppId) $global:mockSetupCalls.Add('NewPrincipal'); return [pscustomobject]@{Id='sp-object';AppId=$AppId} }
function Get-MgServicePrincipalAppRoleAssignment {
    param($ServicePrincipalId, [switch]$All)
    $global:mockSetupCalls.Add('ReadAssignments')
    if ($Scenario -eq 'consent_interrupted') { return }
    if ($Scenario -eq 'excess_consented') {
        return [pscustomobject]@{ResourceId='object-sharepoint';AppRoleId='role-Sites.FullControl.All'}
    }
    foreach ($resource in $global:mockSetupPermissions) {
        foreach ($access in $resource.ResourceAccess) {
            [pscustomobject]@{ResourceId=$global:mockSetupResources[$resource.ResourceAppId].Id;AppRoleId=$access.Id}
        }
    }
}
function Add-MgApplicationPassword {
    param($ApplicationId, $PasswordCredential)
    $global:mockSetupCalls.Add('Password')
    $global:mockPasswordIssued = $true
    if ($Scenario -in @('replace_failure', 'write_failure')) {
        $lockedPath = if ($Scenario -eq 'replace_failure') { Join-Path $PSScriptRoot '.env.restricted' } else { [System.IO.Directory]::GetFiles($PSScriptRoot, '.env.restricted.recovery.*')[0] }
        $global:mockLockedEnvironment = [System.IO.File]::Open($lockedPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    }
    return [pscustomobject]@{SecretText='new-mock-secret';KeyId='new-key-id';EndDateTime=$PasswordCredential.EndDateTime}
}
function Get-MgOrganization { param($Property) [pscustomobject]@{VerifiedDomains=@([pscustomobject]@{IsInitial=$true;Name='test.onmicrosoft.com'})} }
function Start-Process {
    $global:mockSetupCalls.Add('ConsentBrowser')
    $global:mockEnvironmentAtConsent = (Get-Content -LiteralPath (Join-Path $PSScriptRoot '.env.restricted') -Raw).ToString()
    throw 'Mock interrupted consent; a real process must not be started by this offline setup test.'
}
function Read-Host { throw 'Unexpected interactive prompt in offline setup test.' }
$message = ''
try {
    $extraArguments = @{}
    if ($PSBoundParameters.ContainsKey('SharePointUrl')) { $extraArguments.SharePointAdminUrl = $SharePointUrl }
    if ($Scenario -like 'thumbprint*') { $extraArguments.CertificateThumbprint = 'mock-thumb' }
    if ($Scenario -in @('thumbprint_rotate','rotate','consent_interrupted','replace_failure','write_failure','env_reread_failure')) { $extraArguments.RotateCredential = $true }
    & (Join-Path $PSScriptRoot 'setup-service-principal.ps1') -PermissionProfile $Profile -Mode $SetupMode -PreviewCollectors $Preview @extraArguments
} catch { $message = $_.Exception.Message + ' [' + $_.InvocationInfo.ScriptLineNumber + ': ' + $_.InvocationInfo.Line.Trim() + ']' }
if ($global:mockLockedEnvironment) { $global:mockLockedEnvironment.Dispose() }
@{
    error=$message; calls=@($global:mockSetupCalls); scopes=@($global:mockSetupConnectedScopes)
    filter=$global:mockSetupApplicationFilter; permissions=@($global:mockSetupPermissions)
    consent_environment=$global:mockEnvironmentAtConsent
} | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'result.json') -Encoding UTF8
"""


class PermissionRegistryTests(unittest.TestCase):
    def test_restricted_retains_stable_reads_and_excludes_administration(self):
        resources = profile_permission_resources("Restricted")
        self.assertEqual({RESOURCE_APP_IDS["graph"], RESOURCE_APP_IDS["defender"]}, set(resources))
        graph = resources[RESOURCE_APP_IDS["graph"]]
        self.assertTrue({"AuditLog.Read.All", "SecurityAlert.Read.All", "User.Read.All", "Application.Read.All"} <= graph)
        self.assertFalse({"Sites.Read.All", "Group.Read.All", "Directory.Read.All", "UserAuthenticationMethod.Read.All"} & graph)
        self.assertEqual({"Machine.Read.All"}, resources[RESOURCE_APP_IDS["defender"]])
        for name in ("sites", "groups", "oauth_grants", "purview", "sharepoint_governance", "power_platform", "legacy_power_platform"):
            self.assertFalse(source_allowed(name, "restricted"), name)
        selected = selected_collector_ids({"run_m365": True, "run_entra": True, "run_defender": True, "run_purview": True}, "all", True, "restricted")
        self.assertEqual(["graph_core", "m365_usage", "external_connections", "entra_controls", "entra_risk", "graph_security", "defender_endpoint"], selected)

    def test_standard_permission_corrections_and_invalid_profiles(self):
        self.assertIn("Directory.Read.All", collector_permissions("entra_controls"))
        self.assertIn("SecurityAlert.Read.All", collector_permissions("graph_security"))
        self.assertNotIn("UserAuthenticationMethod.Read.All", collector_permissions("entra_controls"))
        self.assertEqual("standard", normalize_permission_profile(None))
        with self.assertRaises(ValueError):
            normalize_permission_profile("restrictd")


@unittest.skipUnless(POWERSHELL, "PowerShell is required for offline setup mocks")
class OfflineSetupProfileTests(unittest.TestCase):
    def run_setup(self, scenario="normal", profile="Restricted", mode="Standard", preview="None", tenant="tenant-current", client="app-current", certificate_options=None, sharepoint_url=None, saved_sharepoint_url=None):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for filename in ("setup-service-principal.ps1", "collector-registry.json"):
                shutil.copyfile(ROOT / filename, root / filename)
            (root / "Core").mkdir()
            shutil.copyfile(ROOT / "Core/certificate_validation.py", root / "Core/certificate_validation.py")
            (root / "Check-PSModules.ps1").write_text(
                "param($ScriptType)\nfunction Test-RequiredModules { param($ScriptType,[switch]$InstallMissing) $global:mockSetupCalls.Add(\"Modules:$ScriptType\"); return $true }\n",
                encoding="utf-8",
            )
            (root / "harness.ps1").write_text(HARNESS, encoding="utf-8")
            initial_standard = "# untouched standard credentials\n"
            (root / ".env").write_text(initial_standard, encoding="utf-8")
            restricted_env = f"TENANT_ID={tenant}\nCLIENT_ID={client}\nCLIENT_SECRET=saved-mock-secret\n"
            if scenario in ("tracked", "expired_key", "revoked_key", "expiring_key", "wrong_secret", "token_expired"):
                restricted_env += "CLIENT_SECRET_KEY_ID=saved-key-id\nCLIENT_SECRET_EXPIRES_AT=2099-01-01T00:00:00Z\n"
            if scenario == "preserve_comments":
                restricted_env = f'# customer notes\nexport TENANT_ID = "{tenant}" # target tenant\nCLIENT_ID={client}\nCLIENT_SECRET=saved-mock-secret # vault reference\nCUSTOM_SETTING="unchanged # literal"\n'
            if scenario == "quoted_env":
                restricted_env = f'export TENANT_ID = "{tenant}" # tenant\nCLIENT_ID=\'{client}\' # application\nCLIENT_SECRET=saved-mock-secret # secret comment\n'
            if certificate_options is not None:
                from tests.test_certificate_validation import certificate_fixture
                from cryptography.hazmat.primitives import serialization
                from cryptography.hazmat.primitives.serialization import pkcs12

                cert_path = root / "graph.pfx"
                password = certificate_options.get("password", "test-only-password")
                thumbprint = certificate_fixture(cert_path, password=password, expired=certificate_options.get("expired", False))
                _key, certificate, _chain = pkcs12.load_key_and_certificates(cert_path.read_bytes(), password.encode())
                if certificate_options.get("registered_mismatch"):
                    other_path = root / "other.pfx"
                    certificate_fixture(other_path)
                    _key, certificate, _chain = pkcs12.load_key_and_certificates(other_path.read_bytes(), b"test-only-password")
                supplied_password = "wrong-password" if certificate_options.get("wrong_password") else password
                restricted_env += f"CERTIFICATE_PATH={cert_path}\nCERTIFICATE_PASSWORD={supplied_password}\n"
                if certificate_options.get("quoted"):
                    restricted_env = restricted_env.replace(f"CERTIFICATE_PATH={cert_path}", f'CERTIFICATE_PATH="{cert_path}" # certificate')
                    restricted_env = restricted_env.replace(f"CERTIFICATE_PASSWORD={supplied_password}", f"CERTIFICATE_PASSWORD='{supplied_password}' # literal password")
                certificate_test = {
                    "thumbprint": thumbprint,
                    "store_thumbprint": "different-thumbprint" if certificate_options.get("store_mismatch") else thumbprint,
                    "registered": certificate_options.get("registered", False),
                    "registered_expired": certificate_options.get("registered_expired", False),
                    "public_certificate": base64.b64encode(certificate.public_bytes(serialization.Encoding.DER)).decode(),
                }
                (root / "certificate-test.json").write_text(json.dumps(certificate_test), encoding="utf-8")
            (root / ".env.restricted").write_text(restricted_env, encoding="utf-8")
            if profile == "Standard" and saved_sharepoint_url is not None:
                (root / ".env").write_text(
                    f"TENANT_ID={tenant}\nCLIENT_ID={client}\nCLIENT_SECRET=saved-mock-secret\n"
                    f"SHAREPOINT_ADMIN_URL={saved_sharepoint_url}\n", encoding="utf-8",
                )
            arguments = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / "harness.ps1"), "-Scenario", scenario, "-Profile", profile, "-SetupMode", mode, "-Preview", preview]
            if sharepoint_url is not None:
                arguments.extend(["-SharePointUrl", sharepoint_url])
            (root / "temporary-recovery").mkdir()
            result = subprocess.run(
                arguments,
                capture_output=True, text=True, timeout=30,
                env={**os.environ, "VIRTUAL_ENV": str(ROOT / ".venv"), "TEMP": str(root / "temporary-recovery"), "TMP": str(root / "temporary-recovery"), "TMPDIR": str(root / "temporary-recovery")},
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertTrue((root / "result.json").exists(), result.stdout + result.stderr)
            payload = json.loads((root / "result.json").read_text(encoding="utf-8-sig"))
            payload["consent_environment"] = payload["consent_environment"].replace("\r\n", "\n")
            payload["restricted_env"] = (root / ".env.restricted").read_text(encoding="utf-8-sig")
            payload["standard_env"] = (root / ".env").read_text(encoding="utf-8-sig")
            payload["output"] = result.stdout + result.stderr
            payload["recovery_files"] = {str(path.relative_to(root)): path.read_text(encoding="utf-8-sig") for path in root.rglob(".env*.recovery.*")}
            payload["gitignore"] = (root / ".gitignore").read_text(encoding="utf-8-sig") if (root / ".gitignore").exists() else ""
            return payload

    def test_restricted_exact_permissions_and_isolated_application_and_environment(self):
        result = self.run_setup()
        self.assertEqual("", result["error"])
        self.assertEqual(["Application.ReadWrite.All", "Organization.Read.All"], result["scopes"])
        self.assertIn("M365 Copilot Readiness Assessment Tool - Restricted", result["filter"])
        actual = {entry["ResourceAppId"]: {permission["Id"].removeprefix("role-") for permission in entry["ResourceAccess"]} for entry in result["permissions"]}
        self.assertEqual(profile_permission_resources("restricted"), actual)
        self.assertIn("Modules:Setup", result["calls"])
        self.assertNotIn("Modules:Purview", result["calls"])
        self.assertNotIn("Password", result["calls"])
        self.assertIn("PERMISSION_PROFILE=restricted", result["restricted_env"])
        self.assertEqual("# untouched standard credentials\n", result["standard_env"])

    def test_standard_exact_stable_permissions(self):
        result = self.run_setup(profile="Standard")
        self.assertEqual("", result["error"])
        expected = {}
        for collector_id, collector in COLLECTOR_REGISTRY.items():
            if collector.get("default_setup"):
                for resource in collector.get("permission_resources", {}):
                    expected.setdefault(RESOURCE_APP_IDS[resource], set()).update(collector_permissions(collector_id, resource=resource))
        actual = {entry["ResourceAppId"]: {permission["Id"].removeprefix("role-") for permission in entry["ResourceAccess"]} for entry in result["permissions"]}
        self.assertEqual(expected, actual)
        self.assertIn("Modules:Purview", result["calls"])
        self.assertIn("PERMISSION_PROFILE=standard", result["standard_env"])

    def test_explicit_sharepoint_admin_url_is_normalized_and_saved(self):
        result = self.run_setup(profile="Standard", sharepoint_url="https://RenamedTenant-admin.sharepoint.com/")
        self.assertEqual("", result["error"])
        self.assertIn("SHAREPOINT_ADMIN_URL=https://renamedtenant-admin.sharepoint.com\n", result["standard_env"])
        self.assertIn("PURVIEW_ORGANIZATION=test.onmicrosoft.com", result["standard_env"])
        self.assertIn("tenant connection not verified", result["output"])

    def test_missing_sharepoint_url_remains_blank_and_never_guesses_initial_domain(self):
        result = self.run_setup(profile="Standard")
        self.assertEqual("", result["error"])
        self.assertIn("SHAREPOINT_ADMIN_URL=\n", result["standard_env"])
        self.assertNotIn("https://test-admin.sharepoint.com", result["standard_env"])
        self.assertIn("Admin centers > SharePoint", result["output"])
        self.assertIn("core identity and security assessment can still run", result["output"])

    def test_valid_saved_sharepoint_url_is_tenant_bound_not_client_bound(self):
        for client in ("app-current", "different-app"):
            with self.subTest(client=client):
                result = self.run_setup(profile="Standard", client=client, saved_sharepoint_url="https://actual-admin.sharepoint.com/")
                self.assertEqual("", result["error"])
                self.assertIn("SHAREPOINT_ADMIN_URL=https://actual-admin.sharepoint.com\n", result["standard_env"])
        result = self.run_setup(profile="Standard", tenant="other-tenant", saved_sharepoint_url="https://oldcustomer-admin.sharepoint.com")
        self.assertEqual("", result["error"])
        self.assertIn("SHAREPOINT_ADMIN_URL=\n", result["standard_env"])
        self.assertNotIn("oldcustomer", result["standard_env"])

    def test_explicit_sharepoint_url_overrides_saved_tenant_url(self):
        result = self.run_setup(profile="Standard", tenant="other-tenant", saved_sharepoint_url="https://oldcustomer-admin.sharepoint.com", sharepoint_url="https://newcustomer-admin.sharepoint.com")
        self.assertEqual("", result["error"])
        self.assertIn("SHAREPOINT_ADMIN_URL=https://newcustomer-admin.sharepoint.com\n", result["standard_env"])
        self.assertNotIn("oldcustomer", result["standard_env"])

    def test_invalid_saved_sharepoint_url_is_cleared_without_blocking_setup(self):
        for saved in ("https://contoso.sharepoint.com", '"https://unterminated-admin.sharepoint.com'):
            with self.subTest(saved=saved):
                result = self.run_setup(profile="Standard", saved_sharepoint_url=saved)
                self.assertEqual("", result["error"])
                self.assertIn("SHAREPOINT_ADMIN_URL=\n", result["standard_env"])
                self.assertIn("saved SHAREPOINT_ADMIN_URL is invalid", result["output"])

    def test_invalid_explicit_sharepoint_urls_stop_before_modules_or_auth(self):
        for url in (
            "http://contoso-admin.sharepoint.com", "https://contoso.sharepoint.com",
            "https://contoso-admin.sharepoint.com:443", "https://contoso-admin.sharepoint.com/sites/admin",
            "https://contoso-admin.sharepoint.com?x=1", "https://contoso-admin.sharepoint.com/#fragment",
            "https://admin:password@contoso-admin.sharepoint.com", "https://contoso-admin.sharepoint.com.evil.example",
            "https://contoso-admin.sharepoint.us", " ",
        ):
            with self.subTest(url=url):
                result = self.run_setup(profile="Standard", sharepoint_url=url)
                self.assertIn("SharePointAdminUrl must be", result["error"])
                self.assertEqual([], result["calls"])
                self.assertEqual("# untouched standard credentials\n", result["standard_env"])

    def test_restricted_rejects_explicit_sharepoint_url_before_modules_or_auth(self):
        result = self.run_setup(sharepoint_url="https://contoso-admin.sharepoint.com")
        self.assertIn("Restricted excludes SharePoint administration", result["error"])
        self.assertEqual([], result["calls"])

    def test_wrong_saved_tenant_or_client_never_reuses_secret(self):
        for tenant, client in (("different-tenant", "app-current"), ("tenant-current", "different-app")):
            with self.subTest(tenant=tenant, client=client):
                result = self.run_setup(tenant=tenant, client=client)
                self.assertEqual("", result["error"])
                self.assertIn("Password", result["calls"])
                self.assertIn("CLIENT_SECRET=new-mock-secret", result["restricted_env"])
                self.assertNotIn("saved-mock-secret", result["restricted_env"])

    def test_duplicate_application_and_excess_permissions_stop_before_mutation(self):
        for scenario in ("duplicate", "excess_requested", "excess_consented"):
            with self.subTest(scenario=scenario):
                result = self.run_setup(scenario)
                self.assertTrue(result["error"])
                self.assertFalse({"NewApplication", "UpdateApplication", "NewPrincipal", "Password"} & set(result["calls"]))
                self.assertNotIn("PERMISSION_PROFILE", result["restricted_env"])
                if scenario == "excess_consented":
                    self.assertIn("excess requested or consented", result["error"])

    def test_forbidden_combinations_stop_before_modules_or_graph(self):
        for mode, preview in (("Unattended", "None"), ("Standard", "All")):
            with self.subTest(mode=mode, preview=preview):
                result = self.run_setup(mode=mode, preview=preview)
                self.assertIn("Restricted rejects", result["error"])
                self.assertEqual([], result["calls"])

    def test_new_application_uses_restricted_name_and_new_secret(self):
        result = self.run_setup("new_app")
        self.assertEqual("", result["error"])
        self.assertIn("NewApplication", result["calls"])
        self.assertIn("NewPrincipal", result["calls"])
        self.assertIn("Password", result["calls"])
        self.assertIn("CLIENT_SECRET=new-mock-secret", result["restricted_env"])

    def test_thumbprint_without_graph_secret_fails_before_mutation(self):
        result = self.run_setup("thumbprint_only", tenant="different-tenant")
        self.assertIn("thumbprint alone cannot authenticate Python Graph", result["error"])
        self.assertNotIn("UpdateApplication", result["calls"])
        self.assertNotIn("Password", result["calls"])

    def test_thumbprint_can_use_matching_secret_or_explicit_rotation(self):
        for scenario, tenant in (("thumbprint_saved", "tenant-current"), ("thumbprint_rotate", "different-tenant")):
            with self.subTest(scenario=scenario):
                result = self.run_setup(scenario, tenant=tenant)
                self.assertEqual("", result["error"])
                self.assertIn("SHAREPOINT_CERTIFICATE_THUMBPRINT=mock-thumb", result["restricted_env"])
                self.assertEqual(scenario == "thumbprint_rotate", "Password" in result["calls"])

    def test_encrypted_saved_certificate_matches_registered_key_or_selected_thumbprint(self):
        for scenario, options in (("normal", {"registered": True}), ("thumbprint_saved", {"password": "test-ü-password"}), ("quoted_env", {"registered": True, "quoted": True})):
            with self.subTest(scenario=scenario):
                result = self.run_setup(scenario, certificate_options=options)
                self.assertEqual("", result["error"])
                self.assertIn(options.get("password", "test-only-password"), result["restricted_env"])
                self.assertNotIn("Password", result["calls"])

    def test_unusable_or_mismatched_saved_certificate_stops_before_application_changes(self):
        for options, expected_error in (
            ({"registered": True, "wrong_password": True}, "cannot be decrypted or parsed"),
            ({"registered": True, "expired": True}, "expired or not yet valid"),
            ({}, "not registered as a currently valid key"),
            ({"registered": True, "registered_expired": True}, "not registered as a currently valid key"),
            ({"registered": True, "registered_mismatch": True}, "not registered as a currently valid key"),
            ({"store_mismatch": True}, "does not match the explicitly selected"),
        ):
            with self.subTest(options=options):
                scenario = "thumbprint_saved" if options.get("store_mismatch") else "normal"
                result = self.run_setup(scenario, certificate_options=options)
                self.assertTrue(result["error"])
                self.assertIn(expected_error, result["error"])
                self.assertNotIn("test-only-password", result["error"])
                self.assertNotIn("wrong-password", result["error"])
                self.assertNotIn("UpdateApplication", result["calls"])
                self.assertNotIn("Password", result["calls"])

    def test_legacy_and_tracked_secrets_are_authenticated_and_record_actual_expiry(self):
        for scenario in ("normal", "tracked"):
            with self.subTest(scenario=scenario):
                result = self.run_setup(scenario)
                self.assertEqual("", result["error"])
                self.assertIn("ValidateSecret", result["calls"])
                self.assertNotIn("Password", result["calls"])
                self.assertIn("CLIENT_SECRET_KEY_ID=saved-key-id\n", result["restricted_env"])
                self.assertIn("CLIENT_SECRET_EXPIRES_AT=", result["restricted_env"])
                self.assertNotIn("2099-01-01", result["restricted_env"])
                self.assertLess(result["calls"].index("ValidateSecret"), result["calls"].index("UpdateApplication"))
                self.assertEqual({}, result["recovery_files"])

    def test_legacy_secret_with_multiple_keys_does_not_invent_metadata(self):
        result = self.run_setup("legacy_multiple")
        self.assertEqual("", result["error"])
        self.assertIn("ValidateSecret", result["calls"])
        self.assertNotIn("Password", result["calls"])
        self.assertIn("CLIENT_SECRET_KEY_ID=\n", result["restricted_env"])
        self.assertIn("CLIENT_SECRET_EXPIRES_AT=\n", result["restricted_env"])
        self.assertIn("multiple active keys", result["output"])
        self.assertIn("-RotateCredential", result["output"])

    def test_revoked_or_expired_saved_key_is_not_validated_by_another_active_key(self):
        for scenario in ("revoked_key", "expired_key"):
            with self.subTest(scenario=scenario):
                result = self.run_setup(scenario)
                self.assertEqual("", result["error"])
                self.assertNotIn("ValidateSecret", result["calls"])
                self.assertIn("Password", result["calls"])
                self.assertIn("CLIENT_SECRET_KEY_ID=new-key-id\n", result["restricted_env"])
                self.assertIn("CLIENT_SECRET=new-mock-secret\n", result["restricted_env"])
                self.assertNotIn("saved-mock-secret", result["restricted_env"])

    def test_invalid_or_expired_saved_secret_is_replaced_without_logging_secret(self):
        for scenario in ("wrong_secret", "token_expired"):
            with self.subTest(scenario=scenario):
                result = self.run_setup(scenario)
                self.assertEqual("", result["error"])
                self.assertIn("ValidateSecret", result["calls"])
                self.assertIn("Password", result["calls"])
                self.assertIn("CLIENT_SECRET_KEY_ID=new-key-id\n", result["restricted_env"])
                self.assertIn("CLIENT_SECRET=new-mock-secret\n", result["restricted_env"])
                self.assertNotIn("saved-mock-secret", result["output"])
                self.assertNotIn("test-only-access-token", result["output"])

    def test_uncertain_token_failure_stops_before_mutation_and_redacts_response(self):
        for scenario in ("network_failure", "policy_failure"):
            with self.subTest(scenario=scenario):
                result = self.run_setup(scenario)
                self.assertIn("Could not verify the saved client secret", result["error"])
                self.assertFalse({"NewApplication", "UpdateApplication", "NewPrincipal", "Password", "ConsentBrowser"} & set(result["calls"]))
                self.assertNotIn("PERMISSION_PROFILE", result["restricted_env"])
                self.assertNotIn("saved-mock-secret", result["error"] + result["output"])
                self.assertNotIn("UNSAFE", result["error"] + result["output"])

    def test_expiring_secret_warns_and_does_not_rotate_automatically(self):
        result = self.run_setup("expiring_key")
        self.assertEqual("", result["error"])
        self.assertIn("saved client secret expires", result["output"])
        self.assertIn("-RotateCredential before that date", result["output"])
        self.assertNotIn("Password", result["calls"])

    def test_atomic_configuration_preserves_operator_comments_and_settings(self):
        result = self.run_setup("preserve_comments")
        self.assertEqual("", result["error"])
        for value in ("# customer notes\n", "export TENANT_ID = tenant-current # target tenant\n", "CLIENT_SECRET=saved-mock-secret # vault reference\n", 'CUSTOM_SETTING="unchanged # literal"\n'):
            self.assertIn(value, result["restricted_env"])
        self.assertEqual({}, result["recovery_files"])

    def test_secret_and_complete_identity_are_saved_before_interrupted_consent(self):
        result = self.run_setup("consent_interrupted")
        self.assertIn("Mock interrupted consent", result["error"])
        self.assertIn("Password", result["calls"])
        self.assertIn("ConsentBrowser", result["calls"])
        self.assertEqual(result["restricted_env"], result["consent_environment"])
        for value in ("TENANT_ID=tenant-current\n", "CLIENT_ID=app-current\n", "PERMISSION_PROFILE=restricted\n", "CLIENT_SECRET=new-mock-secret\n", "CLIENT_SECRET_KEY_ID=new-key-id\n", "CLIENT_SECRET_EXPIRES_AT=", "PURVIEW_ORGANIZATION=test.onmicrosoft.com\n"):
            self.assertIn(value, result["consent_environment"])
        self.assertNotIn("saved-mock-secret", result["consent_environment"])
        self.assertEqual({}, result["recovery_files"])

    def test_secret_is_saved_without_rereading_environment_after_creation(self):
        result = self.run_setup("env_reread_failure")
        self.assertEqual("", result["error"])
        self.assertIn("Password", result["calls"])
        self.assertNotIn("EnvironmentReadAfterPassword", result["calls"])
        self.assertIn("CLIENT_SECRET=new-mock-secret\n", result["restricted_env"])
        self.assertIn("CLIENT_SECRET_KEY_ID=new-key-id\n", result["restricted_env"])
        self.assertIn("PERMISSION_PROFILE=restricted\n", result["restricted_env"])
        self.assertNotIn("new-mock-secret", result["error"] + result["output"])
        self.assertEqual({}, result["recovery_files"])

    @unittest.skipUnless(os.name == "nt", "Windows file sharing prevents atomic replacement")
    def test_replacement_failure_keeps_old_environment_and_private_recovery(self):
        result = self.run_setup("replace_failure")
        self.assertIn("Environment replacement failed", result["error"])
        self.assertIn("private recovery file", result["error"])
        self.assertIn("CLIENT_SECRET=saved-mock-secret\n", result["restricted_env"])
        self.assertNotIn("PERMISSION_PROFILE", result["restricted_env"])
        self.assertNotIn("ConsentBrowser", result["calls"])
        self.assertEqual(1, len(result["recovery_files"]))
        recovery = next(iter(result["recovery_files"].values()))
        self.assertIn("CLIENT_SECRET=new-mock-secret\n", recovery)
        self.assertIn("CLIENT_SECRET_KEY_ID=new-key-id\n", recovery)
        self.assertIn("PERMISSION_PROFILE=restricted\n", recovery)
        self.assertIn(".env.*.recovery.*", result["gitignore"])
        self.assertNotIn("new-mock-secret", result["error"] + result["output"])

    @unittest.skipUnless(os.name == "nt", "Windows file sharing prevents staged file writes")
    def test_staging_write_failure_preserves_new_secret_in_temporary_recovery(self):
        result = self.run_setup("write_failure")
        self.assertIn("Environment save failed", result["error"])
        self.assertIn("CLIENT_SECRET=saved-mock-secret\n", result["restricted_env"])
        self.assertNotIn("ConsentBrowser", result["calls"])
        recovery = [value for path, value in result["recovery_files"].items() if "temporary-recovery" in path]
        self.assertEqual(1, len(recovery))
        self.assertIn("CLIENT_SECRET=new-mock-secret\n", recovery[0])
        self.assertIn("CLIENT_SECRET_KEY_ID=new-key-id\n", recovery[0])
        self.assertNotIn("new-mock-secret", result["error"] + result["output"])

    @unittest.skipUnless(os.name == "nt", "Windows credential file ACL preparation")
    def test_unwritable_private_staging_file_prevents_credential_creation(self):
        result = self.run_setup("staging_failure", tenant="different-tenant")
        self.assertIn("Unable to prepare a private credential recovery file", result["error"])
        self.assertNotIn("Password", result["calls"])
        self.assertNotIn("ConsentBrowser", result["calls"])
        self.assertIn("TENANT_ID=different-tenant\n", result["restricted_env"])
        self.assertEqual({}, result["recovery_files"])


if __name__ == "__main__":
    unittest.main()

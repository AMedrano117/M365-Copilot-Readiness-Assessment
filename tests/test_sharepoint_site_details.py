"""Execute the real site-detail reader with SPO cmdlets mocked locally."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from Core.orchestrator_powershell import powershell_environment
from Core.sharepoint_governance import merge_sharepoint_payloads


POWERSHELL = shutil.which('powershell') or shutil.which('pwsh')
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(POWERSHELL, 'A local PowerShell host is required')
class SharePointIdentityReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (ROOT / 'collect_sharepoint_governance.ps1').read_text(encoding='utf-8-sig')
        functions = source[source.index('function Convert-ObjectToMap {'):source.index('function Find-SharePointModuleManifest {')]
        properties = source[source.index('$siteProperties = @('):source.index('$payload = [ordered]@{')]
        code = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$script:usingCertificate = $false
function Write-CollectionStep { param($Kind,$Value) }
function Get-SPOSite {
    [CmdletBinding()]
    param([string]$Identity, [string]$Limit)
    $script:Calls += [ordered]@{ Identity=$Identity; Limit=$Limit }
    if ($Limit) {
        if ($script:Scenario -eq 'listing_error') { throw 'Synthetic inventory failure' }
        if ($script:Scenario -eq 'empty') { return @() }
        if ($script:Scenario -eq 'missing_url') { return [pscustomobject]@{ Title='Missing URL'; SiteId='stable-site' } }
        return @(
            [pscustomobject]@{ Url='https://fictional.test/alpha'; Title='Alpha inventory'; SharingCapability=0; AnonymousLinkExpirationInDays=0 },
            [pscustomobject]@{ Url='https://fictional.test/beta'; Title='Beta inventory'; SharingCapability=0; AnonymousLinkExpirationInDays=0 }
        )
    }
    if ($script:Scenario -eq 'failed' -or ($script:Scenario -eq 'partial' -and $Identity.EndsWith('/beta'))) {
        throw 'Synthetic access denied'
    }
    return [pscustomobject]@{ Url=$Identity; Title='Identity detail'; SharingCapability=2;
        DefaultSharingLinkType=1; DefaultLinkToExistingAccess=$true; AnonymousLinkExpirationInDays=7;
        OverrideTenantAnonymousLinkExpirationPolicy=$true; OverrideTenantExternalUserExpirationPolicy=$false;
        LastContentModifiedDate=[DateTime]::Parse('2026-09-28T12:00:00Z', [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind);
        LastItemModifiedDate=[DateTimeOffset]::Parse('2026-09-28T15:00:00+03:00'); SiteId='stable-site' }
}
''' + functions + properties + r'''
$results = [ordered]@{}
foreach ($scenario in @('success','partial','failed','empty','missing_url','listing_error')) {
    $script:Scenario = $scenario
    $script:Calls = @()
    try {
        $result = Get-SiteSettingsWithDetails -Properties $siteProperties
        $results[$scenario] = [ordered]@{ result=$result; calls=$script:Calls }
    } catch {
        $results[$scenario] = [ordered]@{ error=$_.Exception.Message; calls=$script:Calls }
    }
}
$results | ConvertTo-Json -Depth 20 -Compress
'''
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'mock-site-details.ps1'
            script.write_text(code, encoding='utf-8')
            response = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy','Bypass',
                '-File', str(script)], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
                env=powershell_environment(POWERSHELL), check=False)
        if response.returncode:
            raise AssertionError(response.stderr)
        cls.results = json.loads(response.stdout)

    def test_identity_reads_replace_unverified_list_defaults_and_preserve_originals(self):
        case = self.results['success']
        result = case['result']
        self.assertEqual(result['records_collected'], 2)
        self.assertEqual(len(case['calls']), 3)
        self.assertEqual(case['calls'][0]['Limit'], 'All')
        self.assertEqual(case['calls'][1]['Identity'], 'https://fictional.test/alpha')
        row = result['items'][0]
        self.assertEqual(row['SharingCapability'], 2)
        self.assertEqual(row['InventoryRecord']['SharingCapability'], 0)
        self.assertEqual(row['AnonymousLinkExpirationInDays'], 7)
        self.assertTrue(row['OverrideTenantAnonymousLinkExpirationPolicy'])
        self.assertFalse(row['OverrideTenantExternalUserExpirationPolicy'])
        self.assertTrue(row['DefaultLinkToExistingAccess'])
        self.assertEqual(row['LastContentModifiedDate'], '2026-09-28T12:00:00.0000000Z')
        self.assertEqual(row['LastItemModifiedDate'], '2026-09-28T15:00:00.0000000+03:00')
        self.assertEqual(row['SettingsReadStatus'], 'available')
        self.assertTrue(row['InventoryObservedAt'])
        self.assertTrue(row['SettingsReadAt'])
        state = result['source_state']
        self.assertEqual(state['settings_read_mode'], 'per_site_identity')
        self.assertEqual(state['details_requested'], 2)
        self.assertEqual(state['details_collected'], 2)
        self.assertTrue(state['complete'])
        self.assertFalse(state['listing']['settings_verified'])
        self.assertEqual(state['collected_at'], state['collection_completed_at'])

    def test_failed_detail_retains_site_without_promoting_inventory_defaults(self):
        result = self.results['partial']['result']
        self.assertTrue(result['available'])
        self.assertEqual(result['records_collected'], 2)
        self.assertEqual(result['items'][0]['SettingsReadStatus'], 'available')
        failed = result['items'][1]
        self.assertEqual(failed['Url'], 'https://fictional.test/beta')
        self.assertEqual(failed['SettingsReadStatus'], 'unavailable')
        self.assertNotIn('SharingCapability', failed)
        self.assertNotIn('AnonymousLinkExpirationInDays', failed)
        self.assertEqual(failed['InventoryRecord']['SharingCapability'], 0)
        state = result['source_state']
        self.assertEqual(state['availability_status'], 'partial')
        self.assertFalse(state['complete'])
        self.assertEqual(state['details_collected'], 1)
        self.assertEqual(state['detail_failures'][0]['identity'], failed['Url'])
        self.assertIn('retained', state['reason'])

    def test_all_details_failed_still_retains_the_known_site_inventory(self):
        result = self.results['failed']['result']
        self.assertTrue(result['available'])
        self.assertEqual(result['records_collected'], 2)
        self.assertEqual(result['source_state']['availability_status'], 'partial')
        self.assertEqual(result['source_state']['details_collected'], 0)
        self.assertEqual(len(result['source_state']['detail_failures']), 2)
        self.assertTrue(all(row['SettingsReadStatus'] == 'unavailable' for row in result['items']))

    def test_empty_listing_and_missing_identity_do_not_invent_settings(self):
        empty = self.results['empty']['result']
        self.assertEqual(empty['items'], [])
        self.assertEqual(empty['source_state']['details_requested'], 0)
        missing = self.results['missing_url']
        self.assertEqual(len(missing['calls']), 1)
        self.assertEqual(missing['result']['items'][0]['SiteId'], 'stable-site')
        self.assertEqual(missing['result']['items'][0]['SettingsReadStatus'], 'unavailable')
        self.assertIn('no Url', missing['result']['source_state']['detail_failures'][0]['reason'])
        self.assertIn('Synthetic inventory failure', self.results['listing_error']['error'])


class SharePointPartialMergeTests(unittest.TestCase):
    def test_usable_partial_site_read_marks_merged_payload_partial(self):
        source = {'available':True, 'availability_status':'partial', 'complete':False,
                  'details_collected':1, 'records_collected':2}
        spo = {'tenant':{'available':True, 'settings':{'SharingCapability':2}},
            'sites':{'available':True, 'items':[{'Url':'https://fictional.test/alpha'},
                     {'Url':'https://fictional.test/beta', 'SettingsReadStatus':'unavailable'}]},
            'dag_reports':{'available':True, 'reports':[]},
            'collection_status':{'sharepoint_site_settings':source}}
        merged = merge_sharepoint_payloads({}, spo)
        self.assertTrue(merged['available'])
        self.assertEqual(merged['availability_status'], 'partial')
        self.assertEqual(merged['sites']['items'], spo['sites']['items'])
        self.assertEqual(merged['collection_status']['sharepoint_site_settings'], source)


if __name__ == '__main__':
    unittest.main()

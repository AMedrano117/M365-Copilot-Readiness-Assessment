param(
    [Parameter(Mandatory = $true)][string]$AdminUrl,
    [Parameter(Mandatory = $true)][string]$TenantId,
    [Parameter(Mandatory = $true)][string]$ClientId,
    [ValidateSet('Auto','Fresh','Skip')][string]$AuthMode = 'Auto',
    [string]$CertificatePath,
    [string]$CertificatePassword,
    [string]$CertificateThumbprint,
    [string]$DownloadPath,
    [switch]$ConnectionOnly,
    # Read secrets as one JSON line from stdin so they never appear in the
    # process command line. The Python orchestrator always uses this path.
    [switch]$SecretsFromStdin
)

if ($SecretsFromStdin) {
    $secretLine = [Console]::In.ReadLine()
    if ($secretLine) {
        $secretPayload = $secretLine | ConvertFrom-Json
        if ($secretPayload.certificate_password) { $CertificatePassword = [string]$secretPayload.certificate_password }
    }
    Remove-Variable -Name secretLine, secretPayload -ErrorAction SilentlyContinue
}

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

# Live progress for the Python orchestrator (stderr):
# PROGRESS:TOTAL:<n>, PROGRESS:STEP:<dataset starting>, PROGRESS:NOTE:<text>, PROGRESS:END.
function Write-CollectionStep {
    param([string]$Kind, [string]$Value = '')
    [Console]::Error.WriteLine("PROGRESS:${Kind}:$Value")
}

function Convert-ObjectToMap {
    param([object]$InputObject, [string[]]$Properties)
    $result = [ordered]@{}
    foreach ($name in $Properties) {
        $property = $InputObject.PSObject.Properties[$name]
        if ($null -ne $property) { $result[$name] = $property.Value }
    }
    return $result
}

function New-SourceState {
    param([bool]$Available, [string]$Reason = '', [int]$Records = 0)
    # Provenance: which identity read this dataset (see collector-registry.json auth_paths).
    $credentialType = if ($script:usingCertificate) { 'workload_app_certificate' } else { 'delegated_user' }
    $authPathId = if ($script:usingCertificate) { 'spo_admin_certificate' } else { 'spo_admin_delegated' }
    return [ordered]@{
        available = $Available
        availability_status = $(if ($Available) { 'available' } else { 'unavailable' })
        records_collected = $Records
        pages_collected = 1
        truncated = $false
        reason = $Reason
        credential_type = $credentialType
        auth_path_id = $authPathId
        coverage = 'full'
        evidence_quality = 'standard'
    }
}

function Find-SharePointModuleManifest {
    if ($env:SHAREPOINT_MODULE_PATH -and (Test-Path -LiteralPath $env:SHAREPOINT_MODULE_PATH)) {
        return $env:SHAREPOINT_MODULE_PATH
    }
    $available = Get-Module -ListAvailable -Name Microsoft.Online.SharePoint.PowerShell | Sort-Object Version -Descending | Select-Object -First 1
    if ($available -and (Test-Path -LiteralPath $available.Path)) { return $available.Path }

    $roots = @()
    $documents = [Environment]::GetFolderPath('MyDocuments')
    if ($documents) {
        $roots += (Join-Path $documents 'WindowsPowerShell\Modules')
        $roots += (Join-Path $documents 'PowerShell\Modules')
    }
    $profileRoot = [Environment]::GetFolderPath('UserProfile')
    if ($profileRoot) {
        $roots += (Join-Path $profileRoot 'Documents\WindowsPowerShell\Modules')
        $roots += (Join-Path $profileRoot 'Documents\PowerShell\Modules')
        foreach ($oneDriveRoot in @(Get-ChildItem -LiteralPath $profileRoot -Directory -Filter 'OneDrive*' -ErrorAction SilentlyContinue)) {
            $roots += (Join-Path $oneDriveRoot.FullName 'Documents\WindowsPowerShell\Modules')
            $roots += (Join-Path $oneDriveRoot.FullName 'Documents\PowerShell\Modules')
        }
    }
    $manifests = foreach ($root in @($roots | Select-Object -Unique)) {
        $moduleDirectory = Join-Path $root 'Microsoft.Online.SharePoint.PowerShell'
        if (Test-Path -LiteralPath $moduleDirectory) {
            Get-ChildItem -LiteralPath $moduleDirectory -Filter 'Microsoft.Online.SharePoint.PowerShell.psd1' -File -Recurse -ErrorAction SilentlyContinue
        }
    }
    return @($manifests | Sort-Object {
        try { [version]$_.Directory.Name } catch { [version]'0.0' }
    } -Descending | Select-Object -First 1).FullName
}

$sharePointModuleManifest = Find-SharePointModuleManifest
if (-not $sharePointModuleManifest) {
    throw 'Microsoft.Online.SharePoint.PowerShell is not installed. Install-Module Microsoft.Online.SharePoint.PowerShell -Scope CurrentUser'
}
if ($PSVersionTable.PSVersion.Major -ge 7 -and $IsWindows) {
    Import-Module $sharePointModuleManifest -UseWindowsPowerShell -ErrorAction Stop | Out-Null
} else {
    Import-Module $sharePointModuleManifest -ErrorAction Stop | Out-Null
}
$loadedSharePointModule = Get-Module Microsoft.Online.SharePoint.PowerShell | Sort-Object Version -Descending | Select-Object -First 1
if (-not $loadedSharePointModule -or $loadedSharePointModule.Version -lt [version]'16.0.27215.12000') {
    throw "Microsoft.Online.SharePoint.PowerShell 16.0.27215.12000 or later is required for item-level Everyone/EEEU report coverage. Installed: $($loadedSharePointModule.Version)"
}

Write-CollectionStep NOTE 'signing in to SharePoint administration'
$script:usingCertificate = -not [string]::IsNullOrWhiteSpace($CertificateThumbprint) -or -not [string]::IsNullOrWhiteSpace($CertificatePath)
$usingCertificate = $script:usingCertificate
if ($usingCertificate) {
    [Console]::Error.WriteLine('AUTH_REUSED:SharePoint:Using application certificate authentication')
    if ($CertificateThumbprint) {
        Connect-SPOService -Url $AdminUrl -ClientId $ClientId -TenantId $TenantId -CertificateThumbprint $CertificateThumbprint | Out-Null
    } else {
        $securePassword = $null
        if ($CertificatePassword) {
            $securePassword = ConvertTo-SecureString $CertificatePassword -AsPlainText -Force
        }
        if ($securePassword) {
            Connect-SPOService -Url $AdminUrl -ClientId $ClientId -TenantId $TenantId -CertificatePath $CertificatePath -CertificatePassword $securePassword | Out-Null
        } else {
            Connect-SPOService -Url $AdminUrl -ClientId $ClientId -TenantId $TenantId -CertificatePath $CertificatePath | Out-Null
        }
    }
} else {
    if ($AuthMode -eq 'Skip') { throw 'SharePoint delegated authentication was skipped and no application certificate was configured.' }
    [Console]::Error.WriteLine('AUTH_PROMPT:SharePoint:Read tenant sharing settings and existing Data Access Governance report status')
    Connect-SPOService -Url $AdminUrl -UseSystemBrowser $true | Out-Null
    [Console]::Error.WriteLine('AUTH_COMPLETE:SharePoint')
}

if ($ConnectionOnly) {
    try {
        $null = Get-SPOTenant -ErrorAction Stop
        [ordered]@{ ready = $true; source = 'SharePoint Online Management Shell' } | ConvertTo-Json -Compress
        Disconnect-SPOService -ErrorAction SilentlyContinue | Out-Null
        exit 0
    } catch {
        [Console]::Error.WriteLine("CONNECTION_ERROR:SharePoint:$($_.Exception.Message)")
        Disconnect-SPOService -ErrorAction SilentlyContinue | Out-Null
        exit 2
    }
}

$tenantProperties = @(
    'SharingCapability','OneDriveSharingCapability','DefaultSharingLinkType','DefaultLinkPermission',
    'FileAnonymousLinkType','FolderAnonymousLinkType','RequireAnonymousLinksExpireInDays',
    'ExternalUserExpirationRequired','ExternalUserExpireInDays','OneDriveForGuestsEnabled',
    'PreventExternalUsersFromResharing','SharingDomainRestrictionMode','SharingAllowedDomainList',
    'SharingBlockedDomainList','LegacyAuthProtocolsEnabled','LegacyBrowserAuthProtocolsEnabled',
    'ConditionalAccessPolicy','IPAddressEnforcement','IPAddressAllowList','ShowEveryoneClaim',
    'ShowEveryoneExceptExternalUsersClaim','OrphanedPersonalSitesRetentionPeriod',
    'OneDriveStorageQuota','EnableAutoExpirationVersionTrim','MajorVersionLimit',
    'ExpireVersionsAfterDays','SelfServiceSiteCreationDisabled','IsLoopEnabled','IsFluidEnabled',
    'IsWhiteboardEnabled','IsCollabMeetingNotesFluidEnabled','EnableRestrictedAccessControl'
)
$siteProperties = @(
    'Url','Title','Template','Owner','SharingCapability','DefaultSharingLinkType',
    'DefaultLinkPermission','AnonymousLinkExpirationInDays','ExternalUserExpirationInDays',
    'ConditionalAccessPolicy','LimitedAccessFileType','SensitivityLabel',
    'RestrictContentOrgWideSearch','RestrictedAccessControl'
)

$payload = [ordered]@{
    source = 'SharePoint Online Management Shell'
    admin_url = $AdminUrl
    authentication = $(if ($usingCertificate) { 'application_certificate' } else { 'delegated_browser' })
    tenant = [ordered]@{ available = $false; settings = [ordered]@{}; reason = '' }
    sites = [ordered]@{ available = $false; records_collected = 0; items = @(); reason = '' }
    dag_reports = [ordered]@{ available = $false; records_collected = 0; reports = @(); reason = '' }
    dag_activity_data = [ordered]@{ available = $false; records_collected = 0; items = @(); reason = '' }
    exported_files = @()
    collection_status = [ordered]@{}
}

Write-CollectionStep TOTAL 6
Write-CollectionStep STEP 'tenant sharing settings'
try {
    $tenant = Get-SPOTenant
    $payload.tenant.available = $true
    $payload.tenant.settings = Convert-ObjectToMap -InputObject $tenant -Properties $tenantProperties
    $payload.collection_status['sharepoint_tenant_settings'] = New-SourceState -Available $true -Records 1
} catch {
    $payload.tenant.reason = $_.Exception.Message
    $payload.collection_status['sharepoint_tenant_settings'] = New-SourceState -Available $false -Reason $_.Exception.Message
}

Write-CollectionStep STEP 'site sharing settings (all sites)'
try {
    $siteItems = @(Get-SPOSite -Limit All -Detailed | ForEach-Object { Convert-ObjectToMap -InputObject $_ -Properties $siteProperties })
    $payload.sites.available = $true
    $payload.sites.items = $siteItems
    $payload.sites.records_collected = $siteItems.Count
    $payload.collection_status['sharepoint_site_settings'] = New-SourceState -Available $true -Records $siteItems.Count
} catch {
    $payload.sites.reason = $_.Exception.Message
    $payload.collection_status['sharepoint_site_settings'] = New-SourceState -Available $false -Reason $_.Exception.Message
}

Write-CollectionStep STEP 'Copilot content controls'
# Copilot content controls. Cmdlets and properties vary by module version, so
# each is read only when present; absence is reported, never treated as off.
$copilotControls = [ordered]@{ available = $false; restricted_search_mode = ''; restricted_search_allowed_sites = $null; reason = '' }
try {
    if (Get-Command Get-SPOTenantRestrictedSearchMode -ErrorAction SilentlyContinue) {
        $searchMode = Get-SPOTenantRestrictedSearchMode -ErrorAction Stop
        $modeValue = if ($searchMode.PSObject.Properties['Mode']) { $searchMode.Mode } else { $searchMode }
        $copilotControls.restricted_search_mode = [string]$modeValue
        $copilotControls.available = $true
        if (Get-Command Get-SPOTenantRestrictedSearchAllowedList -ErrorAction SilentlyContinue) {
            $copilotControls.restricted_search_allowed_sites = @(Get-SPOTenantRestrictedSearchAllowedList -ErrorAction Stop | Where-Object { $_ }).Count
        }
    } else {
        $copilotControls.reason = 'Get-SPOTenantRestrictedSearchMode is not available in the installed SharePoint Online module.'
    }
} catch {
    $copilotControls.reason = $_.Exception.Message
}
$payload['copilot_content_controls'] = $copilotControls

Write-CollectionStep STEP 'Data access governance reports'
$dagQueries = @(
    @{ entity='PermissionedUsers'; workload='SharePoint'; type='Snapshot' },
    @{ entity='PermissionedUsers'; workload='OneDriveForBusiness'; type='Snapshot' },
    @{ entity='Everyone' },
    @{ entity='EveryoneExceptExternalUsers' },
    @{ entity='EveryoneExceptExternalUsersAtSite'; workload='SharePoint'; type='RecentActivity' },
    @{ entity='EveryoneExceptExternalUsersForItems'; workload='SharePoint'; type='RecentActivity' },
    @{ entity='EveryoneExceptExternalUsersForItems'; workload='OneDriveForBusiness'; type='RecentActivity' },
    @{ entity='SharingLinks_Anyone'; workload='SharePoint'; type='RecentActivity' },
    @{ entity='SharingLinks_Anyone'; workload='OneDriveForBusiness'; type='RecentActivity' },
    @{ entity='SharingLinks_Guests'; workload='SharePoint'; type='RecentActivity' },
    @{ entity='SharingLinks_Guests'; workload='OneDriveForBusiness'; type='RecentActivity' },
    @{ entity='SharingLinks_PeopleInYourOrg'; workload='SharePoint'; type='RecentActivity' },
    @{ entity='SharingLinks_PeopleInYourOrg'; workload='OneDriveForBusiness'; type='RecentActivity' }
)
$dagRows = @()
$dagErrors = @()
$dagIndex = 0
foreach ($query in $dagQueries) {
    $dagIndex++
    Write-CollectionStep NOTE "Data access governance reports ($dagIndex of $($dagQueries.Count))"
    try {
        $queryParameters = @{ ReportEntity = $query.entity }
        if ($query.workload) { $queryParameters['Workload'] = $query.workload }
        if ($query.type) { $queryParameters['ReportType'] = $query.type }
        $results = @(Get-SPODataAccessGovernanceInsight @queryParameters)
        foreach ($result in $results) {
            $row = Convert-ObjectToMap -InputObject $result -Properties @(
                'ReportId','ReportID','ReportName','Status','ReportEntity','ReportType','Workload',
                'TriggeredDateTime','CreatedDateTime','CompletedDateTime','ReportStartTime','ReportEndTime',
                'StartTime','EndTime','SitesFound','SiteCount','SitesCount','CountOfSitesInReport','CountOfSitesInTenant'
            )
            $row['RequestedEntity'] = $query.entity
            $row['RequestedWorkload'] = $query.workload
            $row['RequestedType'] = $query.type
            $dagRows += $row
        }
    } catch {
        $dagErrors += "$($query.entity)/$($query.workload): $($_.Exception.Message)"
    }
}

Write-CollectionStep STEP 'recent-activity report status'
# Recent-activity reports require tenant-approved audit data collection. Read the
# state only; this assessment never starts or stops collection.
$activityEntities = @(
    'SharingLinksAnyone','SharingLinksPeopleInYourOrg','SharingLinksGuests',
    'EveryoneExceptExternalUsersAtSite','EveryoneExceptExternalUsersForItems','CopilotAppInsights'
)
$activityRows = @()
$activityErrors = @()
if (Get-Command Get-SPOAuditDataCollectionStatusForActivityInsights -ErrorAction SilentlyContinue) {
    foreach ($entity in $activityEntities) {
        try {
            $state = Get-SPOAuditDataCollectionStatusForActivityInsights -ReportEntity $entity -ErrorAction Stop
            $row = Convert-ObjectToMap -InputObject $state -Properties @('ReportEntity','Status','State','LastUpdatedDateTime','CreatedDateTime')
            $row['RequestedEntity'] = $entity
            $activityRows += $row
        } catch {
            $activityErrors += "${entity}: $($_.Exception.Message)"
        }
    }
} else {
    $activityErrors += 'Get-SPOAuditDataCollectionStatusForActivityInsights is not available in the installed module.'
}
$payload.dag_activity_data.items = $activityRows
$payload.dag_activity_data.records_collected = $activityRows.Count
$payload.dag_activity_data.available = $activityRows.Count -gt 0
if ($activityErrors.Count -gt 0) { $payload.dag_activity_data.reason = ($activityErrors -join ' | ') }
$activityStatus = if ($activityRows.Count -gt 0 -and $activityErrors.Count -gt 0) { 'partial' } elseif ($activityRows.Count -gt 0) { 'available' } else { 'unavailable' }
$payload.collection_status['sharepoint_dag_activity_data'] = [ordered]@{
    available = $activityRows.Count -gt 0
    availability_status = $activityStatus
    records_collected = $activityRows.Count
    pages_collected = 1
    truncated = $false
    reason = $payload.dag_activity_data.reason
}
$payload.dag_reports.reports = $dagRows
$payload.dag_reports.records_collected = $dagRows.Count
$payload.dag_reports.available = $dagRows.Count -gt 0 -or $dagErrors.Count -eq 0
if ($dagErrors.Count -gt 0) { $payload.dag_reports.reason = ($dagErrors -join ' | ') }
$dagStatus = if ($dagRows.Count -gt 0 -and $dagErrors.Count -gt 0) { 'partial' } elseif ($dagErrors.Count -eq 0) { 'available' } else { 'unavailable' }
$payload.collection_status['sharepoint_dag_reports'] = [ordered]@{
    available = $payload.dag_reports.available
    availability_status = $dagStatus
    records_collected = $dagRows.Count
    pages_collected = 1
    truncated = $false
    reason = $payload.dag_reports.reason
}

Write-CollectionStep STEP 'downloading completed reports'
# Reuse completed Microsoft reports without starting a new scan. Downloads remain in the
# tool's ignored cache and are passed into the existing SAM report parser by main.py.
if ($DownloadPath -and $dagRows.Count -gt 0) {
    New-Item -ItemType Directory -Path $DownloadPath -Force | Out-Null
    $completedIds = @($dagRows | Where-Object {
        $_.Status -match '^(Completed|Complete|Succeeded)$' -and ($_.ReportId -or $_.ReportID)
    } | ForEach-Object {
        if ($_.ReportId) { $_.ReportId } else { $_.ReportID }
    } | Select-Object -Unique)
    $exportErrors = @()
    $exportedFiles = @()
    foreach ($reportId in $completedIds) {
        $reportDownloadPath = $null
        $createdReportDirectory = $false
        try {
            # Microsoft can use the same filename for distinct completed
            # reports (for example Everyone and EEEU). Keep each report's
            # original filename in its own directory instead of overwriting.
            $reportGuid = [Guid]::Empty
            if (-not [Guid]::TryParse([string]$reportId, [ref]$reportGuid)) {
                throw 'Completed report ID is not a valid GUID.'
            }
            $reportDownloadPath = Join-Path -Path $DownloadPath -ChildPath ('report-' + $reportGuid.ToString('D'))
            New-Item -ItemType Directory -Path $reportDownloadPath -ErrorAction Stop | Out-Null
            $createdReportDirectory = $true
            Export-SPODataAccessGovernanceInsight -ReportID $reportId -DownloadPath $reportDownloadPath | Out-Null
        } catch {
            $exportErrors += "${reportId}: $($_.Exception.Message)"
        }
        if ($createdReportDirectory) {
            $exportedFiles += @(Get-ChildItem -LiteralPath $reportDownloadPath -File -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
                [ordered]@{ path = $_.FullName; modified_utc = $_.LastWriteTimeUtc.ToString('o'); report_id = [string]$reportId }
            })
        }
    }
    $payload.exported_files = $exportedFiles
    if ($completedIds.Count -gt 0) {
        $payload.collection_status['sharepoint_dag_exports'] = [ordered]@{
            available = $payload.exported_files.Count -gt 0
            availability_status = $(if ($exportErrors.Count -gt 0 -and $payload.exported_files.Count -gt 0) { 'partial' } elseif ($payload.exported_files.Count -gt 0) { 'available' } else { 'unavailable' })
            records_collected = $payload.exported_files.Count
            pages_collected = 1
            truncated = $false
            reason = $(if ($exportErrors.Count -gt 0) { $exportErrors -join ' | ' } elseif ($payload.exported_files.Count -eq 0) { 'Completed report exports returned no files.' } else { '' })
        }
    }
}

Write-CollectionStep END
$payload['available'] = $payload.tenant.available -or $payload.sites.available -or $payload.dag_reports.available
# SharePoint modules may write banners/warnings to stdout. Explicit framing keeps
# those messages separate from the single authoritative evidence document.
[Console]::Out.WriteLine('ASSESSMENT_SHAREPOINT_JSON_BEGIN')
[Console]::Out.WriteLine(($payload | ConvertTo-Json -Depth 12 -Compress))
[Console]::Out.WriteLine('ASSESSMENT_SHAREPOINT_JSON_END')
Disconnect-SPOService -ErrorAction SilentlyContinue | Out-Null

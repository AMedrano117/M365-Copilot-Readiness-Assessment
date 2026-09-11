# Check-PSModules.ps1
# Validates that all required PowerShell modules are installed

param(
    [Parameter(Mandatory=$false)]
    [ValidateSet("Purview", "PowerPlatform", "LegacyPowerPlatform", "SharePoint", "Setup", "All")]
    [string]$ScriptType = "All"
)

function Find-ModuleManifest {
    param([string]$ModuleName)

    $available = Get-Module -ListAvailable -Name $ModuleName | Sort-Object Version -Descending | Select-Object -First 1
    if ($available -and (Test-Path -LiteralPath $available.Path)) {
        return $available.Path
    }

    # Folder redirection and corporate OneDrive can put CurrentUser modules outside the
    # PSModulePath inherited by a new terminal or Python subprocess.
    $roots = @()
    $documents = [Environment]::GetFolderPath('MyDocuments')
    if ($documents) {
        $roots += (Join-Path $documents 'WindowsPowerShell\Modules')
        $roots += (Join-Path $documents 'PowerShell\Modules')
    }
    $userProfile = [Environment]::GetFolderPath('UserProfile')
    if ($userProfile) {
        $roots += (Join-Path $userProfile 'Documents\WindowsPowerShell\Modules')
        $roots += (Join-Path $userProfile 'Documents\PowerShell\Modules')
        foreach ($oneDriveRoot in @(Get-ChildItem -LiteralPath $userProfile -Directory -Filter 'OneDrive*' -ErrorAction SilentlyContinue)) {
            $roots += (Join-Path $oneDriveRoot.FullName 'Documents\WindowsPowerShell\Modules')
            $roots += (Join-Path $oneDriveRoot.FullName 'Documents\PowerShell\Modules')
        }
    }
    if ($env:ProgramFiles) {
        $roots += (Join-Path $env:ProgramFiles 'WindowsPowerShell\Modules')
        $roots += (Join-Path $env:ProgramFiles 'PowerShell\Modules')
    }

    $manifests = foreach ($root in @($roots | Select-Object -Unique)) {
        $moduleDirectory = Join-Path $root $ModuleName
        if (Test-Path -LiteralPath $moduleDirectory) {
            Get-ChildItem -LiteralPath $moduleDirectory -Filter "$ModuleName.psd1" -File -Recurse -ErrorAction SilentlyContinue
        }
    }
    return @($manifests | Sort-Object {
        try { [version]$_.Directory.Name } catch { [version]'0.0' }
    } -Descending | Select-Object -First 1).FullName
}

function Test-ModuleInstalled {
    param([string]$ModuleName, [version]$MinimumVersion = [version]'0.0')
    $manifestPath = Find-ModuleManifest -ModuleName $ModuleName
    if ([string]::IsNullOrWhiteSpace($manifestPath)) { return $false }
    try {
        $manifest = Test-ModuleManifest -Path $manifestPath -ErrorAction Stop
        return $manifest.Version -ge $MinimumVersion
    } catch { return $false }
}

function Show-ModuleStatus {
    param(
        [string]$ModuleName,
        [bool]$IsInstalled,
        [string]$InstallCommand,
        [switch]$Quiet
    )

    if ($Quiet) {
        return
    }
    
    if ($IsInstalled) {
        Write-Host "  [OK] " -ForegroundColor Green -NoNewline
        Write-Host "$ModuleName" -ForegroundColor White
    } else {
        Write-Host "  [MISSING] " -ForegroundColor Red -NoNewline
        Write-Host "$ModuleName" -ForegroundColor White
        Write-Host "    Install: " -ForegroundColor Yellow -NoNewline
        Write-Host $InstallCommand -ForegroundColor Cyan
    }
}

function Test-RequiredModules {
    param(
        [string]$ScriptType,
        [switch]$Quiet,
        [switch]$InstallMissing
    )
    
    $modules = @()
    
    # Define module requirements based on script type
    switch ($ScriptType) {
        "Purview" {
            $modules = @(
                @{Name = "ExchangeOnlineManagement"; MinimumVersion = [version]'3.2.0'; Install = "Install-Module ExchangeOnlineManagement -Scope CurrentUser -Force"}
            )
        }
        "PowerPlatform" {
            $modules = @()
        }
        "LegacyPowerPlatform" {
            $modules = @(
                @{Name = "Az.Accounts"; MinimumVersion = [version]'0.0'; Install = "Install-Module Az.Accounts -Scope CurrentUser -Force"}
            )
        }
        "SharePoint" {
            $modules = @(
                @{Name = "Microsoft.Online.SharePoint.PowerShell"; MinimumVersion = [version]'16.0.27215.12000'; Install = "Install-Module Microsoft.Online.SharePoint.PowerShell -Scope CurrentUser -Force -AllowClobber"}
            )
        }
        "Setup" {
            $modules = @(
                @{Name = "Microsoft.Graph.Authentication"; Install = "Install-Module Microsoft.Graph.Authentication -Scope CurrentUser -Force -AllowClobber"}
                @{Name = "Microsoft.Graph.Applications"; Install = "Install-Module Microsoft.Graph.Applications -Scope CurrentUser -Force -AllowClobber"}
                @{Name = "Microsoft.Graph.Identity.DirectoryManagement"; Install = "Install-Module Microsoft.Graph.Identity.DirectoryManagement -Scope CurrentUser -Force -AllowClobber"}
                @{Name = "ExchangeOnlineManagement"; MinimumVersion = [version]'3.2.0'; Install = "Install-Module ExchangeOnlineManagement -Scope CurrentUser -Force"}
            )
        }
        "All" {
            $modules = @(
                @{Name = "ExchangeOnlineManagement"; MinimumVersion = [version]'3.2.0'; Install = "Install-Module ExchangeOnlineManagement -Scope CurrentUser -Force"}
                @{Name = "Microsoft.Graph.Authentication"; Install = "Install-Module Microsoft.Graph.Authentication -Scope CurrentUser -Force -AllowClobber"}
                @{Name = "Microsoft.Graph.Applications"; Install = "Install-Module Microsoft.Graph.Applications -Scope CurrentUser -Force -AllowClobber"}
                @{Name = "Microsoft.Graph.Identity.DirectoryManagement"; Install = "Install-Module Microsoft.Graph.Identity.DirectoryManagement -Scope CurrentUser -Force -AllowClobber"}
                @{Name = "Microsoft.Online.SharePoint.PowerShell"; MinimumVersion = [version]'16.0.27215.12000'; Install = "Install-Module Microsoft.Online.SharePoint.PowerShell -Scope CurrentUser -Force -AllowClobber"}
            )
        }
    }
    
    $missingModules = @()
    $allInstalled = $true
    
    if (-not $Quiet) {
        Write-Host "Checking PowerShell module dependencies..." -ForegroundColor Cyan
    }
    
    foreach ($module in $modules) {
        $minimumVersion = if ($module.MinimumVersion) { $module.MinimumVersion } else { [version]'0.0' }
        $isInstalled = Test-ModuleInstalled -ModuleName $module.Name -MinimumVersion $minimumVersion
        if (-not $isInstalled -and $InstallMissing) {
            if (-not $Quiet) {
                Write-Host "  Installing $($module.Name) for the current user..." -ForegroundColor Yellow
            }
            try {
                [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
                Install-Module -Name $module.Name -Scope CurrentUser -Force -AllowClobber -Repository PSGallery -ErrorAction Stop
                $isInstalled = Test-ModuleInstalled -ModuleName $module.Name -MinimumVersion $minimumVersion
            } catch {
                if (-not $Quiet) {
                    Write-Host "    Installation failed: $($_.Exception.Message)" -ForegroundColor Red
                }
            }
        }
        Show-ModuleStatus -ModuleName $module.Name -IsInstalled $isInstalled -InstallCommand $module.Install -Quiet:$Quiet
        
        if (-not $isInstalled) {
            $allInstalled = $false
            if ($module -notin $missingModules) {
                $missingModules += $module
            }
        }
    }
    
    if (-not $allInstalled) {
        if (-not $Quiet) {
            Write-Host "`nERROR: Missing required PowerShell modules" -ForegroundColor Red
            Write-Host "`nTo install missing modules, run these commands:" -ForegroundColor Yellow
        }
        
        if (-not $Quiet) {
            foreach ($command in @($missingModules | ForEach-Object { $_.Install } | Sort-Object -Unique)) {
                Write-Host "  $command" -ForegroundColor Cyan
            }
        }

        if (-not $Quiet) {
            Write-Host "`nNote: You may need to run PowerShell as Administrator for installation." -ForegroundColor Yellow
        }
        
        return $false
    }
    
    if (-not $Quiet) {
        Write-Host "All required PowerShell modules are installed" -ForegroundColor Green
    }
    return $true
}

# If script is run directly (not dot-sourced)
if ($MyInvocation.InvocationName -ne '.') {
    $result = Test-RequiredModules -ScriptType $ScriptType -InstallMissing
    
    if (-not $result) {
        exit 1
    }
}

"""Initialization and setup functions for orchestrator."""

import json
import os
import shutil
import subprocess

from .get_graph_client import get_graph_client
from .services_and_licenses import ServicesAndLicenses
from .spinner import get_timestamp


async def load_modules_and_analyze(tenant_id, service_config):
    """Load recommendation modules and analyze service plans.
    
    Args:
        tenant_id: Azure tenant ID
        service_config: Dict with run_* flags from validate_and_prepare_services()
    """
    # Initialize progress bar with actual count
    from .module_loader import start_module_loading
    start_module_loading(service_config['services_to_load'])
    
    # Pre-load recommendation modules for selected services
    if service_config['run_m365']:
        import Recommendations.m365
    if service_config['run_entra']:
        import Recommendations.entra
    if service_config['run_defender']:
        import Recommendations.defender
    if service_config['run_purview']:
        import Recommendations.purview
    if service_config['run_power_platform']:
        import Recommendations.power_platform
    if service_config['run_copilot_studio']:
        import Recommendations.copilot_studio
    
    # Show feature analysis after modules are loaded
    from .check_all_service_plans import analyze_service_plans
    await analyze_service_plans(tenant_id, service_config['services'])
    
    # Print start message after modules are loaded
    if service_config['run_all']:
        print(f"[{get_timestamp()}] 🚀 Starting orchestration for all services...")
    else:
        print(f"[{get_timestamp()}] 🚀 Starting orchestration for: {', '.join(service_config['services'])}...")


async def setup_graph_and_licenses(tenant_id, show_graph_messages):
    """Initialize Microsoft Graph client and ServicesAndLicenses container.
    
    Args:
        tenant_id: Azure tenant ID
        show_graph_messages: Whether to print Graph connection messages
        
    Returns:
        Tuple: (graph_client, services_and_licenses, has_license_data)
    """
    # Always create Graph client (needed for license checks in all services)
    # Use silent mode for PowerShell-only runs (Purview, Power Platform)
    client = await get_graph_client(tenant_id, silent=not show_graph_messages)
    if show_graph_messages:
        print(f"[{get_timestamp()}] ✅ Connected to Microsoft Graph (Service Principal)")
    
    # Setup services container (needed by all pipelines)
    services_and_licenses = ServicesAndLicenses()
    has_license_data = False
    try:
        subscribed_skus = await client.subscribed_skus.get()
        if subscribed_skus:
            await services_and_licenses.set_raw_subscribed_skus(subscribed_skus)
            has_license_data = True
    except Exception as e:
        pass
    
    return client, services_and_licenses, has_license_data


def get_local_powershell_module_availability(install_sharepoint=False):
    """Discover and optionally install modules in the PowerShell host that uses them."""
    module_names = [
        "ExchangeOnlineManagement", "Az.Accounts",
        "Microsoft.Online.SharePoint.PowerShell",
    ]
    status = {name: False for name in module_names}
    status.update({
        "sharepoint_install_attempted": False,
        "sharepoint_install_succeeded": False,
        "sharepoint_install_error": "",
        "sharepoint_module_manifest": "",
        "module_details": {},
    })
    windows_host = shutil.which("powershell")
    modern_host = shutil.which("pwsh")
    host = windows_host or modern_host
    if not host:
        return status

    install_block = ""
    if install_sharepoint:
        install_block = (
            "$result.sharepoint_install_attempted=$true; "
            "foreach($spec in @(@{Name='ExchangeOnlineManagement';Minimum='3.2.0'},"
            "@{Name='Microsoft.Online.SharePoint.PowerShell';Minimum='16.0.27215.12000'})){ "
            "$found=Get-Module -ListAvailable -Name $spec.Name | Sort-Object Version -Descending | Select-Object -First 1; "
            "if((-not $found)-or($found.Version -lt [version]$spec.Minimum)){ "
            "Install-Module $spec.Name -Scope CurrentUser -Force -AllowClobber -Repository PSGallery -MinimumVersion $spec.Minimum } }; "
        )

    # Explicitly include redirected Documents/OneDrive module roots. This fixes
    # machines where the module was installed correctly but PSModulePath omitted
    # the redirected CurrentUser location.
    ps_script = (
        "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; "
        "$result=@{sharepoint_install_attempted=$false;sharepoint_install_succeeded=$false;sharepoint_install_error='';module_details=@{}}; "
        "$roots=@(); $docs=[Environment]::GetFolderPath('MyDocuments'); "
        "if($docs){$roots+=@((Join-Path $docs 'WindowsPowerShell\\Modules'),(Join-Path $docs 'PowerShell\\Modules'))}; "
        "$profileParent=Split-Path $docs -Parent; if($profileParent -and (Test-Path $profileParent)){ "
        "Get-ChildItem -LiteralPath $profileParent -Directory -Filter 'OneDrive*' -ErrorAction SilentlyContinue | ForEach-Object { "
        "$roots+=@((Join-Path $_.FullName 'Documents\\WindowsPowerShell\\Modules'),(Join-Path $_.FullName 'Documents\\PowerShell\\Modules')) } }; "
        "foreach($root in @($roots|Select-Object -Unique)){if((Test-Path $root)-and(($env:PSModulePath -split ';') -notcontains $root)){$env:PSModulePath=\"$root;$env:PSModulePath\"}}; "
        + install_block +
        "$requirements=@{'ExchangeOnlineManagement'=@('3.2.0','Connect-IPPSSession');'Az.Accounts'=@('0.0','Connect-AzAccount');"
        "'Microsoft.Online.SharePoint.PowerShell'=@('16.0.27215.12000','Get-SPODataAccessGovernanceInsight')}; "
        "foreach($name in $requirements.Keys){$m=Get-Module -ListAvailable -Name $name|Sort-Object Version -Descending|Select-Object -First 1; "
        "$ok=$false;$cmd=$false;if($m){try{Import-Module $m.Path -Force -ErrorAction Stop;$cmd=[bool](Get-Command $requirements[$name][1] -ErrorAction SilentlyContinue);"
        "$ok=($m.Version -ge [version]$requirements[$name][0])-and$cmd}catch{}}; "
        "$result[$name]=$ok;$result.module_details[$name]=@{available=$ok;version=$(if($m){[string]$m.Version}else{''});path=$(if($m){[string]$m.Path}else{''});cmdlet=$cmd;host=$PSVersionTable.PSEdition}}; "
        "$result.sharepoint_install_succeeded=[bool]$result['Microsoft.Online.SharePoint.PowerShell']; "
        "$sp=$result.module_details['Microsoft.Online.SharePoint.PowerShell'];if($sp){$result.sharepoint_module_manifest=$sp.path}; "
        "$result|ConvertTo-Json -Depth 6 -Compress"
    )
    try:
        completed = subprocess.run(
            [host, "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180 if install_sharepoint else 15, check=False,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            status["sharepoint_install_error"] = completed.stderr.strip()[:1000]
            return status
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        for name in module_names:
            status[name] = bool(result.get(name))
        for key in (
            "sharepoint_install_attempted", "sharepoint_install_succeeded",
            "sharepoint_install_error", "sharepoint_module_manifest", "module_details",
        ):
            status[key] = result.get(key, status[key])
        if modern_host and modern_host != host and (
            not status["ExchangeOnlineManagement"] or not status["Az.Accounts"]
        ):
            modern_probe = (
                "$r=@{module_details=@{}};$requirements=@{'ExchangeOnlineManagement'=@('3.2.0','Connect-IPPSSession');'Az.Accounts'=@('0.0','Connect-AzAccount')};"
                "foreach($name in $requirements.Keys){$m=Get-Module -ListAvailable -Name $name|Sort-Object Version -Descending|Select-Object -First 1;$ok=$false;$cmd=$false;"
                "if($m){try{Import-Module $m.Path -Force -ErrorAction Stop;$cmd=[bool](Get-Command $requirements[$name][1] -ErrorAction SilentlyContinue);$ok=($m.Version -ge [version]$requirements[$name][0])-and$cmd}catch{}};"
                "$r[$name]=$ok;$r.module_details[$name]=@{available=$ok;version=$(if($m){[string]$m.Version}else{''});path=$(if($m){[string]$m.Path}else{''});cmdlet=$cmd;host=$PSVersionTable.PSEdition}};$r|ConvertTo-Json -Depth 5 -Compress"
            )
            try:
                modern_result = subprocess.run(
                    [modern_host, "-NoProfile", "-Command", modern_probe],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=15, check=False,
                )
                if modern_result.returncode == 0 and modern_result.stdout.strip():
                    modern_detail = json.loads(modern_result.stdout.strip().splitlines()[-1])
                    for name in ("ExchangeOnlineManagement", "Az.Accounts"):
                        if modern_detail.get(name):
                            status[name] = True
                            status["module_details"][name] = modern_detail.get("module_details", {}).get(name, {})
            except Exception:
                # The Windows PowerShell result remains authoritative for
                # SharePoint. A failed secondary probe must not erase it.
                pass
    except Exception as exc:
        status["sharepoint_install_error"] = str(exc)

    if status['sharepoint_module_manifest']:
        os.environ['SHAREPOINT_MODULE_PATH'] = status['sharepoint_module_manifest']
    return status


def prepare_interactive_collection_plan(
    service_config, interactive_auth='auto', legacy_power_platform_collector=False,
    install_missing_modules=True,
):
    """Build a plan for optional interactive data collectors."""
    modules = get_local_powershell_module_availability(
        install_sharepoint=bool(service_config.get('run_m365')) and install_missing_modules
    )
    policy = interactive_auth or 'auto'

    purview_selected = service_config['run_purview']
    pp_selected = bool(legacy_power_platform_collector) and (
        service_config['run_power_platform'] or service_config['run_copilot_studio']
    )
    sharepoint_selected = service_config['run_m365']
    sharepoint_certificate = bool(
        os.environ.get('SHAREPOINT_CERTIFICATE_THUMBPRINT')
        or os.environ.get('SHAREPOINT_CERTIFICATE_PATH')
        or (os.environ.get('CERTIFICATE_PATH', '').lower().endswith(('.pfx', '.p12')))
    )
    purview_certificate = bool(
        os.environ.get('PURVIEW_CERTIFICATE_THUMBPRINT')
        or os.environ.get('PURVIEW_CERTIFICATE_PATH')
        or (os.environ.get('CERTIFICATE_PATH', '').lower().endswith(('.pfx', '.p12')))
    )

    purview_reason = None
    if purview_selected:
        if policy == 'skip' and not purview_certificate:
            purview_reason = 'skipped by policy'
        elif not modules.get('ExchangeOnlineManagement', False):
            purview_reason = 'ExchangeOnlineManagement module not installed'

    pp_reason = None
    if pp_selected:
        if policy == 'skip':
            pp_reason = 'skipped by policy'
        elif not modules.get('Az.Accounts', False):
            pp_reason = 'Az.Accounts module not installed'

    return {
        'policy': policy,
        'modules': modules,
        'purview': {
            'selected': purview_selected,
            'will_attempt': purview_selected and purview_reason is None,
            'skip_reason': purview_reason,
            'application_auth': purview_certificate,
        },
        'power_platform': {
            'selected': pp_selected,
            'will_attempt': pp_selected and pp_reason is None,
            'skip_reason': pp_reason,
            'legacy': bool(legacy_power_platform_collector),
        },
        'sharepoint': {
            'selected': sharepoint_selected,
            'will_attempt': sharepoint_selected
                and modules.get('Microsoft.Online.SharePoint.PowerShell', False)
                and (policy != 'skip' or sharepoint_certificate),
            'application_auth': sharepoint_certificate,
            'skip_reason': (
                None if not sharepoint_selected
                else 'Microsoft.Online.SharePoint.PowerShell module not installed'
                if not modules.get('Microsoft.Online.SharePoint.PowerShell', False)
                else 'delegated authentication skipped and no SharePoint certificate configured'
                if policy == 'skip' and not sharepoint_certificate
                else None
            ),
        },
    }


def print_interactive_collection_summary(interactive_plan):
    """Explain optional interactive collection behavior before orchestration starts."""
    lines = []

    policy = interactive_plan['policy']
    if policy == 'skip':
        lines.append(
            "Interactive collection: skipped. Sources that require delegated sign-in "
            "will be reported as not assessed."
        )
    elif policy == 'fresh':
        lines.append("Interactive deployment enrichment: enabled. A fresh Microsoft 365 sign-in will be requested if needed.")
    else:
        lines.append("Interactive deployment enrichment: enabled. Existing Microsoft 365 sign-ins will be reused when possible.")

    purview_plan = interactive_plan['purview']
    if purview_plan['selected']:
        if purview_plan['will_attempt']:
            method = 'application certificate' if purview_plan.get('application_auth') else 'browser sign-in'
            lines.append(f"Purview core policy collection selected using {method}.")
        else:
            lines.append(
                f"Purview deployment collection: skipped ({purview_plan['skip_reason']}). "
                "DLP and other Purview configuration will be reported as not assessed."
            )
            if purview_plan['skip_reason'] == 'ExchangeOnlineManagement module not installed':
                lines.append(
                    "Install locally with: Install-Module ExchangeOnlineManagement "
                    "-Scope CurrentUser -Force"
                )

    pp_plan = interactive_plan['power_platform']
    if pp_plan['selected']:
        if pp_plan['will_attempt']:
            lines.append(
                "Legacy Power Platform collection selected. This compatibility path uses Az.Accounts and delegated sign-in."
            )
        else:
            lines.append(
                f"Power Platform/Copilot Studio deployment collection: skipped ({pp_plan['skip_reason']}). "
                "Optional extensibility inventory will be reported as not assessed."
            )
            if pp_plan['skip_reason'] == 'Az.Accounts module not installed':
                lines.append(
                    "Install locally with: Install-Module Az.Accounts -Scope CurrentUser -Force. "
                    "The interactive user needs Power Platform Administrator in the target tenant."
                )

    sharepoint_plan = interactive_plan.get('sharepoint', {})
    if sharepoint_plan.get('selected'):
        if sharepoint_plan.get('will_attempt'):
            method = 'application certificate' if sharepoint_plan.get('application_auth') else 'browser sign-in'
            lines.append(
                f"SharePoint sharing and Data Access Governance collection selected using {method}."
            )
        else:
            lines.append(
                f"SharePoint governance collection: skipped ({sharepoint_plan.get('skip_reason')}). "
                "Sharing defaults and SAM report status will be reported as not assessed."
            )
            if sharepoint_plan.get('skip_reason') == 'Microsoft.Online.SharePoint.PowerShell module not installed':
                install_error = interactive_plan.get('modules', {}).get('sharepoint_install_error', '')
                lines.append("The tool attempted to install the SharePoint module for the current user but it is still unavailable.")
                if install_error:
                    lines.append(f"Installation detail: {install_error}")
                lines.append("Manual fallback: Install-Module Microsoft.Online.SharePoint.PowerShell -Scope CurrentUser -Force -AllowClobber")

    if not lines:
        return

    print(f"[{get_timestamp()}] ℹ️  Interactive collection pre-check:")
    for line in lines:
        print(f"[{get_timestamp()}]     {line}")

    if interactive_plan['policy'] == 'auto':
        print(
            f"[{get_timestamp()}]     Tip: use --interactive-auth skip to avoid optional prompts "
            "when running with read-only or non-admin roles."
        )

"""Initialization and setup functions for orchestrator."""

import json
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


def get_local_powershell_module_availability():
    """Check if optional interactive collection modules are installed locally."""
    module_names = ["ExchangeOnlineManagement", "Az.Accounts"]
    default_status = {name: False for name in module_names}

    ps_script = (
        "$result = @{}; "
        "foreach ($name in @('ExchangeOnlineManagement','Az.Accounts')) { "
        "$result[$name] = [bool](Get-Module -ListAvailable -Name $name) "
        "}; "
        "$result | ConvertTo-Json -Compress"
    )

    try:
        completed = subprocess.run(
            ["pwsh", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return default_status
        result = json.loads(completed.stdout.strip())
        return {name: bool(result.get(name)) for name in module_names}
    except Exception:
        return default_status


def prepare_interactive_collection_plan(service_config, interactive_auth='auto'):
    """Build a plan for optional interactive data collectors."""
    modules = get_local_powershell_module_availability()
    policy = interactive_auth or 'auto'

    purview_selected = service_config['run_purview']
    pp_selected = service_config['run_power_platform'] or service_config['run_copilot_studio']

    purview_reason = None
    if purview_selected:
        if policy == 'skip':
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
        },
        'power_platform': {
            'selected': pp_selected,
            'will_attempt': pp_selected and pp_reason is None,
            'skip_reason': pp_reason,
        },
    }


def print_interactive_collection_summary(interactive_plan):
    """Explain optional interactive collection behavior before orchestration starts."""
    lines = []

    policy = interactive_plan['policy']
    if policy == 'skip':
        lines.append("Interactive deployment enrichment: skipped. Continuing with license/basic recommendations only.")
    elif policy == 'fresh':
        lines.append("Interactive deployment enrichment: enabled. A fresh Microsoft 365 sign-in will be requested if needed.")
    else:
        lines.append("Interactive deployment enrichment: enabled. Existing Microsoft 365 sign-ins will be reused when possible.")

    purview_plan = interactive_plan['purview']
    if purview_plan['selected']:
        if purview_plan['will_attempt']:
            lines.append(
                "Purview deployment data selected. Admin-level delegated access may be required for full results."
            )
        else:
            lines.append(
                f"Purview deployment collection: skipped ({purview_plan['skip_reason']}). "
                "Continuing with license-based recommendations."
            )

    pp_plan = interactive_plan['power_platform']
    if pp_plan['selected']:
        if pp_plan['will_attempt']:
            lines.append(
                "Power Platform/Copilot Studio deployment data selected. Admin-level delegated access may be required for full results."
            )
        else:
            lines.append(
                f"Power Platform/Copilot Studio deployment collection: skipped ({pp_plan['skip_reason']}). "
                "Continuing with license/basic recommendations."
            )

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

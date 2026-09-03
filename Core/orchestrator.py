import asyncio
import sys
from datetime import datetime
from azure.identity._exceptions import CredentialUnavailableError
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from .processor import process_and_print_all_information
from .spinner import get_timestamp
from .orchestrator_validation import validate_and_prepare_services
from .orchestrator_setup import (
    load_modules_and_analyze,
    setup_graph_and_licenses,
    prepare_interactive_collection_plan,
    print_interactive_collection_summary
)
from .orchestrator_powershell import collect_power_platform_data
from .orchestrator_pipelines import create_pipelines

# Service-specific imports are now lazy-loaded based on SERVICES parameter


async def resolve_tenant_name(client, tenant_id):
    """Best-effort tenant display name lookup for report labeling."""
    try:
        org = await client.organization.get()
        if org and getattr(org, 'value', None):
            org_details = org.value[0]
            display_name = getattr(org_details, 'display_name', None)
            if display_name:
                return display_name
    except Exception:
        pass
    return tenant_id

async def orchestrate(
    tenant_id,
    services=None,
    interactive_auth='auto',
    open_html_report=False,
    report_format='excel',
    sam_report_paths=None,
    dspm_report_paths=None,
):
    """Orchestrate gathering of service information and service plans.
    
    Args:
        tenant_id: Azure tenant ID (GUID or domain name)
        services: List of services to analyze. Valid values: "M365", "Entra", "Defender", 
                  "Purview", "Power Platform", "Copilot Studio". Empty list or None = all services.
    """
    try:
        # Validate service selection and prepare flags
        service_config = validate_and_prepare_services(services)
        if service_config is None:
            return  # Invalid services specified
        
        # Extract service flags for easy access
        run_all = service_config['run_all']
        run_m365 = service_config['run_m365']
        run_entra = service_config['run_entra']
        run_defender = service_config['run_defender']
        run_purview = service_config['run_purview']
        run_power_platform = service_config['run_power_platform']
        run_copilot_studio = service_config['run_copilot_studio']
        
        # Load modules and analyze service plans
        await load_modules_and_analyze(tenant_id, service_config)

        interactive_plan = prepare_interactive_collection_plan(service_config, interactive_auth)
        print_interactive_collection_summary(interactive_plan)
        
        # PRE-FLIGHT: Launch unified Power Platform/Copilot Studio data collector if needed
        if interactive_plan['power_platform']['will_attempt']:
            await collect_power_platform_data(
                tenant_id,
                run_power_platform,
                run_copilot_studio,
                auth_mode=interactive_auth,
            )
        
        # Check if we need Graph client messages (only for Graph-based services)
        # PowerShell-based services still need client for licenses, but silently
        graph_services = ['m365', 'entra', 'defender', 'copilot_studio']
        show_graph_messages = run_all or any(s.lower() in graph_services for s in service_config['services'])
        
        # Initialize Graph client and licenses
        client, services_and_licenses, has_license_data = await setup_graph_and_licenses(tenant_id, show_graph_messages)
        tenant_name = await resolve_tenant_name(client, tenant_id)
        
        # Create service pipelines with shared context
        pipelines = create_pipelines(
            client,
            services_and_licenses,
            tenant_id,
            service_config,
            interactive_auth=interactive_auth,
            interactive_plan=interactive_plan
        )
        
        # Run independent service pipelines in parallel
        (m365_result, entra_info, purview_info, defender_info, power_platform_info, copilot_studio_info) = await asyncio.gather(
            pipelines['m365'](),
            pipelines['entra'](),
            pipelines['purview'](),
            pipelines['defender'](),
            pipelines['power_platform'](),
            pipelines['copilot_studio']()
        )
        
        print(f"[{get_timestamp()}] ✅ All service information gathered")
        
        # Process and print all information and recommendations
        process_and_print_all_information(
            m365_result, entra_info, 
            purview_info, defender_info, power_platform_info, 
            copilot_studio_info,
            tenant_name=tenant_name,
            open_html_report=open_html_report,
            report_format=report_format,
            sam_report_paths=sam_report_paths,
            dspm_report_paths=dspm_report_paths,
            data_exposure_enabled=(run_m365 or run_purview),
        )
        
    except CredentialUnavailableError as e:
        print("\n" + "="*80)
        print("AUTHENTICATION ERROR")
        print("="*80)
        print(f"\nFailed to authenticate using Azure CLI: {str(e)}")
        print("\nPlease ensure:")
        print("  1. Azure CLI is installed and in your PATH")
        print("  2. You are logged in: Run 'az login'")
        print("  3. Your account has access to the tenant")
        print("="*80)
    except ClientAuthenticationError as e:
        print("\n" + "="*80)
        print("AUTHENTICATION ERROR")
        print("="*80)
        print(f"\nAuthentication failed: {str(e)}")
        print("\nPlease check your credentials and permissions.")
        print("="*80)
    except HttpResponseError as e:
        print("\n" + "="*80)
        print("PERMISSION ERROR")
        print("="*80)
        print(f"\nHTTP {e.status_code}: {e.message}")
        if e.status_code == 403:
            print("\nYou don't have sufficient permissions for this operation.")
            print("Required role: Global Reader or Global Administrator")
        print("="*80)
    except Exception as e:
        print("\n" + "="*80)
        print("ERROR")
        print("="*80)
        print(f"\nAn unexpected error occurred: {str(e)}")
        print(f"Error type: {type(e).__name__}")
        print("="*80)
        raise

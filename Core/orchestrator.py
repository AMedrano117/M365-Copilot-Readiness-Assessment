import asyncio
import sys
from urllib.parse import urlparse
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
from .orchestrator_powershell import collect_power_platform_data, collect_sharepoint_governance_via_powershell
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


def is_valid_sharepoint_admin_url(value):
    """Accept only an HTTPS SharePoint administrative host."""
    try:
        parsed = urlparse(str(value or '').strip())
        return (
            parsed.scheme.lower() == 'https'
            and parsed.hostname is not None
            and parsed.hostname.lower().endswith('-admin.sharepoint.com')
            and not parsed.path.strip('/')
        )
    except Exception:
        return False


async def resolve_sharepoint_admin_url(
    client, explicit_url=None, interactive_auth='auto', prompt=input
):
    """Resolve the SharePoint admin URL using documented operator precedence."""
    os = __import__('os')
    configured = (explicit_url or '').strip()
    resolved = configured.rstrip('/') if is_valid_sharepoint_admin_url(configured) else ''
    if not resolved:
        configured = os.environ.get('SHAREPOINT_ADMIN_URL', '').strip()
        resolved = configured.rstrip('/') if is_valid_sharepoint_admin_url(configured) else ''

    # The explicit URL remains authoritative, but the initial tenant domain is also
    # useful for certificate-based Purview authentication. Resolve it independently
    # when it was not supplied in the environment.
    try:
        if not os.environ.get('PURVIEW_ORGANIZATION', '').strip() or not resolved:
            org = await client.organization.get()
            if org and getattr(org, 'value', None):
                domains = getattr(org.value[0], 'verified_domains', None) or []
                names = [getattr(domain, 'name', '') for domain in domains]
                initial = next((getattr(domain, 'name', '') for domain in domains if getattr(domain, 'is_initial', False)), '')
                domain = initial or next((name for name in names if name.endswith('.onmicrosoft.com')), '')
                if domain:
                    os.environ.setdefault('PURVIEW_ORGANIZATION', domain)
                    if not resolved:
                        resolved = f"https://{domain.split('.')[0]}-admin.sharepoint.com"
    except Exception:
        pass
    if resolved:
        return resolved
    if interactive_auth != 'skip' and sys.stdin.isatty():
        entered = prompt('SharePoint admin URL (for example https://contoso-admin.sharepoint.com): ').strip()
        return entered.rstrip('/') if is_valid_sharepoint_admin_url(entered) else ''
    return ''

async def orchestrate(
    tenant_id,
    services=None,
    interactive_auth='auto',
    open_html_report=False,
    report_format='excel',
    sam_report_paths=None,
    dspm_report_paths=None,
    include_user_usage_detail=False,
    copilot_dashboard_export=None,
    power_platform_inventory=None,
    preview_collectors='none',
    sharepoint_admin_url=None,
    legacy_power_platform_collector=False,
    check_connections=False,
    assessment_profile=None,
    provider_evidence=None,
    snapshot_json=None,
    baseline=None,
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
            return 1
        
        # Extract service flags for easy access
        run_all = service_config['run_all']
        run_m365 = service_config['run_m365']
        run_entra = service_config['run_entra']
        run_defender = service_config['run_defender']
        run_purview = service_config['run_purview']
        run_power_platform = service_config['run_power_platform']
        run_copilot_studio = service_config['run_copilot_studio']

        if check_connections:
            interactive_plan = prepare_interactive_collection_plan(
                service_config,
                interactive_auth,
                legacy_power_platform_collector=legacy_power_platform_collector,
                install_missing_modules=False,
            )
            client, _, _ = await setup_graph_and_licenses(tenant_id, True)
            resolved_sharepoint_admin_url = await resolve_sharepoint_admin_url(
                client,
                explicit_url=sharepoint_admin_url,
                interactive_auth=interactive_auth,
            )
            from .connection_validation import (
                connection_exit_code,
                print_connection_results,
                run_connection_checks,
            )
            results = await run_connection_checks(
                client,
                service_config,
                interactive_plan,
                preview_collectors=preview_collectors,
                legacy_power_platform_collector=legacy_power_platform_collector,
                sharepoint_admin_url=resolved_sharepoint_admin_url,
                interactive_auth=interactive_auth,
                tenant_id=tenant_id,
            )
            print_connection_results(results, detailed=True)
            return connection_exit_code(results)
        
        # Load modules and analyze service plans
        await load_modules_and_analyze(tenant_id, service_config)

        interactive_plan = prepare_interactive_collection_plan(
            service_config,
            interactive_auth,
            legacy_power_platform_collector=legacy_power_platform_collector,
        )
        print_interactive_collection_summary(interactive_plan)
        
        # PRE-FLIGHT: Launch unified Power Platform/Copilot Studio data collector if needed
        use_inventory_source = bool(power_platform_inventory) or preview_collectors in {'power-platform', 'all'}
        if legacy_power_platform_collector and interactive_plan['power_platform']['will_attempt'] and not use_inventory_source:
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
        resolved_sharepoint_admin_url = await resolve_sharepoint_admin_url(
            client,
            explicit_url=sharepoint_admin_url,
            interactive_auth=interactive_auth,
        )
        from .connection_validation import print_connection_results, run_connection_checks
        connection_results = await run_connection_checks(
            client,
            service_config,
            interactive_plan,
            preview_collectors=preview_collectors,
            legacy_power_platform_collector=legacy_power_platform_collector,
            sharepoint_admin_url=resolved_sharepoint_admin_url,
            interactive_auth=interactive_auth,
            probe_endpoints=False,
            tenant_id=tenant_id,
        )
        print_connection_results(connection_results, detailed=False)
        sharepoint_data = {"available": False, "reason": "SharePoint governance collection was not attempted."}
        sharepoint_plan = interactive_plan.get('sharepoint', {})
        if sharepoint_plan.get('will_attempt'):
            sharepoint_data = await collect_sharepoint_governance_via_powershell(
                resolved_sharepoint_admin_url, tenant_id, auth_mode=interactive_auth
            )
            if (
                not sharepoint_data.get('available')
                and interactive_auth != 'skip'
                and sys.stdin.isatty()
            ):
                retry_url = input(
                    'SharePoint connection failed. Enter the verified admin URL to retry once, or press ENTER to continue: '
                ).strip()
                if is_valid_sharepoint_admin_url(retry_url) and retry_url.rstrip('/') != resolved_sharepoint_admin_url:
                    sharepoint_data = await collect_sharepoint_governance_via_powershell(
                        retry_url.rstrip('/'), tenant_id, auth_mode=interactive_auth
                    )
        elif sharepoint_plan.get('selected'):
            sharepoint_data['reason'] = sharepoint_plan.get('skip_reason') or sharepoint_data['reason']
        exported_sam_paths = [
            item.get('path') for item in (sharepoint_data.get('exported_files', []) or [])
            if isinstance(item, dict) and item.get('path')
        ]
        if exported_sam_paths:
            supplied_sam_paths = list(sam_report_paths or []) if not isinstance(sam_report_paths, str) else [sam_report_paths]
            sam_report_paths = supplied_sam_paths + exported_sam_paths
        
        # Create service pipelines with shared context
        pipelines = create_pipelines(
            client,
            services_and_licenses,
            tenant_id,
            service_config,
            interactive_auth=interactive_auth,
            interactive_plan=interactive_plan,
            include_user_usage_detail=include_user_usage_detail,
            copilot_dashboard_export=copilot_dashboard_export,
            power_platform_inventory=power_platform_inventory,
            preview_collectors=preview_collectors,
            sharepoint_data=sharepoint_data,
            legacy_power_platform_collector=legacy_power_platform_collector,
        )
        
        # Run service pipelines in parallel. Defender can gather its own data while
        # Purview runs, but waits for the Purview result before calculating the
        # cross-service Copilot data-governance recommendation.
        purview_task = asyncio.create_task(pipelines['purview']())
        (m365_result, entra_info, purview_info, defender_info, power_platform_info, copilot_studio_info) = await asyncio.gather(
            pipelines['m365'](),
            pipelines['entra'](),
            purview_task,
            pipelines['defender'](purview_task),
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
            assessment_profile=assessment_profile,
            provider_evidence=provider_evidence,
            snapshot_json=snapshot_json,
            baseline=baseline,
            enabled_collectors=[name for name, enabled in (
                ("M365", run_m365), ("Entra", run_entra), ("Defender", run_defender),
                ("Purview", run_purview), ("Power Platform", run_power_platform),
                ("Copilot Studio", run_copilot_studio),
            ) if enabled] + ([] if preview_collectors == "none" else [f"Preview: {preview_collectors}"]),
            connection_results=connection_results,
        )
        
    except CredentialUnavailableError as e:
        print("\n" + "="*80)
        print("AUTHENTICATION ERROR")
        print("="*80)
        print(f"\nThe configured service-principal credential is unavailable: {str(e)}")
        print("\nPlease ensure:")
        print("  1. TENANT_ID and CLIENT_ID identify the target tenant application")
        print("  2. CLIENT_SECRET or CERTIFICATE_PATH is present and usable")
        print("  3. The application credential has not expired")
        print("="*80)
        return 2
    except ClientAuthenticationError as e:
        print("\n" + "="*80)
        print("AUTHENTICATION ERROR")
        print("="*80)
        print(f"\nAuthentication failed: {str(e)}")
        print("\nPlease check your credentials and permissions.")
        print("="*80)
        return 2
    except HttpResponseError as e:
        print("\n" + "="*80)
        print("PERMISSION ERROR")
        print("="*80)
        print(f"\nHTTP {e.status_code}: {e.message}")
        if e.status_code == 403:
            print("\nThe selected resource rejected the application token. Run --check-connections to distinguish permission, role, licensing, and provisioning states.")
        print("="*80)
        return 2 if e.status_code in {401, 403} else 1
    except Exception as e:
        print("\n" + "="*80)
        print("ERROR")
        print("="*80)
        print(f"\nAn unexpected error occurred: {str(e)}")
        print(f"Error type: {type(e).__name__}")
        print("="*80)
        return 1

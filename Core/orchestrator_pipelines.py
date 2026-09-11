"""Service pipeline functions for orchestrator."""

import os
from .spinner import get_timestamp, _stdout_lock
from .orchestrator_powershell import collect_purview_data_via_powershell


def create_pipelines(
    client,
    services_and_licenses,
    tenant_id,
    service_config,
    interactive_auth='auto',
    interactive_plan=None,
    include_user_usage_detail=False,
    copilot_dashboard_export=None,
    power_platform_inventory=None,
    preview_collectors='none',
    sharepoint_data=None,
    legacy_power_platform_collector=False,
):
    """Create all service pipeline functions with shared context.
    
    Args:
        client: Microsoft Graph client
        services_and_licenses: ServicesAndLicenses container
        tenant_id: Azure tenant ID
        service_config: Dict with run_* flags from validate_and_prepare_services()
        
    Returns:
        Dict of pipeline functions keyed by service name
    """
    # Extract flags for easier access
    run_m365 = service_config['run_m365']
    run_entra = service_config['run_entra']
    run_defender = service_config['run_defender']
    run_purview = service_config['run_purview']
    run_power_platform = service_config['run_power_platform']
    run_copilot_studio = service_config['run_copilot_studio']
    interactive_plan = interactive_plan or {
        'purview': {'will_attempt': True},
        'power_platform': {'will_attempt': True}
    }
    pp_client_cache = {'loaded': False, 'client': None}

    async def get_shared_power_platform_client():
        if pp_client_cache['loaded']:
            return pp_client_cache['client']

        pp_client = None
        inventory_attempt = None
        if power_platform_inventory:
            from .power_platform_inventory import load_power_platform_inventory
            inventory_attempt = load_power_platform_inventory(power_platform_inventory)
            if getattr(inventory_attempt, 'power_platform_inventory', {}).get('available'):
                pp_client = inventory_attempt

        if pp_client is None and preview_collectors in {'power-platform', 'all'}:
            from .power_platform_inventory import collect_power_platform_inventory_preview
            inventory_attempt = await collect_power_platform_inventory_preview(tenant_id)
            if getattr(inventory_attempt, 'power_platform_inventory', {}).get('available'):
                pp_client = inventory_attempt

        allow_legacy = bool(legacy_power_platform_collector) and interactive_plan['power_platform'].get('will_attempt', False)
        if pp_client is None and allow_legacy:
            from .get_power_platform_client import get_power_platform_client
            pp_client = await get_power_platform_client(tenant_id)

        if pp_client is not None and not hasattr(pp_client, 'power_platform_inventory'):
            if inventory_attempt is not None:
                pp_client.power_platform_inventory = getattr(inventory_attempt, 'power_platform_inventory', {})
            else:
                pp_client.power_platform_inventory = {
                    'available': False,
                    'reason': 'Optional inventory not assessed. Enable Manage > Inventory, download the completed CSV, and pass --power-platform-inventory PATH; or approve --preview-collectors power-platform.',
                    'source': 'Power Platform unified inventory',
                    'period': 'Current inventory',
                    'refresh_date': '',
                    'freshness': 'Unknown',
                    'stale': False,
                }

        pp_client_cache['loaded'] = True
        pp_client_cache['client'] = pp_client
        return pp_client
    
    # Define pipeline functions as closures over shared context
    async def m365_pipeline():
        """M365: Gather client data, then process"""
        if not run_m365:
            return ([], [])
        
        try:
            # Gathering phase (has its own progress bar inside get_m365_client)
            from .get_m365_client import get_m365_client
            m365_client = await get_m365_client(
                client,
                include_user_usage_detail=include_user_usage_detail,
                copilot_dashboard_export=copilot_dashboard_export,
                preview_collectors=preview_collectors,
            )
            m365_client.sharepoint_governance = sharepoint_data or {
                'available': False, 'reason': 'SharePoint governance collection was not requested.'
            }
            
            # Processing phase with progress bar
            import sys
            
            with _stdout_lock:
                sys.stdout.write(f'\r[{get_timestamp()}]   M365 Data Processing    [░░░░░░░░░░░░░░░░░░░░]   0%')
                sys.stdout.flush()
            
            from .get_m365_info import get_m365_info
            license_info, recommendations = await get_m365_info(client, services_and_licenses, m365_client)
            from .sharepoint_governance import build_sharepoint_recommendations
            recommendations.extend(build_sharepoint_recommendations(m365_client.sharepoint_governance))

            with _stdout_lock:
                sys.stdout.write(f'\r[{get_timestamp()}]   ✓ M365 Data Processing    [████████████████████] 100%\n')
                sys.stdout.flush()

            return (
                {
                    'licenses': license_info,
                    '_client': m365_client,
                },
                recommendations,
            )
        except Exception as e:
            import traceback
            print(f"\n[ERROR] M365 pipeline failed: {e}")
            traceback.print_exc()
            return ([], [])
    
    async def entra_pipeline():
        """Entra: Gather client data, then process"""
        if not run_entra:
            return {'available': False, 'recommendations': []}
        
        try:
            # Gathering phase (has its own progress bar inside get_entra_client)
            from .get_entra_client import get_entra_client
            entra_client = await get_entra_client(
                client, tenant_id, preview_collectors=preview_collectors
            )
            
            # Processing phase with progress bar
            import sys
            
            with _stdout_lock:
                sys.stdout.write(f'\r[{get_timestamp()}]   Entra Data Processing   [░░░░░░░░░░░░░░░░░░░░]   0%')
                sys.stdout.flush()
            
            from .get_entra_info import get_entra_info
            result = await get_entra_info(client, services_and_licenses, entra_client)
            if isinstance(result, dict):
                result['_client'] = entra_client

            with _stdout_lock:
                sys.stdout.write(f'\r[{get_timestamp()}]   ✓ Entra Data Processing   [████████████████████] 100%\n')
                sys.stdout.flush()
            
            return result
        except Exception as e:
            return {'available': False, 'recommendations': []}
    
    async def purview_pipeline():
        """Purview: Gather client data, then process"""
        if not run_purview:
            return {'available': False, 'recommendations': []}
        
        try:
            # Check if Purview data is available from stdin
            purview_data_source = os.environ.get('PURVIEW_DATA_SOURCE')
            allow_purview_collection = interactive_plan['purview'].get('will_attempt', True)
            use_deployment_enrichment = purview_data_source in ('stdin', 'cache')
            attempted_collection = False
            
            # If data not available via stdin, invoke PowerShell to collect it
            if purview_data_source != 'stdin' and allow_purview_collection:
                # Gathering phase - invoke PowerShell
                attempted_collection = True
                collection_success = await collect_purview_data_via_powershell(auth_mode=interactive_auth, tenant_id=tenant_id)
                purview_data_source = os.environ.get('PURVIEW_DATA_SOURCE')
                use_deployment_enrichment = collection_success and purview_data_source in ('subprocess', 'cache')
            elif purview_data_source == 'stdin':
                # Data available via stdin - normal path
                with _stdout_lock:
                    import sys
                    sys.stdout.write(f'\r[{get_timestamp()}]   Purview Data Gathering  [░░░░░░░░░░░░░░░░░░░░]   0%')
                    sys.stdout.flush()
                use_deployment_enrichment = True
            else:
                use_deployment_enrichment = False
             
            if use_deployment_enrichment:
                from .get_purview_client import get_purview_client
                purview_client = await get_purview_client(client)
            else:
                purview_client = None
                if not attempted_collection:
                    with _stdout_lock:
                        import sys
                        sys.stdout.write(f'[{get_timestamp()}]   ℹ️  Purview deployment enrichment skipped; Purview configuration will be reported as not assessed\n')
                        sys.stdout.flush()
            
            if purview_data_source == 'stdin':
                with _stdout_lock:
                    import sys
                    sys.stdout.write(f'\r[{get_timestamp()}]   ✓ Purview Data Gathering  [████████████████████] 100%\n')
                    sys.stdout.flush()
            
            if purview_client is None:
                return {'available': False, 'recommendations': []}
            
            # Processing phase
            with _stdout_lock:
                import sys
                sys.stdout.write(f'\r[{get_timestamp()}]   Purview Data Processing [░░░░░░░░░░░░░░░░░░░░]   0%')
                sys.stdout.flush()
            
            from .get_purview_info import get_purview_info
            result = await get_purview_info(client, services_and_licenses, purview_client)
            if isinstance(result, dict):
                result['_client'] = purview_client

            with _stdout_lock:
                import sys
                sys.stdout.write(f'\r[{get_timestamp()}]   ✓ Purview Data Processing [████████████████████] 100%\n')
                sys.stdout.flush()
            
            return result
        except Exception as e:
            return {'available': False, 'recommendations': []}
    
    async def defender_pipeline(purview_result_task=None):
        """Defender: Gather client data, then process"""
        if not run_defender:
            return {'available': False, 'recommendations': []}
        
        try:
            # Gathering phase
            import sys
            
            from .get_defender_client import get_defender_client
            defender_client = await get_defender_client(tenant_id, client)
            
            if defender_client is None:
                return {'available': False, 'recommendations': []}
            
            # Processing phase
            with _stdout_lock:
                sys.stdout.write(f'[{get_timestamp()}]   Defender Data Processing[░░░░░░░░░░░░░░░░░░░░]   0%')
                sys.stdout.flush()
            
            from .get_defender_info import get_defender_info
            purview_client_for_defender = None
            if purview_result_task is not None:
                purview_result = await purview_result_task
                if isinstance(purview_result, dict):
                    purview_client_for_defender = purview_result.get('_client')
            result = await get_defender_info(client, defender_client, services_and_licenses, purview_client_for_defender)
            if isinstance(result, dict):
                result['_client'] = defender_client

            with _stdout_lock:
                sys.stdout.write(f'\r[{get_timestamp()}]   ✓ Defender Data Processing[████████████████████] 100%\n')
                sys.stdout.flush()
            
            return result
        except Exception as e:
            with _stdout_lock:
                print(f"[{get_timestamp()}] ERROR in defender_pipeline: {str(e)}")
                import traceback
                traceback.print_exc()
            return {'available': False, 'recommendations': []}
    
    async def power_platform_pipeline():
        """Power Platform: Gather client data, then process"""
        if not run_power_platform:
            return {'available': False, 'recommendations': []}
        
        try:
            import sys
            allow_pp_collection = (
                bool(legacy_power_platform_collector) and interactive_plan['power_platform'].get('will_attempt', False)
                or bool(power_platform_inventory)
                or preview_collectors in {'power-platform', 'all'}
            )

            if allow_pp_collection:
                # Data already collected in pre-flight (or not available)
                # Just gather and process
                with _stdout_lock:
                    sys.stdout.write(f'\r[{get_timestamp()}]   Power Platform Gathering[░░░░░░░░░░░░░░░░░░░░]   0%')
                    sys.stdout.flush()
                
                pp_client = await get_shared_power_platform_client()
                
                with _stdout_lock:
                    sys.stdout.write(f'\r[{get_timestamp()}]   ✓ Power Platform Gathering[████████████████████] 100%\n')
                    sys.stdout.flush()
            else:
                pp_client = None
                with _stdout_lock:
                    sys.stdout.write(f'[{get_timestamp()}]   ℹ️  Power Platform inventory not supplied and preview API not selected; optional extensibility inventory remains not assessed\n')
                    sys.stdout.flush()
            
            # Processing phase
            with _stdout_lock:
                sys.stdout.write(f'\r[{get_timestamp()}]   Power Platform Processing[░░░░░░░░░░░░░░░░░░░░]   0%')
                sys.stdout.flush()
            
            from .get_power_platform_info import get_power_platform_info
            result = await get_power_platform_info(
                client,
                services_and_licenses,
                pp_client,
                allow_enrichment_fetch=allow_pp_collection,
                tenant_id=tenant_id
            )
            if isinstance(result, dict):
                result['_client'] = pp_client

            with _stdout_lock:
                sys.stdout.write(f'\r[{get_timestamp()}]   ✓ Power Platform Processing[████████████████████] 100%\n')
                sys.stdout.flush()
            
            return result
        except Exception as e:
            with _stdout_lock:
                sys.stdout.write(f'[{get_timestamp()}]   ✗ Power Platform pipeline error: {type(e).__name__}: {e}\n')
                sys.stdout.flush()
            import traceback
            traceback.print_exc()
            return {'available': False, 'recommendations': []}
    
    async def copilot_studio_pipeline():
        """Copilot Studio: Gather client data (reuses PP client), then process"""
        if not run_copilot_studio:
            return {'available': False, 'recommendations': []}
        
        try:
            # Gathering phase (uses same data as Power Platform from pre-flight)
            import sys
            allow_pp_collection = (
                bool(legacy_power_platform_collector) and interactive_plan['power_platform'].get('will_attempt', False)
                or bool(power_platform_inventory)
                or preview_collectors in {'power-platform', 'all'}
            )

            if allow_pp_collection:
                with _stdout_lock:
                    sys.stdout.write(f'\r[{get_timestamp()}]   Copilot Studio Gathering[░░░░░░░░░░░░░░░░░░░░]   0%')
                    sys.stdout.flush()
                
                pp_client = await get_shared_power_platform_client()
                
                with _stdout_lock:
                    sys.stdout.write(f'\r[{get_timestamp()}]   ✓ Copilot Studio Gathering[████████████████████] 100%\n')
                    sys.stdout.flush()
            else:
                pp_client = None
                with _stdout_lock:
                    sys.stdout.write(f'[{get_timestamp()}]   ℹ️  Copilot Studio inventory not supplied and Power Platform preview API not selected; agent inventory remains supplemental and not assessed\n')
                    sys.stdout.flush()
            
            # pp_client can be None (no enrichment data) - that's OK!
            # get_copilot_studio_info will generate basic recommendations from Graph API
            
            # Processing phase
            with _stdout_lock:
                sys.stdout.write(f'\r[{get_timestamp()}]   Copilot Studio Processing[░░░░░░░░░░░░░░░░░░░░]   0%')
                sys.stdout.flush()
            
            from .get_copilot_studio_info import get_copilot_studio_info
            result = await get_copilot_studio_info(client, services_and_licenses, pp_client)
            if isinstance(result, dict):
                result['_client'] = pp_client

            with _stdout_lock:
                sys.stdout.write(f'\r[{get_timestamp()}]   ✓ Copilot Studio Processing[████████████████████] 100%\n')
                sys.stdout.flush()
            
            return result
        except Exception as e:
            with _stdout_lock:
                sys.stdout.write(f'[{get_timestamp()}]   ✗ Copilot Studio pipeline error: {type(e).__name__}: {e}\n')
                sys.stdout.flush()
            import traceback
            traceback.print_exc()
            return {'available': False, 'recommendations': []}
    
    # Return dict of pipelines
    return {
        'm365': m365_pipeline,
        'entra': entra_pipeline,
        'purview': purview_pipeline,
        'defender': defender_pipeline,
        'power_platform': power_platform_pipeline,
        'copilot_studio': copilot_studio_pipeline
    }

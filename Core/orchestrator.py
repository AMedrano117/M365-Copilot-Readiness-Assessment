import asyncio
import sys
from pathlib import Path
from uuid import UUID
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
from . import console_reporting as console
from .offline_collection import CollectionPackagingError

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


async def resolve_assessment_tenant_id(client, tenant_id):
    """Use the organization's GUID when authentication was configured with a domain."""
    try:
        return str(UUID(str(tenant_id)))
    except (ValueError, TypeError, AttributeError):
        pass
    try:
        organizations = await client.organization.get()
        for organization in getattr(organizations, 'value', None) or []:
            return str(UUID(str(getattr(organization, 'id', ''))))
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
            and not parsed.username and not parsed.password
            and parsed.port is None and not parsed.query and not parsed.fragment
        )
    except Exception:
        return False


async def resolve_sharepoint_admin_url(
    client, explicit_url=None, interactive_auth='auto', prompt=input
):
    """Resolve the SharePoint admin URL using documented operator precedence."""
    os = __import__('os')
    from .credentials_check import env_variable_source
    configured = (explicit_url or '').strip()
    source = '--sharepoint-admin-url'
    if not configured:
        configured = os.environ.get('SHAREPOINT_ADMIN_URL', '').strip()
        source = env_variable_source('SHAREPOINT_ADMIN_URL')
    invalid_configured_url = bool(configured and not is_valid_sharepoint_admin_url(configured))
    if invalid_configured_url:
        console.status(f'{source} is not a valid HTTPS SharePoint admin URL. Correct that setting or enter a verified URL when prompted.', tone='warning')
    resolved = configured.rstrip('/') if configured and not invalid_configured_url else ''

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
                    if not os.environ.get('PURVIEW_ORGANIZATION', '').strip():
                        os.environ['PURVIEW_ORGANIZATION'] = domain
                    if not resolved and not invalid_configured_url:
                        resolved = f"https://{domain.split('.')[0]}-admin.sharepoint.com"
                        source = 'derived from the tenant initial domain; verify this URL if the SharePoint tenant was renamed'
    except Exception:
        pass
    if invalid_configured_url:
        return ''
    if resolved:
        console.detail(f'SharePoint admin URL: {resolved} (source: {source})')
        return resolved
    if interactive_auth != 'skip' and sys.stdin.isatty():
        entered = prompt('SharePoint admin URL (for example https://contoso-admin.sharepoint.com): ').strip()
        return entered.rstrip('/') if is_valid_sharepoint_admin_url(entered) else ''
    return ''


async def collect_sharepoint_with_retry(admin_url, tenant_id, interactive_auth='auto', prompt=None):
    """Retry a failed collection once without mislabeling output errors as sign-in failures."""
    payload = await collect_sharepoint_governance_via_powershell(
        admin_url, tenant_id, auth_mode=interactive_auth
    )
    if payload.get('available') or interactive_auth == 'skip' or not sys.stdin.isatty():
        return payload
    retry_url = (prompt or input)(
        'SharePoint collection is unavailable. Enter the verified admin URL to retry once '
        '(the same URL is allowed), or press ENTER to continue: '
    ).strip()
    if retry_url:
        if is_valid_sharepoint_admin_url(retry_url):
            return await collect_sharepoint_governance_via_powershell(
                retry_url.rstrip('/'), tenant_id, auth_mode=interactive_auth
            )
        console.status('SharePoint retry skipped: the entered URL is not a valid HTTPS SharePoint admin URL.', tone='warning')
    return payload

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
    save_collection_path=None,
    reports_dirs=None,
    copilot_readiness_export=None,
    evaluation_date=None,
    lifecycle_report_max_age_days=None,
    lifecycle_report_dates=None,
    portal_review=None,
):
    """Orchestrate gathering of service information and service plans.
    
    Args:
        tenant_id: Azure tenant ID (GUID or domain name)
        services: List of services to analyze. Valid values: "M365", "Entra", "Defender", 
                  "Purview", "Power Platform", "Copilot Studio". Empty list or None = all services.
    """
    try:
        if not check_connections:
            from .pdf_report_import import prepare_pdf_review
            if snapshot_json:
                target = Path(snapshot_json).resolve()
                for value in list(reports_dirs or []) + ([portal_review] if portal_review else []):
                    original = Path(value).resolve()
                    if target == original or (original.is_dir() and target.is_relative_to(original)):
                        raise ValueError('Snapshot output cannot overwrite an original report or reviewed capture. Choose a new output path.')
            # Read local PDFs/validate reviewed manifests before tenant authentication.
            try:
                known_tenant = str(UUID(str(tenant_id)))
            except (ValueError, TypeError, AttributeError):
                known_tenant = None
            portal_review = prepare_pdf_review(reports_dirs, portal_review,
                                              tenant_id=known_tenant, evaluation_date=evaluation_date)
        if not check_connections and snapshot_json and portal_review:
            # Reject collisions before authentication or collection writes. The
            # package preserves copies, but the operator's originals must remain
            # intact when the final assessment snapshot is written.
            target = Path(snapshot_json).resolve()
            protected = {Path(portal_review).resolve()}
            if target not in protected:
                from .portal_review import load_portal_review
                protected.update(Path(path).resolve() for path in load_portal_review(
                    portal_review, embed_assets=False)['asset_paths'])
            if target in protected:
                raise ValueError('Snapshot output cannot overwrite the portal-review manifest or its original evidence files. Choose a new output path.')

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
        tenant_id = await resolve_assessment_tenant_id(client, tenant_id)
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
            sharepoint_data = await collect_sharepoint_with_retry(
                resolved_sharepoint_admin_url, tenant_id, interactive_auth=interactive_auth
            )
        elif sharepoint_plan.get('selected'):
            sharepoint_data['reason'] = sharepoint_plan.get('skip_reason') or sharepoint_data['reason']
            console.status('SharePoint: skipped. ' + sharepoint_data['reason'], tone='warning')
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
        
        from .console_reporting import detail, status, print_collection_handoff
        detail(f"[{get_timestamp()}] Collection tasks finished.")

        enabled_collectors = [name for name, enabled in (
            ("M365", run_m365), ("Entra", run_entra), ("Defender", run_defender),
            ("Purview", run_purview), ("Power Platform", run_power_platform),
            ("Copilot Studio", run_copilot_studio),
        ) if enabled] + ([] if preview_collectors == "none" else [f"Preview: {preview_collectors}"])
        from .offline_collection import save_collection, load_collection, collection_context, refresh_saved_freshness
        from .assessment_package import evaluation_day, record_package_run, render_with_failure_receipt
        from .data_exposure_assessment import _split_env_paths
        from .data_exposure_assessment import _positive_int_env
        # Freeze environment fallback inputs into the same package as CLI inputs.
        sam_report_paths = sam_report_paths or _split_env_paths('SAM_DAG_REPORT_PATHS')
        dspm_report_paths = dspm_report_paths or _split_env_paths('DSPM_REPORT_PATHS')
        evaluation_date = evaluation_day(evaluation_date)
        from .lifecycle_report_settings import lifecycle_settings
        lifecycle_options = lifecycle_settings(max_age_days=lifecycle_report_max_age_days,
                                              report_dates=lifecycle_report_dates, evaluation_date=evaluation_date)
        saved = save_collection(
            save_collection_path, tenant_id=tenant_id, tenant_name=tenant_name,
            service_results={
                'm365_result': m365_result, 'entra_info': entra_info,
                'purview_info': purview_info, 'defender_info': defender_info,
                'power_platform_info': power_platform_info, 'copilot_studio_info': copilot_studio_info,
            }, enabled_collectors=enabled_collectors, connection_results=connection_results,
            evaluation_date=evaluation_date,
            assessment_settings={
                **lifecycle_options,
                'report_format': report_format, 'data_exposure_enabled': run_m365 or run_purview,
                'preview_collectors': preview_collectors,
                'include_user_usage_detail': include_user_usage_detail,
                'provider_evidence_max_age_days': int(__import__('os').environ.get('PROVIDER_EVIDENCE_MAX_AGE_DAYS', '90')),
                'sam_report_max_age_days': _positive_int_env('SAM_REPORT_MAX_AGE_DAYS', 35),
                'dspm_report_max_age_days': _positive_int_env('DSPM_REPORT_MAX_AGE_DAYS', 8),
            },
            supplemental_inputs={
                'sam_report': sam_report_paths, 'dspm_report': dspm_report_paths,
                'reports_dir': reports_dirs, 'copilot_readiness_export': copilot_readiness_export,
                'copilot_dashboard_export': copilot_dashboard_export,
                'power_platform_inventory': power_platform_inventory,
                'assessment_profile': assessment_profile, 'provider_evidence': provider_evidence,
                'baseline': baseline,
                'portal_review': portal_review,
            },
        )
        status('Tenant evidence saved. Building reports...', 'success')
        try:
            payload = load_collection(saved)
            context = collection_context(payload, saved, evaluation_date, mode='live')
        except Exception:
            print_collection_handoff(saved)
            raise
        packaged_inputs = payload.get('resolved_inputs', {})
        def packaged(role, fallback=None):
            values = packaged_inputs.get(role, [])
            return values[0] if values else fallback
        # Both modes evaluate the same serialized data and the same immutable inputs.
        results = payload['service_results']
        refresh_saved_freshness(results, evaluation_date)
        
        # Process and print all information and recommendations
        receipt = dict(
            folder=payload['package_directory'], mode='live', tenant_id=tenant_id,
            collected_at=payload['collected_at'], evaluation_date=evaluation_date,
            diagnostics=payload['package'].get('diagnostics', []),
            collection_input=saved,
        )
        output = render_with_failure_receipt(
            process_and_print_all_information, receipt=receipt,
            **results,
            tenant_name=tenant_name,
            open_html_report=open_html_report,
            report_format=report_format,
            sam_report_paths=packaged_inputs.get('sam_report', []),
            dspm_report_paths=packaged_inputs.get('dspm_report', []),
            data_exposure_enabled=(run_m365 or run_purview),
            assessment_profile=packaged('assessment_profile'),
            provider_evidence=packaged('provider_evidence'),
            portal_review=packaged('portal_review'),
            snapshot_json=snapshot_json,
            baseline=packaged('baseline'),
            enabled_collectors=[name for name, enabled in (
                ("M365", run_m365), ("Entra", run_entra), ("Defender", run_defender),
                ("Purview", run_purview), ("Power Platform", run_power_platform),
                ("Copilot Studio", run_copilot_studio),
            ) if enabled] + ([] if preview_collectors == "none" else [f"Preview: {preview_collectors}"]),
            connection_results=connection_results,
            reports_dirs=packaged_inputs.get('reports_dir', []),
            copilot_readiness_export=packaged('copilot_readiness_export'),
            include_user_usage_detail=include_user_usage_detail,
            offline_copilot_dashboard_export=packaged('copilot_dashboard_export'),
            offline_power_platform_inventory=packaged('power_platform_inventory'),
            expected_tenant_id=tenant_id,
            collection_context=context,
        )
        if isinstance(output, dict) and snapshot_json:
            output['snapshot_path'] = snapshot_json
        record_package_run(
            **receipt,
            outputs=output, inputs=payload['package'].get('files', []),
        )
        
    except CredentialUnavailableError as e:
        console.section('AUTHENTICATION ERROR', 'error')
        console.status(f"The configured service-principal credential is unavailable: {str(e)}", 'error')
        print("\nPlease ensure:")
        print("  1. TENANT_ID and CLIENT_ID identify the target tenant application")
        print("  2. CLIENT_SECRET or CERTIFICATE_PATH is present and usable")
        print("  3. The application credential has not expired")
        return 2
    except ClientAuthenticationError as e:
        console.section('AUTHENTICATION ERROR', 'error')
        console.status(f"Authentication failed: {str(e)}", 'error')
        print("\nPlease check your credentials and permissions.")
        return 2
    except HttpResponseError as e:
        console.section('PERMISSION ERROR', 'error')
        console.status(f"HTTP {e.status_code}: {e.message}", 'error')
        if e.status_code == 403:
            print("\nThe selected resource rejected the application token. Run --check-connections to distinguish permission, role, licensing, and provisioning states.")
        return 2 if e.status_code in {401, 403} else 1
    except CollectionPackagingError as e:
        console.section('PACKAGING ERROR', 'error')
        console.status(str(e), 'error')
        console.status('Correct the input files and rebuild offline using the saved collection below.', 'warning')
        console.print_collection_handoff(e.collection_path)
        return 1
    except ValueError as e:
        console.section('ASSESSMENT INPUT ERROR', 'error')
        console.status(str(e), 'error')
        return 1
    except Exception as e:
        console.section('ERROR', 'error')
        console.status(f"An unexpected error occurred: {str(e)}", 'error')
        console.detail(f"Error type: {type(e).__name__}")
        return 1

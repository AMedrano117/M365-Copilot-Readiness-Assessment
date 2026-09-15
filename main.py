import sys
import asyncio
import os

# Setup console encoding for Windows
from Core.console_setup import setup_console_encoding
setup_console_encoding()

# Import common utilities
from Core.spinner import get_timestamp

if __name__ == "__main__":
    from params import TENANT_ID, SERVICES
    from Core.cli_parser import parse_arguments
    from Core.credentials_check import load_env_file, validate_credentials_or_exit
    from Core.console_reporting import configure_console, detail, section, status
    
    # Parse command-line arguments
    args = parse_arguments(TENANT_ID, SERVICES)
    configure_console(verbose=args.verbose, color=args.color)
    section('M365 COPILOT READINESS')
    status('Mode: offline — saved evidence and local exports.' if args.offline else
           'Mode: live — tenant evidence collection.')
    detail(f'Started {get_timestamp()}')
    # Show the startup receipt before dependency imports and any live collectors.
    # Console helpers flush explicitly, including when stdout is redirected.
    from Core.check_dependencies import check_dependencies
    check_dependencies(offline=args.offline)

    if args.offline:
        from Core.offline_report import run_offline_report
        try:
            sys.exit(run_offline_report(args))
        except (ValueError, OSError) as exc:
            status(f"Offline report error: {exc}", 'error')
            sys.exit(1)
    
    # Use parsed values (command-line overrides or defaults from params.py)
    load_env_file(args.env_file)

    tenant_id = args.tenant_id or os.environ.get('TENANT_ID') or TENANT_ID
    services = args.services if args.services else []  # Empty list means all services
    interactive_auth = args.interactive_auth
    open_html_report = args.open_html_report
    report_format = args.report_format
    sam_report_paths = args.sam_report
    dspm_report_paths = args.dspm_report
    include_user_usage_detail = args.include_user_usage_detail
    copilot_dashboard_export = args.copilot_dashboard_export
    power_platform_inventory = args.power_platform_inventory
    preview_collectors = args.preview_collectors
    sharepoint_admin_url = args.sharepoint_admin_url
    legacy_power_platform_collector = args.legacy_power_platform_collector
    check_connections = args.check_connections
    assessment_profile = args.assessment_profile
    provider_evidence = args.provider_evidence
    snapshot_json = args.snapshot_json
    baseline = args.baseline

    # Backward-compatible mapping from older, more specific flags.
    if args.interactive_collection_policy == 'skip':
        interactive_auth = 'skip'
    elif args.purview_auth_mode == 'force':
        interactive_auth = 'fresh'
    
    # Check for required credentials before starting orchestration
    validate_credentials_or_exit(get_timestamp, env_file=args.env_file)

    from Core.orchestrator import orchestrate
    
    try:
        exit_code = asyncio.run(
            orchestrate(
                tenant_id,
                services,
                interactive_auth=interactive_auth,
                open_html_report=open_html_report,
                report_format=report_format,
                sam_report_paths=sam_report_paths,
                dspm_report_paths=dspm_report_paths,
                include_user_usage_detail=include_user_usage_detail,
                copilot_dashboard_export=copilot_dashboard_export,
                power_platform_inventory=power_platform_inventory,
                preview_collectors=preview_collectors,
                sharepoint_admin_url=sharepoint_admin_url,
                legacy_power_platform_collector=legacy_power_platform_collector,
                check_connections=check_connections,
                assessment_profile=assessment_profile,
                provider_evidence=provider_evidence,
                snapshot_json=snapshot_json,
                baseline=baseline,
                save_collection_path=args.save_collection,
                reports_dirs=args.reports_dir,
                copilot_readiness_export=args.copilot_readiness_export,
                evaluation_date=args.evaluation_date,
                lifecycle_report_max_age_days=args.lifecycle_report_max_age_days,
                lifecycle_report_dates=args.lifecycle_report_date,
                portal_review=args.portal_review,
            )
        )
        if isinstance(exit_code, int) and exit_code:
            sys.exit(exit_code)
    except ValueError as e:
        # Catch credential-related errors gracefully
        error_msg = str(e)
        if "environment variables" in error_msg.lower() or "credentials" in error_msg.lower():
            status(f"Authentication error: {error_msg}", 'error')
            sys.exit(1)
        raise
    except Exception as e:
        status(f"Unexpected error: {e}", 'error')
        sys.exit(1)

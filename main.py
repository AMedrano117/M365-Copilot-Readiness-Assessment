import sys
import asyncio
import os

# Setup console encoding for Windows
from Core.console_setup import setup_console_encoding
setup_console_encoding()

# Import common utilities
from Core.spinner import get_timestamp

def main():
    from params import TENANT_ID, SERVICES
    from Core.cli_parser import parse_arguments
    from Core.credentials_check import load_env_file, validate_credentials_or_exit, print_configuration_summary
    from Core.console_reporting import configure_console, section, status

    # Parse command-line arguments
    args = parse_arguments(TENANT_ID, SERVICES)
    configure_console(verbose=args.verbose, color=args.color)
    from Core.run_plan import build_run_plan, print_run_plan, plan_json
    from Core.repository_provenance import set_execution_revision
    if args.plan:
        plan = build_run_plan(args)
        print(plan_json(plan)) if args.plan_format == 'json' else print_run_plan(plan)
        sys.exit(0 if plan['execution_eligible'] else 2)
    section('AI READINESS AND M365 HARDENING')
    status('Mode: offline — saved evidence and local exports.' if args.offline else
           'Mode: live — tenant evidence collection.')
    from Core.run_progress import RunProgress
    with RunProgress(offline=args.offline, check_connections=args.check_connections):
        # Show the startup receipt before dependency imports and any live collectors.
        # Console helpers flush explicitly, including when stdout is redirected.
        from Core.check_dependencies import check_dependencies
        check_dependencies(offline=args.offline)

        # Local settings are resolved after the startup receipt, before authentication.
        if not args.offline:
            try:
                load_env_file(args.env_file)
                args.tenant_id = args.tenant_id or os.environ.get('TENANT_ID') or TENANT_ID
                from Core.cli_parser import resolve_live_permission_profile
                args.permission_profile = resolve_live_permission_profile(
                    args.permission_profile, args.preview_collectors, args.legacy_power_platform_collector)
            except ValueError as exc:
                status(f'Configuration error: {exc}', 'error')
                sys.exit(1)
        from Core.effective_configuration import resolve_effective
        args, _, resolution_errors = resolve_effective(args)
        if getattr(args, 'reassess', None):
            from Core.reassessment_cli import resolve_reassessment
            try:
                args = resolve_reassessment(args, args.tenant_id)
                args, _, resolution_errors = resolve_effective(args)
            except (ValueError, OSError) as exc:
                status(f'Reassessment error: {exc}', 'error')
                sys.exit(2)
        plan = build_run_plan(args)
        print_run_plan(plan)
        if not plan['execution_eligible']:
            sys.exit(2)
        set_execution_revision(plan['producing_revision'])

        if args.offline:
            from Core.offline_report import run_offline_report
            try:
                sys.exit(run_offline_report(args))
            except (ValueError, OSError) as exc:
                status(f"Offline report error: {exc}", 'error')
                sys.exit(1)

        # Use parsed values (command-line overrides or defaults from params.py)
        from Core.cli_parser import resolve_live_permission_profile
        try:
            selected_env_path = load_env_file(args.env_file)
            permission_profile = resolve_live_permission_profile(
                args.permission_profile, args.preview_collectors, args.legacy_power_platform_collector
            )
        except ValueError as exc:
            status(f'Configuration error: {exc}', 'error')
            sys.exit(1)

        # No built-in fallback: a consultant working across customers must never
        # collect from a tenant that was not explicitly selected for this run.
        tenant_id = (args.tenant_id or os.environ.get('TENANT_ID') or TENANT_ID or '').strip()
        if not tenant_id:
            status('Configuration error: no tenant selected. Pass --tenant-id or set TENANT_ID in the selected --env-file.', 'error')
            sys.exit(1)
        if permission_profile == 'restricted' and args.delegated == 'required':
            status('Configuration error: the restricted permission profile does not use delegated sign-in.', 'error')
            sys.exit(1)
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
        # Apply the documented tenant override to every workload's shared credentials.
        if tenant_id:
            os.environ['TENANT_ID'] = tenant_id
        validate_credentials_or_exit(get_timestamp, env_file=args.env_file, load_environment=False)
        print_configuration_summary(tenant_id=tenant_id, permission_profile=permission_profile,
                                    env_path=selected_env_path, sharepoint_admin_url=sharepoint_admin_url)

        from Core.orchestrator import orchestrate

        try:
            exit_code = asyncio.run(
                orchestrate(
                    tenant_id,
                    services,
                    interactive_auth=interactive_auth,
                    open_html_report=open_html_report,
                    report_format=report_format,
                    extra_exports=args.extra_exports,
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
                    customer_name=args.customer_name,
                    reports_dirs=args.reports_dir,
                    copilot_readiness_export=args.copilot_readiness_export,
                    ai_baseline_exports=args.ai_baseline_export,
                    evaluation_date=args.evaluation_date,
                    lifecycle_report_max_age_days=args.lifecycle_report_max_age_days,
                    lifecycle_report_dates=args.lifecycle_report_date,
                    effective_assessment_settings=args._effective_settings,
                    portal_review=args.portal_review,
                    permission_profile=permission_profile,
                    confirm_tenant=args.confirm_tenant,
                    delegated=args.delegated,
                    interactive_tenant_confirmation=sys.stdin.isatty() and sys.stdout.isatty(),
                    show_progress=not getattr(args, 'no_progress', False),
                    run_type=args.run_type, assessment_id=args.assessment_id,
                    baseline_run_id=args.baseline_run_id, assessment_history=args.assessment_history,
                    assessment_purpose=args.assessment_purpose, primary_environment_id=args.primary_environment_id,
                    delta_mode=args.delta_mode,
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


if __name__ == '__main__':
    from Core.repository_provenance import producing_context, unrecorded_revision
    with producing_context(unrecorded_revision()):
        try:
            main()
        except KeyboardInterrupt:
            sys.exit(130)

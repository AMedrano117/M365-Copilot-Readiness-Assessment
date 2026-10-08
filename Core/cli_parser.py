"""
Command-line argument parser for the Assessment Tool.
"""

import argparse
import sys


def parse_arguments(tenant_id_default, services_default):
    """
    Parse command-line arguments for the assessment tool.
    
    Args:
        tenant_id_default: Default tenant ID from params.py
        services_default: Default services list from params.py
        
        Returns:
        argparse.Namespace: Parsed arguments with tenant_id, services, and auth options
    """
    parser = argparse.ArgumentParser(
        description='AI Readiness and Microsoft 365 Hardening Assessment',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python main.py
  python main.py --mode live
  python main.py --mode live --save-collection output/tenant-collection.json
  python main.py --mode offline --reports-dir tests/fixtures/microsoft_reports
  python main.py --mode offline --collection-input output/tenant-collection.json --reports-dir exports
  python main.py --mode offline --prior-report Reports/prior.xlsx --purview-cache .cache/purview/saved.json --reports-dir exports
  python main.py --services M365
  python main.py --services M365 Entra Defender
  python main.py --tenant-id "your-tenant-id" --services Purview
  python main.py --env-file .env.prod --services Purview
  python main.py --sam-report sam-permissions.csv --dspm-report dspm-assessment.csv
  python main.py --copilot-dashboard-export copilot-metrics.csv
  python main.py --power-platform-inventory power-platform-inventory.csv
  python main.py --preview-collectors shadow-ai
  python main.py --check-connections
  python main.py --legacy-power-platform-collector
  python main.py --assessment-profile assessment-profile.json --provider-evidence providers.csv
  python main.py --baseline Reports/prior.xlsx --snapshot-json output/latest-snapshot.json
        '''
    )
    parser.add_argument(
        '--permission-profile', choices=['standard', 'restricted'], default=None,
        help='Live access profile. Overrides PERMISSION_PROFILE in the selected environment; defaults to standard. Offline replay preserves the recorded profile.'
    )
    parser.add_argument(
        '--tenant-id', 
        type=str, 
        default=None,
        help='Tenant ID to analyze. Order: --tenant-id, then TENANT_ID in the selected environment file. There is no built-in default tenant.'
    )
    parser.add_argument(
        '--confirm-tenant',
        type=str,
        default=None,
        metavar='DOMAIN',
        help='Expected initial *.onmicrosoft.com domain. Collection stops if the signed-in tenant differs. Overrides EXPECTED_TENANT_DOMAIN and PURVIEW_ORGANIZATION.'
    )
    parser.add_argument(
        '--delegated',
        choices=['auto', 'off', 'required'],
        default='auto',
        help='Delegated sign-in for data that Microsoft only exposes to signed-in administrators. auto=use a cached sign-in or prompt when a terminal is available; off=application permissions only; required=fail preflight without a delegated sign-in.'
    )
    parser.add_argument(
        '--no-progress',
        action='store_true',
        help='Do not draw live progress bars during collection (they are drawn only on an interactive terminal).'
    )
    parser.add_argument(
        '--services', 
        nargs='*', 
        default=services_default,
        help=f'Services to analyze: M365, Entra, Defender, Purview, "Power Platform", "Copilot Studio" (default: {services_default or "all services"})'
    )
    parser.add_argument(
        '--interactive-auth',
        choices=['auto', 'fresh', 'skip'],
        default='auto',
        help='PowerShell authentication: auto=use certificate or browser fallback, fresh=force new delegated sign-in when no certificate is configured, skip=do not use browser authentication'
    )
    parser.add_argument(
        '--env-file',
        type=str,
        default=None,
        help='Existing, readable environment file for credentials and default tenant ID. An explicitly selected missing file is an error.'
    )
    parser.add_argument(
        '--open-html-report',
        action='store_true',
        help='Open the generated HTML recommendations report in your default browser after the run finishes'
    )
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Show detailed collection progress, source dates, import receipts, and package paths.')
    parser.add_argument('--color', choices=['auto', 'always', 'never'], default='auto',
                        help='Console colors: auto for supported terminals (respects NO_COLOR), always, or never. Redirected output is plain in auto mode.')
    parser.add_argument(
        '--report-format',
        choices=['excel', 'csv', 'both'],
        default='excel',
        help='Recommendation export format: excel=Excel primary with CSV fallback if needed, csv=CSV only, both=generate both Excel and CSV'
    )
    parser.add_argument('--extra-exports', nargs='+', default=[],
                        choices=['evidence-pages'],
                        help='Optional exports for this run. By default, write the HTML report, readiness summary and Excel workbooks only. Select evidence-pages for linked HTML evidence pages.')
    parser.add_argument(
        '--sam-report',
        action='append',
        default=[],
        metavar='PATH',
        help='SharePoint Advanced Management Data Access Governance export. Repeat for multiple reports or supply a directory.'
    )
    execution_mode = parser.add_mutually_exclusive_group()
    execution_mode.add_argument('--mode', choices=['live', 'offline'],
                                help='live=collect tenant evidence; offline=build from local files only. Defaults to live, or offline when saved collection/report/cache inputs are supplied.')
    execution_mode.add_argument('--offline', dest='mode', action='store_const', const='offline',
                                help='Compatibility alias for --mode offline.')
    parser.add_argument('--save-collection', metavar='PATH',
                        help='Optional destination override for the live collection JSON, with a companion portable package. By default, evidence and reports share Reports/<customer>/<assessment>/ without duplicate exports.')
    parser.add_argument('--collection-input', metavar='PATH',
                        help='Saved tenant collection, portable collection.json, or offline rebuild.json recipe to replay, restoring packaged reports and settings. Implies offline mode.')
    parser.add_argument('--evaluation-date', metavar='YYYY-MM-DD',
                        help='Date used to evaluate evidence freshness. Defaults to today for new assessments and the recorded date for packaged replay.')
    parser.add_argument('--lifecycle-report-max-age-days', type=int, metavar='DAYS',
                        help='Accepted lifecycle report age, independent of permissions reports. Defaults to the saved setting, LIFECYCLE_REPORT_MAX_AGE_DAYS, or 90 days.')
    parser.add_argument('--lifecycle-report-date', action='append', default=[], metavar='PATH=YYYY-MM-DD',
                        help='Confirm the generation date of an undated lifecycle export. Repeat per file; the confirmation is saved against the file hash.')
    parser.add_argument('--prior-report', metavar='PATH',
                        help='Prior assessment XLSX or snapshot JSON to include as dated historical context alongside new exports. Implies offline mode.')
    parser.add_argument('--purview-cache', metavar='PATH',
                        help='Existing Purview collector cache JSON to read offline. Supplies Purview configuration only; implies offline mode.')
    parser.add_argument('--tenant-name', help='Display name for portal-only offline reports.')
    parser.add_argument('--customer-name', metavar='NAME',
                        help='Customer folder label under Reports/. Defaults to the tenant display name or tenant ID; saved packages retain the label for replay.')
    parser.add_argument('--reports-dir', action='append', default=[], metavar='PATH',
                        help='Directory of structured portal exports and PDFs. PDFs in subfolders are also extracted locally into JSON and report previews. Repeat for multiple directories.')
    parser.add_argument('--portal-review', metavar='PATH',
                        help='Optional reviewed JSON manifest or PDF folder. Normally PDFs are imported automatically with --reports-dir; captures do not score controls.')
    parser.add_argument('--copilot-readiness-export', metavar='PATH',
                        help='Microsoft 365 admin center Copilot Readiness CSV; app readiness is separate from actual Copilot usage.')
    parser.add_argument(
        '--dspm-report',
        action='append',
        default=[],
        metavar='PATH',
        help='Microsoft Purview DSPM data-risk assessment export. Repeat for multiple reports or supply a directory.'
    )
    parser.add_argument(
        '--include-user-usage-detail',
        action='store_true', default=True,
        help='Compatibility alias: user-level usage and prerequisite evidence is included by default in Excel. HTML remains aggregate-only.'
    )
    parser.add_argument(
        '--copilot-dashboard-export',
        type=str,
        default=None,
        metavar='PATH',
        help='Optional CSV export from the Microsoft Copilot Dashboard for returning-user and usage-intensity evidence.'
    )
    parser.add_argument(
        '--power-platform-inventory',
        type=str,
        default=None,
        metavar='PATH',
        help='Optional tenant-wide CSV export from Power Platform admin center Manage > Inventory.'
    )
    parser.add_argument(
        '--preview-collectors',
        choices=['auto', 'none', 'entra-recommendations', 'power-platform', 'shadow-ai', 'network-access', 'copilot-audit', 'all'],
        default='auto',
        help='Standard auto collects Entra recommendations, Shadow AI and Copilot audit. Select none to skip supplemental Microsoft preview APIs. copilot-audit reads aggregate Copilot interaction events from the unified audit log. Beta evidence cannot independently pass a foundation control.'
    )
    parser.add_argument(
        '--sharepoint-admin-url',
        type=str,
        default=None,
        metavar='URL',
        help='Actual SharePoint admin-center HTTPS origin; overrides SHAREPOINT_ADMIN_URL. No tenant-domain inference.'
    )
    parser.add_argument(
        '--legacy-power-platform-collector',
        action='store_true',
        help='Use the legacy delegated Power Platform collector. This compatibility path requires Az.Accounts.'
    )
    parser.add_argument(
        '--check-connections',
        action='store_true',
        help='Validate selected collector connections without running an assessment or creating reports.'
    )
    parser.add_argument(
        '--assessment-profile',
        type=str,
        default=None,
        metavar='PATH',
        help='Versioned JSON describing proposed AI products, users, use cases, data, actions, approvals, and outcome measures.'
    )
    parser.add_argument(
        '--provider-evidence',
        type=str,
        default=None,
        metavar='PATH',
        help='CSV or XLSX register containing product-and-tier-specific external AI review evidence.'
    )
    parser.add_argument(
        '--snapshot-json',
        type=str,
        default=None,
        metavar='PATH',
        help='Also write the existing shared assessment result as a single JSON file to PATH.'
    )
    parser.add_argument(
        '--baseline',
        type=str,
        default=None,
        metavar='PATH',
        help='Prior assessment workbook or snapshot JSON used to classify improvement since the baseline.'
    )
    parser.add_argument(
        '--purview-auth-mode',
        choices=['auto', 'force', 'prompt'],
        default='auto',
        help=argparse.SUPPRESS
    )
    parser.add_argument(
        '--interactive-collection-policy',
        choices=['auto', 'skip'],
        default='auto',
        help=argparse.SUPPRESS
    )
    
    intent = parser.add_mutually_exclusive_group()
    intent.add_argument('--run-type', choices=['Initial','Reassessment','Standalone'],
        help='Explicit new semantic execution. Reassessment requires explicit --baseline-run-id; rendering preserves an existing run.')
    intent.add_argument('--replay-snapshot', metavar='PATH',
        help='Render a shared-result snapshot offline without evaluating evidence or changing run identity/history.')
    parser.add_argument('--assessment-id', help='Existing AST- identity for explicit continuation; requires its history.')
    parser.add_argument('--baseline-run-id', help='Explicit completed RUN- identity in the selected history. No automatic selection.')
    parser.add_argument('--assessment-history', metavar='PATH', help='Assessment history manifest. Initial defaults to its new package.')
    parser.add_argument('--assessment-purpose', help='Recorded assessment family/purpose; defaults to this tool or selected history.')
    parser.add_argument('--primary-environment-id', help='Optional ENV- identity to verify against the selected tenant GUID.')
    parser.add_argument('--render-output-dir', metavar='PATH', help='Parent for snapshot render artifacts; valid only with --replay-snapshot.')
    parser.add_argument('--delta-mode',choices=['enabled','disabled'],
        help='Reassessment delta calculation defaults to enabled. Explicit disabled records an omitted calculation; replay never recalculates.')
    if sum(value=='--run-type' or value.startswith('--run-type=') for value in sys.argv[1:])>1:
        parser.error('Select one explicit --run-type per execution.')
    args = parser.parse_args()
    if args.delta_mode and args.run_type!='Reassessment':
        parser.error('--delta-mode requires an explicit Reassessment run.')
    if args.run_type=='Reassessment' and not (args.assessment_id and args.baseline_run_id and args.assessment_history):
        parser.error('Reassessment requires --assessment-id, --baseline-run-id and --assessment-history.')
    if args.run_type!='Reassessment' and args.baseline_run_id:
        parser.error('--baseline-run-id requires explicit Reassessment intent.')
    if args.run_type=='Initial' and args.assessment_id:
        parser.error('Initial creates an AssessmentId; use Reassessment or Standalone for explicit continuation.')
    if args.run_type=='Standalone' and args.assessment_id and not args.assessment_history:
        parser.error('Standalone continuation requires --assessment-history for ownership validation.')
    if not args.run_type and any((args.assessment_id,args.baseline_run_id,args.assessment_history,
                                  args.assessment_purpose,args.primary_environment_id)):
        parser.error('Run ownership and history options require --run-type.')
    if args.run_type and args.baseline:
        parser.error('--baseline is legacy change tracking; explicit run workflows use --baseline-run-id.')
    if args.render_output_dir and not args.replay_snapshot:
        parser.error('--render-output-dir requires --replay-snapshot.')
    if args.replay_snapshot:
        from .assessment_package import INPUT_KEYS
        if args.mode=='live' or args.evaluation_date or any(getattr(args,key,None) for key in INPUT_KEYS | {'collection_input'}):
            parser.error('Snapshot replay cannot collect, add evidence or change the evaluated date.')
        args.mode = 'offline'
    if args.customer_name is not None:
        args.customer_name = args.customer_name.strip()
        if not args.customer_name:
            parser.error('--customer-name must not be blank.')
    if args.lifecycle_report_max_age_days is not None and args.lifecycle_report_max_age_days <= 0:
        parser.error('--lifecycle-report-max-age-days must be positive.')
    args._provided_options = {value.split('=', 1)[0] for value in sys.argv[1:] if value.startswith('--')}
    if args.evaluation_date:
        from .assessment_package import evaluation_day
        try:
            args.evaluation_date = evaluation_day(args.evaluation_date)
        except ValueError as exc:
            parser.error(str(exc))
    saved_evidence = args.collection_input or args.prior_report or args.purview_cache
    if args.mode == 'live' and saved_evidence:
        parser.error('--mode live cannot be combined with --collection-input, --prior-report, or --purview-cache; use --mode offline for saved evidence.')
    if args.collection_input and args.purview_cache:
        parser.error('Use either --collection-input or --purview-cache so a partial cache cannot overwrite a saved tenant collection.')
    args.mode = args.mode or ('offline' if saved_evidence else 'live')
    args.offline = args.mode == 'offline'
    if args.offline and (args.check_connections or args.save_collection):
        parser.error('Offline mode cannot be combined with --check-connections or --save-collection.')
    if args.offline and (args.preview_collectors not in {'none', 'auto'} or args.legacy_power_platform_collector):
        parser.error('Live collectors cannot be enabled in offline mode.')
    if args.offline and args.env_file:
        parser.error('Offline mode does not use credentials or an --env-file.')
    if args.offline and args.permission_profile:
        parser.error('Offline mode uses the saved collection permission profile; omit --permission-profile.')
    if args.offline and (args.confirm_tenant or args.delegated == 'required'):
        parser.error('Offline mode does not sign in; omit --confirm-tenant and --delegated required.')
    if args.permission_profile == 'restricted' and (args.preview_collectors not in {'none', 'auto'} or args.legacy_power_platform_collector):
        parser.error('The restricted permission profile cannot enable preview or legacy collectors.')
    if args.permission_profile == 'restricted' and args.delegated == 'required':
        parser.error('The restricted permission profile does not use delegated sign-in; omit --delegated required.')
    return args


def resolve_live_permission_profile(explicit=None, preview_collectors='auto', legacy=False):
    """Resolve profile only after the chosen environment file has been loaded."""
    import os
    from .collector_registry import normalize_permission_profile
    profile = normalize_permission_profile(explicit or os.environ.get('PERMISSION_PROFILE') or 'standard')
    if profile == 'restricted' and (preview_collectors not in {'none', 'auto'} or legacy):
        raise ValueError('The restricted permission profile cannot enable preview or legacy collectors.')
    return profile

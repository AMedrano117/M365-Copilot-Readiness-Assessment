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
        description='Microsoft 365 Copilot and Agents Readiness Assessment Tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python main.py
  python main.py --mode live
  python main.py --mode live --save-collection output/tenant-collection.json
  python main.py --mode offline --reports-dir output/portal-sample-inputs
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
        '--tenant-id', 
        type=str, 
        default=None,
        help=f'Tenant ID to analyze. Fallback order: --tenant-id, selected env file TENANT_ID, params.py ({tenant_id_default})'
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
        help='Path to the environment file to use for credentials and default tenant ID'
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
                        help='Optional destination override for the live collection JSON. Live assessments automatically save a unique tenant/date file in output/collections by default.')
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
        action='store_true',
        help='Include user-level Copilot activity and imported readiness flags in the restricted Excel evidence workbook. HTML remains aggregate-only.'
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
        choices=['none', 'power-platform', 'shadow-ai', 'network-access', 'all'],
        default='none',
        help='Opt in to supplemental Microsoft preview APIs. Preview data never changes the core readiness decision.'
    )
    parser.add_argument(
        '--sharepoint-admin-url',
        type=str,
        default=None,
        metavar='URL',
        help='Optional SharePoint admin URL override. Normally derived from the tenant initial domain.'
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
        help='Optionally write a machine-readable assessment snapshot. No snapshot is created by default.'
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
    
    args = parser.parse_args()
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
    if args.offline and (args.preview_collectors != 'none' or args.legacy_power_platform_collector):
        parser.error('Live collectors cannot be enabled in offline mode.')
    if args.offline and args.env_file:
        parser.error('Offline mode does not use credentials or an --env-file.')
    return args

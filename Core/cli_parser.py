"""
Command-line argument parser for the Assessment Tool.
"""

import argparse


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
        help='Include licensed-user Copilot activity in the restricted Excel evidence workbook. HTML remains aggregate-only.'
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
    
    return parser.parse_args()

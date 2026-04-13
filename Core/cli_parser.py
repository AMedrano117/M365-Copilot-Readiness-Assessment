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
        help='Interactive enrichment behavior: auto=reuse existing sign-ins when possible, fresh=force a new sign-in, skip=use license/basic recommendations only'
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

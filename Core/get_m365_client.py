"""
Microsoft 365 Usage & Deployment Client
Provides authenticated access to Microsoft Graph usage reports and deployment data.
Fetches and caches: usage reports, site metadata, user assignments, activity metrics.
Used for enhanced M365 Copilot adoption observations.
"""
from . import console_reporting as console
import asyncio
import csv
import io
from azure.core.exceptions import HttpResponseError
from .spinner import get_timestamp, _stdout_lock
from datetime import datetime, timedelta
from .collector_registry import source_allowed


def _parse_csv_report(report_data):
    """Parse a Microsoft Graph report response body into dictionaries."""
    if not report_data:
        return []
    if isinstance(report_data, list):
        return report_data
    try:
        csv_text = report_data.decode('utf-8-sig') if isinstance(report_data, bytes) else str(report_data)
        return list(csv.DictReader(io.StringIO(csv_text)))
    except Exception:
        return []

async def get_m365_client(
    graph_client,
    include_user_usage_detail=False,
    copilot_dashboard_export=None,
    preview_collectors="none",
    permission_profile="standard",
):
    """
    Get M365 usage analytics and deployment data from Microsoft Graph.
    Fetches comprehensive usage reports and metadata for Copilot readiness assessment.
    
    Args:
        graph_client: Existing Microsoft Graph SDK client
    
    Returns:
        Object with cached M365 data from Graph API,
        or minimal client if permissions are insufficient (graceful degradation)
    """
    
    def field(item, name, default=None):
        if isinstance(item, dict):
            return item.get(name, default)
        snake = []
        for character in name:
            if character.isupper() and snake:
                snake.append('_')
            snake.append(character.lower())
        return getattr(item, ''.join(snake), getattr(item, name, default))
    
    # Create a simple object to store fetched data
    class M365Client:
        def __init__(self):
            self.available = False
            self.collection_status = {}
            
            # Raw data storage
            self.sites = []
            self.users = []
            self.external_connections = []
            
            # Pre-computed summaries for fast access
            self.sites_summary = {}
            self.users_summary = {
                'copilot_eligible_users': None,
                'copilot_licensed': None,
                'known_copilot_licensed_users': None,
                'copilot_license_coverage': None,
                'copilot_adoption_rate': None,
                'coverage_population': '',
                'availability_status': 'unavailable',
                'reason': 'Copilot license coverage was not collected',
                'subscription_catalog_available': False,
            }
            self.license_coverage = {
                'available': False,
                'availability_status': 'unavailable',
                'reason': 'Copilot license coverage was not collected',
            }
            self.email_summary = {}
            self.teams_summary = {}
            self.sharepoint_summary = {}
            self.onedrive_summary = {}
            self.activations_summary = {}
            self.active_users_summary = {}
            self.copilot_usage = {
                'available': False,
                'reason': 'Collection did not run',
                'source': 'Microsoft Graph Microsoft 365 Copilot usage reports',
            }
            self.m365_app_readiness = {
                'available': False,
                'reason': 'Collection did not run',
                'source': 'Microsoft Graph Microsoft 365 Apps usage reports',
            }
            self.copilot_dashboard = {
                'available': False,
                'reason': 'Optional export not supplied. Export Copilot Dashboard data from Viva Insights and pass --copilot-dashboard-export PATH.',
                'source': 'Microsoft Copilot Dashboard export',
            }
            self.shadow_ai_usage = {
                'available': False,
                'reason': 'Preview Shadow AI collector was not enabled. Use --preview-collectors shadow-ai after configuring discovery and optional permission.',
                'source': 'Microsoft Defender for Cloud Apps discovery (preview)',
            }
            
            # Track missing features/permissions
            self.missing_permissions = []
    
    client = M365Client()
    client.permission_profile = permission_profile
    if not source_allowed('sites', permission_profile):
        client.collection_status['sites'] = {
            'available': False, 'availability_status': 'not_requested',
            'records_collected': 0, 'pages_collected': 0, 'truncated': False,
            'reason': 'SharePoint site inventory was not requested by the Restricted permission profile.',
        }
    
    try:
        # Phase 1: Fetch core data in parallel
        # These are the most critical for M365 Copilot observations
        
        # Build tasks for parallel execution
        # Errors are handled by asyncio.gather(return_exceptions=True)
        report_period = 'D30'
        
        # Create tasks with labels for progress tracking
        tasks = {
            'users': graph_client.get_collection(
                "/v1.0/users",
                params={'$select': 'id,displayName,userPrincipalName,assignedLicenses,accountEnabled', '$top': '999'},
            ),
            'external_connections': graph_client.get_collection(
                "/v1.0/external/connections", params={'$top': '999'}
            ),
            'email_activity': graph_client.get_csv(
                "/v1.0/reports/getEmailActivityUserDetail(period='D30')"
            ),
            'teams_activity': graph_client.get_csv(
                "/v1.0/reports/getTeamsUserActivityUserDetail(period='D30')"
            ),
            'sharepoint_usage': graph_client.get_csv(
                "/v1.0/reports/getSharePointSiteUsageDetail(period='D30')"
            ),
            'onedrive_usage': graph_client.get_csv(
                "/v1.0/reports/getOneDriveUsageAccountDetail(period='D30')"
            ),
            'office_activations': graph_client.get_csv(
                "/v1.0/reports/getOffice365ActivationsUserDetail"
            ),
            'active_users': graph_client.get_csv(
                "/v1.0/reports/getOffice365ActiveUserDetail(period='D30')"
            ),
        }
        if source_allowed('sites', permission_profile):
            tasks['sites'] = graph_client.get_collection(
                "/v1.0/sites", params={'$select': 'id,displayName,webUrl', '$top': '999'}
            )
        from .ai_usage import collect_ai_usage
        tasks['ai_usage'] = collect_ai_usage(
            include_user_detail=include_user_usage_detail,
            copilot_dashboard_export=copilot_dashboard_export,
            preview_collectors=preview_collectors,
        )
        
        # Execute all API calls in parallel; elapsed time is not a completion percentage.
        import sys
        
        with _stdout_lock:
            console.detail(f'[{get_timestamp()}]   M365 data collection started.\n')
            sys.stdout.flush()
        
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        
        # Map results to named dictionary
        response_dict = dict(zip(tasks.keys(), results))
        for source_name, response in response_dict.items():
            if source_name == 'ai_usage':
                continue
            if isinstance(response, Exception) or response is None:
                client.collection_status[source_name] = {
                    'availability_status': 'unavailable', 'records_collected': 0,
                    'pages_collected': 0, 'truncated': False,
                    'reason': str(response) if isinstance(response, Exception) else 'No response returned',
                }
                continue
            if isinstance(response, dict) and 'value' in response:
                client.collection_status[source_name] = {
                    key: response.get(key) for key in (
                        'availability_status', 'records_collected', 'pages_collected', 'truncated', 'reason'
                    )
                }
                if not client.collection_status[source_name].get('availability_status'):
                    client.collection_status[source_name]['availability_status'] = (
                        'partial' if response.get('truncated') or response.get('@odata.nextLink')
                        else 'unavailable' if response.get('available') is False else 'available'
                    )
                continue
            next_link = getattr(response, 'odata_next_link', None)
            if not next_link:
                additional = getattr(response, 'additional_data', {}) or {}
                next_link = additional.get('@odata.nextLink') if isinstance(additional, dict) else None
            value = getattr(response, 'value', None)
            client.collection_status[source_name] = {
                'availability_status': 'partial' if next_link else 'available',
                'records_collected': len(value) if isinstance(value, list) else '',
                'pages_collected': 1,
                'truncated': bool(next_link),
                'reason': 'Additional Graph pages were returned but not consumed by this SDK collector.' if next_link else '',
            }
        
        # Process Sites data
        sites_response = response_dict.get('sites')
        sites_status = client.collection_status.get('sites', {})
        if (not isinstance(sites_response, Exception) and sites_response
                and sites_status.get('availability_status') == 'available'):
            try:
                client.sites = sites_response.get('value', []) if isinstance(sites_response, dict) else (sites_response.value if hasattr(sites_response, 'value') else [])
                client.available = True
                
                # Pre-compute sites summary
                total_sites = len(client.sites)
                client.sites_summary = {
                    'available': True,
                    'total': total_sites,
                    'site_names': [field(site, 'displayName', '') for site in client.sites if field(site, 'displayName', '')],
                    'root_site_id': field(client.sites[0], 'id') if total_sites > 0 else None
                }
            except Exception as e:
                client.sites_summary = {'available': False, 'total': None, 'error': f'Failed to process sites: {str(e)}'}
        else:
            client.sites_summary = {
                'available': False, 'total': None,
                'availability_status': sites_status.get('availability_status', 'unavailable'),
                'reason': sites_status.get('reason') or 'SharePoint site inventory was not collected completely.',
            }
            if sites_status.get('availability_status') not in {'not_requested', 'partial'}:
                client.missing_permissions.append('Sites.Read.All')
        
        # Process Users data
        users_response = response_dict.get('users')
        if not isinstance(users_response, Exception) and users_response:
            try:
                client.users = users_response.get('value', []) if isinstance(users_response, dict) else (users_response.value if hasattr(users_response, 'value') else [])
                client.available = True
                
                # Analyze user license assignments
                total_users = len(client.users)
                enabled_users = sum(1 for u in client.users if field(u, 'accountEnabled', True))
                
                # License coverage is resolved once by the dedicated collector,
                # using subscribed products and paid Copilot service plans.
                # A user inventory alone cannot establish Copilot entitlement.
                client.users_summary.update({
                    'total': total_users,
                    'enabled': enabled_users,
                    'disabled': total_users - enabled_users,
                    'sampled': bool((client.collection_status.get('users') or {}).get('truncated'))
                })
            except Exception as e:
                client.users_summary.update({'total': 0, 'error': f'Failed to process users: {str(e)}'})
        else:
            client.users_summary.update({'total': 0, 'error': 'User.Read.All permission missing or API error'})
            client.missing_permissions.append('User.Read.All')

        connections_response = response_dict.get('external_connections')
        if isinstance(connections_response, dict) and connections_response.get('available'):
            client.external_connections = connections_response.get('value', []) or []
        
        # Process Email Activity Report
        # Parse CSV and extract key metrics for Exchange/Outlook observations
        def record_csv_rows(source_name, rows):
            rows = rows or []
            status = client.collection_status.setdefault(source_name, {})
            status.update({
                'availability_status': 'available',
                'records_collected': len(rows),
                'pages_collected': 1,
                'truncated': False,
                'reason': '',
            })

        email_response = response_dict.get('email_activity')
        if not isinstance(email_response, Exception) and email_response:
            parsed_rows = _parse_csv_report(email_response)
            record_csv_rows('email_activity', parsed_rows)
            
            if parsed_rows:
                client.available = True
                # Aggregate metrics in single pass over CSV rows
                send_count = 0
                receive_count = 0
                read_count = 0
                
                for row in parsed_rows:
                    send_count += int(row.get('Send Count', 0) or 0)
                    receive_count += int(row.get('Receive Count', 0) or 0)
                    read_count += int(row.get('Read Count', 0) or 0)
                
                total_users_with_activity = len(parsed_rows)
                
                client.email_summary = {
                    'available': True,
                    'report_period': report_period,
                    'active_users': total_users_with_activity,
                    'total_sent': send_count,
                    'total_received': receive_count,
                    'total_read': read_count,
                    'avg_sent_per_user': round(send_count / total_users_with_activity, 1) if total_users_with_activity > 0 else 0,
                    'avg_received_per_user': round(receive_count / total_users_with_activity, 1) if total_users_with_activity > 0 else 0
                }
            else:
                client.email_summary = {'available': False, 'error': 'No data in report'}
        else:
            client.email_summary = {'available': False}
            if 'Reports.Read.All' not in client.missing_permissions:
                client.missing_permissions.append('Reports.Read.All')
        
        # Process Teams Activity Report
        # Parse CSV and extract Teams usage metrics
        teams_response = response_dict.get('teams_activity')
        if not isinstance(teams_response, Exception) and teams_response:
            parsed_rows = _parse_csv_report(teams_response)
            record_csv_rows('teams_activity', parsed_rows)
            
            if parsed_rows:
                client.available = True
                # Aggregate Teams metrics in single pass
                chat_messages = 0
                private_messages = 0
                calls = 0
                meetings = 0
                
                for row in parsed_rows:
                    chat_messages += int(row.get('Team Chat Message Count', 0) or 0)
                    private_messages += int(row.get('Private Chat Message Count', 0) or 0)
                    calls += int(row.get('Call Count', 0) or 0)
                    meetings += int(row.get('Meeting Count', 0) or 0)
                
                total_users_with_activity = len(parsed_rows)
                
                client.teams_summary = {
                    'available': True,
                    'report_period': report_period,
                    'active_users': total_users_with_activity,
                    'total_team_chat_messages': chat_messages,
                    'total_private_messages': private_messages,
                    'total_calls': calls,
                    'total_meetings': meetings,
                    'avg_meetings_per_user': round(meetings / total_users_with_activity, 1) if total_users_with_activity > 0 else 0,
                    'avg_messages_per_user': round((chat_messages + private_messages) / total_users_with_activity, 1) if total_users_with_activity > 0 else 0
                }
            else:
                client.teams_summary = {'available': False, 'error': 'No data in report'}
        else:
            client.teams_summary = {'available': False}
        
        # Process SharePoint Usage Report
        # Parse CSV and extract SharePoint site metrics
        sharepoint_response = response_dict.get('sharepoint_usage')
        if not isinstance(sharepoint_response, Exception) and sharepoint_response:
            parsed_rows = _parse_csv_report(sharepoint_response)
            record_csv_rows('sharepoint_usage', parsed_rows)
            
            if parsed_rows:
                client.available = True
                # Aggregate SharePoint metrics in single pass
                total_files = 0
                total_page_views = 0
                active_sites = 0
                
                for row in parsed_rows:
                    total_files += int(row.get('File Count', 0) or 0)
                    page_views = int(row.get('Page View Count', 0) or 0)
                    total_page_views += page_views
                    if page_views > 0:
                        active_sites += 1
                
                total_sites_in_report = len(parsed_rows)
                
                client.sharepoint_summary = {
                    'available': True,
                    'report_period': report_period,
                    'sites_in_report': total_sites_in_report,
                    'active_sites': active_sites,
                    'total_files': total_files,
                    'total_page_views': total_page_views,
                    'avg_files_per_site': round(total_files / total_sites_in_report, 1) if total_sites_in_report > 0 else 0,
                    'site_activity_rate': round((active_sites / total_sites_in_report * 100), 1) if total_sites_in_report > 0 else 0
                }
            else:
                client.sharepoint_summary = {'available': False, 'error': 'No data in report'}
        else:
            client.sharepoint_summary = {'available': False}
        
        # Process OneDrive Usage Report
        # Parse CSV and extract OneDrive adoption metrics
        onedrive_response = response_dict.get('onedrive_usage')
        if not isinstance(onedrive_response, Exception) and onedrive_response:
            parsed_rows = _parse_csv_report(onedrive_response)
            record_csv_rows('onedrive_usage', parsed_rows)
            
            if parsed_rows:
                client.available = True
                # Aggregate OneDrive metrics in single pass
                active_accounts = 0
                total_files = 0
                storage_used_bytes = 0
                
                for row in parsed_rows:
                    if row.get('Is Active', 'False') == 'True' or int(row.get('File Count', 0) or 0) > 0:
                        active_accounts += 1
                    total_files += int(row.get('File Count', 0) or 0)
                    storage_used_bytes += int(row.get('Storage Used (Byte)', 0) or 0)
                
                total_accounts = len(parsed_rows)
                storage_used_gb = round(storage_used_bytes / (1024**3), 2)
                
                client.onedrive_summary = {
                    'available': True,
                    'report_period': report_period,
                    'total_accounts': total_accounts,
                    'active_accounts': active_accounts,
                    'adoption_rate': round((active_accounts / total_accounts * 100), 1) if total_accounts > 0 else 0,
                    'total_files': total_files,
                    'storage_used_gb': storage_used_gb,
                    'avg_files_per_user': round(total_files / active_accounts, 1) if active_accounts > 0 else 0
                }
            else:
                client.onedrive_summary = {'available': False, 'error': 'No data in report'}
        else:
            client.onedrive_summary = {'available': False}
        
        # Process Office Activations Report
        # Parse CSV and extract Office app activation metrics
        activations_response = response_dict.get('office_activations')
        if not isinstance(activations_response, Exception) and activations_response:
            parsed_rows = _parse_csv_report(activations_response)
            record_csv_rows('office_activations', parsed_rows)
            
            if parsed_rows:
                client.available = True
                # Count activation types in single pass
                windows_activations = 0
                mac_activations = 0
                mobile_activations = 0
                
                for row in parsed_rows:
                    if int(row.get('Windows', 0) or 0) > 0:
                        windows_activations += 1
                    if int(row.get('Mac', 0) or 0) > 0:
                        mac_activations += 1
                    if int(row.get('Android', 0) or 0) > 0 or int(row.get('iOS', 0) or 0) > 0:
                        mobile_activations += 1
                
                total_users_with_activations = len(parsed_rows)
                
                client.activations_summary = {
                    'available': True,
                    'total_users_with_activations': total_users_with_activations,
                    'windows_users': windows_activations,
                    'mac_users': mac_activations,
                    'mobile_users': mobile_activations,
                    'desktop_adoption_rate': round(((windows_activations + mac_activations) / total_users_with_activations * 100), 1) if total_users_with_activations > 0 else 0
                }
            else:
                client.activations_summary = {'available': False, 'error': 'No data in report'}
        else:
            client.activations_summary = {'available': False}
        
        # Process Active Users Report
        # Parse CSV and extract cross-service activity metrics
        active_users_response = response_dict.get('active_users')
        if not isinstance(active_users_response, Exception) and active_users_response:
            parsed_rows = _parse_csv_report(active_users_response)
            record_csv_rows('active_users', parsed_rows)
            
            if parsed_rows:
                client.available = True
                # Extract latest row (most recent date) for current snapshot
                if parsed_rows:
                    latest_row = parsed_rows[-1]  # CSV is sorted by date, last row is most recent
                    
                    client.active_users_summary = {
                        'available': True,
                        'report_period': report_period,
                        'office_365_active': int(latest_row.get('Office 365', 0) or 0),
                        'exchange_active': int(latest_row.get('Exchange', 0) or 0),
                        'onedrive_active': int(latest_row.get('OneDrive', 0) or 0),
                        'sharepoint_active': int(latest_row.get('SharePoint', 0) or 0),
                        'teams_active': int(latest_row.get('Microsoft Teams', 0) or 0),
                        'yammer_active': int(latest_row.get('Yammer', 0) or 0)
                    }
                else:
                    client.active_users_summary = {'available': False, 'error': 'No rows in report'}
            else:
                client.active_users_summary = {'available': False, 'error': 'No data in report'}
        else:
            client.active_users_summary = {'available': False}
        
        ai_usage_result = response_dict.get('ai_usage')
        if not isinstance(ai_usage_result, Exception) and isinstance(ai_usage_result, dict):
            client.copilot_usage = ai_usage_result.get('copilot_usage', client.copilot_usage)
            client.m365_app_readiness = ai_usage_result.get('m365_app_readiness', client.m365_app_readiness)
            client.copilot_dashboard = ai_usage_result.get('copilot_dashboard', client.copilot_dashboard)
            client.shadow_ai_usage = ai_usage_result.get('shadow_ai_usage', client.shadow_ai_usage)
            direct_coverage = ai_usage_result.get('license_coverage', {}) or {}
            if direct_coverage:
                client.license_coverage = direct_coverage
                client.users_summary.update({
                    key: direct_coverage[key] for key in (
                        'availability_status', 'reason', 'known_copilot_licensed_users',
                        'license_detection_basis', 'subscription_catalog_available',
                        'unresolved_assigned_sku_count', 'users_without_recognized_base_license',
                        'records_collected', 'pages_collected', 'truncated',
                    ) if key in direct_coverage
                })
            if direct_coverage.get('available'):
                client.available = True
                # A partial license read must not replace a complete user
                # inventory with a smaller population used by other insights.
                if not direct_coverage.get('truncated'):
                    client.users_summary.update({
                        'total': direct_coverage.get('total_users', client.users_summary.get('total', 0)),
                        'enabled': direct_coverage.get('enabled_users', client.users_summary.get('enabled', 0)),
                        'disabled': max(
                            direct_coverage.get('total_users', 0) - direct_coverage.get('enabled_users', 0), 0
                        ),
                    })
                client.users_summary.update({
                    'copilot_eligible_users': direct_coverage.get('eligible_users'),
                    'copilot_licensed': direct_coverage.get('copilot_licensed_users'),
                    'copilot_license_coverage': direct_coverage.get('copilot_license_coverage'),
                    'copilot_adoption_rate': direct_coverage.get('copilot_license_coverage'),
                    'coverage_population': direct_coverage.get('coverage_population', ''),
                    'sampled': bool(direct_coverage.get('truncated')),
                })
        elif isinstance(ai_usage_result, Exception):
            reason = 'Collection failed: {}'.format(type(ai_usage_result).__name__)
            client.copilot_usage['reason'] = reason
            client.m365_app_readiness['reason'] = reason
            client.license_coverage['reason'] = reason
            client.users_summary['reason'] = reason

        # Log summary
        if client.available:
            successful_apis = sum([
                1 if client.sites_summary.get('available') else 0,
                1 if client.users_summary.get('total', 0) > 0 else 0,
                1 if client.email_summary.get('available', False) else 0,
                1 if client.teams_summary.get('available', False) else 0,
                1 if client.sharepoint_summary.get('available', False) else 0,
                1 if client.onedrive_summary.get('available', False) else 0,
                1 if client.activations_summary.get('available', False) else 0,
                1 if client.active_users_summary.get('available', False) else 0
            ])
            
            # Success message removed for cleaner output
            if client.missing_permissions:
                with _stdout_lock:
                    console.status(f"Missing permissions: {', '.join(client.missing_permissions)}", tone='warning')
        else:
            with _stdout_lock:
                console.status(f"M365 client: No data available (check permissions)", tone='warning')
        
        with _stdout_lock:
            console.detail(f'[{get_timestamp()}]   M365 data collection finished; see source coverage for completeness.')
        return client
        
    except Exception as e:
        # Catastrophic error - return minimal client
        with _stdout_lock:
            console.status(f"M365 client initialization failed: {str(e)}", tone='error')
        
        client.available = False
        return client


def extract_m365_insights_from_client(m365_client):
    """
    Extract M365 usage insights from cached client data.
    Call this ONCE and reuse the result across all recommendations to avoid redundant processing.
    
    Args:
        m365_client: M365Client object with cached API data
    
    Returns:
        dict with pre-computed metrics for M365 Copilot adoption observations
    """
    if not m365_client or not m365_client.available:
        site_state = (getattr(m365_client, 'collection_status', {}) or {}).get('sites', {})
        return {
            'available': False,

            # Cross-workload active user rollup
            'total_active_users': 0,
            'active_user_data_available': False,

            # Sites & SharePoint
            'total_sites': None,
            'sharepoint_total_sites': None,
            'site_inventory_available': False,
            'site_inventory_status': site_state.get('availability_status', 'unavailable'),
            'site_inventory_reason': site_state.get('reason') or 'SharePoint site inventory was not collected.',
            'sharepoint_active_sites': 0,
            'sharepoint_total_files': 0,
            'sharepoint_page_views': 0,
            'sharepoint_activity_rate': 0,
            
            # Users & Licensing
            'total_users': 0,
            'enabled_users': 0,
            'copilot_eligible_users': None,
            'copilot_licensed_users': None,
            'known_copilot_licensed_users': None,
            'copilot_license_coverage': None,
            'copilot_license_coverage_status': 'unavailable',
            'copilot_license_coverage_reason': 'M365 collection unavailable',
            'copilot_adoption_rate': None,
            'license_coverage': {'available': False, 'availability_status': 'unavailable', 'reason': 'M365 collection unavailable'},
            'copilot_usage': {'available': False, 'reason': 'M365 collection unavailable'},
            'm365_app_readiness': {'available': False, 'reason': 'M365 collection unavailable'},
            'copilot_dashboard': {'available': False, 'reason': 'M365 collection unavailable'},
            'shadow_ai_usage': {'available': False, 'reason': 'M365 collection unavailable'},
            
            # Email Activity
            'email_active_users': 0,
            'email_avg_sent_per_user': 0,
            'email_avg_received_per_user': 0,
            
            # Teams Activity
            'teams_active_users': 0,
            'teams_total_meetings': 0,
            'teams_total_messages': 0,
            'teams_avg_meetings_per_user': 0,
            'teams_avg_messages_per_user': 0,
            
            # OneDrive Usage
            'onedrive_total_accounts': 0,
            'onedrive_active_accounts': 0,
            'onedrive_adoption_rate': 0,
            'onedrive_storage_gb': 0,
            
            # Office Activations
            'activations_total_users': 0,
            'activations_desktop_rate': 0,
            
            # Active Users (Latest Snapshot)
            'office365_active_users': 0,
            'office_active_users': 0,
            'exchange_active_users': 0,
            'teams_active_users_snapshot': 0,
            'sharepoint_active_users': 0,
            'onedrive_active_users': 0
        }
    
    # Extract from pre-computed summaries
    sites_summary = getattr(m365_client, 'sites_summary', {})
    users_summary = getattr(m365_client, 'users_summary', {})
    email_summary = getattr(m365_client, 'email_summary', {})
    teams_summary = getattr(m365_client, 'teams_summary', {})
    sharepoint_summary = getattr(m365_client, 'sharepoint_summary', {})
    onedrive_summary = getattr(m365_client, 'onedrive_summary', {})
    activations_summary = getattr(m365_client, 'activations_summary', {})
    active_users_summary = getattr(m365_client, 'active_users_summary', {})

    # Distinct active users across M365 workloads.
    # The Office 365 active-user report is the aggregate source when available; when it is
    # missing we fall back to the largest per-workload active count, which is a valid lower
    # bound (a user active in email is active in M365). Recommendations use this both for
    # observation text and for organization-size branching, so it must never silently be 0
    # when any workload report did return data.
    _workload_active_counts = [
        active_users_summary.get('office_365_active', 0) or 0,
        email_summary.get('active_users', 0) or 0,
        teams_summary.get('active_users', 0) or 0,
        active_users_summary.get('sharepoint_active', 0) or 0,
        onedrive_summary.get('active_accounts', 0) or 0,
    ]
    total_active_users = max(_workload_active_counts)

    # True when at least one usage report backed the active-user figure above. Consumers use
    # this to tell "nobody is active" apart from "no report was returned".
    active_user_data_available = bool(
        active_users_summary.get('available', False)
        or email_summary.get('available', False)
        or teams_summary.get('available', False)
        or onedrive_summary.get('available', False)
    )

    insights = {
        'available': True,

        # Cross-workload active user rollup (see derivation above)
        'total_active_users': total_active_users,
        'active_user_data_available': active_user_data_available,

        # Sites & SharePoint
        'total_sites': sites_summary.get('total'),
        'site_inventory_available': sites_summary.get('available', 'total' in sites_summary and sites_summary.get('total') is not None and not sites_summary.get('error')),
        'site_inventory_status': sites_summary.get('availability_status', (getattr(m365_client, 'collection_status', {}) or {}).get('sites', {}).get('availability_status', 'unrecorded')),
        'site_inventory_reason': sites_summary.get('reason', sites_summary.get('error', '')),
        'site_names': sites_summary.get('site_names', []),
        'sharepoint_report_available': sharepoint_summary.get('available', False),
        'sharepoint_report_period': sharepoint_summary.get('report_period', 'D30'),
        'sharepoint_active_sites': sharepoint_summary.get('active_sites', 0),
        'sharepoint_total_files': sharepoint_summary.get('total_files', 0),
        'sharepoint_total_page_views': sharepoint_summary.get('total_page_views', 0),
        'sharepoint_activity_rate': sharepoint_summary.get('site_activity_rate', 0),
        'sharepoint_avg_files_per_site': sharepoint_summary.get('avg_files_per_site', 0),
        # Aliases used by recommendation modules
        'sharepoint_total_sites': sites_summary.get('total'),
        'sharepoint_page_views': sharepoint_summary.get('total_page_views', 0),
        
        # Users & Licensing
        'total_users': users_summary.get('total', 0),
        'enabled_users': users_summary.get('enabled', 0),
        'copilot_eligible_users': users_summary.get('copilot_eligible_users'),
        'disabled_users': users_summary.get('disabled', 0),
        'copilot_licensed_users': users_summary.get('copilot_licensed'),
        'known_copilot_licensed_users': users_summary.get('known_copilot_licensed_users'),
        'copilot_license_coverage': users_summary.get('copilot_license_coverage'),
        'copilot_license_coverage_population': users_summary.get('coverage_population', ''),
        'copilot_license_coverage_status': users_summary.get('availability_status', 'unavailable'),
        'copilot_license_coverage_reason': users_summary.get('reason', 'Copilot license coverage was not collected'),
        'license_coverage': getattr(m365_client, 'license_coverage', {}),
        # Compatibility alias for older recommendation modules. This is license coverage,
        # never proof of active Copilot use.
        'copilot_adoption_rate': users_summary.get('copilot_adoption_rate'),
        'user_data_sampled': users_summary.get('sampled', False),

        # Dedicated AI usage evidence. Availability and reasons are retained so consumers
        # never convert an unread report into a zero-usage claim.
        'copilot_usage': getattr(m365_client, 'copilot_usage', {}),
        'm365_app_readiness': getattr(m365_client, 'm365_app_readiness', {}),
        'copilot_dashboard': getattr(m365_client, 'copilot_dashboard', {}),
        'shadow_ai_usage': getattr(m365_client, 'shadow_ai_usage', {}),
        
        # Email Activity (Outlook/Exchange) - Parsed Metrics
        'email_report_available': email_summary.get('available', False),
        'email_report_period': email_summary.get('report_period', 'D30'),
        'email_active_users': email_summary.get('active_users', 0),
        'email_total_sent': email_summary.get('total_sent', 0),
        'email_total_received': email_summary.get('total_received', 0),
        'email_total_read': email_summary.get('total_read', 0),
        'email_avg_sent_per_user': email_summary.get('avg_sent_per_user', 0),
        'email_avg_received_per_user': email_summary.get('avg_received_per_user', 0),
        
        # Teams Activity - Parsed Metrics
        'teams_report_available': teams_summary.get('available', False),
        'teams_report_period': teams_summary.get('report_period', 'D30'),
        'teams_active_users': teams_summary.get('active_users', 0),
        'teams_total_meetings': teams_summary.get('total_meetings', 0),
        'teams_total_calls': teams_summary.get('total_calls', 0),
        'teams_total_team_chat_messages': teams_summary.get('total_team_chat_messages', 0),
        'teams_total_private_messages': teams_summary.get('total_private_messages', 0),
        # Alias used by recommendation modules: all Teams messages, channel plus private chat
        'teams_total_messages': (
            (teams_summary.get('total_team_chat_messages', 0) or 0)
            + (teams_summary.get('total_private_messages', 0) or 0)
        ),
        'teams_avg_meetings_per_user': teams_summary.get('avg_meetings_per_user', 0),
        'teams_avg_messages_per_user': teams_summary.get('avg_messages_per_user', 0),
        
        # OneDrive Usage - Parsed Metrics
        'onedrive_report_available': onedrive_summary.get('available', False),
        'onedrive_report_period': onedrive_summary.get('report_period', 'D30'),
        'onedrive_total_accounts': onedrive_summary.get('total_accounts', 0),
        'onedrive_active_accounts': onedrive_summary.get('active_accounts', 0),
        'onedrive_adoption_rate': onedrive_summary.get('adoption_rate', 0),
        'onedrive_total_files': onedrive_summary.get('total_files', 0),
        'onedrive_storage_gb': onedrive_summary.get('storage_used_gb', 0),
        'onedrive_avg_files_per_user': onedrive_summary.get('avg_files_per_user', 0),
        
        # Office Activations - Parsed Metrics
        'activations_report_available': activations_summary.get('available', False),
        'activations_total_users': activations_summary.get('total_users_with_activations', 0),
        'activations_windows_users': activations_summary.get('windows_users', 0),
        'activations_mac_users': activations_summary.get('mac_users', 0),
        'activations_mobile_users': activations_summary.get('mobile_users', 0),
        'activations_desktop_rate': activations_summary.get('desktop_adoption_rate', 0),
        
        # Active Users (Latest Snapshot) - Parsed Metrics
        'active_users_report_available': active_users_summary.get('available', False),
        'active_users_report_period': active_users_summary.get('report_period', 'D30'),
        'office365_active_users': active_users_summary.get('office_365_active', 0),
        # Alias used by recommendation modules
        'office_active_users': active_users_summary.get('office_365_active', 0),
        'exchange_active_users': active_users_summary.get('exchange_active', 0),
        'teams_active_users_snapshot': active_users_summary.get('teams_active', 0),
        'sharepoint_active_users': active_users_summary.get('sharepoint_active', 0),
        'onedrive_active_users': active_users_summary.get('onedrive_active', 0),
        'yammer_active_users': active_users_summary.get('yammer_active', 0),
        
        # Missing Permissions (for recommendations to flag setup issues)
        'missing_permissions': getattr(m365_client, 'missing_permissions', [])
    }
    
    return insights

"""
Microsoft Purview/Compliance API Client
Fetches actual deployment data from Purview endpoints

NOTE: Uses PowerShell data collector (collect_purview_data.ps1) to collect data.
The script runs Connect-IPPSSession, collects cmdlet outputs, and pipes JSON to Python via stdin.
No cache files are created - data is passed directly in memory.
"""
import json
import os
import sys
from .spinner import get_timestamp, _stdout_lock


# Global variable to store PowerShell data passed via stdin
_PURVIEW_DATA_CACHE = None


def set_purview_data_payload(payload):
    """Pass collected Purview data in memory so large tenants do not hit Windows env limits."""
    global _PURVIEW_DATA_CACHE
    _PURVIEW_DATA_CACHE = payload if isinstance(payload, dict) else None


def _truthy(value):
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in {'true', 'yes', 'enabled', 'enable', 'active'}


def _purview_enabled(item):
    """Treat enforce and simulation modes as active policy configurations."""
    if not isinstance(item, dict):
        return False
    if item.get('Enabled') is not None:
        return _truthy(item.get('Enabled'))
    return str(item.get('Mode', '') or '').strip().lower() in {
        'enable', 'enforce', 'testwithnotifications', 'testwithoutnotifications'
    }


def load_purview_data_from_stdin():
    """Load Purview data from stdin (passed by PowerShell wrapper) or subprocess"""
    global _PURVIEW_DATA_CACHE
    
    # Check data source
    data_source = os.environ.get('PURVIEW_DATA_SOURCE')
    if data_source not in ('stdin', 'subprocess', 'cache'):
        return None
    
    # Return cached data if already loaded
    if _PURVIEW_DATA_CACHE is not None:
        return _PURVIEW_DATA_CACHE
    
    # Load from subprocess or cache JSON environment variable
    if data_source in ('subprocess', 'cache'):
        try:
            json_data = os.environ.get('PURVIEW_DATA_JSON')
            if json_data:
                _PURVIEW_DATA_CACHE = json.loads(json_data)
                return _PURVIEW_DATA_CACHE
        except Exception as e:
            print(f"[{get_timestamp()}] ⚠️ Failed to load Purview data from subprocess: {e}")
            import traceback
            traceback.print_exc()
        return None
    
    # Load from stdin
    try:
        if not sys.stdin.isatty():
            stdin_data = sys.stdin.read()
            if stdin_data.strip():
                _PURVIEW_DATA_CACHE = json.loads(stdin_data)
                # Data loaded silently - progress managed by orchestrator
                return _PURVIEW_DATA_CACHE
    except Exception as e:
        print(f"[{get_timestamp()}] ⚠️ Failed to load Purview data from stdin: {e}")
        import traceback
        traceback.print_exc()
    
    return None


def extract_collection(data, key, nested_key):
    """Normalize PowerShell JSON so collections stay collections.

    PowerShell serializes a single selected object as an object instead of a
    one-item array. Empty but successful queries should still be treated as
    available data if the key exists and was not marked permission_denied.
    """
    if not isinstance(data, dict):
        return [], False

    container = data.get(key, {})
    if not isinstance(container, dict):
        return [], False

    permission_denied = bool(container.get('permission_denied'))
    value = container.get(nested_key, [])

    if isinstance(value, list):
        items = value
    elif isinstance(value, dict):
        items = [value]
    elif value in (None, ""):
        items = []
    else:
        items = [value]

    if 'available' in container:
        available = bool(container.get('available'))
    else:
        available = not permission_denied and (nested_key in container)
    return items, available


def extract_object(data, key):
    """Read a singleton while accepting both the legacy and status-aware payload shapes."""
    if not isinstance(data, dict):
        return {}, False
    container = data.get(key, {})
    if not isinstance(container, dict):
        return {}, False
    if 'data' in container:
        value = container.get('data') or {}
        return value if isinstance(value, dict) else {}, bool(container.get('available'))
    # Legacy collectors wrote the object directly without collection metadata.
    return container, bool(container)


def collection_state(data, key, optional=False):
    """Normalize source status without converting unavailable data into an empty success."""
    container = data.get(key, {}) if isinstance(data, dict) else {}
    if not isinstance(container, dict):
        container = {}
    if 'available' in container:
        available = bool(container.get('available'))
    else:
        available = bool(container) and not bool(container.get('permission_denied'))
    return {
        'availability_status': container.get('availability_status', 'available' if available else 'unavailable'),
        'available': available,
        'records_collected': int(container.get('count', 1 if available else 0) or 0),
        'reason': container.get('reason', ''),
        'error_category': container.get('error_category', 'permission_denied' if container.get('permission_denied') else ''),
        'required_role': container.get('required_role', ''),
        'optional': bool(container.get('optional', optional)),
        'technical_error': container.get('technical_error', ''),
    }


async def get_purview_client(tenant_id, payload=None):
    """
    Create Purview client and fetch deployment data from PowerShell stdin.
    
    Data flow:
    - The default main.py orchestration runs collect_purview_data.ps1 -DataOnly and supplies JSON.
    - Data passed via stdin - no cache files created
    - All data available in single JSON structure
    
    Args:
        tenant_id: Azure tenant ID (GUID or domain name)
        payload: Explicit collected dictionary. When supplied, hydration is fully
            local and does not inspect stdin, environment variables, or the global
            collection cache.
    
    Returns:
        Object with deployment data hydrated from PowerShell cmdlet outputs
    """
    # Note: Graph HTTP client not currently used (Purview data comes from PowerShell)
    # but kept for potential future Graph API integration (e.g., audit logs, license checks)
    graph_http = None
    
    # Load data from PowerShell stdin (no HTTP client needed - uses PowerShell cmdlets)
    if payload is not None and not isinstance(payload, dict):
        raise ValueError("Purview payload must be a dictionary.")
    purview_data = payload if payload is not None else load_purview_data_from_stdin()
    return hydrate_purview_client(purview_data)


def hydrate_purview_client(purview_data):
    """Hydrate collected data without an event loop, sockets, or runtime input.

    The asynchronous public client API delegates here after obtaining its payload.
    Offline import can call this helper directly without creating asyncio's
    Windows loopback socketpair.
    """
    
    # Helper to safely extract nested data
    def safe_get(data, key, nested_key=None):
        """Safely extract data, handling cases where PowerShell JSON structure varies"""
        if not isinstance(data, dict):
            return [] if nested_key else {}
        value = data.get(key, {})
        if not isinstance(value, dict):
            return [] if nested_key else {}
        if nested_key:
            result = value.get(nested_key, [])
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                return [result]
            return []
        return value
    
    # Extract data from PowerShell output
    if purview_data:
        dlp_data, dlp_available = extract_collection(purview_data, 'dlp_policies', 'policies')
        dlp_rule_data, dlp_rules_available = extract_collection(purview_data, 'dlp_rules', 'rules')
        labels_data, labels_available = extract_collection(purview_data, 'sensitivity_labels', 'labels')
        retention_data, retention_available = extract_collection(purview_data, 'retention_policies', 'policies')
        label_policies_data, label_policies_available = extract_collection(purview_data, 'label_policies', 'policies')
        insider_risk_data, insider_risk_available = extract_collection(purview_data, 'insider_risk_policies', 'policies')
        comm_comp_data, comm_comp_available = extract_collection(purview_data, 'communication_compliance', 'policies')
        ib_data, ib_available = extract_collection(purview_data, 'information_barriers', 'policies')
        ediscovery_data, ediscovery_available = extract_collection(purview_data, 'ediscovery_cases', 'cases')
        org_config_data, org_config_available = extract_object(purview_data, 'org_config')
        irm_config_data, irm_config_available = extract_object(purview_data, 'irm_config')
        audit_config_data, audit_config_available = extract_object(purview_data, 'audit_config')
        source_status = {
            'dlp_policies': collection_state(purview_data, 'dlp_policies'),
            'dlp_rules': collection_state(purview_data, 'dlp_rules'),
            'sensitivity_labels': collection_state(purview_data, 'sensitivity_labels'),
            'retention_policies': collection_state(purview_data, 'retention_policies'),
            'label_policies': collection_state(purview_data, 'label_policies'),
            'insider_risk_policies': collection_state(purview_data, 'insider_risk_policies', optional=True),
            'communication_compliance': collection_state(purview_data, 'communication_compliance', optional=True),
            'information_barriers': collection_state(purview_data, 'information_barriers', optional=True),
            'ediscovery_cases': collection_state(purview_data, 'ediscovery_cases', optional=True),
            'org_config': collection_state(purview_data, 'org_config'),
            'irm_config': collection_state(purview_data, 'irm_config'),
            'audit_config': collection_state(purview_data, 'audit_config'),
        }
    else:
        print(f"[{get_timestamp()}] ℹ️  No Purview data provided. Rerun main.py with --interactive-auth fresh for deployment-specific recommendations")
        dlp_data = dlp_rule_data = labels_data = retention_data = label_policies_data = []
        insider_risk_data = comm_comp_data = ib_data = ediscovery_data = []
        org_config_data = irm_config_data = audit_config_data = {}
        dlp_available = dlp_rules_available = labels_available = retention_available = label_policies_available = False
        insider_risk_available = comm_comp_available = ib_available = ediscovery_available = False
        org_config_available = irm_config_available = audit_config_available = False
        source_status = {
            key: {
                'availability_status': 'not_requested', 'available': False,
                'records_collected': 0, 'reason': 'Purview interactive collection was not run.',
                'error_category': '', 'required_role': '', 'optional': optional,
                'technical_error': '',
            }
            for key, optional in {
                'dlp_policies': False, 'dlp_rules': False, 'sensitivity_labels': False,
                'retention_policies': False, 'label_policies': False,
                'insider_risk_policies': True, 'communication_compliance': True,
                'information_barriers': True, 'ediscovery_cases': True,
                'org_config': False, 'irm_config': False, 'audit_config': False,
            }.items()
        }
    
    # Normalize each section of the already collected payload.
    def fetch_retention_labels():
        if retention_available:
            return {
                'available': True, 
                'total_labels': len(retention_data) if isinstance(retention_data, list) else 0,
                'labels': retention_data
            }
        return {'available': False, 'total_labels': 0}
    
    def fetch_sensitivity_labels():
        if labels_available:
            return {
                'available': True,
                'total_labels': len(labels_data) if isinstance(labels_data, list) else 0,
                'labels': labels_data
            }
        return {'available': False, 'total_labels': 0}
    
    def fetch_label_policies():
        if label_policies_available:
            return {
                'available': True,
                'total_policies': len(label_policies_data) if isinstance(label_policies_data, list) else 0,
                'policies': label_policies_data
            }
        return {'available': False, 'total_policies': 0}
    
    def fetch_retention_events():
        return {'available': False, 'total_events': 0}
    
    def fetch_retention_event_types():
        return {'available': False, 'total_types': 0}
    
    def fetch_information_barriers():
        if ib_available:
            return {
                'available': True,
                'total_policies': len(ib_data) if isinstance(ib_data, list) else 0,
                'policies': ib_data
            }
        return {'available': False, 'total_policies': 0}
    
    def fetch_ediscovery_cases():
        if ediscovery_available:
            active_count = sum(1 for c in ediscovery_data if c.get('Status') == 'Active')
            return {
                'available': True,
                'total_cases': len(ediscovery_data),
                'active_cases': active_count,
                'cases': ediscovery_data
            }
        return {'available': False, 'total_cases': 0, 'active_cases': 0}
    
    def fetch_dlp_policies():
        if dlp_available:
            enabled_count = sum(1 for p in dlp_data if _purview_enabled(p))
            endpoint_count = sum(
                1 for p in dlp_data
                if _purview_enabled(p)
                and any(p.get(key) not in (None, '', [], False) for key in ('EndpointDlpLocation', 'Devices'))
            )
            return {
                'available': True,
                'total_policies': len(dlp_data),
                'enabled_policies': enabled_count,
                'endpoint_policies': endpoint_count,
                'policies': dlp_data
            }
        return {'available': False, 'total_policies': 0}

    def fetch_dlp_rules():
        if dlp_rules_available:
            enabled_count = sum(1 for rule in dlp_rule_data if not _truthy(rule.get('Disabled')))
            return {
                'available': True,
                'total_rules': len(dlp_rule_data),
                'enabled_rules': enabled_count,
                'rules': dlp_rule_data,
            }
        return {'available': False, 'total_rules': 0, 'enabled_rules': 0, 'rules': []}
    
    def fetch_dlp_alerts():
        return {'available': False, 'total_alerts': 0}
    
    def fetch_irm_alerts():
        return {'available': False, 'total_alerts': 0}
    
    def fetch_insider_risk():
        if insider_risk_available:
            return {
                'available': True,
                'total_policies': len(insider_risk_data),
                'policies': insider_risk_data
            }
        return {'available': False, 'total_policies': 0}
    
    def fetch_comm_compliance():
        if comm_comp_available:
            return {
                'available': True,
                'total_policies': len(comm_comp_data),
                'policies': comm_comp_data
            }
        return {'available': False, 'total_policies': 0}
    
    def fetch_org_config():
        if org_config_available:
            return {
                'available': True,
                'customer_lockbox_enabled': org_config_data.get('CustomerLockBoxEnabled', False),
                'audit_disabled': org_config_data.get('AuditDisabled', False)
            }
        return {'available': False}
    
    def fetch_irm_config():
        if irm_config_available:
            return {
                'available': True,
                'azure_rms_enabled': irm_config_data.get('AzureRMSLicensingEnabled', False)
            }
        return {'available': False}
    
    def fetch_audit_config():
        if audit_config_available:
            return {
                'available': True,
                'unified_audit_enabled': audit_config_data.get('UnifiedAuditLogIngestionEnabled', False),
                'admin_audit_enabled': audit_config_data.get('AdminAuditLogEnabled', False)
            }
        return {'available': False}
    
    def fetch_audit_logs():
        # Audit logs come from PowerShell data, not Graph API
        # (Graph audit logs API is separate and not part of Purview)
        return {'available': False, 'recent_count': 0}
    
    def fetch_customer_lockbox():
        return {'available': False, 'total_requests': 0}
    
    # Preserve per-section failures while normalizing without async I/O.
    def read_section(reader):
        try:
            return reader()
        except Exception as exc:
            return exc

    try:
        results = [read_section(reader) for reader in (
            fetch_sensitivity_labels,
            fetch_retention_labels,
            fetch_label_policies,
            fetch_retention_events,
            fetch_retention_event_types,
            fetch_information_barriers,
            fetch_ediscovery_cases,
            fetch_dlp_policies,
            fetch_dlp_rules,
            fetch_dlp_alerts,
            fetch_irm_alerts,
            fetch_insider_risk,
            fetch_comm_compliance,
            fetch_org_config,
            fetch_irm_config,
            fetch_audit_config,
            fetch_audit_logs,
            fetch_customer_lockbox,
        )]
        
        (sensitivity_labels, retention_labels, label_policies, retention_events, retention_event_types,
         information_barriers, ediscovery_cases, dlp_policies, dlp_rules, dlp_alerts,
         irm_alerts, insider_risk, comm_compliance, org_config, irm_config, 
         audit_config, audit_logs, customer_lockbox) = results
        
        # Count available endpoints
        endpoint_results = {
            'Sensitivity Labels': sensitivity_labels,
            'Label Policies': label_policies,
            'Retention Labels': retention_labels,
            'Retention Events': retention_events,
            'Retention Event Types': retention_event_types,
            'Information Barriers': information_barriers,
            'eDiscovery Cases': ediscovery_cases,
            'DLP Policies': dlp_policies,
            'DLP Rules': dlp_rules,
            'DLP Alerts': dlp_alerts,
            'Insider Risk': insider_risk,
            'Insider Risk Alerts': irm_alerts,
            'Communication Compliance': comm_compliance,
            'Organization Config': org_config,
            'IRM Config': irm_config,
            'Audit Config': audit_config,
            'Audit Logs': audit_logs,
            'Customer Lockbox': customer_lockbox
        }
        
        success_count = sum(1 for result in endpoint_results.values() 
                          if not isinstance(result, Exception) and result.get('available'))
        
    except Exception as e:
        raise Exception(f"Failed to fetch Purview data: {str(e)}")
    
    # Build client object with all deployment data
    class PurviewClient:
        def __init__(self):
            self.collected_at = (purview_data or {}).get('collected_at', '')
            self.sensitivity_labels = sensitivity_labels if not isinstance(sensitivity_labels, Exception) else {'available': False}
            self.label_policies = label_policies if not isinstance(label_policies, Exception) else {'available': False}
            self.retention_labels = retention_labels if not isinstance(retention_labels, Exception) else {'available': False}
            self.retention_events = retention_events if not isinstance(retention_events, Exception) else {'available': False}
            self.retention_event_types = retention_event_types if not isinstance(retention_event_types, Exception) else {'available': False}
            self.information_barriers = information_barriers if not isinstance(information_barriers, Exception) else {'available': False}
            self.ediscovery_cases = ediscovery_cases if not isinstance(ediscovery_cases, Exception) else {'available': False}
            self.dlp_policies = dlp_policies if not isinstance(dlp_policies, Exception) else {'available': False}
            self.dlp_rules = dlp_rules if not isinstance(dlp_rules, Exception) else {'available': False}
            self.dlp_alerts = dlp_alerts if not isinstance(dlp_alerts, Exception) else {'available': False}
            self.insider_risk = insider_risk if not isinstance(insider_risk, Exception) else {'available': False}
            self.irm_alerts = irm_alerts if not isinstance(irm_alerts, Exception) else {'available': False}
            self.comm_compliance = comm_compliance if not isinstance(comm_compliance, Exception) else {'available': False}
            self.org_config = org_config if not isinstance(org_config, Exception) else {'available': False}
            self.irm_config = irm_config if not isinstance(irm_config, Exception) else {'available': False}
            self.audit_config = audit_config if not isinstance(audit_config, Exception) else {'available': False}
            self.audit_logs = audit_logs if not isinstance(audit_logs, Exception) else {'available': False}
            self.customer_lockbox = customer_lockbox if not isinstance(customer_lockbox, Exception) else {'available': False}
            self.collection_status = source_status
            self.required_failures = [
                {'source': key, **state} for key, state in source_status.items()
                if not state.get('available')
                and state.get('availability_status') != 'not_requested'
                and not state.get('optional')
            ]
            self.optional_failures = [
                {'source': key, **state} for key, state in source_status.items()
                if not state.get('available')
                and state.get('availability_status') != 'not_requested'
                and state.get('optional')
            ]
            
            # Summary counts
            self.total_endpoints_available = sum([
                self.sensitivity_labels.get('available', False),
                self.label_policies.get('available', False),
                self.retention_labels.get('available', False),
                self.retention_events.get('available', False),
                self.retention_event_types.get('available', False),
                self.information_barriers.get('available', False),
                self.ediscovery_cases.get('available', False),
                self.dlp_policies.get('available', False),
                self.dlp_rules.get('available', False),
                self.dlp_alerts.get('available', False),
                self.insider_risk.get('available', False),
                self.irm_alerts.get('available', False),
                self.comm_compliance.get('available', False),
                self.org_config.get('available', False),
                self.irm_config.get('available', False),
                self.audit_config.get('available', False),
                self.audit_logs.get('available', False),
                self.customer_lockbox.get('available', False)
            ])
    
    return PurviewClient()

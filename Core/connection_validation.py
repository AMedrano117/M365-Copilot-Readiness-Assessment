"""Lightweight, read-only connection validation for selected collectors."""

import asyncio
import base64
import json
import os
from pathlib import Path

from .collector_registry import COLLECTOR_REGISTRY, selected_collector_ids, collector_permissions
from .get_graph_client import GRAPH_SCOPE, GraphRequestError, get_api_client
from .orchestrator_powershell import _launch_powershell
from . import console_reporting as console
from .sharepoint_configuration import SHAREPOINT_ADMIN_URL_REQUIRED, is_valid_sharepoint_admin_url
from .http_retry import get_with_retry


READY = "Ready"
SIGN_IN = "Sign-in required"
PERMISSION = "Permission missing"
ROLE = "Role missing"
LICENSE = "License unavailable"
NOT_PROVISIONED = "Feature not provisioned"
MODULE = "Module missing or incompatible"
CONFIGURATION = "Configuration required"
NOT_SELECTED = "Optional source not selected"
FAILED = "Connection failed"

ACTIONABLE = {SIGN_IN, PERMISSION, ROLE, MODULE, CONFIGURATION}


def _decode_roles(token):
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return set(json.loads(base64.urlsafe_b64decode(part.encode())).get("roles", []) or [])
    except Exception:
        return set()


def _result(collector_id, status, detail="", **extra):
    definition = COLLECTOR_REGISTRY[collector_id]
    result = {
        "collector_id": collector_id,
        "collector": definition["name"],
        "status": status,
        "availability_status": status,
        "reason": detail,
        "maturity": definition["maturity"],
        "evidence_purpose": definition["purpose"],
        "decision_effect": definition["decision_effect"],
    }
    result.update(extra)
    return result


def _classify_http(collector_id, status_code, detail, roles, required_permissions):
    if status_code == 401:
        return _result(collector_id, SIGN_IN, detail or "Authentication was rejected.")
    if status_code == 403:
        missing = [name for name in required_permissions if name not in roles]
        if missing:
            return _result(collector_id, PERMISSION, "Grant and consent: " + ", ".join(missing))
        if collector_id == "entra_risk":
            return _result(
                collector_id, LICENSE,
                "The required Graph permission is present, but full risky-user evidence requires Entra ID P2. Entra ID P1 provides only limited risk visibility.",
            )
        if collector_id == "power_platform":
            return _result(collector_id, ROLE, "Assign Power Platform Reader RBAC at tenant scope.")
        return _result(collector_id, NOT_PROVISIONED, detail or "The API is authorized but the workload may not be provisioned.")
    if status_code == 404:
        return _result(collector_id, NOT_PROVISIONED, detail or "The feature or endpoint is not provisioned.")
    if status_code == 429:
        return _result(collector_id, FAILED, "The service throttled the connection probe; retry later.")
    return _result(collector_id, FAILED, detail or f"HTTP {status_code or 'error'}")


async def _graph_probe(client, collector_id, path, roles, required, params=None, accept=None):
    try:
        await client.request("GET", path, params=params, headers={"Accept": accept} if accept else None)
        return _result(collector_id, READY)
    except GraphRequestError as exc:
        return _classify_http(collector_id, exc.status_code, str(exc), roles, required)
    except Exception as exc:
        return _result(collector_id, FAILED, f"{type(exc).__name__}: {exc}")


async def _defender_probe(roles):
    http = None
    try:
        http = await get_api_client("defender")
        response = await get_with_retry(http, "/api/machines", params={"$top": "1"})
        if response.status_code == 200:
            return _result("defender_endpoint", READY)
        return _classify_http(
            "defender_endpoint", response.status_code, response.text[:300],
            roles, collector_permissions('defender_endpoint', resource='defender'),
        )
    except Exception as exc:
        return _result("defender_endpoint", FAILED, f"{type(exc).__name__}: {exc}")
    finally:
        if http is not None:
            await http.aclose()


async def _power_platform_probe(client):
    """Validate preview inventory RBAC with a one-record resource query."""
    http = None
    try:
        token = await asyncio.to_thread(
            client.credential.get_token, "https://api.powerplatform.com/.default"
        )
        import httpx
        http = httpx.AsyncClient(
            base_url="https://api.powerplatform.com",
            headers={"Authorization": f"Bearer {token.token}", "Content-Type": "application/json"},
            timeout=30.0,
        )
        response = await http.post(
            "/resourcequery/resources/query",
            params={"api-version": "2024-10-01"},
            json={
                "TableName": "PowerPlatformResources", "Clauses": [],
                "Options": {"Top": 1, "Skip": 0, "SkipToken": ""},
            },
        )
        if response.status_code == 200:
            return _result("power_platform", READY)
        if response.status_code == 403:
            return _result(
                "power_platform", ROLE,
                "Assign Power Platform Reader RBAC to the application at tenant scope.",
            )
        return _classify_http(
            "power_platform", response.status_code, response.text[:300], set(), set()
        )
    except Exception as exc:
        return _result("power_platform", FAILED, f"{type(exc).__name__}: {exc}")
    finally:
        if http is not None:
            await http.aclose()


async def _powershell_certificate_probe(collector_id, script_name, arguments, prefer_windows=False):
    """Run a read-only representative cmdlet when app-only PowerShell is configured."""
    script_path = str(Path(__file__).resolve().parent.parent / script_name)
    try:
        process = _launch_powershell(script_path, arguments, prefer_windows=prefer_windows)
        stdout, stderr = await asyncio.to_thread(process.communicate)
        if process.returncode == 0:
            return _result(collector_id, READY, "Certificate authentication and representative read succeeded.")
        detail_lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
        detail = detail_lines[-1] if detail_lines else (stdout or "Connection probe failed").strip()[:500]
        if any(marker in detail.lower() for marker in ("access denied", "unauthorized", "forbidden", "not authorized")):
            return _result(collector_id, ROLE, detail[:500])
        return _result(collector_id, FAILED, detail[:500])
    except Exception as exc:
        return _result(collector_id, FAILED, f"{type(exc).__name__}: {exc}")


def _certificate_arguments(prefix):
    certificate_path = os.environ.get(f"{prefix}_CERTIFICATE_PATH", "")
    if not certificate_path and os.environ.get("CERTIFICATE_PATH", "").lower().endswith((".pfx", ".p12")):
        certificate_path = os.environ.get("CERTIFICATE_PATH", "")
    password = os.environ.get(f"{prefix}_CERTIFICATE_PASSWORD", os.environ.get("CERTIFICATE_PASSWORD", ""))
    thumbprint = os.environ.get(f"{prefix}_CERTIFICATE_THUMBPRINT", "")
    arguments = []
    if certificate_path:
        arguments.extend(["-CertificatePath", certificate_path])
    if password:
        arguments.extend(["-CertificatePassword", password])
    if thumbprint:
        arguments.extend(["-CertificateThumbprint", thumbprint])
    return arguments


async def run_connection_checks(
    client,
    service_config,
    interactive_plan,
    preview_collectors="none",
    legacy_power_platform_collector=False,
    sharepoint_admin_url="",
    interactive_auth="auto",
    probe_endpoints=True,
    tenant_id="",
    permission_profile="standard",
):
    """Return one normalized connection result for every relevant collector."""
    from .cli_parser import resolve_live_permission_profile
    permission_profile = resolve_live_permission_profile(permission_profile, preview_collectors, legacy_power_platform_collector)
    selected = set(selected_collector_ids(
        service_config, preview_collectors, legacy_power_platform_collector, permission_profile
    ))
    token = await asyncio.to_thread(client.credential.get_token, GRAPH_SCOPE)
    graph_roles = _decode_roles(token.token)
    if permission_profile == 'restricted':
        from .permission_audit import audit_restricted_access
        audit = await audit_restricted_access(client, os.environ.get('CLIENT_ID'), graph_roles)
        if not audit['verified']:
            return [_result('graph_core', PERMISSION, audit['reason'], access_audit_failed=True,
                            permission_profile=permission_profile)]

    probes = {
        "graph_core": ("/v1.0/organization", {"$select": "id,displayName"}, None),
        "m365_usage": ("/v1.0/reports/getOffice365ActiveUserCounts(period='D7')", None, "text/csv"),
        "entra_controls": ("/v1.0/identity/conditionalAccess/policies", {"$top": "1"}, None),
        "entra_risk": ("/v1.0/identityProtection/riskyUsers", {"$top": "1"}, None),
        "external_connections": ("/v1.0/external/connections", {"$top": "1"}, None),
        "graph_security": ("/v1.0/security/alerts_v2", {"$top": "1"}, None),
        "shadow_ai": ("/beta/security/dataDiscovery/cloudAppDiscovery/uploadedStreams", {"$top": "1"}, None),
        "network_access": ("/beta/networkAccess/filteringPolicies", {"$top": "1"}, None),
    }
    permission_map = {name: collector_permissions(name, permission_profile) for name in probes}
    tasks = {}
    for collector_id, (path, params, accept) in probes.items():
        if collector_id in selected:
            missing = permission_map[collector_id] - graph_roles
            if missing:
                tasks[collector_id] = asyncio.sleep(0, result=_result(
                    collector_id, PERMISSION, "Grant and consent: " + ", ".join(sorted(missing))
                ))
            elif probe_endpoints:
                tasks[collector_id] = _graph_probe(
                    client, collector_id, path, graph_roles,
                    permission_map[collector_id], params=params, accept=accept,
                )
            else:
                tasks[collector_id] = asyncio.sleep(0, result=_result(
                    collector_id, READY, "Token role verified; the collector will perform the representative read once."
                ))
    if "defender_endpoint" in selected:
        required_defender = collector_permissions('defender_endpoint', permission_profile, resource='defender')
        try:
            defender_token = await asyncio.to_thread(
                client.credential.get_token, "https://api.securitycenter.microsoft.com/.default"
            )
            defender_roles = _decode_roles(defender_token.token)
        except Exception:
            defender_roles = set()
        excess_defender = defender_roles - required_defender
        if permission_profile == 'restricted' and excess_defender:
            return [_result('defender_endpoint', PERMISSION,
                            'Restricted profile has excess access in the current Defender token: '
                            + ', '.join(sorted(excess_defender)) + '. Review consent and acquire a new token after cleanup.',
                            access_audit_failed=True, permission_profile=permission_profile)]
        if required_defender - defender_roles:
            tasks["defender_endpoint"] = asyncio.sleep(0, result=_result(
                "defender_endpoint", PERMISSION, "Grant and consent: " + ', '.join(sorted(required_defender - defender_roles))
            ))
        elif probe_endpoints:
            tasks["defender_endpoint"] = _defender_probe(defender_roles)
        else:
            tasks["defender_endpoint"] = asyncio.sleep(0, result=_result(
                "defender_endpoint", READY, "Defender token audience and role verified; the device read will run once during collection."
            ))
    if "power_platform" in selected:
        tasks["power_platform"] = (
            _power_platform_probe(client) if probe_endpoints else
            asyncio.sleep(0, result=_result(
                "power_platform", READY, "Power Platform API authentication is selected; preview RBAC will be verified by the inventory request."
            ))
        )

    values = await asyncio.gather(*tasks.values()) if tasks else []
    results = {name: value for name, value in zip(tasks, values)}

    modules = interactive_plan.get("modules", {}) or {}
    if "sharepoint_governance" in selected:
        if not modules.get("Microsoft.Online.SharePoint.PowerShell"):
            results["sharepoint_governance"] = _result(
                "sharepoint_governance", MODULE,
                "Install or update Microsoft.Online.SharePoint.PowerShell in Windows PowerShell.",
            )
        elif interactive_auth == "skip" and not interactive_plan.get("sharepoint", {}).get("application_auth"):
            results["sharepoint_governance"] = _result(
                "sharepoint_governance", SIGN_IN, "Browser authentication is disabled for this run."
            )
        elif not is_valid_sharepoint_admin_url(sharepoint_admin_url):
            results["sharepoint_governance"] = _result(
                "sharepoint_governance", CONFIGURATION, SHAREPOINT_ADMIN_URL_REQUIRED
            )
        elif interactive_plan.get("sharepoint", {}).get("application_auth"):
            if probe_endpoints:
                sharepoint_args = [
                    "-AdminUrl", sharepoint_admin_url,
                    "-TenantId", tenant_id or os.environ.get("TENANT_ID", ""),
                    "-ClientId", os.environ.get("CLIENT_ID", ""),
                    "-AuthMode", "Skip", "-ConnectionOnly",
                ] + _certificate_arguments("SHAREPOINT")
                results["sharepoint_governance"] = await _powershell_certificate_probe(
                    "sharepoint_governance", "collect_sharepoint_governance.ps1",
                    sharepoint_args, prefer_windows=True,
                )
            else:
                results["sharepoint_governance"] = _result(
                    "sharepoint_governance", READY,
                    "Certificate configuration is present; the collector will perform the representative read once.",
                )
        else:
            results["sharepoint_governance"] = _result(
                "sharepoint_governance", SIGN_IN, "Browser sign-in will be requested during collection."
            )

    if "purview" in selected:
        if not modules.get("ExchangeOnlineManagement"):
            results["purview"] = _result(
                "purview", MODULE, "Install or update ExchangeOnlineManagement."
            )
        elif interactive_plan.get("purview", {}).get("application_auth"):
            if probe_endpoints:
                purview_args = [
                    "-DataOnly", "-ConnectionOnly", "-AuthMode", "Auto",
                    "-TenantId", tenant_id or os.environ.get("TENANT_ID", ""),
                    "-ClientId", os.environ.get("CLIENT_ID", ""),
                    "-Organization", os.environ.get("PURVIEW_ORGANIZATION", ""),
                ] + _certificate_arguments("PURVIEW")
                results["purview"] = await _powershell_certificate_probe(
                    "purview", "collect_purview_data.ps1", purview_args,
                )
            else:
                results["purview"] = _result(
                    "purview", READY, "Certificate configuration is present; supported cmdlets will perform the representative read once."
                )
        elif interactive_auth == "skip":
            results["purview"] = _result("purview", SIGN_IN, "Browser authentication is disabled for this run.")
        else:
            results["purview"] = _result("purview", SIGN_IN, "Browser sign-in will be requested during collection.")

    if "legacy_power_platform" in selected:
        results["legacy_power_platform"] = _result(
            "legacy_power_platform",
            READY if modules.get("Az.Accounts") else MODULE,
            "" if modules.get("Az.Accounts") else "Install Az.Accounts only for this compatibility option.",
        )

    # Include unselected optional sources so operators can see that absence is intentional.
    for collector_id in ("power_platform", "shadow_ai", "network_access", "legacy_power_platform"):
        if collector_id not in results:
            results[collector_id] = _result(collector_id, NOT_SELECTED)
    if permission_profile == 'restricted':
        for collector_id in ('sharepoint_governance', 'purview'):
            results[collector_id] = _result(
                collector_id, NOT_SELECTED,
                'Restricted permission profile: administrative PowerShell is not requested; supply supported exports or saved evidence.',
            )
    for result in results.values():
        result['permission_profile'] = permission_profile
    return [results[key] for key in COLLECTOR_REGISTRY if key in results]


def connection_exit_code(results):
    if any(item.get("status") in ACTIONABLE for item in results):
        return 2
    if any(item.get("status") == FAILED and item.get("decision_effect") for item in results):
        return 1
    return 0


def connection_status_to_availability(status):
    """Translate operator preflight labels into the evidence-state vocabulary."""
    if status == READY:
        return "available"
    if status == NOT_SELECTED:
        return "not_requested"
    return "unavailable"


def print_connection_results(results, detailed=True):
    if detailed:
        console.section('Collector connections')
        console.status('Checks cover selected permissions and representative requests. '
                       'The live run reports whether each dataset was collected successfully.')
        width = max((len(item["collector"]) for item in results), default=20)
        for item in results:
            detail = f" — {item['reason']}" if item.get("reason") else ""
            message = f"{item['collector']:<{width}}  {item['status']}{detail}"
            if item['status'] == NOT_SELECTED:
                console.detail(message)
            else:
                console.status(message, tone='success' if item['status'] == READY else 'warning')
    else:
        expected_sign_ins = [
            item for item in results
            if item.get("status") == SIGN_IN and "will be requested" in item.get("reason", "").lower()
        ]
        actionable = [
            item for item in results
            if item.get("status") in ACTIONABLE | {FAILED, LICENSE, NOT_PROVISIONED} and item not in expected_sign_ins
        ]
        if actionable:
            names = ", ".join(item["collector"] for item in actionable)
            console.status(f"Connection preflight: {len(actionable)} source(s) need attention ({names}).", tone='warning')
            for item in actionable:
                if item.get('reason'):
                    console.status(f"{item['collector']}: {item['reason']}", tone='warning')
        else:
            console.detail('Connection preflight: selected non-interactive sources are usable.')
        if expected_sign_ins:
            names = ", ".join(item["collector"] for item in expected_sign_ins)
            console.detail(f"Browser sign-in will follow for: {names}.")

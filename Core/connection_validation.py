"""Lightweight, read-only connection validation for selected collectors."""

import asyncio
import base64
import json
import os
import re
from pathlib import Path

from .collector_registry import COLLECTOR_REGISTRY, selected_collector_ids, collector_permissions
from .get_graph_client import GRAPH_SCOPE, GraphRequestError, get_api_client
from .orchestrator_powershell import _launch_powershell
from . import console_reporting as console
from .sharepoint_configuration import SHAREPOINT_ADMIN_URL_REQUIRED, is_valid_sharepoint_admin_url
from .http_retry import get_with_retry


READY = "Ready"
# Connected and readable, but the workload role exposes only some datasets.
# Not actionable for the exit code: the run collects what the role allows.
PARTIAL = "Ready with gaps"
SIGN_IN = "Sign-in required"
PERMISSION = "Permission missing"
ROLE = "Role missing"
LICENSE = "License unavailable"
NOT_PROVISIONED = "Feature not provisioned"
MODULE = "Module missing or incompatible"
CONFIGURATION = "Configuration required"
NOT_SELECTED = "Optional source not selected"
FAILED = "Connection failed"
DELEGATED_OPTIONAL = "Optional delegated sign-in"

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


def _probe_payload(stdout):
    """Return the probe's JSON summary line (the last JSON object on stdout)."""
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {}


async def _powershell_probe(collector_id, script_name, arguments, prefer_windows=False, secrets=None,
                            success_detail="Application authentication and representative read succeeded.",
                            partial_hint=""):
    """Run a read-only representative cmdlet for an app-only PowerShell path.

    When the probe reports datasets its role does not expose (``missing``),
    the result is Ready with gaps: collection proceeds and records a
    role-missing reason for those datasets.
    """
    from .orchestrator_powershell import _sanitize_collector_detail
    script_path = str(Path(__file__).resolve().parent.parent / script_name)
    try:
        process = _launch_powershell(script_path, arguments, prefer_windows=prefer_windows, secrets=secrets)
        stdout, stderr = await asyncio.to_thread(process.communicate)
        if process.returncode == 0:
            payload = _probe_payload(stdout)
            missing = [str(item) for item in payload.get("missing") or [] if item]
            if missing:
                exposed = [str(item) for item in payload.get("exposed") or [] if item]
                detail = (f"Connected. Readable: {', '.join(exposed) or 'none'}. "
                          f"Not exposed by the current role: {', '.join(missing)}.")
                if partial_hint:
                    detail += " " + partial_hint
                return _result(collector_id, PARTIAL, detail, exposed=exposed, missing=missing)
            return _result(collector_id, READY, success_detail)
        detail_lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
        detail = detail_lines[-1] if detail_lines else (stdout or "Connection probe failed").strip()[:500]
        detail = _sanitize_collector_detail(detail)
        if any(marker in detail.lower() for marker in ("access denied", "unauthorized", "forbidden", "not authorized", "role_missing")):
            return _result(collector_id, ROLE, detail[:500])
        return _result(collector_id, FAILED, detail[:500])
    except Exception as exc:
        return _result(collector_id, FAILED, f"{type(exc).__name__}: {exc}")


# Backward-compatible name used by existing callers and tests.
_powershell_certificate_probe = _powershell_probe


def _certificate_arguments(prefix):
    """Non-secret certificate arguments plus secrets for stdin delivery."""
    from .orchestrator_powershell import certificate_arguments
    return certificate_arguments(prefix)


def _module_version(interactive_plan, name):
    details = ((interactive_plan or {}).get("modules", {}) or {}).get("module_details", {}) or {}
    return str((details.get(name) or {}).get("version") or "")


def _version_at_least(version, minimum):
    def parts(value):
        return tuple(int(item) for item in re.findall(r"\d+", str(value))[:4])
    return bool(version) and parts(version) >= parts(minimum)


async def run_connection_checks(
    client,
    service_config,
    interactive_plan,
    preview_collectors="auto",
    legacy_power_platform_collector=False,
    sharepoint_admin_url="",
    interactive_auth="auto",
    probe_endpoints=True,
    tenant_id="",
    permission_profile="standard",
    delegated="auto",
    delegated_state=None,
    auth_plan_out=None,
):
    """Return one normalized connection result for every relevant collector.

    When ``auth_plan_out`` is a dict, it receives the resolved evidence plan
    (which auth path each dataset will use and how to unlock better ones).
    """
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
        "report_settings": ("/v1.0/admin/reportSettings", None, None),
        "sharepoint_tenant_settings": ("/v1.0/admin/sharepoint/settings", None, None),
        "purview_labels_graph": ("/beta/security/informationProtection/sensitivityLabels", None, None),
        "copilot_audit": ("/v1.0/security/auditLog/queries", {"$top": "1"}, None),
        "shadow_ai": ("/beta/security/dataDiscovery/cloudAppDiscovery/uploadedStreams", {"$top": "1"}, None),
        "entra_recommendations": ("/beta/directory/recommendations", {"$select": "id,status"}, None),
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
    defender_roles = None
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
                "sharepoint_governance", SIGN_IN,
                "Skipped for this run: --interactive-auth skip turns off the SharePoint administrator sign-in and no "
                "SharePoint certificate is configured. Tenant sharing settings still come from Microsoft Graph. For "
                "per-site settings and Data Access Governance reports, run without --interactive-auth skip (browser "
                "sign-in) or add a certificate (setup-service-principal.ps1 -EnableSharePointAppOnly)."
            )
        elif not is_valid_sharepoint_admin_url(sharepoint_admin_url):
            results["sharepoint_governance"] = _result(
                "sharepoint_governance", CONFIGURATION, SHAREPOINT_ADMIN_URL_REQUIRED
            )
        elif interactive_plan.get("sharepoint", {}).get("application_auth"):
            if probe_endpoints:
                certificate_args, certificate_secrets = _certificate_arguments("SHAREPOINT")
                sharepoint_args = [
                    "-AdminUrl", sharepoint_admin_url,
                    "-TenantId", tenant_id or os.environ.get("TENANT_ID", ""),
                    "-ClientId", os.environ.get("CLIENT_ID", ""),
                    "-AuthMode", "Skip", "-ConnectionOnly",
                ] + certificate_args
                results["sharepoint_governance"] = await _powershell_certificate_probe(
                    "sharepoint_governance", "collect_sharepoint_governance.ps1",
                    sharepoint_args, prefer_windows=True, secrets=certificate_secrets,
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

    exchange_roles = compliance_roles = None
    purview_token_secrets_found = {}
    purview_token_reason = ""
    if "purview" in selected and modules.get("ExchangeOnlineManagement"):
        from .workload_tokens import MANAGE_AS_APP, purview_token_secrets
        purview_token_secrets_found, token_details = await purview_token_secrets(getattr(client, "credential", None))
        exchange_roles = token_details.get("exchange_roles", set())
        compliance_roles = token_details.get("compliance_roles", set())
        purview_token_reason = token_details.get("reason", "")
    exo_version = _module_version(interactive_plan, "ExchangeOnlineManagement")
    token_module_ready = _version_at_least(exo_version, "3.8.0") or not exo_version

    if "purview" in selected:
        if not modules.get("ExchangeOnlineManagement"):
            results["purview"] = _result(
                "purview", MODULE, "Install or update ExchangeOnlineManagement."
            )
        elif purview_token_secrets_found and token_module_ready and os.environ.get("PURVIEW_ORGANIZATION"):
            if probe_endpoints:
                token_args = [
                    "-DataOnly", "-ConnectionOnly", "-AuthMode", "Skip",
                    "-TenantId", tenant_id or os.environ.get("TENANT_ID", ""),
                    "-ClientId", os.environ.get("CLIENT_ID", ""),
                    "-Organization", os.environ.get("PURVIEW_ORGANIZATION", ""),
                ]
                results["purview"] = await _powershell_certificate_probe(
                    "purview", "collect_purview_data.ps1", token_args, secrets=purview_token_secrets_found,
                    success_detail="Application token authentication and representative Purview and Exchange reads succeeded.",
                    partial_hint=("Assign a broader read-only role to add them, such as Global Reader "
                                  "(setup-service-principal.ps1 -WorkloadRbac GlobalReader) or View-Only role groups "
                                  "(-WorkloadRbac RoleGroups)."),
                )
                if results["purview"]["status"] == ROLE:
                    results["purview"]["reason"] = ("The application token connected but a representative read was denied. "
                                                    "Assign the default read-only role: setup-service-principal.ps1 -WorkloadRbac GlobalReader. "
                                                    + results["purview"]["reason"])[:500]
            else:
                results["purview"] = _result(
                    "purview", READY,
                    "Exchange.ManageAsApp is consented; Purview will use an application token. The workload role is verified by the representative read.",
                )
        elif interactive_plan.get("purview", {}).get("application_auth"):
            if probe_endpoints:
                certificate_args, certificate_secrets = _certificate_arguments("PURVIEW")
                purview_args = [
                    "-DataOnly", "-ConnectionOnly", "-AuthMode", "Auto",
                    "-TenantId", tenant_id or os.environ.get("TENANT_ID", ""),
                    "-ClientId", os.environ.get("CLIENT_ID", ""),
                    "-Organization", os.environ.get("PURVIEW_ORGANIZATION", ""),
                ] + certificate_args
                results["purview"] = await _powershell_certificate_probe(
                    "purview", "collect_purview_data.ps1", purview_args, secrets=certificate_secrets,
                )
            else:
                results["purview"] = _result(
                    "purview", READY, "Certificate configuration is present; supported cmdlets will perform the representative read once."
                )
        elif interactive_auth == "skip":
            unlock = purview_token_reason or "Consent Exchange.ManageAsApp and assign the default read-only role (setup-service-principal.ps1 -WorkloadRbac GlobalReader)."
            if purview_token_secrets_found and not token_module_ready:
                unlock = f"ExchangeOnlineManagement {exo_version} is installed; 3.8.0 or later is required for application tokens."
            results["purview"] = _result("purview", SIGN_IN, "Browser authentication is disabled for this run and no application path is available. " + unlock)
        else:
            hint = (" To collect without a sign-in: " + purview_token_reason) if purview_token_reason else ""
            results["purview"] = _result("purview", SIGN_IN, "Browser sign-in will be requested during collection." + hint)

    if "copilot_admin_settings" in selected:
        if delegated == "off":
            results["copilot_admin_settings"] = _result(
                "copilot_admin_settings", NOT_SELECTED,
                "Delegated sign-in is off (--delegated off). Copilot limited mode is only exposed to signed-in administrators.",
            )
        elif delegated_state in {"cached", "signed_in"}:
            results["copilot_admin_settings"] = _result(
                "copilot_admin_settings", READY, "A cached delegated sign-in will be used for Copilot admin settings.")
        elif delegated == "required":
            results["copilot_admin_settings"] = _result(
                "copilot_admin_settings", SIGN_IN, "Delegated sign-in is required (--delegated required) and will be requested.")
        else:
            results["copilot_admin_settings"] = _result(
                "copilot_admin_settings", DELEGATED_OPTIONAL,
                "Optional: an administrator sign-in adds Copilot limited mode. Application-permission collection is unaffected.",
            )

    if "legacy_power_platform" in selected:
        results["legacy_power_platform"] = _result(
            "legacy_power_platform",
            READY if modules.get("Az.Accounts") else MODULE,
            "" if modules.get("Az.Accounts") else "Install Az.Accounts only for this compatibility option.",
        )

    # Include unselected optional sources so operators can see that absence is intentional.
    for collector_id in ("copilot_audit", "power_platform", "shadow_ai", "network_access", "legacy_power_platform"):
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

    if auth_plan_out is not None:
        from .auth_plan import build_auth_context, resolve_auth_plan
        workload_roles = {"exchange": exchange_roles, "eop": compliance_roles}
        if "defender_endpoint" in selected:
            workload_roles["defender"] = defender_roles
        context = build_auth_context(
            permission_profile=permission_profile, interactive_auth=interactive_auth, delegated=delegated,
            modules=modules, graph_roles=graph_roles, workload_roles=workload_roles,
            sharepoint_admin_url=sharepoint_admin_url if is_valid_sharepoint_admin_url(sharepoint_admin_url) else "",
            purview_organization=os.environ.get("PURVIEW_ORGANIZATION", ""), delegated_state=delegated_state,
        )
        plan = resolve_auth_plan(sorted(selected, key=list(COLLECTOR_REGISTRY).index), context)
        auth_plan_out.clear()
        auth_plan_out.update(plan)
        for result in results.values():
            datasets = [entry for entry in plan["datasets"].values() if entry.get("collector") == result["collector_id"]]
            if datasets:
                result["planned_path"] = datasets[0].get("selected_path", "")
    return [results[key] for key in COLLECTOR_REGISTRY if key in results]


def connection_exit_code(results):
    if any(item.get("status") in ACTIONABLE for item in results):
        return 2
    if any(item.get("status") == FAILED and item.get("decision_effect") for item in results):
        return 1
    return 0


def connection_status_to_availability(status):
    """Translate operator preflight labels into the evidence-state vocabulary."""
    if status in {READY, PARTIAL}:
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
            if item.get("status") in ACTIONABLE | {FAILED, LICENSE, NOT_PROVISIONED, PARTIAL} and item not in expected_sign_ins
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

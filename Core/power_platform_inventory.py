"""Optional tenant-wide Power Platform inventory sources."""

import csv
from datetime import datetime, timezone
from pathlib import Path

POWER_PLATFORM_API = "https://api.powerplatform.com"
POWER_PLATFORM_READER_ROLE_ID = "c886ad2e-27f7-4874-8381-5849b8d8a090"


class PowerPlatformInventoryData:
    """Client-compatible container consumed by existing recommendations and evidence sheets."""

    pass


def _base_evidence(source):
    return {
        "available": False,
        "reason": "Not collected",
        "source": source,
        "period": "Current inventory",
        "refresh_date": "",
        "freshness": "Unknown",
        "stale": False,
        "preview": False,
    }


def _key(row, *candidates):
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for candidate in candidates:
        value = lowered.get(candidate.lower())
        if value not in (None, ""):
            return value
    return ""


def _resource_type(row):
    return str(_key(row, "type", "resource type", "resourcetype", "kind") or "").lower()


def _property(row, *names):
    properties = row.get("properties") if isinstance(row, dict) else None
    if isinstance(properties, dict):
        lowered = {str(k).lower(): v for k, v in properties.items()}
        for name in names:
            if name.lower() in lowered and lowered[name.lower()] not in (None, ""):
                return lowered[name.lower()]
    return _key(row, *names)


def _as_legacy_resource(row, resource_type):
    display_name = _property(row, "displayName", "display name", "name") or _key(row, "name", "id")
    environment_id = _property(row, "environmentId", "environment id")
    state = _property(row, "state", "status")
    owner = _property(row, "ownerId", "owner", "createdBy")
    properties = {
        "displayName": display_name,
        "state": state,
        "environmentId": environment_id,
        "ownerId": owner,
        "appType": resource_type,
        "flowType": resource_type,
        "environmentType": _property(row, "environmentType", "environment type"),
        "isManaged": _property(row, "isManaged", "managed"),
        "connectors": _property(row, "powerPlatformConnectors", "connectors", "connector names"),
    }
    return {
        "id": _key(row, "id", "resource id"),
        "name": _key(row, "name", "id") or display_name,
        "location": _key(row, "location", "region"),
        "type": resource_type,
        "properties": properties,
        "inventory": dict(row),
    }


def build_inventory_client(rows, source, refresh_date="", preview=False):
    client = PowerPlatformInventoryData()
    client.inventory_rows = list(rows or [])
    client.environments = []
    client.environment_groups = []
    client.flows = []
    client.apps = []
    client.agents = []
    client.connections = []
    client.ai_models = []
    client.dlp_policies = []
    client.solutions = []
    client.permission_failures = []

    for row in client.inventory_rows:
        resource_type = _resource_type(row)
        item = _as_legacy_resource(row, resource_type)
        if "environmentgroup" in resource_type or "environment group" in resource_type:
            client.environment_groups.append(item)
        elif "environment" in resource_type:
            client.environments.append(item)
        elif "agent" in resource_type or "copilotstudio" in resource_type:
            client.agents.append(item)
        elif "flow" in resource_type or "powerautomate" in resource_type:
            client.flows.append(item)
        elif "app" in resource_type or "powerapps" in resource_type:
            client.apps.append(item)
        elif "connector" in resource_type or "connection" in resource_type:
            client.connections.append(item)

    env_types = {"production": [], "sandbox": [], "developer": [], "default": [], "trial": [], "other": []}
    for env in client.environments:
        env_type = str(env.get("properties", {}).get("environmentType", "") or "").lower()
        bucket = next((name for name in env_types if name in env_type), "other")
        env_types[bucket].append({
            "name": env.get("name", ""),
            "display_name": env.get("properties", {}).get("displayName", ""),
            "type": env_type,
            "state": env.get("properties", {}).get("state", ""),
        })
    client.environment_summary = dict(env_types)
    client.environment_summary.update({"total": len(client.environments), "by_state": {}})
    client.flow_summary = {
        "total": len(client.flows), "cloud_flows": [x.get("name") for x in client.flows],
        "desktop_flows": [], "with_http_trigger": [], "suspended": [], "enabled": 0,
    }
    client.app_summary = {
        "total": len(client.apps),
        "canvas_apps": [x.get("name") for x in client.apps if "canvas" in x.get("type", "")],
        "model_driven_apps": [x.get("name") for x in client.apps if "modeldriven" in x.get("type", "")],
        "teams_apps": [], "with_copilot_control": [], "sharepoint_connected": [],
    }
    connectors = set()
    for row in client.inventory_rows:
        raw = _property(row, "powerPlatformConnectors", "connectorId", "connectors", "connector names")
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    connector_id = item.get("connectorId") or item.get("id")
                    if connector_id:
                        connectors.add(str(connector_id))
                elif item:
                    connectors.add(str(item))
        elif raw:
            connectors.update(part.strip() for part in str(raw).replace(";", ",").split(",") if part.strip())
    client.connection_summary = {
        "total": len(connectors) or len(client.connections),
        "premium_connectors": [], "standard_connectors": sorted(connectors),
        "custom_connectors": [], "sap": False, "salesforce": False,
        "servicenow": False, "sql": False,
    }
    client.ai_model_summary = {
        "available": False, "total": None,
        "error": "AI Builder model detail is not included in this inventory import",
    }
    client.dlp_summary = {"total": 0, "error": "DLP policy detail is not included in unified inventory"}
    client.capacity_summary = {"available": False, "error": "Capacity detail is not included in unified inventory"}
    client.solution_summary = {"total": 0}
    client.agent_summary = {"total": len(client.agents)}

    evidence = _base_evidence(source)
    evidence.update({
        "available": True,
        "reason": "",
        "refresh_date": refresh_date,
        "freshness": "Unknown",
        "preview": preview,
        "records": len(client.inventory_rows),
        "counts": {
            "environments": len(client.environments), "apps": len(client.apps),
            "environment_groups": len(client.environment_groups),
            "flows": len(client.flows), "agents": len(client.agents),
            "connectors": len(connectors) or len(client.connections),
        },
    })
    client.power_platform_inventory = evidence
    return client


def load_power_platform_inventory(path):
    if not path:
        return None
    source = Path(path)
    if not source.exists() or not source.is_file():
        client = PowerPlatformInventoryData()
        client.power_platform_inventory = _base_evidence("Power Platform inventory export")
        client.power_platform_inventory["reason"] = "Inventory export was not found: {}".format(path)
        return client
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except Exception as exc:
        client = PowerPlatformInventoryData()
        client.power_platform_inventory = _base_evidence("Power Platform inventory export")
        client.power_platform_inventory["reason"] = "Unable to read inventory export: {}".format(type(exc).__name__)
        return client
    modified = datetime.fromtimestamp(source.stat().st_mtime, timezone.utc)
    client = build_inventory_client(rows, "Power Platform unified inventory export", modified.date().isoformat())
    age_days = max(0, (datetime.now(timezone.utc) - modified).days)
    client.power_platform_inventory["age_days"] = age_days
    client.power_platform_inventory["stale"] = age_days > 30
    client.power_platform_inventory["freshness"] = "Stale" if age_days > 30 else "Fresh"
    client.power_platform_inventory["source_file"] = source.name
    return client


async def collect_power_platform_inventory_preview(tenant_id):
    # Keep the CSV inventory loader usable without live authentication packages.
    import httpx
    from .get_graph_client import get_shared_credential

    credential = get_shared_credential()
    evidence = _base_evidence("Power Platform inventory API (preview)")
    evidence["preview"] = True
    try:
        token = credential.get_token("https://api.powerplatform.com/.default")
    except Exception as exc:
        client = PowerPlatformInventoryData()
        evidence["reason"] = "Power Platform API token unavailable: {}".format(type(exc).__name__)
        client.power_platform_inventory = evidence
        return client

    rows = []
    skip_token = ""
    request_succeeded = False
    async with httpx.AsyncClient(
        base_url=POWER_PLATFORM_API,
        headers={"Authorization": "Bearer {}".format(token.token), "Content-Type": "application/json"},
        timeout=60.0,
    ) as http:
        while True:
            body = {
                "TableName": "PowerPlatformResources",
                "Clauses": [{
                    "$type": "where",
                    "FieldName": "type",
                    "Operator": "in~",
                    "Values": [
                        "'microsoft.powerplatform/environments'",
                        "'microsoft.powerapps/canvasapps'",
                        "'microsoft.powerapps/modeldrivenapps'",
                        "'microsoft.powerapps/codeapps'",
                        "'microsoft.powerapps/apps'",
                        "'microsoft.powerautomate/cloudflows'",
                        "'microsoft.powerautomate/agentflows'",
                        "'microsoft.powerautomate/m365agentflows'",
                        "'microsoft.copilotstudio/agents'",
                        "'microsoft.powerplatformconnector/connectors'",
                        "'microsoft.powerplatform/environmentgroups'",
                    ],
                }],
                "Options": {"Top": 1000, "Skip": 0, "SkipToken": skip_token},
            }
            try:
                response = await http.post(
                    "/resourcequery/resources/query",
                    params={"api-version": "2024-10-01"},
                    json=body,
                )
            except Exception as exc:
                evidence["reason"] = "Power Platform inventory request failed: {}".format(type(exc).__name__)
                break
            if response.status_code != 200:
                if response.status_code == 403:
                    evidence["reason"] = (
                        "Power Platform Reader RBAC is missing for the service principal at tenant scope"
                    )
                else:
                    evidence["reason"] = "Power Platform inventory API returned HTTP {}".format(response.status_code)
                break
            payload = response.json()
            request_succeeded = True
            rows.extend(payload.get("data", []) or [])
            skip_token = payload.get("skipToken", "") or ""
            if not skip_token or not payload.get("resultTruncated"):
                break

    if request_succeeded:
        return build_inventory_client(
            rows,
            "Power Platform inventory API (preview)",
            "",
            preview=True,
        )
    client = PowerPlatformInventoryData()
    client.power_platform_inventory = evidence
    return client

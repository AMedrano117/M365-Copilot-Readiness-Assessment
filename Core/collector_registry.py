"""Shared collector catalog used by setup, preflight, and report coverage."""

import json
from collections import OrderedDict
from pathlib import Path


REGISTRY_PATH = Path(__file__).resolve().parent.parent / "collector-registry.json"
with REGISTRY_PATH.open("r", encoding="utf-8") as registry_file:
    _REGISTRY_DOCUMENT = json.load(registry_file)

COLLECTOR_REGISTRY = OrderedDict(
    (item["id"], {key: value for key, value in item.items() if key != "id"})
    for item in _REGISTRY_DOCUMENT.get("collectors", [])
)


def selected_collector_ids(service_config, preview_collectors="none", legacy=False):
    selected = ["graph_core"]
    if service_config.get("run_m365"):
        selected.extend(["m365_usage", "external_connections", "sharepoint_governance"])
    if service_config.get("run_entra"):
        selected.extend(["entra_controls", "entra_risk"])
    if service_config.get("run_defender"):
        selected.extend(["graph_security", "defender_endpoint"])
    if service_config.get("run_purview"):
        selected.append("purview")
    if preview_collectors in {"power-platform", "all"}:
        selected.append("power_platform")
    if preview_collectors in {"shadow-ai", "all"}:
        selected.append("shadow_ai")
    if preview_collectors in {"network-access", "all"}:
        selected.append("network_access")
    if legacy:
        selected.append("legacy_power_platform")
    return list(dict.fromkeys(selected))

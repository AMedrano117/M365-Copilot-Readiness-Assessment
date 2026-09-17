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
PERMISSION_PROFILES = _REGISTRY_DOCUMENT["permission_profiles"]
RESOURCE_APP_IDS = _REGISTRY_DOCUMENT["resource_app_ids"]


def normalize_permission_profile(value):
    """Validate a profile without silently broadening a misspelled restriction."""
    profile = str(value or "standard").strip().lower()
    if profile not in PERMISSION_PROFILES:
        raise ValueError(f"Unknown permission profile: {value!r}")
    return profile


def source_allowed(source_name, permission_profile="standard"):
    profile = PERMISSION_PROFILES[normalize_permission_profile(permission_profile)]
    return source_name not in profile["excluded_sources"] and source_name not in profile["excluded_collectors"]


def collector_permissions(collector_id, permission_profile="standard", resource="graph"):
    """Application permissions for a selected collector and resource API."""
    profile_name = normalize_permission_profile(permission_profile)
    if not source_allowed(collector_id, profile_name):
        return set()
    requested = COLLECTOR_REGISTRY[collector_id].get("permission_resources", {}).get(resource, [])
    excluded = PERMISSION_PROFILES[profile_name]["excluded_permissions"].get(resource, [])
    return set(requested) - set(excluded)


def profile_permission_resources(permission_profile="standard"):
    """Maximum stable application access allowed by a profile, by resource app ID."""
    profile_name = normalize_permission_profile(permission_profile)
    resources = {}
    for collector_id, collector in COLLECTOR_REGISTRY.items():
        if collector.get("maturity") != "stable":
            continue
        for resource in collector.get("permission_resources", {}):
            permissions = collector_permissions(collector_id, profile_name, resource)
            if permissions:
                resources.setdefault(RESOURCE_APP_IDS[resource], set()).update(permissions)
    return resources


def selected_collector_ids(service_config, preview_collectors="none", legacy=False, permission_profile="standard"):
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
    return [collector_id for collector_id in dict.fromkeys(selected) if source_allowed(collector_id, permission_profile)]

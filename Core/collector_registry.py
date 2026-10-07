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
AUTH_PATHS = _REGISTRY_DOCUMENT.get("auth_paths", {})
CREDENTIAL_KINDS = _REGISTRY_DOCUMENT.get("credential_kinds", {})


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
        if collector.get("maturity") != "stable" and not (profile_name == 'standard' and collector.get('default_setup')):
            continue
        for resource in collector.get("permission_resources", {}):
            permissions = collector_permissions(collector_id, profile_name, resource)
            if permissions:
                resources.setdefault(RESOURCE_APP_IDS[resource], set()).update(permissions)
    return resources


def supplemental_enabled(selection, collector, permission_profile="standard"):
    if normalize_permission_profile(permission_profile) == 'restricted':
        return False
    return selection == 'all' or selection == collector or (selection == 'auto' and collector in {
        'entra-recommendations', 'shadow-ai', 'copilot-audit'})


def selected_collector_ids(service_config, preview_collectors="auto", legacy=False, permission_profile="standard"):
    selected = ["graph_core"]
    if service_config.get("run_m365"):
        selected.extend(["m365_usage", "report_settings", "external_connections",
                         "sharepoint_tenant_settings", "sharepoint_governance", "copilot_admin_settings"])
    if service_config.get("run_entra"):
        selected.extend(["entra_controls", "entra_risk"])
    if service_config.get("run_defender"):
        selected.extend(["graph_security", "defender_endpoint"])
    if service_config.get("run_purview"):
        selected.extend(["purview_labels_graph", "purview"])
    if supplemental_enabled(preview_collectors, 'entra-recommendations', permission_profile) and service_config.get('run_entra'):
        selected.append('entra_recommendations')
    if supplemental_enabled(preview_collectors, 'copilot-audit', permission_profile) and service_config.get("run_m365"):
        selected.append("copilot_audit")
    if preview_collectors in {"power-platform", "all"}:
        selected.append("power_platform")
    if supplemental_enabled(preview_collectors, 'shadow-ai', permission_profile) and service_config.get('run_m365'):
        selected.append("shadow_ai")
    if preview_collectors in {"network-access", "all"}:
        selected.append("network_access")
    if legacy:
        selected.append("legacy_power_platform")
    return [collector_id for collector_id in dict.fromkeys(selected) if source_allowed(collector_id, permission_profile)]


def delegated_permissions(collector_id, permission_profile="standard", resource="graph"):
    """Delegated scopes for a collector; empty when the profile forbids delegation."""
    profile_name = normalize_permission_profile(permission_profile)
    if not PERMISSION_PROFILES[profile_name].get("allow_delegated", True) or not source_allowed(collector_id, profile_name):
        return set()
    return set(COLLECTOR_REGISTRY[collector_id].get("delegated_permission_resources", {}).get(resource, []))


def collector_datasets(collector_id):
    """Datasets and their ordered auth paths; single-path collectors get an implicit entry."""
    collector = COLLECTOR_REGISTRY[collector_id]
    datasets = collector.get("datasets")
    if datasets:
        return [dict(item) for item in datasets]
    if "delegated_browser" in collector.get("auth_modes", []) and not collector.get("permission_resources"):
        paths = ["graph_delegated"]
    elif collector.get("permission_resources", {}).get("defender"):
        paths = ["defender_app"]
    else:
        paths = ["graph_app"]
    return [{"id": collector_id, "baseline": paths[0] if AUTH_PATHS.get(paths[0], {}).get("kind") == "graph_app" else "",
             "auth_paths": paths}]


def auth_path_label(path_id):
    return AUTH_PATHS.get(path_id, {}).get("label", path_id)


def credential_kind_label(kind):
    return CREDENTIAL_KINDS.get(kind, {}).get("label", kind or CREDENTIAL_KINDS.get("unrecorded", {}).get("label", "Not recorded"))


def validate_registry():
    """Return a list of registry consistency problems (empty when valid)."""
    problems = []
    for path_id, path in AUTH_PATHS.items():
        for profile in path.get("profiles", []):
            if profile not in PERMISSION_PROFILES:
                problems.append(f"auth path {path_id}: unknown profile {profile}")
        if path.get("kind") not in {"graph_app", "workload_app_token", "workload_app_certificate", "delegated_user"}:
            problems.append(f"auth path {path_id}: unknown kind {path.get('kind')}")
        if not path.get("unlock"):
            problems.append(f"auth path {path_id}: missing unlock guidance")
    for collector_id, collector in COLLECTOR_REGISTRY.items():
        graph_permissions = set(collector.get("permission_resources", {}).get("graph", []))
        for resource in collector.get("permission_resources", {}):
            if resource not in RESOURCE_APP_IDS:
                problems.append(f"collector {collector_id}: unknown resource {resource}")
        for resource in collector.get("delegated_permission_resources", {}):
            if resource not in RESOURCE_APP_IDS:
                problems.append(f"collector {collector_id}: unknown delegated resource {resource}")
        for dataset in collector_datasets(collector_id):
            for path_id in dataset.get("auth_paths", []):
                if path_id not in AUTH_PATHS:
                    problems.append(f"collector {collector_id} dataset {dataset.get('id')}: unknown auth path {path_id}")
                    continue
                path = AUTH_PATHS[path_id]
                required_graph = set(path.get("requires", {}).get("graph_roles", []))
                if required_graph and dataset.get("baseline") == path_id and not required_graph <= graph_permissions:
                    problems.append(f"collector {collector_id}: baseline {path_id} needs graph roles not in permission_resources")
            baseline = dataset.get("baseline")
            if baseline and baseline not in dataset.get("auth_paths", []):
                problems.append(f"collector {collector_id} dataset {dataset.get('id')}: baseline {baseline} is not an auth path")
    for profile_name, profile in PERMISSION_PROFILES.items():
        for collector_id in profile.get("excluded_collectors", []):
            if collector_id not in COLLECTOR_REGISTRY:
                problems.append(f"profile {profile_name}: unknown excluded collector {collector_id}")
    return problems

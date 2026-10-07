"""Resolve which authentication path each dataset uses, and record provenance.

Application permissions are the baseline and must stand on their own. Each
dataset lists its auth paths in order (higher coverage first, then app-only
before delegated, then least privilege). The resolver explains every path it
cannot use, so preflight and the report can say exactly how to unlock more
evidence. A delegated path is used only when no app-only path is eligible, or
as enrichment for data Microsoft exposes only to signed-in administrators.
"""

import os
import re
from datetime import datetime, timezone

from .collector_registry import (
    AUTH_PATHS,
    COLLECTOR_REGISTRY,
    PERMISSION_PROFILES,
    auth_path_label,
    collector_datasets,
    collector_permissions,
    credential_kind_label,
    normalize_permission_profile,
    source_allowed,
)
from . import console_reporting as console


AUTH_PLAN_VERSION = 1

# Reason codes for paths that cannot be used.
PROFILE_EXCLUDED = "profile_excluded"
PERMISSION_MISSING = "permission_missing"
WORKLOAD_ROLE_MISSING = "workload_role_missing"
CERTIFICATE_MISSING = "certificate_missing"
CREDENTIAL_MISSING = "credential_missing"
MODULE_MISSING = "module_missing"
MODULE_TOO_OLD = "module_too_old"
CONFIGURATION_MISSING = "configuration_missing"
INTERACTIVE_DISABLED = "interactive_disabled"
DELEGATED_OFF = "delegated_off"
DELEGATED_UNAVAILABLE = "delegated_unavailable"

_MODULE_ALIASES = {
    "Microsoft.Online.SharePoint.PowerShell": "Microsoft.Online.SharePoint.PowerShell",
    "ExchangeOnlineManagement": "ExchangeOnlineManagement",
    "Az.Accounts": "Az.Accounts",
}


def _version_tuple(value):
    parts = re.findall(r"\d+", str(value or ""))
    return tuple(int(part) for part in parts[:4]) if parts else ()


def certificate_configuration():
    """Which workload certificates are configured (never returns secret values)."""
    generic_pfx = os.environ.get("CERTIFICATE_PATH", "").lower().endswith((".pfx", ".p12"))
    return {
        prefix: bool(os.environ.get(f"{prefix}_CERTIFICATE_THUMBPRINT")
                     or os.environ.get(f"{prefix}_CERTIFICATE_PATH") or generic_pfx)
        for prefix in ("SHAREPOINT", "PURVIEW")
    }


def graph_credential_kind():
    if os.environ.get("CERTIFICATE_PATH"):
        return "certificate"
    if os.environ.get("CLIENT_SECRET"):
        return "client_secret"
    return ""


def build_auth_context(*, permission_profile="standard", interactive_auth="auto", delegated="auto",
                       modules=None, graph_roles=None, workload_roles=None, directory_roles=None,
                       sharepoint_admin_url="", purview_organization="", delegated_state=None):
    """Collect everything the resolver needs. ``None`` means "not known yet"."""
    profile = normalize_permission_profile(permission_profile)
    modules = modules or {}
    details = modules.get("module_details", {}) or {}
    module_state = {}
    for name in _MODULE_ALIASES:
        module_state[name] = {
            "available": bool(modules.get(name)),
            "version": str((details.get(name) or {}).get("version") or ""),
        }
    if delegated == "off" or not PERMISSION_PROFILES[profile].get("allow_delegated", True):
        delegated_state = "off"
    return {
        "profile": profile,
        "graph_credential": graph_credential_kind(),
        "graph_roles": set(graph_roles) if graph_roles is not None else None,
        "workload_roles": {key: (set(value) if value is not None else None)
                           for key, value in (workload_roles or {}).items()},
        "directory_roles": list(directory_roles) if directory_roles is not None else None,
        "certificates": certificate_configuration(),
        "modules": module_state,
        "configuration": {
            "SHAREPOINT_ADMIN_URL": bool(sharepoint_admin_url or os.environ.get("SHAREPOINT_ADMIN_URL")),
            "PURVIEW_ORGANIZATION": bool(purview_organization or os.environ.get("PURVIEW_ORGANIZATION")),
        },
        "interactive": interactive_auth != "skip",
        "delegated": delegated or "auto",
        "delegated_state": delegated_state or ("off" if delegated == "off" else "unknown"),
    }


def credential_kind_for_path(path_id, context):
    """Map an auth path to the credential kind recorded as evidence provenance."""
    path = AUTH_PATHS.get(path_id, {})
    kind = path.get("kind")
    if kind == "graph_app":
        return "graph_app_certificate" if (context or {}).get("graph_credential") == "certificate" else "graph_app_secret"
    return kind or "unrecorded"


def path_eligibility(path_id, context, collector_id=None):
    """Return (eligible, reason_code, reason) for one auth path."""
    path = AUTH_PATHS.get(path_id)
    if not path:
        return False, CONFIGURATION_MISSING, f"Unknown auth path {path_id}."
    requires = path.get("requires", {}) or {}
    profile = context["profile"]
    if profile not in path.get("profiles", []):
        return False, PROFILE_EXCLUDED, f"The {profile} permission profile does not use this path."
    kind = path.get("kind")

    if kind in {"graph_app", "workload_app_token", "workload_app_certificate"} and not context.get("graph_credential"):
        return False, CREDENTIAL_MISSING, "No application credential (client secret or certificate) is configured."

    if kind == "graph_app":
        roles = context.get("graph_roles")
        required = set(requires.get("graph_roles", []))
        if not required and collector_id and path_id == "graph_app":
            required = collector_permissions(collector_id, profile)
        if roles is not None and required - roles:
            return False, PERMISSION_MISSING, "Grant and consent: " + ", ".join(sorted(required - roles))

    for resource, required in (requires.get("workload_roles") or {}).items():
        if kind == "workload_app_certificate":
            # Certificate paths are verified by the representative read; the
            # SharePoint token audience depends on the tenant host name.
            continue
        roles = (context.get("workload_roles") or {}).get(resource)
        if roles is not None and set(required) - roles:
            return False, WORKLOAD_ROLE_MISSING, (
                f"Grant and consent {', '.join(sorted(set(required) - roles))} on the {resource} API.")

    certificate = requires.get("certificate")
    if certificate and not context.get("certificates", {}).get(certificate):
        return False, CERTIFICATE_MISSING, f"No {certificate.lower()} application certificate is configured."

    for requirement in requires.get("modules", []):
        name, _, minimum = requirement.partition(">=")
        name = name.strip()
        state = context.get("modules", {}).get(name, {})
        if not state.get("available"):
            return False, MODULE_MISSING, f"Install or update {name}."
        if minimum and state.get("version") and _version_tuple(state["version"]) < _version_tuple(minimum):
            return False, MODULE_TOO_OLD, f"{name} {state['version']} is installed; {minimum.strip()} or later is required."

    for setting in requires.get("configuration", []):
        if not context.get("configuration", {}).get(setting):
            return False, CONFIGURATION_MISSING, f"Configure {setting}."

    if requires.get("interactive") and not context.get("interactive"):
        return False, INTERACTIVE_DISABLED, "Browser sign-in is disabled for this run (--interactive-auth skip)."

    if requires.get("delegated") or kind == "delegated_user":
        if kind == "delegated_user" and path_id == "graph_delegated":
            state = context.get("delegated_state")
            if state == "off":
                return False, DELEGATED_OFF, "Delegated sign-in is off (--delegated off or the Restricted profile)."
            if state == "unavailable":
                return False, DELEGATED_UNAVAILABLE, "No cached sign-in and no interactive terminal for a delegated sign-in."
    return True, "", ""


def resolve_auth_plan(collector_ids, context):
    """Resolve the ordered auth paths for every dataset of the selected collectors."""
    datasets = {}
    for collector_id in collector_ids:
        if collector_id not in COLLECTOR_REGISTRY:
            continue
        excluded = not source_allowed(collector_id, context["profile"])
        for dataset in collector_datasets(collector_id):
            dataset_id = dataset["id"]
            entry = {
                "collector": collector_id,
                "baseline": dataset.get("baseline", ""),
                "selected_path": "",
                "kind": "",
                "credential_type": "",
                "coverage": "",
                "evidence_quality": "",
                "fallbacks": [],
                "skipped": [],
            }
            eligible = []
            for path_id in dataset.get("auth_paths", []):
                if excluded:
                    ok, code, reason = False, PROFILE_EXCLUDED, "Not requested by the selected permission profile."
                else:
                    ok, code, reason = path_eligibility(path_id, context, collector_id)
                if ok:
                    eligible.append(path_id)
                else:
                    entry["skipped"].append({
                        "path": path_id, "label": auth_path_label(path_id), "reason_code": code,
                        "reason": reason, "unlock": AUTH_PATHS.get(path_id, {}).get("unlock", ""),
                    })
            if eligible:
                primary = eligible[0]
                path = AUTH_PATHS[primary]
                entry.update(
                    selected_path=primary, kind=path.get("kind", ""),
                    credential_type=credential_kind_for_path(primary, context),
                    coverage=path.get("coverage", ""), evidence_quality=path.get("evidence_quality", ""),
                    fallbacks=eligible[1:],
                )
            entry["baseline_available"] = bool(entry["baseline"]) and entry["baseline"] in eligible
            datasets[dataset_id] = entry
    return {
        "version": AUTH_PLAN_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile": context["profile"],
        "identities": _identities(context),
        "datasets": datasets,
    }


def _identities(context):
    identities = []
    if context.get("graph_credential"):
        identities.append({
            "id": "app", "type": "service_principal",
            "client_id": os.environ.get("CLIENT_ID", ""),
            "credential_type": "certificate" if context["graph_credential"] == "certificate" else "client_secret",
            "directory_roles": context.get("directory_roles") or [],
        })
    if context.get("delegated_state") in {"signed_in", "cached"} and context.get("delegated_identity"):
        identities.append({"id": "delegated", "type": "user", **context["delegated_identity"]})
    return identities


_SPECIFIC_REASONS = {PERMISSION_MISSING, WORKLOAD_ROLE_MISSING, MODULE_MISSING, MODULE_TOO_OLD, CONFIGURATION_MISSING}


def _unlock_text(skipped):
    """Prefer the specific missing item (for example a permission name) over generic guidance."""
    code, reason, unlock = skipped.get("reason_code"), skipped.get("reason", ""), skipped.get("unlock", "")
    if code == PERMISSION_MISSING and reason:
        return f"{reason.rstrip('.')}. Rerun setup-service-principal.ps1 and grant admin consent."
    if code == WORKLOAD_ROLE_MISSING and unlock:
        return unlock
    if code in _SPECIFIC_REASONS and reason:
        return f"{reason} {unlock}".strip()
    return unlock or reason


def next_better_option(entry):
    """The most useful unlock step for a dataset that is limited or unavailable."""
    selected = entry.get("selected_path")
    skipped = [item for item in entry.get("skipped", []) if item.get("reason_code") != PROFILE_EXCLUDED]
    if not selected:
        # The least-privilege baseline is the first thing to fix when nothing works.
        baseline = next((item for item in skipped if item.get("path") == entry.get("baseline")), None)
        choice = baseline or next((item for item in skipped if AUTH_PATHS.get(item["path"], {}).get("kind") != "delegated_user"), None)             or (skipped[0] if skipped else None)
        return _unlock_text(choice) if choice else ""
    if AUTH_PATHS.get(selected, {}).get("coverage") == "full":
        return ""
    # A partial path is in use; name the next path that gives full coverage.
    for item in skipped:
        if AUTH_PATHS.get(item["path"], {}).get("coverage") == "full":
            return _unlock_text(item)
    return ""


def plan_rows(plan):
    rows = []
    for dataset_id, entry in (plan or {}).get("datasets", {}).items():
        selected = entry.get("selected_path")
        rows.append({
            "Dataset": dataset_id,
            "Collector": COLLECTOR_REGISTRY.get(entry.get("collector"), {}).get("name", entry.get("collector", "")),
            "Will use": auth_path_label(selected) if selected else "Not available",
            "Identity": credential_kind_label(entry.get("credential_type")) if selected else "",
            "Coverage": entry.get("coverage") or ("none" if not selected else ""),
            "Evidence quality": entry.get("evidence_quality", ""),
            "Fallbacks": ", ".join(auth_path_label(path) for path in entry.get("fallbacks", [])),
            "Next better option": next_better_option(entry),
        })
    return rows


def print_auth_plan(plan, detailed=True):
    rows = plan_rows(plan)
    if not rows:
        return
    if not detailed:
        limited = [row for row in rows if row["Will use"] == "Not available" or row["Coverage"] == "partial"]
        if limited:
            console.status(f"Evidence plan: {len(limited)} dataset(s) are limited or unavailable with the current access. "
                           "Run --check-connections for unlock steps.", tone='warning')
        return
    console.section('Evidence collection plan')
    console.status('Application permissions are used first; delegated sign-in only adds data Microsoft does not expose to applications.')
    width = max(len(row["Dataset"]) for row in rows)
    for row in rows:
        detail = f"{row['Will use']}"
        if row["Will use"] != "Not available" and row["Coverage"] and row["Coverage"] != "full":
            detail += f" ({row['Coverage']} coverage)"
        if row["Will use"] != "Not available" and row["Evidence quality"] not in {"", "standard"}:
            detail += f" [{row['Evidence quality']}]"
        if row["Will use"] != "Not available" and row["Fallbacks"]:
            detail += f"; fallback: {row['Fallbacks']}"
        message = f"{row['Dataset']:<{width}}  {detail}"
        tone = 'success' if row["Coverage"] == "full" else 'warning'
        console.status(message, tone=tone)
        if row["Next better option"]:
            console.status(f"{'':<{width}}  To improve: {row['Next better option']}", tone='warning')


# ---------------------------------------------------------------------------
# Provenance on dataset states (collection_status entries)
# ---------------------------------------------------------------------------

LEGACY_AUTHENTICATION = {
    "application_certificate": "workload_app_certificate",
    "delegated_browser": "delegated_user",
    "application_token": "workload_app_token",
}

_PROVENANCE_FIELDS = ("auth_path", "credential_type", "coverage", "evidence_quality", "unlock")


def _dataset_for_source(name):
    """Map a source-status key to (dataset id in the plan, default auth path)."""
    key = str(name or "")
    if key.startswith(("connection_", "pipeline_")):
        return None, None
    if key.startswith("data_exposure_") or key in {"copilot_dashboard"}:
        return None, "offline_import"
    if key.startswith("defender_machines"):
        return "defender_machines", "defender_app"
    if key in {"sharepoint_tenant_settings_graph"}:
        return "sharepoint_tenant_settings", "graph_sharepoint_settings"
    if key.startswith("sharepoint_tenant"):
        return "sharepoint_tenant_settings", None
    if key.startswith("sharepoint_"):
        return "sharepoint_site_settings", None
    if key in {"purview_sensitivity_labels_graph"}:
        return "sensitivity_labels", "graph_sensitivity_labels"
    if key.startswith("purview_"):
        return "purview_policies", None
    if key in {"m365_report_settings", "report_settings"}:
        return "report_settings", "graph_report_settings"
    if key in {"copilot_limited_mode", "m365_copilot_limited_mode"}:
        return "copilot_limited_mode", "graph_delegated"
    if key in {"copilot_interaction_audit", "m365_copilot_interaction_audit"}:
        return "copilot_interaction_audit", "graph_audit_query"
    return None, "graph_app"


def annotate_source_statuses(source_statuses, auth_plan=None, defaults=None):
    """Fill provenance on dataset states without overwriting recorded values.

    States from older collections, or collected before provenance existed,
    receive credential_type ``unrecorded`` unless the saved auth plan
    identifies the identity that was used. ``defaults`` maps a source key to
    a credential type recorded by a collector payload (for example the
    SharePoint or Purview PowerShell ``authentication`` field).
    """
    defaults = defaults or {}
    plan_datasets = (auth_plan or {}).get("datasets", {})
    identities = (auth_plan or {}).get("identities", [])
    app_credential = next((item.get("credential_type") for item in identities if item.get("id") == "app"), "")
    for name, state in (source_statuses or {}).items():
        if not isinstance(state, dict):
            continue
        dataset_id, default_path = _dataset_for_source(name)
        if dataset_id is None and default_path is None:
            continue
        recorded = state.get("auth_path") or ""
        recorded_kind = recorded if recorded and recorded not in AUTH_PATHS else ""
        legacy = (LEGACY_AUTHENTICATION.get(str(state.get("authentication") or ""))
                  or recorded_kind or defaults.get(name))
        path_id = state.get("auth_path_id") or (state.get("auth_path") if state.get("auth_path") in AUTH_PATHS else "")
        if not path_id and dataset_id and plan_datasets.get(dataset_id, {}).get("selected_path") and not legacy \
                and default_path is None:
            path_id = plan_datasets[dataset_id]["selected_path"]
        if not path_id and default_path and default_path != "offline_import":
            path_id = default_path
        credential_type = state.get("credential_type") or legacy
        if not credential_type:
            if default_path == "offline_import" or state.get("source_type") in {"purview_cache", "portal_export", "prior_assessment"}:
                credential_type = "offline_import"
            elif path_id and AUTH_PATHS.get(path_id, {}).get("kind") == "graph_app":
                credential_type = ("graph_app_certificate" if app_credential == "certificate"
                                   else "graph_app_secret" if app_credential else "unrecorded")
            elif path_id:
                credential_type = AUTH_PATHS.get(path_id, {}).get("kind", "unrecorded")
            else:
                credential_type = "unrecorded"
        state.setdefault("credential_type", credential_type)
        if path_id:
            state.setdefault("auth_path_id", path_id)
            path = AUTH_PATHS.get(path_id, {})
            state.setdefault("coverage", path.get("coverage", ""))
            state.setdefault("evidence_quality", path.get("evidence_quality", ""))
        if dataset_id and not state.get("available", state.get("availability_status") == "available") and not state.get("unlock"):
            entry = plan_datasets.get(dataset_id) or {}
            unlock = next_better_option(entry) if entry else ""
            if not unlock and path_id:
                unlock = AUTH_PATHS.get(path_id, {}).get("unlock", "")
            if unlock:
                state["unlock"] = unlock
    return source_statuses


def collected_with_label(state):
    """Short human-readable provenance for reports."""
    if not isinstance(state, dict) or not state:
        return ""
    kind = state.get("credential_type") or LEGACY_AUTHENTICATION.get(str(state.get("authentication") or "")) or "unrecorded"
    label = credential_kind_label(kind)
    path_id = state.get("auth_path_id")
    if path_id and path_id in AUTH_PATHS and AUTH_PATHS[path_id].get("kind") != "graph_app":
        label = auth_path_label(path_id)
    quality = state.get("evidence_quality")
    if quality and quality not in {"standard"}:
        label += f" [{quality}]"
    if state.get("coverage") == "partial":
        label += " (partial coverage)"
    return label

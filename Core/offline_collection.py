"""Portable, data-only tenant collections for reports that require no tenant connection."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

COLLECTION_FORMAT = "m365-readiness-collection"
COLLECTION_VERSION = 2
SERVICE_KEYS = ("m365_result", "entra_info", "purview_info", "defender_info",
                "power_platform_info", "copilot_studio_info")
_DATA_CLASSES = {"SimpleNamespace", "M365Client", "EntraClient", "PurviewClient",
                 "DefenderClient", "PowerPlatformClient", "PowerPlatformData",
                 "PowerPlatformInventoryData", "CopilotStudioClient"}
_LEGACY_PP_FIELDS = {
    "available", "environment_name", "environments", "environment_summary",
    "flows", "flow_summary", "apps", "app_summary", "connections", "connection_summary",
    "ai_models", "ai_model_summary", "dlp_policies", "dlp_summary", "capacity",
    "capacity_summary", "solutions", "solution_summary", "agents", "permission_failures",
    "power_platform_inventory", "collection_status",
}
_PRIVATE_FIELDS = {"clientsecret", "clientassertion", "secrettext", "password",
                   "certificatepassword", "privatekey", "accesstoken", "refreshtoken",
                   "idtoken", "credential", "credentials", "token", "authorization",
                   "headers", "graphclient", "httpclient", "session", "requestadapter"}


class CollectionPackagingError(ValueError):
    """Tenant evidence was saved successfully, but supplemental packaging failed."""

    def __init__(self, collection_path, cause):
        self.collection_path = str(Path(collection_path).resolve())
        super().__init__(f'Tenant evidence is saved, but packaging failed: {cause}')


def _safe_key(key):
    return re.sub(r"[^a-z0-9]", "", str(key).lower()) not in _PRIVATE_FIELDS


def _encode(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return re.sub(r"(?i)\bBearer\s+\S+", "Bearer [redacted]", value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return _encode(value.value)
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()
                if _safe_key(key) and (not str(key).startswith("_") or key == "_client")}
    if isinstance(value, (list, tuple, set)):
        return [_encode(item) for item in value]
    if type(value).__name__ in _DATA_CLASSES:
        return {"$collection_attributes": {
            key: _encode(item) for key, item in vars(value).items()
            if not key.startswith("_") and _safe_key(key) and not callable(item)
        }}
    raise ValueError(f"Cannot save non-data object of type {type(value).__name__} in a collection.")


def _decode(value):
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if isinstance(value, dict):
        if set(value) == {"$collection_attributes"}:
            attributes = value["$collection_attributes"]
            if not isinstance(attributes, dict):
                raise ValueError("Invalid collection attributes.")
            return SimpleNamespace(**{key: _decode(item) for key, item in attributes.items()
                                      if not key.startswith("_") and _safe_key(key)})
        return {key: _decode(item) for key, item in value.items() if _safe_key(key)}
    return value


def save_collection(path=None, *, tenant_id, tenant_name, service_results,
                    enabled_collectors=None, connection_results=None, collected_at=None,
                    assessment_settings=None, supplemental_inputs=None, evaluation_date=None):
    """Save evidence, defaulting to a distinct tenant/date file under output/collections."""
    results = {key: service_results[key] for key in SERVICE_KEYS}
    # The legacy Power Platform collector attaches evidence to its HTTP transport.
    # Copy only its named evidence fields; never serialize transport configuration.
    for key in ("power_platform_info", "copilot_studio_info"):
        info = results[key]
        client = info.get("_client") if isinstance(info, dict) else None
        if type(client).__name__ == "AsyncClient" and type(client).__module__.split(".")[0] == "httpx":
            results[key] = dict(info)
            results[key]["_client"] = SimpleNamespace(**{
                name: item for name, item in vars(client).items() if name in _LEGACY_PP_FIELDS
            })
    payload = {
        "format": COLLECTION_FORMAT, "version": COLLECTION_VERSION,
        "tenant_id": tenant_id, "tenant_name": tenant_name,
        "collected_at": collected_at or datetime.now(timezone.utc).isoformat(),
        "enabled_collectors": enabled_collectors or [],
        "connection_results": connection_results or [],
        "service_results": results,
    }
    from .assessment_package import SETTING_KEYS, evaluation_day
    from .cross_provider_assessment import METHODOLOGY_VERSION, ASSESSMENT_VERSION
    payload['methodology_version'] = METHODOLOGY_VERSION
    payload['assessment_version'] = ASSESSMENT_VERSION
    payload['evaluation_date'] = evaluation_day(evaluation_date)
    payload['assessment_settings'] = {key: value for key, value in (assessment_settings or {}).items()
                                      if key in SETTING_KEYS}
    encoded = _encode(payload)
    if path:
        target = Path(path)
    else:
        tenant_slug = re.sub(r"[^A-Za-z0-9._-]+", "_", str(tenant_name or tenant_id or "tenant"))
        tenant_slug = tenant_slug.strip("._-").lower()[:40] or "tenant"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = Path("output") / "collections" / f"tenant-collection_{tenant_slug}_{timestamp}_{uuid4().hex[:8]}.json"
    encoded['source_file'] = target.name
    target.parent.mkdir(parents=True, exist_ok=True)
    # Preserve the collected facts even if copying a supplemental file fails.
    temporary = target.with_name(target.name + '.tmp')
    temporary.write_text(json.dumps(encoded, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(target)
    from .assessment_package import package_inputs, replace_source_paths
    package_folder = target.with_name(target.stem + '_package')
    # A custom save destination may be reused; preserve its earlier original files
    # and deliverables by giving the new package a distinct companion directory.
    if package_folder.exists():
        package_folder = target.with_name(target.stem + '_package_' + uuid4().hex[:8])
    try:
        manifest, references = package_inputs(package_folder, supplemental_inputs)
        encoded = replace_source_paths(encoded, references)
        encoded['package'] = dict(manifest, directory='.')
        (package_folder / 'collection.json').write_text(
            json.dumps(encoded, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        encoded['package']['directory'] = package_folder.name
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(json.dumps(encoded, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(target)
    except (ValueError, OSError) as exc:
        raise CollectionPackagingError(target, exc) from exc
    return str(target.resolve())


def load_collection(path):
    """Load JSON data only; never deserialize Python classes or open a connection."""
    with Path(path).open(encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    from .assessment_package import REBUILD_FORMAT, load_rebuild_recipe
    if isinstance(payload, dict) and payload.get('format') == REBUILD_FORMAT:
        return load_rebuild_recipe(path, payload)
    if not isinstance(payload, dict) or payload.get("format") != COLLECTION_FORMAT:
        raise ValueError("Use a collection saved by a live assessment (automatically or with --save-collection); an assessment snapshot or workbook is not a replayable collection.")
    if payload.get("version") not in {1, COLLECTION_VERSION}:
        raise ValueError("Unsupported collection version.")
    if payload.get('version') == COLLECTION_VERSION:
        from .assessment_package import methodology_metadata
        methodology_metadata(payload, 'Collection')
    results = payload.get("service_results", {})
    if not isinstance(results, dict) or any(key not in results for key in SERVICE_KEYS):
        raise ValueError("Collection is missing service results.")
    if not isinstance(results["m365_result"], list) or len(results["m365_result"]) != 2:
        raise ValueError("Collection contains invalid M365 results.")
    for key in SERVICE_KEYS[1:]:
        if not isinstance(results[key], dict):
            raise ValueError(f"Collection contains invalid {key}.")
    try:
        timestamp = datetime.fromisoformat(str(payload["collected_at"]).replace("Z", "+00:00"))
        if timestamp.tzinfo is None or timestamp > datetime.now(timezone.utc):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError("Collection requires a valid, non-future UTC collection timestamp.") from None
    if payload.get('version') == 1:
        for key in SERVICE_KEYS:
            rows = results[key][1] if key == 'm365_result' else results[key].get('recommendations', [])
            for row in rows:
                if isinstance(row, dict):
                    row.update(SourceType='prior_assessment', SourceFile=Path(path).name,
                               PriorReportDate=payload['collected_at'],
                               OriginalMethodologyVersion=payload.get('methodology_version', 'unversioned'),
                               EvidenceComplete=False)
    from .assessment_package import resolve_package
    return _decode(resolve_package(payload, path))


def empty_service_results():
    """Portal-only review never claims coverage of uncollected tenant controls."""
    return {"m365_result": [{}, []], **{
        key: {"available": False, "recommendations": []} for key in SERVICE_KEYS[1:]
    }}


def refresh_saved_freshness(results, evaluation_date=None):
    """A later render must not retain the original 'Fresh' usage-data badge."""
    from .assessment_package import evaluation_day
    evaluated = date.fromisoformat(evaluation_day(evaluation_date))
    m365_info = results['m365_result'][0]
    client = m365_info.get('_client') if isinstance(m365_info, dict) else None
    for name in ('copilot_usage', 'm365_app_readiness', 'copilot_dashboard', 'shadow_ai_usage'):
        evidence = getattr(client, name, None)
        if not isinstance(evidence, dict) or not evidence.get('available'):
            continue
        try:
            refreshed = datetime.fromisoformat(str(evidence.get('refresh_date', '')).replace('Z', '+00:00'))
            if refreshed.tzinfo is None:
                refreshed = refreshed.replace(tzinfo=timezone.utc)
            age = (evaluated - refreshed.date()).days
            if age < 0:
                raise ValueError()
            evidence.update(age_days=age, stale=age > 7, freshness='Stale' if age > 7 else 'Fresh')
        except (ValueError, TypeError):
            evidence.update(age_days=None, stale=False, freshness='Unknown')


def collection_context(payload=None, source_file=None, evaluation_date=None, mode='offline'):
    from .assessment_package import evaluation_day
    evaluated = evaluation_day(evaluation_date or (payload or {}).get('evaluation_date'))
    timestamp = (payload or {}).get("collected_at", "")
    age = None
    if timestamp:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        age = (date.fromisoformat(evaluated) - parsed.date()).days
    source_name = (payload or {}).get('source_file') or (Path(source_file).name if source_file else "")
    if payload and payload.get('has_tenant_collection') is False:
        source_name = ''
    return {"mode": mode, "source_file": source_name,
            "collected_at": timestamp, "age_days": age,
            "evaluation_date": evaluated,
            "package_directory": (payload or {}).get('package_directory'),
            "collection_version": (payload or {}).get('version'),
            "collected_methodology_version": (payload or {}).get('methodology_version'),
            "original_methodology_version": (payload or {}).get('original_methodology_version', (payload or {}).get('methodology_version')),
            "effective_methodology_version": (payload or {}).get('effective_methodology_version'),
            "methodology_migration": (payload or {}).get('methodology_migration'),
            "assessment_settings": (payload or {}).get('assessment_settings', {}),
            "freshness": "missing" if age is None else "unknown" if age < 0 else "stale" if age > 35 else "current",
            "scope": "Saved tenant collection plus supplied reports" if payload and payload.get('has_tenant_collection', True) else "Portal exports and historical inputs only"}

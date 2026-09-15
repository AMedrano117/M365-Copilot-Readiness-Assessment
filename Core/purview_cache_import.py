"""Read the existing Purview cache as dated, explicitly selected offline evidence.

This module never invokes the Purview collector or reads its runtime environment.
The cache contains configuration evidence, not a full tenant collection or a
license inventory. Its policy objects remain available to the evidence workbook.
"""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from uuid import UUID

from .get_purview_client import hydrate_purview_client
from .new_recommendation import new_recommendation


_COLLECTIONS = {
    "dlp_policies": ("policies", "DLP policies"),
    "dlp_rules": ("rules", "DLP rules"),
    "sensitivity_labels": ("labels", "sensitivity labels"),
    "retention_policies": ("policies", "retention policies"),
    "label_policies": ("policies", "label publishing policies"),
    "insider_risk_policies": ("policies", "insider risk policies"),
    "communication_compliance": ("policies", "communication compliance policies"),
    "information_barriers": ("policies", "information barrier policies"),
    "ediscovery_cases": ("cases", "eDiscovery cases"),
}
_OBJECTS = {"org_config", "irm_config", "audit_config"}


def _validate_payload(data):
    if not isinstance(data, dict) or not (set(data) & (_COLLECTIONS.keys() | _OBJECTS)):
        raise ValueError("Purview cache payload contains no recognized configuration sections.")
    for key in set(data) & (_COLLECTIONS.keys() | _OBJECTS):
        section = data[key]
        if not isinstance(section, dict):
            raise ValueError(f"Purview cache section {key} must be an object.")
        for flag in ("available", "optional", "permission_denied"):
            if flag in section and not isinstance(section[flag], bool):
                raise ValueError(f"Purview cache {key}.{flag} must be a boolean.")
        if "count" in section:
            count = section["count"]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError(f"Purview cache {key}.count must be a nonnegative integer.")
        if key in _COLLECTIONS:
            nested_key = _COLLECTIONS[key][0]
            records = section.get(nested_key, [])
            if isinstance(records, dict):
                records = [records]
            elif records in (None, ""):
                records = []
            if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
                raise ValueError(f"Purview cache {key}.{nested_key} must contain objects.")
            if section.get("available") and nested_key not in section:
                raise ValueError(f"Available Purview cache section {key} is missing {nested_key}.")
        elif "data" in section and section["data"] is not None and not isinstance(section["data"], dict):
            raise ValueError(f"Purview cache {key}.data must be an object.")


def load_purview_cache(path):
    """Load schema-v2/v3 Purview configuration without authenticating or collecting.

    Returns tenant identity, original collection timestamp, source provenance,
    and the service_info shape consumed by the normal evidence/report pipeline.
    """
    source_path = Path(path)
    try:
        document = json.loads(source_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError("Purview cache is not valid JSON.") from exc
    if not isinstance(document, dict) or document.get("schema_version") not in {2, 3}:
        raise ValueError("Purview cache must use schema_version 2 or 3.")
    try:
        tenant_id = str(UUID(str(document.get("tenant_id", ""))))
    except (ValueError, AttributeError) as exc:
        raise ValueError("Purview cache must contain a valid tenant GUID.") from exc
    epoch = document.get("cached_at_epoch")
    if isinstance(epoch, bool) or not isinstance(epoch, (int, float)) or not math.isfinite(epoch):
        raise ValueError("Purview cache must contain a valid cached_at_epoch timestamp.")
    try:
        collected = datetime.fromtimestamp(epoch, timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("Purview cache timestamp is outside the supported range.") from exc
    now = datetime.now(timezone.utc)
    if epoch <= 0 or collected > now:
        raise ValueError("Purview cache timestamp must be in the past.")
    raw_payload = document.get("purview_data_json")
    if not isinstance(raw_payload, str):
        raise ValueError("Purview cache must contain the purview_data_json string.")
    try:
        data = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise ValueError("Purview cache configuration is not valid JSON.") from exc
    _validate_payload(data)

    client = hydrate_purview_client(data)
    age_days = (now - collected).days
    provenance = {
        "source_file": source_path.name,
        "collected_at": collected.isoformat(),
        "refresh_date": collected.date().isoformat(),
        "age_days": age_days,
        "freshness": "Stale" if age_days > 35 else "Historical cache",
        "source_type": "purview_cache",
    }
    client.cache_provenance = dict(provenance)
    for state in client.collection_status.values():
        state.update(provenance)

    counts = []
    for key, (nested_key, label) in _COLLECTIONS.items():
        if not client.collection_status[key].get("available"):
            continue
        records = data[key].get(nested_key) or []
        counts.append(f"{1 if isinstance(records, dict) else len(records)} {label}")
    available_sources = sum(bool(state.get("available")) for state in client.collection_status.values())
    description = ", ".join(counts) or "configuration settings"
    observation = (
        f"The saved Purview collection from {collected.isoformat()} contains {description}. "
        f"{available_sources} of {len(client.collection_status)} configuration sources were available at collection time. "
        "This is historical configuration evidence; licenses and current tenant state were not rechecked."
    )
    if age_days > 35:
        observation += f" The cache is {age_days} days old and is marked stale."
    recommendation = new_recommendation(
        service="Purview", feature="Cached Purview configuration", observation=observation,
        status="Imported", disposition="Reference", finding_key="purview.cached_configuration",
        evidence_key="purview_policy_detail",
        evidence_summary=f"Policies and labels from {source_path.name}, collected {collected.isoformat()}.",
        evidence_basis="Tenant evidence", confidence="High",
        impact_area="Data protection & compliance", ai_applicability="All AI using M365 data",
    )
    return {
        "tenant_id": tenant_id,
        "source_file": source_path.name,
        "collected_at": collected.isoformat(),
        "provenance": provenance,
        "service_info": {
            "_client": client, "recommendations": [recommendation],
            "available": available_sources > 0,
            "cache_provenance": provenance,
        },
    }

"""Versioned, deterministic evidence facts and conservative snapshot selection.

This module has no collectors or rendering dependencies. A missing measurement is
None; availability explains why. Dates belong to observations, never report builds.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json


EVIDENCE_SCHEMA_VERSION = "1.0.0"
RECONCILIATION_VERSION = "1.0.0"
AVAILABILITY_STATES = frozenset({
    "available", "missing", "not_requested", "inaccessible", "unsupported",
    "empty", "unknown", "partial", "unavailable", "not_applicable",
})


def parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).strip().replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        return None


def evaluation_day(value=None):
    if value is None:
        return datetime.now(timezone.utc).date()
    parsed = parse_date(value)
    if parsed is None:
        raise ValueError("Evaluation date must be an ISO date or timestamp.")
    return parsed


def stable_id(value):
    encoded = json.dumps(value, sort_keys=True, default=str, ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def normalize_observation(source, *, evaluation_date=None, expected_tenant_id=None):
    """Normalize one fact without interpreting absence as zero or inventing scope."""
    row = deepcopy(source)
    tenant = str(row.get("tenant_id") or "").lower()
    expected = str(expected_tenant_id or "").lower()
    if tenant and expected and tenant != expected:
        raise ValueError("Evidence tenant ID does not match the assessed tenant.")
    availability = str(row.get("availability") or "unknown").lower()
    availability = {"permission_denied": "inaccessible", "error": "unavailable",
                    "not_supplied": "missing"}.get(availability, availability)
    if availability not in AVAILABILITY_STATES:
        availability = "unknown"
    value = row.get("value")
    if availability not in {"available", "partial"}:
        value = None
    elif value is None and availability == "available":
        availability = "unknown"
    observed = parse_date(row.get("observed_at"))
    age = (evaluation_day(evaluation_date) - observed).days if observed else None
    max_age = int(row.get("max_age_days", 35))
    freshness = "unknown" if age is None else "future" if age < 0 else "stale" if age > max_age else "current"
    complete = row.get("complete") is True and not row.get("truncated", False) and availability != "partial"
    qualifiers = list(row.get("qualifications") or [])
    if not tenant:
        qualifiers.append("Tenant identity is not recorded in this source.")
    if not row.get("scope"):
        qualifiers.append("The assessed population is not recorded.")
    if freshness == "unknown":
        qualifiers.append("The original observation date is not recorded.")
    elif freshness == "future":
        qualifiers.append("The observation date is after the evaluation date; confirm the source date.")
    elif freshness == "stale":
        qualifiers.append(f"The observation is {age} days old and requires confirmation.")
    if not complete:
        qualifiers.append("Completeness is not established for the stated population.")
    row.update({
        "schema_version": EVIDENCE_SCHEMA_VERSION, "tenant_id": tenant,
        "domain_id": row.get("domain_id", ""), "control_id": row.get("control_id", ""),
        "metric_id": row.get("metric_id", ""), "metric_definition": row.get("metric_definition", row.get("metric_id", "")),
        "population": row.get("population", ""), "scope": row.get("scope", ""),
        "affected_objects": sorted(set(str(item) for item in row.get("affected_objects", []) or [])),
        "value": value, "unit": row.get("unit", ""), "numerator": row.get("numerator"),
        "denominator": row.get("denominator"), "window": row.get("window", ""),
        "reporting_basis": row.get("reporting_basis", "snapshot"),
        "observed_at": observed.isoformat() if observed else "", "age_days": age,
        "freshness": freshness, "availability": availability, "complete": complete,
        "truncated": bool(row.get("truncated")), "source_type": row.get("source_type", "unknown"),
        "source_file": row.get("source_file", ""), "source_schema": row.get("source_schema", ""),
        "source_hash": row.get("source_hash", ""), "selection": "pending",
        "qualifications": list(dict.fromkeys(qualifiers)),
    })
    row["qualification"] = " ".join(row["qualifications"])
    row["evidence_id"] = row.get("evidence_id") or "EV-" + stable_id({
        key: value for key, value in row.items()
        if key not in {"age_days", "freshness", "qualifications", "qualification", "selection"}
    })
    return row


def compatibility_key(row):
    """Only equal metric definitions, populations, units and windows are comparable.

    Unknown population or tenant is deliberately source-local: an undated export
    cannot silently replace a known whole-tenant collection.
    """
    scope = row.get("scope") or {"unknown_source": row.get("source_file") or row.get("evidence_id")}
    tenant = row.get("tenant_id") or {"unknown_source": row.get("source_file") or row.get("evidence_id")}
    return stable_id([tenant, row.get("control_id"), row.get("metric_id"),
                      row.get("metric_definition"), scope, row.get("population"),
                      row.get("affected_objects"), row.get("unit"),
                      row.get("reporting_basis"), row.get("window")])


def _comparison_value(value):
    """Compare measured numbers by value while preserving their source encoding.

    JSON's 20 and 20.0 encode the same measurement. Booleans and strings remain
    distinct, and decimal conversion avoids rounding large integers to floats.
    """
    if type(value) in {int, float}:
        number = Decimal(str(value))
        if number.is_finite():
            return ("number", number)
    return ("value", stable_id(value))


def reconcile_observations(observations, *, evaluation_date=None, expected_tenant_id=None):
    """Retain every source and select one suitable fact per compatible question.

    Complete evidence outranks a newer partial snapshot. Different values for the
    same date (or for dates that cannot be ordered) remain explicit conflicts.
    Repeated exports are duplicates, never totals to be added together.
    """
    rows = [normalize_observation(row, evaluation_date=evaluation_date,
                                  expected_tenant_id=expected_tenant_id) for row in observations]
    groups = defaultdict(list)
    for row in rows:
        groups[compatibility_key(row)].append(row)
    for group in groups.values():
        candidates = [row for row in group if row["availability"] in {"available", "partial"}
                      and row["value"] is not None and row["freshness"] != "future"]
        if not candidates:
            for row in group:
                row["selection"] = "unavailable"
            continue
        complete = [row for row in candidates if row["complete"]]
        pool = complete or candidates
        dated = [row for row in pool if row["observed_at"]]
        latest = max((row["observed_at"] for row in dated), default="")
        finalists = [row for row in pool if not row["observed_at"] or row["observed_at"] == latest]
        values = {_comparison_value(row["value"]) for row in finalists}
        if len(values) > 1:
            for row in group:
                row["selection"] = "conflict" if row in finalists else "superseded"
                if row in finalists:
                    row["qualification"] = (row["qualification"] + " Compatible sources disagree; confirm the value before use.").strip()
            continue
        winner = min(finalists, key=lambda row: (not bool(row["observed_at"]), row["source_file"], row["evidence_id"]))
        for row in group:
            if row is winner:
                row["selection"] = "selected"
            elif (_comparison_value(row["value"]) == _comparison_value(winner["value"]) and row["observed_at"] == winner["observed_at"]
                  and row["availability"] == winner["availability"] and row["complete"] == winner["complete"]):
                row["selection"] = "duplicate"
                row["selected_evidence_id"] = winner["evidence_id"]
            else:
                row["selection"] = "superseded"
                row["selected_evidence_id"] = winner["evidence_id"]
                if not row["complete"] and winner["complete"]:
                    row["qualification"] = (row["qualification"] + " A complete comparable source was retained.").strip()
    return sorted(rows, key=lambda row: (row["domain_id"], row["metric_id"], compatibility_key(row), row["observed_at"], row["evidence_id"]))

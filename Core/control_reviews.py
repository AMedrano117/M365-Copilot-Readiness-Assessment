"""Validate dated operator attestations supplied in an assessment profile.

An attestation records who reviewed a defined population and which evidence they
used. It is distinct from collected facts and cannot silently resolve an observed
failure or a historical finding. No files, credentials or network are accessed.
"""

from __future__ import annotations

from copy import deepcopy
from uuid import UUID

from .evidence_contract import evaluation_day, normalize_observation, parse_date


REVIEW_VERSION = "1.0"
REVIEW_MAX_AGE_DAYS = 35


class LoadedAssessmentProfile(dict):
    """Internal loader wrapper, distinguishable from arbitrary JSON keys."""


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _content(value):
    return _text(value) or isinstance(value, list) and bool(value) and all(_text(item) for item in value)


def _profile_raw(profile):
    if isinstance(profile, LoadedAssessmentProfile):
        payload = profile.get("raw")
        return payload if isinstance(payload, dict) else {}
    return profile if isinstance(profile, dict) else {}


def _plan_content(raw, plan):
    """Reuse the existing use-case profile instead of requiring a second charter."""
    derived = deepcopy(plan)
    names = plan.get("use_case_names")
    if not isinstance(names, list) or not all(_text(name) for name in names):
        names = None
    cases = raw.get("use_cases")
    if not isinstance(cases, list):
        cases = []
    selected = [case for case in cases if isinstance(case, dict)
                and (not names or case.get("name") in names)]
    if selected:
        mappings = {
            "business_owner": "business_owner", "use_cases": "name", "approved_data": "approved_data",
            "success_measures": "required_outcome", "stop_expand_criteria": "expand_stop_decision",
        }
        for output, source in mappings.items():
            if not _content(derived.get(output)) and all(_content(case.get(source)) for case in selected):
                values = []
                for case in selected:
                    values.extend(case[source] if isinstance(case[source], list) else [case[source]])
                derived[output] = list(dict.fromkeys(values))
    return derived


def validate_readiness_review(profile, *, allowed_controls=None):
    """Return schema errors without evaluating time or modifying supplied values."""
    raw = _profile_raw(profile)
    supplied = raw.get("readiness_review")
    if supplied is None:
        return []
    if not isinstance(supplied, dict):
        return ["readiness_review must be an object"]
    errors = []
    if supplied.get("version") != REVIEW_VERSION:
        errors.append("readiness_review.version must be 1.0")
    try:
        UUID(str(supplied.get("tenant_id") or ""))
    except (ValueError, TypeError):
        errors.append("readiness_review.tenant_id must be a tenant GUID")
    if allowed_controls is None:
        # Lazy import avoids coupling the profile reader's module initialization
        # to the assessment model.
        from .assessment_result import OPTIONAL_QUESTIONS, QUESTIONS
        allowed_controls = {row[0] for row in (*QUESTIONS, *OPTIONAL_QUESTIONS)}
    scopes = {}

    def fields(record, label, names):
        if not isinstance(record, dict):
            errors.append(label + " must be an object")
            return False
        for name in names:
            if not _text(record.get(name)):
                errors.append(f"{label}.{name} must contain text")
        for name in ("reviewed_at",):
            if parse_date(record.get(name)) is None:
                errors.append(f"{label}.{name} must be an ISO date")
        return True

    for key in ("pilot_scope", "expansion_scope"):
        scope = supplied.get(key)
        if key == "expansion_scope" and scope is None:
            continue
        if fields(scope, "readiness_review." + key, ("id", "description", "reviewer_role", "evidence_reference")):
            if scope.get("population_count") is not None and (isinstance(scope["population_count"], bool)
                    or not isinstance(scope["population_count"], int) or scope["population_count"] < 1):
                errors.append(f"readiness_review.{key}.population_count must be a positive integer")
            if _text(scope.get("id")):
                scopes[scope["id"]] = scope
    if isinstance(supplied.get("expansion_scope"), dict) and isinstance(supplied.get("pilot_scope"), dict):
        if supplied["expansion_scope"].get("id") == supplied["pilot_scope"].get("id"):
            errors.append("expansion_scope.id must identify a different population from pilot_scope.id")

    plan = supplied.get("pilot_plan")
    if plan is not None and fields(plan, "readiness_review.pilot_plan", ("reviewer_role", "evidence_reference")):
        if "use_case_names" in plan and (not isinstance(plan["use_case_names"], list) or not plan["use_case_names"]
                                         or not all(_text(name) for name in plan["use_case_names"])):
            errors.append("readiness_review.pilot_plan.use_case_names must list profile use-case names")
        cases = raw.get("use_cases") if isinstance(raw.get("use_cases"), list) else []
        names = {case.get("name") for case in cases if isinstance(case, dict) and _text(case.get("name"))}
        if isinstance(plan.get("use_case_names"), list) and all(_text(name) for name in plan["use_case_names"]) and any(name not in names for name in plan["use_case_names"]):
            errors.append("readiness_review.pilot_plan refers to an unknown profile use case")
        derived = _plan_content(raw, plan)
        for name in ("business_owner", "use_cases", "approved_data", "baseline", "success_measures", "stop_expand_criteria"):
            if not _content(derived.get(name)):
                errors.append(f"readiness_review.pilot_plan requires {name} directly or through its selected profile use cases")

    for key, required in (
        ("control_reviews", ("control_id", "scope_id", "reviewer_role", "rationale", "evidence_reference")),
        ("pilot_conditions", ("action_id", "scope_id", "reviewer_role", "condition", "evidence_reference")),
    ):
        rows = supplied.get(key, [])
        if not isinstance(rows, list):
            errors.append("readiness_review." + key + " must be an array")
            continue
        for index, row in enumerate(rows):
            label = f"readiness_review.{key}[{index}]"
            if not fields(row, label, required):
                continue
            if not _text(row.get("scope_id")) or row.get("scope_id") not in scopes:
                errors.append(label + ".scope_id must identify a supplied scope")
            if key == "control_reviews":
                if not _text(row.get("control_id")) or row.get("control_id") not in allowed_controls:
                    errors.append(label + ".control_id is not a supported control")
                if not _text(row.get("result")) or row.get("result") not in {"pass", "fail"}:
                    errors.append(label + ".result must be pass or fail; blanket not-applicable results are not supported")
            elif row.get("scope_id") != (supplied.get("pilot_scope") if isinstance(supplied.get("pilot_scope"), dict) else {}).get("id"):
                errors.append(label + ".scope_id must identify the pilot scope")

    outcomes = supplied.get("pilot_outcomes")
    if outcomes is not None and fields(outcomes, "readiness_review.pilot_outcomes", (
            "scope_id", "reviewer_role", "outcome_summary", "risk_review", "evidence_reference")):
        if outcomes.get("scope_id") != (supplied.get("pilot_scope") if isinstance(supplied.get("pilot_scope"), dict) else {}).get("id"):
            errors.append("readiness_review.pilot_outcomes.scope_id must identify the pilot scope")
        if not isinstance(outcomes.get("success_measures_met"), bool):
            errors.append("readiness_review.pilot_outcomes.success_measures_met must be true or false")
        start, end, reviewed = (parse_date(outcomes.get(name)) for name in ("period_start", "period_end", "reviewed_at"))
        if not start or not end or start > end or reviewed and end > reviewed:
            errors.append("readiness_review.pilot_outcomes requires an ordered period ending on or before its review date")
    approval = supplied.get("expansion_approval")
    if approval is not None and fields(approval, "readiness_review.expansion_approval", (
            "scope_id", "reviewer_role", "rationale", "evidence_reference")):
        if approval.get("scope_id") != (supplied.get("expansion_scope") if isinstance(supplied.get("expansion_scope"), dict) else {}).get("id"):
            errors.append("readiness_review.expansion_approval.scope_id must identify the expansion scope")
        if not isinstance(approval.get("approved"), bool):
            errors.append("readiness_review.expansion_approval.approved must be true or false")
    return list(dict.fromkeys(errors))


def build_review_evidence(profile, *, evaluation_date=None, expected_tenant_id=None, allowed_controls=None):
    """Return validated, scoped attestations and their canonical observations."""
    raw = _profile_raw(profile)
    supplied = raw.get("readiness_review")
    result = {"available": False, "status": "not_supplied", "errors": [], "observations": [],
              "pilot_scope": {}, "expansion_scope": {}, "pilot_plan": {}, "pilot_outcomes": {},
              "expansion_approval": {}, "pilot_conditions": [], "control_reviews": []}
    if supplied is None:
        return result
    errors = validate_readiness_review(raw, allowed_controls=allowed_controls)
    if errors:
        result.update(status="invalid", errors=errors)
        return result
    tenant = str(UUID(supplied["tenant_id"]))
    if expected_tenant_id and tenant.lower() != str(expected_tenant_id).lower():
        raise ValueError("Readiness review tenant ID does not match the assessed tenant.")
    day = evaluation_day(evaluation_date)
    result.update(available=True, status="available", tenant_id=tenant)
    source_file = (profile or {}).get("filename", "")

    def qualify(record):
        copied = deepcopy(record)
        age = (day - parse_date(record["reviewed_at"])).days
        copied["freshness"] = "future" if age < 0 else "stale" if age > REVIEW_MAX_AGE_DAYS else "current"
        copied["current"] = copied["freshness"] == "current"
        copied["reason"] = "Current dated operator attestation." if copied["current"] else (
            "The review is after the assessment evaluation date." if age < 0 else "The review is more than 35 days old and requires confirmation.")
        return copied

    for key in ("pilot_scope", "expansion_scope", "pilot_plan", "pilot_outcomes", "expansion_approval"):
        if supplied.get(key):
            item = _plan_content(raw, supplied[key]) if key == "pilot_plan" else supplied[key]
            result[key] = qualify(item)
    scopes = {result[key]["id"]: result[key] for key in ("pilot_scope", "expansion_scope") if result[key]}
    for original in supplied.get("control_reviews", []):
        row = qualify(original)
        scope = scopes[row["scope_id"]]
        row["current"] = row["current"] and scope["current"] and parse_date(row["reviewed_at"]) >= parse_date(scope["reviewed_at"])
        if not row["current"] and row["freshness"] == "current":
            row["reason"] = "The population definition must be current and reviewed no later than this control review."
        result["control_reviews"].append(row)
        observation = normalize_observation({
            "tenant_id": tenant, "control_id": row["control_id"], "metric_id": "operator_review." + row["control_id"],
            "domain_id": {"DATA": "data_protection", "LICENSE": "licensing", "APPS": "applications", "THREAT": "endpoints", "ENDPOINT": "endpoints", "EXTERNAL": "external_ai"}.get(row["control_id"].split(".")[0], row["control_id"].split(".")[0].lower()),
            "scope": {"id": scope["id"], "description": scope["description"]},
            "population": scope["description"], "value": ("Passed review: " if row["result"] == "pass" else "Failed review: ") + row["rationale"], "unit": "control review",
            "control_result": row["result"], "availability": "available", "observed_at": row["reviewed_at"],
            "source_type": "operator_attestation", "source_file": source_file, "source_schema": REVIEW_VERSION,
            "complete": row["current"], "max_age_days": REVIEW_MAX_AGE_DAYS,
            "reviewer_role": row["reviewer_role"], "evidence_reference": row["evidence_reference"],
            "scope_id": scope["id"], "qualifications": ["Dated operator attestation: " + row["evidence_reference"]],
        }, evaluation_date=day, expected_tenant_id=expected_tenant_id)
        result["observations"].append(observation)
    for row in supplied.get("pilot_conditions", []):
        qualified = qualify(row)
        qualified["current"] = (qualified["current"] and result["pilot_scope"]["current"]
                                and parse_date(qualified["reviewed_at"]) >= parse_date(result["pilot_scope"]["reviewed_at"]))
        if not qualified["current"] and qualified["freshness"] == "current":
            qualified["reason"] = "The pilot condition must be reviewed on or after the current population definition."
        result["pilot_conditions"].append(qualified)
    return result

"""Availability checks shared by collectors and saved-evidence replay."""

SOURCE_ALIASES = {"comm_compliance": "communication_compliance", "insider_risk": "insider_risk_policies", "retention_labels": "retention_policies"}


def source_is_complete(client, key, payload=None):
    """Require the specific dataset to have completed, including pagination.

    A legacy payload may carry its own availability flag. Overall service access
    never proves that an individual query succeeded.
    """
    if client is None:
        return False
    states = getattr(client, "collection_status", {}) or {}
    state = states.get(SOURCE_ALIASES.get(key, key), states.get(key))
    if isinstance(state, dict):
        status = str(state.get("availability_status") or "").lower()
        return state.get("available") is not False and (status == "available" or (not status and state.get("available") is True)) and not state.get("truncated") and state.get("complete") is not False
    if isinstance(payload, dict):
        return payload.get("available") is True and str(payload.get("availability_status") or "available").lower() == "available" and not payload.get("truncated") and payload.get("complete") is not False
    return (getattr(client, "data_sources", {}) or {}).get(key) is True


def source_availability(client, key, payload=None):
    states = (getattr(client, "collection_status", {}) or {}) if client else {}
    state = states.get(SOURCE_ALIASES.get(key, key), states.get(key, {}))
    state = state if isinstance(state, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    if source_is_complete(client, key, payload):
        return "available"
    outcome = state or payload
    status = str(outcome.get("availability_status") or "unknown").lower()
    if outcome.get("truncated") or outcome.get("complete") is False:
        return "partial"
    if status == "available":
        return "unavailable" if outcome.get("available") is False else "unknown"
    return status

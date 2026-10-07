"""Availability checks shared by collectors and saved-evidence replay."""

SOURCE_ALIASES = {"comm_compliance": "communication_compliance", "insider_risk": "insider_risk_policies", "retention_labels": "retention_policies"}


def envelope_flag(value):
    """Parse collection-envelope flags only; unknown values remain unknown."""
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {'true', 'false', '1', '0'}:
        return value.strip().lower() in {'true', '1'}
    return None


def resolve_source(states, key, service=''):
    """Resolve equivalent dataset names without selecting by insertion order."""
    def canonical(name):
        name = str(name)
        prefix = str(service).lower().replace(' ', '_') + '_'
        if service and name.startswith(prefix):
            name = name[len(prefix):]
        return SOURCE_ALIASES.get(name, name)
    matches = [(name, state) for name, state in states.items()
               if isinstance(state, dict) and canonical(name) == canonical(key)]
    if not matches:
        return {}, 'missing'
    first = sorted(matches, key=lambda item: item[0])[0][1]
    if any(state != first for _, state in matches):
        return {}, 'ambiguous'
    return first, 'matched'


def envelope_complete(state):
    status = str(state.get('availability_status') or '').lower()
    available = envelope_flag(state.get('available'))
    return ((status == 'available' and ('available' not in state or available is True))
            or (not status and available is True)) and (
                'complete' not in state or envelope_flag(state['complete']) is True) and (
                'truncated' not in state or envelope_flag(state['truncated']) is False)


def envelope_availability(state):
    status = str(state.get('availability_status') or '').lower()
    if status and status not in {'available', 'partial'}:
        return status
    if status == 'partial':
        return 'partial'
    available = envelope_flag(state.get('available'))
    if 'available' in state and available is not True:
        return 'unavailable' if available is False else 'unknown'
    if status == 'available' or available is True:
        if envelope_flag(state.get('truncated')) is True or envelope_flag(state.get('complete')) is False:
            return 'partial'
        return 'available' if envelope_complete(state) else 'unknown'
    return 'unknown'


def source_is_complete(client, key, payload=None):
    """Require the specific dataset to have completed, including pagination.

    A legacy payload may carry its own availability flag. Overall service access
    never proves that an individual query succeeded.
    """
    if client is None:
        return False
    states = getattr(client, "collection_status", {}) or {}
    state, association = resolve_source(states, key)
    if association == 'ambiguous':
        return False
    if association == 'matched':
        return envelope_complete(state)
    if isinstance(payload, dict):
        return envelope_complete(payload)
    return (getattr(client, "data_sources", {}) or {}).get(key) is True


def source_availability(client, key, payload=None):
    states = (getattr(client, "collection_status", {}) or {}) if client else {}
    state, association = resolve_source(states, key)
    if association == 'ambiguous':
        return 'unknown'
    payload = payload if isinstance(payload, dict) else {}
    outcome = state if association == 'matched' else payload
    if not outcome and source_is_complete(client, key):
        return 'available'
    return envelope_availability(outcome)

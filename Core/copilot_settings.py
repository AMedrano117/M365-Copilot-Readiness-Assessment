"""Copilot admin settings that Microsoft exposes only to signed-in administrators.

``GET /v1.0/copilot/admin/settings/limitedMode`` supports delegated
permissions only (CopilotSettings-LimitedMode.Read; Global Reader is the least
privileged role). It is collected as optional enrichment when a delegated
sign-in is available and never affects application-permission collection.
"""

AUTH_PATH = "graph_delegated"
SOURCE = "Microsoft Graph Copilot admin settings (delegated)"
LIMITED_MODE_PATH = "/v1.0/copilot/admin/settings/limitedMode"


def not_collected(reason):
    return {
        "available": False, "availability_status": "not_requested", "records_collected": 0,
        "reason": reason, "source": SOURCE, "auth_path_id": AUTH_PATH,
        "credential_type": "delegated_user", "coverage": "full", "evidence_quality": "standard",
        "unlock": ("Run with --delegated auto (or required) and sign in as at least a Global Reader. "
                   "Setup adds the delegated scope and the public-client redirect."),
    }


async def collect_copilot_limited_mode(session):
    """Read limited mode with a delegated session; never raises."""
    if session is None:
        return not_collected("Delegated sign-in was not available for this run.")
    client = session.graph_client()
    try:
        payload = await client.get_json(LIMITED_MODE_PATH)
    except Exception as exc:
        from .access_errors import describe_access_failure, status_code_of
        _category, message = describe_access_failure("Copilot limited mode", exc, delegated=True)
        state = not_collected(message)
        state.update(availability_status="unavailable", status_code=status_code_of(exc))
        if status_code_of(exc) == 403:
            state["reason"] = ("The signed-in account could not read Copilot limited mode (HTTP 403). Sign in with at least "
                               "Global Reader and consent the CopilotSettings-LimitedMode.Read delegated permission.")
        return state
    finally:
        try:
            await client.aclose()
        except Exception:
            pass
    payload = payload if isinstance(payload, dict) else {}
    return {
        "available": True, "availability_status": "available", "records_collected": 1,
        "reason": "", "source": SOURCE, "auth_path_id": AUTH_PATH, "credential_type": "delegated_user",
        "coverage": "full", "evidence_quality": "standard", "identity_ref": "delegated",
        "is_enabled_for_group": payload.get("isEnabledForGroup"),
        "group_id": payload.get("groupId") or "",
    }


def limited_mode_value(state):
    if not isinstance(state, dict) or not state.get("available"):
        return None
    if state.get("is_enabled_for_group") is True:
        return "Enabled for group " + (state.get("group_id") or "(group not returned)")
    if state.get("is_enabled_for_group") is False:
        return "Not enabled"
    return "Returned without a value"

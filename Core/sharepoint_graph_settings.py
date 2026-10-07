"""SharePoint tenant settings through Microsoft Graph (application permission).

``GET /v1.0/admin/sharepoint/settings`` works with a client secret and the
narrow ``SharePointTenantSettings.Read.All`` application permission, unlike
SharePoint admin PowerShell, which needs a certificate and the broad
``Sites.FullControl.All`` permission, or a delegated administrator sign-in.

Graph returns only part of the tenant sharing configuration. The payload
therefore records partial coverage and the settings it could not establish,
so the report never treats a missing field as a healthy default.
"""

from .auth_plan import credential_kind_for_path, graph_credential_kind

GRAPH_SETTINGS_SOURCE = "Microsoft Graph SharePoint tenant settings"
STATUS_KEY = "sharepoint_tenant_settings_graph"
AUTH_PATH = "graph_sharepoint_settings"

# SharePoint admin settings used by findings that Graph does not return.
UNAVAILABLE_FROM_GRAPH = (
    "OneDriveSharingCapability", "DefaultSharingLinkType", "DefaultLinkPermission",
    "FileAnonymousLinkType", "FolderAnonymousLinkType", "RequireAnonymousLinksExpireInDays",
    "ExternalUserExpirationRequired", "ExternalUserExpireInDays", "ShowEveryoneClaim",
    "ShowEveryoneExceptExternalUsersClaim", "ConditionalAccessPolicy",
)

_ENUM_FIELDS = {
    "sharingCapability": "SharingCapability",
    "sharingDomainRestrictionMode": "SharingDomainRestrictionMode",
}
_BOOLEAN_FIELDS = {
    "isLegacyAuthProtocolsEnabled": ("LegacyAuthProtocolsEnabled", False),
    "isRequireAcceptingUserToMatchInvitedUserEnabled": ("RequireAcceptingAccountMatchInvitedAccount", False),
    "isLoopEnabled": ("IsLoopEnabled", False),
    "isResharingByExternalUsersEnabled": ("PreventExternalUsersFromResharing", True),
    "isSiteCreationEnabled": ("SelfServiceSiteCreationDisabled", True),
}
_LIST_FIELDS = {
    "sharingAllowedDomainList": "SharingAllowedDomainList",
    "sharingBlockedDomainList": "SharingBlockedDomainList",
}
_VALUE_FIELDS = {
    "personalSiteDefaultStorageLimitInMB": "OneDriveStorageQuota",
    "deletedUserPersonalSiteRetentionPeriodInDays": "OrphanedPersonalSitesRetentionPeriod",
    "idleSessionSignOut": "IdleSessionSignOut",
}


def _pascal(value):
    text = str(value or "").strip()
    return text[:1].upper() + text[1:] if text else ""


def map_graph_settings(raw):
    """Translate Graph sharepointSettings into SharePoint admin setting names."""
    raw = raw.get("value", raw) if isinstance(raw, dict) and isinstance(raw.get("value"), dict) else (raw or {})
    settings, sources = {}, {}
    for graph_name, spo_name in _ENUM_FIELDS.items():
        if raw.get(graph_name) not in (None, ""):
            settings[spo_name] = _pascal(raw[graph_name])
            sources[spo_name] = graph_name
    for graph_name, (spo_name, inverted) in _BOOLEAN_FIELDS.items():
        if isinstance(raw.get(graph_name), bool):
            settings[spo_name] = (not raw[graph_name]) if inverted else raw[graph_name]
            sources[spo_name] = graph_name
    for graph_name, spo_name in _LIST_FIELDS.items():
        values = raw.get(graph_name)
        if isinstance(values, list):
            settings[spo_name] = " ".join(str(item) for item in values if item)
            sources[spo_name] = graph_name
    for graph_name, spo_name in _VALUE_FIELDS.items():
        if raw.get(graph_name) is not None:
            settings[spo_name] = raw[graph_name]
            sources[spo_name] = graph_name
    return settings, sources


def _state(available, *, reason="", status_code=None, credential_type=""):
    state = {
        "available": bool(available),
        "availability_status": "available" if available else "unavailable",
        "records_collected": 1 if available else 0,
        "pages_collected": 1 if available else 0,
        "truncated": False,
        "reason": reason,
        "auth_path_id": AUTH_PATH,
        "credential_type": credential_type,
        "coverage": "partial",
        "evidence_quality": "standard",
        "source": GRAPH_SETTINGS_SOURCE,
    }
    if status_code:
        state["status_code"] = status_code
    return state


def _administration_gap(reason):
    return {"available": False, "records_collected": 0, "reason": reason}


async def collect_sharepoint_tenant_settings_graph(graph_client):
    """Return a SharePoint governance payload built from Microsoft Graph."""
    credential_type = credential_kind_for_path(AUTH_PATH, {"graph_credential": graph_credential_kind()})
    admin_reason = ("Per-site sharing settings and Data Access Governance reports require SharePoint administration "
                    "(application certificate or administrator sign-in); Microsoft Graph does not return them.")
    payload = {
        "source": GRAPH_SETTINGS_SOURCE,
        "authentication": "application_graph",
        "credential_type": credential_type,
        "tenant": {"available": False, "settings": {}, "reason": "", "coverage": "partial",
                   "unavailable_fields": list(UNAVAILABLE_FROM_GRAPH), "field_sources": {}},
        "sites": {**_administration_gap(admin_reason), "items": []},
        "dag_reports": {**_administration_gap(admin_reason), "reports": []},
        "dag_activity_data": {**_administration_gap(admin_reason), "items": []},
        "exported_files": [],
        "collection_status": {},
    }
    try:
        raw = await graph_client.get_json("/v1.0/admin/sharepoint/settings")
    except Exception as exc:  # GraphRequestError, transport errors
        from .access_errors import describe_access_failure, status_code_of
        _category, message = describe_access_failure("SharePoint tenant settings (Microsoft Graph)", exc)
        status_code = status_code_of(exc)
        if status_code == 403:
            message = ("Microsoft Graph denied SharePoint tenant settings (HTTP 403). Grant and consent the "
                       "SharePointTenantSettings.Read.All application permission, then rerun setup and preflight.")
        payload["tenant"]["reason"] = message
        payload["collection_status"][STATUS_KEY] = _state(False, reason=message, status_code=status_code,
                                                           credential_type=credential_type)
        payload.update(available=False, availability_status="unavailable", reason=message)
        return payload
    settings, sources = map_graph_settings(raw if isinstance(raw, dict) else {})
    payload["tenant"].update(available=bool(settings), settings=settings, field_sources=sources,
                             graph_settings=raw.get("value", raw) if isinstance(raw, dict) else {})
    if not settings:
        payload["tenant"]["reason"] = "Microsoft Graph returned no recognized SharePoint settings."
    payload["collection_status"][STATUS_KEY] = _state(bool(settings), reason=payload["tenant"]["reason"],
                                                       credential_type=credential_type)
    payload.update(available=bool(settings), availability_status="partial" if settings else "unavailable",
                   reason=payload["tenant"]["reason"])
    return payload

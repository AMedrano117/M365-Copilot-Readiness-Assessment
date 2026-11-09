"""Normalize SharePoint sharing configuration and create evidence-based findings."""

import copy
import re
from datetime import datetime, timezone

from .new_recommendation import (
    CATEGORY_SCAN_COVERAGE,
    NOT_ASSESSED_STATUS,
    new_recommendation,
)


ANYONE_VALUES = {"externaluserandguestsharing", "anonymousaccess"}

# SharePoint Online PowerShell serializes these enums as numbers; Microsoft
# Graph returns names. Members follow the Set-SPOTenant documentation (see
# customer_report._sharing_setting_value for the verified display mapping).
_CAPABILITY = {0: "disabled", 1: "externalusersharingonly", 2: "externaluserandguestsharing", 3: "existingexternalusersharingonly"}
_LINK_PERMISSION = {0: "none", 1: "view", 2: "edit"}
SHARING_ENUMS = {
    "SharingCapability": _CAPABILITY, "OneDriveSharingCapability": _CAPABILITY,
    "DefaultSharingLinkType": {0: "none", 1: "direct", 2: "internal", 3: "anonymousaccess"},
    "FileAnonymousLinkType": _LINK_PERMISSION, "FolderAnonymousLinkType": _LINK_PERMISSION,
    "DefaultLinkPermission": _LINK_PERMISSION,
}
SHARING_LABELS = {
    "disabled": "off", "externalusersharingonly": "new and existing guests",
    "externaluserandguestsharing": "Anyone links and guests", "existingexternalusersharingonly": "existing guests only",
    "direct": "specific people", "specificpeople": "specific people", "internal": "people in the organization",
    "anonymousaccess": "Anyone", "none": "the most permissive allowed", "view": "view", "edit": "edit",
}


def setting_name(key, value):
    """Lowercase enum name for a SharePoint sharing setting given as a name or a number."""
    # Enum member 0 (Disabled, None) is a value; only None is absent.
    text = "" if value is None else str(value).strip()
    names = SHARING_ENUMS.get(key)
    if names and not isinstance(value, bool) and re.fullmatch(r"[+-]?\d+", text):
        return names.get(int(text), text.lower())
    return text.lower()


def setting_label(key, value):
    name = setting_name(key, value)
    return SHARING_LABELS.get(name, name or "not returned")


# Breadth of a SharingCapability value, as ordered in Microsoft's "Manage sharing
# settings": Anyone > New and existing guests > Existing guests > Only people in
# the organization.
_CAPABILITY_RANK = {"disabled": 0, "existingexternalusersharingonly": 1, "externalusersharingonly": 2,
                    "externaluserandguestsharing": 3}
TENANT_SHARING_SCOPE = "Tenant-wide SharePoint and OneDrive sharing configuration"
_BOOLEAN_SETTINGS = {"ExternalUserExpirationRequired", "PreventExternalUsersFromResharing", "LegacyAuthProtocolsEnabled",
                     "RequireAcceptingAccountMatchInvitedAccount"}


def _value_text(value):
    """Text of a returned value; unlike _text, a numeric 0 is kept."""
    return "" if value is None else str(value).strip()


def _capability(value):
    name = setting_name("SharingCapability", value) if value not in (None, "") and not isinstance(value, bool) else ""
    return name if name in _CAPABILITY_RANK else None


def effective_sharing_capability(site_value, tenant_value):
    """Effective sharing level of a site under the tenant setting, or None when it is not established.

    Microsoft documents that a site's setting must be the same as or more
    restrictive than the organization's, and that turning organization-level
    sharing off makes it unavailable on every site. A stored site value can still
    be broader. A disabled value at either scope establishes "disabled";
    otherwise both values are required. Missing evidence is never filled.
    """
    site, tenant = _capability(site_value), _capability(tenant_value)
    if "disabled" in {site, tenant}:
        return "disabled"
    if site is None or tenant is None:
        return None
    return min((site, tenant), key=_CAPABILITY_RANK.get)


def _anyone_permitted(settings):
    """True or False when the returned boundary establishes it; None otherwise."""
    values = [_capability(settings.get(key)) for key in ("SharingCapability", "OneDriveSharingCapability") if key in settings]
    if any(value == "externaluserandguestsharing" for value in values):
        return True
    # OneDrive cannot be more permissive than SharePoint, so a known SharePoint
    # boundary without Anyone establishes that neither workload permits Anyone.
    sharepoint = _capability(settings.get("SharingCapability"))
    return False if sharepoint is not None else None


# Settings that govern only Anyone links (Set-SPOTenant). Where the effective
# boundary does not permit Anyone links, their stored value governs no link that
# can be created: it is inactive, neither meeting nor failing a rule.
ANYONE_DEPENDENT_SETTINGS = ("RequireAnonymousLinksExpireInDays", "FileAnonymousLinkType", "FolderAnonymousLinkType")
_ANYONE = "externaluserandguestsharing"


def anyone_links_permitted(settings, tenant_settings=None, *, personal=False):
    """Whether the effective boundary of one scope permits Anyone links: True, False, or None when not established.

    Without ``tenant_settings`` the settings are the tenant's own. For a site the
    tenant setting is the ceiling (and the OneDrive setting for a personal site):
    any known value narrower than Anyone at either scope establishes False, and
    True needs Anyone at every scope.
    """
    settings = settings or {}
    if tenant_settings is None:
        return _anyone_permitted(settings)
    tenant_settings = tenant_settings or {}
    values = [_capability(settings.get("SharingCapability")), _capability(tenant_settings.get("SharingCapability"))]
    if personal:
        values.append(_capability(tenant_settings.get("OneDriveSharingCapability")))
    if any(value is not None and value != _ANYONE for value in values):
        return False
    return True if all(value == _ANYONE for value in values) else None


def anyone_applicability(permitted):
    """Applicability of an Anyone-dependent setting under a resolved boundary."""
    return "not_established" if permitted is None else "applicable" if permitted else "not_applicable"


def effective_default_link(settings):
    """Effective tenant default link type, or '' when it is not established.

    Set-SPOTenant documents that DefaultSharingLinkType None uses the widest
    scope permitted: an Anyone link when Anyone links are enabled, otherwise an
    organization link.
    """
    settings = settings or {}
    if settings.get("DefaultSharingLinkType") in (None, ""):
        return ""
    name = setting_name("DefaultSharingLinkType", settings.get("DefaultSharingLinkType"))
    name = {"specificpeople": "direct", "anonymous": "anonymousaccess"}.get(name, name)
    if name == "none":
        anyone = _anyone_permitted(settings)
        return "" if anyone is None else "anonymousaccess" if anyone else "internal"
    return name if name in {"direct", "internal", "anonymousaccess"} else ""


def _strict_bool(value):
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    return {"true": True, "yes": True, "enabled": True, "1": True,
            "false": False, "no": False, "disabled": False, "0": False}.get(_text(value).lower())


def _comparable(name, value):
    if name in _BOOLEAN_SETTINGS:
        parsed = _strict_bool(value)
        return _text(value).lower() if parsed is None else str(parsed).lower()
    return setting_name(name, value)


# Methodology rules for tenant sharing (docs/CONTENT_SHARING_VALIDATION.md).
# CONTENT.SHARING requirements decide the sharing-defaults control; hardening
# results are the recommended baseline and never change a control.
_EXPECTED = {
    "SharingCapability": "New and existing guests or more restrictive (no Anyone links)",
    "OneDriveSharingCapability": "New and existing guests or more restrictive (no Anyone links)",
    "DefaultSharingLinkType": "Specific people",
    "RequireAnonymousLinksExpireInDays": "1 to 30 days when Anyone links are permitted",
    "FileAnonymousLinkType": "View only when Anyone links are permitted",
    "FolderAnonymousLinkType": "View only when Anyone links are permitted",
    "ExternalUserExpirationRequired": "Enabled when guest sharing is permitted",
    "PreventExternalUsersFromResharing": "Enabled when guest sharing is permitted",
}


def evaluate_tenant_sharing(settings, conflicts=()):
    """Result of each tenant sharing setting against the methodology rule.

    Results are meets, does_not_meet, not_applicable or not_established. A value
    that is missing, unmapped or reported differently by two sources is never
    classified as meeting a rule. Every result describes configuration only.
    """
    settings = settings or {}
    conflicted = {item.get("setting") for item in conflicts or [] if isinstance(item, dict)}
    # A boundary that two sources report differently decides no applicability.
    boundary_conflict = bool(conflicted & {"SharingCapability", "OneDriveSharingCapability"})
    anyone = None if boundary_conflict else _anyone_permitted(settings)
    sharepoint = _capability(settings.get("SharingCapability"))
    guests = None if sharepoint is None or "SharingCapability" in conflicted else sharepoint != "disabled"
    results = []

    def add(name, observed, result, requirement, *, effective=None, basis="Methodology rule", applicability=None):
        if name in conflicted:
            result, basis = "not_established", "Sources returned different values; neither is selected."
        item = {"setting": name, "observed": observed, "effective": effective, "expected": _EXPECTED[name],
                "result": result, "requirement": requirement, "basis": basis, "establishes": "configuration"}
        if applicability:
            item["applicability"] = applicability
        results.append(item)

    for name in ("SharingCapability", "OneDriveSharingCapability"):
        raw = settings.get(name)
        value = _capability(raw)
        observed = setting_name(name, raw) if raw not in (None, "") else ""
        if value is None and name == "OneDriveSharingCapability" and raw in (None, "") and sharepoint is not None and sharepoint != "externaluserandguestsharing":
            add(name, "", "meets", "CONTENT.SHARING", effective=sharepoint,
                basis="Documented product behavior: OneDrive cannot be more permissive than SharePoint.")
            continue
        add(name, observed, "not_established" if value is None else "does_not_meet" if value == "externaluserandguestsharing" else "meets",
            "CONTENT.SHARING", effective=value)

    raw_default = settings.get("DefaultSharingLinkType")
    effective = effective_default_link(settings)
    add("DefaultSharingLinkType", setting_name("DefaultSharingLinkType", raw_default) if raw_default not in (None, "") else "",
        "not_established" if not effective else "meets" if effective == "direct" else "does_not_meet", "CONTENT.SHARING",
        effective=effective or None,
        basis=("Documented product behavior: None uses the widest permitted scope." if setting_name("DefaultSharingLinkType", raw_default) == "none"
               else "Methodology rule"))

    def anyone_rule(name, evaluate):
        # The configured value is kept as returned (normalized) whatever its applicability.
        raw = settings.get(name)
        observed = setting_name(name, raw) if raw not in (None, "") else ""
        applicability = anyone_applicability(anyone)
        if anyone is False:
            add(name, observed, "not_applicable", "CONTENT.SHARING", applicability=applicability,
                basis="Inactive: the effective sharing boundary does not permit Anyone links, so this configured value governs "
                      "no link that can be created. It is preserved and is evaluated if Anyone links become permitted.")
        elif anyone is None:
            add(name, observed, "not_established", "CONTENT.SHARING", applicability=applicability,
                basis="Whether Anyone links are permitted is not established, so whether this setting applies is unknown.")
        elif raw in (None, "") or isinstance(raw, bool):
            add(name, observed, "not_established", "CONTENT.SHARING", applicability=applicability)
        else:
            add(name, observed, evaluate(raw), "CONTENT.SHARING", applicability=applicability)

    def expiry(raw):
        if not re.fullmatch(r"[+-]?\d+", _value_text(raw)):
            return "not_established"
        days = int(_value_text(raw))
        return "meets" if 1 <= days <= 30 else "does_not_meet" if days in (0, -1) or 30 < days <= 730 else "not_established"

    anyone_rule("RequireAnonymousLinksExpireInDays", expiry)
    for name in ("FileAnonymousLinkType", "FolderAnonymousLinkType"):
        anyone_rule(name, lambda raw, name=name: {"view": "meets", "edit": "does_not_meet"}.get(setting_name(name, raw), "not_established"))
    for name in ("ExternalUserExpirationRequired", "PreventExternalUsersFromResharing"):
        raw = settings.get(name)
        value = _strict_bool(raw)
        if guests is False:
            add(name, _value_text(raw), "not_applicable", "hardening")
        else:
            add(name, _value_text(raw), "not_established" if guests is None or value is None else "meets" if value else "does_not_meet", "hardening")
    return results


def tenant_settings_source(governance):
    """Name of the source state that supplied the evaluated tenant settings."""
    states = governance.get("collection_status") or {}
    from .sharepoint_graph_settings import GRAPH_SETTINGS_SOURCE, STATUS_KEY
    if governance.get("source") == GRAPH_SETTINGS_SOURCE:
        return STATUS_KEY if STATUS_KEY in states else ""
    return "sharepoint_tenant_settings" if "sharepoint_tenant_settings" in states else ""


def sharing_strength(governance):
    """The verified sharing strength, only when every CONTENT.SHARING requirement is met.

    Collection success, a partial source or conflicting sources never establish it.
    """
    governance = governance or {}
    if not governance.get("available") or governance.get("coverage") == "partial":
        return None
    settings = governance.get("settings") or {}
    conflicts = [item for item in governance.get("conflicts") or [] if item.get("setting") in _EXPECTED]
    results = evaluate_tenant_sharing(settings, conflicts)
    required = [item for item in results if item["requirement"] == "CONTENT.SHARING"]
    if conflicts or not required or any(item["result"] not in {"meets", "not_applicable"} for item in required):
        return None
    from .cross_provider_assessment import METHODOLOGY_VERSION
    observed = {"SharingCapability": setting_name("SharingCapability", settings.get("SharingCapability")),
                "OneDriveSharingCapability": setting_name("OneDriveSharingCapability", settings.get("OneDriveSharingCapability"))
                if settings.get("OneDriveSharingCapability") not in (None, "") else "not returned (limited by SharePoint)",
                "DefaultSharingLinkType": setting_name("DefaultSharingLinkType", settings.get("DefaultSharingLinkType")),
                "DefaultSharingLinkType (effective)": effective_default_link(settings)}
    row = {
        "Area": "SharePoint sharing",
        "Strength": ("Tenant sharing configuration: new sharing links default to Specific people, and SharePoint and OneDrive do not "
                     "permit Anyone links. This describes how new links are created; it does not establish who can already reach "
                     "existing content."),
        "Evidence": f"Default link type: {settings.get('DefaultSharingLinkType')}; SharePoint sharing level: {settings.get('SharingCapability')}"
                    + (f"; OneDrive sharing level: {settings.get('OneDriveSharingCapability')}" if settings.get("OneDriveSharingCapability") not in (None, "") else "")
                    + ".",
        "Benefit": "This reduces accidental broad access to content that can later ground an AI response.",
        "Key": "sharepoint-sharing-restricted",
        "ControlId": "CONTENT.SHARING",
        "EvidenceKey": "sharepoint_governance_detail",
        "DomainId": "content",
        "EvidenceScope": TENANT_SHARING_SCOPE,
        "ObservedValues": observed,
        "ExpectedRule": "SharePoint and OneDrive external sharing is New and existing guests or more restrictive (no Anyone links), "
                        "and the effective default sharing link is Specific people.",
        "MethodologyRule": f"CONTENT.SHARING tenant sharing defaults, methodology {METHODOLOGY_VERSION}; configuration evidence only.",
        "SharingSettingResults": results,
    }
    source = tenant_settings_source(governance)
    if source:
        row["EvidenceSource"] = source
    return row


_WORKLOAD_SCOPE = {"SharingCapability": "Tenant-wide SharePoint sharing configuration",
                   "OneDriveSharingCapability": "Tenant-wide OneDrive sharing configuration"}


def _corroborating_sources(governance, names):
    """Sources that returned the same value for every returned setting in ``names``."""
    primary = tenant_settings_source(governance)
    sources = [primary] if primary else []
    settings = governance.get("settings") or {}
    graph = governance.get("graph_settings") or {}
    returned = [name for name in names if settings.get(name) not in (None, "")]
    from .sharepoint_graph_settings import STATUS_KEY
    if (graph and returned and STATUS_KEY not in sources and STATUS_KEY in (governance.get("collection_status") or {})
            and all(name in graph and _comparable(name, graph[name]) == _comparable(name, settings[name]) for name in returned)):
        sources.append(STATUS_KEY)
    return sources


def sharing_sub_strengths(governance):
    """Content & Sharing sub-control strengths, one per semantic protection.

    Each describes a returned, unconflicted configuration value that meets its
    methodology rule, at the scope it was read. A strength never establishes the
    CONTENT.SHARING control, operational effectiveness or the absence of existing
    links, and an inactive (not applicable) value never produces one. Corroborating
    sources are kept as provenance of the single strength.
    """
    governance = governance or {}
    if not governance.get("available"):
        return []
    settings = governance.get("settings") or {}
    conflicts = [item for item in governance.get("conflicts") or [] if item.get("setting") in _EXPECTED]
    evaluated = {item["setting"]: item for item in evaluate_tenant_sharing(settings, conflicts)}
    meets = lambda name: (evaluated.get(name) or {}).get("result") == "meets"
    label = lambda name: setting_label(name, settings.get(name)) if settings.get(name) not in (None, "") else "limited by the SharePoint setting"
    from .cross_provider_assessment import METHODOLOGY_VERSION
    source = tenant_settings_source(governance)
    rows = []

    def strength(key, statement, names, *, scope=TENANT_SHARING_SCOPE, requirement="CONTENT.SHARING", expected=None):
        observed = {name: setting_name(name, settings.get(name)) if settings.get(name) not in (None, "")
                    else "not returned (limited by SharePoint)" for name in names}
        rule = ("Content & Sharing sub-control" if requirement == "CONTENT.SHARING" else "Recommended sharing hardening")
        row = {
            "Area": "SharePoint sharing", "Key": key, "Strength": statement,
            "Evidence": "; ".join(f"{name}: {settings.get(name)}" for name in names if settings.get(name) not in (None, "")) + ".",
            "DomainId": "content", "SubControlOf": "CONTENT.SHARING", "Requirement": requirement, "Establishes": "configuration",
            "EvidenceKey": "sharepoint_governance_detail", "EvidenceScope": scope, "ObservedValues": observed,
            "ExpectedRule": expected or "; ".join(dict.fromkeys(_EXPECTED[name] for name in names)),
            "MethodologyRule": f"{rule}, methodology {METHODOLOGY_VERSION}; configuration evidence only. "
                               "It does not by itself establish the CONTENT.SHARING control.",
            "SharingSettingResults": [copy.deepcopy(evaluated[name]) for name in names],
            "CorroboratingSources": _corroborating_sources(governance, names),
        }
        if source:
            row["EvidenceSource"] = source
        rows.append(row)

    sharepoint, onedrive = meets("SharingCapability"), meets("OneDriveSharingCapability")
    if sharepoint and onedrive:
        strength("content.sharing.anyone_creation_restricted",
                 "Anyone-link creation is restricted for the assessed tenant configuration: SharePoint and OneDrive external sharing "
                 f"are limited to authenticated guests or stricter (SharePoint: {label('SharingCapability')}; OneDrive: "
                 f"{label('OneDriveSharingCapability')}). This describes how new links can be created; existing links are reported separately.",
                 ["SharingCapability", "OneDriveSharingCapability"],
                 expected="SharePoint and OneDrive external sharing is New and existing guests or more restrictive (no Anyone links).")
    elif sharepoint:
        strength("content.sharing.sharepoint_guest_boundary",
                 f"SharePoint external sharing is limited to authenticated guests or stricter ({label('SharingCapability')}) under the "
                 "assessed tenant configuration, so new Anyone links cannot be created on SharePoint sites.",
                 ["SharingCapability"], scope=_WORKLOAD_SCOPE["SharingCapability"])
    elif onedrive:
        strength("content.sharing.onedrive_guest_boundary",
                 f"OneDrive external sharing is limited to authenticated guests or stricter ({label('OneDriveSharingCapability')}) under "
                 "the assessed tenant configuration, so new Anyone links cannot be created in OneDrive.",
                 ["OneDriveSharingCapability"], scope=_WORKLOAD_SCOPE["OneDriveSharingCapability"])
    if meets("DefaultSharingLinkType"):
        strength("content.sharing.default_link_specific_people",
                 "New sharing links default to Specific people under the assessed tenant configuration.", ["DefaultSharingLinkType"])
    # Anyone-link settings produce a strength only while they apply.
    if meets("RequireAnonymousLinksExpireInDays"):
        days = _value_text(settings.get("RequireAnonymousLinksExpireInDays"))
        strength("content.sharing.anyone_link_expiry_required",
                 f"Anyone links must expire within {days} days under the assessed tenant configuration.", ["RequireAnonymousLinksExpireInDays"])
    view_only = [name for name in ("FileAnonymousLinkType", "FolderAnonymousLinkType") if meets(name)]
    if view_only:
        target = "files and folders" if len(view_only) == 2 else "files" if view_only == ["FileAnonymousLinkType"] else "folders"
        strength("content.sharing.anyone_links_view_only",
                 f"Anyone links to {target} default to view only under the assessed tenant configuration.", view_only)
    if meets("ExternalUserExpirationRequired"):
        days = _value_text(settings.get("ExternalUserExpireInDays"))
        strength("content.sharing.guest_expiration_required",
                 "Guest access expiration is configured for the assessed tenant configuration"
                 + (f" (guest access expires after {days} days)" if re.fullmatch(r"\d+", days) else "")
                 + ". This is a configuration setting; it does not show whether existing guest access has been reviewed.",
                 ["ExternalUserExpirationRequired"], requirement="hardening")
    if meets("PreventExternalUsersFromResharing"):
        strength("content.sharing.guest_resharing_prevented",
                 "Guests cannot reshare items they do not own under the assessed tenant configuration.",
                 ["PreventExternalUsersFromResharing"], requirement="hardening")
    return rows


def _text(value):
    return str(value or "").strip()


def _bool(value):
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"true", "yes", "1", "enabled"}


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _date(value):
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def merge_sharepoint_payloads(graph_payload, spo_payload):
    """Combine the Graph baseline with SharePoint administration results.

    SharePoint administration (certificate or delegated) is authoritative for
    the settings it returns. Graph remains the baseline when administration was
    unavailable, and its dataset state is kept either way. A disagreement on a
    shared setting is recorded as a conflict instead of silently choosing one.
    """
    graph_payload = graph_payload if isinstance(graph_payload, dict) else {}
    spo_payload = spo_payload if isinstance(spo_payload, dict) else {}
    graph_tenant = graph_payload.get("tenant") or {}
    spo_tenant = spo_payload.get("tenant") or {}
    states = {}
    states.update(graph_payload.get("collection_status") or {})
    states.update(spo_payload.get("collection_status") or {})
    if spo_tenant.get("available"):
        merged = dict(spo_payload)
        merged["tenant"] = dict(spo_tenant)
        conflicts = []
        graph_settings = graph_tenant.get("settings") or {}
        for name, value in graph_settings.items():
            spo_value = (spo_tenant.get("settings") or {}).get(name)
            # SharePoint PowerShell returns enum numbers and Graph returns names;
            # the same value in two encodings is not a conflict.
            if spo_value is not None and _comparable(name, spo_value) != _comparable(name, value):
                conflicts.append({"setting": name, "sharepoint_admin": spo_value, "graph": value})
        if conflicts:
            merged["conflicts"] = conflicts
        if graph_tenant.get("available"):
            merged["graph_settings"] = graph_settings
    elif graph_tenant.get("available"):
        merged = dict(graph_payload)
        # Keep any SharePoint administration datasets that did return data.
        for name in ("sites", "dag_reports", "dag_activity_data"):
            if (spo_payload.get(name) or {}).get("available"):
                merged[name] = spo_payload[name]
        if spo_payload:
            merged["administration_reason"] = spo_payload.get("reason") or spo_tenant.get("reason") or ""
            for key in ("configuration_required", "admin_url", "exported_files"):
                if spo_payload.get(key):
                    merged[key] = spo_payload[key]
    else:
        merged = dict(spo_payload or graph_payload)
        reasons = [str(value).strip().rstrip(".") + "." for value in (spo_payload.get("reason"), graph_payload.get("reason")) if value]
        if reasons:
            merged["reason"] = " ".join(dict.fromkeys(reasons))
    merged["collection_status"] = states
    tenant = merged.get("tenant") or {}
    administration = [(merged.get(name) or {}).get("available") for name in ("sites", "dag_reports")]
    merged["available"] = bool(tenant.get("available")) or any(administration)
    partial = tenant.get("coverage") == "partial" or not all(administration) or any(
        not state.get("available") or state.get("availability_status") == "partial" or state.get("complete") is False
        for state in states.values() if isinstance(state, dict))
    merged["availability_status"] = ("partial" if merged["available"] and partial else
                                     "available" if merged["available"] else
                                     merged.get("availability_status") or "unavailable")
    return merged


def summarize_sharepoint_governance(payload, evaluation_date=None, sam_max_age_days=None):
    payload = payload if isinstance(payload, dict) else {}
    tenant = payload.get("tenant") or {}
    sites = payload.get("sites") or {}
    reports = payload.get("dag_reports") or {}
    activity = payload.get("dag_activity_data") or {}
    settings = tenant.get("settings") or {}
    site_items = sites.get("items") or []
    report_items = reports.get("reports") or []
    from .data_exposure_assessment import DEFAULT_SAM_MAX_AGE_DAYS
    # The methodology's SAM permission/sharing report threshold (35 days by default).
    max_age = sam_max_age_days or DEFAULT_SAM_MAX_AGE_DAYS

    # Configured site values can be broader than the tenant setting; the
    # effective count applies the tenant ceiling and stays unknown without it.
    anyone_sites = [row for row in site_items if setting_name("SharingCapability", row.get("SharingCapability")) in ANYONE_VALUES]
    tenant_capability = _capability(settings.get("SharingCapability"))
    effective_anyone_sites = None if tenant_capability is None else sum(
        effective_sharing_capability(row.get("SharingCapability"), settings.get("SharingCapability")) == "externaluserandguestsharing"
        for row in site_items)
    completed_reports = [row for row in report_items if _text(row.get("Status")).lower() in {"completed", "complete", "succeeded"}]
    now = _date(evaluation_date) or datetime.now(timezone.utc)
    recent_completed = []
    stale_completed = []
    for row in completed_reports:
        completed = next((_date(row.get(key)) for key in (
            "CompletedDateTime", "ReportEndTime", "EndTime", "CreatedDateTime",
            "TriggeredDateTime", "ReportStartTime", "StartTime",
        ) if _date(row.get(key))), None)
        if completed and 0 <= (now.date() - completed.date()).days <= max_age:
            recent_completed.append(row)
        else:
            stale_completed.append(row)
    running_reports = [row for row in report_items if _text(row.get("Status")).lower() in {
        "started", "notstarted", "running", "inprogress", "queued", "inqueue",
    }]

    def matches(rows, entity, workload=""):
        return [row for row in rows if (
            _text(row.get("RequestedEntity") or row.get("ReportEntity")).lower() == entity.lower()
            and (not workload or _text(row.get("RequestedWorkload") or row.get("Workload")).lower() == workload.lower())
        )]

    coverage_specs = [
        ("SharePoint permission state", [("PermissionedUsers", "SharePoint")]),
        ("OneDrive permission state", [("PermissionedUsers", "OneDriveForBusiness")]),
        ("Everyone and Everyone except external users", [("Everyone", ""), ("EveryoneExceptExternalUsers", "")]),
        ("Anyone and guest sharing links", [("SharingLinks_Anyone", "SharePoint"), ("SharingLinks_Guests", "SharePoint")]),
        ("Everyone except external users activity", [("EveryoneExceptExternalUsersAtSite", "SharePoint"), ("EveryoneExceptExternalUsersForItems", "SharePoint")]),
    ]
    dag_coverage = []
    for label, requirements in coverage_specs:
        if all(matches(recent_completed, entity, workload) for entity, workload in requirements):
            state = "Recent"
        elif any(matches(completed_reports, entity, workload) for entity, workload in requirements):
            state = "Stale or incomplete"
        elif any(matches(running_reports, entity, workload) for entity, workload in requirements):
            state = "Running"
        else:
            state = "Missing"
        dag_coverage.append({"report": label, "status": state})

    copilot_activity = next((
        row for row in (activity.get("items") or [])
        if _text(row.get("RequestedEntity") or row.get("ReportEntity")).lower() == "copilotappinsights"
    ), None)
    dag_coverage.append({
        "report": "Copilot app insight activity data",
        "status": _text((copilot_activity or {}).get("Status") or (copilot_activity or {}).get("State")) or "Not available",
    })
    return {
        "available": bool(tenant.get("available")),
        "availability_status": payload.get("availability_status", "available" if tenant.get("available") else "unavailable"),
        "reason": tenant.get("reason") or payload.get("reason", ""),
        "configuration_required": payload.get("configuration_required", ""),
        "source": payload.get("source", "SharePoint Online Management Shell"),
        "authentication": payload.get("authentication", ""),
        "coverage": tenant.get("coverage", "full" if tenant.get("available") else ""),
        "unavailable_fields": tenant.get("unavailable_fields") or [],
        "administration_reason": payload.get("administration_reason", ""),
        "conflicts": payload.get("conflicts") or [],
        "settings": settings,
        # Graph values kept beside authoritative SharePoint administration values; used for corroboration only.
        "graph_settings": payload.get("graph_settings") or {},
        "sites_available": bool(sites.get("available")),
        "sites": site_items,
        "site_count": len(site_items),
        "anyone_site_count": len(anyone_sites),
        "configured_anyone_site_count": len(anyone_sites),
        "effective_anyone_site_count": effective_anyone_sites,
        "tenant_settings_source": tenant_settings_source(payload),
        "source_conflicts_for_sharing": [item for item in payload.get("conflicts") or [] if item.get("setting") in _EXPECTED],
        "dag_available": bool(reports.get("available")),
        "dag_reports": report_items,
        "dag_completed_count": len(completed_reports),
        "dag_recent_completed_count": len(recent_completed),
        "dag_stale_completed_count": len(stale_completed),
        "dag_running_count": len(running_reports),
        "dag_coverage": dag_coverage,
        "dag_reason": reports.get("reason", ""),
        "dag_activity_available": bool(activity.get("available")),
        "dag_activity_states": activity.get("items") or [],
        "dag_activity_reason": activity.get("reason", ""),
        "collection_status": payload.get("collection_status") or {},
        "copilot_content_controls": payload.get("copilot_content_controls") or {},
    }


def build_copilot_content_control_recommendations(governance):
    """Reference rows for SharePoint controls that scope what Copilot can ground on.

    These describe configuration only; they never change the readiness decision.
    """
    rows = []
    settings = governance.get("settings") or {}
    controls = governance.get("copilot_content_controls") or {}
    mode = _text(controls.get("restricted_search_mode"))
    if controls.get("available") and mode:
        enabled = mode.lower() == "enabled"
        allowed = controls.get("restricted_search_allowed_sites")
        allowed_text = f" with {allowed} allowed site(s)" if isinstance(allowed, int) else ""
        rows.append(new_recommendation(
            service="M365",
            feature="Restricted SharePoint Search",
            observation=(f"Restricted SharePoint Search is enabled{allowed_text}. Organization-wide search and Copilot are limited to "
                         "allowed sites plus content users already own or frequently use."
                         if enabled else
                         "Restricted SharePoint Search is not enabled. Copilot can ground on any content each user can already access."),
            recommendation=("Keep it as a temporary guardrail while oversharing is remediated, then plan to replace it with permission cleanup "
                            "and Restricted Content Discovery for specific sites."
                            if enabled else
                            "Fix oversharing first. If remediation cannot finish before the pilot, consider Restricted Content Discovery for "
                            "the riskiest sites or Restricted SharePoint Search as a temporary tenant-wide guardrail."),
            link_text="Restricted SharePoint Search",
            link_url="https://learn.microsoft.com/sharepoint/restricted-sharepoint-search",
            priority="Low", status="Insight", disposition="Reference",
            finding_key="sharepoint.copilot.restricted_search",
            evidence_key="sharepoint_governance_detail", evidence_basis="Tenant configuration", confidence="High",
        ))
    sites = governance.get("sites") or []
    if governance.get("sites_available") and any("RestrictContentOrgWideSearch" in row for row in sites):
        restricted = [row for row in sites if _bool(row.get("RestrictContentOrgWideSearch"))]
        rows.append(new_recommendation(
            service="M365",
            feature="Restricted Content Discovery",
            observation=(f"{len(restricted)} of {len(sites)} collected site(s) use Restricted Content Discovery, which keeps their content "
                         "out of Copilot and organization-wide search results for users who do not already use it."),
            recommendation=("Review whether sites holding sensitive content that is not yet permission-remediated should use Restricted "
                            "Content Discovery during the pilot. It hides content from discovery; it does not change access."),
            link_text="Restricted Content Discovery",
            link_url="https://learn.microsoft.com/sharepoint/restricted-content-discovery",
            priority="Low", status="Insight", disposition="Reference",
            finding_key="sharepoint.copilot.restricted_content_discovery",
            evidence_key="sharepoint_governance_detail", evidence_basis="Site configuration", confidence="High",
        ))
    if "EnableRestrictedAccessControl" in settings:
        enabled = _bool(settings.get("EnableRestrictedAccessControl"))
        rows.append(new_recommendation(
            service="M365",
            feature="Restricted access control",
            observation=("Restricted access control is enabled for the tenant; sites can limit access to members of specified groups."
                         if enabled else "Restricted access control is not enabled for the tenant."),
            recommendation="" if enabled else (
                "Where SharePoint Advanced Management is available, consider restricted access control for sites whose content must stay "
                "with a defined group regardless of sharing, before Copilot is broadly enabled. Without it, limit those sites' "
                "membership and sharing directly and record a dated owner review."),
            link_text="Restricted access control",
            link_url="https://learn.microsoft.com/sharepoint/restricted-access-control",
            priority="Low", status="Insight", disposition="Reference",
            finding_key="sharepoint.copilot.restricted_access_control",
            evidence_key="sharepoint_governance_detail", evidence_basis="Tenant configuration", confidence="High",
        ))
    return rows


def build_sharepoint_recommendations(governance, evaluation_date=None, sam_max_age_days=None):
    governance = summarize_sharepoint_governance(governance, evaluation_date=evaluation_date, sam_max_age_days=sam_max_age_days)
    if not governance.get("available"):
        excluded = governance.get('availability_status') == 'not_requested'
        needs_admin_url = governance.get('configuration_required') == 'SHAREPOINT_ADMIN_URL'
        if excluded:
            next_step = "Supply supported customer SAM/DAG exports for their covered access evidence and retain a dated owner review or saved configuration for tenant sharing settings. Exports do not reconstruct every excluded administrative check."
        elif needs_admin_url:
            next_step = "Open the target tenant's SharePoint admin center and copy its HTTPS origin. Set SHAREPOINT_ADMIN_URL in the selected environment file or pass --sharepoint-admin-url, then rerun preflight and collection. The initial onmicrosoft.com domain does not verify the SharePoint hostname."
        else:
            next_step = ("Grant the Microsoft Graph application permission SharePointTenantSettings.Read.All for the "
                         "tenant sharing baseline (works with the existing client secret). For default link types, "
                         "anonymous link expiry, per-site settings and Data Access Governance reports, also run setup "
                         "with -EnableSharePointAppOnly (certificate) or allow the SharePoint administrator browser sign-in.")
        return [new_recommendation(
            service="M365",
            feature="SharePoint sharing and oversharing assessment",
            observation=(governance.get('reason') + ' ' if (excluded or needs_admin_url) and governance.get('reason') else '')
                + "SharePoint tenant sharing settings were not collected; permissive sharing defaults and Data Access Governance report status remain unverified.",
            recommendation=next_step,
            link_text="SharePoint Data Access Governance" if excluded else "Connect to SharePoint Online",
            link_url=("https://learn.microsoft.com/sharepoint/data-access-governance-reports" if excluded else
                      "https://learn.microsoft.com/powershell/module/microsoft.online.sharepoint.powershell/connect-sposervice"),
            priority="High",
            status=NOT_ASSESSED_STATUS,
            category=CATEGORY_SCAN_COVERAGE,
            finding_key="sharepoint.governance.not_assessed",
            evidence_key="sharepoint_governance_detail",
            evidence_basis="Collection status",
            confidence="High",
        )]

    settings = governance.get("settings") or {}
    sharing = setting_label("SharingCapability", settings.get("SharingCapability")) if "SharingCapability" in settings else ""
    onedrive_sharing = setting_label("OneDriveSharingCapability", settings.get("OneDriveSharingCapability")) if "OneDriveSharingCapability" in settings else ""
    # A default of None is resolved to the link type it produces (Set-SPOTenant).
    default_link = effective_default_link(settings)
    file_link = setting_name("FileAnonymousLinkType", settings.get("FileAnonymousLinkType"))
    folder_link = setting_name("FolderAnonymousLinkType", settings.get("FolderAnonymousLinkType"))
    expiration = _int(settings.get("RequireAnonymousLinksExpireInDays"), 0)
    permits_anyone = (setting_name("SharingCapability", settings.get("SharingCapability")) in ANYONE_VALUES
                      or setting_name("OneDriveSharingCapability", settings.get("OneDriveSharingCapability")) in ANYONE_VALUES)
    anonymous_default = default_link == "anonymousaccess"
    edit_anyone = file_link == "edit" or folder_link == "edit"
    conflicts = governance.get("source_conflicts_for_sharing") or []
    setting_results = evaluate_tenant_sharing(settings, conflicts)
    recommendations = []

    if permits_anyone and anonymous_default and expiration <= 0:
        recommendations.append(new_recommendation(
            service="M365",
            feature="SharePoint and OneDrive sharing defaults",
            observation=(
                f"SharePoint sharing is {sharing or 'not returned'} and OneDrive sharing is {onedrive_sharing or 'not returned'}. "
                f"Anyone links are the default, no tenant-wide expiration is enforced"
                + (", and anonymous file or folder links permit editing." if edit_anyone else ".")
            ),
            recommendation="Change the default sharing link to Specific people, require Anyone links to expire in 30 days or fewer, and set Anyone links to view only unless a documented business case requires editing. Then lower SharePoint and OneDrive external sharing to New and existing guests, after confirming which sites need an approved exception.",
            link_text="Manage external sharing",
            link_url="https://learn.microsoft.com/sharepoint/turn-external-sharing-on-or-off",
            priority="High",
            status="Action Required",
            finding_key="sharepoint.sharing.permissive_anonymous_defaults",
            evidence_key="sharepoint_governance_detail",
            evidence_summary="Tenant and site sharing settings were collected from SharePoint Online.",
            impact_area="Content access & grounding",
            ai_applicability="AI grounded in SharePoint or OneDrive can surface content to anyone the requesting user can access; permissive links increase unintended access paths.",
            evidence_basis="Tenant configuration",
            confidence="High",
        ))
    elif permits_anyone:
        site_note = (f" {governance.get('anyone_site_count', 0)} collected site(s) also permit Anyone links."
                     if governance.get("sites_available") else
                     " Per-site sharing settings were not collected, so the sites that allow Anyone links are not identified.")
        default_note = ""
        if anonymous_default:
            default_note = (f" Anyone links are the default link type and expire after {expiration} day(s)"
                            + (" with edit access allowed." if edit_anyone else "."))
        else:
            # Only returned settings are described; Graph does not return these.
            if "RequireAnonymousLinksExpireInDays" in settings and expiration <= 0:
                default_note += " Anyone links have no expiration requirement."
            if edit_anyone:
                default_note += " Anyone file or folder links permit edit access."
        recommendations.append(new_recommendation(
            service="M365",
            feature="SharePoint and OneDrive external sharing",
            observation="The tenant permits Anyone sharing." + default_note + site_note,
            recommendation="Confirm that Anyone sharing is required, review the sites where it is allowed, and use a recent Data Access Governance sharing-link report to measure actual exposure before broad AI deployment.",
            link_text="External sharing overview",
            link_url="https://learn.microsoft.com/sharepoint/external-sharing-overview",
            priority="Medium",
            status="Attention Required",
            finding_key="sharepoint.sharing.anyone_enabled",
            evidence_key="sharepoint_governance_detail",
            evidence_basis="Tenant and site configuration",
            confidence="High",
        ))

    if (not permits_anyone and default_link == "internal" and governance.get("coverage") != "partial"):
        recommendations.append(new_recommendation(
            service="M365",
            feature="SharePoint default sharing link",
            observation="Anyone links are off, but new sharing links default to People in your organization. Anyone in the organization who receives or redeems such a link can open the content, and Copilot can then use it in their answers.",
            recommendation="Change the default link type to Specific people so new links reach only the named recipients. Keep People in your organization available for content that is meant to be shared broadly.",
            link_text="Change the default sharing link",
            link_url="https://learn.microsoft.com/sharepoint/turn-external-sharing-on-or-off",
            priority="Medium",
            status="Attention Required",
            finding_key="sharepoint.sharing.organization_default",
            evidence_key="sharepoint_governance_detail",
            evidence_basis="Tenant configuration",
            confidence="High",
        ))

    # Tenant sharing configuration read from SharePoint administration answers
    # the sharing-defaults check. Without an explicit disposition, M365 rows
    # default to optional opportunities and never reach the pilot decision.
    for row in recommendations:
        if row.get("FindingKey") in {"sharepoint.sharing.permissive_anonymous_defaults", "sharepoint.sharing.anyone_enabled",
                                     "sharepoint.sharing.organization_default"}:
            row.update(Disposition="Action", ControlId="CONTENT.SHARING", DomainId="content",
                       SharingSettingResults=copy.deepcopy(setting_results))

    if _bool(settings.get("LegacyAuthProtocolsEnabled")):
        recommendations.append(new_recommendation(
            service="M365",
            feature="SharePoint legacy authentication",
            observation="SharePoint is configured to permit legacy authentication protocols. This setting alone does not prove that legacy sign-ins are occurring or bypassing Entra controls.",
            recommendation="Validate recent Entra sign-in evidence and effective Conditional Access coverage, then disable SharePoint legacy authentication after confirming that required clients and integrations use modern authentication.",
            link_text="Block legacy authentication",
            link_url="https://learn.microsoft.com/entra/identity/conditional-access/block-legacy-authentication",
            priority="Medium",
            status="Attention Required",
            finding_key="sharepoint.authentication.legacy_permitted",
            evidence_key="sharepoint_governance_detail",
            evidence_basis="Tenant configuration",
            confidence="Medium",
        ))

    if governance.get("coverage") == "partial":
        unverified = [name for name in governance.get("unavailable_fields") or [] if name not in settings]
        recommendations.append(new_recommendation(
            service="M365",
            feature="SharePoint sharing defaults (partial evidence)",
            observation=(
                "SharePoint tenant settings were read through Microsoft Graph with application permissions. "
                "Graph does not return: " + (", ".join(unverified) or "some sharing defaults")
                + ". Default link type, anonymous link expiry and per-site exceptions therefore remain unverified."
            ),
            recommendation=(
                "Confirm these settings in the SharePoint admin center, or collect them with SharePoint administration: "
                "run setup-service-principal.ps1 -EnableSharePointAppOnly (certificate) or allow the SharePoint "
                "administrator browser sign-in (--interactive-auth auto)."
            ),
            link_text="Manage sharing settings",
            link_url="https://learn.microsoft.com/sharepoint/turn-external-sharing-on-or-off",
            priority="Medium",
            status=NOT_ASSESSED_STATUS,
            category=CATEGORY_SCAN_COVERAGE,
            finding_key="sharepoint.sharing.partial_settings",
            evidence_key="sharepoint_governance_detail",
            evidence_basis="Collection status",
            confidence="High",
        ))

    recommendations.extend(build_copilot_content_control_recommendations(governance))

    required_dag_gaps = [
        row for row in (governance.get("dag_coverage") or [])[:5]
        if row.get("status") != "Recent"
    ]
    if not governance.get("dag_available") or required_dag_gaps:
        activity_states = governance.get("dag_activity_states") or []
        not_started = [
            _text(row.get("RequestedEntity") or row.get("ReportEntity"))
            for row in activity_states
            if _text(row.get("Status") or row.get("State")).lower() in {"notinitiated", "paused", "disabled"}
        ]
        collection_note = (
            " Recent-activity data collection is not ready for: " + ", ".join(filter(None, not_started)) + "."
            if not_started else ""
        )
        recommendations.append(new_recommendation(
            service="M365",
            feature="SharePoint Data Access Governance reports",
            observation=(
                "SharePoint Data Access Governance evidence is incomplete for: "
                + ", ".join(f"{row['report']} ({row['status'].lower()})" for row in required_dag_gaps)
                + ". Actual broad permissions and sharing-link activity remain unverified."
                + collection_note
            ),
            recommendation=(
                "SharePoint Advanced Management is optional. Without it, supply dated site permission and sharing-link inventories "
                "from SharePoint Online PowerShell or Microsoft Graph, with owner reviews, through the SharePoint governance evidence import. "
                "Where it is available, open SharePoint admin center > Reports > Data access governance. "
                "Check only the report types and scope listed in the gaps. Reuse current completed results; let running reports finish without starting duplicates. "
                "For missing or stale results, ask an administrator to enable activity-data collection where needed and generate the relevant permission-state, Everyone/EEEU or sharing-link reports. "
                "Do not wait in this tool: Microsoft's first permission-state report can take up to five days; later runs generally complete within 24 hours. "
                "Rerun this assessment after the reports show Completed; it will reuse and download the results. If a download fails, import the specific structured export with --reports-dir. A Completed status alone does not prove complete exposure coverage."
            ),
            link_text="Data Access Governance reports",
            link_url="https://learn.microsoft.com/sharepoint/data-access-governance-reports",
            priority="High",
            status=NOT_ASSESSED_STATUS,
            category=CATEGORY_SCAN_COVERAGE,
            finding_key="sharepoint.dag.not_assessed",
            evidence_key="sharepoint_governance_detail",
            evidence_basis="Report inventory",
            confidence="High",
        ))
        recommendations[-1]["DagGaps"] = [{"report": row["report"], "status": row["status"]} for row in required_dag_gaps]

    if conflicts:
        # Appended last so existing SharePoint display IDs keep their positions.
        described = "; ".join(f"{item['setting']} (SharePoint administration: {item.get('sharepoint_admin')}; "
                              f"Microsoft Graph: {item.get('graph')})" for item in conflicts)
        recommendations.append(new_recommendation(
            service="M365",
            feature="SharePoint sharing settings (conflicting sources)",
            observation=("SharePoint administration and Microsoft Graph returned different values for: " + described
                         + ". Both observations are retained; the assessment does not choose between them."),
            recommendation=("Confirm the current value in SharePoint admin center > Policies > Sharing and record a dated review, "
                            "or collect both sources again. The sharing-defaults check stays open until the values agree."),
            link_text="Manage sharing settings",
            link_url="https://learn.microsoft.com/sharepoint/turn-external-sharing-on-or-off",
            priority="Medium",
            status=NOT_ASSESSED_STATUS,
            category=CATEGORY_SCAN_COVERAGE,
            finding_key="sharepoint.sharing.conflicting_sources",
            evidence_key="sharepoint_governance_detail",
            evidence_basis="Conflicting tenant configuration",
            confidence="Unknown",
        ))
        recommendations[-1].update(Disposition="Coverage", ControlId="CONTENT.SHARING", DomainId="content",
                                   SourceConflicts=copy.deepcopy(conflicts))
    return recommendations


# Exported Data access governance reports (--reports-dir, --sam-report) that
# answer each report family the SharePoint collector looks for. The permission
# snapshot carries per-site permission state. Sharing activity is evaluated
# separately from that state and requires its own parsed report evidence.
_EXPORT_COVERAGE = (
    ("SharePoint permission state", lambda r: r.get("report_type") == "permission_snapshot" and _text(r.get("workload")).lower() == "sharepoint"),
    ("OneDrive permission state", lambda r: r.get("report_type") == "permission_snapshot" and _text(r.get("workload")).lower() == "onedrive"),
    ("Everyone and Everyone except external users", lambda r: r.get("report_type") == "special_group_permissions"),
)


def _dag_gaps(row):
    if isinstance(row.get("DagGaps"), list):
        return [dict(item) for item in row["DagGaps"] if isinstance(item, dict)]
    # Rows saved before DagGaps was recorded list the gaps in the observation.
    import re
    text = _text(row.get("Observation")).split("incomplete for: ", 1)[-1].split(". ", 1)[0]
    return [{"report": name.strip(), "status": status.strip()} for name, status in re.findall(r"([^,(]+)\(([^)]+)\)", text)]


def _parsed_dag_families(usable):
    covered = [label for label, answers in _EXPORT_COVERAGE if any(answers(report) for report in usable)]
    # Link counts in a permission snapshot are state, not sharing activity.
    entities = {str(report.get('collector_report_entity') or '').lower() for report in usable
                if report.get('workload') == 'SharePoint'}
    from .data_exposure_assessment import sharing_link_type
    manual_links = {sharing_link_type(report.get('source_file', '')) for report in usable
                    if report.get('report_type') == 'sharing_links_activity' and report.get('workload') == 'SharePoint'}
    if {'sharinglinks_anyone', 'sharinglinks_guests'} <= entities or {'Anyone links', 'Specific people links shared externally'} <= manual_links:
        covered.append('Anyone and guest sharing links')
    if {'everyoneexceptexternalusersatsite', 'everyoneexceptexternalusersforitems'} <= entities:
        covered.append('Everyone except external users activity')
    return covered


def reconcile_dag_coverage(recommendations, sam_reports):
    """Separate missing report families from the SAM date-maintenance reminder."""
    parsed = [report for report in sam_reports or [] if report.get('status') in {'selected', 'no_activity'}]
    available = _parsed_dag_families(parsed)
    covered = _parsed_dag_families([report for report in parsed if report.get('freshness') == 'fresh'])
    if not available:
        return list(recommendations or [])
    reconciled = []
    for row in recommendations or []:
        if row.get("FindingKey") != "sharepoint.dag.not_assessed":
            reconciled.append(row)
            continue
        gaps = _dag_gaps(row)
        remaining = [gap for gap in gaps if gap["report"] not in available]
        answered = [gap["report"] for gap in gaps if gap["report"] in available]
        date_review = [name for name in answered if name not in covered]
        date_note = (' Date confirmation or freshness review remains for: '+', '.join(date_review)+
                     '; see the separate report-date reminder.') if date_review else ''
        if not answered:
            reconciled.append(row)
            continue
        if not remaining:
            reconciled.append(dict(row, Disposition="Reference", Status="Insight", Priority='Low',
                                   Observation="Parsed Data access governance reports from automatic collection and/or supplied exports answer the previously missing report families: "
                                               + ", ".join(answered) + ". Conclusions remain limited to their retained dates, objects and scope."+date_note, Recommendation="", DagGaps=[],
                                   DagCoveredReports=[name for name in answered if name in covered], DagDateReviewReports=date_review))
            continue
        reconciled.append(dict(
            row, DagGaps=remaining,
            Observation=("SharePoint Data Access Governance evidence is incomplete for: "
                         + ", ".join(f"{gap['report']} ({gap['status'].lower()})" for gap in remaining)
                         + ". Parsed collected/imported reports answer: " + ", ".join(answered) + ". Conclusions apply to the exported scope."+date_note),
            DagCoveredReports=[name for name in answered if name in covered], DagDateReviewReports=date_review,
            Recommendation='Review only the remaining report families: '+', '.join(gap['report'] for gap in remaining)+'. '
                'Reuse completed results through automatic collection. Check failed downloads, unrecognized formats, report dates and selected scope before requesting new reports. '
                'Use a structured manual export only where automatic collection remains unavailable.',
        ))
    return reconciled

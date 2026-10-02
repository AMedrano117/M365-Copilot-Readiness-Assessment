"""Tenant-wide baseline checks for the pilot decision (methodology 3.0).

A pilot group is chosen from the tenant's own users, so a control that is
enforced for all users covers any pilot group. The assessment therefore judges
each required control on tenant-wide configuration and never needs the pilot
roster. Each check reads collected configuration and returns explicit rows:

* ``Assurance`` - the control is met tenant-wide (the control passes);
* ``Action`` - a tenant condition to fix (the control fails);
* ``Coverage`` - the configuration was not readable, or it is scoped to groups
  the tool cannot map to users; confirm it before the pilot starts.

Checks never infer a result from a license, a policy name or a policy that is
disabled, in simulation or report-only mode. Rows from checks whose source was
not collected are left to the shared gap logic, which explains how to collect it.
"""

import json

from .new_recommendation import new_recommendation
from .source_evidence import source_is_complete

OFFICE_365_APP_IDS = {"Office365", "00000003-0000-0ff1-ce00-000000000000"}
LEGACY_CLIENTS = {"exchangeActiveSync", "other"}
M365_DLP_LOCATIONS = (
    ("ExchangeLocation", "Exchange email"), ("SharePointLocation", "SharePoint"),
    ("OneDriveLocation", "OneDrive"), ("TeamsLocation", "Teams chat"),
)
COPILOT_DLP_LOCATION = "copilot.m365"
CONTROL_DOMAINS = {"IDENTITY": "identity", "CONTENT": "content", "DATA": "data_protection",
                   "APPS": "applications", "ENDPOINT": "endpoints", "THREAT": "endpoints", "LICENSE": "licensing"}


def _get(obj, key, default=None):
    """Read a Graph/PowerShell field from a dict or a decoded object."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        value = obj.get(key)
    else:
        value = getattr(obj, key, None)
    return default if value is None else value


def _list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _names(items, limit=3):
    items = [str(item) for item in items if item]
    shown = ", ".join(f"'{item}'" for item in items[:limit])
    return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")


def _row(service, control_id, feature, observation, recommendation, *, disposition, priority="Medium",
         finding_key, evidence_key, source, complete=True, scope, date="", stage=None):
    status = {"Assurance": "Success", "Action": "Action Required", "Coverage": "Not Assessed"}[disposition]
    row = new_recommendation(
        service, feature, observation, recommendation, priority=priority, status=status,
        disposition=disposition, finding_key=finding_key, evidence_key=evidence_key,
        evidence_basis="Tenant-wide configuration" if disposition != "Coverage" else "Not verified",
        confidence="High" if disposition != "Coverage" else "Medium",
    )
    row.update(ControlId=control_id, DomainId=CONTROL_DOMAINS[control_id.split(".")[0]], EvidenceSource=source,
               EvidenceComplete=complete, EvidenceScope=scope, ObservationDate=date or "", BaselineCheck=True)
    if stage:
        row["ReadinessStage"] = stage
    return row


# --- Identity: Conditional Access and security defaults -------------------------------

def conditional_access_facts(policies):
    """Classify Conditional Access policies by what they enforce for everyone."""
    facts = {"enforced_mfa_all": [], "report_only_mfa_all": [], "enforced_mfa_scoped": [],
             "legacy_block_all": [], "report_only_legacy_block": [], "mfa_all_exclusions": 0}
    for policy in _list(policies):
        state = str(_get(policy, "state", "")).lower()
        name = _get(policy, "displayName", "") or "Unnamed policy"
        conditions = _get(policy, "conditions", {}) or {}
        users = _get(conditions, "users", {}) or {}
        applications = _get(conditions, "applications", {}) or {}
        grant = _get(policy, "grantControls", {}) or {}
        controls = {str(item) for item in _list(_get(grant, "builtInControls", []))}
        all_users = "All" in _list(_get(users, "includeUsers", []))
        apps = set(map(str, _list(_get(applications, "includeApplications", []))))
        all_apps = "All" in apps or bool(apps & OFFICE_365_APP_IDS)
        requires_mfa = "mfa" in controls or bool(_get(grant, "authenticationStrength"))
        clients = set(map(str, _list(_get(conditions, "clientAppTypes", []))))
        blocks_legacy = "block" in controls and bool(clients & LEGACY_CLIENTS)
        enforced = state == "enabled"
        report_only = state in {"enabledforreportingbutnotenforced", "enabled_for_reporting_but_not_enforced"}
        if requires_mfa and all_users and all_apps:
            if enforced:
                facts["enforced_mfa_all"].append(name)
                facts["mfa_all_exclusions"] += (len(_list(_get(users, "excludeUsers", [])))
                                                + len(_list(_get(users, "excludeGroups", []))))
            elif report_only:
                facts["report_only_mfa_all"].append(name)
        elif requires_mfa and enforced and all_apps:
            facts["enforced_mfa_scoped"].append(name)
        if blocks_legacy and all_users:
            if enforced:
                facts["legacy_block_all"].append(name)
            elif report_only:
                facts["report_only_legacy_block"].append(name)
    return facts


def assess_identity_baseline(entra_client, collected_at=""):
    if entra_client is None:
        return []
    defaults = getattr(entra_client, "security_defaults", {}) or {}
    defaults_on = _get(defaults, "is_enabled") is True
    scope = "Conditional Access policies and security defaults for the assessed tenant"
    if not source_is_complete(entra_client, "ca_policies"):
        if defaults_on:
            return [_row("Entra", "IDENTITY.AUTH", "Require MFA and block legacy sign-in for all users",
                         "Security defaults are enabled: every user must register for and use multifactor authentication, "
                         "and legacy authentication is blocked tenant-wide.", "",
                         disposition="Assurance", priority="Low", finding_key="baseline.identity.sign_in",
                         evidence_key="conditional_access_detail", source="security_defaults",
                         scope=scope, date=collected_at)]
        return []
    facts = conditional_access_facts(getattr(entra_client, "ca_policies", []) or [])
    feature = "Require MFA and block legacy sign-in for all users"
    mfa_all = facts["enforced_mfa_all"]
    legacy = facts["legacy_block_all"]
    if (mfa_all or defaults_on) and (legacy or defaults_on):
        parts = []
        if mfa_all:
            exclusions = facts["mfa_all_exclusions"]
            parts.append(f"Conditional Access requires MFA for all users and cloud apps ({_names(mfa_all)}"
                         + (f"; {exclusions} excluded user or group assignment(s) to review" if exclusions else "") + ")")
        elif defaults_on:
            parts.append("Security defaults require MFA for all users")
        parts.append(f"legacy authentication is blocked for all users ({_names(legacy)})" if legacy
                     else "security defaults block legacy authentication")
        return [_row("Entra", "IDENTITY.AUTH", feature, "; ".join(parts) + ".", "",
                     disposition="Assurance", priority="Low", finding_key="baseline.identity.sign_in",
                     evidence_key="conditional_access_detail", source="ca_policies", scope=scope, date=collected_at)]
    gaps, fixes = [], []
    if not (mfa_all or defaults_on):
        detail = "No enforced Conditional Access policy requires MFA for all users and cloud apps, and security defaults are off"
        if facts["report_only_mfa_all"]:
            detail += f". {_names(facts['report_only_mfa_all'])} would do so but {'is' if len(facts['report_only_mfa_all']) == 1 else 'are'} in report-only mode (not enforced)"
            fixes.append("review the report-only sign-in results, then switch the all-users MFA policy to On with break-glass accounts excluded")
        else:
            fixes.append("create a Conditional Access policy that requires MFA for all users and all cloud apps, excluding only break-glass accounts")
        if facts["enforced_mfa_scoped"]:
            detail += f". {len(facts['enforced_mfa_scoped'])} enforced MFA polic{'y targets' if len(facts['enforced_mfa_scoped']) == 1 else 'ies target'} specific users or groups only"
        gaps.append(detail)
    if not (legacy or defaults_on):
        detail = "No enforced policy blocks legacy authentication for all users"
        if facts["report_only_legacy_block"]:
            detail += f" ({_names(facts['report_only_legacy_block'])} is report-only)"
        gaps.append(detail)
        fixes.append("block legacy authentication (Exchange ActiveSync and other clients) for all users")
    priority = "High" if not (mfa_all or defaults_on) else "Medium"
    recommendation = "Before the pilot, " + "; and ".join(fixes) + ". Copilot acts with the signed-in user's access, so every account needs strong sign-in protection."
    return [_row("Entra", "IDENTITY.AUTH", feature, ". ".join(gaps) + ".", recommendation,
                 disposition="Action", priority=priority, finding_key="baseline.identity.sign_in",
                 evidence_key="conditional_access_detail", source="ca_policies", scope=scope, date=collected_at)]


def enforced_mfa_policy_count(policies):
    """Enabled (enforced) policies that require MFA; report-only and disabled policies do not count."""
    count = 0
    for policy in _list(policies):
        grant = _get(policy, "grantControls", {}) or {}
        if str(_get(policy, "state", "")).lower() == "enabled" and (
                "mfa" in {str(item) for item in _list(_get(grant, "builtInControls", []))} or _get(grant, "authenticationStrength")):
            count += 1
    return count


# --- Data protection: DLP, label publishing and retention -------------------------------

def _all_location(value):
    for item in _list(value):
        name = _get(item, "Name", item) if isinstance(item, dict) else item
        if str(name).strip().lower() == "all":
            return True
    return False


def _unified_locations(policy):
    raw = _get(policy, "Locations", "")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else []
        except ValueError:
            raw = []
    return [item for item in _list(raw) if isinstance(item, dict)]


def dlp_facts(policies):
    facts = {"total": 0, "enabled": 0, "enforced": [], "enforced_tenant": [], "workloads": set(),
             "copilot_enforced": [], "copilot_simulation": [], "scoped_enforced": []}
    for policy in _list(policies):
        if not isinstance(policy, dict) and not hasattr(policy, "__dict__"):
            continue
        facts["total"] += 1
        enabled = _get(policy, "Enabled") is True or str(_get(policy, "Enabled", "")).lower() == "true"
        mode = str(_get(policy, "Mode", "")).lower()
        name = _get(policy, "DisplayName") or _get(policy, "Name") or "Unnamed policy"
        if enabled:
            facts["enabled"] += 1
        unified = _unified_locations(policy)
        copilot = any(COPILOT_DLP_LOCATION in str(_get(item, "Location", "")).lower() for item in unified)
        tenant_unified = any(any(_get(inc, "Identity") == "All" or _get(inc, "Type") == "Tenant"
                                 for inc in _list(_get(item, "Inclusions", []))) for item in unified)
        workloads = {label for field, label in M365_DLP_LOCATIONS if _all_location(_get(policy, field))}
        if copilot and enabled and mode == "enable":
            facts["copilot_enforced"].append(name)
        elif copilot and enabled:
            facts["copilot_simulation"].append(name)
        if not (enabled and mode == "enable"):
            continue
        facts["enforced"].append(name)
        if workloads or (copilot and tenant_unified):
            facts["enforced_tenant"].append(name)
            facts["workloads"].update(workloads)
            if copilot:
                facts["workloads"].add("Microsoft 365 Copilot")
        elif any(_list(_get(policy, field)) for field, _ in M365_DLP_LOCATIONS):
            facts["scoped_enforced"].append(name)
    return facts


def _purview_row(client, control_id, feature, observation, recommendation, *, disposition, priority="Medium",
                 finding_key, source, date):
    return _row("Purview", control_id, feature, observation, recommendation, disposition=disposition, priority=priority,
                finding_key=finding_key, evidence_key="purview_policy_detail", source="purview_" + source,
                scope="Tenant policy configuration returned by Purview", date=date)


def assess_data_protection_baseline(purview_client, collected_at=""):
    if purview_client is None:
        return []
    provenance = getattr(purview_client, "cache_provenance", {}) or {}
    date = provenance.get("collected_at") or collected_at or ""
    rows = []

    dlp = getattr(purview_client, "dlp_policies", {}) or {}
    policies = _list(dlp.get("policies") if isinstance(dlp, dict) else [])
    if source_is_complete(purview_client, "dlp_policies", dlp) and policies:
        facts = dlp_facts(policies)
        if facts["enforced_tenant"]:
            copilot = (f" {len(facts['copilot_enforced'])} enforced polic{'y applies' if len(facts['copilot_enforced']) == 1 else 'ies apply'} "
                       f"to Microsoft 365 Copilot ({_names(facts['copilot_enforced'], 2)})." if facts["copilot_enforced"] else "")
            rows.append(_purview_row(purview_client, "DATA.DLP", "Data loss prevention is enforced tenant-wide",
                f"{len(facts['enforced'])} of {facts['total']} DLP policies are enforced; {len(facts['enforced_tenant'])} apply to all "
                f"users or locations for {', '.join(sorted(facts['workloads']))}.{copilot}", "",
                disposition="Assurance", priority="Low", finding_key="baseline.dlp.enforced", source="dlp_policies", date=date))
            if not facts["copilot_enforced"]:
                opportunity = new_recommendation(
                    "Purview", "Add a DLP policy for Microsoft 365 Copilot",
                    ("No enforced DLP policy targets the Microsoft 365 Copilot location"
                     + (f"; {_names(facts['copilot_simulation'], 2)} {'is' if len(facts['copilot_simulation']) == 1 else 'are'} in simulation mode" if facts["copilot_simulation"] else "")
                     + ". Copilot DLP can stop Copilot from processing files and emails with selected sensitivity labels, and can block prompts that contain sensitive information."),
                    "Review the simulation results, then enforce a DLP policy on the Microsoft 365 Copilot location for the labels and sensitive information types the business agrees on.",
                    priority="Medium", status="Insight", disposition="Opportunity", finding_key="baseline.dlp.copilot_location",
                    evidence_key="purview_policy_detail", evidence_basis="Tenant-wide configuration", confidence="High")
                opportunity.update(ControlId="DATA.DLP", EvidenceSource="purview_dlp_policies", EvidenceComplete=True,
                                   EvidenceScope="Tenant policy configuration returned by Purview", ObservationDate=date)
                rows.append(opportunity)
        elif facts["scoped_enforced"]:
            rows.append(_purview_row(purview_client, "DATA.DLP", "Confirm the scope of enforced DLP policies",
                f"{len(facts['scoped_enforced'])} enforced DLP polic{'y is' if len(facts['scoped_enforced']) == 1 else 'ies are'} limited to specific "
                f"users, groups or sites ({_names(facts['scoped_enforced'])}); none applies to all users.",
                "Confirm that the pilot users and the content they work with are inside these policies' scope, or extend a policy to all users.",
                disposition="Coverage", finding_key="baseline.dlp.scoped", source="dlp_policies", date=date))

    labels = getattr(purview_client, "label_policies", {}) or {}
    label_policies = _list(labels.get("policies") if isinstance(labels, dict) else [])
    if source_is_complete(purview_client, "label_policies", labels) and label_policies:
        active = [policy for policy in label_policies
                  if _get(policy, "Enabled") is not False and str(_get(policy, "Mode", "")).lower() not in {"pendingdeletion", "disable", "disabled"}]
        names = [_get(policy, "Name", "") for policy in active]
        has_scope = any(_get(policy, "ExchangeLocation") is not None or _get(policy, "ExchangeLocationCount") is not None
                        for policy in label_policies)
        to_everyone = [_get(policy, "Name", "") for policy in active if _all_location(_get(policy, "ExchangeLocation"))]
        feature = "Confirm sensitivity labels are published to users"
        if not active:
            rows.append(_purview_row(purview_client, "DATA.PUBLISHING", feature,
                f"None of the {len(label_policies)} label publishing policies is active (disabled or pending deletion), so no labels are available to users.",
                "Publish the agreed sensitivity labels to all users, or at least to the pilot group, with a default label for new content.",
                disposition="Action", finding_key="baseline.labels.published", source="label_policies", date=date))
        elif to_everyone:
            rows.append(_purview_row(purview_client, "DATA.PUBLISHING", "Sensitivity labels are published to all users",
                f"{len(active)} active label publishing polic{'y' if len(active) == 1 else 'ies'}; {_names(to_everyone, 2)} publish{'es' if len(to_everyone) == 1 else ''} labels to all users.", "",
                disposition="Assurance", priority="Low", finding_key="baseline.labels.published", source="label_policies", date=date))
        else:
            reason = ("publish to specific users or groups only" if has_scope else
                      "were collected without their publishing scope (collections made before assessment version 3.0 do not record it)")
            rows.append(_purview_row(purview_client, "DATA.PUBLISHING", feature,
                f"{len(active)} active label publishing polic{'y' if len(active) == 1 else 'ies'} ({_names(names, 2)}) {reason}.",
                "Confirm in the Microsoft Purview portal (Information protection > Publishing policies) that the pilot users receive the labels, "
                "or rerun collection to record the publishing scope.",
                disposition="Coverage", finding_key="baseline.labels.published", source="label_policies", date=date))

    retention = getattr(purview_client, "retention_labels", {}) or {}
    retention_rows = _list(retention.get("labels") if isinstance(retention, dict) else [])
    if source_is_complete(purview_client, "retention_labels", retention):
        enabled = [row for row in retention_rows if _get(row, "Enabled") is not False]
        if enabled:
            rows.append(_purview_row(purview_client, "DATA.RETENTION", "Retention policies are configured",
                f"{len(enabled)} enabled retention polic{'y' if len(enabled) == 1 else 'ies'} ({_names([_get(row, 'Name', '') for row in enabled])}). "
                "Check that one covers Copilot and AI app interactions if the business needs to keep or delete them on a schedule.", "",
                disposition="Assurance", priority="Low", finding_key="baseline.retention.configured", source="retention_policies", date=date))
        else:
            rows.append(_purview_row(purview_client, "DATA.RETENTION", "Decide retention for Copilot interactions and content",
                "No enabled retention policy was returned. Without one, Copilot prompts and responses and business content follow default deletion by users.",
                "Agree with the records or compliance owner how long Copilot interactions and business content must be kept, then configure retention policies. "
                "This is a planning condition; it does not block a pilot.",
                disposition="Action", priority="Medium", finding_key="baseline.retention.configured", source="retention_policies", date=date))
    return rows


# --- Applications and licensing ---------------------------------------------------------

def assess_m365_baseline(m365_client, collected_at=""):
    if m365_client is None:
        return []
    rows = []
    scope = "Assessed tenant"
    if source_is_complete(m365_client, "external_connections"):
        connections = _list(getattr(m365_client, "external_connections", []) or [])
        if not connections:
            rows.append(_row("M365", "APPS.CONNECTIONS", "No Copilot connectors are configured",
                "Microsoft Graph returned no Copilot (Graph) connectors, so Copilot answers only from Microsoft 365 content the user can already open.", "",
                disposition="Assurance", priority="Low", finding_key="baseline.connectors.inventory",
                evidence_key="external_connection_detail", source="m365_external_connections", scope=scope, date=collected_at))
        else:
            names = [_get(item, "name") or _get(item, "id", "") for item in connections]
            rows.append(_row("M365", "APPS.CONNECTIONS", "Review Copilot connectors before the pilot",
                f"{len(connections)} Copilot connector(s) bring external content into Microsoft 365 search and Copilot ({_names(names)}).",
                "Confirm each connector's owner, the content it indexes and how item permissions are mapped, so pilot users only see external content they are entitled to.",
                disposition="Coverage", finding_key="baseline.connectors.inventory",
                evidence_key="external_connection_detail", source="m365_external_connections", scope=scope, date=collected_at))
    coverage = getattr(m365_client, "license_coverage", {}) or {}
    capacity = _get(coverage, "copilot_subscription_capacity", {}) or {}
    if _get(capacity, "available") is True:
        seats = _get(capacity, "enabled_seats", 0) or 0
        assigned = _get(capacity, "assigned_seats", 0) or 0
        if seats > 0:
            rows.append(_row("M365", "LICENSE.ASSIGNMENT", "Copilot licenses are available",
                f"The tenant has {seats} Microsoft 365 Copilot license(s): {assigned} assigned and {max(seats - assigned, 0)} unassigned.",
                "", disposition="Assurance", priority="Low", finding_key="baseline.license.capacity",
                evidence_key="copilot_readiness_detail", source="m365_license_coverage", scope=scope, date=collected_at))
        else:
            rows.append(_row("M365", "LICENSE.ASSIGNMENT", "Acquire Copilot licenses for the pilot",
                "No Microsoft 365 Copilot subscription was found in the tenant.",
                "Purchase or trial enough Microsoft 365 Copilot licenses for the pilot group, and assign them through a group. "
                "This is a procurement step, not a security condition.",
                disposition="Coverage", finding_key="baseline.license.capacity",
                evidence_key="copilot_readiness_detail", source="m365_license_coverage", scope=scope, date=collected_at))
    return rows


def assess_tenant_baseline(*, m365_client=None, entra_client=None, purview_client=None, collected_at=""):
    """All tenant-wide baseline rows for the available clients."""
    return [
        *assess_identity_baseline(entra_client, collected_at),
        *assess_data_protection_baseline(purview_client, collected_at),
        *assess_m365_baseline(m365_client, collected_at),
    ]

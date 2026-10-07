"""Sensitivity labels through Microsoft Graph (application permission).

``GET /beta/security/informationProtection/sensitivityLabels`` returns the
organization's label definitions to an application with
``InformationProtectionPolicy.Read.All``; a client secret is sufficient. It is
a beta API and does not return label publishing policies, DLP, retention or
audit configuration, so the evidence is marked preview quality and partial
coverage. It never proves that labels are published to users.
"""

from .auth_plan import credential_kind_for_path, graph_credential_kind

AUTH_PATH = "graph_sensitivity_labels"
SOURCE = "Microsoft Graph sensitivity labels (beta)"
LABELS_PATH = "/beta/security/informationProtection/sensitivityLabels"

# Datasets that only Purview/Exchange PowerShell returns.
REQUIRED_POWERSHELL_SOURCES = ("dlp_policies", "dlp_rules", "retention_policies", "label_policies",
                               "org_config", "irm_config", "audit_config")
OPTIONAL_POWERSHELL_SOURCES = ("insider_risk_policies", "communication_compliance",
                               "information_barriers", "ediscovery_cases")
_ITEM_KEYS = {"dlp_policies": "policies", "dlp_rules": "rules", "retention_policies": "policies",
              "label_policies": "policies", "insider_risk_policies": "policies",
              "communication_compliance": "policies", "information_barriers": "policies",
              "ediscovery_cases": "cases"}

POWERSHELL_UNLOCK = (
    "Consent Exchange.ManageAsApp and assign the default read-only role (setup-service-principal.ps1 -WorkloadRbac "
    "GlobalReader) for application tokens, configure a Purview certificate, or allow the administrator browser "
    "sign-in (--interactive-auth auto)."
)


def map_graph_label(item):
    parent = item.get("parent") if isinstance(item.get("parent"), dict) else {}
    return {
        "Name": item.get("name") or "",
        "DisplayName": item.get("name") or "",
        "Tooltip": item.get("tooltip") or "",
        "Enabled": bool(item.get("isActive")),
        "Guid": item.get("id") or "",
        "ParentId": parent.get("id") or "",
        "Priority": item.get("sensitivity"),
        "HasProtection": item.get("hasProtection"),
        "ContentFormats": item.get("contentFormats") or [],
        "IsAppliable": item.get("isAppliable"),
    }


async def collect_sensitivity_labels_graph(graph_client):
    """Return a Purview-shaped ``sensitivity_labels`` dataset from Graph; never raises."""
    credential_type = credential_kind_for_path(AUTH_PATH, {"graph_credential": graph_credential_kind()})
    base = {"source": SOURCE, "auth_path_id": AUTH_PATH, "credential_type": credential_type,
            "coverage": "partial", "evidence_quality": "preview", "optional": False}
    try:
        result = await graph_client.get_collection(LABELS_PATH)
    except Exception as exc:
        result = {"available": False, "availability_status": "unavailable", "value": [],
                  "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
    if not result.get("available"):
        reason = result.get("reason") or "Microsoft Graph did not return sensitivity labels."
        if result.get("status_code") == 403:
            reason = ("Microsoft Graph denied sensitivity labels (HTTP 403). Grant and consent the "
                      "InformationProtectionPolicy.Read.All application permission.")
        return {**base, "available": False, "availability_status": "unavailable", "count": 0, "labels": [],
                "reason": reason, "error_category": "permission_denied" if result.get("status_code") == 403 else "collection_error",
                "status_code": result.get("status_code")}
    labels = [map_graph_label(item) for item in result.get("value", []) if isinstance(item, dict)]
    return {
        **base,
        "available": True,
        "availability_status": "partial" if result.get("truncated") else "available",
        "count": len(labels),
        "active_count": sum(1 for label in labels if label["Enabled"]),
        "labels": labels,
        # Beta API: definitions are observed, but publishing and enforcement are not.
        "complete": False,
        "truncated": bool(result.get("truncated")),
        "reason": ("Label definitions read through the Microsoft Graph beta API. Label publishing policies, "
                   "DLP, retention and audit configuration require Purview PowerShell."),
    }


def merge_purview_payloads(powershell_payload, graph_labels, powershell_reason=""):
    """Combine Purview PowerShell output with the Graph label baseline.

    PowerShell label definitions win when present; the Graph count is kept as a
    cross-check. Without PowerShell, the Graph labels are used and every other
    Purview dataset is marked unavailable with the steps that unlock it.
    Returns ``None`` when neither source produced anything.
    """
    graph_labels = graph_labels if isinstance(graph_labels, dict) else None
    if isinstance(powershell_payload, dict) and powershell_payload:
        merged = dict(powershell_payload)
        ps_labels = merged.get("sensitivity_labels") if isinstance(merged.get("sensitivity_labels"), dict) else {}
        if graph_labels and graph_labels.get("available"):
            if ps_labels.get("available"):
                merged["sensitivity_labels"] = {**ps_labels, "graph_label_count": graph_labels.get("count")}
            else:
                replacement = dict(graph_labels)
                replacement["powershell_reason"] = ps_labels.get("reason", "")
                merged["sensitivity_labels"] = replacement
        return merged
    if not graph_labels or not graph_labels.get("available"):
        # Nothing was collected; the pipeline reports Purview as not assessed.
        return None
    reason = "Requires Purview or Exchange PowerShell, which did not run." + (f" {powershell_reason}" if powershell_reason else "")
    merged = {"source": SOURCE, "authentication": "", "sensitivity_labels": dict(graph_labels)}
    for key in REQUIRED_POWERSHELL_SOURCES:
        entry = {"available": False, "availability_status": "unavailable", "error_category": "no_auth_path",
                 "reason": reason, "required_role": "", "optional": False, "auth_path_id": "purview_ps_token",
                 "unlock": POWERSHELL_UNLOCK}
        if key in _ITEM_KEYS:
            entry[_ITEM_KEYS[key]] = []
        merged[key] = entry
    for key in OPTIONAL_POWERSHELL_SOURCES:
        merged[key] = {"available": False, "availability_status": "not_requested",
                       "error_category": "optional_not_requested", "reason": "Requires Purview PowerShell.",
                       "required_role": "", "optional": True, _ITEM_KEYS[key]: []}
    merged["collection_summary"] = {
        "required_failures": [{"source": key, "category": "no_auth_path", "reason": reason, "required_role": ""}
                              for key in REQUIRED_POWERSHELL_SOURCES],
        "optional_failures": [],
    }
    return merged

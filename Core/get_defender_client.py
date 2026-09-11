"""Focused Defender and Microsoft Graph Security collectors.

Only evidence that can support the AI-readiness decision is collected here: XDR
alerts/incidents, Secure Score, and Defender for Endpoint onboarding/risk state.
External-AI activity is deliberately collected by Defender Cloud Apps discovery in
``ai_usage.py``; ordinary Office traffic, files, and mail are not treated as AI use.
"""

import asyncio

from .get_graph_client import GraphRequestError, get_api_client
from .spinner import get_timestamp, _stdout_lock


class DefenderClient:
    def __init__(self):
        self.available = False
        self.graph_security_available = False
        self.defender_api_available = False
        self.activation_needed = False
        self.activation_message = ""
        self.missing_features = []
        self.data_sources = {}
        self.collection_status = {}

        self.security_alerts = []
        self.alert_summary = {
            "total": 0, "by_severity": {}, "by_category": {}, "copilot_related": 0
        }
        self.security_incidents = []
        self.incident_summary = {
            "total": 0, "active": 0, "resolved": 0, "high_severity": 0
        }
        self.secure_score = {}
        self.secure_score_summary = {"current_score": 0, "max_score": 0, "percentage": 0}
        self.secure_score_controls = []
        self.control_summary = {"total": 0, "implemented": 0, "not_implemented": 0}
        self.identity_controls = []
        self.data_controls = []
        self.control_focus_summary = {
            "identity_controls": 0, "data_controls": 0, "copilot_relevant": 0
        }

        self.defender_devices = []
        self.device_summary = {
            "total": 0, "high_risk": 0, "medium_risk": 0,
            "low_risk": 0, "copilot_enabled": 0,
        }

        # Compatibility fields remain explicitly empty. Their source is not read by this
        # focused collector, so downstream code can distinguish unavailable from zero.
        self.defender_incidents = []
        self.defender_incident_summary = {"total": 0, "in_progress": 0, "new": 0}
        self.defender_vulnerabilities = []
        self.vulnerability_summary = {
            "total": 0, "critical": 0, "high": 0, "medium": 0, "low": 0
        }
        self.advanced_hunting_results = {}
        self.copilot_process_events = {}
        self.copilot_network_events = {}
        self.copilot_file_access_events = {}
        self.copilot_email_threats = {}
        self.hunting_summary = {
            "suspicious_processes": 0, "unusual_network_activity": 0,
            "sensitive_file_access": 0, "phishing_attempts": 0,
            "affected_devices": 0, "affected_users": 0,
        }
        self.risky_users = []
        self.risky_users_summary = {
            "total": 0, "high": 0, "medium": 0, "low": 0,
            "confirmed_compromised": 0,
        }
        self.risky_sign_ins = []
        self.risky_sign_ins_summary = {"total": 0, "high_risk": 0, "medium_risk": 0}
        self.oauth_apps = []
        self.oauth_risk_summary = {
            "total_apps": 0, "high_risk": 0, "medium_risk": 0, "over_privileged": 0
        }
        self.dlp_incidents = []
        self.dlp_incident_summary = {
            "total": 0, "high_severity": 0, "copilot_related": 0
        }
        self.email_threats = []
        self.email_threat_summary = {"total": 0, "phishing": 0, "malware": 0, "spam": 0}
        self.security_recommendations = []
        self.recommendations_summary = {
            "total": 0, "critical": 0, "high": 0, "copilot_related": 0
        }
        self.software_inventory = []
        self.software_summary = {"total_apps": 0, "copilot_apps": 0, "vulnerable_apps": 0}
        self.exposure_score = {}
        self.exposure_summary = {"score": 0, "level": "Unknown", "trend": "Unknown"}


def _status(result):
    return {
        "availability_status": result.get("availability_status", "unavailable"),
        "available": bool(result.get("available")),
        "records_collected": int(result.get("records_collected", 0) or 0),
        "pages_collected": int(result.get("pages_collected", 0) or 0),
        "truncated": bool(result.get("truncated")),
        "reason": result.get("reason", "") or result.get("error", ""),
        "status_code": result.get("status_code"),
    }


def _count(items, field):
    result = {}
    for item in items:
        value = str(item.get(field, "Unknown") or "Unknown")
        result[value] = result.get(value, 0) + 1
    return result


async def _collect_mde_machines():
    items = []
    pages = 0
    http = None
    try:
        http = await get_api_client("defender")
        next_url = "/api/machines"
        while next_url and pages < 1000:
            response = await http.get(next_url)
            pages += 1
            if response.status_code >= 400:
                message = ""
                try:
                    message = response.json().get("error", {}).get("message", "")
                except Exception:
                    message = response.text[:300]
                return {
                    "available": bool(items),
                    "availability_status": "partial" if items else "unavailable",
                    "value": items,
                    "records_collected": len(items),
                    "pages_collected": pages - 1 if not items else pages,
                    "truncated": bool(items),
                    "status_code": response.status_code,
                    "reason": message or f"Defender returned HTTP {response.status_code}",
                }
            payload = response.json()
            items.extend(payload.get("value", []) or [])
            next_url = payload.get("@odata.nextLink") or payload.get("nextLink")
        return {
            "available": True,
            "availability_status": "partial" if next_url else "available",
            "value": items,
            "records_collected": len(items),
            "pages_collected": pages,
            "truncated": bool(next_url),
            "reason": "Pagination safety limit reached" if next_url else "",
        }
    except Exception as exc:
        return {
            "available": False, "availability_status": "unavailable", "value": [],
            "records_collected": 0, "pages_collected": pages, "truncated": False,
            "status_code": getattr(exc, "status_code", None), "reason": str(exc),
        }
    finally:
        if http is not None:
            await http.aclose()


async def get_defender_client(tenant_id, graph_client):
    """Collect focused Graph Security and Defender for Endpoint evidence."""
    client = DefenderClient()
    graph_requests = {
        "alerts": ("/v1.0/security/alerts_v2", {"$top": "999"}),
        "incidents": ("/v1.0/security/incidents", {"$top": "999"}),
        "secure_scores": ("/v1.0/security/secureScores", {"$top": "30"}),
        "secure_score_controls": (
            "/v1.0/security/secureScoreControlProfiles", {"$top": "999"}
        ),
    }
    tasks = {
        name: graph_client.get_collection(path, params=params)
        for name, (path, params) in graph_requests.items()
    }
    tasks["machines"] = _collect_mde_machines()
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    collected = {}
    for name, result in zip(tasks, results):
        if isinstance(result, Exception):
            result = {
                "available": False, "availability_status": "unavailable", "value": [],
                "records_collected": 0, "pages_collected": 0, "truncated": False,
                "status_code": getattr(result, "status_code", None), "reason": str(result),
            }
        collected[name] = result
        client.collection_status[name] = _status(result)
        client.data_sources[name] = bool(result.get("available")) and not result.get("truncated")

    alerts = collected["alerts"].get("value", []) if collected["alerts"].get("available") else []
    client.security_alerts = alerts
    client.alert_summary.update({
        "total": len(alerts),
        "by_severity": _count(alerts, "severity"),
        "by_category": _count(alerts, "category"),
        "copilot_related": 0,
    })

    incidents = collected["incidents"].get("value", []) if collected["incidents"].get("available") else []
    client.security_incidents = incidents
    active_incidents = [
        item for item in incidents
        if str(item.get("status", "")).lower() in {"active", "new", "inprogress"}
    ]
    client.incident_summary = {
        "total": len(incidents),
        "active": len(active_incidents),
        "resolved": sum(1 for item in incidents if str(item.get("status", "")).lower() in {"resolved", "closed"}),
        "high_severity": sum(1 for item in active_incidents if str(item.get("severity", "")).lower() == "high"),
    }

    scores = collected["secure_scores"].get("value", []) if collected["secure_scores"].get("available") else []
    if scores:
        latest = scores[0]
        client.secure_score = latest
        current = float(latest.get("currentScore", 0) or 0)
        maximum = float(latest.get("maxScore", 0) or 0)
        client.secure_score_summary = {
            "current_score": current,
            "max_score": maximum,
            "percentage": round(current / maximum * 100, 2) if maximum else 0,
        }

    controls = collected["secure_score_controls"].get("value", []) if collected["secure_score_controls"].get("available") else []
    client.secure_score_controls = controls
    client.identity_controls = [
        item for item in controls
        if any(term in (str(item.get("controlCategory", "")) + " " + str(item.get("title", ""))).lower()
               for term in ("identity", "authentication", "mfa", "conditional"))
    ]
    client.data_controls = [
        item for item in controls
        if any(term in (str(item.get("controlCategory", "")) + " " + str(item.get("title", ""))).lower()
               for term in ("data", "dlp", "encryption", "information"))
    ]
    implemented = sum(
        1 for item in controls
        if str(item.get("implementationStatus", "")).lower() == "implemented"
    )
    client.control_summary = {
        "total": len(controls), "implemented": implemented,
        "not_implemented": len(controls) - implemented,
    }
    client.control_focus_summary = {
        "identity_controls": len(client.identity_controls),
        "data_controls": len(client.data_controls),
        "copilot_relevant": 0,
    }

    machines = collected["machines"].get("value", []) if collected["machines"].get("available") else []
    client.defender_devices = machines
    client.device_summary = {
        "total": len(machines),
        "high_risk": sum(1 for item in machines if str(item.get("riskScore", "")).lower() == "high"),
        "medium_risk": sum(1 for item in machines if str(item.get("riskScore", "")).lower() == "medium"),
        "low_risk": sum(1 for item in machines if str(item.get("riskScore", "")).lower() == "low"),
        "copilot_enabled": 0,
    }

    client.graph_security_available = any(
        client.collection_status[name]["available"]
        for name in ("alerts", "incidents", "secure_scores", "secure_score_controls")
    )
    client.defender_api_available = client.collection_status["machines"]["available"]
    client.available = client.graph_security_available or client.defender_api_available
    if client.collection_status["machines"].get("status_code") == 404:
        client.activation_needed = True
        client.activation_message = "Defender for Endpoint is not provisioned in this tenant."

    with _stdout_lock:
        successful = sum(1 for state in client.collection_status.values() if state["available"])
        print(f"[{get_timestamp()}] ✓ Defender evidence: {successful}/{len(client.collection_status)} supported datasets read")
    return client

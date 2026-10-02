"""Build the findings-driven 30/60/90-day getting-started roadmap.

A pure function over the assessment result. It links the roadmap to open
actions and control statuses but never writes to recommendations, actions,
controls or the decision. Owner-review items are recorded with the status
"Owner review" only.
"""

from .adoption_guidance_content import (
    ADOPTION_CHECKLIST,
    BASELINE_CONTROLS,
    CONTENT_VERSION,
    CROSS_PLATFORM_MANUAL_CHECKS,
    ROADMAP_PHASES,
)

CONTROL_STATUS_TEXT = {
    "Action required": "Action needed",
    "Observed": "Evidence supports it",
    "Not established": "Not yet confirmed",
}
OWNER_REVIEW = "Owner review"


def _action_entry(action, number=None, title_for=None):
    title = title_for(action) if title_for else (action.get("ActionTitle") or action.get("Feature") or "")
    return {
        "recommendation_id": action.get("RecommendationId", ""),
        "number": number,
        "title": title,
        "priority": action.get("Priority") or "",
        "domain_id": action.get("DomainId") or "",
        "owner": action.get("OwnerRole") or "",
        "action_type": action.get("ActionType") or "",
        "security_gate": bool(action.get("SecurityGate")),
    }


def _phase_for_action(action):
    stage = str(action.get("ReadinessStage") or "").strip().lower()
    if stage == "before broad rollout":
        return "days_61_90"
    if stage == "before pilot" or action.get("SecurityGate"):
        return "days_0_30"
    return "days_31_60"


def build_adoption_guidance(result, action_numbers=None, title_for=None):
    """Return the roadmap, baseline table and owner checklists for one result.

    ``title_for`` formats an action title (the HTML report passes its own
    customer-facing heading so both sections name actions identically).
    """
    result = result or {}
    action_numbers = action_numbers or {}
    phases = {phase["id"]: {
        "id": phase["id"], "title": phase["title"], "goal": phase["goal"],
        "steps": list(phase["standard_steps"]), "actions": [], "checklist": [],
    } for phase in ROADMAP_PHASES}

    for action in result.get("actions") or []:
        entry = _action_entry(action, action_numbers.get(action.get("RecommendationId")), title_for)
        phases[_phase_for_action(action)]["actions"].append(entry)
    priority_order = {"High": 0, "Medium": 1, "Low": 2, "": 3}
    for phase in phases.values():
        phase["actions"].sort(key=lambda item: (not item["security_gate"], priority_order.get(item["priority"], 3), item["title"]))

    for item in ADOPTION_CHECKLIST:
        phases[item["phase"]]["checklist"].append({
            "id": item["id"], "topic": item["topic"], "guidance": item["guidance"],
            "owner": item["owner"], "status": OWNER_REVIEW,
        })

    controls = {row.get("control_id"): row for row in result.get("controls") or []}
    baseline = []
    for control_id, content in BASELINE_CONTROLS.items():
        control = controls.get(control_id) or {}
        status = control.get("status")
        baseline.append({
            "control_id": control_id,
            "title": control.get("title") or control_id,
            "expectation": content["expectation"],
            "owner": content["owner"],
            "link": content["link"],
            "status": CONTROL_STATUS_TEXT.get(status, "Not assessed in this report"),
            "security_gate": bool(control.get("security_gate")),
        })

    external = [{
        "topic": item["control"], "why": item["why"], "verify": item["verify"], "status": OWNER_REVIEW,
    } for item in CROSS_PLATFORM_MANUAL_CHECKS]

    open_gates = sum(1 for row in baseline if row["security_gate"] and row["status"] != CONTROL_STATUS_TEXT["Observed"])
    return {
        "content_version": CONTENT_VERSION,
        "decision": result.get("decision", ""),
        "open_security_gates": open_gates,
        "phases": [phases[phase["id"]] for phase in ROADMAP_PHASES],
        "baseline": baseline,
        "external_ai_checks": external,
        "qualification": ("Guidance only. It does not change control results, actions or the readiness decision; "
                          "owner-review items record a decision by the responsible owner, not an automatic pass."),
    }


def guidance_rows(guidance):
    """Flatten guidance for the workbook tab."""
    rows = []
    for phase in (guidance or {}).get("phases", []):
        for step in phase["steps"]:
            rows.append({"Phase": phase["title"], "Type": "Standard step", "Item": step, "Owner": "",
                         "Status": "", "Action": "", "Reference": ""})
        for action in phase["actions"]:
            rows.append({"Phase": phase["title"], "Type": "Assessment action" + (" (security gate)" if action["security_gate"] else ""),
                         "Item": action["title"], "Owner": action["owner"], "Status": action["priority"],
                         "Action": f"Action {action['number']}" if action.get("number") else action["recommendation_id"],
                         "Reference": ""})
        for item in phase["checklist"]:
            rows.append({"Phase": phase["title"], "Type": "Adoption checklist", "Item": f"{item['topic']}: {item['guidance']}",
                         "Owner": item["owner"], "Status": item["status"], "Action": "", "Reference": ""})
    for row in (guidance or {}).get("baseline", []):
        rows.append({"Phase": "Security baseline", "Type": row["control_id"], "Item": row["expectation"],
                     "Owner": row["owner"], "Status": row["status"], "Action": "", "Reference": row["link"]})
    for item in (guidance or {}).get("external_ai_checks", []):
        rows.append({"Phase": "External AI services", "Type": "Owner review", "Item": f"{item['topic']}: {item['verify']}",
                     "Owner": "AI service and security owners", "Status": item["status"], "Action": "", "Reference": ""})
    return rows

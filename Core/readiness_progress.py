"""Derive rollout milestones from the same control results and open actions.

Milestones describe verified readiness, not whether people already use Copilot.
No usage percentage, source count or unsupported approval advances a milestone.
"""

from __future__ import annotations

from .evidence_contract import parse_date, reconcile_observations


STAGES = (("started", "Just started"), ("preparing", "Preparing for pilot"),
          ("pilot", "Ready for pilot"), ("broader", "Ready for broader adoption"))


def _requirement(identifier, title, status, reason, *, action_ids=None, control_id=None, owner_role=None):
    return {"id": identifier, "title": title, "status": status, "reason": reason,
            "action_ids": list(dict.fromkeys(action_ids or [])), "control_id": control_id or "",
            "owner_role": owner_role or ""}


def _counts(requirements):
    return {"total": len(requirements), "met": sum(row["status"] == "met" for row in requirements),
            "open": sum(row["status"] == "open" for row in requirements),
            "issues": sum(row["status"] == "issue" for row in requirements),
            "conditions": sum(row["status"] == "condition" for row in requirements)}


def _all_met(requirements, *, allow_conditions=False):
    permitted = {"met", "condition"} if allow_conditions else {"met"}
    return bool(requirements) and all(row["status"] in permitted for row in requirements)


def build_rollout_progress(result, review=None):
    review = review or {}
    controls = [row for row in result.get("controls", []) if row.get("security_gate")]
    actions = [row for row in result.get("actions", []) if row.get("SecurityGate")]
    by_id = {row["RecommendationId"]: row for row in actions}
    scope = review.get("pilot_scope") or {}
    plan = review.get("pilot_plan") or {}
    scope_ok = bool(scope.get("current"))
    plan_ok = bool(scope_ok and plan.get("current") and parse_date(plan["reviewed_at"]) >= parse_date(scope["reviewed_at"]))
    scope_requirement = _requirement("pilot.scope", "Define the pilot users and content scope", "met" if scope_ok else "open",
        "A dated review identifies the pilot population: " + str(scope.get("description", "")) if scope_ok else
        "Identify the pilot users, approved content and devices in a dated review with an accountable owner and supporting evidence.",
        owner_role="Business sponsor and Microsoft 365 administrator")
    plan_requirement = _requirement("pilot.plan", "Review the pilot use cases, baseline and success measures", "met" if plan_ok else "open",
        "A current reviewed plan records an owner, approved data, baseline, success measures and stop or expansion criteria." if plan_ok else
        "Have the sponsor review the pilot charter, including its users, approved data, baseline, success measures and stop or expansion criteria.",
        control_id="ADOPTION.BASELINE", owner_role="Business sponsor and adoption lead")
    pilot_requirements = [scope_requirement, plan_requirement]
    if review.get("status") == "invalid":
        pilot_requirements.append(_requirement("review.validation", "Correct the supplied readiness review", "open",
            "The supplied review is incomplete or inconsistent. Have the assessment owner correct its scope, dates and required evidence before relying on it.", owner_role="Assessment owner"))

    conditions = {}
    for row in review.get("pilot_conditions", []):
        action = by_id.get(row.get("action_id"))
        if not action or not scope_ok or not row.get("current") or row.get("scope_id") != scope.get("id"):
            continue
        if (action.get("ActionType") != "Remediation" or action.get("EvidenceStatus") != "supported"
                or str(action.get("Status", "")).lower() == "critical"
                or action.get("ReadinessStage") not in {"Before broad rollout", "Pilot condition", "Planned improvement"}):
            continue
        observed = parse_date(action.get("ObservationDate"))
        if not observed or parse_date(row.get("reviewed_at")) < observed:
            continue
        previous = conditions.get(row["action_id"])
        if not previous or row["reviewed_at"] > previous["reviewed_at"]:
            conditions[row["action_id"]] = row

    covered_actions = set()
    for control in controls:
        related = [by_id[identifier] for identifier in control.get("recommendation_ids", []) if identifier in by_id]
        related.extend(row for row in actions if row.get("ControlId") == control["control_id"] and row not in related)
        covered_actions.update(row["RecommendationId"] for row in related)
        identifiers = [row["RecommendationId"] for row in related]
        unknown = [row for row in related if row.get("ActionType") in {"Evidence", "Confirmation"}]
        untreated = [row for row in related if row.get("ActionType") == "Remediation" and row["RecommendationId"] not in conditions]
        if unknown or control["status"] == "Not established":
            status = "open"
            reason = ("Confirm the earlier finding with current evidence for the affected population; a general control review does not resolve it."
                      if any(row.get("ActionType") == "Confirmation" for row in unknown) else
                      "Supply current evidence showing the result of this check for the intended users and content.")
        elif untreated or control["status"] == "Action required" and not related:
            status, reason = "issue", "Address the observed condition before the pilot, or document an eligible bounded pilot treatment for its specific action."
        elif related:
            status, reason = "condition", "The issue remains open for broader adoption. A current review documents how the specific pilot will be protected."
        else:
            status, reason = "met", "The required check has a current supported result for the assessed scope."
        pilot_requirements.append(_requirement("pilot." + control["control_id"], control["title"], status, reason,
                                              action_ids=identifiers, control_id=control["control_id"]))
    for action in actions:
        identifier = action["RecommendationId"]
        if identifier in covered_actions:
            continue
        treated = identifier in conditions
        status = "condition" if treated else "issue" if action.get("ActionType") == "Remediation" else "open"
        reason = "A specific pilot treatment is documented; the action remains open for broader adoption." if treated else (
            "Resolve the observed issue or document an eligible specific pilot treatment." if status == "issue" else
            "Confirm or complete this action; a general control pass cannot close it.")
        pilot_requirements.append(_requirement("pilot.action." + identifier, action.get("ActionTitle") or action.get("Feature", identifier), status, reason,
            action_ids=[identifier], owner_role=action.get("OwnerRole")))
    pilot_ready = _all_met(pilot_requirements, allow_conditions=True)

    expanded = review.get("expansion_scope") or {}
    outcomes = review.get("pilot_outcomes") or {}
    approval = review.get("expansion_approval") or {}
    pilot_count = scope.get("population_count")
    expanded_count = expanded.get("population_count")
    expansion_ok = bool(expanded.get("current") and expanded.get("id") != scope.get("id")
                        and isinstance(pilot_count, int) and isinstance(expanded_count, int) and expanded_count > pilot_count)
    outcomes_ok = bool(plan_ok and outcomes.get("current") and outcomes.get("success_measures_met") is True
                       and outcomes.get("scope_id") == scope.get("id")
                       and parse_date(outcomes.get("period_start")) >= parse_date(plan.get("reviewed_at")))
    expansion_facts = reconcile_observations(
        [row for row in review.get("observations", []) if row.get("scope_id") == expanded.get("id")],
        evaluation_date=result.get("evaluation_date"), expected_tenant_id=result.get("tenant_id"))
    expansion_reviews = {control["control_id"]: [row for row in expansion_facts if row["control_id"] == control["control_id"]]
                         for control in controls}
    last_expansion_review = max((parse_date(row["observed_at"]) for row in expansion_facts if row["observed_at"]), default=None)
    approval_ok = bool(expansion_ok and outcomes_ok and approval.get("current") and approval.get("approved") is True
                       and approval.get("scope_id") == expanded.get("id")
                       and parse_date(approval.get("reviewed_at")) >= parse_date(outcomes.get("reviewed_at"))
                       and parse_date(approval.get("reviewed_at")) >= parse_date(expanded.get("reviewed_at"))
                       and (not last_expansion_review or parse_date(approval.get("reviewed_at")) >= last_expansion_review))
    broader_requirements = [
        _requirement("broader.pilot", "Meet the controlled-pilot requirements", "met" if pilot_ready else "open",
                     "The pilot requirements are established." if pilot_ready else "Complete the pilot requirements first."),
        _requirement("broader.outcomes", "Review pilot outcomes and risk results with the sponsor", "met" if outcomes_ok else "open",
                     "A dated sponsor review confirms the agreed success measures and risk results." if outcomes_ok else
                     "Record a dated review of a completed pilot period, measured outcomes and risk results against the reviewed plan. Usage alone is insufficient.",
                     owner_role="Business sponsor"),
        _requirement("broader.scope", "Identify the larger rollout population", "met" if expansion_ok else "open",
                     "A current review identifies a larger population than the pilot." if expansion_ok else
                     "Define a separate dated expansion scope and record both population counts so the larger population is explicit.",
                     owner_role="Business sponsor and Microsoft 365 administrator"),
        _requirement("broader.approval", "Record the sponsor's expansion decision", "met" if approval_ok else "open",
                     "A dated expansion approval follows the outcomes review and the review of the larger population's controls." if approval_ok else
                     "Record an affirmative expansion decision after reviewing the pilot outcomes and the larger population's controls.",
                     owner_role="Business sponsor"),
        _requirement("broader.actions", "Resolve remaining rollout issues and confirmations", "met" if not actions else "issue",
                     "No required rollout actions remain." if not actions else
                     "Pilot treatments do not close underlying findings. Supply current evidence resolving the remaining issues and confirmations.",
                     action_ids=[row["RecommendationId"] for row in actions]),
    ]
    for control in controls:
        candidates = expansion_reviews[control["control_id"]]
        selected = [row for row in candidates if row["selection"] == "selected" and row["complete"] and row["freshness"] == "current"]
        passed = bool(expansion_ok and outcomes_ok and len(selected) == 1 and selected[0].get("control_result") == "pass"
                      and parse_date(selected[0]["observed_at"]) >= parse_date(outcomes["reviewed_at"]))
        failed = any(row.get("control_result") == "fail" and row["selection"] == "selected" for row in candidates)
        broader_requirements.append(_requirement("broader." + control["control_id"], control["title"],
            "met" if passed else "issue" if failed else "open",
            "A current review confirms this control for the larger population after the pilot review." if passed else
            "Review this control specifically for the larger population after the pilot outcomes review; pilot scope evidence alone is insufficient.",
            control_id=control["control_id"]))
    broader_ready = _all_met(broader_requirements)
    has_evidence = any(control["status"] != "Not established" for control in controls) or any(
        row.get("availability") == "available" and row.get("value") is not None for row in result.get("evidence", []))
    current = "broader" if broader_ready else "pilot" if pilot_ready else "preparing" if has_evidence or scope_ok else "started"
    labels = dict(STAGES)
    requirements_by_stage = {
        "started": [_requirement("started.evidence", "Begin the assessment with evidence or a reviewed scope", "met" if has_evidence or scope_ok else "open",
                                 "An initial evidence base or reviewed scope is available." if has_evidence or scope_ok else
                                 "Supply initial evidence or a dated pilot scope to begin the assessment.")],
        "preparing": [scope_requirement, plan_requirement,
                      _requirement("preparing.evidence", "Review the initial control evidence", "met" if has_evidence else "open",
                                   "The report contains control evidence to review." if has_evidence else "Collect or supply dated evidence for the required controls.")],
        "pilot": pilot_requirements, "broader": broader_requirements,
    }
    order = [key for key, _ in STAGES]
    stages = [{"id": key, "label": label,
               "status": "complete" if order.index(key) < order.index(current) else "current" if key == current else "pending",
               "requirements": requirements_by_stage[key], "counts": _counts(requirements_by_stage[key])}
              for key, label in STAGES]
    next_requirements = broader_requirements if current == "pilot" else [] if current == "broader" else pilot_requirements
    return {
        "current_stage_id": current, "current_stage": labels[current], "label": labels[current], "stages": stages,
        "counts": {"required_controls": len(controls), "established_controls": sum(row["status"] == "Observed" for row in controls),
                   "unconfirmed_controls": sum(row["status"] == "Not established" for row in controls),
                   "observed_issues": sum(row["status"] == "Action required" for row in controls),
                   "open_actions": len(result.get("actions", [])),
                   "pilot_blockers": sum(row["status"] in {"open", "issue"} for row in pilot_requirements)},
        "next_requirements": [row for row in next_requirements if row["status"] in {"open", "issue"}],
        "pilot_conditions": list(conditions.values()),
        "qualification": "These milestones describe verified Microsoft 365 Copilot rollout readiness. Existing license assignment or usage does not establish authorization or readiness for a larger rollout.",
    }

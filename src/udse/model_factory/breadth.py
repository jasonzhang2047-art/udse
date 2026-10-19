"""Breadth-first possibility discovery (§9, Pass 0).

Independent generators propose families before any detailed modeling, so a narrow first model
cannot silently remove the trajectory a user needs. The inventory is a living registry of
*discovered* families; undiscovered possibilities appear only as a warning, never as fake items.
"""

from __future__ import annotations

import re
from typing import Any

from ..llm.provider import LLMCall, LLMProvider, LLMRefused, LLMUnavailable, LLMOutputError
from ..model_ir.enums import CoverageStatus
from ..model_ir.schemas import CoverageFrontier, DecisionWorldModel, FrontierItem

METHODS = [
    "policy_alternatives", "external_variations", "behavioral_responses", "omitted_actors", "timing_irreversibility",
    "feedback_loops", "second_order_consequences", "low_probability_high_impact", "domain_constraints", "adverse_cases",
]

GENERIC_FAMILIES = [
    ("behavioral_responses", "Participants disagree and the decision stalls", "Deliberation fails to satisfy the governance rule before the horizon.", [], None),
    ("behavioral_responses", "A participant withdraws mid-way", "Someone's availability or commitment changes and the plan must adapt.", ["participant_exit"], None),
    ("omitted_actors", "An external party changes terms", "A supplier, venue, regulator or counterparty acts in a way not yet modeled.", ["external_actor_response"], None),
    ("timing_irreversibility", "Early irreversible commitment", "Committing early forecloses later options; the cost of reversal is not modeled.", ["irreversibility_cost"], None),
    ("feedback_loops", "Reinforcing feedback between outcomes and behaviour", "Early results change effort or preferences, which changes later results.", ["behavioral_feedback"], None),
    ("second_order_consequences", "Knock-on effects outside the decision boundary", "Effects on other commitments, relationships or resources.", ["second_order_effects"], None),
    ("low_probability_high_impact", "Rare disruptive event", "A low-probability, high-impact shock occurs during the horizon.", ["shock_process"], None),
    ("adverse_cases", "Key resource exhausted before the horizon", "A hard resource floor is reached and the trajectory ends early.", [], {"terminal": True}),
]


def _key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:48]


def discover(spec, scope, package_hints: list[dict[str, Any]], llm: LLMProvider | None = None,
             llm_records: list[dict[str, Any]] | None = None) -> CoverageFrontier:
    items: dict[str, FrontierItem] = {}

    def add(method: str, label: str, description: str = "", policy_id: str | None = None, scenario_ids=None,
            required=None, outcome_match=None, infeasible_under=None, kind: str | None = None, interest: bool = False):
        fam_kind = kind or method
        struct = (policy_id, tuple(sorted(scenario_ids or [])), tuple(sorted(required or [])),
                  tuple(sorted((outcome_match or {}).items())))
        fid = next((i.id for i in items.values()
                    if (i.policy_id, tuple(sorted(i.scenario_ids)), tuple(sorted(i.required_mechanisms)),
                        tuple(sorted((i.outcome_match or {}).items()))) == struct and struct != (None, (), (), ())), None)
        fid = fid or next((i.id for i in items.values() if _key(i.label) == _key(label)), None)
        if fid:
            if method not in items[fid].discovered_by:
                items[fid].discovered_by.append(method)  # structurally similar → deduplicated
            return
        fid = f"fam_{_key(label)}"
        items[fid] = FrontierItem(id=fid, family_kind=fam_kind, label=label, description=description, policy_id=policy_id,
                                  scenario_ids=list(scenario_ids or []), required_mechanisms=list(required or []),
                                  outcome_match=outcome_match, infeasible_under=infeasible_under, discovered_by=[method],
                                  user_interest=interest)

    for a in scope.alternatives:
        add("policy_alternatives", a.label or a.id.replace("_", " "),
            ("System-suggested alternative. " if a.suggested_by_system else "") + (a.description or f"Policy {a.policy}"),
            policy_id=a.id, kind="policy", interest=not a.suggested_by_system)
    for h in package_hints:
        add(h.get("method", "domain_constraints"), h["label"], h.get("description", ""), policy_id=h.get("policy_id"),
            scenario_ids=h.get("scenario_ids"), required=h.get("required_mechanisms"), outcome_match=h.get("outcome_match"),
            infeasible_under=h.get("infeasible_under"), kind=h.get("family_kind"), interest=h.get("user_interest", False))
    for method, label, desc, req, extra in GENERIC_FAMILIES:
        add(method, label, desc, required=req, kind=method)
    methods = list(METHODS)
    if llm is not None:
        try:
            res = llm.complete_json(LLMCall(
                task="breadth_discovery", effort="medium", max_tokens=8000,
                system=("You enumerate structurally distinct possibility families for a decision before any modeling. "
                        "Return families, not probabilities. Prefer mechanisms and decision options a narrow model might omit."),
                user=(f"Decision: {spec.question}\nAlternatives: {[a.id for a in scope.alternatives]}\nHorizon: "
                      f"{scope.horizon_start} → {scope.horizon_end}\nFor each category in {METHODS}, list up to 3 families."),
                schema={"type": "object", "additionalProperties": False, "required": ["families"],
                        "properties": {"families": {"type": "array", "items": {
                            "type": "object", "additionalProperties": False, "required": ["category", "label", "description"],
                            "properties": {"category": {"type": "string"}, "label": {"type": "string"}, "description": {"type": "string"}}}}}},
            ))
            if llm_records is not None:
                llm_records.append(res.record)
            for f in res.data.get("families", [])[:30]:
                cat = f["category"] if f["category"] in METHODS else "domain_constraints"
                add(cat, f["label"][:80], f["description"][:400], required=[f"unmodeled:{_key(f['label'])}"], kind=cat)
            methods.append("llm_breadth_prompt")
        except (LLMUnavailable, LLMRefused, LLMOutputError):
            pass
    return CoverageFrontier(
        frontier_id=f"frontier_{spec.decision_id}", decision_id=spec.decision_id, items=list(items.values()),
        discovery_methods=methods,
        suspected_blind_spots=["actors or institutions not mentioned in the decision", "interactions between simultaneous shocks",
                               "changes in participants' own goals over the horizon"],
    )


def annotate_frontier(frontier: CoverageFrontier, model: DecisionWorldModel, previous: CoverageFrontier | None = None) -> CoverageFrontier:
    """Mark which families the compiled model can execute; nothing is pruned, only labelled."""
    mech_ids = {m.id for m in model.mechanisms}
    policy_ids = {p.id for p in model.policies}
    scenario_ids = {s.id for s in model.scenarios}
    constraints = {c.id: c for c in model.constraints}
    prev = {i.id: i for i in (previous.items if previous else [])}
    blocked_prefixes = ("requires unmodeled", "policy ", "scenario not")
    for it in frontier.items:
        old = prev.get(it.id)
        if old:
            it.run_ids = list(old.run_ids)
            it.history = list(old.history)
        missing = [m for m in it.required_mechanisms if m not in mech_ids]
        c = constraints.get(it.infeasible_under) if it.infeasible_under else None
        cite: list[str] = []
        if c is not None and c.validated:
            status, reason = CoverageStatus.REJECTED_VALIDATED_CONSTRAINT, f"not feasible under constraint {c.id}: {c.description}"
            cite = list(c.evidence_refs)
        elif missing:
            status, reason = CoverageStatus.DISCOVERED_UNSIMULATED, f"requires unmodeled mechanism(s): {', '.join(missing)}"
        elif it.policy_id and it.policy_id not in policy_ids:
            status, reason = CoverageStatus.DISCOVERED_UNSIMULATED, f"policy {it.policy_id} is not represented in model v{model.model_version}"
        elif any(s not in scenario_ids for s in it.scenario_ids):
            status, reason = CoverageStatus.DISCOVERED_UNSIMULATED, "scenario not represented in this model version"
        elif old is not None and (old.status == CoverageStatus.REJECTED_VALIDATED_CONSTRAINT or
                                  (old.status == CoverageStatus.DISCOVERED_UNSIMULATED and old.status_reason.startswith(blocked_prefixes))):
            # The justification for not executing this family was overturned by the revision (§9.5).
            status = CoverageStatus.REOPENED_AFTER_REVISION
            reason = f"reopened by model v{model.model_version}: previously '{old.status_reason}'"
        elif old is not None and old.status != CoverageStatus.DISCOVERED_UNSIMULATED:
            status, reason = old.status, old.status_reason
        else:
            status, reason = CoverageStatus.DISCOVERED_UNSIMULATED, "executable in this model version; not yet simulated"
        if status != it.status or reason != it.status_reason:
            it.history.append({"model_version": model.model_version, "from": str(it.status), "to": str(status), "reason": reason})
        it.status, it.status_reason, it.citation = status, reason, cite
    frontier.model_version = model.model_version
    return frontier

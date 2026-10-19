"""Generic (clean-room) model synthesis from a DecisionSpec alone (§6.5 repair ladder, step 3).

Without any category adapter, this builds the smallest coherent model the stated facts support:
typed state from user facts, resource flows inferred from units (``X/time`` rates draining an ``X``
stock), alternative policies and their stated consequences, a governed mid-run policy switch,
constraint guards and early-warning gates. Anything it cannot represent — a metric with no
mechanism, a flow of unclear direction — becomes a structural unknown and a research need rather
than a fabricated transition.
"""

from __future__ import annotations

import re
from typing import Any

from ..common import parse_dt
from ..model_ir.enums import FactorLabel, FidelityTier, OriginKind, ResearchNeedKind
from ..model_ir.schemas import DecisionSpec, EvidenceBundle, InitialFact
from ..model_ir.units import TIME_UNITS, UnitError, parse_unit
from ..model_registry.registry import Registry, TargetContext, transfer_gate

INTERVAL = {"minute": "PT1M", "hour": "PT1H", "day": "P1D", "week": "P1W", "month": "P1M", "year": "P1Y"}
REVIEW = {"minute": "PT30M", "hour": "PT2H", "day": "P7D", "week": "P14D", "month": "P3M"}
RESPONSE_LATENCY = {"minute": "PT10M", "hour": "PT30M", "day": "P1D", "week": "P2D", "month": "P7D"}
OUT_WORDS = ("cost", "outflow", "expense", "burn", "spend", "fee", "payment", "rent", "bill", "usage", "upkeep", "maintenance")
IN_WORDS = ("income", "revenue", "inflow", "saving", "deposit", "grant", "donation", "contribution", "yield_value")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_")


def infer_fact_type(f: InitialFact) -> tuple[str, str | None, list[str] | None]:
    if f.type:
        return f.type, (f.unit or ("1" if f.type == "quantity" else None)), f.values
    v = f.value
    if isinstance(v, bool):
        return "boolean", None, None
    if isinstance(v, (int, float)):
        return "quantity", f.unit or "1", None
    if isinstance(v, str):
        try:
            parse_dt(v)
            return "timestamp", None, None
        except ValueError:
            pass
        if f.values:
            return "enum", None, f.values
        return "text", None, None
    return "text", None, None


def literal(value: Any, typ: str, unit: str | None) -> str:
    if value is None or value == "UNKNOWN":
        return "UNKNOWN"
    if typ == "quantity":
        return f"{float(value)!r}[{unit}]" if unit and unit != "1" else f"{float(value)!r}"
    if typ == "boolean":
        return "true" if value else "false"
    if typ == "timestamp":
        return f"time('{value}')"
    return "'" + str(value).replace("'", "") + "'"


def _time_unit_of_rate(unit: str | None) -> str | None:
    if not unit or "/" not in unit:
        return None
    den = unit.split("/")[-1].strip()
    den = {"months": "month", "days": "day", "hours": "hour", "minutes": "minute", "weeks": "week", "years": "year"}.get(den, den)
    return den if den in TIME_UNITS and den != "second" else None


class GenericSynthesizer:
    version = "generic-synthesizer/0.5.0"

    def __init__(self, registry: Registry | None = None):
        self.registry = registry

    def synthesize(self, spec: DecisionSpec, scope, bundle: EvidenceBundle) -> tuple[dict[str, Any], dict[str, Any]]:
        res = scope.time_resolution
        res_key = res if res in REVIEW else "day"
        notes: dict[str, Any] = {"research_needs": [], "reuse": [], "flows": [], "unrepresented": []}
        entities: dict[str, dict[str, Any]] = {"decision": {"id": "decision", "type": "decision_context"}}
        state: dict[str, dict[str, Any]] = {}
        facts = {f.ref: f for f in spec.initial_facts}
        alt_ids = [a.id for a in scope.alternatives]

        def add_entity(ref: str) -> None:
            ent = ref.split(".")[0]
            entities.setdefault(ent, {"id": ent, "type": "entity"})

        for f in spec.initial_facts:
            typ, unit, values = infer_fact_type(f)
            add_entity(f.ref)
            state[f.ref] = {
                "ref": f.ref, "type": typ, "unit": unit, "values": values,
                "initial_value": float(f.value) if typ == "quantity" else f.value,
                "origin": str(f.origin), "evidence_refs": [f.evidence_ref or f"user_claim_{f.ref}"],
                "epistemic_tier": "B_ASSUMPTION_BOUND", "description": f.description,
                "visibility": {"scope": "public" if f.visibility == "public" else "hidden"},
            }
        # alternative consequences may introduce refs not among the facts
        for a in scope.alternatives:
            for asm in a.assumptions:
                if asm.ref in state:
                    continue
                add_entity(asm.ref)
                v = asm.value if asm.value is not None else asm.delta
                typ = "quantity" if isinstance(v, (int, float)) and not isinstance(v, bool) else ("boolean" if isinstance(v, bool) else "text")
                state[asm.ref] = {
                    "ref": asm.ref, "type": typ, "unit": asm.unit or ("1" if typ == "quantity" else None),
                    "initial_value": 0.0 if (typ == "quantity" and asm.delta is not None) else ("UNKNOWN" if typ != "quantity" else 0.0),
                    "allow_unknown": typ != "quantity", "origin": "USER_ASSERTED",
                    "evidence_refs": [asm.evidence_ref or f"user_claim_{a.id}_{asm.ref}"],
                    "epistemic_tier": "B_ASSUMPTION_BOUND", "description": asm.description,
                }
        state["decision.policy"] = {"ref": "decision.policy", "type": "enum", "values": alt_ids, "initial_value": "UNKNOWN",
                                    "allow_unknown": True, "origin": "DERIVED_CALCULATION", "epistemic_tier": "A_GROUNDED",
                                    "description": "policy currently in force (set by the controlled intervention or a governed switch)"}
        mechanisms: list[dict[str, Any]] = []
        structural_unknowns: list[dict[str, Any]] = []
        omissions: list[dict[str, Any]] = []
        factor_table: list[dict[str, Any]] = []
        flow_rates: list[str] = []
        stocks_with_flows: dict[str, list[str]] = {}

        # ---- resource flows inferred from units ----
        target = TargetContext(domain_labels=scope.domain_labels or ["*"], jurisdiction=scope.jurisdiction, time_resolution=res,
                               horizon_start=scope.horizon_start, horizon_end=scope.horizon_end)
        for ref, sv in list(state.items()):
            if sv["type"] != "quantity":
                continue
            tu = _time_unit_of_rate(sv["unit"])
            if not tu:
                continue
            num = sv["unit"].rsplit("/", 1)[0]
            try:
                num_u = parse_unit(num)
            except UnitError:
                continue
            cands = [r for r, s in state.items() if r != ref and s["type"] == "quantity" and not _time_unit_of_rate(s["unit"])
                     and parse_unit(s["unit"]).same_dimension(num_u)]
            same_ent = [r for r in cands if r.split(".")[0] == ref.split(".")[0]]
            cands = same_ent or cands
            name = ref.split(".", 1)[1]
            direction = "subtract" if any(w in name for w in OUT_WORDS) else ("add" if any(w in name for w in IN_WORDS) else None)
            if len(cands) != 1 or direction is None:
                why = "no matching stock" if not cands else ("several candidate stocks" if len(cands) > 1 else "flow direction unclear from naming")
                structural_unknowns.append({"id": f"flow_{_slug(ref)}", "description": f"Rate {ref} ({sv['unit']}) is not connected to a stock: {why}.",
                                            "impact": "medium", "status": "DISCOVERED_UNMODELED"})
                notes["unrepresented"].append(ref)
                continue
            stock = cands[0]
            mech = {
                "id": f"flow_{_slug(ref)}", "operator": "resource_flow",
                "description": f"{'Debits' if direction == 'subtract' else 'Credits'} {stock} by {ref} every {tu} (accounting identity).",
                "rule": {"kind": f"{direction}_flow", "target": stock, "rate_ref": ref, "interval_unit": tu},
                "schedule": {"kind": "recurring", "interval": INTERVAL[tu]}, "inputs": [stock, ref], "output_refs": [stock],
                "epistemic_tier": "B_ASSUMPTION_BOUND", "origin": "DERIVED_CALCULATION",
                "evidence_refs": state[stock]["evidence_refs"] + sv["evidence_refs"],
                "assumptions": [f"{ref} stays at its user-asserted value unless an alternative or scenario changes it"],
            }
            if self.registry is not None and direction == "subtract":
                entry = self.registry.get("accounting.recurring_outflow")
                if entry is not None:
                    tgt = target.model_copy(update={"slot_units": {"stock": state[stock]["unit"], "rate": sv["unit"]}})
                    dec = transfer_gate(entry, tgt)
                    notes["reuse"].append({"entry": entry.ref, "transfer_ok": dec.ok, "reasons": dec.reasons, "checks": dec.checks,
                                           "used": dec.ok, "for": f"flow_{_slug(ref)}"})
                    if dec.ok:
                        mech["registry_ref"] = {"entry_id": entry.entry_id, "version": entry.version, "hash": entry.ir_hash()}
                        if str(entry.status) == "A_VALIDATED":
                            mech["epistemic_tier"] = "A_GROUNDED"
            mechanisms.append(mech)
            flow_rates.append(ref)
            stocks_with_flows.setdefault(stock, []).append(ref)
            notes["flows"].append({"rate": ref, "stock": stock, "direction": direction})

        # ---- constraints, guards and early-warning gates ----
        constraints: list[dict[str, Any]] = []
        for c in spec.constraints:
            if c.ref and c.ref not in state:
                structural_unknowns.append({"id": f"constraint_{_slug(c.id)}", "description": f"Constraint {c.id} refers to {c.ref}, which no fact declares.",
                                            "impact": "medium"})
                continue
            ev = [c.evidence_ref or f"user_constraint_{c.id}"]
            if c.ref and c.min is not None:
                constraints.append({"id": c.id, "type": "guarded_floor", "path": c.ref, "min": c.min, "validated": c.hard,
                                    "evidence_refs": ev, "description": c.description or f"{c.ref} ≥ {c.min}",
                                    "on_violation": {"action": "terminate", "outcome": f"{_slug(c.ref).upper()}_LIMIT_REACHED",
                                                     "reason": f"{c.ref} would fall below {c.min}; the model stops rather than clipping the value"}})
                init = state[c.ref].get("initial_value")
                if isinstance(init, (int, float)) and init > 0 and c.ref in stocks_with_flows:
                    flag = f"decision.{_slug(c.ref.split('.', 1)[1])}_low"
                    state[flag] = {"ref": flag, "type": "boolean", "initial_value": False, "origin": "DERIVED_CALCULATION",
                                   "epistemic_tier": "A_GROUNDED", "description": f"{c.ref} below 25% of its starting value"}
                    unit = state[c.ref]["unit"]
                    mechanisms.append({
                        "id": f"warn_{_slug(c.ref)}", "operator": "threshold_gate",
                        "description": f"Early warning when {c.ref} falls below 25% of its initial value.",
                        "rule": {"kind": "threshold_gate", "target": c.ref, "comparator": "lt",
                                 "bound_ref": f"{0.25 * float(init)!r}" + (f"[{unit}]" if unit and unit != "1" else ""),
                                 "on_trigger": [{"op": "set", "ref": flag, "value": "true"},
                                                {"op": "wake", "agents": ["*"], "reason": f"{c.ref} running low"}]},
                        "schedule": {"kind": "watch"}, "epistemic_tier": "A_GROUNDED", "origin": "DERIVED_CALCULATION",
                    })
            elif c.ref and c.max is not None:
                constraints.append({"id": c.id, "type": "guarded_ceiling", "path": c.ref, "max": c.max, "validated": c.hard,
                                    "evidence_refs": ev, "description": c.description,
                                    "on_violation": {"action": "reject" if not c.hard else "terminate",
                                                     "outcome": f"{_slug(c.ref).upper()}_CEILING_REACHED", "reason": c.description}})
            elif c.expr:
                constraints.append({"id": c.id, "type": "expression", "expr": c.expr, "validated": c.hard, "evidence_refs": ev,
                                    "description": c.description, "on_violation": {"action": "reject"}})

        # ---- policies from alternatives ----
        policies = []
        for a in scope.alternatives:
            eff = [{"op": "set", "ref": "decision.policy", "value": f"'{a.id}'"}]
            sched = []
            for i, asm in enumerate(a.assumptions):
                sv = state[asm.ref]
                if asm.value is not None:
                    e = {"op": "set", "ref": asm.ref, "value": literal(asm.value, sv["type"], sv.get("unit"))}
                else:
                    e = {"op": "inc", "ref": asm.ref, "by": literal(asm.delta, sv["type"], sv.get("unit"))}
                if asm.at:
                    sched.append({"at": asm.at, "event_type": f"{a.id}_consequence_{i}", "effects": [e],
                                  "description": asm.description or f"stated consequence of {a.label or a.id}"})
                else:
                    eff.append(e)
            policies.append({"id": a.id, "label": a.label or a.id.replace("_", " "), "alternative_ref": a.id,
                             "suggested_by_system": a.suggested_by_system, "description": a.description,
                             "initial_effects": eff, "scheduled_effects": sched})

        # ---- agents, governed mid-run switch ----
        agents, actions = [], []
        participants = scope.participants or [spec.owner_id]
        for pid in participants:
            prof = next((p.profile_ref for p in spec.participants if p.agent_id == pid), None)
            agents.append({"id": pid, "display_name": next((p.display_name for p in spec.participants if p.agent_id == pid), pid),
                           "profile_ref": prof, "periodic_review": REVIEW[res_key], "max_wakeups": 12})
        gp = spec.governance_policy
        rule = gp.kind
        state["decision.switch_ledger"] = {"ref": "decision.switch_ledger", "type": "ledger", "initial_value": None,
                                           "origin": "DERIVED_CALCULATION", "epistemic_tier": "A_GROUNDED"}
        state["decision.switch_choice"] = {"ref": "decision.switch_choice", "type": "enum", "values": alt_ids,
                                           "initial_value": "UNKNOWN", "allow_unknown": True, "origin": "DERIVED_CALCULATION",
                                           "epistemic_tier": "A_GROUNDED"}
        on_sat = [{"op": "set", "ref": "decision.policy", "value": "gov.option"}]
        for a in scope.alternatives:
            imm = [x for x in a.assumptions if not x.at]
            if imm:
                on_sat.append({"op": "emit", "event_type": f"switched_to_{a.id}", "when": f"gov.option == '{a.id}'"})
                effs = []
                for asm in imm:
                    sv = state[asm.ref]
                    if asm.value is not None:
                        effs.append({"op": "set", "ref": asm.ref, "value": literal(asm.value, sv["type"], sv.get("unit"))})
                    else:
                        effs.append({"op": "inc", "ref": asm.ref, "by": literal(asm.delta, sv["type"], sv.get("unit"))})
                mechanisms.append({"id": f"apply_{_slug(a.id)}", "operator": "information_process",
                                   "description": f"Applies the stated immediate consequences of switching to {a.id}.",
                                   "rule": {"kind": "on_event", "effects": effs},
                                   "schedule": {"kind": "on_event", "event_types": [f"switched_to_{a.id}"]},
                                   "epistemic_tier": "B_ASSUMPTION_BOUND", "origin": "USER_ASSERTED",
                                   "evidence_refs": [x.evidence_ref or f"user_claim_{a.id}_{x.ref}" for x in imm]})
        mechanisms.append({
            "id": "policy_switch", "operator": "social_interaction",
            "description": f"Participants may agree ({rule}) to switch policy mid-run; agreement is simulated, never a real commitment.",
            "rule": {"kind": "governance", "participants": participants,
                     "decision_rule": rule if rule != "single_owner" else "single_owner",
                     "quorum": gp.quorum, "signatories": gp.signatories or ([participants[0]] if rule in ("single_owner", "authorized_signatories") else []),
                     "ledger_ref": "decision.switch_ledger", "outcome_ref": "decision.switch_choice", "option_values": alt_ids,
                     "on_satisfied": on_sat, "max_rounds": 6, "proposal_requires_gain": True},
            "schedule": {"kind": "none"}, "epistemic_tier": "B_ASSUMPTION_BOUND", "origin": "USER_ASSERTED",
        })
        appraisal = {}
        numeric_refs = sorted({asm.ref for a in scope.alternatives for asm in a.assumptions if state[asm.ref]["type"] == "quantity"})
        for ref in numeric_refs:
            unit = state[ref]["unit"]
            zero = "0" + (f"[{unit}]" if unit and unit != "1" else "")
            expr = zero
            for a in reversed(scope.alternatives):
                asm = next((x for x in a.assumptions if x.ref == ref), None)
                if asm is None:
                    continue
                v = asm.delta if asm.delta is not None else (asm.value - (state[ref]["initial_value"] or 0) if isinstance(asm.value, (int, float)) else 0)
                expr = f"if(arg.alternative == '{a.id}', {literal(v, 'quantity', unit)}, {expr})"
            appraisal[f"{ref.split('.', 1)[1]}_change"] = expr
        actions += [
            {"id": "propose_switch", "description": "Propose switching the group to another alternative from now on.",
             "actors": participants, "args": [{"name": "alternative", "type": "enum", "values": alt_ids}],
             "preconditions": [{"id": "not_current", "expr": "not (decision.policy == arg.alternative)", "rejection_code": "ALREADY_IN_FORCE"}],
             "governance": {"mechanism": "policy_switch", "act": "propose", "option_arg": "alternative"},
             "appraisal": appraisal, "epistemic_tier": "B_ASSUMPTION_BOUND"},
            {"id": "endorse_switch", "actors": participants, "governance": {"mechanism": "policy_switch", "act": "endorse"},
             "description": "Endorse the open switch proposal."},
            {"id": "object_switch", "actors": participants, "governance": {"mechanism": "policy_switch", "act": "object"},
             "description": "Object to the open switch proposal."},
        ]
        mechanisms.append({"id": "respond_to_proposals", "operator": "feedback_control",
                           "description": "Participants reconsider after a proposal or objection.",
                           "rule": {"kind": "wakeup", "agents": ["*"], "latency": RESPONSE_LATENCY[res_key], "reason": "proposal_needs_response"},
                           "schedule": {"kind": "on_event", "event_types": ["action:propose_switch", "action:object_switch"]},
                           "epistemic_tier": "B_ASSUMPTION_BOUND"})

        # ---- metrics ----
        metrics = []
        var_index = {ref.split(".", 1)[1]: ref for ref in state if "." in ref}
        for mid in spec.metrics:
            ref = mid if mid in state else var_index.get(mid)
            if ref and state[ref]["type"] in ("quantity", "boolean", "enum", "timestamp"):
                metrics.append({"id": mid, "label": mid.replace("_", " "), "expr": ref, "unit": state[ref].get("unit")})
                continue
            m_runway = re.match(r"(.+?)_?runway(_months)?$", mid)
            stock_for = None
            if m_runway or "runway" in mid:
                for stock, rates in stocks_with_flows.items():
                    out_rates = [r for r in rates if any(w in r for w in OUT_WORDS)]
                    if out_rates:
                        stock_for = (stock, out_rates[0])
                        break
            if stock_for:
                s, r = stock_for
                metrics.append({"id": mid, "label": mid.replace("_", " "), "unit": "month",
                                "expr": f"if({r} > 0, to({s} / {r}, 'month'), UNKNOWN)"})
                continue
            reason = f"no representable mechanism for '{mid}' from the stated facts; research or a user-supplied mechanism is needed"
            metrics.append({"id": mid, "label": mid.replace("_", " "), "computable": False, "reason": reason})
            structural_unknowns.append({"id": f"mechanism_for_{_slug(mid)}", "description": reason, "impact": "high",
                                        "affects_metrics": [mid]})
            notes["research_needs"].append({"kind": ResearchNeedKind.MECHANISM.value, "question": f"What process determines {mid.replace('_', ' ')} under each alternative?",
                                            "target_refs": [mid], "why": f"metric {mid} cannot be computed without it", "critical": True,
                                            "affects_metrics": [mid]})

        # ---- scenarios: declared exploratory ranges around user-asserted rates ----
        scenarios = []
        for ref in flow_rates[:2]:
            v = state[ref]["initial_value"]
            for tag, k in (("plus_20", 1.2), ("minus_20", 0.8)):
                scenarios.append({"id": f"{_slug(ref.split('.', 1)[1])}_{tag}", "label": f"{ref} {'+' if k > 1 else '-'}20%",
                                  "kind": "external_condition", "state_overrides": {ref: v * k}, "epistemic_tier": "C_ILLUSTRATIVE",
                                  "assumptions": [f"declared exploratory range: {ref} {'+' if k > 1 else '-'}20% around the user-asserted value"],
                                  "description": "Illustrative external variation; no probability is asserted."})
        if (parse_dt(scope.horizon_end) - parse_dt(scope.horizon_start)).days > 90:
            notes["research_needs"].append({"kind": ResearchNeedKind.FORECAST_INPUT.value,
                                            "question": "How might prices, demand or availability change over the horizon?",
                                            "target_refs": ["external_conditions"], "why": "long horizons depend on unobservable future conditions"})
        for a in scope.alternatives:
            if not a.assumptions and not a.suggested_by_system:
                notes["research_needs"].append({"kind": ResearchNeedKind.PRIVATE_FACT.value,
                                                "question": f"What concrete costs, timing or effects does '{a.label or a.id}' have?",
                                                "target_refs": [a.id], "why": "alternatives without stated consequences cannot be distinguished"})
        omissions.append({"id": "external_actors", "description": "External actors (suppliers, regulators, competitors) are not modeled.",
                          "reason": "no evidence or stated facts about their behaviour", "risk": "medium"})
        omissions.append({"id": "exogenous_shocks", "description": "Exogenous shocks beyond declared exploratory scenarios are not represented.",
                          "reason": "breadth inventory lists them as unsimulated families", "risk": "medium"})
        for ref, sv in state.items():
            if ref.startswith("decision."):
                continue
            label = FactorLabel.MUST_MODEL if (ref in flow_rates or ref in stocks_with_flows or any(c.get("path") == ref for c in constraints)) else FactorLabel.USEFUL_SECONDARY
            factor_table.append({"factor_id": ref, "description": sv.get("description") or ref, "label": label.value,
                                 "decision_relevance": "drives a constraint or resource flow" if label == FactorLabel.MUST_MODEL else "stated decision fact",
                                 "evidence_availability": "user-asserted", "rationale": "included in the minimal coherent model"})
        for su in structural_unknowns:
            factor_table.append({"factor_id": su["id"], "description": su["description"],
                                 "label": FactorLabel.HIGH_IMPACT_UNCERTAIN.value if su.get("impact") == "high" else FactorLabel.EXPLICIT_OMISSION.value,
                                 "rationale": "cannot be represented responsibly from available evidence"})

        ir = {
            "model_id": f"wm_{spec.decision_id}", "decision_id": spec.decision_id,
            "purpose": f"Explore how '{spec.question}' may unfold over the stated horizon under each alternative.",
            "decision_criteria": list(spec.metrics),
            "odd": {
                "purpose": spec.question,
                "entities_state_scales": f"{len(state)} typed state variables over {len(entities)} entities; resolution {res}.",
                "process_overview_scheduling": "Recurring resource flows, threshold warnings, periodic participant reviews and governed policy switches.",
                "initialization": "User-asserted facts; the chosen alternative's stated consequences applied at the start.",
                "inputs": "User-asserted facts and stated alternative consequences; exploratory ±20% scenarios.",
                "submodels": ", ".join(m["id"] for m in mechanisms),
                "evaluation_rationale": "Contract and invariant tests only; no external calibration.",
            },
            "scope": {"decision_rev": spec.revision, "horizon_start": scope.horizon_start, "horizon_end": scope.horizon_end,
                      "time_resolution": res if res in ("minute", "hour", "day", "week", "month") else "day",
                      "domain_labels": scope.domain_labels, "high_stakes": scope.high_stakes, "jurisdiction": scope.jurisdiction},
            "entities": list(entities.values()), "state": [{k: v for k, v in s.items() if v is not None} for s in state.values()],
            "parameters": [], "scenarios": scenarios, "policies": policies, "agents": agents, "actions": actions,
            "mechanisms": mechanisms, "constraints": constraints, "metrics": metrics,
            "causal_assumptions": ["Stated facts hold unless an alternative, scenario or agent action changes them.",
                                   "Resource flows follow accounting identities; no behavioural or market response is assumed."],
            "structural_unknowns": structural_unknowns, "omissions": omissions, "factor_table": factor_table,
            "reuse_manifest": notes["reuse"],
        }
        return ir, notes

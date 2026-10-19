"""Trajectory analytics (§11): paths, not terminal cards.

Every explanation is assembled from committed events and recorded Agent rationales — never from an
LLM-written anecdote. Explanations separate observed within-run events, mechanisms, Agent actions,
counterfactual comparisons and limitations (§11.3). Nothing here converts simulated frequencies
into real-world likelihoods.
"""

from __future__ import annotations

from typing import Any

from ..common import parse_dt
from ..model_ir.enums import FidelityTier, RegistryStatus, weakest_tier
from ..model_ir.expr import UNKNOWN, Q, EvalError, evaluate, value_to_display
from ..model_ir.index import ModelIndex, StateEnv, decode_value, typed_param_value
from ..model_ir.schemas import DecisionWorldModel, Distribution, RunManifest, WorldEvent
from ..model_ir.units import parse_unit
from ..storage.sqlite_store import Store
from ..temporal_kernel.replay import replay_event_log, replay_transitions, state_at
from ..temporal_kernel.state import SimState

LIMITATION_BASE = "This trajectory is a simulation under explicit assumptions; it does not estimate real-world likelihood."


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    if isinstance(v, str) and len(v) >= 16 and v[4] == "-" and "T" in v:
        return v[11:16] if v[:10] == v[:10] else v
    return str(v)


class Analytics:
    def __init__(self, store: Store):
        self.store = store

    # ---------------- loading ----------------
    def _load(self, run_id: str) -> tuple[RunManifest, DecisionWorldModel, list[WorldEvent], dict[str, Any], Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        man: RunManifest = run["manifest"]
        model = self.store.get_model_by_hash(man.model_ref.hash)["model"]
        return man, model, self.store.get_events(run_id), run["genesis"], run["result"]

    def _params(self, model: DecisionWorldModel, man: RunManifest) -> dict[str, Any]:
        out = {}
        scen = {s.id: s for s in model.scenarios}
        for p in model.parameters:
            raw = p.distribution.model_dump(mode="json", exclude_none=True) if p.type == "distribution" and p.distribution else p.value
            for sid in man.scenario_ids:
                if sid in scen and p.id in scen[sid].parameter_overrides:
                    raw = scen[sid].parameter_overrides[p.id]
                if sid in p.scenario_values:
                    raw = p.scenario_values[sid]
            raw = man.parameter_overrides.get(p.id, raw)
            out[p.id] = Distribution.model_validate(raw) if p.type == "distribution" and isinstance(raw, dict) else (
                raw if p.type == "distribution" else typed_param_value(p, raw))
        return out

    def _metrics_at(self, model, idx, world, params, at) -> dict[str, Any]:
        env = StateEnv(idx, world, params, parse_dt(at))
        out = {}
        for m in model.metrics:
            if not m.computable or not m.expr:
                continue
            try:
                v = evaluate(m.expr, env)
                if isinstance(v, Q) and m.unit:
                    v = v.to(parse_unit(m.unit))
                out[m.id] = value_to_display(v)
            except (EvalError, ValueError):
                out[m.id] = "ERROR"
        return out

    # ---------------- summaries ----------------
    def describe_event(self, e: WorldEvent, model: DecisionWorldModel, names: dict[str, str]) -> str:
        idx = {m.id: m for m in model.mechanisms}
        changes = ", ".join(f"{op.key} → {_fmt(op.after)}" for op in e.patch if op.layer == "world")[:240]
        who = names.get(e.actor_id or "", e.actor_id or "")
        if e.kind == "POLICY_INIT":
            return f"Policy '{e.payload.get('policy_id')}' applied" + (f" with scenarios {e.payload.get('scenario_ids')}" if e.payload.get("scenario_ids") else "")
        if e.kind == "AGENT_DECISION":
            intent = e.payload.get("decision", {}).get("intent", {})
            args = intent.get("parameters") or {}
            argtxt = " ".join(str(v) for v in args.values())
            if e.event_type == "agent_wait":
                return f"{who} waited"
            if e.event_type == "agent_intent_rejected":
                return f"{who}'s intent '{intent.get('action_type')} {argtxt}' was rejected ({e.payload.get('rejection_code')}); world unchanged"
            return f"{who}: {intent.get('action_type', '').replace('_', ' ')} {argtxt}".strip()
        if e.kind == "MECHANISM_TICK":
            m = idx.get(e.transition_ref)
            return f"{(m.description or m.id) if m else e.event_type}" + (f" ({changes})" if changes else " (no change)")
        if e.kind == "GATE_FIRE":
            return f"Threshold crossed: {e.payload.get('condition')}" + (f" ({changes})" if changes else "")
        if e.kind == "CONSTRAINT_BLOCKED":
            return f"Blocked by constraint {e.payload.get('constraint')}: {e.payload.get('detail')} — value not clipped"
        if e.kind == "EXOGENOUS":
            return e.payload.get("description") or e.event_type
        if e.kind == "QUIESCENT_COLLAPSE":
            return "No registered process changes state before the horizon (documented collapse)"
        if e.kind == "INTERVENTION":
            return f"Intervention at fork: {e.payload.get('intervention', {}).get('description', '')}"
        payload = {k: value_to_display(decode_value(v)) for k, v in (e.payload.get("event_payload") or {}).items()}
        pl = ", ".join(f"{k}={v if not isinstance(v, dict) else v.get('value')}" for k, v in payload.items())
        return f"{e.event_type.replace('_', ' ')}" + (f" [{pl}]" if pl else "") + (f" → {changes}" if changes else "")

    def timeline(self, run_id: str, max_events: int = 2000) -> dict[str, Any]:
        man, model, events, genesis, result = self._load(run_id)
        idx = ModelIndex(model)
        params = self._params(model, man)
        names = {a.id: a.display_name or a.id for a in model.agents}
        state = SimState.from_snapshot(genesis["snapshot"])
        series: dict[str, list[dict[str, Any]]] = {m.id: [] for m in model.metrics if m.computable and m.expr}
        first_at = events[0].sim_at if events else model.scope.horizon_start
        for k, v in self._metrics_at(model, idx, state.world, params, model.scope.horizon_start).items():
            series[k].append({"sim_at": model.scope.horizon_start, "seq": -1, "value": v})
        out_events = []
        for e in events[:max_events]:
            state.apply(e.patch)
            mvals = self._metrics_at(model, idx, state.world, params, e.sim_at)
            for k, v in mvals.items():
                if not series[k] or series[k][-1]["value"] != v:
                    series[k].append({"sim_at": e.sim_at, "seq": e.seq, "value": v})
            dec = e.payload.get("decision", {})
            out_events.append({
                "seq": e.seq, "event_id": e.event_id, "sim_at": e.sim_at, "event_type": e.event_type, "kind": e.kind,
                "actor": e.actor_id, "actor_name": names.get(e.actor_id or ""), "summary": self.describe_event(e, model, names),
                "changes": [{"ref": op.key, "before": op.before, "after": op.after} for op in e.patch if op.layer == "world"],
                "tier": str(e.tier) if e.tier else None, "parents": e.parent_event_ids, "draws": e.random_draw_refs,
                "assumptions": e.assumption_refs, "visibility": e.visibility_scope.scope,
                "rationale": (dec.get("intent") or {}).get("rationale_summary"), "uncertainty": (dec.get("intent") or {}).get("uncertainty_note"),
                "policy_kind": dec.get("policy_kind"), "rejection_code": e.payload.get("rejection_code"),
                "origin_kind": str(e.origin_kind),
            })
        horizon_end = model.scope.horizon_end
        last = result.last_simulated_at if result else (events[-1].sim_at if events else model.scope.horizon_start)
        return {
            "run_id": run_id, "label": man.label, "policy_id": man.policy_id, "scenario_ids": man.scenario_ids,
            "deadline_disclosure": str(man.deadline_disclosure), "fidelity_mode": str(man.fidelity_mode),
            "model_ref": man.model_ref.model_dump(), "status": str(result.status) if result else "RUNNING",
            "terminal_outcome": result.terminal_outcome if result else None, "reason": result.reason if result else None,
            "outcome_flags": result.outcome_flags if result else [],
            "horizon_start": model.scope.horizon_start, "horizon_end": horizon_end, "last_simulated_at": last,
            "horizon_reached": bool(result and result.horizon_reached),
            "grayed_after": None if (result and (result.horizon_reached or str(result.status) == "TERMINAL_STATE")) else last,
            "grayed_reason": (None if (result and result.horizon_reached) else
                              ("trajectory ended at a terminal state" if result and str(result.status) == "TERMINAL_STATE" else
                               f"horizon not reached: {result.reason if result else 'running'}")),
            "events": out_events, "series": series,
            "metric_meta": {m.id: {"label": m.label or m.id, "unit": m.unit, "computable": m.computable, "reason": m.reason} for m in model.metrics},
            "resolution_note": "Points are committed states only; values between events are not interpolated.",
            "first_event_at": first_at,
        }

    def state_at_seq(self, run_id: str, seq: int, viewer_scope: str = "public") -> dict[str, Any]:
        man, model, events, genesis, _ = self._load(run_id)
        st = state_at(genesis["snapshot"], events, seq)
        idx = ModelIndex(model)
        world = {}
        for ref, v in st.world.items():
            sv = idx.state.get(ref)
            if sv is None or sv.type == "ledger":
                continue
            world[ref] = {"value": v, "unit": sv.unit, "tier": str(sv.epistemic_tier), "label": sv.label or ref,
                          "visibility": sv.visibility.scope, "origin": str(sv.origin)}
        at = next((e.sim_at for e in events if e.seq == seq), model.scope.horizon_start)
        return {"run_id": run_id, "seq": seq, "sim_at": at, "world": world, "state_hash": st.hash(),
                "note": "Committed simulated state (SIMULATION_OUTPUT), not an observation of the real world."}

    # ---------------- explanations ----------------
    def why(self, run_id: str, seq: int, depth: int = 8) -> dict[str, Any]:
        man, model, events, _, _ = self._load(run_id)
        by_id = {e.event_id: e for e in events}
        names = {a.id: a.display_name or a.id for a in model.agents}
        mechs = {m.id: m for m in model.mechanisms}
        target = next((e for e in events if e.seq == seq), None)
        if target is None:
            raise KeyError(seq)
        chain, frontier, seen = [], [target], set()
        while frontier and len(chain) < depth:
            e = frontier.pop(0)
            if e.event_id in seen:
                continue
            seen.add(e.event_id)
            item: dict[str, Any] = {"seq": e.seq, "sim_at": e.sim_at, "category": "OBSERVED_EVENT",
                                    "text": f"At simulated {e.sim_at[:16]}: {self.describe_event(e, model, names)}"}
            if e.kind == "AGENT_DECISION":
                intent = e.payload.get("decision", {}).get("intent", {})
                item["category"] = "AGENT_ACTION"
                item["rationale"] = intent.get("rationale_summary")
                item["uncertainty"] = intent.get("uncertainty_note")
                item["policy_kind"] = e.payload.get("decision", {}).get("policy_kind")
            for ref in (e.transition_ref or "").split(","):
                m = mechs.get(ref)
                if m:
                    item.setdefault("mechanisms", []).append({"id": m.id, "description": m.description, "tier": str(m.epistemic_tier),
                                                              "evidence_refs": m.evidence_refs, "assumptions": m.assumptions})
            if e.random_draw_refs:
                item["random_draws"] = [{"stream": d["stream"], "value": d["value"], "distribution": d.get("distribution")} for d in e.random_draw_refs]
            chain.append(item)
            frontier += [by_id[p] for p in e.parent_event_ids if p in by_id]
            if not e.parent_event_ids and e.seq > 0 and e.kind == "AGENT_DECISION":
                frontier += [x for x in events if x.seq < e.seq and x.kind != "AGENT_DECISION"][-1:]
        return {"run_id": run_id, "seq": seq, "chain": chain, "limitations": [LIMITATION_BASE] + self._limitations(model, man)}

    def _limitations(self, model: DecisionWorldModel, man: RunManifest) -> list[str]:
        out = []
        for m in model.mechanisms:
            if m.epistemic_tier in (FidelityTier.C_ILLUSTRATIVE, FidelityTier.X_LIMITED_OR_ABSTAIN):
                out.append(f"{m.id} is {m.epistemic_tier}: {m.description}")
        for sid in man.scenario_ids:
            s = next((x for x in model.scenarios if x.id == sid), None)
            if s:
                out.append(f"Scenario '{s.label}' is an explicit assumption ({s.kind}); no probability is asserted.")
        for ov in man.agent_configs.values():
            for o in ov.overlays:
                out.append(f"Run-local overlay on {ov.agent_id}: {o.key}={o.value} — hypothesis, not the real profile.")
        for r in model.reuse_manifest:
            if r.get("used"):
                out.append(f"Reuses {r['entry']}")
        return out

    def explain(self, run_id: str) -> dict[str, Any]:
        man, model, events, _, result = self._load(run_id)
        names = {a.id: a.display_name or a.id for a in model.agents}
        key = [e for e in events if e.kind in ("GATE_FIRE", "CONSTRAINT_BLOCKED", "EXOGENOUS")
               or e.event_type in ("governance_satisfied",) or (e.kind == "AGENT_DECISION" and e.event_type.startswith("action:"))
               or (e.kind == "DOMAIN_EVENT" and e.patch)]
        observed = [{"seq": e.seq, "sim_at": e.sim_at, "text": self.describe_event(e, model, names)} for e in key[:40]]
        mech_ids = {r for e in events for r in (e.transition_ref or "").split(",") if r}
        mechanisms = [{"id": m.id, "text": m.description or m.id, "tier": str(m.epistemic_tier), "evidence_refs": m.evidence_refs}
                      for m in model.mechanisms if m.id in mech_ids]
        actions = [{"seq": e.seq, "sim_at": e.sim_at, "actor": names.get(e.actor_id, e.actor_id), "text": self.describe_event(e, model, names),
                    "rationale": e.payload.get("decision", {}).get("intent", {}).get("rationale_summary")}
                   for e in events if e.kind == "AGENT_DECISION" and e.event_type != "agent_wait"][:40]
        return {
            "run_id": run_id, "status": str(result.status) if result else None, "outcome": result.terminal_outcome if result else None,
            "observed_events": observed, "mechanisms": mechanisms, "agent_actions": actions,
            "counterfactuals": [], "limitations": [LIMITATION_BASE] + self._limitations(model, man),
        }

    def card(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        man: RunManifest = run["manifest"]
        result = run["result"]
        model = self.store.get_model_by_hash(man.model_ref.hash)["model"]
        labels = {m.id: (m.label or m.id) for m in model.metrics}
        weakest = result.weakest_tier if result else None
        outcome = result.terminal_outcome if result else None
        bits = []
        for mid, v in (result.final_metrics if result else {}).items():
            if v.get("status") != "OK":
                continue
            val = v.get("value")
            if isinstance(val, dict):
                unit = "" if val.get("unit") in ("1", None) else f" {val['unit']}"
                txt = f"{val['value']:,.1f}".rstrip("0").rstrip(".") + unit
            elif isinstance(val, bool):
                txt = "yes" if val else "no"
            else:
                txt = _fmt(val)
            bits.append(f"{labels.get(mid, mid)}: {txt}")
        caveats = []
        if result and not result.horizon_reached and str(result.status) != "TERMINAL_STATE":
            caveats.append(f"Horizon not reached: {result.reason}")
        if weakest in (FidelityTier.C_ILLUSTRATIVE, FidelityTier.X_LIMITED_OR_ABSTAIN):
            caveats.append(f"Depends on {weakest} mechanisms (illustrative assumptions)")
        scen = {s.id: s.label for s in model.scenarios}
        if man.scenario_ids:
            caveats.append("Scenario assumptions: " + ", ".join(scen.get(x, x) for x in man.scenario_ids))
        if result and "UNMODELED_CRITICAL_DEPENDENCY" in result.outcome_flags:
            caveats.append("Some metrics are UNKNOWN or non-computable (the model abstains rather than guessing)")
        when = result.last_simulated_at[:16].replace("T", " ") if result else ""
        head = (outcome.replace("_", " ").lower() if outcome else
                ("reached the horizon" if result and result.horizon_reached else str(result.status if result else "running").lower()))
        return {
            "run_id": run_id, "name": man.label or man.policy_id, "policy_id": man.policy_id, "scenario_ids": man.scenario_ids,
            "deadline_disclosure": str(man.deadline_disclosure), "fidelity_mode": str(man.fidelity_mode),
            "status": str(result.status) if result else "RUNNING", "outcome": outcome, "reason": result.reason if result else None,
            "synopsis": f"{head.capitalize()} at simulated {when}. " + "; ".join(bits[:5]),
            "horizon_reached": bool(result and result.horizon_reached), "weakest_tier": str(weakest) if weakest else None,
            "model_tier_note": "tier labels describe evidence support, not forecast accuracy", "caveats": caveats,
            "events": result.events_committed if result else 0, "flags": result.outcome_flags if result else [],
            "model_version": man.model_ref.version,
        }

    def compare(self, run_a: str, run_b: str) -> dict[str, Any]:
        ma, model_a, ea, _, ra = self._load(run_a)
        mb, model_b, eb, _, rb = self._load(run_b)
        names = {a.id: a.display_name or a.id for a in model_a.agents}
        same_model = ma.model_ref.hash == mb.model_ref.hash
        diff_fields = {}
        for f in ("policy_id", "scenario_ids", "deadline_disclosure", "parameter_overrides", "random_streams"):
            va, vb = getattr(ma, f), getattr(mb, f)
            if va != vb:
                diff_fields[f] = {"a": va if not hasattr(va, "value") else str(va), "b": vb if not hasattr(vb, "value") else str(vb)}
        ov_a = {k: [o.model_dump() for o in v.overlays] for k, v in ma.agent_configs.items() if v.overlays}
        ov_b = {k: [o.model_dump() for o in v.overlays] for k, v in mb.agent_configs.items() if v.overlays}
        if ov_a != ov_b:
            diff_fields["agent_overlays"] = {"a": ov_a, "b": ov_b}
        if ma.branch_parent or mb.branch_parent:
            diff_fields["branch_parent"] = {"a": ma.branch_parent.model_dump() if ma.branch_parent else None,
                                            "b": mb.branch_parent.model_dump() if mb.branch_parent else None}

        def sig(e):
            return (e.sim_at, e.event_type, e.actor_id, [(op.key, op.after) for op in e.patch if op.layer == "world"])

        prefix = 0
        if same_model:
            for x, y in zip(ea, eb):
                if sig(x) != sig(y):
                    break
                prefix += 1
        first_a = ea[prefix] if prefix < len(ea) else None
        first_b = eb[prefix] if prefix < len(eb) else None
        ancestor_hash = ea[prefix - 1].post_state_hash if prefix > 0 else (ea[0].pre_state_hash if ea else None)
        metrics = {}
        for mid in sorted(set(ra.final_metrics if ra else {}) | set(rb.final_metrics if rb else {})):
            metrics[mid] = {"a": (ra.final_metrics.get(mid) if ra else None), "b": (rb.final_metrics.get(mid) if rb else None)}
        counterfactual = None
        if same_model and first_a is not None:
            axis = ", ".join(diff_fields) or "no manifest difference (stochastic or Agent-model variation)"
            counterfactual = (f"In the paired run with the same model and exogenous streams but a different {axis}, the trajectories "
                              f"share {prefix} committed events; the first difference is at simulated {first_a.sim_at[:16]}: "
                              f"A: {self.describe_event(first_a, model_a, names)} | B: {self.describe_event(first_b, model_b, names) if first_b else 'no further event'}.")
        return {
            "run_a": run_a, "run_b": run_b, "same_model": same_model,
            "banner": None if same_model else "Model assumptions changed; results are not paired under identical causal structure.",
            "intervention": diff_fields, "common_prefix_events": prefix, "common_ancestor_state_hash": ancestor_hash,
            "first_divergence": {"a": {"seq": first_a.seq, "sim_at": first_a.sim_at, "text": self.describe_event(first_a, model_a, names)} if first_a else None,
                                 "b": {"seq": first_b.seq, "sim_at": first_b.sim_at, "text": self.describe_event(first_b, model_b, names)} if first_b else None},
            "outcomes": {"a": ra.terminal_outcome if ra else None, "b": rb.terminal_outcome if rb else None,
                         "a_status": str(ra.status) if ra else None, "b_status": str(rb.status) if rb else None},
            "metrics": metrics, "counterfactual": counterfactual,
            "limitations": [LIMITATION_BASE, "Within-model attribution only: identical exogenous streams make differences attributable to the intervention inside this model, not in reality."],
        }

    def replay(self, run_id: str) -> dict[str, Any]:
        man, model, events, genesis, result = self._load(run_id)
        ev = replay_event_log(genesis["snapshot"], genesis["hash"], events)
        final_ok = (result is None) or ev.final_state_hash == result.final_state_hash
        out = {"event_log": ev.model_dump(exclude={"reconstructed_state"}), "final_hash_matches_result": final_ok}
        if man.branch_parent is None:
            tr = replay_transitions(model, man, events, profiles=self._profiles_for_replay(man, model))
            out["transition"] = tr.model_dump(exclude={"reconstructed_state"})
        out["llm_calls_during_replay"] = 0
        return out

    def _profiles_for_replay(self, man: RunManifest, model: DecisionWorldModel):
        from ..agent_runtime.profiles import select_working_set

        dims = {d for a in model.actions for d in a.appraisal}
        out = {}
        for aid, ref in man.profile_snapshot_refs.items():
            pid, _, ver = ref.rpartition("@v")
            p = self.store.get_profile(pid, int(ver))
            if p:
                out[aid] = select_working_set(p, dims)
        return out

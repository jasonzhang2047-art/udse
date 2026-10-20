"""Derived, read-only views for the Futures map and World model screens (docs/design/ui-redesign.md §10).

Threads collapse identical runs, branch at the first observable difference from the thread with
the longest shared history, and carry only key moments in plain language. Nothing here mutates
state or exposes other people's private profile features.
"""

from __future__ import annotations

import re
from typing import Any

from ..common import canonical_json, parse_dt
from ..model_ir.enums import COVERAGE_UI_LABEL, CoverageStatus, FidelityTier, TIER_ORDER
from ..model_ir.expr import Q, EvalError, evaluate, value_to_display
from ..model_ir.index import ModelIndex, StateEnv, decode_value
from ..model_ir.schemas import DecisionWorldModel, RunManifest, WorldEvent
from ..model_ir.units import parse_unit
from ..storage.sqlite_store import Store
from ..temporal_kernel.state import SimState
from .analytics import Analytics

OUTCOME_WORDS = {
    "CASH_EXHAUSTED": "Ran out of cash",
    "NO_AGREEMENT_BY_DEADLINE": "No agreement by the decide-by time",
    "UNRESOLVED_TABLE_AVAILABILITY": "Arrived without a confirmed table",
    "DINED": "Dined together",
    "DEFERRED": "Stayed in",
    "OVER_BUDGET": "Would exceed the budget",
    "ARRIVED_AFTER_CLOSING": "Arrived after closing",
}
STATUS_WORDS = {
    "COMPLETED_HORIZON": "Reached the horizon",
    "BUDGET_EXHAUSTED": "Stopped early — not simulated after this",
    "MODEL_EXECUTION_BLOCKED": "Model could not continue",
    "FAILED": "Run failed (infrastructure)",
    "CANCELLED": "Cancelled",
}
DETAIL = {"depth": "detailed", "screening": "preview", "sensitivity": "probe", "replay": "preview"}
GOV_VERBS = {"propose": "proposes", "endorse": "agrees to", "object": "objects to", "withdraw": "withdraws"}


def humanize(s: str) -> str:
    s = re.sub(r"[_\.]+", " ", str(s)).strip()
    return s[:1].upper() + s[1:]


def _metric_label(label: str, unit: str | None) -> str:
    """'budget_usd' / 'budget usd' → 'Budget'; the unit is shown on the axis, not in the name."""
    s = humanize(label)
    u = (unit or "").lower()
    for suffix in sorted({u + "s", u} - {"s", "", "1", "1s"}, key=len, reverse=True):
        s = re.sub(rf"\s+{re.escape(suffix)}$", "", s, flags=re.I)
    return s


def outcome_words(code: str | None, status: str) -> str:
    if code:
        return OUTCOME_WORDS.get(code, humanize(code.lower()))
    return STATUS_WORDS.get(status, humanize(status.lower()))


def _signature(e: WorldEvent) -> tuple:
    return (e.sim_at, e.event_type, e.actor_id, tuple((op.key, canonical_json(op.after)) for op in e.patch if op.layer == "world"))


def _prefix(a: list, b: list) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


class Labeler:
    """Plain-language labels for refs, entities, actions and events of one model."""

    def __init__(self, model: DecisionWorldModel):
        self.m = model
        self.idx = ModelIndex(model)
        self.entity = {e.id: (e.label or humanize(e.id)).replace(" (fixture)", "") for e in model.entities}
        self.agent = {a.id: a.display_name or humanize(a.id) for a in model.agents}
        self.policy = {p.id: p.label for p in model.policies}
        self.scenario = {s.id: s.label for s in model.scenarios}
        self.state = {s.ref: (s.label or humanize(s.ref.split(".", 1)[-1])) for s in model.state}

    def value(self, v: Any) -> str:
        if isinstance(v, str):
            if v in self.entity:
                return self.entity[v]
            if v in self.policy:
                return self.policy[v]
            if len(v) >= 16 and v[4] == "-" and "T" in v:
                return v[11:16] if self.m.scope.time_resolution == "minute" else v[:10]
            return v.replace("_", " ")
        if isinstance(v, bool):
            return "yes" if v else "no"
        if isinstance(v, float):
            return f"{v:,.2f}".rstrip("0").rstrip(".")
        d = value_to_display(decode_value(v)) if isinstance(v, dict) else v
        if isinstance(d, dict) and "value" in d:
            unit = "" if d.get("unit") in ("1", None) else f" {d['unit']}"
            return f"{d['value']:,.2f}".rstrip("0").rstrip(".") + unit
        return str(d)

    # ---- Agent reasoning in plain words (the exact engine rationale stays in the event log) ----
    def _opt(self, o: str) -> str:
        return self.entity.get(o.strip(), self.policy.get(o.strip(), o.strip().replace("_", " ")))

    def _refs(self, raw: str) -> str:
        items = re.findall(r"'([^']+)'", raw) or [raw]
        return ", ".join(humanize(i.split(".")[-1]).lower().replace(" pp", " per person") for i in items)

    def _factors(self, raw: str) -> str:
        out = []
        for part in raw.split(";"):
            m = (re.match(r"\s*(.+?) (-?[\d.,]+(?: [A-Za-z/%]+)?) \(weight [^)]*\)?\s*$", part)
                 or re.match(r"\s*(.+) (\S+) \(weight [^)]*\)?\s*$", part))
            if not m:
                continue
            dim, val = m.group(1).replace(" pp", " per person"), m.group(2)
            if val == "UNKNOWN":
                out.append(f"{dim}: unknown")
            elif val.endswith(" USD"):
                out.append(f"{dim} ${val[:-4]}")
            elif val in ("True", "true", "yes", "False", "false", "no"):
                out.append(f"{dim}: {'yes' if val in ('True', 'true', 'yes') else 'no'}")
            else:
                out.append(f"{dim} {val}")
        return " · ".join(out)

    def reasoning(self, text: str | None) -> str | None:
        if not text:
            return text
        body, _, factors = text.partition(" Factors: ")
        body = body.strip()
        rules: list[tuple[str, Any]] = [
            (r"^(Object|Endorse|Counter) proposal \S+ \(([^)]+)\): (.*)$",
             lambda m: f"{ {'Object': 'Objects to', 'Endorse': 'Supports', 'Counter': 'Pushes back on'}[m.group(1)]} {self._opt(m.group(2))} — {self._why(m.group(3))}"),
            (r"^Counter-proposal: prefer (\S+) \([^)]*\) over (\S+) \([^)]*\)$",
             lambda m: f"Counters with {self._opt(m.group(1))} — likes it more than {self._opt(m.group(2))}"),
            (r"^Propose (\S+): best option satisfying my constraints.*$",
             lambda m: f"Proposes {self._opt(m.group(1))} — the best option that meets every must-have"),
            (r"^Cannot verify (.+) for (\S+); requesting information\.?$",
             lambda m: f"Can't confirm {self._refs(m.group(1))} at {self._opt(m.group(2))}, so asks first"),
            (r"^No option verifiably satisfies my constraints; asking about (\S+)\.?$",
             lambda m: f"No option clearly meets every must-have — asks about {self._opt(m.group(1))}"),
            (r"^(\S+) has positive value for me.*$", lambda m: "Acts because it is worth it to them"),
            (r"^No eligible action improves on waiting.*$", lambda m: "Waits — nothing is worth doing yet"),
        ]
        plain = body
        for pat, fn in rules:
            m = re.match(pat, body)
            if m:
                plain = fn(m)
                break
        f = self._factors(factors.rstrip(".")) if factors else ""
        return plain + (f". Weighed: {f}" if f else "")

    def _why(self, why: str) -> str:
        why = why.strip()
        m = re.match(r"cannot verify hard constraint\(s\) (.+)$", why)
        if m:
            return f"can't confirm {self._refs(m.group(1))}"
        m = re.match(r"violates my hard constraint\(s\) (.+)$", why)
        if m:
            return f"breaks a must-have: {self._refs(m.group(1))}"
        if why.startswith("worse for me than keeping the current course"):
            return "worse for them than not changing course"
        if why.startswith("acceptable:"):
            return "close enough to their own first choice"
        m = re.match(r"prefer (\S+) \([^)]*\) over (\S+) \([^)]*\)$", why)
        if m:
            return f"prefers {self._opt(m.group(1))}"
        return why

    def changes(self, e: WorldEvent, limit: int = 3) -> str:
        bits = []
        for op in e.patch:
            if op.layer != "world":
                continue
            sv = self.idx.state.get(op.key)
            if sv is None or sv.type == "ledger" or sv.visibility.scope == "hidden":
                continue
            bits.append(f"{self.state.get(op.key, op.key)} → {self.value(op.after)}")
        return "; ".join(bits[:limit])

    def moment(self, e: WorldEvent) -> tuple[str, str] | None:
        """(kind, headline) for key events; None for routine ones."""
        k = e.kind
        who = self.agent.get(e.actor_id or "", "")
        if k == "POLICY_INIT":
            pol = self.policy.get(e.payload.get("policy_id"), e.payload.get("policy_id"))
            scen = [self.scenario.get(s, s) for s in e.payload.get("scenario_ids") or []]
            return "start", f"Start: {pol}" + (f" · assuming {', '.join(scen)}" if scen else "")
        if k == "AGENT_DECISION":
            if not e.event_type.startswith("action:"):
                return None
            act = self.idx.actions.get(e.event_type.split(":", 1)[1])
            intent = e.payload.get("decision", {}).get("intent", {})
            args = intent.get("parameters") or {}
            if act and act.governance:
                opt = args.get(act.governance.option_arg) if act.governance.option_arg else None
                if opt is None:
                    for op in e.patch:
                        if op.layer == "world" and isinstance(op.after, dict) and op.after.get("proposals"):
                            opt = op.after["proposals"][-1].get("option")
                return "action", f"{who}'s Agent {GOV_VERBS[act.governance.act]} {self.value(opt) if opt else 'the proposal'}"
            what = (act.description.rstrip(".") if act and act.description else humanize(e.event_type.split(":", 1)[1]))
            what = what[:1].lower() + what[1:]
            arg = ", ".join(self.value(v) for v in args.values())
            return "action", f"{who}'s Agent: {what}" + (f" — {arg}" if arg else "")
        if k == "GATE_FIRE":
            mech = self.idx.mechanisms.get(e.transition_ref or "")
            return "threshold", (mech.description.rstrip(".") if mech and mech.description else humanize(e.event_type))
        if k == "EXOGENOUS":
            return "external", (e.payload.get("description") or humanize(e.event_type)).rstrip(".")
        if k == "CONSTRAINT_BLOCKED":
            c = self.idx.constraints.get(e.payload.get("constraint", ""))
            return "blocked", "Stopped by a rule: " + ((c.description.rstrip(".") if c and c.description else e.payload.get("detail", "")))
        if k == "QUIESCENT_COLLAPSE":
            return "end", "Nothing more changes before the horizon"
        if k == "INTERVENTION":
            return "external", "Fork: " + (e.payload.get("intervention", {}).get("description") or "intervention")
        if k == "DOMAIN_EVENT":
            pl = {kk: decode_value(v) for kk, v in (e.payload.get("event_payload") or {}).items()}
            if e.event_type == "governance_satisfied":
                return "agreement", f"Agreement reached: {self.value(pl.get('option'))}"
            parts = [f"{humanize(kk).lower()} {self.value(v if not isinstance(v, Q) else {'value': v.value, 'unit': v.unit.label})}"
                     for kk, v in pl.items() if kk not in ("mechanism", "proposal_id")]
            ch = self.changes(e, 2)
            return "info", humanize(e.event_type) + (f" · {', '.join(parts)}" if parts else "") + (f" ({ch})" if ch and not parts else "")
        return None


_SCEN_PREFIX = re.compile(r"^\s*(EVIDENCE|EXPLORATORY|HYPOTHETICAL)\s+SCENARIO\s*:\s*", re.I)


_PCT_SHIFT = re.compile(r"^(?:[A-Za-z_]+\.)?([A-Za-z_]+)\s*([+-])(\d+(?:\.\d+)?)%$")


def _plain_scenario(label: str) -> str:
    s = _SCEN_PREFIX.sub("", label or "").strip()
    m = _PCT_SHIFT.match(s)
    if m:  # generic clean-room variations such as "garden.monthly_dues_income +20%"
        return f"{m.group(1).replace('_', ' ')} {m.group(3)}% {'higher' if m.group(2) == '+' else 'lower'}"
    return s


def _scenario_chip(label: str) -> str:
    m = _SCEN_PREFIX.match(label or "")
    kind = (m.group(1).capitalize() + " scenario") if m else "Assumes"
    plain = _plain_scenario(label)
    return f"{kind}: {plain[:1].upper() + plain[1:]}"


def _lower_first(s: str) -> str:
    return s[:1].lower() + s[1:] if s[:2] != s[:2].upper() else s


def _thread_label(man: RunManifest, lab: "Labeler") -> str:
    """Policy name plus plain-language qualifiers; never the raw arm label with engine prefixes."""
    base = lab.policy.get(man.policy_id, man.policy_id) if (man.parameter_overrides or " × " not in (man.label or "") and "(sensitivity probe)" in (man.label or "")) \
        else (man.label or "").split(" × ")[0] or lab.policy.get(man.policy_id, man.policy_id)
    quals = [_lower_first(_plain_scenario(lab.scenario.get(s, s))) for s in man.scenario_ids]
    quals += [f"{lab.agent.get(a, a)} has {o.key.split('.')[-1].replace('_', ' ')} {o.value}"
              for a, c in man.agent_configs.items() for o in c.overlays]
    quals += [f"{k.split('.')[-1].replace('_', ' ')} = {v}" for k, v in man.parameter_overrides.items()]
    if str(man.deadline_disclosure) == "DEADLINE_AWARE" and " × " in (man.label or "") and "deadline" in man.label:
        quals.append("the deadline is told to every Agent")
    return f"{base} — if {', '.join(quals)}" if quals else base


class FuturesView:
    def __init__(self, store: Store):
        self.store = store
        self.analytics = Analytics(store)

    # ------------------------------------------------------------------
    def plain_why(self, run_id: str, why: dict[str, Any]) -> dict[str, Any]:
        """Add plain-language `reasoning` beside each raw Agent rationale in a causal chain."""
        run = self.store.get_run(run_id)
        row = run and self.store.get_model_by_hash(run["manifest"].model_ref.hash)
        if not row:
            return why
        lab = Labeler(row["model"])
        by_seq = {e.seq: e for e in self.store.get_events(run_id)}
        for step in why.get("chain", []):
            e = by_seq.get(step.get("seq"))
            mo = lab.moment(e) if e is not None else None
            if mo:
                step["headline"] = mo[1]
            if step.get("rationale"):
                step["reasoning"] = lab.reasoning(step["rationale"])
        return why

    # ------------------------------------------------------------------
    def futures(self, decision_id: str, version: int | None = None, light: bool = False) -> dict[str, Any]:
        row = self.store.get_model(f"wm_{decision_id}", version)
        if row is None:
            raise KeyError("no model for this decision")
        model: DecisionWorldModel = row["model"]
        lab = Labeler(model)
        runs = [r for r in self.store.list_runs(decision_id=decision_id)
                if r["result"] is not None and r["manifest"].model_ref.hash == model.hash]
        withheld = set(row["report"].get("non_computable_metrics", {}))
        numeric = [m for m in model.metrics if m.computable and m.expr and m.unit and m.id not in withheld]
        policy_order = {p.id: i for i, p in enumerate(model.policies)}
        frontier = self.store.get_frontier(decision_id) or {"items": []}
        interest = [i.get("policy_id") for i in frontier["items"] if i.get("user_interest") and i.get("policy_id")]

        # 1. signatures and collapse identical histories into threads
        groups: dict[str, dict[str, Any]] = {}
        for r in runs:
            man: RunManifest = r["manifest"]
            events = self.store.get_events(man.run_id)
            sig = [_signature(e) for e in events]
            key = canonical_json([list(s[:3]) + [list(s[3])] for s in sig]) + str(r["result"].status) + str(r["result"].terminal_outcome)
            g = groups.setdefault(key, {"runs": [], "sig": sig, "events": events})
            g["runs"].append(r)
        threads: list[dict[str, Any]] = []
        for g in groups.values():
            rank = {"depth": 0, "screening": 1, "replay": 1, "sensitivity": 2}
            g["runs"].sort(key=lambda r: (rank.get(str(r["manifest"].fidelity_mode), 3), r["manifest"].label or ""))
            rep = g["runs"][0]
            man: RunManifest = rep["manifest"]
            res = rep["result"]
            detail = DETAIL.get(str(man.fidelity_mode), "preview")
            status = str(res.status)
            fam = res.terminal_outcome or ("HORIZON" if res.horizon_reached else status)
            threads.append({
                "rep": rep, "sig": g["sig"], "events": g["events"],
                "thread_id": "", "run_ids": [r["manifest"].run_id for r in g["runs"]], "rep_run_id": man.run_id,
                "label": _thread_label(man, lab), "detail": detail,
                "also": sorted({DETAIL.get(str(r["manifest"].fidelity_mode), "preview") for r in g["runs"]} - {detail}),
                "policy_id": man.policy_id, "family": fam, "outcome_label": outcome_words(res.terminal_outcome, status),
                "status": status, "ended_at": res.last_simulated_at, "horizon_reached": res.horizon_reached,
                "stopped_early": (not res.horizon_reached) and status != "TERMINAL_STATE",
                "carries": ([_scenario_chip(lab.scenario.get(s, s)) for s in man.scenario_ids]
                            + [f"Hypothetical: {lab.agent.get(a, a)} {o.key.split('.')[-1].replace('_', ' ')} = {o.value}"
                               for a, c in man.agent_configs.items() for o in c.overlays]
                            + (["Agents were told about the deadline"] if str(man.deadline_disclosure) == "DEADLINE_AWARE" else [])
                            + [f"Sensitivity probe: {k.split('.')[-1].replace('_', ' ')} = {v}" for k, v in man.parameter_overrides.items()]),
                "reason": res.reason, "weakest_tier": str(res.weakest_tier) if res.weakest_tier else None,
                "_order": (0 if detail == "detailed" else 1 if detail == "preview" else 2,
                           0 if (man.policy_id in interest[:1]) else 1, len(man.scenario_ids) + len(man.parameter_overrides) +
                           sum(len(c.overlays) for c in man.agent_configs.values()) + (1 if str(man.deadline_disclosure) == "DEADLINE_AWARE" else 0),
                           policy_order.get(man.policy_id, 99), man.label or ""),
            })
        threads.sort(key=lambda t: t["_order"])
        # a trunk that dies at t0 (e.g. "stay in") makes a poor spine: prefer the longest detailed history
        if threads:
            det = [t for t in threads if t["detail"] == "detailed"] or threads
            trunk = next((t for t in det if len(t["sig"]) > 2), det[0])
            threads.remove(trunk)
            threads.insert(0, trunk)

        # 2. parents and split points
        for i, t in enumerate(threads):
            t["thread_id"] = f"t{i}"
            if i == 0:
                t.update(parent=None, split_index=0, split_at=model.scope.horizon_start, first_difference=None)
                continue
            best, best_n = threads[0], -1
            for p in threads[:i]:
                n = _prefix(t["sig"], p["sig"])
                if n > best_n:
                    best, best_n = p, n
            idx = best_n
            if idx < len(t["events"]):
                split_at = t["events"][idx].sim_at
            else:
                split_at = t["events"][-1].sim_at if t["events"] else model.scope.horizon_start
            mine = lab.moment(t["events"][idx])[1] if idx < len(t["events"]) and lab.moment(t["events"][idx]) else self._raw(lab, t["events"], idx)
            theirs = lab.moment(best["events"][idx])[1] if idx < len(best["events"]) and lab.moment(best["events"][idx]) else self._raw(lab, best["events"], idx)
            if mine == theirs:
                mc = lab.changes(t["events"][idx]) if idx < len(t["events"]) else ""
                tc = lab.changes(best["events"][idx]) if idx < len(best["events"]) else ""
                mine, theirs = f"{mine} ({mc})" if mc else mine, f"{theirs} ({tc})" if tc else theirs
            t.update(parent=best["thread_id"], split_index=idx, split_at=split_at, shared_events=idx,
                     first_difference={"self": mine, "parent": theirs})

        # 3. series, tier paths, moments
        out_threads = []
        for t in threads:
            rep = t["rep"]
            man: RunManifest = rep["manifest"]
            params = self.analytics._params(model, man)
            genesis = self.store.get_run(man.run_id)["genesis"]
            state = SimState.from_snapshot(genesis["snapshot"])
            series: dict[str, list] = {m.id: [] for m in numeric}

            def sample(at: str):
                env = StateEnv(lab.idx, state.world, params, parse_dt(at))
                for m in numeric:
                    try:
                        v = evaluate(m.expr, env)
                        if isinstance(v, Q):
                            v = round(v.to(parse_unit(m.unit)).value, 6)
                            if not series[m.id] or series[m.id][-1][1] != v:
                                series[m.id].append([at, v])
                    except (EvalError, ValueError, ZeroDivisionError):
                        pass

            sample(model.scope.horizon_start)
            tier_path: list[list[str]] = []
            weakest = None
            moments = []
            for i, e in enumerate(t["events"]):
                state.apply(e.patch)
                sample(e.sim_at)
                if e.tier:
                    nw = e.tier if weakest is None else max(weakest, e.tier, key=TIER_ORDER.index)
                    if nw != weakest:
                        weakest = nw
                        tier_path.append([e.sim_at, str(nw)])
                m = lab.moment(e)
                if m:
                    moments.append({"i": i, "seq": e.seq, "t": e.sim_at, "kind": m[0], "headline": m[1],
                                    "actor": e.actor_id, "tier": str(e.tier) if e.tier else None,
                                    "rationale": (rat := (e.payload.get("decision", {}).get("intent", {}) or {}).get("rationale_summary")),
                                    "reasoning": lab.reasoning(rat)})
            res = rep["result"]
            moments.append({"i": len(t["events"]), "seq": None, "t": res.last_simulated_at, "kind": "end",
                            "headline": t["outcome_label"], "actor": None, "tier": None, "rationale": res.reason, "reasoning": res.reason})
            out_threads.append({k: v for k, v in t.items() if k not in ("rep", "sig", "events", "_order")} | {
                "series": {} if light else series, "tier_path": tier_path, "moments": [] if light else moments,
                "synopsis": self._synopsis(t, moments, lab, model, res)})

        # 4. families (stable colour order), ghosts, evidence
        fam_order: list[str] = []
        for t in out_threads:
            if t["family"] not in fam_order:
                fam_order.append(t["family"])
        families = [{"key": f, "label": next(t["outcome_label"] for t in out_threads if t["family"] == f), "color_index": i}
                    for i, f in enumerate(fam_order)]
        ghosts = self._ghosts(frontier)
        return {
            "decision_id": decision_id,
            "horizon": {"start": model.scope.horizon_start, "end": model.scope.horizon_end, "resolution": model.scope.time_resolution},
            "model": {"version": model.model_version, "hash": model.hash, "high_stakes": model.scope.high_stakes,
                      "versions": [r["model"].model_version for r in self.store.list_model_versions(model_id=model.model_id)]},
            "evidence_as_of": self._as_of(decision_id),
            "metrics": [{"id": m.id, "label": _metric_label(m.label or m.id, m.unit), "unit": m.unit, "direction": m.direction} for m in numeric],
            "people": [{"id": a.id, "name": lab.agent[a.id]} for a in model.agents],
            "families": families, "threads": out_threads,
            "ghosts": ghosts[:6], "ghosts_total": len(ghosts),
            "evidence": self._evidence(decision_id),
            "note": "Paths are simulated under stated assumptions. Counts are coverage, never likelihood.",
        }

    @staticmethod
    def _raw(lab: Labeler, events: list[WorldEvent], i: int) -> str:
        if i >= len(events):
            return "history ends here"
        e = events[i]
        ch = lab.changes(e)
        return humanize(e.event_type.replace("tick:", "")) + (f" ({ch})" if ch else "")

    def _synopsis(self, t, moments, lab: Labeler, model, res) -> str:
        def when(iso: str) -> str:
            if model.scope.time_resolution in ("minute", "hour"):
                return iso[11:16]
            months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            return f"{months[int(iso[5:7]) - 1]} {iso[:4]}"

        turning = [m for m in moments if m["kind"] in ("action", "agreement", "external") and "proposes" not in m["headline"]
                   and "agrees to" not in m["headline"] and "objects to" not in m["headline"]]
        picks = turning[:1] + turning[-2:] if len(turning) > 3 else turning
        along = "; ".join(f"{m['headline'][:1].lower()}{m['headline'][1:]} ({when(m['t'])})" for m in picks)
        if t["horizon_reached"]:
            head = f"Reached the end of the horizon ({when(res.last_simulated_at)})."
        elif t["stopped_early"]:
            head = f"Simulation stopped early at {when(res.last_simulated_at)} — nothing after that point is simulated."
        else:
            head = f"{t['outcome_label']} — {when(res.last_simulated_at)}."
        return head + (f" Along the way: {along}." if along else "")

    def _as_of(self, decision_id: str) -> str | None:
        b = self.store.latest_evidence_bundle(decision_id)
        return b.as_of if b else None

    def _ghosts(self, frontier: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for it in frontier.get("items", []):
            st = it["status"]
            if it.get("outcome_match") or st in ("DEPTH_SIMULATED", "LOW_FIDELITY_EXPLORED"):
                continue
            if st == "REJECTED_VALIDATED_CONSTRAINT":
                kind = "ruled_out"
            elif st == "SET_ASIDE_BUDGET":
                kind = "set_aside"
            elif st == "REOPENED_AFTER_REVISION":
                kind = "reopened"
            else:
                kind = "unsimulated"
            reason = it.get("status_reason", "")
            m = re.match(r"requires unmodeled mechanism\(s\): (.*)", reason)
            why = ("needs a model of " + ", ".join(humanize(x).lower() for x in m.group(1).split(", "))) if m else reason
            generic = it["id"].startswith("fam_") and it.get("discovered_by") and all(
                d in ("behavioral_responses", "omitted_actors", "timing_irreversibility", "feedback_loops", "second_order_consequences",
                      "low_probability_high_impact", "adverse_cases") for d in it["discovered_by"]) and not it.get("policy_id")
            out.append({"id": it["id"], "label": it["label"], "status": st, "ui_label": COVERAGE_UI_LABEL[CoverageStatus(st)],
                        "why": why, "kind": kind, "citation": it.get("citation", []), "generic": bool(generic),
                        "_p": (0 if kind == "ruled_out" else 1 if kind == "reopened" else 2 if not generic else 3, it["label"])})
        out.sort(key=lambda g: g["_p"])
        for g in out:
            g.pop("_p")
        return out

    def _evidence(self, decision_id: str) -> dict[str, Any]:
        b = self.store.latest_evidence_bundle(decision_id)
        if b is None:
            return {"verified": 0, "stale": 0, "unknown": 0, "contested": 0, "user": 0, "warnings": [], "gaps": []}
        c = {"verified": 0, "stale": 0, "unknown": 0, "contested": 0, "user": 0}
        for cl in b.claims:
            if cl.object == "UNKNOWN":
                c["unknown"] += 1
            elif cl.status == "STALE":
                c["stale"] += 1
            elif cl.status == "CONFLICTED" or cl.record_type == "contextual_lead":
                c["contested"] += 1
            elif cl.status == "VERIFIED":
                c["verified"] += 1
            elif str(cl.origin_kind) == "USER_ASSERTED":
                c["user"] += 1
        gaps = [{"question": n.question, "consequence": n.consequence_if_unresolved, "status": n.status, "kind": str(n.kind)}
                for n in b.needs if n.status in ("UNRESOLVED", "ASK_USER", "SKIPPED_PERMISSION")]
        gaps.sort(key=lambda g: (g["kind"] == "FORECAST_INPUT", g["question"]))
        return {**c, "warnings": b.warnings, "gaps": gaps, "as_of": b.as_of}

    @staticmethod
    def _element_label(el: str, lab: Labeler, model: DecisionWorldModel) -> str:
        kind, _, ident = el.partition(":")
        if el.startswith("param."):
            return humanize(el[6:]) + " (assumed)"
        if kind == "state":
            return lab.state.get(ident, humanize(ident))
        if kind == "mechanism":
            m = lab.idx.mechanisms.get(ident)
            return humanize(ident) + (" (assumed)" if m and str(m.epistemic_tier) in ("C_ILLUSTRATIVE", "X_LIMITED_OR_ABSTAIN") else "")
        if kind == "structural_unknown":
            return "missing: " + humanize(ident).lower()
        return humanize(ident or el)

    # ------------------------------------------------------------------
    def world_model(self, decision_id: str, version: int | None = None) -> dict[str, Any]:
        row = self.store.get_model(f"wm_{decision_id}", version)
        if row is None:
            raise KeyError("no model for this decision")
        model: DecisionWorldModel = row["model"]
        rep = row["report"]
        lab = Labeler(model)
        bundle = self.store.latest_evidence_bundle(decision_id)
        claims = {c.claim_id: c for c in (bundle.claims if bundle else [])}
        inputs: dict[str, dict[str, Any]] = {}
        input_refs: dict[str, str] = {}
        for sv in model.state:
            if not sv.evidence_refs and str(sv.origin) not in ("USER_ASSERTED", "REAL_EXTERNAL_OBSERVATION", "OFFICIAL_RULE", "HISTORICAL_DATASET"):
                continue
            ent = sv.ref.split(".")[0]
            node = inputs.setdefault(f"entity:{ent}", {"id": f"entity:{ent}", "label": lab.entity.get(ent, humanize(ent)), "kind": "entity",
                                                       "facts": [], "counts": {"verified": 0, "stale": 0, "unknown": 0, "user": 0, "assumed": 0},
                                                       "tier": "A_GROUNDED"})
            status = "assumed"
            for ref in sv.evidence_refs:
                cl = claims.get(ref)
                if cl is not None:
                    status = "stale" if cl.status == "STALE" else "verified" if cl.status == "VERIFIED" else (
                        "user" if str(cl.origin_kind) == "USER_ASSERTED" else "assumed")
            if sv.initial_value in (None, "UNKNOWN"):
                status = "unknown"
            node["counts"][status] += 1
            node["facts"].append({"ref": sv.ref, "label": lab.state[sv.ref], "value": lab.value(sv.initial_value) if sv.initial_value is not None else "UNKNOWN",
                                  "unit": sv.unit, "status": status, "tier": str(sv.epistemic_tier), "hidden": sv.visibility.scope == "hidden"})
            node["tier"] = max(node["tier"], str(sv.epistemic_tier), key=lambda x: TIER_ORDER.index(FidelityTier(x)))
            input_refs[sv.ref] = node["id"]
        if model.parameters:
            inputs["params"] = {"id": "params", "label": "Assumed parameters", "kind": "params", "tier": max(
                (str(p.epistemic_tier) for p in model.parameters), key=lambda x: TIER_ORDER.index(FidelityTier(x))),
                "facts": [{"ref": f"param.{p.id}", "label": humanize(p.id), "value": lab.value(p.value) if p.value is not None else "distribution",
                           "unit": p.unit, "status": "assumed" if p.source_kind != "dataset" else "historical", "tier": str(p.epistemic_tier),
                           "note": p.description} for p in model.parameters],
                "counts": {"assumed": len(model.parameters)}}
        processes = []
        edges = []
        for m in model.mechanisms:
            if m.rule.kind == "wakeup":
                continue
            node_id = f"mechanism:{m.id}"
            processes.append({"id": node_id, "label": humanize(m.id), "description": m.description, "kind": "mechanism",
                              "operator": m.operator, "tier": str(m.epistemic_tier), "assumptions": m.assumptions,
                              "evidence": m.evidence_refs, "reuses": (f"{m.registry_ref['entry_id']}@{m.registry_ref['version']}" if m.registry_ref else None)})
            srcs = set()
            for ref in m.inputs:
                if ref.startswith("param."):
                    srcs.add("params")
                elif ref in input_refs:
                    srcs.add(input_refs[ref])
            edges += [{"from": s, "to": node_id} for s in sorted(srcs) if s in inputs]
        for a in model.actions:
            if not a.effects and not a.governance:
                continue
            node_id = f"action:{a.id}"
            processes.append({"id": node_id, "label": (a.description.rstrip(".") if a.description else humanize(a.id)), "kind": "choice",
                              "tier": str(a.epistemic_tier), "actors": [lab.agent.get(x, x) for x in a.actors if x != "*"] or ["anyone"],
                              "assumptions": [], "evidence": a.evidence_refs})
        proc_ids = {p["id"] for p in processes}
        outcomes = []
        for mt in model.metrics:
            mid = f"metric:{mt.id}"
            deps = rep["metric_dependencies"].get(mt.id, [])
            weakest = [w["element"] for w in rep.get("weakest_influential", []) if w["metric"] == mt.id]
            outcomes.append({"id": mid, "label": mt.label or humanize(mt.id), "tier": rep["metric_fidelity"].get(mt.id),
                             "computable": mt.computable and mt.id not in rep["non_computable_metrics"],
                             "reason": rep["non_computable_metrics"].get(mt.id, mt.reason), "unit": mt.unit,
                             "weakest": [self._element_label(w, lab, model) for w in weakest][:3]})
            for d in deps:
                ref = d.rsplit("=", 1)[0]
                if ref in proc_ids:
                    edges.append({"from": ref, "to": mid})
                elif ref.startswith("state:") and ref[6:] in input_refs:
                    edges.append({"from": input_refs[ref[6:]], "to": mid})
        missing = [{"id": f"unknown:{u.id}", "label": humanize(u.id), "description": u.description, "impact": u.impact,
                    "affects": u.affects_metrics, "status": u.status} for u in model.structural_unknowns]
        for u in missing:
            edges += [{"from": u["id"], "to": f"metric:{m}", "missing": True} for m in u["affects"]]
        seen = set()
        uniq = []
        for e in edges:
            k = (e["from"], e["to"])
            if k not in seen:
                seen.add(k)
                uniq.append(e)
        versions = []
        from ..model_factory.factory import model_diff

        rows = self.store.list_model_versions(model_id=model.model_id)
        for i, r in enumerate(rows):
            m = r["model"]
            versions.append({"version": m.model_version, "reason": m.revision_reason, "created_at": r["created_at"],
                             "diff": model_diff(rows[i - 1]["model"], m) if i > 0 else None})
        return {"model": {"version": model.model_version, "hash": model.hash, "purpose": model.purpose,
                          "promotion_state": str(model.promotion_state), "high_stakes": model.scope.high_stakes},
                "inputs": list(inputs.values()), "processes": processes, "missing": missing, "outcomes": outcomes,
                "edges": uniq, "omissions": [o.model_dump() for o in model.omissions], "versions": versions,
                "scorecard": rep["scorecard"], "downgrades": rep["downgrades"]}

"""Agent policies (§7.3–§7.6).

``ProfileUtilityPolicy`` is a deterministic, individualised policy used offline, for screening runs
and in tests. It reasons only from the Agent's own selected profile features, its observation and
the model's declared action appraisals. Behaviour under deadlines emerges from each person's own
``behavior.deadline_sensitivity`` feature when — and only when — a decision-world deadline is
disclosed; there is no global ``compromise_tendency += 0.2``.

``LLMAgentPolicy`` (see ``udse.llm.agent_policy``) asks a language model for one structured
ActionIntent. ``RecordedAgentRuntime`` replays committed decisions without any model calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..common import parse_dt
from ..model_ir.enums import FeatureStatus
from ..model_ir.expr import UNKNOWN, Q, compare, value_to_display
from ..model_ir.schemas import ActionIntent, ProfileFeature, WorldEvent
from ..model_ir.units import UnitError, parse_unit
from .contracts import WAIT, AgentDecision, DecisionContext, EligibleAction

DEFAULT_TOLERANCE = 0.5
DEFAULT_INITIATIVE = 0.5
OPS = {"==": "==", "!=": "!=", "<=": "<=", ">=": ">=", "<": "<", ">": ">"}


@dataclass
class Score:
    utility: float = 0.0
    contributions: list[dict[str, Any]] = field(default_factory=list)
    hard_fail: list[str] = field(default_factory=list)
    soft_fail: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    unknown_prefs: list[str] = field(default_factory=list)

    @property
    def passes(self) -> bool:
        return not self.hard_fail and not self.unresolved


def _numeric(v: Any, unit: str | None) -> float | None:
    if v is UNKNOWN or v is None:
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, Q):
        if unit:
            try:
                return v.to(parse_unit(unit)).value
            except UnitError:
                return None
        return v.value
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _show(v: Any) -> str:
    if isinstance(v, dict) and "value" in v:
        unit = "" if v.get("unit") in ("1", None) else f" {v['unit']}"
        return f"{v['value']:,.2f}".rstrip("0").rstrip(".") + unit
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, str) and len(v) > 16 and v[4] == "-" and "T" in v:
        return v[11:16]
    return str(v)


def _feature_number(f: ProfileFeature | None, default: float) -> tuple[float, str | None]:
    if f is None or f.status == FeatureStatus.UNKNOWN or f.value is None:
        return default, (f"{f.key if f else 'feature'} unknown → assumed {default:g}")
    v = f.value
    if isinstance(v, dict) and "low" in v and "high" in v:
        return (float(v["low"]) + float(v["high"])) / 2.0, f"{f.key} uncertain in [{v['low']}, {v['high']}] → midpoint"
    if isinstance(v, dict) and "value" in v:
        return float(v["value"]), None
    return float(v), None


class ProfileUtilityPolicy:
    kind = "profile_utility"

    def estimate_tokens(self, agent_id: str) -> int:
        return 0

    # ---------------- scoring ----------------
    def score(self, ctx: DecisionContext, action_type: str, args: dict[str, Any]) -> Score:
        ap = ctx.appraise(action_type, args)
        s = Score()
        for f in ctx.features:
            ns, _, dim = f.key.partition(".")
            if dim not in ap:
                continue
            val = ap[dim]
            if ns == "preference":
                if f.status == FeatureStatus.UNKNOWN or f.value is None:
                    s.unknown_prefs.append(f.key)
                    continue
                spec = f.value if isinstance(f.value, dict) else {"weight": f.value}
                if "low" in spec and "high" in spec:
                    w = (float(spec["low"]) + float(spec["high"])) / 2.0
                    s.unknown_prefs.append(f"{f.key} (range midpoint)")
                else:
                    w = float(spec.get("weight", 0.0))
                x = _numeric(val, spec.get("unit"))
                if x is None:
                    s.contributions.append({"dim": dim, "value": "UNKNOWN", "weight": w, "contribution": 0.0})
                    continue
                c = w * x
                s.utility += c
                s.contributions.append({"dim": dim, "value": value_to_display(val), "weight": w, "contribution": round(c, 4)})
            elif ns == "constraint":
                spec = f.value if isinstance(f.value, dict) else {"op": "==", "value": f.value}
                target = spec.get("value")
                if isinstance(target, (int, float)) and not isinstance(target, bool):
                    target = Q(float(target), parse_unit(spec.get("unit")))
                elif isinstance(target, str) and spec.get("type") == "timestamp":
                    target = parse_dt(target)
                try:
                    ok = compare(OPS.get(spec.get("op", "=="), "=="), val, target)
                except Exception:  # noqa: BLE001 - incomparable means we cannot verify
                    ok = UNKNOWN
                hard = f.status == FeatureStatus.HARD_CONSTRAINT
                if ok is UNKNOWN:
                    if hard:
                        s.unresolved.append(f.key)
                elif ok is False:
                    if hard:
                        s.hard_fail.append(f.key)
                    else:
                        s.soft_fail.append(f.key)
                        s.utility -= float(spec.get("penalty", 2.0))
        return s

    def _tolerance(self, ctx: DecisionContext) -> tuple[float, list[str]]:
        feats = {f.key: f for f in ctx.features}
        notes = []
        tol, note = _feature_number(feats.get("behavior.compromise_tolerance"), DEFAULT_TOLERANCE)
        if note:
            notes.append(note)
        dl = ctx.observation.get("deadline")
        if ctx.deadline_disclosed and dl:
            sens_f = feats.get("behavior.deadline_sensitivity")
            if sens_f is None or sens_f.status == FeatureStatus.UNKNOWN:
                notes.append("deadline disclosed; own deadline response unknown → no adjustment")
            else:
                sens, _ = _feature_number(sens_f, 0.0)
                urgency = self._urgency(ctx, dl)
                if urgency > 0:
                    tol = min(1.0, tol + sens * urgency)
                    notes.append(f"disclosed deadline urgency {urgency:.2f} × own sensitivity {sens:g}")
        return tol, notes

    @staticmethod
    def _urgency(ctx: DecisionContext, dl: dict[str, Any]) -> float:
        start = ctx.observation.get("horizon", {}).get("start")
        if not start:
            return 0.0
        start_dt = parse_dt(start)
        best = 0.0
        for v in dl.get("deadlines", {}).values():
            if not isinstance(v, str) or v == "UNKNOWN":
                continue
            d = parse_dt(v)
            total = (d - start_dt).total_seconds()
            if total <= 0:
                continue
            remaining = (d - ctx.sim_now).total_seconds()
            best = max(best, min(1.0, max(0.0, 1.0 - remaining / total)))
        return best

    # ---------------- decision ----------------
    def decide(self, ctx: DecisionContext) -> AgentDecision:
        tol, tol_notes = self._tolerance(ctx)
        feats = {f.key: f for f in ctx.features}
        initiative, init_note = _feature_number(feats.get("behavior.initiative"), DEFAULT_INITIATIVE)
        threshold, _ = _feature_number(feats.get("behavior.action_threshold"), 0.0)
        requested = {(r["action"], tuple(sorted(r.get("args", {}).items()))) for r in ctx.agent_state.get("requests", [])}
        el = {e.action_type: e for e in ctx.eligible}
        trace: dict[str, Any] = {"tolerance": tol, "notes": tol_notes + ([init_note] if init_note else [])}

        def info_for(option: str) -> tuple[EligibleAction, dict[str, Any]] | None:
            for e in ctx.eligible:
                if "information_request" not in e.tags or not e.args:
                    continue
                a = e.args[0]
                if option in a["options"]:
                    args = {a["name"]: option}
                    if (e.action_type, tuple(sorted(args.items()))) not in requested:
                        return e, args
            return None

        # Governance: respond to or create proposals
        for gov_e in [e for e in ctx.eligible if e.governance]:
            g = gov_e.governance
            mech = g["mechanism"]
            propose_id, opt_arg = g.get("propose_action"), g.get("propose_option_arg")
            if not propose_id:
                continue
            options = g.get("options", [])
            scored = {o: self.score(ctx, propose_id, {opt_arg: o}) for o in options}
            # Observed objections that blocked a proposal mean the group cannot agree on that option
            # under its rule; learn from the visible ledger instead of re-proposing it (memory, §7.1).
            ledger = next((x["ledger"] for x in ctx.governance_status if x["mechanism"] == mech), {"proposals": []})
            blocked = {p["option"] for p in ledger.get("proposals", []) if p.get("status") == "blocked"}
            if g.get("requires_gain"):
                # Status-quo comparison: only options this person values above "no change" are candidates.
                scored = {o: sc for o, sc in scored.items() if sc.contributions and sc.utility > 1e-9} | (
                    {g["open_proposal"]["option"]: self.score(ctx, propose_id, {opt_arg: g["open_proposal"]["option"]})}
                    if g.get("open_proposal") else {})
            passing_all = {o: s for o, s in scored.items() if s.passes}
            passing = {o: s for o, s in passing_all.items() if o not in blocked} or passing_all
            persistence, _ = _feature_number(feats.get("behavior.persistence"), 2.0)
            mine: dict[str, int] = {}
            for p in ledger.get("proposals", []):
                if p.get("proposer") == ctx.agent_id:
                    mine[p["option"]] = mine.get(p["option"], 0) + 1
            exhausted = {o for o, n in mine.items() if n >= persistence}  # this person has insisted enough
            if blocked:
                trace.setdefault("notes", []).append(f"options blocked by objections so far: {sorted(blocked)}")
            best_opt = max(passing, key=lambda o: (passing[o].utility, -options.index(o))) if passing else None
            spread = (max(s.utility for s in passing.values()) - min(s.utility for s in passing.values())) if len(passing) > 1 else 0.0
            open_p = g.get("open_proposal")
            if open_p:
                my_pos = open_p["positions"].get(ctx.agent_id)
                sp = scored.get(open_p["option"]) or self.score(ctx, propose_id, {opt_arg: open_p["option"]})
                if sp.hard_fail:
                    want, why = "object", f"violates my hard constraint(s) {sp.hard_fail}"
                elif g.get("requires_gain") and sp.utility < -1e-9:
                    want, why = "object", f"worse for me than keeping the current course (utility {sp.utility:.2f} < 0)"
                elif sp.unresolved:
                    info = info_for(open_p["option"])
                    if info:
                        return self._intent(ctx, info[0].action_type, info[1],
                                            f"Cannot verify {sp.unresolved} for {open_p['option']}; requesting information.", sp, trace)
                    want, why = "object", f"cannot verify hard constraint(s) {sp.unresolved}"
                elif best_opt is None or sp.utility >= passing[best_opt].utility - tol * max(spread, 1e-9) - 1e-9:
                    want, why = "endorse", f"acceptable: utility {sp.utility:.2f} vs my best {passing[best_opt].utility if best_opt else sp.utility:.2f} (tolerance {tol:.2f})"
                else:
                    want, why = "counter", f"prefer {best_opt} ({passing[best_opt].utility:.2f}) over {open_p['option']} ({sp.utility:.2f})"
                if want == "counter":
                    prop = el.get(propose_id)
                    if prop and best_opt in prop.args[0]["options"] and best_opt != open_p["option"] and best_opt not in exhausted:
                        return self._intent(ctx, propose_id, {opt_arg: best_opt}, "Counter-proposal: " + why, passing[best_opt], trace)
                    want = "object"
                if my_pos != want:
                    act_id = self._gov_action(ctx, mech, want)
                    if act_id:
                        return self._intent(ctx, act_id, {}, f"{want.capitalize()} proposal {open_p['id']} ({open_p['option']}): {why}", sp, trace)
                continue
            prop = el.get(propose_id)
            if prop and gov_e.action_type == propose_id:
                if best_opt is not None and best_opt in exhausted:
                    alt = [o for o in sorted(passing, key=lambda o: -passing[o].utility) if o not in exhausted]
                    best_opt = alt[0] if alt else None
                if best_opt is not None and (initiative >= 0.3 or ctx.agent_state.get("wakeups", 0) >= 2):
                    if best_opt in prop.args[0]["options"]:
                        return self._intent(ctx, propose_id, {opt_arg: best_opt},
                                            f"Propose {best_opt}: best option satisfying my constraints (utility {passing[best_opt].utility:.2f}).",
                                            passing[best_opt], trace)
                if best_opt is None:
                    unresolved = sorted((o for o, s in scored.items() if s.unresolved and not s.hard_fail),
                                        key=lambda o: -scored[o].utility)
                    for o in unresolved:
                        info = info_for(o)
                        if info:
                            return self._intent(ctx, info[0].action_type, info[1],
                                                f"No option verifiably satisfies my constraints; asking about {o}.", scored[o], trace)

        # Other domain actions by individual utility versus waiting
        best: tuple[float, str, dict[str, Any], Score] | None = None
        for e in ctx.eligible:
            if e.action_type == WAIT or e.governance:
                continue
            combos = [{}]
            if e.args:
                a = e.args[0]
                combos = [{a["name"]: o} for o in a["options"]]
                for extra in e.args[1:]:
                    combos = [{**c, extra["name"]: extra["options"][0]} for c in combos if extra["options"]]
            for args in combos:
                if "information_request" in e.tags:
                    continue  # information requests are driven by unresolved constraints above
                s = self.score(ctx, e.action_type, args)
                if not s.passes:
                    continue
                if not s.contributions:
                    continue  # nothing this person cares about is affected → no reason to act
                if best is None or s.utility > best[0]:
                    best = (s.utility, e.action_type, args, s)
        if best is not None and best[0] > threshold + 1e-9:
            u, act, args, s = best
            return self._intent(ctx, act, args, f"{act} has positive value for me (utility {u:.2f} > threshold {threshold:g}).", s, trace)
        return self._intent(ctx, WAIT, {}, "No eligible action improves on waiting for the next relevant event.", None, trace)

    @staticmethod
    def _gov_action(ctx: DecisionContext, mech: str, act: str) -> str | None:
        for e in ctx.eligible:
            if e.governance and e.governance["mechanism"] == mech and e.governance["act"] == act:
                return e.action_type
        return None

    def _intent(self, ctx: DecisionContext, action_type: str, args: dict[str, Any], why: str, s: Score | None,
                trace: dict[str, Any]) -> AgentDecision:
        contrib = ""
        notes = list(trace.get("notes", []))
        world_unknown = []
        if s is not None:
            top = sorted(s.contributions, key=lambda c: -abs(c["contribution"]))[:4]
            if top:
                contrib = "; ".join(f"{c['dim'].replace('_', ' ')} {_show(c['value'])} (weight {c['weight']:g})" for c in top)
            notes += [f"profile: {k} unknown" for k in s.unknown_prefs]
            world_unknown = [f"{k} not verifiable from observations" for k in s.unresolved]
        rationale = why + (f" Factors: {contrib}." if contrib else "")
        uncertainty = "; ".join(
            ([f"profile uncertainty: {', '.join(notes)}"] if notes else [])
            + ([f"world uncertainty: {', '.join(world_unknown)}"] if world_unknown else [])
        ) or None
        intent = ActionIntent(
            run_id=ctx.run_id, agent_id=ctx.agent_id, observed_at=ctx.sim_now.isoformat(), action_type=action_type,
            parameters=args, rationale_summary=rationale, uncertainty_note=uncertainty,
            expected_effects=[f"hypothesis: {action_type} changes {', '.join(sorted({c['dim'] for c in (s.contributions if s else [])}))}"] if s and s.contributions else [],
        )
        beliefs = {"note.last_rationale": rationale}
        if s is not None and s.unresolved:
            beliefs["belief.unverified"] = s.unresolved
        return AgentDecision(intent=intent, policy_kind=self.kind, usage={}, belief_updates=beliefs,
                             trace={**trace, "utility": s.utility if s else None})


class RecordedAgentRuntime:
    """Replays committed Agent decisions in order — no LLM calls (§8.4 transition replay)."""

    def __init__(self, events: list[WorldEvent]):
        self.queues: dict[str, list[dict[str, Any]]] = {}
        for e in events:
            if e.kind == "AGENT_DECISION" and e.actor_id:
                self.queues.setdefault(e.actor_id, []).append(e.payload["decision"])
        self.calls = 0

    def estimate_tokens(self, agent_id: str) -> int:
        return 0  # replay never calls a model; the run stops at the recorded event count

    def decide(self, ctx: DecisionContext) -> AgentDecision:
        q = self.queues.get(ctx.agent_id)
        if not q:
            raise RuntimeError(f"no recorded decision left for {ctx.agent_id}")
        rec = q.pop(0)
        self.calls += 1
        return AgentDecision(
            intent=ActionIntent.model_validate(rec["intent"]), policy_kind=rec["policy_kind"], usage=rec.get("usage", {}),
            model_record=rec.get("model_record"), belief_updates=rec.get("belief_updates", {}), trace=rec.get("trace", {}),
        )


class AgentRuntimeRouter:
    """Selects a policy per Agent from the run's RunAgentConfig.policy_kind."""

    def __init__(self, policies: dict[str, Any], kinds: dict[str, str] | None = None, default: str = "profile_utility"):
        self.policies = policies
        self.kinds = kinds or {}
        self.default = default

    def _policy(self, agent_id: str):
        kind = self.kinds.get(agent_id, self.default)
        if kind not in self.policies:
            kind = "profile_utility"
        return self.policies[kind]

    def estimate_tokens(self, agent_id: str) -> int:
        return self._policy(agent_id).estimate_tokens(agent_id)

    def decide(self, ctx: DecisionContext) -> AgentDecision:
        return self._policy(ctx.agent_id).decide(ctx)

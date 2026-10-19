"""Typed transition operators (§4.2) and effect application.

Every world mutation in a run is produced here from declarative IR — effects (set/inc/dec/emit/
terminate/wake) and rule kinds bound to the operator allowlist. Agents never reach this code
directly: their intents are validated first and then executed as registered action effects.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..common import Duration, ModelExecutionError, json_copy
from ..model_ir.enums import FidelityTier
from ..model_ir.expr import (
    UNKNOWN,
    EvalError,
    Q,
    add_sub,
    compare,
    evaluate,
    to_json,
    value_to_display,
)
from ..model_ir.index import StateEnv, decode_value, encode_value
from ..model_ir.schemas import (
    ActionSpec,
    ConstraintSpec,
    Distribution,
    GovernanceAct,
    MechanismSpec,
    Visibility,
)
from ..model_ir.units import parse_unit
from .queue import ScheduledEvent
from .state import PatchBuilder

if TYPE_CHECKING:  # pragma: no cover
    from .kernel import Simulator

# operator name -> allowed rule kinds (the IR operator allowlist, §19 "8–10 safe typed operators")
OPERATOR_RULES: dict[str, set[str]] = {
    "resource_flow": {"subtract_flow", "add_flow", "transfer"},
    "capacity_queue": {"queue_service"},
    "schedule_dependency": {"milestone_progress", "scheduled_effects"},
    "threshold_gate": {"threshold_gate"},
    "state_machine": {"state_transition"},
    "stochastic_event": {"stochastic_draw"},
    "feedback_control": {"wakeup"},
    "information_process": {"on_event"},
    "social_interaction": {"governance"},
    "exogenous_process": {"scheduled_effects", "on_event"},
}
OPERATOR_VERSIONS = {name: "1.0.0" for name in OPERATOR_RULES}

CMP = {"lt": "<", "lte": "<=", "gt": ">", "gte": ">=", "eq": "==", "neq": "!="}


def interval_count(d: Duration, unit: str) -> float:
    """How many ``unit`` intervals a schedule interval spans, exact for calendar months."""
    if d.seconds == 0 and d.months:
        if unit == "month":
            return float(d.months)
        if unit == "year":
            return d.months / 12.0
    if d.months == 0:
        return d.seconds / parse_unit(unit).scale
    return d.approx_seconds() / parse_unit(unit).scale


def default_ledger() -> dict[str, Any]:
    return {"proposals": [], "rounds": 0, "satisfied": None, "real_world_commitment": False}


class Transition:
    """Accumulates one prospective committed event: patch, spawned events, draws, lineage."""

    def __init__(self, sim: "Simulator", builder: PatchBuilder, now: datetime, event_id: str):
        self.sim = sim
        self.builder = builder
        self.now = now
        self.event_id = event_id
        self.spawned: list[ScheduledEvent] = []
        self.draws: list[dict[str, Any]] = []
        self.terminate: dict[str, Any] | None = None
        self.assumptions: set[str] = set()
        self.tiers: list[FidelityTier] = []
        self.mechanisms: list[str] = []
        self.wakes: list[tuple[str, datetime, str]] = []
        self.notes: list[str] = []
        self._draw_counter: dict[str, int] = {}

    # ----- environment -----
    def env(self, extra: dict[str, Any] | None = None) -> StateEnv:
        return StateEnv(self.sim.index, self.builder.world_view, self.sim.params, self.now, extra)

    def eval(self, expr: str, extra: dict[str, Any] | None = None) -> Any:
        try:
            return evaluate(expr, self.env(extra))
        except EvalError as e:
            raise ModelExecutionError(f"evaluation failed for {expr!r}: {e}") from e

    def is_true(self, expr: str | None, extra: dict[str, Any] | None = None) -> bool:
        if expr is None:
            return True
        v = self.eval(expr, extra)
        if v is UNKNOWN:
            return False
        if not isinstance(v, bool):
            raise ModelExecutionError(f"condition {expr!r} is not boolean")
        return v

    # ----- writes -----
    def write(self, ref: str, value: Any) -> None:
        sv = self.sim.index.state.get(ref)
        if sv is None:
            raise ModelExecutionError(f"write to undeclared state variable {ref!r}")
        try:
            self.builder.set_world(ref, to_json(value, sv.type, sv.unit))
        except EvalError as e:
            raise ModelExecutionError(f"type/unit error writing {ref}: {e}") from e

    def spawn(self, ev: ScheduledEvent) -> None:
        if ev.sim_at < self.now:
            raise ModelExecutionError("retroactive scheduling is forbidden (time monotonicity)")
        ev.parent_event_ids = [self.event_id]
        self.spawned.append(ev)

    def wake(self, agent_id: str, at: datetime, reason: str) -> None:
        self.wakes.append((agent_id, at, reason))

    def note_mechanism(self, mech: MechanismSpec) -> None:
        self.mechanisms.append(mech.id)
        self.tiers.append(mech.epistemic_tier)
        self.assumptions.update(mech.assumptions)

    # ----- effects -----
    def apply_effects(self, effects: list, extra: dict[str, Any] | None = None) -> None:
        for eff in effects:
            op = eff.op
            if op in ("set", "inc", "dec") and not self.is_true(eff.when, extra):
                continue
            if op == "set":
                self.write(eff.ref, self.eval(eff.value, extra))
            elif op in ("inc", "dec"):
                cur = self.env(extra).lookup(eff.ref)
                by = self.eval(eff.by, extra)
                try:
                    self.write(eff.ref, add_sub("+" if op == "inc" else "-", cur, by))
                except EvalError as e:
                    raise ModelExecutionError(f"{op} {eff.ref}: {e}") from e
            elif op == "emit":
                if not self.is_true(eff.when, extra):
                    continue
                at = self.resolve_delay(eff.delay, extra)
                payload = {k: encode_value(self.eval(v, extra)) for k, v in eff.payload.items()}
                self.spawn(
                    ScheduledEvent(
                        sim_at=at, kind="DOMAIN_EVENT", event_type=eff.event_type,
                        payload=payload, visibility=eff.visibility.model_dump(mode="json"),
                        reason=f"emitted by {self.event_id}",
                    )
                )
            elif op == "terminate":
                if self.is_true(eff.when, extra) and self.terminate is None:
                    self.terminate = {"outcome": eff.outcome, "reason": eff.reason}
            elif op == "wake":
                at = Duration.parse(eff.delay).add_to(self.now)
                targets = list(self.sim.index.agents) if "*" in eff.agents else eff.agents
                for a in targets:
                    self.wake(a, at, eff.reason)
            else:  # pragma: no cover - schema prevents this
                raise ModelExecutionError(f"unknown effect op {op}")

    def resolve_delay(self, delay: str, extra: dict[str, Any] | None = None) -> datetime:
        d = delay.strip()
        if d.startswith("P") or d.startswith("-P"):
            dur = Duration.parse(d)
            if dur.is_negative:
                raise ModelExecutionError("negative delay")
            return dur.add_to(self.now)
        v = self.eval(d, extra)
        if v is UNKNOWN:
            raise ModelExecutionError(f"delay {d!r} evaluated to UNKNOWN; cannot schedule an event at an unknown time")
        if not isinstance(v, Q) or not v.unit.same_dimension(parse_unit("second")):
            raise ModelExecutionError(f"delay {d!r} is not a time quantity")
        secs = v.to(parse_unit("second")).value
        if secs < 0:
            raise ModelExecutionError("negative delay")
        from datetime import timedelta

        return self.now + timedelta(seconds=secs)

    # ----- mechanisms -----
    def apply_mechanism(self, mech: MechanismSpec, n_intervals: float = 1.0, trigger_type: str | None = None,
                        extra: dict[str, Any] | None = None) -> None:
        rule = mech.rule
        k = rule.kind
        self.note_mechanism(mech)
        if k in ("subtract_flow", "add_flow"):
            sv = self.sim.index.state[rule.target]
            rate = self.eval(rule.rate_ref, extra)
            cur = self.env(extra).lookup(rule.target)
            if rate is UNKNOWN or cur is UNKNOWN:
                self.write(rule.target, UNKNOWN)
                self.notes.append(f"{mech.id}: rate or stock UNKNOWN → {rule.target} UNKNOWN")
                return
            per = parse_unit(sv.unit) / parse_unit(rule.interval_unit)
            try:
                amount = rate.to(per).value * n_intervals
            except Exception as e:  # noqa: BLE001
                raise ModelExecutionError(f"{mech.id}: {e}") from e
            base = cur.to(parse_unit(sv.unit)).value
            self.builder.set_world(rule.target, float(base - amount if k == "subtract_flow" else base + amount))
        elif k == "transfer":
            amount = self.eval(rule.amount, extra)
            for ref, op in ((rule.source, "-"), (rule.sink, "+")):
                cur = self.env(extra).lookup(ref)
                try:
                    self.write(ref, add_sub(op, cur, amount))
                except EvalError as e:
                    raise ModelExecutionError(f"{mech.id}: {e}") from e
        elif k == "queue_service":
            queue = self.env(extra).lookup(rule.queue_ref)
            cap = self.eval(rule.capacity, extra)
            if queue is UNKNOWN or cap is UNKNOWN:
                self.notes.append(f"{mech.id}: queue or capacity UNKNOWN; no service")
                return
            qsv = self.sim.index.state[rule.queue_ref]
            per = parse_unit(qsv.unit) / parse_unit(rule.interval_unit)
            capacity = cap.to(per).value * n_intervals
            served = min(queue.value, capacity)
            self.builder.set_world(rule.queue_ref, float(queue.value - served))
            cur = self.env(extra).lookup(rule.served_ref)
            self.write(rule.served_ref, add_sub("+", cur, Q(served, queue.unit)))
        elif k == "milestone_progress":
            done = self.env(extra).lookup(rule.done_ref)
            if done is True:
                return
            prog = self.env(extra).lookup(rule.progress_ref)
            rate = self.eval(rule.rate, extra)
            required = self.eval(rule.required, extra)
            if prog is UNKNOWN or rate is UNKNOWN:
                self.write(rule.progress_ref, UNKNOWN)
                return
            psv = self.sim.index.state[rule.progress_ref]
            per = parse_unit(psv.unit) / parse_unit(rule.interval_unit)
            new = prog.to(parse_unit(psv.unit)).value + rate.to(per).value * n_intervals
            self.builder.set_world(rule.progress_ref, float(new))
            reached = compare(">=", Q(new, parse_unit(psv.unit)), required)
            if reached is True:
                self.write(rule.done_ref, True)
                self.apply_effects(rule.on_complete, extra)
        elif k == "threshold_gate":
            self.apply_effects(rule.on_trigger, extra)
        elif k == "state_transition":
            cur = self.env(extra).lookup(rule.state_ref)
            for edge in rule.transitions:
                if edge.from_state not in ("*", cur):
                    continue
                if edge.on_event is not None and edge.on_event != trigger_type:
                    continue
                if not self.is_true(edge.when, extra):
                    continue
                self.write(rule.state_ref, edge.to)
                self.apply_effects(edge.effects, extra)
                break
        elif k == "stochastic_draw":
            if not self.is_true(rule.when, extra):
                return
            dist = rule.distribution
            if rule.distribution_param:
                dist = self.sim.params.get(rule.distribution_param)
                if not isinstance(dist, Distribution):
                    raise ModelExecutionError(f"{mech.id}: parameter {rule.distribution_param} is not a distribution")
            occ = self._draw_counter.get(mech.id, 0)
            self._draw_counter[mech.id] = occ + 1
            key = f"{mech.id}|{self.now.isoformat()}|{occ}"
            try:
                value, u = self.sim.rng.sample(rule.stream, key, dist)
            except (ValueError, TypeError) as e:
                raise ModelExecutionError(f"{mech.id}: {e}") from e
            self.draws.append({
                "stream": rule.stream, "key": key, "u": u, "value": value, "mechanism": mech.id,
                "distribution": dist.model_dump(mode="json", exclude_none=True),
            })
            ex = dict(extra or {})
            if dist.kind == "bernoulli":
                ex["draw.value"] = bool(value)
                self.apply_effects(rule.on_true if value else rule.on_false, ex)
            elif dist.kind == "choice":
                ex["draw.value"] = value
                if rule.target:
                    self.write(rule.target, value)
                self.apply_effects(rule.outcomes.get(str(value), []), ex)
            else:
                q = Q(float(value), parse_unit(dist.unit))
                ex["draw.value"] = q
                if rule.target:
                    self.write(rule.target, q)
                self.apply_effects(rule.outcomes.get("*", []), ex)
        elif k in ("on_event", "scheduled_effects"):
            if self.is_true(rule.when, extra):
                self.apply_effects(rule.effects, extra)
        elif k in ("wakeup", "governance"):
            pass  # wakeups are scheduled post-commit; governance runs through validated actions
        else:  # pragma: no cover
            raise ModelExecutionError(f"unsupported rule kind {k}")

    # ----- governance (social_interaction operator) -----
    def governance_act(self, act: GovernanceAct, args: dict[str, Any], actor: str, dry_run: bool = False) -> str | None:
        """Apply (or dry-run) a governance act. Returns a rejection code or None.

        Layers kept distinct (§7.6): agreement_proposed → endorsement_recorded → governance_satisfied.
        real_world_commitment is never set by the simulation."""
        mech = self.sim.index.mechanisms[act.mechanism]
        rule = mech.rule
        ledger = json_copy(self.builder.get_world(rule.ledger_ref) or default_ledger())
        if ledger.get("satisfied"):
            return "GOVERNANCE_ALREADY_SATISFIED"
        if actor not in rule.participants:
            return "NOT_A_PARTICIPANT"
        open_p = next((p for p in reversed(ledger["proposals"]) if p["status"] == "open"), None)
        if act.act == "propose":
            option = args.get(act.option_arg) if act.option_arg else None
            if option not in rule.option_values:
                return "INVALID_OPTION"
            if rule.max_rounds is not None and ledger["rounds"] >= rule.max_rounds:
                return "NEGOTIATION_ROUNDS_EXHAUSTED"
            if dry_run:
                return None
            if open_p:
                open_p["status"] = "superseded"
            ledger["rounds"] += 1
            open_p = {
                "id": f"p{len(ledger['proposals']) + 1}", "option": option, "proposer": actor,
                "at": self.now.isoformat(), "status": "open", "positions": {actor: "endorse"},
            }
            ledger["proposals"].append(open_p)
            self.sim.budget.negotiation_rounds += 1
        elif act.act in ("endorse", "object"):
            if open_p is None:
                return "NO_OPEN_PROPOSAL"
            if act.option_arg and args.get(act.option_arg) not in (None, open_p["option"]):
                return "PROPOSAL_MISMATCH"
            if dry_run:
                return None
            open_p["positions"][actor] = "endorse" if act.act == "endorse" else "object"
        elif act.act == "withdraw":
            if open_p is None or open_p["proposer"] != actor:
                return "NOTHING_TO_WITHDRAW"
            if dry_run:
                return None
            open_p["status"] = "withdrawn"
        if open_p is not None and open_p["status"] == "open":
            endorsers = [a for a, pos in open_p["positions"].items() if pos == "endorse"]
            objectors = [a for a, pos in open_p["positions"].items() if pos == "object"]
            r = rule.decision_rule
            parts = rule.participants
            if r == "unanimous":
                ok = set(endorsers) >= set(parts)
                if objectors:
                    open_p["status"] = "blocked"
            elif r == "majority":
                ok = len(endorsers) * 2 > len(parts)
            elif r == "quorum":
                ok = len(endorsers) >= (rule.quorum or len(parts))
            elif r == "authorized_signatories":
                ok = set(endorsers) >= set(rule.signatories or parts)
            else:  # single_owner
                owner = (rule.signatories or parts)[0]
                ok = owner in endorsers
            if ok and open_p["status"] == "open":
                open_p["status"] = "satisfied"
                ledger["satisfied"] = {"proposal_id": open_p["id"], "option": open_p["option"], "at": self.now.isoformat()}
                self.builder.set_world(rule.ledger_ref, ledger)
                self.write(rule.outcome_ref, open_p["option"])
                ex = {"gov.option": open_p["option"], "gov.proposer": open_p["proposer"]}
                self.note_mechanism(mech)
                self.apply_effects(rule.on_satisfied, ex)
                self.spawn(ScheduledEvent(
                    sim_at=self.now, kind="DOMAIN_EVENT", event_type="governance_satisfied",
                    payload={"mechanism": mech.id, "option": open_p["option"], "proposal_id": open_p["id"]},
                    visibility=Visibility().model_dump(mode="json"), reason="governance rule satisfied",
                ))
                return None
        self.builder.set_world(rule.ledger_ref, ledger)
        return None


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------

def type_violations(sim: "Simulator", builder: PatchBuilder) -> list[str]:
    errs = []
    for ref in builder.touched_world():
        sv = sim.index.state[ref]
        v = builder.get_world(ref)
        if v == "UNKNOWN" or v is None:
            if not sv.allow_unknown:
                errs.append(f"{ref} became UNKNOWN but allow_unknown is false")
            continue
        if sv.type == "quantity":
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                errs.append(f"{ref} is not numeric")
            elif sv.min is not None and v < sv.min - 1e-9:
                errs.append(f"{ref}={v} below declared type minimum {sv.min}")
            elif sv.max is not None and v > sv.max + 1e-9:
                errs.append(f"{ref}={v} above declared type maximum {sv.max}")
        elif sv.type == "boolean" and not isinstance(v, bool):
            errs.append(f"{ref} is not boolean")
        elif sv.type == "enum" and v not in (sv.values or []):
            errs.append(f"{ref}={v!r} is not one of {sv.values}")
    return errs


def constraint_violations(sim: "Simulator", builder: PatchBuilder, now: datetime) -> list[tuple[ConstraintSpec, str]]:
    out = []
    env = StateEnv(sim.index, builder.world_view, sim.params, now)
    touched = builder.touched_world()
    for c in sim.index.model.constraints:
        if c.type in ("guarded_floor", "guarded_ceiling"):
            if c.path not in touched:
                continue
            v = env.lookup(c.path)
            if v is UNKNOWN:
                continue
            val = v.value if isinstance(v, Q) else v
            if c.type == "guarded_floor" and c.min is not None and val < c.min - 1e-9:
                out.append((c, f"{c.path}={val:g} < {c.min:g}"))
            if c.type == "guarded_ceiling" and c.max is not None and val > c.max + 1e-9:
                out.append((c, f"{c.path}={val:g} > {c.max:g}"))
        elif c.expr:
            try:
                ok = evaluate(c.expr, env)
            except EvalError as e:
                raise ModelExecutionError(f"constraint {c.id}: {e}") from e
            if ok is False:
                out.append((c, f"{c.expr} is false"))
    return out


def action_matches_actor(action: ActionSpec, agent_id: str, role: str) -> bool:
    return "*" in action.actors or agent_id in action.actors or f"role:{role}" in action.actors


def display(v: Any) -> Any:
    return value_to_display(decode_value(v) if isinstance(v, dict) else v)

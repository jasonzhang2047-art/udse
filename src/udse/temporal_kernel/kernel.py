"""Universal event-sourced temporal kernel (§8, ADR-001/002).

observe → decide → validate → transition → schedule → advance, identical for every decision
domain. The kernel knows nothing about any particular decision category: all domain content arrives as a
version-pinned DecisionWorldModel (Layer B) and every committed event is SIMULATION_OUTPUT (Layer C).
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable

from ..agent_runtime.contracts import WAIT, AgentDecision, AgentRuntime, DecisionContext, EligibleAction
from ..agent_runtime.observation import build_observation, visible_params, visible_refs
from ..agent_runtime.profiles import effective_features
from ..common import Duration, ModelExecutionError, iso, json_copy, new_id, resolve_time
from ..model_ir.enums import (
    DeadlineDisclosure,
    FidelityTier,
    OutcomeFlag,
    RunStatus,
    weakest_tier,
)
from ..model_ir.expr import UNKNOWN, EvalError, Q, compare, evaluate, referenced_names, value_to_display
from ..model_ir.index import ModelIndex, StateEnv, decode_value, typed_param_value
from ..model_ir.schemas import (
    ActionIntent,
    DecisionWorldModel,
    Distribution,
    ModelDiagnostic,
    ProfileFeature,
    RunAgentConfig,
    RunManifest,
    RunResult,
    ValidatedIntent,
    Visibility,
    WorldEvent,
)
from ..model_ir.units import parse_unit
from .budget import BudgetTracker
from .operators import (
    CMP,
    Transition,
    action_matches_actor,
    constraint_violations,
    default_ledger,
    interval_count,
    type_violations,
)
from .queue import EventQueue, ScheduledEvent
from .rng import CounterRNG
from .state import PatchBuilder, SimState
from .store import RunStore

MEMORY_LIMIT = 12


class _Finish(Exception):
    def __init__(self, status: RunStatus, reason: str, outcome: str | None = None, horizon_reached: bool = False):
        self.status = status
        self.reason = reason
        self.outcome = outcome
        self.horizon_reached = horizon_reached


def _matches(event_type: str, patterns: list[str]) -> bool:
    for p in patterns:
        if p == "*" or p == event_type or (p.endswith("*") and event_type.startswith(p[:-1])):
            return True
    return False


class Simulator:
    def __init__(
        self,
        model: DecisionWorldModel,
        manifest: RunManifest,
        agent_runtime: AgentRuntime,
        store: RunStore,
        profiles: dict[str, list[ProfileFeature]] | None = None,
        *,
        checkpoint: dict[str, Any] | None = None,
        prior_events: list[WorldEvent] | None = None,
        clock: Callable[[], float] = time.monotonic,
        enforce_wall: bool = True,
        checkpoint_every: int = 20,
        stop_after_events: int | None = None,
    ):
        if not model.hash or model.hash != manifest.model_ref.hash:
            raise ValueError("a run must pin the exact model hash it executes (§2.3)")
        self.model = model
        self.index = ModelIndex(model)
        self.manifest = manifest
        self.run_id = manifest.run_id
        if manifest.policy_id not in self.index.policies:
            raise ValueError(f"unknown policy {manifest.policy_id!r}")
        self.policy = self.index.policies[manifest.policy_id]
        for sid in manifest.scenario_ids:
            if sid not in self.index.scenarios:
                raise ValueError(f"unknown scenario {sid!r}")
        self.scenarios = [self.index.scenarios[s] for s in manifest.scenario_ids]
        self.params, self.params_json = self._resolve_params()
        self.rng = CounterRNG(manifest.random_streams)
        self.budget = BudgetTracker(manifest.execution_budget, clock, enforce_wall)
        self.runtime = agent_runtime
        self.store = store
        self.profiles = profiles or {}
        self.events: list[WorldEvent] = list(prior_events or [])
        self.queue = EventQueue()
        self.pending_wakeups: dict[str, set[str]] = {}
        self.pending_gates: set[str] = set()
        self.diagnostics: list[ModelDiagnostic] = []
        self.checkpoint = checkpoint
        self.checkpoint_every = checkpoint_every
        self.stop_after_events = stop_after_events
        self.start = self.index.horizon_start
        self.end = self.index.horizon_end
        self.now = self.start
        self._zero_chain = 0
        self.watchers = {
            m.id: set().union(referenced_names(m.rule.target), referenced_names(m.rule.bound_ref))
            for m in model.mechanisms
            if m.schedule.kind == "watch" and m.rule.kind == "threshold_gate"
        }
        self.wakeup_mechs = [m for m in model.mechanisms if m.rule.kind == "wakeup"]
        self.state: SimState | None = None
        self._state_hash: str | None = None
        self.result: RunResult | None = None

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------
    def _resolve_params(self) -> tuple[dict[str, Any], dict[str, Any]]:
        typed, raw_out = {}, {}
        for p in self.model.parameters:
            raw: Any = p.distribution.model_dump(mode="json", exclude_none=True) if p.type == "distribution" and p.distribution else p.value
            for sc in self.scenarios:
                if p.id in sc.parameter_overrides:
                    raw = sc.parameter_overrides[p.id]
                if sc.id in p.scenario_values:
                    raw = p.scenario_values[sc.id]
            if p.id in self.manifest.parameter_overrides:
                raw = self.manifest.parameter_overrides[p.id]
            raw_out[p.id] = raw
            if p.type == "distribution":
                typed[p.id] = Distribution.model_validate(raw) if isinstance(raw, dict) else raw
            else:
                typed[p.id] = typed_param_value(p, raw)
        return typed, raw_out

    def _initial_state(self) -> SimState:
        world: dict[str, Any] = {}
        for sv in self.model.state:
            v = sv.initial_value
            if sv.type == "ledger":
                v = v if v else default_ledger()
            elif v is None:
                v = "UNKNOWN"
            elif sv.type == "quantity" and v != "UNKNOWN":
                v = float(v)
            world[sv.ref] = json_copy(v)
        for sc in self.scenarios:
            for ref, v in sc.state_overrides.items():
                if ref not in self.index.state:
                    raise ValueError(f"scenario {sc.id} overrides undeclared state {ref}")
                sv = self.index.state[ref]
                world[ref] = float(v) if sv.type == "quantity" and v != "UNKNOWN" else v
        agents = {
            a.id: {
                "beliefs": json_copy(a.initial_beliefs), "memory": [], "commitments": [], "requests": [],
                "last_seen_seq": -1, "wakeups": 0, "last_decision_at": None, "notices": [],
            }
            for a in self.model.agents
        }
        return SimState(world, agents, {"policy": self.policy.id})

    def _schedule_initial(self) -> None:
        self._push(ScheduledEvent(sim_at=self.start, kind="POLICY_INIT", event_type="policy_initialized",
                                  reason="apply decision policy and scenario assumptions"))
        for i, se in enumerate(self.policy.scheduled_effects):
            self._push(ScheduledEvent(sim_at=resolve_time(se.at, self.start), kind="EXOGENOUS", event_type=se.event_type,
                                      meta={"source": f"policy:{self.policy.id}", "index": i},
                                      visibility=se.visibility.model_dump(mode="json"), reason="policy-scheduled effect"))
        for sc in self.scenarios:
            self._schedule_scenario_events(sc, after=None)
        for m in self.model.mechanisms:
            s = m.schedule
            if s.kind == "recurring":
                interval = Duration.parse(s.interval)
                first = resolve_time(s.start, self.start) if s.start else interval.add_to(self.start)
                self._push(ScheduledEvent(sim_at=first, kind="MECHANISM_TICK", event_type=f"tick:{m.id}",
                                          mechanism_id=m.id, meta={"k": 0, "anchor": first.isoformat()},
                                          reason="recurring process"))
            elif s.kind == "at":
                for t in s.times:
                    self._push(ScheduledEvent(sim_at=resolve_time(t, self.start), kind="MECHANISM_TICK",
                                              event_type=f"tick:{m.id}", mechanism_id=m.id, meta={"at": t},
                                              reason="scheduled process"))
        for a in self.model.agents:
            for t in a.initial_wakeups:
                self._schedule_wakeup(a.id, resolve_time(t, self.start), "planned_decision_time", [])
            if a.periodic_review:
                self._schedule_wakeup(a.id, Duration.parse(a.periodic_review).add_to(self.start), "periodic_review", [])

    def _schedule_scenario_events(self, sc, after: datetime | None) -> None:
        for i, xe in enumerate(sc.exogenous_events):
            at = resolve_time(xe.at, self.start)
            if after is not None and at <= after:
                continue
            self._push(ScheduledEvent(sim_at=at, kind="EXOGENOUS", event_type=xe.event_type,
                                      meta={"source": f"scenario:{sc.id}", "index": i},
                                      visibility=xe.visibility.model_dump(mode="json"), reason=xe.description or "external scenario event"))

    def _push(self, ev: ScheduledEvent) -> None:
        if ev.sim_at > self.end:
            return
        self.queue.push(ev)
        self.budget.scheduled += 1

    def _schedule_wakeup(self, agent_id: str, at: datetime, reason: str, parents: list[str]) -> None:
        if at > self.end or agent_id not in self.index.agents:
            return
        pend = self.pending_wakeups.setdefault(agent_id, set())
        if reason != "periodic_review":
            for t in pend:
                if self.now <= datetime.fromisoformat(t) <= at:
                    return
        pend.add(at.isoformat())
        self._push(ScheduledEvent(sim_at=at, kind="AGENT_WAKEUP", event_type="agent_wakeup", actor_id=agent_id,
                                  reason=reason, parent_event_ids=list(parents)))

    # ------------------------------------------------------------------
    # main loop (§8.2)
    # ------------------------------------------------------------------
    def run(self) -> RunResult:
        try:
            if self.checkpoint is None:
                self.state = self._initial_state()
                self._state_hash = self.state.hash()
                self.manifest.initial_state_hash = self._state_hash
                self._schedule_initial()
                self.store.commit_genesis(self.manifest, self.state.snapshot(), self._state_hash, self._checkpoint_payload())
            else:
                self._restore(self.checkpoint)
                self.manifest.initial_state_hash = self._state_hash
                self.store.commit_genesis(self.manifest, self.state.snapshot(), self._state_hash, self._checkpoint_payload())
                if self.manifest.intervention is not None:
                    self._apply_intervention()
            self._loop()
        except _Finish as f:
            return self._finalize(f.status, f.reason, f.outcome, f.horizon_reached)
        except ModelExecutionError as e:
            self.diagnostics.append(self._diag("MODEL_EXECUTION_ERROR", str(e), "high"))
            return self._finalize(RunStatus.MODEL_EXECUTION_BLOCKED, str(e))
        except Exception as e:  # noqa: BLE001 - infrastructure failure is distinct from model outcomes
            if self.state is None:
                raise
            self.diagnostics.append(self._diag("MODEL_EXECUTION_ERROR", f"infrastructure failure: {e!r}", "high"))
            return self._finalize(RunStatus.FAILED, f"infrastructure failure: {e!r}")
        raise AssertionError("unreachable")

    def _loop(self) -> None:
        while True:
            if self.stop_after_events is not None and len(self.events) >= self.stop_after_events:
                raise _Finish(RunStatus.COMPLETED_HORIZON, "replay reached recorded event count")
            reason = self.budget.exhausted_reason()
            if reason:
                self.diagnostics.append(self._diag("BUDGET_LIMIT", reason, "low"))
                raise _Finish(RunStatus.BUDGET_EXHAUSTED, reason)
            if not len(self.queue):
                # No registered process can change state before the horizon: document the collapse.
                ev = ScheduledEvent(sim_at=self.end, kind="QUIESCENT_COLLAPSE", event_type="quiescent_until_horizon",
                                    reason="no registered future process can change the state")
                quiet_since = self.now
                self.now = self.end
                tr = Transition(self, PatchBuilder(self.state), self.now, self._next_event_id())
                self._commit(tr, ev, "QUIESCENT_COLLAPSE", "quiescent_until_horizon",
                             {"quiescent_since": quiet_since.isoformat()})
                raise _Finish(RunStatus.COMPLETED_HORIZON, "horizon reached (quiescent world collapsed to horizon)",
                              horizon_reached=True)
            ev = self.queue.pop()
            if ev.sim_at > self.end:
                self.now = self.end
                raise _Finish(RunStatus.COMPLETED_HORIZON, "simulation horizon reached", horizon_reached=True)
            if ev.sim_at < self.now:
                raise ModelExecutionError("event scheduled before current simulated time (retroactive)")
            if ev.sim_at == self.now and self.events:
                self._zero_chain += 1
                if self._zero_chain > self.manifest.execution_budget.max_zero_time_chain:
                    self.diagnostics.append(self._diag("ZERO_TIME_LOOP", "too many zero-time events", "high"))
                    raise ModelExecutionError("zero-time event chain exceeded bound")
            else:
                self._zero_chain = 0
            self.now = ev.sim_at
            self._dispatch(ev)

    def _dispatch(self, ev: ScheduledEvent) -> None:
        k = ev.kind
        if k == "POLICY_INIT":
            tr = self._new_transition()
            tr.assumptions.update(self.manifest.assumptions)
            for sc in self.scenarios:
                tr.assumptions.update(sc.assumptions)
                tr.tiers.append(sc.epistemic_tier)
            tr.apply_effects(self.policy.initial_effects)
            payload = {
                "policy_id": self.policy.id, "scenario_ids": self.manifest.scenario_ids,
                "resolved_parameters": self.params_json,
                "agent_overlays": {a: [o.model_dump() for o in c.overlays] for a, c in self.manifest.agent_configs.items() if c.overlays},
                "deadline_disclosure": self.manifest.deadline_disclosure,
            }
            self._check_and_commit(tr, ev, "POLICY_INIT", "policy_initialized", payload, transition_ref=f"policy:{self.policy.id}")
        elif k == "EXOGENOUS":
            src, idx = ev.meta["source"], ev.meta["index"]
            if src.startswith("policy:"):
                spec = self.policy.scheduled_effects[idx]
                tier = None
            else:
                sc = self.index.scenarios[src.split(":", 1)[1]]
                spec = sc.exogenous_events[idx]
                tier = sc.epistemic_tier
            tr = self._new_transition()
            if tier:
                tr.tiers.append(tier)
                tr.assumptions.update(self.index.scenarios[src.split(":", 1)[1]].assumptions)
            tr.apply_effects(spec.effects)
            self._run_subscribers(tr, ev.event_type, {})
            self._check_and_commit(tr, ev, "EXOGENOUS", ev.event_type, {"source": src, "description": spec.description},
                                   transition_ref=src, visibility=ev.visibility)
        elif k == "MECHANISM_TICK":
            mech = self.index.mechanisms[ev.mechanism_id]
            n = 1.0
            if mech.schedule.kind == "recurring":
                interval = Duration.parse(mech.schedule.interval)
                n = interval_count(interval, getattr(mech.rule, "interval_unit", None) or "month") if hasattr(mech.rule, "interval_unit") else 1.0
                k_next = ev.meta["k"] + 1
                anchor = datetime.fromisoformat(ev.meta["anchor"])
                nxt = Duration(interval.months * k_next, interval.seconds * k_next).add_to(anchor)
                end = resolve_time(mech.schedule.end, self.start) if mech.schedule.end else self.end
                if nxt <= end:
                    self._push(ScheduledEvent(sim_at=nxt, kind="MECHANISM_TICK", event_type=ev.event_type, mechanism_id=mech.id,
                                              meta={"k": k_next, "anchor": ev.meta["anchor"]}, reason="recurring process"))
            tr = self._new_transition()
            tr.apply_mechanism(mech, n_intervals=n, trigger_type=ev.event_type)
            self._check_and_commit(tr, ev, "MECHANISM_TICK", ev.event_type, {"mechanism": mech.id, "intervals": n},
                                   transition_ref=mech.id, visibility=mech.visibility.model_dump(mode="json"))
        elif k == "DOMAIN_EVENT":
            tr = self._new_transition()
            extra = {f"event.{key}": decode_value(v) for key, v in ev.payload.items()}
            self._run_subscribers(tr, ev.event_type, extra)
            self._check_and_commit(tr, ev, "DOMAIN_EVENT", ev.event_type, {"event_payload": ev.payload},
                                   transition_ref=",".join(tr.mechanisms) or None, visibility=ev.visibility)
        elif k == "GATE_FIRE":
            self.pending_gates.discard(ev.mechanism_id)
            mech = self.index.mechanisms[ev.mechanism_id]
            if self._gate_condition(mech) is not True:
                return
            tr = self._new_transition()
            gs = dict(tr.builder.get_internal(f"gate.{mech.id}", {"active": False, "fired": 0}))
            gs["active"] = True
            gs["fired"] = gs.get("fired", 0) + 1
            tr.builder.set_internal(f"gate.{mech.id}", gs)
            tr.apply_mechanism(mech, trigger_type=ev.event_type)
            self._check_and_commit(tr, ev, "GATE_FIRE", f"gate:{mech.id}", {"mechanism": mech.id, "condition": self._gate_text(mech)},
                                   transition_ref=mech.id, visibility=mech.visibility.model_dump(mode="json"))
        elif k == "GATE_RESET":
            self.pending_gates.discard(f"reset:{ev.mechanism_id}")
            mech = self.index.mechanisms[ev.mechanism_id]
            if self._gate_condition(mech) is not False:
                return
            tr = self._new_transition()
            gs = dict(tr.builder.get_internal(f"gate.{mech.id}", {"active": False, "fired": 0}))
            gs["active"] = False
            tr.builder.set_internal(f"gate.{mech.id}", gs)
            self._check_and_commit(tr, ev, "GATE_RESET", f"gate_reset:{mech.id}", {"mechanism": mech.id},
                                   transition_ref=mech.id, visibility={"scope": "hidden", "agents": []})
        elif k == "AGENT_WAKEUP":
            self._process_wakeup(ev)
        else:
            raise ModelExecutionError(f"unknown scheduled event kind {k}")

    def _run_subscribers(self, tr: Transition, event_type: str, extra: dict[str, Any]) -> None:
        for m in self.model.mechanisms:
            if m.schedule.kind == "on_event" and m.rule.kind not in ("wakeup", "governance") and _matches(event_type, m.schedule.event_types):
                tr.apply_mechanism(m, n_intervals=1.0, trigger_type=event_type, extra=extra)

    # ------------------------------------------------------------------
    # Agent decision events (§7.3, §7.5)
    # ------------------------------------------------------------------
    def _process_wakeup(self, ev: ScheduledEvent) -> None:
        aid = ev.actor_id
        spec = self.index.agents[aid]
        self.pending_wakeups.get(aid, set()).discard(ev.sim_at.isoformat())
        if ev.reason == "periodic_review" and spec.periodic_review:
            self._schedule_wakeup(aid, Duration.parse(spec.periodic_review).add_to(self.now), "periodic_review", [])
        st = self.state.agents[aid]
        if spec.max_wakeups is not None and st["wakeups"] >= spec.max_wakeups:
            return  # AgentWakeupPolicy: bounded reconsideration, no event committed
        eligible = self._eligible_actions(aid)
        if ev.reason == "periodic_review" and all(e.action_type == WAIT for e in eligible):
            return  # nothing this Agent could do: no decision invocation, no busy-loop event
        if self.budget.wakeups >= self.manifest.execution_budget.max_agent_wakeups:
            raise _Finish(RunStatus.BUDGET_EXHAUSTED, f"agent wakeup cap reached ({self.budget.wakeups})")
        if not self.budget.can_afford_decision(self.runtime.estimate_tokens(aid)):
            raise _Finish(RunStatus.BUDGET_EXHAUSTED, "insufficient model budget for the next Agent decision")
        cfg = self.manifest.agent_configs.get(aid) or RunAgentConfig(agent_id=aid)
        disclosure = self.manifest.deadline_disclosure
        vis = visible_refs(self.index, aid, disclosure)
        vparams = visible_params(self.index)
        obs = build_observation(self.index, aid, self.state.world, self.events, st["last_seen_seq"], disclosure, iso(self.now))
        obs["horizon"] = {"start": self.start.isoformat(), "end": self.end.isoformat()}
        features = effective_features(self.profiles.get(aid, []), cfg.overlays)
        ctx = DecisionContext(
            run_id=self.run_id, agent_id=aid, display_name=spec.display_name or aid, sim_now=self.now,
            wakeup_reason=ev.reason, observation=obs, eligible=eligible, features=features, overlays=cfg.overlays,
            appraise=lambda action_type, args: self._appraise(aid, action_type, args, vis, vparams),
            governance_status=self._visible_governance(aid), deadline_disclosed=disclosure == DeadlineDisclosure.DEADLINE_AWARE,
            objectives=self.model.decision_criteria, agent_state=json_copy(st),
            seed=self.rng.seeds.get("agent_sampling", 0) + self.budget.wakeups, notices=list(st.get("notices", [])),
        )
        decision = self.runtime.decide(ctx)
        self.budget.wakeups += 1
        self.budget.debit_model(decision.usage)
        intent = decision.intent
        tr = self._new_transition()
        validated = self._validate_intent(intent, aid, eligible, tr)
        payload = {"decision": decision.to_record(), "wakeup_reason": ev.reason, "validated": validated.model_dump(mode="json")}
        usage = dict(decision.usage)
        if not validated.valid:
            will_retry = ev.meta.get("retry", 0) < 1
            notices = [{"rejection_code": validated.rejection_code, "detail": validated.detail}] if will_retry else []
            self._update_agent_state(tr.builder, aid, obs, decision, validated, vis, notices=notices)
            payload["rejection_code"] = validated.rejection_code
            self._commit(tr, ev, "AGENT_DECISION", "agent_intent_rejected", payload, actor=aid, usage=usage,
                         visibility={"scope": "agents", "agents": [aid]})
            if will_retry:
                retry = ScheduledEvent(sim_at=self.now, kind="AGENT_WAKEUP", event_type="agent_wakeup", actor_id=aid,
                                       reason="retry_after_rejection", meta={"retry": ev.meta.get("retry", 0) + 1},
                                       parent_event_ids=[self.events[-1].event_id])
                self._push(retry)
            return
        if intent.action_type == WAIT:
            self._update_agent_state(tr.builder, aid, obs, decision, validated, vis)
            self._commit(tr, ev, "AGENT_DECISION", "agent_wait", payload, actor=aid, usage=usage,
                         visibility={"scope": "agents", "agents": [aid]})
            return
        action = self.index.actions[intent.action_type]
        extra = self._arg_env(action, intent.parameters, aid)
        if action.governance:
            code = tr.governance_act(action.governance, intent.parameters, aid)
            if code:
                raise ModelExecutionError(f"governance act failed after validation: {code}")
        tr.apply_effects(action.effects, extra)
        tr.tiers.append(action.epistemic_tier)
        self._update_agent_state(tr.builder, aid, obs, decision, validated, vis, action_tags=action.tags, args=intent.parameters)
        terrs = type_violations(self, tr.builder)
        if terrs:
            raise ModelExecutionError("; ".join(terrs))
        viols = constraint_violations(self, tr.builder, self.now)
        if viols:
            c, detail = viols[0]
            tr2 = self._new_transition()
            rej = ValidatedIntent(valid=False, rejection_code=f"INVARIANT:{c.id}", detail=detail)
            self._update_agent_state(tr2.builder, aid, obs, decision, rej, vis)
            payload["rejection_code"] = rej.rejection_code
            payload["validated"] = rej.model_dump(mode="json")
            self._commit(tr2, ev, "AGENT_DECISION", "agent_intent_rejected", payload, actor=aid, usage=usage,
                         visibility={"scope": "agents", "agents": [aid]})
            return
        self._commit(tr, ev, "AGENT_DECISION", f"action:{action.id}", payload, actor=aid, usage=usage,
                     transition_ref=f"action:{action.id}", visibility=action.visibility.model_dump(mode="json"))

    def _arg_env(self, action, params: dict[str, Any], aid: str) -> dict[str, Any]:
        extra: dict[str, Any] = {"actor": aid}
        for a in action.args:
            if a.name not in params:
                continue
            v = params[a.name]
            extra[f"arg.{a.name}"] = Q(float(v), parse_unit(a.unit)) if a.type == "number" else v
        return extra

    def _preconds(self, action, aid: str, params: dict[str, Any]) -> tuple[Any, str | None, list[str]]:
        env = StateEnv(self.index, self.state.world, self.params, self.now, self._arg_env(action, params, aid))
        matched = []
        for p in action.preconditions:
            try:
                v = evaluate(p.expr, env)
            except EvalError as e:
                return False, f"PRECONDITION_ERROR:{p.id}", matched
            if v is UNKNOWN:
                return UNKNOWN, "PRECONDITION_UNKNOWN", matched
            if v is not True:
                return False, p.rejection_code, matched
            matched.append(p.id)
        return True, None, matched

    def _window_ok(self, action_id: str) -> bool:
        if action_id in self.policy.forbidden_actions:
            return False
        for w in self.policy.action_windows:
            if w.action != action_id:
                continue
            if w.available_from and self.now < resolve_time(w.available_from, self.start):
                return False
            if w.available_until and self.now >= resolve_time(w.available_until, self.start):
                return False
            if w.when:
                v = evaluate(w.when, StateEnv(self.index, self.state.world, self.params, self.now))
                if v is not True:
                    return False
        return True

    def _eligible_actions(self, aid: str) -> list[EligibleAction]:
        spec = self.index.agents[aid]
        out: list[EligibleAction] = []
        probe = self._new_transition()
        for act in self.model.actions:
            if not action_matches_actor(act, aid, spec.role) or not self._window_ok(act.id):
                continue
            args_out = []
            ok = True
            for arg in act.args:
                if arg.type == "enum":
                    opts = list(arg.values or [])
                    if len(act.args) == 1:
                        opts = [v for v in opts if self._preconds(act, aid, {arg.name: v})[0] is True]
                    if act.governance and act.governance.option_arg == arg.name:
                        rule = self.index.mechanisms[act.governance.mechanism].rule
                        opts = [o for o in opts if o in rule.option_values]
                    if not opts:
                        ok = False
                    args_out.append({"name": arg.name, "type": "enum", "options": opts})
                elif arg.type == "number":
                    opts = arg.options or [v for v in (arg.min, arg.max) if v is not None]
                    args_out.append({"name": arg.name, "type": "number", "options": opts, "unit": arg.unit,
                                     "min": arg.min, "max": arg.max})
                else:
                    args_out.append({"name": arg.name, "type": "boolean", "options": [True, False]})
            if not ok:
                continue
            if not act.args and self._preconds(act, aid, {})[0] is not True:
                continue
            gov = None
            if act.governance:
                sample = {a["name"]: a["options"][0] for a in args_out if a["options"]}
                if probe.governance_act(act.governance, sample, aid, dry_run=True):
                    continue
                rule = self.index.mechanisms[act.governance.mechanism].rule
                ledger = self.state.world.get(rule.ledger_ref) or default_ledger()
                open_p = next((p for p in reversed(ledger["proposals"]) if p["status"] == "open"), None)
                propose = next((a for a in self.model.actions if a.governance and a.governance.mechanism == act.governance.mechanism
                                and a.governance.act == "propose"), None)
                gov = {"mechanism": act.governance.mechanism, "act": act.governance.act,
                       "option_arg": act.governance.option_arg, "open_proposal": open_p,
                       "decision_rule": rule.decision_rule, "rounds_used": ledger["rounds"], "max_rounds": rule.max_rounds,
                       "options": list(rule.option_values), "requires_gain": rule.proposal_requires_gain,
                       "propose_action": propose.id if propose else None,
                       "propose_option_arg": propose.governance.option_arg if propose else None}
            out.append(EligibleAction(action_type=act.id, description=act.description, args=args_out, tags=list(act.tags),
                                      governance=gov, appraisal_dims=sorted(act.appraisal)))
        out.append(EligibleAction(action_type=WAIT, description="Take no action now; reconsider at the next relevant event."))
        return out

    def _validate_intent(self, intent: ActionIntent, aid: str, eligible: list[EligibleAction], tr: Transition) -> ValidatedIntent:
        if intent.agent_id != aid or intent.run_id != self.run_id:
            return ValidatedIntent(valid=False, rejection_code="INTENT_IDENTITY_MISMATCH")
        if intent.action_type == WAIT:
            return ValidatedIntent(valid=True, authorized_transition_id=None)
        act = self.index.actions.get(intent.action_type)
        if act is None:
            return ValidatedIntent(valid=False, rejection_code="UNKNOWN_ACTION", detail=intent.action_type)
        el = next((e for e in eligible if e.action_type == act.id), None)
        if el is None:
            return ValidatedIntent(valid=False, rejection_code="NOT_ELIGIBLE")
        names = {a.name for a in act.args}
        extra_keys = set(intent.parameters) - names
        if extra_keys:
            return ValidatedIntent(valid=False, rejection_code="UNKNOWN_ARGUMENT", detail=",".join(sorted(extra_keys)))
        for arg, spec in zip(act.args, el.args):
            if arg.name not in intent.parameters:
                return ValidatedIntent(valid=False, rejection_code="MISSING_ARGUMENT", detail=arg.name)
            v = intent.parameters[arg.name]
            if arg.type == "enum" and v not in spec["options"]:
                return ValidatedIntent(valid=False, rejection_code="INVALID_ARGUMENT_VALUE", detail=f"{arg.name}={v!r}")
            if arg.type == "number":
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    return ValidatedIntent(valid=False, rejection_code="INVALID_ARGUMENT_TYPE", detail=arg.name)
                if (arg.min is not None and v < arg.min) or (arg.max is not None and v > arg.max):
                    return ValidatedIntent(valid=False, rejection_code="ARGUMENT_OUT_OF_RANGE", detail=arg.name)
            if arg.type == "boolean" and not isinstance(v, bool):
                return ValidatedIntent(valid=False, rejection_code="INVALID_ARGUMENT_TYPE", detail=arg.name)
        ok, code, matched = self._preconds(act, aid, intent.parameters)
        if ok is not True:
            return ValidatedIntent(valid=False, rejection_code=code, matched_precondition_ids=matched)
        if act.governance:
            code = tr.governance_act(act.governance, intent.parameters, aid, dry_run=True)
            if code:
                return ValidatedIntent(valid=False, rejection_code=code, matched_precondition_ids=matched)
        return ValidatedIntent(valid=True, matched_precondition_ids=matched, authorized_transition_id=f"action:{act.id}")

    def _appraise(self, aid: str, action_type: str, args: dict[str, Any], vis: set[str], vparams: set[str]) -> dict[str, Any]:
        act = self.index.actions.get(action_type)
        if act is None:
            return {}
        env = StateEnv(self.index, self.state.world, self.params, self.now, self._arg_env(act, args, aid),
                       visible=vis, visible_params=vparams)
        out = {}
        for dim, expr in act.appraisal.items():
            try:
                out[dim] = evaluate(expr, env)
            except EvalError:
                out[dim] = UNKNOWN
        return out

    def _visible_governance(self, aid: str) -> list[dict[str, Any]]:
        out = []
        for m in self.model.mechanisms:
            if m.rule.kind == "governance" and aid in m.rule.participants:
                ledger = self.state.world.get(m.rule.ledger_ref) or default_ledger()
                out.append({"mechanism": m.id, "decision_rule": m.rule.decision_rule, "participants": m.rule.participants,
                            "options": m.rule.option_values, "ledger": ledger})
        return out

    def _update_agent_state(self, b: PatchBuilder, aid: str, obs: dict[str, Any], decision: AgentDecision,
                            validated: ValidatedIntent, vis: set[str], action_tags: list[str] | None = None,
                            args: dict[str, Any] | None = None, notices: list[dict[str, Any]] | None = None) -> None:
        b.set_agent(aid, "last_seen_seq", len(self.events) - 1)
        b.set_agent(aid, "last_decision_at", self.now.isoformat())
        b.set_agent(aid, "wakeups", b.get_agent(aid, "wakeups", 0) + 1)
        b.set_agent(aid, "notices", notices or [])
        mem = list(b.get_agent(aid, "memory", []))
        mem.append({
            "at": self.now.isoformat(),
            "observed": [e["event_type"] for e in obs["new_events"]][-8:],
            "action": decision.intent.action_type,
            "valid": validated.valid,
        })
        b.set_agent(aid, "memory", mem[-MEMORY_LIMIT:])
        beliefs = dict(b.get_agent(aid, "beliefs", {}))
        for key, val in decision.belief_updates.items():
            # Beliefs may only summarise what the Agent observed (no omniscient writes).
            if key in vis or key.startswith("note.") or key.startswith("belief."):
                beliefs[key] = val
        b.set_agent(aid, "beliefs", beliefs)
        if action_tags and "information_request" in action_tags:
            reqs = list(b.get_agent(aid, "requests", []))
            reqs.append({"at": self.now.isoformat(), "action": decision.intent.action_type, "args": args or {}})
            b.set_agent(aid, "requests", reqs)

    # ------------------------------------------------------------------
    # commit + post-commit scheduling
    # ------------------------------------------------------------------
    def _next_event_id(self) -> str:
        return f"{self.run_id}:{len(self.events):06d}"

    def _new_transition(self) -> Transition:
        return Transition(self, PatchBuilder(self.state), self.now, self._next_event_id())

    def _check_and_commit(self, tr: Transition, ev: ScheduledEvent, kind: str, event_type: str, payload: dict[str, Any],
                          transition_ref: str | None = None, visibility: dict[str, Any] | None = None) -> None:
        terrs = type_violations(self, tr.builder)
        if terrs:
            raise ModelExecutionError("; ".join(terrs))
        viols = constraint_violations(self, tr.builder, self.now)
        if viols:
            c, detail = viols[0]
            pol = c.on_violation
            self.diagnostics.append(self._diag("INVARIANT_VIOLATION", f"{c.id}: {detail} (blocked {event_type})", "medium",
                                               impacted=tr.mechanisms))
            blank = self._new_transition()
            if pol.action == "terminate":
                blank.terminate = {"outcome": pol.outcome or f"CONSTRAINT_{c.id.upper()}", "reason": pol.reason or detail}
            elif pol.action == "emit" and pol.event_type:
                blank.spawn(ScheduledEvent(sim_at=self.now, kind="DOMAIN_EVENT", event_type=pol.event_type,
                                           payload={"constraint": c.id}, visibility=Visibility().model_dump(mode="json"),
                                           reason=f"constraint {c.id} violated"))
            blank.tiers.extend(tr.tiers)
            blank.mechanisms.extend(tr.mechanisms)
            self._commit(blank, ev, "CONSTRAINT_BLOCKED", f"blocked:{event_type}",
                         {"constraint": c.id, "detail": detail, "blocked_transition": event_type,
                          "on_violation": pol.action, "description": c.description},
                         transition_ref=c.id)
            return
        self._commit(tr, ev, kind, event_type, payload, transition_ref=transition_ref, visibility=visibility)

    def _commit(self, tr: Transition, ev: ScheduledEvent, kind: str, event_type: str, payload: dict[str, Any],
                actor: str | None = None, transition_ref: str | None = None, visibility: dict[str, Any] | None = None,
                usage: dict[str, int] | None = None) -> WorldEvent:
        ops = tr.builder.ops()
        pre = self._state_hash
        self.state.apply(ops)
        post = self.state.hash()
        self._state_hash = post
        if tr.notes:
            payload = {**payload, "notes": tr.notes}
        if tr.terminate:
            payload = {**payload, "terminate": tr.terminate}
        we = WorldEvent(
            event_id=tr.event_id, run_id=self.run_id, branch_id=self.run_id, seq=len(self.events),
            sim_at=self.now.isoformat(), event_type=event_type, kind=kind, actor_id=actor,
            parent_event_ids=list(ev.parent_event_ids), transition_ref=transition_ref, pre_state_hash=pre,
            patch=ops, post_state_hash=post, assumption_refs=sorted(tr.assumptions), random_draw_refs=tr.draws,
            visibility_scope=Visibility(**visibility) if visibility else Visibility(), payload=payload,
            usage=usage or {}, tier=weakest_tier(tr.tiers) if tr.tiers else None,
        )
        self.events.append(we)
        self.budget.events += 1
        for sp in tr.spawned:
            self._push(sp)
        for agent_id, at, reason in tr.wakes:
            self._schedule_wakeup(agent_id, at, reason, [we.event_id])
        self._post_commit(we)
        cp = None
        if kind == "AGENT_DECISION" or we.seq % self.checkpoint_every == 0:
            cp = self._checkpoint_payload()
        self.store.append_event(we, cp)
        if tr.terminate:
            raise _Finish(RunStatus.TERMINAL_STATE, tr.terminate.get("reason") or tr.terminate["outcome"], tr.terminate["outcome"])
        return we

    def _gate_condition(self, mech) -> Any:
        r = mech.rule
        env = StateEnv(self.index, self.state.world, self.params, self.now)
        try:
            a, b = evaluate(r.target, env), evaluate(r.bound_ref, env)
            return compare(CMP[r.comparator], a, b)
        except EvalError as e:
            raise ModelExecutionError(f"gate {mech.id}: {e}") from e

    def _gate_text(self, mech) -> str:
        return f"{mech.rule.target} {CMP[mech.rule.comparator]} {mech.rule.bound_ref}"

    def _post_commit(self, we: WorldEvent) -> None:
        changed = {op.key for op in we.patch if op.layer == "world"}
        for gid, inputs in self.watchers.items():
            if inputs and not (inputs & changed) and we.kind not in ("POLICY_INIT", "INTERVENTION") and "now" not in inputs:
                continue
            mech = self.index.mechanisms[gid]
            cond = self._gate_condition(mech)
            gs = self.state.internal.get(f"gate.{gid}", {"active": False, "fired": 0})
            if cond is True and not gs["active"] and gid not in self.pending_gates and not (mech.rule.once and gs["fired"] > 0):
                self.pending_gates.add(gid)
                self._push(ScheduledEvent(sim_at=self.now, kind="GATE_FIRE", event_type=f"gate:{gid}", mechanism_id=gid,
                                          parent_event_ids=[we.event_id], reason="threshold crossed"))
            elif cond is False and gs["active"] and not mech.rule.once and f"reset:{gid}" not in self.pending_gates:
                self.pending_gates.add(f"reset:{gid}")
                self._push(ScheduledEvent(sim_at=self.now, kind="GATE_RESET", event_type=f"gate_reset:{gid}", mechanism_id=gid,
                                          parent_event_ids=[we.event_id], reason="threshold cleared"))
        for m in self.wakeup_mechs:
            if m.schedule.kind != "on_event" or not _matches(we.event_type, m.schedule.event_types):
                continue
            r = m.rule
            if r.when:
                v = evaluate(r.when, StateEnv(self.index, self.state.world, self.params, self.now, {"actor": we.actor_id or ""}))
                if v is not True:
                    continue
            targets = list(self.index.agents) if "*" in r.agents else r.agents
            at = Duration.parse(r.latency).add_to(self.now)
            for a in targets:
                if r.exclude_actor and a == we.actor_id:
                    continue
                self._schedule_wakeup(a, at, r.reason, [we.event_id])

    # ------------------------------------------------------------------
    # checkpoints / forks (§10.3 "fork from a shared valid checkpoint")
    # ------------------------------------------------------------------
    def _checkpoint_payload(self) -> dict[str, Any]:
        return {
            "state": self.state.snapshot(), "state_hash": self._state_hash, "queue": self.queue.snapshot(),
            "pending_wakeups": {a: sorted(t) for a, t in self.pending_wakeups.items()},
            "pending_gates": sorted(self.pending_gates), "clock": self.now.isoformat(), "events_len": len(self.events),
        }

    def _restore(self, cp: dict[str, Any]) -> None:
        self.state = SimState.from_snapshot(cp["state"])
        self._state_hash = self.state.hash()
        if self._state_hash != cp["state_hash"]:
            raise ValueError("checkpoint state hash mismatch")
        self.queue = EventQueue.restore(cp["queue"])
        self.pending_wakeups = {a: set(t) for a, t in cp["pending_wakeups"].items()}
        self.pending_gates = set(cp["pending_gates"])
        self.now = datetime.fromisoformat(cp["clock"])

    def _apply_intervention(self) -> None:
        iv = self.manifest.intervention
        tr = self._new_transition()
        if iv.scenario_ids is not None:
            keep = {f"scenario:{s}" for s in iv.scenario_ids}
            items = [e for e in self.queue.snapshot()["items"]
                     if not (e["kind"] == "EXOGENOUS" and e["meta"].get("source", "").startswith("scenario:") and e["meta"]["source"] not in keep)]
            self.queue = EventQueue.restore({"seq": self.queue.snapshot()["seq"], "items": items})
            for sc in self.scenarios:
                self._schedule_scenario_events(sc, after=self.now)
        if iv.policy_id is not None:
            tr.builder.set_internal("policy", self.policy.id)
            tr.apply_effects(self.policy.initial_effects)
            items = [e for e in self.queue.snapshot()["items"] if not (e["kind"] == "EXOGENOUS" and e["meta"].get("source", "").startswith("policy:"))]
            self.queue = EventQueue.restore({"seq": self.queue.snapshot()["seq"], "items": items})
            for i, se in enumerate(self.policy.scheduled_effects):
                at = resolve_time(se.at, self.start)
                if at > self.now:
                    self._push(ScheduledEvent(sim_at=at, kind="EXOGENOUS", event_type=se.event_type,
                                              meta={"source": f"policy:{self.policy.id}", "index": i}, reason="policy-scheduled effect"))
        ev = ScheduledEvent(sim_at=self.now, kind="INTERVENTION", event_type="intervention",
                            parent_event_ids=[self.events[-1].event_id] if self.events else [])
        self._check_and_commit(tr, ev, "INTERVENTION", "intervention", {"intervention": iv.model_dump(mode="json"),
                                                                         "resolved_parameters": self.params_json},
                               transition_ref="intervention")

    # ------------------------------------------------------------------
    # finalisation
    # ------------------------------------------------------------------
    def evaluate_metrics(self, world: dict[str, Any] | None = None) -> dict[str, Any]:
        world = world if world is not None else self.state.world
        env = StateEnv(self.index, world, self.params, self.now)
        out = {}
        for m in self.model.metrics:
            if not m.computable or not m.expr:
                out[m.id] = {"status": "NON_COMPUTABLE", "reason": m.reason}
                continue
            try:
                v = evaluate(m.expr, env)
                if isinstance(v, Q) and m.unit:
                    v = v.to(parse_unit(m.unit))
                out[m.id] = {"status": "UNKNOWN" if v is UNKNOWN else "OK", "value": value_to_display(v)}
            except (EvalError, ValueError) as e:
                out[m.id] = {"status": "ERROR", "reason": str(e)}
        return out

    def _finalize(self, status: RunStatus, reason: str, outcome: str | None = None, horizon_reached: bool = False) -> RunResult:
        if self.state is None:
            raise RuntimeError("run failed before genesis")
        metrics = self.evaluate_metrics()
        flags = []
        if not horizon_reached and status != RunStatus.TERMINAL_STATE:
            flags.append(OutcomeFlag.PARTIAL_HORIZON)
        if status == RunStatus.FAILED:
            flags.append(OutcomeFlag.FAILED_INFRASTRUCTURE)
        for m in self.model.mechanisms:
            if m.rule.kind == "governance":
                ledger = self.state.world.get(m.rule.ledger_ref) or {}
                if ledger.get("proposals") and not ledger.get("satisfied") and status in (RunStatus.COMPLETED_HORIZON, RunStatus.TERMINAL_STATE):
                    flags.append(OutcomeFlag.UNRESOLVED)
                    break
        if any(v["status"] in ("UNKNOWN", "NON_COMPUTABLE") for v in metrics.values()):
            flags.append(OutcomeFlag.UNMODELED_CRITICAL_DEPENDENCY)
        result = RunResult(
            run_id=self.run_id, status=status, reason=reason, terminal_outcome=outcome, outcome_flags=[str(f) for f in flags],
            horizon_reached=horizon_reached, last_simulated_at=self.now.isoformat(), horizon_end=self.end.isoformat(),
            events_committed=len(self.events), final_state_hash=self._state_hash, usage=self.budget.as_dict(),
            diagnostics=self.diagnostics, final_metrics=metrics,
            weakest_tier=weakest_tier([e.tier for e in self.events if e.tier]) if any(e.tier for e in self.events) else None,
        )
        self.result = result
        self.store.finalize(result, {"state": self.state.snapshot(), "sim_at": self.now.isoformat(), "hash": self._state_hash})
        return result

    def _diag(self, kind: str, effect: str, risk: str, impacted: list[str] | None = None) -> ModelDiagnostic:
        return ModelDiagnostic(diagnostic_id=new_id("diag"), run_id=self.run_id, model_hash=self.model.hash,
                               failure_or_gap_type=kind, impacted_mechanisms=impacted or [], observed_sim_effect=effect,
                               risk_class=risk)

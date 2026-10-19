"""CompilerValidator (§6.2 "programmatic and authoritative, not another LLM"; §6.6; §15.4).

compile(candidate, evidence, registry, policy) → {model, validation_report}. Compilation fails on
invalid contracts or non-authorised constructs — never merely on incomplete domain coverage;
unsupported metrics are marked non-computable instead. Every tier downgrade is recorded in the
report (§6.5 "never downgrade invisibly").
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ..common import Duration, new_id, parse_dt, resolve_time
from ..model_ir.enums import (
    FidelityTier,
    GROUNDING_ORIGINS,
    OriginKind,
    PromotionState,
    RunStatus,
    TIER_ORDER,
    weakest_tier,
)
from ..model_ir.expr import (
    T,
    T_ANY,
    T_BOOL,
    T_Q,
    T_STR,
    DictTypeEnv,
    ExprError,
    infer,
    parse_expr,
    referenced_names,
    field_refs,
)
from ..model_ir.index import ModelIndex, compute_model_hash, param_type, state_type
from ..model_ir.schemas import (
    DecisionWorldModel,
    Distribution,
    EvidenceBundle,
    FidelityScorecard,
    MechanismSpec,
    ValidationCheck,
    ValidationReport,
)
from ..model_ir.units import UnitError, parse_unit
from ..research.planner import is_grounding
from ..temporal_kernel.operators import OPERATOR_RULES, OPERATOR_VERSIONS

BUILTIN_EVENT_PAYLOADS: dict[str, dict[str, T]] = {
    "governance_satisfied": {"mechanism": T_STR(), "option": T_ANY, "proposal_id": T_STR()},
}
RULE_SCHEDULES = {
    "subtract_flow": {"recurring", "on_event", "at"}, "add_flow": {"recurring", "on_event", "at"},
    "transfer": {"recurring", "on_event", "at"}, "queue_service": {"recurring", "on_event", "at"},
    "milestone_progress": {"recurring", "on_event", "at"}, "threshold_gate": {"watch"},
    "state_transition": {"recurring", "on_event", "at"}, "stochastic_draw": {"recurring", "on_event", "at"},
    "on_event": {"on_event"}, "wakeup": {"on_event"}, "governance": {"none"},
    "scheduled_effects": {"recurring", "at", "on_event"},
}


class CompileError(Exception):
    pass


def _compatible(target: T, value: T) -> bool:
    if value.kind == "any" or target.kind == "any":
        return True
    if target.kind != value.kind:
        return False
    if target.kind == "quantity":
        return value.zero_literal or target.unit.same_dimension(value.unit)
    if target.kind == "str" and target.values and value.values:
        return value.values <= target.values
    return True


class _Compiler:
    def __init__(self, model: DecisionWorldModel, bundle: EvidenceBundle | None, registry_status: dict[str, str],
                 high_stakes: bool):
        self.m = model
        self.idx = ModelIndex(model)
        self.bundle = bundle
        self.registry_status = registry_status
        self.high_stakes = high_stakes
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.checks: dict[str, ValidationCheck] = {}
        self.downgrades: list[dict[str, Any]] = []
        self.payload_types: dict[str, dict[str, T]] = {k: dict(v) for k, v in BUILTIN_EVENT_PAYLOADS.items()}
        self.event_producers: set[str] = {"governance_satisfied"}
        self.mech_writes: dict[str, set[str]] = {}
        self.mech_reads: dict[str, set[str]] = {}
        # ref -> [(writer, tier, deps)] where deps are the state refs / params that determine the written value
        self.write_deps: dict[str, list[tuple[str, Any, set[str]]]] = {}
        self.payload_reads: dict[str, dict[str, set[str]]] = {}
        self.delay_reads: dict[str, set[str]] = {}
        self.zero_edges: dict[str, set[str]] = {}
        self.base = self.idx.base_type_env()

    # ---------------- bookkeeping ----------------
    def check(self, cid: str, ok: bool, detail: str | None = None, warn: bool = False) -> None:
        c = self.checks.setdefault(cid, ValidationCheck(id=cid, status="pass"))
        if detail:
            c.details.append(detail)
        if not ok:
            if warn:
                if c.status == "pass":
                    c.status = "warn"
                self.warnings.append(f"[{cid}] {detail}")
            else:
                c.status = "fail"
                self.errors.append(f"[{cid}] {detail}")

    def infer(self, cid: str, expr: str, env: DictTypeEnv, where: str) -> T | None:
        try:
            parse_expr(expr)
            return infer(expr, env)
        except ExprError as e:
            self.check(cid, False, f"{where}: {e}")
            return None

    # ---------------- passes ----------------
    def run(self) -> None:
        self.identifiers()
        self.state_decls()
        self.params()
        self.operators()
        self.collect_emits()
        self.mechanisms()
        self.actions_agents()
        self.policies_scenarios()
        self.constraints_metrics()
        self.schedules()
        self.zero_time_cycles()
        self.evidence_and_tiers()
        self.registry_refs()
        self.initial_invariants()

    def identifiers(self) -> None:
        cid = "C03_identifiers"
        seen: dict[str, str] = {}
        groups = [("state", [s.ref for s in self.m.state]), ("parameter", [p.id for p in self.m.parameters]),
                  ("mechanism", [x.id for x in self.m.mechanisms]), ("action", [x.id for x in self.m.actions]),
                  ("agent", [x.id for x in self.m.agents]), ("policy", [x.id for x in self.m.policies]),
                  ("scenario", [x.id for x in self.m.scenarios]), ("metric", [x.id for x in self.m.metrics]),
                  ("constraint", [x.id for x in self.m.constraints])]
        for kind, ids in groups:
            dup = {i for i in ids if ids.count(i) > 1}
            for d in dup:
                self.check(cid, False, f"duplicate {kind} id {d!r}")
            for i in ids:
                if kind in ("mechanism", "action", "policy"):
                    if i in seen and seen[i] != kind:
                        self.check(cid, False, f"id {i!r} reused across {seen[i]} and {kind}")
                    seen.setdefault(i, kind)
        ents = set(self.idx.entities)
        for s in self.m.state:
            if "." not in s.ref:
                self.check(cid, False, f"state ref {s.ref!r} must be entity.var")
            elif s.ref.split(".")[0] not in ents:
                self.check(cid, False, f"state ref {s.ref!r} refers to undeclared entity", warn=True)
            if s.ref.split(".")[0] in ("param", "arg", "event", "gov", "draw", "horizon"):
                self.check(cid, False, f"state ref {s.ref!r} uses a reserved namespace")
        if not self.m.policies:
            self.check(cid, False, "model declares no decision policy")
        self.checks.setdefault(cid, ValidationCheck(id=cid, status="pass"))

    def state_decls(self) -> None:
        cid = "C04_state_declarations"
        for s in self.m.state:
            if s.type == "quantity":
                try:
                    parse_unit(s.unit)
                except UnitError as e:
                    self.check(cid, False, f"{s.ref}: {e}")
                if s.unit is None:
                    self.check(cid, False, f"{s.ref}: quantity without unit (use '1' for dimensionless)")
            if s.type == "enum" and not s.values:
                self.check(cid, False, f"{s.ref}: enum without values")
            v = s.initial_value
            if v in (None, "UNKNOWN"):
                if s.type != "ledger" and not s.allow_unknown:
                    self.check(cid, False, f"{s.ref}: initial value UNKNOWN but allow_unknown is false")
                continue
            if s.type == "quantity" and (not isinstance(v, (int, float)) or isinstance(v, bool)):
                self.check(cid, False, f"{s.ref}: initial value {v!r} is not numeric")
            elif s.type == "quantity" and ((s.min is not None and v < s.min) or (s.max is not None and v > s.max)):
                self.check(cid, False, f"{s.ref}: initial value {v} outside declared bounds")
            elif s.type == "boolean" and not isinstance(v, bool):
                self.check(cid, False, f"{s.ref}: initial value {v!r} is not boolean")
            elif s.type == "enum" and v not in (s.values or []):
                self.check(cid, False, f"{s.ref}: initial value {v!r} not in {s.values}")
            elif s.type == "timestamp":
                try:
                    parse_dt(v)
                except (ValueError, TypeError) as e:
                    self.check(cid, False, f"{s.ref}: {e}")
            if s.origin == OriginKind.SIMULATION_OUTPUT:
                self.check(cid, False, f"{s.ref}: SIMULATION_OUTPUT cannot initialise a world model")
        self.checks.setdefault(cid, ValidationCheck(id=cid, status="pass"))

    def params(self) -> None:
        cid = "C04b_parameters"
        for p in self.m.parameters:
            if p.type == "quantity":
                try:
                    parse_unit(p.unit)
                except UnitError as e:
                    self.check(cid, False, f"param {p.id}: {e}")
                if p.value not in (None, "UNKNOWN") and not isinstance(p.value, (int, float)):
                    self.check(cid, False, f"param {p.id}: value {p.value!r} not numeric")
                if p.range and (len(p.range) != 2 or p.range[0] > p.range[1]):
                    self.check(cid, False, f"param {p.id}: invalid range {p.range}")
            if p.type == "distribution":
                if p.distribution is None:
                    self.check(cid, False, f"param {p.id}: distribution parameter without distribution")
                else:
                    self._check_dist(cid, p.distribution, f"param {p.id}")
                for sid, v in p.scenario_values.items():
                    try:
                        self._check_dist(cid, Distribution.model_validate(v), f"param {p.id}[{sid}]")
                    except ValidationError as e:
                        self.check(cid, False, f"param {p.id}[{sid}]: {e.errors()[0]['msg']}")
            for sid in p.scenario_values:
                if sid not in self.idx.scenarios:
                    self.check(cid, False, f"param {p.id}: scenario_values for unknown scenario {sid}")
            if p.source_kind == "declared_exploratory_range" and p.epistemic_tier in (FidelityTier.A_GROUNDED, FidelityTier.B_ASSUMPTION_BOUND):
                self._downgrade("parameter", p, FidelityTier.C_ILLUSTRATIVE, "declared exploratory ranges are illustrative")
        self.checks.setdefault(cid, ValidationCheck(id=cid, status="pass"))

    def _check_dist(self, cid: str, d: Distribution, where: str) -> None:
        k = d.kind
        ok = True
        if k == "bernoulli":
            ok = d.p is not None and 0 <= d.p <= 1
        elif k == "uniform":
            ok = d.low is not None and d.high is not None and d.low <= d.high
        elif k == "triangular":
            ok = None not in (d.low, d.high, d.mode) and d.low <= d.mode <= d.high
        elif k == "normal":
            ok = d.mean is not None and d.sd is not None and d.sd > 0
        elif k == "choice":
            ok = bool(d.options) and all("value" in o and float(o.get("weight", 1)) >= 0 for o in d.options)
        if not ok:
            self.check(cid, False, f"{where}: invalid {k} distribution parameters")
        if k in ("uniform", "triangular", "normal") and d.unit:
            try:
                parse_unit(d.unit)
            except UnitError as e:
                self.check(cid, False, f"{where}: {e}")

    def operators(self) -> None:
        cid = "C02_operator_allowlist"
        for mech in self.m.mechanisms:
            allowed = OPERATOR_RULES.get(mech.operator)
            if allowed is None:
                self.check(cid, False, f"{mech.id}: operator {mech.operator!r} is not on the allowlist {sorted(OPERATOR_RULES)}")
                continue
            if mech.rule.kind not in allowed:
                self.check(cid, False, f"{mech.id}: rule {mech.rule.kind} not permitted for operator {mech.operator}")
            if mech.operator_version != OPERATOR_VERSIONS[mech.operator]:
                self.check(cid, False, f"{mech.id}: operator version {mech.operator_version} unknown")
        self.checks.setdefault(cid, ValidationCheck(id=cid, status="pass"))

    # ---------------- expression contexts ----------------
    def _effect_lists(self):
        """Yield (where, effects, env-builder-kind, extra) for every effect list in the model."""
        for mech in self.m.mechanisms:
            r = mech.rule
            for name in ("on_complete", "on_trigger", "on_true", "on_false", "effects", "on_satisfied"):
                if hasattr(r, name):
                    yield f"mechanism {mech.id}.{name}", getattr(r, name), ("mech", mech)
            if r.kind == "state_transition":
                for i, e in enumerate(r.transitions):
                    yield f"mechanism {mech.id}.transitions[{i}]", e.effects, ("mech", mech)
            if r.kind == "stochastic_draw":
                for k, effs in r.outcomes.items():
                    yield f"mechanism {mech.id}.outcomes[{k}]", effs, ("mech", mech)
        for a in self.m.actions:
            yield f"action {a.id}", a.effects, ("action", a)
        for p in self.m.policies:
            yield f"policy {p.id}.initial_effects", p.initial_effects, ("base", None)
            for i, se in enumerate(p.scheduled_effects):
                yield f"policy {p.id}.scheduled_effects[{i}]", se.effects, ("base", None)
        for s in self.m.scenarios:
            for i, xe in enumerate(s.exogenous_events):
                yield f"scenario {s.id}.exogenous_events[{i}]", xe.effects, ("base", None)

    def _env(self, kind_obj, include_event: bool = True) -> DictTypeEnv:
        kind, obj = kind_obj
        if kind == "action":
            return self.idx.action_type_env(obj.id, self.base)
        if kind == "mech":
            mech: MechanismSpec = obj
            types: dict[str, T] = {}
            r = mech.rule
            if r.kind == "governance":
                rule = r
                types["gov.option"] = T_STR(rule.option_values)
                types["gov.proposer"] = T_STR(rule.participants)
            if r.kind == "stochastic_draw":
                d = r.distribution
                if r.distribution_param and r.distribution_param in self.idx.params:
                    d = self.idx.params[r.distribution_param].distribution
                if d is not None:
                    types["draw.value"] = T_BOOL if d.kind == "bernoulli" else (
                        T_ANY if d.kind == "choice" else T_Q(d.unit))
            if include_event and mech.schedule.kind == "on_event":
                keysets = [self.payload_types.get(t, {}) for t in mech.schedule.event_types if not t.endswith("*")]
                if keysets:
                    common = set(keysets[0]).intersection(*[set(k) for k in keysets[1:]]) if len(keysets) > 1 else set(keysets[0])
                    for key in common:
                        types[f"event.{key}"] = keysets[0][key]
            return DictTypeEnv(types, self.base)
        return self.base

    def collect_emits(self) -> None:
        for where, effects, ko in self._effect_lists():
            env = self._env(ko, include_event=False)
            for eff in effects:
                if eff.op != "emit":
                    continue
                pr = self.payload_reads.setdefault(eff.event_type, {})
                for key, expr in eff.payload.items():
                    pr.setdefault(key, set()).update(self._names(expr))
                if not eff.delay.strip().startswith("P"):
                    self.delay_reads.setdefault(eff.event_type, set()).update(self._names(eff.delay))
                self.event_producers.add(eff.event_type)
                pt = self.payload_types.setdefault(eff.event_type, {})
                for key, expr in eff.payload.items():
                    try:
                        t = infer(expr, env)
                    except ExprError:
                        t = T_ANY
                    if key in pt and not (_compatible(pt[key], t) or _compatible(t, pt[key])):
                        self.check("C05_expressions", False, f"event {eff.event_type}.{key} emitted with inconsistent types")
                    pt.setdefault(key, t)
        for s in self.m.scenarios:
            for xe in s.exogenous_events:
                self.event_producers.add(xe.event_type)
        for p in self.m.policies:
            for se in p.scheduled_effects:
                self.event_producers.add(se.event_type)
        for c in self.m.constraints:
            if c.on_violation.action == "emit" and c.on_violation.event_type:
                self.event_producers.add(c.on_violation.event_type)

    def _names(self, expr: str | None) -> set[str]:
        if not expr:
            return set()
        try:
            names = referenced_names(expr)
        except ExprError:
            return set()
        out = {n for n in names if n in self.idx.state or n.startswith("param.") or n.startswith("event.")}
        try:
            # field(entity, 'var') may read that var on any entity: include every declared <entity>.<var>
            for _ent, var in field_refs(expr):
                out |= {ref for ref in self.idx.state if ref.endswith("." + var)}
        except ExprError:
            pass
        if "now" in names:
            out.add("now")
        return out

    def _resolve_deps(self, names: set[str], event_types: list[str]) -> set[str]:
        out = set()
        for n in names:
            if n.startswith("event."):
                key = n[6:]
                for t in event_types:
                    out |= {x for x in self.payload_reads.get(t, {}).get(key, set()) if not x.startswith("event.")}
            elif n == "now":
                for t in event_types:  # timing of the triggering event depends on its delay inputs
                    out |= {x for x in self.delay_reads.get(t, set()) if not x.startswith("event.")}
            else:
                out.add(n)
        return out

    def record_write(self, ref: str, writer: str, tier, deps: set[str]) -> None:
        self.write_deps.setdefault(ref, []).append((writer, tier, deps))

    def effects(self, where: str, effects: list, env: DictTypeEnv, writes: set[str], emits_zero: set[str],
                writer: tuple[str, Any, list[str]] | None = None) -> None:
        cid = "C05_expressions"
        for eff in effects:
            if eff.op in ("set", "inc", "dec"):
                sv = self.idx.state.get(eff.ref)
                if sv is None:
                    self.check(cid, False, f"{where}: write to undeclared state {eff.ref!r}")
                    continue
                if sv.type == "ledger":
                    self.check(cid, False, f"{where}: ledger {eff.ref} may only be written by governance")
                    continue
                writes.add(eff.ref)
                if writer is not None:
                    names = self._names(eff.value if eff.op == "set" else eff.by) | self._names(eff.when)
                    if eff.op != "set":
                        names.add(eff.ref)
                    self.record_write(eff.ref, writer[0], writer[1], self._resolve_deps(names, writer[2]))
                target = state_type(sv)
                expr = eff.value if eff.op == "set" else eff.by
                t = self.infer(cid, expr, env, where)
                if t is None:
                    continue
                if eff.op != "set" and sv.type != "quantity":
                    self.check(cid, False, f"{where}: {eff.op} on non-quantity {eff.ref}")
                elif not _compatible(target, t):
                    self.check(cid, False, f"{where}: {eff.op} {eff.ref} expects {target.describe()}, got {t.describe()}")
                if eff.op == "set" and expr.strip() == "UNKNOWN" and not sv.allow_unknown:
                    self.check(cid, False, f"{where}: sets {eff.ref} to UNKNOWN but allow_unknown is false")
                self._bool(cid, eff.when, env, f"{where} {eff.op} {eff.ref} when")
            elif eff.op == "emit":
                dl = eff.delay.strip()
                if dl.startswith("P") or dl.startswith("-P"):
                    try:
                        d = Duration.parse(dl)
                        if d.is_negative:
                            self.check(cid, False, f"{where}: negative delay {eff.delay}")
                        if d.is_zero:
                            emits_zero.add(eff.event_type)
                    except ValueError as e:
                        self.check(cid, False, f"{where}: {e}")
                else:
                    t = self.infer(cid, dl, env, f"{where} delay")
                    if t is not None and t.kind not in ("any",) and not (t.kind == "quantity" and t.unit.same_dimension(parse_unit("second"))):
                        self.check(cid, False, f"{where}: delay expression must be a time quantity, got {t.describe()}")
                for key, expr in eff.payload.items():
                    self.infer(cid, expr, env, f"{where} payload {key}")
                if eff.when:
                    t = self.infer(cid, eff.when, env, f"{where} when")
                    if t and t.kind not in ("bool", "any"):
                        self.check(cid, False, f"{where}: emit.when must be boolean")
            elif eff.op == "terminate":
                if eff.when:
                    t = self.infer(cid, eff.when, env, f"{where} when")
                    if t and t.kind not in ("bool", "any"):
                        self.check(cid, False, f"{where}: terminate.when must be boolean")
            elif eff.op == "wake":
                for a in eff.agents:
                    if a != "*" and a not in self.idx.agents:
                        self.check(cid, False, f"{where}: wake of unknown agent {a}")
                try:
                    Duration.parse(eff.delay)
                except ValueError as e:
                    self.check(cid, False, f"{where}: {e}")

    def _bool(self, cid: str, expr: str | None, env: DictTypeEnv, where: str) -> None:
        if expr is None:
            return
        t = self.infer(cid, expr, env, where)
        if t is not None and t.kind not in ("bool", "any"):
            self.check(cid, False, f"{where}: expected boolean, got {t.describe()}")

    def _quantity_ref(self, cid: str, ref: str, where: str):
        sv = self.idx.state.get(ref)
        if sv is None:
            self.check(cid, False, f"{where}: unknown state {ref!r}")
            return None
        if sv.type != "quantity":
            self.check(cid, False, f"{where}: {ref} must be a quantity")
            return None
        return sv

    def mechanisms(self) -> None:
        cid = "C05_expressions"
        for mech in self.m.mechanisms:
            r = mech.rule
            env = self._env(("mech", mech))
            writes: set[str] = set()
            emits_zero: set[str] = set()
            where = f"mechanism {mech.id}"
            k = r.kind
            evts = list(mech.schedule.event_types) if mech.schedule.kind == "on_event" else []
            wr = (f"mechanism:{mech.id}", mech.epistemic_tier, evts)
            self._cur_writer = wr
            if k in ("subtract_flow", "add_flow"):
                sv = self._quantity_ref(cid, r.target, where)
                t = self.infer(cid, r.rate_ref, env, f"{where} rate")
                if sv and t and t.kind == "quantity":
                    want = parse_unit(sv.unit) / parse_unit(r.interval_unit)
                    if not t.unit.same_dimension(want):
                        self.check(cid, False, f"{where}: rate unit {t.unit.label} incompatible with {sv.unit}/{r.interval_unit}")
                writes.add(r.target)
            elif k == "transfer":
                a, b = self._quantity_ref(cid, r.source, where), self._quantity_ref(cid, r.sink, where)
                t = self.infer(cid, r.amount, env, f"{where} amount")
                if a and b and not parse_unit(a.unit).same_dimension(parse_unit(b.unit)):
                    self.check(cid, False, f"{where}: transfer between incompatible units")
                if a and t and not _compatible(T_Q(a.unit), t):
                    self.check(cid, False, f"{where}: amount unit incompatible with {a.unit}")
                writes |= {r.source, r.sink}
            elif k == "queue_service":
                q, s = self._quantity_ref(cid, r.queue_ref, where), self._quantity_ref(cid, r.served_ref, where)
                t = self.infer(cid, r.capacity, env, f"{where} capacity")
                if q and t and t.kind == "quantity" and not t.unit.same_dimension(parse_unit(q.unit) / parse_unit(r.interval_unit)):
                    self.check(cid, False, f"{where}: capacity unit must be {q.unit}/{r.interval_unit}")
                writes |= {r.queue_ref, r.served_ref}
            elif k == "milestone_progress":
                p = self._quantity_ref(cid, r.progress_ref, where)
                t = self.infer(cid, r.rate, env, f"{where} rate")
                req = self.infer(cid, r.required, env, f"{where} required")
                if p and t and t.kind == "quantity" and not t.unit.same_dimension(parse_unit(p.unit) / parse_unit(r.interval_unit)):
                    self.check(cid, False, f"{where}: rate unit must be {p.unit}/{r.interval_unit}")
                if p and req and not _compatible(T_Q(p.unit), req):
                    self.check(cid, False, f"{where}: required incompatible with progress unit")
                d = self.idx.state.get(r.done_ref)
                if d is None or d.type != "boolean":
                    self.check(cid, False, f"{where}: done_ref {r.done_ref} must be a boolean state")
                writes |= {r.progress_ref, r.done_ref}
                self.effects(f"{where}.on_complete", r.on_complete, env, writes, emits_zero, wr)
            elif k == "threshold_gate":
                cmp = {"lt": "<", "lte": "<=", "gt": ">", "gte": ">=", "eq": "==", "neq": "!="}[r.comparator]
                self.infer(cid, f"({r.target}) {cmp} ({r.bound_ref})", env, f"{where} condition")
                self.effects(f"{where}.on_trigger", r.on_trigger, env, writes, emits_zero, wr)
            elif k == "state_transition":
                sv = self.idx.state.get(r.state_ref)
                if sv is None or sv.type != "enum":
                    self.check(cid, False, f"{where}: state_ref {r.state_ref} must be an enum")
                else:
                    for e in r.transitions:
                        if e.from_state != "*" and e.from_state not in sv.values:
                            self.check(cid, False, f"{where}: from-state {e.from_state!r} undeclared")
                        if e.to not in sv.values:
                            self.check(cid, False, f"{where}: to-state {e.to!r} undeclared")
                        self._bool(cid, e.when, env, f"{where} edge when")
                        self.effects(f"{where} edge", e.effects, env, writes, emits_zero, wr)
                writes.add(r.state_ref)
            elif k == "stochastic_draw":
                if (r.distribution is None) == (r.distribution_param is None):
                    self.check(cid, False, f"{where}: exactly one of distribution / distribution_param required")
                if r.distribution:
                    self._check_dist(cid, r.distribution, where)
                if r.distribution_param:
                    p = self.idx.params.get(r.distribution_param)
                    if p is None or p.type != "distribution":
                        self.check(cid, False, f"{where}: distribution_param {r.distribution_param} is not a distribution parameter")
                if r.target:
                    sv = self.idx.state.get(r.target)
                    dt = env.types.get("draw.value", T_ANY)
                    if sv is None or not _compatible(state_type(sv), dt):
                        self.check(cid, False, f"{where}: draw target {r.target} incompatible")
                    writes.add(r.target)
                self._bool(cid, r.when, env, f"{where} when")
                for name, effs in (("on_true", r.on_true), ("on_false", r.on_false), *r.outcomes.items()):
                    self.effects(f"{where}.{name}", effs, env, writes, emits_zero, wr)
            elif k in ("on_event", "scheduled_effects"):
                self._bool(cid, r.when, env, f"{where} when")
                self.effects(f"{where}.effects", r.effects, env, writes, emits_zero, wr)
            elif k == "wakeup":
                for a in r.agents:
                    if a != "*" and a not in self.idx.agents:
                        self.check(cid, False, f"{where}: unknown agent {a}")
                try:
                    Duration.parse(r.latency)
                except ValueError as e:
                    self.check(cid, False, f"{where}: {e}")
                self._bool(cid, r.when, env, f"{where} when")
            elif k == "governance":
                for a in r.participants + r.signatories:
                    if a not in self.idx.agents:
                        self.check(cid, False, f"{where}: unknown agent {a}")
                if not set(r.signatories) <= set(r.participants):
                    self.check(cid, False, f"{where}: signatories must be participants")
                if r.decision_rule == "quorum" and not (r.quorum and 0 < r.quorum <= len(r.participants)):
                    self.check(cid, False, f"{where}: invalid quorum")
                lsv = self.idx.state.get(r.ledger_ref)
                if lsv is None or lsv.type != "ledger":
                    self.check(cid, False, f"{where}: ledger_ref {r.ledger_ref} must be a ledger state")
                osv = self.idx.state.get(r.outcome_ref)
                if osv is None or osv.type != "enum" or not set(r.option_values) <= set(osv.values or []):
                    self.check(cid, False, f"{where}: outcome_ref must be an enum containing every option value")
                elif not osv.allow_unknown and osv.initial_value in (None, "UNKNOWN"):
                    self.check(cid, False, f"{where}: outcome_ref needs allow_unknown before agreement")
                writes |= {r.ledger_ref, r.outcome_ref}
                self.effects(f"{where}.on_satisfied", r.on_satisfied, env, writes, emits_zero, wr)
                emits_zero.add("governance_satisfied")
            rule_deps: dict[str, set[str]] = {}
            if k in ("subtract_flow", "add_flow"):
                rule_deps[r.target] = self._names(r.rate_ref) | {r.target}
            elif k == "transfer":
                rule_deps[r.source] = rule_deps[r.sink] = self._names(r.amount) | {r.source, r.sink}
            elif k == "queue_service":
                rule_deps[r.queue_ref] = rule_deps[r.served_ref] = self._names(r.capacity) | {r.queue_ref, r.served_ref}
            elif k == "milestone_progress":
                rule_deps[r.progress_ref] = self._names(r.rate) | {r.progress_ref}
                rule_deps[r.done_ref] = self._names(r.rate) | self._names(r.required) | {r.progress_ref}
            elif k == "stochastic_draw" and r.target:
                rule_deps[r.target] = ({f"param.{r.distribution_param}"} if r.distribution_param else set()) | self._names(r.when)
            elif k == "state_transition":
                rule_deps[r.state_ref] = {r.state_ref} | {n for e in r.transitions for n in self._names(e.when)}
            elif k == "governance":
                rule_deps[r.outcome_ref] = set()
            for ref, deps in rule_deps.items():
                self.record_write(ref, wr[0], wr[1], self._resolve_deps(deps, evts))
            # dependency closure (§14.3 "closure over all referenced state variables")
            reads = set()
            for expr in self._rule_exprs(mech):
                try:
                    reads |= {n for n in referenced_names(expr) if n in self.idx.state or n.startswith("param.")}
                except ExprError:
                    pass
            self.mech_reads[mech.id] = reads
            self.mech_writes[mech.id] = writes
            if mech.output_refs:
                undeclared = writes - set(mech.output_refs)
                if undeclared:
                    self.check("C06_dependency_closure", False, f"{mech.id}: writes {sorted(undeclared)} not declared in output_refs")
            else:
                mech.output_refs = sorted(writes)
                if writes:
                    self.check("C06_dependency_closure", False, f"{mech.id}: output_refs computed by compiler", warn=True)
            if not mech.inputs:
                mech.inputs = sorted(reads)
            if mech.schedule.kind == "on_event":
                for t in mech.schedule.event_types:
                    self.zero_edges.setdefault(t, set()).update(emits_zero)
        self.checks.setdefault("C05_expressions", ValidationCheck(id="C05_expressions", status="pass"))
        self.checks.setdefault("C06_dependency_closure", ValidationCheck(id="C06_dependency_closure", status="pass"))

    def _rule_exprs(self, mech: MechanismSpec) -> list[str]:
        r = mech.rule
        out: list[str] = []
        for name in ("rate_ref", "amount", "capacity", "rate", "required", "target", "bound_ref", "when"):
            v = getattr(r, name, None)
            if isinstance(v, str) and not (name == "target" and r.kind in ("subtract_flow", "add_flow", "stochastic_draw")):
                out.append(v)
        effect_lists = [getattr(r, n) for n in ("on_complete", "on_trigger", "on_true", "on_false", "effects", "on_satisfied") if hasattr(r, n)]
        if r.kind == "state_transition":
            effect_lists += [e.effects for e in r.transitions]
            out += [e.when for e in r.transitions if e.when]
        if r.kind == "stochastic_draw":
            effect_lists += list(r.outcomes.values())
        if r.kind in ("subtract_flow", "add_flow"):
            out.append(r.target)
        for effs in effect_lists:
            for e in effs:
                for attr in ("value", "by", "when"):
                    v = getattr(e, attr, None)
                    if isinstance(v, str):
                        out.append(v)
                if e.op == "emit":
                    out += list(e.payload.values())
                    if not e.delay.strip().startswith("P"):
                        out.append(e.delay)
        return out

    def actions_agents(self) -> None:
        cid = "C08_agents_actions"
        roles = {a.role for a in self.m.agents}
        for a in self.m.agents:
            for t in a.initial_wakeups:
                try:
                    resolve_time(t, self.idx.horizon_start)
                except ValueError as e:
                    self.check(cid, False, f"agent {a.id}: {e}")
            if a.periodic_review:
                try:
                    d = Duration.parse(a.periodic_review)
                    if d.is_zero or d.is_negative:
                        self.check(cid, False, f"agent {a.id}: periodic review must be positive")
                except ValueError as e:
                    self.check(cid, False, f"agent {a.id}: {e}")
        for act in self.m.actions:
            where = f"action {act.id}"
            for actor in act.actors:
                if actor == "*" or actor in self.idx.agents or (actor.startswith("role:") and actor[5:] in roles):
                    continue
                self.check(cid, False, f"{where}: unknown actor {actor}")
            env = self.idx.action_type_env(act.id, self.base)
            for a in act.args:
                if a.type == "enum" and not a.values:
                    self.check(cid, False, f"{where}: enum arg {a.name} without values")
                if a.type == "number":
                    try:
                        parse_unit(a.unit)
                    except UnitError as e:
                        self.check(cid, False, f"{where}: {e}")
            for p in act.preconditions:
                self._bool(cid, p.expr, env, f"{where} precondition {p.id}")
            for dim, expr in act.appraisal.items():
                self.infer(cid, expr, env, f"{where} appraisal {dim}")
            writes: set[str] = set()
            self.effects(where, act.effects, env, writes, set(), (f"action:{act.id}", act.epistemic_tier, []))
            if act.governance:
                g = act.governance
                mech = self.idx.mechanisms.get(g.mechanism)
                if mech is None or mech.rule.kind != "governance":
                    self.check(cid, False, f"{where}: governance mechanism {g.mechanism} missing")
                elif g.act == "propose":
                    arg = next((x for x in act.args if x.name == g.option_arg), None)
                    if arg is None or arg.type != "enum" or not set(arg.values or []) <= set(mech.rule.option_values):
                        self.check(cid, False, f"{where}: propose needs an enum option_arg within the option values")
        self.checks.setdefault(cid, ValidationCheck(id=cid, status="pass"))

    def policies_scenarios(self) -> None:
        cid = "C05_expressions"
        for p in self.m.policies:
            self.effects(f"policy {p.id}", p.initial_effects, self.base, set(), set(), (f"policy:{p.id}", FidelityTier.B_ASSUMPTION_BOUND, []))
            for i, se in enumerate(p.scheduled_effects):
                self._time_in_horizon("C07_schedules", se.at, f"policy {p.id} scheduled[{i}]")
                self.effects(f"policy {p.id} scheduled[{i}]", se.effects, self.base, set(), set())
            for w in p.action_windows:
                if w.action not in self.idx.actions:
                    self.check(cid, False, f"policy {p.id}: window for unknown action {w.action}")
                for t in (w.available_from, w.available_until):
                    if t:
                        self._time_in_horizon("C07_schedules", t, f"policy {p.id} window")
                self._bool(cid, w.when, self.base, f"policy {p.id} window when")
            for a in p.forbidden_actions:
                if a not in self.idx.actions:
                    self.check(cid, False, f"policy {p.id}: forbids unknown action {a}")
        for s in self.m.scenarios:
            for pid, v in s.parameter_overrides.items():
                p = self.idx.params.get(pid)
                if p is None:
                    self.check(cid, False, f"scenario {s.id}: overrides unknown parameter {pid}")
                elif p.type == "quantity" and not isinstance(v, (int, float)):
                    self.check(cid, False, f"scenario {s.id}: override {pid}={v!r} not numeric")
                elif p.type == "distribution":
                    try:
                        self._check_dist(cid, Distribution.model_validate(v), f"scenario {s.id} {pid}")
                    except ValidationError as e:
                        self.check(cid, False, f"scenario {s.id} {pid}: {e.errors()[0]['msg']}")
            for ref, v in s.state_overrides.items():
                sv = self.idx.state.get(ref)
                if sv is None:
                    self.check(cid, False, f"scenario {s.id}: overrides unknown state {ref}")
                elif sv.type == "enum" and v not in (sv.values or []) and v != "UNKNOWN":
                    self.check(cid, False, f"scenario {s.id}: {ref}={v!r} not in {sv.values}")
            for i, xe in enumerate(s.exogenous_events):
                self._time_in_horizon("C07_schedules", xe.at, f"scenario {s.id} event[{i}]")
            if s.probability is not None:
                ok = bool(s.probability_basis) and self.bundle is not None and all(
                    (c := self.bundle.claim(ref)) is not None and is_grounding(c) for ref in s.probability_basis)
                if not ok:
                    self.check("C09_evidence", False,
                               f"scenario {s.id}: probability {s.probability} asserted without independently estimable grounding; "
                               "scenarios must stay unweighted (§18.3)")

    def constraints_metrics(self) -> None:
        cid = "C05_expressions"
        for c in self.m.constraints:
            if c.type in ("guarded_floor", "guarded_ceiling"):
                self._quantity_ref(cid, c.path or "", f"constraint {c.id}")
                if (c.type == "guarded_floor" and c.min is None) or (c.type == "guarded_ceiling" and c.max is None):
                    self.check(cid, False, f"constraint {c.id}: missing bound")
            else:
                self._bool(cid, c.expr or "", self.base, f"constraint {c.id}")
            if c.on_violation.action == "emit" and not c.on_violation.event_type:
                self.check(cid, False, f"constraint {c.id}: emit violation policy needs event_type")
        for mt in self.m.metrics:
            if not mt.computable:
                if not mt.reason:
                    self.check(cid, False, f"metric {mt.id}: non-computable metrics must state a reason")
                continue
            if not mt.expr:
                self.check(cid, False, f"metric {mt.id}: computable metric without expression")
                continue
            t = self.infer(cid, mt.expr, self.base, f"metric {mt.id}")
            if t and mt.unit and t.kind == "quantity" and not t.unit.same_dimension(parse_unit(mt.unit)):
                self.check(cid, False, f"metric {mt.id}: expression unit {t.unit.label} incompatible with {mt.unit}")

    def _time_in_horizon(self, cid: str, spec: str, where: str) -> None:
        try:
            t = resolve_time(spec, self.idx.horizon_start)
        except ValueError as e:
            self.check(cid, False, f"{where}: {e}")
            return
        if t < self.idx.horizon_start or t > self.idx.horizon_end:
            self.check(cid, False, f"{where}: time {t.isoformat()} outside the simulation horizon", warn=True)

    def schedules(self) -> None:
        cid = "C07_schedules"
        if self.idx.horizon_end <= self.idx.horizon_start:
            self.check(cid, False, "horizon end must be after start (nonzero simulated-time advance, §1.3)")
        for mech in self.m.mechanisms:
            s = mech.schedule
            where = f"mechanism {mech.id}"
            if s.kind not in RULE_SCHEDULES.get(mech.rule.kind, set()):
                self.check(cid, False, f"{where}: schedule {s.kind} not valid for rule {mech.rule.kind}")
            if s.kind == "recurring":
                try:
                    d = Duration.parse(s.interval or "")
                    if d.is_zero or d.is_negative:
                        self.check(cid, False, f"{where}: recurring interval must be positive")
                except ValueError as e:
                    self.check(cid, False, f"{where}: {e}")
                for t in (s.start, s.end):
                    if t:
                        self._time_in_horizon(cid, t, where)
            elif s.kind == "at":
                if not s.times:
                    self.check(cid, False, f"{where}: 'at' schedule without times")
                for t in s.times:
                    self._time_in_horizon(cid, t, where)
            elif s.kind == "on_event":
                if not s.event_types:
                    self.check(cid, False, f"{where}: on_event schedule without event types")
                for t in s.event_types:
                    if t.endswith("*") or t.startswith("action:") or t.startswith("tick:") or t.startswith("gate:"):
                        continue
                    if t not in self.event_producers:
                        self.check(cid, False, f"{where}: no producer for event type {t!r}", warn=True)
        self.checks.setdefault(cid, ValidationCheck(id=cid, status="pass"))

    def zero_time_cycles(self) -> None:
        cid = "C07b_zero_time_cycles"
        graph = self.zero_edges
        state: dict[str, int] = {}

        def visit(n: str, path: list[str]) -> None:
            state[n] = 1
            for m in graph.get(n, ()):
                if state.get(m) == 1:
                    self.check(cid, False, f"zero-delay event loop: {' -> '.join(path + [n, m])}")
                elif state.get(m) is None:
                    visit(m, path + [n])
            state[n] = 2

        for n in list(graph):
            if state.get(n) is None:
                visit(n, [])
        self.checks.setdefault(cid, ValidationCheck(id=cid, status="pass"))

    # ---------------- evidence, tiers, registry ----------------
    def _resolve(self, ref: str):
        if self.bundle is not None:
            c = self.bundle.claim(ref)
            if c is not None:
                return ("claim", c)
        if ref in self.registry_status:
            return ("registry", self.registry_status[ref])
        return None

    def _downgrade(self, kind: str, obj, to: FidelityTier, why: str) -> None:
        cur = obj.epistemic_tier
        if TIER_ORDER.index(to) <= TIER_ORDER.index(cur):
            return
        self.downgrades.append({"kind": kind, "id": getattr(obj, "ref", None) or getattr(obj, "id", None),
                                "from": str(cur), "to": str(to), "reason": why})
        obj.epistemic_tier = to

    def evidence_and_tiers(self) -> None:
        cid = "C09_evidence"
        man = self.m.epistemic_manifest
        man.stale_evidence, man.conflicted_evidence = [], []
        pins: set[str] = set()
        items = [("state", s) for s in self.m.state] + [("parameter", p) for p in self.m.parameters] + \
                [("mechanism", x) for x in self.m.mechanisms] + [("action", a) for a in self.m.actions]
        for kind, obj in items:
            name = getattr(obj, "ref", None) or obj.id
            origin = getattr(obj, "origin", None)
            if origin == OriginKind.SIMULATION_OUTPUT:
                self.check(cid, False, f"{kind} {name}: SIMULATION_OUTPUT can never ground a model element")
            grounded = bool(obj.evidence_refs)
            for ref in obj.evidence_refs:
                res = self._resolve(ref)
                if res is None:
                    self.check(cid, False, f"{kind} {name}: dangling evidence reference {ref!r}")
                    grounded = False
                    continue
                if res[0] == "claim":
                    c = res[1]
                    pins.add(c.claim_id)
                    if c.origin_kind == OriginKind.SIMULATION_OUTPUT:
                        self.check(cid, False, f"{kind} {name}: cites simulated content {ref}")
                    if c.status == "STALE":
                        man.stale_evidence.append(ref)
                        self._downgrade(kind, obj, FidelityTier.B_ASSUMPTION_BOUND, f"evidence {ref} is stale")
                    if c.status == "CONFLICTED":
                        man.conflicted_evidence.append(ref)
                        self._downgrade(kind, obj, FidelityTier.C_ILLUSTRATIVE, f"evidence {ref} is in unresolved conflict")
                    if not is_grounding(c):
                        grounded = False
                else:
                    if res[1] != "A_VALIDATED":
                        grounded = False
            if kind == "mechanism" and obj.registry_ref:
                ref = f"{obj.registry_ref.get('entry_id')}@{obj.registry_ref.get('version')}"
                if self.registry_status.get(ref) == "A_VALIDATED":
                    grounded = grounded or not obj.evidence_refs or all(
                        (r := self._resolve(e)) is not None and (r[0] == "registry" or r[1].origin_kind in (OriginKind.USER_ASSERTED,) or is_grounding(r[1]))
                        for e in obj.evidence_refs)
            if kind == "state" and origin == OriginKind.DERIVED_CALCULATION and not obj.evidence_refs:
                grounded = True  # simulation bookkeeping: its fidelity comes from the writers in the dependency graph
            if obj.epistemic_tier == FidelityTier.A_GROUNDED and not grounded:
                to = FidelityTier.B_ASSUMPTION_BOUND if origin in (OriginKind.USER_ASSERTED, OriginKind.DERIVED_CALCULATION, None) or obj.evidence_refs else FidelityTier.C_ILLUSTRATIVE
                self._downgrade(kind, obj, to, "declared A_GROUNDED without verified external evidence or a Layer A mechanism")
            if origin == OriginKind.MODEL_HYPOTHESIS and not getattr(obj, "assumptions", None) and kind == "mechanism":
                self._downgrade(kind, obj, FidelityTier.C_ILLUSTRATIVE, "model hypothesis without declared assumptions")
            if kind in ("state", "parameter"):
                getattr(man, "state" if kind == "state" else "parameters")[name] = {
                    "origin": str(origin), "tier": str(obj.epistemic_tier), "evidence_refs": list(obj.evidence_refs)}
            elif kind == "mechanism":
                man.mechanisms[name] = {"operator": obj.operator, "tier": str(obj.epistemic_tier), "origin": str(origin),
                                        "evidence_refs": list(obj.evidence_refs), "assumptions": list(obj.assumptions),
                                        "registry_ref": obj.registry_ref}
        for c in self.m.constraints:
            if c.validated:
                ok = bool(c.evidence_refs) and all(
                    (r := self._resolve(e)) is not None and (r[0] == "registry" or is_grounding(r[1]) or r[1].origin_kind in (OriginKind.USER_ASSERTED, OriginKind.HUMAN_FEEDBACK))
                    for e in c.evidence_refs)
                if not ok:
                    c.validated = False
                    self.downgrades.append({"kind": "constraint", "id": c.id, "from": "validated", "to": "unvalidated",
                                            "reason": "validated constraints need resolvable evidence citations"})
        man.evidence_pins = sorted(pins)
        man.evidence_bundle_hash = self.bundle.hash if self.bundle else None
        self.checks.setdefault(cid, ValidationCheck(id=cid, status="pass"))

    def registry_refs(self) -> None:
        cid = "C10_registry_transfer"
        for mech in self.m.mechanisms:
            if not mech.registry_ref:
                continue
            ref = f"{mech.registry_ref.get('entry_id')}@{mech.registry_ref.get('version')}"
            st = self.registry_status.get(ref)
            if st is None:
                self.check(cid, False, f"{mech.id}: registry entry {ref} not found")
            elif st in ("REVOKED", "QUARANTINED"):
                self.check(cid, False, f"{mech.id}: registry entry {ref} is {st}; it cannot be used in new models")
        for r in self.m.reuse_manifest:
            if r.get("used") and not r.get("transfer_ok", True):
                self.check(cid, False, f"reuse of {r.get('entry')} failed the transfer gate: {r.get('reasons')}")
        self.checks.setdefault(cid, ValidationCheck(id=cid, status="pass"))

    def initial_invariants(self) -> None:
        cid = "C11_initial_invariants"
        from ..model_ir.expr import UNKNOWN, evaluate, EvalError
        from ..model_ir.index import StateEnv, typed_param_value

        world = {s.ref: (s.initial_value if s.initial_value is not None else "UNKNOWN") for s in self.m.state}
        params = {p.id: typed_param_value(p, p.value) if p.type != "distribution" else p.distribution for p in self.m.parameters}
        env = StateEnv(self.idx, world, params, self.idx.horizon_start)
        for c in self.m.constraints:
            try:
                if c.type in ("guarded_floor", "guarded_ceiling"):
                    v = env.lookup(c.path)
                    if v is UNKNOWN:
                        continue
                    if (c.type == "guarded_floor" and v.value < c.min) or (c.type == "guarded_ceiling" and v.value > c.max):
                        self.check(cid, False, f"initial state violates constraint {c.id}")
                elif c.expr and evaluate(c.expr, env) is False:
                    self.check(cid, False, f"initial state violates constraint {c.id}")
            except (EvalError, AttributeError, TypeError) as e:
                self.check(cid, False, f"constraint {c.id} cannot be evaluated on the initial state: {e}")
        self.checks.setdefault(cid, ValidationCheck(id=cid, status="pass"))

    # ---------------- fidelity per metric ----------------
    def fidelity(self, report: ValidationReport) -> None:
        """Per-metric fidelity = weakest tier along the metric's write-dependency closure (§1.5:
        expose the weakest influential mechanisms; never average)."""
        report.mechanism_fidelity = {m.id: m.epistemic_tier for m in self.m.mechanisms}
        weakest_list = []
        for mt in self.m.metrics:
            if not mt.computable or not mt.expr:
                report.metric_fidelity[mt.id] = FidelityTier.X_LIMITED_OR_ABSTAIN
                report.non_computable_metrics[mt.id] = mt.reason or "no representable mechanism"
                continue
            roots = {n for n in self._names(mt.expr) if n != "now"}
            seen: set[str] = set()
            stack = list(roots)
            tiers: list[FidelityTier] = []
            contributors: set[str] = set()
            hypothesis = False
            while stack:
                n = stack.pop()
                if n in seen or n.startswith("event."):
                    continue
                seen.add(n)
                if n.startswith("param."):
                    p = self.idx.params.get(n[6:])
                    if p:
                        tiers.append(p.epistemic_tier)
                        contributors.add(f"{n}={p.epistemic_tier}")
                        hypothesis |= p.origin == OriginKind.MODEL_HYPOTHESIS
                    continue
                sv = self.idx.state.get(n)
                if sv is None:
                    continue
                tiers.append(sv.epistemic_tier)
                if sv.epistemic_tier != FidelityTier.A_GROUNDED:
                    contributors.add(f"state:{n}={sv.epistemic_tier}")
                for writer, wtier, deps in self.write_deps.get(n, []):
                    tiers.append(wtier)
                    contributors.add(f"{writer}={wtier}")
                    if writer.startswith("mechanism:"):
                        hypothesis |= self.idx.mechanisms[writer.split(":", 1)[1]].origin == OriginKind.MODEL_HYPOTHESIS
                    stack.extend(d for d in deps if d != "now")
            tier = weakest_tier(tiers) if tiers else FidelityTier.X_LIMITED_OR_ABSTAIN
            for su in self.m.structural_unknowns:
                if mt.id in su.affects_metrics and su.impact == "high" and su.status == "DISCOVERED_UNMODELED":
                    tier = weakest_tier([tier, FidelityTier.C_ILLUSTRATIVE])
                    contributors.add(f"structural_unknown:{su.id}")
            if self.high_stakes and (tier in (FidelityTier.C_ILLUSTRATIVE, FidelityTier.X_LIMITED_OR_ABSTAIN) or hypothesis):
                report.non_computable_metrics.setdefault(mt.id, "high-stakes decision: unverified structure supports conditional analysis only")
                tier = FidelityTier.X_LIMITED_OR_ABSTAIN
            report.metric_fidelity[mt.id] = tier
            report.metric_dependencies[mt.id] = sorted(contributors)
            for c in sorted(contributors):
                if c.endswith(str(FidelityTier.C_ILLUSTRATIVE)) or c.endswith(str(FidelityTier.X_LIMITED_OR_ABSTAIN)) or c.startswith("structural_unknown"):
                    weakest_list.append({"metric": mt.id, "element": c.rsplit("=", 1)[0]})
        report.weakest_influential = weakest_list


def _property_tests(model: DecisionWorldModel, comp: _Compiler) -> list[ValidationCheck]:
    """Smoke, metamorphic and counterfactual tests on the compiled model with mechanisms only
    (Agents always WAIT). These verify contract behaviour, never real-world validity (§15.1)."""
    from ..agent_runtime.contracts import WAIT, AgentDecision
    from ..model_ir.schemas import ActionIntent, ExecutionBudget, ModelRef, RunManifest, ScenarioSpec
    from ..temporal_kernel.kernel import Simulator
    from ..temporal_kernel.replay import state_at
    from ..temporal_kernel.store import InMemoryRunStore

    class _Wait:
        def estimate_tokens(self, agent_id):
            return 0

        def decide(self, ctx):
            return AgentDecision(intent=ActionIntent(run_id=ctx.run_id, agent_id=ctx.agent_id, observed_at=ctx.sim_now.isoformat(),
                                                     action_type=WAIT), policy_kind="property_test")

    checks = []
    m = model.model_copy(deep=True)
    budget = ExecutionBudget(max_events=160, max_agent_wakeups=40, max_wall_seconds=20)

    def run(policy_id, scenario_ids, mm=None, overrides=None):
        mm = mm or m
        mm.hash = compute_model_hash(mm)
        store = InMemoryRunStore()
        man = RunManifest(run_id=new_id("ptest"), decision_id=mm.decision_id, model_ref=ModelRef(model_id=mm.model_id, version=mm.model_version, hash=mm.hash),
                          policy_id=policy_id, scenario_ids=scenario_ids, execution_budget=budget, random_streams={"default": 7},
                          parameter_overrides=overrides or {})
        res = Simulator(mm, man, _Wait(), store, {}, enforce_wall=False).run()
        return res, store, man.run_id

    smoke = ValidationCheck(id="P01_smoke_runs", status="pass")
    combos = [(p.id, []) for p in m.policies[:4]] + [(m.policies[0].id, [s.id]) for s in m.scenarios[:4]] if m.policies else []
    for pid, sids in combos:
        res, _, _ = run(pid, sids)
        smoke.details.append(f"{pid}{sids}: {res.status} ({res.events_committed} events)")
        if res.status in (RunStatus.MODEL_EXECUTION_BLOCKED, RunStatus.FAILED):
            smoke.status = "fail"
            smoke.details.append(f"  reason: {res.reason}")
    checks.append(smoke)
    meta = ValidationCheck(id="P02_metamorphic_flows", status="pass")
    cf = ValidationCheck(id="P03_counterfactual_locality", status="pass")
    if smoke.status == "pass" and m.policies:
        for mech in m.mechanisms:
            r = mech.rule
            if r.kind != "subtract_flow":
                continue
            rate = r.rate_ref.strip()
            pid = m.policies[0].id
            base_res, base_store, base_id = run(pid, [])
            if rate.startswith("param."):
                p = comp.idx.params.get(rate[6:])
                if not p or not isinstance(p.value, (int, float)):
                    continue
                pert_res, pert_store, pert_id = run(pid, [], overrides={p.id: p.value * 1.25})
            elif rate in comp.idx.state and isinstance(comp.idx.state[rate].initial_value, (int, float)):
                mm = m.model_copy(deep=True)
                sid = "__metamorphic_probe__"
                mm.scenarios.append(ScenarioSpec(id=sid, label="probe", kind="model_alternative",
                                                 state_overrides={rate: comp.idx.state[rate].initial_value * 1.25}))
                pert_res, pert_store, pert_id = run(pid, [sid], mm=mm)
            else:
                continue
            be, pe = base_store.get_events(base_id), pert_store.get_events(pert_id)
            if not be or not pe:
                continue
            t_star = min(be[-1].sim_at, pe[-1].sim_at)
            bs = state_at(base_store.get_genesis(base_id)["snapshot"], be, max(e.seq for e in be if e.sim_at <= t_star))
            ps = state_at(pert_store.get_genesis(pert_id)["snapshot"], pe, max(e.seq for e in pe if e.sim_at <= t_star))
            bv, pv = bs.world.get(r.target), ps.world.get(r.target)
            if isinstance(bv, (int, float)) and isinstance(pv, (int, float)):
                ok = pv <= bv + 1e-9
                meta.details.append(f"{mech.id}: +25% outflow → {r.target} {pv:g} vs baseline {bv:g} at {t_star}")
                if not ok:
                    meta.status = "warn"
                    comp.warnings.append(f"[P02] larger outflow increased {r.target} in {mech.id}; review compensating mechanisms")
            # counterfactual locality: only refs downstream of the perturbed input may differ
            downstream = {rate}
            changed = True
            while changed:
                changed = False
                for mid, reads in comp.mech_reads.items():
                    if reads & downstream and not comp.mech_writes.get(mid, set()) <= downstream:
                        downstream |= comp.mech_writes.get(mid, set())
                        changed = True
            diff = {k for k in set(bs.world) | set(ps.world) if bs.world.get(k) != ps.world.get(k)}
            stray = sorted(k for k in diff - downstream if comp.idx.state.get(k) and comp.idx.state[k].type != "ledger")
            if stray:
                cf.status = "warn"
                cf.details.append(f"{mech.id}: perturbing {rate} also changed {stray} (check declared dependencies)")
            else:
                cf.details.append(f"{mech.id}: perturbing {rate} changed only declared downstream state")
    checks += [meta, cf]
    return checks


def compile_model(candidate: Any, bundle: EvidenceBundle | None, registry_status: dict[str, str] | None = None, *,
                  high_stakes: bool = False, property_tests: bool = True) -> tuple[DecisionWorldModel | None, ValidationReport]:
    report = ValidationReport(report_id=new_id("val"), passed=False)
    try:
        model = DecisionWorldModel.model_validate(candidate if isinstance(candidate, dict) else candidate.model_dump(mode="json", by_alias=True))
    except ValidationError as e:
        report.checks.append(ValidationCheck(id="C01_schema", status="fail",
                                             details=[f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()[:40]]))
        report.errors = [f"[C01_schema] {d}" for d in report.checks[0].details]
        report.scorecard.schema_validity = "fail"
        return None, report
    comp = _Compiler(model, bundle, registry_status or {}, high_stakes)
    comp.checks["C01_schema"] = ValidationCheck(id="C01_schema", status="pass", details=["strict schema (extra=forbid) satisfied"])
    try:
        comp.run()
    except Exception as e:  # noqa: BLE001 - any crash in validation is a failed contract, not a pass
        comp.check("C99_internal", False, f"validator could not complete: {e!r}")
    if not comp.errors and property_tests:
        for chk in _property_tests(model, comp):
            comp.checks[chk.id] = chk
            if chk.status == "fail":
                comp.errors.append(f"[{chk.id}] " + "; ".join(chk.details[-3:]))
    report.checks = sorted(comp.checks.values(), key=lambda c: c.id)
    report.errors = comp.errors
    report.warnings = comp.warnings
    report.downgrades = comp.downgrades
    report.passed = not comp.errors
    comp.fidelity(report)
    tiers = [m.epistemic_tier for m in model.mechanisms]
    open_high = [s.id for s in model.structural_unknowns if s.impact == "high" and s.status == "DISCOVERED_UNMODELED"]
    computable = [m for m in model.metrics if report.metric_fidelity.get(m.id) != FidelityTier.X_LIMITED_OR_ABSTAIN]
    report.scorecard = FidelityScorecard(
        schema_validity="pass" if report.passed else "fail",
        mechanism_coverage=f"{len(computable)}/{len(model.metrics)} metrics computable; {len(open_high)} high-impact structural unknowns open",
        empirical_support=", ".join(f"{sum(1 for t in tiers if t == k)} {k}" for k in TIER_ORDER),
        parameter_identifiability=f"{sum(1 for p in model.parameters if p.source_kind == 'declared_exploratory_range')} of {len(model.parameters)} parameters are declared exploratory ranges",
        transfer_applicability="; ".join(f"{r.get('entry')}: {'used' if r.get('used') else ('compatible, unused' if r.get('transfer_ok') else 'rejected: ' + '; '.join(r.get('reasons', [])))}" for r in model.reuse_manifest) or "no reused components",
        trajectory_stability="not_yet_measured (requires paired screening runs)",
        structural_omission_risk="high" if open_high else ("medium" if model.structural_unknowns or model.omissions else "low"),
        interpretive_limitations=[
            "Valid execution is not external validation: tests check contracts, not real-world accuracy.",
            "Simulated frequencies are not real-world likelihoods.",
        ] + ([f"High-stakes decision: {', '.join(sorted(report.non_computable_metrics))} limited to conditional analysis."] if high_stakes else []),
    )
    if report.passed:
        report.allowed_run_modes = ["screening", "sensitivity", "conditional"] if high_stakes else ["screening", "depth", "sensitivity"]
        verified = all(c.status != "fail" for c in report.checks if c.id.startswith("P"))
        model.promotion_state = PromotionState.VERIFIED_INTERNAL_B if (property_tests and verified) else PromotionState.COMPILED_B
    else:
        model.promotion_state = PromotionState.DRAFT_B
    model.validation_report_ref = report.report_id
    model.hash = compute_model_hash(model)
    report.model_hash = model.hash
    return model, report

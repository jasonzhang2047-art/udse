"""Indexed view of a DecisionWorldModel plus the typed environments used by the compiler (static
types) and the kernel (runtime values)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..common import canonical_json, content_hash, parse_dt
from .expr import (
    T,
    T_ANY,
    T_BOOL,
    T_Q,
    T_STR,
    T_TIME,
    UNKNOWN,
    UNKNOWN_JSON,
    DictTypeEnv,
    EvalError,
    Q,
    to_typed,
)
from .schemas import DecisionWorldModel, ParameterSpec, StateVar
from .units import base_unit, parse_unit


def model_payload(model: DecisionWorldModel) -> dict[str, Any]:
    data = model.model_dump(mode="json", by_alias=True)
    data["hash"] = None
    return data


def compute_model_hash(model: DecisionWorldModel) -> str:
    return content_hash(model_payload(model))


def state_type(sv: StateVar) -> T:
    if sv.type == "quantity":
        return T_Q(sv.unit)
    if sv.type == "boolean":
        return T_BOOL
    if sv.type == "enum":
        return T_STR(sv.values)
    if sv.type == "timestamp":
        return T_TIME
    if sv.type == "text":
        return T_STR()
    return T_ANY


def param_type(p: ParameterSpec) -> T:
    if p.type == "quantity":
        return T_Q(p.unit)
    if p.type == "boolean":
        return T_BOOL
    if p.type == "enum":
        return T_STR(p.values)
    if p.type == "timestamp":
        return T_TIME
    return T_ANY


def typed_param_value(p: ParameterSpec, raw: Any) -> Any:
    if raw is None or raw == UNKNOWN_JSON:
        return UNKNOWN
    if p.type == "quantity":
        return Q(float(raw), parse_unit(p.unit))
    if p.type == "timestamp":
        return parse_dt(raw)
    return raw


def encode_value(v: Any) -> Any:
    """Type-tagged JSON encoding for event payloads. Quantities are stored in base units with their
    dimension vector so the round trip never depends on re-parsing a display label."""
    if v is UNKNOWN:
        return UNKNOWN_JSON
    if isinstance(v, Q):
        return {"q": v.value * v.unit.scale, "dims": [list(d) for d in v.unit.dims], "label": v.unit.label}
    if isinstance(v, datetime):
        return {"t": v.isoformat()}
    return v


def decode_value(v: Any) -> Any:
    if v == UNKNOWN_JSON:
        return UNKNOWN
    if isinstance(v, dict) and "q" in v and "dims" in v:
        return Q(float(v["q"]), base_unit(tuple((str(k), int(e)) for k, e in v["dims"])))
    if isinstance(v, dict) and set(v) == {"t"}:
        return parse_dt(v["t"])
    return v


class ModelIndex:
    def __init__(self, model: DecisionWorldModel):
        self.model = model
        self.state = {s.ref: s for s in model.state}
        self.params = {p.id: p for p in model.parameters}
        self.mechanisms = {m.id: m for m in model.mechanisms}
        self.actions = {a.id: a for a in model.actions}
        self.agents = {a.id: a for a in model.agents}
        self.policies = {p.id: p for p in model.policies}
        self.scenarios = {s.id: s for s in model.scenarios}
        self.metrics = {m.id: m for m in model.metrics}
        self.entities = {e.id: e for e in model.entities}
        self.constraints = {c.id: c for c in model.constraints}
        self.horizon_start = parse_dt(model.scope.horizon_start)
        self.horizon_end = parse_dt(model.scope.horizon_end)

    # ----- static typing -----
    def base_type_env(self) -> DictTypeEnv:
        types: dict[str, T] = {ref: state_type(sv) for ref, sv in self.state.items()}
        for pid, p in self.params.items():
            types[f"param.{pid}"] = param_type(p)
        types["now"] = T_TIME
        types["horizon.start"] = T_TIME
        types["horizon.end"] = T_TIME
        types["actor"] = T_STR(list(self.agents) or None)
        return DictTypeEnv(types)

    def action_type_env(self, action_id: str, parent: DictTypeEnv | None = None) -> DictTypeEnv:
        act = self.actions[action_id]
        types: dict[str, T] = {}
        for a in act.args:
            if a.type == "enum":
                types[f"arg.{a.name}"] = T_STR(a.values)
            elif a.type == "number":
                types[f"arg.{a.name}"] = T_Q(a.unit)
            else:
                types[f"arg.{a.name}"] = T_BOOL
        return DictTypeEnv(types, parent or self.base_type_env())


class StateEnv:
    """Runtime evaluation environment over the authoritative world state (kernel view)."""

    def __init__(
        self,
        index: ModelIndex,
        world: dict[str, Any],
        params: dict[str, Any],
        now: datetime,
        extra: dict[str, Any] | None = None,
        visible: set[str] | None = None,
        visible_params: set[str] | None = None,
    ):
        self.index = index
        self.world = world
        self.params = params  # already-typed parameter values
        self.now = now
        self.extra = extra or {}
        self.visible = visible  # None = omniscient (kernel). Otherwise Agent observation scope.
        self.visible_params = visible_params

    def lookup(self, name: str) -> Any:
        if name in self.extra:
            return self.extra[name]
        if name == "now":
            return self.now
        if name == "horizon.start":
            return self.index.horizon_start
        if name == "horizon.end":
            return self.index.horizon_end
        if name.startswith("param."):
            pid = name[6:]
            if pid not in self.params:
                raise EvalError(f"unknown parameter {pid!r}")
            if self.visible_params is not None and pid not in self.visible_params:
                return UNKNOWN
            return self.params[pid]
        sv = self.index.state.get(name)
        if sv is None:
            raise EvalError(f"unresolved reference {name!r}")
        if self.visible is not None and name not in self.visible:
            return UNKNOWN
        return to_typed(self.world.get(name, UNKNOWN_JSON), sv.type, sv.unit)


def stable_digest(obj: Any) -> str:
    return content_hash(obj)[7:23]


__all__ = [
    "ModelIndex",
    "StateEnv",
    "compute_model_hash",
    "encode_value",
    "decode_value",
    "typed_param_value",
    "state_type",
    "param_type",
    "canonical_json",
    "stable_digest",
]

"""Layer C simulation state, atomic patches and state hashing (§8.2, §8.4)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Iterator

from ..common import content_hash, json_copy
from ..model_ir.schemas import PatchOp


class SimState:
    """World variables (flat ref → JSON value), per-Agent SimAgentState(t) and kernel-internal
    bookkeeping (gate arming, terminal marker). The clock is carried by events, not the hash."""

    def __init__(self, world: dict[str, Any], agents: dict[str, dict[str, Any]], internal: dict[str, Any]):
        self.world = world
        self.agents = agents
        self.internal = internal

    def as_dict(self) -> dict[str, Any]:
        return {"world": self.world, "agents": self.agents, "internal": self.internal}

    def hash(self) -> str:
        return content_hash(self.as_dict())

    def snapshot(self) -> dict[str, Any]:
        return json_copy(self.as_dict())

    @classmethod
    def from_snapshot(cls, snap: dict[str, Any]) -> "SimState":
        s = json_copy(snap)
        return cls(s["world"], s["agents"], s["internal"])

    def apply(self, ops: list[PatchOp]) -> None:
        for op in ops:
            value = json_copy(op.after)
            if op.layer == "world":
                self.world[op.key] = value
            elif op.layer == "internal":
                if value is None:
                    self.internal.pop(op.key, None)
                else:
                    self.internal[op.key] = value
            else:
                agent_id, field = op.key.split("/", 1)
                self.agents.setdefault(agent_id, {})[field] = value


class _Overlay(Mapping):
    def __init__(self, base: dict[str, Any], pending: dict[str, Any]):
        self.base = base
        self.pending = pending

    def __getitem__(self, k: str) -> Any:
        if k in self.pending:
            return self.pending[k]
        return self.base[k]

    def __iter__(self) -> Iterator[str]:
        yield from self.base
        for k in self.pending:
            if k not in self.base:
                yield k

    def __len__(self) -> int:
        return len(set(self.base) | set(self.pending))


class PatchBuilder:
    """Accumulates one transition's writes; reads see earlier writes (sequential semantics).
    Nothing reaches the authoritative state until the kernel commits the whole patch."""

    def __init__(self, state: SimState):
        self.state = state
        self._world: dict[str, Any] = {}
        self._internal: dict[str, Any] = {}
        self._agents: dict[str, Any] = {}

    @property
    def world_view(self) -> Mapping:
        return _Overlay(self.state.world, self._world)

    def get_world(self, ref: str, default: Any = None) -> Any:
        if ref in self._world:
            return self._world[ref]
        return self.state.world.get(ref, default)

    def set_world(self, ref: str, value: Any) -> None:
        self._world[ref] = json_copy(value)

    def get_internal(self, key: str, default: Any = None) -> Any:
        if key in self._internal:
            return self._internal[key]
        return self.state.internal.get(key, default)

    def set_internal(self, key: str, value: Any) -> None:
        self._internal[key] = json_copy(value)

    def get_agent(self, agent_id: str, field: str, default: Any = None) -> Any:
        k = f"{agent_id}/{field}"
        if k in self._agents:
            return self._agents[k]
        return self.state.agents.get(agent_id, {}).get(field, default)

    def set_agent(self, agent_id: str, field: str, value: Any) -> None:
        self._agents[f"{agent_id}/{field}"] = json_copy(value)

    def touched_world(self) -> set[str]:
        return set(self._world)

    def ops(self) -> list[PatchOp]:
        out: list[PatchOp] = []
        for k in sorted(self._world):
            before = self.state.world.get(k)
            if before != self._world[k]:
                out.append(PatchOp(layer="world", key=k, before=before, after=self._world[k]))
        for k in sorted(self._agents):
            agent_id, field = k.split("/", 1)
            before = self.state.agents.get(agent_id, {}).get(field)
            if before != self._agents[k]:
                out.append(PatchOp(layer="agent", key=k, before=before, after=self._agents[k]))
        for k in sorted(self._internal):
            before = self.state.internal.get(k)
            if before != self._internal[k]:
                out.append(PatchOp(layer="internal", key=k, before=before, after=self._internal[k]))
        return out

    def preview(self) -> SimState:
        """The state that would result from committing this patch (for invariant checks)."""
        s = SimState.from_snapshot(self.state.as_dict())
        s.apply(self.ops())
        return s

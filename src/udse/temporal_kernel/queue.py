"""Deterministic event queue: stable ordering by (sim_at, priority, seq) (§8.2, §8.6)."""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..common import parse_dt

PRIORITY = {
    "POLICY_INIT": 0,
    "INTERVENTION": 0,
    "GATE_FIRE": 5,
    "GATE_RESET": 6,
    "EXOGENOUS": 10,
    "MECHANISM_TICK": 20,
    "DOMAIN_EVENT": 30,
    "AGENT_WAKEUP": 50,
}


@dataclass
class ScheduledEvent:
    sim_at: datetime
    kind: str
    event_type: str
    mechanism_id: str | None = None
    actor_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    parent_event_ids: list[str] = field(default_factory=list)
    reason: str = ""
    visibility: dict[str, Any] | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    priority: int = -1
    seq: int = -1

    def to_json(self) -> dict[str, Any]:
        return {
            "sim_at": self.sim_at.isoformat(), "kind": self.kind, "event_type": self.event_type,
            "mechanism_id": self.mechanism_id, "actor_id": self.actor_id, "payload": self.payload,
            "parent_event_ids": self.parent_event_ids, "reason": self.reason, "visibility": self.visibility,
            "meta": self.meta, "priority": self.priority, "seq": self.seq,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "ScheduledEvent":
        d = dict(d)
        d["sim_at"] = parse_dt(d["sim_at"])
        return cls(**d)


class EventQueue:
    def __init__(self) -> None:
        self._heap: list[tuple[datetime, int, int, ScheduledEvent]] = []
        self._seq = 0

    def push(self, ev: ScheduledEvent) -> ScheduledEvent:
        if ev.priority < 0:
            ev.priority = PRIORITY.get(ev.kind, 40)
        ev.seq = self._seq
        self._seq += 1
        heapq.heappush(self._heap, (ev.sim_at, ev.priority, ev.seq, ev))
        return ev

    def pop(self) -> ScheduledEvent:
        return heapq.heappop(self._heap)[3]

    def peek(self) -> ScheduledEvent | None:
        return self._heap[0][3] if self._heap else None

    def __len__(self) -> int:
        return len(self._heap)

    def snapshot(self) -> dict[str, Any]:
        return {"seq": self._seq, "items": [e.to_json() for _, _, _, e in sorted(self._heap, key=lambda t: t[:3])]}

    @classmethod
    def restore(cls, snap: dict[str, Any]) -> "EventQueue":
        q = cls()
        for item in snap["items"]:
            ev = ScheduledEvent.from_json(item)
            heapq.heappush(q._heap, (ev.sim_at, ev.priority, ev.seq, ev))
        q._seq = snap["seq"]
        return q

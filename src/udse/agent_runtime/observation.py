"""Observation redaction (§7.3 item 3): visible world events since the last decision, not
omniscient world state. Deadline variables are visible only in DEADLINE_AWARE arms (§7.4)."""

from __future__ import annotations

from typing import Any

from ..model_ir.enums import DeadlineDisclosure
from ..model_ir.index import ModelIndex, decode_value
from ..model_ir.expr import value_to_display
from ..model_ir.schemas import Visibility, WorldEvent

SIMULATION_DISCLOSURE = (
    "You are a simulated Agent inside a hypothetical decision simulation. Events you observe are "
    "simulated, not real-world facts. Choose exactly one eligible action or WAIT."
)


def visible_refs(index: ModelIndex, agent_id: str, disclosure: DeadlineDisclosure) -> set[str]:
    out = set()
    for ref, sv in index.state.items():
        if sv.is_deadline and disclosure != DeadlineDisclosure.DEADLINE_AWARE:
            continue
        if sv.visibility.scope == "public" or (sv.visibility.scope == "agents" and agent_id in sv.visibility.agents):
            out.add(ref)
    return out


def visible_params(index: ModelIndex) -> set[str]:
    return {pid for pid, p in index.params.items() if p.agent_visible}


def event_visible_to(vis: Visibility | dict[str, Any] | None, agent_id: str, actor_id: str | None) -> bool:
    if actor_id == agent_id:
        return True
    if vis is None:
        return True
    v = vis if isinstance(vis, Visibility) else Visibility(**vis)
    if v.scope == "public":
        return True
    if v.scope == "agents":
        return agent_id in v.agents
    return False


def summarize_event(ev: WorldEvent, visible: set[str]) -> dict[str, Any]:
    changed = {
        op.key: op.after for op in ev.patch if op.layer == "world" and op.key in visible
    }
    payload: dict[str, Any] = {}
    if ev.kind == "AGENT_DECISION":
        intent = ev.payload.get("decision", {}).get("intent", {})
        payload = {"action": intent.get("action_type"), "args": intent.get("parameters", {})}
        if ev.payload.get("rejection_code"):
            payload["rejected"] = ev.payload["rejection_code"]
    elif "event_payload" in ev.payload:
        payload = {k: value_to_display(decode_value(v)) for k, v in ev.payload["event_payload"].items()}
    return {
        "seq": ev.seq, "sim_at": ev.sim_at, "event_type": ev.event_type, "actor": ev.actor_id,
        "payload": payload, "visible_changes": changed,
    }


def build_observation(
    index: ModelIndex,
    agent_id: str,
    world: dict[str, Any],
    events: list[WorldEvent],
    last_seen_seq: int,
    disclosure: DeadlineDisclosure,
    now_iso: str,
    max_events: int = 25,
) -> dict[str, Any]:
    vis = visible_refs(index, agent_id, disclosure)
    new_events = [
        summarize_event(e, vis)
        for e in events
        if e.seq > last_seen_seq and event_visible_to(e.visibility_scope, agent_id, e.actor_id)
    ]
    visible_state = {}
    for ref in sorted(vis):
        sv = index.state[ref]
        if sv.type == "ledger":
            continue
        visible_state[ref] = world.get(ref)
    deadline = None
    if disclosure == DeadlineDisclosure.DEADLINE_AWARE:
        deadlines = {ref: world.get(ref) for ref, sv in index.state.items() if sv.is_deadline}
        if deadlines:
            deadline = {
                "deadlines": deadlines,
                "note": "This is a decision-world deadline. A timely acceptable decision matters; it does not require agreement.",
            }
    return {
        "disclosure": SIMULATION_DISCLOSURE,
        "sim_now": now_iso,
        "visible_state": visible_state,
        "new_events": new_events[-max_events:],
        "omitted_older_events": max(0, len(new_events) - max_events),
        "deadline": deadline,
    }

"""Reproducibility (§8.4).

* Event-log replay: rebuild every state from genesis + committed patches and verify each pre/post
  hash bit-for-bit. No model code, no LLM.
* Transition replay: re-execute the pinned model with recorded Agent decisions (and the counter-
  based random streams) and verify the kernel regenerates identical patches and hashes.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..agent_runtime.policies import RecordedAgentRuntime
from ..model_ir.schemas import DecisionWorldModel, ProfileFeature, RunManifest, WorldEvent
from .kernel import Simulator
from .state import SimState
from .store import InMemoryRunStore


class ReplayReport(BaseModel):
    mode: str
    ok: bool
    events_checked: int
    final_state_hash: str | None = None
    mismatches: list[dict[str, Any]] = Field(default_factory=list)
    llm_calls: int = 0
    reconstructed_state: dict[str, Any] | None = None


def replay_event_log(genesis_snapshot: dict[str, Any], genesis_hash: str, events: list[WorldEvent],
                     upto_seq: int | None = None, include_state: bool = False) -> ReplayReport:
    state = SimState.from_snapshot(genesis_snapshot)
    mismatches = []
    h = state.hash()
    if h != genesis_hash:
        mismatches.append({"at": "genesis", "expected": genesis_hash, "actual": h})
    checked = 0
    for e in events:
        if upto_seq is not None and e.seq > upto_seq:
            break
        if h != e.pre_state_hash:
            mismatches.append({"seq": e.seq, "check": "pre_state_hash", "expected": e.pre_state_hash, "actual": h})
        state.apply(e.patch)
        h = state.hash()
        if h != e.post_state_hash:
            mismatches.append({"seq": e.seq, "check": "post_state_hash", "expected": e.post_state_hash, "actual": h})
        checked += 1
    return ReplayReport(mode="event_log", ok=not mismatches, events_checked=checked, final_state_hash=h,
                        mismatches=mismatches, reconstructed_state=state.snapshot() if include_state else None)


def state_at(genesis_snapshot: dict[str, Any], events: list[WorldEvent], upto_seq: int) -> SimState:
    state = SimState.from_snapshot(genesis_snapshot)
    for e in events:
        if e.seq > upto_seq:
            break
        state.apply(e.patch)
    return state


def _signature(e: WorldEvent) -> dict[str, Any]:
    return {
        "seq": e.seq, "sim_at": e.sim_at, "event_type": e.event_type, "kind": e.kind, "actor": e.actor_id,
        "patch": [op.model_dump(mode="json") for op in e.patch], "pre": e.pre_state_hash, "post": e.post_state_hash,
        "draws": e.random_draw_refs,
    }


def replay_transitions(model: DecisionWorldModel, manifest: RunManifest, recorded: list[WorldEvent],
                       profiles: dict[str, list[ProfileFeature]] | None = None,
                       checkpoint: dict[str, Any] | None = None, prior_events: list[WorldEvent] | None = None) -> ReplayReport:
    runtime = RecordedAgentRuntime(recorded)
    store = InMemoryRunStore()
    m = manifest.model_copy(deep=True)
    total = (len(prior_events or [])) + len(recorded)
    sim = Simulator(model, m, runtime, store, profiles, checkpoint=checkpoint, prior_events=prior_events,
                    enforce_wall=False, stop_after_events=total)
    sim.run()
    produced = store.get_events(manifest.run_id)
    mismatches = []
    for i, rec in enumerate(recorded):
        if i >= len(produced):
            mismatches.append({"seq": rec.seq, "check": "missing", "expected": rec.event_type})
            break
        a, b = _signature(rec), _signature(produced[i])
        if a != b:
            diff = {k: {"expected": a[k], "actual": b[k]} for k in a if a[k] != b[k]}
            mismatches.append({"seq": rec.seq, "check": "transition", "diff": diff})
            break
    return ReplayReport(mode="transition", ok=not mismatches, events_checked=min(len(recorded), len(produced)),
                        final_state_hash=produced[-1].post_state_hash if produced else None, mismatches=mismatches,
                        llm_calls=0)

"""Run-store protocol used by the kernel, plus an in-memory implementation (screening runs, tests).

The persistent implementation lives in ``udse.storage`` and writes with simulation-worker
credentials only (§14.6)."""

from __future__ import annotations

from typing import Any, Protocol

from ..model_ir.schemas import RunManifest, RunResult, WorldEvent


class RunStore(Protocol):
    def commit_genesis(self, manifest: RunManifest, snapshot: dict[str, Any], state_hash: str, checkpoint: dict[str, Any]) -> None: ...

    def append_event(self, event: WorldEvent, checkpoint: dict[str, Any] | None) -> None: ...

    def finalize(self, result: RunResult, final_snapshot: dict[str, Any]) -> None: ...


class InMemoryRunStore:
    def __init__(self) -> None:
        self.manifests: dict[str, RunManifest] = {}
        self.genesis: dict[str, dict[str, Any]] = {}
        self.events: dict[str, list[WorldEvent]] = {}
        self.checkpoints: dict[str, dict[int, dict[str, Any]]] = {}
        self.results: dict[str, RunResult] = {}
        self.final_snapshots: dict[str, dict[str, Any]] = {}

    def commit_genesis(self, manifest: RunManifest, snapshot: dict[str, Any], state_hash: str, checkpoint: dict[str, Any]) -> None:
        if manifest.run_id in self.genesis:
            raise ValueError(f"run {manifest.run_id} already has a genesis snapshot (immutable)")
        self.manifests[manifest.run_id] = manifest
        self.genesis[manifest.run_id] = {"snapshot": snapshot, "hash": state_hash}
        self.events[manifest.run_id] = []
        self.checkpoints[manifest.run_id] = {-1: checkpoint}

    def append_event(self, event: WorldEvent, checkpoint: dict[str, Any] | None) -> None:
        evs = self.events[event.run_id]
        if evs and evs[-1].seq >= event.seq:
            raise ValueError("events are append-only and strictly ordered")
        evs.append(event)
        if checkpoint is not None:
            self.checkpoints[event.run_id][event.seq] = checkpoint

    def finalize(self, result: RunResult, final_snapshot: dict[str, Any]) -> None:
        self.results[result.run_id] = result
        self.final_snapshots[result.run_id] = final_snapshot

    # read helpers (same names as the SQLite store)
    def get_events(self, run_id: str) -> list[WorldEvent]:
        return list(self.events.get(run_id, []))

    def get_genesis(self, run_id: str) -> dict[str, Any]:
        return self.genesis[run_id]

    def get_manifest(self, run_id: str) -> RunManifest:
        return self.manifests[run_id]

    def get_result(self, run_id: str) -> RunResult | None:
        return self.results.get(run_id)

    def get_checkpoint(self, run_id: str, seq: int) -> dict[str, Any] | None:
        return self.checkpoints.get(run_id, {}).get(seq)

    def list_checkpoints(self, run_id: str) -> list[int]:
        return sorted(self.checkpoints.get(run_id, {}))

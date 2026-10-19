"""Reuse registry (§6.3–§6.4): mechanisms, structure templates and decision-scoped model packages
as immutable versions with applicability envelopes, plus the formal transfer gate and the curator
promotion workflow.

reusable(candidate, target) iff type/units compatible, dependency closure holds, the applicability
envelope contains the target, evidence provenance remains valid, licensing allows use, and the
entry's regression tests pass. Label/embedding similarity is only candidate retrieval.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..common import PermissionDenied, ProvenanceRejected, canonical_json, content_hash, parse_dt
from ..model_ir.enums import GROUNDING_ORIGINS, RegistryStatus, Role
from ..model_ir.units import UnitError, parse_unit
from ..storage.credentials import Credential
from ..storage.sqlite_store import Store

ALLOWED_LICENSES = {"*", "fixture_only", "open", "cc-by-4.0", "internal"}


class Applicability(BaseModel):
    domains: list[str] = Field(default_factory=lambda: ["*"])
    jurisdictions: list[str] = Field(default_factory=lambda: ["*"])
    time_resolutions: list[str] = Field(default_factory=lambda: ["minute", "hour", "day", "week", "month"])
    populations: list[str] = Field(default_factory=lambda: ["*"])
    valid_from: str | None = None
    valid_to: str | None = None
    parameter_envelope: dict[str, list[float]] = Field(default_factory=dict)


class RegistryEntry(BaseModel):
    entry_id: str
    version: str
    kind: Literal["mechanism", "structure", "model_package"]
    status: RegistryStatus
    title: str
    description: str = ""
    operator: str | None = None
    interface: dict[str, Any] = Field(default_factory=dict)  # slot -> {"dimension": unit string}
    template: dict[str, Any] = Field(default_factory=dict)
    template_file: str | None = None
    applicability: Applicability = Field(default_factory=Applicability)
    causal_assumptions: list[str] = Field(default_factory=list)
    evidence_claim_ids: list[str] = Field(default_factory=list)
    evidence_origins: list[str] = Field(default_factory=list)
    tests: list[dict[str, Any]] = Field(default_factory=list)
    curator_decision: dict[str, Any] | None = None
    allowed_tenants: list[str] = Field(default_factory=lambda: ["*"])
    known_failure_modes: list[str] = Field(default_factory=list)
    license: str = "fixture_only"
    breadth_hints: list[dict[str, Any]] = Field(default_factory=list)
    research_needs: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def ref(self) -> str:
        return f"{self.entry_id}@{self.version}"

    def ir_hash(self) -> str:
        return content_hash({"template": self.template, "interface": self.interface, "operator": self.operator})


class TransferDecision(BaseModel):
    entry: str
    ok: bool
    reasons: list[str] = Field(default_factory=list)
    checks: dict[str, bool] = Field(default_factory=dict)


class TargetContext(BaseModel):
    domain_labels: list[str]
    jurisdiction: str | None
    time_resolution: str
    horizon_start: str
    horizon_end: str
    tenant: str = "default"
    slot_units: dict[str, str] = Field(default_factory=dict)


def transfer_gate(entry: RegistryEntry, target: TargetContext) -> TransferDecision:
    checks: dict[str, bool] = {}
    reasons: list[str] = []
    a = entry.applicability
    checks["status"] = entry.status not in (RegistryStatus.REVOKED, RegistryStatus.QUARANTINED)
    if not checks["status"]:
        reasons.append(f"entry status {entry.status} forbids new use")
    checks["domain"] = "*" in a.domains or bool(set(a.domains) & set(target.domain_labels))
    if not checks["domain"]:
        reasons.append(f"domain {target.domain_labels} outside envelope {a.domains}")
    if "*" in a.jurisdictions:
        checks["jurisdiction"] = True
    elif not target.jurisdiction:
        checks["jurisdiction"] = False
        reasons.append(f"entry is jurisdiction-scoped {a.jurisdictions}; target jurisdiction unknown")
    else:
        checks["jurisdiction"] = any(target.jurisdiction.upper().startswith(j.upper()) for j in a.jurisdictions)
        if not checks["jurisdiction"]:
            reasons.append(f"jurisdiction {target.jurisdiction} outside envelope {a.jurisdictions}")
    checks["time_resolution"] = target.time_resolution in a.time_resolutions
    if not checks["time_resolution"]:
        reasons.append(f"time resolution {target.time_resolution} not in {a.time_resolutions}")
    ok_period = True
    if a.valid_from and parse_dt(target.horizon_start) < parse_dt(a.valid_from):
        ok_period = False
    if a.valid_to and parse_dt(target.horizon_end) > parse_dt(a.valid_to):
        ok_period = False
    checks["validity_period"] = ok_period
    if not ok_period:
        reasons.append("target horizon outside the entry's validity period")
    unit_ok = True
    for slot, spec in entry.interface.items():
        want = spec.get("dimension")
        have = target.slot_units.get(slot)
        if want and have:
            try:
                unit_ok &= parse_unit(want).same_dimension(parse_unit(have))
            except UnitError:
                unit_ok = False
            if not unit_ok:
                reasons.append(f"slot {slot}: unit {have} incompatible with {want}")
    checks["units"] = unit_ok
    checks["license"] = entry.license in ALLOWED_LICENSES
    if not checks["license"]:
        reasons.append(f"license {entry.license} does not permit reuse")
    checks["tenant"] = "*" in entry.allowed_tenants or target.tenant in entry.allowed_tenants
    if not checks["tenant"]:
        reasons.append("tenant not allowed")
    checks["tests"] = bool(entry.tests) and all(t.get("status") == "pass" for t in entry.tests)
    if not checks["tests"]:
        reasons.append("regression/invariant tests missing or failing")
    return TransferDecision(entry=entry.ref, ok=all(checks.values()), reasons=reasons, checks=checks)


class Registry:
    def __init__(self, store: Store, fixtures_dir: Path | None = None):
        self.store = store
        self.fixtures_dir = fixtures_dir

    def entries(self, kind: str | None = None) -> list[RegistryEntry]:
        out = []
        for row in self.store.list_registry(kind):
            e = RegistryEntry.model_validate({k: v for k, v in row.items() if not k.startswith("_")})
            e.status = RegistryStatus(row["_status"])
            out.append(e)
        return out

    def get(self, entry_id: str, version: str | None = None) -> RegistryEntry | None:
        cands = [e for e in self.entries() if e.entry_id == entry_id and (version is None or e.version == version)]
        return sorted(cands, key=lambda e: [int(x) for x in e.version.split(".")])[-1] if cands else None

    def load_template(self, entry: RegistryEntry) -> dict[str, Any]:
        if entry.template:
            return entry.template
        if entry.template_file and self.fixtures_dir:
            return json.loads((self.fixtures_dir / entry.template_file).read_text())
        return {}

    def search_compatible(self, target: TargetContext, kind: str | None = None) -> list[tuple[RegistryEntry, TransferDecision]]:
        """Candidate retrieval by label overlap, then the formal transfer gate for each candidate."""
        out = []
        for e in self.entries(kind):
            if "*" not in e.applicability.domains and not (set(e.applicability.domains) & set(target.domain_labels)):
                continue
            out.append((e, transfer_gate(e, target)))
        return out

    def status_map(self) -> dict[str, str]:
        return {e.ref: str(e.status) for e in self.entries()}


class CuratorService:
    """Layer A promotion requires independent external evidence, applicability scope, tests and a
    designated curator approval. Simulation success alone never qualifies (§6.4, §19)."""

    def __init__(self, store: Store, cred: Credential):
        if cred.role != Role.CURATOR:
            raise PermissionDenied("curator service requires the curator credential")
        self.store = store
        self.cred = cred

    def register(self, entry: RegistryEntry) -> None:
        self.store.put_registry_entry(self.cred, entry.entry_id, entry.version, str(entry.status), entry.kind,
                                      json.loads(canonical_json(entry.model_dump(mode="json"))), entry.ir_hash())

    def promote_to_layer_a(self, entry: RegistryEntry, approver: str, rationale: str) -> RegistryEntry:
        problems = []
        claims = [self.store.get_claim(cid) for cid in entry.evidence_claim_ids]
        if not entry.evidence_claim_ids:
            problems.append("no independent external evidence cited")
        for cid, c in zip(entry.evidence_claim_ids, claims):
            if c is None:
                problems.append(f"evidence {cid} is not an ingested external claim")
            elif c.origin_kind not in GROUNDING_ORIGINS:
                problems.append(f"evidence {cid} has origin {c.origin_kind} (not external observation/rule/dataset)")
        if any(o == "SIMULATION_OUTPUT" for o in entry.evidence_origins):
            problems.append("simulation outputs cannot support Layer A promotion")
        if not entry.tests or not all(t.get("status") == "pass" for t in entry.tests):
            problems.append("tests missing or failing")
        if entry.applicability.domains == ["*"] and entry.kind != "mechanism":
            problems.append("structure/package applicability must be explicitly scoped")
        if problems:
            self.store.audit(self.cred, "promote_layer_a", f"registry:{entry.ref}", False, "; ".join(problems))
            raise ProvenanceRejected("Layer A promotion refused: " + "; ".join(problems))
        promoted = entry.model_copy(deep=True)
        promoted.status = RegistryStatus.A_VALIDATED
        promoted.curator_decision = {"approver": approver, "rationale": rationale, "decision": "A_VALIDATED"}
        new_version = entry.version.split(".")
        new_version[-1] = str(int(new_version[-1]) + 1)
        promoted.version = ".".join(new_version)
        self.store.put_curated(self.cred, promoted.entry_id, promoted.version, promoted.kind, "A_VALIDATED",
                               json.loads(canonical_json(promoted.model_dump(mode="json"))), entry.evidence_claim_ids,
                               [promoted.curator_decision])
        self.register(promoted)
        return promoted

    def register_local_template(self, entry: RegistryEntry) -> RegistryEntry:
        """A model that is only internally consistent may become a clearly labelled local hypothesis
        template — never an externally validated shared mechanism."""
        e = entry.model_copy(deep=True)
        e.status = RegistryStatus.LOCAL_HYPOTHESIS_TEMPLATE
        self.register(e)
        return e

    def revoke(self, entry_id: str, version: str, reason: str) -> None:
        self.store.set_registry_status(self.cred, entry_id, version, str(RegistryStatus.REVOKED))
        self.store.audit(self.cred, "revoke", f"registry:{entry_id}@{version}", True, reason)

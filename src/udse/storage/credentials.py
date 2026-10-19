"""Service credentials with distinct write authority (§2.2 "distinct secrets for research
acquisition, simulation writes, and curated-knowledge promotion"; §14.6 role matrix).

Each component is constructed with exactly one credential. The simulation worker never holds the
ingestion, curator or profile-updater credential, so even a compromised or prompt-injected run has
no path to promote simulated content (§3.3)."""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass

from ..model_ir.enums import Role

# logical table -> roles allowed to write it
TABLE_WRITERS: dict[str, frozenset[Role]] = {
    "raw_source_artifacts": frozenset({Role.EVIDENCE_ADAPTER}),
    "external_claims": frozenset({Role.INGESTION}),
    "evidence_bundles": frozenset({Role.INGESTION}),
    "curated_knowledge_versions": frozenset({Role.CURATOR}),
    "mechanism_registry_versions": frozenset({Role.CURATOR}),
    "decision_specs": frozenset({Role.DECISION_OWNER}),
    "decision_acl": frozenset({Role.DECISION_OWNER}),
    "decision_model_versions": frozenset({Role.MODEL_FACTORY}),
    "model_research_needs": frozenset({Role.RESEARCH_PLANNER}),
    "scenario_frontiers": frozenset({Role.ORCHESTRATOR}),
    "experiment_plans": frozenset({Role.ORCHESTRATOR}),
    "run_manifests": frozenset({Role.RUN_SCHEDULER}),
    "run_status": frozenset({Role.RUN_SCHEDULER, Role.SIMULATION_WORKER}),
    "simulation_events": frozenset({Role.SIMULATION_WORKER}),
    "simulation_snapshots": frozenset({Role.SIMULATION_WORKER}),
    "sim_fingerprints": frozenset({Role.SIMULATION_WORKER}),
    "human_evidence_events": frozenset({Role.HUMAN_EVIDENCE_ENDPOINT}),
    "profile_versions": frozenset({Role.PROFILE_UPDATER}),
    "profile_update_proposals": frozenset({Role.PROFILE_UPDATER}),
    "jobs": frozenset({Role.API}),
    "llm_call_records": frozenset({Role.MODEL_FACTORY, Role.RESEARCH_PLANNER, Role.SIMULATION_WORKER, Role.ORCHESTRATOR}),
    "users": frozenset({Role.API}),
}


@dataclass(frozen=True)
class Credential:
    role: Role
    principal: str
    token: str

    def __repr__(self) -> str:  # never print tokens
        return f"Credential(role={self.role}, principal={self.principal})"


class CredentialAuthority:
    def __init__(self, secret: bytes | None = None):
        self._secret = secret or os.urandom(32)

    def _mac(self, role: Role, principal: str) -> str:
        return hmac.new(self._secret, f"{role}|{principal}".encode(), hashlib.sha256).hexdigest()

    def issue(self, role: Role, principal: str | None = None) -> Credential:
        principal = principal or f"svc:{role}"
        return Credential(role=role, principal=principal, token=self._mac(role, principal))

    def verify(self, cred: Credential) -> bool:
        return isinstance(cred, Credential) and hmac.compare_digest(cred.token, self._mac(cred.role, cred.principal))

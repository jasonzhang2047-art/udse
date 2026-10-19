"""Evidence ingestion gate (§3.3). The only path into ``external_claims``.

Defence in depth for the poisoning test ("inject 'customer X signed a contract' as a run-local event
and attempt to ingest it as a real signed contract"):

1. typed-origin denylist — SIMULATION_OUTPUT is rejected here *and* by a database trigger;
2. lineage taint — a claim derived from a simulation event, a tainted LLM call (one that read
   Layer C content) or another tainted claim is rejected, even if an LLM rewrote the wording;
3. provenance envelope — grounding origins must cite a raw artifact that an evidence adapter (a
   different credential than the simulation worker) actually stored, with a matching content hash;
4. content fingerprint — a claim whose normalised content matches committed simulated content is
   quarantined for curator review instead of being accepted.
"""

from __future__ import annotations

from ..common import ProvenanceRejected
from ..model_ir.enums import FORBIDDEN_EVIDENCE_ORIGINS, GROUNDING_ORIGINS, ClaimStatus, OriginKind
from ..model_ir.schemas import EvidenceClaim
from ..storage.credentials import Credential
from ..storage.sqlite_store import Store


class EvidenceIngestionService:
    def __init__(self, store: Store, cred: Credential):
        self.store = store
        self.cred = cred

    def _reject(self, claim: EvidenceClaim, reason: str) -> None:
        self.store.audit(self.cred, "ingest_claim", f"external_claims:{claim.claim_id}", False, reason)
        raise ProvenanceRejected(reason)

    def _tainted_lineage(self, refs: list[str], depth: int = 0) -> str | None:
        if depth > 8:
            return "lineage too deep to verify"
        for ref in refs:
            kind, _, ident = ref.partition(":")
            if kind == "sim" or self.store.simulation_event_exists(ref) or self.store.simulation_event_exists(ident):
                return f"derived from simulation event {ref}"
            if kind == "llm":
                rec = self.store.get_llm_record(ident)
                if rec is None:
                    return f"derived from unrecorded LLM call {ident}"
                if rec.get("tainted_by_simulation"):
                    return f"derived from LLM call {ident} whose input contained simulated content"
            if kind == "claim":
                parent = self.store.get_claim(ident)
                if parent is None:
                    return f"derived from unknown claim {ident}"
                if parent.origin_kind in FORBIDDEN_EVIDENCE_ORIGINS:
                    return f"derived from simulated claim {ident}"
                why = self._tainted_lineage(parent.derived_from, depth + 1)
                if why:
                    return why
        return None

    def ingest(self, claim: EvidenceClaim, tenant: str = "default") -> EvidenceClaim:
        if claim.origin_kind in FORBIDDEN_EVIDENCE_ORIGINS:
            self._reject(claim, "origin_kind SIMULATION_OUTPUT can never enter the evidence ledger")
        why = self._tainted_lineage(claim.derived_from)
        if why:
            self._reject(claim, f"lineage taint: {why}")
        if claim.origin_kind in GROUNDING_ORIGINS:
            if not claim.source_refs:
                self._reject(claim, "grounding origin without a provenance envelope (no source refs)")
            for ref in claim.source_refs:
                art = self.store.get_raw_artifact(ref.artifact_id) if ref.artifact_id else self.store.find_artifact_by_hash(ref.content_hash)
                if art is None or art.content_hash != ref.content_hash:
                    self._reject(claim, f"source {ref.uri} was not retrieved by an evidence adapter (no matching raw artifact)")
        if claim.origin_kind == OriginKind.MODEL_HYPOTHESIS:
            claim.status = ClaimStatus.HYPOTHESIS
        hits = []
        for text in (f"{claim.subject} {claim.predicate} {claim.object}", f"{claim.subject} {claim.predicate}"):
            hits += self.store.is_simulation_fingerprint(text)
        if hits:
            claim.status = ClaimStatus.QUARANTINED
            claim.notes.append(f"matches simulated content in {len(hits)} committed event(s); quarantined for curator review")
            self.store.audit(self.cred, "ingest_claim", f"external_claims:{claim.claim_id}", True,
                             "ALERT: content fingerprint matches simulation output; quarantined")
        self.store.put_claim(self.cred, claim, tenant)
        return claim

"""Real human evidence and profile learning (§12) — a separate store from Layers A/B/C.

Simulation produces hypotheses; people produce evidence. The interpreter follows the signal ladder
(§12.2): exposure changes nothing, inspection suggests *relevance* (never direction), explicit
selections and corrections become candidate contextual evidence, and every change is a proposal —
automated interpretations run in shadow mode; durable or hard changes need the user's approval.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..common import PermissionDenied, ProvenanceRejected, new_id
from ..model_ir.enums import IMPLICIT_SIGNALS, FeatureStatus, SignalType
from ..model_ir.schemas import (
    FeatureChange,
    HumanEvidenceEvent,
    ProfileFeature,
    ProfileUpdateProposal,
    RealProfileVersion,
)
from ..storage.credentials import Credential
from ..storage.sqlite_store import Store

RULE_VERSION = "signal-ladder/0.5.0"
SIM_MARKERS = ("agent_", "simulat", "run_", "sim:")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HumanEvidenceService:
    def __init__(self, store: Store, endpoint_cred: Credential, updater_cred: Credential):
        self.store = store
        self.endpoint_cred = endpoint_cred
        self.updater_cred = updater_cred

    # ---------------- ingestion ----------------
    def ingest(self, ev: HumanEvidenceEvent, authenticated_user: str) -> HumanEvidenceEvent | None:
        if ev.user_id != authenticated_user:
            self.store.audit(self.endpoint_cred, "ingest_human_evidence", ev.event_id, False, "user mismatch")
            raise PermissionDenied("human evidence must come from the authenticated user it describes")
        source = str(ev.payload.get("source", "")).lower()
        actor = str(ev.payload.get("actor_id", ""))
        if any(m in source for m in ("agent", "simulat")) or actor.startswith("agent_") or ev.payload.get("origin_kind") == "SIMULATION_OUTPUT":
            self.store.audit(self.endpoint_cred, "ingest_human_evidence", ev.event_id, False, "simulated behaviour presented as human evidence")
            raise ProvenanceRejected("Agent-generated or simulated behaviour can never be ingested as human evidence")
        if ev.signal_type in IMPLICIT_SIGNALS and not ev.consent.implicit_learning:
            self.store.audit(self.endpoint_cred, "ingest_human_evidence", ev.event_id, True,
                             "implicit signal not stored: user has not opted in to implicit learning")
            return None
        if ev.decision_id not in ("onboarding",):
            spec = self.store.get_decision_spec(ev.decision_id)
            if spec is None:
                raise ValueError(f"unknown decision {ev.decision_id}")
            if self.store.decision_role(ev.decision_id, ev.user_id) is None:
                raise PermissionDenied("user is not a participant in this decision")
            if ev.decision_revision != spec.revision and ev.signal_type in (SignalType.EXPLICIT_SELECTION,):
                ev.payload = {**ev.payload, "stale_revision_note": f"bound to revision {ev.decision_revision}; current is {spec.revision}"}
        self.store.put_human_evidence(self.endpoint_cred, ev)
        return ev

    # ---------------- interpretation (§12.2 ladder) ----------------
    def interpret(self, ev: HumanEvidenceEvent) -> dict[str, Any]:
        st = ev.signal_type
        dims = list(ev.payload.get("dimensions", []))
        if st == SignalType.EXPOSURE:
            return {"interpretation": "user had an opportunity to view", "update_policy": "no change", "changes": []}
        if st == SignalType.OVERVIEW_OPEN:
            return {"interpretation": "attracted attention; may reflect presentation order", "update_policy": "relevance hypothesis only", "changes": []}
        if st in (SignalType.DETAILED_INSPECTION, SignalType.TRACE_INSPECTION, SignalType.COMPARISON):
            changes = [{"key_dim": d, "change_kind": "relevance"} for d in dims]
            return {"interpretation": "dimension may matter in this decision (relevance, not direction)",
                    "update_policy": "relevance only; no polarity asserted", "changes": changes}
        if st == SignalType.EXPLICIT_SELECTION:
            return {"interpretation": "contextual stated preference among the options shown",
                    "update_policy": "candidate feature evidence; options shown preserved", "changes": [
                        {"key": k, "value": v, "change_kind": "value"} for k, v in ev.payload.get("feature_hints", {}).items()]}
        if st == SignalType.EXPLICIT_CORRECTION:
            key = ev.payload.get("feature_key")
            if not key:
                return {"interpretation": "free-text correction; needs review", "update_policy": "offer correction", "changes": []}
            kind = "constraint_candidate" if ev.payload.get("hard") else "value"
            return {"interpretation": "strong evidence of a contextual boundary or concern",
                    "update_policy": "propose update; confirm if durable/hard",
                    "changes": [{"key": key, "value": ev.payload.get("value"), "change_kind": kind, "scope": ev.payload.get("scope", "general")}]}
        return {"interpretation": "real-world choice in its documented context", "update_policy": "higher behavioural evidence, not a unique causal explanation",
                "changes": [{"key": k, "value": v, "change_kind": "value"} for k, v in ev.payload.get("feature_hints", {}).items()]}

    def propose(self, ev: HumanEvidenceEvent, profile_id: str) -> ProfileUpdateProposal | None:
        prof = self.store.get_profile(profile_id)
        if prof is None or prof.user_id != ev.user_id:
            raise PermissionDenied("evidence can only update its own author's profile")
        interp = self.interpret(ev)
        feats = {f.key: f for f in prof.features}
        changes: list[FeatureChange] = []
        for ch in interp["changes"]:
            if ch["change_kind"] == "relevance":
                dim = ch["key_dim"]
                key = next((k for k in feats if k.split(".", 1)[-1] == dim), f"relevance.{dim}")
                before = feats.get(key)
                after = (before.model_copy(deep=True) if before else ProfileFeature(key=key, status=FeatureStatus.UNKNOWN))
                after.relevance = round(min(1.0, (after.relevance or 0.0) + 0.1), 3)
                after.evidence_refs = sorted(set(after.evidence_refs) | {ev.event_id})
                after.note = "relevance raised by inspection; direction not inferred"
                changes.append(FeatureChange(key=key, before=before, after=after, change_kind="relevance"))
            else:
                key = ch["key"]
                before = feats.get(key)
                status = FeatureStatus.HARD_CONSTRAINT if ch["change_kind"] == "constraint_candidate" else FeatureStatus.CONFIRMED
                after = ProfileFeature(key=key, value=ch.get("value"), status=status, scope=ch.get("scope", "general"),
                                       epistemic_confidence="high" if ev.signal_type == SignalType.EXPLICIT_CORRECTION else "medium",
                                       evidence_refs=sorted(set((before.evidence_refs if before else [])) | {ev.event_id}),
                                       relevance=max(before.relevance or 0.0, 0.7) if before else 0.7,
                                       privacy=before.privacy if before else "private",
                                       feature_version=(before.feature_version + 1) if before else 1,
                                       note=f"from {ev.signal_type}: {ev.payload.get('statement', '')}"[:280])
                if before and before.value not in (None, after.value) and before.status in (FeatureStatus.CONFIRMED, FeatureStatus.HARD_CONSTRAINT):
                    after.note += " | contradicts earlier confirmed value: scope it rather than overwrite globally"
                changes.append(FeatureChange(key=key, before=before, after=after, change_kind=ch["change_kind"] if ch["change_kind"] != "value" or before else "new_feature"))
        if not changes:
            return None
        implicit = ev.signal_type in IMPLICIT_SIGNALS
        proposal = ProfileUpdateProposal(
            proposal_id=new_id("prop"), profile_id=profile_id, user_id=ev.user_id, base_version=prof.version, changes=changes,
            evidence_ids=[ev.event_id], interpretation_rule=RULE_VERSION, mode="shadow" if implicit else "visible",
            requires_user_confirmation=True, status="SHADOW_ONLY" if implicit else "PROPOSED",
            reason=interp["interpretation"], created_at=_now(),
        )
        self.store.put_profile_proposal(self.updater_cred, proposal)
        return proposal

    def decide(self, proposal_id: str, user_id: str, approve: bool) -> RealProfileVersion | None:
        p = self.store.get_profile_proposal(proposal_id)
        if p is None:
            raise KeyError(proposal_id)
        if p.user_id != user_id:
            raise PermissionDenied("only the profile owner can approve changes")
        if p.status != "PROPOSED":
            raise ValueError(f"proposal is {p.status}; shadow-mode proposals are never applied automatically")
        if not approve:
            p.status = "REJECTED"
            self.store.put_profile_proposal(self.updater_cred, p)
            return None
        current = self.store.get_profile(p.profile_id)
        if current.version != p.base_version:
            raise ValueError("profile changed since the proposal was made; re-interpret against the current version")
        feats = {f.key: f for f in current.features}
        for ch in p.changes:
            if ch.after is not None:
                feats[ch.key] = ch.after
        new = RealProfileVersion(profile_id=current.profile_id, user_id=current.user_id, version=current.version + 1,
                                 features=list(feats.values()), evidence_ids=sorted(set(current.evidence_ids) | set(p.evidence_ids)),
                                 created_at=_now(), created_by="profile_updater", reason=f"approved proposal {p.proposal_id}: {p.reason}",
                                 parent_version=current.version, implicit_learning_opt_in=current.implicit_learning_opt_in)
        self.store.put_profile_version(self.updater_cred, new)
        p.status = "APPLIED"
        self.store.put_profile_proposal(self.updater_cred, p)
        return new

    def rollback(self, profile_id: str, to_version: int, user_id: str) -> RealProfileVersion:
        current = self.store.get_profile(profile_id)
        if current is None or current.user_id != user_id:
            raise PermissionDenied("only the owner can roll back a profile")
        old = self.store.get_profile(profile_id, to_version)
        new = old.model_copy(update={"version": current.version + 1, "created_at": _now(), "parent_version": current.version,
                                     "reason": f"rollback to v{to_version} (audit trail preserved)",
                                     "evidence_ids": sorted(set(old.evidence_ids))})
        self.store.put_profile_version(self.updater_cred, new)
        return new

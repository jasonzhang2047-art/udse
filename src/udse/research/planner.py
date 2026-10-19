"""Research Planner + acquisition (§5.1, §5.4): research is a planned computation.

Needs are ranked with a transparent rule-based ordinal (not invented probability scores). Private
decision facts are asked of the user instead of searched for. Future conditions cannot be observed:
forecast inputs remain unresolved and are represented as named conditional scenarios.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..common import ProvenanceRejected, content_hash, new_id
from ..model_ir.enums import ClaimStatus, OriginKind, RecordType, ResearchNeedKind
from ..model_ir.schemas import DecisionSpec, EvidenceBundle, EvidenceClaim, ResearchNeed
from ..storage.credentials import Credential
from ..storage.sqlite_store import Store
from .adapters import SourceAdapter
from .ingestion import EvidenceIngestionService
from .normalizer import EvidenceNormalizer

USABLE = {ClaimStatus.VERIFIED, ClaimStatus.UNVERIFIED_EXTRACT}


def _claim_matches(c: EvidenceClaim, need: ResearchNeed) -> bool:
    for t in need.target_refs:
        if "." in t:
            if f"{c.subject}.{c.predicate}" == t:
                return True
        elif c.subject == t or c.predicate == t:
            return True
    return False


def user_claims(spec: DecisionSpec) -> list[EvidenceClaim]:
    """User-asserted facts are Layer B decision context (tenant-private), labelled USER_ASSERTED."""
    out = []
    for f in spec.initial_facts:
        subj, _, pred = f.ref.rpartition(".")
        out.append(EvidenceClaim(
            claim_id=f.evidence_ref or f"user_claim_{f.ref}", origin_kind=f.origin, record_type=RecordType.USER_STATEMENT,
            subject=subj or f.ref, predicate=pred or "value", object=f.value, unit=f.unit, status=ClaimStatus.UNVERIFIED_EXTRACT,
            scope={"decision_id": spec.decision_id, "private": True}, license_scope="user_private",
            notes=["user-asserted decision fact"] + ([f.description] if f.description else []),
        ))
    for c in spec.constraints:
        if c.evidence_ref:
            continue
        out.append(EvidenceClaim(
            claim_id=f"user_constraint_{c.id}", origin_kind=OriginKind.USER_ASSERTED, record_type=RecordType.USER_STATEMENT,
            subject=c.ref or c.id, predicate="hard_constraint" if c.hard else "soft_constraint",
            object={"min": c.min, "max": c.max, "expr": c.expr}, status=ClaimStatus.UNVERIFIED_EXTRACT,
            scope={"decision_id": spec.decision_id, "private": True}, license_scope="user_private",
            notes=[f"user-declared {'hard' if c.hard else 'soft'} constraint: {c.description}"],
        ))
    for a in spec.alternatives:
        for asm in a.assumptions:
            if asm.evidence_ref:
                continue
            subj, _, pred = asm.ref.rpartition(".")
            out.append(EvidenceClaim(
                claim_id=f"user_claim_{a.id}_{asm.ref}", origin_kind=asm.origin, record_type=RecordType.USER_STATEMENT,
                subject=subj or asm.ref, predicate=pred or "value", object=asm.value if asm.value is not None else asm.delta,
                unit=asm.unit, scope={"decision_id": spec.decision_id, "alternative": a.id, "private": True},
                license_scope="user_private", notes=[f"user-stated consequence of alternative {a.id}: {asm.description}"],
            ))
    return out


class ResearchPlanner:
    def plan(self, spec: DecisionSpec, slot_needs: list[dict[str, Any]], generic_needs: list[dict[str, Any]],
             trigger_diagnostic_id: str | None = None) -> list[ResearchNeed]:
        needs: list[ResearchNeed] = []
        seen = set()
        for raw in slot_needs + generic_needs:
            key = (raw["kind"], tuple(sorted(raw.get("target_refs", []))), raw["question"])
            if key in seen:
                continue
            seen.add(key)
            kind = ResearchNeedKind(raw["kind"])
            critical = raw.get("critical", False)
            if kind == ResearchNeedKind.PRIVATE_FACT:
                prio, why = 1 if critical else 2, "private decision fact: ask the user (never searched without permission)"
            elif kind == ResearchNeedKind.FORECAST_INPUT:
                prio, why = 3, "future conditions cannot be observed; research can only supply historical reference ranges"
            elif critical:
                prio, why = 1, "could change feasibility or violate a hard constraint"
            elif raw.get("affects_metrics"):
                prio, why = 2, "influences a decision metric"
            else:
                prio, why = 3, "secondary context"
            needs.append(ResearchNeed(
                need_id=raw.get("need_id") or new_id("need"), kind=kind, question=raw["question"],
                why_it_matters=raw.get("why", why), target_refs=raw.get("target_refs", []),
                affects_metrics=raw.get("affects_metrics", []), affects_families=raw.get("affects_families", []),
                candidate_sources=raw.get("candidate_sources", []), permitted_scopes=raw.get("permitted_scopes", ["public"]),
                freshness=raw.get("freshness"), jurisdiction=spec.jurisdiction, est_cost=float(raw.get("est_cost", 1.0)),
                consequence_if_unresolved=raw.get("consequence", "value remains UNKNOWN; dependent mechanisms are downgraded or abstain"),
                priority=prio, priority_rationale=why, trigger_diagnostic_id=trigger_diagnostic_id,
                status="ASK_USER" if kind == ResearchNeedKind.PRIVATE_FACT else "OPEN",
            ))
        needs.sort(key=lambda n: (n.priority, n.need_id))
        return needs


class ResearchAcquirer:
    def __init__(self, store: Store, adapter_cred: Credential, ingestion_cred: Credential):
        self.store = store
        self.adapter_cred = adapter_cred
        self.ingestion = EvidenceIngestionService(store, ingestion_cred)
        self.ingestion_cred = ingestion_cred

    def acquire(self, spec: DecisionSpec, needs: list[ResearchNeed], adapters: list[SourceAdapter], as_of: datetime,
                budget_units: float = 50.0) -> EvidenceBundle:
        policy = spec.research_policy
        normalizer = EvidenceNormalizer(as_of, spec.jurisdiction)
        fetched: dict[str, Any] = {}
        claims_by_need: dict[str, list[str]] = {}
        raw_claims: dict[str, EvidenceClaim] = {}
        warnings: list[str] = []
        rejected: list[dict[str, Any]] = []
        spent = 0.0
        for ad in adapters:
            if hasattr(ad, "as_of"):
                ad.as_of = as_of
        for need in needs:
            if need.status in ("ASK_USER", "SKIPPED_PERMISSION"):
                continue
            if spent + need.est_cost > budget_units:
                need.status = "UNRESOLVED"
                need.consequence_if_unresolved += " (research budget exhausted before this need)"
                continue
            spent += need.est_cost
            found_ids: list[str] = []
            for ad in adapters:
                if ad.name not in policy.allowed_adapters:
                    continue
                if ad.access_scope == "public" and policy.external_web != "allowed":
                    warnings.append(f"adapter {ad.name} skipped: external research not permitted")
                    continue
                if ad.access_scope == "private" and policy.private_sources == "denied":
                    need.status = "SKIPPED_PERMISSION"
                    warnings.append(f"adapter {ad.name} skipped for {need.need_id}: private sources not consented")
                    continue
                for doc in ad.search(need):
                    key = f"{ad.name}:{doc.get('uri')}"
                    if key not in fetched:
                        if doc.get("access") == "private_unauthorized":
                            warnings.append(f"{doc.get('uri')} requires unauthorized private access; skipped (no access escalation)")
                            fetched[key] = None
                            continue
                        fr = ad.fetch(doc)
                        self.store.put_raw_artifact(self.adapter_cred, fr.artifact)
                        cl, rej, warn = normalizer.build_claims(fr)
                        rejected += rej
                        warnings += warn
                        fetched[key] = cl
                        for c in cl:
                            raw_claims[c.claim_id] = c
                    for c in fetched[key] or []:
                        if _claim_matches(c, need):
                            found_ids.append(c.claim_id)
            claims_by_need[need.need_id] = sorted(set(found_ids))
        reconciled, conflicts = normalizer.reconcile(list(raw_claims.values()))
        accepted: list[EvidenceClaim] = []
        for c in reconciled:
            try:
                accepted.append(self.ingestion.ingest(c))
            except ProvenanceRejected as e:
                rejected.append({"claim_id": c.claim_id, "reason": str(e)})
        acc_by_id = {c.claim_id: c for c in accepted}
        for need in needs:
            ids = [i for i in claims_by_need.get(need.need_id, []) if i in acc_by_id]
            need.resolution_claim_ids = ids
            if need.status in ("ASK_USER", "SKIPPED_PERMISSION", "UNRESOLVED"):
                continue
            usable = [acc_by_id[i] for i in ids if acc_by_id[i].status in USABLE and acc_by_id[i].object != "UNKNOWN"
                      and acc_by_id[i].record_type != RecordType.CONTEXTUAL_LEAD]
            if need.kind == ResearchNeedKind.FORECAST_INPUT:
                need.status = "UNRESOLVED"
                need.consequence_if_unresolved = "future conditions are represented as named conditional scenarios without asserted probabilities"
            elif usable:
                need.status = "RESOLVED"
            else:
                need.status = "UNRESOLVED"
        bundle = EvidenceBundle(
            bundle_id=new_id("bundle"), decision_id=spec.decision_id, decision_rev=spec.revision, as_of=as_of.isoformat(),
            claims=accepted + user_claims(spec), artifacts=[], needs=needs, rejected=rejected, conflicts=conflicts,
            warnings=sorted(set(warnings)),
        )
        bundle.hash = content_hash({"claims": [c.model_dump(mode="json") for c in bundle.claims],
                                    "needs": [n.model_dump(mode="json") for n in needs]})
        self.store.put_evidence_bundle(self.ingestion_cred, bundle)
        return bundle


def claim_value_status(bundle: EvidenceBundle, subject: str, predicate: str) -> tuple[EvidenceClaim | None, str]:
    """Best usable claim for (subject, predicate) with an explainable reason when none is usable."""
    cands = [c for c in bundle.claims if c.subject == subject and c.predicate == predicate]
    if not cands:
        return None, "NO_EVIDENCE"
    conflicted = [c for c in cands if c.status == ClaimStatus.CONFLICTED]
    if conflicted:
        return None, "CONFLICTED"
    rank = {ClaimStatus.VERIFIED: 0, ClaimStatus.UNVERIFIED_EXTRACT: 1}
    usable = sorted((c for c in cands if c.status in rank and c.record_type != RecordType.CONTEXTUAL_LEAD),
                    key=lambda c: (c.object == "UNKNOWN", rank[c.status], _neg_time(c.observed_at), c.claim_id))
    if usable:
        return usable[0], usable[0].status.value
    if any(c.status == ClaimStatus.STALE for c in cands):
        return next(c for c in cands if c.status == ClaimStatus.STALE), "STALE"
    if any(c.status == ClaimStatus.INAPPLICABLE for c in cands):
        return None, "INAPPLICABLE"
    leads = [c for c in cands if c.record_type == RecordType.CONTEXTUAL_LEAD]
    if leads:
        return None, "CONTEXTUAL_LEAD_ONLY"
    return None, "UNUSABLE"


def _neg_time(ts: str | None) -> float:
    from ..common import parse_dt

    try:
        return -parse_dt(ts).timestamp() if ts else 0.0
    except ValueError:
        return 0.0


def is_grounding(c: EvidenceClaim) -> bool:
    return c.origin_kind in (OriginKind.REAL_EXTERNAL_OBSERVATION, OriginKind.OFFICIAL_RULE, OriginKind.HISTORICAL_DATASET) \
        and c.status == ClaimStatus.VERIFIED and c.record_type != RecordType.CONTEXTUAL_LEAD

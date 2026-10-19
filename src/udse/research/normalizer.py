"""Evidence normalisation (§5.3): candidate claim → typed EvidenceClaim with provenance, freshness,
applicability, conflict and independence handling. UNKNOWN stays UNKNOWN; nothing is guessed."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from ..common import canonical_json, parse_dt, sha256_hex
from ..model_ir.enums import ClaimStatus, OriginKind, RecordType
from ..model_ir.schemas import EvidenceClaim, SourceRef
from .adapters import ADAPTER_DEFAULTS, FetchResult

INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior) instructions",
    r"\bsystem prompt\b",
    r"you (must|should) (now )?(mark|set|record|treat|promote)",
    r"disregard (the )?(rules|policy|policies)",
    r"as an ai",
    r"<\s*/?\s*(system|instructions?)\s*>",
]


def injection_suspected(text: str) -> bool:
    t = text.lower()
    return any(re.search(p, t) for p in INJECTION_PATTERNS)


def _jurisdiction_ok(claim_j: str | None, target_j: str | None) -> bool:
    if not claim_j or not target_j or claim_j in ("*", "illustrative"):
        return True
    return target_j.upper().startswith(claim_j.upper()) or claim_j.upper().startswith(target_j.upper())


class EvidenceNormalizer:
    version = "normalizer/0.5.0"

    def __init__(self, as_of: datetime, jurisdiction: str | None):
        self.as_of = as_of
        self.jurisdiction = jurisdiction

    def build_claims(self, fr: FetchResult) -> tuple[list[EvidenceClaim], list[dict[str, Any]], list[str]]:
        defaults = ADAPTER_DEFAULTS.get(fr.source_kind, ADAPTER_DEFAULTS["third_party_summary"])
        claims, rejected, warnings = [], [], []
        suspicious = injection_suspected(fr.artifact.content)
        if suspicious:
            warnings.append(
                f"Source {fr.artifact.uri} contains instruction-like text; treated strictly as data (no instructions executed)."
            )
        for c in fr.candidates:
            origin = OriginKind(c.get("origin_kind", defaults["origin"]))
            if origin == OriginKind.SIMULATION_OUTPUT:
                rejected.append({"candidate": c, "reason": "SIMULATION_OUTPUT can never be ingested as evidence"})
                continue
            record_type = RecordType(c.get("record_type", defaults["record_type"]))
            obj = c.get("object", "UNKNOWN")
            key = canonical_json({"s": c["subject"], "p": c["predicate"], "o": obj, "u": fr.artifact.uri})
            claim_id = c.get("claim_id") or f"claim_{sha256_hex(key)[:12]}"
            observed = c.get("observed_at") or fr.source_meta.get("observed_at") or fr.artifact.retrieved_at
            expires = c.get("expires_at")
            fd = c.get("freshness_days", defaults["freshness_days"])
            if expires is None and fd is not None and observed:
                expires = (parse_dt(observed) + timedelta(days=fd)).isoformat()
            jur = c.get("jurisdiction") or fr.source_meta.get("jurisdiction")
            notes = []
            status = ClaimStatus.UNVERIFIED_EXTRACT
            if origin == OriginKind.MODEL_HYPOTHESIS:
                status = ClaimStatus.HYPOTHESIS
                notes.append("LLM/model hypothesis: never measured real-world evidence")
            elif expires and self.as_of > parse_dt(expires):
                status = ClaimStatus.STALE
                notes.append(f"stale: expired {expires} (as of {self.as_of.isoformat()})")
            elif not _jurisdiction_ok(jur, self.jurisdiction):
                status = ClaimStatus.INAPPLICABLE
                notes.append(f"inapplicable: claim jurisdiction {jur} vs decision {self.jurisdiction}")
            elif defaults["primary"] and record_type != RecordType.CONTEXTUAL_LEAD and origin != OriginKind.USER_ASSERTED:
                status = ClaimStatus.VERIFIED
            if obj == "UNKNOWN":
                notes.append("source reports no value: UNKNOWN is preserved, not filled in")
            if suspicious:
                notes.append("source flagged for instruction-like content")
            claims.append(
                EvidenceClaim(
                    claim_id=claim_id, origin_kind=origin, record_type=record_type, subject=c["subject"],
                    predicate=c["predicate"], object=obj, unit=c.get("unit"), claim_valid_at=c.get("claim_valid_at"),
                    observed_at=observed, retrieved_at=fr.artifact.retrieved_at, expires_at=expires,
                    verified_at=self.as_of.isoformat() if status == ClaimStatus.VERIFIED else None,
                    source_refs=[SourceRef(uri=fr.artifact.uri, content_hash=fr.artifact.content_hash, span=c.get("span"),
                                           artifact_id=fr.artifact.artifact_id)],
                    scope={"jurisdiction": jur, "entity": c["subject"], **c.get("scope", {})}, status=status,
                    original_source_id=c.get("original_source_id") or fr.source_meta.get("source_id"),
                    license_scope=fr.artifact.license_scope, extractor_version=self.version,
                    quality={"directness": "primary" if defaults["primary"] else "secondary", "source_kind": fr.source_kind,
                             "injection_suspected": suspicious},
                    notes=notes,
                )
            )
        return claims, rejected, warnings

    def reconcile(self, claims: list[EvidenceClaim]) -> tuple[list[EvidenceClaim], list[dict[str, Any]]]:
        """Conflict + independence handling with an explainable adjudication rule: a primary
        source outranks a contextual lead; otherwise both are kept and marked CONFLICTED."""
        conflicts = []
        by_key: dict[tuple[str, str], list[EvidenceClaim]] = {}
        for c in claims:
            if c.status in (ClaimStatus.STALE, ClaimStatus.INAPPLICABLE, ClaimStatus.HYPOTHESIS, ClaimStatus.REJECTED):
                continue
            by_key.setdefault((c.subject, c.predicate), []).append(c)
        for (subj, pred), group in by_key.items():
            # Repetition of one original source is not independent support.
            originals: dict[str, list[EvidenceClaim]] = {}
            for c in group:
                originals.setdefault(c.original_source_id or c.claim_id, []).append(c)
            for orig, members in originals.items():
                if len(members) > 1:
                    for m in members:
                        m.notes.append(f"{len(members)} records repeat original source {orig}; counted as one source")
            values = {canonical_json(c.object) for c in group if c.object != "UNKNOWN"}
            if len(values) <= 1:
                for c in group:
                    others = [o.claim_id for o in group if o is not c and canonical_json(o.object) == canonical_json(c.object)]
                    c.supported_by = sorted(set(c.supported_by) | set(others))
                continue
            primaries = [c for c in group if c.quality.get("directness") == "primary" and c.record_type != RecordType.CONTEXTUAL_LEAD]
            leads = [c for c in group if c not in primaries]
            primary_values = {canonical_json(c.object) for c in primaries if c.object != "UNKNOWN"}
            if len(primary_values) == 1 and leads:
                for lead in leads:
                    if canonical_json(lead.object) not in primary_values:
                        for p in primaries:
                            p.conflicting_claim_ids.append(lead.claim_id)
                        lead.conflicting_claim_ids.extend(p.claim_id for p in primaries)
                        lead.notes.append("contextual lead contradicted by a primary source; primary source retained")
                conflicts.append({"subject": subj, "predicate": pred, "resolution": "primary_over_contextual_lead",
                                  "claims": [c.claim_id for c in group]})
                continue
            for c in group:
                c.status = ClaimStatus.CONFLICTED
                c.conflicting_claim_ids = sorted({o.claim_id for o in group if o is not c})
                c.notes.append("conflicting values retained; resolution requested (never selected silently)")
            conflicts.append({"subject": subj, "predicate": pred, "resolution": "unresolved_conflict",
                              "claims": [c.claim_id for c in group]})
        return claims, conflicts

"""Source adapters (§5.2). Retrieved content is untrusted data: adapters return raw artifacts and
candidate claim records; they never execute or obey instructions found in sources."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..common import new_id, sha256_hex
from ..model_ir.enums import OriginKind, RecordType
from ..model_ir.schemas import RawSourceArtifact, ResearchNeed

ADAPTER_DEFAULTS: dict[str, dict[str, Any]] = {
    "official_page": {"origin": OriginKind.REAL_EXTERNAL_OBSERVATION, "record_type": RecordType.OBSERVATION, "freshness_days": 90, "primary": True},
    "official_rule": {"origin": OriginKind.OFFICIAL_RULE, "record_type": RecordType.OFFICIAL_RULE, "freshness_days": None, "primary": True},
    "provider_api": {"origin": OriginKind.REAL_EXTERNAL_OBSERVATION, "record_type": RecordType.OBSERVATION, "freshness_days": 1, "primary": True},
    "filing": {"origin": OriginKind.HISTORICAL_DATASET, "record_type": RecordType.OBSERVATION, "freshness_days": None, "primary": True},
    "dataset": {"origin": OriginKind.HISTORICAL_DATASET, "record_type": RecordType.OBSERVATION, "freshness_days": None, "primary": True},
    "research_paper": {"origin": OriginKind.HISTORICAL_DATASET, "record_type": RecordType.ANALYST_MODEL, "freshness_days": None, "primary": True},
    "third_party_summary": {"origin": OriginKind.REAL_EXTERNAL_OBSERVATION, "record_type": RecordType.CONTEXTUAL_LEAD, "freshness_days": 365, "primary": False},
    "user_attachment": {"origin": OriginKind.USER_ASSERTED, "record_type": RecordType.USER_STATEMENT, "freshness_days": None, "primary": True},
}


@dataclass
class FetchResult:
    artifact: RawSourceArtifact
    candidates: list[dict[str, Any]]
    source_kind: str
    source_meta: dict[str, Any] = field(default_factory=dict)


class SourceAdapter(Protocol):
    name: str
    access_scope: str  # "public" | "private"

    def search(self, need: ResearchNeed) -> list[dict[str, Any]]: ...

    def fetch(self, doc: dict[str, Any]) -> FetchResult: ...


def _doc_matches(doc: dict[str, Any], need: ResearchNeed) -> bool:
    targets = set(need.target_refs)
    for c in doc.get("claims", []):
        subj, pred = c.get("subject"), c.get("predicate")
        if subj in targets or f"{subj}.{pred}" in targets or pred in targets:
            return True
    return bool(set(doc.get("topics", [])) & targets)


class FixtureSourceAdapter:
    """Serves synthetic, clearly-marked fixture sources (example.invalid URIs) as if retrieved from
    official pages, provider APIs, filings and third-party summaries. No live availability claims."""

    name = "fixture"
    access_scope = "public"

    def __init__(self, sources_path: str | Path):
        data = json.loads(Path(sources_path).read_text())
        self.documents: list[dict[str, Any]] = data["documents"]
        self.as_of = None  # research time; documents retrieved later do not exist yet

    def search(self, need: ResearchNeed) -> list[dict[str, Any]]:
        from ..common import parse_dt

        return [d for d in self.documents if _doc_matches(d, need)
                and (self.as_of is None or parse_dt(d["retrieved_at"]) <= self.as_of)]

    def fetch(self, doc: dict[str, Any]) -> FetchResult:
        content = doc.get("content", "")
        h = "sha256:" + sha256_hex(content + json.dumps(doc.get("claims", []), sort_keys=True))
        art = RawSourceArtifact(
            artifact_id=f"art_{h[7:19]}", uri=doc["uri"], adapter=f"{self.name}:{doc['kind']}", content_hash=h,
            retrieved_at=doc["retrieved_at"], access={"scope": doc.get("access", "public"), "robots": "fixture"},
            license_scope=doc.get("license_scope", "fixture_only"), content=content,
        )
        return FetchResult(artifact=art, candidates=list(doc.get("claims", [])), source_kind=doc["kind"],
                           source_meta={"source_id": doc.get("source_id"), "jurisdiction": doc.get("jurisdiction"),
                                        "observed_at": doc.get("observed_at"), "access": doc.get("access", "public")})


class UserAttachmentAdapter:
    """User-provided facts/documents attached to the DecisionSpec (consented, tenant-isolated)."""

    name = "user_attachment"
    access_scope = "private"

    def __init__(self, attachments: list[dict[str, Any]], retrieved_at: str):
        self.attachments = attachments
        self.retrieved_at = retrieved_at

    def search(self, need: ResearchNeed) -> list[dict[str, Any]]:
        return [a for a in self.attachments if _doc_matches(a, need)]

    def fetch(self, doc: dict[str, Any]) -> FetchResult:
        content = doc.get("content", "")
        h = "sha256:" + sha256_hex(content + json.dumps(doc.get("claims", []), sort_keys=True))
        art = RawSourceArtifact(
            artifact_id=f"art_{h[7:19]}", uri=doc.get("uri", f"attachment://{new_id('att')}"), adapter=self.name,
            content_hash=h, retrieved_at=self.retrieved_at, access={"scope": "private", "consent": True},
            license_scope="user_private", content=content,
        )
        return FetchResult(artifact=art, candidates=list(doc.get("claims", [])), source_kind="user_attachment",
                           source_meta={"source_id": doc.get("source_id"), "access": "private"})

"""Composition root: wires every component with its own credential (§2.2) and exposes the
end-to-end operations used by the API, CLI and tests.

The simulation worker receives only the simulation-worker credential; the curator, ingestion,
profile-updater and evidence-adapter credentials live in separate services.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import new_id, parse_dt
from .llm.provider import LLMProvider, provider_from_env
from .model_ir.enums import RegistryStatus, Role, SignalType
from .model_ir.schemas import (
    ConsentFlags,
    DecisionSpec,
    HumanEvidenceEvent,
    ProfileFeature,
    RealProfileVersion,
    ResearchNeed,
)
from .model_registry.registry import CuratorService, Registry, RegistryEntry
from .model_factory.factory import BuildResult, ModelFactory
from .research.adapters import FixtureSourceAdapter, SourceAdapter, UserAttachmentAdapter
from .research.normalizer import EvidenceNormalizer
from .research.ingestion import EvidenceIngestionService
from .storage.sqlite_store import Store

DEFAULT_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Engine:
    def __init__(self, db_path: str = ":memory:", fixtures_dir: str | Path | None = None, llm: LLMProvider | None = None,
                 use_env_llm: bool = False):
        self.fixtures_dir = Path(fixtures_dir or os.environ.get("UDSE_FIXTURES_DIR", DEFAULT_FIXTURES))
        self.store = Store(db_path)
        auth = self.store.authority
        self.creds = {role: auth.issue(role) for role in Role}
        self.llm = llm if llm is not None else (provider_from_env() if use_env_llm else None)
        self.registry = Registry(self.store, self.fixtures_dir)
        self.curator = CuratorService(self.store, self.creds[Role.CURATOR])
        self.ingestion = EvidenceIngestionService(self.store, self.creds[Role.INGESTION])
        self.factory = ModelFactory(
            self.store, self.registry,
            {r: self.creds[r] for r in (Role.EVIDENCE_ADAPTER, Role.INGESTION, Role.RESEARCH_PLANNER, Role.MODEL_FACTORY)},
            self.adapters_for, self.llm,
        )
        self._source_files = sorted(self.fixtures_dir.glob("*/sources.json")) if self.fixtures_dir.exists() else []
        from .experiments.orchestrator import Orchestrator
        from .human_evidence.service import HumanEvidenceService

        self.orchestrator = Orchestrator(self)
        self.human = HumanEvidenceService(self.store, self.creds[Role.HUMAN_EVIDENCE_ENDPOINT], self.creds[Role.PROFILE_UPDATER])

    # ------------------------------------------------------------------
    # adapters + seeding
    # ------------------------------------------------------------------
    def adapters_for(self, spec: DecisionSpec) -> list[SourceAdapter]:
        adapters: list[SourceAdapter] = []
        merged = {"documents": []}
        for f in self._source_files:
            merged["documents"] += json.loads(f.read_text())["documents"]
        if merged["documents"]:
            fa = FixtureSourceAdapter.__new__(FixtureSourceAdapter)
            fa.documents = merged["documents"]
            fa.as_of = None
            adapters.append(fa)
        if spec.attachments:
            adapters.append(UserAttachmentAdapter(spec.attachments, _now().isoformat()))
        return adapters

    def seed_registry(self) -> None:
        reg_dir = self.fixtures_dir / "registry"
        if not (reg_dir / "registry.json").exists() or self.registry.entries():
            return
        src = FixtureSourceAdapter(reg_dir / "sources.json")
        normalizer = EvidenceNormalizer(parse_dt("2026-10-02T00:00:00+00:00"), None)
        for doc in src.documents:
            fr = src.fetch(doc)
            self.store.put_raw_artifact(self.creds[Role.EVIDENCE_ADAPTER], fr.artifact)
            claims, _, _ = normalizer.build_claims(fr)
            for c in claims:
                self.ingestion.ingest(c)
        for raw in json.loads((reg_dir / "registry.json").read_text())["entries"]:
            self.curator.register(RegistryEntry.model_validate(raw))

    def seed_profiles(self, profiles_file: Path) -> list[str]:
        data = json.loads(profiles_file.read_text())
        out = []
        for u in data["users"]:
            self.store.put_user(self.creds[Role.API], u["user_id"], u["display_name"], u["token"])
            if self.store.get_profile(u["profile_id"]) is not None:
                continue
            ev = HumanEvidenceEvent(
                event_id=f"hev_onboard_{u['user_id']}", user_id=u["user_id"], decision_id="onboarding", decision_revision=0,
                signal_type=SignalType.EXPLICIT_CORRECTION, consent=ConsentFlags(explicit_feedback=True),
                payload={"statement": u["onboarding"], "context": "onboarding questionnaire (fixture)"},
                created_at="2026-10-01T00:00:00+00:00", idempotency_key=f"onboard-{u['user_id']}",
            )
            self.human.ingest(ev, authenticated_user=u["user_id"])
            feats = [ProfileFeature(**{**f, "evidence_refs": [ev.event_id], "last_reviewed_at": "2026-10-01T00:00:00+00:00"})
                     for f in u["features"]]
            prof = RealProfileVersion(profile_id=u["profile_id"], user_id=u["user_id"], version=1, features=feats,
                                      evidence_ids=[ev.event_id], created_at="2026-10-01T00:00:00+00:00",
                                      reason="onboarding statements confirmed by the user")
            self.store.put_profile_version(self.creds[Role.PROFILE_UPDATER], prof)
            out.append(u["profile_id"])
        return out

    def seed_fixtures(self) -> None:
        self.seed_registry()
        for f in sorted(self.fixtures_dir.glob("*/profiles.json")):
            self.seed_profiles(f)

    # ------------------------------------------------------------------
    # decisions + models
    # ------------------------------------------------------------------
    def create_decision(self, spec: DecisionSpec, owner_user: str) -> DecisionSpec:
        cred = self.store.authority.issue(Role.DECISION_OWNER, f"user:{owner_user}")
        if spec.owner_id != owner_user:
            raise PermissionError("only the authenticated owner can create this decision")
        existing = self.store.get_decision_spec(spec.decision_id)
        if existing is not None:
            spec = spec.model_copy(update={"revision": existing.revision + 1})
        self.store.put_decision_spec(cred, spec)
        self.store.grant(cred, spec.decision_id, owner_user, "owner")
        for p in spec.participants:
            if p.user_id and p.user_id != owner_user:
                self.store.grant(cred, spec.decision_id, p.user_id, "participant")
        return spec

    def build_model(self, decision_id: str, as_of: datetime | None = None, *, extra_needs: list[dict[str, Any]] | None = None,
                    revision_reason: str | None = None) -> BuildResult:
        spec = self.store.get_decision_spec(decision_id)
        if spec is None:
            raise KeyError(decision_id)
        latest = self.store.get_model(f"wm_{decision_id}")
        parent = latest["model"] if (latest and revision_reason) else None
        prev_frontier = None
        fr = self.store.get_frontier(decision_id)
        if fr is not None:
            from .model_ir.schemas import CoverageFrontier

            prev_frontier = CoverageFrontier.model_validate(fr)
        as_of = as_of or _now()
        res = self.factory.build(spec, as_of, extra_needs=extra_needs, parent=parent, previous_frontier=prev_frontier,
                                 revision_reason=revision_reason)
        self.store.put_frontier(self.creds[Role.ORCHESTRATOR], res.frontier.model_dump(mode="json"))
        return res

    def revise_model(self, decision_id: str, reason: str, research_questions: list[dict[str, Any]], as_of: datetime | None = None) -> BuildResult:
        """B@v → B@v+1 from new external evidence (§3.4). Pinned runs on B@v are never patched."""
        return self.build_model(decision_id, as_of, extra_needs=research_questions, revision_reason=reason)

    def latest_model(self, decision_id: str):
        row = self.store.get_model(f"wm_{decision_id}")
        return row["model"] if row else None

    def profiles_for(self, model) -> dict[str, list[ProfileFeature]]:
        out = {}
        for a in model.agents:
            if a.profile_ref:
                p = self.store.get_profile(a.profile_ref)
                if p:
                    out[a.id] = p
        return out

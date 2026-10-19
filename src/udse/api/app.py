"""HTTP API (§14.7) — FastAPI over the Engine.

Mutation endpoints are authorised against decision and user scopes; build/run endpoints are
asynchronous jobs with idempotency keys. The simulation runs server-side; clients never run models.
"""

from __future__ import annotations

import json
import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..common import PermissionDenied, ProvenanceRejected, new_id, parse_dt
from ..decision_scope.planner import plan_scope
from ..engine import Engine
from ..model_ir.enums import ClaimStatus, Role, SignalType
from ..model_ir.schemas import (
    ConsentFlags,
    CoverageFrontier,
    DecisionSpec,
    EvidenceClaim,
    ExposureContext,
    FeatureOverlay,
    HumanEvidenceEvent,
    Intervention,
)
from ..trajectory_analytics.analytics import Analytics

WEB_DIR = Path(__file__).resolve().parents[1] / "web" / "static"


class CompileRequest(BaseModel):
    as_of: str | None = None
    idempotency_key: str | None = None


class ReviseRequest(BaseModel):
    reason: str
    research_questions: list[dict[str, Any]] = Field(default_factory=list)
    as_of: str | None = None
    idempotency_key: str | None = None


class ExperimentRequest(BaseModel):
    budget_profile: str | None = None
    seed: int = 1093


class RunRequest(BaseModel):
    idempotency_key: str | None = None
    depth: bool = True
    sensitivity: bool = True


class ForkRequest(BaseModel):
    checkpoint_seq: int
    policy_id: str | None = None
    scenario_ids: list[str] | None = None
    agent_overlays: dict[str, list[FeatureOverlay]] = Field(default_factory=dict)
    description: str = ""


class HumanEvidenceRequest(BaseModel):
    decision_id: str
    decision_revision: int | None = None
    signal_type: SignalType
    payload: dict[str, Any] = Field(default_factory=dict)
    consent: ConsentFlags = Field(default_factory=ConsentFlags)
    exposure: ExposureContext = Field(default_factory=ExposureContext)
    referenced_run_ids: list[str] = Field(default_factory=list)
    profile_id: str | None = None
    idempotency_key: str | None = None


class ApprovalRequest(BaseModel):
    proposal_id: str
    approve: bool


class RollbackRequest(BaseModel):
    to_version: int


class ProposalRequest(BaseModel):
    evidence_event_id: str


def create_app(engine: Engine | None = None, *, dev_mode: bool = True, seed_demo: bool = False) -> FastAPI:
    eng = engine or Engine(os.environ.get("UDSE_DB", ":memory:"))
    eng.seed_fixtures()
    if seed_demo:
        seed_demo_decisions(eng)
    analytics = Analytics(eng.store)
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="udse-job")
    api_cred = eng.creds[Role.API]
    app = FastAPI(title="Universal Decision Simulation Engine", version="0.5.0",
                  description="Evidence-grounded, decision-scoped world models with an event-sourced temporal kernel.")
    app.state.engine = eng

    # ---------------- auth ----------------
    def current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "missing bearer token")
        user = eng.store.user_for_token(authorization.split(" ", 1)[1].strip())
        if user is None:
            raise HTTPException(401, "invalid token")
        return user

    def require_access(decision_id: str, user: dict[str, Any], owner: bool = False) -> DecisionSpec:
        spec = eng.store.get_decision_spec(decision_id)
        if spec is None:
            raise HTTPException(404, "decision not found")
        role = eng.store.decision_role(decision_id, user["user_id"])
        if role is None or (owner and role != "owner"):
            raise HTTPException(403, "not authorised for this decision")
        return spec

    def run_access(run_id: str, user: dict[str, Any]) -> dict[str, Any]:
        run = eng.store.get_run(run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        require_access(run["manifest"].decision_id, user)
        return run

    @app.exception_handler(PermissionDenied)
    async def _pd(_: Request, exc: PermissionDenied):
        return JSONResponse(status_code=403, content={"error": "PERMISSION_DENIED", "detail": str(exc)})

    @app.exception_handler(ProvenanceRejected)
    async def _pr(_: Request, exc: ProvenanceRejected):
        return JSONResponse(status_code=422, content={"error": "PROVENANCE_REJECTED", "detail": str(exc)})

    # ---------------- jobs ----------------
    def submit(kind: str, request: dict[str, Any], idem: str | None, fn) -> dict[str, Any]:
        job_id, created = eng.store.create_job(api_cred, new_id("job"), kind, idem, request)
        if created:
            def work():
                eng.store.update_job(api_cred, job_id, "RUNNING")
                try:
                    eng.store.update_job(api_cred, job_id, "SUCCEEDED", fn())
                except Exception as e:  # noqa: BLE001 - surface the failure on the job
                    eng.store.update_job(api_cred, job_id, "FAILED", error=f"{e!r}\n{traceback.format_exc()[-2000:]}")
            pool.submit(work)
        return {"job_id": job_id, "created": created, "status_url": f"/v1/jobs/{job_id}"}

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str, user=Depends(current_user)):
        job = eng.store.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return job

    # ---------------- misc ----------------
    @app.get("/health")
    def health():
        return {"ok": True, "version": "0.5.0", "llm": eng.llm.model if eng.llm else None}

    @app.get("/v1/me")
    def me(user=Depends(current_user)):
        return {**user, "profile_ids": _profile_ids_for(eng, user["user_id"])}

    if dev_mode:
        @app.get("/v1/dev/users")
        def dev_users():
            out = []
            for f in sorted(eng.fixtures_dir.glob("*/profiles.json")):
                for u in json.loads(f.read_text())["users"]:
                    out.append({"user_id": u["user_id"], "display_name": u["display_name"], "token": u["token"],
                                "fixture": f.parent.name})
            return {"dev_mode": True, "warning": "Development tokens for synthetic fixture users only.", "users": out}

    @app.get("/v1/registry")
    def registry(user=Depends(current_user)):
        return [{"entry": e.ref, "kind": e.kind, "status": str(e.status), "title": e.title,
                 "applicability": e.applicability.model_dump(), "tests": e.tests} for e in eng.registry.entries()]

    @app.get("/v1/audit")
    def audit(denied_only: bool = True, user=Depends(current_user)):
        return eng.store.audit_entries(allowed=False if denied_only else None, limit=200)

    # ---------------- decisions ----------------
    @app.get("/v1/decisions")
    def list_decisions(user=Depends(current_user)):
        out = []
        for spec in eng.store.list_decisions(user["user_id"]):
            m = eng.store.get_model(f"wm_{spec.decision_id}")
            runs = eng.store.list_runs(decision_id=spec.decision_id)
            out.append({"decision_id": spec.decision_id, "revision": spec.revision, "question": spec.question,
                        "participants": [p.display_name or p.agent_id for p in spec.participants],
                        "role": eng.store.decision_role(spec.decision_id, user["user_id"]),
                        "model_version": m["model"].model_version if m else None,
                        "promotion_state": str(m["model"].promotion_state) if m else None,
                        "runs": len(runs), "horizon": spec.horizon.model_dump()})
        return out

    @app.post("/v1/decisions")
    def create_decision(spec: DecisionSpec, user=Depends(current_user)):
        if spec.owner_id != user["user_id"]:
            raise HTTPException(403, "owner_id must be the authenticated user")
        return eng.create_decision(spec, user["user_id"])

    @app.patch("/v1/decisions/{decision_id}")
    def patch_decision(decision_id: str, changes: dict[str, Any], user=Depends(current_user)):
        spec = require_access(decision_id, user, owner=True)
        data = spec.model_dump(mode="json")
        data.update({k: v for k, v in changes.items() if k not in ("decision_id", "owner_id", "revision")})
        return eng.create_decision(DecisionSpec.model_validate(data), user["user_id"])

    @app.get("/v1/decisions/{decision_id}")
    def get_decision(decision_id: str, user=Depends(current_user)):
        spec = require_access(decision_id, user)
        return {"spec": spec, "scope": plan_scope(spec).model_dump(), "role": eng.store.decision_role(decision_id, user["user_id"])}

    @app.post("/v1/decisions/{decision_id}/research-plan")
    def research_plan(decision_id: str, user=Depends(current_user)):
        require_access(decision_id, user)
        return [n.model_dump(mode="json") for n in eng.store.list_research_needs(decision_id)]

    @app.post("/v1/decisions/{decision_id}/models/compile")
    def compile_model(decision_id: str, req: CompileRequest, user=Depends(current_user)):
        require_access(decision_id, user, owner=True)
        as_of = parse_dt(req.as_of) if req.as_of else None
        return submit("model_build", {"decision_id": decision_id, **req.model_dump()}, req.idempotency_key,
                      lambda: eng.build_model(decision_id, as_of).summary())

    @app.post("/v1/decisions/{decision_id}/models/revise")
    def revise_model(decision_id: str, req: ReviseRequest, user=Depends(current_user)):
        require_access(decision_id, user, owner=True)
        as_of = parse_dt(req.as_of) if req.as_of else None
        return submit("model_revision", {"decision_id": decision_id, **req.model_dump()}, req.idempotency_key,
                      lambda: eng.revise_model(decision_id, req.reason, req.research_questions, as_of).summary())

    @app.get("/v1/decisions/{decision_id}/models")
    def model_versions(decision_id: str, user=Depends(current_user)):
        require_access(decision_id, user)
        return [_model_summary(r) for r in eng.store.list_model_versions(decision_id=decision_id)]

    @app.get("/v1/decisions/{decision_id}/models/{version}")
    def get_model(decision_id: str, version: int, user=Depends(current_user)):
        require_access(decision_id, user)
        row = eng.store.get_model(f"wm_{decision_id}", version)
        if row is None:
            raise HTTPException(404, "model version not found")
        return {"model": row["model"].model_dump(mode="json", by_alias=True), "report": row["report"],
                "registry_status": eng.registry.status_map(), **_model_summary(row)}

    @app.get("/v1/models/{model_id}/versions")
    def model_lineage(model_id: str, user=Depends(current_user)):
        rows = eng.store.list_model_versions(model_id=model_id)
        if not rows:
            raise HTTPException(404, "model not found")
        require_access(rows[0]["model"].decision_id, user)
        return [_model_summary(r) for r in rows]

    @app.get("/v1/decisions/{decision_id}/coverage")
    def coverage(decision_id: str, user=Depends(current_user)):
        require_access(decision_id, user)
        fr = eng.store.get_frontier(decision_id)
        if fr is None:
            raise HTTPException(404, "no coverage frontier yet; build a model first")
        from ..model_ir.enums import COVERAGE_UI_LABEL, CoverageStatus

        for it in fr["items"]:
            it["ui_label"] = COVERAGE_UI_LABEL[CoverageStatus(it["status"])]
        return fr

    @app.get("/v1/decisions/{decision_id}/evidence")
    def evidence(decision_id: str, user=Depends(current_user)):
        require_access(decision_id, user)
        b = eng.store.latest_evidence_bundle(decision_id)
        if b is None:
            raise HTTPException(404, "no evidence bundle yet")
        return b.model_dump(mode="json")

    # ---------------- experiments + runs ----------------
    @app.post("/v1/decisions/{decision_id}/experiments")
    def create_experiment(decision_id: str, req: ExperimentRequest, user=Depends(current_user)):
        require_access(decision_id, user, owner=True)
        try:
            return eng.orchestrator.plan(decision_id, budget_profile=req.budget_profile, seed=req.seed).model_dump(mode="json")
        except (KeyError, ValueError) as e:
            raise HTTPException(409, str(e)) from e

    @app.get("/v1/decisions/{decision_id}/experiments")
    def list_experiments(decision_id: str, user=Depends(current_user)):
        require_access(decision_id, user)
        return eng.store.list_experiments(decision_id)

    @app.post("/v1/experiments/{experiment_id}/runs")
    def run_experiment(experiment_id: str, req: RunRequest, user=Depends(current_user)):
        stored = eng.store.get_experiment(experiment_id)
        if stored is None:
            raise HTTPException(404, "experiment not found")
        require_access(stored["decision_id"], user, owner=True)
        from ..experiments.orchestrator import ExperimentPlan

        plan = ExperimentPlan.model_validate({k: v for k, v in stored.items() if k != "stages"})
        return submit("experiment_run", {"experiment_id": experiment_id, **req.model_dump()}, req.idempotency_key,
                      lambda: eng.orchestrator.execute(plan, depth=req.depth, sensitivity=req.sensitivity))

    @app.get("/v1/experiments/{experiment_id}")
    def get_experiment(experiment_id: str, user=Depends(current_user)):
        stored = eng.store.get_experiment(experiment_id)
        if stored is None:
            raise HTTPException(404, "experiment not found")
        require_access(stored["decision_id"], user)
        return stored

    @app.get("/v1/decisions/{decision_id}/runs")
    def list_runs(decision_id: str, user=Depends(current_user)):
        require_access(decision_id, user)
        out = []
        for r in eng.store.list_runs(decision_id=decision_id):
            if r["result"] is None:
                out.append({"run_id": r["manifest"].run_id, "status": r["state"], "name": r["manifest"].label})
                continue
            out.append(analytics.card(r["manifest"].run_id))
        return out

    @app.get("/v1/runs/{run_id}")
    def get_run(run_id: str, user=Depends(current_user)):
        run = run_access(run_id, user)
        res = run["result"]
        return {"manifest": run["manifest"].model_dump(mode="json"), "state": run["state"],
                "result": res.model_dump(mode="json") if res else None,
                "partial_horizon_end": res.last_simulated_at if res and not res.horizon_reached else None,
                "card": analytics.card(run_id) if res else None}

    @app.get("/v1/runs/{run_id}/timeline")
    def timeline(run_id: str, user=Depends(current_user)):
        run_access(run_id, user)
        return analytics.timeline(run_id)

    @app.get("/v1/runs/{run_id}/state")
    def state(run_id: str, seq: int, user=Depends(current_user)):
        run_access(run_id, user)
        return analytics.state_at_seq(run_id, seq)

    @app.get("/v1/runs/{run_id}/why/{seq}")
    def why(run_id: str, seq: int, user=Depends(current_user)):
        run_access(run_id, user)
        return analytics.why(run_id, seq)

    @app.get("/v1/runs/{run_id}/explain")
    def explain(run_id: str, user=Depends(current_user)):
        run_access(run_id, user)
        return analytics.explain(run_id)

    @app.get("/v1/runs/{run_id}/diagnostics")
    def diagnostics(run_id: str, user=Depends(current_user)):
        run = run_access(run_id, user)
        res = run["result"]
        return {"diagnostics": [d.model_dump(mode="json") for d in (res.diagnostics if res else [])],
                "note": "Diagnostics are simulation outputs (is_real_world_evidence=false); they can only queue research."}

    @app.get("/v1/runs/{run_id}/replay")
    def replay(run_id: str, user=Depends(current_user)):
        run_access(run_id, user)
        return analytics.replay(run_id)

    @app.get("/v1/runs/{run_a}/compare/{run_b}")
    def compare(run_a: str, run_b: str, user=Depends(current_user)):
        run_access(run_a, user)
        run_access(run_b, user)
        return analytics.compare(run_a, run_b)

    @app.post("/v1/runs/{run_id}/fork")
    def fork(run_id: str, req: ForkRequest, user=Depends(current_user)):
        run = run_access(run_id, user)
        require_access(run["manifest"].decision_id, user, owner=True)
        iv = Intervention(policy_id=req.policy_id, scenario_ids=req.scenario_ids, agent_overlays=req.agent_overlays,
                          description=req.description or "fork")
        try:
            res = eng.orchestrator.fork(run_id, req.checkpoint_seq, iv)
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        return {"run_id": res.run_id, "card": analytics.card(res.run_id)}

    @app.get("/v1/runs/{run_id}/events/{seq}/promotion-check")
    def promotion_check(run_id: str, seq: int, user=Depends(current_user)):
        """Demonstrates the epistemic boundary: every path that could turn this simulated event into
        real-world evidence or profile evidence is attempted and must be refused (§3.3 poisoning test)."""
        run_access(run_id, user)
        ev = next((e for e in eng.store.get_events(run_id) if e.seq == seq), None)
        if ev is None:
            raise HTTPException(404, "event not found")
        return attempt_all_promotions(eng, ev, user["user_id"])

    # ---------------- human evidence + profiles ----------------
    @app.post("/v1/human-evidence")
    def human_evidence(req: HumanEvidenceRequest, user=Depends(current_user)):
        spec = require_access(req.decision_id, user)
        ev = HumanEvidenceEvent(
            event_id=new_id("hev"), user_id=user["user_id"], decision_id=req.decision_id,
            decision_revision=req.decision_revision or spec.revision, signal_type=req.signal_type, consent=req.consent,
            exposure=req.exposure, payload=req.payload, referenced_run_ids=req.referenced_run_ids,
            created_at=datetime.now(timezone.utc).isoformat(), idempotency_key=req.idempotency_key,
        )
        stored = eng.human.ingest(ev, authenticated_user=user["user_id"])
        if stored is None:
            return {"stored": False, "reason": "implicit signal not stored: implicit learning is off (opt-in)"}
        interp = eng.human.interpret(stored)
        proposal = None
        pid = req.profile_id or (_profile_ids_for(eng, user["user_id"]) or [None])[0]
        if pid:
            proposal = eng.human.propose(stored, pid)
        return {"stored": True, "event": stored.model_dump(mode="json"), "interpretation": interp,
                "proposal": proposal.model_dump(mode="json") if proposal else None}

    @app.get("/v1/profiles/{profile_id}")
    def get_profile(profile_id: str, user=Depends(current_user)):
        p = eng.store.get_profile(profile_id)
        if p is None:
            raise HTTPException(404, "profile not found")
        if p.user_id != user["user_id"]:
            raise HTTPException(403, "profiles are private to their owner")
        return {"current": p.model_dump(mode="json"),
                "versions": [{"version": v.version, "created_at": v.created_at, "reason": v.reason} for v in eng.store.list_profile_versions(profile_id)],
                "proposals": [x.model_dump(mode="json") for x in eng.store.list_profile_proposals(profile_id)],
                "evidence": [e.model_dump(mode="json") for e in eng.store.list_human_evidence(user["user_id"])]}

    @app.post("/v1/profiles/{profile_id}/update-proposals")
    def make_proposal(profile_id: str, req: ProposalRequest, user=Depends(current_user)):
        ev = eng.store.get_human_evidence(req.evidence_event_id)
        if ev is None or ev.user_id != user["user_id"]:
            raise HTTPException(404, "evidence not found")
        p = eng.human.propose(ev, profile_id)
        return p.model_dump(mode="json") if p else {"proposal": None, "reason": "signal implies no profile change"}

    @app.post("/v1/profiles/{profile_id}/approvals")
    def approve(profile_id: str, req: ApprovalRequest, user=Depends(current_user)):
        try:
            new = eng.human.decide(req.proposal_id, user["user_id"], req.approve)
        except (ValueError, KeyError) as e:
            raise HTTPException(409, str(e)) from e
        return {"applied": new is not None, "profile": new.model_dump(mode="json") if new else None}

    @app.post("/v1/profiles/{profile_id}/rollback")
    def rollback(profile_id: str, req: RollbackRequest, user=Depends(current_user)):
        return eng.human.rollback(profile_id, req.to_version, user["user_id"]).model_dump(mode="json")

    # ---------------- web client ----------------
    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/")
        def index():
            return FileResponse(WEB_DIR / "index.html")

    return app


def _profile_ids_for(eng: Engine, user_id: str) -> list[str]:
    rows = eng.store._all("SELECT DISTINCT profile_id FROM profile_versions WHERE user_id = ?", (user_id,))
    return [r["profile_id"] for r in rows]


def _model_summary(row: dict[str, Any]) -> dict[str, Any]:
    m = row["model"]
    rep = row["report"]
    return {"model_id": m.model_id, "version": m.model_version, "hash": m.hash, "parent_version": m.parent_version,
            "revision_reason": m.revision_reason, "promotion_state": str(m.promotion_state), "created_at": row["created_at"],
            "passed": rep["passed"], "metric_fidelity": rep["metric_fidelity"], "non_computable_metrics": rep["non_computable_metrics"],
            "scorecard": rep["scorecard"], "downgrades": rep["downgrades"], "allowed_run_modes": rep["allowed_run_modes"],
            "evidence_bundle_hash": row["evidence_bundle_hash"]}


def attempt_all_promotions(eng: Engine, ev, user_id: str) -> dict[str, Any]:
    """Try every write path from Layer C into Layer A / the profile store. All must fail."""
    results = []
    before = eng.store.knowledge_hash()
    text = ev.event_type.replace("_", " ")

    def attempt(name, fn):
        try:
            fn()
            results.append({"path": name, "refused": False, "detail": "ACCEPTED — boundary breached"})
        except (ProvenanceRejected, PermissionDenied) as e:
            results.append({"path": name, "refused": True, "detail": str(e)})

    attempt("ingest event as SIMULATION_OUTPUT claim", lambda: eng.ingestion.ingest(EvidenceClaim(
        claim_id=new_id("probe"), origin_kind="SIMULATION_OUTPUT", subject=ev.run_id, predicate=ev.event_type, object=True)))
    attempt("relabel as REAL_EXTERNAL_OBSERVATION without provenance", lambda: eng.ingestion.ingest(EvidenceClaim(
        claim_id=new_id("probe"), origin_kind="REAL_EXTERNAL_OBSERVATION", subject=ev.event_type, predicate="occurred", object=True,
        derived_from=[ev.event_id])))
    attempt("LLM-summarised rewrite (tainted lineage)", lambda: _tainted_rewrite(eng, ev))
    attempt("simulation-worker credential writes external_claims", lambda: eng.store.put_claim(
        eng.creds[Role.SIMULATION_WORKER], EvidenceClaim(claim_id=new_id("probe"), origin_kind="REAL_EXTERNAL_OBSERVATION",
                                                         subject=text, predicate="occurred", object=True)))
    attempt("simulation-worker credential promotes to Layer A", lambda: eng.store.put_curated(
        eng.creds[Role.SIMULATION_WORKER], new_id("probe"), "1.0.0", "fact", "A_VALIDATED", {"event": ev.event_id}, [], []))
    attempt("Agent action ingested as human evidence", lambda: eng.human.ingest(HumanEvidenceEvent(
        event_id=new_id("probe"), user_id=user_id, decision_id=eng.store.get_run(ev.run_id)["manifest"].decision_id, decision_revision=1,
        signal_type=SignalType.EXPLICIT_SELECTION, payload={"source": "simulated_agent", "actor_id": ev.actor_id or "agent", "event": ev.event_id},
        created_at=datetime.now(timezone.utc).isoformat()), authenticated_user=user_id))
    from ..model_ir.schemas import RealProfileVersion

    pids = _profile_ids_for(eng, user_id)
    current = eng.store.get_profile(pids[0]) if pids else None
    forged = RealProfileVersion(profile_id=current.profile_id if current else f"profile_{user_id}", user_id=user_id,
                                version=(current.version + 1) if current else 1, features=current.features if current else [],
                                evidence_ids=[ev.event_id], created_at=datetime.now(timezone.utc).isoformat(),
                                reason="forged update from simulated behaviour")
    attempt("simulation-worker credential writes a profile version", lambda: eng.store.put_profile_version(
        eng.creds[Role.SIMULATION_WORKER], forged))
    attempt("profile updater cites a simulated event as evidence", lambda: eng.store.put_profile_version(
        eng.creds[Role.PROFILE_UPDATER], forged))
    after = eng.store.knowledge_hash()
    return {"event_id": ev.event_id, "origin_kind": str(ev.origin_kind), "attempts": results,
            "all_refused": all(r["refused"] for r in results), "layer_a_hash_unchanged": before == after}


def _tainted_rewrite(eng: Engine, ev) -> None:
    from ..llm.provider import LLMCall, ScriptedProvider

    llm = ScriptedProvider({"summarize": [{"claim": f"{ev.event_type} happened in the real world"}]})
    res = llm.complete_json(LLMCall(task="summarize", system="", user=json.dumps(ev.payload, default=str), tainted_by_simulation=True))
    eng.store.put_llm_record(eng.creds[Role.SIMULATION_WORKER], res.record)
    eng.ingestion.ingest(EvidenceClaim(claim_id=new_id("probe"), origin_kind="REAL_EXTERNAL_OBSERVATION", subject="summary",
                                       predicate="states", object=res.data["claim"], derived_from=[f"llm:{res.record['call_id']}"]))


def seed_demo_decisions(eng: Engine) -> None:
    """Create, compile and run the fixture decisions so the web client has data to explore."""
    demos = [("restaurant/decision.json", "user_a", "2026-10-24T12:30:00-07:00"),
             ("fundraising/decision.json", "user_fa", "2026-10-18T09:00:00-07:00"),
             ("new_domain/decision.json", "user_g1", "2026-11-01T09:00:00-07:00")]
    for rel, owner, as_of in demos:
        spec = DecisionSpec.model_validate_json((eng.fixtures_dir / rel).read_text())
        if eng.store.get_decision_spec(spec.decision_id) is not None:
            continue
        eng.create_decision(spec, owner)
        res = eng.build_model(spec.decision_id, parse_dt(as_of))
        if res.model is not None:
            plan = eng.orchestrator.plan(spec.decision_id)
            eng.orchestrator.execute(plan)

"""HTTP API contracts (§14.7) and orchestrator budget semantics (§10)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FIX, build, load_spec
from udse.api.app import create_app
from udse.engine import Engine
from udse.experiments.orchestrator import BUDGET_PROFILES
from udse.model_ir.enums import CoverageStatus
from udse.model_ir.schemas import CoverageFrontier

A = {"Authorization": "Bearer dev-token-founder-a"}
B = {"Authorization": "Bearer dev-token-founder-b"}
OUTSIDER = {"Authorization": "Bearer dev-token-user-a"}


def _wait(client, job, headers):
    for _ in range(300):
        j = client.get(f"/v1/jobs/{job['job_id']}", headers=headers).json()
        if j["status"] in ("SUCCEEDED", "FAILED"):
            assert j["status"] == "SUCCEEDED", j.get("error")
            return j["result"]
        time.sleep(0.05)
    raise AssertionError("job timed out")


@pytest.fixture(scope="module")
def client():
    eng = Engine(":memory:", FIX)
    with TestClient(create_app(eng)) as c:
        yield c


def test_full_api_flow_with_authorisation(client):
    spec = load_spec("fundraising").model_dump(mode="json")
    assert client.get("/v1/decisions").status_code == 401
    assert client.post("/v1/decisions", json=spec, headers=B).status_code == 403  # B is not the owner
    assert client.post("/v1/decisions", json=spec, headers=A).status_code == 200
    job = client.post(f"/v1/decisions/{spec['decision_id']}/models/compile",
                      json={"as_of": "2026-10-18T09:00:00-07:00", "idempotency_key": "k1"}, headers=A).json()
    again = client.post(f"/v1/decisions/{spec['decision_id']}/models/compile", json={"idempotency_key": "k1"}, headers=A).json()
    assert again["job_id"] == job["job_id"] and again["created"] is False  # idempotent
    built = _wait(client, job, A)
    assert built["status"] == "MODEL_READY"
    assert client.post(f"/v1/decisions/{spec['decision_id']}/models/compile", json={}, headers=B).status_code == 403  # participant, not owner
    assert client.get(f"/v1/decisions/{spec['decision_id']}", headers=B).status_code == 200                       # participant can read
    assert client.get(f"/v1/decisions/{spec['decision_id']}", headers=OUTSIDER).status_code == 403
    cov = client.get(f"/v1/decisions/{spec['decision_id']}/coverage", headers=A).json()
    assert cov["items"] and all("ui_label" in i for i in cov["items"])
    plan = client.post(f"/v1/decisions/{spec['decision_id']}/experiments", json={}, headers=A).json()
    summary = _wait(client, client.post(f"/v1/experiments/{plan['experiment_id']}/runs", json={}, headers=A).json(), A)
    rid = summary["depth_runs"][0]["run_id"]
    run = client.get(f"/v1/runs/{rid}", headers=B).json()
    assert run["manifest"]["model_ref"]["hash"] == built["model_ref"]["hash"]
    tl = client.get(f"/v1/runs/{rid}/timeline", headers=A).json()
    assert tl["events"] and all(e["origin_kind"] == "SIMULATION_OUTPUT" for e in tl["events"])
    seq = tl["events"][-1]["seq"]
    assert client.get(f"/v1/runs/{rid}/why/{seq}", headers=A).json()["chain"]
    pc = client.get(f"/v1/runs/{rid}/events/{seq}/promotion-check", headers=A).json()
    assert pc["all_refused"] and pc["layer_a_hash_unchanged"]
    rep = client.get(f"/v1/runs/{rid}/replay", headers=A).json()
    assert rep["event_log"]["ok"] and rep["transition"]["ok"]
    assert client.get("/v1/audit", headers=A).json()


def test_profile_privacy_and_feedback_flow(client):
    assert client.get("/v1/profiles/profile_founder_b", headers=A).status_code == 403
    r = client.post("/v1/human-evidence", headers=B, json={
        "decision_id": "dec_funding_001", "signal_type": "EXPLICIT_CORRECTION",
        "payload": {"statement": "I will not accept terms that remove founder control", "feature_key": "constraint.founder_control_retained",
                    "value": {"op": "==", "value": True}, "hard": True}}).json()
    assert r["stored"] and r["proposal"]["status"] == "PROPOSED"
    ok = client.post("/v1/profiles/profile_founder_b/approvals", headers=B, json={"proposal_id": r["proposal"]["proposal_id"], "approve": True}).json()
    assert ok["applied"]
    implicit = client.post("/v1/human-evidence", headers=A, json={"decision_id": "dec_funding_001", "signal_type": "DETAILED_INSPECTION"}).json()
    assert implicit["stored"] is False


def test_budget_set_aside_is_not_infeasible():
    eng = Engine(":memory:", FIX)
    eng.seed_fixtures()
    spec, res = build(eng, "fundraising")
    plan = eng.orchestrator.plan(spec.decision_id)
    plan.budget = BUDGET_PROFILES["quick"].model_copy(update={"max_screening_runs": 1})
    out = eng.orchestrator.execute(plan, depth=False, sensitivity=False)
    assert len(out["screening_runs"]) == 1 and out["set_aside_arms"]
    fr = CoverageFrontier.model_validate(eng.store.get_frontier(spec.decision_id))
    aside = [i for i in fr.items if i.status == CoverageStatus.SET_ASIDE_BUDGET]
    assert aside and all("not infeasible" in i.status_reason for i in aside)


def test_cors_is_off_unless_origins_are_configured(client, monkeypatch):
    origin = {"Origin": "http://localhost:8081"}
    assert "access-control-allow-origin" not in client.get("/health", headers=origin).headers
    monkeypatch.delenv("UDSE_CORS_ORIGINS", raising=False)
    with TestClient(create_app(Engine(":memory:", FIX), cors_origins=["http://localhost:8081"])) as c:
        assert c.get("/health", headers=origin).headers["access-control-allow-origin"] == "http://localhost:8081"
        pre = c.options("/v1/decisions", headers={**origin, "Access-Control-Request-Method": "GET",
                                                   "Access-Control-Request-Headers": "authorization"})
        assert pre.status_code == 200
        assert "access-control-allow-origin" not in c.get("/health", headers={"Origin": "http://evil.example"}).headers

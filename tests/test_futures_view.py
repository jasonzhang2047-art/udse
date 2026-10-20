"""Futures map and world-model read models behind the UI (docs/design/ui-redesign.md §8–§9)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FIX, build
from udse.api.app import create_app
from udse.common import parse_dt
from udse.engine import Engine
from udse.trajectory_analytics.futures import FuturesView, Labeler, _metric_label

USER_A = {"Authorization": "Bearer dev-token-user-a"}
OUTSIDER = {"Authorization": "Bearer dev-token-founder-a"}
ENGINE_WORDS = ("×", "SCENARIO:", "(sensitivity probe)", "utility", "['constraint")


@pytest.fixture(scope="module")
def futures(restaurant):
    eng, spec, *_ = restaurant
    return eng, spec, FuturesView(eng.store).futures(spec.decision_id)


def test_threads_partition_the_runs_of_one_model_version(futures):
    eng, spec, f = futures
    model_hash = eng.store.get_model(f"wm_{spec.decision_id}")["model"].hash
    runs = {r["manifest"].run_id for r in eng.store.list_runs(decision_id=spec.decision_id)
            if r["result"] is not None and r["manifest"].model_ref.hash == model_hash}
    seen = [rid for t in f["threads"] for rid in t["run_ids"]]
    assert sorted(seen) == sorted(runs)  # every run in exactly one thread, no other model's runs mixed in
    assert len({t["thread_id"] for t in f["threads"]}) == len(f["threads"])


def test_branch_structure_and_split_times(futures):
    _, _, f = futures
    start, end = parse_dt(f["horizon"]["start"]), parse_dt(f["horizon"]["end"])
    ids = [t["thread_id"] for t in f["threads"]]
    trunk, rest = f["threads"][0], f["threads"][1:]
    assert trunk["parent"] is None
    for t in rest:
        assert t["parent"] in ids[: ids.index(t["thread_id"])]  # parents come earlier, so the map draws top-down
        assert start <= parse_dt(t["split_at"]) <= max(parse_dt(t["ended_at"]), start) <= end
    for t in f["threads"]:
        times = [parse_dt(m["t"]) for m in t["moments"]]
        assert times == sorted(times)


def test_labels_and_reasoning_are_plain_language(futures):
    _, _, f = futures
    for t in f["threads"]:
        assert not any(w in t["label"] for w in ENGINE_WORDS), t["label"]
        for m in t["moments"]:
            if m.get("rationale") and m["kind"] != "end":
                assert m["reasoning"] and not any(w in m["reasoning"] for w in ENGINE_WORDS), m["reasoning"]
    assert any(" — if " in t["label"] for t in f["threads"])  # variations name their condition


def test_ghosts_evidence_and_light_payload(futures, restaurant):
    eng, spec, *_ = restaurant
    _, _, f = futures
    assert len(f["ghosts"]) <= 6 and f["ghosts_total"] >= len(f["ghosts"])
    assert any(g["kind"] == "ruled_out" for g in f["ghosts"])
    ev = f["evidence"]
    assert all(isinstance(ev[k], int) for k in ("verified", "stale", "unknown", "contested", "user"))
    light = FuturesView(eng.store).futures(spec.decision_id, light=True)
    assert [t["thread_id"] for t in light["threads"]] == [t["thread_id"] for t in f["threads"]]


def test_world_model_edges_connect_known_nodes(restaurant):
    eng, spec, *_ = restaurant
    w = FuturesView(eng.store).world_model(spec.decision_id)
    nodes = {n["id"] for k in ("inputs", "processes", "outcomes", "missing") for n in w[k]}
    assert w["inputs"] and w["processes"] and w["outcomes"]
    assert w["edges"] and all(e["from"] in nodes and e["to"] in nodes for e in w["edges"])
    assert all(o["tier"] for o in w["outcomes"])


def test_reasoning_translation():
    lab = Labeler.__new__(Labeler)
    lab.entity, lab.policy = {"r_a": "R-A", "r_b": "R-B"}, {}
    raw = ("Object proposal p2 (r_b): cannot verify hard constraint(s) ['constraint.gluten_safe'] "
           "Factors: cost pp 30 USD (weight -0.02); table confirmed UNKNOWN (weight 1)")
    assert lab.reasoning(raw) == "Objects to R-B — can't confirm gluten safe. Weighed: cost per person $30 · table confirmed: unknown"
    assert lab.reasoning("Propose r_a: best option satisfying my constraints (utility 2.48).").startswith("Proposes R-A")
    assert lab.reasoning("something the translator has never seen") == "something the translator has never seen"
    assert _metric_label("budget_runway_months", "month") == "Budget runway"
    assert _metric_label("budget_usd", "USD") == "Budget"


def test_futures_and_world_model_endpoints():
    eng = Engine(":memory:", FIX)
    eng.seed_fixtures()
    spec, _ = build(eng, "restaurant")
    eng.orchestrator.execute(eng.orchestrator.plan(spec.decision_id))
    with TestClient(create_app(eng)) as c:
        did = spec.decision_id
        assert c.get(f"/v1/decisions/{did}/futures").status_code == 401
        assert c.get(f"/v1/decisions/{did}/futures", headers=OUTSIDER).status_code == 403
        assert c.get(f"/v1/decisions/{did}/world-model", headers=OUTSIDER).status_code == 403
        f = c.get(f"/v1/decisions/{did}/futures", headers=USER_A).json()
        assert f["threads"] and f["model"]["version"] == 1
        assert c.get(f"/v1/decisions/{did}/futures?version=99", headers=USER_A).status_code == 404
        assert c.get(f"/v1/decisions/{did}/world-model", headers=USER_A).json()["outcomes"]
        t = f["threads"][0]
        seq = next(m["seq"] for m in t["moments"] if m["kind"] == "agreement")
        chain = c.get(f"/v1/runs/{t['rep_run_id']}/why/{seq}", headers=USER_A).json()["chain"]
        assert chain[0].get("headline")
        assert all(s.get("reasoning") for s in chain if s.get("rationale"))

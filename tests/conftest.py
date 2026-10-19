from __future__ import annotations

from pathlib import Path

import pytest

from udse.common import parse_dt
from udse.engine import Engine
from udse.model_ir.schemas import DecisionSpec

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures"

FIXTURES = {
    "restaurant": ("restaurant/decision.json", "user_a", "2026-10-24T12:30:00-07:00"),
    "fundraising": ("fundraising/decision.json", "user_fa", "2026-10-18T09:00:00-07:00"),
    "garden": ("new_domain/decision.json", "user_g1", "2026-11-01T09:00:00-07:00"),
    "medical": ("new_domain/high_stakes_decision.json", "user_m1", "2026-11-01T09:00:00-07:00"),
}


def load_spec(name: str) -> DecisionSpec:
    return DecisionSpec.model_validate_json((FIX / FIXTURES[name][0]).read_text())


def build(engine: Engine, name: str):
    rel, owner, as_of = FIXTURES[name]
    spec = load_spec(name)
    engine.create_decision(spec, owner)
    return spec, engine.build_model(spec.decision_id, parse_dt(as_of))


@pytest.fixture
def engine() -> Engine:
    eng = Engine(":memory:", FIX)
    eng.seed_fixtures()
    return eng


def _executed(name: str):
    eng = Engine(":memory:", FIX)
    eng.seed_fixtures()
    spec, res = build(eng, name)
    plan = eng.orchestrator.plan(spec.decision_id)
    out = eng.orchestrator.execute(plan)
    return eng, spec, res, plan, out


@pytest.fixture(scope="module")
def restaurant():
    return _executed("restaurant")


@pytest.fixture(scope="module")
def fundraising():
    return _executed("fundraising")

"""Model IR safety, units, three-valued logic, and the temporal kernel's core guarantees."""

from __future__ import annotations

import pytest

from udse.agent_runtime.policies import ProfileUtilityPolicy
from udse.model_ir.enums import OutcomeFlag, RunStatus
from udse.model_ir.expr import UNKNOWN, DictEnv, DictTypeEnv, ExprError, Q, T_Q, T_STR, evaluate, infer
from udse.model_ir.index import compute_model_hash
from udse.model_ir.schemas import DecisionWorldModel, ExecutionBudget, ModelRef, RunManifest
from udse.model_ir.units import parse_unit
from udse.model_validator.compiler import compile_model
from udse.temporal_kernel import InMemoryRunStore, Simulator
from udse.temporal_kernel.replay import replay_event_log, replay_transitions


def burn_model(**over) -> dict:
    m = {
        "model_id": "wm_t", "decision_id": "dec_t",
        "scope": {"decision_rev": 1, "horizon_start": "2026-10-18T00:00:00-07:00", "horizon_end": "2027-10-18T00:00:00-07:00", "time_resolution": "month"},
        "entities": [{"id": "company", "type": "organization"}],
        "state": [
            {"ref": "company.cash_usd", "type": "quantity", "unit": "USD", "initial_value": 100000, "origin": "USER_ASSERTED"},
            {"ref": "company.monthly_outflow_usd", "type": "quantity", "unit": "USD/month", "initial_value": 15000, "origin": "USER_ASSERTED"},
        ],
        "policies": [{"id": "p", "label": "p"}],
        "mechanisms": [{"id": "cash_burn", "operator": "resource_flow",
                        "rule": {"kind": "subtract_flow", "target": "company.cash_usd", "rate_ref": "company.monthly_outflow_usd", "interval_unit": "month"},
                        "schedule": {"kind": "recurring", "interval": "P1M"}}],
        "constraints": [{"id": "nonneg", "type": "guarded_floor", "path": "company.cash_usd", "min": 0,
                         "on_violation": {"action": "terminate", "outcome": "CASH_EXHAUSTED"}}],
        "metrics": [{"id": "runway", "expr": "company.cash_usd / company.monthly_outflow_usd", "unit": "month"}],
    }
    m.update(over)
    return m


def run(model: DecisionWorldModel, budget: ExecutionBudget | None = None, runtime=None):
    store = InMemoryRunStore()
    man = RunManifest(run_id="run_t", decision_id=model.decision_id,
                      model_ref=ModelRef(model_id=model.model_id, version=model.model_version, hash=model.hash),
                      policy_id=model.policies[0].id, execution_budget=budget or ExecutionBudget())
    res = Simulator(model, man, runtime or ProfileUtilityPolicy(), store, {}).run()
    return res, store, man


# ---------------- expressions ----------------

def test_expression_language_rejects_code_and_unit_errors():
    tenv = DictTypeEnv({"a.cash": T_Q("USD"), "a.rate": T_Q("USD/month"), "v": T_STR(["x", "y"])})
    for bad in ["__import__('os').system('ls')", "a.cash + a.rate", "a.cash > 5", "v == 'z'", "open('f')", "a.cash; 1"]:
        with pytest.raises(ExprError):
            infer(bad, tenv)
    assert infer("to(a.cash / a.rate, 'month')", tenv).unit.label == "month"


def test_unknown_is_three_valued_never_guessed():
    env = DictEnv({"x": UNKNOWN, "q": Q(3, parse_unit("USD"))})
    assert evaluate("x > 1", env) is UNKNOWN
    assert evaluate("x > 1 and false", env) is False
    assert evaluate("x > 1 or true", env) is True
    assert evaluate("coalesce(x, 2[USD]) + q", env) == Q(5, parse_unit("USD"))
    assert evaluate("is_known(x)", env) is False


# ---------------- kernel ----------------

def test_cash_floor_terminates_instead_of_clipping_and_replays():
    model, report = compile_model(burn_model(), None)
    assert report.passed, report.errors
    res, store, man = run(model)
    assert res.status == RunStatus.TERMINAL_STATE and res.terminal_outcome == "CASH_EXHAUSTED"
    events = store.get_events("run_t")
    cash = [op.after for e in events for op in e.patch if op.key == "company.cash_usd"]
    assert min(cash) >= 0, "cash must never be clipped or go negative"
    assert events[-1].kind == "CONSTRAINT_BLOCKED" and not events[-1].patch
    g = store.get_genesis("run_t")
    assert replay_event_log(g["snapshot"], g["hash"], events).ok
    tr = replay_transitions(model, man, events)
    assert tr.ok and tr.llm_calls == 0


def test_budget_exhaustion_returns_partial_truthful_state():
    model, _ = compile_model(burn_model(constraints=[]), None)
    res, store, _ = run(model, ExecutionBudget(max_events=5, reserve_events=1))
    assert res.status == RunStatus.BUDGET_EXHAUSTED
    assert not res.horizon_reached and OutcomeFlag.PARTIAL_HORIZON in res.outcome_flags
    events = store.get_events("run_t")
    assert res.last_simulated_at == events[-1].sim_at < res.horizon_end
    assert res.final_state_hash == events[-1].post_state_hash  # exact last committed state, nothing fabricated


def test_quiescent_world_collapses_to_horizon_with_documented_event():
    m = burn_model(mechanisms=[], constraints=[])
    model, _ = compile_model(m, None)
    res, store, _ = run(model)
    assert res.status == RunStatus.COMPLETED_HORIZON and res.horizon_reached
    assert store.get_events("run_t")[-1].kind == "QUIESCENT_COLLAPSE"


def test_run_must_pin_exact_model_hash():
    model, _ = compile_model(burn_model(), None)
    man = RunManifest(run_id="r", decision_id="dec_t", model_ref=ModelRef(model_id="wm_t", version=1, hash="sha256:other"), policy_id="p")
    with pytest.raises(ValueError):
        Simulator(model, man, ProfileUtilityPolicy(), InMemoryRunStore())


def test_model_hash_covers_content():
    m1, _ = compile_model(burn_model(), None)
    m2, _ = compile_model(burn_model(state=[
        {"ref": "company.cash_usd", "type": "quantity", "unit": "USD", "initial_value": 100001, "origin": "USER_ASSERTED"},
        {"ref": "company.monthly_outflow_usd", "type": "quantity", "unit": "USD/month", "initial_value": 15000, "origin": "USER_ASSERTED"}]), None)
    assert m1.hash != m2.hash and m1.hash == compute_model_hash(m1)


# ---------------- compiler contracts ----------------

@pytest.mark.parametrize("mutation,needle", [
    (lambda m: m["mechanisms"][0].update(operator="arbitrary_python"), "allowlist"),
    (lambda m: m["mechanisms"][0]["rule"].update(rate_ref="company.cash_usd"), "rate unit"),
    (lambda m: m["mechanisms"].append({"id": "w", "operator": "information_process", "rule": {"kind": "on_event", "effects": [
        {"op": "set", "ref": "company.ghost", "value": "1"}]}, "schedule": {"kind": "on_event", "event_types": ["x"]}}), "undeclared"),
    (lambda m: m.update(scenarios=[{"id": "s", "label": "s", "probability": 0.7}]), "probability"),
    (lambda m: m["mechanisms"].append({"id": "loop", "operator": "information_process", "rule": {"kind": "on_event", "effects": [
        {"op": "emit", "event_type": "ping"}]}, "schedule": {"kind": "on_event", "event_types": ["ping"]}}), "zero-delay"),
    (lambda m: m["state"][0].update(origin="SIMULATION_OUTPUT"), "SIMULATION_OUTPUT"),
])
def test_compiler_rejects_invalid_contracts(mutation, needle):
    m = burn_model()
    mutation(m)
    model, report = compile_model(m, None)
    assert not report.passed
    assert any(needle.lower() in e.lower() for e in report.errors), report.errors


def test_compiler_rejects_unknown_fields_strictly():
    m = burn_model()
    m["mechanisms"][0]["python_code"] = "import os"
    model, report = compile_model(m, None)
    assert model is None and not report.passed and report.checks[0].id == "C01_schema"


def test_unsupported_tier_claims_are_downgraded_visibly():
    m = burn_model()
    m["mechanisms"][0]["epistemic_tier"] = "A_GROUNDED"  # no evidence, no Layer A entry
    model, report = compile_model(m, None)
    assert report.passed
    assert model.mechanisms[0].epistemic_tier != "A_GROUNDED"
    assert any(d["id"] == "cash_burn" for d in report.downgrades)

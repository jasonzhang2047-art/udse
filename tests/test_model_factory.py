"""Dynamic Model Factory (§6): clean-room synthesis, abstention, high-stakes limits, LLM synthesis
with bounded repair, and the reuse transfer gate."""

from __future__ import annotations

import copy
import json

from tests.conftest import FIX, build, load_spec
from udse.engine import Engine
from udse.llm.provider import ScriptedProvider
from udse.model_ir.enums import FidelityTier
from udse.model_registry.registry import TargetContext, transfer_gate


def test_unsupported_category_builds_researched_model_without_adapter(engine):
    """§15.3 Dynamic domain without registered full model."""
    spec, res = build(engine, "garden")
    assert res.path == "generic" and res.status in ("MODEL_READY", "LIMITED_MODEL")
    assert res.report.passed
    assert not any(r.get("used") for r in res.model.reuse_manifest if r["entry"].startswith("pkg."))
    # research needs recorded; unsupported outcome exposed instead of fabricated
    assert any("plant yield" in n.question for n in res.needs)
    assert res.report.metric_fidelity["plant_yield"] == FidelityTier.X_LIMITED_OR_ABSTAIN
    assert next(m for m in res.model.metrics if m.id == "plant_yield").computable is False
    # a safe Layer A primitive was reused through the transfer gate
    flows = [m for m in res.model.mechanisms if m.operator == "resource_flow"]
    assert flows and any(m.registry_ref for m in flows)
    # executes several time-evolving branches
    out = engine.orchestrator.execute(engine.orchestrator.plan(spec.decision_id), depth=False, sensitivity=False)
    finals = {r["label"]: r["metrics"]["budget_usd"]["value"]["value"] for r in out["screening_runs"] if r["metrics"]["budget_usd"]["status"] == "OK"}
    assert len(set(finals.values())) >= 3, finals


def test_high_stakes_decisions_withhold_numeric_predictions_from_unverified_structure(engine):
    spec, res = build(engine, "medical")
    assert res.scope.high_stakes
    assert res.report.metric_fidelity["symptom_severity"] == FidelityTier.X_LIMITED_OR_ABSTAIN
    assert "depth" not in res.report.allowed_run_modes
    assert any(o.id == "high_stakes_scope" for o in res.model.omissions)


def test_system_suggested_alternatives_are_labelled(engine):
    spec, res = build(engine, "garden")
    defer = next(a for a in res.scope.alternatives if a.id == "defer")
    assert defer.suggested_by_system


def _llm_candidate(engine) -> dict:
    """Start from the generic model and inject contract errors an LLM might make."""
    spec, res = build(engine, "garden")
    ir = json.loads(res.model.model_dump_json(by_alias=True))
    for k in ("hash", "promotion_state", "validation_report_ref", "construction_tasks", "epistemic_manifest"):
        ir.pop(k, None)
    bad = copy.deepcopy(ir)
    bad["mechanisms"].append({"id": "llm_growth", "operator": "plant_growth_magic", "description": "made up",
                              "rule": {"kind": "on_event", "effects": [{"op": "inc", "ref": "garden.budget_usd", "by": "5[month]"}]},
                              "schedule": {"kind": "on_event", "event_types": ["tick:flow_garden_water_cost_usd_per_month"]},
                              "epistemic_tier": "A_GROUNDED", "origin": "MODEL_HYPOTHESIS"})
    good = copy.deepcopy(ir)
    good["mechanisms"].append({"id": "llm_volunteer_hours", "operator": "information_process", "description": "LLM hypothesis",
                               "rule": {"kind": "on_event", "effects": [{"op": "set", "ref": "decision.policy", "value": "decision.policy"}]},
                               "schedule": {"kind": "on_event", "event_types": ["switched_to_install_now"]},
                               "epistemic_tier": "A_GROUNDED", "origin": "MODEL_HYPOTHESIS"})
    return {"bad": bad, "good": good}


def test_llm_synthesis_is_validated_repaired_and_never_self_certified():
    probe = Engine(":memory:", FIX)
    probe.seed_fixtures()
    cands = _llm_candidate(probe)
    llm = ScriptedProvider({
        "breadth_discovery": [{"families": [{"category": "omitted_actors", "label": "Neighbour donates a rain tank", "description": "x"}]}],
        "model_architect": [{"factors": [], "research_needs": [], "structural_unknowns": [
            {"id": "volunteer_capacity", "description": "volunteer labour availability", "impact": "medium"}]}],
        "mechanism_synthesizer": [cands["bad"]],
        "critic": [{"omissions": [{"id": "pests", "description": "pest pressure", "risk": "low"}], "structural_unknowns": []}] * 4,
        "ir_repair": [cands["good"]],
    })
    eng = Engine(":memory:", FIX, llm=llm)
    eng.seed_fixtures()
    spec, res = build(eng, "garden")
    assert res.path == "generic+llm_synthesis" and res.report.passed, res.report.errors
    assert any(r["action"] == "llm_repair" for r in res.repair_log)
    mech = next(m for m in res.model.mechanisms if m.id == "llm_volunteer_hours")
    assert mech.epistemic_tier == FidelityTier.C_ILLUSTRATIVE  # A claim without evidence or assumptions is downgraded
    assert any(d["id"] == "llm_volunteer_hours" for d in res.report.downgrades)
    assert {t.task for t in res.model.construction_tasks} >= {"model_architect", "mechanism_synthesizer", "ir_repair"}
    assert any(o.id == "pests" and "critic hypothesis" in o.reason for o in res.model.omissions)
    assert any(i.label == "Neighbour donates a rain tank" for i in res.frontier.items)


def test_transfer_gate_rejects_wrong_jurisdiction_despite_similarity(engine):
    spec, res = build(engine, "restaurant")
    uk = next(r for r in res.model.reuse_manifest if r["entry"].startswith("pkg.uk_licensed_premises"))
    assert uk["transfer_ok"] is False and any("jurisdiction" in x for x in uk["reasons"]) and not uk["used"]
    de = engine.registry.get("us_de.annual_franchise_fee")
    dec = transfer_gate(de, TargetContext(domain_labels=["startup"], jurisdiction="US-CA", time_resolution="month",
                                          horizon_start="2026-10-18T00:00:00-07:00", horizon_end="2028-10-18T00:00:00-07:00"))
    assert not dec.ok and not dec.checks["jurisdiction"]
    ok = transfer_gate(de, TargetContext(domain_labels=["startup"], jurisdiction="US-DE", time_resolution="month",
                                         horizon_start="2026-10-18T00:00:00-07:00", horizon_end="2028-10-18T00:00:00-07:00"))
    assert ok.ok


def test_compile_never_fails_merely_on_incomplete_domain_coverage(engine):
    spec = load_spec("garden")
    spec = spec.model_copy(update={"decision_id": "dec_sparse", "metrics": ["happiness", "community_spirit"], "initial_facts": [],
                                   "constraints": [], "alternatives": [a.model_copy(update={"assumptions": []}) for a in spec.alternatives]})
    engine.create_decision(spec, "user_g1")
    res = engine.build_model("dec_sparse")
    assert res.report.passed and res.status == "LIMITED_MODEL"
    assert set(res.report.non_computable_metrics) == {"happiness", "community_spirit"}
    assert res.actionable_questions

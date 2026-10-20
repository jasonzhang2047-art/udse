"""Worked Example A (§17): group restaurant choice, short horizon, minute resolution."""

from __future__ import annotations

from udse.agent_runtime.observation import build_observation
from udse.common import parse_dt
from udse.model_ir.enums import ClaimStatus, CoverageStatus, DeadlineDisclosure, FidelityTier, RunStatus
from udse.model_ir.index import ModelIndex
from udse.model_ir.schemas import CoverageFrontier
from udse.trajectory_analytics.analytics import Analytics


def _world_values(eng, run_id, ref):
    return [op.after for e in eng.store.get_events(run_id) for op in e.patch if op.key == ref]


def test_evidence_freshness_leads_injection_and_unknowns(restaurant):
    eng, spec, res, plan, out = restaurant
    claims = {c.claim_id: c for c in res.bundle.claims}
    assert claims["claim_rb_price"].status == ClaimStatus.STALE                       # 6-month-old menu screenshot
    assert claims["claim_rb_gluten_review"].record_type == "contextual_lead"          # a review cannot certify allergens
    assert any("instruction-like" in w for w in res.bundle.warnings)                  # prompt injection treated as data
    st = {s.ref: s for s in res.model.state}
    assert st["r_c.table_for_4"].initial_value == "UNKNOWN"                           # no provider response → UNKNOWN
    assert st["r_b.table_for_4"].initial_value == "UNKNOWN"                           # injection did not mark it AVAILABLE
    assert st["r_b.gluten_safe"].initial_value == "UNKNOWN"                           # lead not used as a guarantee
    assert st["r_a.opens_at"].epistemic_tier == FidelityTier.A_GROUNDED
    assert st["r_b.price_pp"].epistemic_tier == FidelityTier.B_ASSUMPTION_BOUND       # stale → downgraded, visible
    needs = {n.question: n.status for n in res.needs}
    assert any("R-C table for 4 at 19:00" in q and s == "UNRESOLVED" for q, s in needs.items())


def test_no_invented_live_table_and_no_unsafe_dietary_claim(restaurant):
    eng, spec, res, plan, out = restaurant
    for r in out["screening_runs"] + out["depth_runs"]:
        rid = r["run_id"]
        # simulation never turns an UNKNOWN provider answer into availability
        for ref in ("r_b.table_for_4", "r_c.table_for_4"):
            assert all(v == "UNKNOWN" for v in _world_values(eng, rid, ref))
        # whenever the group agreed on a venue, Person B's hard constraint was verified through the venue's own notice
        if r["metrics"]["decision_reached"]["value"] is True:
            assert r["metrics"]["dietary_constraints_verified"]["value"] is True
        # seating only with provider-confirmed availability or an explicit branch assumption
        man = eng.store.get_run(rid)["manifest"]
        seated = "seated" in _world_values(eng, rid, "group.status")
        if seated:
            final = eng.store.get_final_snapshot(rid)["state"]["world"]
            assert final[f"{final['group.choice']}.table_for_4"] == "AVAILABLE" or "assume_tables_available" in man.scenario_ids


def test_trajectories_are_stateful_and_hard_constraints_drive_behaviour(restaurant):
    eng, spec, res, plan, out = restaurant
    depth = next(r for r in out["depth_runs"] if r["arm_id"] == "arm_deliberate")
    events = eng.store.get_events(depth["run_id"])
    types = [e.event_type for e in events]
    assert "action:request_allergen_info" in types and "allergen_info_response" in types   # information request, not reassurance
    assert "action:object_proposal" in types and "governance_satisfied" in types
    times = [parse_dt(e.sim_at) for e in events]
    assert times == sorted(times) and times[-1] > times[0]                                  # nonzero simulated-time advance
    b_obj = [e for e in events if e.actor_id == "agent_b" and e.event_type == "action:object_proposal"]
    assert b_obj and all("constraint.gluten_safe" in e.payload["decision"]["intent"]["rationale_summary"] for e in b_obj)


def test_coverage_frontier_statuses(restaurant):
    eng, spec, res, plan, out = restaurant
    fr = CoverageFrontier.model_validate(eng.store.get_frontier(spec.decision_id))
    by = {i.label: i for i in fr.items}
    late = by["Late dinner at R-C after its 21:00 closing"]
    assert late.status == CoverageStatus.REJECTED_VALIDATED_CONSTRAINT and late.citation
    assert by["Split into two smaller groups"].status == CoverageStatus.DISCOVERED_UNSIMULATED
    assert "split_coordination" in by["Split into two smaller groups"].status_reason
    assert by["Dinner at R-A"].status in (CoverageStatus.DEPTH_SIMULATED, CoverageStatus.LOW_FIDELITY_EXPLORED)
    assert "not exhaustive" in fr.not_yet_discovered_warning.lower()


def test_deadline_arms_share_caps_and_streams_and_only_disclosure_differs(restaurant):
    eng, spec, res, plan, out = restaurant
    runs = {r["arm_id"]: r for r in out["screening_runs"]}
    a = eng.store.get_run(runs["arm_deliberate"]["run_id"])["manifest"]
    b = eng.store.get_run(runs["arm_deliberate_deadline_aware"]["run_id"])["manifest"]
    assert a.execution_budget == b.execution_budget and a.random_streams == b.random_streams and a.model_ref == b.model_ref
    assert a.deadline_disclosure == DeadlineDisclosure.DEADLINE_UNDISCLOSED and b.deadline_disclosure == DeadlineDisclosure.DEADLINE_AWARE
    idx = ModelIndex(res.model)
    world = {s.ref: s.initial_value for s in res.model.state}
    hidden = build_observation(idx, "agent_c", world, [], -1, DeadlineDisclosure.DEADLINE_UNDISCLOSED, "2026-10-24T17:20:00-07:00")
    shown = build_observation(idx, "agent_c", world, [], -1, DeadlineDisclosure.DEADLINE_AWARE, "2026-10-24T17:20:00-07:00")
    assert "group.decision_deadline" not in hidden["visible_state"] and hidden["deadline"] is None
    assert shown["deadline"]["deadlines"]["group.decision_deadline"].startswith("2026-10-24T18:45")
    assert "token" not in str(shown).lower()                      # engineering caps are never disclosed to Agents
    assert "agent_b" not in str(shown["visible_state"])           # no other person's private features


def test_gap_triggered_research_creates_new_version_and_old_runs_stay_pinned(restaurant):
    eng, spec, res, plan, out = restaurant
    v1 = res.model
    v1_run = out["depth_runs"][0]["run_id"]
    an = Analytics(eng.store)
    rev = eng.revise_model(spec.decision_id, "provider recheck for R-C availability",
                           [{"kind": "INITIAL_CONDITION", "question": "Recheck R-C table for four at 19:00",
                             "target_refs": ["r_c.table_for_4_at_1900"], "critical": True}],
                           as_of=parse_dt("2026-10-24T16:30:00-07:00"))
    v2 = rev.model
    assert v2.model_version == v1.model_version + 1 and v2.hash != v1.hash and v2.parent_version == v1.model_version
    st = {s.ref: s for s in v2.state}
    assert st["r_c.table_for_4"].initial_value == "AVAILABLE" and st["r_c.table_for_4"].epistemic_tier == FidelityTier.A_GROUNDED
    assert any(c["ref"] == "r_c.table_for_4" for c in rev.diff["state_changed"])
    assert "not paired" in rev.diff["banner"]
    # the earlier run is untouched: same pinned hash, still replays bit-for-bit
    assert eng.store.get_run(v1_run)["manifest"].model_ref.hash == v1.hash
    rep = an.replay(v1_run)
    assert rep["event_log"]["ok"] and rep["transition"]["ok"]
    assert eng.store.get_model(f"wm_{spec.decision_id}", 1)["model"].hash == v1.hash


def test_replay_reconstructs_every_run_without_llm_calls(restaurant):
    eng, spec, res, plan, out = restaurant
    an = Analytics(eng.store)
    for r in out["screening_runs"] + out["depth_runs"]:
        rep = an.replay(r["run_id"])
        assert rep["event_log"]["ok"] and rep["transition"]["ok"] and rep["final_hash_matches_result"]
        assert rep["llm_calls_during_replay"] == 0


def test_fork_from_checkpoint_and_divergence(restaurant):
    eng, spec, res, plan, out = restaurant
    from udse.model_ir.schemas import Intervention

    base = next(r for r in out["depth_runs"] if r["arm_id"] == "arm_deliberate")["run_id"]
    cps = eng.store.list_checkpoints(base)
    cp = next(c for c in cps if c > 3)
    forked = eng.orchestrator.fork(base, cp, Intervention(scenario_ids=["r_a_fully_booked"], description="provider shows R-A booked"))
    man = eng.store.get_run(forked.run_id)["manifest"]
    assert man.branch_parent.run_id == base and man.branch_parent.checkpoint_seq == cp
    an = Analytics(eng.store)
    assert an.replay(forked.run_id)["event_log"]["ok"]
    cmp = an.compare(base, forked.run_id)
    assert cmp["same_model"] and "scenario_ids" in cmp["intervention"]
    assert forked.status in (RunStatus.TERMINAL_STATE, RunStatus.COMPLETED_HORIZON)

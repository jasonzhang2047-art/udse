"""Worked Example B (§18): fundraising now vs later vs staged, 24 months, monthly resolution."""

from __future__ import annotations

from udse.common import parse_dt
from udse.model_ir.enums import CoverageStatus, FidelityTier, RunStatus
from udse.model_ir.schemas import CoverageFrontier
from udse.temporal_kernel.kernel import Simulator
from udse.trajectory_analytics.analytics import Analytics


def test_no_invented_future_funding_probability(fundraising):
    eng, spec, res, plan, out = fundraising
    assert all(s.probability is None for s in res.model.scenarios)
    assert res.model.state[[s.ref for s in res.model.state].index("market.climate")].epistemic_tier == FidelityTier.C_ILLUSTRATIVE
    forecast = [n for n in res.needs if n.kind == "FORECAST_INPUT"]
    assert forecast and all(n.status == "UNRESOLVED" for n in forecast)
    blog = next(c for c in res.bundle.claims if c.claim_id == "claim_vc_blog_recovering")
    assert blog.record_type == "contextual_lead"
    pre = next(p for p in res.model.parameters if p.id == "reference_pre_money_usd")
    assert pre.epistemic_tier == FidelityTier.B_ASSUMPTION_BOUND  # 2025 history capped: not a 2027 fact


def test_cash_never_clipped_and_funding_only_through_investor_case_and_signatories(fundraising):
    eng, spec, res, plan, out = fundraising
    exhausted = [r for r in out["screening_runs"] if r["outcome"] == "CASH_EXHAUSTED"]
    funded = [r for r in out["screening_runs"] if r["metrics"]["funded"]["value"] is True]
    assert exhausted and funded, "the policies must lead to causally distinct trajectory families"
    for r in out["screening_runs"] + out["depth_runs"]:
        events = eng.store.get_events(r["run_id"])
        cash = [op.after for e in events for op in e.patch if op.key == "company.cash_usd"]
        assert min(cash, default=0) >= 0
        for e in events:
            for op in e.patch:
                if op.key == "company.cash_usd" and op.after > op.before:
                    assert e.event_type == "offer_decided" and e.transition_ref and "close_round" in e.transition_ref
                    sat = next(x for x in events if x.event_id in e.parent_event_ids)
                    ledger = next(o.after for o in sat.patch if o.key == "fundraising.ledger")
                    proposal = ledger["proposals"][-1]
                    assert {"founder_a", "founder_b"} <= {a for a, pos in proposal["positions"].items() if pos == "endorse"}
                    assert ledger["real_world_commitment"] is False


def test_policy_comparisons_are_paired_on_common_random_numbers(fundraising):
    eng, spec, res, plan, out = fundraising
    mans = [eng.store.get_run(r["run_id"])["manifest"] for r in out["screening_runs"]]
    assert len({tuple(sorted(m.random_streams.items())) for m in mans}) == 1
    assert len({m.model_ref.hash for m in mans}) == 1


def test_emergent_founder_behaviour_under_deferral(fundraising):
    eng, spec, res, plan, out = fundraising
    later = next(r for r in out["screening_runs"] if r["arm_id"] == "arm_later")
    events = eng.store.get_events(later["run_id"])
    acts = [(e.sim_at[:7], e.actor_id, e.event_type) for e in events if e.event_type.startswith("action:")]
    assert ("founder_a", "action:cut_burn") in {(a, t) for _, a, t in acts}
    d = [x for x in acts if x[2] == "action:reduce_commitment"]
    assert d and d[0][1] == "founder_d"
    gate = next(e for e in events if e.event_type == "gate:runway_alert")
    assert parse_dt(gate.sim_at) <= parse_dt(next(e.sim_at for e in events if e.event_type == "action:cut_burn"))


def test_structural_gap_then_external_study_creates_v2(fundraising):
    eng, spec, res, plan, out = fundraising
    assert res.report.metric_fidelity["founder_retention"] == FidelityTier.X_LIMITED_OR_ABSTAIN
    assert not any(m.id == "founder_retention" for m in res.model.mechanisms)
    old_runs = [r["run_id"] for r in out["screening_runs"]]
    rev = eng.revise_model(spec.decision_id, "external study: founder runway drives retention",
                           [{"kind": "MECHANISM", "question": "Does low runway drive co-founder departure?",
                             "target_refs": ["study_runway_retention.founder_runway_effect", "founder_retention"], "critical": True}],
                           as_of=parse_dt("2026-11-20T09:00:00-08:00"))
    v2 = rev.model
    assert "founder_retention" in rev.diff["mechanisms_added"]
    assert rev.report.metric_fidelity["founder_retention"] != FidelityTier.X_LIMITED_OR_ABSTAIN
    fr = CoverageFrontier.model_validate(eng.store.get_frontier(spec.decision_id))
    leaves = next(i for i in fr.items if i.label == "A founder leaves")
    assert leaves.status == CoverageStatus.REOPENED_AFTER_REVISION
    for rid in old_runs:  # no simulated event was promoted, v1 runs remain pinned
        assert eng.store.get_run(rid)["manifest"].model_ref.hash == res.model.hash
    plan2 = eng.orchestrator.plan(spec.decision_id)
    assert plan2.model_hash == v2.hash
    out2 = eng.orchestrator.execute(plan2, depth=False, sensitivity=False)
    later2 = next(r for r in out2["screening_runs"] if r["arm_id"] == "arm_later")
    types = {e.event_type for e in eng.store.get_events(later2["run_id"])}
    assert "founder_left" in types
    cmp = Analytics(eng.store).compare(old_runs[1], later2["run_id"])
    assert cmp["banner"] and not cmp["same_model"]


def test_two_domains_share_one_kernel(restaurant, fundraising):
    """§15.3 Two-domain kernel: the same Simulator, clock, events, snapshots, observation and replay."""
    for eng, spec, res, plan, out in (restaurant, fundraising):
        rid = out["depth_runs"][0]["run_id"]
        rep = Analytics(eng.store).replay(rid)
        assert rep["event_log"]["ok"] and rep["transition"]["ok"]
    assert restaurant[2].model.scope.time_resolution == "minute" and fundraising[2].model.scope.time_resolution == "month"
    import udse.temporal_kernel.kernel as k

    src = open(k.__file__).read()
    for word in ("restaurant", "venue", "fundrais", "investor", "founder"):
        assert word not in src.lower(), f"kernel must stay domain-agnostic ({word})"

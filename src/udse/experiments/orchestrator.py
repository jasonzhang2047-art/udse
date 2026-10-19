"""Experiment Orchestrator (§10): breadth before depth under a unified, multi-resource budget.

Experiment unit = {frozen model version, profile snapshots, initial state, horizon, policy,
treatment, exogenous random streams, budget}. Paired arms share random-stream seeds (common random
numbers) and identical engineering caps, so within-model differences are attributable to the
intervention. Interventions stay identified: policy, external scenario, Agent overlay, deadline
disclosure and model version are never pooled.
"""

from __future__ import annotations

import itertools
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ..agent_runtime.policies import AgentRuntimeRouter, ProfileUtilityPolicy
from ..agent_runtime.profiles import select_working_set, snapshot_ref
from ..common import new_id
from ..llm.agent_policy import LLMAgentPolicy
from ..model_ir.enums import (
    CoverageStatus,
    DeadlineDisclosure,
    FeatureStatus,
    FidelityMode,
    ResearchNeedKind,
    Role,
    RunStatus,
)
from ..model_ir.schemas import (
    CoverageFrontier,
    DecisionWorldModel,
    ExecutionBudget,
    FeatureOverlay,
    Intervention,
    BranchParent,
    ModelRef,
    ResearchNeed,
    RunAgentConfig,
    RunManifest,
    RunResult,
)
from ..temporal_kernel.kernel import Simulator

if TYPE_CHECKING:  # pragma: no cover
    from ..engine import Engine


class DecisionBudget(BaseModel):
    profile: str
    max_screening_runs: int
    max_depth_runs: int
    max_sensitivity_runs: int
    total_model_tokens: int
    total_wall_seconds: float
    screening_caps: ExecutionBudget
    depth_caps: ExecutionBudget
    allocations: dict[str, float] = Field(default_factory=dict)
    reserve_fraction: float = 0.1


BUDGET_PROFILES: dict[str, DecisionBudget] = {
    "quick": DecisionBudget(profile="quick", max_screening_runs=8, max_depth_runs=2, max_sensitivity_runs=2, total_model_tokens=60000,
                            total_wall_seconds=60, screening_caps=ExecutionBudget(max_events=200, max_agent_wakeups=50, max_wall_seconds=10, max_model_tokens=0),
                            depth_caps=ExecutionBudget(max_events=500, max_agent_wakeups=100, max_wall_seconds=60, max_model_tokens=20000)),
    "balanced": DecisionBudget(profile="balanced", max_screening_runs=16, max_depth_runs=4, max_sensitivity_runs=4, total_model_tokens=160000,
                               total_wall_seconds=180, screening_caps=ExecutionBudget(max_events=300, max_agent_wakeups=80, max_wall_seconds=15, max_model_tokens=0),
                               depth_caps=ExecutionBudget(max_events=800, max_agent_wakeups=160, max_wall_seconds=120, max_model_tokens=40000)),
    "deep": DecisionBudget(profile="deep", max_screening_runs=30, max_depth_runs=5, max_sensitivity_runs=8, total_model_tokens=400000,
                           total_wall_seconds=600, screening_caps=ExecutionBudget(max_events=400, max_agent_wakeups=100, max_wall_seconds=20, max_model_tokens=0),
                           depth_caps=ExecutionBudget(max_events=1200, max_agent_wakeups=200, max_wall_seconds=180, max_model_tokens=80000)),
}
for _b in BUDGET_PROFILES.values():
    _b.allocations = {"breadth": 0.35, "depth": 0.35, "robustness": 0.15, "validation": 0.05, "reserve": 0.10}


class ExperimentArm(BaseModel):
    arm_id: str
    label: str
    policy_id: str
    scenario_ids: list[str] = Field(default_factory=list)
    deadline_disclosure: DeadlineDisclosure = DeadlineDisclosure.NOT_APPLICABLE
    overlays: dict[str, list[FeatureOverlay]] = Field(default_factory=dict)
    parameter_overrides: dict[str, Any] = Field(default_factory=dict)
    frontier_item_ids: list[str] = Field(default_factory=list)
    pair_group: str | None = None
    intervention_axis: str = "policy"
    stage: str = "screening"


class ExperimentPlan(BaseModel):
    experiment_id: str
    decision_id: str
    model_ref: ModelRef
    model_hash: str
    created_at: str
    random_streams: dict[str, int]
    budget: DecisionBudget
    arms: list[ExperimentArm]
    pairings: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    stages: dict[str, Any] = Field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Orchestrator:
    def __init__(self, engine: "Engine"):
        self.engine = engine
        self.store = engine.store
        self.cred = engine.creds[Role.ORCHESTRATOR]
        self.scheduler_cred = engine.creds[Role.RUN_SCHEDULER]
        self.worker_cred = engine.creds[Role.SIMULATION_WORKER]

    # ------------------------------------------------------------------
    # planning
    # ------------------------------------------------------------------
    def plan(self, decision_id: str, *, budget_profile: str | None = None, seed: int = 1093) -> ExperimentPlan:
        spec = self.store.get_decision_spec(decision_id)
        row = self.store.get_model(f"wm_{decision_id}")
        if row is None:
            raise KeyError("no compiled model for this decision")
        model: DecisionWorldModel = row["model"]
        if not row["report"]["allowed_run_modes"]:
            raise ValueError("model is not authorised for simulation")
        frontier = CoverageFrontier.model_validate(self.store.get_frontier(decision_id))
        budget = BUDGET_PROFILES[budget_profile or {"quick": "quick", "balanced": "balanced", "deep": "deep"}[spec.fidelity_request]]
        policies = [p.id for p in model.policies]
        main = next((it.policy_id for it in frontier.items if it.user_interest and it.policy_id in policies), policies[0])
        external = [s for s in model.scenarios if s.kind == "external_condition"]
        branch = [s for s in model.scenarios if s.kind in ("branch_assumption", "evidence_scenario")]
        has_deadline = any(sv.is_deadline for sv in model.state)
        arms: list[ExperimentArm] = []

        def fam_ids(policy_id, scenario_ids):
            return [it.id for it in frontier.items if (it.policy_id in (None, policy_id) and (it.policy_id or it.scenario_ids))
                    and set(it.scenario_ids) <= set(scenario_ids) and not it.required_mechanisms]

        disclosure = DeadlineDisclosure.DEADLINE_UNDISCLOSED if has_deadline else DeadlineDisclosure.NOT_APPLICABLE
        # stratified grid: every policy once, every external scenario at least once, quotas before extras
        plabel = {p.id: p.label for p in model.policies}
        for pid in policies:
            arms.append(ExperimentArm(arm_id=f"arm_{pid}", label=plabel[pid], policy_id=pid, deadline_disclosure=disclosure,
                                      frontier_item_ids=fam_ids(pid, []), pair_group="policy", intervention_axis="policy"))
        for s in external:
            arms.append(ExperimentArm(arm_id=f"arm_{main}_{s.id}", label=f"{plabel[main]} × {s.label}", policy_id=main, scenario_ids=[s.id],
                                      deadline_disclosure=disclosure, frontier_item_ids=fam_ids(main, [s.id]),
                                      pair_group="external_condition", intervention_axis="external_scenario"))
        for s in branch:
            arms.append(ExperimentArm(arm_id=f"arm_{main}_{s.id}", label=f"{plabel[main]} × {s.label}", policy_id=main, scenario_ids=[s.id],
                                      deadline_disclosure=disclosure, frontier_item_ids=fam_ids(main, [s.id]),
                                      pair_group="evidence_or_branch_assumption", intervention_axis="branch_assumption"))
        if has_deadline and model.agents:
            # Deadline-aware arm: identical world deadline and identical engineering caps; only Agent information differs.
            arms.append(ExperimentArm(arm_id=f"arm_{main}_deadline_aware", label=f"{plabel[main]} × deadline disclosed to Agents", policy_id=main,
                                      deadline_disclosure=DeadlineDisclosure.DEADLINE_AWARE, frontier_item_ids=fam_ids(main, []),
                                      pair_group="deadline_disclosure", intervention_axis="deadline_disclosure"))
        profiles = self.engine.profiles_for(model)
        for aid, prof in profiles.items():
            unknown = [f for f in prof.features if f.status == FeatureStatus.UNKNOWN and f.key.startswith("behavior.")
                       and (has_deadline or f.key != "behavior.deadline_sensitivity")]
            if unknown:
                f = unknown[0]
                for v in (0.2, 0.8):
                    arms.append(ExperimentArm(
                        arm_id=f"arm_{main}_overlay_{aid}_{f.key.split('.')[-1]}_{int(v * 10)}",
                        label=f"{plabel[main]} × {aid} {f.key.split('.')[-1]}={v} (hypothetical overlay)", policy_id=main, deadline_disclosure=DeadlineDisclosure.DEADLINE_AWARE if has_deadline else disclosure,
                        overlays={aid: [FeatureOverlay(key=f.key, value=v, hypothesis=f"{f.key} is unknown for this person; test {v}")]},
                        pair_group=f"overlay:{aid}:{f.key}", intervention_axis="agent_overlay"))
                break
        notes = []
        if len(arms) > budget.max_screening_runs:
            notes.append(f"{len(arms) - budget.max_screening_runs} arms set aside by the screening budget (not infeasible)")
        pairings = [
            {"group": g, "arms": [a.arm_id for a in arms if a.pair_group == g],
             "identical": ["model hash", "random streams", "engineering caps", "profile snapshots"],
             "differs_by": {"policy": "decision policy", "external_condition": "external scenario", "deadline_disclosure": "Agent knowledge of the real deadline",
                            "evidence_or_branch_assumption": "explicit branch assumption"}.get(g, "hypothetical Agent overlay")}
            for g in sorted({a.pair_group for a in arms if a.pair_group})
        ]
        if has_deadline:
            pairings.append({"group": "deadline_pair", "arms": [f"arm_{main}", f"arm_{main}_deadline_aware"],
                             "identical": ["world deadline state", "engineering caps", "random streams"],
                             "differs_by": "deadline disclosed to Agents (DEADLINE_AWARE) vs not (DEADLINE_UNDISCLOSED)"})
        plan = ExperimentPlan(
            experiment_id=new_id("exp"), decision_id=decision_id,
            model_ref=ModelRef(model_id=model.model_id, version=model.model_version, hash=model.hash), model_hash=model.hash,
            created_at=_now(), random_streams={"default": seed, "travel": seed + 1, "venue_capacity": seed + 2, "market": seed + 3,
                                               "pilot": seed + 4, "agent_sampling": 581},
            budget=budget, arms=arms, pairings=pairings, notes=notes,
        )
        self.store.put_experiment(self.cred, plan.model_dump(mode="json"))
        return plan

    # ------------------------------------------------------------------
    # execution
    # ------------------------------------------------------------------
    def _runtime(self, model: DecisionWorldModel, kinds: dict[str, str]):
        policies: dict[str, Any] = {"profile_utility": ProfileUtilityPolicy()}
        if self.engine.llm is not None:
            policies["llm"] = LLMAgentPolicy(self.engine.llm, lambda rec: self.store.put_llm_record(self.worker_cred, rec))
        return AgentRuntimeRouter(policies, kinds)

    def _features(self, model: DecisionWorldModel):
        dims = {d for a in model.actions for d in a.appraisal}
        out, refs = {}, {}
        for aid, prof in self.engine.profiles_for(model).items():
            out[aid] = select_working_set(prof, dims)
            refs[aid] = snapshot_ref(prof)
        return out, refs

    def run_arm(self, plan: ExperimentPlan, model: DecisionWorldModel, arm: ExperimentArm, mode: FidelityMode,
                caps: ExecutionBudget, use_llm: bool = False) -> RunResult:
        features, refs = self._features(model)
        kind = "llm" if (use_llm and self.engine.llm is not None) else "profile_utility"
        configs = {a.id: RunAgentConfig(agent_id=a.id, profile_snapshot_ref=refs.get(a.id), overlays=arm.overlays.get(a.id, []),
                                        policy_kind=kind) for a in model.agents}
        man = RunManifest(
            run_id=new_id("run"), experiment_id=plan.experiment_id, decision_id=plan.decision_id, model_ref=plan.model_ref,
            profile_snapshot_refs=refs, agent_configs=configs, policy_id=arm.policy_id, scenario_ids=arm.scenario_ids,
            deadline_disclosure=arm.deadline_disclosure, random_streams=plan.random_streams, execution_budget=caps,
            fidelity_mode=mode, parameter_overrides=arm.parameter_overrides, pair_group=arm.pair_group,
            frontier_item_ids=arm.frontier_item_ids, label=arm.label,
            assumptions=[o.hypothesis for ov in arm.overlays.values() for o in ov],
        )
        self.store.put_run_manifest(self.scheduler_cred, man)  # model hash resolved once, at run creation (§14.8)
        sim = Simulator(model, man, self._runtime(model, {a: kind for a in configs}), self.store.run_store(self.worker_cred), features)
        return sim.run()

    def fork(self, run_id: str, checkpoint_seq: int, intervention: Intervention) -> RunResult:
        """Fork from a shared valid checkpoint with one identified intervention (§10.3)."""
        parent = self.store.get_run(run_id)
        if parent is None:
            raise KeyError(run_id)
        cp = self.store.get_checkpoint(run_id, checkpoint_seq)
        if cp is None:
            raise ValueError(f"no checkpoint at seq {checkpoint_seq}; available: {self.store.list_checkpoints(run_id)}")
        pm: RunManifest = parent["manifest"]
        row = self.store.get_model_by_hash(pm.model_ref.hash)
        model = row["model"]
        configs = {a: c.model_copy(update={"overlays": intervention.agent_overlays.get(a, c.overlays)}) for a, c in pm.agent_configs.items()}
        man = pm.model_copy(deep=True, update={
            "run_id": new_id("run"), "branch_parent": BranchParent(run_id=run_id, checkpoint_seq=checkpoint_seq),
            "intervention": intervention, "policy_id": intervention.policy_id or pm.policy_id,
            "scenario_ids": intervention.scenario_ids if intervention.scenario_ids is not None else pm.scenario_ids,
            "agent_configs": configs, "label": f"fork of {pm.label or run_id}: {intervention.description}", "state": RunStatus.CREATED,
            "initial_state_hash": None,
        })
        self.store.put_run_manifest(self.scheduler_cred, man)
        features, _ = self._features(model)
        prior = [e for e in self.store.get_events(run_id) if e.seq <= checkpoint_seq]
        kinds = {a: c.policy_kind for a, c in configs.items()}
        sim = Simulator(model, man, self._runtime(model, kinds), self.store.run_store(self.worker_cred), features,
                        checkpoint=cp, prior_events=prior)
        return sim.run()

    def execute(self, plan: ExperimentPlan, *, depth: bool = True, sensitivity: bool = True) -> dict[str, Any]:
        t0 = time.monotonic()
        row = self.store.get_model_by_hash(plan.model_hash)
        model: DecisionWorldModel = row["model"]
        frontier = CoverageFrontier.model_validate(self.store.get_frontier(plan.decision_id))
        b = plan.budget
        results: dict[str, RunResult] = {}
        arm_of: dict[str, ExperimentArm] = {}
        screened, set_aside = plan.arms[: b.max_screening_runs], plan.arms[b.max_screening_runs:]
        for arm in screened:
            if time.monotonic() - t0 > b.total_wall_seconds * (1 - b.reserve_fraction):
                set_aside.append(arm)
                continue
            r = self.run_arm(plan, model, arm, FidelityMode.SCREENING, b.screening_caps)
            results[r.run_id] = r
            arm_of[r.run_id] = arm
        diagnostics = [d for r in results.values() for d in r.diagnostics]
        sens = self._sensitivity(plan, model, results, arm_of) if sensitivity else []
        depth_runs: dict[str, RunResult] = {}
        if depth:
            for arm in self._select_depth(model, results, arm_of, b.max_depth_runs):
                d_arm = arm.model_copy(update={"stage": "depth"})
                r = self.run_arm(plan, model, d_arm, FidelityMode.DEPTH, b.depth_caps, use_llm=True)
                depth_runs[r.run_id] = r
                arm_of[r.run_id] = d_arm
                diagnostics += r.diagnostics
        frontier = self._update_frontier(frontier, model, results, depth_runs, arm_of, set_aside)
        self.store.put_frontier(self.cred, frontier.model_dump(mode="json"))
        needs = self._diagnostic_research(plan, model, diagnostics, sens, frontier)
        summary = {
            "experiment_id": plan.experiment_id, "model_hash": plan.model_hash,
            "screening_runs": [self._run_row(r, arm_of[r.run_id]) for r in results.values()],
            "depth_runs": [self._run_row(r, arm_of[r.run_id]) for r in depth_runs.values()],
            "set_aside_arms": [a.arm_id for a in set_aside],
            "sensitivity": sens, "queued_research_needs": [n.model_dump(mode="json") for n in needs],
            "wall_seconds": round(time.monotonic() - t0, 3),
            "note": "Run counts are coverage statistics within one model, never real-world likelihoods.",
        }
        stored = plan.model_dump(mode="json")
        stored["stages"] = {"executed_at": _now(), "summary": {k: v for k, v in summary.items() if k != "sensitivity"}}
        self.store.put_experiment(self.cred, stored)
        return summary

    @staticmethod
    def _run_row(r: RunResult, arm: ExperimentArm) -> dict[str, Any]:
        return {"run_id": r.run_id, "arm_id": arm.arm_id, "label": arm.label, "stage": arm.stage, "status": str(r.status),
                "outcome": r.terminal_outcome, "flags": r.outcome_flags, "horizon_reached": r.horizon_reached,
                "last_simulated_at": r.last_simulated_at, "events": r.events_committed,
                "metrics": r.final_metrics, "pair_group": arm.pair_group, "axis": arm.intervention_axis}

    def _signature(self, run_id: str, r: RunResult) -> tuple:
        evs = self.store.get_events(run_id)
        kinds = sorted({e.event_type.split(":")[0] if e.event_type.startswith("tick") else e.event_type for e in evs
                        if e.kind not in ("AGENT_DECISION",) or e.event_type.startswith("action:")})
        return (r.terminal_outcome or str(r.status), tuple(kinds))

    def _select_depth(self, model, results: dict[str, RunResult], arm_of, n: int) -> list[ExperimentArm]:
        """Choose distinct causal pathways, not the top-scoring narratives (§9.4)."""
        chosen: list[ExperimentArm] = []
        seen_outcomes: set[str] = set()
        seen_paths: set[tuple] = set()
        cands = sorted(results.items(), key=lambda kv: (arm_of[kv[0]].intervention_axis != "policy", arm_of[kv[0]].arm_id))
        for rid, r in cands:
            if len(chosen) >= n:
                break
            sig = self._signature(rid, r)
            if r.status in (RunStatus.FAILED, RunStatus.MODEL_EXECUTION_BLOCKED):
                continue
            if sig[0] in seen_outcomes and sig in seen_paths:
                continue  # superficial variant of an already selected pathway
            if sig[0] in seen_outcomes and arm_of[rid].intervention_axis != "deadline_disclosure":
                continue
            chosen.append(arm_of[rid])
            seen_outcomes.add(sig[0])
            seen_paths.add(sig)
        return chosen

    def _sensitivity(self, plan, model, results, arm_of) -> list[dict[str, Any]]:
        out = []
        metric = next((m for m in model.metrics if m.computable and m.direction), None)
        probes = [p for p in model.parameters if p.range and p.type == "quantity"][: plan.budget.max_sensitivity_runs // 2]
        policy_runs = {arm_of[rid].policy_id: r for rid, r in results.items() if arm_of[rid].intervention_axis == "policy"}
        for p in probes:
            ranks = {}
            for bound in p.range:
                vals = {}
                for pid in list(policy_runs)[:3]:
                    arm = ExperimentArm(arm_id=f"sens_{p.id}_{bound}_{pid}", label=f"{pid} with {p.id}={bound} (sensitivity probe)", policy_id=pid,
                                        parameter_overrides={p.id: bound}, intervention_axis="sensitivity", stage="sensitivity")
                    r = self.run_arm(plan, model, arm, FidelityMode.SENSITIVITY, plan.budget.screening_caps)
                    v = (r.final_metrics.get(metric.id, {}) or {}).get("value") if metric else None
                    vals[pid] = v["value"] if isinstance(v, dict) else v
                ranks[bound] = sorted(vals, key=lambda k: (vals[k] is None, -(vals[k] or 0) if metric.direction == "higher_better" else (vals[k] or 0)))
            flips = len({tuple(v) for v in ranks.values()}) > 1
            out.append({"parameter": p.id, "range": p.range, "metric": metric.id if metric else None, "policy_ranking": ranks,
                        "ranking_flips": flips,
                        "interpretation": "ranking depends on this unvalidated parameter within its declared range" if flips else
                        "ranking stable across the declared range (within-model statement only)"})
        return out

    def _update_frontier(self, frontier: CoverageFrontier, model, screening, depth, arm_of, set_aside) -> CoverageFrontier:
        def final_world(rid):
            snap = self.store.get_final_snapshot(rid)
            return snap["state"]["world"] if snap else {}

        all_runs = {**screening, **depth}
        for it in frontier.items:
            if it.status in (CoverageStatus.REJECTED_VALIDATED_CONSTRAINT,) or it.required_mechanisms and any(
                    m not in {x.id for x in model.mechanisms} for m in it.required_mechanisms):
                continue
            matched_s, matched_d = [], []
            for rid, r in all_runs.items():
                arm = arm_of[rid]
                if it.outcome_match:
                    ok = final_world(rid).get(it.outcome_match["ref"]) == it.outcome_match["value"]
                elif it.policy_id or it.scenario_ids:
                    ok = (it.policy_id in (None, arm.policy_id)) and set(it.scenario_ids) == set(arm.scenario_ids) and not arm.overlays \
                         and arm.deadline_disclosure != DeadlineDisclosure.DEADLINE_AWARE
                else:
                    ok = False
                if ok:
                    (matched_d if rid in depth else matched_s).append(rid)
            prev = it.status
            if matched_d:
                it.status = CoverageStatus.DEPTH_SIMULATED
                it.status_reason = f"{len(matched_d)} detailed trajectory(ies) with pinned model v{model.model_version}"
            elif matched_s:
                it.status = CoverageStatus.LOW_FIDELITY_EXPLORED
                it.status_reason = f"{len(matched_s)} screening run(s); exploratory preview only"
            elif it.policy_id and any(a.policy_id == it.policy_id for a in set_aside):
                it.status = CoverageStatus.SET_ASIDE_BUDGET
                it.status_reason = "not explored within the approved budget (not infeasible)"
            elif it.outcome_match:
                it.status_reason = "no simulated trajectory reached this outcome under the tested arms"
            it.run_ids = sorted(set(it.run_ids) | set(matched_s) | set(matched_d))
            if it.status != prev:
                it.history.append({"model_version": model.model_version, "from": str(prev), "to": str(it.status), "reason": it.status_reason})
        unrun = sum(1 for it in frontier.items if it.status == CoverageStatus.DISCOVERED_UNSIMULATED)
        frontier.stop_rationale = (f"Stopped after the approved screening/depth tranches. {unrun} discovered families remain unsimulated; "
                                   "coverage is not exhaustive.")
        return frontier

    def _diagnostic_research(self, plan, model, diagnostics, sens, frontier) -> list[ResearchNeed]:
        """Diagnostics only queue research requests — never model edits or world facts (§3.4, §10.6)."""
        needs = []
        for s in sens:
            if s["ranking_flips"]:
                needs.append(ResearchNeed(need_id=new_id("need"), kind=ResearchNeedKind.MECHANISM,
                                          question=f"What external evidence constrains parameter {s['parameter']}?",
                                          why_it_matters="policy ranking flips within its declared exploratory range",
                                          target_refs=[s["parameter"]], priority=1, priority_rationale="high sensitivity diagnostic",
                                          consequence_if_unresolved="comparison remains assumption-dependent"))
        for su in model.structural_unknowns:
            if su.impact == "high" and su.status == "DISCOVERED_UNMODELED":
                needs.append(ResearchNeed(need_id=new_id("need"), kind=ResearchNeedKind.MECHANISM,
                                          question=f"Is there external evidence for: {su.description}",
                                          why_it_matters="high-impact structural unknown", target_refs=[su.id], priority=2,
                                          priority_rationale="structural gap", affects_metrics=su.affects_metrics))
        for d in diagnostics:
            if d.failure_or_gap_type in ("INVARIANT_VIOLATION", "MODEL_EXECUTION_ERROR"):
                needs.append(ResearchNeed(need_id=new_id("need"), kind=ResearchNeedKind.MECHANISM,
                                          question=f"Review model behaviour: {d.observed_sim_effect}", why_it_matters="simulation diagnostic (not evidence)",
                                          target_refs=d.impacted_mechanisms, priority=2, priority_rationale="diagnostic",
                                          trigger_diagnostic_id=d.diagnostic_id))
                break
        for n in needs:
            self.store.put_research_need(self.engine.creds[Role.RESEARCH_PLANNER], plan.decision_id, n)
        return needs

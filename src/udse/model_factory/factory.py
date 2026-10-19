"""Dynamic Model Factory (§6): research-first, on-demand assembly of an immutable decision-scoped
world model (Layer B).

START → decision parse → breadth inventory → registry retrieval (transfer gate) → prioritised
research → evidence normalisation → structural synthesis → typed IR compilation → static validation
→ property tests → coverage/fidelity review → versioned B model → simulation authorisation.

Missing predefined models trigger the repair ladder (§6.5) — reuse → research → synthesize →
validate → downgrade → limited abstention — never an automatic refusal.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from ..common import new_id
from ..decision_scope.planner import DecisionScope, plan_scope
from ..llm.provider import LLMProvider
from ..model_ir.enums import FidelityTier, Role
from ..model_ir.schemas import (
    CoverageFrontier,
    DecisionSpec,
    DecisionWorldModel,
    EvidenceBundle,
    LLMTaskRecord,
    ResearchNeed,
    ValidationReport,
)
from ..model_registry.registry import Registry, TargetContext
from ..model_validator.compiler import compile_model
from ..research.adapters import SourceAdapter
from ..research.planner import ResearchAcquirer, ResearchPlanner
from ..storage.credentials import Credential
from ..storage.sqlite_store import Store
from .binder import bind_template, slot_research_needs
from .breadth import annotate_frontier, discover
from .generic import GenericSynthesizer
from .llm_synthesis import LLMModelTasks

MAX_REPAIR_ATTEMPTS = 3


@dataclass
class BuildResult:
    status: str  # MODEL_READY | LIMITED_MODEL | INSUFFICIENT_MODEL
    model: DecisionWorldModel | None
    report: ValidationReport
    frontier: CoverageFrontier
    bundle: EvidenceBundle
    needs: list[ResearchNeed]
    path: str
    scope: DecisionScope
    binding: dict[str, Any] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)
    actionable_questions: list[str] = field(default_factory=list)
    repair_log: list[dict[str, Any]] = field(default_factory=list)
    diff: dict[str, Any] | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.status, "path": self.path,
            "model_ref": {"model_id": self.model.model_id, "version": self.model.model_version, "hash": self.model.hash} if self.model else None,
            "promotion_state": str(self.model.promotion_state) if self.model else None,
            "report": {"passed": self.report.passed, "errors": self.report.errors, "warnings": self.report.warnings[:20],
                       "downgrades": self.report.downgrades, "allowed_run_modes": self.report.allowed_run_modes,
                       "metric_fidelity": {k: str(v) for k, v in self.report.metric_fidelity.items()},
                       "non_computable_metrics": self.report.non_computable_metrics,
                       "scorecard": self.report.scorecard.model_dump()},
            "limitations": self.limitations, "actionable_questions": self.actionable_questions,
            "repair_log": self.repair_log, "binding": self.binding, "diff": self.diff,
            "research": {"needs": [n.model_dump(mode="json") for n in self.needs], "conflicts": self.bundle.conflicts,
                         "rejected": self.bundle.rejected, "warnings": self.bundle.warnings},
        }


def heuristic_repair(ir: dict[str, Any], report: ValidationReport, log: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Downgrade-and-isolate repair when no LLM is available: drop elements the compiler rejected,
    record each as a structural unknown, and keep everything else (§6.5 steps 4–5)."""
    ir = copy.deepcopy(ir)
    changed = False
    for err in report.errors:
        if err.startswith("[C01_schema]"):
            return None
        for kind, key, coll in (("mechanism", "id", "mechanisms"), ("action", "id", "actions"), ("scenario", "id", "scenarios")):
            m = re.search(rf"{kind} ([A-Za-z0-9_]+)", err)
            if m and any(x.get(key) == m.group(1) for x in ir.get(coll, [])):
                ir[coll] = [x for x in ir[coll] if x.get(key) != m.group(1)]
                ir.setdefault("structural_unknowns", []).append({
                    "id": f"removed_{kind}_{m.group(1)}", "impact": "high",
                    "description": f"{kind} {m.group(1)} was removed during repair because it failed validation: {err}"})
                log.append({"action": f"removed {kind}", "id": m.group(1), "error": err})
                changed = True
                break
        else:
            m = re.search(r"metric ([A-Za-z0-9_]+)", err)
            if m:
                for mt in ir.get("metrics", []):
                    if mt.get("id") == m.group(1) and mt.get("computable", True):
                        mt.update({"computable": False, "reason": f"repair: {err}", "expr": None})
                        log.append({"action": "metric marked non-computable", "id": m.group(1), "error": err})
                        changed = True
    if changed:
        # drop dangling references to removed mechanisms (governance acts, wakeups)
        mech_ids = {x["id"] for x in ir.get("mechanisms", [])}
        before = len(ir.get("actions", []))
        ir["actions"] = [a for a in ir.get("actions", []) if not a.get("governance") or a["governance"]["mechanism"] in mech_ids]
        if len(ir["actions"]) != before:
            log.append({"action": "removed actions bound to removed governance mechanisms"})
    return ir if changed else None


class ModelFactory:
    def __init__(self, store: Store, registry: Registry, creds: dict[Role, Credential],
                 adapters_for: Callable[[DecisionSpec], list[SourceAdapter]], llm: LLMProvider | None = None):
        self.store = store
        self.registry = registry
        self.creds = creds
        self.adapters_for = adapters_for
        self.llm = llm
        self.planner = ResearchPlanner()
        self.acquirer = ResearchAcquirer(store, creds[Role.EVIDENCE_ADAPTER], creds[Role.INGESTION])

    def _select_package(self, spec: DecisionSpec, target: TargetContext) -> tuple[Any, list[dict[str, Any]]]:
        cands = self.registry.search_compatible(target, kind="model_package")
        manifest, chosen = [], None
        for entry, dec in cands:
            rec = {"entry": entry.ref, "kind": entry.kind, "transfer_ok": dec.ok, "reasons": dec.reasons, "checks": dec.checks, "used": False}
            manifest.append(rec)
        ok = [(e, d) for e, d in cands if d.ok]
        if spec.model_package_hint:
            ok = [(e, d) for e, d in ok if e.entry_id == spec.model_package_hint] or []
        if ok:
            chosen = max(ok, key=lambda ed: len(set(ed[0].applicability.domains) & set(target.domain_labels)))[0]
            for rec in manifest:
                if rec["entry"] == chosen.ref:
                    rec["used"] = True
        return chosen, manifest

    def build(self, spec: DecisionSpec, as_of: datetime, *, extra_needs: list[dict[str, Any]] | None = None,
              parent: DecisionWorldModel | None = None, previous_frontier: CoverageFrontier | None = None,
              revision_reason: str | None = None, trigger_diagnostic_id: str | None = None) -> BuildResult:
        scope = plan_scope(spec)
        llm_records: list[dict[str, Any]] = []
        target = TargetContext(domain_labels=scope.domain_labels or ["*"], jurisdiction=scope.jurisdiction,
                               time_resolution=scope.time_resolution, horizon_start=scope.horizon_start, horizon_end=scope.horizon_end)
        package, reuse_manifest = self._select_package(spec, target)
        template = self.registry.load_template(package) if package else None
        hints = (package.breadth_hints if package else []) + (template or {}).get("breadth_hints", [])
        frontier = discover(spec, scope, hints, self.llm, llm_records)          # Pass 0 before any pruning
        generic_ir, generic_notes = (None, {"research_needs": []})
        if template is None:
            generic_ir, generic_notes = GenericSynthesizer(self.registry).synthesize(spec, scope, EvidenceBundle(
                bundle_id="pre", decision_id=spec.decision_id, decision_rev=spec.revision, as_of=as_of.isoformat()))
        slot_needs = slot_research_needs(template) if template else []
        pkg_needs = (package.research_needs if package else []) + (template or {}).get("research_needs", [])
        tasks = LLMModelTasks(self.llm, llm_records) if self.llm else None
        arch = tasks.architect(spec, scope, EvidenceBundle(bundle_id="pre", decision_id=spec.decision_id, decision_rev=spec.revision,
                                                           as_of=as_of.isoformat())) if tasks else None
        arch_needs = (arch or {}).get("research_needs", [])
        needs = self.planner.plan(spec, slot_needs + pkg_needs, generic_notes["research_needs"] + arch_needs + (extra_needs or []),
                                  trigger_diagnostic_id)
        bundle = self.acquirer.acquire(spec, needs, self.adapters_for(spec), as_of)
        for n in needs:
            self.store.put_research_need(self.creds[Role.RESEARCH_PLANNER], spec.decision_id, n)

        binding: dict[str, Any] = {}
        candidates: list[tuple[str, dict[str, Any]]] = []
        if template is not None:
            ir, binding = bind_template(template, spec, bundle)
            ir["reuse_manifest"] = reuse_manifest
            candidates.append((f"model_package:{package.ref}", ir))
        else:
            generic_ir, _ = GenericSynthesizer(self.registry).synthesize(spec, scope, bundle)
            generic_ir["reuse_manifest"] = generic_ir.get("reuse_manifest", []) + reuse_manifest
            if arch:
                generic_ir["structural_unknowns"] += [dict(u, status="DISCOVERED_UNMODELED") for u in arch.get("structural_unknowns", [])]
                generic_ir["factor_table"] += [f for f in arch.get("factors", [])]
            if tasks:
                llm_ir = tasks.synthesize(spec, scope, bundle, generic_ir)
                if llm_ir:
                    candidates.append(("generic+llm_synthesis", llm_ir))
            candidates.append(("generic", generic_ir))

        version = (parent.model_version + 1) if parent else (len(self.store.list_model_versions(model_id=f"wm_{spec.decision_id}")) + 1)
        registry_status = self.registry.status_map()
        repair_log: list[dict[str, Any]] = []
        model, report, path = None, None, "none"
        for path, ir in candidates:
            ir = self._finalize_ir(ir, spec, scope, version, parent, revision_reason, tasks)
            for attempt in range(MAX_REPAIR_ATTEMPTS + 1):
                model, report = compile_model(ir, bundle, registry_status, high_stakes=scope.high_stakes)
                if report.passed:
                    break
                if attempt == MAX_REPAIR_ATTEMPTS:
                    break
                repaired = tasks.repair(ir, report) if tasks and path != "generic" else None
                if repaired is not None:
                    repair_log.append({"path": path, "attempt": attempt + 1, "action": "llm_repair", "errors": report.errors[:10]})
                    ir = self._finalize_ir(repaired, spec, scope, version, parent, revision_reason, tasks)
                    continue
                repaired = heuristic_repair(ir, report, repair_log)
                if repaired is None:
                    break
                ir = repaired
            if report.passed:
                break
            repair_log.append({"path": path, "action": "candidate_abandoned", "errors": report.errors[:10]})

        frontier = annotate_frontier(frontier, model if (model and report.passed) else self._empty_model(spec, scope, version), previous_frontier)
        limitations, questions = [], []
        for n in needs:
            if n.status in ("UNRESOLVED", "ASK_USER", "SKIPPED_PERMISSION"):
                questions.append(n.question if n.status != "UNRESOLVED" else f"Unresolved: {n.question} ({n.consequence_if_unresolved})")
        if not report.passed:
            status = "INSUFFICIENT_MODEL"
            limitations = ["No executable model could be compiled responsibly."] + report.errors[:10]
            return BuildResult(status=status, model=None, report=report, frontier=frontier, bundle=bundle, needs=needs, path=path,
                               scope=scope, binding=binding, limitations=limitations, actionable_questions=questions, repair_log=repair_log)
        for mid, why in report.non_computable_metrics.items():
            limitations.append(f"metric {mid}: {why}")
        for su in model.structural_unknowns:
            if su.impact == "high" and su.status == "DISCOVERED_UNMODELED":
                limitations.append(f"structural unknown {su.id}: {su.description}")
        removed = [r for r in repair_log if str(r.get("action", "")).startswith("removed")]
        status = "LIMITED_MODEL" if (removed or (model.metrics and len(report.non_computable_metrics) == len(model.metrics))) else "MODEL_READY"
        model.construction_tasks = [LLMTaskRecord(task=r["task"], provider=r["provider"], model=r["model"], prompt_hash=r["prompt_hash"],
                                                  output_hash=r["output_hash"], usage=r["usage"], created_at=r["created_at"]) for r in llm_records]
        if llm_records:  # task provenance does not affect validation: re-hash, keep the authoritative report
            from ..model_ir.index import compute_model_hash

            model.hash = compute_model_hash(model)
            report.model_hash = model.hash
        for r in llm_records:
            self.store.put_llm_record(self.creds[Role.MODEL_FACTORY], r)
        self.store.put_model_version(self.creds[Role.MODEL_FACTORY], model, report.model_dump(mode="json"),
                                     frontier.model_dump(mode="json"), bundle.hash)
        diff = model_diff(parent, model) if parent else None
        return BuildResult(status=status, model=model, report=report, frontier=frontier, bundle=bundle, needs=needs, path=path,
                           scope=scope, binding=binding, limitations=limitations, actionable_questions=questions,
                           repair_log=repair_log, diff=diff)

    def _finalize_ir(self, ir: dict[str, Any], spec: DecisionSpec, scope: DecisionScope, version: int,
                     parent: DecisionWorldModel | None, reason: str | None, tasks: LLMModelTasks | None) -> dict[str, Any]:
        ir = copy.deepcopy(ir)
        ir["model_id"] = f"wm_{spec.decision_id}"
        ir["decision_id"] = spec.decision_id
        ir["model_version"] = version
        ir["parent_version"] = parent.model_version if parent else None
        ir["revision_reason"] = reason
        ir.setdefault("scope", {})
        ir["scope"].update({"decision_rev": spec.revision, "horizon_start": scope.horizon_start, "horizon_end": scope.horizon_end,
                            "high_stakes": scope.high_stakes, "jurisdiction": scope.jurisdiction,
                            "domain_labels": sorted(set(ir["scope"].get("domain_labels", [])) | set(scope.domain_labels))})
        for k in ("hash", "promotion_state", "validation_report_ref", "construction_tasks", "epistemic_manifest"):
            ir.pop(k, None)
        # heuristic critic: always-on omission checks (LLM critic adds hypotheses when available)
        unknowns = ir.setdefault("structural_unknowns", [])
        omissions = ir.setdefault("omissions", [])
        if not ir.get("scenarios"):
            omissions.append({"id": "no_external_scenarios", "description": "No external-condition scenarios are represented.",
                              "reason": "critic: external variation families remain unsimulated", "risk": "medium"})
        if not any(m.get("rule", {}).get("kind") == "stochastic_draw" for m in ir.get("mechanisms", [])):
            omissions.append({"id": "no_stochastic_processes", "description": "All modeled processes are deterministic.",
                              "reason": "critic: aleatory variation not represented", "risk": "low"})
        if scope.high_stakes:
            omissions.append({"id": "high_stakes_scope", "description": "High-stakes decision: numeric outputs from unverified structure are withheld.",
                              "reason": "; ".join(scope.high_stakes_reasons), "risk": "high"})
        if tasks:
            crit = tasks.critic(ir)
            if crit:
                existing = {o.get("id") for o in omissions} | {u.get("id") for u in unknowns}
                omissions += [dict(o, reason="LLM critic hypothesis (not validation)") for o in crit.get("omissions", []) if o["id"] not in existing]
                unknowns += [dict(u, status="DISCOVERED_UNMODELED") for u in crit.get("structural_unknowns", []) if u["id"] not in existing]
        seen, dedup = set(), []
        for o in omissions:
            if o["id"] not in seen:
                seen.add(o["id"])
                dedup.append(o)
        ir["omissions"] = dedup
        return ir

    @staticmethod
    def _empty_model(spec: DecisionSpec, scope: DecisionScope, version: int) -> DecisionWorldModel:
        return DecisionWorldModel(model_id=f"wm_{spec.decision_id}", decision_id=spec.decision_id, model_version=version,
                                  scope={"decision_rev": spec.revision, "horizon_start": scope.horizon_start, "horizon_end": scope.horizon_end})


def model_diff(old: DecisionWorldModel | None, new: DecisionWorldModel) -> dict[str, Any]:
    if old is None:
        return {}
    om, nm = {m.id: m for m in old.mechanisms}, {m.id: m for m in new.mechanisms}
    os_, ns = {s.ref: s for s in old.state}, {s.ref: s for s in new.state}
    changed_state = []
    for ref in sorted(set(os_) & set(ns)):
        a, b = os_[ref], ns[ref]
        if a.initial_value != b.initial_value or a.epistemic_tier != b.epistemic_tier:
            changed_state.append({"ref": ref, "before": {"value": a.initial_value, "tier": str(a.epistemic_tier), "evidence": a.evidence_refs},
                                  "after": {"value": b.initial_value, "tier": str(b.epistemic_tier), "evidence": b.evidence_refs}})
    return {
        "from_version": old.model_version, "to_version": new.model_version, "from_hash": old.hash, "to_hash": new.hash,
        "mechanisms_added": sorted(set(nm) - set(om)), "mechanisms_removed": sorted(set(om) - set(nm)),
        "state_added": sorted(set(ns) - set(os_)), "state_changed": changed_state,
        "metrics_added": sorted({m.id for m in new.metrics if m.computable} - {m.id for m in old.metrics if m.computable}),
        "banner": "Model assumptions changed; results are not paired under identical causal structure.",
    }

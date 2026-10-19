"""Binding data-driven model packages to a decision's evidence (§6.3 model packages; §17.3).

Package templates are declarative IR with slots:

* ``{"$claim": "subject.predicate", ...}`` — value from the EvidenceBundle. Provenance and tier are
  set from the claim (verified external → A_GROUNDED; user/unverified → B; missing, conflicted or
  contextual-lead-only → UNKNOWN). ``stale_policy`` = ``use_with_downgrade`` | ``unknown``.
* ``{"$fact": "ref"}`` — a user-asserted DecisionSpec fact (USER_ASSERTED, tier B).
* ``{"$decision": "horizon.starts_at"}`` — a DecisionSpec field.
* ``"$requires": "subject.predicate"`` on any list element — the element (e.g. a mechanism) is
  included only when usable evidence exists; otherwise it is reported as pending evidence.

Each ``$claim`` slot also implies a ResearchNeed, so research is driven by what the model needs.
"""

from __future__ import annotations

import copy
from typing import Any

from ..model_ir.enums import ClaimStatus, FidelityTier, OriginKind, ResearchNeedKind
from ..model_ir.schemas import DecisionSpec, EvidenceBundle
from ..research.planner import claim_value_status, is_grounding

SLOT_KEYS = ("$claim", "$fact", "$decision")


def _is_slot(v: Any) -> bool:
    return isinstance(v, dict) and any(k in v for k in SLOT_KEYS)


def collect_slots(template: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(x):
        if isinstance(x, dict):
            if _is_slot(x):
                out.append(x)
                return
            if "$requires" in x:
                out.append({"$claim": x["$requires"], "kind": x.get("$requires_kind", "MECHANISM"),
                            "why": x.get("$requires_why", "evidence needed before this mechanism may be included"),
                            "requires": True})
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(template)
    return out


def slot_research_needs(template: dict[str, Any]) -> list[dict[str, Any]]:
    needs = []
    for s in collect_slots(template):
        if "$claim" not in s:
            continue
        subj, _, pred = s["$claim"].rpartition(".")
        needs.append({
            "kind": s.get("kind", ResearchNeedKind.INITIAL_CONDITION.value),
            "question": s.get("question") or f"What is the current {pred.replace('_', ' ')} for {subj}?",
            "target_refs": [s["$claim"]] + s.get("topics", []),
            "why": s.get("why", "grounds a state variable of the decision model"),
            "critical": bool(s.get("critical")),
            "affects_metrics": s.get("metrics", []),
            "freshness": s.get("freshness"),
            "est_cost": s.get("est_cost", 1.0),
            "candidate_sources": s.get("sources", []),
        })
    return needs


def _get_path(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        cur = cur[int(part)] if isinstance(cur, list) else (cur.get(part) if isinstance(cur, dict) else getattr(cur, part))
    return cur


class BindingReport(dict):
    pass


def bind_template(template: dict[str, Any], spec: DecisionSpec, bundle: EvidenceBundle) -> tuple[dict[str, Any], dict[str, Any]]:
    tpl = copy.deepcopy(template)
    report: dict[str, Any] = {"slots": [], "excluded_pending_evidence": [], "stale": [], "unknown": []}
    facts = {f.ref: f for f in spec.initial_facts}
    spec_json = spec.model_dump(mode="json")

    def resolve(slot: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        if "$fact" in slot:
            f = facts.get(slot["$fact"])
            if f is None:
                report["unknown"].append(slot["$fact"])
                return slot.get("default", "UNKNOWN"), {"status": "MISSING_USER_FACT", "origin": OriginKind.MODEL_HYPOTHESIS,
                                                        "tier": FidelityTier.X_LIMITED_OR_ABSTAIN, "refs": []}
            return f.value, {"status": "USER_ASSERTED", "origin": f.origin, "tier": FidelityTier.B_ASSUMPTION_BOUND,
                             "refs": [f.evidence_ref or f"user_claim_{f.ref}"]}
        if "$decision" in slot:
            return _get_path(spec_json, slot["$decision"]), {"status": "DECISION_SPEC", "origin": OriginKind.USER_ASSERTED,
                                                             "tier": FidelityTier.B_ASSUMPTION_BOUND, "refs": []}
        subj, _, pred = slot["$claim"].rpartition(".")
        claim, status = claim_value_status(bundle, subj, pred)
        default = slot.get("default", "UNKNOWN")
        if claim is None:
            report["unknown"].append({"slot": slot["$claim"], "reason": status})
            return default, {"status": status, "origin": OriginKind.MODEL_HYPOTHESIS, "tier": FidelityTier.C_ILLUSTRATIVE, "refs": []}
        if status == "STALE":
            report["stale"].append({"slot": slot["$claim"], "claim": claim.claim_id, "expired": claim.expires_at})
            if slot.get("stale_policy", "use_with_downgrade") == "unknown":
                return default, {"status": "STALE_TREATED_UNKNOWN", "origin": claim.origin_kind,
                                 "tier": FidelityTier.C_ILLUSTRATIVE, "refs": [claim.claim_id]}
            return claim.object, {"status": "STALE", "origin": claim.origin_kind, "tier": FidelityTier.B_ASSUMPTION_BOUND,
                                  "refs": [claim.claim_id]}
        if claim.object == "UNKNOWN":
            report["unknown"].append({"slot": slot["$claim"], "reason": "source reports UNKNOWN", "claim": claim.claim_id})
            return "UNKNOWN", {"status": "REPORTED_UNKNOWN", "origin": claim.origin_kind, "tier": FidelityTier.C_ILLUSTRATIVE,
                               "refs": [claim.claim_id]}
        tier = FidelityTier.A_GROUNDED if is_grounding(claim) else FidelityTier.B_ASSUMPTION_BOUND
        if claim.unit and slot.get("unit") and claim.unit != slot["unit"] and isinstance(claim.object, (int, float)):
            from ..model_ir.units import convert

            return convert(float(claim.object), claim.unit, slot["unit"]), {
                "status": str(claim.status), "origin": claim.origin_kind, "tier": tier, "refs": [claim.claim_id]}
        if slot.get("max_tier") and [FidelityTier.A_GROUNDED, FidelityTier.B_ASSUMPTION_BOUND, FidelityTier.C_ILLUSTRATIVE].index(
                FidelityTier(slot["max_tier"])) > [FidelityTier.A_GROUNDED, FidelityTier.B_ASSUMPTION_BOUND, FidelityTier.C_ILLUSTRATIVE].index(tier):
            tier = FidelityTier(slot["max_tier"])
        return claim.object, {"status": str(claim.status), "origin": claim.origin_kind, "tier": tier, "refs": [claim.claim_id]}

    def usable(ref: str) -> bool:
        subj, _, pred = ref.rpartition(".")
        c, status = claim_value_status(bundle, subj, pred)
        return c is not None and status in (ClaimStatus.VERIFIED.value, ClaimStatus.UNVERIFIED_EXTRACT.value) and c.object != "UNKNOWN"

    def walk(x: Any, owner: dict[str, Any] | None = None, key: str | None = None) -> Any:
        if isinstance(x, list):
            out = []
            for item in x:
                if isinstance(item, str) and item.startswith("$claim_id:"):
                    subj, _, pred = item.split(":", 1)[1].rpartition(".")
                    c, st = claim_value_status(bundle, subj, pred)
                    if c is not None and st not in ("STALE",):
                        out.append(c.claim_id)
                    else:
                        report["unknown"].append({"citation": item, "reason": st})
                    continue
                if isinstance(item, dict) and "$unless" in item:
                    if usable(item["$unless"]):
                        report.setdefault("superseded_by_evidence", []).append({"element": item.get("id") or item.get("ref"), "evidence": item["$unless"]})
                        continue
                    item = {k: v for k, v in item.items() if k != "$unless"}
                if isinstance(item, dict) and "$requires" in item:
                    if not usable(item["$requires"]):
                        report["excluded_pending_evidence"].append({
                            "element": item.get("id") or item.get("ref"), "requires": item["$requires"],
                            "why": item.get("$requires_why", "")})
                        continue
                    item = {k: v for k, v in item.items() if not k.startswith("$requires")}
                out.append(walk(item))
            return out
        if isinstance(x, dict):
            if _is_slot(x):
                if owner is not None and owner.get("type") == "quantity" and owner.get("unit") and "unit" not in x:
                    x = {**x, "unit": owner["unit"]}
                value, prov = resolve(x)
                if owner is not None and key in ("initial_value", "value"):
                    owner.setdefault("_prov", []).append(prov)
                report["slots"].append({"slot": x.get("$claim") or x.get("$fact") or x.get("$decision"),
                                        "value": value, "status": prov["status"], "tier": str(prov["tier"]), "refs": prov["refs"]})
                return value
            out = {}
            for k, v in list(x.items()):
                out[k] = walk(v, x, k)
            if "_prov" in x:
                provs = x.pop("_prov")
                p = provs[0]
                out.pop("_prov", None)
                out["origin"] = str(p["origin"])  # slot provenance is authoritative
                out["evidence_refs"] = sorted(set(out.get("evidence_refs", [])) | {r for pr in provs for r in pr["refs"]})
                declared = out.get("epistemic_tier")
                tier = p["tier"]
                if declared and [str(t) for t in FidelityTier].index(declared) > [str(t) for t in FidelityTier].index(str(tier)):
                    tier = declared
                out["epistemic_tier"] = str(tier)
                if out.get("initial_value", out.get("value")) == "UNKNOWN" and "type" in out and out.get("type") != "ledger":
                    if "ref" in out:
                        out["allow_unknown"] = True
            return out
        return x

    bound = walk(tpl)
    # remove binder-only keys that are not part of the IR
    for k in ("breadth_hints", "research_needs", "package"):
        bound.pop(k, None)
    return bound, report

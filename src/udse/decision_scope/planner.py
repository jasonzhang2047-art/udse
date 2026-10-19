"""Decision Scope Planner (§1.2, §2.1): choices, horizon resolution, stakeholder authority and risk
class. It may *suggest* alternatives, always labelled as system-suggested."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ..common import parse_dt
from ..model_ir.schemas import Alternative, DecisionSpec

HIGH_STAKES_PATTERNS = {
    "medical": r"\b(medical|medication|surgery|treatment|diagnos\w*|chemo\w*|therapy|dose|symptom)\b",
    "legal": r"\b(lawsuit|sue|legal strategy|custody|immigration|plea|contract dispute)\b",
    "major_financial": r"\b(mortgage|retirement savings|life savings|bankrupt\w*|leverage|margin loan|crypto\w*|stock trade)\b",
    "safety": r"\b(weapon|self-harm|unsafe|evacuat\w*)\b",
}

DOMAIN_HINTS = {
    "dining": r"\b(restaurant|dinner|lunch|eat|menu|reservation)\b",
    "startup": r"\b(startup|founder\w*|seed round|fundrais\w*|investor\w*|runway)\b",
    "funding": r"\b(raise|fundrais\w*|capital|investor\w*|round)\b",
    "group_choice": r"\b(we|our|friends|team|group|together)\b",
    "home": r"\b(garden|irrigation|apartment|house|renovat\w*)\b",
}


class DecisionScope(BaseModel):
    decision_id: str
    decision_rev: int
    question: str
    horizon_start: str
    horizon_end: str
    time_resolution: str
    domain_labels: list[str]
    high_stakes: bool
    high_stakes_reasons: list[str] = Field(default_factory=list)
    jurisdiction: str | None = None
    alternatives: list[Alternative]
    suggested_alternatives: list[str] = Field(default_factory=list)
    participants: list[str]
    metrics: list[str]
    notes: list[str] = Field(default_factory=list)


def resolution_for(start: str, end: str) -> str:
    secs = (parse_dt(end) - parse_dt(start)).total_seconds()
    if secs <= 2 * 86400:
        return "minute"
    if secs <= 90 * 86400:
        return "day"
    return "month"


def plan_scope(spec: DecisionSpec) -> DecisionScope:
    q = spec.question.lower()
    reasons = []
    if spec.risk_class == "high_stakes":
        reasons.append("declared high-stakes by the decision owner")
    elif spec.risk_class is None:
        for label, pat in HIGH_STAKES_PATTERNS.items():
            if re.search(pat, q):
                reasons.append(f"question mentions {label} matters")
    labels = list(spec.domain_labels)
    for label, pat in DOMAIN_HINTS.items():
        if label not in labels and re.search(pat, q):
            labels.append(label)
    alts = list(spec.alternatives)
    suggested = []
    if not any(any(w in f"{a.id} {a.policy}".lower() for w in ("defer", "gather", "wait")) for a in alts):
        alts.append(Alternative(id="defer", policy="defer", label="Defer / gather more evidence", suggested_by_system=True,
                                description="System-suggested: deferring or gathering evidence is a legitimate alternative (§1.2)."))
        suggested.append("defer")
    participants = spec.participant_refs or [p.agent_id for p in spec.participants]
    notes = []
    if not spec.alternatives:
        notes.append("No explicit alternatives: this is a scenario-exploration prompt, not a controlled policy comparison.")
    return DecisionScope(
        decision_id=spec.decision_id, decision_rev=spec.revision, question=spec.question,
        horizon_start=spec.horizon.starts_at, horizon_end=spec.horizon.ends_at,
        time_resolution=resolution_for(spec.horizon.starts_at, spec.horizon.ends_at), domain_labels=labels,
        high_stakes=bool(reasons), high_stakes_reasons=reasons, jurisdiction=spec.jurisdiction, alternatives=alts,
        suggested_alternatives=suggested, participants=participants, metrics=list(spec.metrics), notes=notes,
    )

"""§12 human evidence, signal ladder and profile updates (a store separate from Layers A/B/C)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests.conftest import build
from udse.common import PermissionDenied, ProvenanceRejected, new_id
from udse.model_ir.enums import FeatureStatus, SignalType
from udse.model_ir.schemas import ConsentFlags, HumanEvidenceEvent


def _ev(user, decision, signal, payload=None, consent=None):
    return HumanEvidenceEvent(event_id=new_id("hev"), user_id=user, decision_id=decision, decision_revision=1, signal_type=signal,
                              payload=payload or {}, consent=consent or ConsentFlags(), created_at=datetime.now(timezone.utc).isoformat())


def test_review_click_without_choice_never_updates_direction(engine):
    """§15.3 Evidence is not preference: inspecting a cash timeline raises relevance only."""
    spec, _ = build(engine, "fundraising")
    # implicit signals are not even stored without opt-in
    assert engine.human.ingest(_ev("user_fa", spec.decision_id, SignalType.DETAILED_INSPECTION, {"dimensions": ["runway_months_after"]}),
                               authenticated_user="user_fa") is None
    ev = engine.human.ingest(_ev("user_fa", spec.decision_id, SignalType.DETAILED_INSPECTION, {"dimensions": ["runway_months_after"]},
                                 ConsentFlags(implicit_learning=True)), authenticated_user="user_fa")
    prop = engine.human.propose(ev, "profile_founder_a")
    assert prop.mode == "shadow" and prop.status == "SHADOW_ONLY"
    assert all(c.change_kind == "relevance" for c in prop.changes)
    ch = prop.changes[0]
    assert ch.after.value == ch.before.value  # no polarity asserted
    with pytest.raises(ValueError, match="shadow"):
        engine.human.decide(prop.proposal_id, "user_fa", True)


def test_explicit_hard_correction_requires_confirmation_then_versions(engine):
    spec, _ = build(engine, "fundraising")
    ev = engine.human.ingest(_ev("user_fb", spec.decision_id, SignalType.EXPLICIT_CORRECTION, {
        "statement": "I will not accept fundraising terms that remove founder control",
        "feature_key": "constraint.founder_control_retained", "value": {"op": "==", "value": True}, "hard": True}),
        authenticated_user="user_fb")
    prop = engine.human.propose(ev, "profile_founder_b")
    assert prop.mode == "visible" and prop.requires_user_confirmation and prop.changes[0].change_kind == "constraint_candidate"
    before = engine.store.get_profile("profile_founder_b")
    with pytest.raises(PermissionDenied):
        engine.human.decide(prop.proposal_id, "user_fa", True)  # another founder cannot approve
    new = engine.human.decide(prop.proposal_id, "user_fb", True)
    feat = next(f for f in new.features if f.key == "constraint.founder_control_retained")
    assert new.version == before.version + 1 and feat.status == FeatureStatus.HARD_CONSTRAINT and ev.event_id in feat.evidence_refs
    rolled = engine.human.rollback("profile_founder_b", before.version, "user_fb")
    assert rolled.version == new.version + 1 and not any(f.key == "constraint.founder_control_retained" for f in rolled.features)
    assert len(engine.store.list_profile_versions("profile_founder_b")) == 3  # audit trail preserved


def test_agent_behaviour_is_never_human_evidence(engine):
    spec, _ = build(engine, "fundraising")
    with pytest.raises(ProvenanceRejected):
        engine.human.ingest(_ev("user_fa", spec.decision_id, SignalType.EXPLICIT_SELECTION,
                                {"source": "simulated_agent", "actor_id": "founder_a", "choice": "accept"}), authenticated_user="user_fa")
    with pytest.raises(PermissionDenied):
        engine.human.ingest(_ev("user_fb", spec.decision_id, SignalType.EXPLICIT_SELECTION), authenticated_user="user_fa")


def test_profiles_are_private_and_only_owners_evidence_can_change_them(engine):
    spec, _ = build(engine, "fundraising")
    ev = engine.human.ingest(_ev("user_fa", spec.decision_id, SignalType.EXPLICIT_SELECTION, {"feature_hints": {"preference.runway_months_after": {"weight": 0.5, "unit": "month"}}}),
                             authenticated_user="user_fa")
    with pytest.raises(PermissionDenied):
        engine.human.propose(ev, "profile_founder_b")

"""§3.3 [GATE] poisoning test and Appendix C adversarial-write fixtures: Layer C can never reach
Layer A, Layer B or the real profile store — through any credential, relabelling or rewrite."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from udse.api.app import attempt_all_promotions
from udse.common import PermissionDenied, ProvenanceRejected, new_id
from udse.llm.provider import LLMCall, ScriptedProvider
from udse.model_ir.enums import ClaimStatus, Role, SignalType
from udse.model_ir.schemas import EvidenceClaim, ExogenousEvent, HumanEvidenceEvent, ScenarioSpec, SetEffect
from udse.research.adapters import UserAttachmentAdapter
from udse.research.normalizer import EvidenceNormalizer


def _poisoned_run(engine):
    """A run whose scenario injects the run-local event 'customer X signed a contract'."""
    from tests.conftest import build

    spec, res = build(engine, "fundraising")
    model = res.model
    poisoned = model.model_copy(deep=True)
    poisoned.scenarios.append(ScenarioSpec(id="poison", label="poison", exogenous_events=[
        ExogenousEvent(at="+P1M", event_type="customer_x_signed_contract", description="customer X signed a contract",
                       effects=[SetEffect(op="set", ref="pilot.traction_met", value="true")])]))
    from udse.model_validator.compiler import compile_model

    poisoned, rep = compile_model(poisoned, engine.store.latest_evidence_bundle(spec.decision_id), engine.registry.status_map())
    assert rep.passed, rep.errors
    from udse.experiments.orchestrator import ExperimentArm
    from udse.model_ir.enums import FidelityMode

    plan = engine.orchestrator.plan(spec.decision_id)
    plan.model_hash = poisoned.hash
    plan.model_ref = plan.model_ref.model_copy(update={"hash": poisoned.hash})
    engine.store.put_model_version(engine.creds[Role.MODEL_FACTORY], poisoned.model_copy(update={"model_version": 99}),
                                   rep.model_dump(mode="json"), None, None)
    r = engine.orchestrator.run_arm(plan, poisoned, ExperimentArm(arm_id="poison", label="poison", policy_id="now", scenario_ids=["poison"]),
                                    FidelityMode.SCREENING, plan.budget.screening_caps)
    ev = next(e for e in engine.store.get_events(r.run_id) if e.event_type == "customer_x_signed_contract")
    return spec, r, ev


def test_poisoning_gate_every_path_refused(engine):
    spec, r, ev = _poisoned_run(engine)
    before = engine.store.knowledge_hash()
    report = attempt_all_promotions(engine, ev, "user_fa")
    assert report["all_refused"], report["attempts"]
    assert report["layer_a_hash_unchanged"] and engine.store.knowledge_hash() == before
    denied = engine.store.audit_entries(allowed=False)
    assert len(denied) >= len(report["attempts"]), "every refusal is audited"


def test_llm_rewrite_of_simulated_claim_is_rejected(engine):
    spec, r, ev = _poisoned_run(engine)
    llm = ScriptedProvider({"summarize": [{"claim": "Customer X signed a contract (confirmed)"}]})
    res = llm.complete_json(LLMCall(task="summarize", system="", user=ev.model_dump_json(), tainted_by_simulation=True))
    engine.store.put_llm_record(engine.creds[Role.RESEARCH_PLANNER], res.record)
    with pytest.raises(ProvenanceRejected, match="simulated content"):
        engine.ingestion.ingest(EvidenceClaim(claim_id=new_id("c"), origin_kind="REAL_EXTERNAL_OBSERVATION", subject="customer_x",
                                              predicate="signed_contract", object=True, derived_from=[f"llm:{res.record['call_id']}"]))


def test_laundered_simulated_text_through_an_adapter_is_quarantined(engine):
    """Even a genuine adapter fetch of a document that repeats simulated content cannot become a fact."""
    spec, r, ev = _poisoned_run(engine)
    att = UserAttachmentAdapter([{"uri": "attachment://notes.txt", "content": "customer X signed a contract",
                                  "claims": [{"subject": "customer_x", "predicate": "signed_contract", "object": True,
                                              "origin_kind": "REAL_EXTERNAL_OBSERVATION", "record_type": "observation"}]}],
                                datetime.now(timezone.utc).isoformat())
    fr = att.fetch(att.attachments[0])
    engine.store.put_raw_artifact(engine.creds[Role.EVIDENCE_ADAPTER], fr.artifact)
    claims, _, _ = EvidenceNormalizer(datetime.now(timezone.utc), None).build_claims(fr)
    stored = engine.ingestion.ingest(claims[0])
    assert stored.status == ClaimStatus.QUARANTINED
    assert any("ALERT" in (a["reason"] or "") for a in engine.store.audit_entries(allowed=None))


def test_database_triggers_enforce_provenance_and_append_only(engine):
    from tests.conftest import build

    spec, res = build(engine, "garden")
    engine.orchestrator.execute(engine.orchestrator.plan(spec.decision_id), depth=False, sensitivity=False)
    conn = engine.store.conn
    with pytest.raises(sqlite3.DatabaseError, match="PROVENANCE"):
        conn.execute("INSERT INTO external_claims VALUES ('x','t','SIMULATION_OUTPUT','UNVERIFIED_EXTRACT','s','p','{}','w','now')")
    with pytest.raises(sqlite3.DatabaseError, match="APPEND-ONLY"):
        conn.execute("UPDATE simulation_events SET payload = '{}'")
    with pytest.raises(sqlite3.DatabaseError, match="APPEND-ONLY"):
        conn.execute("DELETE FROM simulation_events")
    with pytest.raises(sqlite3.DatabaseError, match="IMMUTABLE"):
        conn.execute("UPDATE decision_model_versions SET hash = 'sha256:tampered'")
    with pytest.raises(sqlite3.DatabaseError, match="IMMUTABLE"):
        conn.execute("UPDATE run_manifests SET model_hash = 'sha256:other'")


@pytest.mark.parametrize("role,table_call", [
    (Role.SIMULATION_WORKER, lambda s, c: s.put_claim(c, EvidenceClaim(claim_id="x", origin_kind="REAL_EXTERNAL_OBSERVATION", subject="s", predicate="p", object=1))),
    (Role.SIMULATION_WORKER, lambda s, c: s.put_curated(c, "e", "1.0.0", "fact", "A_VALIDATED", {}, [], [])),
    (Role.MODEL_FACTORY, lambda s, c: s.put_curated(c, "e", "1.0.0", "fact", "A_VALIDATED", {}, [], [])),
    (Role.RESEARCH_PLANNER, lambda s, c: s.put_claim(c, EvidenceClaim(claim_id="x", origin_kind="OFFICIAL_RULE", subject="s", predicate="p", object=1))),
    (Role.ORCHESTRATOR, lambda s, c: s.put_human_evidence(c, HumanEvidenceEvent(event_id="h", user_id="u", decision_id="d", decision_revision=1,
                                                                                signal_type=SignalType.EXPLICIT_SELECTION, created_at="2026-01-01T00:00:00+00:00"))),
])
def test_role_matrix_denies_cross_layer_writes(engine, role, table_call):
    with pytest.raises(PermissionDenied):
        table_call(engine.store, engine.creds[role])


def test_forged_credentials_are_rejected(engine):
    from udse.storage.credentials import Credential

    forged = Credential(role=Role.CURATOR, principal="svc:curator", token="0" * 64)
    with pytest.raises(PermissionDenied, match="invalid credential"):
        engine.store.put_curated(forged, "e", "1.0.0", "fact", "A_VALIDATED", {}, [], [])


def test_layer_a_promotion_requires_independent_external_evidence(engine):
    from udse.model_registry.registry import RegistryEntry

    tmpl = RegistryEntry(entry_id="local.sim_only_template", version="0.1.0", kind="structure", status="CANDIDATE",
                         title="validated only by simulations", evidence_claim_ids=[], evidence_origins=["SIMULATION_OUTPUT"],
                         tests=[{"id": "t", "status": "pass"}], applicability={"domains": ["startup"]})
    with pytest.raises(ProvenanceRejected, match="simulation outputs cannot support"):
        engine.curator.promote_to_layer_a(tmpl, "curator_x", "looks plausible")
    local = engine.curator.register_local_template(tmpl)
    assert str(local.status) == "LOCAL_HYPOTHESIS_TEMPLATE"

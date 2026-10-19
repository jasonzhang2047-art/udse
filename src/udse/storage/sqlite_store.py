"""Persistent store with role-enforced writes, append-only tables and database-level provenance
triggers (§14.6). SQLite stands in for PostgreSQL in this reference implementation; the same
logical tables, write-authority matrix and constraints map onto Postgres schemas/roles + RLS
(see docs/postgres-mapping.md).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Iterable

from ..common import PermissionDenied, ProvenanceRejected, canonical_json, sha256_hex
from ..model_ir.enums import Role
from ..model_ir.schemas import (
    DecisionSpec,
    DecisionWorldModel,
    EvidenceBundle,
    EvidenceClaim,
    HumanEvidenceEvent,
    ProfileUpdateProposal,
    RawSourceArtifact,
    RealProfileVersion,
    ResearchNeed,
    RunManifest,
    RunResult,
    WorldEvent,
)
from .credentials import TABLE_WRITERS, Credential, CredentialAuthority

SCHEMA = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS raw_source_artifacts (
  artifact_id TEXT PRIMARY KEY, tenant TEXT NOT NULL, uri TEXT NOT NULL, adapter TEXT NOT NULL,
  content_hash TEXT NOT NULL, retrieved_at TEXT NOT NULL, payload TEXT NOT NULL, writer TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS external_claims (
  claim_id TEXT PRIMARY KEY, tenant TEXT NOT NULL, origin_kind TEXT NOT NULL, status TEXT NOT NULL,
  subject TEXT, predicate TEXT, payload TEXT NOT NULL, writer TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS external_claims_no_simulation BEFORE INSERT ON external_claims
  WHEN NEW.origin_kind = 'SIMULATION_OUTPUT'
  BEGIN SELECT RAISE(ABORT, 'PROVENANCE: SIMULATION_OUTPUT cannot enter external_claims'); END;
CREATE TRIGGER IF NOT EXISTS external_claims_origin_immutable BEFORE UPDATE OF origin_kind ON external_claims
  BEGIN SELECT RAISE(ABORT, 'PROVENANCE: origin_kind is immutable'); END;

CREATE TABLE IF NOT EXISTS evidence_bundles (
  bundle_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL, decision_rev INTEGER NOT NULL, hash TEXT NOT NULL,
  payload TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS curated_knowledge_versions (
  entry_id TEXT NOT NULL, version TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
  claim_ids TEXT NOT NULL, approvals TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (entry_id, version));
CREATE TRIGGER IF NOT EXISTS curated_requires_grounded_claims BEFORE INSERT ON curated_knowledge_versions
  WHEN EXISTS (SELECT 1 FROM json_each(NEW.claim_ids) j LEFT JOIN external_claims c ON c.claim_id = j.value
               WHERE c.claim_id IS NULL OR c.origin_kind IN ('SIMULATION_OUTPUT', 'MODEL_HYPOTHESIS')
                  OR c.status IN ('QUARANTINED', 'REJECTED'))
  BEGIN SELECT RAISE(ABORT, 'PROVENANCE: curated knowledge must cite ingested, non-simulated, non-hypothesis claims'); END;
CREATE TRIGGER IF NOT EXISTS curated_payload_immutable BEFORE UPDATE OF payload, claim_ids ON curated_knowledge_versions
  BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: curated versions cannot be overwritten'); END;

CREATE TABLE IF NOT EXISTS mechanism_registry_versions (
  entry_id TEXT NOT NULL, version TEXT NOT NULL, status TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
  ir_hash TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (entry_id, version));
CREATE TRIGGER IF NOT EXISTS registry_payload_immutable BEFORE UPDATE OF payload, ir_hash ON mechanism_registry_versions
  BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: registry versions cannot be overwritten'); END;

CREATE TABLE IF NOT EXISTS decision_specs (
  decision_id TEXT NOT NULL, revision INTEGER NOT NULL, owner_id TEXT NOT NULL, payload TEXT NOT NULL,
  created_at TEXT NOT NULL, PRIMARY KEY (decision_id, revision));
CREATE TRIGGER IF NOT EXISTS decision_specs_immutable BEFORE UPDATE ON decision_specs
  BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: create a new decision revision instead'); END;

CREATE TABLE IF NOT EXISTS decision_acl (
  decision_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL, PRIMARY KEY (decision_id, user_id));

CREATE TABLE IF NOT EXISTS decision_model_versions (
  model_id TEXT NOT NULL, version INTEGER NOT NULL, decision_id TEXT NOT NULL, decision_rev INTEGER NOT NULL,
  hash TEXT NOT NULL UNIQUE, promotion_state TEXT NOT NULL, ir TEXT NOT NULL, validation_report TEXT NOT NULL,
  frontier TEXT, evidence_bundle_hash TEXT, parent_version INTEGER, created_at TEXT NOT NULL,
  PRIMARY KEY (model_id, version));
CREATE TRIGGER IF NOT EXISTS model_versions_immutable BEFORE UPDATE OF ir, hash, validation_report ON decision_model_versions
  BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: a validated model hash can never be mutated; create B@v+1'); END;

CREATE TABLE IF NOT EXISTS model_research_needs (
  need_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
  trigger_diagnostic_id TEXT, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS scenario_frontiers (
  frontier_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL, model_version INTEGER, payload TEXT NOT NULL,
  updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS experiment_plans (
  experiment_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL, model_hash TEXT NOT NULL, payload TEXT NOT NULL,
  created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS run_manifests (
  run_id TEXT PRIMARY KEY, experiment_id TEXT, decision_id TEXT NOT NULL, model_hash TEXT NOT NULL,
  payload TEXT NOT NULL, state TEXT NOT NULL, result TEXT, genesis TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS run_manifest_pinned BEFORE UPDATE OF model_hash, payload ON run_manifests
  WHEN OLD.genesis IS NOT NULL
  BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: a run cannot replace its pinned model hash or manifest'); END;

CREATE TABLE IF NOT EXISTS simulation_events (
  run_id TEXT NOT NULL, seq INTEGER NOT NULL, event_id TEXT NOT NULL UNIQUE,
  origin_kind TEXT NOT NULL CHECK (origin_kind = 'SIMULATION_OUTPUT'),
  sim_at TEXT NOT NULL, event_type TEXT NOT NULL, payload TEXT NOT NULL, pre_hash TEXT NOT NULL, post_hash TEXT NOT NULL,
  PRIMARY KEY (run_id, seq));
CREATE TRIGGER IF NOT EXISTS simulation_events_no_update BEFORE UPDATE ON simulation_events
  BEGIN SELECT RAISE(ABORT, 'APPEND-ONLY: simulation events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS simulation_events_no_delete BEFORE DELETE ON simulation_events
  BEGIN SELECT RAISE(ABORT, 'APPEND-ONLY: simulation events are immutable'); END;

CREATE TABLE IF NOT EXISTS simulation_snapshots (
  run_id TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL, sim_at TEXT, state_hash TEXT, blob TEXT NOT NULL,
  PRIMARY KEY (run_id, seq, kind));
CREATE TRIGGER IF NOT EXISTS simulation_snapshots_no_update BEFORE UPDATE ON simulation_snapshots
  BEGIN SELECT RAISE(ABORT, 'APPEND-ONLY: snapshots are immutable'); END;

CREATE TABLE IF NOT EXISTS sim_fingerprints (
  fingerprint TEXT NOT NULL, run_id TEXT NOT NULL, event_id TEXT NOT NULL, PRIMARY KEY (fingerprint, event_id));

CREATE TABLE IF NOT EXISTS human_evidence_events (
  event_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, decision_id TEXT, decision_rev INTEGER, signal_type TEXT NOT NULL,
  origin_kind TEXT NOT NULL CHECK (origin_kind = 'HUMAN_FEEDBACK'), payload TEXT NOT NULL,
  idempotency_key TEXT UNIQUE, created_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS human_evidence_no_update BEFORE UPDATE ON human_evidence_events
  BEGIN SELECT RAISE(ABORT, 'APPEND-ONLY: human evidence is immutable (delete-eligible only)'); END;

CREATE TABLE IF NOT EXISTS profile_versions (
  profile_id TEXT NOT NULL, version INTEGER NOT NULL, user_id TEXT NOT NULL, payload TEXT NOT NULL,
  evidence_ids TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (profile_id, version));
CREATE TRIGGER IF NOT EXISTS profile_requires_human_evidence BEFORE INSERT ON profile_versions
  WHEN EXISTS (SELECT 1 FROM json_each(NEW.evidence_ids) j LEFT JOIN human_evidence_events h ON h.event_id = j.value
               WHERE h.event_id IS NULL OR h.user_id != NEW.user_id)
  BEGIN SELECT RAISE(ABORT, 'PROVENANCE: profile versions may cite only the owner''s authenticated human evidence'); END;
CREATE TRIGGER IF NOT EXISTS profile_versions_immutable BEFORE UPDATE ON profile_versions
  BEGIN SELECT RAISE(ABORT, 'IMMUTABLE: create a new profile version (rollback = new version)'); END;

CREATE TABLE IF NOT EXISTS profile_update_proposals (
  proposal_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, principal TEXT NOT NULL, role TEXT NOT NULL,
  operation TEXT NOT NULL, target TEXT NOT NULL, allowed INTEGER NOT NULL, reason TEXT);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
  BEGIN SELECT RAISE(ABORT, 'APPEND-ONLY: audit log'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
  BEGIN SELECT RAISE(ABORT, 'APPEND-ONLY: audit log'); END;

CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY, kind TEXT NOT NULL, idempotency_key TEXT UNIQUE, status TEXT NOT NULL, request TEXT,
  result TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS llm_call_records (
  call_id TEXT PRIMARY KEY, task TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, prompt_hash TEXT NOT NULL,
  output_hash TEXT NOT NULL, payload TEXT NOT NULL, tainted_by_simulation INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS users (
  user_id TEXT PRIMARY KEY, display_name TEXT, token_hash TEXT NOT NULL UNIQUE);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(obj: Any) -> str:
    if hasattr(obj, "model_dump"):
        return canonical_json(obj.model_dump(mode="json", by_alias=True))
    return canonical_json(obj)


STOPWORDS = {"a", "an", "the", "has", "have", "had", "of", "to", "is", "was", "be", "been", "and", "with", "by",
             "for", "on", "true", "false"}


def content_tokens(text: str) -> str:
    """Order-insensitive token bag, so reworded variants of the same simulated content collide."""
    import re

    toks = sorted({t for t in re.split(r"[^a-z0-9]+", str(text).lower()) if t and t not in STOPWORDS})
    return " ".join(toks)


def sim_fingerprint(text: str) -> str:
    return sha256_hex(content_tokens(text))[:32]


class Store:
    def __init__(self, path: str = ":memory:", authority: CredentialAuthority | None = None):
        self.path = path
        self.authority = authority or CredentialAuthority()
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._auditor = self.authority.issue(Role.AUDITOR, "svc:auditor")

    # ------------------------------------------------------------------
    # authorization + audit
    # ------------------------------------------------------------------
    def audit(self, cred: Credential | None, operation: str, target: str, allowed: bool, reason: str = "") -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO audit_log(at, principal, role, operation, target, allowed, reason) VALUES (?,?,?,?,?,?,?)",
                (_now(), getattr(cred, "principal", "anonymous"), str(getattr(cred, "role", "none")), operation, target,
                 1 if allowed else 0, reason),
            )

    def authorize(self, cred: Credential, table: str, operation: str, target: str = "") -> None:
        if not self.authority.verify(cred):
            self.audit(cred, operation, f"{table}:{target}", False, "invalid credential")
            raise PermissionDenied(f"invalid credential for {operation} on {table}")
        if cred.role not in TABLE_WRITERS.get(table, frozenset()):
            self.audit(cred, operation, f"{table}:{target}", False, f"role {cred.role} may not write {table}")
            raise PermissionDenied(f"role {cred.role} may not write {table}")

    def _exec(self, cred: Credential, table: str, operation: str, target: str, sql: str, params: Iterable[Any]) -> None:
        self.authorize(cred, table, operation, target)
        with self._lock:
            try:
                self.conn.execute("BEGIN")
                self.conn.execute(sql, tuple(params))
                self.conn.execute("COMMIT")
            except sqlite3.DatabaseError as e:
                self.conn.execute("ROLLBACK")
                msg = str(e)
                self.audit(cred, operation, f"{table}:{target}", False, msg)
                if "PROVENANCE" in msg or "CHECK constraint" in msg:
                    raise ProvenanceRejected(msg) from e
                if "IMMUTABLE" in msg or "APPEND-ONLY" in msg:
                    raise PermissionDenied(msg) from e
                raise
        self.audit(cred, operation, f"{table}:{target}", True)

    def _one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchone()

    def _all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchall()

    def audit_entries(self, allowed: bool | None = None, limit: int = 200) -> list[dict[str, Any]]:
        q = "SELECT * FROM audit_log"
        p: list[Any] = []
        if allowed is not None:
            q += " WHERE allowed = ?"
            p.append(1 if allowed else 0)
        q += " ORDER BY id DESC LIMIT ?"
        p.append(limit)
        return [dict(r) for r in self._all(q, p)]

    # ------------------------------------------------------------------
    # Layer A staging: raw artifacts + claims
    # ------------------------------------------------------------------
    def put_raw_artifact(self, cred: Credential, a: RawSourceArtifact) -> None:
        self._exec(cred, "raw_source_artifacts", "insert", a.artifact_id,
                   "INSERT OR IGNORE INTO raw_source_artifacts VALUES (?,?,?,?,?,?,?,?)",
                   (a.artifact_id, a.tenant, a.uri, a.adapter, a.content_hash, a.retrieved_at, _dump(a), cred.principal))

    def get_raw_artifact(self, artifact_id: str) -> RawSourceArtifact | None:
        r = self._one("SELECT payload FROM raw_source_artifacts WHERE artifact_id = ?", (artifact_id,))
        return RawSourceArtifact.model_validate_json(r["payload"]) if r else None

    def find_artifact_by_hash(self, content_hash: str) -> RawSourceArtifact | None:
        r = self._one("SELECT payload FROM raw_source_artifacts WHERE content_hash = ?", (content_hash,))
        return RawSourceArtifact.model_validate_json(r["payload"]) if r else None

    def put_claim(self, cred: Credential, c: EvidenceClaim, tenant: str = "default") -> None:
        self._exec(cred, "external_claims", "insert", c.claim_id,
                   "INSERT OR REPLACE INTO external_claims VALUES (?,?,?,?,?,?,?,?,?)",
                   (c.claim_id, tenant, str(c.origin_kind), str(c.status), c.subject, c.predicate, _dump(c), cred.principal, _now()))

    def get_claim(self, claim_id: str) -> EvidenceClaim | None:
        r = self._one("SELECT payload FROM external_claims WHERE claim_id = ?", (claim_id,))
        return EvidenceClaim.model_validate_json(r["payload"]) if r else None

    def list_claims(self, subject: str | None = None) -> list[EvidenceClaim]:
        if subject:
            rows = self._all("SELECT payload FROM external_claims WHERE subject = ? ORDER BY claim_id", (subject,))
        else:
            rows = self._all("SELECT payload FROM external_claims ORDER BY claim_id")
        return [EvidenceClaim.model_validate_json(r["payload"]) for r in rows]

    def knowledge_hash(self) -> str:
        """Hash of all Layer A content (claims + curated + registry) — used to prove adversarial
        writes left shared knowledge unchanged."""
        rows = self._all("SELECT 'c', claim_id, payload FROM external_claims UNION ALL "
                         "SELECT 'k', entry_id || '@' || version, payload FROM curated_knowledge_versions UNION ALL "
                         "SELECT 'r', entry_id || '@' || version, payload || status FROM mechanism_registry_versions ORDER BY 1, 2")
        return "sha256:" + sha256_hex("\n".join("|".join(map(str, tuple(r))) for r in rows))

    def put_evidence_bundle(self, cred: Credential, b: EvidenceBundle) -> None:
        self._exec(cred, "evidence_bundles", "insert", b.bundle_id, "INSERT OR IGNORE INTO evidence_bundles VALUES (?,?,?,?,?,?)",
                   (b.bundle_id, b.decision_id, b.decision_rev, b.hash or "", _dump(b), _now()))

    def get_evidence_bundle(self, bundle_id: str) -> EvidenceBundle | None:
        r = self._one("SELECT payload FROM evidence_bundles WHERE bundle_id = ?", (bundle_id,))
        return EvidenceBundle.model_validate_json(r["payload"]) if r else None

    def latest_evidence_bundle(self, decision_id: str) -> EvidenceBundle | None:
        r = self._one("SELECT payload FROM evidence_bundles WHERE decision_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", (decision_id,))
        return EvidenceBundle.model_validate_json(r["payload"]) if r else None

    # ------------------------------------------------------------------
    # Layer A curated knowledge + mechanism registry
    # ------------------------------------------------------------------
    def put_curated(self, cred: Credential, entry_id: str, version: str, kind: str, status: str, payload: dict[str, Any],
                    claim_ids: list[str], approvals: list[dict[str, Any]]) -> None:
        self._exec(cred, "curated_knowledge_versions", "promote", f"{entry_id}@{version}",
                   "INSERT INTO curated_knowledge_versions VALUES (?,?,?,?,?,?,?,?)",
                   (entry_id, version, kind, status, canonical_json(payload), json.dumps(claim_ids), canonical_json(approvals), _now()))

    def put_registry_entry(self, cred: Credential, entry_id: str, version: str, status: str, kind: str,
                           payload: dict[str, Any], ir_hash: str) -> None:
        self._exec(cred, "mechanism_registry_versions", "insert", f"{entry_id}@{version}",
                   "INSERT INTO mechanism_registry_versions VALUES (?,?,?,?,?,?,?)",
                   (entry_id, version, status, kind, canonical_json(payload), ir_hash, _now()))

    def set_registry_status(self, cred: Credential, entry_id: str, version: str, status: str) -> None:
        self._exec(cred, "mechanism_registry_versions", "status", f"{entry_id}@{version}",
                   "UPDATE mechanism_registry_versions SET status = ? WHERE entry_id = ? AND version = ?", (status, entry_id, version))

    def list_registry(self, kind: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM mechanism_registry_versions"
        rows = self._all(q + (" WHERE kind = ?" if kind else "") + " ORDER BY entry_id, version", (kind,) if kind else ())
        return [{**json.loads(r["payload"]), "_status": r["status"], "_ir_hash": r["ir_hash"]} for r in rows]

    # ------------------------------------------------------------------
    # decisions + ACL
    # ------------------------------------------------------------------
    def put_decision_spec(self, cred: Credential, spec: DecisionSpec) -> None:
        self._exec(cred, "decision_specs", "insert", f"{spec.decision_id}@{spec.revision}",
                   "INSERT INTO decision_specs VALUES (?,?,?,?,?)",
                   (spec.decision_id, spec.revision, spec.owner_id, _dump(spec), _now()))

    def get_decision_spec(self, decision_id: str, revision: int | None = None) -> DecisionSpec | None:
        if revision is None:
            r = self._one("SELECT payload FROM decision_specs WHERE decision_id = ? ORDER BY revision DESC LIMIT 1", (decision_id,))
        else:
            r = self._one("SELECT payload FROM decision_specs WHERE decision_id = ? AND revision = ?", (decision_id, revision))
        return DecisionSpec.model_validate_json(r["payload"]) if r else None

    def list_decisions(self, user_id: str) -> list[DecisionSpec]:
        rows = self._all(
            "SELECT d.payload FROM decision_specs d JOIN decision_acl a ON a.decision_id = d.decision_id "
            "WHERE a.user_id = ? AND d.revision = (SELECT MAX(revision) FROM decision_specs x WHERE x.decision_id = d.decision_id) "
            "ORDER BY d.created_at DESC", (user_id,))
        return [DecisionSpec.model_validate_json(r["payload"]) for r in rows]

    def grant(self, cred: Credential, decision_id: str, user_id: str, role: str) -> None:
        self._exec(cred, "decision_acl", "grant", f"{decision_id}:{user_id}", "INSERT OR REPLACE INTO decision_acl VALUES (?,?,?)",
                   (decision_id, user_id, role))

    def decision_role(self, decision_id: str, user_id: str) -> str | None:
        r = self._one("SELECT role FROM decision_acl WHERE decision_id = ? AND user_id = ?", (decision_id, user_id))
        return r["role"] if r else None

    # ------------------------------------------------------------------
    # Layer B models
    # ------------------------------------------------------------------
    def put_model_version(self, cred: Credential, model: DecisionWorldModel, report: dict[str, Any],
                          frontier: dict[str, Any] | None, bundle_hash: str | None) -> None:
        self._exec(cred, "decision_model_versions", "insert", f"{model.model_id}@{model.model_version}",
                   "INSERT INTO decision_model_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (model.model_id, model.model_version, model.decision_id, model.scope.decision_rev, model.hash,
                    str(model.promotion_state), _dump(model), canonical_json(report), canonical_json(frontier) if frontier else None,
                    bundle_hash, model.parent_version, _now()))

    def set_model_promotion(self, cred: Credential, model_id: str, version: int, state: str) -> None:
        self._exec(cred, "decision_model_versions", "promotion", f"{model_id}@{version}",
                   "UPDATE decision_model_versions SET promotion_state = ? WHERE model_id = ? AND version = ?",
                   (state, model_id, version))

    def _model_row(self, r: sqlite3.Row | None) -> dict[str, Any] | None:
        if not r:
            return None
        model = DecisionWorldModel.model_validate_json(r["ir"])
        model.promotion_state = r["promotion_state"]
        return {"model": model, "report": json.loads(r["validation_report"]),
                "frontier": json.loads(r["frontier"]) if r["frontier"] else None,
                "evidence_bundle_hash": r["evidence_bundle_hash"], "created_at": r["created_at"]}

    def get_model(self, model_id: str, version: int | None = None) -> dict[str, Any] | None:
        if version is None:
            r = self._one("SELECT * FROM decision_model_versions WHERE model_id = ? ORDER BY version DESC LIMIT 1", (model_id,))
        else:
            r = self._one("SELECT * FROM decision_model_versions WHERE model_id = ? AND version = ?", (model_id, version))
        return self._model_row(r)

    def get_model_by_hash(self, h: str) -> dict[str, Any] | None:
        return self._model_row(self._one("SELECT * FROM decision_model_versions WHERE hash = ?", (h,)))

    def list_model_versions(self, model_id: str | None = None, decision_id: str | None = None) -> list[dict[str, Any]]:
        if model_id:
            rows = self._all("SELECT * FROM decision_model_versions WHERE model_id = ? ORDER BY version", (model_id,))
        else:
            rows = self._all("SELECT * FROM decision_model_versions WHERE decision_id = ? ORDER BY version", (decision_id,))
        return [self._model_row(r) for r in rows]

    # ------------------------------------------------------------------
    # research needs, frontiers, experiments
    # ------------------------------------------------------------------
    def put_research_need(self, cred: Credential, decision_id: str, need: ResearchNeed) -> None:
        self._exec(cred, "model_research_needs", "upsert", need.need_id, "INSERT OR REPLACE INTO model_research_needs VALUES (?,?,?,?,?,?)",
                   (need.need_id, decision_id, need.status, _dump(need), need.trigger_diagnostic_id, _now()))

    def list_research_needs(self, decision_id: str) -> list[ResearchNeed]:
        rows = self._all("SELECT payload FROM model_research_needs WHERE decision_id = ? ORDER BY created_at, need_id", (decision_id,))
        return [ResearchNeed.model_validate_json(r["payload"]) for r in rows]

    def put_frontier(self, cred: Credential, frontier: dict[str, Any]) -> None:
        self._exec(cred, "scenario_frontiers", "upsert", frontier["frontier_id"],
                   "INSERT OR REPLACE INTO scenario_frontiers VALUES (?,?,?,?,?)",
                   (frontier["frontier_id"], frontier["decision_id"], frontier.get("model_version"), canonical_json(frontier), _now()))

    def get_frontier(self, decision_id: str) -> dict[str, Any] | None:
        r = self._one("SELECT payload FROM scenario_frontiers WHERE decision_id = ? ORDER BY updated_at DESC LIMIT 1", (decision_id,))
        return json.loads(r["payload"]) if r else None

    def put_experiment(self, cred: Credential, plan: dict[str, Any]) -> None:
        self._exec(cred, "experiment_plans", "insert", plan["experiment_id"], "INSERT OR REPLACE INTO experiment_plans VALUES (?,?,?,?,?)",
                   (plan["experiment_id"], plan["decision_id"], plan["model_hash"], canonical_json(plan), _now()))

    def get_experiment(self, experiment_id: str) -> dict[str, Any] | None:
        r = self._one("SELECT payload FROM experiment_plans WHERE experiment_id = ?", (experiment_id,))
        return json.loads(r["payload"]) if r else None

    def list_experiments(self, decision_id: str) -> list[dict[str, Any]]:
        rows = self._all("SELECT payload FROM experiment_plans WHERE decision_id = ? ORDER BY created_at", (decision_id,))
        return [json.loads(r["payload"]) for r in rows]

    # ------------------------------------------------------------------
    # runs (Layer C)
    # ------------------------------------------------------------------
    def put_run_manifest(self, cred: Credential, m: RunManifest) -> None:
        self._exec(cred, "run_manifests", "insert", m.run_id, "INSERT INTO run_manifests VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (m.run_id, m.experiment_id, m.decision_id, m.model_ref.hash, _dump(m), str(m.state), None, None, _now(), _now()))

    def set_run_state(self, cred: Credential, run_id: str, state: str, result: RunResult | None = None) -> None:
        self._exec(cred, "run_status", "status", run_id,
                   "UPDATE run_manifests SET state = ?, result = COALESCE(?, result), updated_at = ? WHERE run_id = ?",
                   (state, _dump(result) if result else None, _now(), run_id))

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        r = self._one("SELECT * FROM run_manifests WHERE run_id = ?", (run_id,))
        if not r:
            return None
        return {"manifest": RunManifest.model_validate_json(r["payload"]), "state": r["state"],
                "result": RunResult.model_validate_json(r["result"]) if r["result"] else None,
                "genesis": json.loads(r["genesis"]) if r["genesis"] else None}

    def list_runs(self, decision_id: str | None = None, experiment_id: str | None = None) -> list[dict[str, Any]]:
        if experiment_id:
            rows = self._all("SELECT run_id FROM run_manifests WHERE experiment_id = ? ORDER BY created_at, run_id", (experiment_id,))
        else:
            rows = self._all("SELECT run_id FROM run_manifests WHERE decision_id = ? ORDER BY created_at, run_id", (decision_id,))
        return [self.get_run(r["run_id"]) for r in rows]

    def get_events(self, run_id: str) -> list[WorldEvent]:
        rows = self._all("SELECT payload FROM simulation_events WHERE run_id = ? ORDER BY seq", (run_id,))
        return [WorldEvent.model_validate_json(r["payload"]) for r in rows]

    def get_checkpoint(self, run_id: str, seq: int) -> dict[str, Any] | None:
        r = self._one("SELECT blob FROM simulation_snapshots WHERE run_id = ? AND seq = ? AND kind = 'checkpoint'", (run_id, seq))
        return json.loads(r["blob"]) if r else None

    def list_checkpoints(self, run_id: str) -> list[int]:
        return [r["seq"] for r in self._all("SELECT seq FROM simulation_snapshots WHERE run_id = ? AND kind = 'checkpoint' ORDER BY seq", (run_id,))]

    def get_final_snapshot(self, run_id: str) -> dict[str, Any] | None:
        r = self._one("SELECT blob FROM simulation_snapshots WHERE run_id = ? AND kind = 'final'", (run_id,))
        return json.loads(r["blob"]) if r else None

    def is_simulation_fingerprint(self, text: str) -> list[str]:
        rows = self._all("SELECT event_id FROM sim_fingerprints WHERE fingerprint = ?", (sim_fingerprint(text),))
        return [r["event_id"] for r in rows]

    def simulation_event_exists(self, event_id: str) -> bool:
        return self._one("SELECT 1 FROM simulation_events WHERE event_id = ?", (event_id,)) is not None

    def run_store(self, cred: Credential) -> "SqliteRunStore":
        return SqliteRunStore(self, cred)

    # ------------------------------------------------------------------
    # human evidence + profiles (separate real-user store)
    # ------------------------------------------------------------------
    def put_human_evidence(self, cred: Credential, ev: HumanEvidenceEvent) -> bool:
        if ev.idempotency_key:
            r = self._one("SELECT event_id FROM human_evidence_events WHERE idempotency_key = ?", (ev.idempotency_key,))
            if r:
                return False
        self._exec(cred, "human_evidence_events", "insert", ev.event_id,
                   "INSERT INTO human_evidence_events VALUES (?,?,?,?,?,?,?,?,?)",
                   (ev.event_id, ev.user_id, ev.decision_id, ev.decision_revision, str(ev.signal_type), str(ev.origin_kind),
                    _dump(ev), ev.idempotency_key, ev.created_at))
        return True

    def get_human_evidence(self, event_id: str) -> HumanEvidenceEvent | None:
        r = self._one("SELECT payload FROM human_evidence_events WHERE event_id = ?", (event_id,))
        return HumanEvidenceEvent.model_validate_json(r["payload"]) if r else None

    def list_human_evidence(self, user_id: str) -> list[HumanEvidenceEvent]:
        rows = self._all("SELECT payload FROM human_evidence_events WHERE user_id = ? ORDER BY created_at", (user_id,))
        return [HumanEvidenceEvent.model_validate_json(r["payload"]) for r in rows]

    def delete_human_evidence(self, cred: Credential, event_id: str, user_id: str) -> None:
        self._exec(cred, "human_evidence_events", "delete", event_id,
                   "DELETE FROM human_evidence_events WHERE event_id = ? AND user_id = ?", (event_id, user_id))

    def put_profile_version(self, cred: Credential, p: RealProfileVersion) -> None:
        self._exec(cred, "profile_versions", "insert", f"{p.profile_id}@v{p.version}",
                   "INSERT INTO profile_versions VALUES (?,?,?,?,?,?)",
                   (p.profile_id, p.version, p.user_id, _dump(p), json.dumps(p.evidence_ids), p.created_at))

    def get_profile(self, profile_id: str, version: int | None = None) -> RealProfileVersion | None:
        if version is None:
            r = self._one("SELECT payload FROM profile_versions WHERE profile_id = ? ORDER BY version DESC LIMIT 1", (profile_id,))
        else:
            r = self._one("SELECT payload FROM profile_versions WHERE profile_id = ? AND version = ?", (profile_id, version))
        return RealProfileVersion.model_validate_json(r["payload"]) if r else None

    def list_profile_versions(self, profile_id: str) -> list[RealProfileVersion]:
        rows = self._all("SELECT payload FROM profile_versions WHERE profile_id = ? ORDER BY version", (profile_id,))
        return [RealProfileVersion.model_validate_json(r["payload"]) for r in rows]

    def put_profile_proposal(self, cred: Credential, p: ProfileUpdateProposal) -> None:
        self._exec(cred, "profile_update_proposals", "upsert", p.proposal_id,
                   "INSERT OR REPLACE INTO profile_update_proposals VALUES (?,?,?,?,?)",
                   (p.proposal_id, p.profile_id, p.status, _dump(p), p.created_at))

    def get_profile_proposal(self, proposal_id: str) -> ProfileUpdateProposal | None:
        r = self._one("SELECT payload FROM profile_update_proposals WHERE proposal_id = ?", (proposal_id,))
        return ProfileUpdateProposal.model_validate_json(r["payload"]) if r else None

    def list_profile_proposals(self, profile_id: str) -> list[ProfileUpdateProposal]:
        rows = self._all("SELECT payload FROM profile_update_proposals WHERE profile_id = ? ORDER BY created_at", (profile_id,))
        return [ProfileUpdateProposal.model_validate_json(r["payload"]) for r in rows]

    # ------------------------------------------------------------------
    # jobs, LLM records, users
    # ------------------------------------------------------------------
    def create_job(self, cred: Credential, job_id: str, kind: str, idem: str | None, request: dict[str, Any]) -> tuple[str, bool]:
        if idem:
            r = self._one("SELECT job_id FROM jobs WHERE idempotency_key = ?", (idem,))
            if r:
                return r["job_id"], False
        self._exec(cred, "jobs", "insert", job_id, "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)",
                   (job_id, kind, idem, "QUEUED", canonical_json(request), None, None, _now(), _now()))
        return job_id, True

    def update_job(self, cred: Credential, job_id: str, status: str, result: Any = None, error: str | None = None) -> None:
        self._exec(cred, "jobs", "update", job_id, "UPDATE jobs SET status = ?, result = ?, error = ?, updated_at = ? WHERE job_id = ?",
                   (status, canonical_json(result) if result is not None else None, error, _now(), job_id))

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        r = self._one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        if not r:
            return None
        d = dict(r)
        d["request"] = json.loads(d["request"]) if d["request"] else None
        d["result"] = json.loads(d["result"]) if d["result"] else None
        return d

    def put_llm_record(self, cred: Credential, record: dict[str, Any]) -> None:
        self._exec(cred, "llm_call_records", "insert", record["call_id"], "INSERT INTO llm_call_records VALUES (?,?,?,?,?,?,?,?,?)",
                   (record["call_id"], record["task"], record["provider"], record["model"], record["prompt_hash"],
                    record["output_hash"], canonical_json(record), 1 if record.get("tainted_by_simulation") else 0, record["created_at"]))

    def get_llm_record(self, call_id: str) -> dict[str, Any] | None:
        r = self._one("SELECT payload, tainted_by_simulation FROM llm_call_records WHERE call_id = ?", (call_id,))
        return {**json.loads(r["payload"]), "tainted_by_simulation": bool(r["tainted_by_simulation"])} if r else None

    def put_user(self, cred: Credential, user_id: str, display_name: str, token: str) -> None:
        self._exec(cred, "users", "insert", user_id, "INSERT OR REPLACE INTO users VALUES (?,?,?)",
                   (user_id, display_name, sha256_hex(token)))

    def user_for_token(self, token: str) -> dict[str, Any] | None:
        r = self._one("SELECT user_id, display_name FROM users WHERE token_hash = ?", (sha256_hex(token),))
        return dict(r) if r else None


class SqliteRunStore:
    """RunStore implementation bound to the simulation-worker credential."""

    def __init__(self, store: Store, cred: Credential):
        self.store = store
        self.cred = cred

    def commit_genesis(self, manifest: RunManifest, snapshot: dict[str, Any], state_hash: str, checkpoint: dict[str, Any]) -> None:
        s = self.store
        s.authorize(self.cred, "simulation_snapshots", "genesis", manifest.run_id)
        with s._lock:
            try:
                s.conn.execute("BEGIN")
                s.conn.execute("INSERT INTO simulation_snapshots VALUES (?,?,?,?,?,?)",
                               (manifest.run_id, -1, "genesis", None, state_hash, canonical_json(snapshot)))
                s.conn.execute("INSERT INTO simulation_snapshots VALUES (?,?,?,?,?,?)",
                               (manifest.run_id, -1, "checkpoint", None, state_hash, canonical_json(checkpoint)))
                s.conn.execute("UPDATE run_manifests SET genesis = ?, payload = ?, state = 'RUNNING', updated_at = ? WHERE run_id = ?",
                               (canonical_json({"snapshot": snapshot, "hash": state_hash}), _dump(manifest), _now(), manifest.run_id))
                s.conn.execute("COMMIT")
            except sqlite3.DatabaseError:
                s.conn.execute("ROLLBACK")
                raise
        s.audit(self.cred, "genesis", f"simulation_snapshots:{manifest.run_id}", True)

    def append_event(self, event: WorldEvent, checkpoint: dict[str, Any] | None) -> None:
        s = self.store
        s.authorize(self.cred, "simulation_events", "append", event.event_id)
        texts = [event.event_type]
        intent = event.payload.get("decision", {}).get("intent") if isinstance(event.payload, dict) else None
        if intent:
            texts.append(f"{intent.get('action_type')} {' '.join(map(str, intent.get('parameters', {}).values()))}")
        if "event_payload" in event.payload:
            vals = " ".join(str(v) for v in event.payload["event_payload"].values() if isinstance(v, (str, int, float)))
            texts.append(f"{event.event_type} {vals}")
        if "description" in event.payload:
            texts.append(str(event.payload["description"]))
        with s._lock:
            try:
                s.conn.execute("BEGIN")  # event + snapshot commit atomically (§8.2, §14.8)
                s.conn.execute("INSERT INTO simulation_events VALUES (?,?,?,?,?,?,?,?,?)",
                               (event.run_id, event.seq, event.event_id, str(event.origin_kind), event.sim_at, event.event_type,
                                _dump(event), event.pre_state_hash, event.post_state_hash))
                if checkpoint is not None:
                    s.conn.execute("INSERT INTO simulation_snapshots VALUES (?,?,?,?,?,?)",
                                   (event.run_id, event.seq, "checkpoint", event.sim_at, event.post_state_hash, canonical_json(checkpoint)))
                for t in texts:
                    s.conn.execute("INSERT OR IGNORE INTO sim_fingerprints VALUES (?,?,?)", (sim_fingerprint(t), event.run_id, event.event_id))
                s.conn.execute("COMMIT")
            except sqlite3.DatabaseError:
                s.conn.execute("ROLLBACK")
                raise

    def finalize(self, result: RunResult, final_snapshot: dict[str, Any]) -> None:
        s = self.store
        s.authorize(self.cred, "simulation_snapshots", "finalize", result.run_id)
        with s._lock:
            s.conn.execute("INSERT OR REPLACE INTO simulation_snapshots VALUES (?,?,?,?,?,?)",
                           (result.run_id, 10**9, "final", result.last_simulated_at, result.final_state_hash, canonical_json(final_snapshot)))
        s.set_run_state(self.cred, result.run_id, str(result.status), result)

    # read helpers mirroring InMemoryRunStore
    def get_events(self, run_id: str) -> list[WorldEvent]:
        return self.store.get_events(run_id)

    def get_genesis(self, run_id: str) -> dict[str, Any]:
        return self.store.get_run(run_id)["genesis"]

    def get_checkpoint(self, run_id: str, seq: int) -> dict[str, Any] | None:
        return self.store.get_checkpoint(run_id, seq)

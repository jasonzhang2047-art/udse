# Mapping the SQLite reference store to PostgreSQL (§14.6)

The reference build uses SQLite so it runs anywhere. Production should keep the same logical
tables and move enforcement into PostgreSQL roles, schemas and row-level security.

| Reference mechanism | PostgreSQL equivalent |
|---|---|
| `TABLE_WRITERS` role matrix + HMAC service credentials | one database role per service (`evidence_adapter`, `ingestion`, `curator`, `model_factory`, `orchestrator`, `run_scheduler`, `simulation_worker`, `human_evidence_endpoint`, `profile_updater`) with `GRANT INSERT` only on its tables; separate secrets per role |
| `external_claims_no_simulation` trigger | `CHECK (origin_kind <> 'SIMULATION_OUTPUT')` plus a `BEFORE INSERT` trigger that also checks lineage arrays |
| `curated_requires_grounded_claims` trigger | foreign keys from a `curated_claim_refs` join table to `external_claims` with a check on origin and status |
| `simulation_events` append-only triggers | `REVOKE UPDATE, DELETE` from every role; `simulation_worker` gets `INSERT` only |
| `model_versions_immutable`, `run_manifest_pinned` | column-level `REVOKE UPDATE` on `ir`, `hash`, `validation_report`, `model_hash`, `payload` |
| `profile_requires_human_evidence` trigger | FK from `profile_version_evidence` to `human_evidence_events(event_id, user_id)` |
| tenant / decision ACL checks in the API | RLS policies on `decision_specs`, `decision_model_versions`, `run_manifests`, `simulation_events`, `human_evidence_events`, `profile_versions` keyed by tenant and decision membership |
| JSON snapshots in `simulation_snapshots` | object storage with write-once retention, content-hash keys; the table keeps pointers and hashes |
| in-process thread pool + `jobs` table | durable task queue with idempotency keys (`run_id + event_id` for kernel commits) |

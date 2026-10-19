# ADR register (spec Appendix B)

Each record states the decision and where it is enforced in code and tests.

| ADR | Decision | Enforcement |
|---|---|---|
| ADR-001 | Universal event-sourced temporal kernel; immutable run model hashes | `temporal_kernel/kernel.py` (one loop for all domains), `Simulator.__init__` refuses unpinned hashes, `run_manifest_pinned` trigger; `test_two_domains_share_one_kernel` |
| ADR-002 | Agents intend, typed model executes; no direct LLM state mutation | `ActionIntent` → `_validate_intent` → declarative effects in `operators.py`; rejected intents commit no world change |
| ADR-003 | Three epistemic layers with role-enforced write boundaries; real profile separate | `storage/credentials.py` role matrix, `sqlite_store.py` triggers, `HumanEvidenceService`; `test_epistemic_isolation.py` |
| ADR-004 | Dynamic factory uses constrained typed IR, not runtime code | `model_ir/expr.py` safe parser, operator allowlist in `operators.py`, strict schemas; `test_compiler_rejects_*` |
| ADR-005 | Retrieval is first-class; evidence records quality and freshness | `research/` planner, adapters, normaliser, ingestion gate; restaurant stale/lead/injection tests |
| ADR-006 | Breadth inventory precedes selective depth; never claims exhaustiveness | `model_factory/breadth.py`, `CoverageFrontier.not_yet_discovered_warning`, orchestrator depth selection |
| ADR-007 | Simulated outcomes excluded from knowledge/profile promotion | ingestion gate (origin, lineage taint, provenance envelope, fingerprints), curator rules, profile trigger |
| ADR-008 | Reuse requires causal/applicability checks and versioned promotion | `model_registry/registry.py::transfer_gate`, `CuratorService` |
| ADR-009 | Multi-resource budgets across research, construction, validation, breadth, depth | `experiments/orchestrator.py::DecisionBudget`, `temporal_kernel/budget.py` |
| ADR-010 | Mobile-first with private Agent features and consented interaction evidence | `web/static`, profile privacy in API, implicit signals stored only with opt-in |

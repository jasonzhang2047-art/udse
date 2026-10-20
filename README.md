# Universal Decision Simulation Engine — v0.5 reference implementation

A working vertical slice of *Universal Decision Simulation Engine — Design & Implementation v0.5*:
dynamic, evidence-grounded, decision-scoped world models feeding a domain-agnostic, event-sourced
temporal kernel, with strict epistemic isolation between real evidence, model hypotheses and
simulated worlds.

> Status: reference implementation for technical review. Not deployed, calibrated or empirically
> validated. All restaurants, founders, prices, availability and market figures are synthetic
> fixtures (`example.invalid` sources).

## Quick start

```bash
uv sync
uv run pytest                      # 55 acceptance tests (§15.3, Appendix C)
uv run udse demo fundraising       # terminal walkthrough: research → model → runs → replay
uv run udse serve                  # API + mobile-first web client on http://127.0.0.1:8765
(cd apps/mobile && npm install && npx expo start --web)   # native Expo client (web target)
```

`udse serve` seeds the registry, fixture users and three decisions (restaurant, fundraising,
community-garden clean-room) and runs their experiments. Pick a fixture participant on the
sign-in screen (development tokens only).

### Using Claude

Offline by default: Agents use an individualised deterministic policy and the factory uses the
generic synthesizer. To enable Claude (ModelArchitect, MechanismSynthesizer, Critic, Repair,
breadth prompts and LLM Agents for detailed runs):

```bash
uv sync --extra llm
UDSE_LLM=anthropic uv run udse serve --llm
```

The provider uses `claude-opus-5-5` with adaptive thinking, an explicit effort per task,
streaming, refusal handling and server-side refusal fallbacks (`fallbacks: "default"`). Credentials
resolve the usual SDK way (`ANTHROPIC_API_KEY` or an `ant auth login` profile). Every call is
recorded with prompt/output hashes; calls that read simulated content are marked tainted and can
never feed evidence ingestion. The compiler, not the model, decides what is valid.

## Definition of done (spec §0) — where it is demonstrated

| Requirement | Evidence |
|---|---|
| Clean-room request in an unsupported category → researched, evidence-labelled draft model without a category adapter | `tests/test_model_factory.py::test_unsupported_category_builds_researched_model_without_adapter` (community garden) |
| Passes compile-time checks; executes several time-evolving branches | same test; `udse demo garden` |
| Returns trace-linked limitations; abstains only where mechanisms are unrepresentable | `plant_yield` → `X_LIMITED_OR_ABSTAIN` with research need; medical decision limited to conditional analysis |
| A generated hypothetical event fails every attempt to enter the evidence ledger | `tests/test_epistemic_isolation.py` (8 promotion paths, DB triggers, LLM rewrite, adapter laundering) |
| One kernel passes a short-horizon restaurant fixture and a long-horizon fundraising fixture | `tests/test_example_restaurant.py`, `tests/test_example_fundraising.py::test_two_domains_share_one_kernel` |
| Replay reconstructs committed state without fresh LLM calls | event-log *and* transition replay for every run; `/v1/runs/{id}/replay` |

## Architecture → code

| Spec | Package | Notes |
|---|---|---|
| §1.2 DecisionSpec, §4 Model IR, §14 schemas | `udse.model_ir` | strict Pydantic contracts, unit system, safe expression language (no `eval`, Kleene UNKNOWN) |
| §2 Decision Scope Planner | `udse.decision_scope` | horizon resolution, high-stakes detection, labelled system-suggested alternatives |
| §5 Research + evidence | `udse.research` | need planning with ordinal priority, adapters, normaliser (freshness, applicability, conflicts, independence, injection flags), ingestion gate |
| §6 Dynamic Model Factory | `udse.model_factory` | breadth discovery, package binding (`$claim`/`$fact`/`$requires`/`$unless`), generic clean-room synthesis, LLM tasks, repair ladder, versioning and diffs |
| §6.6 Compiler / validator | `udse.model_validator` | operator allowlist, types/units, dependency closure, zero-delay loops, evidence & visible tier downgrades, smoke/metamorphic/counterfactual property tests, per-metric fidelity, scorecard |
| §6.3 Reuse registry | `udse.model_registry` | applicability envelopes, formal transfer gate, curator promotion (never from simulation alone), revocation |
| §7 Agents | `udse.agent_runtime`, `udse.llm.agent_policy` | 3-layer state, sparse working sets, run-local overlays, observation redaction, deterministic individualised policy, LLM policy, recorded replay runtime |
| §8 Temporal kernel | `udse.temporal_kernel` | stable event queue, counter-based common random numbers, atomic patches with pre/post hashes, invariants (never clip), budgets, checkpoints, forks, replay |
| §9–10 Coverage + orchestrator | `udse.experiments`, `udse.model_factory.breadth` | coverage statuses, stratified screening, paired deadline/overlay arms, sensitivity probes, diversity-based depth, unified budget |
| §11 Trajectory analytics | `udse.trajectory_analytics` | timelines, scrubbable state, causal "why" chains, cards, divergence, replay reports |
| §12 Human evidence | `udse.human_evidence` | signal ladder, shadow vs visible proposals, approval, rollback |
| §14.6 Storage + tenancy | `udse.storage` | per-table writer roles, HMAC service credentials, append-only tables, provenance triggers, audit log |
| §14.7 API | `udse.api` | FastAPI, async idempotent jobs |
| §13 Mobile UX | `udse/web/static` | mobile-first web client: decisions, model coverage, evidence, frontier, timeline scrubber, divergence, profile approvals, boundary log |
| §13 Native app | `apps/mobile` | React Native + Expo + TypeScript (Expo Router) client for the same API, centred on the Futures map. See `apps/mobile/README.md`. |

Fixtures live in `fixtures/` (registry seed, restaurant, fundraising, clean-room and high-stakes decisions).
See `docs/adr/README.md` for the ADR register and `docs/postgres-mapping.md` for the production database mapping.

## Deliberate scope limits (not silently implied)

- **Native app:** `apps/mobile` is the React Native + Expo client the spec recommends. It has been verified on the web target (`npx expo start --web`), but not yet on an iOS or Android device.
- **Database:** SQLite with the same logical tables, role matrix and triggers stands in for PostgreSQL + RLS (mapping documented).
- **Live research:** source adapters for fixtures and user attachments are implemented; a live web/provider adapter is an interface, not a connector.
- **Auth:** development bearer tokens for fixture users; production sign-in, guest links and notifications are out of scope.
- **LLM paths** are covered by tests with a scripted provider and a fake SDK client; they have not been run against the live API in this build.
- **Calibration:** nothing here is calibrated against real outcomes. Tiers describe evidence support, not forecast accuracy.

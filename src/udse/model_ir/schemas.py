"""Canonical typed contracts (§14).

All models are strict (``extra="forbid"``): an LLM-proposed IR with unknown fields fails schema
validation instead of being silently accepted (§14.5).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    ClaimStatus,
    CoverageStatus,
    DeadlineDisclosure,
    FactorLabel,
    FeatureStatus,
    FidelityMode,
    FidelityTier,
    OriginKind,
    PromotionState,
    RecordType,
    ResearchNeedKind,
    RunStatus,
    SignalType,
)

SCHEMA_VERSION = "0.5.0"
IR_VERSION = "0.5.0"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------------------------------------------------------------------------
# §1.2 / §14.1 DecisionSpec
# ---------------------------------------------------------------------------

class AlternativeAssumption(StrictModel):
    """A user-stated consequence of choosing an alternative (e.g., a one-time cost at a date)."""

    ref: str
    value: Any = None
    delta: Any = None
    unit: str | None = None
    at: str | None = None  # offset ("+P6M") or ISO timestamp; None = at policy start
    origin: OriginKind = OriginKind.USER_ASSERTED
    evidence_ref: str | None = None
    description: str = ""


class Alternative(StrictModel):
    id: str
    policy: str
    label: str | None = None
    description: str = ""
    suggested_by_system: bool = False  # §1.2 newly suggested alternatives must be labelled
    assumptions: list[AlternativeAssumption] = Field(default_factory=list)


class Horizon(StrictModel):
    starts_at: str
    ends_at: str
    clock_policy: str = "discrete_event"
    timezone: str | None = None


class InitialFact(StrictModel):
    ref: str
    value: Any
    unit: str | None = None
    type: Literal["quantity", "boolean", "enum", "timestamp", "text"] | None = None
    values: list[str] | None = None
    origin: OriginKind = OriginKind.USER_ASSERTED
    evidence_ref: str | None = None
    description: str = ""
    visibility: Literal["public", "hidden"] = "public"


class DecisionConstraint(StrictModel):
    id: str
    ref: str | None = None
    min: float | None = None
    max: float | None = None
    expr: str | None = None
    hard: bool = True
    description: str = ""
    evidence_ref: str | None = None


class Participant(StrictModel):
    agent_id: str
    user_id: str | None = None
    display_name: str | None = None
    role: str = "participant"
    profile_ref: str | None = None


class ResearchPolicy(StrictModel):
    external_web: Literal["allowed", "denied"] = "denied"
    private_sources: Literal["consent_only", "denied"] = "consent_only"
    allowed_adapters: list[str] = Field(default_factory=lambda: ["fixture", "user_attachment"])


class GovernancePolicy(StrictModel):
    kind: Literal["unanimous", "majority", "quorum", "authorized_signatories", "single_owner"] = "unanimous"
    quorum: int | None = None
    signatories: list[str] = Field(default_factory=list)


class DecisionSpec(StrictModel):
    schema_version: str = SCHEMA_VERSION
    decision_id: str
    revision: int = 1
    question: str
    owner_id: str
    participant_refs: list[str] = Field(default_factory=list)
    participants: list[Participant] = Field(default_factory=list)
    alternatives: list[Alternative] = Field(default_factory=list)
    horizon: Horizon
    metrics: list[str] = Field(default_factory=list)
    initial_facts: list[InitialFact] = Field(default_factory=list)
    constraints: list[DecisionConstraint] = Field(default_factory=list)
    research_policy: ResearchPolicy = Field(default_factory=ResearchPolicy)
    governance_policy: GovernancePolicy = Field(default_factory=GovernancePolicy)
    fidelity_request: Literal["quick", "balanced", "deep"] = "balanced"
    privacy_policy_ref: str | None = None
    budget_profile_id: str = "balanced_v1"
    domain_labels: list[str] = Field(default_factory=list)
    jurisdiction: str | None = None
    risk_class: Literal["standard", "high_stakes"] | None = None
    model_package_hint: str | None = None  # optional registry structure id the user/setup selected
    attachments: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# §3.2 / §14.2 Evidence (Layer A staging) and research needs
# ---------------------------------------------------------------------------

class SourceRef(StrictModel):
    uri: str
    content_hash: str
    span: str | None = None
    artifact_id: str | None = None


class EvidenceClaim(StrictModel):
    claim_id: str
    origin_kind: OriginKind
    record_type: RecordType = RecordType.OBSERVATION
    subject: str
    predicate: str
    object: Any
    unit: str | None = None
    claim_valid_at: str | None = None  # the date the claim pertains to
    observed_at: str | None = None
    retrieved_at: str | None = None
    expires_at: str | None = None
    verified_at: str | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)
    scope: dict[str, Any] = Field(default_factory=dict)  # jurisdiction, entity, population...
    status: ClaimStatus = ClaimStatus.UNVERIFIED_EXTRACT
    conflicting_claim_ids: list[str] = Field(default_factory=list)
    supported_by: list[str] = Field(default_factory=list)
    derived_from: list[str] = Field(default_factory=list)  # lineage (claims, artifacts, sim events, llm calls)
    supersedes: list[str] = Field(default_factory=list)
    original_source_id: str | None = None  # secondary articles repeating one original are not independent
    license_scope: str = "fixture_only"
    extractor_version: str = "structured-fixture-extractor/1"
    quality: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class RawSourceArtifact(StrictModel):
    artifact_id: str
    uri: str
    adapter: str
    content_hash: str
    retrieved_at: str
    tenant: str = "default"
    access: dict[str, Any] = Field(default_factory=dict)
    license_scope: str = "fixture_only"
    content: str = ""


class ResearchNeed(StrictModel):
    need_id: str
    kind: ResearchNeedKind
    question: str
    why_it_matters: str
    target_refs: list[str] = Field(default_factory=list)
    affects_metrics: list[str] = Field(default_factory=list)
    affects_families: list[str] = Field(default_factory=list)
    candidate_sources: list[str] = Field(default_factory=list)
    permitted_scopes: list[str] = Field(default_factory=list)
    freshness: str | None = None
    jurisdiction: str | None = None
    est_cost: float = 1.0
    consequence_if_unresolved: str = ""
    priority: int = 3  # 1 highest; transparent rule-based ordinal (§5.4, §10.5)
    priority_rationale: str = ""
    status: Literal["OPEN", "RESOLVED", "UNRESOLVED", "ASK_USER", "SKIPPED_PERMISSION"] = "OPEN"
    resolution_claim_ids: list[str] = Field(default_factory=list)
    trigger_diagnostic_id: str | None = None


class EvidenceBundle(StrictModel):
    bundle_id: str
    decision_id: str
    decision_rev: int
    as_of: str
    claims: list[EvidenceClaim] = Field(default_factory=list)
    artifacts: list[RawSourceArtifact] = Field(default_factory=list)
    needs: list[ResearchNeed] = Field(default_factory=list)
    rejected: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    hash: str | None = None

    def claim(self, claim_id: str) -> EvidenceClaim | None:
        return next((c for c in self.claims if c.claim_id == claim_id), None)


# ---------------------------------------------------------------------------
# §4 / §14.3 Model IR
# ---------------------------------------------------------------------------

class Visibility(StrictModel):
    scope: Literal["public", "agents", "hidden"] = "public"
    agents: list[str] = Field(default_factory=list)


class EntitySpec(StrictModel):
    id: str
    type: str
    label: str | None = None
    description: str = ""


class StateVar(StrictModel):
    ref: str
    type: Literal["quantity", "boolean", "enum", "timestamp", "ledger", "text"]
    unit: str | None = None
    values: list[str] | None = None
    initial_value: Any = None
    allow_unknown: bool = False
    min: float | None = None
    max: float | None = None
    visibility: Visibility = Field(default_factory=Visibility)
    is_deadline: bool = False  # visible to Agents only in DEADLINE_AWARE arms (§7.4)
    origin: OriginKind = OriginKind.MODEL_HYPOTHESIS
    evidence_refs: list[str] = Field(default_factory=list)
    epistemic_tier: FidelityTier = FidelityTier.B_ASSUMPTION_BOUND
    description: str = ""
    label: str | None = None


class Distribution(StrictModel):
    kind: Literal["bernoulli", "uniform", "triangular", "normal", "choice"]
    p: float | None = None
    low: float | None = None
    high: float | None = None
    mode: float | None = None
    mean: float | None = None
    sd: float | None = None
    options: list[dict[str, Any]] | None = None  # [{"value": ..., "weight": ...}]
    unit: str | None = None


class ParameterSpec(StrictModel):
    id: str
    unit: str | None = None
    type: Literal["quantity", "boolean", "enum", "timestamp", "distribution"] = "quantity"
    values: list[str] | None = None
    value: Any = None
    distribution: Distribution | None = None
    range: list[float] | None = None  # declared sensitivity bounds [low, high]
    scenario_values: dict[str, Any] = Field(default_factory=dict)
    source_kind: Literal[
        "dataset", "official_rule", "user_asserted", "approved_analyst_assumption",
        "declared_exploratory_range", "model_hypothesis", "derived",
    ] = "model_hypothesis"
    origin: OriginKind = OriginKind.MODEL_HYPOTHESIS
    evidence_refs: list[str] = Field(default_factory=list)
    epistemic_tier: FidelityTier = FidelityTier.C_ILLUSTRATIVE
    description: str = ""
    agent_visible: bool = False  # model parameters are hidden from Agents unless humans would know them


class ExogenousEvent(StrictModel):
    at: str
    event_type: str
    effects: list["Effect"] = Field(default_factory=list)
    description: str = ""
    visibility: Visibility = Field(default_factory=Visibility)


class ScenarioSpec(StrictModel):
    id: str
    label: str
    kind: Literal["external_condition", "evidence_scenario", "branch_assumption", "model_alternative"] = (
        "external_condition"
    )
    description: str = ""
    parameter_overrides: dict[str, Any] = Field(default_factory=dict)
    state_overrides: dict[str, Any] = Field(default_factory=dict)
    exogenous_events: list[ExogenousEvent] = Field(default_factory=list)
    probability: float | None = None  # must stay None unless independently estimable (§18.3)
    probability_basis: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    epistemic_tier: FidelityTier = FidelityTier.C_ILLUSTRATIVE


class ActionWindow(StrictModel):
    action: str
    available_from: str | None = None
    available_until: str | None = None
    when: str | None = None


class PolicySpec(StrictModel):
    id: str
    label: str
    description: str = ""
    alternative_ref: str | None = None
    suggested_by_system: bool = False
    initial_effects: list["Effect"] = Field(default_factory=list)
    scheduled_effects: list[ExogenousEvent] = Field(default_factory=list)
    action_windows: list[ActionWindow] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)


class AgentSpec(StrictModel):
    id: str
    display_name: str | None = None
    role: str = "participant"
    profile_ref: str | None = None
    entity_ref: str | None = None
    initial_wakeups: list[str] = Field(default_factory=list)
    periodic_review: str | None = None
    max_wakeups: int | None = None
    initial_beliefs: dict[str, Any] = Field(default_factory=dict)


class ActionArg(StrictModel):
    name: str
    type: Literal["enum", "number", "boolean"]
    values: list[str] | None = None
    min: float | None = None
    max: float | None = None
    unit: str | None = None
    options: list[float] | None = None  # candidate numeric values an Agent may choose


class Precondition(StrictModel):
    id: str
    expr: str
    rejection_code: str = "PRECONDITION_FAILED"


class GovernanceAct(StrictModel):
    mechanism: str
    act: Literal["propose", "endorse", "object", "withdraw"]
    option_arg: str | None = None


# ----- effects (typed patch operations, never code) -----

class SetEffect(StrictModel):
    op: Literal["set"]
    ref: str
    value: str
    when: str | None = None


class IncEffect(StrictModel):
    op: Literal["inc"]
    ref: str
    by: str
    when: str | None = None


class DecEffect(StrictModel):
    op: Literal["dec"]
    ref: str
    by: str
    when: str | None = None


class EmitEffect(StrictModel):
    op: Literal["emit"]
    event_type: str
    delay: str = "PT0S"  # ISO-8601 duration, or an expression yielding a time quantity
    payload: dict[str, str] = Field(default_factory=dict)
    visibility: Visibility = Field(default_factory=Visibility)
    when: str | None = None


class TerminateEffect(StrictModel):
    op: Literal["terminate"]
    outcome: str
    reason: str = ""
    when: str | None = None


class WakeEffect(StrictModel):
    op: Literal["wake"]
    agents: list[str] = Field(default_factory=lambda: ["*"])
    delay: str = "PT0S"
    reason: str = "world_event"


Effect = Annotated[Union[SetEffect, IncEffect, DecEffect, EmitEffect, TerminateEffect, WakeEffect], Field(discriminator="op")]


class ActionSpec(StrictModel):
    id: str
    description: str = ""
    actors: list[str] = Field(default_factory=lambda: ["*"])
    args: list[ActionArg] = Field(default_factory=list)
    preconditions: list[Precondition] = Field(default_factory=list)
    effects: list[Effect] = Field(default_factory=list)
    governance: GovernanceAct | None = None
    appraisal: dict[str, str] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)  # e.g. information_request, irreversible
    visibility: Visibility = Field(default_factory=Visibility)
    epistemic_tier: FidelityTier = FidelityTier.B_ASSUMPTION_BOUND
    evidence_refs: list[str] = Field(default_factory=list)


# ----- mechanism rules (§4.2 typed operators) -----

class SubtractFlow(StrictModel):
    kind: Literal["subtract_flow"]
    target: str
    rate_ref: str  # expression (a bare ref is a valid expression)
    interval_unit: Literal["minute", "hour", "day", "week", "month", "year"]


class AddFlow(StrictModel):
    kind: Literal["add_flow"]
    target: str
    rate_ref: str
    interval_unit: Literal["minute", "hour", "day", "week", "month", "year"]


class Transfer(StrictModel):
    kind: Literal["transfer"]
    source: str
    sink: str
    amount: str


class QueueService(StrictModel):
    kind: Literal["queue_service"]
    queue_ref: str
    served_ref: str
    capacity: str  # items per interval_unit
    interval_unit: Literal["minute", "hour", "day", "week", "month", "year"]


class MilestoneProgress(StrictModel):
    kind: Literal["milestone_progress"]
    progress_ref: str
    rate: str  # progress per interval_unit
    interval_unit: Literal["minute", "hour", "day", "week", "month", "year"]
    required: str
    done_ref: str
    on_complete: list[Effect] = Field(default_factory=list)


class ThresholdGate(StrictModel):
    kind: Literal["threshold_gate"]
    target: str
    comparator: Literal["lt", "lte", "gt", "gte", "eq", "neq"]
    bound_ref: str
    on_trigger: list[Effect] = Field(default_factory=list)
    once: bool = True


class StateTransitionEdge(StrictModel):
    from_state: str = Field(alias="from")
    to: str
    on_event: str | None = None
    when: str | None = None
    effects: list[Effect] = Field(default_factory=list)


class StateTransition(StrictModel):
    kind: Literal["state_transition"]
    state_ref: str
    transitions: list[StateTransitionEdge]


class StochasticDraw(StrictModel):
    kind: Literal["stochastic_draw"]
    stream: str
    distribution: Distribution | None = None
    distribution_param: str | None = None  # id of a ParameterSpec of type distribution
    target: str | None = None  # numeric draws: set target to the draw
    on_true: list[Effect] = Field(default_factory=list)
    on_false: list[Effect] = Field(default_factory=list)
    outcomes: dict[str, list[Effect]] = Field(default_factory=dict)  # choice draws
    when: str | None = None


class OnEvent(StrictModel):
    kind: Literal["on_event"]
    effects: list[Effect]
    when: str | None = None


class Wakeup(StrictModel):
    kind: Literal["wakeup"]
    agents: list[str] = Field(default_factory=lambda: ["*"])
    latency: str = "PT0S"
    reason: str = "relevant_world_event"
    exclude_actor: bool = True
    when: str | None = None


class Governance(StrictModel):
    kind: Literal["governance"]
    participants: list[str]
    decision_rule: Literal["unanimous", "majority", "quorum", "authorized_signatories", "single_owner"]
    quorum: int | None = None
    signatories: list[str] = Field(default_factory=list)
    ledger_ref: str
    outcome_ref: str
    option_values: list[str]
    on_satisfied: list[Effect] = Field(default_factory=list)
    max_rounds: int | None = None
    proposal_requires_gain: bool = False  # appraisals are changes vs the status quo; propose/endorse only on net gain


class ScheduledEffects(StrictModel):
    kind: Literal["scheduled_effects"]
    effects: list[Effect]
    when: str | None = None


Rule = Annotated[
    Union[
        SubtractFlow, AddFlow, Transfer, QueueService, MilestoneProgress, ThresholdGate, StateTransition,
        StochasticDraw, OnEvent, Wakeup, Governance, ScheduledEffects,
    ],
    Field(discriminator="kind"),
]


class ScheduleSpec(StrictModel):
    kind: Literal["recurring", "at", "on_event", "watch", "none"]
    interval: str | None = None
    start: str | None = None
    end: str | None = None
    times: list[str] = Field(default_factory=list)
    event_types: list[str] = Field(default_factory=list)


class MechanismSpec(StrictModel):
    id: str
    operator: str
    operator_version: str = "1.0.0"
    description: str = ""
    rule: Rule
    schedule: ScheduleSpec = Field(default_factory=lambda: ScheduleSpec(kind="none"))
    inputs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    epistemic_tier: FidelityTier = FidelityTier.B_ASSUMPTION_BOUND
    evidence_refs: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    origin: OriginKind = OriginKind.MODEL_HYPOTHESIS
    registry_ref: dict[str, Any] | None = None  # {"entry_id", "version", "hash"} if reused from Layer A
    visibility: Visibility = Field(default_factory=Visibility)


class ViolationPolicy(StrictModel):
    action: Literal["reject", "terminate", "emit"] = "reject"
    event_type: str | None = None
    outcome: str | None = None
    reason: str = ""


class ConstraintSpec(StrictModel):
    id: str
    type: Literal["guarded_floor", "guarded_ceiling", "expression"]
    path: str | None = None
    min: float | None = None
    max: float | None = None
    expr: str | None = None
    on_violation: ViolationPolicy = Field(default_factory=ViolationPolicy)
    validated: bool = False  # an evidenced hard constraint (needed for REJECTED_VALIDATED_CONSTRAINT)
    evidence_refs: list[str] = Field(default_factory=list)
    description: str = ""


class MetricSpec(StrictModel):
    id: str
    label: str | None = None
    expr: str | None = None
    unit: str | None = None
    direction: Literal["higher_better", "lower_better", "neutral"] | None = None
    computable: bool = True
    reason: str = ""


class StructuralUnknown(StrictModel):
    id: str
    description: str = ""
    impact: Literal["high", "medium", "low"] = "medium"
    status: Literal["DISCOVERED_UNMODELED", "MODELED_AS_SCENARIO", "RESOLVED"] = "DISCOVERED_UNMODELED"
    affects_metrics: list[str] = Field(default_factory=list)
    research_need_ids: list[str] = Field(default_factory=list)


class Omission(StrictModel):
    id: str
    description: str
    reason: str = ""
    risk: Literal["high", "medium", "low"] = "low"


class FactorAssessment(StrictModel):
    factor_id: str
    description: str
    label: FactorLabel
    decision_relevance: str = ""
    evidence_availability: str = ""
    uncertainty: str = ""
    omission_risk: str = ""
    incremental_cost: str = ""
    rationale: str = ""


class OddDescription(StrictModel):
    """ODD-inspired human-readable description (§4.1, Grimm et al.)."""

    purpose: str = ""
    entities_state_scales: str = ""
    process_overview_scheduling: str = ""
    initialization: str = ""
    inputs: str = ""
    submodels: str = ""
    evaluation_rationale: str = ""


class ModelScope(StrictModel):
    decision_rev: int
    horizon_start: str
    horizon_end: str
    time_resolution: Literal["minute", "hour", "day", "week", "month"] = "day"
    domain_labels: list[str] = Field(default_factory=list)
    high_stakes: bool = False
    jurisdiction: str | None = None


class FrontierItem(StrictModel):
    id: str
    family_kind: str
    label: str
    description: str = ""
    policy_id: str | None = None
    scenario_ids: list[str] = Field(default_factory=list)
    required_mechanisms: list[str] = Field(default_factory=list)
    outcome_match: dict[str, Any] | None = None  # {"ref": ..., "value": ...} terminal-state family
    infeasible_under: str | None = None  # constraint id that, if validated, rules this family out
    status: CoverageStatus = CoverageStatus.DISCOVERED_UNSIMULATED
    status_reason: str = ""
    discovered_by: list[str] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    citation: list[str] = Field(default_factory=list)
    user_interest: bool = False
    history: list[dict[str, Any]] = Field(default_factory=list)


class CoverageFrontier(StrictModel):
    frontier_id: str
    decision_id: str
    model_version: int | None = None
    items: list[FrontierItem] = Field(default_factory=list)
    not_yet_discovered_warning: str = (
        "Coverage is not exhaustive: possibilities may exist outside this inventory and cannot be enumerated."
    )
    discovery_methods: list[str] = Field(default_factory=list)
    stop_rationale: str = ""
    suspected_blind_spots: list[str] = Field(default_factory=list)


class FidelityScorecard(StrictModel):
    """§4.6 separate scores; never averaged into one percentage."""

    schema_validity: str = "unchecked"
    mechanism_coverage: str = "unchecked"
    empirical_support: str = "unchecked"
    parameter_identifiability: str = "unchecked"
    transfer_applicability: str = "unchecked"
    trajectory_stability: str = "not_yet_measured"
    structural_omission_risk: str = "unchecked"
    interpretive_limitations: list[str] = Field(default_factory=list)


class ValidationCheck(StrictModel):
    id: str
    status: Literal["pass", "fail", "warn", "skip"]
    details: list[str] = Field(default_factory=list)


class ValidationReport(StrictModel):
    report_id: str
    model_hash: str | None = None
    passed: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checks: list[ValidationCheck] = Field(default_factory=list)
    metric_fidelity: dict[str, FidelityTier] = Field(default_factory=dict)
    mechanism_fidelity: dict[str, FidelityTier] = Field(default_factory=dict)
    metric_dependencies: dict[str, list[str]] = Field(default_factory=dict)
    non_computable_metrics: dict[str, str] = Field(default_factory=dict)
    allowed_run_modes: list[str] = Field(default_factory=list)
    scorecard: FidelityScorecard = Field(default_factory=FidelityScorecard)
    downgrades: list[dict[str, Any]] = Field(default_factory=list)
    weakest_influential: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def can_execute(self) -> bool:
        return self.passed and bool(self.allowed_run_modes)


class EpistemicManifest(StrictModel):
    state: dict[str, dict[str, Any]] = Field(default_factory=dict)
    parameters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    mechanisms: dict[str, dict[str, Any]] = Field(default_factory=dict)
    evidence_pins: list[str] = Field(default_factory=list)
    evidence_bundle_hash: str | None = None
    stale_evidence: list[str] = Field(default_factory=list)
    conflicted_evidence: list[str] = Field(default_factory=list)


class LLMTaskRecord(StrictModel):
    task: str
    provider: str
    model: str
    prompt_hash: str
    output_hash: str
    usage: dict[str, int] = Field(default_factory=dict)
    created_at: str


class DecisionWorldModel(StrictModel):
    ir_version: str = IR_VERSION
    model_id: str
    model_version: int = 1
    decision_id: str
    purpose: str = ""
    decision_criteria: list[str] = Field(default_factory=list)
    odd: OddDescription = Field(default_factory=OddDescription)
    scope: ModelScope
    entities: list[EntitySpec] = Field(default_factory=list)
    state: list[StateVar] = Field(default_factory=list)
    parameters: list[ParameterSpec] = Field(default_factory=list)
    scenarios: list[ScenarioSpec] = Field(default_factory=list)
    policies: list[PolicySpec] = Field(default_factory=list)
    agents: list[AgentSpec] = Field(default_factory=list)
    actions: list[ActionSpec] = Field(default_factory=list)
    mechanisms: list[MechanismSpec] = Field(default_factory=list)
    constraints: list[ConstraintSpec] = Field(default_factory=list)
    metrics: list[MetricSpec] = Field(default_factory=list)
    causal_assumptions: list[str] = Field(default_factory=list)
    structural_unknowns: list[StructuralUnknown] = Field(default_factory=list)
    omissions: list[Omission] = Field(default_factory=list)
    factor_table: list[FactorAssessment] = Field(default_factory=list)
    epistemic_manifest: EpistemicManifest = Field(default_factory=EpistemicManifest)
    reuse_manifest: list[dict[str, Any]] = Field(default_factory=list)
    test_fixtures: list[dict[str, Any]] = Field(default_factory=list)
    promotion_state: PromotionState = PromotionState.DRAFT_B
    parent_version: int | None = None
    revision_reason: str | None = None
    construction_tasks: list[LLMTaskRecord] = Field(default_factory=list)
    validation_report_ref: str | None = None
    coverage_frontier_ref: str | None = None
    hash: str | None = None


# ---------------------------------------------------------------------------
# §7 Agents and profiles
# ---------------------------------------------------------------------------

class ProfileFeature(StrictModel):
    key: str  # namespace.key, e.g. preference.cost_pp, constraint.gluten_safe
    value: Any = None  # value_or_distribution
    scope: str = "general"
    status: FeatureStatus = FeatureStatus.UNKNOWN
    epistemic_confidence: Literal["low", "medium", "high", "unknown"] = "unknown"
    relevance: float | None = None
    identifiability: Literal["clear", "overlapping", "unknown"] = "unknown"
    evidence_refs: list[str] = Field(default_factory=list)
    last_reviewed_at: str | None = None
    feature_version: int = 1
    privacy: Literal["private", "group_visible", "shared"] = "private"
    note: str = ""


class RealProfileVersion(StrictModel):
    profile_id: str
    user_id: str
    version: int
    features: list[ProfileFeature] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: str
    created_by: str = "profile_updater"
    reason: str = ""
    parent_version: int | None = None
    implicit_learning_opt_in: bool = False


class FeatureOverlay(StrictModel):
    """RunAgentConfig hypothesis (§7.1): a counterfactual, never written to the real profile."""

    key: str
    value: Any
    hypothesis: str


class RunAgentConfig(StrictModel):
    agent_id: str
    profile_snapshot_ref: str | None = None
    overlays: list[FeatureOverlay] = Field(default_factory=list)
    policy_kind: Literal["profile_utility", "llm"] = "profile_utility"
    temperature: float | None = None
    seed: int | None = None


class ActionIntent(StrictModel):
    """§7.8 behaviour contract."""

    run_id: str
    agent_id: str
    observed_at: str
    action_type: str
    target_entity_ids: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_effects: list[str] = Field(default_factory=list)
    rationale_summary: str | None = None
    uncertainty_note: str | None = None


class ValidatedIntent(StrictModel):
    valid: bool
    rejection_code: str | None = None
    matched_precondition_ids: list[str] = Field(default_factory=list)
    authorized_transition_id: str | None = None
    detail: str | None = None


# ---------------------------------------------------------------------------
# §8 / §14.4 Runs and events (Layer C)
# ---------------------------------------------------------------------------

class ExecutionBudget(StrictModel):
    """Engineering caps — never shown to Agents as a deadline (§7.4, §8.5)."""

    max_wall_seconds: float = 180.0
    max_model_tokens: int = 40000
    max_model_calls: int = 60
    max_events: int = 600
    max_scheduled_events: int = 5000
    max_agent_wakeups: int = 60
    max_negotiation_rounds: int = 12
    max_zero_time_chain: int = 200
    reserve_events: int = 2


class ModelRef(StrictModel):
    model_id: str
    version: int
    hash: str


class BranchParent(StrictModel):
    run_id: str
    checkpoint_seq: int


class Intervention(StrictModel):
    policy_id: str | None = None
    scenario_ids: list[str] | None = None
    agent_overlays: dict[str, list[FeatureOverlay]] = Field(default_factory=dict)
    description: str = ""


class RunManifest(StrictModel):
    run_id: str
    experiment_id: str | None = None
    decision_id: str
    model_ref: ModelRef
    profile_snapshot_refs: dict[str, str] = Field(default_factory=dict)
    agent_configs: dict[str, RunAgentConfig] = Field(default_factory=dict)
    policy_id: str
    scenario_ids: list[str] = Field(default_factory=list)
    deadline_disclosure: DeadlineDisclosure = DeadlineDisclosure.NOT_APPLICABLE
    branch_parent: BranchParent | None = None
    intervention: Intervention | None = None
    random_streams: dict[str, int] = Field(default_factory=dict)
    execution_budget: ExecutionBudget = Field(default_factory=ExecutionBudget)
    fidelity_mode: FidelityMode = FidelityMode.DEPTH
    parameter_overrides: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    initial_state_hash: str | None = None
    pair_group: str | None = None
    frontier_item_ids: list[str] = Field(default_factory=list)
    label: str | None = None
    state: RunStatus = RunStatus.CREATED


class PatchOp(StrictModel):
    layer: Literal["world", "agent", "internal"]
    key: str
    before: Any = None
    after: Any = None


class WorldEvent(StrictModel):
    event_id: str
    run_id: str
    branch_id: str
    seq: int
    sim_at: str
    event_type: str
    kind: str
    origin_kind: Literal[OriginKind.SIMULATION_OUTPUT] = OriginKind.SIMULATION_OUTPUT
    actor_id: str | None = None
    parent_event_ids: list[str] = Field(default_factory=list)
    transition_ref: str | None = None
    pre_state_hash: str
    patch: list[PatchOp] = Field(default_factory=list)
    post_state_hash: str
    assumption_refs: list[str] = Field(default_factory=list)
    random_draw_refs: list[dict[str, Any]] = Field(default_factory=list)
    visibility_scope: Visibility = Field(default_factory=Visibility)
    payload: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, int] = Field(default_factory=dict)
    tier: FidelityTier | None = None


class ModelDiagnostic(StrictModel):
    diagnostic_id: str
    run_id: str | None = None
    model_hash: str
    failure_or_gap_type: Literal[
        "INVARIANT_VIOLATION", "HIGH_SENSITIVITY", "OMITTED_CAUSAL_PARENT", "UNEXPLAINED_DISCONTINUITY",
        "UNMODELED_PATH", "UNKNOWN_DEPENDENCY", "MODEL_EXECUTION_ERROR", "BUDGET_LIMIT", "ZERO_TIME_LOOP",
        "STALE_EVIDENCE", "SOURCE_CONFLICT",
    ]
    impacted_mechanisms: list[str] = Field(default_factory=list)
    observed_sim_effect: str = ""
    risk_class: Literal["high", "medium", "low"] = "medium"
    external_research_need: str | None = None
    is_real_world_evidence: Literal[False] = False
    origin_kind: Literal[OriginKind.SIMULATION_OUTPUT] = OriginKind.SIMULATION_OUTPUT


class RunResult(StrictModel):
    run_id: str
    status: RunStatus
    reason: str = ""
    terminal_outcome: str | None = None
    outcome_flags: list[str] = Field(default_factory=list)
    horizon_reached: bool = False
    last_simulated_at: str
    horizon_end: str
    events_committed: int = 0
    final_state_hash: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    diagnostics: list[ModelDiagnostic] = Field(default_factory=list)
    final_metrics: dict[str, Any] = Field(default_factory=dict)
    weakest_tier: FidelityTier | None = None


# ---------------------------------------------------------------------------
# §12 Human evidence and profile updates (separate store)
# ---------------------------------------------------------------------------

class ConsentFlags(StrictModel):
    implicit_learning: bool = False
    explicit_feedback: bool = True
    share_with_group: bool = False


class ExposureContext(StrictModel):
    options_shown: list[str] = Field(default_factory=list)
    order: list[str] = Field(default_factory=list)
    session_id: str | None = None
    screen: str | None = None


class HumanEvidenceEvent(StrictModel):
    event_id: str
    user_id: str
    decision_id: str
    decision_revision: int
    signal_type: SignalType
    origin_kind: Literal[OriginKind.HUMAN_FEEDBACK] = OriginKind.HUMAN_FEEDBACK
    consent: ConsentFlags = Field(default_factory=ConsentFlags)
    exposure: ExposureContext = Field(default_factory=ExposureContext)
    payload: dict[str, Any] = Field(default_factory=dict)
    referenced_run_ids: list[str] = Field(default_factory=list)
    created_at: str
    idempotency_key: str | None = None


class FeatureChange(StrictModel):
    key: str
    before: ProfileFeature | None = None
    after: ProfileFeature | None = None
    change_kind: Literal["relevance", "value", "constraint_candidate", "scope", "uncertainty", "new_feature"]


class ProfileUpdateProposal(StrictModel):
    proposal_id: str
    profile_id: str
    user_id: str
    base_version: int
    changes: list[FeatureChange]
    evidence_ids: list[str]
    interpretation_rule: str
    mode: Literal["shadow", "visible"] = "shadow"
    requires_user_confirmation: bool = True
    status: Literal["PROPOSED", "APPROVED", "REJECTED", "APPLIED", "SHADOW_ONLY"] = "PROPOSED"
    reason: str = ""
    created_at: str


ExogenousEvent.model_rebuild()
PolicySpec.model_rebuild()

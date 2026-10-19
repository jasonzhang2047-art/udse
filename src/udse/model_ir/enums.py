"""Controlled vocabularies from the v0.5 specification.

Each enum maps to a named list in the spec; names are kept verbatim so that stored records,
API payloads and UI labels share one vocabulary.
"""

from __future__ import annotations

from enum import StrEnum


class OriginKind(StrEnum):
    """§3.2 explicit origin kinds. Orthogonal to confidence and to source class."""

    REAL_EXTERNAL_OBSERVATION = "REAL_EXTERNAL_OBSERVATION"
    OFFICIAL_RULE = "OFFICIAL_RULE"
    USER_ASSERTED = "USER_ASSERTED"
    HUMAN_FEEDBACK = "HUMAN_FEEDBACK"
    HISTORICAL_DATASET = "HISTORICAL_DATASET"
    MODEL_HYPOTHESIS = "MODEL_HYPOTHESIS"
    SIMULATION_OUTPUT = "SIMULATION_OUTPUT"
    DERIVED_CALCULATION = "DERIVED_CALCULATION"


# Origins that may ground a Layer A/B fact (subject to freshness/applicability/conflict checks).
GROUNDING_ORIGINS = frozenset(
    {OriginKind.REAL_EXTERNAL_OBSERVATION, OriginKind.OFFICIAL_RULE, OriginKind.HISTORICAL_DATASET}
)
# Origins that can never enter any real-world evidence ingestion or promotion path.
FORBIDDEN_EVIDENCE_ORIGINS = frozenset({OriginKind.SIMULATION_OUTPUT})


class FidelityTier(StrEnum):
    """§1.5 per-mechanism / per-metric fidelity. Ordered strongest → weakest."""

    A_GROUNDED = "A_GROUNDED"
    B_ASSUMPTION_BOUND = "B_ASSUMPTION_BOUND"
    C_ILLUSTRATIVE = "C_ILLUSTRATIVE"
    X_LIMITED_OR_ABSTAIN = "X_LIMITED_OR_ABSTAIN"


TIER_ORDER = [
    FidelityTier.A_GROUNDED,
    FidelityTier.B_ASSUMPTION_BOUND,
    FidelityTier.C_ILLUSTRATIVE,
    FidelityTier.X_LIMITED_OR_ABSTAIN,
]


def weakest_tier(tiers) -> FidelityTier:
    tiers = list(tiers)
    if not tiers:
        return FidelityTier.X_LIMITED_OR_ABSTAIN
    return max(tiers, key=TIER_ORDER.index)


class RunStatus(StrEnum):
    """§8.1 run lifecycle (+ CREATED/... intermediate states)."""

    CREATED = "CREATED"
    MODEL_VALIDATED = "MODEL_VALIDATED"
    INITIALIZED = "INITIALIZED"
    RUNNING = "RUNNING"
    COMPLETED_HORIZON = "COMPLETED_HORIZON"
    TERMINAL_STATE = "TERMINAL_STATE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    MODEL_EXECUTION_BLOCKED = "MODEL_EXECUTION_BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


FINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.COMPLETED_HORIZON,
        RunStatus.TERMINAL_STATE,
        RunStatus.BUDGET_EXHAUSTED,
        RunStatus.MODEL_EXECUTION_BLOCKED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }
)


class OutcomeFlag(StrEnum):
    """§1.5 outcome semantics distinct from infrastructure failure."""

    PARTIAL_HORIZON = "PARTIAL_HORIZON"
    UNRESOLVED = "UNRESOLVED"
    UNMODELED_CRITICAL_DEPENDENCY = "UNMODELED_CRITICAL_DEPENDENCY"
    FAILED_INFRASTRUCTURE = "FAILED_INFRASTRUCTURE"


class CoverageStatus(StrEnum):
    """§9.2 possibility inventory statuses. NOT_YET_DISCOVERED is a warning, never an item."""

    DISCOVERED_UNSIMULATED = "DISCOVERED_UNSIMULATED"
    LOW_FIDELITY_EXPLORED = "LOW_FIDELITY_EXPLORED"
    DEPTH_SIMULATED = "DEPTH_SIMULATED"
    REJECTED_VALIDATED_CONSTRAINT = "REJECTED_VALIDATED_CONSTRAINT"
    SET_ASIDE_BUDGET = "SET_ASIDE_BUDGET"
    REOPENED_AFTER_REVISION = "REOPENED_AFTER_REVISION"


COVERAGE_UI_LABEL = {
    CoverageStatus.DISCOVERED_UNSIMULATED: "Identified, not simulated",
    CoverageStatus.LOW_FIDELITY_EXPLORED: "Exploratory preview",
    CoverageStatus.DEPTH_SIMULATED: "Detailed simulation",
    CoverageStatus.REJECTED_VALIDATED_CONSTRAINT: "Not feasible under a cited rule",
    CoverageStatus.SET_ASIDE_BUDGET: "Unexplored due to budget",
    CoverageStatus.REOPENED_AFTER_REVISION: "Reopened after model revision",
}


class FactorLabel(StrEnum):
    """§4.3 auditable factor prioritisation labels."""

    MUST_MODEL = "MUST_MODEL"
    HIGH_IMPACT_UNCERTAIN = "HIGH_IMPACT_UNCERTAIN"
    USEFUL_SECONDARY = "USEFUL_SECONDARY"
    EXPLICIT_OMISSION = "EXPLICIT_OMISSION"
    IRRELEVANT_IN_SCOPE = "IRRELEVANT_IN_SCOPE"


class PromotionState(StrEnum):
    """§6.4 Layer B promotion states."""

    DRAFT_B = "DRAFT_B"
    COMPILED_B = "COMPILED_B"
    VERIFIED_INTERNAL_B = "VERIFIED_INTERNAL_B"
    GROUNDING_REVIEWED_B = "GROUNDING_REVIEWED_B"
    REUSABLE_TEMPLATE_CANDIDATE = "REUSABLE_TEMPLATE_CANDIDATE"
    QUARANTINED = "QUARANTINED"


class RegistryStatus(StrEnum):
    """Layer A registry entry lifecycle."""

    CANDIDATE = "CANDIDATE"
    A_VALIDATED = "A_VALIDATED"
    LOCAL_HYPOTHESIS_TEMPLATE = "LOCAL_HYPOTHESIS_TEMPLATE"
    REVOKED = "REVOKED"
    QUARANTINED = "QUARANTINED"


class ClaimStatus(StrEnum):
    UNVERIFIED_EXTRACT = "UNVERIFIED_EXTRACT"
    VERIFIED = "VERIFIED"
    STALE = "STALE"
    CONFLICTED = "CONFLICTED"
    INAPPLICABLE = "INAPPLICABLE"
    HYPOTHESIS = "HYPOTHESIS"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"


class RecordType(StrEnum):
    OBSERVATION = "observation"
    OFFICIAL_RULE = "official_rule"
    ANALYST_MODEL = "analyst_model"
    EXPERT_OPINION = "expert_opinion"
    USER_STATEMENT = "user_statement"
    CONTEXTUAL_LEAD = "contextual_lead"


class ResearchNeedKind(StrEnum):
    """§5.1 different evidence standards apply to each kind."""

    INITIAL_CONDITION = "INITIAL_CONDITION"
    MECHANISM = "MECHANISM"
    FORECAST_INPUT = "FORECAST_INPUT"
    PRIVATE_FACT = "PRIVATE_FACT"


class FeatureStatus(StrEnum):
    """§7.2 epistemic status of a human-characteristic feature."""

    CONFIRMED = "CONFIRMED"
    INFERRED = "INFERRED"
    UNKNOWN = "UNKNOWN"
    HARD_CONSTRAINT = "HARD_CONSTRAINT"


class SignalType(StrEnum):
    """§12.2 interaction signal ladder."""

    EXPOSURE = "EXPOSURE"
    OVERVIEW_OPEN = "OVERVIEW_OPEN"
    DETAILED_INSPECTION = "DETAILED_INSPECTION"
    TRACE_INSPECTION = "TRACE_INSPECTION"
    COMPARISON = "COMPARISON"
    EXPLICIT_SELECTION = "EXPLICIT_SELECTION"
    EXPLICIT_CORRECTION = "EXPLICIT_CORRECTION"
    ACTUAL_FOLLOW_THROUGH = "ACTUAL_FOLLOW_THROUGH"


IMPLICIT_SIGNALS = frozenset(
    {
        SignalType.EXPOSURE,
        SignalType.OVERVIEW_OPEN,
        SignalType.DETAILED_INSPECTION,
        SignalType.TRACE_INSPECTION,
        SignalType.COMPARISON,
    }
)


class DeadlineDisclosure(StrEnum):
    """§7.4 treatment arms. Computational caps are never disclosed as deadlines."""

    DEADLINE_AWARE = "DEADLINE_AWARE"
    DEADLINE_UNDISCLOSED = "DEADLINE_UNDISCLOSED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class FidelityMode(StrEnum):
    SCREENING = "screening"
    DEPTH = "depth"
    SENSITIVITY = "sensitivity"
    REPLAY = "replay"


class Role(StrEnum):
    """Service identities with distinct write authority (§2.2, §14.6)."""

    EVIDENCE_ADAPTER = "evidence_adapter"
    INGESTION = "ingestion_service"
    CURATOR = "curator"
    DECISION_OWNER = "decision_owner"
    RESEARCH_PLANNER = "research_planner"
    MODEL_FACTORY = "model_factory"
    ORCHESTRATOR = "orchestrator"
    RUN_SCHEDULER = "run_scheduler"
    SIMULATION_WORKER = "simulation_worker"
    HUMAN_EVIDENCE_ENDPOINT = "human_evidence_endpoint"
    PROFILE_UPDATER = "profile_updater"
    API = "api"
    AUDITOR = "auditor"

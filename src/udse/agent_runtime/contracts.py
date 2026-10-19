"""Contracts between the temporal kernel and Agent policies (§7.3).

Agents receive a redacted observation and an eligible-action schema, and return a structured
ActionIntent. They never receive the authoritative world state, other people's private profile
features, or engineering budgets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Protocol

from ..model_ir.schemas import ActionIntent, FeatureOverlay, ProfileFeature

WAIT = "WAIT"


@dataclass
class EligibleAction:
    action_type: str
    description: str = ""
    args: list[dict[str, Any]] = field(default_factory=list)  # [{name, type, options, unit}]
    tags: list[str] = field(default_factory=list)
    governance: dict[str, Any] | None = None  # {mechanism, act, option_arg, open_proposal}
    appraisal_dims: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type, "description": self.description, "args": self.args,
            "tags": self.tags, "governance": self.governance, "appraisal_dims": self.appraisal_dims,
        }


@dataclass
class DecisionContext:
    run_id: str
    agent_id: str
    display_name: str
    sim_now: datetime
    wakeup_reason: str
    observation: dict[str, Any]
    eligible: list[EligibleAction]
    features: list[ProfileFeature]
    overlays: list[FeatureOverlay]
    appraise: Callable[[str, dict[str, Any]], dict[str, Any]]
    governance_status: list[dict[str, Any]]
    deadline_disclosed: bool
    objectives: list[str]
    agent_state: dict[str, Any]
    seed: int
    notices: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AgentDecision:
    intent: ActionIntent
    policy_kind: str
    usage: dict[str, int] = field(default_factory=dict)
    model_record: dict[str, Any] | None = None
    belief_updates: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "intent": self.intent.model_dump(mode="json"),
            "policy_kind": self.policy_kind,
            "usage": self.usage,
            "model_record": self.model_record,
            "belief_updates": self.belief_updates,
            "trace": self.trace,
        }


class AgentRuntime(Protocol):
    def decide(self, ctx: DecisionContext) -> AgentDecision: ...

    def estimate_tokens(self, agent_id: str) -> int: ...

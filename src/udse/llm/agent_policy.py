"""LLM Agent policy (§7.3, §7.8): one structured ActionIntent per decision invocation.

The prompt contains only the Agent's redacted observation, its own selected profile features and
the eligible-action schema — never other people's private features or engineering budgets. The
call is marked as tainted by simulation (observations are Layer C content). If the model is
unavailable or declines, the individualised deterministic policy decides and the fallback is
recorded.
"""

from __future__ import annotations

import json
from typing import Any

from ..agent_runtime.contracts import WAIT, AgentDecision, DecisionContext
from ..agent_runtime.policies import ProfileUtilityPolicy
from ..model_ir.expr import value_to_display
from ..model_ir.schemas import ActionIntent
from .provider import LLMCall, LLMOutputError, LLMProvider, LLMRefused, LLMUnavailable

SYSTEM = (
    "You are simulating one specific person as a decision Agent inside a hypothetical simulation. Act from this person's "
    "own stated features and what they can observe. Choose exactly one eligible action or WAIT. Do not invent facts, do not "
    "assume others' private preferences, and treat UNKNOWN as unknown. A disclosed deadline is information, not an order "
    "to agree. Keep the rationale short and tied to observations and features."
)

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["action_type", "parameters_json", "rationale_summary", "uncertainty_note"],
    "properties": {
        "action_type": {"type": "string"},
        "parameters_json": {"type": "string", "description": "JSON object with the chosen action's arguments, e.g. {\"venue\": \"r_a\"}"},
        "rationale_summary": {"type": "string"},
        "uncertainty_note": {"type": "string"},
    },
}


class LLMAgentPolicy:
    kind = "llm"

    def __init__(self, llm: LLMProvider, record_sink=None, est_tokens: int = 2500):
        self.llm = llm
        self.record_sink = record_sink
        self.est = est_tokens
        self.fallback = ProfileUtilityPolicy()

    def estimate_tokens(self, agent_id: str) -> int:
        return self.est

    def decide(self, ctx: DecisionContext) -> AgentDecision:
        eligible = [e.to_json() for e in ctx.eligible]
        appraisals = {}
        for e in ctx.eligible:
            if e.args and e.args[0]["type"] == "enum" and e.appraisal_dims:
                appraisals[e.action_type] = {o: {k: value_to_display(v) for k, v in ctx.appraise(e.action_type, {e.args[0]["name"]: o}).items()}
                                             for o in e.args[0]["options"]}
        user = json.dumps({
            "you_are": ctx.display_name, "simulated_time": ctx.sim_now.isoformat(), "wakeup_reason": ctx.wakeup_reason,
            "your_features": [f.model_dump(mode="json", include={"key", "value", "status", "scope", "note"}) for f in ctx.features],
            "observation": ctx.observation, "group_process": ctx.governance_status, "eligible_actions": eligible,
            "what_you_can_estimate_per_option": appraisals, "notices": ctx.notices, "your_memory": ctx.agent_state.get("memory", [])[-5:],
        }, default=str)
        try:
            res = self.llm.complete_json(LLMCall(task="agent_decision", system=SYSTEM, user=user, schema=SCHEMA, effort="low",
                                                 max_tokens=4000, tainted_by_simulation=True, input_refs=[ctx.run_id]))
        except (LLMUnavailable, LLMRefused, LLMOutputError) as e:
            d = self.fallback.decide(ctx)
            d.policy_kind = "profile_utility_fallback"
            d.trace = {**d.trace, "llm_error": str(e)}
            return d
        if self.record_sink:
            self.record_sink(res.record)
        data = res.data
        try:
            params = json.loads(data.get("parameters_json") or "{}")
        except json.JSONDecodeError:
            params = {}
        intent = ActionIntent(run_id=ctx.run_id, agent_id=ctx.agent_id, observed_at=ctx.sim_now.isoformat(),
                              action_type=str(data.get("action_type") or WAIT), parameters=params if isinstance(params, dict) else {},
                              rationale_summary=str(data.get("rationale_summary", ""))[:600],
                              uncertainty_note=str(data.get("uncertainty_note", ""))[:400] or None)
        return AgentDecision(intent=intent, policy_kind=self.kind, usage=res.usage,
                             model_record={k: res.record[k] for k in ("call_id", "provider", "model", "prompt_hash", "output_hash")},
                             belief_updates={"note.last_rationale": intent.rationale_summary})

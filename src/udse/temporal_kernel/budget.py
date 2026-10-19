"""Per-run execution caps (§8.5). Each physical resource is capped and logged independently; none
of these is ever disclosed to Agents."""

from __future__ import annotations

import time
from typing import Any, Callable

from ..model_ir.schemas import ExecutionBudget


class BudgetTracker:
    def __init__(self, budget: ExecutionBudget, clock: Callable[[], float] = time.monotonic, enforce_wall: bool = True):
        self.budget = budget
        self.clock = clock
        self.started = clock()
        self.enforce_wall = enforce_wall
        self.tokens_in = 0
        self.tokens_out = 0
        self.model_calls = 0
        self.events = 0
        self.scheduled = 0
        self.wakeups = 0
        self.negotiation_rounds = 0

    def wall_seconds(self) -> float:
        return self.clock() - self.started

    def exhausted_reason(self) -> str | None:
        b = self.budget
        if self.enforce_wall and self.wall_seconds() >= b.max_wall_seconds:
            return f"wall-clock cap reached ({b.max_wall_seconds:g}s)"
        used = self.tokens_in + self.tokens_out
        if used > 0 and used >= b.max_model_tokens:
            return f"model token cap reached ({b.max_model_tokens})"
        if self.model_calls > 0 and self.model_calls >= b.max_model_calls:
            return f"model call cap reached ({b.max_model_calls})"
        if self.events >= b.max_events - b.reserve_events:
            return f"committed-event cap reached ({b.max_events}, {b.reserve_events} reserved for finalisation)"
        if self.scheduled >= b.max_scheduled_events:
            return f"scheduled-event cap reached ({b.max_scheduled_events})"
        if self.negotiation_rounds > b.max_negotiation_rounds:
            return f"negotiation-round cap reached ({b.max_negotiation_rounds})"
        return None

    def can_afford_decision(self, estimated_tokens: int) -> bool:
        return (
            self.tokens_in + self.tokens_out + estimated_tokens <= self.budget.max_model_tokens
            and self.model_calls < self.budget.max_model_calls
        )

    def debit_model(self, usage: dict[str, int]) -> None:
        self.tokens_in += int(usage.get("input_tokens", 0))
        self.tokens_out += int(usage.get("output_tokens", 0))
        self.model_calls += int(usage.get("calls", 0))

    def as_dict(self) -> dict[str, Any]:
        return {
            "wall_seconds": round(self.wall_seconds(), 4),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "model_calls": self.model_calls,
            "events": self.events,
            "scheduled": self.scheduled,
            "agent_wakeups": self.wakeups,
            "negotiation_rounds": self.negotiation_rounds,
            "caps": self.budget.model_dump(mode="json"),
        }

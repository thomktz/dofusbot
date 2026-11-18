"""Durable, account-scoped state for the protocol LLM goal loop."""
from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Any


@dataclass
class AgentGoalRun:
    account_id: int
    goal: str
    state: str = "running"  # running | waiting_confirmation | paused | completed | error
    steps: int = 0
    last_narration: str | None = None
    last_action: dict[str, Any] | None = None
    error: str | None = None
    updated_at: int = field(default_factory=lambda: int(time()))
    history: list[dict[str, Any]] = field(default_factory=list)

    def set_state(self, state: str, *, error: str | None = None) -> None:
        self.state, self.error, self.updated_at = state, error, int(time())
        self.history.append({"at": self.updated_at, "kind": "state", "state": state, "error": error})
        del self.history[:-40]

    def record(self, action: dict[str, Any], narration: str | None) -> None:
        self.steps += 1
        self.last_action = dict(action)
        self.last_narration = narration or None
        self.updated_at = int(time())
        self.history.append({"at": self.updated_at, "kind": "decision", "action": self.last_action,
                             "narration": self.last_narration})
        del self.history[:-40]

    def payload(self) -> dict[str, Any]:
        return {"account_id": self.account_id, "goal": self.goal, "state": self.state,
                "steps": self.steps, "last_narration": self.last_narration,
                "last_action": self.last_action, "error": self.error,
                "updated_at": self.updated_at, "history": list(self.history)}

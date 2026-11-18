"""One local-model scheduler shared by independent Dofus accounts."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from .base import LLMClient, LLMResponse


@dataclass
class PlannerQueueStatus:
    active_account_id: int | None = None
    queued: int = 0
    completed: int = 0
    last_wait_ms: float | None = None

    def to_json(self) -> dict[str, int | float | None]:
        return {"active_account_id": self.active_account_id, "queued": self.queued,
                "completed": self.completed, "last_wait_ms": self.last_wait_ms}


class SharedLLMCoordinator:
    """Serialise account-scoped planner calls through one loaded local model.

    Account state remains independent; only inference capacity is shared. This
    prevents Ollama from loading a 4B model once per account and makes queued
    work observable to the dashboard.
    """

    def __init__(self, llm: LLMClient):
        self.llm = llm
        self._lock = asyncio.Lock()
        self.status = PlannerQueueStatus()

    async def chat_json(self, account_id: int, prompt: str, **kwargs: Any) -> LLMResponse:
        queued_at = perf_counter()
        self.status.queued += 1
        try:
            async with self._lock:
                self.status.queued -= 1
                self.status.active_account_id = int(account_id)
                self.status.last_wait_ms = round((perf_counter() - queued_at) * 1000, 1)
                scoped_prompt = (
                    f"Planner account scope: {int(account_id)}. Use only the state supplied for this account. "
                    "Never infer facts or entities from another account.\n\n" + prompt
                )
                return await self.llm.chat_json(scoped_prompt, **kwargs)
        finally:
            self.status.active_account_id = None
            self.status.completed += 1

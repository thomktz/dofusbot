"""Mock LLM: deterministic canned answers (default `{}`), records prompts."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from .base import LLMClient, LLMResponse, Timings


class MockLLM(LLMClient):
    backend = "mock"

    def __init__(self, model: str = "mock", responder: Callable[[str, int], Any] | str | dict | None = None,
                 latency_s: float = 0.0, **kw: Any):
        super().__init__(model, **kw)
        self.responder = responder
        self.latency_s = latency_s
        self.prompts: list[str] = []

    async def _chat(self, messages, images, json_mode, schema, max_tokens) -> LLMResponse:
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        if self.latency_s:
            await asyncio.sleep(self.latency_s)
        r = self.responder(prompt, len(images)) if callable(self.responder) else self.responder
        if r is None:
            r = {}
        text = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
        return LLMResponse(text=text, timings=Timings(prompt_tokens=len(prompt) // 4, eval_tokens=len(text) // 4))

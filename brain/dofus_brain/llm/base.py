"""LLM client abstraction. Backends: Ollama, MLX (mlx_vlm.server, OpenAI-compatible), Mock.

All calls are async (httpx), have a timeout, measure latency and, when the backend reports
them, load time / prompt (image) prefill / generation speed.
"""
from __future__ import annotations

import abc
import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .images import ImageInput
from .jsonparse import JSONParseError, extract_json


@dataclass
class Timings:
    total_ms: float = 0.0              # wall clock of the HTTP call
    load_ms: float | None = None       # model load (Ollama load_duration; cold start)
    prompt_tokens: int | None = None   # incl. image tokens
    prompt_ms: float | None = None     # prefill (text + image encoding)
    eval_tokens: int | None = None
    eval_ms: float | None = None
    ttft_ms: float | None = None       # time to first token (streaming)

    @property
    def prompt_tok_s(self) -> float | None:
        if self.prompt_tokens and self.prompt_ms:
            return self.prompt_tokens / (self.prompt_ms / 1000)
        return None

    @property
    def tok_s(self) -> float | None:
        if self.eval_tokens and self.eval_ms:
            return self.eval_tokens / (self.eval_ms / 1000)
        return None

    def as_dict(self) -> dict:
        d = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in self.__dict__.items()}
        d["prompt_tok_s"] = round(self.prompt_tok_s, 1) if self.prompt_tok_s else None
        d["tok_s"] = round(self.tok_s, 1) if self.tok_s else None
        return d


@dataclass
class LLMResponse:
    text: str
    timings: Timings = field(default_factory=Timings)
    data: Any = None          # parsed JSON when json mode was requested
    raw: dict | None = None
    attempts: int = 1


class LLMError(RuntimeError):
    pass


class LLMClient(abc.ABC):
    backend: str = "base"

    def __init__(self, model: str, timeout_s: float = 60.0, retries: int = 2, temperature: float = 0.1,
                 max_image_side: int = 1024):
        self.model = model
        self.timeout_s = timeout_s
        self.retries = retries
        self.temperature = temperature
        self.max_image_side = max_image_side
        self.busy = False
        self.last_latency_ms: float | None = None

    @abc.abstractmethod
    async def _chat(self, messages: list[dict], images: list[ImageInput], json_mode: bool,
                    schema: dict | None, max_tokens: int | None) -> LLMResponse: ...

    async def chat(self, prompt: str, images: list[ImageInput] | None = None, system: str | None = None,
                   json_mode: bool = False, schema: dict | None = None, max_tokens: int | None = None) -> LLMResponse:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        self.busy = True
        t0 = time.perf_counter()
        try:
            resp = await asyncio.wait_for(self._chat(messages, list(images or []), json_mode, schema, max_tokens),
                                          timeout=self.timeout_s)
        except asyncio.TimeoutError as e:
            raise LLMError(f"{self.backend} timed out after {self.timeout_s}s") from e
        finally:
            self.busy = False
        resp.timings.total_ms = (time.perf_counter() - t0) * 1000
        self.last_latency_ms = resp.timings.total_ms
        return resp

    async def chat_json(self, prompt: str, images: list[ImageInput] | None = None, system: str | None = None,
                        schema: dict | None = None, validate: Callable[[Any], Any] | None = None,
                        max_tokens: int | None = 512) -> LLMResponse:
        """Ask for strict JSON. The schema (if any) is appended to the prompt and passed to the
        backend's structured-output feature. Invalid / unparsable answers are retried with
        the error fed back, up to `retries` extra attempts."""
        full = prompt
        if schema is not None:
            full += "\n\nRéponds UNIQUEMENT avec un objet JSON valide conforme à ce schéma (pas de texte autour):\n" \
                    + json.dumps(schema, ensure_ascii=False)
        last_err: Exception | None = None
        total_ms = 0.0
        for attempt in range(1, self.retries + 2):
            resp = await self.chat(full, images, system, json_mode=True, schema=schema, max_tokens=max_tokens)
            total_ms += resp.timings.total_ms
            try:
                data = extract_json(resp.text)
                if validate is not None:
                    data = validate(data)
                resp.data = data
                resp.attempts = attempt
                resp.timings.total_ms = total_ms
                self.last_latency_ms = total_ms
                return resp
            except (JSONParseError, ValueError, KeyError, TypeError) as e:
                last_err = e
                full = (prompt + "\n\nTa réponse précédente était invalide (" + str(e)[:200]
                        + "). Réponds UNIQUEMENT avec un objet JSON valide."
                        + ("\nSchéma: " + json.dumps(schema, ensure_ascii=False) if schema else ""))
        raise LLMError(f"no valid JSON after {self.retries + 1} attempts: {last_err}")

    async def health(self) -> dict:
        return {"ok": True}

    async def aclose(self) -> None:
        return None

"""Ollama backend: POST {url}/api/chat (non-streaming), images as base64 in the message,
`format: "json"` (or a JSON schema for structured outputs, Ollama >= 0.5).

Ollama returns durations in nanoseconds: load_duration, prompt_eval_count/duration
(prefill incl. image tokens), eval_count/duration (generation)."""
from __future__ import annotations

from typing import Any

import httpx

from .base import LLMClient, LLMError, LLMResponse, Timings
from .images import ImageInput, encode_image

NS = 1e-6  # ns -> ms


class OllamaClient(LLMClient):
    backend = "ollama"

    def __init__(self, model: str = "qwen3.5:4b", url: str = "http://127.0.0.1:11434",
                 keep_alive: str | int | None = "10m", num_ctx: int | None = 4096, think: bool | None = False,
                 **kw: Any):
        super().__init__(model, **kw)
        self.url = url.rstrip("/")
        self.keep_alive = keep_alive
        self.num_ctx = num_ctx
        self.think = think
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_s, connect=5.0))

    def _payload(self, messages: list[dict], images: list[ImageInput], json_mode: bool, schema: dict | None,
                 max_tokens: int | None, stream: bool = False) -> dict:
        msgs = [dict(m) for m in messages]
        if images:
            msgs[-1]["images"] = [encode_image(i, self.max_image_side)[0] for i in images]
        options: dict[str, Any] = {"temperature": self.temperature}
        if self.num_ctx:
            options["num_ctx"] = self.num_ctx
        if max_tokens:
            options["num_predict"] = max_tokens
        payload: dict[str, Any] = {"model": self.model, "messages": msgs, "stream": stream, "options": options}
        if json_mode:
            payload["format"] = schema if schema is not None else "json"
        if self.keep_alive is not None:
            payload["keep_alive"] = self.keep_alive
        if self.think is not None:
            payload["think"] = self.think  # qwen3-vl "thinking" variants: off for latency
        return payload

    async def _post(self, payload: dict) -> dict:
        try:
            r = await self._http.post(f"{self.url}/api/chat", json=payload)
        except httpx.HTTPError as e:
            raise LLMError(f"ollama unreachable at {self.url}: {e}") from e
        if r.status_code == 400 and "think" in payload and "think" in r.text.lower():
            payload = {k: v for k, v in payload.items() if k != "think"}  # model without thinking support
            r = await self._http.post(f"{self.url}/api/chat", json=payload)
        if r.status_code == 404:
            raise LLMError(f"ollama: model {self.model!r} not found; run `ollama pull {self.model}`")
        if r.status_code >= 400:
            raise LLMError(f"ollama HTTP {r.status_code}: {r.text[:300]}")
        return r.json()

    async def _chat(self, messages, images, json_mode, schema, max_tokens) -> LLMResponse:
        data = await self._post(self._payload(messages, images, json_mode, schema, max_tokens))
        text = (data.get("message") or {}).get("content", "")
        t = Timings(
            load_ms=data["load_duration"] * NS if data.get("load_duration") is not None else None,
            prompt_tokens=data.get("prompt_eval_count"),
            prompt_ms=data["prompt_eval_duration"] * NS if data.get("prompt_eval_duration") is not None else None,
            eval_tokens=data.get("eval_count"),
            eval_ms=data["eval_duration"] * NS if data.get("eval_duration") is not None else None,
        )
        return LLMResponse(text=text, timings=t, raw=data)

    # --- management helpers (used by the CLI / benchmark)
    async def health(self) -> dict:
        try:
            r = await self._http.get(f"{self.url}/api/version")
            return {"ok": r.status_code == 200, **(r.json() if r.status_code == 200 else {})}
        except httpx.HTTPError as e:
            return {"ok": False, "error": str(e)}

    async def list_models(self) -> list[str]:
        r = await self._http.get(f"{self.url}/api/tags")
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]

    async def running(self) -> list[dict]:
        """/api/ps: loaded models with `size` and `size_vram` (bytes)."""
        r = await self._http.get(f"{self.url}/api/ps")
        r.raise_for_status()
        return r.json().get("models", [])

    async def unload(self) -> None:
        await self._http.post(f"{self.url}/api/generate", json={"model": self.model, "keep_alive": 0})

    async def aclose(self) -> None:
        await self._http.aclose()

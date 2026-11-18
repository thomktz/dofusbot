"""MLX backend: talks to `mlx_vlm.server` (OpenAI-compatible chat completions).

    pip install -e 'brain[mlx]'
    python -m mlx_vlm.server --model mlx-community/Qwen2.5-VL-7B-Instruct-4bit --port 8080

Images are sent as data: URIs in `image_url` content parts. JSON mode: the schema is in
the prompt and `response_format` is sent when supported (ignored otherwise); the answer is
parsed robustly anyway. With stream=True we measure time-to-first-token (≈ prefill incl.
image) and generation speed from the SSE stream.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .base import LLMClient, LLMError, LLMResponse, Timings
from .images import ImageInput, encode_image


class MLXClient(LLMClient):
    backend = "mlx"

    def __init__(self, model: str = "mlx-community/Qwen2.5-VL-7B-Instruct-4bit", url: str = "http://127.0.0.1:8080",
                 path: str = "/v1/chat/completions", stream: bool = True, **kw: Any):
        super().__init__(model, **kw)
        self.url = url.rstrip("/")
        self.path = path
        self.stream = stream
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_s, connect=5.0))

    def _payload(self, messages, images, json_mode, schema, max_tokens, stream) -> dict:
        msgs = [dict(m) for m in messages]
        if images:
            parts: list[dict] = [{"type": "text", "text": msgs[-1]["content"]}]
            for i in images:
                b64, _ = encode_image(i, self.max_image_side)
                parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
            msgs[-1]["content"] = parts
        p: dict[str, Any] = {"model": self.model, "messages": msgs, "temperature": self.temperature,
                             "max_tokens": max_tokens or 512, "stream": stream}
        if json_mode:
            p["response_format"] = {"type": "json_object"}
        return p

    async def _send(self, payload: dict) -> httpx.Response:
        url = f"{self.url}{self.path}"
        r = await self._http.post(url, json=payload)
        if r.status_code == 404 and self.path.startswith("/v1"):
            self.path = self.path[3:]  # older servers expose /chat/completions only
            r = await self._http.post(f"{self.url}{self.path}", json=payload)
        if r.status_code == 422 and "response_format" in payload:
            payload = {k: v for k, v in payload.items() if k != "response_format"}
            r = await self._http.post(f"{self.url}{self.path}", json=payload)
        return r

    async def _chat(self, messages, images, json_mode, schema, max_tokens) -> LLMResponse:
        if self.stream:
            return await self._chat_stream(messages, images, json_mode, schema, max_tokens)
        payload = self._payload(messages, images, json_mode, schema, max_tokens, False)
        try:
            r = await self._send(payload)
        except httpx.HTTPError as e:
            raise LLMError(f"mlx server unreachable at {self.url}: {e}") from e
        if r.status_code >= 400:
            raise LLMError(f"mlx HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        text = data["choices"][0]["message"]["content"]
        u = data.get("usage") or {}
        t = Timings(prompt_tokens=u.get("prompt_tokens") or u.get("input_tokens"),
                    eval_tokens=u.get("completion_tokens") or u.get("output_tokens"))
        if u.get("prompt_tps") and t.prompt_tokens:
            t.prompt_ms = t.prompt_tokens / u["prompt_tps"] * 1000
        if u.get("generation_tps") and t.eval_tokens:
            t.eval_ms = t.eval_tokens / u["generation_tps"] * 1000
        return LLMResponse(text=text, timings=t, raw=data)

    async def _chat_stream(self, messages, images, json_mode, schema, max_tokens) -> LLMResponse:
        payload = self._payload(messages, images, json_mode, schema, max_tokens, True)
        url = f"{self.url}{self.path}"
        t0 = time.perf_counter()
        ttft = None
        chunks: list[str] = []
        n_chunks = 0
        usage: dict = {}
        try:
            async with self._http.stream("POST", url, json=payload) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode(errors="replace")
                    if r.status_code in (404, 422):
                        self.stream = False  # fall back to non-streaming
                        return await self._chat(messages, images, json_mode, schema, max_tokens)
                    raise LLMError(f"mlx HTTP {r.status_code}: {body[:300]}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        ev = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    usage = ev.get("usage") or usage
                    for ch in ev.get("choices", []):
                        piece = (ch.get("delta") or {}).get("content") or (ch.get("message") or {}).get("content") or ""
                        if piece:
                            if ttft is None:
                                ttft = (time.perf_counter() - t0) * 1000
                            chunks.append(piece)
                            n_chunks += 1
        except httpx.HTTPError as e:
            raise LLMError(f"mlx server unreachable at {self.url}: {e}") from e
        end = (time.perf_counter() - t0) * 1000
        t = Timings(ttft_ms=ttft, prompt_tokens=usage.get("prompt_tokens") or usage.get("input_tokens"),
                    eval_tokens=usage.get("completion_tokens") or usage.get("output_tokens") or n_chunks)
        if ttft is not None:
            t.prompt_ms = ttft
            t.eval_ms = max(1e-3, end - ttft)
        return LLMResponse(text="".join(chunks), timings=t, raw={"usage": usage})

    async def health(self) -> dict:
        for p in ("/health", "/v1/models", "/models"):
            try:
                r = await self._http.get(f"{self.url}{p}")
                if r.status_code == 200:
                    return {"ok": True, "endpoint": p}
            except httpx.HTTPError as e:
                return {"ok": False, "error": str(e)}
        return {"ok": False}

    async def aclose(self) -> None:
        await self._http.aclose()

"""LLM clients. `make_llm(cfg)` builds the backend from config/llm.json."""
from __future__ import annotations

from typing import Any

from .base import LLMClient, LLMError, LLMResponse, Timings  # noqa: F401
from .jsonparse import JSONParseError, extract_json  # noqa: F401
from .mock import MockLLM
from .prompts import load_prompt  # noqa: F401


def make_llm(cfg: dict[str, Any], backend: str | None = None, model: str | None = None) -> LLMClient:
    b = backend or cfg.get("backend", "ollama")
    common = {"timeout_s": float(cfg.get("timeout_s", 60)), "retries": int(cfg.get("retries", 2)),
              "temperature": float(cfg.get("temperature", 0.1)), "max_image_side": int(cfg.get("max_image_side", 1024))}
    if b == "ollama":
        from .ollama import OllamaClient

        o = cfg.get("ollama", {})
        return OllamaClient(model=model or cfg.get("model", "qwen3.5:4b"), url=o.get("url", "http://127.0.0.1:11434"),
                            keep_alive=o.get("keep_alive", "10m"), num_ctx=o.get("num_ctx", 4096),
                            think=o.get("think", False), **common)
    if b == "mlx":
        from .mlx import MLXClient

        m = cfg.get("mlx", {})
        return MLXClient(model=model or m.get("model", "mlx-community/Qwen2.5-VL-7B-Instruct-4bit"),
                         url=m.get("url", "http://127.0.0.1:8080"), path=m.get("path", "/v1/chat/completions"),
                         stream=m.get("stream", True), **common)
    if b == "mock":
        return MockLLM(model=model or "mock", **common)
    raise ValueError(f"unknown llm backend {b!r}")

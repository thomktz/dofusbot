"""Prompt templates live in dofus_brain/prompts/*.txt ({placeholders} via str.format)."""
from __future__ import annotations

from functools import lru_cache

from ..paths import prompts_dir


@lru_cache(maxsize=None)
def _raw(name: str) -> str:
    return (prompts_dir() / f"{name}.txt").read_text(encoding="utf-8")


def load_prompt(name: str, **kw: object) -> str:
    t = _raw(name)
    return t.format(**kw) if kw else t

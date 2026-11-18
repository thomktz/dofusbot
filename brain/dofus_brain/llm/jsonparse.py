"""Robust JSON extraction from LLM output (code fences, prose around, trailing commas,
<think> blocks from reasoning models)."""
from __future__ import annotations

import json
import re
from typing import Any


class JSONParseError(ValueError):
    pass


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)
_TRAILING = re.compile(r",\s*([}\]])")


def _balanced(text: str, start: int) -> str | None:
    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return text[start: i + 1]
    return None


def _loads(s: str) -> Any:
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return json.loads(_TRAILING.sub(r"\1", s))


def extract_json(text: str) -> Any:
    if text is None:
        raise JSONParseError("empty response")
    t = _THINK.sub("", text).strip()
    if not t:
        raise JSONParseError("empty response")
    candidates = [t] + _FENCE.findall(t)
    for c in candidates:
        try:
            return _loads(c.strip())
        except json.JSONDecodeError:
            pass
    for i, ch in enumerate(t):
        if ch in "{[":
            blob = _balanced(t, i)
            if blob:
                try:
                    return _loads(blob)
                except json.JSONDecodeError:
                    continue
    raise JSONParseError(f"no JSON object found in: {t[:120]!r}")

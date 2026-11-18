"""Validation / normalisation of protocol §2.3 Action dicts (pure)."""
from __future__ import annotations

import math
from typing import Any

from .errors import InvalidAction
from .keys import normalize_modifiers, resolve_key

KINDS = ("click", "move", "drag", "key", "type", "scroll", "wait")
#: Keys allowed on every action besides the kind-specific ones.
COMMON_KEYS = {"kind", "reason", "cell", "exact"}
_FIELDS: dict[str, set[str]] = {
    "click": {"x", "y", "button", "double"},
    "move": {"x", "y"},
    "drag": {"x", "y", "to_x", "to_y"},
    "key": {"key", "modifiers"},
    "type": {"text"},
    "scroll": {"x", "y", "dy"},
    "wait": {"ms"},
}
MAX_TEXT = 2000
MAX_WAIT_MS = 60_000
MAX_SCROLL = 100


def _num(a: dict, key: str) -> float:
    if key not in a:
        raise InvalidAction(f"{a.get('kind')} action needs {key!r}")
    v = a[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise InvalidAction(f"{key!r} must be a number, got {v!r}")
    v = float(v)
    if not math.isfinite(v):
        raise InvalidAction(f"{key!r} must be finite")
    return v


def validate_action(action: Any, layout: str = "auto") -> dict[str, Any]:
    """Return a normalised copy of ``action`` or raise InvalidAction.

    Unknown fields are rejected (typos like ``"buton"`` would otherwise be silent).
    """
    if not isinstance(action, dict):
        raise InvalidAction(f"action must be a dict, got {type(action).__name__}")
    kind = action.get("kind")
    if kind not in KINDS:
        raise InvalidAction(f"unknown action kind {kind!r}; expected one of {KINDS}")
    extra = set(action) - COMMON_KEYS - _FIELDS[kind]
    if extra:
        raise InvalidAction(f"unexpected field(s) for {kind}: {sorted(extra)}")

    out: dict[str, Any] = {"kind": kind}
    if "reason" in action and action["reason"] is not None:
        out["reason"] = str(action["reason"])
    if "cell" in action and action["cell"] is not None:
        out["cell"] = action["cell"]
    exact = action.get("exact", False)
    if not isinstance(exact, bool):
        raise InvalidAction("'exact' must be a bool")
    out["exact"] = exact

    if kind in ("click", "move", "drag", "scroll"):
        out["x"] = _num(action, "x")
        out["y"] = _num(action, "y")
    if kind == "click":
        button = action.get("button", "left") or "left"
        if button not in ("left", "right"):
            raise InvalidAction(f"button must be 'left' or 'right', got {button!r}")
        out["button"] = button
        dbl = action.get("double", False)
        if not isinstance(dbl, bool):
            raise InvalidAction("'double' must be a bool")
        out["double"] = dbl
    elif kind == "drag":
        out["to_x"] = _num(action, "to_x")
        out["to_y"] = _num(action, "to_y")
    elif kind == "scroll":
        dy = _num(action, "dy")
        if dy != int(dy):
            raise InvalidAction("'dy' must be a whole number of lines")
        if abs(dy) > MAX_SCROLL:
            raise InvalidAction(f"'dy' out of range (|dy| <= {MAX_SCROLL})")
        out["dy"] = int(dy)
    elif kind == "key":
        key = action.get("key")
        resolve_key(key, layout)  # raises on unknown keys
        out["key"] = key
        out["modifiers"] = list(normalize_modifiers(action.get("modifiers")))
    elif kind == "type":
        text = action.get("text")
        if not isinstance(text, str):
            raise InvalidAction("'text' must be a string")
        if len(text) > MAX_TEXT:
            raise InvalidAction(f"'text' too long (> {MAX_TEXT} chars)")
        out["text"] = text
    elif kind == "wait":
        ms = _num(action, "ms")
        if ms < 0 or ms > MAX_WAIT_MS:
            raise InvalidAction(f"'ms' must be in 0..{MAX_WAIT_MS}")
        out["ms"] = ms
    return out


def action_points(a: dict[str, Any]) -> list[tuple[float, float]]:
    """Window-point coordinates an (already validated) action touches."""
    if a["kind"] in ("click", "move", "scroll"):
        return [(a["x"], a["y"])]
    if a["kind"] == "drag":
        return [(a["x"], a["y"]), (a["to_x"], a["to_y"])]
    return []


def check_in_window(a: dict[str, Any], width: float, height: float) -> None:
    """Raise InvalidAction if a point lies outside the content area (0..w, 0..h)."""
    for x, y in action_points(a):
        if not (0 <= x <= width and 0 <= y <= height):
            raise InvalidAction(
                f"point ({x:g},{y:g}) is outside the window content (0..{width:g}, 0..{height:g}); "
                "coordinates are WINDOW POINTS (protocol §1), not capture pixels"
            )

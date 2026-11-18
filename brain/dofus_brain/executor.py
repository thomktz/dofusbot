"""Executor: validates §2.3 Actions and sends them to the controller (in a worker thread,
because dofus_control does humanlike blocking moves). Every executed action is logged as a
log.entry carrying the action."""
from __future__ import annotations

import asyncio
from typing import Any, Callable

ACTION_FIELDS = {
    "click": {"x", "y"}, "move": {"x", "y"}, "drag": {"x", "y", "to_x", "to_y"},
    "key": {"key"}, "type": {"text"}, "scroll": {"x", "y", "dy"}, "wait": {"ms"},
}
KEY_NAMES = {"enter", "escape", "space", "tab", "backspace", "up", "down", "left", "right",
             *(f"f{i}" for i in range(1, 13))}
MODIFIERS = {"cmd", "ctrl", "alt", "shift"}


class InvalidAction(ValueError):
    pass


def validate_action(a: dict) -> dict:
    if not isinstance(a, dict) or a.get("kind") not in ACTION_FIELDS:
        raise InvalidAction(f"unknown action kind: {a!r}")
    missing = ACTION_FIELDS[a["kind"]] - a.keys()
    if missing:
        raise InvalidAction(f"{a['kind']} action missing {sorted(missing)}")
    if a["kind"] == "key":
        k = str(a["key"])
        if len(k) != 1 and k.lower() not in KEY_NAMES:
            raise InvalidAction(f"bad key name {k!r}")
        if not set(a.get("modifiers") or []) <= MODIFIERS:
            raise InvalidAction(f"bad modifiers {a.get('modifiers')}")
    if a["kind"] == "click" and a.get("button", "left") not in ("left", "right"):
        raise InvalidAction("button must be left|right")
    return a


def is_kill_error(e: BaseException) -> bool:
    return type(e).__name__ == "KillSwitchTripped"


def describe(a: dict) -> str:
    k = a.get("kind")
    if k in ("click", "move"):
        s = f"{k} ({a['x']:.0f},{a['y']:.0f})"
        if a.get("cell") is not None:
            s += f" cell {a['cell']}"
    elif k == "key":
        s = "key " + "+".join([*(a.get("modifiers") or []), a["key"]])
    elif k == "type":
        s = f"type {a['text']!r}"
    elif k == "drag":
        s = f"drag ({a['x']:.0f},{a['y']:.0f})->({a['to_x']:.0f},{a['to_y']:.0f})"
    elif k == "scroll":
        s = f"scroll {a['dy']} at ({a['x']:.0f},{a['y']:.0f})"
    else:
        s = f"wait {a.get('ms')}ms"
    if a.get("reason"):
        s += f" — {a['reason']}"
    return s


class Executor:
    def __init__(self, controller: Any, log: Callable[..., None]):
        self.ctl = controller
        self.log = log
        self.count = 0

    async def execute(self, action: dict) -> None:
        a = validate_action(dict(action))
        if a["kind"] == "wait":
            await asyncio.sleep(float(a["ms"]) / 1000)
        else:
            await asyncio.to_thread(self.ctl.execute, a)
        self.count += 1
        self.log("info", "brain", "executed " + describe(a), action=a)

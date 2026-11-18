"""In-process event bus between the agent and the server (log history, change events)."""
from __future__ import annotations

import logging
from collections import deque
from typing import Any, Callable

from .util import now_ms

pylog = logging.getLogger("dofus_brain")
LEVELS = ("debug", "info", "warn", "error")
SOURCES = ("brain", "planner", "llm", "perception", "control", "server", "user")


class Bus:
    def __init__(self, history: int = 200):
        self.history: deque[dict] = deque(maxlen=history)
        self._subs: list[Callable[[str, Any], None]] = []

    def subscribe(self, fn: Callable[[str, Any], None]) -> None:
        self._subs.append(fn)

    def emit(self, kind: str, payload: Any = None) -> None:
        for fn in list(self._subs):
            try:
                fn(kind, payload)
            except Exception:  # never let a subscriber break the loop
                pylog.exception("bus subscriber failed")

    def log(self, level: str, source: str, message: str, action: dict | None = None) -> dict:
        level = level if level in LEVELS else "info"
        source = source if source in SOURCES else "brain"
        e: dict[str, Any] = {"ts": now_ms(), "level": level, "source": source, "message": message}
        if action is not None:
            e["action"] = action
        self.history.append(e)
        getattr(pylog, "warning" if level == "warn" else level)("[%s] %s", source, message)
        self.emit("log", e)
        return e

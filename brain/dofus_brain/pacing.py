"""Capture pacing (Thomas, 9 Nov): sample the game at 1 fps at most, and after an action
(a move or a spell click) wait 2-3 s before the next capture so the animation settles.

`PacedController` wraps any controller with the control API (dofus_control.Controller,
MockController, ReplayController). capture() blocks until the next capture is allowed;
everything else passes straight through. The replay uses the same wrapper, so it skips
frames exactly like the live loop would.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

DEFAULT_MIN_INTERVAL_S = 1.0          # 1 fps at most
DEFAULT_SETTLE_S = {"click": 2.5}     # after a click (move, cast, FIN DE TOUR, FERMER); keys: no extra wait


class PacedController:
    def __init__(self, inner: Any, min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
                 settle_s: dict[str, float] | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.inner = inner
        self.min_interval_s = float(min_interval_s)
        self.settle_s = dict(DEFAULT_SETTLE_S if settle_s is None else settle_s)
        self.clock = clock
        self.sleep = sleep
        self.last_capture: float | None = None
        self.not_before = 0.0               # earliest next capture after the last action
        self.waits: list[float] = []         # how long each capture waited (tests, logs)
        self._lock = threading.Lock()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def next_capture_at(self) -> float:
        t = self.not_before
        if self.last_capture is not None:
            t = max(t, self.last_capture + self.min_interval_s)
        return t

    def capture(self):
        with self._lock:
            wait = self.next_capture_at() - self.clock()
            if wait > 0:
                self.sleep(wait)
            self.waits.append(max(0.0, wait))
            self.last_capture = self.clock()
        return self.inner.capture()

    def execute(self, action: dict) -> None:
        self.inner.execute(action)
        settle = float(self.settle_s.get(action.get("kind", ""), 0.0))
        if settle > 0:
            with self._lock:
                self.not_before = max(self.not_before, self.clock() + settle)

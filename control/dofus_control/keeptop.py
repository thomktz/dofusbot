"""keep_on_top: best-effort "always on top" for another app's window.

macOS has no public API that lets one process pin *another* process's window
above everything (window levels belong to the owning app; CGSSetWindowLevel and
friends are private). What we do instead: a background thread wakes up every
~1 s, checks whether the target window is covered by another app's window (or,
optionally, simply not frontmost) and, if so, re-activates the target app
(NSRunningApplication.activateWithOptions_ + AXRaise on the window).

It never steals focus while:
  * paused via ``pause(seconds)`` (the brain calls this when the user focuses
    the dashboard's goal box),
  * the user typed on the keyboard less than ``typing_grace_s`` seconds ago,
  * the kill switch is tripped (the user wants control back).

Pure threading code; the macOS calls are injected.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)


class KeepOnTop:
    def __init__(
        self,
        tick: Callable[[], bool],
        is_suppressed: Callable[[], bool] = lambda: False,
        interval: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        """``tick()`` does one check-and-raise and returns True if it raised the window."""
        self.tick = tick
        self.is_suppressed = is_suppressed
        self.interval = interval
        self.clock = clock
        self._paused_until = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.raises = 0
        self.last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def pause(self, seconds: float) -> None:
        with self._lock:
            self._paused_until = max(self._paused_until, self.clock() + max(0.0, seconds))

    def resume(self) -> None:
        with self._lock:
            self._paused_until = 0.0

    @property
    def paused(self) -> bool:
        return self.clock() < self._paused_until

    def step(self) -> bool:
        """One iteration (public for tests). Returns True if the window was raised."""
        if self.paused:
            return False
        try:
            if self.is_suppressed():
                return False
            raised = bool(self.tick())
            if raised:
                self.raises += 1
            self.last_error = None
            return raised
        except Exception as e:  # keep the thread alive no matter what
            self.last_error = str(e)
            log.debug("keep_on_top tick failed: %s", e)
            return False

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.step()

    def start(self) -> None:
        if self.enabled:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="keep-on-top", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        self._thread = None
        if t is not None and t is not threading.current_thread():
            t.join(timeout=2.0)

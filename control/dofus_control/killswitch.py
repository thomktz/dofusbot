"""Kill switch logic (protocol §4). Pure Python + threading; no macOS imports.

Triggers (any one):
  * ``kill()`` (also what ``steer.kill`` ends up calling),
  * the global hotkey ctrl+alt+cmd+K (the macOS event tap lives in
    ``macos/events.py`` and calls ``KillSwitch.trip``; a key-state poller here is
    a fallback when the tap cannot be created),
  * the real user moving the mouse more than ``user_move_threshold`` (40 pt)
    away from where control last put it *while an action runs*,
  * the mouse slammed into the top-left corner of a display.

Once tripped, ``check()`` raises KillSwitchTripped until ``reset()``.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterable

from .errors import KillSwitchTripped
from .geometry import distance, point_in_corner
from .types import Rect

log = logging.getLogger(__name__)

Point = tuple[float, float]


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    L2 = dx * dx + dy * dy
    if L2 <= 1e-12:
        return distance(p, a)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return distance(p, (ax + t * dx, ay + t * dy))


class KillSwitch:
    def __init__(self, user_move_threshold: float = float("inf")):   # off (Thomas: unusable)
        self.user_move_threshold = float(user_move_threshold)
        self._lock = threading.RLock()
        self._tripped = threading.Event()
        self._reason: str | None = None
        self._callbacks: list[Callable[[str], None]] = []
        self._action_depth = 0
        self._expected: Point | None = None
        self._prev_expected: Point | None = None

    # -- state ----------------------------------------------------------------
    @property
    def tripped(self) -> bool:
        return self._tripped.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    def on_kill(self, callback: Callable[[str], None]) -> None:
        """Register ``callback(reason)``; called from whatever thread trips the switch."""
        with self._lock:
            self._callbacks.append(callback)

    def trip(self, reason: str = "kill()") -> bool:
        """Trip the switch. Returns True if this call tripped it (False if already tripped)."""
        with self._lock:
            if self._tripped.is_set():
                return False
            self._reason = reason
            self._tripped.set()
            callbacks = list(self._callbacks)
        log.warning("KILL SWITCH: %s", reason)
        for cb in callbacks:
            try:
                cb(reason)
            except Exception:  # never let a callback break the trip
                log.exception("kill callback failed")
        return True

    def reset(self) -> None:
        with self._lock:
            self._tripped.clear()
            self._reason = None

    def check(self) -> None:
        if self._tripped.is_set():
            raise KillSwitchTripped(self._reason or "tripped")

    def wait(self, timeout: float | None = None) -> bool:
        """Block until tripped (or timeout). Returns tripped state."""
        return self._tripped.wait(timeout)

    # -- action tracking (user mouse movement detection) ------------------------
    @property
    def action_running(self) -> bool:
        return self._action_depth > 0

    @property
    def expected_mouse(self) -> Point | None:
        return self._expected

    @contextmanager
    def action(self, mouse_now: Point | None):
        """Mark an action as running; baseline the expected mouse position."""
        with self._lock:
            self._action_depth += 1
            if mouse_now is not None:
                self._expected = (float(mouse_now[0]), float(mouse_now[1]))
                self._prev_expected = self._expected
        try:
            yield self
        finally:
            with self._lock:
                self._action_depth -= 1

    def set_expected_mouse(self, pt: Point) -> None:
        """Called by the input layer right before it posts a mouse event at ``pt``.

        The previous expected point is kept too: while the cursor travels between
        the two, any point on that segment counts as "where we put it".
        """
        with self._lock:
            self._prev_expected = self._expected
            self._expected = (float(pt[0]), float(pt[1]))

    def check_user_mouse(self, actual: Point | None) -> None:
        """Trip if the real cursor drifted > threshold from where we put it (during an action)."""
        if actual is None or not self.action_running or self._expected is None:
            return
        with self._lock:
            a, b = self._prev_expected or self._expected, self._expected
        d = point_segment_distance(actual, a, b)
        if d > self.user_move_threshold:
            self.trip(f"user moved the mouse ({d:.0f} pt from expected position)")


class KillSwitchMonitor:
    """Background poller: corner slam, user mouse movement, hotkey key-state fallback.

    ``get_mouse()`` -> (x, y) screen points; ``get_displays()`` -> display bounds;
    ``hotkey_down()`` -> bool (optional, used when no event tap is available).
    """

    def __init__(
        self,
        killswitch: KillSwitch,
        get_mouse: Callable[[], Point | None],
        get_displays: Callable[[], Iterable[Rect]],
        hotkey_down: Callable[[], bool] | None = None,
        interval: float = 0.02,
        corner_zone: float = 3.0,
        corner_anytime: bool = True,
    ):
        self.ks = killswitch
        self.get_mouse = get_mouse
        self.get_displays = get_displays
        self.hotkey_down = hotkey_down
        self.interval = interval
        self.corner_zone = corner_zone
        self.corner_anytime = corner_anytime
        self._displays: list[Rect] = []
        self._displays_at = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> None:
        now = time.monotonic()
        if now - self._displays_at > 2.0 or not self._displays:
            try:
                self._displays = list(self.get_displays())
            except Exception:
                log.debug("get_displays failed", exc_info=True)
            self._displays_at = now
        try:
            pos = self.get_mouse()
        except Exception:
            pos = None
        if pos is not None:
            ours = (
                self.ks.action_running
                and self.ks.expected_mouse is not None
                and distance(pos, self.ks.expected_mouse) < 1.0
            )
            if (self.corner_anytime or self.ks.action_running) and not ours:
                if point_in_corner(pos[0], pos[1], self._displays, self.corner_zone):
                    self.ks.trip("mouse slammed into the top-left screen corner")
            self.ks.check_user_mouse(pos)
        if self.hotkey_down is not None:
            try:
                if self.hotkey_down():
                    self.ks.trip("hotkey ctrl+alt+cmd+K")
            except Exception:
                log.debug("hotkey_down failed", exc_info=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.interval)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="killswitch-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

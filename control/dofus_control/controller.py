"""Controller: the protocol §4 API. Platform-independent logic over a Backend.

On macOS the backend is ``dofus_control.macos.backend.MacBackend`` (imported
lazily). Anywhere else, constructing a Controller without an explicit backend
raises PlatformNotSupported (use ``dofus_brain.mock_control`` there).
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import replace
from typing import Any, Callable

from .actions import check_in_window, validate_action
from .backend import Backend
from .driver import InputDriver
from .errors import (
    AlignmentFailed, CalibrationFailed, InvalidAction, PermissionMissing,
    PlatformNotSupported, WindowCovered, WindowMoved, WindowNotFound, WindowNotFrontmost,
)
from .geometry import (
    DEFAULT_TITLE_BAR_HEIGHT, clamp_to_rect, content_from_frame, covering_windows, crop_content,
    frame_for_content, matches, point_in_corner, select_window, window_to_screen,
)
from .human import HumanProfile
from .keeptop import KeepOnTop
from .killswitch import KillSwitch, KillSwitchMonitor
from .markers import SlotDetection, find_slot
from .types import (
    DEFAULT_MARKERS, AlignResult, DisplayInfo, Frame, MarkerSpec, PermissionStatus,
    RawWindow, Rect, WindowInfo,
)

log = logging.getLogger(__name__)

#: Refuse to post input this close (points) to a display's top-left corner.
CORNER_GUARD_PT = 6.0


def _default_backend() -> Backend:
    if sys.platform != "darwin":
        raise PlatformNotSupported(
            "dofus_control drives real macOS windows and only runs on macOS; "
            "use dofus_brain.mock_control (or pass backend=...) elsewhere"
        )
    from .macos.backend import MacBackend  # lazy: PyObjC only on macOS

    return MacBackend()


def _now_ms() -> int:
    return int(time.time() * 1000)


class Controller:
    """Find / align / capture / drive one app window. Thread-safe."""

    def __init__(
        self,
        app_name: str | None = "Dofus",
        title_contains: str | None = None,
        *,
        backend: Backend | None = None,
        profile: HumanProfile | None = None,
        title_bar_height: float | None = None,
        keyboard_layout: str = "auto",
        require_frontmost: bool = True,
        move_tolerance: float = 2.0,
        align_tolerance: float = 1.0,
        align_attempts: int = 3,
        user_move_threshold: float = float("inf"),   # mouse-distance kill off (Thomas, 9 Nov)
        start_kill_sources: bool = True,
        keep_on_top_interval: float = 1.0,
        keep_on_top_when_not_frontmost: bool = False,
        typing_grace_s: float = 1.5,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.app_name = app_name
        self.title_contains = title_contains
        self.b: Backend = backend if backend is not None else _default_backend()
        self.profile = profile or HumanProfile()
        self.title_bar_override = title_bar_height
        self.layout = keyboard_layout
        self.require_frontmost = require_frontmost
        self.move_tolerance = move_tolerance
        self.align_tolerance = align_tolerance
        self.align_attempts = max(1, int(align_attempts))
        self.keep_on_top_when_not_frontmost = keep_on_top_when_not_frontmost
        self.typing_grace_s = typing_grace_s
        self._sleep = sleep

        self._lock = threading.RLock()
        self._ks = KillSwitch(user_move_threshold)
        self._driver = InputDriver(self.b, self._ks, self.profile, sleep=sleep, layout=keyboard_layout)
        self._kot = KeepOnTop(self._kot_tick, self._kot_suppressed, interval=keep_on_top_interval)
        self._expected_content: Rect | None = None
        self._tb_cache: dict[int, float] = {}
        self._title_cache: dict[int, str | None] = {}
        self._handles: dict[int, Any] = {}
        self._monitor: KillSwitchMonitor | None = None
        self._hotkey_stop: Callable[[], None] | None = None
        self.hotkey_via_event_tap = False
        self.last_align: AlignResult | None = None
        self.last_calibration: SlotDetection | None = None
        self.last_window: WindowInfo | None = None
        self._closed = False
        if start_kill_sources:
            self.start_kill_sources()

    # ================================================================ permissions
    def permissions(self) -> PermissionStatus:
        """Current permission state; never prompts."""
        return self.b.permissions()

    def request_permissions(self) -> None:
        """Trigger the macOS permission prompts (Accessibility, Screen Recording, Input Monitoring)."""
        self.b.request_permissions()

    def _require(self, perm: str) -> None:
        st = self.b.permissions()
        if not getattr(st, perm):
            raise PermissionMissing(perm)

    # ================================================================ windows
    def list_windows(self) -> list[RawWindow]:
        return self.b.list_windows()

    def find_window(self) -> WindowInfo | None:
        """Largest on-screen normal-layer window of ``app_name`` (and ``title_contains``)."""
        raws = self.b.list_windows()
        raw = select_window(raws, self.app_name, self.title_contains)
        if raw is None and self.title_contains:
            # Without Screen Recording, CGWindowList omits titles: ask Accessibility.
            enriched = []
            for w in raws:
                if w.title is None and matches(w, self.app_name, None):
                    w = replace(w, title=self._ax_title(w))
                enriched.append(w)
            raw = select_window(enriched, self.app_name, self.title_contains)
        if raw is None:
            return None
        info = self._to_info(raw)
        self.last_window = info
        return info

    def focus(self) -> None:
        """Bring the selected app window to the front before a user-requested action."""
        win = self.find_window()
        if win is None:
            raise WindowNotFound(f"{self.app_name or 'target'} window not found")
        if not self.b.activate(win.pid, self._ax_handle(win.window_id, win.pid, win.frame)):
            raise WindowNotFrontmost(f"could not focus {win.app}")

    def _ax_title(self, w: RawWindow) -> str | None:
        if w.window_id not in self._title_cache:
            try:
                self._title_cache[w.window_id] = self.b.ax_title(w.pid, w.bounds)
            except Exception:
                self._title_cache[w.window_id] = None
        return self._title_cache[w.window_id]

    def _to_info(self, raw: RawWindow) -> WindowInfo:
        tb = self._title_bar_height(raw)
        title = raw.title if raw.title is not None else self._ax_title(raw)
        return WindowInfo(
            window_id=raw.window_id, pid=raw.pid, app=raw.owner, title=title,
            frame=raw.bounds, content=content_from_frame(raw.bounds, tb),
            title_bar_height=tb, layer=raw.layer, on_screen=raw.on_screen,
        )

    def _title_bar_height(self, raw: RawWindow) -> float:
        if self.title_bar_override is not None:
            return float(self.title_bar_override)
        if raw.window_id in self._tb_cache:
            return self._tb_cache[raw.window_id]
        tb = None
        try:
            h = self._ax_handle(raw.window_id, raw.pid, raw.bounds)
            if h is not None:
                tb = self.b.title_bar_height(h, raw.bounds)
        except Exception:
            log.debug("title bar detection failed", exc_info=True)
        if tb is None:
            tb = DEFAULT_TITLE_BAR_HEIGHT
            # don't cache the fallback while Accessibility is missing; retry later
            if not self.b.permissions().accessibility:
                return tb
        self._tb_cache[raw.window_id] = float(tb)
        return float(tb)

    def _ax_handle(self, window_id: int, pid: int, frame: Rect) -> Any | None:
        h = self._handles.get(window_id)
        if h is not None:
            f = self.b.ax_get_frame(h)
            if f is not None and f.approx_equal(frame, 2.0):
                return h
        h = self.b.ax_window(pid, frame)
        if h is not None:
            self._handles[window_id] = h
        return h

    # ================================================================ alignment
    def align_window(self, target: Rect, content: bool = True, strict: bool = False) -> WindowInfo:
        """Move + resize so the content area (or whole frame) equals ``target`` (screen points).

        Sets, re-reads and retries up to ``align_attempts`` times. If the app
        refuses the exact size (minimum size, fixed aspect ratio) but the
        top-left is right, the window is returned with ``aligned=False`` and a
        ``note`` (or AlignmentFailed if ``strict``). Raises AlignmentFailed when
        the position can't be reached (e.g. macOS keeps the title bar below the
        menu bar) and WindowNotFound / PermissionMissing as appropriate.
        """
        if not target.is_finite() or target.w <= 0 or target.h <= 0:
            raise AlignmentFailed(f"invalid target rect {target}")
        with self._lock:
            self._require("accessibility")
            win = self.find_window()
            if win is None:
                raise WindowNotFound(self._not_found_msg())
            tb = win.title_bar_height
            want = frame_for_content(target, tb) if content else target
            handle = self._ax_handle(win.window_id, win.pid, win.frame)
            if handle is None:
                raise AlignmentFailed(
                    f"no Accessibility window for {win.app!r} (pid {win.pid}); is it a normal window?"
                )
            # A Unity window launched by the local Zaap adapter may initially
            # honour its previous fullscreen state. AX size changes are ignored
            # in that state, so leave it before applying the validated slot.
            exit_fullscreen = getattr(self.b, "ax_exit_fullscreen", None)
            if callable(exit_fullscreen) and not exit_fullscreen(handle):
                log.debug("could not leave fullscreen before alignment")
            tol = self.align_tolerance
            history: list[Rect] = []
            got: Rect | None = None
            attempts = 0
            for attempt in range(1, self.align_attempts + 1):
                attempts = attempt
                # position, size, position: macOS may shift a window that would
                # not fit on screen at its old position after the resize
                self.b.ax_set_position(handle, want.x, want.y)
                self.b.ax_set_size(handle, want.w, want.h)
                self.b.ax_set_position(handle, want.x, want.y)
                self._sleep(0.05 * attempt)
                got = self.b.ax_get_frame(handle)
                if got is not None:
                    history.append(got)
                    if got.approx_equal(want, tol):
                        break
                    # stop early if the app clearly enforces this size
                    if len(history) >= 2 and history[-1].approx_equal(history[-2], 0.5):
                        break
            pos_ok = got is not None and abs(got.x - want.x) <= tol and abs(got.y - want.y) <= tol
            size_ok = got is not None and abs(got.w - want.w) <= tol and abs(got.h - want.h) <= tol
            note = None
            if got is None:
                note = "could not read the window frame back via Accessibility"
            elif not pos_ok:
                note = (
                    f"window top-left is at ({got.x:g},{got.y:g}), wanted ({want.x:g},{want.y:g}); "
                    "macOS keeps title bars below the menu bar and on a display"
                )
            elif not size_ok:
                note = (
                    f"app kept size {got.w:g}x{got.h:g} instead of {want.w:g}x{want.h:g} "
                    "(minimum size or fixed aspect ratio)"
                )
            self.last_align = AlignResult(want, got, attempts, pos_ok, size_ok, note, history)
            if got is None or not pos_ok or (strict and not size_ok):
                raise AlignmentFailed(note or "alignment failed")
            # Re-read through CGWindowList (what everything else uses); it can lag AX briefly.
            final = None
            for _ in range(10):
                final = self.find_window()
                if final is not None and final.frame.approx_equal(got, 1.0):
                    break
                self._sleep(0.03)
            if final is None:
                raise WindowNotFound("window disappeared during alignment")
            final.aligned = pos_ok and size_ok
            final.note = note
            self._expected_content = final.content if content else content_from_frame(final.frame, tb)
            self.last_window = final
            return final

    def forget_alignment(self) -> None:
        """Stop refusing actions when the window is not where align_window() put it."""
        self._expected_content = None

    @property
    def expected_content(self) -> Rect | None:
        return self._expected_content

    # ================================================================ keep on top
    def keep_on_top(self, enabled: bool) -> None:
        """Best effort: re-activate the app ~1x/s whenever another app's window covers it.

        See keeptop.py: true always-on-top for another process's window needs private APIs.
        """
        if enabled:
            self._kot.start()
        else:
            self._kot.stop()

    @property
    def keep_on_top_enabled(self) -> bool:
        return self._kot.enabled

    def pause_keep_on_top(self, seconds: float) -> None:
        """Don't re-activate the app for ``seconds`` (user is typing in the dashboard)."""
        self._kot.pause(seconds)

    def resume_keep_on_top(self) -> None:
        self._kot.resume()

    def _kot_suppressed(self) -> bool:
        if self._ks.tripped:
            return True
        try:
            return self.b.seconds_since_keyboard_input() < self.typing_grace_s
        except Exception:
            return False

    def _kot_tick(self) -> bool:
        win = self.find_window()
        if win is None:
            return False
        raws = self.b.list_windows()
        cover = covering_windows(raws, win.window_id, win.content)
        front = self.b.frontmost_pid()
        if cover or (self.keep_on_top_when_not_frontmost and front != win.pid):
            log.debug("keep_on_top: raising %s (covered by %s)", win.app, [w.owner for w in cover])
            self.b.activate(win.pid, self._ax_handle(win.window_id, win.pid, win.frame))
            return True
        return False

    # ================================================================ capture
    def _display_for(self, rect: Rect) -> DisplayInfo | None:
        best, best_area = None, 0.0
        for d in self.b.displays():
            inter = d.bounds.intersection(rect)
            a = inter.area if inter else 0.0
            if a > best_area:
                best, best_area = d, a
        return best

    def capture(self) -> Frame:
        """Capture the window's content area (works while covered, not while minimised)."""
        with self._lock:
            win = self.find_window()
            if win is None:
                raise WindowNotFound(self._not_found_msg())
            img = self.b.capture_window(win.window_id)
            disp = self._display_for(win.frame)
            crop, scale = crop_content(img, win.frame, win.content, disp.scale if disp else None)
            return Frame(image=crop, scale=scale, ts=_now_ms(), window=win)

    def capture_holding(self, key: str, settle_ms: float = 250.0) -> Frame:
        """Hold ``key`` in the target window, wait ``settle_ms`` for the game to redraw,
        capture the content area, then release the key (released even on errors or a
        kill). Used for hold-to-show keys such as Dofus' interactive / entity overlays."""
        self._ks.check()
        validate_action({"kind": "key", "key": key}, self.layout)
        if not 0 <= settle_ms <= 2000:
            raise InvalidAction("settle_ms must be in 0..2000")
        with self._lock:
            self._prepare_window()
            with self._ks.action(self.b.mouse_position()):
                with self._driver.hold_key(key):
                    self._driver.sleep_ms(settle_ms)
                    self._ks.check()
                    win = self.find_window()
                    if win is None:
                        raise WindowNotFound(self._not_found_msg())
                    img = self.b.capture_window(win.window_id)
                    disp = self._display_for(win.frame)
                    crop, scale = crop_content(img, win.frame, win.content, disp.scale if disp else None)
                    return Frame(image=crop, scale=scale, ts=_now_ms(), window=win)

    def capture_display(self, display: int = 0, exclude_window_ids: tuple[int, ...] = ()) -> Frame:
        """Whole display ``display`` (0 = main display), capture pixels."""
        img, info = self.b.capture_display(display, tuple(exclude_window_ids))
        scale = img.shape[1] / info.bounds.w if info.bounds.w else info.scale
        return Frame(image=img, scale=float(scale), ts=_now_ms(), window=None, display_bounds=info.bounds)

    # ================================================================ calibration
    def calibrate_slot(
        self,
        markers: MarkerSpec = DEFAULT_MARKERS,
        display: int | None = None,
        tol: int = 48,
        settle_s: float = 0.6,
    ) -> Rect:
        """Protocol §3: hide the target window, find the 4 corner markers, return the slot (screen points)."""
        with self._lock:
            win = self.find_window()
            restore: Callable[[], None] | None = None
            try:
                if win is not None:
                    restore = self._get_out_of_the_way(win)
                    self._sleep(settle_s)  # let the browser repaint what was hidden
                n = len(self.b.displays())
                indices = [display] if display is not None else list(range(max(1, n)))
                errors = []
                exclude = (win.window_id,) if win is not None else ()
                for idx in indices:
                    fr = self.capture_display(idx, exclude)
                    try:
                        det = find_slot(fr.image, markers, tol=tol, scale_hint=fr.scale)
                    except CalibrationFailed as e:
                        errors.append(f"display {idx}: {e}")
                        continue
                    b = fr.display_bounds or Rect(0, 0, 0, 0)
                    rect = det.to_screen(fr.scale, (b.x, b.y))
                    if rect.w < 50 or rect.h < 50:
                        errors.append(f"display {idx}: slot too small {rect}")
                        continue
                    for wmsg in det.warnings:
                        log.warning("calibrate_slot: %s", wmsg)
                    self.last_calibration = det
                    return rect
                raise CalibrationFailed("; ".join(errors) or "no display")
            finally:
                if restore is not None:
                    try:
                        restore()
                    except Exception:
                        log.exception("could not restore the window after calibration")

    def _get_out_of_the_way(self, win: WindowInfo) -> Callable[[], None]:
        """Hide the app (preferred) or slide its window off-screen. Returns a restore function."""
        if self.b.hide_app(win.pid):
            def restore() -> None:
                self.b.unhide_app(win.pid)
                self._sleep(0.3)
                self.b.activate(win.pid, self._ax_handle(win.window_id, win.pid, win.frame))
            return restore
        handle = self._ax_handle(win.window_id, win.pid, win.frame)
        if handle is None:
            log.warning("could not hide or move %s; calibrating anyway (window excluded from capture)", win.app)
            return lambda: None
        saved = self.b.ax_get_frame(handle) or win.frame
        disp = self._display_for(win.frame)
        right = disp.bounds.right if disp else 10_000
        self.b.ax_set_position(handle, right - 20, saved.y)

        def restore_move() -> None:
            self.b.ax_set_position(handle, saved.x, saved.y)
            self.b.activate(win.pid, handle)
        return restore_move

    # ================================================================ input
    def execute(self, action: dict) -> None:
        """Run one protocol §2.3 action (window points) with humanlike timing.

        Raises KillSwitchTripped, InvalidAction, WindowNotFound, WindowMoved,
        WindowNotFrontmost, WindowCovered.
        """
        self._ks.check()
        a = validate_action(action, self.layout)
        if a["kind"] == "wait":
            self._driver.sleep_ms(a["ms"])
            return
        with self._lock:
            win = self._prepare_window()
            c = win.content
            check_in_window(a, c.w, c.h)
            local = Rect(0, 0, c.w, c.h)

            def to_screen(x: float, y: float, jitter: bool = True) -> tuple[float, float]:
                if jitter:
                    x, y = self.profile.jitter(x, y, exact=a["exact"])
                x, y = clamp_to_rect(x, y, local)
                sx, sy = window_to_screen(c, x, y)
                self._guard_point(win, sx, sy)
                return sx, sy

            kind = a["kind"]
            with self._ks.action(self.b.mouse_position()):
                if kind == "click":
                    sx, sy = to_screen(a["x"], a["y"])
                    self._driver.click(sx, sy, a["button"], a["double"])
                elif kind == "move":
                    self._driver.move(*to_screen(a["x"], a["y"]))
                elif kind == "drag":
                    sx, sy = to_screen(a["x"], a["y"])
                    tx, ty = to_screen(a["to_x"], a["to_y"])
                    self._driver.drag(sx, sy, tx, ty)
                elif kind == "scroll":
                    sx, sy = to_screen(a["x"], a["y"])
                    self._driver.scroll(sx, sy, a["dy"])
                elif kind == "key":
                    self._driver.key(a["key"], a["modifiers"])
                elif kind == "type":
                    self._driver.type_text(a["text"])
        self._driver.sleep_ms(self.profile.post_pause())

    def _prepare_window(self) -> WindowInfo:
        win = self.find_window()
        if win is None:
            raise WindowNotFound(self._not_found_msg())
        exp = self._expected_content
        if exp is not None and win.content.max_edge_delta(exp) > self.move_tolerance:
            raise WindowMoved(
                f"{win.app} content is at {win.content}, expected {exp} (moved or resized?); "
                "call align_window() again (or forget_alignment())"
            )
        if self.require_frontmost:
            front = self.b.frontmost_pid()
            if front != win.pid and self._kot.enabled and not self._kot.paused and not self._kot_suppressed():
                self.b.activate(win.pid, self._ax_handle(win.window_id, win.pid, win.frame))
                for _ in range(10):
                    self._sleep(0.05)
                    front = self.b.frontmost_pid()
                    if front == win.pid:
                        break
            if front != win.pid:
                raise WindowNotFrontmost(
                    f"{win.app} (pid {win.pid}) is not the frontmost app (frontmost pid: {front}); "
                    "input would go elsewhere. Click it or enable keep_on_top."
                )
        return win

    def _guard_point(self, win: WindowInfo, sx: float, sy: float) -> None:
        displays = [d.bounds for d in self.b.displays()]
        if point_in_corner(sx, sy, displays, CORNER_GUARD_PT):
            raise InvalidAction("refusing to move the mouse into a top-left screen corner (kill-switch zone)")
        if displays and not any(d.contains(sx, sy) for d in displays):
            raise InvalidAction(f"screen point ({sx:g},{sy:g}) is not on any display")
        covered = covering_windows(self.b.list_windows(), win.window_id, Rect(sx - 1, sy - 1, 2, 2), min_overlap=0.5)
        if covered:
            raise WindowCovered(
                f"point ({sx:g},{sy:g}) is covered by {covered[0].owner!r} "
                f"({covered[0].title or 'untitled'}); refusing to click the wrong app"
            )

    # ================================================================ kill switch
    def start_kill_sources(self) -> None:
        """Start the hotkey event tap and the corner / mouse-movement monitor."""
        if self._hotkey_stop is None:
            try:
                self._hotkey_stop = self.b.start_hotkey_listener(
                    lambda: self._ks.trip("hotkey ctrl+alt+cmd+K")
                )
            except Exception:
                log.exception("hotkey listener failed")
                self._hotkey_stop = None
            self.hotkey_via_event_tap = self._hotkey_stop is not None
            if not self.hotkey_via_event_tap:
                log.warning(
                    "ctrl+alt+cmd+K event tap unavailable (grant Input Monitoring); "
                    "falling back to key-state polling"
                )
        if self._monitor is None:
            self._monitor = KillSwitchMonitor(
                self._ks,
                get_mouse=self.b.mouse_position,
                get_displays=lambda: [d.bounds for d in self.b.displays()],
                hotkey_down=None if self.hotkey_via_event_tap else self.b.kill_hotkey_down,
            )
            self._monitor.start()

    def kill(self, reason: str = "kill()") -> None:
        self._ks.trip(reason)

    def reset_kill(self) -> None:
        self._ks.reset()

    @property
    def killed(self) -> bool:
        return self._ks.tripped

    @property
    def kill_reason(self) -> str | None:
        return self._ks.reason

    def on_kill(self, callback: Callable[[str], None]) -> None:
        """``callback(reason)`` is called from any thread when the kill switch trips."""
        self._ks.on_kill(callback)

    @property
    def killswitch(self) -> KillSwitch:
        return self._ks

    # ================================================================ misc
    def overlay_status(self, slot: Rect | None = None, calibrated: bool = False) -> dict:
        """Protocol §2.3 OverlayStatus for the current window."""
        win = self.find_window()
        frame = win.content if win else None
        return {
            "dofus_window": {
                "found": win is not None,
                "title": win.title if win else None,
                "pid": win.pid if win else None,
                "frame": frame.as_dict() if frame else None,
            },
            "slot": slot.as_dict() if slot else None,
            "aligned": bool(frame and slot and frame.approx_equal(slot, 1.0)),
            "keep_on_top": self.keep_on_top_enabled,
            "calibrated": calibrated,
        }

    def _not_found_msg(self) -> str:
        m = f"no on-screen window for app {self.app_name!r}"
        if self.title_contains:
            m += f" with title containing {self.title_contains!r}"
        return m + " (is it running, not minimised, and on the current Space?)"

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._kot.stop()
        if self._monitor is not None:
            self._monitor.stop()
            self._monitor = None
        if self._hotkey_stop is not None:
            try:
                self._hotkey_stop()
            except Exception:
                log.debug("hotkey stop failed", exc_info=True)
            self._hotkey_stop = None
        try:
            self.b.close()
        except Exception:
            log.debug("backend close failed", exc_info=True)

    def __enter__(self) -> "Controller":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

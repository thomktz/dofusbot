"""MacBackend: the Backend protocol implemented with PyObjC."""
from __future__ import annotations

import logging
from typing import Any, Callable

import numpy as np

from ..errors import CaptureFailed
from ..types import DisplayInfo, PermissionStatus, RawWindow, Rect
from . import capture, events, permissions, windows

log = logging.getLogger(__name__)


class MacBackend:
    def __init__(self) -> None:
        self._displays_cache: list[DisplayInfo] | None = None
        self._tap: events.HotkeyTap | None = None

    # -- permissions --------------------------------------------------------
    def permissions(self) -> PermissionStatus:
        return permissions.status()

    def request_permissions(self) -> None:
        permissions.request()

    # -- windows ------------------------------------------------------------
    def list_windows(self, include_offscreen: bool = False) -> list[RawWindow]:
        return windows.list_windows(include_offscreen)

    def ax_title(self, pid: int, frame: Rect) -> str | None:
        return windows.ax_title(pid, frame)

    def ax_window(self, pid: int, frame: Rect) -> Any | None:
        return windows.find_ax_window(pid, frame)

    def ax_get_frame(self, handle: Any) -> Rect | None:
        return windows.element_frame(handle)

    def ax_set_position(self, handle: Any, x: float, y: float) -> bool:
        return windows.set_position(handle, x, y)

    def ax_set_size(self, handle: Any, w: float, h: float) -> bool:
        return windows.set_size(handle, w, h)

    def ax_exit_fullscreen(self, handle: Any) -> bool:
        return windows.exit_fullscreen(handle)

    def title_bar_height(self, handle: Any, frame: Rect) -> float | None:
        return windows.title_bar_height(handle, frame)

    def frontmost_pid(self) -> int | None:
        return windows.frontmost_pid()

    def activate(self, pid: int, handle: Any | None = None) -> bool:
        return windows.activate(pid, handle)

    def hide_app(self, pid: int) -> bool:
        return windows.hide_app(pid)

    def unhide_app(self, pid: int) -> bool:
        return windows.unhide_app(pid)

    # -- displays / capture ---------------------------------------------------
    def displays(self) -> list[DisplayInfo]:
        return capture.displays()

    def _scale_for(self, rect: Rect) -> float:
        best, area = 2.0, 0.0
        for d in self.displays():
            inter = d.bounds.intersection(rect)
            if inter and inter.area > area:
                best, area = d.scale, inter.area
        return best

    def capture_window(self, window_id: int) -> np.ndarray:
        w = next((w for w in windows.list_windows(True) if w.window_id == window_id), None)
        if w is None:
            raise CaptureFailed(f"window {window_id} no longer exists")
        return capture.capture_window(window_id, w.bounds, self._scale_for(w.bounds))

    def capture_display(self, index: int, exclude_window_ids: tuple[int, ...] = ()) -> tuple[np.ndarray, DisplayInfo]:
        ds = self.displays()
        if not 0 <= index < len(ds):
            raise CaptureFailed(f"no display {index} (have {len(ds)})")
        return capture.capture_display(ds[index], exclude_window_ids), ds[index]

    # -- input ----------------------------------------------------------------
    def mouse_position(self) -> tuple[float, float]:
        return events.mouse_position()

    def post_mouse(self, kind: str, x: float, y: float, button: str = "left", click_state: int = 1) -> None:
        events.post_mouse(kind, x, y, button, click_state)

    def post_key(self, keycode: int, down: bool, flags: int = 0, char: str | None = None) -> None:
        events.post_key(keycode, down, flags, char)

    def post_text(self, text: str, down: bool) -> None:
        events.post_text(text, down)

    def post_scroll(self, x: float, y: float, lines: int) -> None:
        events.post_scroll(x, y, lines)

    def seconds_since_keyboard_input(self) -> float:
        return events.seconds_since_keyboard_input()

    def kill_hotkey_down(self) -> bool:
        return events.kill_hotkey_down()

    def start_hotkey_listener(self, on_hotkey: Callable[[], None]) -> Callable[[], None] | None:
        tap = events.HotkeyTap(on_hotkey)
        if not tap.start():
            log.warning("hotkey tap unavailable: %s", tap.error)
            tap.stop()
            return None
        self._tap = tap
        return tap.stop

    def close(self) -> None:
        if self._tap is not None:
            self._tap.stop()
            self._tap = None

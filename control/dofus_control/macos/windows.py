"""Window discovery (CGWindowList) and manipulation (Accessibility API). macOS only.

Coordinates: CGWindowList bounds and AX positions both use global screen points
with the origin at the top-left of the main display, y down. Same as protocol §1.
"""
from __future__ import annotations

import logging
from typing import Any

import AppKit
import HIServices
import Quartz

from ..geometry import parse_axvalue_description, title_bar_from_close_button
from ..types import RawWindow, Rect

log = logging.getLogger(__name__)

# AX attribute / action names (string values of the kAX* constants; using the
# literals avoids depending on which constants a PyObjC version exports).
AX_WINDOWS = "AXWindows"
AX_POSITION = "AXPosition"
AX_SIZE = "AXSize"
AX_TITLE = "AXTitle"
AX_ROLE = "AXRole"
AX_SUBROLE = "AXSubrole"
AX_MINIMIZED = "AXMinimized"
AX_FULLSCREEN = "AXFullScreen"
AX_CLOSE_BUTTON = "AXCloseButton"
AX_FRONTMOST = "AXFrontmost"
AX_MAIN = "AXMain"
AX_FOCUSED_APP = "AXFocusedApplication"
AX_RAISE = "AXRaise"

kAXErrorSuccess = 0
kAXValueTypeCGPoint = getattr(HIServices, "kAXValueTypeCGPoint", 1)
kAXValueTypeCGSize = getattr(HIServices, "kAXValueTypeCGSize", 2)

AX_TIMEOUT_S = 1.0


# ------------------------------------------------------------------ CGWindowList
def list_windows(include_offscreen: bool = False) -> list[RawWindow]:
    """All windows, front to back. Titles are None without Screen Recording permission."""
    opts = Quartz.kCGWindowListExcludeDesktopElements
    opts |= Quartz.kCGWindowListOptionAll if include_offscreen else Quartz.kCGWindowListOptionOnScreenOnly
    infos = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []
    out: list[RawWindow] = []
    for info in infos:
        try:
            b = info.get(Quartz.kCGWindowBounds) or {}
            rect = Rect(
                float(b.get("X", 0)), float(b.get("Y", 0)),
                float(b.get("Width", 0)), float(b.get("Height", 0)),
            )
            title = info.get(Quartz.kCGWindowName)
            out.append(
                RawWindow(
                    window_id=int(info.get(Quartz.kCGWindowNumber, 0)),
                    pid=int(info.get(Quartz.kCGWindowOwnerPID, 0)),
                    owner=str(info.get(Quartz.kCGWindowOwnerName) or ""),
                    title=str(title) if title is not None else None,
                    layer=int(info.get(Quartz.kCGWindowLayer, 0)),
                    bounds=rect,
                    alpha=float(info.get(Quartz.kCGWindowAlpha, 1.0)),
                    on_screen=bool(info.get(Quartz.kCGWindowIsOnscreen, False)),
                )
            )
        except Exception:
            log.debug("skipping unparsable window entry", exc_info=True)
    return out


# ------------------------------------------------------------------ AX helpers
def ax_app(pid: int):
    app = HIServices.AXUIElementCreateApplication(pid)
    try:
        HIServices.AXUIElementSetMessagingTimeout(app, AX_TIMEOUT_S)
    except Exception:
        pass
    return app


def ax_get(elem, attr: str) -> Any | None:
    """AXUIElementCopyAttributeValue; returns None on any AX error."""
    try:
        err, value = HIServices.AXUIElementCopyAttributeValue(elem, attr, None)
    except Exception:
        log.debug("AXUIElementCopyAttributeValue(%s) raised", attr, exc_info=True)
        return None
    if err != kAXErrorSuccess:
        return None
    return value


def ax_set(elem, attr: str, value) -> bool:
    try:
        err = HIServices.AXUIElementSetAttributeValue(elem, attr, value)
    except Exception:
        log.debug("AXUIElementSetAttributeValue(%s) raised", attr, exc_info=True)
        return False
    return err == kAXErrorSuccess


def _decode_pair(value, ax_type: int, kind: str) -> tuple[float, float] | None:
    if value is None:
        return None
    try:
        ok, res = HIServices.AXValueGetValue(value, ax_type, None)
        if ok and res is not None:
            if hasattr(res, "x"):
                return (float(res.x), float(res.y))
            if hasattr(res, "width"):
                return (float(res.width), float(res.height))
            return (float(res[0]), float(res[1]))
    except Exception:
        log.debug("AXValueGetValue failed; parsing description", exc_info=True)
    return parse_axvalue_description(str(value), kind)


def element_frame(elem) -> Rect | None:
    pos = _decode_pair(ax_get(elem, AX_POSITION), kAXValueTypeCGPoint, "point")
    size = _decode_pair(ax_get(elem, AX_SIZE), kAXValueTypeCGSize, "size")
    if pos is None or size is None:
        return None
    return Rect(pos[0], pos[1], size[0], size[1])


def set_position(elem, x: float, y: float) -> bool:
    val = HIServices.AXValueCreate(kAXValueTypeCGPoint, (float(x), float(y)))
    return ax_set(elem, AX_POSITION, val)


def set_size(elem, w: float, h: float) -> bool:
    val = HIServices.AXValueCreate(kAXValueTypeCGSize, (float(w), float(h)))
    return ax_set(elem, AX_SIZE, val)


def exit_fullscreen(elem) -> bool:
    """Return a window to normal mode before trying to apply its saved size."""
    if not ax_get(elem, AX_FULLSCREEN):
        return True
    return ax_set(elem, AX_FULLSCREEN, False)


def app_windows(pid: int) -> list:
    wins = ax_get(ax_app(pid), AX_WINDOWS)
    return list(wins) if wins else []


def find_ax_window(pid: int, frame: Rect):
    """The AX window of ``pid`` whose frame is closest to the CGWindow ``frame``."""
    best, best_d = None, float("inf")
    for w in app_windows(pid):
        try:
            HIServices.AXUIElementSetMessagingTimeout(w, AX_TIMEOUT_S)
        except Exception:
            pass
        f = element_frame(w)
        if f is None:
            continue
        d = f.max_edge_delta(frame)
        role = ax_get(w, AX_SUBROLE)
        if role is not None and str(role) != "AXStandardWindow":
            d += 5.0  # prefer standard windows on ties
        if d < best_d:
            best, best_d = w, d
    if best is not None and best_d > 40:
        log.debug("closest AX window is %.0f pt off the CG frame", best_d)
    return best


def ax_title(pid: int, frame: Rect) -> str | None:
    w = find_ax_window(pid, frame)
    if w is None:
        return None
    t = ax_get(w, AX_TITLE)
    return str(t) if t is not None else None


def title_bar_height(ax_window, frame: Rect) -> float | None:
    """Title bar height from the close button position; None if not determinable.

    A full-screen or borderless window (no close button) has no title bar: 0.
    """
    if ax_get(ax_window, AX_FULLSCREEN):
        return 0.0
    btn = ax_get(ax_window, AX_CLOSE_BUTTON)
    if btn is None:
        # Unity's normal window can be borderless even after it leaves macOS
        # fullscreen.  AX is available (we found the window), but there is no
        # close button and therefore no title-bar offset to subtract.
        return 0.0
    bf = element_frame(btn)
    wf = element_frame(ax_window) or frame
    return title_bar_from_close_button(wf, bf, standard=standard_title_bar_height())


_STD_TB: float | None = None


def standard_title_bar_height() -> float:
    """Title bar height AppKit uses for a plain titled window on this macOS version."""
    global _STD_TB
    if _STD_TB is None:
        try:
            content = AppKit.NSMakeRect(0, 0, 400, 300)
            mask = getattr(AppKit, "NSWindowStyleMaskTitled", 1)
            fr = AppKit.NSWindow.frameRectForContentRect_styleMask_(content, mask)
            _STD_TB = float(fr.size.height - 300)
            if not (10 <= _STD_TB <= 60):
                _STD_TB = 28.0
        except Exception:
            _STD_TB = 28.0
    return _STD_TB


# ------------------------------------------------------------------ apps / focus
def frontmost_pid() -> int | None:
    """PID of the focused app. AX system-wide element first (always fresh), then NSWorkspace."""
    try:
        sysw = HIServices.AXUIElementCreateSystemWide()
        app = ax_get(sysw, AX_FOCUSED_APP)
        if app is not None:
            err, pid = HIServices.AXUIElementGetPid(app, None)
            if err == kAXErrorSuccess and pid:
                return int(pid)
    except Exception:
        log.debug("AX focused app failed", exc_info=True)
    try:
        # NB: NSWorkspace only updates this while a run loop runs; it can be stale in a CLI.
        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        return int(app.processIdentifier()) if app is not None else None
    except Exception:
        return None


def running_app(pid: int):
    return AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)


def activate(pid: int, ax_window=None) -> bool:
    """Bring ``pid`` to the front: NSRunningApplication + AXFrontmost + AXRaise.

    macOS 14+ uses cooperative activation, so activateWithOptions_ called from a
    background process may be ignored; the AX route (needs Accessibility) is the
    one that reliably works, hence both.
    """
    ok = False
    try:
        app = running_app(pid)
        if app is not None:
            opts = getattr(AppKit, "NSApplicationActivateIgnoringOtherApps", 1 << 1)
            ok = bool(app.activateWithOptions_(opts)) or ok
    except Exception:
        log.debug("activateWithOptions_ failed", exc_info=True)
    try:
        ok = ax_set(ax_app(pid), AX_FRONTMOST, True) or ok
    except Exception:
        log.debug("AXFrontmost failed", exc_info=True)
    if ax_window is not None:
        try:
            ax_set(ax_window, AX_MAIN, True)
            err = HIServices.AXUIElementPerformAction(ax_window, AX_RAISE)
            ok = (err == kAXErrorSuccess) or ok
        except Exception:
            log.debug("AXRaise failed", exc_info=True)
    return ok


def hide_app(pid: int) -> bool:
    app = running_app(pid)
    return bool(app.hide()) if app is not None else False


def unhide_app(pid: int) -> bool:
    app = running_app(pid)
    return bool(app.unhide()) if app is not None else False

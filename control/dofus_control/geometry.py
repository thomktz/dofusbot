"""Pure geometry helpers: window selection, title bar, coordinate conversion, cropping.

No macOS imports: everything here is unit-tested on Linux.
"""
from __future__ import annotations

import math
import re
from typing import Iterable, Sequence

import numpy as np

from .types import RawWindow, Rect

#: Height of a standard macOS title bar (Big Sur and later, no toolbar), in points.
DEFAULT_TITLE_BAR_HEIGHT = 28.0

#: Windows smaller than this (points) are never chosen (tooltips, palettes...).
MIN_WINDOW_SIZE = 50.0

#: CGWindow layer of ordinary application windows.
NORMAL_LAYER = 0


# --------------------------------------------------------------------------- frames
def content_from_frame(frame: Rect, title_bar_height: float) -> Rect:
    """Content area of a window = frame minus the title bar at the top."""
    tb = max(0.0, min(float(title_bar_height), frame.h))
    return Rect(frame.x, frame.y + tb, frame.w, frame.h - tb)


def frame_for_content(content: Rect, title_bar_height: float) -> Rect:
    """Window frame whose content area is ``content``."""
    tb = max(0.0, float(title_bar_height))
    return Rect(content.x, content.y - tb, content.w, content.h + tb)


def window_to_screen(content: Rect, x: float, y: float) -> tuple[float, float]:
    """Window points (origin = content top-left) -> screen points."""
    return (content.x + float(x), content.y + float(y))


def screen_to_window(content: Rect, sx: float, sy: float) -> tuple[float, float]:
    return (float(sx) - content.x, float(sy) - content.y)


def clamp_to_rect(x: float, y: float, rect: Rect, margin: float = 0.5) -> tuple[float, float]:
    """Clamp a point into ``rect`` (window points: rect = (0,0,w,h))."""
    x = min(max(x, rect.x + margin), rect.right - margin)
    y = min(max(y, rect.y + margin), rect.bottom - margin)
    return (x, y)


# --------------------------------------------------------------------------- title bar
def title_bar_from_close_button(
    window_frame: Rect,
    close_button: Rect | None,
    standard: float = DEFAULT_TITLE_BAR_HEIGHT,
    snap: float = 4.0,
) -> float | None:
    """Estimate the title bar height from the AX close button ("traffic light") frame.

    The traffic lights are vertically centred in the title bar, so
    ``height ~= 2 * (button_centre_y - window_top)``. Values within ``snap`` points
    of ``standard`` are snapped to it (the estimate is only accurate to ~1 pt).
    Returns None when the result is implausible (button missing or outside the
    top 120 pt of the window).
    """
    if close_button is None or close_button.h <= 0:
        return None
    centre = close_button.y + close_button.h / 2.0 - window_frame.y
    if centre <= 0 or centre > 60:
        return None
    est = 2.0 * centre
    if abs(est - standard) <= snap:
        return float(standard)
    if est < 12 or est > 120:
        return None
    return float(round(est))


# --------------------------------------------------------------------------- window choice
def _norm(s: str | None) -> str:
    return (s or "").casefold().strip()


def matches(win: RawWindow, app_name: str | None, title_contains: str | None) -> bool:
    if app_name and _norm(app_name) not in _norm(win.owner):
        return False
    if title_contains:
        if win.title is None:
            return False
        if _norm(title_contains) not in _norm(win.title):
            return False
    return True


def candidate_windows(
    windows: Iterable[RawWindow],
    app_name: str | None,
    title_contains: str | None = None,
) -> list[RawWindow]:
    """Normal-layer, visible, big enough windows that match the app/title filters."""
    out = []
    for w in windows:
        if w.layer != NORMAL_LAYER:
            continue
        if w.alpha <= 0.0:
            continue
        if w.bounds.w < MIN_WINDOW_SIZE or w.bounds.h < MIN_WINDOW_SIZE:
            continue
        if not matches(w, app_name, title_contains):
            continue
        out.append(w)
    return out


def select_window(
    windows: Iterable[RawWindow],
    app_name: str | None,
    title_contains: str | None = None,
) -> RawWindow | None:
    """Pick the window to control: largest on-screen normal-layer match.

    An exact (case-insensitive) owner-name match beats a substring match, so
    ``app_name="Dofus"`` prefers the app "Dofus" over e.g. "Dofus Retro Helper".
    On-screen windows beat off-screen ones (minimised / other Space).
    """
    cands = candidate_windows(windows, app_name, title_contains)
    if not cands:
        return None
    want = _norm(app_name)

    def key(w: RawWindow):
        exact = 1 if (want and _norm(w.owner) == want) else 0
        return (1 if w.on_screen else 0, exact, w.bounds.area)

    return max(cands, key=key)


def frontmost_pid_from_order(windows: Sequence[RawWindow]) -> int | None:
    """Owner of the front-most normal-layer window (the list is front-to-back)."""
    for w in windows:
        if w.layer == NORMAL_LAYER and w.on_screen and w.alpha > 0 and w.bounds.area >= MIN_WINDOW_SIZE**2:
            return w.pid
    return None


def covering_windows(
    windows: Sequence[RawWindow],
    target_window_id: int,
    area: Rect,
    min_overlap: float = 4.0,
    include_layers: tuple[int, ...] = (NORMAL_LAYER,),
    ignore_pids: Iterable[int] = (),
) -> list[RawWindow]:
    """Windows that are in front of ``target_window_id`` and overlap ``area``.

    ``windows`` must be in CGWindowList order (front to back). Windows of the
    target's own process are ignored (the app's own panels are fine).
    """
    ignore = set(ignore_pids)
    target = next((w for w in windows if w.window_id == target_window_id), None)
    if target is None:
        return []
    ignore.add(target.pid)
    out = []
    for w in windows:
        if w.window_id == target_window_id:
            break
        if w.pid in ignore or w.layer not in include_layers or w.alpha <= 0.01 or not w.on_screen:
            continue
        inter = w.bounds.intersection(area)
        if inter is not None and inter.area >= min_overlap:
            out.append(w)
    return out


def point_in_corner(
    x: float, y: float, display_bounds: Iterable[Rect], zone: float = 3.0
) -> bool:
    """True when (x, y) is within ``zone`` points of the top-left corner of any display."""
    for b in display_bounds:
        if b.x - 0.5 <= x <= b.x + zone and b.y - 0.5 <= y <= b.y + zone:
            return True
    return False


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# --------------------------------------------------------------------------- capture
def crop_content(
    image: np.ndarray,
    frame: Rect,
    content: Rect,
    expected_scale: float | None = None,
) -> tuple[np.ndarray, float]:
    """Crop a whole-window capture down to the content area.

    ``image`` covers ``frame`` (screen points). Returns ``(crop, scale)`` where
    ``scale`` = capture pixels per point. If ``expected_scale`` (the display's
    backing scale) is given and the image matches ``frame * expected_scale``
    within 2 px, the exact value is used instead of the measured ratio.
    """
    if image.ndim != 3:
        raise ValueError("image must be HxWxC")
    ih, iw = image.shape[:2]
    if frame.w <= 0 or frame.h <= 0:
        raise ValueError("empty frame")
    scale = iw / frame.w
    if expected_scale and abs(iw - frame.w * expected_scale) <= 2 and abs(ih - frame.h * expected_scale) <= 2:
        scale = float(expected_scale)
    left = int(round((content.x - frame.x) * scale))
    top = int(round((content.y - frame.y) * scale))
    width = int(round(content.w * scale))
    height = int(round(content.h * scale))
    left = min(max(left, 0), iw)
    top = min(max(top, 0), ih)
    right = min(left + width, iw)
    bottom = min(top + height, ih)
    crop = np.ascontiguousarray(image[top:bottom, left:right, :3])
    return crop, float(scale)


def rgba_buffer_to_rgb(buf, width: int, height: int, bytes_per_row: int, order: str = "RGBA") -> np.ndarray:
    """Convert a raw 32-bit pixel buffer into an HxWx3 RGB uint8 array.

    ``order`` is the byte order in memory: "RGBA", "BGRA", "ARGB" or "ABGR".
    """
    arr = np.frombuffer(buf, dtype=np.uint8)
    need = bytes_per_row * height
    if arr.size < need:
        raise ValueError(f"buffer too small: {arr.size} < {need}")
    arr = arr[:need].reshape(height, bytes_per_row)[:, : width * 4].reshape(height, width, 4)
    idx = {"RGBA": (0, 1, 2), "BGRA": (2, 1, 0), "ARGB": (1, 2, 3), "ABGR": (3, 2, 1)}[order]
    return np.ascontiguousarray(arr[:, :, list(idx)])


# --------------------------------------------------------------------------- AX values
_POINT_RE = re.compile(r"x:\s*(-?[\d.]+)\s+y:\s*(-?[\d.]+)")
_SIZE_RE = re.compile(r"w:\s*(-?[\d.]+)\s+h:\s*(-?[\d.]+)")


def parse_axvalue_description(text: str, kind: str) -> tuple[float, float] | None:
    """Fallback parser for ``str(AXValueRef)``.

    macOS prints AXValues like
    ``<AXValue 0x...> {value = x:12.000000 y:34.000000 type = kAXValueCGPointType}``.
    ``kind`` is "point" or "size".
    """
    m = (_POINT_RE if kind == "point" else _SIZE_RE).search(text or "")
    if not m:
        return None
    return (float(m.group(1)), float(m.group(2)))


def parse_macos_version(text: str) -> tuple[int, int, int]:
    """'14.5' -> (14, 5, 0); '' -> (0, 0, 0)."""
    nums = [int(p) for p in re.findall(r"\d+", text or "")[:3]]
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])

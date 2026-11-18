"""Plain data types shared by the whole package (protocol §1, §2.3, §4).

Everything here is platform independent.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class Rect:
    """Axis-aligned rectangle. Units depend on context (screen points by default)."""

    x: float
    y: float
    w: float
    h: float

    # -- construction -------------------------------------------------------
    @classmethod
    def parse(cls, text: str) -> "Rect":
        """Parse ``"x,y,w,h"`` (commas or spaces)."""
        parts = [p for p in text.replace(",", " ").split() if p]
        if len(parts) != 4:
            raise ValueError(f"expected 'x,y,w,h', got {text!r}")
        x, y, w, h = (float(p) for p in parts)
        return cls(x, y, w, h)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Rect":
        return cls(float(d["x"]), float(d["y"]), float(d["w"]), float(d["h"]))

    def as_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}

    # -- geometry -----------------------------------------------------------
    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)

    @property
    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.right and self.y <= py < self.bottom

    def intersection(self, other: "Rect") -> "Rect | None":
        x0 = max(self.x, other.x)
        y0 = max(self.y, other.y)
        x1 = min(self.right, other.right)
        y1 = min(self.bottom, other.bottom)
        if x1 <= x0 or y1 <= y0:
            return None
        return Rect(x0, y0, x1 - x0, y1 - y0)

    def intersects(self, other: "Rect") -> bool:
        return self.intersection(other) is not None

    def translated(self, dx: float, dy: float) -> "Rect":
        return Rect(self.x + dx, self.y + dy, self.w, self.h)

    def edge_deltas(self, other: "Rect") -> tuple[float, float, float, float]:
        """Absolute difference of left, top, right, bottom edges."""
        return (
            abs(self.x - other.x),
            abs(self.y - other.y),
            abs(self.right - other.right),
            abs(self.bottom - other.bottom),
        )

    def max_edge_delta(self, other: "Rect") -> float:
        return max(self.edge_deltas(other))

    def approx_equal(self, other: "Rect", tol: float = 1.0) -> bool:
        return self.max_edge_delta(other) <= tol

    def is_finite(self) -> bool:
        return all(math.isfinite(v) for v in (self.x, self.y, self.w, self.h))

    def __str__(self) -> str:  # compact, used by the CLI
        return f"({self.x:g},{self.y:g} {self.w:g}x{self.h:g})"


@dataclass(frozen=True)
class RawWindow:
    """One entry of CGWindowListCopyWindowInfo, already converted to Python."""

    window_id: int
    pid: int
    owner: str
    title: str | None
    layer: int
    bounds: Rect           # whole window, screen points
    alpha: float = 1.0
    on_screen: bool = True


@dataclass
class WindowInfo:
    """Protocol §4 window description (plus a few extras)."""

    window_id: int
    pid: int
    app: str
    title: str | None
    frame: Rect                     # whole window, screen points
    content: Rect                   # content area (below the title bar), screen points
    title_bar_height: float = 28.0
    layer: int = 0
    on_screen: bool = True
    aligned: bool | None = None     # set by align_window()
    note: str | None = None         # human readable remark from align_window()

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["frame"] = self.frame.as_dict()
        d["content"] = self.content.as_dict()
        return d


@dataclass
class Frame:
    """A captured image (protocol §4).

    ``image`` is HxWx3 uint8 RGB in capture pixels. ``scale`` = capture pixels per point.
    ``ts`` is milliseconds since the epoch (same convention as protocol §2 ``ts``).
    """

    image: np.ndarray
    scale: float
    ts: int
    window: WindowInfo | None = None
    display_bounds: Rect | None = None   # set by capture_display (screen points)

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])


@dataclass(frozen=True)
class PermissionStatus:
    accessibility: bool
    screen_recording: bool
    # Listen-only keyboard event taps (the kill hotkey) need "Input Monitoring" on
    # macOS 10.15+. None = unknown.
    input_monitoring: bool | None = None

    @property
    def ok(self) -> bool:
        return self.accessibility and self.screen_recording

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarkerSpec:
    """Slot corner markers (protocol §3)."""

    tl: tuple[int, int, int] = (0xFF, 0x00, 0xFF)
    tr: tuple[int, int, int] = (0x00, 0xFF, 0xFF)
    bl: tuple[int, int, int] = (0xFF, 0xFF, 0x00)
    br: tuple[int, int, int] = (0x00, 0xFF, 0x00)
    size_css: float = 12.0

    def colours(self) -> dict[str, tuple[int, int, int]]:
        return {"tl": self.tl, "tr": self.tr, "bl": self.bl, "br": self.br}


DEFAULT_MARKERS = MarkerSpec()


@dataclass
class DisplayInfo:
    index: int
    display_id: int
    bounds: Rect            # screen points (global, top-left of main display = 0,0)
    scale: float            # pixels per point
    is_main: bool = False


@dataclass
class AlignResult:
    """Detailed outcome of an alignment attempt (kept on ``Controller.last_align``)."""

    requested_frame: Rect
    actual_frame: Rect | None
    attempts: int
    position_ok: bool
    size_ok: bool
    note: str | None = None
    history: list[Rect] = field(default_factory=list)

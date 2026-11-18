"""Slot corner-marker detection (protocol §3). Pure numpy, unit-tested on Linux.

The webapp draws four 12x12 CSS px squares *inside* the slot corners:
``#FF00FF`` top-left, ``#00FFFF`` top-right, ``#FFFF00`` bottom-left,
``#00FF00`` bottom-right. Given a display capture (RGB, capture pixels) we find
each colour's connected blobs, keep square-ish solid ones, pick the combination
of four that forms a consistent rectangle, and return the slot rectangle:
top-left = TL marker's top-left pixel, bottom-right = BR marker's bottom-right.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np

from .errors import CalibrationFailed
from .types import DEFAULT_MARKERS, MarkerSpec, Rect


@dataclass(frozen=True)
class Blob:
    x0: int   # inclusive
    y0: int
    x1: int   # exclusive
    y1: int
    count: int

    @property
    def w(self) -> int:
        return self.x1 - self.x0

    @property
    def h(self) -> int:
        return self.y1 - self.y0

    @property
    def fill(self) -> float:
        return self.count / float(max(1, self.w * self.h))

    @property
    def size(self) -> float:
        return (self.w + self.h) / 2.0


@dataclass
class SlotDetection:
    rect_px: Rect                 # slot rect in capture pixels (relative to the image)
    markers: dict[str, Blob]
    marker_px: float              # mean marker side, pixels
    misalignment_px: float        # worst edge disagreement between markers
    warnings: list[str] = field(default_factory=list)

    def to_screen(self, scale: float, origin: tuple[float, float] = (0.0, 0.0)) -> Rect:
        """Convert to screen points: divide by display scale, add the display origin."""
        r = self.rect_px
        return Rect(origin[0] + r.x / scale, origin[1] + r.y / scale, r.w / scale, r.h / scale)


# --------------------------------------------------------------------------- masks
def colour_mask(image: np.ndarray, rgb: tuple[int, int, int], tol: int = 48) -> np.ndarray:
    """Boolean mask of pixels within ``tol`` (per channel) of ``rgb``."""
    if image.ndim != 3 or image.shape[2] < 3:
        raise ValueError("image must be HxWx3")
    img = image[:, :, :3]
    m = np.ones(img.shape[:2], dtype=bool)
    for c in range(3):
        ch = img[:, :, c].astype(np.int16)
        m &= np.abs(ch - int(rgb[c])) <= tol
    return m


def connected_components(mask: np.ndarray, max_runs: int = 300_000) -> list[Blob]:
    """4-connected components of a boolean mask, via run-length union-find.

    Efficient when the mask is sparse (a few marker squares on a full screen).
    """
    if mask.ndim != 2:
        raise ValueError("mask must be 2-D")
    rows = np.nonzero(mask.any(axis=1))[0]
    runs_y: list[int] = []
    runs_s: list[int] = []
    runs_e: list[int] = []
    for y in rows:
        row = mask[y].astype(np.int8)
        d = np.diff(np.concatenate(([0], row, [0])))
        starts = np.nonzero(d == 1)[0]
        ends = np.nonzero(d == -1)[0]
        runs_y.extend([int(y)] * len(starts))
        runs_s.extend(starts.tolist())
        runs_e.extend(ends.tolist())
        if len(runs_y) > max_runs:
            raise CalibrationFailed(
                "too many matching pixels (is the screen full of a marker colour?)"
            )
    n = len(runs_y)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # runs are ordered by row then start; sweep pairs of consecutive rows
    prev_start = 0
    prev_end = 0  # [prev_start, prev_end) = run indices of previous row
    i = 0
    while i < n:
        y = runs_y[i]
        j = i
        while j < n and runs_y[j] == y:
            j += 1
        if prev_end > prev_start and runs_y[prev_start] == y - 1:
            p = prev_start
            for k in range(i, j):
                s, e = runs_s[k], runs_e[k]
                while p < prev_end and runs_e[p] <= s:
                    p += 1
                q = p
                while q < prev_end and runs_s[q] < e:
                    union(q, k)
                    q += 1
        prev_start, prev_end = i, j
        i = j

    acc: dict[int, list[int]] = {}
    for k in range(n):
        r = find(k)
        y, s, e = runs_y[k], runs_s[k], runs_e[k]
        a = acc.get(r)
        if a is None:
            acc[r] = [s, y, e, y + 1, e - s]
        else:
            a[0] = min(a[0], s)
            a[1] = min(a[1], y)
            a[2] = max(a[2], e)
            a[3] = max(a[3], y + 1)
            a[4] += e - s
    return [Blob(*v) for v in acc.values()]


def marker_candidates(
    image: np.ndarray,
    rgb: tuple[int, int, int],
    tol: int = 48,
    min_px: int = 4,
    max_px: int = 200,
    min_fill: float = 0.7,
) -> list[Blob]:
    """Solid, roughly square blobs of colour ``rgb``, biggest first."""
    blobs = connected_components(colour_mask(image, rgb, tol))
    out = []
    for b in blobs:
        if b.w < min_px or b.h < min_px or b.w > max_px or b.h > max_px:
            continue
        if not (0.6 <= b.w / b.h <= 1.67):
            continue
        if b.fill < min_fill:
            continue
        out.append(b)
    out.sort(key=lambda b: -b.count)
    return out


# --------------------------------------------------------------------------- slot
def find_slot(
    image: np.ndarray,
    markers: MarkerSpec = DEFAULT_MARKERS,
    tol: int = 48,
    scale_hint: float | None = None,
    min_slot_px: int = 100,
    max_candidates: int = 6,
) -> SlotDetection:
    """Locate the slot from its four corner markers. Raises CalibrationFailed."""
    colours = markers.colours()
    cands: dict[str, list[Blob]] = {}
    for name, rgb in colours.items():
        c = marker_candidates(image, rgb, tol)
        if scale_hint:
            expect = markers.size_css * scale_hint
            c.sort(key=lambda b: abs(b.size - expect))
        cands[name] = c[:max_candidates]
    missing = [k for k, v in cands.items() if not v]
    if missing:
        raise CalibrationFailed(
            f"marker(s) not found: {', '.join(missing)} "
            "(is the dashboard visible, at 100% zoom, and not covered?)"
        )

    best: tuple[float, dict[str, Blob]] | None = None
    for tl, tr, bl, br in itertools.product(cands["tl"], cands["tr"], cands["bl"], cands["br"]):
        # ordering constraints
        if not (tl.x1 <= tr.x0 and bl.x1 <= br.x0 and tl.y1 <= bl.y0 and tr.y1 <= br.y0):
            continue
        if br.x1 - tl.x0 < min_slot_px or br.y1 - tl.y0 < min_slot_px:
            continue
        mis = max(
            abs(tl.x0 - bl.x0),   # left edge
            abs(tr.x1 - br.x1),   # right edge
            abs(tl.y0 - tr.y0),   # top edge
            abs(bl.y1 - br.y1),   # bottom edge
        )
        sizes = [b.size for b in (tl, tr, bl, br)]
        size_spread = max(sizes) - min(sizes)
        score = mis + size_spread
        if best is None or score < best[0]:
            best = (score, {"tl": tl, "tr": tr, "bl": bl, "br": br})
    if best is None:
        raise CalibrationFailed("markers found but they do not form a rectangle")

    m = best[1]
    tl, tr, bl, br = m["tl"], m["tr"], m["bl"], m["br"]
    span = max(br.x1 - tl.x0, br.y1 - tl.y0)
    mis = max(abs(tl.x0 - bl.x0), abs(tr.x1 - br.x1), abs(tl.y0 - tr.y0), abs(bl.y1 - br.y1))
    allowed = max(3.0, 0.01 * span)
    if mis > allowed:
        raise CalibrationFailed(
            f"markers are not aligned (edge disagreement {mis} px > {allowed:.0f} px); "
            "is the page partially covered or scrolled?"
        )
    sizes = [b.size for b in (tl, tr, bl, br)]
    marker_px = float(np.mean(sizes))
    warnings: list[str] = []
    if max(sizes) - min(sizes) > max(2.0, 0.2 * marker_px):
        raise CalibrationFailed(f"marker sizes inconsistent: {sizes}")
    if scale_hint:
        zoom = marker_px / (markers.size_css * scale_hint)
        if abs(zoom - 1.0) > 0.15:
            warnings.append(
                f"markers are {marker_px:.0f} px, expected {markers.size_css * scale_hint:.0f} px: "
                f"browser zoom looks like {zoom * 100:.0f}% (use 100%)"
            )
    rect = Rect(float(tl.x0), float(tl.y0), float(br.x1 - tl.x0), float(br.y1 - tl.y0))
    return SlotDetection(rect_px=rect, markers=m, marker_px=marker_px, misalignment_px=float(mis), warnings=warnings)


def draw_markers(
    image: np.ndarray, rect_px: Rect, marker_px: int, markers: MarkerSpec = DEFAULT_MARKERS
) -> np.ndarray:
    """Paint the four markers inside ``rect_px`` (test helper / smoke test)."""
    x0, y0 = int(rect_px.x), int(rect_px.y)
    x1, y1 = int(rect_px.x + rect_px.w), int(rect_px.y + rect_px.h)
    s = int(marker_px)
    c = markers.colours()
    image[y0:y0 + s, x0:x0 + s] = c["tl"]
    image[y0:y0 + s, x1 - s:x1] = c["tr"]
    image[y1 - s:y1, x0:x0 + s] = c["bl"]
    image[y1 - s:y1, x1 - s:x1] = c["br"]
    return image

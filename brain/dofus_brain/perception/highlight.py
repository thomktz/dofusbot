"""Interactive-element highlight (Y key): diff a plain frame with the highlighted one and
return the changed blobs (resources, doors, usable objects).

Pure numpy: the frame is downsampled by `step`, thresholded on the max channel difference,
and labelled with a small union-find over 4-connected pixels.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Blob:
    x: float            # centre, window points
    y: float
    w: float            # bounding box size, window points
    h: float
    area_px: int        # changed pixels at full resolution (approx.)

    def contains(self, x: float, y: float, margin: float = 0.0) -> bool:
        return abs(x - self.x) <= self.w / 2 + margin and abs(y - self.y) <= self.h / 2 + margin


def _label(mask: np.ndarray) -> tuple[np.ndarray, int]:
    h, w = mask.shape
    labels = np.zeros((h, w), dtype=np.int32)
    parent = [0]

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    nxt = 1
    ys, xs = np.nonzero(mask)
    for y, x in zip(ys.tolist(), xs.tolist()):
        up = labels[y - 1, x] if y > 0 else 0
        left = labels[y, x - 1] if x > 0 else 0
        if up and left:
            a, b = find(up), find(left)
            labels[y, x] = min(a, b)
            if a != b:
                parent[max(a, b)] = min(a, b)
        elif up or left:
            labels[y, x] = find(up or left)
        else:
            parent.append(nxt)
            labels[y, x] = nxt
            nxt += 1
    if nxt == 1:
        return labels, 0
    roots = np.array([find(i) for i in range(nxt)], dtype=np.int32)
    return roots[labels], nxt - 1


def highlight_blobs(plain: np.ndarray, highlighted: np.ndarray, scale: float = 1.0, threshold: int = 40,
                    min_px: int = 40, step: int = 2, exclude: list[tuple[int, int, int, int]] = ()) -> list[Blob]:
    """Blobs that changed between `plain` and `highlighted` (both capture pixels, same size).
    `exclude` are pixel boxes (x0, y0, x1, y1) to ignore (HUD, chat). Sorted by area, largest first."""
    if plain.shape != highlighted.shape:
        raise ValueError(f"frame sizes differ: {plain.shape} vs {highlighted.shape}")
    a = plain[::step, ::step].astype(np.int16)
    b = highlighted[::step, ::step].astype(np.int16)
    mask = np.abs(a - b).max(axis=2) > threshold
    for x0, y0, x1, y1 in exclude:
        mask[y0 // step: (y1 + step - 1) // step, x0 // step: (x1 + step - 1) // step] = False
    labels, n = _label(mask)
    blobs = []
    for i in np.unique(labels[labels > 0]):
        ys, xs = np.nonzero(labels == i)
        area = len(xs) * step * step
        if area < min_px:
            continue
        x0, x1 = xs.min() * step, (xs.max() + 1) * step
        y0, y1 = ys.min() * step, (ys.max() + 1) * step
        blobs.append(Blob(((x0 + x1) / 2) / scale, ((y0 + y1) / 2) / scale, (x1 - x0) / scale, (y1 - y0) / scale, area))
    return sorted(blobs, key=lambda b: -b.area_px)


def region_changed(a: np.ndarray, b: np.ndarray, blob: Blob, scale: float = 1.0, threshold: int = 40,
                   frac: float = 0.15) -> bool:
    """True if more than `frac` of the blob's box changed between frames a and b (e.g. the
    resource was harvested and its sprite changed)."""
    x0 = int(max(0, (blob.x - blob.w / 2) * scale))
    x1 = int(min(a.shape[1], (blob.x + blob.w / 2) * scale))
    y0 = int(max(0, (blob.y - blob.h / 2) * scale))
    y1 = int(min(a.shape[0], (blob.y + blob.h / 2) * scale))
    if x1 <= x0 or y1 <= y0:
        return False
    d = np.abs(a[y0:y1, x0:x1].astype(np.int16) - b[y0:y1, x0:x1].astype(np.int16)).max(axis=2) > threshold
    return float(d.mean()) > frac

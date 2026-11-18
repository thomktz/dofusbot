"""Terrain preview reader: a replica of the fight board built BEFORE the fight.

Thomas turns on the client's terrain preview; during placement it shows a miniature of the
whole board in the top-left corner. We read it only during placement (never during a turn),
and the planner freezes the result as the fight map when the fight starts, so the solver and
pathing know the obstacles and holes before the first move.

How it reads the preview, without any calibration of the miniature itself:
1. Crop the search area (`rect`, fractions of the 1280x800 content; top-left by default).
2. Background = median colour of the crop's border. Pixels that differ from it by more than
   `bg_tol` are "drawn". If too few or too many pixels are drawn, the preview is off.
3. Fit the board grid (14 cols x 40 rows, odd rows shifted half a cell) onto the bounding box
   of the drawn pixels, or use a pinned `mini_grid` {ox, oy, cw, ch} in crop pixels once it
   has been measured on a real frame.
4. Sample a small patch at every cell centre and classify it:
   hole (background colour), red / blue start cell, blocked (much darker than the floor, or
   nearest to a configured `palette.blocked`), walkable (the rest).

All colours and the search area are PLACEHOLDERS until measured on a saved crop
(phases.json `terrain`); the planner saves the crop of every fight for that.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..game.grid import COLS, ROWS, Grid

REF_W, REF_H = 1280.0, 800.0

DEFAULTS: dict = {
    "enabled": True,
    "rect": [0.0, 0.0, 0.30, 0.40],   # search area: x, y, w, h as fractions of the content
    "border_px": 3,                   # border strip (crop px) used for the background colour
    "bg_tol": 28,                     # max channel difference that still counts as background
    "min_drawn": 0.08,                # fraction of drawn pixels for the preview to count as on
    "max_drawn": 0.90,
    "min_cells": 40,                  # cells present (not holes) for a plausible board
    "dark_frac": 0.62,                # blocked when luminance < dark_frac * floor median
    "patch_frac": 0.22,               # sample patch radius as a fraction of the mini cell height
    "mini_grid": None,                # pinned {ox, oy, cw, ch} in crop px; None = fit to bbox
    "palette": {},                    # optional {"walkable": [r,g,b], "blocked": [r,g,b], ...}
    "palette_tol": 40,
    "save_crops": True,
}


@dataclass
class TerrainReplica:
    walkable: list[int] = field(default_factory=list)
    blocked: list[int] = field(default_factory=list)     # obstacles drawn on the board
    holes: list[int] = field(default_factory=list)       # no cell drawn (off-map, void)
    red: list[int] = field(default_factory=list)         # start cells (walkable)
    blue: list[int] = field(default_factory=list)
    crop_rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # x, y, w, h in capture px
    mini: tuple[float, float, float, float] = (0, 0, 0, 0)  # fitted ox, oy, cw, ch in crop px

    @property
    def present(self) -> int:
        return len(self.walkable) + len(self.blocked) + len(self.red) + len(self.blue)

    def not_walkable(self) -> list[int]:
        return sorted(set(self.blocked) | set(self.holes))

    def summary(self) -> dict:
        return {"walkable": len(self.walkable) + len(self.red) + len(self.blue),
                "blocked": len(self.blocked), "holes": len(self.holes),
                "red": len(self.red), "blue": len(self.blue)}


def _lum(c: np.ndarray) -> float:
    return float(0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2])


class TerrainPreviewReader:
    def __init__(self, cfg: dict | None = None):
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.grid = Grid(ox=0.0, oy=0.0, cw=1.0, ch=1.0)   # unit grid; scaled per read

    def crop(self, img: np.ndarray) -> tuple[np.ndarray, tuple[int, int, int, int]]:
        H, W = img.shape[:2]
        fx, fy, fw, fh = self.cfg["rect"]
        x, y = int(round(fx * W)), int(round(fy * H))
        w, h = int(round(fw * W)), int(round(fh * H))
        w, h = max(0, min(w, W - x)), max(0, min(h, H - y))
        return img[y:y + h, x:x + w, :3], (x, y, w, h)

    def _fit(self, drawn: np.ndarray) -> tuple[float, float, float, float] | None:
        pinned = self.cfg.get("mini_grid")
        if pinned:
            return (float(pinned["ox"]), float(pinned["oy"]), float(pinned["cw"]), float(pinned["ch"]))
        rows = np.flatnonzero(drawn.mean(axis=1) > 0.02)
        cols = np.flatnonzero(drawn.mean(axis=0) > 0.02)
        if rows.size < 4 or cols.size < 4:
            return None
        x0, x1 = float(cols[0]), float(cols[-1] + 1)
        y0, y1 = float(rows[0]), float(rows[-1] + 1)
        # the board spans 14.5 cell widths (odd rows shifted) and 20.5 cell heights
        cw = (x1 - x0) / (COLS + 0.5)
        ch = (y1 - y0) / (ROWS / 2 + 0.5)
        if cw < 2 or ch < 1:
            return None
        return (x0 + cw / 2, y0 + ch / 2, cw, ch)

    def _classify(self, c: np.ndarray, bg: np.ndarray) -> str:
        tol = float(self.cfg["bg_tol"])
        if float(np.abs(c - bg).max()) <= tol:
            return "hole"
        pal = self.cfg.get("palette") or {}
        if pal:
            best, best_d = None, 1e9
            for name, ref in pal.items():
                d = float(np.abs(c - np.asarray(ref, dtype=float)).max())
                if d < best_d:
                    best, best_d = name, d
            if best is not None and best_d <= float(self.cfg["palette_tol"]):
                return str(best)
        r, g, b = (float(v) for v in c)
        if r > 140 and r - g > 60 and r - b > 60:
            return "red"
        if b > 140 and b - r > 60 and b - g > 20:
            return "blue"
        return "floor"

    def read(self, img: np.ndarray) -> TerrainReplica | None:
        """Replica of the board from the preview, or None if the preview is not shown."""
        if not self.cfg.get("enabled", True):
            return None
        crop, rect = self.crop(img)
        h, w = crop.shape[:2]
        bp = int(self.cfg["border_px"])
        if h < 4 * bp + 10 or w < 4 * bp + 10:
            return None
        c = crop.astype(float)
        border = np.concatenate([c[:bp].reshape(-1, 3), c[-bp:].reshape(-1, 3),
                                 c[:, :bp].reshape(-1, 3), c[:, -bp:].reshape(-1, 3)])
        bg = np.median(border, axis=0)
        drawn = np.abs(c - bg).max(axis=-1) > float(self.cfg["bg_tol"])
        frac = float(drawn.mean())
        if not (float(self.cfg["min_drawn"]) <= frac <= float(self.cfg["max_drawn"])):
            return None
        fit = self._fit(drawn)
        if fit is None:
            return None
        ox, oy, cw, ch = fit
        g = Grid(ox=ox, oy=oy, cw=cw, ch=ch)
        rad = max(1, int(round(ch * float(self.cfg["patch_frac"]))))
        kinds: dict[int, str] = {}
        colours: dict[int, np.ndarray] = {}
        for cell in range(g.n_cells):
            x, y = g.center(cell)
            xi, yi = int(round(x)), int(round(y))
            if not (0 <= xi < w and 0 <= yi < h):
                kinds[cell] = "hole"
                continue
            patch = c[max(0, yi - rad):yi + rad + 1, max(0, xi - rad):xi + rad + 1].reshape(-1, 3)
            col = np.median(patch, axis=0)
            colours[cell] = col
            kinds[cell] = self._classify(col, bg)
        floor = [_lum(colours[k]) for k, v in kinds.items() if v == "floor"]
        out = TerrainReplica(crop_rect=rect, mini=(ox, oy, cw, ch))
        med = float(np.median(floor)) if floor else 0.0
        for cell, k in kinds.items():
            if k == "floor":
                k = "blocked" if med > 0 and _lum(colours[cell]) < float(self.cfg["dark_frac"]) * med else "walkable"
            {"walkable": out.walkable, "blocked": out.blocked, "hole": out.holes,
             "red": out.red, "blue": out.blue}.get(k, out.walkable).append(cell)
        if out.present < int(self.cfg["min_cells"]):
            return None
        return out

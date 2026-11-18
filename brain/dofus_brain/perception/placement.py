"""Placement phase reader (tactical mode): the coloured start cells of both teams, who is
standing where, and which colour is ours.

Placement colours are PLACEHOLDERS (config/phases.json placement.palette): re-sample them on
the real client. Our team is the colour of the circle that carries the self marker; if the
self marker is not found we fall back to fight.ally_color from regions.json.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..game.grid import Grid
from .fight import FightReader
from .regions import Regions

DEFAULT_PALETTE = {"placement_red": [170, 70, 70], "placement_blue": [70, 90, 170]}


@dataclass
class PlacementView:
    our_colour: str                                  # "red" | "blue"
    our_cells: list[int] = field(default_factory=list)      # free start cells of our team
    their_cells: list[int] = field(default_factory=list)
    self_cell: int | None = None
    allies: list[int] = field(default_factory=list)
    enemies: list[int] = field(default_factory=list)
    blocked: list[int] = field(default_factory=list)

    @property
    def is_placement(self) -> bool:
        return len(self.our_cells) + len(self.their_cells) >= 2


class PlacementReader:
    def __init__(self, regions: Regions, palette: dict | None = None, tol: int = 28):
        self.fr = FightReader(regions)
        self.r = regions
        self.pal = {k: np.array(v, dtype=np.int16) for k, v in (palette or DEFAULT_PALETTE).items()}
        self.tol = tol

    def _placement_colour(self, rgb: np.ndarray) -> str | None:
        best, best_d = None, 1e9
        for name, ref in self.pal.items():
            d = float(np.abs(rgb.astype(np.int16) - ref).max())
            if d < best_d:
                best, best_d = name, d
        if best is None or best_d > self.tol:
            return None
        return "red" if best.endswith("red") else "blue"

    def read(self, img: np.ndarray, grid: Grid, scale: float = 1.0) -> PlacementView:
        classes = self.fr.cell_classes(img, grid, scale)   # fighters, blocked cells
        H, W = img.shape[:2]
        rad = max(1, int(grid.ch * self.fr.radius_frac * scale))
        start: dict[int, str] = {}
        for c in range(grid.n_cells):
            if c in classes:
                continue
            x, y = grid.center_px(c, scale)
            xi, yi = int(round(x)), int(round(y))
            if not (rad <= xi < W - rad and rad <= yi < H - rad):
                continue
            patch = img[yi - rad: yi + rad + 1, xi - rad: xi + rad + 1].reshape(-1, 3)
            col = self._placement_colour(np.median(patch, axis=0))
            if col:
                start[c] = col
        team_of = {self.fr.ally_colour: "red" if "red" in self.fr.ally_colour else "blue",
                   self.fr.enemy_colour: "red" if "red" in self.fr.enemy_colour else "blue"}
        self_cell = next((c for c, k in classes.items() if k == "self"), None)
        fighters = {c: team_of[k] for c, k in classes.items() if k in team_of}
        if self_cell is not None:
            # the self marker sits inside our circle: our colour is whichever team circle
            # surrounds it (sample a ring around the centre)
            ring = self._ring_colour(img, grid, scale, self_cell)
            ours = ring or team_of[self.fr.ally_colour]
        else:
            ours = team_of[self.fr.ally_colour]
        theirs = "blue" if ours == "red" else "red"
        allies = [c for c, t in fighters.items() if t == ours]
        enemies = [c for c, t in fighters.items() if t == theirs]
        return PlacementView(
            our_colour=ours,
            our_cells=sorted(c for c, t in start.items() if t == ours),
            their_cells=sorted(c for c, t in start.items() if t == theirs),
            self_cell=self_cell, allies=sorted(allies), enemies=sorted(enemies),
            blocked=sorted(c for c, k in classes.items() if k == "blocked_cell"))

    def _ring_colour(self, img: np.ndarray, grid: Grid, scale: float, cell: int) -> str | None:
        x, y = grid.center_px(cell, scale)
        rx = grid.cw * 0.24 * scale
        pts = [(int(x + rx), int(y)), (int(x - rx), int(y))]
        names = {self.fr.ally_colour, self.fr.enemy_colour}
        votes: dict[str, int] = {}
        for px, py in pts:
            if 0 <= py < img.shape[0] and 0 <= px < img.shape[1]:
                m = self.fr._match(img[py, px], list(names))
                if m:
                    t = "red" if "red" in m else "blue"
                    votes[t] = votes.get(t, 0) + 1
        return max(votes, key=votes.get) if votes else None

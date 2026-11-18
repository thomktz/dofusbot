"""Fight reader for TACTICAL MODE (flat cells) with team circles on.

For each of the 560 cells we sample a small patch around the cell centre (grid §1, scaled to
capture pixels) and classify its median colour: walkable / blocked / movement highlight /
red circle / blue circle. A yellow self-marker inside a circle marks our own character
(placeholder heuristic: on the real client the own fighter is better found from the
timeline highlight or by hovering; recalibrate).
"""
from __future__ import annotations

import numpy as np

from ..game.grid import Grid
from ..game.state import Fighter, FightState
from .regions import Regions


class FightReader:
    def __init__(self, regions: Regions):
        self.r = regions
        fc = regions.cfg.get("fight", {})
        self.ally_colour = fc.get("ally_color", "team_red")
        self.enemy_colour = fc.get("enemy_color", "team_blue")
        self.radius_frac = float(fc.get("sample_radius_frac", 0.18))
        self.exclude = [regions[n] for n in fc.get("exclude_regions", ["fight_timeline", "hud_bar"]) if n in regions.regions]

    def _match(self, rgb: np.ndarray, names: list[str], tol: int = 30) -> str | None:
        best, best_d = None, 1e9
        for n in names:
            if n not in self.r.palette:
                continue
            d = float(np.abs(rgb.astype(np.int16) - self.r.palette[n]).max())
            if d < best_d:
                best, best_d = n, d
        return best if best_d <= tol else None

    def cell_classes(self, img: np.ndarray, grid: Grid, scale: float) -> dict[int, str]:
        H, W = img.shape[:2]
        rad = max(1, int(grid.ch * self.radius_frac * scale))
        names = ["tactical_cell", "tactical_cell_alt", "blocked_cell", "move_highlight",
                 self.ally_colour, self.enemy_colour, "self_marker"]
        out: dict[int, str] = {}
        for c in range(grid.n_cells):
            x, y = grid.center_px(c, scale)
            xi, yi = int(round(x)), int(round(y))
            if not (rad <= xi < W - rad and rad <= yi < H - rad):
                continue
            if any(x0 <= xi < x1 and y0 <= yi < y1 for x0, y0, x1, y1 in (e.box(W, H) for e in self.exclude)):
                continue  # covered by UI (timeline, HUD)
            patch = img[yi - rad: yi + rad + 1, xi - rad: xi + rad + 1].reshape(-1, 3)
            centre = img[yi, xi]
            if self._match(centre, ["self_marker"]) == "self_marker":
                out[c] = "self"
                continue
            m = self._match(np.median(patch, axis=0), names)
            if m is not None:
                out[c] = m
        return out

    def read(self, img: np.ndarray, grid: Grid, scale: float, my_turn: bool | None = None) -> FightState:
        classes = self.cell_classes(img, grid, scale)
        fighters: list[Fighter] = []
        blocked, reach = [], []
        n_ally = n_enemy = 0
        for c, k in sorted(classes.items()):
            if k == "self":
                fighters.append(Fighter(id="self", name="self", team="ally", is_self=True, cell=c))
            elif k == self.ally_colour:
                n_ally += 1
                fighters.append(Fighter(id=f"ally{n_ally}", name=f"ally{n_ally}", team="ally", cell=c))
            elif k == self.enemy_colour:
                n_enemy += 1
                fighters.append(Fighter(id=f"enemy{n_enemy}", name=f"enemy{n_enemy}", team="enemy", cell=c))
            elif k == "blocked_cell":
                blocked.append(c)
            elif k == "move_highlight":
                reach.append(c)
        if my_turn is None:
            btn = self.r.crop(img, "end_turn_button")
            my_turn = self.r.color_fraction(btn, "end_turn_active", 30) > 0.5 if "end_turn_active" in self.r.palette else False
        return FightState(phase="my_turn" if my_turn else "enemy_turn", turn=0, timer_s=None,
                          fighters=fighters, blocked_cells=blocked, reachable_cells=reach)

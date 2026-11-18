"""Synthetic Dofus-like screenshots for tests and --mock mode.

They are NOT pretty: flat colours at the positions given by config/grid.json and
config/regions.json (or the geometry stored in fixtures/fixtures.json), text drawn with the
Pillow-bundled font so TemplateOCR reads it exactly (that font has no accents, so the
synthetic texts are ASCII; accents are Apple Vision's job on the real client). Used by scripts/make_fixtures.py and by
MockController (which re-renders the world screen whenever the mock map coords change).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw

from .game.grid import Grid
from .ocr.backends import draw_tracked_text, synthetic_font

TEXT = (240, 240, 240)


@dataclass
class FighterSpec:
    cell: int
    team: str            # "ally" | "enemy"
    is_self: bool = False
    name: str = ""


@dataclass
class FightSpec:
    fighters: list[FighterSpec] = field(default_factory=list)
    blocked: list[int] = field(default_factory=list)
    highlight: list[int] = field(default_factory=list)
    my_turn: bool = True
    pa: int = 6
    pm: int = 3
    start_cells: dict[int, tuple[int, int, int]] = field(default_factory=dict)  # placement phase colours


class Renderer:
    def __init__(self, grid_cfg: dict, regions_cfg: dict, size: tuple[int, int] = (1280, 800), scale: float = 1.0):
        self.grid = Grid.from_config(grid_cfg)
        self.regions = regions_cfg["regions"]
        self.pal = {k: tuple(v) for k, v in regions_cfg["palette"].items()}
        self.fight_cfg = regions_cfg.get("fight", {})
        self.size = size     # window points
        self.scale = scale   # capture pixels per point
        self.font = synthetic_font(int(22 * scale))
        self.small = synthetic_font(int(18 * scale))

    # -- helpers (all inputs in window points, drawn in capture pixels)
    def _canvas(self, colour) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        im = Image.new("RGB", (int(self.size[0] * self.scale), int(self.size[1] * self.scale)), colour)
        return im, ImageDraw.Draw(im)

    def _box(self, name: str) -> tuple[int, int, int, int]:
        r = self.regions[name]
        W, H = self.size[0] * self.scale, self.size[1] * self.scale
        return (int(r["x"] * W), int(r["y"] * H), int((r["x"] + r["w"]) * W), int((r["y"] + r["h"]) * H))

    def _text_in(self, d: ImageDraw.ImageDraw, name: str, text: str, font=None, dy: int = 0):
        x0, y0, _, _ = self._box(name)
        draw_tracked_text(d, (x0 + int(4 * self.scale), y0 + int(4 * self.scale) + dy), text, font or self.font, TEXT)

    def _hud(self, d, hp="250/250", pa=6, pm=3, end_turn_active=False):
        d.rectangle(self._box("hud_bar"), fill=self.pal["hud_bg"])
        self._text_in(d, "hp", hp)
        self._text_in(d, "pa", str(pa))
        self._text_in(d, "pm", str(pm))
        d.rectangle(self._box("end_turn_button"), fill=self.pal["end_turn_active"] if end_turn_active else (90, 80, 70))

    def _diamond(self, d, cell: int, fill, shrink: float = 0.92):
        x, y = self.grid.center(cell)
        hw, hh = self.grid.cw / 2 * shrink, self.grid.ch / 2 * shrink
        s = self.scale
        d.polygon([((x) * s, (y - hh) * s), ((x + hw) * s, y * s), (x * s, (y + hh) * s), ((x - hw) * s, y * s)], fill=fill)

    # -- screens
    def world(self, coords: tuple[int, int] | None = (4, -18), area: str = "Astrub", hp="250/250", pa=6, pm=3) -> np.ndarray:
        im, d = self._canvas(self.pal["world_ground"])
        rng = np.random.default_rng(1234)
        for _ in range(40):  # some "decor"
            x, y = rng.integers(0, im.width), rng.integers(0, int(im.height * 0.85))
            r = int(rng.integers(8, 30) * self.scale)
            d.ellipse((x - r, y - r, x + r, y + r), fill=(60, 95, 45))
        # dark banner behind area name + coords (like the game's top-left text shadow)
        x0, y0, _, _ = self._box("area_name")
        _, _, x1, y1 = self._box("map_coords")
        d.rectangle((x0, y0, x1, y1), fill=(30, 30, 30))
        self._text_in(d, "area_name", area, self.small)
        if coords is not None:
            self._text_in(d, "map_coords", f"{coords[0]},{coords[1]}")
        self._hud(d, hp, pa, pm)
        return np.asarray(im)

    def fight(self, spec: FightSpec) -> np.ndarray:
        im, d = self._canvas((25, 25, 25))
        g = self.grid
        hl = set(spec.highlight)
        blk = set(spec.blocked)
        for c in range(g.n_cells):
            row, col = g.rc(c)
            if c in spec.start_cells:
                colour = tuple(spec.start_cells[c])
            elif c in blk:
                colour = self.pal["blocked_cell"]
            elif c in hl:
                colour = self.pal["move_highlight"]
            else:
                colour = self.pal["tactical_cell"] if (row // 2 + col) % 2 == 0 else self.pal["tactical_cell_alt"]
            self._diamond(d, c, colour)
        ally = self.pal[self.fight_cfg.get("ally_color", "team_red")]
        enemy = self.pal[self.fight_cfg.get("enemy_color", "team_blue")]
        s = self.scale
        for f in spec.fighters:
            x, y = g.center(f.cell)
            rx, ry = g.cw * 0.32, g.ch * 0.32
            d.ellipse(((x - rx) * s, (y - ry) * s, (x + rx) * s, (y + ry) * s), fill=ally if f.team == "ally" else enemy)
            if f.is_self:
                r = g.ch * 0.12
                d.ellipse(((x - r) * s, (y - r) * s, (x + r) * s, (y + r) * s), fill=self.pal["self_marker"])
        d.rectangle(self._box("fight_timeline"), fill=(50, 45, 40))
        self._hud(d, pa=spec.pa, pm=spec.pm, end_turn_active=spec.my_turn)
        return np.asarray(im)

    def dialog(self, npc: str, text: str, options: list[str], coords=(4, -18)) -> np.ndarray:
        base = Image.fromarray(self.world(coords))
        d = ImageDraw.Draw(base)
        d.rectangle(self._box("dialog_box"), fill=self.pal["dialog_bg"])
        self._text_in(d, "dialog_npc", npc)
        self._text_in(d, "dialog_text", text, self.small)
        line_h = int(30 * self.scale)
        for i, o in enumerate(options):
            self._text_in(d, "dialog_options", f"{i + 1}. {o}", self.small, dy=i * line_h)
        return np.asarray(base)

    def loading(self) -> np.ndarray:
        im, d = self._canvas((8, 8, 10))
        return np.asarray(im)

    def popup(self, text: str = "Niveau superieur !") -> np.ndarray:
        base = Image.fromarray(self.world())
        d = ImageDraw.Draw(base)
        d.rectangle(self._box("popup_center"), fill=self.pal["popup_bg"])
        self._text_in(d, "popup_center", text, self.small)
        return np.asarray(base)


# Default scenario stored in fixtures/fixtures.json
DEFAULT_FIGHT = FightSpec(
    fighters=[FighterSpec(241, "ally", True, "Moi"),
              FighterSpec(245, "enemy", False, "Bouftou"),
              FighterSpec(301, "enemy", False, "Tofu")],
    blocked=[243, 257, 271, 200, 214],
    highlight=[227, 255, 226, 254, 240, 242],
    my_turn=True, pa=6, pm=3,
)
DEFAULT_DIALOG = {
    "npc": "Ganymede",
    "text": "Bonjour aventurier, que cherches-tu ?",
    "options": ["Je cherche du travail.", "Ou est la banque ?", "Au revoir."],
}

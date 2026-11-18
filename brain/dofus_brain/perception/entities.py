"""Name plates shown by the "show entities" key (Z): NPC, monster and fighter names, with a
level or an HP fraction when the game shows one.

Input is OCR lines with boxes (capture pixels) of the game area; a plate sits above the
entity, so the entity's cell is looked up slightly below the plate. Formats handled:
    "Bouftou (12/30)"   "Bouftou 12/30"   "Bouftou niv. 5"   "Bouftou - Niveau 5"
    "Groupe de 3 monstres (niv. 24)"   "Ganymede"
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..game.grid import Grid
from ..ocr.backends import OcrLine

HP_RE = re.compile(r"\(?\s*(\d{1,5})\s*/\s*(\d{1,5})\s*\)?")
LEVEL_RE = re.compile(r"(?:niv(?:eau)?\.?|lvl|level|lv\.?)\s*(\d{1,3})", re.I)
GROUP_RE = re.compile(r"(?:groupe de|group of)\s*(\d{1,2})", re.I)
CLEAN_RE = re.compile(r"[\s\-–—:,(]+$")


@dataclass
class Plate:
    name: str
    level: int | None = None
    hp: int | None = None
    hp_max: int | None = None
    group_size: int | None = None
    x: float = 0.0          # window points, centre of the plate
    y: float = 0.0
    cell: int | None = None
    conf: float = 1.0


def parse_plate(text: str) -> Plate | None:
    t = " ".join(text.split())
    if not t:
        return None
    hp = hp_max = level = group = None
    m = HP_RE.search(t)
    if m:
        hp, hp_max = int(m.group(1)), int(m.group(2))
        t = (t[:m.start()] + t[m.end():]).strip()
    m = LEVEL_RE.search(t)
    if m:
        level = int(m.group(1))
        t = (t[:m.start()] + t[m.end():]).strip()
    m = GROUP_RE.search(t)
    if m:
        group = int(m.group(1))
    name = CLEAN_RE.sub("", t.replace("()", "").strip())
    name = re.sub(r"\s*[-–—]\s*$", "", name).strip()
    if not name or not any(ch.isalpha() for ch in name):
        return None
    if hp is not None and hp_max is not None and (hp_max == 0 or hp > hp_max):
        hp = hp_max = None  # misread
    return Plate(name, level, hp, hp_max, group)


def read_plates(lines: list[OcrLine], grid: Grid | None, scale: float = 1.0, offset_px: tuple[int, int] = (0, 0),
                below_pt: float | None = None) -> list[Plate]:
    """OCR lines (boxes relative to a crop starting at offset_px) -> plates with window-point
    positions and, if a grid is given, the cell under the plate (`below_pt` points lower;
    default one cell height)."""
    out = []
    for ln in lines:
        p = parse_plate(ln.text)
        if p is None:
            continue
        x0, y0, x1, y1 = ln.box
        p.x = (offset_px[0] + (x0 + x1) / 2) / scale
        p.y = (offset_px[1] + (y0 + y1) / 2) / scale
        p.conf = ln.conf
        if grid is not None:
            dy = grid.ch if below_pt is None else below_pt
            p.cell = grid.cell_at(p.x, p.y + dy)
        out.append(p)
    return out


def attach_to_fighters(plates: list[Plate], fight, grid: Grid, max_dist: int = 1) -> int:
    """Copy names/HP from plates onto FightState fighters on the same (or adjacent) cell.
    Returns how many fighters were matched."""
    used: set[int] = set()
    n = 0
    for f in fight.fighters:
        if f.cell is None:
            continue
        best, best_d = None, max_dist + 1
        for i, p in enumerate(plates):
            if i in used or p.cell is None:
                continue
            d = grid.distance(f.cell, p.cell)
            if d < best_d:
                best, best_d = i, d
        if best is None:
            continue
        used.add(best)
        p = plates[best]
        if not f.is_self:
            f.name = p.name
        if p.hp is not None:
            f.hp, f.hp_max = p.hp, p.hp_max
        n += 1
    return n


class EntityScanner:
    """OCR the game area of a frame captured with the entity key held -> plates with cells.
    `grid_for(window_width_points)` gives the grid (Perceiver.grid_for)."""

    def __init__(self, ocr, regions, grid_for, region: str = "game_area"):
        self.ocr = ocr
        self.r = regions
        self.grid_for = grid_for
        self.region = region

    def __call__(self, img, scale: float = 1.0) -> list[Plate]:
        crop = self.r.crop(img, self.region)
        off = self.r.offset(img, self.region)
        grid = self.grid_for(img.shape[1] / scale, img.shape[0] / scale)
        return read_plates(self.ocr.recognize(crop), grid, scale, off)

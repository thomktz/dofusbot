"""Deterministic placement-board parser: terrain, start cells, enemies and our position.

Measured on Thomas's client (1280x800 content, fight camera, placement frame 215228_045 of
stream-215200). That frame has NO separate terrain-preview box: the top-left is scene art (no
framed rectangle, no grid-periodic miniature). Start cells are drawn on the main board, so we
parse the board itself with the calibrated grid (config/grid.json).

Algorithm, per cell (diamond mask = |dx|/(cw/2) + |dy|/(ch/2) <= `shrink`):
1. unknown: centre off the frame, or inside an excluded UI band (HUD at the bottom).
2. start cells: fraction of pixels within `start_tol` of the red-start colour (lit or shaded
   variant) or blue-start colour >= `start_min`. Only the largest 8-connected group (in the
   (row, col) grid, gap 1 allowed) of each colour is kept, and only if it has >= `min_group`
   cells, so stray orange/blue decor is dropped (`is_placement` needs both sides).
3. occupied start cell (a fighter stands on it): the inner patch (shrink `inner`) holds less
   than `free_min` start colour while the whole diamond still shows it at its rim.
4. board tiles: the fight area is a pale cream checkerboard (r>215, g>185, b>=108), unlike
   the surrounding sand (b ~ 85-95). A cell is a tile when >= `walk_min` of it is tile colour.
   The board = the 4-connected (iso) group of tiles + start cells that contains the start
   cells. Board cells -> walkable. Non-board cells enclosed by the board (holes, crates)
   -> blocked; a cell on the board's outer ring is blocked only when it is not mostly sand
   (`sand_max`; a crate or wall on the edge), else it is outside. Everything else -> outside (scenery, not part of the
   fight; also not walkable). Thomas checked the first render: the old sand test marked
   most of the ground around the fight as walkable.
5. our colour: given explicitly, or "auto" = the side with FEWER fighters (solo / small
   party vs a monster group; a tie falls back to `fallback_colour`). Enemies = occupied cells
   of the other colour; our position = the occupied cell of our colour (lowest id if several
   allies are placed; `allies` lists them all).

Measured on 215228_045: red start cells (234,132,62), blue (100,158,206). The 4 monsters are
small dark round sprites (~(47,53,57)) on red cells; they vanish one by one in the later
frames of the stream as they die, which confirms them. We stand on blue cell 259 (row 18,
col 7): it is the centre of the 3-PM green move diamond of frame 215234_064.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..game.grid import Grid

DEFAULTS: dict = {
    "shrink": 0.92,
    "inner": 0.40,
    "start_colours": {
        "red": [[234, 132, 62], [208, 82, 34]],   # lit, shaded
        "blue": [[100, 158, 206], [64, 132, 212]],
    },
    "start_tol": 30,
    "start_min": 0.15,
    "min_group": 4,         # fewer start cells than this on a side -> not that colour
    "free_min": 0.35,
    "walk_min": 0.60,
    "sand_max": 0.45,       # ring cell with less sand than this is an object on the board edge
    "ui_bottom": 0.90,      # content fraction where the HUD starts
    "ui_top": 0.0,
}


@dataclass
class BoardView:
    walkable: list[int] = field(default_factory=list)
    blocked: list[int] = field(default_factory=list)
    outside: list[int] = field(default_factory=list)    # scenery off the fight board
    unknown: list[int] = field(default_factory=list)
    red: list[int] = field(default_factory=list)        # all red start cells (free + occupied)
    blue: list[int] = field(default_factory=list)
    occupied: list[int] = field(default_factory=list)   # start cells with a fighter on them
    our_colour: str = "red"
    self_cell: int | None = None
    allies: list[int] = field(default_factory=list)
    enemies: list[int] = field(default_factory=list)
    kinds: dict[int, str] = field(default_factory=dict)

    @property
    def is_placement(self) -> bool:
        return bool(self.red) and bool(self.blue)

    def free_start(self, colour: str) -> list[int]:
        cells = self.red if colour == "red" else self.blue
        return [c for c in cells if c not in self.occupied]

    def summary(self) -> dict:
        return {"walkable": len(self.walkable), "blocked": len(self.blocked),
                "outside": len(self.outside),
                "unknown": len(self.unknown), "red": self.red, "blue": self.blue,
                "occupied": self.occupied, "our_colour": self.our_colour,
                "self": self.self_cell, "allies": self.allies, "enemies": self.enemies}

    def ascii(self, grid: Grid) -> str:
        ch = {"walkable": ".", "blocked": "#", "outside": " ", "unknown": "?", "red": "r", "blue": "b"}
        lines = []
        for row in range(grid.rows):
            s = " " if row % 2 else ""
            for col in range(grid.cols):
                c = row * grid.cols + col
                k = ch.get(self.kinds.get(c, "unknown"), "?")
                if c == self.self_cell:
                    k = "@"
                elif c in self.enemies:
                    k = "E"
                elif c in self.allies:
                    k = "A"
                s += k + " "
            lines.append(f"{row:2d} {s}")
        return "\n".join(lines)


def _tile_mask(img: np.ndarray) -> np.ndarray:
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    return (r > 215) & (g > 185) & (b >= 108) & (np.abs(r - g) < 60)


def _sand_mask(img: np.ndarray) -> np.ndarray:
    """Plain or shaded sand around the fight (yellow, low blue), not crates or holes."""
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    return (r > 170) & (g > 140) & (b < 110) & (r - b > 90) & (r - g < 60)


def _largest_group(cells: set[int], grid: Grid) -> set[int]:
    best: set[int] = set()
    todo = set(cells)
    while todo:
        seed = todo.pop()
        comp, stack = {seed}, [seed]
        while stack:
            r, c = divmod(stack.pop(), grid.cols)
            for dr in range(-3, 4):
                for dc in range(-2, 3):
                    n = (r + dr) * grid.cols + (c + dc)
                    if 0 <= r + dr < grid.rows and 0 <= c + dc < grid.cols and n in todo:
                        todo.discard(n)
                        comp.add(n)
                        stack.append(n)
        if len(comp) > len(best):
            best = comp
    return best


class PlacementBoardParser:
    def __init__(self, grid: Grid, cfg: dict | None = None, our_colour: str = "auto",
                 fallback_colour: str = "red"):
        self.grid = grid
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.our_colour = our_colour
        self.fallback_colour = fallback_colour

    def _colour_mask(self, img: np.ndarray, refs) -> np.ndarray:
        tol = int(self.cfg["start_tol"])
        out = np.zeros(img.shape[:2], bool)
        for ref in refs:
            out |= np.abs(img - np.asarray(ref, dtype=np.int16)).max(-1) <= tol
        return out

    def read(self, img: np.ndarray, scale: float = 1.0) -> BoardView:
        img = img[..., :3].astype(np.int16)
        H, W = img.shape[:2]
        g, cfg = self.grid, self.cfg
        masks = {k: self._colour_mask(img, v) for k, v in cfg["start_colours"].items()}
        floor = _tile_mask(img)
        sand = _sand_mask(img)
        hw, hh = g.cw * scale / 2, g.ch * scale / 2
        ui_y0, ui_y1 = cfg["ui_top"] * H, cfg["ui_bottom"] * H
        pad_x, pad_y = int(hw) + 1, int(hh) + 1
        ly, lx = np.mgrid[-pad_y:pad_y + 1, -pad_x:pad_x + 1]
        dist = np.abs(lx) / hw + np.abs(ly) / hh
        outer, inner = dist <= cfg["shrink"], dist <= cfg["inner"]

        frac: dict[int, dict[str, float]] = {}
        unknown: set[int] = set()
        for cell in range(g.n_cells):
            x, y = g.center_px(cell, scale)
            xi, yi = int(round(x)), int(round(y))
            if not (pad_x <= xi < W - pad_x and pad_y <= yi < H - pad_y) or not (ui_y0 <= yi < ui_y1):
                unknown.add(cell)
                continue
            sl = (slice(yi - pad_y, yi + pad_y + 1), slice(xi - pad_x, xi + pad_x + 1))
            f = {"floor": float(floor[sl][outer].mean()), "sand": float(sand[sl][outer].mean())}
            for k, m in masks.items():
                f[k] = float(m[sl][outer].mean())
                f[k + "_in"] = float(m[sl][inner].mean())
            frac[cell] = f

        start: dict[str, set[int]] = {}
        for k in masks:
            cand = {c for c, f in frac.items() if f[k] >= cfg["start_min"]
                    and f[k] >= max(f[o] for o in masks if o != k)}
            grp = _largest_group(cand, g) if cand else set()
            start[k] = grp if len(grp) >= int(cfg["min_group"]) else set()

        v = BoardView()
        starts = start["red"] | start["blue"]
        tiles = {c for c, f in frac.items() if f["floor"] >= cfg["walk_min"]} | starts
        board: set[int] = set()
        stack = list(starts)
        while stack:
            c = stack.pop()
            if c in board:
                continue
            board.add(c)
            stack.extend(n for n in g.neighbours(c) if n in tiles and n not in board)
        ring = {n for c in board for n in g.neighbours(c)} - board
        # non-board cells that cannot reach the grid border without crossing the board:
        # holes / obstacles inside the board
        outer_reach: set[int] = set()
        stack = [c for c in range(g.n_cells) if c not in board and len(g.neighbours(c)) < 4]
        while stack:
            c = stack.pop()
            if c in outer_reach or c in board:
                continue
            outer_reach.add(c)
            stack.extend(g.neighbours(c))
        edge = set()
        for c in ring:
            sand = frac.get(c, {}).get("sand", 0.0)
            if c not in outer_reach or sand < cfg["sand_max"]:
                edge.add(c)      # enclosed, or an object (crate, wall) at the board edge
        for cell in range(g.n_cells):
            if cell in start["red"]:
                kind = "red"
            elif cell in start["blue"]:
                kind = "blue"
            elif cell in board:
                kind = "walkable"
            elif cell in edge:
                kind = "blocked"
            elif cell in unknown:
                kind = "unknown"
            else:
                kind = "outside"
            v.kinds[cell] = kind
            getattr(v, kind).append(cell)
        for k in ("red", "blue"):
            for c in getattr(v, k):
                if frac[c][k + "_in"] < cfg["free_min"]:
                    v.occupied.append(c)
        v.occupied.sort()
        if not v.is_placement:          # one side only: decor, not a placement board
            v.occupied = []
        ours = self.our_colour
        if ours not in ("red", "blue"):
            n_red = sum(c in v.red for c in v.occupied)
            n_blue = len(v.occupied) - n_red
            ours = ("red" if n_red < n_blue else "blue" if n_blue < n_red
                    else self.fallback_colour)
        v.our_colour = ours
        theirs = "blue" if ours == "red" else "red"
        v.allies = [c for c in v.occupied if c in getattr(v, ours)]
        v.enemies = [c for c in v.occupied if c in getattr(v, theirs)]
        v.self_cell = v.allies[0] if v.allies else None
        v._frac = frac  # debug: per-cell fractions
        return v


COLOURS = {"walkable": (222, 214, 170), "blocked": (120, 75, 50), "outside": (35, 35, 40), "unknown": (150, 120, 190),
           "red": (230, 90, 60), "blue": (70, 130, 220)}


def render(img: np.ndarray, v: BoardView, grid: Grid, scale: float = 1.0) -> "Image.Image":
    """Side by side: the real frame (left) and the parsed board drawn as diamonds (right)."""
    from PIL import Image, ImageDraw
    H, W = img.shape[:2]
    out = Image.new("RGB", (W * 2 + 10, H), (255, 255, 255))
    out.paste(Image.fromarray(img[..., :3].astype(np.uint8)), (0, 0))
    board = Image.new("RGB", (W, H), (25, 25, 30))
    d = ImageDraw.Draw(board)
    for cell, kind in v.kinds.items():
        pts = [(x * scale, y * scale) for x, y in grid.diamond(cell)]
        d.polygon(pts, fill=COLOURS.get(kind, (255, 0, 255)), outline=(15, 15, 15))
        x, y = grid.center_px(cell, scale)
        if cell == v.self_cell or cell in v.allies or cell in v.enemies:
            r = grid.ch * scale * 0.3
            col = (40, 220, 60) if cell == v.self_cell else (255, 230, 0) if cell in v.allies else (200, 0, 0)
            d.ellipse([x - r, y - r, x + r, y + r], fill=col, outline=(0, 0, 0), width=2)
            d.text((x - 8, y - 6), "ME" if cell == v.self_cell else "E" if cell in v.enemies else "A",
                   fill=(0, 0, 0))
    leg = [("walkable", "walkable"), ("blocked", "blocked (on board)"), ("outside", "outside board"), ("unknown", "unknown"),
           ("red", "red start"), ("blue", "blue start")]
    for i, (k, label) in enumerate(leg):
        d.rectangle([10, 10 + i * 18, 24, 24 + i * 18], fill=COLOURS[k])
        d.text((30, 11 + i * 18), label, fill=(255, 255, 255))
    d.text((10, 10 + len(leg) * 18), f"green = me ({v.self_cell}), red dot = enemy {v.enemies}",
           fill=(255, 255, 255))
    out.paste(board, (W + 10, 0))
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    from pathlib import Path

    from PIL import Image

    ap = argparse.ArgumentParser(description="Parse a placement frame (no image display).")
    ap.add_argument("frame")
    ap.add_argument("--grid", default=str(Path(__file__).resolve().parents[2] / "config" / "grid.json"))
    ap.add_argument("--ours", default="auto", choices=["auto", "red", "blue"])
    ap.add_argument("--debug", action="store_true", help="print per-start-cell fractions")
    ap.add_argument("--render", help="write a side-by-side PNG (real frame | parsed board)")
    a = ap.parse_args(argv)
    img = np.asarray(Image.open(a.frame).convert("RGB"))
    grid = Grid.from_config(json.loads(Path(a.grid).read_text()))
    scale = img.shape[1] / 1280.0
    v = PlacementBoardParser(grid, our_colour=a.ours).read(img, scale)
    print(v.ascii(grid))
    print(json.dumps(v.summary()))
    if a.render:
        render(img, v, grid, scale).save(a.render)
        print("wrote", a.render)
    if a.debug:
        for c in v.red + v.blue:
            print(c, divmod(c, grid.cols), {k: round(x, 2) for k, x in v._frac[c].items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

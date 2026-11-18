"""Reader for the client's "PRÉVISUALISATION DE LA CARTE DE COMBAT" box (shown on the map,
BEFORE entering the fight), plus the anchor that maps its cells onto board cell ids.

Measured on Thomas's client (1280x800 content), preview/current.png, map Incarnam 3,-3:
- the box is open at x 1..230, y 71..214; title bar (61,64,92) rows 71..85; the miniature
  sits in x 3..227, y 86..204 on a dark blue-grey background.
- the miniature is an iso diamond grid, cell width P ~ 27.1 px (0.394 of the 68.8 px board
  cell), drawn MIRRORED left-right relative to the fight camera.
- shades: flat dark-grey diamonds (two checker tones) = walkable floor; red / blue tinted
  diamonds = start cells; light-grey raised blocks = obstacles (crates, walls, the cube);
  background showing through = hole / no cell.

Algorithm:
1. Box: rows whose run of title-bar colour is >= 150 px give the title bar; the map area is the
   rows below it down to the box's bottom strip (longest uniform rows).
2. Lattice: edge magnitude projected on p = x + k*y and q = x - k*y; the period P, the slope
   factor k (the mini diamonds are flatter than 2:1, k ~ 2.15) and the phases come from the
   strongest Fourier component. Cell centres sit half a period from the edges:
   (i, j) -> p = p0 + P*i, q = q0 + P*j, x = (p + q) / 2, y = (p - q) / (2k) (box px).
3. Classify each lattice cell from the median of a small inner diamond: red (r - b > 25 and
   r > 100), blue (b - r > 40), obstacle (luma >= 118), hole (within 14 of the local
   background), else walkable.
4. Anchor: (i, j) -> board (u, v) by one of the 8 lattice symmetries + an integer shift, chosen
   to maximise the overlap of the mini red / blue cells with the board's start cells (from the
   placement parse). On 3,-3 the winner is the left-right mirror (u, v) = (-j + du, -i + dv).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..game.grid import Grid

TITLE_RGB = (61, 64, 92)
TITLE_H = 15                         # title bar rows (71..85 on the measured frame)

SYMS = {
    "id": lambda i, j: (i, j), "swap": lambda i, j: (j, i),
    "neg": lambda i, j: (-i, -j), "mirror": lambda i, j: (-j, -i),
    "fi": lambda i, j: (-i, j), "fj": lambda i, j: (i, -j),
    "r90": lambda i, j: (-j, i), "r270": lambda i, j: (j, -i),
}


@dataclass
class PreviewView:
    box: tuple[int, int, int, int]            # x, y, w, h of the map area (window px)
    period: float
    phase: tuple[float, float]
    slope: float = 2.0                         # k in p = x + k*y (2.0 = a 2:1 diamond)
    cells: dict[tuple[int, int], str] = field(default_factory=dict)   # (i, j) -> kind
    centres: dict[tuple[int, int], tuple[int, int]] = field(default_factory=dict)

    def of_kind(self, kind: str) -> list[tuple[int, int]]:
        return sorted(k for k, v in self.cells.items() if v == kind)


def find_box(img: np.ndarray) -> tuple[int, int, int, int] | None:
    """Map area (x, y, w, h) of the open preview box, or None if it is closed."""
    reg = img[:400, :500, :3].astype(np.int16)
    title = np.abs(reg - np.array(TITLE_RGB)).max(-1) <= 12
    rows = np.flatnonzero(title.sum(1) >= 150)
    if rows.size < 3:
        return None
    y_top = int(rows[0])
    y_bar = y_top + TITLE_H - 1          # the map background has the title colour too
    cols = np.flatnonzero(title[y_top:y_top + 3].any(0))
    x0, x1 = int(cols[0]), int(cols[-1])
    # bottom: first row (well below the bar) that is one uniform colour run (bottom strip)
    y1 = y_bar + 1
    for y in range(y_bar + 20, reg.shape[0]):
        d = np.abs(np.diff(reg[y, x0 + 1:x1], axis=0)).max(-1) <= 6
        if d.mean() > 0.97:
            y1 = y
            break
    else:
        return None
    return (x0 + 2, y_bar + 1, x1 - x0 - 3, y1 - y_bar - 1)


def _lattice(box: np.ndarray) -> tuple[float, float, float, float]:
    """(period P, slope factor k, centre phase p0, q0) with p = x + k*y, q = x - k*y."""
    L = box.astype(float).mean(-1)
    e = np.abs(np.diff(L, axis=1))[:-1] + np.abs(np.diff(L, axis=0))[:, :-1]
    H, W = e.shape
    yy, xx = np.mgrid[0:H, 0:W]
    best = None
    for k in np.arange(1.8, 2.5, 0.02):
        for P in np.arange(24.0, 32.0, 0.05):
            zp = (e * np.exp(2j * np.pi * (xx + k * yy) / P)).sum()
            zq = (e * np.exp(2j * np.pi * (xx - k * yy) / P)).sum()
            score = abs(zp) + abs(zq)
            if best is None or score > best[0]:
                best = (score, P, k, zp, zq)
    _, P, k, zp, zq = best
    edge_p = (np.angle(zp) / (2 * np.pi) * P) % P
    edge_q = (np.angle(zq) / (2 * np.pi) * P) % P
    return float(P), float(k), float((edge_p + P / 2) % P), float((edge_q + P / 2) % P)


class TerrainPreviewParser:
    def read(self, img: np.ndarray) -> PreviewView | None:
        rect = find_box(img)
        if rect is None:
            return None
        x, y, w, h = rect
        box = img[y:y + h, x:x + w, :3].astype(np.int16)
        P, kk, p0, q0 = _lattice(box)
        bg_row = np.median(np.concatenate([box[:, :4], box[:, -4:]], 1), 1)
        ry, rx = int(P / 8), int(P / 4)
        ly, lx = np.mgrid[-ry:ry + 1, -rx:rx + 1]
        mask = (np.abs(lx) / (P / 2) + np.abs(ly) / (P / (2 * kk))) <= 0.6
        v = PreviewView(box=rect, period=P, phase=(p0, q0), slope=kk)
        n = int(2 * (w + 2 * h) / P) + 2
        for i in range(-n, n):
            for j in range(-n, n):
                p, q = p0 + P * i, q0 + P * j
                cx, cy = int(round((p + q) / 2)), int(round((p - q) / (2 * kk)))
                if not (ry <= cy < h - ry and rx <= cx < w - rx):
                    continue
                med = np.median(box[cy - ry:cy + ry + 1, cx - rx:cx + rx + 1][mask], 0)
                r, g, b = (float(t) for t in med)
                if r - b > 25 and r > 100:
                    k = "red"
                elif b - r > 40:
                    k = "blue"
                elif med.mean() >= 118:
                    k = "obstacle"
                elif np.abs(med - bg_row[cy]).max() <= 14:
                    k = "hole"
                else:
                    k = "walkable"
                v.cells[(i, j)] = k
                v.centres[(i, j)] = (x + cx, y + cy)
        return v


@dataclass
class Anchor:
    sym: str
    du: int
    dv: int
    score: int

    def cell(self, grid: Grid, ij: tuple[int, int]) -> int | None:
        a, b = SYMS[self.sym](*ij)
        return grid.from_uv(a + self.du, b + self.dv)


def anchor(view: PreviewView, grid: Grid, red: list[int], blue: list[int],
           syms: tuple[str, ...] = tuple(SYMS), board: dict[int, str] | None = None) -> Anchor:
    """Best symmetry + shift mapping the mini cells onto board ids.

    Score = start cells landing on the board's start cells of the same colour; when a board
    parse is given (cell -> kind), plus walkable/not-walkable agreement minus disagreement.
    On map 3,-3 starts alone pick mirror (17, 3) (red 8/8, blue 5/8, floor 79/128); starts +
    shape pick mirror (16, 4) (red 3/8, blue 6/8, floor 90/128)."""
    uv_r, uv_b = {grid.uv(c) for c in red}, {grid.uv(c) for c in blue}
    walk = {"walkable", "red", "blue"}
    bw = {grid.uv(c) for c, k in (board or {}).items() if k in walk}
    bn = {grid.uv(c) for c, k in (board or {}).items() if k not in walk}
    best = Anchor("id", 0, 0, -10 ** 6)
    for name in syms:
        f = SYMS[name]
        fr = [f(*k) for k in view.of_kind("red")]
        fb = [f(*k) for k in view.of_kind("blue")]
        mw = [f(*ij) for ij, k in view.cells.items() if k in ("walkable", "red", "blue")]
        mn = [f(*ij) for ij, k in view.cells.items() if k not in ("walkable", "red", "blue")]
        cand = {(a - x, b - y) for (x, y) in fr for (a, b) in uv_r} | \
               {(a - x, b - y) for (x, y) in fb for (a, b) in uv_b}
        for du, dv in cand:
            sc = sum((x + du, y + dv) in uv_r for x, y in fr) + \
                 sum((x + du, y + dv) in uv_b for x, y in fb)
            if board:
                sc += sum((x + du, y + dv) in bw for x, y in mw) + sum((x + du, y + dv) in bn for x, y in mn)
                sc -= sum((x + du, y + dv) in bn for x, y in mw) + sum((x + du, y + dv) in bw for x, y in mn)
            if sc > best.score:
                best = Anchor(name, du, dv, sc)
    return best


def board_kinds(view: PreviewView, grid: Grid, a: Anchor) -> dict[int, str]:
    """cell id -> walkable | blocked | hole | red | blue for every mini cell on the grid."""
    out: dict[int, str] = {}
    for ij, k in view.cells.items():
        c = a.cell(grid, ij)
        if c is not None:
            out[c] = "blocked" if k == "obstacle" else k
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    from pathlib import Path

    from PIL import Image

    from .placement_board import PlacementBoardParser

    ap = argparse.ArgumentParser(description="Parse the terrain preview box (no display).")
    ap.add_argument("frame", help="map frame with the Prévisualisation box open")
    ap.add_argument("--placement", help="placement frame of the same map, to anchor + compare")
    ap.add_argument("--shape", action="store_true", help="anchor on start cells + board shape")
    ap.add_argument("--grid", default=str(Path(__file__).resolve().parents[2] / "config" / "grid.json"))
    a = ap.parse_args(argv)
    grid = Grid.from_config(json.loads(Path(a.grid).read_text()))
    img = np.asarray(Image.open(a.frame).convert("RGB"))
    v = TerrainPreviewParser().read(img)
    if v is None:
        print("preview box closed / not found")
        return 1
    from collections import Counter
    print("box", v.box, "period %.2f" % v.period, dict(Counter(v.cells.values())))
    if not a.placement:
        return 0
    pimg = np.asarray(Image.open(a.placement).convert("RGB"))
    b = PlacementBoardParser(grid).read(pimg, pimg.shape[1] / 1280.0)
    an = anchor(v, grid, b.red, b.blue, board=b.kinds if a.shape else None)
    kinds0 = board_kinds(v, grid, an)
    print("anchor", an, "| start cells matched: red %d/%d blue %d/%d" % (
        sum(kinds0.get(c) == "red" for c in b.red), len(b.red),
        sum(kinds0.get(c) == "blue" for c in b.blue), len(b.blue)))
    kinds = board_kinds(v, grid, an)
    walk = {"walkable", "red", "blue"}
    agree = sum((kinds[c] in walk) == (b.kinds[c] in walk) for c in kinds)
    print(f"walkable/not agreement on {len(kinds)} mapped cells: {agree}/{len(kinds)}"
          f" = {agree / max(1, len(kinds)):.0%}")
    bad = sorted((c, kinds[c], b.kinds[c]) for c in kinds if (kinds[c] in walk) != (b.kinds[c] in walk))
    print("disagree (cell, preview, board):", bad)
    print(json.dumps({"walkable": sorted(c for c, k in kinds.items() if k == "walkable"),
                      "blocked": sorted(c for c, k in kinds.items() if k == "blocked"),
                      "hole": sorted(c for c, k in kinds.items() if k == "hole"),
                      "red": sorted(c for c, k in kinds.items() if k == "red"),
                      "blue": sorted(c for c, k in kinds.items() if k == "blue")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

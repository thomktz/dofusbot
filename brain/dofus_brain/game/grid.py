"""Isometric map grid (protocol §1).

Cell ids 0..559, 14 per row, 40 rows, odd rows shifted right by half a cell.
    row = id // 14, col = id % 14
    x = ox + col*cw + (row % 2)*cw/2,   y = oy + row*ch/2        (window points)

For distances/LOS we use a proper iso transform. Let X2 = 2*col + row%2 (half-cell-width
units) and Y = row (half-cell-height units). X2 + Y is always even, so
    u = (X2 + Y) / 2,   v = (X2 - Y) / 2
are integers and the diamond cells form a regular square lattice in (u, v):
the 4 edge-sharing neighbours of a cell differ by ±1 in exactly one of u, v.
Movement distance in Dofus = |du| + |dv| (Manhattan on that lattice).
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Iterable

ROWS = 40
COLS = 14
N_CELLS = ROWS * COLS  # 560


@dataclass(frozen=True)
class Grid:
    ox: float = 180.0
    oy: float = 40.0
    cw: float = 68.0
    ch: float = 34.0
    rows: int = ROWS
    cols: int = COLS

    @classmethod
    def from_config(cls, cfg: dict) -> "Grid":
        return cls(ox=float(cfg.get("ox", 180.0)), oy=float(cfg.get("oy", 40.0)),
                   cw=float(cfg.get("cw", 68.0)), ch=float(cfg.get("ch", 34.0)),
                   rows=int(cfg.get("rows", ROWS)), cols=int(cfg.get("cols", COLS)))

    @property
    def n_cells(self) -> int:
        return self.rows * self.cols

    # --- id <-> (row, col)
    def is_valid(self, cell: int) -> bool:
        return 0 <= cell < self.n_cells

    def rc(self, cell: int) -> tuple[int, int]:
        if not self.is_valid(cell):
            raise ValueError(f"cell {cell} out of range")
        return divmod(cell, self.cols)

    def cell_id(self, row: int, col: int) -> int:
        if not (0 <= row < self.rows and 0 <= col < self.cols):
            raise ValueError(f"(row={row}, col={col}) out of range")
        return row * self.cols + col

    # --- (row, col) <-> window points
    def center(self, cell: int) -> tuple[float, float]:
        row, col = self.rc(cell)
        x = self.ox + col * self.cw + (row % 2) * self.cw / 2
        y = self.oy + row * self.ch / 2
        return (x, y)

    def center_px(self, cell: int, scale: float) -> tuple[float, float]:
        x, y = self.center(cell)
        return (x * scale, y * scale)

    def cell_at(self, x: float, y: float) -> int | None:
        """Window point -> cell whose diamond contains it (None if off-grid)."""
        # continuous half-units relative to cell (0,0) centre
        X2 = (x - self.ox) / (self.cw / 2)
        Y = (y - self.oy) / (self.ch / 2)
        u = round((X2 + Y) / 2)
        v = round((X2 - Y) / 2)
        return self.from_uv(u, v)

    def diamond(self, cell: int) -> list[tuple[float, float]]:
        x, y = self.center(cell)
        return [(x, y - self.ch / 2), (x + self.cw / 2, y), (x, y + self.ch / 2), (x - self.cw / 2, y)]

    # --- iso lattice
    def uv(self, cell: int) -> tuple[int, int]:
        row, col = self.rc(cell)
        x2 = 2 * col + (row % 2)
        return ((x2 + row) // 2, (x2 - row) // 2)

    def from_uv(self, u: int, v: int) -> int | None:
        x2 = u + v
        row = u - v
        if row < 0 or row >= self.rows:
            return None
        if (x2 - (row % 2)) % 2:
            return None
        col = (x2 - (row % 2)) // 2
        if col < 0 or col >= self.cols:
            return None
        return row * self.cols + col

    def distance(self, a: int, b: int) -> int:
        ua, va = self.uv(a)
        ub, vb = self.uv(b)
        return abs(ua - ub) + abs(va - vb)

    def neighbours(self, cell: int) -> list[int]:
        u, v = self.uv(cell)
        out = []
        for du, dv in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = self.from_uv(u + du, v + dv)
            if n is not None:
                out.append(n)
        return out

    def in_line(self, a: int, b: int) -> bool:
        ua, va = self.uv(a)
        ub, vb = self.uv(b)
        return ua == ub or va == vb

    # --- line of sight
    def line_cells(self, a: int, b: int) -> list[int]:
        """Cells crossed by the segment between the centres of a and b, in (u, v) space
        (supercover DDA). When the segment passes exactly through a lattice corner both
        side cells are returned as a pair-group; see `has_los` for how they are treated."""
        return [c for group in self._line_groups(a, b) for c in group]

    def _line_groups(self, a: int, b: int) -> list[tuple[int, ...]]:
        ua, va = self.uv(a)
        ub, vb = self.uv(b)
        du, dv = ub - ua, vb - va
        nu, nv = abs(du), abs(dv)
        su = 1 if du > 0 else -1
        sv = 1 if dv > 0 else -1
        u, v = ua, va
        groups: list[tuple[int, ...]] = []
        iu = iv = 0
        while iu < nu or iv < nv:
            # compare (0.5+iu)/nu with (0.5+iv)/nv without division
            lhs = (1 + 2 * iu) * nv
            rhs = (1 + 2 * iv) * nu
            if lhs == rhs:  # exact corner: both side cells touched, then diagonal step
                side1 = self.from_uv(u + su, v)
                side2 = self.from_uv(u, v + sv)
                groups.append(tuple(c for c in (side1, side2) if c is not None))
                u += su
                v += sv
                iu += 1
                iv += 1
            elif lhs < rhs:
                u += su
                iu += 1
            else:
                v += sv
                iv += 1
            c = self.from_uv(u, v)
            if c is not None:
                groups.append((c,))
        return groups

    def has_los(self, a: int, b: int, blockers: Iterable[int]) -> bool:
        """True if nothing in `blockers` lies strictly between a and b.
        Corner crossings block only if both side cells are blockers (permissive, like Dofus)."""
        if a == b:
            return True
        blk = set(blockers)
        for group in self._line_groups(a, b):
            if group == (b,):
                continue
            if group and all(c in blk for c in group):
                return False
        return True

    # --- movement
    def reachable(self, start: int, pm: int, blocked: Iterable[int] = (), occupied: Iterable[int] = ()) -> dict[int, int]:
        """BFS: cell -> path length for every cell reachable with <= pm steps
        (start included with 0). Blocked and occupied cells are impassable."""
        walls = set(blocked) | set(occupied)
        walls.discard(start)
        dist = {start: 0}
        q = deque([start])
        while q:
            c = q.popleft()
            if dist[c] >= pm:
                continue
            for n in self.neighbours(c):
                if n in walls or n in dist:
                    continue
                dist[n] = dist[c] + 1
                q.append(n)
        return dist

    def path(self, start: int, goal: int, blocked: Iterable[int] = (), occupied: Iterable[int] = ()) -> list[int] | None:
        walls = (set(blocked) | set(occupied)) - {start, goal}
        prev: dict[int, int | None] = {start: None}
        q = deque([start])
        while q:
            c = q.popleft()
            if c == goal:
                out = [c]
                while prev[out[-1]] is not None:
                    out.append(prev[out[-1]])  # type: ignore[arg-type]
                return out[::-1]
            for n in self.neighbours(c):
                if n not in walls and n not in prev:
                    prev[n] = c
                    q.append(n)
        return None

    # --- map edges (for changing map)
    def edge_cells(self, direction: str) -> list[int]:
        if direction == "right":
            return [self.cell_id(r, self.cols - 1) for r in range(1, self.rows, 2)]
        if direction == "left":
            return [self.cell_id(r, 0) for r in range(0, self.rows, 2)]
        if direction == "top":
            return [self.cell_id(0, c) for c in range(self.cols)]
        if direction == "bottom":
            return [self.cell_id(self.rows - 1, c) for c in range(self.cols)]
        raise ValueError(direction)


def map_distance(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Distance between world maps (map coordinates, x right, y down): maps connect by
    their 4 edges, so it is Manhattan distance."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def direction_between(a: tuple[int, int], b: tuple[int, int]) -> str | None:
    d = (b[0] - a[0], b[1] - a[1])
    return {(1, 0): "right", (-1, 0): "left", (0, -1): "top", (0, 1): "bottom"}.get(d)


def euclid(p: tuple[float, float], q: tuple[float, float]) -> float:
    return math.hypot(p[0] - q[0], p[1] - q[1])

"""Protocol key-cell paths for role-play movement.

This mirrors AstraBot's `MapPoint` orientation and key-cell encoding without
depending on its Windows-only runtime. It is intentionally separate from the
combat grid solver: role-play movement permits all eight Dofus directions and
the server expects compressed orientation-bearing key cells.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable

from ..game.grid import Grid


@dataclass(frozen=True)
class RoleplayPath:
    cells: tuple[int, ...]
    key_cells: tuple[int, ...]


def _direction(grid: Grid, source: int, target: int) -> int:
    sx, sy = grid.uv(source)
    tx, ty = grid.uv(target)
    dx = 1 if tx > sx else -1 if tx < sx else 0
    dy = 1 if ty > sy else -1 if ty < sy else 0
    # Exact Astra MapPoint.orientation_to mapping: x=u, y=v.
    return {
        (1, 1): 0, (1, 0): 1, (1, -1): 2, (0, -1): 3,
        (-1, -1): 4, (-1, 0): 5, (-1, 1): 6, (0, 1): 7,
    }[(dx, dy)]


def roleplay_neighbours(grid: Grid, cell: int) -> list[int]:
    u, v = grid.uv(cell)
    out: list[int] = []
    for du, dv in ((1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1), (0, 1)):
        candidate = grid.from_uv(u + du, v + dv)
        if candidate is not None:
            out.append(candidate)
    return out


def key_cell(cell: int, direction: int) -> int:
    if not 0 <= int(cell) < 560 or not 0 <= int(direction) < 8:
        raise ValueError("invalid Dofus role-play key cell")
    return int(cell) | (int(direction) << 12)


def find_roleplay_path(start: int, goal: int, blocked: Iterable[int] = (), grid: Grid | None = None) -> RoleplayPath | None:
    """Find a shortest eight-direction path and Astra-compatible key cells."""
    grid = grid or Grid()
    if not grid.is_valid(start) or not grid.is_valid(goal):
        raise ValueError("role-play path endpoints must be valid map cells")
    if start == goal:
        return RoleplayPath((start,), ())
    walls = {int(cell) for cell in blocked if grid.is_valid(int(cell))} - {start, goal}
    previous: dict[int, int | None] = {start: None}
    queue: deque[int] = deque([start])
    while queue:
        cell = queue.popleft()
        if cell == goal:
            break
        for candidate in roleplay_neighbours(grid, cell):
            if candidate not in walls and candidate not in previous:
                previous[candidate] = cell
                queue.append(candidate)
    if goal not in previous:
        return None
    cells = [goal]
    while previous[cells[-1]] is not None:
        cells.append(previous[cells[-1]])  # type: ignore[arg-type]
    cells.reverse()
    directions = [_direction(grid, cells[index], cells[index + 1]) for index in range(len(cells) - 1)]
    keys: list[int] = []
    current: int | None = None
    for index, direction in enumerate(directions):
        if direction != current:
            keys.append(key_cell(cells[index], direction))
            current = direction
    keys.append(key_cell(cells[-1], directions[-1]))
    return RoleplayPath(tuple(cells), tuple(keys))

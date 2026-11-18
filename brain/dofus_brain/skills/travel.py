"""Map travel: a learned map graph, A* routes, edge-cell click planning, verification by the
map coordinates changing, and stuck detection.

World maps are a grid of coordinates (x right, y down) connected by their 4 edges. We assume
every edge leads to the neighbouring coordinate until observed otherwise: a transition that
lands somewhere else is recorded as a real link, and an edge that never works after several
different edge cells is marked blocked. The graph persists to data/maps.json.
"""
from __future__ import annotations

import heapq
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..game.grid import Grid, direction_between, map_distance
from ..game.state import GameState
from ..planner import PlanContext, PlannedStep, StepResult
from . import Outcome, StepOrOutcome, wait

Coords = tuple[int, int]
DIRECTIONS: dict[str, Coords] = {"right": (1, 0), "left": (-1, 0), "top": (0, -1), "bottom": (0, 1)}


def neighbour(c: Coords, direction: str) -> Coords:
    dx, dy = DIRECTIONS[direction]
    return (c[0] + dx, c[1] + dy)


@dataclass
class MapNode:
    coords: Coords
    links: dict[str, Coords] = field(default_factory=dict)       # observed: direction -> destination
    blocked: set[str] = field(default_factory=set)              # directions that do not work
    failed_cells: dict[str, list[int]] = field(default_factory=dict)  # direction -> edge cells that failed
    visits: int = 0

    def to_json(self) -> dict:
        return {"links": {d: list(c) for d, c in self.links.items()}, "blocked": sorted(self.blocked),
                "failed_cells": self.failed_cells, "visits": self.visits}

    @classmethod
    def from_json(cls, coords: Coords, d: dict) -> "MapNode":
        return cls(coords, {k: (int(v[0]), int(v[1])) for k, v in d.get("links", {}).items()},
                   set(d.get("blocked", [])), {k: list(v) for k, v in d.get("failed_cells", {}).items()},
                   int(d.get("visits", 0)))


class MapGraph:
    def __init__(self, bounds: tuple[int, int, int, int] | None = (-100, -100, 100, 100)):
        self.nodes: dict[Coords, MapNode] = {}
        self.bounds = bounds  # xmin, ymin, xmax, ymax: A* never leaves this box

    def node(self, c: Coords) -> MapNode:
        c = (int(c[0]), int(c[1]))
        if c not in self.nodes:
            self.nodes[c] = MapNode(c)
        return self.nodes[c]

    # -- learning
    def record_transition(self, src: Coords, direction: str, dst: Coords) -> None:
        n = self.node(src)
        n.links[direction] = (int(dst[0]), int(dst[1]))
        n.blocked.discard(direction)
        n.failed_cells.pop(direction, None)
        self.node(dst).visits += 1

    def record_failure(self, src: Coords, direction: str, cell: int, cells_per_side: int) -> bool:
        """Remember a failed edge cell. Returns True if the edge is now marked blocked."""
        n = self.node(src)
        cells = n.failed_cells.setdefault(direction, [])
        if cell not in cells:
            cells.append(cell)
        if len(cells) >= cells_per_side and direction not in n.links:
            n.blocked.add(direction)
            return True
        return False

    def exits(self, c: Coords) -> list[tuple[str, Coords]]:
        n = self.nodes.get(c)
        out = []
        for d in DIRECTIONS:
            if n is not None and d in n.blocked:
                continue
            dst = n.links[d] if n is not None and d in n.links else neighbour(c, d)
            if self.bounds is not None:
                x0, y0, x1, y1 = self.bounds
                if not (x0 <= dst[0] <= x1 and y0 <= dst[1] <= y1):
                    continue
            out.append((d, dst))
        return out

    # -- routing
    def route(self, start: Coords, goal: Coords, cost: Callable[[Coords], float] | None = None,
              max_expand: int = 20000) -> list[Coords] | None:
        """A* over maps. `cost(map)` adds an entry cost (e.g. aggro danger); base cost is 1.
        Learned links may jump, so the heuristic is Manhattan distance capped to stay admissible
        only when no jumping link exists (we then fall back to Dijkstra)."""
        start, goal = tuple(start), tuple(goal)  # type: ignore[assignment]
        if start == goal:
            return [start]
        jumps = any(map_distance(n.coords, d) != 1 for n in self.nodes.values() for d in n.links.values())
        h = (lambda c: 0.0) if jumps else (lambda c: float(map_distance(c, goal)))
        best: dict[Coords, float] = {start: 0.0}
        prev: dict[Coords, Coords] = {}
        heap = [(h(start), 0.0, start)]
        expanded = 0
        while heap:
            _, g, c = heapq.heappop(heap)
            if c == goal:
                path = [c]
                while path[-1] in prev:
                    path.append(prev[path[-1]])
                return path[::-1]
            if g > best.get(c, float("inf")):
                continue
            expanded += 1
            if expanded > max_expand:
                return None
            for _, dst in self.exits(c):
                ng = g + 1.0 + (cost(dst) if cost else 0.0)
                if ng < best.get(dst, float("inf")):
                    best[dst] = ng
                    prev[dst] = c
                    heapq.heappush(heap, (ng + h(dst), ng, dst))
        return None

    def direction_to(self, src: Coords, dst: Coords) -> str | None:
        for d, target in self.exits(src):
            if target == tuple(dst):
                return d
        return direction_between(src, dst)

    # -- persistence
    def to_json(self) -> dict:
        return {"maps": {f"{c[0]},{c[1]}": n.to_json() for c, n in sorted(self.nodes.items())}}

    @classmethod
    def from_json(cls, d: dict) -> "MapGraph":
        g = cls()
        for key, nd in d.get("maps", {}).items():
            x, y = (int(v) for v in key.split(","))
            g.nodes[(x, y)] = MapNode.from_json((x, y), nd)
        return g

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "MapGraph":
        if not path.exists():
            return cls()
        return cls.from_json(json.loads(path.read_text(encoding="utf-8")))


def edge_cell_order(grid: Grid, direction: str) -> list[int]:
    """Edge cells of one side, middle first, then alternating outwards (middle cells are the
    least likely to be hidden by UI or decor)."""
    cells = grid.edge_cells(direction)
    mid = (len(cells) - 1) / 2
    return sorted(cells, key=lambda c: (abs(cells.index(c) - mid), cells.index(c)))


def coords_changed_from(src: Coords) -> Callable[[GameState, GameState], bool]:
    """Verify: we are on a readable world-ish screen whose coords differ from `src`."""
    def check(before: GameState, now: GameState) -> bool:
        return (now.screen in ("world", "dialog", "popup") and now.map.coords is not None
                and tuple(now.map.coords) != tuple(src))
    return check


class TravelSkill:
    """Go to `goal` map coordinates, one map edge at a time."""

    name = "travel"

    def __init__(self, goal: Coords, graph: MapGraph | None = None, cfg: dict | None = None,
                 danger: Callable[[Coords], float] | None = None, avoid_cells: Callable[[int], bool] | None = None):
        from .config import PHASES_DEFAULTS

        self.goal = (int(goal[0]), int(goal[1]))
        self.graph = graph or MapGraph()
        tc = (cfg or PHASES_DEFAULTS)["travel"]
        self.cells_per_side = int(tc.get("edge_cells_per_side", 3))
        self.max_stuck = int(tc.get("max_stuck_steps", 6))
        self.danger = danger
        self.avoid_cells = avoid_cells     # e.g. cells next to an aggressive group on this map
        self.stuck = 0                     # consecutive failed steps
        self.route: list[Coords] | None = None
        self.last_reason = ""

    def plan_route(self, here: Coords) -> list[Coords] | None:
        self.route = self.graph.route(here, self.goal, self.danger)
        return self.route

    def step(self, state: GameState, ctx: PlanContext) -> StepOrOutcome:
        if state.screen == "loading":
            return wait("loading")
        if state.screen != "world":
            return wait(f"not on world screen ({state.screen})")
        here = state.map.coords
        if here is None:
            return Outcome("ask", "map coordinates unreadable")
        here = (int(here[0]), int(here[1]))
        if here == self.goal:
            return Outcome("done", f"arrived at {self.goal}")
        if self.stuck >= self.max_stuck:
            return Outcome("failed", f"stuck at {here} after {self.stuck} failed steps")
        route = self.plan_route(here)
        if not route or len(route) < 2:
            return Outcome("failed", f"no route from {here} to {self.goal}")
        nxt = route[1]
        direction = self.graph.direction_to(here, nxt)
        if direction is None:
            return Outcome("failed", f"no edge from {here} to {nxt}")
        failed = set(self.graph.node(here).failed_cells.get(direction, []))
        candidates = [c for c in edge_cell_order(ctx.grid, direction)
                      if c not in failed and not (self.avoid_cells and self.avoid_cells(c))]
        if not candidates:
            self.graph.node(here).blocked.add(direction)
            return self.step(state, ctx)
        cell = candidates[0]
        x, y = ctx.grid.center(cell)
        return PlannedStep(
            action={"kind": "click", "x": round(x, 1), "y": round(y, 1), "cell": cell,
                    "reason": f"travel {direction} {here} -> {nxt} (goal {self.goal})"},
            task=f"travel to {self.goal}: {here} -> {nxt}",
            verify=coords_changed_from(here),
            meta={"skill": self.name, "from": here, "to": nxt, "direction": direction, "cell": cell})

    def on_result(self, result: StepResult, state: GameState) -> None:
        m = result.step.meta
        if m.get("skill") != self.name:
            return
        src, direction, cell = tuple(m["from"]), m["direction"], int(m["cell"])
        if result.ok and state.map.coords is not None:
            dst = (int(state.map.coords[0]), int(state.map.coords[1]))
            self.graph.record_transition(src, direction, dst)  # type: ignore[arg-type]
            self.stuck = 0
            self.last_reason = "moved" if dst == tuple(m["to"]) else f"landed on {dst} instead of {m['to']}"
        else:
            self.stuck += 1
            blocked = self.graph.record_failure(src, direction, cell, self.cells_per_side)  # type: ignore[arg-type]
            self.last_reason = f"edge {direction} blocked" if blocked else f"cell {cell} failed ({result.reason})"

"""Aggro: remember monster groups seen with the Z key, decide engage/avoid with a rule list,
feed danger into travel (route cost + cells to avoid), and recognise being aggroed.

A group that started a fight without us clicking it is marked aggressive (by name), so it is
avoided on later maps too.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from ..game.grid import Grid
from ..game.state import GameState
from ..perception.entities import Plate
from ..planner import PlanContext, PlannedStep, StepResult
from . import Outcome, StepOrOutcome

Coords = tuple[int, int]


@dataclass
class MonsterGroup:
    name: str
    coords: Coords
    cell: int | None
    level: int | None = None
    size: int = 1
    seen_at: float = 0.0


def groups_from_plates(plates: list[Plate], coords: Coords, now: float) -> list[MonsterGroup]:
    """Plates with a level or a group size are monsters; plain names are NPCs."""
    out = []
    for p in plates:
        if p.level is None and p.group_size is None:
            continue
        out.append(MonsterGroup(p.name, coords, p.cell, p.level, p.group_size or 1, now))
    return out


class DangerRules:
    def __init__(self, cfg: dict):
        ac = cfg["aggro"]
        self.rules = ac.get("rules", [])
        self.aggressive = {n.casefold() for n in ac.get("aggressive_names", [])}
        self.radius = int(ac.get("default_radius_cells", 4))
        self.cost = float(ac.get("danger_cost", 6.0))

    def is_aggressive(self, g: MonsterGroup) -> bool:
        n = g.name.casefold()
        return any(a in n for a in self.aggressive)

    def engage(self, g: MonsterGroup, char_level: int | None) -> bool:
        """First matching rule decides. A rule matches when its name substring is in the
        group name and every limit it sets holds."""
        for r in self.rules:
            if r.get("name", "").casefold() not in g.name.casefold():
                continue
            if "max_group_size" in r and g.size > int(r["max_group_size"]):
                continue
            if "max_level_ratio" in r:
                if g.level is None or not char_level:
                    continue
                if g.level / char_level > float(r["max_level_ratio"]):
                    continue
            return bool(r.get("engage", False))
        return False

    def learn_aggressive(self, name: str) -> None:
        self.aggressive.add(name.casefold())


class AggroMap:
    def __init__(self, rules: DangerRules, ttl_s: float = 120.0, clock: Callable[[], float] = time.monotonic):
        self.rules = rules
        self.ttl = ttl_s
        self.clock = clock
        self.groups: dict[Coords, list[MonsterGroup]] = {}

    def update(self, coords: Coords, groups: list[MonsterGroup]) -> None:
        self.groups[tuple(coords)] = list(groups)  # type: ignore[index]

    def current(self, coords: Coords) -> list[MonsterGroup]:
        now = self.clock()
        return [g for g in self.groups.get(tuple(coords), []) if now - g.seen_at <= self.ttl]  # type: ignore[arg-type]

    def threats(self, coords: Coords, char_level: int | None) -> list[MonsterGroup]:
        return [g for g in self.current(coords) if self.rules.is_aggressive(g) and not self.rules.engage(g, char_level)]

    def danger_cost(self, char_level: int | None) -> Callable[[Coords], float]:
        def cost(c: Coords) -> float:
            return self.rules.cost if self.threats(c, char_level) else 0.0
        return cost

    def avoid_cells(self, coords: Coords, grid: Grid, char_level: int | None) -> Callable[[int], bool]:
        threat_cells = [g.cell for g in self.threats(coords, char_level) if g.cell is not None]

        def avoid(cell: int) -> bool:
            return any(grid.distance(cell, t) <= self.rules.radius for t in threat_cells)
        return avoid

    def engageable(self, coords: Coords, char_level: int | None) -> list[MonsterGroup]:
        return [g for g in self.current(coords) if self.rules.engage(g, char_level)]


def detect_aggro(prev: GameState | None, now: GameState, last_step_meta: dict | None) -> bool:
    """world -> fight without our last step being a click on a monster (step meta
    target == "monster") = aggro."""
    if prev is None or prev.screen != "world" or now.screen != "fight":
        return False
    return not (last_step_meta and last_step_meta.get("target") == "monster")


def on_aggro(aggro: AggroMap, coords: Coords | None, player_cell: int | None, grid: Grid) -> list[str]:
    """Mark the groups that were closest to us when the fight started as aggressive."""
    if coords is None:
        return []
    groups = aggro.current(coords)
    if not groups:
        return []
    if player_cell is not None:
        known = [g for g in groups if g.cell is not None]
        if known:
            d = min(grid.distance(player_cell, g.cell) for g in known)  # type: ignore[arg-type]
            groups = [g for g in known if grid.distance(player_cell, g.cell) == d]  # type: ignore[arg-type]
    names = sorted({g.name for g in groups})
    for n in names:
        aggro.rules.learn_aggressive(n)
    return names


class EngageSkill:
    """Click an engageable group on this map to start a fight."""

    name = "engage"

    def __init__(self, aggro: AggroMap):
        self.aggro = aggro
        self.tried: set[int] = set()

    def step(self, state: GameState, ctx: PlanContext) -> StepOrOutcome:
        if state.screen == "fight":
            return Outcome("done", "fight started")
        if state.screen != "world" or state.map.coords is None:
            return Outcome("wait", f"screen {state.screen}")
        groups = [g for g in self.aggro.engageable(state.map.coords, state.character.level)
                  if g.cell is not None and g.cell not in self.tried]
        if not groups:
            return Outcome("failed", "no engageable group on this map")
        g = min(groups, key=lambda g: (g.level or 0, g.size))
        x, y = ctx.grid.center(g.cell)  # type: ignore[arg-type]
        return PlannedStep(action={"kind": "click", "x": round(x, 1), "y": round(y, 1), "cell": g.cell,
                                   "reason": f"engage {g.name} (lvl {g.level}, {g.size})"},
                           task=f"engage {g.name}",
                           verify=lambda before, now: now.screen == "fight", timeout_s=6.0, retries=1,
                           meta={"skill": self.name, "cell": g.cell, "target": "monster"})

    def on_result(self, result: StepResult, state: GameState) -> None:
        if result.step.meta.get("skill") == self.name and not result.ok:
            self.tried.add(int(result.step.meta["cell"]))  # group moved: rescan before retrying it

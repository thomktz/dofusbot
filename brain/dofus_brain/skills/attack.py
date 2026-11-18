"""Dashboard goals for the fight flow (Thomas, 9 Nov 23:03):

- `AttackSkill`: attack one monster group picked from the "Locate NPCs" list (a Z scan,
  npcs.list_npcs): click it on the map, then the fight interrupt takes over (placement:
  'Choisir les challenges' -> PRÊT -> turns -> FERMER windows). Done when the fight is over
  and we are back on the map.
- `FightSequenceSkill`: Thomas starts this himself during character placement. It only
  waits; the fight interrupts do the work, and it is done when the fight is over.
"""
from __future__ import annotations

import time
from typing import Callable

from ..game.grid import Grid
from ..game.state import GameState
from ..planner import PlanContext, PlannedStep, StepResult
from . import Outcome, StepOrOutcome, wait


def target_point(ent, grid: Grid | None) -> tuple[float, float]:
    """Where to click a monster: its cell centre when known, else just below the name plate
    (the plate floats above the creature)."""
    if getattr(ent, "cell", None) is not None and grid is not None:
        return grid.center(int(ent.cell))
    return float(ent.x), float(ent.y) + 30.0


def attack_points(ent, grid: Grid | None, n: int = 6) -> list[tuple[float, float, int | None]]:
    """Where to try, in order: the estimated spot, then the cells around it (nearest first).
    The plate is not always straight above its group (it shifts with the screen position),
    so when a click starts no fight we try the neighbours instead of modelling the offset."""
    x0, y0 = target_point(ent, grid)
    if grid is not None:
        base = ent.cell if getattr(ent, "cell", None) is not None else grid.cell_at(x0, y0)
        if base is not None:
            base = int(base)
            ring = [c for c in range(grid.n_cells) if 0 < grid.distance(base, c) <= 2]
            ring.sort(key=lambda c: (grid.distance(base, c),
                                     (grid.center(c)[0] - x0) ** 2 + (grid.center(c)[1] - y0) ** 2))
            return [(*grid.center(c), c) for c in [base, *ring][:n]]
    steps = [(0, 0), (0, 17), (0, -17), (-34, 0), (34, 0), (0, 34)]
    return [(x0 + dx, y0 + dy, None) for dx, dy in steps[:n]]


class FightSequenceSkill:
    """Goal 'fight sequence': nothing to do outside a fight except wait for it to end."""

    name = "fight_sequence"

    def __init__(self, clock: Callable[[], float] = time.monotonic, wait_for_fight_s: float = 30.0):
        self.clock = clock
        self.started = clock()
        self.seen_fight = False
        self.wait_for_fight_s = wait_for_fight_s

    def note(self, state: GameState) -> None:
        if state.screen == "fight":
            self.seen_fight = True

    def step(self, state: GameState, ctx: PlanContext) -> StepOrOutcome:
        self.note(state)
        if state.screen == "fight":
            return wait("fight in progress")
        if self.seen_fight:
            return Outcome("done", "fight over")
        if self.clock() - self.started > self.wait_for_fight_s:
            return Outcome("failed", f"no fight within {self.wait_for_fight_s:.0f}s (start it during placement)")
        return wait("waiting for the fight (placement)")

    def on_result(self, result: StepResult, state: GameState) -> None:
        pass


class AttackSkill:
    """Goal 'attack <name>': click the monster group, then let the fight sequence run."""

    name = "attack"

    def __init__(self, target, clock: Callable[[], float] = time.monotonic, max_clicks: int = 6):
        self.target = target
        self.clock = clock
        self.clicks = 0
        self.max_clicks = max_clicks
        self.seq = FightSequenceSkill(clock, wait_for_fight_s=1e9)
        self.points: list[tuple[float, float, int | None]] | None = None

    def note(self, state: GameState) -> None:
        self.seq.note(state)

    def step(self, state: GameState, ctx: PlanContext) -> StepOrOutcome:
        self.seq.note(state)
        if self.seq.seen_fight:
            return self.seq.step(state, ctx)            # wait for the fight to end
        if state.screen != "world":
            return wait("waiting for the map")
        if self.points is None:
            self.points = attack_points(self.target, ctx.grid, self.max_clicks)
        if self.clicks >= min(self.max_clicks, len(self.points)):
            return Outcome("failed", f"clicked {self.target.name} {self.clicks}x around it, no fight started")
        x, y, cell = self.points[self.clicks]

        def fight_started(before: GameState, now: GameState) -> bool:
            return now.screen == "fight" or (now.screen == "loading")

        return PlannedStep(action={"kind": "click", "x": round(x, 1), "y": round(y, 1),
                                   **({"cell": cell} if cell is not None else {}),
                                   "reason": f"attack {self.target.name} (try {self.clicks + 1})"},
                           task=f"attack {self.target.name}", verify=fight_started, timeout_s=8.0, retries=0,
                           meta={"skill": self.name, "op": "attack", "target": self.target.name})

    def on_result(self, result: StepResult, state: GameState) -> None:
        if result.step.meta.get("op") == "attack":
            self.clicks += 1

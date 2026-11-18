"""DemoWalkPlanner: exercises the loop end-to-end (not a real strategy). On the world screen
it clicks a right-edge cell and verifies that the map x coordinate increased by one."""
from __future__ import annotations

from ..game.state import GameState
from . import PlanContext, PlannedStep, StepResult
from .verify import coords_changed


class DemoWalkPlanner:
    name = "demo"

    def __init__(self, direction: str = "right", max_steps: int | None = None):
        self.goal: str | None = None
        self.direction = direction
        self.max_steps = max_steps
        self.done = 0

    def set_goal(self, goal: str | None) -> None:
        self.goal = goal

    async def propose(self, state: GameState, ctx: PlanContext) -> PlannedStep | None:
        if not self.goal or state.screen != "world" or state.map.coords is None:
            return None
        if self.max_steps is not None and self.done >= self.max_steps:
            return None
        cells = ctx.grid.edge_cells(self.direction)
        cell = cells[len(cells) // 2]
        x, y = ctx.grid.center(cell)
        dx, dy = {"right": (1, 0), "left": (-1, 0), "top": (0, -1), "bottom": (0, 1)}[self.direction]
        target = (state.map.coords[0] + dx, state.map.coords[1] + dy)
        return PlannedStep(
            action={"kind": "click", "x": round(x, 1), "y": round(y, 1), "cell": cell,
                    "reason": f"demo: change map {self.direction} -> {target}"},
            task=f"demo walk {self.direction} to {target}", verify=coords_changed(target))

    def on_result(self, result: StepResult, state: GameState) -> None:
        if result.ok:
            self.done += 1

"""Fight placement: pick one of our free start cells by role, click it, verify we moved,
then press Ready. If the view is unreadable, just press Ready (the game places us).

Challenges are ignored (Thomas's rule). The main button first reads "CHOISIR LES CHALLENGES":
we click it, and it turns into "PRÊT" (the challenge cards and Valider show up meanwhile; we
never touch them). Then we click PRÊT. If the challenge button is still there after two
clicks, we fall back to the ready key."""
from __future__ import annotations

import time

from ..game.grid import Grid
from ..game.state import GameState
from ..perception.placement import PlacementView
from ..planner import PlanContext, PlannedStep, StepResult
from . import Outcome, StepOrOutcome


def choose_start_cell(view: PlacementView, grid: Grid, role: str, preferred_range: int = 6) -> int | None:
    """ranged: as far as possible from the nearest enemy, but with line of sight to an enemy
    within `preferred_range`; melee: closest to an enemy. Ties: lowest cell id (stable)."""
    if not view.our_cells:
        return None
    enemies = view.enemies or view.their_cells
    if not enemies:
        return view.self_cell if view.self_cell in view.our_cells else view.our_cells[0]
    blockers = set(view.blocked) | set(view.allies) | set(view.enemies)

    def score(c: int) -> tuple:
        ds = [grid.distance(c, e) for e in enemies]
        dmin = min(ds)
        if role == "ranged":
            los = any(grid.has_los(c, e, blockers - {c, e}) and grid.distance(c, e) <= preferred_range for e in enemies)
            return (1 if los else 0, min(dmin, preferred_range), dmin, -c)
        return (-dmin, -c)

    return max(view.our_cells + ([view.self_cell] if view.self_cell is not None else []), key=score)


class PlacementSkill:
    name = "placement"

    def __init__(self, cfg: dict, role: str, preferred_range: int = 6, clock=time.monotonic):
        self.ready_key = cfg["keys"]["ready"]
        pcfg = cfg.get("placement", {})
        self.ready_retry_s = float(pcfg.get("ready_retry_s", 3.0))
        self.role = role
        self.preferred_range = preferred_range
        self.clock = clock
        self.view: PlacementView | None = None
        self.placed = False
        self.ready_sent = False
        self.ready_sent_at: float | None = None
        self.ready_retried = False
        self.challenge_clicks = 0
        self.challenge_clicked_at: float | None = None

    def set_view(self, view: PlacementView | None) -> None:
        self.view = view

    def step(self, state: GameState, ctx: PlanContext) -> StepOrOutcome:
        f = state.fight
        if state.screen != "fight" or f is None or f.phase != "placement":
            return Outcome("done", "not in placement")
        v = self.view
        button = getattr(f, "button", None)
        if self.ready_sent:
            # one more click if "PRÊT" is still showing a while after we pressed it
            if (button is not None and button.kind == "ready" and not self.ready_retried
                    and self.ready_sent_at is not None and self.clock() - self.ready_sent_at >= self.ready_retry_s):
                self.ready_retried = True
                return self._ready("PRÊT still showing: press it again", button)
            return Outcome("wait", "ready sent, waiting for the fight to start")
        if v is None or not v.is_placement or v.self_cell is None or self.placed:
            return self._ready("placement unreadable: let the game place us" if not self.placed else "placed", button)
        target = choose_start_cell(v, ctx.grid, self.role, self.preferred_range)
        if target is None or target == v.self_cell:
            self.placed = True
            return self._ready("already on the best start cell", button)
        x, y = ctx.grid.center(target)
        src = v.self_cell

        def moved(before: GameState, now: GameState) -> bool:
            me = now.fight.me() if now.fight else None
            return me is not None and me.cell == target

        return PlannedStep(action={"kind": "click", "x": round(x, 1), "y": round(y, 1), "cell": target,
                                   "reason": f"placement ({self.role}): {src} -> {target}"},
                           task=f"placement: move to start cell {target}", verify=moved, timeout_s=5.0, retries=1,
                           meta={"skill": self.name, "op": "place", "cell": target})

    def _ready(self, why: str, button=None) -> StepOrOutcome:
        def started(before: GameState, now: GameState) -> bool:
            return now.screen != "fight" or now.fight is None or now.fight.phase != "placement"

        meta = {"skill": self.name, "op": "ready"}
        if button is not None and button.kind == "challenges":
            # click "Choisir les challenges" so the button turns into PRÊT; never the cards/Valider
            if self.challenge_clicked_at is not None and self.clock() - self.challenge_clicked_at < self.ready_retry_s:
                return Outcome("wait", "clicked 'Choisir les challenges', waiting for PRÊT")
            if self.challenge_clicks < 2:
                def turned(before: GameState, now: GameState) -> bool:
                    b = getattr(now.fight, "button", None) if now.fight else None
                    return started(before, now) or (b is not None and b.kind != "challenges")

                return PlannedStep(action={"kind": "click", "x": round(button.x, 1), "y": round(button.y, 1),
                                           "reason": f"{why}: click 'Choisir les challenges' to get PRÊT"},
                                   task="placement: skip challenges", verify=turned, timeout_s=max(self.ready_retry_s, 5.0),
                                   retries=0, meta={"skill": self.name, "op": "challenges"})
            why = f"{why}; 'Choisir les challenges' still showing after 2 clicks: ready key"
        if button is not None and button.kind == "ready":
            return PlannedStep(action={"kind": "click", "x": round(button.x, 1), "y": round(button.y, 1),
                                       "reason": f"{why}: click PRÊT"},
                               task="placement: ready (PRÊT)", verify=started, timeout_s=6.0, retries=0,
                               meta={**meta, "via": "click"})
        return PlannedStep(action={"kind": "key", "key": self.ready_key, "reason": why}, task="placement: ready",
                           verify=started, timeout_s=6.0, retries=0, meta={**meta, "via": "key"})

    def on_result(self, result: StepResult, state: GameState) -> None:
        m = result.step.meta
        if m.get("skill") != self.name:
            return
        if m["op"] == "place":
            self.placed = True  # placed or not, don't keep trying: Ready next
        elif m["op"] == "challenges":
            self.challenge_clicks += 1
            self.challenge_clicked_at = self.clock()
        elif m["op"] == "ready":
            self.ready_sent = True
            self.ready_sent_at = self.clock()

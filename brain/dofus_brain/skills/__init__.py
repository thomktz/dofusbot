"""Skills: one small deterministic routine per game phase (travel, placement, fight turn,
harvest, dialog, popup, ...). See docs/agent_phases.md.

A skill looks at the GameState and returns either the next PlannedStep (one action plus a
verify predicate) or an Outcome when it has finished, failed or needs a human. Skills never
send input themselves: the agent executes steps (and only in assist/auto mode).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, Union, runtime_checkable

from ..game.state import GameState

if TYPE_CHECKING:  # the planner package imports the skills (PhasePlanner): no runtime import here
    from ..planner import PlanContext, PlannedStep, StepResult

OutcomeStatus = Literal["done", "failed", "ask", "wait", "scan"]


@dataclass
class Outcome:
    """`done` = goal reached, `failed` = gave up (reason), `ask` = pause and ask the human,
    `wait` = nothing to do on this frame (e.g. loading, enemy turn), `scan` = please capture
    a frame while holding data["key"] and hand it back with the skill's `on_scan()`."""
    status: OutcomeStatus
    reason: str = ""
    data: dict[str, Any] = field(default_factory=dict)


StepOrOutcome = Union["PlannedStep", Outcome]


@runtime_checkable
class Skill(Protocol):
    name: str

    def step(self, state: GameState, ctx: PlanContext) -> StepOrOutcome: ...

    def on_result(self, result: StepResult, state: GameState) -> None: ...


def wait(reason: str = "") -> Outcome:
    return Outcome("wait", reason)


from .config import load_phases_config  # noqa: E402,F401

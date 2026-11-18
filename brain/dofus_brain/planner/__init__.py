"""Planner interface. Goals and strategy are intentionally NOT implemented yet: they will be
defined later and plug in here.

A planner looks at the current GameState and proposes at most one PlannedStep (one §2.3
Action + how to verify it worked). The agent loop decides whether to execute it (mode
observe/assist/auto, pause, kill), executes it through the Executor, then checks `verify`
on subsequent frames until it passes or `timeout_s` expires (then retries / reports).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

from ..game.grid import Grid
from ..game.state import GameState

Verify = Callable[[GameState, GameState], bool]  # (state_before_action, state_now) -> success


@dataclass
class PlannedStep:
    action: dict                       # one protocol §2.3 Action, window points
    task: str | None = None            # human readable, shown as AgentStatus.task
    verify: Verify | None = None       # None = fire and forget
    timeout_s: float | None = None     # default: agent.action_timeout_s
    retries: int | None = None         # default: agent.action_retries
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlanContext:
    goal: str | None
    mode: str
    grid: Grid
    config: dict
    llm: Any = None                    # LLMClient, for planners that need fuzzy reading
    attempt: int = 0                   # retry counter of the current step (0 = first try)


@dataclass
class StepResult:
    step: PlannedStep
    ok: bool
    reason: str = ""                   # "verified" | "timeout" | "no_verify" | "stuck" | "error: ..."


@runtime_checkable
class Planner(Protocol):
    name: str

    def set_goal(self, goal: str | None) -> None: ...

    async def propose(self, state: GameState, ctx: PlanContext) -> PlannedStep | None: ...

    def on_result(self, result: StepResult, state: GameState) -> None: ...


class NoopPlanner:
    """Proposes nothing. The default until goals are defined."""

    name = "noop"

    def __init__(self) -> None:
        self.goal: str | None = None

    def set_goal(self, goal: str | None) -> None:
        self.goal = goal

    async def propose(self, state: GameState, ctx: PlanContext) -> PlannedStep | None:
        return None

    def on_result(self, result: StepResult, state: GameState) -> None:
        return None


from .verify import coords_changed, screen_is  # noqa: E402,F401
from .demo import DemoWalkPlanner  # noqa: E402,F401


def _phase_planner(*args, **kwargs):
    """Imported lazily: phases.py uses the skills package, which itself imports this module."""
    from .phases import PhasePlanner

    return PhasePlanner(*args, **kwargs)


def __getattr__(name: str):
    if name == "PhasePlanner":
        from .phases import PhasePlanner

        return PhasePlanner
    raise AttributeError(name)


PLANNERS = {"noop": NoopPlanner, "demo": DemoWalkPlanner, "phases": _phase_planner}

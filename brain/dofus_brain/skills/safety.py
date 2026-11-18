"""Safety layer shared by every phase.

SafetyGuard sits between the skills and the agent loop:
- every proposed action must carry a verify predicate (except kinds in allow_unverified_kinds)
- keys must be one of the named keys in config/phases.json (no free typing)
- an unknown or low-confidence screen for longer than unknown_stop_s pauses and asks
- too many failed steps in a row pauses and asks
- once halted (or killed) it proposes nothing until reset() by the human
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from ..game.state import GameState
from ..planner import PlannedStep, StepResult
from .config import allowed_keys

log = logging.getLogger("dofus_brain.safety")


class SafetyGuard:
    def __init__(self, cfg: dict, clock: Callable[[], float] = time.monotonic,
                 on_halt: Callable[[str], None] | None = None):
        sc = cfg["safety"]
        self.unknown_stop_s = float(sc["unknown_stop_s"])
        self.min_conf = float(sc["min_confidence"])
        self.max_fail = int(sc["max_consecutive_failures"])
        self.unverified_ok = set(sc.get("allow_unverified_kinds", ["wait"]))
        self.keys = allowed_keys(cfg)
        self.clock = clock
        self.on_halt = on_halt
        self.halted: str | None = None
        self.failures = 0
        self._unsure_since: float | None = None

    # -- state
    def halt(self, reason: str) -> None:
        if self.halted is None:
            self.halted = reason
            log.warning("safety halt: %s", reason)
            if self.on_halt:
                self.on_halt(reason)

    def kill(self) -> None:
        self.halt("kill switch")

    def reset(self) -> None:
        self.halted = None
        self.failures = 0
        self._unsure_since = None

    # -- per frame
    def observe(self, state: GameState) -> None:
        unsure = state.screen in ("unknown", "menu") or state.confidence < self.min_conf
        if state.screen == "loading":
            unsure = False  # loading is expected and short; the travel skill waits for it
        now = self.clock()
        if not unsure:
            self._unsure_since = None
            return
        if self._unsure_since is None:
            self._unsure_since = now
        elif now - self._unsure_since >= self.unknown_stop_s:
            self.halt(f"screen {state.screen!r} (confidence {state.confidence:.2f}) for "
                      f"{now - self._unsure_since:.0f}s: pausing, please check the game")

    def screen_ok(self) -> bool:
        return self._unsure_since is None

    # -- per step
    def check(self, step: PlannedStep) -> str | None:
        """None if the step may be proposed, else the reason it is refused."""
        if self.halted:
            return f"halted: {self.halted}"
        a = step.action
        kind = a.get("kind")
        if step.verify is None and kind not in self.unverified_ok:
            return f"{kind} action without verification"
        if kind == "key" and str(a.get("key", "")).lower() not in self.keys:
            return f"key {a.get('key')!r} is not a configured key"
        if kind == "type":
            return "typing is never allowed"
        if a.get("modifiers"):
            return "key modifiers are not allowed"
        return None

    def on_result(self, result: StepResult) -> None:
        if result.ok:
            self.failures = 0
            return
        self.failures += 1
        if self.failures >= self.max_fail:
            self.halt(f"{self.failures} failed steps in a row (last: {result.step.task or result.step.action.get('kind')}: "
                      f"{result.reason})")

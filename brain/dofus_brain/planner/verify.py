"""Small reusable verification predicates for PlannedStep.verify."""
from __future__ import annotations

from ..game.state import GameState
from . import Verify


def coords_changed(expected: tuple[int, int] | None = None) -> Verify:
    """Map coords differ from before (and equal `expected` if given)."""

    def check(before: GameState, now: GameState) -> bool:
        if now.screen not in ("world", "dialog", "popup") or now.map.coords is None:
            return False
        if expected is not None:
            return tuple(now.map.coords) == tuple(expected)
        return now.map.coords != before.map.coords

    return check


def screen_is(*screens: str) -> Verify:
    def check(before: GameState, now: GameState) -> bool:
        return now.screen in screens

    return check

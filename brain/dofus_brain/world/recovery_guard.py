"""Protocol-first anti-stuck guard for one account.

A timeout means the server did not acknowledge the action.  Retrying the same
packet blindly is precisely how an agent becomes stuck or noisy.  Instead we
quarantine the account until a materially fresh protocol state arrives; the
planner then recomputes from that state rather than replaying an old intent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .state import WorldState


def _fingerprint(state: WorldState) -> tuple[Any, ...]:
    return (
        state.map_id, state.character.get("cell"), state.fight_phase,
        state.fight_turn, state.fight_actor_id,
        None if state.dialog is None else (state.dialog.get("message_id"), tuple(state.dialog.get("replies", ()))),
    )


@dataclass
class RecoveryHold:
    transition: dict[str, Any]
    baseline: tuple[Any, ...]


class RecoveryGuard:
    """Stop a timed-out account from issuing more mutations against stale state."""

    def __init__(self) -> None:
        self._holds: dict[int, RecoveryHold] = {}

    def hold(self, account_id: int, transition: dict[str, Any], state: WorldState) -> dict[str, Any]:
        hold = RecoveryHold(dict(transition), _fingerprint(state))
        self._holds[int(account_id)] = hold
        return self.status(account_id)

    def observe(self, account_id: int, state: WorldState) -> dict[str, Any] | None:
        hold = self._holds.get(int(account_id))
        if hold is None:
            return None
        if _fingerprint(state) == hold.baseline:
            return None
        self._holds.pop(int(account_id), None)
        return {"state": "recovered", "reason": "fresh protocol state observed"}

    def status(self, account_id: int) -> dict[str, Any]:
        hold = self._holds.get(int(account_id))
        if hold is None:
            return {"state": "idle"}
        return {"state": "waiting_for_fresh_state", "kind": hold.transition.get("kind"),
                "reason": "previous action timed out; waiting for a changed protocol state before replanning"}

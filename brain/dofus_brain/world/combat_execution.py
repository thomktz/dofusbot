"""Acknowledged combat commands for protocol-driven turns."""
from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from ..managed_transport import ManagedTransport
from .state import WorldState


@dataclass
class PendingCombatAction:
    kind: str
    baseline_offset: int
    spell_id: int | None = None
    cell: int | None = None
    started_at: float = field(default_factory=monotonic)


class CombatExecutor:
    """One combat request at a time, completed only by a newer fight event."""

    def __init__(self) -> None:
        self.pending: PendingCombatAction | None = None

    def status(self) -> dict[str, Any]:
        if self.pending is None:
            return {"state": "idle"}
        return {"state": "awaiting_ack", "kind": self.pending.kind,
                "spell_id": self.pending.spell_id, "cell": self.pending.cell}

    def cast(self, state: WorldState, spell_id: int, cell: int, transport: ManagedTransport) -> None:
        self._ensure_turn(state)
        self._ensure_idle()
        transport.cast_spell(int(spell_id), int(cell))
        self.pending = PendingCombatAction("cast_spell", self._last_offset(state), int(spell_id), int(cell))

    def end_turn(self, state: WorldState, transport: ManagedTransport) -> None:
        self._ensure_turn(state)
        self._ensure_idle()
        transport.end_turn()
        self.pending = PendingCombatAction("end_turn", self._last_offset(state))

    def observe(self, state: WorldState) -> dict[str, Any] | None:
        pending = self.pending
        if pending is None:
            return None
        if state.fight_phase in {"ended", "out_of_fight"}:
            self.pending = None
            return {"state": "cancelled", "reason": "fight ended before action acknowledgement", "kind": pending.kind}
        new = [event for event in state.events if int(event.get("offset", -1)) > pending.baseline_offset]
        if pending.kind == "cast_spell":
            matching = next((event for event in new if event.get("kind") == "spell_cast"
                             and event.get("spell_id") == pending.spell_id
                             and (event.get("destination_cell") is None or event.get("destination_cell") == pending.cell)), None)
            if matching is None:
                return None
            self.pending = None
            return {"state": "confirmed", "kind": pending.kind, "spell_id": pending.spell_id,
                    "cell": pending.cell, "offset": matching.get("offset")}
        matching = next((event for event in new if event.get("kind") == "turn_end"), None)
        if matching is None:
            return None
        self.pending = None
        return {"state": "confirmed", "kind": pending.kind, "offset": matching.get("offset")}

    def cancel(self) -> None:
        self.pending = None

    def expire(self, timeout_s: float = 12.0) -> dict[str, Any] | None:
        if self.pending is None or monotonic() - self.pending.started_at < timeout_s:
            return None
        kind = self.pending.kind
        self.pending = None
        return {"state": "timed_out", "kind": kind}

    @staticmethod
    def _last_offset(state: WorldState) -> int:
        return max((int(event.get("offset", -1)) for event in state.events), default=-1)

    def _ensure_idle(self) -> None:
        if self.pending is not None:
            raise ValueError("previous combat action is awaiting server acknowledgement")

    @staticmethod
    def _ensure_turn(state: WorldState) -> None:
        if state.fight_phase != "turn":
            raise ValueError("combat action is only available during the current player turn")

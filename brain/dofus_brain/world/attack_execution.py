"""Confirmation barrier for engaging a role-play monster group."""
from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from ..managed_transport import ManagedTransport
from .state import WorldState


@dataclass
class PendingAttack:
    group_id: int
    source_map_id: int | None
    started_at: float = field(default_factory=monotonic)


class AttackExecutor:
    def __init__(self) -> None:
        self.pending: PendingAttack | None = None

    def status(self) -> dict[str, Any]:
        return {"state": "awaiting_fight_start", "group_id": self.pending.group_id} if self.pending else {"state": "idle"}

    def attack(self, state: WorldState, group_id: int, transport: ManagedTransport) -> None:
        if self.pending is not None:
            raise ValueError("previous monster attack is awaiting a fight-start confirmation")
        actor = state.actors.get(int(group_id))
        if actor is None or actor.kind != "monster_group" or not actor.alive or state.fight_phase != "out_of_fight":
            raise ValueError("target is not a live monster group on the current map")
        ready, reason = self.fight_readiness(state)
        if not ready:
            raise ValueError(reason)
        transport.attack_monster(int(group_id))
        self.pending = PendingAttack(int(group_id), state.map_id)

    @staticmethod
    def fight_readiness(state: WorldState) -> tuple[bool, str]:
        """Return whether it is safe to initiate the next fight.

        Health must come from the current protocol snapshot.  We deliberately
        do not estimate recovery from time elapsed or pixels: if the server
        does not currently say the character is at full health, a new fight is
        refused and the caller must wait (or explicitly use a validated item).
        """
        hp, hp_max = state.character.get("hp"), state.character.get("hp_max")
        if not isinstance(hp, (int, float)) or not isinstance(hp_max, (int, float)) or hp_max <= 0:
            return False, "character health is not protocol-confirmed; refusing to start a fight"
        if hp < hp_max:
            return False, f"character is not at full health ({int(hp)}/{int(hp_max)}); wait for protocol-confirmed recovery before fighting"
        return True, "character health is protocol-confirmed at full"

    def observe(self, state: WorldState) -> dict[str, Any] | None:
        if self.pending is None:
            return None
        pending = self.pending
        if state.fight_phase != "out_of_fight":
            self.pending = None
            return {"state": "confirmed", "kind": "attack", "group_id": pending.group_id,
                    "fight_map_id": state.map_id}
        if state.map_id != pending.source_map_id:
            self.pending = None
            return {"state": "cancelled", "kind": "attack", "group_id": pending.group_id,
                    "reason": "map changed before fight start"}
        return None

    def expire(self, timeout_s: float = 20.0) -> dict[str, Any] | None:
        if self.pending is None or monotonic() - self.pending.started_at < timeout_s:
            return None
        group_id = self.pending.group_id
        self.pending = None
        return {"state": "timed_out", "kind": "attack", "group_id": group_id}

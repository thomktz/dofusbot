"""Protocol-confirmed opening of an NPC dialogue."""
from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from ..managed_transport import ManagedTransport
from .state import WorldState


@dataclass
class PendingTalk:
    entity_id: int
    npc_id: int
    map_id: int
    started_at: float = field(default_factory=monotonic)


class TalkExecutor:
    def __init__(self) -> None:
        self.pending: PendingTalk | None = None

    def status(self) -> dict[str, Any]:
        return {"state": "awaiting_dialogue_open", "npc_id": self.pending.npc_id} if self.pending else {"state": "idle"}

    def talk(self, state: WorldState, entity_id: int, transport: ManagedTransport) -> None:
        if self.pending is not None:
            raise ValueError("previous NPC talk action is awaiting dialogue confirmation")
        actor = state.actors.get(int(entity_id))
        npc_id = (actor.metadata if actor else {}).get("npc_id")
        if actor is None or actor.kind != "npc" or not actor.alive or npc_id is None or state.map_id is None:
            raise ValueError("NPC is not a current protocol-identified dialogue target")
        transport.talk(int(npc_id), 3, int(state.map_id))
        self.pending = PendingTalk(int(entity_id), int(npc_id), int(state.map_id))

    def observe(self, state: WorldState) -> dict[str, Any] | None:
        pending = self.pending
        if pending is None:
            return None
        if state.map_id != pending.map_id:
            self.pending = None
            return {"state": "cancelled", "kind": "talk", "npc_id": pending.npc_id,
                    "reason": "map changed before dialogue opened"}
        dialog = state.dialog
        if dialog is None:
            return None
        if dialog.get("npc_id") != pending.npc_id:
            self.pending = None
            return {"state": "cancelled", "kind": "talk", "npc_id": pending.npc_id,
                    "reason": "a different dialogue opened"}
        self.pending = None
        return {"state": "confirmed", "kind": "talk", "npc_id": pending.npc_id,
                "message_id": dialog.get("message_id")}

    def expire(self, timeout_s: float = 20.0) -> dict[str, Any] | None:
        if self.pending is None or monotonic() - self.pending.started_at < timeout_s:
            return None
        npc_id = self.pending.npc_id
        self.pending = None
        return {"state": "timed_out", "kind": "talk", "npc_id": npc_id}

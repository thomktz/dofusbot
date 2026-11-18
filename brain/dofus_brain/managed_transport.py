"""Protocol-first action transport.

This is the boundary between the dashboard/agent and an Astra-compatible game
proxy.  It intentionally has no screen-coordinate fallback: callers describe
an action in game terms and the attached codec/proxy owns wire encoding,
obfuscation, UID sequencing and delivery to the active Dofus session.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Callable, Protocol


class TransportState(StrEnum):
    OFFLINE = "offline"
    CONNECTING = "connecting"
    READY = "ready"
    ERROR = "error"


def encode_varint(value: int) -> bytes:
    """Length-prefix encoding used by AstraBot's Dofus proxy."""
    if value < 0:
        raise ValueError("varint value must be non-negative")
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def frame(payload: bytes) -> bytes:
    return encode_varint(len(payload)) + payload


@dataclass(frozen=True)
class GameIntent:
    """A typed game action, safe for the LLM-facing tool boundary."""

    kind: str
    payload: dict[str, int | bool | str | list[int]]


class ProtocolCodec(Protocol):
    """Implemented by the ported Astra protobuf/mapping runtime."""

    def encode(self, intent: GameIntent, uid: int) -> bytes: ...

    # Astra's request envelope uses a positive UID only for a small set of
    # connection/control messages.  Gameplay requests intentionally use -1.
    # Kept optional at runtime so the tiny test codecs and future codecs remain
    # compatible with the transport boundary.
    def request_uid(self, intent: GameIntent, next_uid: int) -> int: ...


@dataclass
class ManagedTransport:
    """One writable game session supplied by a local MITM proxy.

    The proxy calls :meth:`attach` only after the game connection is live.  No
    action can be emitted before that point, which prevents a stale dashboard
    from sending requests into a different client session.
    """

    state: TransportState = TransportState.OFFLINE
    session_id: str | None = None
    error: str | None = None
    uid: int = 1
    _codec: ProtocolCodec | None = field(default=None, repr=False)
    _send_frame: Callable[[bytes], None] | None = field(default=None, repr=False)
    sent: list[GameIntent] = field(default_factory=list)

    def attach(self, session_id: str, codec: ProtocolCodec, send_frame: Callable[[bytes], None], uid: int = 1) -> None:
        self.state, self.session_id, self.error = TransportState.READY, session_id, None
        self.uid, self._codec, self._send_frame = max(1, int(uid)), codec, send_frame

    def detach(self, error: str | None = None) -> None:
        self.state = TransportState.ERROR if error else TransportState.OFFLINE
        self.error, self.session_id, self._codec, self._send_frame = error, None, None, None

    def observe_uid(self, uid: int | None) -> None:
        if uid is not None and uid > 0:
            self.uid = int(uid)

    def send(self, intent: GameIntent) -> None:
        if self.state is not TransportState.READY or self._codec is None or self._send_frame is None:
            raise RuntimeError("managed Dofus transport is not connected; launch Dofus through the local proxy first")
        next_uid = self.uid + 1
        request_uid = getattr(self._codec, "request_uid", lambda _intent, candidate: candidate)(intent, next_uid)
        payload = self._codec.encode(intent, int(request_uid))
        if not payload:
            raise RuntimeError(f"protocol codec produced no frame for {intent.kind}")
        self._send_frame(frame(payload))
        # A -1 request is not part of the client UID sequence.  Advancing it
        # would desynchronise the next proxied connection/control request.
        if request_uid > 0:
            self.uid = int(request_uid)
        self.sent.append(intent)

    def status(self) -> dict[str, object]:
        return {"state": self.state, "session_id": self.session_id, "error": self.error, "uid": self.uid,
                "sent": len(self.sent)}

    # Typed actions, matching AstraBot's request classes without leaking those
    # classes into the dashboard or the LLM context.
    def reply(self, reply_id: int) -> None:
        self.send(GameIntent("npc_dialog_reply", {"reply_id": int(reply_id)}))

    def leave_dialog(self) -> None:
        self.send(GameIntent("dialog_leave", {}))

    def refuse_party_invitation(self, party_id: int) -> None:
        self.send(GameIntent("party_invitation_refuse", {"party_id": int(party_id)}))

    def refuse_friendly_fight(self, fight_id: int) -> None:
        self.send(GameIntent("friendly_fight_answer", {"fight_id": int(fight_id), "accept": False}))

    def talk(self, npc_id: int, action_id: int, map_id: int) -> None:
        self.send(GameIntent("npc_action", {"npc_id": int(npc_id), "npc_action_id": int(action_id), "npc_map_id": int(map_id)}))

    def attack_monster(self, group_id: int) -> None:
        self.send(GameIntent("attack_monster", {"monster_group_id": int(group_id)}))

    def use_item(self, object_uid: int) -> None:
        self.send(GameIntent("object_use", {"object_uid": int(object_uid)}))

    def set_item_position(self, object_uid: int, position: int, quantity: int = 1) -> None:
        self.send(GameIntent("object_set_position", {"object_uid": int(object_uid), "position": int(position), "quantity": int(quantity)}))

    def move(self, map_id: int, key_cells: list[int]) -> None:
        if not key_cells:
            raise ValueError("movement path is empty")
        self.send(GameIntent("map_movement", {"map_id": int(map_id), "key_cells": [int(cell) for cell in key_cells]}))

    def change_map(self, map_id: int) -> None:
        """Request the already-planned transition map after movement confirms."""
        self.send(GameIntent("map_change", {"map_id": int(map_id), "auto_pilot": False}))

    def go_to_map(self, map_id: int) -> None:
        """Ask the game client/server to use its native world-map Go To route.

        This is deliberately distinct from ``change_map``: the latter is only
        for a local, observed border transition after role-play movement.  A
        destination known outside the current roaming area should use the
        game's own route planner instead.
        """
        self.send(GameIntent("map_change", {"map_id": int(map_id), "auto_pilot": True}))

    def use_interactive(self, element_id: int, skill_instance_uid: int) -> None:
        self.send(GameIntent("interactive_use", {"element_id": int(element_id), "skill_instance_uid": int(skill_instance_uid)}))

    def cast_spell(self, spell_id: int, cell: int) -> None:
        self.send(GameIntent("fight_cast", {"spell_id": int(spell_id), "cell": int(cell)}))

    def end_turn(self) -> None:
        self.send(GameIntent("fight_turn_finish", {}))

"""Account-local safety policy for unsolicited game UI/events.

Decoding obfuscated packet aliases is intentionally separate from this module.
Once a reducer can identify an unsolicited event, it supplies a normalized
``Interruption`` and gets a deterministic default-deny decision without ever
touching another account's planner state.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class InterruptionKind(StrEnum):
    TRADE_REQUEST = "trade_request"
    GROUP_INVITE = "group_invite"
    FIGHT_INVITE = "fight_invite"
    CHALLENGE_INVITE = "challenge_invite"
    UNEXPECTED_DIALOG = "unexpected_dialog"


@dataclass(frozen=True)
class Interruption:
    account_id: int
    kind: InterruptionKind
    source_id: int | None = None
    source_name: str | None = None
    reference_id: int | None = None  # party/fight ID where the refusal requires it
    offset: int | None = None


class InterruptionPolicy:
    """Default-deny, one pending interruption per independent account."""

    def __init__(self) -> None:
        self._pending: dict[int, Interruption] = {}

    def observe(self, event: Interruption, *, managed_coordination: bool = False) -> dict[str, Any]:
        if event.account_id in self._pending:
            return {"state": "already_pending", "account_id": event.account_id,
                    "kind": self._pending[event.account_id].kind}
        if managed_coordination:
            return {"state": "allowed", "account_id": event.account_id, "kind": event.kind}
        self._pending[event.account_id] = event
        # Every default action below is a current protobuf request verified in
        # Astra's mapping.  IDs must be decoded from the matching event before
        # we emit anything; an unknown invitation remains blocked, never guessed.
        action = None
        if event.kind == InterruptionKind.TRADE_REQUEST:
            action = "dialog_leave"
        elif event.kind == InterruptionKind.GROUP_INVITE and event.reference_id is not None:
            action = "party_invitation_refuse"
        elif event.kind in {InterruptionKind.FIGHT_INVITE, InterruptionKind.CHALLENGE_INVITE} and event.reference_id is not None:
            action = "friendly_fight_answer"
        return {"state": "blocked", "account_id": event.account_id, "kind": event.kind,
                "default_action": action, "reason": "unsolicited event default-denied"}

    def closed(self, account_id: int) -> dict[str, Any] | None:
        event = self._pending.pop(int(account_id), None)
        if event is None:
            return None
        return {"state": "resumed", "account_id": event.account_id, "kind": event.kind}

    def status(self, account_id: int) -> dict[str, Any]:
        event = self._pending.get(int(account_id))
        if event is None:
            return {"state": "idle"}
        return {"state": "blocked", "kind": event.kind, "source_id": event.source_id,
                "source_name": event.source_name}

"""Protocol-confirmed NPC dialogue progression.

NPC dialogues are multi-stage.  A reply is only safe to emit for the current
server-provided question, and the next reply must wait until a different
question (or a dialog close) is observed.  This keeps both dashboard and LLM
actions from racing old UI state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from ..managed_transport import ManagedTransport
from .state import WorldState


def _fingerprint(dialog: dict[str, Any] | None) -> tuple[int | None, tuple[int, ...]] | None:
    if not dialog:
        return None
    return (dialog.get("message_id"), tuple(int(value) for value in dialog.get("replies", ()) if value is not None))


@dataclass
class PendingDialogueReply:
    before: tuple[int | None, tuple[int, ...]]
    reply_id: int
    started_at: float = field(default_factory=monotonic)


class DialogueExecutor:
    def __init__(self) -> None:
        self.pending: PendingDialogueReply | None = None

    def status(self) -> dict[str, Any]:
        return {"state": "waiting_for_next_stage", "reply_id": self.pending.reply_id} if self.pending else {"state": "idle"}

    def choose(self, state: WorldState, reply_id: int, transport: ManagedTransport) -> None:
        if self.pending is not None:
            raise ValueError("previous dialogue reply is awaiting server confirmation")
        dialog = state.dialog
        before = _fingerprint(dialog)
        if before is None or int(reply_id) not in before[1]:
            raise ValueError("reply is not available in the current dialogue stage")
        transport.reply(int(reply_id))
        self.pending = PendingDialogueReply(before, int(reply_id))

    def observe(self, state: WorldState) -> dict[str, Any] | None:
        pending = self.pending
        if pending is None:
            return None
        current = _fingerprint(state.dialog)
        if current == pending.before:
            return None
        self.pending = None
        if current is None:
            return {"state": "closed", "reply_id": pending.reply_id}
        return {"state": "advanced", "reply_id": pending.reply_id,
                "message_id": current[0], "replies": list(current[1])}

    def cancel(self) -> None:
        self.pending = None

    def expire(self, timeout_s: float = 20.0) -> dict[str, Any] | None:
        if self.pending is None or monotonic() - self.pending.started_at < timeout_s:
            return None
        reply_id = self.pending.reply_id
        self.pending = None
        return {"state": "timed_out", "kind": "dialogue_reply", "reply_id": reply_id}

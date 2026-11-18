"""Confirmed two-stage execution for role-play interactives.

The game rejects many resource requests unless the character has reached the
adjacent approach cell.  This tiny state machine is intentionally independent
of packet parsing: the live scan feeds it a fresh :class:`WorldState`, and it
only emits the next protocol request once that state confirms the previous
one.  It is therefore safe to use from the dashboard and later from the LLM.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from ..managed_transport import ManagedTransport
from .state import WorldState


@dataclass
class PendingInteractive:
    map_id: int
    element_id: int
    approach_cell: int
    skill_instance_uid: int
    phase: str = "moving"
    use_baseline_offset: int = -1
    started_at: float = field(default_factory=monotonic)


class InteractiveExecutor:
    """Execute one plan at a time, waiting for protocol confirmation."""

    def __init__(self) -> None:
        self.pending: PendingInteractive | None = None

    def status(self) -> dict[str, Any]:
        if self.pending is None:
            return {"state": "idle"}
        return {"state": self.pending.phase, "map_id": self.pending.map_id,
                "element_id": self.pending.element_id,
                "approach_cell": self.pending.approach_cell}

    def start(self, state: WorldState, plan: dict[str, Any], transport: ManagedTransport) -> dict[str, Any]:
        if self.pending is not None:
            raise ValueError("another interactive action is awaiting protocol confirmation")
        if not plan.get("ready") or plan.get("kind") != "move_then_interact":
            raise ValueError(str(plan.get("reason") or "interactive is not ready"))
        if state.map_id is None:
            raise ValueError("current map is not protocol-confirmed")
        approach, use = plan["approach"], plan["use"]
        pending = PendingInteractive(int(state.map_id), int(use["element_id"]),
                                     int(approach["cell"]), int(use["skill_instance_uid"]))
        self.pending = pending
        if state.character.get("cell") == pending.approach_cell:
            self._send_use(state, transport)
            return {"started": True, "phase": "using", "movement_sent": False}
        transport.move(int(state.map_id), [int(value) for value in approach["key_cells"]])
        return {"started": True, "phase": "moving", "movement_sent": True,
                "approach_cell": pending.approach_cell}

    def observe(self, state: WorldState, transport: ManagedTransport) -> dict[str, Any] | None:
        """Advance only from a new protocol snapshot; return state transition."""
        pending = self.pending
        if pending is None:
            return None
        if state.map_id != pending.map_id:
            self.pending = None
            return {"state": "cancelled", "reason": "map changed before interactive completed"}
        if pending.phase == "moving":
            if state.character.get("cell") != pending.approach_cell:
                return None
            try:
                self._send_use(state, transport)
            except (RuntimeError, ValueError) as exc:
                self.pending = None
                return {"state": "failed", "reason": str(exc), "element_id": pending.element_id}
            return {"state": "using", "element_id": pending.element_id}
        # A resource state update/removal after the request is the protocol
        # acknowledgement.  Do not infer success merely because a request was
        # sent: some requests are rejected silently by the game server.
        item = state.interactives.get(pending.element_id)
        if item is None or item.available is False:
            self.pending = None
            return {"state": "completed", "element_id": pending.element_id}
        # Streams are retained so the dashboard can reconstruct state.  A
        # lifecycle event from a previous use of this same element is not an
        # acknowledgement of the request we just sent.
        for event in state.events:
            if int(event.get("offset", -1)) <= pending.use_baseline_offset:
                continue
            if event.get("element_id") != pending.element_id:
                continue
            if event.get("kind") == "interactive_error":
                self.pending = None
                return {"state": "failed", "reason": "game rejected interactive use", "element_id": pending.element_id}
            if event.get("kind") in {"interactive_used", "interactive_ended", "element_removed"}:
                self.pending = None
                return {"state": "completed", "element_id": pending.element_id}
        return None

    def _send_use(self, state: WorldState, transport: ManagedTransport) -> None:
        pending = self.pending
        assert pending is not None
        item = state.interactives.get(pending.element_id)
        instances = item.details.get("skill_instances", ()) if item else ()
        if item is None or item.available is False or not any(
                entry.get("skill_instance_uid") == pending.skill_instance_uid for entry in instances):
            self.pending = None
            raise ValueError("interactive changed before it could be used; refresh and plan again")
        transport.use_interactive(pending.element_id, pending.skill_instance_uid)
        pending.use_baseline_offset = max((int(event.get("offset", -1)) for event in state.events), default=-1)
        pending.phase = "using"

    def expire(self, timeout_s: float = 20.0) -> dict[str, Any] | None:
        if self.pending is None or monotonic() - self.pending.started_at < timeout_s:
            return None
        element_id = self.pending.element_id
        self.pending = None
        return {"state": "timed_out", "kind": "interactive", "element_id": element_id}

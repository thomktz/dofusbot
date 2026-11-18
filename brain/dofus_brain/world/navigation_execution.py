"""Confirmed execution of one explicit world-graph transition at a time."""
from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from ..managed_transport import ManagedTransport
from .state import WorldState


@dataclass
class PendingTransition:
    target_map_id: int
    source_map_id: int
    expected_map_id: int
    transition_map_id: int
    approach_cell: int
    mode: str = "local_transition"
    phase: str = "moving"
    started_at: float = field(default_factory=monotonic)


class NavigationExecutor:
    """Never advance a graph route based on a sent request alone."""

    def __init__(self) -> None:
        self.pending: PendingTransition | None = None

    def status(self) -> dict[str, Any]:
        if self.pending is None:
            return {"state": "idle"}
        return {"state": self.pending.phase, "target_map_id": self.pending.target_map_id,
                "source_map_id": self.pending.source_map_id,
                "expected_map_id": self.pending.expected_map_id,
                "approach_cell": self.pending.approach_cell}

    def start(self, state: WorldState, route: dict[str, Any], transport: ManagedTransport) -> dict[str, Any]:
        if self.pending is not None:
            raise ValueError("a world transition is already awaiting protocol confirmation")
        execution = route.get("execution") or {}
        if not route.get("found"):
            raise ValueError(str(route.get("reason") or "route is not executable"))
        target_map_id = int(route["target_map_id"])
        if state.map_id == target_map_id:
            raise ValueError("already on destination map")

        # Non-roaming travel belongs to the native world-map Go To system.
        # We retain the graph only to verify that the destination is known,
        # never to replay every border click ourselves.
        if getattr(transport, "go_to_map", None):
            self.pending = PendingTransition(target_map_id, int(state.map_id), target_map_id,
                                             target_map_id, -1, phase="auto_pilot", mode="auto_pilot")
            transport.go_to_map(target_map_id)
            return {"state": "auto_pilot", "target_map_id": target_map_id,
                    "confirmation": "wait for the native Go To route to reach the destination map"}

        if not execution.get("ready"):
            raise ValueError(str(execution.get("reason") or route.get("reason") or "route is not executable"))
        if execution.get("kind") != "move_then_map_change" or state.map_id is None:
            raise ValueError("route has no supported next world transition")
        move, change = execution["move"], execution["map_change"]
        pending = PendingTransition(target_map_id, int(state.map_id),
                                    int(change["expected_map_id"]), int(change["transition_map_id"]),
                                    int(move["cells"][-1]))
        self.pending = pending
        if state.character.get("cell") == pending.approach_cell:
            transport.change_map(pending.transition_map_id)
            pending.phase = "changing_map"
            return {"state": pending.phase, "movement_sent": False, "expected_map_id": pending.expected_map_id}
        transport.move(int(move["map_id"]), [int(cell) for cell in move["key_cells"]])
        return {"state": pending.phase, "movement_sent": True, "approach_cell": pending.approach_cell}

    def observe(self, state: WorldState, transport: ManagedTransport) -> dict[str, Any] | None:
        pending = self.pending
        if pending is None:
            return None
        if state.map_id == pending.expected_map_id:
            self.pending = None
            return {"state": "leg_completed", "target_map_id": pending.target_map_id,
                    "map_id": pending.expected_map_id}
        if pending.mode == "auto_pilot":
            # The client reports every intermediate map while Go To is
            # running.  None of those are errors; only the target confirms
            # completion and timeout/cancellation handles the remainder.
            return {"state": "auto_pilot_progress", "target_map_id": pending.target_map_id,
                    "map_id": state.map_id}
        if state.map_id != pending.source_map_id:
            self.pending = None
            return {"state": "cancelled", "reason": "unexpected map change", "target_map_id": pending.target_map_id}
        if pending.phase == "moving" and state.character.get("cell") == pending.approach_cell:
            try:
                transport.change_map(pending.transition_map_id)
            except RuntimeError as exc:
                self.pending = None
                return {"state": "failed", "reason": str(exc), "target_map_id": pending.target_map_id}
            pending.phase = "changing_map"
            return {"state": "changing_map", "expected_map_id": pending.expected_map_id}
        return None

    def expire(self, timeout_s: float = 30.0) -> dict[str, Any] | None:
        if self.pending is None or monotonic() - self.pending.started_at < timeout_s:
            return None
        target_map_id = self.pending.target_map_id
        self.pending = None
        return {"state": "timed_out", "kind": "navigation", "target_map_id": target_map_id}

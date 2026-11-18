"""Confirmation barrier for inventory use/equipment requests."""
from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from ..managed_transport import ManagedTransport
from .state import WorldState


@dataclass
class PendingInventoryAction:
    kind: str
    object_uid: int
    quantity: int | None
    position: int | None
    target_position: int | None = None
    started_at: float = field(default_factory=monotonic)


class InventoryExecutor:
    """Serialize inventory mutations and confirm them from the live snapshot."""

    def __init__(self) -> None:
        self.pending: PendingInventoryAction | None = None

    def status(self) -> dict[str, Any]:
        if self.pending is None:
            return {"state": "idle"}
        return {"state": "awaiting_inventory_delta", "kind": self.pending.kind,
                "object_uid": self.pending.object_uid, "target_position": self.pending.target_position}

    def use(self, state: WorldState, object_uid: int, transport: ManagedTransport) -> None:
        item = self._item(state, object_uid)
        self._ensure_idle()
        transport.use_item(int(object_uid))
        self.pending = PendingInventoryAction("use", int(object_uid), self._quantity(item), self._position(item))

    def equip(self, state: WorldState, object_uid: int, position: int, transport: ManagedTransport) -> None:
        item = self._item(state, object_uid)
        self._ensure_idle()
        transport.set_item_position(int(object_uid), int(position), 1)
        self.pending = PendingInventoryAction("equip", int(object_uid), self._quantity(item), self._position(item), int(position))

    def observe(self, state: WorldState) -> dict[str, Any] | None:
        pending = self.pending
        if pending is None:
            return None
        item = next((entry for entry in state.inventory.get("objects", ())
                     if entry.get("uid") == pending.object_uid), None)
        if pending.kind == "equip":
            if item is None or self._position(item) != pending.target_position:
                return None
        else:
            # ``ObjectUse`` has no universal success event. Consumption/removal
            # is the only cross-item acknowledgement we can assert safely.
            if item is not None and (pending.quantity is None or self._quantity(item) is None
                                     or self._quantity(item) >= pending.quantity):
                return None
        self.pending = None
        return {"state": "confirmed", "kind": pending.kind, "object_uid": pending.object_uid,
                **({"position": pending.target_position} if pending.kind == "equip" else {})}

    @staticmethod
    def _item(state: WorldState, object_uid: int) -> dict[str, Any]:
        item = next((entry for entry in state.inventory.get("objects", ()) if entry.get("uid") == int(object_uid)), None)
        if item is None:
            raise ValueError("object UID is not present in the current inventory")
        return item

    @staticmethod
    def _quantity(item: dict[str, Any]) -> int | None:
        value = item.get("quantity")
        return int(value) if value is not None else None

    @staticmethod
    def _position(item: dict[str, Any]) -> int | None:
        value = item.get("position")
        return int(value) if value is not None else None

    def _ensure_idle(self) -> None:
        if self.pending is not None:
            raise ValueError("previous inventory action is awaiting a live inventory update")

    def expire(self, timeout_s: float = 20.0) -> dict[str, Any] | None:
        if self.pending is None or monotonic() - self.pending.started_at < timeout_s:
            return None
        kind, object_uid = self.pending.kind, self.pending.object_uid
        self.pending = None
        return {"state": "timed_out", "kind": kind, "object_uid": object_uid}

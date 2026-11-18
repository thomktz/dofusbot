import pytest

from dofus_brain.world.inventory_execution import InventoryExecutor
from dofus_brain.world.state import WorldState


class Transport:
    def __init__(self): self.uses, self.equips = [], []
    def use_item(self, uid): self.uses.append(uid)
    def set_item_position(self, uid, position, quantity): self.equips.append((uid, position, quantity))


def state(uid=5, quantity=3, position=63):
    value = WorldState.empty()
    value.inventory = {"objects": [{"uid": uid, "quantity": quantity, "position": position}]}
    return value


def test_item_use_waits_for_a_confirmed_consumption_or_removal():
    executor, transport = InventoryExecutor(), Transport()
    executor.use(state(), 5, transport)  # type: ignore[arg-type]
    assert transport.uses == [5]
    assert executor.observe(state()) is None
    assert executor.observe(state(quantity=2)) == {"state": "confirmed", "kind": "use", "object_uid": 5}


def test_equip_waits_for_live_target_position_and_serializes_mutations():
    executor, transport = InventoryExecutor(), Transport()
    executor.equip(state(), 5, 6, transport)  # type: ignore[arg-type]
    assert transport.equips == [(5, 6, 1)]
    with pytest.raises(ValueError, match="previous"):
        executor.use(state(), 5, transport)  # type: ignore[arg-type]
    assert executor.observe(state(position=63)) is None
    assert executor.observe(state(position=6)) == {"state": "confirmed", "kind": "equip", "object_uid": 5, "position": 6}


def test_inventory_executor_refuses_stale_uid():
    with pytest.raises(ValueError, match="not present"):
        InventoryExecutor().use(state(), 9, Transport())  # type: ignore[arg-type]


def test_inventory_timeout_releases_barrier_without_claiming_success():
    executor, transport = InventoryExecutor(), Transport()
    executor.use(state(), 5, transport)  # type: ignore[arg-type]
    executor.pending.started_at = 0
    assert executor.expire(0.1) == {"state": "timed_out", "kind": "use", "object_uid": 5}

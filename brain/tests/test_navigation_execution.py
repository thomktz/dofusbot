from dofus_brain.world.navigation_execution import NavigationExecutor
from dofus_brain.world.state import WorldState


class Transport:
    def __init__(self): self.moves, self.changes = [], []
    def move(self, map_id, cells): self.moves.append((map_id, cells))
    def change_map(self, map_id): self.changes.append(map_id)


def state(map_id, cell):
    value = WorldState.empty(); value.map_id = map_id; value.character = {"cell": cell}; return value


def route():
    return {"found": True, "target_map_id": 12, "execution": {"ready": True, "kind": "move_then_map_change",
            "move": {"map_id": 10, "cells": [1, 2], "key_cells": [99]},
            "map_change": {"transition_map_id": 1002, "expected_map_id": 11}}}


def test_navigation_waits_for_approach_then_observed_map_change():
    executor, transport = NavigationExecutor(), Transport()
    assert executor.start(state(10, 1), route(), transport)["state"] == "moving"
    assert transport.moves == [(10, [99])] and transport.changes == []
    assert executor.observe(state(10, 2), transport) == {"state": "changing_map", "expected_map_id": 11}
    assert transport.changes == [1002]
    assert executor.observe(state(11, 2), transport) == {"state": "leg_completed", "target_map_id": 12, "map_id": 11}


def test_navigation_cancels_on_an_unexpected_map_change():
    executor, transport = NavigationExecutor(), Transport()
    executor.start(state(10, 1), route(), transport)
    assert executor.observe(state(99, 1), transport) == {"state": "cancelled", "reason": "unexpected map change", "target_map_id": 12}


class GoToTransport(Transport):
    def __init__(self):
        super().__init__(); self.go_to = []
    def go_to_map(self, map_id): self.go_to.append(map_id)


def test_navigation_uses_native_go_to_for_known_non_roaming_destination():
    executor, transport = NavigationExecutor(), GoToTransport()
    assert executor.start(state(10, 1), route(), transport)["state"] == "auto_pilot"
    assert transport.go_to == [12] and transport.moves == []
    # Intermediate maps are normal while the game drives the route.
    assert executor.observe(state(11, 2), transport) == {"state": "auto_pilot_progress", "target_map_id": 12, "map_id": 11}
    assert executor.observe(state(12, 3), transport) == {"state": "leg_completed", "target_map_id": 12, "map_id": 12}

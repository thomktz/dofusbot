import pytest

from dofus_brain.world.combat_execution import CombatExecutor
from dofus_brain.world.state import WorldState


class Transport:
    def __init__(self): self.casts, self.ends = [], 0
    def cast_spell(self, spell_id, cell): self.casts.append((spell_id, cell))
    def end_turn(self): self.ends += 1


def state(events=(), phase="turn"):
    value = WorldState.empty(); value.fight_phase = phase
    for event in events: value.apply_event(event)
    value.fight_phase = phase
    return value


def test_spell_waits_for_matching_new_server_event():
    executor, transport = CombatExecutor(), Transport()
    executor.cast(state([{"offset": 4, "kind": "turn_marker"}]), 12, 77, transport)  # type: ignore[arg-type]
    assert transport.casts == [(12, 77)]
    assert executor.observe(state([{"offset": 4, "kind": "spell_cast", "spell_id": 12, "destination_cell": 77}])) is None
    assert executor.observe(state([{"offset": 5, "kind": "spell_cast", "spell_id": 12, "destination_cell": 77}])) == {"state": "confirmed", "kind": "cast_spell", "spell_id": 12, "cell": 77, "offset": 5}


def test_end_turn_waits_for_new_turn_end_and_serializes_actions():
    executor, transport = CombatExecutor(), Transport()
    executor.end_turn(state([{"offset": 4, "kind": "turn_marker"}]), transport)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="previous"):
        executor.end_turn(state(), transport)  # type: ignore[arg-type]
    assert executor.observe(state([{"offset": 5, "kind": "turn_end"}])) == {"state": "confirmed", "kind": "end_turn", "offset": 5}


def test_combat_actions_refuse_non_turn_state():
    with pytest.raises(ValueError, match="current player turn"):
        CombatExecutor().end_turn(state(phase="placement"), Transport())  # type: ignore[arg-type]

from dofus_brain.world.action_trace import ActionTrace
from dofus_brain.world.state import WorldState


def test_trace_keeps_bounded_action_evidence_with_protocol_snapshot():
    state = WorldState.empty()
    state.map_id = 12
    state.character = {"cell": 42, "hp": 100, "hp_max": 100}
    trace = ActionTrace(limit=2)
    trace.record("action_requested", {"action": "attack_monster_group"}, state)
    trace.record("action_transition", {"state": "confirmed"}, state)
    trace.record("action_transition", {"state": "completed"}, state)
    payload = trace.payload()
    assert payload["retained"] == 2
    assert payload["entries"][-1]["snapshot"] == {"map_id": 12, "cell": 42, "fight_phase": "out_of_fight", "hp": 100, "hp_max": 100, "dialogue_open": False}

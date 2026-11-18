from dofus_brain.world.recovery_guard import RecoveryGuard
from dofus_brain.world.state import WorldState


def state(cell=1, phase="out_of_fight"):
    value = WorldState.empty()
    value.map_id = 42
    value.character = {"cell": cell}
    value.fight_phase = phase
    return value


def test_timeout_hold_blocks_until_materially_fresh_protocol_state_arrives():
    guard = RecoveryGuard()
    held = guard.hold(7, {"kind": "navigation", "state": "timed_out"}, state())
    assert held["state"] == "waiting_for_fresh_state"
    assert guard.observe(7, state()) is None
    assert guard.observe(7, state(cell=2)) == {"state": "recovered", "reason": "fresh protocol state observed"}
    assert guard.status(7) == {"state": "idle"}

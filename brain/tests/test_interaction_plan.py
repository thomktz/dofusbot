from dofus_brain.world.interaction import plan_interactive
from dofus_brain.world.state import Interactive, WorldState


def _state() -> WorldState:
    state = WorldState.empty()
    state.map_id = 10
    state.character = {"cell": 0}
    state.interactives[50] = Interactive(50, cell=2, name="Ash", available=True, skills=[1],
                                         details={"skill_instances": [{"skill_id": 1, "skill_instance_uid": 99}]})
    return state


def test_interactive_plan_moves_then_uses_live_skill_instance() -> None:
    plan = plan_interactive(_state(), 50)
    assert plan["ready"] is True
    assert plan["kind"] == "move_then_interact"
    assert plan["use"] == {"element_id": 50, "skill_instance_uid": 99, "skill_id": 1}
    assert plan["approach"]["key_cells"]


def test_interactive_plan_refuses_world_transition_cell() -> None:
    plan = plan_interactive(_state(), 50, exit_cells=[2])
    assert plan == {"ready": False, "reason": "interactive cell is a world-map transition; route it instead"}

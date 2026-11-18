from dofus_brain.managed_transport import ManagedTransport
from dofus_brain.world.interaction import plan_interactive
from dofus_brain.world.interaction_execution import InteractiveExecutor
from dofus_brain.world.state import Interactive, WorldState


class Transport:
    def __init__(self):
        self.moves = []
        self.uses = []

    def move(self, map_id, key_cells): self.moves.append((map_id, key_cells))
    def use_interactive(self, element_id, skill_uid): self.uses.append((element_id, skill_uid))


def state(cell=0, available=True):
    value = WorldState.empty()
    value.map_id = 10
    value.character = {"cell": cell}
    value.interactives[50] = Interactive(50, cell=2, name="Ash", available=available, skills=[1],
                                         details={"skill_instances": [{"skill_id": 1, "skill_instance_uid": 99}]})
    return value


def test_interactive_waits_for_arrival_before_sending_use():
    executor, transport = InteractiveExecutor(), Transport()
    result = executor.start(state(), plan_interactive(state(), 50), transport)  # type: ignore[arg-type]
    assert result["phase"] == "moving" and transport.uses == []
    approach = result["approach_cell"]
    assert executor.observe(state(approach), transport) == {"state": "using", "element_id": 50}
    assert transport.uses == [(50, 99)]


def test_interactive_completes_only_after_server_event_or_state_change():
    executor, transport = InteractiveExecutor(), Transport()
    plan = plan_interactive(state(1), 50)
    executor.start(state(1), plan, transport)  # type: ignore[arg-type]
    assert executor.status()["state"] == "using"
    assert executor.observe(state(1), transport) is None
    done = state(1, available=False)
    assert executor.observe(done, transport) == {"state": "completed", "element_id": 50}


def test_disconnect_after_movement_is_reported_without_leaving_a_pending_action():
    executor, transport = InteractiveExecutor(), Transport()
    result = executor.start(state(), plan_interactive(state(), 50), transport)  # type: ignore[arg-type]
    class BrokenTransport(Transport):
        def use_interactive(self, element_id, skill_uid): raise RuntimeError("session disconnected")
    transition = executor.observe(state(result["approach_cell"]), BrokenTransport())
    assert transition == {"state": "failed", "reason": "session disconnected", "element_id": 50}
    assert executor.status() == {"state": "idle"}


def test_interactive_ignores_lifecycle_event_that_predates_its_request():
    executor, transport = InteractiveExecutor(), Transport()
    before = state(1)
    before.events.append({"offset": 10, "kind": "interactive_ended", "element_id": 50})
    executor.start(before, plan_interactive(before, 50), transport)  # type: ignore[arg-type]
    # The old retained event is evidence about an earlier action, not this one.
    after = state(1)
    after.events.append({"offset": 10, "kind": "interactive_ended", "element_id": 50})
    assert executor.observe(after, transport) is None
    after.events.append({"offset": 11, "kind": "interactive_ended", "element_id": 50})
    assert executor.observe(after, transport) == {"state": "completed", "element_id": 50}

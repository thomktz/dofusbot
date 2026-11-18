from dofus_brain.world.state import Actor, WorldState
from dofus_brain.world.talk_execution import TalkExecutor


class Transport:
    def __init__(self): self.talks = []
    def talk(self, npc_id, action_id, map_id): self.talks.append((npc_id, action_id, map_id))


def state(map_id=10, dialog=None):
    value = WorldState.empty(); value.map_id = map_id; value.dialog = dialog
    value.actors[-2] = Actor(-2, "npc", cell=100, metadata={"npc_id": 77})
    return value


def test_talk_waits_for_the_matching_npc_dialogue_open():
    executor, transport = TalkExecutor(), Transport()
    executor.talk(state(), -2, transport)  # type: ignore[arg-type]
    assert transport.talks == [(77, 3, 10)]
    assert executor.observe(state()) is None
    assert executor.observe(state(dialog={"npc_id": 77, "message_id": 44})) == {"state": "confirmed", "kind": "talk", "npc_id": 77, "message_id": 44}


def test_talk_cancels_on_wrong_dialogue_or_timeout():
    executor, transport = TalkExecutor(), Transport()
    executor.talk(state(), -2, transport)  # type: ignore[arg-type]
    assert executor.observe(state(dialog={"npc_id": 99})) == {"state": "cancelled", "kind": "talk", "npc_id": 77, "reason": "a different dialogue opened"}
    executor.talk(state(), -2, transport)  # type: ignore[arg-type]
    executor.pending.started_at = 0
    assert executor.expire(0.1) == {"state": "timed_out", "kind": "talk", "npc_id": 77}

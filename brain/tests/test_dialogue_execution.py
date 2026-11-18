import pytest

from dofus_brain.world.dialogue_execution import DialogueExecutor
from dofus_brain.world.state import WorldState


class Transport:
    def __init__(self): self.replies = []
    def reply(self, reply_id): self.replies.append(reply_id)


def dialogue(message_id, replies):
    state = WorldState.empty()
    state.dialog = {"message_id": message_id, "replies": replies}
    return state


def test_dialogue_waits_for_a_different_server_question_before_next_reply():
    executor, transport = DialogueExecutor(), Transport()
    executor.choose(dialogue(10, [1, 2]), 2, transport)  # type: ignore[arg-type]
    assert transport.replies == [2]
    assert executor.observe(dialogue(10, [1, 2])) is None
    with pytest.raises(ValueError, match="awaiting"):
        executor.choose(dialogue(10, [1, 2]), 1, transport)  # type: ignore[arg-type]
    assert executor.observe(dialogue(11, [7])) == {"state": "advanced", "reply_id": 2, "message_id": 11, "replies": [7]}


def test_dialogue_close_is_an_acknowledged_terminal_transition():
    executor, transport = DialogueExecutor(), Transport()
    executor.choose(dialogue(10, [1]), 1, transport)  # type: ignore[arg-type]
    assert executor.observe(WorldState.empty()) == {"state": "closed", "reply_id": 1}


def test_dialogue_refuses_reply_not_in_current_server_question():
    with pytest.raises(ValueError, match="not available"):
        DialogueExecutor().choose(dialogue(10, [1]), 2, Transport())  # type: ignore[arg-type]


def test_dialogue_timeout_releases_the_reply_barrier():
    executor, transport = DialogueExecutor(), Transport()
    executor.choose(dialogue(10, [1]), 1, transport)  # type: ignore[arg-type]
    executor.pending.started_at = 0
    assert executor.expire(0.1) == {"state": "timed_out", "kind": "dialogue_reply", "reply_id": 1}
    assert executor.status() == {"state": "idle"}

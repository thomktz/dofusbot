import asyncio

from dofus_brain.llm.base import LLMClient, LLMResponse
from dofus_brain.llm.coordinator import SharedLLMCoordinator
from dofus_brain.world.orchestrator import LocalWorldOrchestrator
from dofus_brain.world.state import WorldState


class RecordingLLM(LLMClient):
    backend = "recording"

    def __init__(self):
        super().__init__("one-model")
        self.prompts = []

    async def _chat(self, messages, images, json_mode, schema, max_tokens):
        self.prompts.append(messages[-1]["content"])
        await asyncio.sleep(0)
        return LLMResponse('{"action":"observe_world","args":{}}')


def test_one_shared_model_scopes_each_queued_account_prompt() -> None:
    async def run():
        llm = RecordingLLM()
        coordinator = SharedLLMCoordinator(llm)
        await asyncio.gather(
            coordinator.chat_json(101, "State: first", schema={"type": "object"}),
            coordinator.chat_json(202, "State: second", schema={"type": "object"}),
        )
        return coordinator, llm

    coordinator, llm = asyncio.run(run())
    assert len(llm.prompts) == 2
    assert "scope: 101" in llm.prompts[0]
    assert "scope: 202" in llm.prompts[1]
    assert coordinator.status.to_json()["completed"] == 2
    assert coordinator.status.to_json()["active_account_id"] is None


def test_world_planner_uses_shared_model_with_its_own_account_scope() -> None:
    async def run():
        llm = RecordingLLM()
        coordinator = SharedLLMCoordinator(llm)
        state = WorldState.empty()
        await LocalWorldOrchestrator(coordinator, account_id=77).propose(state, "observe")
        return llm

    llm = asyncio.run(run())
    assert "scope: 77" in llm.prompts[0]

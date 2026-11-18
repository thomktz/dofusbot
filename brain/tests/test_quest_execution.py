from dofus_brain.world.quest_execution import QuestExecutor
from dofus_brain.world.state import WorldState


def test_quest_executor_only_navigates_to_verified_objective_map():
    plan = {"active_quest_plans": [{"guide": {"found": True}, "missing": [], "objectives": [{"map_id": 42, "reached": False}]}]}
    state = WorldState.empty(); state.map_id = 41
    step = QuestExecutor().next_step(plan, state)
    assert step.state == "navigate" and step.intent and step.intent.args == {"map_id": 42}


def test_quest_executor_stops_for_missing_items_or_ambiguous_objectives():
    state = WorldState.empty()
    assert QuestExecutor().next_step({"active_quest_plans": [{"missing": [{"item": "Milk"}]}]}, state).state == "needs_preparation"
    assert QuestExecutor().next_step({"active_quest_plans": [{"guide": {"found": True}, "objectives": [{}]}]}, state).state == "needs_human_or_guide"

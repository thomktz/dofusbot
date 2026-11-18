from dofus_brain.world.quest_planner import QuestPlanner


class GameData:
    def quest_info(self, quest_id, step_id):
        assert (quest_id, step_id) == (42, 7)
        return {"name": "Milk for Cat", "step_id": 7, "step_name": "Feed the cat", "step_description": "Bring milk."}

    def quest_graph(self, quest_id):
        assert quest_id == 42
        return {"prerequisites": [], "unlocks": [{"quest_id": 43}], "success_groups": [{"achievement_id": 9}]}


class Guides:
    def resolve(self, quest_id, name):
        return {"found": True, "quest_id": quest_id, "quest_name": name}

    def resolve_success(self, achievement_id):
        assert achievement_id == 9
        return {"found": True, "achievement_id": 9, "preparation": {"items": [["Milk", 2], ["Bread", 1]]}}


def test_quest_planner_reconciles_success_prep_with_live_inventory():
    result = {
        "quests": {"active": [{"quest_id": 42, "step_id": 7, "objectives": [{"description": "Give milk", "reached": False}]}]},
        "inventory": {"objects": [{"name": "Milk", "quantity": 1}, {"name": "Bread", "quantity": 1}]},
    }
    plan = QuestPlanner(GameData(), Guides()).plan(result)["active_quest_plans"][0]
    assert plan["status"] == "needs_preparation"
    assert plan["missing"] == [{"item": "Milk", "needed": 2, "have": 1, "missing": 1}]
    assert plan["unlocks"] == [{"quest_id": 43}]

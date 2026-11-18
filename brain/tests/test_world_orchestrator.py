import pytest

from dofus_brain.managed_transport import GameIntent
from dofus_brain.world import Actor, Interactive, WorldState
from dofus_brain.world.orchestrator import WorldIntent, WorldToolRegistry


def test_world_tools_validate_and_translate_protocol_actions():
    state = WorldState.empty()
    state.map_id = 101
    state.actors[-7] = Actor(-7, "npc", "Anta", cell=100, metadata={"npc_id": 2892})
    state.actors[-8] = Actor(-8, "monster_group", cell=102)
    state.interactives[4] = Interactive(4, cell=22, available=True,
                                        details={"skill_instances": [{"skill_id": 68, "skill_instance_uid": 99123}]})
    state.inventory = {"objects": [{"uid": 55, "gid": 88, "quantity": 2}]}
    state.dialog = {"stages": [{"message_id": 4, "options": [{"reply_id": 17, "text": "Continue"}]}]}
    tools = WorldToolRegistry(state)
    assert tools.game_intent(WorldIntent("talk_to_npc", {"entity_id": -7})) == GameIntent("npc_action", {"npc_id": 2892, "npc_action_id": 3, "npc_map_id": 101})
    assert tools.game_intent(WorldIntent("attack_monster_group", {"entity_id": -8})) == GameIntent("attack_monster", {"monster_group_id": -8})
    assert tools.game_intent(WorldIntent("choose_dialogue_reply", {"reply_id": 17})) == GameIntent("npc_dialog_reply", {"reply_id": 17})
    assert tools.game_intent(WorldIntent("leave_dialogue", {})) == GameIntent("dialog_leave", {})
    assert tools.game_intent(WorldIntent("use_interactive", {"element_id": 4})) == GameIntent("interactive_use", {"element_id": 4, "skill_instance_uid": 99123})
    assert tools.game_intent(WorldIntent("use_inventory_item", {"object_uid": 55})) == GameIntent("object_use", {"object_uid": 55})
    assert tools.game_intent(WorldIntent("equip_inventory_item", {"object_uid": 55, "position": 6})) == GameIntent("object_set_position", {"object_uid": 55, "position": 6, "quantity": 1})
    with pytest.raises(ValueError):
        tools.validate(WorldIntent("choose_dialogue_reply", {"reply_id": 18}))


def test_world_tools_reject_invalid_fight_action():
    with pytest.raises(ValueError):
        WorldToolRegistry(WorldState.empty()).validate(WorldIntent("cast_spell", {"spell_id": 1, "cell": 2}))


def test_world_tools_admit_navigation_only_as_a_hub_expanded_intent():
    state = WorldState.empty()
    state.map_id = 101
    tools = WorldToolRegistry(state)
    intent = WorldIntent("navigate_to_map", {"map_id": 202})
    assert tools.validate(intent) is intent
    assert tools.game_intent(intent) is None
    with pytest.raises(ValueError, match="already"):
        tools.validate(WorldIntent("navigate_to_map", {"map_id": 101}))


def test_world_tools_scope_quest_guide_lookup_to_active_quests():
    state = WorldState.empty()
    state.quests = {"active": [{"quest_id": 1649}]}
    tools = WorldToolRegistry(state)
    assert tools.validate(WorldIntent("inspect_active_quests", {})).action == "inspect_active_quests"
    assert tools.game_intent(WorldIntent("get_active_quest_guide", {"quest_id": 1649})) is None
    with pytest.raises(ValueError, match="not active"):
        tools.validate(WorldIntent("get_active_quest_guide", {"quest_id": 999}))


def test_world_tools_gate_spell_and_turn_by_real_turn_state():
    state = WorldState.empty()
    state.fight_phase = "turn"
    state.character = {"cell": 42}
    state.spells = [{"spell_id": 25, "available": True, "min_range": 1, "range": 2}]
    tools = WorldToolRegistry(state)
    assert tools.game_intent(WorldIntent("cast_spell", {"spell_id": 25, "cell": 57})) == GameIntent("fight_cast", {"spell_id": 25, "cell": 57})
    assert tools.game_intent(WorldIntent("end_turn", {})) == GameIntent("fight_turn_finish", {})
    with pytest.raises(ValueError, match="outside known spell range"):
        tools.validate(WorldIntent("cast_spell", {"spell_id": 25, "cell": 140}))


def test_world_tools_enforce_spell_line_and_known_los():
    state = WorldState.empty()
    state.fight_phase, state.character = "turn", {"cell": 42}
    state.spells = [{"spell_id": 9, "available": True, "min_range": 1, "range": 8, "cast_in_line": True, "requires_los": True}]
    tools = WorldToolRegistry(state)
    # 57 is an edge-neighbour of 42 and therefore a legal straight-line cast.
    assert tools.validate(WorldIntent("cast_spell", {"spell_id": 9, "cell": 57})).action == "cast_spell"
    with pytest.raises(ValueError, match="straight line"):
        tools.validate(WorldIntent("cast_spell", {"spell_id": 9, "cell": 58}))
    # Cell 57 lies between 42 and 71 on this Dofus diamond grid.
    state.cells[57].obstacle = True
    with pytest.raises(ValueError, match="line of sight"):
        tools.validate(WorldIntent("cast_spell", {"spell_id": 9, "cell": 71}))


def test_agent_context_exposes_all_actionable_entities_and_terrain():
    state = WorldState.empty()
    state.map_id = 101
    state.cells[7].movable = True
    state.cells[8].obstacle = True
    state.actors[-7] = Actor(-7, "npc", "Anta", cell=100, metadata={"npc_id": 2892})
    state.interactives[4] = Interactive(4, cell=22, name="Wheat", available=True,
                                        details={"skill_instances": [{"skill_instance_uid": 99123}]})
    context = state.agent_context()
    assert context["map"]["terrain"]["walkable_cells"] == [7]
    assert context["map"]["terrain"]["blocked_cells"] == [8]
    assert context["actors"] == [{"entity_id": -7, "kind": "npc", "name": "Anta", "cell": 100,
                                   "level": None, "team": None, "alive": True, "metadata": {"npc_id": 2892}}]
    assert context["interactives"][0]["name"] == "Wheat"

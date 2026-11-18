from dofus_brain.world.state import WorldState


def test_world_state_reduces_actors_and_paths() -> None:
    state = WorldState.from_scan({
        "groups": [{"entity_id": -1, "cell": 231, "members": [{"monster_id": 3250}]}],
        "players": [{"entity_id": 42, "name": "Cra", "cell": 317}],
        "fight_events": [
            {"kind": "fighter_show", "actor_id": -1, "cell": 231, "team": 1},
            {"kind": "movement", "actor_id": -1, "path_cells": [231, 245, 259], "end_cell": 259, "turn": 1},
            {"kind": "death", "dead_id": -1, "turn": 2},
        ],
    })
    assert state.actors[-1].cell == 259
    assert state.actors[-1].alive is False
    assert state.movement_paths == [{"actor_id": -1, "cells": [231, 245, 259], "turn": 1}]
    assert state.actor_ids_at(259) == []


def test_world_state_marks_saved_terrain() -> None:
    state = WorldState.from_scan({"terrain": {"blocked": [10], "holes": [11], "red": [12], "blue": [13]}})
    assert state.cells[10].obstacle is True and state.cells[10].movable is False
    assert state.cells[11].movable is None
    assert state.cells[12].start_team == "red"
    assert state.cells[13].start_team == "blue"


def test_fight_state_tracks_phase_and_placement_ack() -> None:
    state = WorldState.from_scan({
        "fight": {"active": True, "map_id": 123, "fighters": [{"id": 10, "cell": 100, "team": 0}]},
        "fight_events": [
            {"kind": "fighter_show", "actor_id": 10, "cell": 100, "team": 0},
            {"kind": "placement_possible", "cells": [100, 101, 102]},
            {"kind": "placement_update", "actor_id": 10, "cell": 101},
            {"kind": "turn_marker", "actor_id": 10, "turn": 1},
        ],
    })
    assert state.fight_phase == "turn"
    assert state.fight_turn == 1
    assert state.fight_actor_id == 10
    assert state.legal_placement_cells == {100, 101, 102}
    assert state.actors[10].cell == 101


def test_world_state_exposes_safe_action_affordances() -> None:
    state = WorldState.from_scan({
        "groups": [{"entity_id": -1, "cell": 231, "members": [{"monster_id": 3250}]}],
        "players": [{"entity_id": 42, "name": "Cra", "cell": 317}],
        "interactives": [{"id": 9, "cell": 81, "name": "Ortie", "available": True, "skills": [68]}],
        "map_events": [{"kind": "map_snapshot", "actors": [
            {"entity_id": -1, "kind": "monster_group", "cell": 231, "metadata": {"members": [{"monster_id": 3250}]}},
            {"entity_id": -2, "kind": "npc", "name": "Anta Brok", "cell": 123, "metadata": {}},
        ]}],
        "dialog": {"stages": [{"options": [{"reply_id": 17, "text": "Continue"}]}]},
    })
    actions = state.to_json()["affordances"]
    assert {action["kind"] for action in actions} == {"attack", "talk", "use", "dialog_reply"}
    assert next(action for action in actions if action["kind"] == "talk")["label"] == "Anta Brok"
    assert next(action for action in actions if action["kind"] == "dialog_reply")["reply_id"] == 17
    attack = next(action for action in actions if action["kind"] == "attack")
    assert attack["label"] == "Monster 3250"
    assert attack["members"] == [{"name": "Monster 3250", "level": None, "monster_id": 3250}]
    compact = state.agent_context()
    assert compact["map"]["in_fight"] is False
    assert "cells" not in compact and "events" not in compact
    assert compact["affordances"] == actions


def test_agent_context_compacts_inventory_spells_quests_and_dialogue() -> None:
    state = WorldState.from_scan({
        "inventory": {"kamas": 321, "weight": 17, "weight_max": 100, "objects": [
            {"uid": 1, "gid": 44, "name": "Ash wood", "quantity": 3, "container": "bag"},
            {"uid": 2, "gid": 44, "name": "Ash wood", "quantity": 7, "container": "bag"},
            {"uid": 3, "gid": 45, "name": "Starter hat", "quantity": 1, "container": "equipped", "position": 6, "position_name": "Hat"},
        ]},
        "spells": [{"spell_id": 9, "name": "Magic Arrow", "available": True, "ap_cost": 3, "range": 8}],
        "shortcuts": [{"kind": "spell", "spell_id": 9, "slot": 2}],
        "quests": {"active": [{"quest_id": 12, "name": "First steps", "step_id": 4, "step_name": "Talk",
                                  "objectives": [{"objective_id": 22, "reached": False, "description": "Visit Anta", "coords": {"x": 4, "y": -18}}]}],
                   "completed": [{"quest_id": 1}]},
        "dialog": {"npc_name": "Anta", "npc_id": 7, "actor_id": -9, "stages": [
            {"message_id": 100, "text": "Hello", "options": [{"reply_id": 1, "text": "Continue"}]},
            {"message_id": 101, "text": "Choose", "options": [{"reply_id": 2, "text": "Leave"}]},
        ]},
    })
    context = state.agent_context()
    assert context["inventory"] == {
        "kamas": 321, "pods": {"used": 17, "max": 100, "percent": 17.0},
        "items": [
            {"name": "Starter hat", "item_id": 45, "level": None, "container": "equipped", "slot": "Hat", "quantity": 1, "object_uids": [3]},
            {"name": "Ash wood", "item_id": 44, "level": None, "container": "bag", "slot": None, "quantity": 10, "object_uids": [1, 2]},
        ],
    }
    assert context["spells"] == [{"spell_id": 9, "name": "Magic Arrow", "available": True, "ap_cost": 3, "range": 8, "slot": 2}]
    assert context["quests"] == {"active": [{"quest_id": 12, "name": "First steps", "step_id": 4, "step": "Talk", "objectives": [
        {"objective_id": 22, "reached": False, "description": "Visit Anta", "type_name": None, "completion": None, "coords": {"x": 4, "y": -18}, "map_id": None},
    ]}], "completed_count": 1}
    assert context["dialog"] == {"npc": "Anta", "npc_id": 7, "actor_id": -9, "stage": 2,
                                  "history": [{"message_id": 100, "text": "Hello", "options": [{"reply_id": 1, "text": "Continue"}]}],
                                  "current": {"message_id": 101, "text": "Choose", "options": [{"reply_id": 2, "text": "Leave"}]}}

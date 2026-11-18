"""Regression coverage for the protocol state consumed by future automation."""
from dofus_brain.network_scan import NetworkCharacter, NetworkPlayer, _dialog, _join_character_actor, _map_events, _quests
from dofus_brain.world.map_data import GameDataProvider


def _varint(value: int) -> bytes:
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def _scalar(field: int, value: int) -> bytes:
    return _varint(field << 3) + _varint(value)


def _blob(field: int, value: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(value)) + value


def _event(alias: str, payload: bytes) -> bytes:
    name = f"type.ankama.com/{alias}".encode()
    return b"\x0a" + _varint(len(name)) + name + b"\x12" + _varint(len(payload)) + payload


def test_active_quest_snapshot_retains_step_and_objective_progress() -> None:
    # The current obfuscated build shuffles field numbers.  This records only
    # the stable nested shape: QuestActive → Details → QuestObjective.
    objective = _scalar(4, 12345) + _scalar(1, 0) + _blob(3, _scalar(1, 2) + _scalar(2, 5))
    details = _scalar(1, 42) + _blob(2, objective)
    active = _scalar(1, 123) + _blob(2, details)
    result = _quests(_event("ica", _blob(6, active)))
    assert result == {"active": [{"quest_id": 123, "step_id": 42, "objectives": [
        {"objective_id": 12345, "reached": False, "params": [], "completion": {"current": 2, "max": 5}},
    ]}], "completed": []}


def test_dialog_keeps_every_stage_and_clears_when_closed() -> None:
    created = _event("ilx", _scalar(5, 501))
    first = _event("ilf", _blob(1, _scalar(1, 11)) + _blob(1, _scalar(1, 12)) + _blob(2, b"Chern-Simons") + _scalar(3, 9001))
    second = _event("ilf", _blob(1, _scalar(1, 21)) + _scalar(3, 9002))
    dialog = _dialog(created + first + second)
    assert dialog is not None
    assert dialog.actor_id == 501
    assert dialog.message_id == 9002
    assert dialog.replies == [21]
    assert dialog.stages == [
        {"message_id": 9001, "params": ["Chern-Simons"], "replies": [11, 12]},
        {"message_id": 9002, "params": [], "replies": [21]},
    ]
    assert _dialog(created + first + _event("kis", b"")) is None


def test_interactive_retains_static_skill_and_protocol_instance_uid() -> None:
    # Astra's InteractiveElementSkill has skill_instance_uid (field 1) and
    # static skill_id (field 3). The former, not the latter, must be sent by
    # InteractiveUseRequest.
    skill = _scalar(1, 99123) + _scalar(3, 68)
    interactive = _scalar(6, 444) + _blob(5, skill)
    _map, _events, interactives, _states = _map_events(_event("jnj", _blob(7, interactive)))
    assert interactives == [{"id": 444, "cell": None, "type_id": None, "skills": [68],
                             "skill_instances": [{"skill_id": 68, "skill_instance_uid": 99123, "name_id": None}],
                             "available": None, "_offset": 0, "metadata": {"raw": interactive.hex()}}]


def test_trade_request_and_close_use_current_astra_aliases() -> None:
    request = _event("kaz", _scalar(1, 3) + _scalar(2, 42) + _scalar(3, 77))
    close = _event("kbr", _scalar(1, 3) + _scalar(2, 1))
    _map, events, _interactives, _states = _map_events(request + close)
    assert events == [
        {"kind": "trade_requested", "type": "kaz", "offset": 0, "exchange_type": 3, "target_id": 42, "source_id": 77},
        {"kind": "exchange_left", "type": "kbr", "offset": len(request), "dialog_type": 3, "success": True},
    ]


def test_incremental_quest_events_follow_known_quest_identity() -> None:
    # Give the snapshot one active quest/objective, then deliberately reverse
    # scalar ordering in the incremental packets. Known active quest identity
    # must win over wire order.
    objective = _scalar(4, 12345) + _scalar(1, 0)
    details = _scalar(1, 42) + _blob(2, objective)
    active = _scalar(1, 123) + _blob(2, details)
    stream = _event("ica", _blob(6, active))
    stream += _event("ici", _scalar(9, 77) + _scalar(2, 123))
    stream += _event("ibg", _scalar(8, 12345) + _scalar(1, 123))
    stream += _event("ibe", _scalar(1, 123) + _scalar(2, 77))
    quest = _quests(stream)["active"][0]
    assert quest["step_id"] == 77
    assert quest["validated_step_id"] == 77
    assert quest["objectives"][0]["reached"] is True


def test_quest_objective_local_data_resolves_text_without_id_collisions() -> None:
    data = GameDataProvider()
    # Parameter 1 in this recipe objective is a quantity, but it also happens
    # to be a valid NPC ID. The resolver must not turn it into an NPC name.
    craft = data.quest_objective_info(9863)
    assert craft["description"] == "Fabriquer 1 Pain d'Incarnam et fermer l'interface"
    assert craft["parameter_labels"] == {"parameter0": "Pain d'Incarnam"}
    talk = data.quest_objective_info(13793)
    assert talk["description"] == "Aller voir Milicienne de Bonta"


def test_local_quest_database_search_and_record() -> None:
    data = GameDataProvider()
    assert any(result["quest_id"] == 1629 for result in data.search_quests("anneau"))
    record = data.quest_record(1629)
    assert record["found"] is True
    assert record["name"] == "L'anneau de tous les dangers"
    assert len(record["steps"]) == 1


def test_local_quest_graph_links_unlocks_and_success_groups() -> None:
    data = GameDataProvider()
    graph = data.quest_graph(1629)
    assert graph["found"] is True
    assert any(item["quest_id"] == 1630 for item in graph["unlocks"])
    assert any(item["achievement_id"] == 1378 for item in data.quest_graph(1631)["success_groups"])


def test_character_header_joins_name_and_cell_from_roleplay_or_fight_actor() -> None:
    character = NetworkCharacter(entity_id=42)
    _join_character_actor(character, [NetworkPlayer(42, "Chern-Simons", 22)], [])
    assert (character.name, character.cell) == ("Chern-Simons", 22)
    fighting = NetworkCharacter(entity_id=42)
    _join_character_actor(fighting, [], [NetworkPlayer(42, "Chern-Simons", 301)])
    assert (fighting.name, fighting.cell) == ("Chern-Simons", 301)

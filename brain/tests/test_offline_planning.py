from dofus_brain.world.offline_planning import farming_candidates, pack_assessment, quest_preparation


def test_farming_candidates_only_uses_visible_enabled_protocol_resources():
    result = {"interactives": [
        {"id": 1, "cell": 42, "name": "Blé", "available": True, "skill_instances": [{"skill_id": 1, "skill_instance_uid": 9}]},
        {"id": 2, "cell": 43, "name": "Blé", "available": False, "skill_instances": [{"skill_instance_uid": 10}]},
    ]}
    assert farming_candidates(result, "blé")["candidates"] == [{"element_id": 1, "cell": 42, "name": "Blé", "job": None, "level_min": None, "skills": [{"skill_id": 1, "name": None}]}]


def test_quest_preparation_prefers_confirmed_missing_items():
    plan = {"source": "test", "active_quest_plans": [{"quest_id": 1, "quest": "Milk", "missing": [{"item": "Milk", "missing": 1}], "objectives": []}]}
    assert quest_preparation(plan)["actions"][0]["kind"] == "obtain_items"


def test_pack_assessment_never_claims_a_win():
    assessment = pack_assessment({"level": 10, "hp": 100, "hp_max": 100}, {"entity_id": -1, "cell": 3, "members": [{"level": 5}]})
    assert assessment["risk"] == "review"
    assert "inspect" in assessment["reason"]
    assert assessment["fight_ready"] is True


def test_pack_assessment_requires_protocol_confirmed_full_health():
    assessment = pack_assessment({"level": 10, "hp": 70, "hp_max": 100}, {"entity_id": -1, "cell": 3, "members": []})
    assert assessment["fight_ready"] is False
    assert "wait for full health" in assessment["fight_readiness_reason"]

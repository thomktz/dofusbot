from dofus_brain.network_scan import _fight_snapshot


def test_fight_snapshot_tracks_turn_and_death():
    events = [
        {"kind": "fighter_show", "actor_id": 10, "cell": 100, "team": 0},
        {"kind": "fighter_show", "actor_id": -20, "cell": 200, "team": 1},
        {"kind": "turn_marker", "actor_id": 10, "turn": 3},
        {"kind": "movement", "actor_id": 10, "end_cell": 101},
        {"kind": "death", "dead_id": -20},
    ]
    fight = _fight_snapshot(events, [], [], 123)
    assert fight["active"] is True
    assert fight["turn"] == 3
    assert {f["id"] for f in fight["fighters"]} == {10, -20}
    assert next(f for f in fight["fighters"] if f["id"] == -20)["alive"] is False


def test_fight_snapshot_clears_after_result():
    fight = _fight_snapshot([{"kind": "fighter_show", "actor_id": 10}, {"kind": "fight_result"}], [], [], 123)
    assert fight["active"] is False
    assert fight["phase"] == "out_of_fight"
    assert fight["fighters"] == []

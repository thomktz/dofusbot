import json

import pytest

from dofus_brain.world.world_route import WorldGraphUnavailable, WorldRouteProvider


def _graph() -> dict:
    vertex = lambda map_id, zone, uid: {"m_mapId": map_id, "m_zoneId": zone, "m_uid": uid}
    edge = lambda source, target, transition: {"m_from": source, "m_to": target, "m_transitions": [transition]}
    transition = lambda ident, cell: {"m_type": 1, "m_direction": 3, "m_skillId": 0, "m_criterion": "", "m_transitionMapId": ident + 1000, "m_cellId": cell, "m_id": ident}
    a, b, c = vertex(10, 1, 1), vertex(11, 1, 2), vertex(12, 2, 3)
    return {
        "m_vertices": {"10": {"1": a}, "11": {"1": b}, "12": {"2": c}},
        "m_outgoingEdges": {
            "1": {"m_edgeList": [edge(a, b, transition(101, 77)), edge(a, c, transition(102, 78))]},
            "2": {"m_edgeList": [edge(b, c, transition(103, 79))]},
        },
    }


def test_routes_explicit_transition_data(tmp_path) -> None:
    path = tmp_path / "world-graph.json"
    path.write_text(json.dumps(_graph()))
    result = WorldRouteProvider(path).route(10, 12)
    assert result["hops"] == 1
    assert result["steps"] == [{"from_map_id": 10, "from_zone_id": 1, "to_map_id": 12, "to_zone_id": 2,
                                "type": 1, "direction": 3, "skill_id": 0, "criterion": "",
                                "transition_map_id": 1102, "cell_id": 78, "id": 102}]


def test_lfs_pointer_fails_closed(tmp_path) -> None:
    path = tmp_path / "world-graph.json"
    path.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:abc\n")
    provider = WorldRouteProvider(path)
    assert provider.status()["available"] is False
    with pytest.raises(WorldGraphUnavailable, match="Git-LFS pointer"):
        provider.route(10, 12)

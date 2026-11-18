from dofus_brain import network_scan


def _varint(value: int) -> bytes:
    out = bytearray()
    while value >= 128:
        out.append((value & 127) | 128); value >>= 7
    out.append(value)
    return bytes(out)


def _field(number: int, value: int | bytes) -> bytes:
    if isinstance(value, bytes):
        return _varint(number << 3 | 2) + _varint(len(value)) + value
    return _varint(number << 3) + _varint(value)


def test_achievement_reducer_only_decodes_verified_mappings(monkeypatch):
    objective = _field(1, 55) + _field(2, 10) + _field(3, 4)
    detailed = _field(1, 42) + _field(2, objective)
    completed = _field(1, 42)
    monkeypatch.setattr(network_scan, "_any_events", lambda _: [(1, "mdo", _field(1, detailed)), (2, "mdp", _field(1, completed)), (3, "unknown", _field(1, detailed))])
    assert network_scan._achievements(b"ignored") == {"detailed": [{"achievement_id": 42, "objectives": [{"objective_id": 55, "max": 10, "current": 4}]}], "completed": [{"achievement_id": 42}]}

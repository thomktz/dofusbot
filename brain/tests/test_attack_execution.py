from dofus_brain.world.attack_execution import AttackExecutor
from dofus_brain.world.state import Actor, WorldState


class Transport:
    def __init__(self): self.attacks = []
    def attack_monster(self, group_id): self.attacks.append(group_id)


def state(map_id=10, fight="out_of_fight"):
    value = WorldState.empty(); value.map_id = map_id; value.fight_phase = fight
    value.character = {"hp": 100, "hp_max": 100}
    value.actors[-5] = Actor(-5, "monster_group", cell=100)
    return value


def test_attack_waits_for_fight_state_confirmation():
    executor, transport = AttackExecutor(), Transport()
    executor.attack(state(), -5, transport)  # type: ignore[arg-type]
    assert transport.attacks == [-5]
    assert executor.observe(state()) is None
    assert executor.observe(state(fight="placement")) == {"state": "confirmed", "kind": "attack", "group_id": -5, "fight_map_id": 10}


def test_attack_timeout_and_map_change_are_not_success():
    executor, transport = AttackExecutor(), Transport()
    executor.attack(state(), -5, transport)  # type: ignore[arg-type]
    assert executor.observe(state(map_id=11)) == {"state": "cancelled", "kind": "attack", "group_id": -5, "reason": "map changed before fight start"}
    executor.attack(state(), -5, transport)  # type: ignore[arg-type]
    executor.pending.started_at = 0
    assert executor.expire(0.1) == {"state": "timed_out", "kind": "attack", "group_id": -5}


def test_attack_refuses_any_non_full_or_unconfirmed_health():
    executor, transport = AttackExecutor(), Transport()
    wounded = state(); wounded.character = {"hp": 99, "hp_max": 100}
    import pytest
    with pytest.raises(ValueError, match="not at full health"):
        executor.attack(wounded, -5, transport)  # type: ignore[arg-type]
    unconfirmed = state(); unconfirmed.character = {}
    with pytest.raises(ValueError, match="not protocol-confirmed"):
        executor.attack(unconfirmed, -5, transport)  # type: ignore[arg-type]
    assert transport.attacks == []

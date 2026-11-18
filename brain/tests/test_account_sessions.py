import pytest

from dofus_brain.managed_transport import ManagedTransport
from dofus_brain.world.sessions import AccountSessionRegistry


def test_each_account_has_isolated_state_and_transport() -> None:
    registry = AccountSessionRegistry()
    first = registry.bind_transport(101, "game-a", ManagedTransport())
    second = registry.bind_transport(202, "game-b", ManagedTransport())
    registry.update_scan(101, {"map_id": 10, "character": {"id": 1, "name": "First", "level": 20}})
    registry.update_scan(202, {"map_id": 20, "character": {"id": 2, "name": "Second", "level": 30}})
    assert first.transport is not second.transport
    assert registry.get(101).scan["map_id"] == 10
    assert registry.get(202).scan["map_id"] == 20
    assert registry.list()[1]["character"] == {"id": 2, "name": "Second", "level": 30}


def test_session_cannot_be_bound_to_two_accounts() -> None:
    registry = AccountSessionRegistry()
    registry.bind_transport(101, "same-game-connection", ManagedTransport())
    with pytest.raises(ValueError, match="already bound"):
        registry.bind_transport(202, "same-game-connection", ManagedTransport())

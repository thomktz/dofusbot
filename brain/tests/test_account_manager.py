from dofus_brain.world.account_manager import AccountManager
from dofus_brain.managed_transport import ManagedTransport


def test_manager_owns_one_default_account_without_a_terminal_per_account():
    manager = AccountManager()
    manager.observe_primary({"map_id": 42, "character": {"name": "Cra", "level": 18}})
    payload = manager.payload()
    assert payload["mode"] == "independent"
    assert payload["model_runtime"] == "shared"
    assert payload["accounts"][0]["map_id"] == 42


def test_selection_changes_command_scope_and_never_creates_an_account():
    manager = AccountManager()
    manager.observe(12, {"map_id": 12, "character": {"name": "First"}})
    manager.observe(34, {"map_id": 34, "character": {"name": "Second"}})
    manager.select(34)
    assert manager.payload()["active_account_id"] == 34
    assert manager.scan()["map_id"] == 34
    try:
        manager.select(999)
    except ValueError as exc:
        assert "not connected" in str(exc)
    else:
        raise AssertionError("unknown dashboard account must not be fabricated")


def test_legacy_scan_stays_on_its_real_source_when_dashboard_scope_changes():
    manager = AccountManager()
    manager.observe(12, {"map_id": 12})
    manager.observe_primary({"map_id": 1})
    manager.select(12)
    manager.observe_primary({"map_id": 2})
    assert manager.sessions.get(0).scan["map_id"] == 2
    assert manager.sessions.get(12).scan["map_id"] == 12


def test_identified_proxy_transport_keeps_passive_observation_in_legacy_scope():
    manager = AccountManager()
    manager.observe(12, {"map_id": 12})
    manager.bind_identified_transport(34, "session-34", ManagedTransport())
    manager.observe_primary({"map_id": 99})
    assert manager.sessions.get(34).session_id == "session-34"
    assert manager.sessions.get(34).scan is None
    assert manager.sessions.get(0).scan["map_id"] == 99
    assert manager.active_account_id == 0

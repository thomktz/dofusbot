from dofus_brain.world.client_lifecycle import ClientLifecycleManager


def test_each_profile_has_an_isolated_zaap_port_and_launch_record():
    manager = ClientLifecycleManager()
    first, second = manager.get_or_create("main"), manager.get_or_create("farmer")
    assert (first.zaap_port, second.zaap_port) == (26116, 26117)
    manager.mark_launched("main", 123)
    assert manager.status()[0] == {"profile_id": "farmer", "zaap_port": 26117, "state": "idle", "pid": None, "error": None}
    assert manager.status()[1]["pid"] == 123


def test_client_cannot_be_launched_twice_from_one_profile():
    manager = ClientLifecycleManager(); manager.mark_launched("main", 1)
    try:
        manager.mark_launched("main", 2)
    except ValueError as exc:
        assert "already" in str(exc)
    else:
        raise AssertionError("second launch must not share the client record")

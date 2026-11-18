import threading
import time

import pytest

from dofus_control.errors import KillSwitchTripped
from dofus_control.keeptop import KeepOnTop
from dofus_control.killswitch import KillSwitch, KillSwitchMonitor, point_segment_distance
from dofus_control.types import Rect


def test_trip_reset_callbacks():
    ks = KillSwitch()
    got = []
    ks.on_kill(got.append)
    ks.on_kill(lambda r: 1 / 0)          # a failing callback must not break anything
    ks.check()
    assert ks.trip("test") is True
    assert ks.trip("again") is False      # callbacks fire once per trip
    assert got == ["test"] and ks.tripped and ks.reason == "test"
    with pytest.raises(KillSwitchTripped, match="test"):
        ks.check()
    ks.reset()
    ks.check()
    assert not ks.tripped


def test_trip_from_threads():
    ks = KillSwitch()
    hits = []
    ks.on_kill(hits.append)
    ts = [threading.Thread(target=ks.trip, args=(f"t{i}",)) for i in range(20)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(hits) == 1


def test_user_mouse_only_during_action():
    ks = KillSwitch(user_move_threshold=40)
    ks.check_user_mouse((1000, 1000))     # no action running: ignored
    assert not ks.tripped
    with ks.action((100, 100)):
        ks.check_user_mouse((120, 110))   # small drift ok
        assert not ks.tripped
        ks.set_expected_mouse((200, 100))
        ks.check_user_mouse((150, 105))   # between previous and new expected: ok
        assert not ks.tripped
        ks.check_user_mouse((200, 200))
        assert ks.tripped and "user moved" in ks.reason


def test_point_segment_distance():
    assert point_segment_distance((5, 5), (0, 0), (10, 0)) == 5
    assert point_segment_distance((-3, 4), (0, 0), (10, 0)) == 5
    assert point_segment_distance((3, 4), (0, 0), (0, 0)) == 5


def test_monitor_corner_and_hotkey():
    ks = KillSwitch()
    pos = [(500.0, 500.0)]
    hot = [False]
    mon = KillSwitchMonitor(ks, lambda: pos[0], lambda: [Rect(0, 0, 1728, 1117)], lambda: hot[0])
    mon.poll_once()
    assert not ks.tripped
    pos[0] = (0.0, 0.0)
    mon.poll_once()
    assert ks.tripped and "corner" in ks.reason
    ks.reset()
    pos[0] = (500.0, 500.0)
    hot[0] = True
    mon.poll_once()
    assert ks.tripped and "hotkey" in ks.reason


def test_monitor_ignores_our_own_corner_move():
    ks = KillSwitch()
    mon = KillSwitchMonitor(ks, lambda: (1.0, 1.0), lambda: [Rect(0, 0, 100, 100)])
    with ks.action((1.0, 1.0)):
        mon.poll_once()
    assert not ks.tripped


def test_monitor_thread_detects_user_move():
    ks = KillSwitch()
    pos = [(100.0, 100.0)]
    mon = KillSwitchMonitor(ks, lambda: pos[0], lambda: [], interval=0.005)
    mon.start()
    try:
        with ks.action(pos[0]):
            pos[0] = (400.0, 400.0)
            assert ks.wait(1.0)
    finally:
        mon.stop()


def test_keep_on_top_pause_and_suppress():
    t = [0.0]
    ticks = []
    sup = [False]
    k = KeepOnTop(lambda: ticks.append(1) or True, lambda: sup[0], interval=1.0, clock=lambda: t[0])
    assert k.step() is True and k.raises == 1
    k.pause(5)
    assert k.paused and k.step() is False
    t[0] = 6.0
    assert not k.paused and k.step() is True
    sup[0] = True
    assert k.step() is False
    sup[0] = False
    k.pause(10)
    k.resume()
    assert k.step() is True


def test_keep_on_top_survives_errors_and_threads():
    calls = []

    def tick():
        calls.append(1)
        raise RuntimeError("boom")

    k = KeepOnTop(tick, interval=0.01)
    k.start()
    assert k.enabled
    time.sleep(0.1)
    k.stop()
    assert not k.enabled
    assert len(calls) >= 2 and k.last_error == "boom"

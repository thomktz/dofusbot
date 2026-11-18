"""Capture pacing: 1 fps at most, and 2.5 s of settling after a click (move / spell)."""
import pytest

from dofus_brain.pacing import PacedController
from dofus_brain.replay import ReplayController, ReplayFrame


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class Inner:
    def __init__(self):
        self.captures, self.executed, self.other = 0, [], 0

    def capture(self):
        self.captures += 1
        return self.captures

    def execute(self, a):
        self.executed.append(a)

    def find_window(self):
        self.other += 1
        return "win"


def paced(clock=None):
    clock = clock or FakeClock()
    return PacedController(Inner(), clock=clock, sleep=clock.advance), clock


def test_one_capture_per_second_at_most():
    p, clock = paced()
    p.capture()
    t0 = clock.t
    p.capture()
    assert clock.t - t0 == 1.0                       # waited the full second
    clock.advance(0.4)
    t1 = clock.t
    p.capture()
    assert clock.t - t1 == pytest.approx(0.6)                      # only the rest of the second
    clock.advance(3.0)
    t2 = clock.t
    p.capture()
    assert clock.t == t2                             # already more than 1 s: no wait


def test_settles_after_a_click_not_after_a_key():
    p, clock = paced()
    p.capture()
    clock.advance(1.0)
    p.execute({"kind": "click", "x": 1, "y": 2})     # move or cast
    t = clock.t
    p.capture()
    assert clock.t - t == 2.5
    assert p.inner.executed == [{"kind": "click", "x": 1, "y": 2}]
    clock.advance(1.0)
    p.execute({"kind": "key", "key": "&"})           # selecting a spell: nothing animates
    t = clock.t
    p.capture()
    assert clock.t == t


def test_everything_else_passes_through():
    p, _ = paced()
    assert p.find_window() == "win" and p.inner.other == 1


def test_replay_skips_frames_like_live(tmp_path):
    """Frames every 0.5 s: paced captures see one frame per second, and none of the frames
    shown during the 2.5 s after a click."""
    clock = FakeClock()
    frames = [ReplayFrame(tmp_path / f"{i:02d}.png", 100.0 + i * 0.5, str(i)) for i in range(20)]
    for f in frames:
        f.path.write_bytes(b"")
    ctl = ReplayController(frames, clock=clock, grid_cfg={}, regions_cfg={})
    p = PacedController(ctl, clock=lambda: ctl.now(), sleep=clock.advance)
    seen = []
    orig = ctl.capture
    ctl.capture = lambda: seen.append(ctl.current().label)   # no image decoding needed here
    p.capture(); p.capture(); p.capture()
    assert seen == ["0", "2", "4"]                   # 1 fps over 2 fps frames
    p.execute({"kind": "click", "x": 0, "y": 0})
    p.capture()
    assert seen[-1] == "9"                           # 2.5 s later: frames 5..8 skipped
    ctl.capture = orig

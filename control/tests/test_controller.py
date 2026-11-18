import numpy as np
import pytest

from dofus_control import (
    AlignmentFailed, Controller, HumanProfile, InvalidAction, KillSwitchTripped,
    PermissionMissing, Rect, WindowNotFound,
)
from dofus_control.errors import CalibrationFailed, WindowCovered, WindowMoved, WindowNotFrontmost
from dofus_control.markers import draw_markers
from dofus_control.types import PermissionStatus

from fakes import FakeBackend

DOFUS_PID = 100


def make(backend=None, **kw):
    b = backend or FakeBackend()
    if not b.wins:
        b.add_window(1, DOFUS_PID, "Dofus", Rect(100, 100, 1000, 728), title="Dofus - Bob")
        b.add_window(2, 200, "Google Chrome", Rect(0, 25, 1728, 1092), title="Dashboard", front=False)
        b.front = DOFUS_PID
    kw.setdefault("profile", HumanProfile(seed=1, speed=0))
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("start_kill_sources", False)
    return Controller("dofus", backend=b, **kw), b


def test_find_window():
    ctl, b = make()
    w = ctl.find_window()
    assert w.window_id == 1 and w.pid == DOFUS_PID and w.title == "Dofus - Bob"
    assert w.frame == Rect(100, 100, 1000, 728)
    assert w.content == Rect(100, 128, 1000, 700)
    assert w.title_bar_height == 28
    assert Controller("nothing", backend=b, start_kill_sources=False).find_window() is None


def test_find_window_title_via_ax_when_cg_titles_missing():
    b = FakeBackend()
    b.add_window(1, 5, "TextEdit", Rect(0, 0, 500, 500), title=None)
    ctl = Controller("textedit", title_contains="ax ti", backend=b, start_kill_sources=False)
    w = ctl.find_window()
    assert w is not None and w.title == "AX title"


def test_title_bar_override_and_detection_fallback():
    ctl, b = make(title_bar_height=0)
    assert ctl.find_window().content == Rect(100, 100, 1000, 728)
    b2 = FakeBackend()
    b2.add_window(1, 1, "Dofus", Rect(0, 30, 800, 600)).tb = None
    ctl2 = Controller("dofus", backend=b2, start_kill_sources=False)
    assert ctl2.find_window().title_bar_height == 28


def test_align_window_content():
    ctl, b = make()
    target = Rect(300, 120, 1280, 800)
    w = ctl.align_window(target)
    assert w.content == target and w.aligned is True
    assert w.frame == Rect(300, 92, 1280, 828)
    assert ctl.expected_content == target
    assert ctl.last_align.attempts == 1


def test_align_window_min_size_tolerated():
    ctl, b = make()
    b.get(1).min_w = 1400
    w = ctl.align_window(Rect(300, 120, 1280, 800))
    assert w.aligned is False and "kept size" in w.note
    assert w.content.x == 300 and w.content.y == 120
    with pytest.raises(AlignmentFailed):
        ctl.align_window(Rect(300, 120, 1280, 800), strict=True)


def test_align_window_menubar_clamp_fails():
    ctl, b = make()
    with pytest.raises(AlignmentFailed, match="menu bar"):
        ctl.align_window(Rect(300, 30, 1280, 800))   # frame y would be 2 < 25


def test_align_window_retries_and_errors():
    ctl, b = make()
    b.frozen_frames = True
    with pytest.raises(AlignmentFailed):
        ctl.align_window(Rect(300, 120, 1280, 800))
    assert ctl.last_align.attempts >= 2
    b.perms = PermissionStatus(False, True)
    with pytest.raises(PermissionMissing):
        ctl.align_window(Rect(300, 120, 1280, 800))
    ctl2 = Controller("nope", backend=FakeBackend(), start_kill_sources=False)
    with pytest.raises(WindowNotFound):
        ctl2.align_window(Rect(300, 120, 1280, 800))


def test_capture_crops_title_bar():
    ctl, b = make()
    fr = ctl.capture()
    assert fr.scale == 2.0
    assert fr.image.shape == (1400, 2000, 3)
    assert fr.image.dtype == np.uint8
    assert (fr.image == (10, 20, 30)).all()      # no title bar pixels
    assert fr.window.window_id == 1 and fr.ts > 1e12


def test_capture_display():
    ctl, b = make()
    fr = ctl.capture_display()
    assert fr.scale == 2.0 and fr.display_bounds == Rect(0, 0, 1728, 1117)


def test_execute_click_converts_window_points():
    ctl, b = make()
    ctl.align_window(Rect(300, 120, 1280, 800))
    b.events.clear()
    ctl.execute({"kind": "click", "x": 100, "y": 50, "exact": True})
    downs = [e for e in b.kinds("mouse") if e[1] == "down"]
    ups = [e for e in b.kinds("mouse") if e[1] == "up"]
    assert downs == [("mouse", "down", 400, 170, "left", 1)]
    assert ups == [("mouse", "up", 400, 170, "left", 1)]
    moves = [e for e in b.kinds("mouse") if e[1] == "move"]
    assert len(moves) > 5 and moves[-1][2:4] == (400, 170)


def test_execute_click_jitter_within_2pt_and_double_right():
    ctl, b = make()
    w = ctl.find_window()
    ctl.execute({"kind": "click", "x": 100, "y": 50, "button": "right", "double": True})
    downs = [e for e in b.kinds("mouse") if e[1] == "down"]
    assert [d[5] for d in downs] == [1, 2] and all(d[4] == "right" for d in downs)
    x, y = downs[0][2], downs[0][3]
    assert abs(x - (w.content.x + 100)) <= 2 and abs(y - (w.content.y + 50)) <= 2


def test_execute_drag_scroll_key_type():
    ctl, b = make()
    ctl.execute({"kind": "drag", "x": 10, "y": 10, "to_x": 200, "to_y": 100, "exact": True})
    kinds = [e[1] for e in b.kinds("mouse")]
    assert "drag" in kinds and kinds.index("down") < kinds.index("drag") < kinds.index("up")
    b.events.clear()
    ctl.execute({"kind": "scroll", "x": 10, "y": 10, "dy": 3})
    assert [e[3] for e in b.kinds("scroll")] == [-1, -1, -1]   # dy>0 = down = negative wheel
    b.events.clear()
    ctl.execute({"kind": "key", "key": "a", "modifiers": ["cmd", "shift"]})
    keys = b.kinds("key")
    # shift down, cmd down, a down, a up, cmd up, shift up
    assert [(k[1], k[2]) for k in keys] == [(56, True), (55, True), (0, True), (0, False), (55, False), (56, False)]
    assert keys[2][3] == 0x00020000 | 0x00100000 and keys[2][4] is None
    b.events.clear()
    ctl.execute({"kind": "type", "text": "hé\nx"})
    seq = [(e[0], e[1], e[2]) for e in b.events]
    assert seq == [("text", "h", True), ("text", "h", False), ("text", "é", True), ("text", "é", False),
                   ("key", 36, True), ("key", 36, False), ("text", "x", True), ("text", "x", False)]


def test_execute_key_char_attached():
    ctl, b = make(keyboard_layout="azerty")
    ctl.execute({"kind": "key", "key": "a"})
    k = b.kinds("key")
    assert k[0][1] == 12 and k[0][4] == "a"


def test_execute_refusals():
    ctl, b = make()
    with pytest.raises(InvalidAction):
        ctl.execute({"kind": "click", "x": 5000, "y": 5})
    ctl.align_window(Rect(300, 120, 1280, 800))
    # user moved the window
    w = b.get(1)
    from dataclasses import replace
    w.raw = replace(w.raw, bounds=w.raw.bounds.translated(50, 0))
    with pytest.raises(WindowMoved):
        ctl.execute({"kind": "click", "x": 5, "y": 5})
    ctl.forget_alignment()
    b.front = 200
    with pytest.raises(WindowNotFrontmost):
        ctl.execute({"kind": "click", "x": 5, "y": 5})
    # with keep_on_top enabled it re-activates instead
    ctl._kot.start()
    try:
        ctl.execute({"kind": "click", "x": 5, "y": 5})
        assert DOFUS_PID in b.activations
    finally:
        ctl.keep_on_top(False)
    # covering window in front at the click point
    b.add_window(9, 300, "Notes", Rect(300, 120, 100, 100), title="n")
    with pytest.raises(WindowCovered):
        ctl.execute({"kind": "click", "x": 10, "y": 10, "exact": True})  # window content now at x=350
    b.wins = [w for w in b.wins if w.raw.window_id != 9]
    b.wins = [w for w in b.wins if w.raw.window_id != 1]
    with pytest.raises(WindowNotFound):
        ctl.execute({"kind": "click", "x": 5, "y": 5})


def test_corner_guard():
    b = FakeBackend()
    b.menubar = 0
    b.add_window(1, DOFUS_PID, "Dofus", Rect(0, -28, 800, 628))
    b.front = DOFUS_PID
    ctl, _ = make(b)
    with pytest.raises(InvalidAction, match="corner"):
        ctl.execute({"kind": "click", "x": 1, "y": 1, "exact": True})


def test_kill_switch_blocks_execute_and_reset():
    ctl, b = make()
    got = []
    ctl.on_kill(got.append)
    ctl.kill()
    assert ctl.killed and got == ["kill()"]
    with pytest.raises(KillSwitchTripped):
        ctl.execute({"kind": "click", "x": 5, "y": 5})
    with pytest.raises(KillSwitchTripped):
        ctl.execute({"kind": "wait", "ms": 10})
    ctl.reset_kill()
    ctl.execute({"kind": "click", "x": 5, "y": 5})


def test_user_mouse_movement_trips_mid_action():
    ctl, b = make()
    n = [0]

    def hook(backend):
        n[0] += 1
        if n[0] == 3:  # the real user yanks the mouse away
            backend.mouse = (backend.mouse[0] + 300, backend.mouse[1] + 300)

    b.mouse_hook = hook
    with pytest.raises(KillSwitchTripped, match="user moved"):
        ctl.execute({"kind": "click", "x": 900, "y": 600})
    assert not [e for e in b.kinds("mouse") if e[1] == "down"]   # never clicked


def test_kill_mid_typing_releases_keys():
    ctl, b = make()
    count = [0]
    orig = b.post_text

    def post_text(t, down):
        orig(t, down)
        count[0] += 1
        if count[0] == 3:
            ctl.kill("test")

    b.post_text = post_text
    with pytest.raises(KillSwitchTripped):
        ctl.execute({"kind": "type", "text": "abcdef"})
    texts = b.kinds("text")
    downs = [t for t in texts if t[2]]
    ups = [t for t in texts if not t[2]]
    assert len(downs) == len(ups) == 2  # 'b' released even though killed mid-press


def test_hotkey_listener_wiring():
    b = FakeBackend()
    b.add_window(1, DOFUS_PID, "Dofus", Rect(100, 100, 1000, 728))
    ctl = Controller("dofus", backend=b, profile=HumanProfile(seed=1, speed=0), sleep=lambda s: None)
    try:
        assert ctl.hotkey_via_event_tap
        b.hotkey()
        assert ctl.killed and "hotkey" in ctl.kill_reason
    finally:
        ctl.close()


def test_keep_on_top_tick():
    ctl, b = make()
    b.add_window(9, 300, "Notes", Rect(150, 150, 200, 200), title="n")   # covers dofus
    b.front = 300
    assert ctl._kot_tick() is True and b.activations == [DOFUS_PID]
    assert ctl._kot_tick() is False   # now in front, not covered
    b.kb_idle = 0.2
    assert ctl._kot_suppressed() is True
    ctl.pause_keep_on_top(10)
    assert ctl._kot.step() is False


def test_calibrate_slot_hides_window_and_restores():
    b = FakeBackend()
    img = np.full((2234, 3456, 3), 40, np.uint8)
    draw_markers(img, Rect(600, 240, 2560, 1600), 24)
    b.display_image = img
    ctl, _ = make(b)
    rect = ctl.calibrate_slot()
    assert rect == Rect(300, 120, 1280, 800)
    cap = [e for e in b.events if e[0] == "capture_display"][0]
    assert cap[2] == (1,) and cap[3] == (DOFUS_PID,)     # excluded + hidden while capturing
    assert ("unhide", DOFUS_PID) in b.events and DOFUS_PID in b.activations
    assert not b.hidden


def test_calibrate_slot_failure_still_restores():
    ctl, b = make()
    with pytest.raises(CalibrationFailed):
        ctl.calibrate_slot()
    assert not b.hidden


def test_overlay_status():
    ctl, b = make()
    ctl.align_window(Rect(300, 120, 1280, 800))
    st = ctl.overlay_status(Rect(300, 120, 1280, 800), calibrated=True)
    assert st["aligned"] and st["dofus_window"]["found"] and st["dofus_window"]["pid"] == DOFUS_PID
    assert st["dofus_window"]["frame"] == {"x": 300, "y": 120, "w": 1280, "h": 800}


def test_capture_holding_presses_captures_releases():
    ctl, b = make()
    order = []
    orig_cap = b.capture_window

    def cap(wid):
        order.append(("capture", [k[2] for k in b.kinds("key")]))
        return orig_cap(wid)

    b.capture_window = cap
    fr = ctl.capture_holding("y", settle_ms=0)
    keys = b.kinds("key")
    assert [k[2] for k in keys] == [True, False] and keys[0][1] == keys[1][1]
    assert order == [("capture", [True])]               # captured while the key was down
    assert fr.image.shape[1] == round(1000 * fr.scale) and fr.window.window_id == 1


def test_capture_holding_releases_on_error_and_validates():
    ctl, b = make()

    def boom(wid):
        raise RuntimeError("capture failed")

    b.capture_window = boom
    with pytest.raises(RuntimeError):
        ctl.capture_holding("z", settle_ms=0)
    assert [k[2] for k in b.kinds("key")] == [True, False]
    with pytest.raises(InvalidAction):
        ctl.capture_holding("notakey")
    with pytest.raises(InvalidAction):
        ctl.capture_holding("y", settle_ms=5000)


def test_capture_holding_refuses_when_killed():
    ctl, b = make()
    ctl.kill("test")
    with pytest.raises(KillSwitchTripped):
        ctl.capture_holding("y", settle_ms=0)
    assert b.kinds("key") == []


def test_execute_azerty_spell_key_without_shift():
    ctl, b = make(keyboard_layout="azerty")
    ctl.execute({"kind": "key", "key": '"'})
    keys = b.kinds("key")
    assert [(k[1], k[2], k[3], k[4]) for k in keys] == [(20, True, 0, '"'), (20, False, 0, '"')]  # no shift (56)

import json

from dofus_brain.bus import Bus
from dofus_brain.mock_control import MockController
from dofus_brain.slot import SlotManager, estimate_from_report, rects_close

REPORT = {"rect_css": {"x": 300, "y": 60, "w": 1280, "h": 800},
          "window": {"screen_x": 10, "screen_y": 25, "outer_w": 1700, "outer_h": 1000, "inner_w": 1700, "inner_h": 920},
          "dpr": 2, "zoom_hint": 1, "estimate_screen": {"x": 310, "y": 165, "w": 1280, "h": 800}}


def test_estimate_formula():
    assert estimate_from_report(REPORT) == {"x": 310.0, "y": 165.0, "w": 1280.0, "h": 800.0}
    z = dict(REPORT, zoom_hint=1.25)
    e = estimate_from_report(z)
    assert e["w"] == 1600 and e["x"] == 10 + 300 * 1.25


async def test_report_aligns_and_calibration_persisted(config_dir):
    ctl = MockController()
    events = []
    bus = Bus()
    bus.subscribe(lambda k, p: events.append((k, p)))
    s = SlotManager(ctl, bus, config_dir)
    await s.on_report(REPORT)
    st = s.status()
    assert st["slot"] == {"x": 310.0, "y": 165.0, "w": 1280.0, "h": 800.0}
    assert st["aligned"] and not st["calibrated"]
    assert any(k == "overlay" for k, _ in events)

    ctl.calibrate_slot = lambda markers=None: type("R", (), {"x": 312.0, "y": 170.0, "w": 1280.0, "h": 800.0})()
    st = await s.calibrate()
    assert st["calibrated"] and st["slot"]["y"] == 170.0 and st["aligned"]
    saved = json.loads((config_dir / "slot.json").read_text())
    assert saved["rect"]["x"] == 312.0

    # a new SlotManager reloads it; browser unchanged -> still calibrated
    s2 = SlotManager(ctl, bus, config_dir)
    await s2.on_report(REPORT)
    assert s2.status()["calibrated"]
    # browser moved -> calibration dropped, estimate used again
    moved = json.loads(json.dumps(REPORT))
    moved["window"]["screen_x"] = 50
    await s2.on_report(moved)
    st = s2.status()
    assert not st["calibrated"] and st["slot"]["x"] == 350.0 and st["aligned"]


def test_rects_close():
    a = {"x": 0, "y": 0, "w": 10, "h": 10}
    assert rects_close(a, {"x": 1, "y": 0, "w": 9, "h": 10.5})
    assert not rects_close(a, {"x": 2, "y": 0, "w": 10, "h": 10})

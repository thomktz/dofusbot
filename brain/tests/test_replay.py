"""Replay source: captured frames as a fake live screen for the real agent loop (dry run)."""
import asyncio

import pytest

from conftest import FIX
from dofus_brain.replay import ReplayController, ReplayFrame, dry_run, frame_time, load_frames

SEQ = FIX / "fight_seq"


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def test_frame_time():
    assert frame_time("215228_045.png") == pytest.approx(21 * 3600 + 52 * 60 + 28.045)
    assert frame_time("215228.png") == 21 * 3600 + 52 * 60 + 28
    assert frame_time("03_placement.jpg") is None


def test_load_frames_uses_capture_times_from_labels():
    frames = load_frames(SEQ)
    assert [f.path.name for f in frames][:3] == ["01_map_hover_mob.jpg", "02_loading.jpg", "03_placement_challenges.jpg"]
    assert all(a.t < b.t for a, b in zip(frames, frames[1:]))
    assert frames[2].label.startswith("character placement")


def test_controller_follows_the_clock_and_never_sends(tmp_path):
    clock = FakeClock()
    fr = [ReplayFrame(tmp_path / "a.png", 100.0, "a"), ReplayFrame(tmp_path / "b.png", 102.0, "b")]
    ctl = ReplayController(fr, clock=clock, grid_cfg={}, regions_cfg={})
    assert ctl.current().label == "a"
    clock.advance(1.9)
    assert ctl.current().label == "a"
    clock.advance(0.2)
    assert ctl.current().label == "b" and not ctl.finished
    ctl.execute({"kind": "click", "x": 1, "y": 2})
    assert ctl.executed == [{"t": 2.1, "frame": "b.png", "label": "b", "action": {"kind": "click", "x": 1, "y": 2}}]
    clock.advance(6.5)
    assert ctl.finished
    # speed 2: same frames in half the time
    clock2 = FakeClock()
    ctl2 = ReplayController(fr, speed=2.0, clock=clock2, grid_cfg={}, regions_cfg={})
    ctl2.current()
    clock2.advance(1.05)
    assert ctl2.current().label == "b"


def test_dry_run_over_thomas_fight(monkeypatch):
    """The real perceiver + planner + agent loop on the labelled fight, as if live."""
    from dofus_brain.ocr.backends import VisionOCR
    try:
        ocr = VisionOCR(["fr-FR", "en-US"])
    except Exception as e:
        pytest.skip(f"Apple Vision OCR unavailable: {e}")
    import dofus_brain.agent as agent_mod

    clock = FakeClock()
    monkeypatch.setattr(agent_mod.time, "monotonic", clock)     # action timeouts on replay time
    lines = []
    acts, _ = asyncio.run(dry_run(SEQ, ocr=ocr, clock=clock, tick_s=0.5, log=lines.append, max_failures=99))
    by_frame = {}
    for a in acts:
        by_frame.setdefault(a["frame"], []).append(a["action"])

    def clicks_near(frame, x, y, dx=25, dy=15):
        return any(a["kind"] == "click" and abs(a["x"] - x) < dx and abs(a["y"] - y) < dy for a in by_frame.get(frame, []))

    assert not by_frame.get("01_map_hover_mob.jpg") and not by_frame.get("02_loading.jpg")
    assert clicks_near("03_placement_challenges.jpg", 920, 754)       # Choisir les challenges
    assert clicks_near("04_placement_ready.jpg", 920, 754)            # PRÊT
    fight = [a for f in ("05_my_turn_pm.jpg", "06_arrived_spell_selected.jpg", "07_spell_landed.jpg")
             for a in by_frame.get(f, [])]
    assert fight and all(a["kind"] in ("click", "key") for a in fight)
    assert not by_frame.get("08_monsters_turn.jpg")                   # monsters' turn: nothing
    assert clicks_near("09_level_up_fermer.jpg", 640, 435)
    assert clicks_near("10_fight_result_fermer.jpg", 640, 657)
    # never anywhere near the challenge cards / Valider (top-left panel)
    assert not any(a["kind"] == "click" and a["x"] < 230 and a["y"] < 270 for a in (x["action"] for x in acts))
    assert any("placement" in ln for ln in lines)


def test_dry_run_ends_the_turn_then_waits(monkeypatch, tmp_path):
    """Our turn lasts longer than the bot needs (frame 07 stays 20 s): once nothing useful
    lands any more it clicks FIN DE TOUR (not more repositioning), then on the grey
    FIN DE TOUR frame it does nothing."""
    import json
    import shutil

    from dofus_brain.ocr.backends import VisionOCR
    try:
        ocr = VisionOCR(["fr-FR", "en-US"])
    except Exception as e:
        pytest.skip(f"Apple Vision OCR unavailable: {e}")
    import dofus_brain.agent as agent_mod

    keep = ["05_my_turn_pm.jpg", "06_arrived_spell_selected.jpg", "07_spell_landed.jpg", "08_monsters_turn.jpg"]
    labels = {e["file"]: e for e in json.loads((SEQ / "labels.json").read_text())}
    out = []
    for i, name in enumerate(keep):
        shutil.copy(SEQ / name, tmp_path / name)
        t = 10 * 60 + [0, 2, 4, 24][i]                      # 00:10:00, :02, :04, then :24
        out.append({**labels[name], "captured": f"0010{t - 600:02d}_000.png"})
    (tmp_path / "labels.json").write_text(json.dumps(out))

    clock = FakeClock()
    monkeypatch.setattr(agent_mod.time, "monotonic", clock)
    acts, _ = asyncio.run(dry_run(tmp_path, ocr=ocr, clock=clock, tick_s=0.5, log=lambda s: None))
    ends = [a for a in acts if "FIN DE TOUR" in a["action"].get("reason", "")]
    assert ends, [a["action"].get("reason") for a in acts]
    first_end = acts.index(ends[0])
    assert ends[0]["frame"] != "08_monsters_turn.jpg"
    assert abs(ends[0]["action"]["x"] - 920) < 25 and abs(ends[0]["action"]["y"] - 754) < 15
    # after ending the turn: no more moves on our frames, nothing at all on the monsters' turn
    assert not [a for a in acts[first_end + 1:] if a["frame"] != "08_monsters_turn.jpg"
                and a["action"].get("cell") is not None]
    assert not [a for a in acts if a["frame"] == "08_monsters_turn.jpg"]

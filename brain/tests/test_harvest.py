import numpy as np
import pytest
from PIL import Image, ImageDraw

from dofus_brain.game.grid import Grid
from dofus_brain.game.state import Character, GameState, MapInfo
from dofus_brain.mock_render import Renderer
from dofus_brain.perception.highlight import highlight_blobs, region_changed
from dofus_brain.planner import PlanContext, StepResult
from dofus_brain.skills.config import load_phases_config
from dofus_brain.skills.harvest import HarvestSkill

OBJECTS = [(300, 200, 40, 50), (900, 450, 60, 40), (640, 300, 30, 30)]  # x, y, w, h window points


def make_pair(meta, scale=1.0):
    """Synthetic fixture pair: plain world frame + the same frame with Y-style outlines."""
    r = Renderer(meta["grid"], meta["regions"], scale=scale)
    plain = Image.fromarray(r.world((4, -18)))
    d = ImageDraw.Draw(plain)
    for x, y, w, h in OBJECTS:  # the "resources"
        d.ellipse(((x - w / 2) * scale, (y - h / 2) * scale, (x + w / 2) * scale, (y + h / 2) * scale), fill=(120, 80, 40))
    hl = plain.copy()
    d2 = ImageDraw.Draw(hl)
    for x, y, w, h in OBJECTS:
        d2.ellipse(((x - w / 2 - 3) * scale, (y - h / 2 - 3) * scale, (x + w / 2 + 3) * scale, (y + h / 2 + 3) * scale),
                   outline=(255, 255, 120), width=int(3 * scale))
    return np.asarray(plain), np.asarray(hl)


@pytest.mark.parametrize("scale", [1.0, 2.0])
def test_blobs_found_at_objects(meta, scale):
    plain, hl = make_pair(meta, scale)
    blobs = highlight_blobs(plain, hl, scale=scale)
    assert len(blobs) == len(OBJECTS)
    for x, y, w, h in OBJECTS:
        assert any(abs(b.x - x) < 6 and abs(b.y - y) < 6 for b in blobs), (x, y, blobs)


def test_exclude_box_and_noise(meta):
    plain, hl = make_pair(meta)
    hl = hl.copy()
    hl[750:790, 10:400] = 255           # HUD change (e.g. chat line)
    hl[100:102, 100:102] = 0            # tiny noise
    blobs = highlight_blobs(plain, hl, exclude=[(0, 720, 1280, 800)])
    assert len(blobs) == len(OBJECTS)
    with pytest.raises(ValueError):
        highlight_blobs(plain, hl[:10])


def test_region_changed(meta):
    plain, hl = make_pair(meta)
    b = highlight_blobs(plain, hl)[0]
    harvested = plain.copy()
    x, y = int(b.x), int(b.y)
    harvested[y - 20:y + 20, x - 20:x + 20] = (60, 95, 45)
    assert region_changed(plain, harvested, b)
    assert not region_changed(plain, plain, b)


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_skill_full_cycle_pods(meta):
    plain, hl = make_pair(meta)
    clock = Clock()
    sk = HarvestSkill(load_phases_config(override={"key_mode": "toggle"}), target_count=1, clock=clock,
                      origin=lambda st: (880, 440))
    ctx = PlanContext(None, "auto", Grid(), {})
    st = GameState(screen="world", confidence=0.9, map=MapInfo((4, -18)), character=Character(pods=10))
    assert sk.step(st, ctx).status == "wait"          # no frame yet
    sk.feed_frame(plain)
    s = sk.step(st, ctx)
    assert s.action["key"] == "y" and not s.verify(st, st)
    sk.feed_frame(hl)
    assert s.verify(st, st)
    sk.on_result(StepResult(s, True, "verified"), st)
    s2 = sk.step(st, ctx)
    assert s2.meta["op"] == "hide" and s2.action["key"] == "y"
    sk.feed_frame(plain)
    sk.on_result(StepResult(s2, True, "verified"), st)
    s3 = sk.step(st, ctx)
    assert s3.action["kind"] == "click" and abs(s3.action["x"] - 900) < 6 and abs(s3.action["y"] - 450) < 6
    assert not s3.verify(st, st)
    after = GameState(screen="world", character=Character(pods=11))
    assert s3.verify(st, after)
    sk.on_result(StepResult(s3, True, "verified"), after)
    assert sk.step(after, ctx).status == "done"


def test_skill_confirms_by_sprite_change_and_skips_failed(meta):
    plain, hl = make_pair(meta)
    clock = Clock()
    sk = HarvestSkill(load_phases_config(), target_count=2, clock=clock,
                      count_fn=lambda st: None, origin=lambda st: (300, 200))
    ctx = PlanContext(None, "auto", Grid(), {})
    st = GameState(screen="world")
    sk.feed_frame(plain)
    scan = sk.step(st, ctx)                         # hold mode (default): ask for a held-key capture
    assert scan.status == "scan" and scan.data["key"] == "y"
    assert sk.step(st, ctx).status == "scan"        # still waiting for it
    sk.on_scan(hl)
    click = sk.step(st, ctx)
    assert click.action["kind"] == "click" and abs(click.action["x"] - 300) < 6
    harvested = plain.copy()
    harvested[180:220, 280:320] = (60, 95, 45)
    clock.t = 1.0
    sk.feed_frame(harvested)
    assert click.verify(st, st)
    sk.on_result(StepResult(click, False, "timeout"), st)   # pretend it failed: skip it next time
    sk.feed_frame(plain)
    assert sk.step(st, ctx).status == "scan"
    sk.on_scan(hl)
    nxt = sk.step(st, ctx)
    assert not (abs(nxt.action["x"] - 300) < 6 and abs(nxt.action["y"] - 200) < 6)


def test_skill_no_highlight(meta):
    plain, _ = make_pair(meta)
    sk = HarvestSkill(load_phases_config(override={"key_mode": "toggle"}), target_count=1)
    ctx = PlanContext(None, "auto", Grid(), {})
    st = GameState(screen="world")
    sk.feed_frame(plain)
    s = sk.step(st, ctx)
    sk.feed_frame(plain)
    sk.on_result(StepResult(s, True, "verified"), st)
    h = sk.step(st, ctx)
    sk.on_result(StepResult(h, True, "verified"), st)
    assert sk.step(st, ctx).status == "failed"


def test_hold_scan_ignores_resized_frame(meta):
    plain, hl = make_pair(meta)
    sk = HarvestSkill(load_phases_config(), target_count=1)
    st = GameState(screen="world")
    sk.feed_frame(plain)
    assert sk.step(st, PlanContext(None, "auto", Grid(), {})).status == "scan"
    sk.on_scan(hl[:400])
    assert sk.phase == "idle"

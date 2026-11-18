import numpy as np
import pytest
from PIL import Image, ImageDraw

from dofus_brain.game.grid import Grid
from dofus_brain.game.state import GameState, MapInfo
from dofus_brain.mock_render import Renderer
from dofus_brain.ocr import TemplateOCR, draw_tracked_text, synthetic_font
from dofus_brain.perception.entities import Plate
from dofus_brain.perception.npc_menu import MenuOption, NpcMenuReader
from dofus_brain.planner import PlanContext, StepResult
from dofus_brain.skills.quest import TalkSkill

NPC_AT = (400.0, 330.0)   # click point (plate at 400,300 + 30 pt below)


def menu_frame(meta, entries=("Parler", "Acheter", "Vendre"), at=NPC_AT, scale=1.0):
    """Fixture: world frame with an NPC context menu opened next to the click point."""
    r = Renderer(meta["grid"], meta["regions"], scale=scale)
    im = Image.fromarray(r.world((4, -18)))
    d = ImageDraw.Draw(im)
    font = synthetic_font(int(18 * scale))
    x0, y0 = (at[0] + 10) * scale, (at[1] + 5) * scale
    d.rectangle((x0, y0, x0 + 170 * scale, y0 + (12 + 30 * len(entries)) * scale), fill=(35, 30, 28))
    for i, e in enumerate(entries):
        draw_tracked_text(d, (int(x0 + 10 * scale), int(y0 + (8 + 30 * i) * scale)), e, font, (240, 240, 240))
    return np.asarray(im)


@pytest.fixture(scope="module")
def reader():
    return NpcMenuReader(TemplateOCR.from_font())


@pytest.mark.parametrize("scale", [1.0, 2.0])
def test_reader_finds_entries_with_positions(meta, reader, scale):
    if scale != 1.0:  # template OCR glyphs must match the drawn font size
        reader = NpcMenuReader(TemplateOCR.from_font(sizes=(int(18 * scale),)))
    opts = reader.read(menu_frame(meta, scale=scale), scale, NPC_AT)
    texts = [o.text for o in opts]
    assert len(texts) == 3 and texts[0] == "Parler", texts
    if scale == 1.0:
        assert texts == ["Parler", "Acheter", "Vendre"]
    talk = next(o for o in opts if o.is_talk)
    assert NPC_AT[0] + 10 < talk.x < NPC_AT[0] + 180 and NPC_AT[1] < talk.y < NPC_AT[1] + 45


def test_reader_no_menu(meta, reader):
    plain = Renderer(meta["grid"], meta["regions"]).world((4, -18))
    assert reader.read(plain, 1.0, NPC_AT) == []


@pytest.mark.parametrize("text,talk", [("Parler", True), ("Talk", True), ("Discuter avec", True),
                                       ("Acheter", False), ("Vendre", False)])
def test_is_talk(text, talk):
    assert MenuOption(text, 0, 0).is_talk is talk


def ctx():
    return PlanContext(None, "auto", Grid(), {})


def test_talk_skill_menu_flow(meta, reader):
    sk = TalkSkill("Ganymède", menu_reader=reader)
    sk.set_plates([Plate("Ganymede", x=400, y=300)])
    w = GameState(screen="world", map=MapInfo((4, -18)))
    s1 = sk.step(w, ctx())
    assert (s1.action["x"], s1.action["y"]) == NPC_AT and s1.meta["op"] == "npc"
    assert not s1.verify(w, w)                        # no menu yet
    sk.feed_frame(menu_frame(meta), 1.0)
    assert s1.verify(w, w)                            # menu seen
    sk.on_result(StepResult(s1, True, "verified"), w)
    s2 = sk.step(GameState(screen="menu"), ctx())     # menu may be classified as menu/popup
    assert s2.meta["op"] == "menu" and "Parler" in s2.action["reason"]
    assert s2.verify(w, GameState(screen="dialog"))
    sk.on_result(StepResult(s2, True, "verified"), GameState(screen="dialog"))
    assert sk.step(GameState(screen="dialog"), ctx()).status == "done"


def test_talk_skill_menu_without_talk_asks(meta, reader):
    sk = TalkSkill("Ganymede", menu_reader=reader)
    sk.set_plates([Plate("Ganymede", x=400, y=300)])
    w = GameState(screen="world")
    s1 = sk.step(w, ctx())
    sk.feed_frame(menu_frame(meta, entries=("Acheter", "Vendre")), 1.0)
    sk.on_result(StepResult(s1, True, "verified"), w)
    out = sk.step(w, ctx())
    assert out.status == "ask" and "Acheter" in out.reason


def test_talk_skill_direct_dialog_counts(meta, reader):
    sk = TalkSkill("Ganymede", menu_reader=reader)
    sk.set_plates([Plate("Ganymede", x=400, y=300)])
    w = GameState(screen="world")
    s1 = sk.step(w, ctx())
    d = GameState(screen="dialog")
    assert s1.verify(w, d)
    sk.on_result(StepResult(s1, True, "verified"), d)
    assert sk.step(d, ctx()).status == "done"


def test_planner_talk_goal_through_menu(tmp_path, meta, reader):
    import asyncio

    from dofus_brain.planner.phases import PhasePlanner
    from dofus_brain.skills.config import load_phases_config
    from dofus_brain.skills.quest import LocationBook
    from dofus_brain.skills.travel import MapGraph

    p = PhasePlanner(load_phases_config(), graph=MapGraph(), book=LocationBook(), maps_path=tmp_path / "m.json",
                     menu_reader=reader)
    p.set_goal("talk Ganymede")
    w = GameState(screen="world", confidence=0.9, map=MapInfo((4, -18)))
    p.scanned_maps[(4, -18)] = p.clock()
    p.set_plates([Plate("Ganymede", x=400, y=300)], (4, -18))
    s1 = asyncio.run(p.propose(w, ctx()))
    assert s1.meta["op"] == "npc"
    menu = menu_frame(meta)
    p.feed_frame(menu, 1.0, w)
    p.on_result(StepResult(s1, True, "verified"), w)
    s2 = asyncio.run(p.propose(GameState(screen="popup", confidence=0.9), ctx()))   # not treated as a popup
    assert s2.meta["skill"] == "talk" and s2.meta["op"] == "menu"

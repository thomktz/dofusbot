import numpy as np
from PIL import Image, ImageDraw

from dofus_brain.game.grid import Grid
from dofus_brain.mock_render import Renderer
from dofus_brain.npcs import entities_from_ocr, format_entities, list_npcs
from dofus_brain.ocr import OcrLine, TemplateOCR, draw_tracked_text, synthetic_font
from dofus_brain.perception import Regions

PLATES = [("Ganymede", (400, 300)), ("Bouftou niv. 6", (800, 200)), ("Groupe de 3 monstres (niv. 24)", (300, 500))]


def frames(meta, scale=1.0):
    """Rendered fixture: plain world frame, and the same frame with Z-style name plates."""
    r = Renderer(meta["grid"], meta["regions"], scale=scale)
    plain = Image.fromarray(r.world((4, -18)))
    held = plain.copy()
    d = ImageDraw.Draw(held)
    font = synthetic_font(int(18 * scale))
    for text, (x, y) in PLATES:
        w = len(text) * 11
        d.rectangle(((x - w / 2 - 4) * scale, (y - 12) * scale, (x + w / 2 + 4) * scale, (y + 12) * scale), fill=(20, 20, 20))
        draw_tracked_text(d, (int((x - w / 2) * scale), int((y - 10) * scale)), text, font, (240, 240, 240))
    return np.asarray(plain), np.asarray(held)


def test_entities_from_rendered_plates(meta):
    plain, held = frames(meta)
    regions = Regions(meta["regions"])
    ocr = TemplateOCR.from_font()
    lines = ocr.recognize(regions.crop(held, "game_area"))
    ents = entities_from_ocr(lines, 1.0, regions.offset(held, "game_area"), plain, held, Grid())
    by_name = {e.name: e for e in ents}
    assert set(by_name) == {"Ganymede", "Bouftou", "Groupe de 3 monstres"}, [e.raw for e in ents]
    g = by_name["Ganymede"]
    assert g.kind == "npc" and g.level is None and abs(g.x - 400) < 30 and abs(g.y - 300) < 8
    assert by_name["Bouftou"].kind == "monster" and by_name["Bouftou"].level == 6
    grp = by_name["Groupe de 3 monstres"]
    assert grp.kind == "monster" and grp.group_size == 3 and grp.level == 24
    assert ents[0].kind == "npc"                      # NPCs listed first
    assert g.cell == Grid().cell_at(g.x, g.y + 30)
    assert "Ganymede" in format_entities(ents)


def test_unchanged_text_is_not_a_plate(meta):
    plain, held = frames(meta)
    # the area name / coords text exists in both frames: never reported
    lines = [OcrLine("Astrub niv. 5", (10, 6, 200, 30)), OcrLine("Ganymede", (360, 290, 440, 310))]
    ents = entities_from_ocr(lines, 1.0, (0, 0), plain, held)
    assert [e.name for e in ents] == ["Ganymede"] and ents[0].cell is None
    assert format_entities([]) == "no name plates found"


class FakeFrame:
    def __init__(self, image, scale=1.0):
        self.image, self.scale = image, scale


class FakeCtl:
    def __init__(self, plain, held):
        self.plain, self.held, self.calls = plain, held, []

    def capture(self):
        self.calls.append("capture")
        return FakeFrame(self.plain)

    def capture_holding(self, key, settle_ms):
        self.calls.append(("hold", key))
        return FakeFrame(self.held)


def test_list_npcs_sends_one_hold_only(meta):
    plain, held = frames(meta)
    ctl = FakeCtl(plain, held)
    ents, frame = list_npcs(ctl, TemplateOCR.from_font(), Regions(meta["regions"]))
    assert ctl.calls == ["capture", ("hold", "z")]
    assert {e.name for e in ents} >= {"Ganymede"} and frame.image is held


def test_multiline_monster_plates_group():
    """Real Dofus plate layout (from list_npcs on 9 Nov): level, XP, then members."""
    def ln(text, x, y):
        return OcrLine(text, (int(x - 40), int(y - 6), int(x + 40), int(y + 6)), 1.0)

    lines = [ln("Niveau 13", 725, 352), ln("304 ХP", 725, 369), ln("Bouftou Nuageux (6)", 724, 387),
             ln("Bouftou Nuageux (4)", 726, 398), ln("Boufton Orageux (3)", 725, 411),
             ln("Joseph Ahistos", 896, 416),
             ln("Niveau 10", 380, 403), ln("230 KP", 380, 420), ln("Bouftou Nuageux (6)", 380, 437),
             ln("Boufton Orageux (4)", 380, 450)]
    ents = entities_from_ocr(lines, 1.0)
    npcs = [e for e in ents if e.kind == "npc"]
    groups = sorted((e for e in ents if e.kind == "monster"), key=lambda e: e.level)
    assert [e.name for e in npcs] == ["Joseph Ahistos"] and npcs[0].x == 896
    assert [(g.level, g.xp, g.group_size) for g in groups] == [(10, 230, 2), (13, 304, 3)]
    assert groups[1].members == [{"name": "Bouftou Nuageux", "level": 6}, {"name": "Bouftou Nuageux", "level": 4},
                                 {"name": "Boufton Orageux", "level": 3}]
    assert abs(groups[1].y - 352) < 1    # top of the box

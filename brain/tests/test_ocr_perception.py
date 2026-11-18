import numpy as np
import pytest

from dofus_brain.ocr import MockOCR, OcrLine, TemplateOCR, crop, make_ocr, upscale
from dofus_brain.perception import (DialogReader, FightReader, HudReader, Perceiver, Regions, ScreenClassifier,
                                    parse_coords, parse_fraction, parse_int, parse_options)
from dofus_brain.game.grid import Grid


@pytest.fixture(scope="module")
def tocr():
    return TemplateOCR.from_font()


@pytest.mark.parametrize("text,expected", [
    ("4,-18", (4, -18)), ("4, -18", (4, -18)), ("-12,34", (-12, 34)), ("Astrub (4,-18)", (4, -18)),
    ("Cité d'Astrub\n5,-17", (5, -17)), ("4.-18", (4, -18)), ("-1O,5", (-10, 5)), ("O,-l8", (0, -18)),
    ("niveau 200", None), ("", None),
])
def test_parse_coords(text, expected):
    assert parse_coords(text) == expected


def test_parse_numbers():
    assert parse_int("PA 6") == 6 and parse_int("") is None
    assert parse_fraction("1 250 / 2 000".replace(" ", "")) == (1250, 2000)
    assert parse_fraction("PV 120/250") == (120, 250)
    opts = parse_options(["1. Oui", "2) Non merci", "texte", "3 - Au revoir"])
    assert [(o.index, o.text) for o in opts] == [(1, "Oui"), (2, "Non merci"), (3, "Au revoir")]


def test_hud_reader_with_mocked_ocr(meta, load_fixture):
    """Coords come from OCR of the fixed map_coords crop + regex; OCR is mocked here."""
    regions = Regions(meta["regions"])
    seen = []

    def responder(img):
        seen.append(img.shape)
        return [OcrLine("Astrub", (0, 0, 10, 10)), OcrLine("-3 , 12", (0, 10, 10, 20))]

    hud = HudReader(MockOCR(responder), regions)
    img = load_fixture("world_4_-18.png")
    assert hud.coords(img) == (-3, 12)
    x0, y0, x1, y1 = regions["map_coords"].box(img.shape[1], img.shape[0])
    assert seen[0][:2] == (y1 - y0, x1 - x0)  # the OCR only saw the configured crop


def test_regions_survive_resize(meta, load_fixture):
    regions = Regions(meta["regions"])
    img = load_fixture("world_4_-18.png")
    big = upscale(img, 2.0)  # e.g. Retina capture of the same window
    a = regions.crop(img, "map_coords")
    b = regions.crop(big, "map_coords")
    assert abs(b.shape[0] - 2 * a.shape[0]) <= 2 and abs(b.shape[1] - 2 * a.shape[1]) <= 2


def test_template_ocr_reads_fixture_hud(meta, load_fixture, tocr):
    hud = HudReader(tocr, Regions(meta["regions"]))
    for name in ("world_4_-18.png", "world_5_-18.png"):
        img = load_fixture(name)
        assert hud.coords(img) == tuple(meta["world"][name])
        assert hud.pa(img) == 6 and hud.pm(img) == 3 and hud.hp(img) == (250, 250)
        assert hud.area(img) == "Astrub"


def test_make_ocr_fallback_on_linux():
    o = make_ocr({"backend": "auto"}, mock=True)
    assert o.name == "template"
    with pytest.raises(RuntimeError):
        import platform

        if platform.system() == "Darwin":
            pytest.skip("mac")
        make_ocr({"backend": "vision"})


def test_crop_helper():
    img = np.zeros((100, 200, 3), np.uint8)
    assert crop(img, (0.5, 0.5, 0.5, 0.5)).shape == (50, 100, 3)
    assert crop(img, (10, 10, 30, 20)).shape == (20, 30, 3)


@pytest.mark.parametrize("name", ["world_4_-18.png", "world_5_-18.png", "fight.png", "dialog.png", "loading.png", "popup.png"])
def test_classifier_picks_right_screen(meta, load_fixture, name):
    c = ScreenClassifier(Regions(meta["regions"])).classify(load_fixture(name))
    assert c.screen == meta["screens"][name], c.scores
    assert c.confidence >= 0.8


def test_classifier_unknown_on_noise(meta):
    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, (800, 1280, 3), dtype=np.uint8)
    c = ScreenClassifier(Regions(meta["regions"])).classify(img)
    assert c.screen == "unknown" or c.confidence < 0.55


def test_fight_reader_finds_fighters(meta, load_fixture):
    grid = Grid.from_config(meta["grid"])
    fs = FightReader(Regions(meta["regions"])).read(load_fixture("fight.png"), grid, scale=meta["scale"])
    truth = meta["fight"]
    me = fs.me()
    assert me is not None and me.cell == next(f["cell"] for f in truth["fighters"] if f["is_self"])
    enemies = sorted(f.cell for f in fs.enemies())
    assert enemies == sorted(f["cell"] for f in truth["fighters"] if f["team"] == "enemy")
    assert sorted(fs.blocked_cells) == sorted(truth["blocked"])
    assert sorted(fs.reachable_cells) == sorted(truth["highlight"])
    assert fs.phase == "my_turn"


def test_fight_reader_on_retina_capture(meta, load_fixture):
    grid = Grid.from_config(meta["grid"])
    img = upscale(load_fixture("fight.png"), 2.0)
    fs = FightReader(Regions(meta["regions"])).read(img, grid, scale=2.0)
    assert fs.me().cell == 241 and sorted(f.cell for f in fs.enemies()) == [245, 301]


def test_dialog_reader(meta, load_fixture, tocr):
    d = DialogReader(tocr, Regions(meta["regions"])).read(load_fixture("dialog.png"))
    truth = meta["dialog"]
    assert d.npc == truth["npc"]
    assert d.text == truth["text"]
    assert [o.index for o in d.options] == [1, 2, 3]
    assert [o.text.lower() for o in d.options] == [t.lower() for t in truth["options"]]
    assert set(d.option_points) == {1, 2, 3}


def test_perceiver_end_to_end(meta, load_fixture, tocr):
    p = Perceiver(meta["grid"], meta["regions"], tocr)
    w = p.perceive(load_fixture("world_4_-18.png")).state
    assert w.screen == "world" and w.map.coords == (4, -18) and w.character.pa == 6
    f = p.perceive(load_fixture("fight.png"), prev=w).state
    assert f.screen == "fight" and f.fight is not None and f.fight.me().cell == 241
    assert f.map.coords == (4, -18)  # carried over from the previous state
    d = p.perceive(load_fixture("dialog.png")).state
    assert d.dialog is not None and len(d.dialog.options) == 3
    ld = p.perceive(load_fixture("loading.png"))
    assert ld.state.screen == "loading" and not ld.needs_vlm

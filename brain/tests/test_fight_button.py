"""Fight main button (PRÊT / CHOISIR LES CHALLENGES / FIN DE TOUR) and the placement rule
"ignore challenges, just PRÊT". Real-client fixtures are Thomas's 1280x800 combat shots:
real_placement_challenges (shot 01), real_placement_ready (shot 02, the challenge cards
Bonus 90% / Bonus 65% + Valider are on screen), real_fight_turn (shot 03)."""
import numpy as np
import pytest

from dofus_brain.game.grid import Grid
from dofus_brain.game.state import FightState, GameState
from dofus_brain.ocr.backends import MockOCR, OcrLine
from dofus_brain.perception import Regions
from dofus_brain.perception.fight_button import DEFAULT_RECT, FightButton, FightButtonReader, classify_label
from dofus_brain.planner import PlanContext, PlannedStep, StepResult
from dofus_brain.skills import Outcome
from dofus_brain.skills.config import load_phases_config
from dofus_brain.skills.placement import PlacementSkill

# where things are on the real 1280x800 client (shots 01/02)
PRET_XY = (920, 754)
BONUS_CARDS = [(86, 186), (170, 186)]        # Bonus 90% / Bonus 65%
VALIDER_XY = (128, 241)


@pytest.mark.parametrize("text,kind", [
    ("PRÊT", "ready"), ("Prêt", "ready"), ("PRET", "ready"),
    ("CHOISIR LES\nCHALLENGES", "challenges"), ("CHOISIR LES CHALLENGES", "challenges"),
    ("FIN DE TOUR", "end_turn"), ("", None), ("VALIDER", None), ("Bonus 90%", None),
])
def test_classify_label(text, kind):
    assert classify_label(text) == kind


def test_reader_maps_label_box_back_to_window_points():
    img = np.zeros((800, 1280, 3), np.uint8)
    seen = []

    def ocr(crop):
        seen.append(crop.shape)
        return [OcrLine("PRÊT", (125, 33, 196, 60))]   # box in the 2x upscaled crop

    b = FightButtonReader(MockOCR(ocr)).read(img)
    assert b is not None and b.kind == "ready"
    x0, y0 = round(DEFAULT_RECT[0] * 1280), round(DEFAULT_RECT[1] * 800)
    assert b.x == pytest.approx(x0 + (125 + 196) / 4) and b.y == pytest.approx(y0 + (33 + 60) / 4)
    assert seen and seen[0][0] > 50   # upscaled crop
    # retina capture: same pixels at scale 2 -> half the points
    assert FightButtonReader(MockOCR(ocr)).read(np.zeros((1600, 2560, 3), np.uint8), 2.0).kind == "ready"


def test_reader_ignores_other_text():
    assert FightButtonReader(MockOCR("VALIDER")).read(np.zeros((800, 1280, 3), np.uint8)) is None


# ------------------------------------------------------------------ skill
def state_with(button):
    return GameState(screen="fight", confidence=0.9, fight=FightState(phase="placement", button=button))


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def ctx():
    return PlanContext(goal=None, mode="auto", grid=Grid(), config={})


def not_a_challenge_click(step):
    a = step.action
    if a["kind"] != "click":
        return True
    pts = BONUS_CARDS + [VALIDER_XY]
    return all(abs(a["x"] - x) > 40 or abs(a["y"] - y) > 25 for x, y in pts)


def test_ready_button_is_clicked():
    sk = PlacementSkill(load_phases_config(), "ranged", clock=Clock())
    s = sk.step(state_with(FightButton("ready", "PRÊT", *PRET_XY)), ctx())
    assert isinstance(s, PlannedStep) and s.meta["op"] == "ready" and s.meta["via"] == "click"
    assert (s.action["kind"], s.action["x"], s.action["y"]) == ("click", *PRET_XY)
    assert not_a_challenge_click(s)
    started = GameState(screen="fight", fight=FightState(phase="my_turn"))
    assert s.verify(state_with(None), started)


CHALLENGES_XY = (920, 754)


def test_challenge_button_is_clicked_then_ready():
    """Thomas: click 'Choisir les challenges', then PRÊT. Never the cards or Valider."""
    clock = Clock()
    sk = PlacementSkill(load_phases_config(), "ranged", clock=clock)
    ch = state_with(FightButton("challenges", "CHOISIR LES CHALLENGES", *CHALLENGES_XY))
    ready = state_with(FightButton("ready", "PRÊT", *PRET_XY))
    s1 = sk.step(ch, ctx())
    assert isinstance(s1, PlannedStep) and s1.meta["op"] == "challenges"
    assert (s1.action["kind"], s1.action["x"], s1.action["y"]) == ("click", *CHALLENGES_XY)
    assert not_a_challenge_click(s1)
    assert s1.verify(ch, ready) and not s1.verify(ch, ch)
    sk.on_result(StepResult(s1, True, "verified"), ready)
    clock.t = 0.5
    s2 = sk.step(ready, ctx())
    assert s2.meta["op"] == "ready" and s2.action["kind"] == "click" and (s2.action["x"], s2.action["y"]) == PRET_XY


def test_challenge_button_clicked_twice_then_ready_key():
    clock = Clock()
    sk = PlacementSkill(load_phases_config(), "ranged", clock=clock)
    ch = state_with(FightButton("challenges", "CHOISIR LES CHALLENGES", *CHALLENGES_XY))
    s = sk.step(ch, ctx())
    sk.on_result(StepResult(s, False, "timeout"), ch)
    clock.t = 1.0
    assert sk.step(ch, ctx()).status == "wait"
    clock.t = 3.5
    s = sk.step(ch, ctx())
    assert s.meta["op"] == "challenges"
    sk.on_result(StepResult(s, False, "timeout"), ch)
    clock.t = 7.0
    s = sk.step(ch, ctx())
    assert s.action["kind"] == "key" and s.action["key"] == "f1" and s.meta["via"] == "key"


def test_ready_is_pressed_again_once_if_still_showing():
    clock = Clock()
    sk = PlacementSkill(load_phases_config(), "ranged", clock=clock)
    st = state_with(FightButton("ready", "PRÊT", *PRET_XY))
    s = sk.step(st, ctx())
    sk.on_result(StepResult(s, False, "timeout"), st)
    clock.t = 1.0
    assert sk.step(st, ctx()).status == "wait"
    clock.t = 3.5
    assert sk.step(st, ctx()).action["kind"] == "click"
    assert sk.step(st, ctx()).status == "wait"   # only once


# ------------------------------------------------------------------ real client shots (Apple Vision)
@pytest.fixture(scope="module")
def vision():
    from dofus_brain.ocr.backends import VisionOCR
    try:
        return VisionOCR(["fr-FR", "en-US"])
    except Exception as e:
        pytest.skip(f"Apple Vision OCR unavailable: {e}")


@pytest.fixture(scope="module")
def perceiver(vision):
    from dofus_brain.config import load_json
    from dofus_brain.perception.perceiver import Perceiver
    return Perceiver(load_json("grid"), load_json("regions"), vision)


@pytest.mark.parametrize("name,kind", [
    ("real_placement_challenges.jpg", "challenges"),
    ("real_placement_ready.jpg", "ready"),
    ("real_fight_turn.jpg", "end_turn"),
])
def test_real_shots_button(vision, load_fixture, name, kind):
    b = FightButtonReader(vision, Regions({})).read(load_fixture(name))
    assert b is not None and b.kind == kind
    assert abs(b.x - PRET_XY[0]) < 25 and abs(b.y - PRET_XY[1]) < 15


def test_real_shots_are_fight_placement(perceiver, load_fixture):
    for name in ("real_placement_challenges.jpg", "real_placement_ready.jpg"):
        st = perceiver.perceive(load_fixture(name)).state
        assert st.screen == "fight" and st.fight.phase == "placement", name
    st = perceiver.perceive(load_fixture("real_fight_turn.jpg")).state
    assert st.screen == "fight" and st.fight.phase != "placement"


def test_real_shots_placement_flow(perceiver, load_fixture):
    """Shot 01 -> click 'Choisir les challenges'; shot 02 -> click PRÊT. Never the challenge
    cards or Valider."""
    clock = Clock()
    sk = PlacementSkill(load_phases_config(), "ranged", clock=clock)
    st1 = perceiver.perceive(load_fixture("real_placement_challenges.jpg")).state
    s1 = sk.step(st1, ctx())
    assert isinstance(s1, PlannedStep) and s1.meta["op"] == "challenges" and s1.action["kind"] == "click"
    assert abs(s1.action["x"] - PRET_XY[0]) < 25 and abs(s1.action["y"] - PRET_XY[1]) < 15
    assert not_a_challenge_click(s1)
    st2 = perceiver.perceive(load_fixture("real_placement_ready.jpg")).state
    assert s1.verify(st1, st2)
    sk.on_result(StepResult(s1, True, "verified"), st2)
    clock.t = 1.0
    s = sk.step(st2, ctx())
    assert isinstance(s, PlannedStep) and s.action["kind"] == "click"
    assert abs(s.action["x"] - PRET_XY[0]) < 25 and abs(s.action["y"] - PRET_XY[1]) < 15
    assert not_a_challenge_click(s)

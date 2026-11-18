"""The whole fight as Thomas played it (stream of 9 Nov 21:52-21:53, frames he picked and
labelled, brain/fixtures/fight_seq/labels.json), and what the bot reads and does at each step:

  state                              signal the bot reads                    bot action
  map, hovering a mob                no fight button, no FERMER              (not a popup)
  black loading screen               dark game area                          wait
  placement, challenge step          button "CHOISIR LES CHALLENGES"         click it
  challenge choice                   button "PRÊT"                           click PRÊT
  our turn, 3 PM shown               "FIN DE TOUR" lime, self + 4 rings      select spell / move
  moved, spell selected              self on the new cell, 4 enemies         keep playing
  spell landed (-1 PA)               one enemy fewer (dead or hidden)        re-plan
  monsters' turn                     "FIN DE TOUR" grey                      wait
  level up window                    "NIVEAU 11" + FERMER                    click FERMER
  fight result window                "VICTOIRE" + FERMER                     click FERMER
"""
import asyncio
import json

import pytest

from dofus_brain.config import load_json
from dofus_brain.game.grid import Grid
from dofus_brain.planner import PlanContext, StepResult
from dofus_brain.planner.phases import PhasePlanner
from dofus_brain.skills.config import load_phases_config
from dofus_brain.skills.quest import LocationBook
from dofus_brain.skills.travel import MapGraph

from conftest import FIX

SEQ = FIX / "fight_seq"
BUTTON_XY = (920, 754)
# expected reading per frame: screen, fight phase, main button, self cell, enemy cells, FERMER (x, y)
EXPECT = {
    "01_map_hover_mob.jpg":           dict(screen_not="popup"),
    "02_loading.jpg":                 dict(screen="loading"),
    "03_placement_challenges.jpg":    dict(screen="fight", phase="placement", button="challenges"),
    "04_placement_ready.jpg":         dict(screen="fight", phase="placement", button="ready"),
    "05_my_turn_pm.jpg":              dict(screen="fight", phase="my_turn", button="end_turn", me=259,
                                           enemies=[316, 329, 330, 373]),
    "06_arrived_spell_selected.jpg":  dict(screen="fight", phase="my_turn", button="end_turn", me=286,
                                           enemies=[316, 329, 330, 373]),
    "07_spell_landed.jpg":            dict(screen="fight", phase="my_turn", button="end_turn", me=286),
    "08_monsters_turn.jpg":           dict(screen="fight", phase="enemy_turn", button="end_turn", me=271),
    "09_level_up_fermer.jpg":         dict(screen="popup", close="level_up", fermer=(640, 435)),
    "10_fight_result_fermer.jpg":     dict(screen="popup", close="fight_result", fermer=(640, 657)),
}


def frames():
    return [e["file"] for e in json.loads((SEQ / "labels.json").read_text())]


def test_labels_cover_the_fixtures():
    assert frames() == sorted(EXPECT)


@pytest.fixture(scope="module")
def perceiver():
    from dofus_brain.ocr.backends import VisionOCR
    from dofus_brain.perception.perceiver import Perceiver
    try:
        ocr = VisionOCR(["fr-FR", "en-US"])
    except Exception as e:
        pytest.skip(f"Apple Vision OCR unavailable: {e}")
    return Perceiver(load_json("grid"), load_json("regions"), ocr)


@pytest.fixture(scope="module")
def states(perceiver, load_fixture):
    return {f: perceiver.perceive(load_fixture(f"fight_seq/{f}")).state for f in frames()}


@pytest.mark.parametrize("name", sorted(EXPECT))
def test_each_state_is_read(states, name):
    st, want = states[name], EXPECT[name]
    if "screen_not" in want:
        assert st.screen != want["screen_not"]
    if "screen" in want:
        assert st.screen == want["screen"]
    if "phase" in want:
        assert st.fight is not None and st.fight.phase == want["phase"]
    if "button" in want:
        b = st.fight.button
        assert b is not None and b.kind == want["button"]
        assert abs(b.x - BUTTON_XY[0]) < 25 and abs(b.y - BUTTON_XY[1]) < 15
    if "me" in want:
        me = [f for f in st.fight.fighters if f.is_self]
        assert len(me) == 1 and me[0].cell == want["me"]      # never twice (red body on blue ring)
    if "enemies" in want:
        assert sorted(e.cell for e in st.fight.enemies()) == want["enemies"]
    if "close" in want:
        c = st.close_button
        assert c is not None and c.kind == want["close"]
        assert abs(c.x - want["fermer"][0]) < 15 and abs(c.y - want["fermer"][1]) < 10
    else:
        assert st.close_button is None


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_the_bot_plays_the_sequence(states, tmp_path):
    """Feed the frames in Thomas's order to the planner: the right action at each step,
    nothing ever clicked near the challenge cards / Valider."""
    clock = Clock()
    p = PhasePlanner(load_phases_config(), graph=MapGraph(), book=LocationBook(), clock=clock,
                     maps_path=tmp_path / "maps.json")
    ctx = PlanContext(goal=None, mode="auto", grid=Grid.from_config(load_json("grid")), config={})

    def propose(name):
        clock.t += 1.0
        return asyncio.run(p.propose(states[name], ctx))

    def near(step, xy, dx=25, dy=15):
        return step.action["kind"] == "click" and abs(step.action["x"] - xy[0]) < dx and abs(step.action["y"] - xy[1]) < dy

    assert propose("02_loading.jpg") is None                                   # wait
    s = propose("03_placement_challenges.jpg")
    assert s is not None and s.meta["op"] == "challenges" and near(s, BUTTON_XY)
    p.on_result(StepResult(s, True, "verified"), states["04_placement_ready.jpg"])
    s = propose("04_placement_ready.jpg")
    assert s is not None and s.meta["op"] == "ready" and near(s, BUTTON_XY)
    p.on_result(StepResult(s, True, "verified"), states["05_my_turn_pm.jpg"])
    s = propose("05_my_turn_pm.jpg")
    assert s is not None and s.meta["skill"] == "fight" and s.meta["op"] in ("select", "move")
    p.on_result(StepResult(s, True, "verified"), states["06_arrived_spell_selected.jpg"])
    s = propose("06_arrived_spell_selected.jpg")
    assert s is not None and s.meta["skill"] == "fight" and s.meta["op"] != "end_turn"
    assert propose("08_monsters_turn.jpg") is None                             # monsters play: wait
    s = propose("09_level_up_fermer.jpg")
    assert s is not None and s.meta["skill"] == "popup" and near(s, (640, 435), 15, 10)
    p.on_result(StepResult(s, True, "verified"), states["10_fight_result_fermer.jpg"])
    s = propose("10_fight_result_fermer.jpg")
    assert s is not None and s.meta["skill"] == "popup" and near(s, (640, 657), 15, 10)
    assert not p.safety.halted

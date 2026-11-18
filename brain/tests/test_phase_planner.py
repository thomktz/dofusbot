import asyncio

import numpy as np
import pytest

from dofus_brain.game.grid import Grid
from dofus_brain.game.state import Character, Dialog, DialogOption, Fighter, FightState, GameState, MapInfo, Quest
from dofus_brain.ocr import TemplateOCR
from dofus_brain.perception import Perceiver
from dofus_brain.perception.entities import Plate
from dofus_brain.planner import PLANNERS, PlanContext, StepResult
from dofus_brain.planner.phases import PhasePlanner
from dofus_brain.skills.config import load_phases_config
from dofus_brain.skills.quest import LocationBook
from dofus_brain.skills.travel import MapGraph


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def planner(tmp_path, clock=None, book=None, **kw):
    return PhasePlanner(load_phases_config(), graph=MapGraph(), book=book or LocationBook(),
                        clock=clock or Clock(), maps_path=tmp_path / "maps.json", **kw)


def ctx(grid=None, llm=None):
    return PlanContext(goal=None, mode="auto", grid=grid or Grid(), config={}, llm=llm)


BLANK = np.zeros((800, 1280, 3), np.uint8)


def propose(p, st, c=None, answer_scans=True):
    """Like the agent: answer a hold-key scan request with a captured frame, then re-plan."""
    s = asyncio.run(p.propose(st, c or ctx()))
    while s is None and answer_scans and p.scan_request is not None:
        p.on_scan(p.scan_request["key"], BLANK, 1.0, st)
        s = asyncio.run(p.propose(st, c or ctx()))
    return s


def world(coords, **kw):
    return GameState(screen="world", confidence=0.9, map=MapInfo(coords), **kw)


def test_registered():
    assert isinstance(PLANNERS["phases"](), PhasePlanner)


def test_goto_on_fixtures_persists_graph(tmp_path, meta, load_fixture):
    perc = Perceiver(meta["grid"], meta["regions"], TemplateOCR.from_font())
    a = perc.perceive(load_fixture("world_4_-18.png")).state
    b = perc.perceive(load_fixture("world_5_-18.png"), prev=a).state
    p = planner(tmp_path)
    p.set_goal("goto 5,-18")
    c = ctx(Grid.from_config(meta["grid"]))
    s = propose(p, a, c)
    assert s.meta["skill"] == "travel" and s.verify(a, b)
    p.on_result(StepResult(s, True, "verified"), b)
    assert (tmp_path / "maps.json").exists()
    assert propose(p, b, c) is None and p.goal is None      # arrived: goal done


def test_interrupts_win_over_goal(tmp_path):
    p = planner(tmp_path)
    p.set_goal("goto 9,9")
    p.set_popup_text("Niveau supérieur !")
    s = propose(p, GameState(screen="popup", confidence=0.9))
    assert s.meta["skill"] == "popup" and s.action["key"] == "escape"
    fs = FightState(phase="my_turn", turn=1, fighters=[Fighter("self", "self", "ally", True, 241, 50, 50, 6, 3),
                                                       Fighter("e", "Tofu", "enemy", False, 245, 10, 10)])
    f = GameState(screen="fight", confidence=0.9, character=Character(pa=6, pm=3), fight=fs)
    s = propose(p, f)
    assert s.meta["skill"] == "fight"


def test_aggro_detected_and_learned(tmp_path):
    p = planner(tmp_path)
    p.set_plates([Plate("Arakne", level=12, cell=200)], (1, 1))
    propose(p, world((1, 1)))
    propose(p, GameState(screen="fight", confidence=0.9, fight=FightState(phase="enemy_turn")))
    assert p.aggro.rules.is_aggressive(p.aggro.current((1, 1))[0])


def test_placement_when_view_says_so(tmp_path, meta):
    from dofus_brain.perception.placement import PlacementView

    p = planner(tmp_path)
    p.placement_view = PlacementView("red", our_cells=[100, 101], their_cells=[400], self_cell=100, enemies=[400])
    st = GameState(screen="fight", confidence=0.9,
                   fight=FightState(phase="enemy_turn", fighters=[Fighter("self", "self", "ally", True, 100)]))
    s = propose(p, st)
    assert s.meta["skill"] == "placement"


def test_dialog_ask_halts_until_new_goal(tmp_path):
    p = planner(tmp_path)
    d = Dialog("Bob", "?", [DialogOption(1, "Oui"), DialogOption(2, "Non")], {1: (1, 1), 2: (1, 2)})
    assert propose(p, GameState(screen="dialog", confidence=0.9, dialog=d)) is None
    assert p.attention and "dialog" in p.attention
    assert propose(p, world((0, 0))) is None and p.safety.halted
    p.set_goal("goto 1,0")
    assert propose(p, world((0, 0))).meta["skill"] == "travel"


def test_unknown_screen_stops(tmp_path):
    clock = Clock()
    p = planner(tmp_path, clock=clock)
    p.set_goal("goto 1,0")
    assert propose(p, GameState(screen="unknown", confidence=0.1)) is None
    clock.t = 10
    propose(p, GameState(screen="unknown", confidence=0.1))
    assert p.safety.halted
    assert propose(p, world((0, 0))) is None


def test_bad_goals_ask(tmp_path):
    p = planner(tmp_path)
    p.set_goal("dance")
    assert propose(p, world((0, 0))) is None and "unknown goal" in p.attention
    p.set_goal("quest")
    assert propose(p, world((0, 0))) is None and "quest" in p.attention
    p.set_goal("quest")
    propose(p, world((0, 0), quest=Quest("Q", None, "Parler à Personne")))
    assert "Where is NPC" in p.attention


def test_quest_goal_chains_travel_then_talk(tmp_path):
    book = LocationBook({"npcs": {"Ganymede": [1, 0]}})
    p = planner(tmp_path, book=book)
    p.set_goal("quest")
    st = world((0, 0), quest=Quest("Q", None, "Parler à Ganymède"))
    s = propose(p, st)
    assert s.meta["skill"] == "travel"
    there = world((1, 0), quest=st.quest)
    p.on_result(StepResult(s, True, "verified"), there)
    p.set_plates([Plate("Ganymede", x=400, y=300)], (1, 0))
    s2 = propose(p, there)
    assert s2.meta["skill"] == "talk"


def test_failures_halt_through_safety(tmp_path):
    p = planner(tmp_path)
    p.set_goal("goto 3,0")
    st = world((0, 0))
    for _ in range(3):
        s = propose(p, st)
        p.on_result(StepResult(s, False, "timeout"), st)
    assert p.safety.halted and propose(p, st) is None


def test_entity_scan_on_each_new_map(tmp_path):
    seen = []

    def reader(img, scale):
        seen.append(img.shape)
        return [Plate("Arakne", level=40, group_size=3, cell=200)]

    clock = Clock()
    p = planner(tmp_path, clock=clock, entity_reader=reader)
    p.set_goal("goto 3,0")
    st = world((0, 0), character=Character(level=5))
    assert propose(p, st, answer_scans=False) is None
    assert p.scan_request["key"] == "z" and p.scan_request["coords"] == (0, 0)
    assert propose(p, st, answer_scans=False) is None          # still waiting: no action meanwhile
    p.on_scan("z", BLANK, 1.0, st)
    assert seen and p.aggro.current((0, 0))[0].name == "Arakne"
    assert propose(p, st, answer_scans=False).meta["skill"] == "travel"
    assert propose(p, world((1, 0)), answer_scans=False) is None and p.scan_request["coords"] == (1, 0)
    clock.t += 61                                               # sighting is stale: rescan
    p.scan_request = None
    assert propose(p, st, answer_scans=False) is None and p.scan_request is not None


def test_harvest_goal_uses_hold_scan(tmp_path):
    p = planner(tmp_path)
    p.set_goal("harvest 1")
    st = world((0, 0))
    p.scanned_maps[(0, 0)] = 0.0
    assert propose(p, st, answer_scans=False) is None and p.goal_skills
    p.feed_frame(BLANK, 1.0, st)
    assert propose(p, st, answer_scans=False) is None
    assert p.scan_request == {"key": "y", "purpose": "harvest", "coords": (0, 0), "settle_ms": 250.0}
    hl = BLANK.copy()
    hl[300:340, 600:640] = 255
    p.on_scan("y", hl, 1.0, st)
    s = propose(p, st, answer_scans=False)
    assert s.action["kind"] == "click" and abs(s.action["x"] - 620) < 4


def test_actions_are_valid_for_control(tmp_path, meta, load_fixture):
    """Every action the skills emit must pass dofus_control's strict validation."""
    validate = pytest.importorskip("dofus_control.actions").validate_action
    from dofus_brain.skills.fight import Spell

    p = planner(tmp_path)
    acts = []
    p.set_goal("goto 2,0")
    acts.append(propose(p, world((0, 0))).action)
    p.set_popup_text("Niveau supérieur !")
    acts.append(propose(p, GameState(screen="popup", confidence=0.9)).action)
    fs = FightState(phase="my_turn", turn=1, fighters=[Fighter("self", "self", "ally", True, 241, 50, 50, 6, 3),
                                                       Fighter("e", "Tofu", "enemy", False, 245, 10, 10)])
    acts.append(propose(p, GameState(screen="fight", confidence=0.9, character=Character(pa=6, pm=3), fight=fs)).action)
    from dofus_brain.skills.aggro import EngageSkill, MonsterGroup
    p.aggro.update((0, 0), [MonsterGroup("Tofu", (0, 0), 300, 3, 1, 0.0)])
    acts.append(EngageSkill(p.aggro).step(world((0, 0), character=Character(level=5)), ctx()).action)
    from dofus_brain.skills.quest import TalkSkill
    t = TalkSkill("Bob")
    t.set_plates([Plate("Bob", x=10, y=10)])
    acts.append(t.step(world((0, 0)), ctx()).action)
    for a in acts:
        validate(a, "azerty")


@pytest.mark.parametrize("module", ["dofus_brain.skills.fight", "dofus_brain.skills.config", "dofus_brain.npcs",
                                    "dofus_brain.planner", "dofus_brain.skills", "dofus_brain.perception.npc_menu"])
def test_import_order_has_no_cycle(module):
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

"""Fight sequence on Thomas's real client (combat shots of 9 Nov, 1280x800 content):

    placement: click 'Choisir les challenges', then PRÊT   (test_fight_button.py)
 -> fight start: FIN DE TOUR shows, our turn read from its colour, HP/PM from the HUD
 -> our turn: solver plans within PA/PM, select spell (key) + click target, verified by the
    combat log, then click FIN DE TOUR
 -> enemy turn: wait; next turn: PA/PM back to the maximum
 -> unknown state at any point: stop.

Fixtures: real_fight_turn.jpg (shot 03: our turn, 3 PM shown in green, one Feu Follet next to
us behind a crate), real_fight_range.jpg (shot 04: Flèche Glacée range in blue, 4 Feux Follets;
the right strip is blacked out because another window covered it).
"""
import asyncio

import numpy as np
import pytest

from dofus_brain.config import load_json
from dofus_brain.game.grid import Grid
from dofus_brain.game.state import Fighter, FightState, GameState
from dofus_brain.perception.fight_button import FightButton
from dofus_brain.perception.fight_real import RealFightReader, casts_in_log
from dofus_brain.planner import PlanContext, PlannedStep, StepResult
from dofus_brain.planner.phases import PhasePlanner
from dofus_brain.skills import Outcome
from dofus_brain.skills.config import load_phases_config
from dofus_brain.skills.fight import FightTurnSkill
from dofus_brain.skills.quest import LocationBook
from dofus_brain.skills.travel import MapGraph

FIN_DE_TOUR = FightButton("end_turn", "FIN DE TOUR", 920.5, 754.0)
# measured on the shots: (cell, centre in window points)
SHOT03 = {"self": 218, "enemies": [246]}
SHOT04 = {"self": 231, "enemies": [313, 314, 315, 341]}


@pytest.fixture(scope="module")
def grid():
    return Grid.from_config(load_json("grid"))


def ctx(grid):
    return PlanContext(goal=None, mode="auto", grid=grid, config={})


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


# ------------------------------------------------------------------ grid + fighters (no OCR)
def test_grid_is_calibrated_on_the_range_cells(grid, load_fixture):
    """Cell centres land inside the blue spell-range cells of shot 04, measured by hand."""
    for x, y in [(519.0, 289.0), (588.0, 323.5), (553.5, 340.5), (725.5, 357.5), (863.0, 426.5)]:
        c = grid.cell_at(x, y)
        cx, cy = grid.center(c)
        assert abs(cx - x) < 2.5 and abs(cy - y) < 2.5, (x, y, c)


@pytest.mark.parametrize("name,want", [("real_fight_turn.jpg", SHOT03), ("real_fight_range.jpg", SHOT04)])
def test_fighters_from_rings(grid, load_fixture, name, want):
    fs = RealFightReader(None).fighters(load_fixture(name), grid)
    me = [f for f in fs if f.is_self]
    assert len(me) == 1 and me[0].cell == want["self"]
    assert sorted(f.cell for f in fs if f.team == "enemy") == want["enemies"]
    assert not [f for f in fs if f.team == "ally" and not f.is_self]


def test_our_turn_from_the_button_colour(load_fixture):
    r = RealFightReader(None)
    for name in ("real_fight_turn.jpg", "real_fight_range.jpg"):
        img = load_fixture(name)
        assert r.is_my_turn(img)
        grey = img.copy()
        region = grey[738:772, 860:982].astype(int)
        grey[738:772, 860:982] = region.mean(axis=-1, keepdims=True).astype(np.uint8)   # greyed out
        assert not r.is_my_turn(grey)


def test_casts_in_log():
    lines = ["[21:13] Chern-Simons lance Flèche de Recul.", "[21:13] Feu Vif : -21 PV (mort).",
             "[21:18] Feu de Joie lance Brûlure légère.", "[21:18] Chern-Simons lance Flèche Glacée."]
    assert casts_in_log(lines, "chern") == [("Chern-Simons", "Flèche de Recul"), ("Chern-Simons", "Flèche Glacée")]
    assert ("Feu de Joie", "Brûlure légère") in casts_in_log(lines)


# ------------------------------------------------------------------ the turn sequence
def turn_state(fighters, log=(), phase="my_turn", hp=108, pm=None):
    st = GameState(screen="fight", confidence=0.9,
                   fight=FightState(phase=phase, fighters=list(fighters), button=FIN_DE_TOUR, log=list(log)))
    st.character.hp, st.character.pa, st.character.pm = hp, None, pm
    return st


def shot03_fighters():
    return [Fighter("self", "self", "ally", True, SHOT03["self"]), Fighter("enemy1", "enemy1", "enemy", cell=246)]


def test_turn_cast_verified_by_log_then_end_turn_click(grid):
    clock = Clock()
    sk = FightTurnSkill(load_phases_config(), "cra", clock=clock)
    st = turn_state(shot03_fighters(), log=["[21:18] Chern-Simons lance Flèche Glacée."])
    sel = sk.step(st, ctx(grid))
    assert isinstance(sel, PlannedStep) and sel.meta["op"] == "select" and sel.action["kind"] == "key"
    spell = sel.meta["spell"]
    sk.on_result(StepResult(sel, True, "verified"), st)
    cast = sk.step(st, ctx(grid))
    assert cast.meta["op"] == "cast" and cast.action["kind"] == "click" and cast.meta["cell"] == 246
    assert (cast.action["x"], cast.action["y"]) == tuple(round(v, 1) for v in grid.center(246))
    # PA unreadable on the real HUD: the cast is verified by a new line in the combat log
    assert not cast.verify(st, st)
    after = turn_state(shot03_fighters(), log=st.fight.log + [f"[21:20] Chern-Simons lance {spell}."])
    assert cast.verify(st, after)
    pa0 = sk.pa_left
    sk.on_result(StepResult(cast, True, "verified"), after)
    assert sk.pa_left < pa0
    # play the rest of the turn with every action landing; it must end by clicking
    # FIN DE TOUR and never spend more PA/PM than the turn has
    cur, spent = after, {"pa": pa0 - sk.pa_left}
    end = None
    for _ in range(12):
        s = sk.step(cur, ctx(grid))
        assert isinstance(s, PlannedStep)
        op = s.meta["op"]
        if op == "end_turn":
            end = s
            break
        if op == "cast":
            nxt = turn_state(cur.fight.fighters, log=cur.fight.log + [f"Chern-Simons lance {s.meta['spell']}."])
        elif op == "move":
            fs = [Fighter("self", "self", "ally", True, s.meta["cell"])] + [f for f in cur.fight.fighters if not f.is_self]
            nxt = turn_state(fs, log=cur.fight.log)
        else:
            nxt = cur
        sk.on_result(StepResult(s, True, "verified"), nxt)
        cur = nxt
    assert end is not None and end.action["kind"] == "click"
    assert (end.action["x"], end.action["y"]) == (FIN_DE_TOUR.x, FIN_DE_TOUR.y)
    assert sk.pa_left >= 0 and sk.pm_left >= 0
    enemy = turn_state(shot03_fighters(), phase="enemy_turn")
    assert end.verify(cur, enemy)
    sk.on_result(StepResult(end, True, "verified"), enemy)
    # monsters' turn: wait; our next turn: PA/PM back to full
    assert sk.step(enemy, ctx(grid)).status == "wait"
    nxt = sk.step(turn_state(shot03_fighters()), ctx(grid))
    assert isinstance(nxt, PlannedStep)
    assert (sk.pa_left, sk.pm_left) == (sk.max_pa, sk.max_pm)


def test_no_self_on_the_grid_stops(grid):
    sk = FightTurnSkill(load_phases_config(), "cra")
    out = sk.step(turn_state([Fighter("enemy1", "enemy1", "enemy", cell=246)]), ctx(grid))
    assert isinstance(out, Outcome) and out.status == "ask"


def test_unknown_frame_mid_fight_stops_the_planner(grid, tmp_path):
    clock = Clock()
    p = PhasePlanner(load_phases_config(), graph=MapGraph(), book=LocationBook(), clock=clock,
                     maps_path=tmp_path / "maps.json")
    st = turn_state(shot03_fighters())
    assert asyncio.run(p.propose(st, ctx(grid))) is not None
    unknown = GameState(screen="unknown", confidence=0.1)
    assert asyncio.run(p.propose(unknown, ctx(grid))) is None
    clock.t = 10.0
    assert asyncio.run(p.propose(unknown, ctx(grid))) is None
    assert p.safety.halted
    assert asyncio.run(p.propose(st, ctx(grid))) is None   # stays stopped until a human resets it


# ------------------------------------------------------------------ full perception (Apple Vision)
@pytest.fixture(scope="module")
def perceiver():
    from dofus_brain.ocr.backends import VisionOCR
    from dofus_brain.perception.perceiver import Perceiver
    try:
        ocr = VisionOCR(["fr-FR", "en-US"])
    except Exception as e:
        pytest.skip(f"Apple Vision OCR unavailable: {e}")
    return Perceiver(load_json("grid"), load_json("regions"), ocr)


@pytest.mark.parametrize("name,want", [("real_fight_turn.jpg", SHOT03), ("real_fight_range.jpg", SHOT04)])
def test_real_shot_perceived_as_our_turn(perceiver, load_fixture, name, want):
    st = perceiver.perceive(load_fixture(name)).state
    assert st.screen == "fight" and st.fight.phase == "my_turn"
    assert st.fight.button is not None and st.fight.button.kind == "end_turn"
    assert st.fight.me() is not None and st.fight.me().cell == want["self"]
    assert sorted(e.cell for e in st.fight.enemies()) == want["enemies"]
    assert st.character.hp == 108 and st.character.pm == 3
    assert st.character.pa in (None, 6)          # the star digit: rejected rather than misread


def test_real_shot_03_first_action(perceiver, load_fixture, grid):
    st = perceiver.perceive(load_fixture("real_fight_turn.jpg")).state
    assert any("lance" in ln for ln in st.fight.log)          # combat log is read on our turn
    sk = FightTurnSkill(load_phases_config(), "cra")
    s = sk.step(st, ctx(grid))
    assert isinstance(s, PlannedStep) and s.meta["op"] in ("select", "move")
    if s.meta["op"] == "select":
        keys = load_phases_config()["keys"]["spells"]
        assert s.action["key"] in keys
        sk.on_result(StepResult(s, True, "verified"), st)
        c = sk.step(st, ctx(grid))
        assert c.meta["op"] == "cast" and c.meta["cell"] in SHOT03["enemies"]


def test_turn_ends_on_low_timer_and_repeated_failures(grid):
    sk = FightTurnSkill(load_phases_config(), "cra")
    st = turn_state(shot03_fighters())
    st.fight.timer_s = 3.0
    s = sk.step(st, ctx(grid))
    assert s.meta["op"] == "end_turn" and "timer" in s.action["reason"]
    # two actions in a row that don't land: end the turn instead of repositioning forever
    sk = FightTurnSkill(load_phases_config(), "cra")
    st = turn_state(shot03_fighters())
    for _ in range(2):
        s = sk.step(st, ctx(grid))
        assert s.meta["op"] != "end_turn"
        sk.on_result(StepResult(s, False, "timeout"), st)
        if s.meta["op"] == "move":
            assert s.meta["cell"] in sk.refused_moves      # planned around from now on
    e = sk.step(st, ctx(grid))
    assert e.meta["op"] == "end_turn" and e.action["kind"] == "click"

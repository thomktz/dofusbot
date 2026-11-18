import pytest

from dofus_brain.game.grid import Grid
from dofus_brain.game.state import Fighter, FightState, GameState
from dofus_brain.mock_render import FighterSpec, FightSpec, Renderer
from dofus_brain.perception import Regions
from dofus_brain.perception.placement import DEFAULT_PALETTE, PlacementReader, PlacementView
from dofus_brain.planner import PlanContext, PlannedStep, StepResult
from dofus_brain.skills.config import load_phases_config
from dofus_brain.skills.placement import PlacementSkill, choose_start_cell

RED, BLUE = tuple(DEFAULT_PALETTE["placement_red"]), tuple(DEFAULT_PALETTE["placement_blue"])


def uv(grid, u, v):
    c = grid.from_uv(u, v)
    assert c is not None
    return c


@pytest.fixture
def scene(meta):
    grid = Grid.from_config(meta["grid"])
    rc = grid.cell_id
    ours = [rc(10, 2), rc(14, 2), rc(12, 5), rc(12, 7)]
    theirs = [rc(10, 11), rc(14, 11)]
    me, enemy = rc(12, 2), rc(12, 11)
    spec = FightSpec(fighters=[FighterSpec(me, "ally", True), FighterSpec(enemy, "enemy")],
                     start_cells={**{c: RED for c in ours}, **{c: BLUE for c in theirs}}, my_turn=False)
    img = Renderer(meta["grid"], meta["regions"]).fight(spec)
    return grid, img, ours, theirs, me, enemy


def test_reader_finds_start_cells_and_team(meta, scene):
    grid, img, ours, theirs, me, enemy = scene
    v = PlacementReader(Regions(meta["regions"])).read(img, grid)
    assert v.is_placement and v.our_colour == "red"
    assert v.our_cells == sorted(ours) and v.their_cells == sorted(theirs)
    assert v.self_cell == me and v.enemies == [enemy]


def test_reader_on_normal_fight_fixture_is_not_placement(meta, load_fixture):
    grid = Grid.from_config(meta["grid"])
    v = PlacementReader(Regions(meta["regions"])).read(load_fixture("fight.png"), grid)
    assert not v.is_placement


def test_choose_cell_by_role(meta, scene):
    grid, img, ours, theirs, me, enemy = scene
    v = PlacementReader(Regions(meta["regions"])).read(img, grid)
    ranged = choose_start_cell(v, grid, "ranged", preferred_range=12)
    melee = choose_start_cell(v, grid, "melee")
    d = lambda c: grid.distance(c, enemy)
    assert d(melee) == min(d(c) for c in ours + [me])
    assert d(ranged) == max(d(c) for c in ours + [me] if d(c) <= 12)


def test_ranged_needs_line_of_sight():
    g = Grid()
    enemy = uv(g, 20, 0)
    near, far = uv(g, 15, 0), uv(g, 14, 0)
    wall = uv(g, 17, 0)
    v = PlacementView("red", our_cells=[near, far], their_cells=[], self_cell=near, enemies=[enemy], blocked=[wall])
    # far is farther but the wall breaks LoS for both in that line; add a clear cell off the line
    clear = uv(g, 15, 3)
    v.our_cells.append(clear)
    assert choose_start_cell(v, g, "ranged") == clear


def placement_state(me_cell):
    return GameState(screen="fight", confidence=0.9,
                     fight=FightState(phase="placement", fighters=[Fighter("self", "self", "ally", True, me_cell)]))


def test_skill_place_then_ready(meta, scene):
    grid, img, ours, theirs, me, enemy = scene
    sk = PlacementSkill(load_phases_config(), "melee")
    sk.set_view(PlacementReader(Regions(meta["regions"])).read(img, grid))
    ctx = PlanContext(goal=None, mode="auto", grid=grid, config={})
    st = placement_state(me)
    s1 = sk.step(st, ctx)
    assert s1.meta["op"] == "place"
    assert s1.verify(st, placement_state(s1.action["cell"])) and not s1.verify(st, st)
    sk.on_result(StepResult(s1, True, "verified"), st)
    s2 = sk.step(st, ctx)
    assert s2.action["key"] == "f1"
    started = GameState(screen="fight", fight=FightState(phase="my_turn"))
    assert s2.verify(st, started)
    sk.on_result(StepResult(s2, True, "verified"), started)
    assert sk.step(st, ctx).status == "wait"
    assert sk.step(started, ctx).status == "done"


def test_skill_unreadable_just_ready():
    sk = PlacementSkill(load_phases_config(), "ranged")
    ctx = PlanContext(goal=None, mode="auto", grid=Grid(), config={})
    s = sk.step(placement_state(5), ctx)
    assert isinstance(s, PlannedStep) and s.meta["op"] == "ready"

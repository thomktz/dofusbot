import pytest

from dofus_brain.game.grid import Grid
from dofus_brain.game.state import GameState, MapInfo
from dofus_brain.ocr import TemplateOCR
from dofus_brain.perception import Perceiver
from dofus_brain.planner import PlanContext, PlannedStep, StepResult
from dofus_brain.skills import Outcome
from dofus_brain.skills.config import load_phases_config
from dofus_brain.skills.travel import MapGraph, TravelSkill, edge_cell_order


def ctx(grid=None):
    return PlanContext(goal="travel", mode="auto", grid=grid or Grid(), config={})


def world(coords, screen="world"):
    return GameState(screen=screen, confidence=0.9, map=MapInfo(coords=coords))


def test_route_straight_and_around_blocked_edge():
    g = MapGraph()
    assert g.route((0, 0), (3, 0)) == [(0, 0), (1, 0), (2, 0), (3, 0)]
    g.node((1, 0)).blocked.add("right")
    r = g.route((0, 0), (3, 0))
    assert r[0] == (0, 0) and r[-1] == (3, 0) and len(r) == 6
    assert ((1, 0), (2, 0)) not in list(zip(r, r[1:]))


def test_route_uses_learned_jump_and_danger_cost():
    g = MapGraph()
    g.record_transition((0, 0), "right", (5, 0))  # e.g. a door / non-grid link
    assert g.route((0, 0), (5, 0)) == [(0, 0), (5, 0)]
    g2 = MapGraph()
    danger = {(1, 0): 10.0}
    r = g2.route((0, 0), (2, 0), cost=lambda c: danger.get(c, 0.0))
    assert (1, 0) not in r and len(r) == 5


def test_route_none_when_walled_in():
    g = MapGraph()
    g.node((0, 0)).blocked |= {"right", "left", "top", "bottom"}
    assert g.route((0, 0), (1, 0)) is None


def test_graph_persistence(tmp_path):
    g = MapGraph()
    g.record_transition((4, -18), "right", (5, -18))
    g.record_failure((5, -18), "top", 20, 3)
    p = tmp_path / "maps.json"
    g.save(p)
    h = MapGraph.load(p)
    assert h.node((4, -18)).links == {"right": (5, -18)}
    assert h.node((5, -18)).failed_cells == {"top": [20]}


def test_edge_cell_order_middle_first():
    grid = Grid()
    order = edge_cell_order(grid, "right")
    assert sorted(order) == sorted(grid.edge_cells("right"))
    cells = grid.edge_cells("right")
    assert order[0] in cells[len(cells) // 2 - 1: len(cells) // 2 + 1]


def test_travel_on_fixtures(meta, load_fixture):
    """Perceive world_4_-18, plan a right-edge click, verify against world_5_-18."""
    p = Perceiver(meta["grid"], meta["regions"], TemplateOCR.from_font())
    before = p.perceive(load_fixture("world_4_-18.png")).state
    after = p.perceive(load_fixture("world_5_-18.png"), prev=before).state
    assert before.map.coords == (4, -18) and after.map.coords == (5, -18)
    grid = Grid.from_config(meta["grid"])
    sk = TravelSkill((6, -18))
    step = sk.step(before, ctx(grid))
    assert isinstance(step, PlannedStep)
    assert step.action["kind"] == "click" and step.action["cell"] in grid.edge_cells("right")
    assert step.verify(before, after) and not step.verify(before, before)
    sk.on_result(StepResult(step, True, "verified"), after)
    assert sk.graph.node((4, -18)).links["right"] == (5, -18)
    nxt = sk.step(after, ctx(grid))
    assert nxt.meta["to"] == (6, -18)
    assert sk.step(world((6, -18)), ctx(grid)).status == "done"


def test_travel_waits_on_loading_and_other_screens():
    sk = TravelSkill((1, 0))
    assert sk.step(world((0, 0), "loading"), ctx()).status == "wait"
    assert sk.step(world((0, 0), "fight"), ctx()).status == "wait"
    assert sk.step(world(None), ctx()).status == "ask"


def test_travel_tries_other_cells_then_blocks_edge_then_detours():
    cfg = load_phases_config(override={"travel": {"edge_cells_per_side": 2, "max_stuck_steps": 10}})
    sk = TravelSkill((1, 0), cfg=cfg)
    st = world((0, 0))
    s1 = sk.step(st, ctx())
    sk.on_result(StepResult(s1, False, "timeout"), st)
    s2 = sk.step(st, ctx())
    assert s2.meta["direction"] == "right" and s2.meta["cell"] != s1.meta["cell"]
    sk.on_result(StepResult(s2, False, "timeout"), st)
    assert "right" in sk.graph.node((0, 0)).blocked
    s3 = sk.step(st, ctx())
    assert s3.meta["direction"] in ("top", "bottom")


def test_travel_records_unexpected_destination():
    sk = TravelSkill((3, 0))
    st = world((0, 0))
    s = sk.step(st, ctx())
    sk.on_result(StepResult(s, True, "verified"), world((0, 7)))
    assert sk.graph.node((0, 0)).links["right"] == (0, 7)
    assert "instead" in sk.last_reason


def test_travel_stuck_detection():
    cfg = load_phases_config(override={"travel": {"edge_cells_per_side": 99, "max_stuck_steps": 3}})
    sk = TravelSkill((5, 0), cfg=cfg)
    st = world((0, 0))
    for _ in range(3):
        s = sk.step(st, ctx())
        assert isinstance(s, PlannedStep)
        sk.on_result(StepResult(s, False, "timeout"), st)
    out = sk.step(st, ctx())
    assert isinstance(out, Outcome) and out.status == "failed" and "stuck" in out.reason


def test_travel_avoids_cells():
    grid = Grid()
    first = edge_cell_order(grid, "right")[0]
    sk = TravelSkill((1, 0), avoid_cells=lambda c: c == first)
    s = sk.step(world((0, 0)), ctx(grid))
    assert s.meta["cell"] != first

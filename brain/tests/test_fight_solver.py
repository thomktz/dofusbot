import pytest

from dofus_brain.game.grid import Grid
from dofus_brain.game.state import Character, Fighter, FightState, GameState
from dofus_brain.ocr import OcrLine, TemplateOCR
from dofus_brain.perception import FightReader, Perceiver, Regions
from dofus_brain.perception.entities import attach_to_fighters, parse_plate, read_plates
from dofus_brain.planner import PlanContext, PlannedStep, StepResult
from dofus_brain.skills.config import load_phases_config
from dofus_brain.skills.fight import FightSolver, FightTurnSkill, Spell

G = Grid()
BOW = Spell("Arrow", "1", pa=3, min_range=1, max_range=6, los=True, damage=10, per_turn=3, per_target=3)
FIST = Spell("Punch", "1", pa=3, min_range=1, max_range=1, los=True, damage=15, per_turn=2, per_target=2)


def ctx():
    return PlanContext(goal=None, mode="auto", grid=G, config={})


def cell_at_uv(u, v):
    c = G.from_uv(u, v)
    assert c is not None
    return c


def fight_state(me, enemies, blocked=(), pa=6, pm=3, phase="my_turn"):
    fs = FightState(phase=phase, turn=1, fighters=[Fighter("self", "self", "ally", True, me, 100, 100, pa, pm)]
                    + [Fighter(f"e{i}", n, "enemy", False, c, hp, hp) for i, (n, c, hp) in enumerate(enemies)],
                    blocked_cells=list(blocked))
    return GameState(screen="fight", confidence=0.9, character=Character(pa=pa, pm=pm), fight=fs)


@pytest.mark.parametrize("text,name,level,hp,group", [
    ("Bouftou (12/30)", "Bouftou", None, (12, 30), None),
    ("Tofu 7/20", "Tofu", None, (7, 20), None),
    ("Piou Rouge niv. 5", "Piou Rouge", 5, None, None),
    ("Arakne - Niveau 12", "Arakne", 12, None, None),
    ("Groupe de 3 monstres (niv. 24)", "Groupe de 3 monstres", 24, None, 3),
    ("Ganymede", "Ganymede", None, None, None),
])
def test_parse_plate(text, name, level, hp, group):
    p = parse_plate(text)
    assert p.name == name and p.level == level and p.group_size == group
    assert (p.hp, p.hp_max) == (hp if hp else (None, None))


def test_parse_plate_rejects_noise():
    assert parse_plate("") is None and parse_plate("12/30") is None
    assert parse_plate("Bouftou (40/30)").hp is None  # hp > max: misread


def test_plates_attach_names_and_hp_on_fixture(meta, load_fixture):
    grid = Grid.from_config(meta["grid"])
    fs = FightReader(Regions(meta["regions"])).read(load_fixture("fight.png"), grid, scale=1.0)
    lines = []
    for name, cell, hp in (("Bouftou", 245, "(20/40)"), ("Tofu", 301, "(9/30)")):
        x, y = grid.center(cell)
        lines.append(OcrLine(f"{name} {hp}", (int(x - 30), int(y - grid.ch - 6), int(x + 30), int(y - grid.ch + 6))))
    plates = read_plates(lines, grid)
    assert [p.cell for p in plates] == [245, 301]
    assert attach_to_fighters(plates, fs, grid) == 2
    by_cell = {f.cell: f for f in fs.fighters}
    assert by_cell[245].name == "Bouftou" and by_cell[245].hp == 20
    assert by_cell[301].name == "Tofu" and by_cell[301].hp_max == 30


def test_solver_casts_from_place_when_in_range():
    me, e = cell_at_uv(10, 0), cell_at_uv(13, 0)
    st = fight_state(me, [("Bouftou", e, 50)])
    plan = FightSolver(G, [BOW], "ranged", keep_distance=2).plan(st.fight, pa=6, pm=0)
    assert plan.move_to is None and [c.target_cell for c in plan.casts] == [e, e]


def test_solver_melee_walks_then_hits():
    me, e = cell_at_uv(10, 0), cell_at_uv(13, 0)
    st = fight_state(me, [("Bouftou", e, 50)])
    plan = FightSolver(G, [FIST], "melee").plan(st.fight, pa=6, pm=3)
    assert plan.move_to is not None and G.distance(plan.move_to, e) == 1
    assert len(plan.casts) == 2


def test_solver_prefers_kill_and_respects_los():
    me = cell_at_uv(10, 0)
    weak, strong = cell_at_uv(14, 0), cell_at_uv(10, 4)
    st = fight_state(me, [("Weak", weak, 10), ("Strong", strong, 200)])
    plan = FightSolver(G, [BOW], "ranged").plan(st.fight, pa=3, pm=0)
    assert plan.casts[0].target_cell == weak
    # a wall between us and the weak one: shoot the other
    wall = cell_at_uv(12, 0)
    st2 = fight_state(me, [("Weak", weak, 10), ("Strong", strong, 200)], blocked=[wall])
    plan2 = FightSolver(G, [BOW], "ranged").plan(st2.fight, pa=3, pm=0)
    assert plan2.casts[0].target_cell == strong


def test_solver_ranged_kites_after_casting():
    me, e = cell_at_uv(10, 0), cell_at_uv(11, 0)
    st = fight_state(me, [("Bouftou", e, 100)])
    plan = FightSolver(G, [BOW], "ranged", keep_distance=4).plan(st.fight, pa=3, pm=3)
    assert plan.casts
    end = plan.move_after if plan.move_after is not None else (plan.move_to or me)
    assert G.distance(end, e) > 1


def test_solver_on_fixture(meta, load_fixture):
    p = Perceiver(meta["grid"], meta["regions"], TemplateOCR.from_font())
    st = p.perceive(load_fixture("fight.png")).state
    assert st.fight.phase == "my_turn" and st.character.pa == 6
    grid = Grid.from_config(meta["grid"])
    plan = FightSolver(grid, [BOW], "ranged", 2).plan(st.fight, st.character.pa, st.character.pm)
    blk = set(st.fight.blocked_cells)
    assert plan.casts, plan.why
    reach = grid.reachable(st.fight.me().cell, 3, blk, {f.cell for f in st.fight.enemies()})
    if plan.move_to is not None:
        assert plan.move_to in reach


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def skill(cls="cra", hook=None, clock=None):
    cfg = load_phases_config(override={"fight": {"classes": {"cra": {"role": "ranged", "spells": [BOW.__dict__]}}}})
    return FightTurnSkill(cfg, cls, strategy_hook=hook, clock=clock or Clock(), config_dir="/nonexistent")


def test_turn_skill_select_click_verify_then_end_turn():
    calls = []
    sk = skill(hook=lambda st: calls.append(1) or {"focus": "Tofu"})
    me, e = cell_at_uv(10, 0), cell_at_uv(14, 0)
    st = fight_state(me, [("Tofu", e, 50)], pa=3, pm=0)
    s1 = sk.step(st, ctx())
    assert s1.action == {"kind": "key", "key": "1", "reason": s1.action["reason"]}
    assert s1.verify(st, st)
    sk.on_result(StepResult(s1, True, "verified"), st)
    s2 = sk.step(st, ctx())
    assert s2.action["kind"] == "click" and s2.action["cell"] == e
    after = fight_state(me, [("Tofu", e, 40)], pa=0, pm=0)
    assert s2.verify(st, after) and not s2.verify(st, st)
    sk.on_result(StepResult(s2, True, "verified"), after)
    s3 = sk.step(after, ctx())
    assert s3.action["key"] == "f1" and s3.meta["op"] == "end_turn"
    assert s3.verify(after, fight_state(me, [("Tofu", e, 40)], phase="enemy_turn"))
    sk.step(st, ctx())
    assert calls == [1]  # strategy hook: once per fight


def test_turn_skill_refused_cast_tries_next_target():
    sk = skill()
    me = cell_at_uv(10, 0)
    a, b = cell_at_uv(13, 0), cell_at_uv(10, 3)
    st = fight_state(me, [("A", a, 5), ("B", b, 50)], pa=3, pm=0)
    sk.on_result(StepResult(sk.step(st, ctx()), True, "verified"), st)
    s = sk.step(st, ctx())
    first = s.action["cell"]
    sk.on_result(StepResult(s, False, "timeout"), st)  # PA unchanged: refused
    sk.on_result(StepResult(sk.step(st, ctx()), True, "verified"), st)
    s2 = sk.step(st, ctx())
    assert s2.action["kind"] == "click" and s2.action["cell"] != first


def test_turn_skill_move_verify_and_tackle_replan():
    sk = skill()
    me, e = cell_at_uv(10, 0), cell_at_uv(20, 0)
    st = fight_state(me, [("Far", e, 50)], pa=3, pm=3)
    s = sk.step(st, ctx())
    assert s.meta["op"] == "move"
    tackled = fight_state(me, [("Far", e, 50)], pa=3, pm=1)  # PM lost, did not move
    assert s.verify(st, tackled)


def test_turn_skill_waits_and_finishes():
    sk = skill()
    assert sk.step(fight_state(1, [], phase="enemy_turn"), ctx()).status == "wait"
    assert sk.step(fight_state(1, [], phase="placement"), ctx()).status == "wait"
    assert sk.step(GameState(screen="world"), ctx()).status == "done"


def test_turn_skill_time_budget_ends_turn():
    clock = Clock()
    sk = skill(clock=clock)
    me, e = cell_at_uv(10, 0), cell_at_uv(14, 0)
    st = fight_state(me, [("Tofu", e, 50)], pa=6, pm=0)
    assert sk.step(st, ctx()).meta["op"] == "select"
    clock.t = 25.0  # > 0.7 * 30 s
    assert sk.step(st, ctx()).meta["op"] == "end_turn"


def test_turn_skill_hook_errors_are_ignored():
    def bad(st):
        raise RuntimeError("llm down")
    sk = skill(hook=bad)
    me, e = cell_at_uv(10, 0), cell_at_uv(14, 0)
    assert isinstance(sk.step(fight_state(me, [("Tofu", e, 50)]), ctx()), PlannedStep)
    assert "error" in sk.strategy

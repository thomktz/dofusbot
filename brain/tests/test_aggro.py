from dofus_brain.game.grid import Grid
from dofus_brain.game.state import Character, GameState, MapInfo
from dofus_brain.ocr import OcrLine
from dofus_brain.perception.entities import read_plates
from dofus_brain.planner import PlanContext, StepResult
from dofus_brain.skills.aggro import (AggroMap, DangerRules, EngageSkill, MonsterGroup, detect_aggro,
                                      groups_from_plates, on_aggro)
from dofus_brain.skills.config import load_phases_config
from dofus_brain.skills.travel import TravelSkill

G = Grid()


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


def rules(**over):
    return DangerRules(load_phases_config(override={"aggro": over} if over else None))


def plate_line(text, cell):
    x, y = G.center(cell)
    return OcrLine(text, (int(x - 30), int(y - G.ch - 6), int(x + 30), int(y - G.ch + 6)))


def test_groups_from_plates_skip_npcs():
    plates = read_plates([plate_line("Ganymede", 100), plate_line("Bouftou niv. 6", 200),
                          plate_line("Groupe de 4 monstres (niv. 30)", 300)], G)
    groups = groups_from_plates(plates, (4, -18), now=1.0)
    assert [(g.name, g.level, g.size, g.cell) for g in groups] == [
        ("Bouftou", 6, 1, 200), ("Groupe de 4 monstres", 30, 4, 300)]


def test_rules_first_match_wins():
    r = rules()
    assert r.engage(MonsterGroup("Bouftou", (0, 0), 1, level=6, size=2), char_level=5)
    assert not r.engage(MonsterGroup("Bouftou", (0, 0), 1, level=30, size=2), char_level=5)   # too strong
    assert not r.engage(MonsterGroup("Bouftou", (0, 0), 1, level=5, size=5), char_level=5)    # too many
    assert not r.engage(MonsterGroup("Bouftou", (0, 0), 1, level=None), char_level=5)        # unknown level
    r2 = rules(rules=[{"name": "boss", "engage": False}, {"name": "", "engage": True}])
    assert not r2.engage(MonsterGroup("Boss Bouftou", (0, 0), 1), 50)
    assert r2.engage(MonsterGroup("Tofu", (0, 0), 1), 50)


def test_route_around_dangerous_map_and_cells():
    clock = Clock()
    am = AggroMap(rules(aggressive_names=["arakne"]), clock=clock)
    am.update((1, 0), [MonsterGroup("Arakne Majeure", (1, 0), 200, level=40, size=3, seen_at=clock.t)])
    sk = TravelSkill((2, 0), danger=am.danger_cost(5))
    st = GameState(screen="world", confidence=0.9, map=MapInfo((0, 0)), character=Character(level=5))
    step = sk.step(st, PlanContext(None, "auto", G, {}))
    assert step.meta["to"] != (1, 0)
    avoid = am.avoid_cells((1, 0), G, 5)
    assert avoid(200) and avoid(G.neighbours(200)[0]) and not avoid(0)
    clock.t += 1000  # sighting expired
    assert am.danger_cost(5)((1, 0)) == 0.0


def test_non_aggressive_groups_are_not_threats():
    am = AggroMap(rules(), clock=Clock())
    am.update((0, 0), [MonsterGroup("Bouftou", (0, 0), 200, level=50, size=4, seen_at=100.0)])
    assert am.threats((0, 0), 5) == []


def test_detect_and_learn_aggro():
    w = GameState(screen="world")
    f = GameState(screen="fight")
    assert detect_aggro(w, f, None)
    assert detect_aggro(w, f, {"skill": "travel"})
    assert not detect_aggro(w, f, {"skill": "engage", "target": "monster"})
    assert not detect_aggro(f, f, None)
    am = AggroMap(rules(), clock=Clock())
    am.update((0, 0), [MonsterGroup("Bouftou", (0, 0), 200, 5, 1, 100.0), MonsterGroup("Tofu", (0, 0), 400, 3, 1, 100.0)])
    assert on_aggro(am, (0, 0), 201, G) == ["Bouftou"]
    assert am.rules.is_aggressive(MonsterGroup("Bouftou Royal", (0, 0), None))


def test_engage_skill():
    am = AggroMap(rules(), clock=Clock())
    am.update((0, 0), [MonsterGroup("Bouftou", (0, 0), 200, 6, 2, 100.0), MonsterGroup("Tofu", (0, 0), 300, 3, 1, 100.0)])
    sk = EngageSkill(am)
    st = GameState(screen="world", map=MapInfo((0, 0)), character=Character(level=5))
    ctx = PlanContext(None, "auto", G, {})
    s = sk.step(st, ctx)
    assert s.action["cell"] == 300 and s.meta["target"] == "monster"
    assert s.verify(st, GameState(screen="fight"))
    sk.on_result(StepResult(s, False, "timeout"), st)
    assert sk.step(st, ctx).action["cell"] == 200
    assert sk.step(GameState(screen="fight"), ctx).status == "done"

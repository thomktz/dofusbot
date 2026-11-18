import json
import time

import pytest

from dofus_brain.game.grid import Grid
from dofus_brain.game.state import Character, Fighter, FightState, GameState
from dofus_brain.planner import PlanContext, StepResult
from dofus_brain.skills.combat import FightSession, TurnSolver
from dofus_brain.skills.config import load_phases_config
from dofus_brain.skills.fight import FightTurnSkill
from dofus_brain.skills.spellmodel import (SpellModel, Zone, collision_damage, evaluate_cast, push_result,
                                           zone_cells)
from dofus_brain.skills.spells import parse_spell_tooltip
from dofus_brain.skills.threat import MonsterBook, ThreatMap

G = Grid()


def C(u, v):
    c = G.from_uv(u, v)
    assert c is not None, (u, v)
    return c


def UV(cell):
    return G.uv(cell)


def spell(name="Arrow", key="1", pa=3, lo=1, hi=6, dmg=(10, 10), element="air", zone=None, line=False, los=True,
          boost=False, effects=None, per_turn=3, per_target=3, cooldown=0):
    eff = effects if effects is not None else [{"kind": "damage", "element": element, "min": dmg[0], "max": dmg[1]}]
    d = {"name": name, "key": key, "pa": pa, "targeting": {"min_range": lo, "max_range": hi, "line": line, "los": los,
                                                           "boostable": boost},
         "effects": eff, "per_turn": per_turn, "per_target": per_target, "cooldown": cooldown}
    if zone:
        d["zone"] = {"shape": zone[0], "size": zone[1]}
    return SpellModel.from_json(d)


def me_at(cell, pa=6, pm=3):
    return Fighter("self", "Chern-Simons", "ally", True, cell, 100, 100, pa, pm)


def enemy(i, cell, hp=40, name="Bouftou Nuageux"):
    return Fighter(f"e{i}", name, "enemy", False, cell, hp, hp)


# ------------------------------------------------------------------ zones (one fixture per area type)
def test_zone_single_and_circle():
    t = C(10, 0)
    assert zone_cells(G, C(5, 0), t, Zone("single", 0)) == [t]
    circle = zone_cells(G, C(5, 0), t, Zone("circle", 1))
    assert sorted(circle) == sorted([t, C(11, 0), C(9, 0), C(10, 1), C(10, -1)])
    assert len(zone_cells(G, C(5, 0), t, Zone("circle", 2))) == 13


def test_zone_cross_line_cone_follow_cast_direction():
    caster, t = C(5, 0), C(10, 0)
    cross = zone_cells(G, caster, t, Zone("cross", 2))
    assert len(cross) == 9 and C(12, 0) in cross and C(10, -2) in cross and C(11, 1) not in cross
    line = zone_cells(G, caster, t, Zone("line", 2))
    assert line == [t, C(11, 0), C(12, 0)]                      # continues away from the caster
    cone = zone_cells(G, caster, t, Zone("cone", 2))
    assert t in cone and C(11, 1) in cone and C(12, -2) in cone and C(9, 0) not in cone
    assert len(cone) == 1 + 3 + 5


# ------------------------------------------------------------------ effects
def test_damage_after_resistances_and_unknown_flag():
    sp = spell(dmg=(8, 12))
    caster, e = me_at(C(5, 0)), enemy(1, C(9, 0))
    book = MonsterBook({"monsters": {"Bouftou Nuageux": {"resistances": {"air": 50}}}})
    v = evaluate_cast(G, sp, caster, e.cell, [caster, e], set(), book.resist)
    assert v.damage == {"e1": 5.0} and not v.unknown_resist
    v2 = evaluate_cast(G, sp, caster, e.cell, [caster, e], set(), MonsterBook().resist)
    assert v2.damage == {"e1": 10.0} and v2.unknown_resist         # unknown -> 0 %, flagged


def test_push_collision_and_friendly_fire():
    push = spell(effects=[{"kind": "damage", "element": "air", "min": 4, "max": 4}, {"kind": "push", "value": 3}])
    caster, e = me_at(C(5, 0)), enemy(1, C(8, 0))
    final, rem = push_result(G, caster.cell, e.cell, 3, {C(10, 0)})
    assert final == C(9, 0) and rem == 2
    v = evaluate_cast(G, push, caster, e.cell, [caster, e], {C(10, 0)}, level=10)
    assert v.pushed_to["e1"] == C(9, 0)
    assert v.damage["e1"] == pytest.approx(4 + collision_damage(2, 10))
    boom = spell(zone=("circle", 1), dmg=(10, 10))
    ally = Fighter("a", "Ally", "ally", False, C(9, 1), 50, 50)
    v2 = evaluate_cast(G, boom, caster, C(9, 0), [caster, e, ally], set())
    assert v2.friendly == 10 and "e1" in v2.damage


def test_ap_mp_removal_recorded():
    sp = spell(effects=[{"kind": "mp_remove", "value": 2}, {"kind": "ap_remove", "value": 1}])
    caster, e = me_at(C(5, 0)), enemy(1, C(8, 0))
    v = evaluate_cast(G, sp, caster, e.cell, [caster, e], set())
    assert v.mp_removed == {"e1": 2} and v.ap_removed == {"e1": 1} and not v.damage


# ------------------------------------------------------------------ threat map + monster book
def test_threat_map_melee_reach_and_los():
    e = enemy(1, C(10, 0))
    book = MonsterBook({"monsters": {"Bouftou Nuageux": {"mp": 2, "attack": {"min_range": 1, "max_range": 1,
                                                                            "damage": 9, "los": True}}}})
    t = ThreatMap.build(G, [e], set(), {e.cell}, book)
    assert t[C(13, 0)] == 9          # 2 MP + 1 range
    assert t[C(14, 0)] == 0
    assert t.without(C(13, 0), {"e1"}) == 0


def test_monster_book_roundtrip(tmp_path):
    p = tmp_path / "monsters.json"
    b = MonsterBook.load(p)
    b.saw("Bouftou Nuageux (6)")
    b.set_resistances("Bouftou Nuageux", {"air": 10, "fire": -5})
    b.save()
    b2 = MonsterBook.load(p)
    r = b2.resist(enemy(1, 0, name="Bouftou Nuageux (4)"))
    assert r["air"] == 10 and r["fire"] == -5 and r["earth"] is None
    assert b2.get("bouftou nuageux")["seen"] == 1


def test_repo_monster_cache_has_incarnam_bouftous_unknown():
    from dofus_brain.paths import data_dir

    b = MonsterBook.load(data_dir() / "monsters.json")
    for n in ("Bouftou Nuageux", "Bouftou Orageux", "Bouftou Pâlichon"):
        m = b.get(n)
        assert m is not None and all(v is None for v in m["resistances"].values())


# ------------------------------------------------------------------ tooltip: zones and effects
def test_tooltip_zone_effects_and_review():
    s = parse_spell_tooltip(["Flèche Explosive", "4 PA", "Portée : 1 - 8", "Dommages Feu : 20 à 24",
                             "Zone : cercle de rayon 1", "Retrait de 1 PM", "Effet bizarre inconnu", "Repousse de 2 cases"])
    assert s["zone"] == {"shape": "circle", "size": 1}
    assert {"kind": "damage", "element": "fire", "min": 20, "max": 24} in s["effects"]
    assert {"kind": "mp_remove", "value": 1} in s["effects"] and {"kind": "push", "value": 2} in s["effects"]
    assert s["review"] == ["effet bizarre inconnu"]
    m = SpellModel.from_json({"key": "1", **s})
    assert m.zone.shape == "circle" and len(m.effects) == 3
    en = parse_spell_tooltip(["Long Shot", "2 AP", "+3 range (2 turns)", "Zone: cross of 2"])
    assert {"kind": "range_buff", "value": 3, "turns": 2} in en["effects"] and en["zone"] == {"shape": "cross", "size": 2}


# ------------------------------------------------------------------ solver: Incarnam Bouftou scenario
def bouftou_fight(me_cell, pa=6, pm=3):
    """Level 13 group from the 9 Nov scan: Bouftou Nuageux (6), Bouftou Nuageux (4), Bouftou Orageux (3)."""
    fighters = [me_at(me_cell, pa, pm), enemy(1, C(14, 0), 30, "Bouftou Nuageux"),
                enemy(2, C(14, 2), 22, "Bouftou Nuageux"), enemy(3, C(15, -2), 12, "Bouftou Orageux")]
    return FightState(phase="my_turn", turn=1, fighters=fighters, blocked_cells=[C(12, 1)])


BOW = spell("Fleche", '"', pa=3, lo=1, hi=6, dmg=(10, 14))


def test_solver_kills_weakest_and_ends_out_of_melee():
    fs = bouftou_fight(C(8, 0))
    book = MonsterBook({"monsters": {"Bouftou Orageux": {"mp": 3}, "Bouftou Nuageux": {"mp": 3}}})
    t0 = time.perf_counter()
    plan = TurnSolver(G, [BOW], "ranged", book).plan(fs, 6, 3)
    assert (time.perf_counter() - t0) < 2.0
    assert [c.target_id for c in plan.casts].count("e3") >= 1        # Orageux (12 HP) dies
    end = plan.end_cell
    threat = ThreatMap.build(G, [f for f in fs.fighters if f.team == "enemy" and f.id != "e3"],
                             set(fs.blocked_cells), {f.cell for f in fs.fighters if not f.is_self}, book)
    assert threat[end] == plan.danger


def test_solver_prefers_area_hitting_two():
    fs = FightState(phase="my_turn", fighters=[me_at(C(5, 0)), enemy(1, C(10, 0), 100), enemy(2, C(10, 2), 100)])
    single = spell("Single", "1", pa=4, lo=1, hi=8, dmg=(12, 12))
    area = spell("Burst", "2", pa=4, lo=1, hi=8, dmg=(10, 10), zone=("circle", 1))
    plan = TurnSolver(G, [single, area], "ranged").plan(fs, 4, 0)
    c = plan.casts[0]
    assert c.spell.name == "Burst" and c.target_cell == C(10, 1) and len(c.value.damage) == 2


def test_solver_uses_range_buff_only_when_it_enables_a_cast():
    buff = spell("Tirs", "è", pa=2, lo=0, hi=0, effects=[{"kind": "range_buff", "value": 3, "turns": 1}])
    bow = spell("Fleche", '"', pa=3, lo=1, hi=5, dmg=(10, 10), boost=True)
    far = FightState(phase="my_turn", fighters=[me_at(C(5, 0)), enemy(1, C(12, 0), 100)])
    plan = TurnSolver(G, [buff, bow], "ranged").plan(far, 5, 0)
    assert [c.spell.name for c in plan.casts] == ["Tirs", "Fleche"]
    near = FightState(phase="my_turn", fighters=[me_at(C(5, 0)), enemy(1, C(9, 0), 100)])
    plan2 = TurnSolver(G, [buff, bow], "ranged").plan(near, 5, 0)
    assert [c.spell.name for c in plan2.casts] == ["Fleche"]


def test_solver_resistances_change_the_choice():
    fs = FightState(phase="my_turn", fighters=[me_at(C(5, 0)), enemy(1, C(9, 0), 100, "Bouftou Orageux")])
    air = spell("Air", "1", pa=3, dmg=(12, 12), element="air")
    fire = spell("Fire", "2", pa=3, dmg=(10, 10), element="fire")
    book = MonsterBook({"monsters": {"Bouftou Orageux": {"resistances": {"air": 50, "fire": 0}}}})
    plan = TurnSolver(G, [air, fire], "ranged", book).plan(fs, 3, 0)
    assert plan.casts[0].spell.name == "Fire"


def test_solver_mp_removal_and_push_have_value():
    fs = FightState(phase="my_turn", fighters=[me_at(C(5, 0)), enemy(1, C(7, 0), 100)])
    slow = spell("Glacee", '"', pa=3, effects=[{"kind": "mp_remove", "value": 2}])
    plan = TurnSolver(G, [slow], "ranged").plan(fs, 3, 0)
    assert plan.casts and plan.casts[0].spell.name == "Glacee"
    recul = spell("Recul", "é", pa=3, line=True, effects=[{"kind": "push", "value": 4}])
    plan2 = TurnSolver(G, [recul], "ranged").plan(fs, 3, 0)
    assert plan2.casts and plan2.casts[0].value.pushed_to["e1"] == C(11, 0)


def test_session_cooldown_buff_turns_and_per_target():
    buff = spell("Tirs", "è", pa=2, lo=0, hi=0, effects=[{"kind": "range_buff", "value": 3, "turns": 2}], cooldown=3)
    bow = spell("Fleche", '"', pa=3, per_target=1)
    s = FightSession([buff, bow], "ranged")
    s.start_turn()
    s.record_cast(buff, "", 1)
    assert s.range_bonus == 3 and buff not in s.available()
    s.record_cast(bow, "e1", 5)
    assert s.per_target[("Fleche", "e1")] == 1
    s.start_turn()
    assert s.range_bonus == 3 and s.per_target == {}          # buff lasts 2 turns
    s.start_turn()
    assert s.range_bonus == 0 and buff not in s.available()
    s.start_turn()
    assert buff in s.available()                               # cooldown 3


def test_skill_replans_after_each_action_and_records_casts(tmp_path):
    spells_file = tmp_path / "spells_cra.json"
    spells_file.write_text(json.dumps({"spells": [{"name": "Fleche", "key": '"', "pa": 3,
                                                   "targeting": {"min_range": 1, "max_range": 6},
                                                   "effects": [{"kind": "damage", "element": "air", "min": 10, "max": 14}],
                                                   "per_turn": 2, "per_target": 2}]}))
    sk = FightTurnSkill(load_phases_config(), "cra", config_dir=tmp_path)
    fs = bouftou_fight(C(8, 0), pa=6, pm=0)
    st = GameState(screen="fight", character=Character(pa=6, pm=0, level=10), fight=fs)
    ctx = PlanContext(None, "auto", G, {})
    sel = sk.step(st, ctx)
    assert sel.action["key"] == '"' and sk.session is not None and sk.strategy == {}
    sk.on_result(StepResult(sel, True, "verified"), st)
    cast = sk.step(st, ctx)
    sk.on_result(StepResult(cast, True, "verified"), st)
    assert sk.session.used == {"Fleche": 1} and sk.session.log[0]["spell"] == "Fleche"
    fs2 = bouftou_fight(C(8, 0), pa=3, pm=3)
    fs2.fighters = [f for f in fs2.fighters if f.id != cast.meta.get("target_id")]   # target died
    nxt = sk.step(GameState(screen="fight", character=Character(pa=3, pm=3), fight=fs2), ctx)
    assert nxt.meta["op"] in ("select", "move")                 # re-planned on the new state
    assert sk.last_plan.casts and all(c.target_id != cast.meta.get("target_id") for c in sk.last_plan.casts)


def test_retreat_moves_the_caster_and_sets_end_cell():
    vag = spell("Vagabonde", "(", pa=3, lo=1, hi=6, line=True,
                effects=[{"kind": "retreat", "value": 1}, {"kind": "damage", "element": "earth", "min": 16, "max": 18}])
    caster, e = me_at(C(5, 0), pa=3, pm=0), enemy(1, C(7, 0), 100)
    v = evaluate_cast(G, vag, caster, e.cell, [caster, e], set())
    assert v.caster_to == C(4, 0) and v.damage == {"e1": 17.0}
    plan = TurnSolver(G, [vag], "ranged").plan(FightState(phase="my_turn", fighters=[caster, e]), 3, 0)
    assert plan.casts and plan.end_cell == C(4, 0)

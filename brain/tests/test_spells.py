import json

import pytest

from dofus_brain.game.grid import Grid
from dofus_brain.game.state import Character, Fighter, FightState, GameState
from dofus_brain.planner import PlanContext, StepResult
from dofus_brain.skills.config import load_phases_config
from dofus_brain.skills.fight import FightTurnSkill, spells_for
from dofus_brain.skills.spells import load_spells_file, parse_spell_tooltip, spells_path, update_spells_file

FR = ["Flèche Magique", "4 PA", "Portée : 1 - 8", "Portée modifiable", "Ligne de vue",
      "Nb. de lancers par tour : 2", "Nb. de lancers par cible : 1", "Dommages Air : 12 à 14"]
EN = ["Retreat Arrow", "AP cost: 3", "Range: 1 to 6", "Cast in a straight line", "No line of sight",
      "Casts per turn: 1", "Recast interval: 2", "Air damage: 8 to 10"]


def test_parse_french_tooltip():
    s = parse_spell_tooltip(FR)
    assert s == {"name": "Flèche Magique", "pa": 4, "min_range": 1, "max_range": 8, "range_modifiable": True,
                 "los": True, "per_turn": 2, "per_target": 1, "damage": 13.0,
                 "effects": [{"kind": "damage", "element": "air", "min": 12, "max": 14}]}


def test_parse_english_tooltip():
    s = parse_spell_tooltip(EN)
    assert (s["pa"], s["min_range"], s["max_range"], s["line"], s["los"], s["per_turn"], s["cooldown"], s["damage"]) == \
        (3, 1, 6, True, False, 1, 2, 9.0)


def test_parse_partial_and_empty():
    assert parse_spell_tooltip(["Sort étrange", "Portée : 3"]) == {"name": "Sort étrange", "min_range": 3, "max_range": 3}
    with pytest.raises(ValueError):
        parse_spell_tooltip(["", " "])


def test_spells_file_roundtrip_and_solver_reads_it(tmp_path):
    p = spells_path(tmp_path, "cra")
    assert load_spells_file(p) is None
    update_spells_file(p, parse_spell_tooltip(FR), "1", "cra")
    update_spells_file(p, parse_spell_tooltip(EN), "2", "cra")
    update_spells_file(p, {**parse_spell_tooltip(FR), "pa": 3}, "1", "cra")   # re-read replaces by key
    data = json.loads(p.read_text())
    assert [s["key"] for s in data["spells"]] == ["1", "2"] and data["spells"][0]["pa"] == 3
    assert all(s["verified"] for s in data["spells"])
    role, spells = spells_for(load_phases_config(), "cra", tmp_path)
    assert role == "ranged" and [s.name for s in spells] == ["Flèche Magique", "Retreat Arrow"]
    assert spells[1].cooldown == 2 and spells[1].line and not spells[1].los


def test_incomplete_entries_skipped_and_fallback(tmp_path):
    p = spells_path(tmp_path, "cra")
    p.write_text(json.dumps({"spells": [{"key": "1", "name": "No cost"}]}))
    assert spells_for(load_phases_config(), "cra", tmp_path)[1] == []
    _, fallback = spells_for(load_phases_config(), "cra", tmp_path / "missing")
    assert fallback  # placeholder table from phases config


def test_repo_spells_file_is_loadable():
    from dofus_brain.paths import config_dir

    _, spells = spells_for(load_phases_config(), "cra", config_dir())
    assert spells and all(s.pa > 0 for s in spells)


def test_cooldown_blocks_recast_until_interval(tmp_path):
    p = spells_path(tmp_path, "cra")
    p.write_text(json.dumps({"spells": [{"key": "1", "name": "Big", "pa": 3, "min_range": 1, "max_range": 8,
                                         "damage": 30, "per_turn": 1, "cooldown": 2}]}))
    sk = FightTurnSkill(load_phases_config(), "cra", config_dir=tmp_path)
    g = Grid()
    me, e = g.from_uv(10, 0), g.from_uv(13, 0)
    ctx = PlanContext(None, "auto", g, {})

    def st(phase="my_turn", pa=6):
        fs = FightState(phase=phase, fighters=[Fighter("self", "self", "ally", True, me, 100, 100, pa, 0),
                                               Fighter("e", "Tofu", "enemy", False, e, 500, 500)])
        return GameState(screen="fight", character=Character(pa=pa, pm=0), fight=fs)

    def play_turn():
        first = sk.step(st(), ctx)
        if first.meta["op"] == "select":
            sk.on_result(StepResult(first, True, "verified"), st())
            cast = sk.step(st(), ctx)
            sk.on_result(StepResult(cast, True, "verified"), st(pa=3))
        sk.step(st("enemy_turn"), ctx)
        return first.meta["op"]

    assert play_turn() == "select"      # turn 1: cast
    assert play_turn() == "end_turn"    # turn 2: on cooldown
    assert play_turn() == "select"      # turn 3: available again


def test_repo_bar_keys_from_thomas():
    from dofus_brain.paths import config_dir

    data = json.loads(spells_path(config_dir(), "cra").read_text())
    keys = {s["name"].casefold(): s["key"] for s in data["spells"]}
    assert keys["flèche de recul"] == "é" and keys["flèche glacée"] == '"' and keys["flèche de barrage"] == "'"
    assert keys["flèche vagabonde"] == "(" and keys["tirs éloignés"] == "è"
    _, spells = spells_for(load_phases_config(), "cra", config_dir())
    names = {s.name for s in spells}
    assert "Fleche Magique" not in names          # disabled: bar key unknown
    from dofus_brain.skills.config import allowed_keys

    assert {s.key for s in spells} <= allowed_keys(load_phases_config())


def test_cast_fleche_glacee_presses_its_bar_key(tmp_path):
    """Casting Flèche glacée selects it with the key '"' (AZERTY 3 without shift), and the
    action passes control's validation as a plain, unshifted key."""
    from dofus_brain.paths import config_dir
    from dofus_brain.skills.safety import SafetyGuard

    data = json.loads(spells_path(config_dir(), "cra").read_text())
    glacee = next(s for s in data["spells"] if s["name"].casefold() == "flèche glacée")
    spells_path(tmp_path, "cra").write_text(json.dumps({"spells": [glacee]}))
    sk = FightTurnSkill(load_phases_config(), "cra", config_dir=tmp_path)
    g = Grid()
    me, e = g.from_uv(10, 0), g.from_uv(14, 0)
    fs = FightState(phase="my_turn", fighters=[Fighter("self", "self", "ally", True, me, 100, 100, 6, 0),
                                               Fighter("e", "Tofu", "enemy", False, e, 50, 50)])
    st = GameState(screen="fight", character=Character(pa=6, pm=0), fight=fs)
    step = sk.step(st, PlanContext(None, "auto", g, {}))
    assert step.meta["op"] == "select" and step.meta["spell"] == "Flèche Glacée"
    assert step.action["key"] == '"' and not step.action.get("modifiers")
    assert SafetyGuard(load_phases_config()).check(step) is None
    actions = pytest.importorskip("dofus_control.actions")
    keys = pytest.importorskip("dofus_control.keys")
    assert actions.validate_action(step.action, "azerty")["modifiers"] == []
    spec = keys.resolve_key(step.action["key"], "azerty")
    assert (spec.keycode, spec.shift) == (20, False)


def test_thomas_spells_load_into_the_model():
    from dofus_brain.paths import config_dir
    from dofus_brain.skills.fight import models_for

    _, models = models_for(load_phases_config(), "cra", config_dir())
    m = {s.name: s for s in models}
    assert set(m) == {"Flèche de Recul", "Flèche Glacée", "Flèche de Barrage", "Flèche Vagabonde", "Tirs Éloignés"}
    recul, glacee, barrage, vag, tirs = (m[n] for n in ("Flèche de Recul", "Flèche Glacée", "Flèche de Barrage",
                                                        "Flèche Vagabonde", "Tirs Éloignés"))
    assert recul.key == "é" and recul.targeting.max_range == 6 and recul.per_target == 2 and recul.per_turn == 3
    assert [e.kind for e in recul.effects] == ["damage", "push"] and recul.crit == 0.1
    assert recul.effects[0].expected(0.1) == pytest.approx(0.9 * 16 + 0.1 * 19)
    assert glacee.targeting.needs_target and any(e.kind == "ap_remove" and e.value == 2 for e in glacee.effects)
    assert barrage.targeting.line and barrage.zone.shape == "single" and barrage.targeting.max_range == 7
    assert vag.targeting.line and vag.targeting.diagonal and vag.targeting.max_range == 6
    assert any(e.kind == "retreat" and e.value == 1 for e in vag.effects)   # "Recule": the caster steps back
    assert not any(e.kind == "push" for e in vag.effects)
    assert any(e.kind == "damage" and (e.min, e.max) == (16, 18) for e in vag.effects)
    assert not tirs.offensive and tirs.range_buff == 6 and tirs.min_range_buff == 3 and tirs.cooldown == 2


def test_tirs_eloignes_only_when_it_enables_a_hit():
    from dofus_brain.game.state import FightState
    from dofus_brain.paths import config_dir
    from dofus_brain.skills.combat import TurnSolver
    from dofus_brain.skills.fight import models_for

    _, models = models_for(load_phases_config(), "cra", config_dir())
    g = Grid()
    me = g.from_uv(5, 0)

    def fight(enemy_cell):
        return FightState(phase="my_turn", fighters=[Fighter("self", "me", "ally", True, me, 100, 100, 7, 0),
                                                     Fighter("e", "Bouftou Nuageux", "enemy", False, enemy_cell, 200, 200)])

    far = TurnSolver(g, models, "ranged").plan(fight(g.from_uv(15, 0)), 7, 0)     # 10 away: only with +6
    assert far.casts[0].spell.name == "Tirs Éloignés" and len(far.casts) >= 2
    near = TurnSolver(g, models, "ranged").plan(fight(g.from_uv(8, 0)), 7, 0)     # 3 away: no buff needed
    assert all(c.spell.name != "Tirs Éloignés" for c in near.casts) and near.casts


def test_barrage_is_single_target_until_shape_known():
    from dofus_brain.game.state import FightState
    from dofus_brain.paths import config_dir
    from dofus_brain.skills.combat import TurnSolver
    from dofus_brain.skills.fight import models_for

    _, models = models_for(load_phases_config(), "cra", config_dir())
    barrage = [s for s in models if s.name == "Flèche de Barrage"]
    g = Grid()
    me = g.from_uv(5, 0)
    fs = FightState(phase="my_turn", fighters=[Fighter("self", "me", "ally", True, me, 100, 100, 3, 0),
                                               Fighter("a", "Bouftou", "enemy", False, g.from_uv(9, 0), 200, 200),
                                               Fighter("b", "Bouftou", "enemy", False, g.from_uv(10, 0), 200, 200)])
    plan = TurnSolver(g, barrage, "ranged").plan(fs, 3, 0)
    assert len(plan.casts) == 1 and set(plan.casts[0].value.damage) == {"a"}

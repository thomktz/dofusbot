import asyncio

import pytest

from dofus_brain.game.grid import Grid
from dofus_brain.game.state import Dialog, DialogOption, GameState, Quest
from dofus_brain.llm.mock import MockLLM
from dofus_brain.ocr import TemplateOCR
from dofus_brain.perception import Perceiver
from dofus_brain.planner import PlanContext, StepResult
from dofus_brain.skills.config import load_phases_config
from dofus_brain.skills.quest import (DialogChooser, DialogSkill, LocationBook, goals_for, parse_objective,
                                      parse_tracker)

CFG = load_phases_config()


@pytest.mark.parametrize("text,kind,target,count,progress,coords", [
    ("Parler à Ganymède", "talk", "ganymede", None, None, None),
    ("Aller voir Maître Bolet", "talk", "maitre bolet", None, None, None),
    ("Vaincre 3 Bouftous 1/3", "kill", "bouftous", 3, 1, None),
    ("Tuer des Pious (0/5)", "kill", "des pious", 5, 0, None),
    ("Récolter 10 Blé 4/10", "gather", "ble", 10, 4, None),
    ("Se rendre en [3,-4]", "goto", None, None, None, (3, -4)),
    ("Aller en 5, -18", "goto", None, None, None, (5, -18)),
    ("Faire quelque chose d'étrange", "unknown", None, None, None, None),
])
def test_parse_objective(text, kind, target, count, progress, coords):
    o = parse_objective(text)
    assert (o.kind, o.target, o.count, o.progress, o.coords) == (kind, target, count, progress, coords)


def test_parse_bring():
    o = parse_objective("Ramener 5 Laine de Bouftou à Ganymède 2/5")
    assert (o.kind, o.target, o.count, o.progress, o.to) == ("bring", "laine de bouftou", 5, 2, "ganymede")


def test_parse_tracker_picks_first_unfinished():
    q, objs = parse_tracker(["Le chemin d'Incarnam", "Etape : 2/4", "Vaincre 3 Bouftous 3/3",
                             "Parler à Ganymède", ""])
    assert q.name == "Le chemin d'Incarnam" and q.step == "2/4"
    assert [o.kind for o in objs] == ["kill", "talk"] and objs[0].complete
    assert q.objective == "Parler à Ganymède"
    assert parse_tracker([]) == (None, [])


def test_goals_for_objectives():
    book = LocationBook({"npcs": {"Ganymède": [4, -18]}, "monsters": {"Bouftou": [[10, 10], [5, -17]]},
                         "resources": {"Blé": [[3, -4]]}})
    assert [g.kind for g in goals_for(parse_objective("Parler à Ganymède"), book, (0, 0))] == ["travel", "talk"]
    kill = goals_for(parse_objective("Vaincre 3 Bouftou 1/3"), book, (4, -18))
    assert kill[0].params["coords"] == (5, -17) and kill[1].params["count"] == 2
    assert goals_for(parse_objective("Parler à Inconnu"), book, None)[0].kind == "ask"
    assert goals_for(parse_objective("Vaincre 3 Bouftou 3/3"), book, None) == []
    bring = goals_for(parse_objective("Ramener 5 Blé à Ganymède 0/5"), book, (0, 0))
    assert [g.kind for g in bring] == ["travel", "harvest", "travel", "talk"]
    assert goals_for(parse_objective("Se rendre en [1,2]"), book, None)[0].params == {"coords": (1, 2)}


def test_location_book_load_and_learn(tmp_path):
    p = tmp_path / "locations.json"
    assert LocationBook.load(p).npc("x") is None
    p.write_text('{"npcs": {"Ganymede le sage": [4, -18]}}')
    b = LocationBook.load(p)
    assert b.npc("Ganymède") == (4, -18)
    b.learn_monster("Tofu", (1, 1))
    assert b.monster_maps("tofu") == [(1, 1)]


def dialog(*opts, npc="Ganymede", text="Bonjour"):
    return Dialog(npc=npc, text=text, options=[DialogOption(i + 1, o) for i, o in enumerate(opts)],
                  option_points={i + 1: (500.0, 600.0 + 30 * i) for i in range(len(opts))})


def test_chooser_rules():
    ch = DialogChooser(CFG)
    q = Quest("Le chemin d'Incarnam", None, "Parler à Ganymède du travail")
    c = ch.choose(dialog("Je cherche du travail.", "Où est la banque ?", "Au revoir."), q)
    assert c.index == 1 and c.source == "rule"
    assert ch.choose(dialog("Acheter des objets", "Continuer"), None).index == 2
    assert ch.choose(dialog("Acheter", "Vendre"), None).index is None
    assert ch.choose(dialog("Oui", "Non"), None).index is None          # ambiguous -> ask
    assert ch.choose(dialog("Vendre", "Au revoir"), None).index == 2     # only allowed option


def test_chooser_llm_fallback():
    ch = DialogChooser(CFG)
    d = dialog("Oui", "Non")
    llm = MockLLM(responder={"index": 2, "confidence": 0.9, "why": "refuse"})
    c = asyncio.run(ch.choose_with_llm(d, None, llm))
    assert (c.index, c.source) == (2, "llm") and llm.prompts
    unsure = MockLLM(responder={"index": 1, "confidence": 0.3})
    assert asyncio.run(ch.choose_with_llm(d, None, unsure)).index is None
    bad = MockLLM(responder={"index": 9, "confidence": 0.99})
    assert asyncio.run(ch.choose_with_llm(d, None, bad)).source == "ask"
    forb = MockLLM(responder={"index": 1, "confidence": 0.99})
    assert asyncio.run(ch.choose_with_llm(dialog("Acheter", "Payer 100 kamas", "Oui", "Non"), None, forb)).index is None
    clear = MockLLM(responder={"index": 1, "confidence": 1})
    asyncio.run(ch.choose_with_llm(dialog("Continuer"), None, clear))
    assert clear.prompts == []                                          # rule decided, no LLM call


def test_dialog_skill_on_fixture(meta, load_fixture):
    p = Perceiver(meta["grid"], meta["regions"], TemplateOCR.from_font())
    st = p.perceive(load_fixture("dialog.png")).state
    assert st.screen == "dialog" and len(st.dialog.options) == 3
    st.quest = Quest("Travail", None, "Chercher du travail")
    sk = DialogSkill(CFG)
    ctx = PlanContext(None, "auto", Grid.from_config(meta["grid"]), {})
    s = sk.step(st, ctx)
    assert s.meta["index"] == 1 and s.action["kind"] == "click"
    assert s.verify(st, GameState(screen="world")) and not s.verify(st, st)


def test_dialog_skill_asks_when_unsure():
    sk = DialogSkill(CFG)
    st = GameState(screen="dialog", dialog=dialog("Oui", "Non"))
    out = sk.step(st, PlanContext(None, "auto", Grid(), {}))
    assert out.status == "ask" and len(out.data["options"]) == 2
    asyncio.run(sk.prepare(st, MockLLM(responder={"index": 1, "confidence": 0.8})))
    assert sk.step(st, PlanContext(None, "auto", Grid(), {})).meta["source"] == "llm"


def test_talk_skill():
    from dofus_brain.perception.entities import Plate
    from dofus_brain.skills.quest import TalkSkill

    sk = TalkSkill("Ganymède")
    ctx = PlanContext(None, "auto", Grid(), {})
    w = GameState(screen="world")
    assert sk.step(w, ctx).status == "ask"
    sk.set_plates([Plate("Bouftou", level=3, x=100, y=100), Plate("Ganymede", x=400, y=300)])
    s = sk.step(w, ctx)
    assert (s.action["x"], s.action["y"], s.meta["target"]) == (400, 330, "npc")
    assert s.verify(w, GameState(screen="dialog"))
    sk.on_result(StepResult(s, False, "timeout"), w)
    sk.on_result(StepResult(s, False, "timeout"), w)
    assert sk.step(w, ctx).status == "ask"
    assert sk.step(GameState(screen="dialog"), ctx).status == "done"

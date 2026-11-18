import pytest

from dofus_brain.game.grid import Grid
from dofus_brain.game.state import GameState
from dofus_brain.ocr import TemplateOCR
from dofus_brain.perception import Perceiver, Regions
from dofus_brain.planner import PlanContext, StepResult
from dofus_brain.skills.config import load_phases_config
from dofus_brain.skills.popup import PopupReader, PopupSkill, classify_popup

CFG = load_phases_config()
CTX = PlanContext(None, "auto", Grid(), {})


@pytest.mark.parametrize("text,kind", [
    ("Niveau supérieur !", "dismiss"), ("Succès débloqué : Premiers pas", "dismiss"), ("Astuce du jour", "dismiss"),
    ("Bob vous propose un échange", "ask"), ("Invitation à rejoindre un groupe", "ask"),
    ("Félicitations ! Bob vous défie en duel", "ask"),   # ask wins over dismiss
    ("Voulez-vous supprimer cet objet ?", "unknown"), ("", "unknown"),
])
def test_classify(text, kind):
    assert classify_popup(text, CFG) == kind


def test_reader_and_skill_on_fixture(meta, load_fixture):
    img = load_fixture("popup.png")
    st = Perceiver(meta["grid"], meta["regions"], TemplateOCR.from_font()).perceive(img).state
    assert st.screen == "popup"
    text = PopupReader(TemplateOCR.from_font(), Regions(meta["regions"])).read(img)
    assert "niveau" in text.lower()
    sk = PopupSkill(CFG)
    sk.set_text(text)
    s = sk.step(st, CTX)
    assert s.action == {"kind": "key", "key": "escape", "reason": s.action["reason"]}
    assert s.verify(st, GameState(screen="world")) and not s.verify(st, st)


def test_dismiss_once_then_ask():
    sk = PopupSkill(CFG)
    sk.set_text("Niveau supérieur !")
    st = GameState(screen="popup")
    s = sk.step(st, CTX)
    sk.on_result(StepResult(s, False, "timeout"), st)
    assert sk.step(st, CTX).status == "ask"
    sk2 = PopupSkill(CFG)
    sk2.set_text("Niveau supérieur !")
    s = sk2.step(st, CTX)
    sk2.on_result(StepResult(s, True, "verified"), GameState(screen="world"))
    assert sk2.step(st, CTX).action["key"] == "escape"   # next level up popup is closed again


def test_players_and_unknown_ask():
    sk = PopupSkill(CFG)
    st = GameState(screen="popup")
    sk.set_text("Bob vous propose un échange")
    assert sk.step(st, CTX).status == "ask"
    sk.set_text("Voulez-vous supprimer cet objet ?")
    assert sk.step(st, CTX).status == "ask"
    assert sk.step(GameState(screen="world"), CTX).status == "done"

"""Dashboard fight flow: Locate NPCs (Z scan) -> Attack a monster -> fight sequence ->
done, with live planner status, and the kill switch that also leaves the window alone."""
import asyncio

import pytest

from dofus_brain.config import BrainConfig
from dofus_brain.game.grid import Grid
from dofus_brain.game.state import FightState, GameState, MapInfo
from dofus_brain.mock_control import MockController
from dofus_brain.npcs import Entity
from dofus_brain.planner import PlanContext, PlannedStep, StepResult
from dofus_brain.planner.phases import PhasePlanner
from dofus_brain.server import create_app
from dofus_brain.skills import Outcome
from dofus_brain.skills.attack import AttackSkill, FightSequenceSkill, target_point
from dofus_brain.skills.config import load_phases_config
from dofus_brain.skills.quest import LocationBook
from dofus_brain.skills.travel import MapGraph

MOB = Entity("Feu Follet (7)", "monster", 7, 1, 640.0, 300.0, 259, 0.9, "Niveau 7 | Feu Follet (7)")
NPC = Entity("Ganymede", "npc", None, None, 400.0, 200.0, 120, 0.9, "Ganymede")


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def world():
    return GameState(screen="world", confidence=0.9, map=MapInfo((3, -3)))


def fight(phase="placement"):
    return GameState(screen="fight", confidence=0.9, fight=FightState(phase=phase))


# ------------------------------------------------------------------ skills
def test_attack_clicks_the_monster_then_follows_the_fight():
    grid = Grid()
    sk = AttackSkill(MOB, Clock())
    ctx = PlanContext(goal=None, mode="auto", grid=grid, config={})
    s = sk.step(world(), ctx)
    assert isinstance(s, PlannedStep) and s.action["kind"] == "click" and s.action["cell"] == 259
    assert (s.action["x"], s.action["y"]) == tuple(round(v, 1) for v in grid.center(259))
    assert s.verify(world(), fight()) and not s.verify(world(), world())
    sk.on_result(StepResult(s, True, "verified"), fight())
    sk.note(fight())
    assert sk.step(fight("my_turn"), ctx).status == "wait"
    assert sk.step(world(), ctx).status == "done"             # back on the map after the fight


def test_attack_gives_up_after_configured_neighbour_clicks():
    sk = AttackSkill(MOB, Clock())
    ctx = PlanContext(goal=None, mode="auto", grid=Grid(), config={})
    for _ in range(sk.max_clicks):
        s = sk.step(world(), ctx)
        sk.on_result(StepResult(s, False, "timeout"), world())
    out = sk.step(world(), ctx)
    assert isinstance(out, Outcome) and out.status == "failed"


def test_target_point_without_cell_clicks_below_the_plate():
    e = Entity("X", "monster", 1, 1, 100.0, 50.0, None, 1.0, "")
    assert target_point(e, None) == (100.0, 80.0)


def test_fight_sequence_waits_for_placement_then_ends_with_the_fight():
    clock = Clock()
    sk = FightSequenceSkill(clock, wait_for_fight_s=30)
    ctx = PlanContext(goal=None, mode="auto", grid=Grid(), config={})
    assert sk.step(world(), ctx).status == "wait"
    sk.note(fight())
    assert sk.step(world(), ctx).status == "done"
    sk2 = FightSequenceSkill(clock, wait_for_fight_s=30)
    clock.t = 31
    assert sk2.step(world(), ctx).status == "failed"


def test_planner_goals_and_status(tmp_path):
    clock = Clock()
    p = PhasePlanner(load_phases_config(), graph=MapGraph(), book=LocationBook(), clock=clock,
                     maps_path=tmp_path / "maps.json")
    ctx = PlanContext(goal=None, mode="auto", grid=Grid(), config={})
    p.set_goal("attack Feu Follet (7)")
    assert "locate NPCs first" in p._build_goal(world())   # no target picked yet
    p.attack_target = MOB
    p.set_goal("attack Feu Follet (7)")
    s = asyncio.run(p.propose(world(), ctx))          # no Z scan request first: the dashboard did it
    assert s is not None and s.meta["skill"] == "attack"
    st = p.status()
    assert st["goal"] == "attack Feu Follet (7)" and st["skill"] == "attack" and st["attack_target"] == MOB.name
    p.on_result(StepResult(s, True, "verified"), fight())
    asyncio.run(p.propose(fight(), ctx))              # the fight interrupt runs (placement)
    asyncio.run(p.propose(world(), ctx))
    assert p.goal is None                             # done after the fight


# ------------------------------------------------------------------ server
class KotController(MockController):
    def __init__(self):
        super().__init__()
        self.kot = []

    def keep_on_top(self, enabled):
        self.kot.append(bool(enabled))


@pytest.fixture
def app(config_dir, monkeypatch):
    import dofus_brain.npcs as npcs_mod

    monkeypatch.setattr(npcs_mod, "list_npcs", lambda ctl, ocr, regions, grid, key: ([NPC, MOB], ctl.capture()))
    ctl = KotController()
    a = create_app(mock=True, controller=ctl, planner=PhasePlanner(load_phases_config(), graph=MapGraph(),
                                                                   book=LocationBook(), maps_path=config_dir / "m.json"),
                   config=BrainConfig.load(config_dir))
    return a, ctl


class FakeClient:
    def __init__(self):
        self.sent = []
        self.preview = False

    def put(self, text):
        import json
        self.sent.append(json.loads(text))


def run(hub, client, type_, payload=None):
    asyncio.run(hub.handle(client, {"type": type_, "payload": payload or {}, "id": "1"}))
    return client.sent[-1]


def test_locate_attack_fight_and_kill(app):
    a, ctl = app
    hub, agent = a.state.hub, a.state.agent
    c = FakeClient()
    hub.clients.add(c)
    assert not run(hub, c, "npcs.scan")["payload"]["ok"]            # buttons primed only after Start
    assert run(hub, c, "session.start")["payload"]["ok"]
    assert hub.armed and hub.planner_payload()["armed"] and ctl.kot[-1] is True and hub.slot.keep_on_top
    assert run(hub, c, "npcs.scan")["payload"]["ok"]
    lists = [m for m in c.sent if m["type"] == "npcs.list"]
    assert lists and [e["name"] for e in lists[-1]["payload"]["entities"]] == ["Ganymede", "Feu Follet (7)"]
    # NPCs can't be attacked; monsters can
    assert not run(hub, c, "npcs.attack", {"index": 0})["payload"]["ok"]
    assert run(hub, c, "npcs.attack", {"index": 1})["payload"]["ok"]
    assert agent.status.goal == "attack Feu Follet (7)" and agent.status.mode == "auto"
    assert agent.planner.attack_target is MOB
    assert hub.planner_payload()["attack_target"] == "Feu Follet (7)"
    # fight sequence, started during placement
    assert run(hub, c, "fight.start")["payload"]["ok"]
    assert agent.status.goal == "fight sequence"
    # kill: input stops, and the window is left alone (no keep-on-top, no auto-align)
    assert run(hub, c, "steer.kill")["payload"]["ok"]
    assert agent.status.state == "stopped" and ctl.killed
    assert ctl.kot[-1] is False and hub.slot.status()["keep_on_top"] is False and hub.slot.suspended
    with pytest.raises(ValueError):
        asyncio.run(hub.slot.align())
    assert not run(hub, c, "npcs.scan")["payload"]["ok"]          # nothing is sent while killed
    assert not hub.armed and not run(hub, c, "fight.start")["payload"]["ok"]
    assert not run(hub, c, "session.start")["payload"]["ok"]       # kill switch still on
    assert run(hub, c, "steer.resume")["payload"]["ok"]
    assert hub.slot.suspended and ctl.kot[-1] is False             # resume alone doesn't take the window
    assert run(hub, c, "session.start")["payload"]["ok"]
    assert hub.slot.suspended is None and ctl.kot[-1] is True and hub.armed


def test_hotkey_kill_also_leaves_the_window_alone(app):
    a, ctl = app
    hub = a.state.hub
    c = FakeClient()
    assert run(hub, c, "session.start")["payload"]["ok"] and hub.armed
    ctl.kill()                                     # what ctrl+alt+cmd+K does in dofus_control
    assert hub.slot.suspended and ctl.kot[-1] is False and not hub.armed

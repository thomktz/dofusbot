import asyncio
import threading

import pytest

from dofus_brain.agent import Agent, CaptureTimeoutError
from dofus_brain.bus import Bus
from dofus_brain.corpus import CorpusRecorder
from dofus_brain.llm import MockLLM
from dofus_brain.mock_control import KillSwitchTripped, MockController
from dofus_brain.ocr import TemplateOCR
from dofus_brain.perception import Perceiver
from dofus_brain.planner import DemoWalkPlanner, NoopPlanner, PlannedStep, Planner, StepResult

FAST = {"loop_hz": 50, "action_timeout_s": 0.5, "action_retries": 1, "stuck_after_s": 30, "llm_min_interval_s": 0}


def make_agent(planner=None, cfg=None, ctl=None):
    ctl = ctl or MockController()
    p = Perceiver(ctl.grid_cfg, ctl.regions_cfg, TemplateOCR.from_font())
    return Agent(ctl, p, MockLLM(), Bus(), {**FAST, **(cfg or {})}, planner=planner, mock=True), ctl


async def ticks(agent, n):
    for _ in range(n):
        await agent.tick()


def test_planner_protocol():
    assert isinstance(NoopPlanner(), Planner) and isinstance(DemoWalkPlanner(), Planner)


async def test_observe_never_acts_but_proposes():
    agent, ctl = make_agent(DemoWalkPlanner())
    assert agent.status.mode == "observe" and agent.status.state == "idle"
    agent.set_goal("marche")
    assert agent.status.state == "running"
    await ticks(agent, 5)
    assert ctl.executed == []
    assert agent.status.proposed is not None and agent.status.proposed["kind"] == "click"
    assert agent.game.map.coords == (4, -18)


async def test_auto_acts_and_verifies():
    agent, ctl = make_agent(DemoWalkPlanner(max_steps=2))
    agent.set_goal("marche")
    agent.set_mode("auto")
    for _ in range(30):
        await agent.tick()
    assert len(ctl.executed) == 2
    assert agent.game.map.coords == (6, -18)
    msgs = [e["message"] for e in agent.bus.history]
    assert sum(m.startswith("verified") for m in msgs) == 2
    assert any("action" in e for e in agent.bus.history)


async def test_assist_waits_for_step():
    agent, ctl = make_agent(DemoWalkPlanner())
    agent.set_goal("marche")
    agent.set_mode("assist")
    await ticks(agent, 3)
    assert ctl.executed == [] and agent.status.proposed
    await agent.step()
    assert len(ctl.executed) == 1
    agent.set_mode("observe")
    with pytest.raises(ValueError):
        await agent.step()


class NeverWorks:
    name = "never"

    def __init__(self):
        self.goal, self.results = None, []

    def set_goal(self, g):
        self.goal = g

    async def propose(self, state, ctx):
        if self.results:
            return None
        return PlannedStep({"kind": "click", "x": 640, "y": 300}, task="click nothing",
                           verify=lambda before, now: False, timeout_s=0.05, retries=2)

    def on_result(self, result: StepResult, state):
        self.results.append(result)


async def test_timeout_retries_then_fails():
    pl = NeverWorks()
    agent, ctl = make_agent(pl)
    agent.set_goal("x")
    agent.set_mode("auto")
    for _ in range(40):
        await agent.tick()
        await asyncio.sleep(0.01)
    assert len(ctl.executed) == 3                     # first try + 2 retries
    assert len(pl.results) == 1 and not pl.results[0].ok and pl.results[0].reason == "timeout"


async def test_pause_and_kill_and_controller_kill():
    agent, ctl = make_agent(DemoWalkPlanner())
    agent.set_goal("marche")
    agent.set_mode("auto")
    agent.pause()
    await ticks(agent, 5)
    assert ctl.executed == [] and agent.status.state == "paused"
    agent.kill()
    assert ctl.killed and agent.status.state == "stopped"
    await ticks(agent, 5)
    assert ctl.executed == []
    agent.resume()
    assert not ctl.killed and agent.status.state == "running"
    # the controller's own kill switch (hotkey etc.) reaches the agent through on_kill
    task = asyncio.create_task(agent.run())
    await asyncio.sleep(0.05)
    ctl.kill()
    await asyncio.sleep(0.05)
    assert agent.status.state == "stopped"
    agent.stop()
    await task


async def test_kill_during_execute():
    agent, ctl = make_agent(DemoWalkPlanner())
    agent.set_goal("marche")
    agent.set_mode("auto")

    def boom(a):
        raise KillSwitchTripped("user moved the mouse")

    ctl.execute = boom
    await ticks(agent, 2)
    assert agent.status.state == "stopped"


async def test_vlm_fallback_on_low_confidence():
    import numpy as np

    agent, ctl = make_agent(cfg={"vlm_with_mock": True})
    agent.llm = MockLLM(responder={"screen": "menu", "confidence": 0.8, "reason": "inventaire"})
    noise = np.random.default_rng(0).integers(0, 255, (800, 1280, 3), dtype=np.uint8)
    ctl.capture = lambda: type("F", (), {"image": noise, "scale": 1.0, "ts": 0, "window": None})()
    await agent.tick()
    await asyncio.sleep(0.05)
    await agent.tick()
    assert agent.game.screen == "menu" and agent.status.last_decision_ms is not None


async def test_ambiguous_frame_never_reaches_planner_or_executes():
    import numpy as np

    agent, ctl = make_agent(DemoWalkPlanner(), cfg={"vlm_with_mock": True})
    agent.llm = MockLLM(responder={"screen": "fight", "confidence": 0.9, "reason": "combat"})
    # Noise has no trustworthy deterministic classification.  Auto mode must wait for
    # VLM instead of proposing or executing a world action from this frame.
    noise = np.random.default_rng(12).integers(0, 255, (800, 1280, 3), dtype=np.uint8)
    ctl.capture = lambda: type("F", (), {"image": noise, "scale": 1.0, "ts": 0, "window": None})()
    agent.set_goal("marche")
    agent.set_mode("auto")
    await agent.tick()
    assert ctl.executed == []
    assert agent.status.proposed is None
    await asyncio.sleep(0.05)
    await agent.tick()
    assert agent.game.screen == "fight"
    assert ctl.executed == []


async def test_capture_timeout_reuses_the_single_blocked_capture():
    started = threading.Event()
    release = threading.Event()
    calls = 0
    agent, ctl = make_agent(cfg={"capture_timeout_s": 0.01})

    def blocked_capture():
        nonlocal calls
        calls += 1
        started.set()
        release.wait(timeout=1)
        return MockController().capture()

    ctl.capture = blocked_capture
    with pytest.raises(CaptureTimeoutError):
        await agent.tick()
    assert started.wait(0.1) and calls == 1
    with pytest.raises(CaptureTimeoutError):
        await agent.tick()
    assert calls == 1
    release.set()
    await agent.tick()
    assert calls == 1


def test_corpus_recorder_deduplicates_and_writes_label_template(tmp_path):
    import json
    import numpy as np

    from dofus_brain.perception.classifier import Classification

    recorder = CorpusRecorder(tmp_path, min_interval_s=0, min_hash_distance=1)
    image = np.full((80, 128, 3), 42, dtype=np.uint8)
    cls = Classification("world", 0.8, {"world": 0.8})
    saved = recorder.record(image, 1.0, cls)
    assert saved is not None and saved.exists()
    assert recorder.record(image, 1.0, cls) is None
    meta = json.loads(saved.with_suffix(".json").read_text())
    assert meta["truth_screen"] is None
    assert meta["predicted_screen"] == "world"
